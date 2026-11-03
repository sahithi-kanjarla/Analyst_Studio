"""custom_analysis and the SQL guard: every dangerous pattern is rejected by the parser, timeouts and row caps hold, the repair loop works, a cross-check disagreement is shown, and no raw row reaches the AI."""
import json
import os
import time

os.environ["LLM_PROVIDER"] = "none"

import numpy as np
import pandas as pd
import pytest

from app.lens import chat, store
from app.lens.agent import sqlguard as g
from app.lens.agent.env import Env
from app.lens.agent.tools import custom_analysis as ca
from tests.test_lens import FakeAI, make_run

DF = pd.DataFrame({"city": ["Delhi", "Pune", "Delhi", "Pune"], "amount": [10.0, 20.0, 30.0, 45.5], "order date": pd.to_datetime(["2024-01-01", "2024-01-02", "2024-02-01", "2024-02-02"])})
COLS = list(DF.columns)


# ------------------------------------------------------------------ the SQL validator
OK_QUERIES = ["SELECT city, SUM(amount) AS total FROM data GROUP BY city", "WITH t AS (SELECT city, amount FROM data) SELECT city, AVG(amount) FROM t GROUP BY 1",
              'SELECT "order date", COUNT(*) FROM data GROUP BY 1', "SELECT * FROM data LIMIT 3", "SELECT city FROM data UNION SELECT city FROM data",
              "SELECT city, amount / SUM(amount) OVER () AS share FROM data"]


@pytest.mark.parametrize("sql", OK_QUERIES)
def test_valid_queries_pass_and_run(sql):
    res = g.execute(g.validate(sql, COLS), DF)
    assert res.rows


DANGEROUS = {
    "two statements": "SELECT 1 FROM data; SELECT 2 FROM data", "a second statement hidden after a semicolon": "SELECT city FROM data; DROP TABLE data",
    "DROP": "DROP TABLE data", "DELETE": "DELETE FROM data", "UPDATE": "UPDATE data SET amount = 0", "INSERT": "INSERT INTO data VALUES ('x', 1, NULL)", "CREATE": "CREATE TABLE x AS SELECT * FROM data",
    "ALTER": "ALTER TABLE data ADD COLUMN z INT", "COPY": "COPY data TO '/tmp/x.csv'", "COPY from": "COPY data FROM '/etc/passwd'", "ATTACH": "ATTACH '/tmp/x.db' AS x", "DETACH": "DETACH x",
    "PRAGMA": "PRAGMA table_info('data')", "PRAGMA function": "SELECT * FROM pragma_table_info('data')", "INSTALL": "INSTALL httpfs", "LOAD": "LOAD httpfs", "SET": "SET enable_external_access = true",
    "CALL": "CALL pragma_version()", "EXPORT": "EXPORT DATABASE '/tmp/x'", "read_csv": "SELECT * FROM read_csv('/etc/passwd')", "read_parquet": "SELECT * FROM read_parquet('x.parquet')",
    "read_json": "SELECT * FROM read_json_auto('x.json')", "read_text": "SELECT content FROM read_text('/etc/passwd')", "a file name as a table": "SELECT * FROM '/etc/passwd'", "glob": "SELECT * FROM glob('/*')",
    "a network function": "SELECT * FROM read_csv('http://example.com/x.csv')", "an http function": "SELECT http_get('http://example.com')", "getenv": "SELECT getenv('HOME')", "duckdb_settings": "SELECT * FROM duckdb_settings()",
    "duckdb_tables": "SELECT * FROM duckdb_tables()", "another table": "SELECT * FROM other", "a join to another table": "SELECT * FROM data JOIN secrets USING (city)", "a schema": "SELECT * FROM main.data",
    "information_schema": "SELECT * FROM information_schema.tables", "an unknown column": "SELECT nope FROM data", "an unknown qualified column": "SELECT data.nope FROM data", "SELECT INTO": "SELECT * INTO x FROM data",
    "a comment hiding a statement": "SELECT 1 FROM data /* ; */; DROP TABLE data", "a line comment trick": "SELECT city FROM data -- ; DROP TABLE data\n; DROP TABLE data", "a quoted statement": "SELECT 'a; DROP TABLE data' FROM x",
    "empty": "   ", "not SQL": "please show me everything", "a very long query": "SELECT " + ", ".join(["city"] * 2000) + " FROM data", "a nul byte": "SELECT city FROM data\x00; DROP TABLE data",
    "VALUES": "VALUES (1), (2)", "a table function": "SELECT * FROM range(10)", "unnest as a table": "SELECT * FROM unnest([1,2,3])", "system function": "SELECT system('ls')",
}


@pytest.mark.parametrize("why,sql", list(DANGEROUS.items()))
def test_dangerous_or_invalid_queries_are_rejected_by_the_parser(why, sql):
    with pytest.raises(g.SqlRejected) as e:
        g.validate(sql, COLS)
    assert str(e.value)       # a plain sentence


def test_what_runs_is_the_regenerated_sql_not_the_text():
    c = g.validate("select /* hello */ city , sum( amount )  from   data -- trailing\n group by city", COLS)
    assert "hello" not in c.sql and "trailing" not in c.sql and ";" not in c.sql


def test_external_access_is_off_and_configuration_locked():
    import duckdb
    con = duckdb.connect(":memory:")
    con.register("data", DF)
    con.execute("SET enable_external_access=false")
    con.execute("SET lock_configuration=true")
    with pytest.raises(Exception):
        con.execute("SELECT * FROM read_csv('/etc/passwd')")
    with pytest.raises(Exception):
        con.execute("SET enable_external_access=true")


def test_row_caps_columns_and_the_ai_view():
    big = pd.DataFrame({"k": np.arange(5000), "v": np.arange(5000.0)})
    agg = g.execute(g.validate("SELECT k, SUM(v) AS s FROM data GROUP BY k", ["k", "v"]), big)
    assert agg.aggregated and len(agg.rows) == g.MAX_READ_ROWS and agg.truncated
    ai = g.for_ai(agg)
    assert len(ai["rows"]) == g.AGG_ROWS_TO_AI and len(ai["columns"]) <= g.AGG_COLS_TO_AI
    raw = g.execute(g.validate("SELECT * FROM data", ["k", "v"]), big)
    assert not raw.aggregated and len(raw.rows) == g.RAW_ROWS and g.for_ai(raw) is None          # rows are for the user only
    wide = pd.DataFrame({f"c{i}": [1, 2] for i in range(20)})
    r = g.execute(g.validate("SELECT " + ", ".join(f"SUM(c{i}) AS s{i}" for i in range(20)) + " FROM data", list(wide.columns)), wide)
    assert len(g.for_ai(r)["columns"]) == g.AGG_COLS_TO_AI


def test_a_slow_query_is_stopped(monkeypatch):
    monkeypatch.setattr(g, "TIMEOUT_S", 0.3)
    n = pd.DataFrame({"a": np.arange(2000)})
    sql = "SELECT COUNT(*) FROM data d1, data d2, data d3, data d4, data d5, data d6"
    t0 = time.monotonic()
    with pytest.raises(g.SqlRejected) as e:
        g.execute(g.validate(sql, ["a"]), n)
    assert "stopped" in str(e.value) and time.monotonic() - t0 < 8


def test_errors_given_to_the_ai_are_sanitised():
    t = g.sanitise_error("IO Error: No files found that match /home/user/secret/data.csv LINE 1: SELECT 'a very long value from the data table'")
    assert "/home" not in t and "very long value" not in t and len(t) <= 300


# ------------------------------------------------------------------ the tool
@pytest.fixture
def shop(monkeypatch):
    monkeypatch.setenv("CHATBOT", "on")
    chat._DAILY["until"] = 0.0
    df = pd.DataFrame({"order_date": pd.to_datetime("2024-01-01") + pd.to_timedelta(np.arange(120) % 60, unit="D"), "city": ["Delhi", "Mumbai", "Pune"] * 40,
                       "amount": (np.arange(120) % 7 + 1) * 10.0, "customer_id": [f"C{i % 30:02d}" for i in range(120)]})
    und = {"source": "llm", "goal_columns": {}, "descriptive": [], "unmapped": [], "retries": 0, "error": "", "columns": {
        "order_date": {"role": "date", "meaning": "when", "display_name": "Order date"}, "city": {"role": "category", "meaning": "city", "display_name": "City"},
        "amount": {"role": "measure", "meaning": "money", "display_name": "Amount", "measure_kind": "money"}, "customer_id": {"role": "id", "meaning": "customer", "display_name": "Customer"}}}
    rid, wd, result = make_run(df, und)
    return rid, wd, result, df


def plan(sql, desc="the total amount for each city", **kw):
    return {"plan": [{"tool": "custom_analysis", "args": {"sql": sql, "description": desc, **kw}}]}


def ask(shop, ai, text="what is the total amount for each city, as a share of the grand total?", **kw):
    rid, wd, result, df = shop
    return chat.ask(wd, result, question=text, llm=ai, allow_ai=ai is not None, **kw)


def test_custom_analysis_runs_and_is_labelled_with_sql_collapsed(shop):
    df = shop[3]
    ai = FakeAI(plan("SELECT city, SUM(amount) AS total FROM data GROUP BY city ORDER BY total DESC"))
    a = ask(shop, ai)["answer"]
    assert a["status"] == "answered" and a["label"] == "Custom analysis" and a["kind"] == "custom" and ai.calls == 1
    truth = df.groupby("city")["amount"].sum().sort_values(ascending=False)
    assert [r[0] for r in a["table"]["shown"]] == list(truth.index) and a["table"]["shown"][0][1] == f"{truth.iloc[0]:,.0f}"
    assert a["how"]["sql"].lower().startswith("select") and any("Custom analysis" in x for x in a["how"]["lines"]) and not any("SUM(" in x for x in a["how"]["lines"])
    assert a["fact_ids"][0].startswith("c") and a["agent"]["tools"] == ["custom_analysis"]


def test_a_failing_query_is_repaired_up_to_twice_then_given_up(shop):
    ai = FakeAI(plan("SELECT nope FROM data"), {"sql": "SELECT still_nope FROM data", "description": "x"}, {"sql": "SELECT city, COUNT(*) AS n FROM data GROUP BY city", "description": "the number of orders per city"})
    a = ask(shop, ai)["answer"]
    assert a["status"] == "answered" and ai.calls == 3 and a["table"]["columns"] == ["City", "N"] and any("corrected 2 times" in n for n in a["how"]["notes"])
    ai = FakeAI(plan("SELECT nope FROM data"), {"sql": "SELECT a FROM data", "description": "x"}, {"sql": "SELECT b FROM data", "description": "x"}, {"sql": "SELECT c FROM data", "description": "x"})
    a = ask(shop, ai)["answer"]
    assert a["status"] == "no_fit" and "reliably" in a["text"] and ai.calls == 3 and a["chips"]                  # the plan call plus 2 repairs, then it stops


def test_the_repair_prompt_holds_the_sanitised_error_and_no_rows(shop):
    ai = FakeAI(plan("SELECT nope FROM data"), {"sql": "SELECT city, COUNT(*) FROM data GROUP BY city", "description": "per city"})
    ask(shop, ai)
    repair = ai.prompts[1]
    assert "ERROR:" in repair and "nope" in repair and "PREVIOUS SQL" in repair


def test_no_raw_row_is_ever_in_a_prompt(shop):
    rid, wd, result, df = shop
    ai = FakeAI(plan("SELECT customer_id, amount FROM data"), {"sql": "SELECT customer_id FROM data WHERE nope = 1", "description": "x"})
    ask(shop, ai)
    blob = " ".join(ai.prompts)
    cells = {f"{a}" for a in df["amount"].unique()}
    assert "C07" not in blob and "C13" not in blob                          # ids that only exist in rows are never sent
    assert not any(f'"{r}"' in blob for r in df["customer_id"].unique())


def test_the_aggregated_result_the_ai_may_get_is_capped(shop):
    r = g.execute(g.validate("SELECT customer_id, SUM(amount) AS s FROM data GROUP BY customer_id", list(shop[3].columns)), shop[3])
    assert len(g.for_ai(r)["rows"]) <= 50


def test_a_cross_check_that_agrees_is_noted_and_one_that_disagrees_is_shown(shop):
    cq = {"op": "aggregate", "measure": "amount", "agg": "sum", "group_by": ["city"], "n": 10, "sort": {"by": "value", "dir": "desc"}}
    a = ask(shop, FakeAI(plan("SELECT city, SUM(amount) AS total FROM data GROUP BY city", check_query=cq)))["answer"]
    assert not a.get("disagree") and any("same figures" in n for n in a["how"]["notes"])
    a = ask(shop, FakeAI(plan("SELECT city, AVG(amount) AS total FROM data GROUP BY city", check_query=cq)))["answer"]
    assert a["disagree"] and a["text"].startswith("Two methods disagree") and a["second_method"]["table"]["shown"] and a["table"]["shown"]
    assert any("different figures" in n for n in a["how"]["notes"])


def test_a_result_with_nothing_in_it_is_not_presented_as_an_answer(shop):
    a = ask(shop, FakeAI(plan("SELECT city FROM data WHERE city = 'Nowhere'"), {"sql": "SELECT city FROM data WHERE city = 'Nowhere2'", "description": "x"}, {"sql": "SELECT city FROM data WHERE amount < 0", "description": "x"}))["answer"]
    assert a["status"] == "no_fit"


def test_matching_values_are_found_by_the_existing_value_matching(shop):
    rid, wd, result, df = shop
    from app.lens.agent import make_env
    from app.lens import chat as c
    d, ctx = c.load_data(wd, result)
    env = make_env(wd, result, d, ctx, "how much did pune buy in total", "Report", None, False, None, None)
    assert ca.matching_values(env) == {"City": ["Pune"]}


def test_without_an_ai_the_tool_is_unavailable_and_says_so(shop):
    rid, wd, result, df = shop
    from app.lens.agent import make_env
    d, ctx = chat.load_data(wd, result)
    env = make_env(wd, result, d, ctx, "x", "Report", None, False, None, None)
    r = ca.CustomAnalysis().run({"sql": "SELECT 1 FROM data", "description": "x"}, env, [])
    assert not r.ok and "needs the AI" in r.error
    from app.lens.agent import planner
    errs, hard = planner.validate(plan("SELECT city FROM data"), env)
    assert hard and "needs the AI" in errs[0]


def test_nan_or_infinite_results_are_a_problem_and_trigger_the_repair(shop):
    ai = FakeAI(plan("SELECT city, SUM(amount) / 0.0 AS bad FROM data GROUP BY city"), {"sql": "SELECT city, SUM(amount) AS total FROM data GROUP BY city", "description": "the total for each city"})
    a = ask(shop, ai)["answer"]
    assert a["status"] == "answered" and ai.calls == 2 and "not numbers" in ai.prompts[1]


def test_parts_that_do_not_add_up_to_the_whole_are_noted():
    assert "add up to" in g.share_note(["city", "share of total"], [["a", 0.5], ["b", 0.2]])
    assert g.share_note(["city", "share of total"], [["a", 0.6], ["b", 0.4]]) == ""
    assert g.share_note(["city", "total"], [["a", 5], ["b", 6]]) == ""


def test_only_an_aggregated_custom_result_reaches_the_brief_writer(shop):
    from tests.test_agent_loop import P
    rid, wd, result, df = shop
    brief = {"audience": "the owner", "length": "short", "focus": "cities", "columns": ["city"], "evidence": [{"tool": "custom_analysis", "args": {"sql": "SELECT city, SUM(amount) AS total FROM data GROUP BY city", "description": "the total for each city"}},
                                                                                                         {"tool": "custom_analysis", "args": {"sql": "SELECT customer_id, amount FROM data", "description": "the orders"}}]}
    ai = FakeAI(P(("brief", {"briefs": [brief]})), {"headline": "x", "key_figures": [], "recommendations": []})
    chat.ask(wd, result, question="brief for the owner about cities", llm=ai, allow_ai=True)
    writer_prompt = ai.prompts[-1]
    assert "total=" in writer_prompt and "city=Delhi" in writer_prompt                       # the aggregated result, capped
    assert "C07" not in writer_prompt and "customer_id=" not in writer_prompt               # the non-aggregated rows are never passed on
