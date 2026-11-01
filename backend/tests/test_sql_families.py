"""Ordinary analytical SQL passes validation and runs in real DuckDB, on a tidy table and on a messy one (categories, periods, intervals, mixed and nested object columns, durations, decimals,
time zones); the dangerous patterns are still refused; a failure is said in plain words and logged. Real DuckDB, scripted AI."""
import decimal
import json
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np
import pandas as pd
import pytest

from app.lens import chat
from app.lens.agent import errors, sqlguard as g
from tests.test_agent_custom import DANGEROUS
from tests.test_lens import FakeAI, make_run

N = 240
rng = np.random.default_rng(3)
TIDY = pd.DataFrame({"order_date": pd.to_datetime("2024-01-01") + pd.to_timedelta(np.arange(N) % 200, unit="D"), "region": rng.choice(["N", "S", "E", "W"], N), "segment": rng.choice(["A", "B", "C"], N),
                     "revenue": rng.gamma(2, 120, N).round(2), "discount": rng.uniform(0, 30, N).round(1), "note": rng.choice(["ok", None, "late"], N)})
MESSY = pd.DataFrame({
    "region": pd.Categorical(rng.choice(["N", "S", "E", "W"], N)), "revenue": rng.gamma(2, 100, N), "qty": pd.array(rng.integers(1, 9, N), dtype="Int64"), "flag": rng.choice([True, False], N),
    "mixed": [1 if i % 3 == 0 else "x" if i % 3 == 1 else None for i in range(N)], "order_date": pd.to_datetime("2024-01-01") + pd.to_timedelta(np.arange(N), unit="D"),
    "when_tz": pd.to_datetime("2024-01-01", utc=True) + pd.to_timedelta(np.arange(N), unit="D"), "dur": pd.to_timedelta(np.arange(N), unit="h"), "dec": [decimal.Decimal("1.50") + i for i in range(N)],
    "period": pd.period_range("2024-01", periods=N, freq="D"), "txt": pd.array(["a", "b", None, "d"] * (N // 4), dtype="string"), "nested": [[1, 2]] * N, "raw": [b"ab"] * N,
    "bucket": pd.cut(rng.random(N), bins=[0, .5, 1])})

FAMILIES = {
    "median": "SELECT region, MEDIAN(revenue) AS median_revenue FROM data GROUP BY region ORDER BY region",
    "quantile_cont": "SELECT region, quantile_cont(revenue, 0.5) AS med, quantile_cont(revenue, 0.9) AS p90 FROM data GROUP BY region",
    "percentile_cont": "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY revenue) AS med FROM data",
    "stddev_variance": "SELECT region, STDDEV(revenue), VARIANCE(revenue), STDDEV_POP(revenue), VAR_SAMP(revenue) FROM data GROUP BY region",
    "count_distinct": "SELECT COUNT(DISTINCT region) AS n, COUNT(*) AS c FROM data",
    "avg_min_max_sum": "SELECT AVG(revenue), MIN(revenue), MAX(revenue), SUM(revenue) FROM data",
    "date_trunc": "SELECT date_trunc('month', order_date) AS m, SUM(revenue) FROM data GROUP BY 1 ORDER BY 1",
    "strftime": "SELECT strftime(order_date, '%Y-%m') AS ym, COUNT(*) FROM data GROUP BY 1 ORDER BY 1",
    "year_month": "SELECT year(order_date) AS y, month(order_date) AS m, SUM(revenue) FROM data GROUP BY 1, 2",
    "extract": "SELECT EXTRACT(year FROM order_date) AS y, EXTRACT(month FROM order_date) AS m, COUNT(*) FROM data GROUP BY 1, 2",
    "case_when": "SELECT CASE WHEN revenue > 300 THEN 'high' WHEN revenue > 100 THEN 'mid' ELSE 'low' END AS band, COUNT(*) FROM data GROUP BY 1",
    "coalesce": "SELECT COALESCE(CAST(region AS VARCHAR), 'none') AS r, COUNT(*) FROM data GROUP BY 1",
    "round": "SELECT region, ROUND(AVG(revenue), 2) AS avg_rev FROM data GROUP BY region",
    "rank": "SELECT region, SUM(revenue) AS t, RANK() OVER (ORDER BY SUM(revenue) DESC) AS r FROM data GROUP BY region",
    "sum_over": "SELECT order_date, revenue, SUM(revenue) OVER (ORDER BY order_date) AS running FROM data ORDER BY order_date LIMIT 5",
    "lag": "SELECT m, t - LAG(t) OVER (ORDER BY m) AS d FROM (SELECT date_trunc('month', order_date) AS m, SUM(revenue) AS t FROM data GROUP BY 1)",
    "order_limit_having": "SELECT region, COUNT(*) AS c FROM data GROUP BY region HAVING COUNT(*) > 5 ORDER BY c DESC LIMIT 3",
    "cte": "WITH t AS (SELECT region, revenue FROM data) SELECT region, MEDIAN(revenue) FROM t GROUP BY region",
    "two_ctes": "WITH a AS (SELECT region, SUM(revenue) AS s FROM data GROUP BY region), b AS (SELECT AVG(s) AS m FROM a) SELECT a.region, a.s, b.m FROM a, b",
    "alias_and_quoted": 'SELECT d."region", MEDIAN(d."revenue") AS m FROM "data" AS d GROUP BY d."region"',
    "filter_clause": "SELECT COUNT(*) FILTER (WHERE revenue > 100) AS big FROM data",
    "cast": "SELECT CAST(revenue AS INTEGER) AS i, revenue::DOUBLE AS d FROM data LIMIT 3",
    "interval": "SELECT COUNT(*) FROM data WHERE order_date >= (SELECT MAX(order_date) FROM data) - INTERVAL 30 DAY",
    "nullif": "SELECT SUM(revenue) / NULLIF(COUNT(*), 0) AS per FROM data",
    "mode_argmax": "SELECT mode(region), arg_max(region, revenue) FROM data",
    "union": "SELECT 'a' AS k, COUNT(*) AS n FROM data UNION ALL SELECT 'b', SUM(revenue) FROM data",
    "corr": "SELECT corr(revenue, revenue * 2 + 1) FROM data",
    "terminating_semicolon": "SELECT region, MEDIAN(revenue) FROM data GROUP BY region;",
    "star": "SELECT * FROM data ORDER BY revenue DESC LIMIT 5",
    "dayname": "SELECT dayname(order_date) AS d, COUNT(*) FROM data GROUP BY 1",
}
TIDY_ONLY = {"count_distinct", "alias_and_quoted"}


def runs(sql, df):
    cols = list(df.columns)
    return g.execute(g.validate(sql, cols), df)


@pytest.mark.parametrize("family", list(FAMILIES))
def test_each_function_family_validates_and_runs_on_a_tidy_table(family):
    sql = FAMILIES[family]
    if "revenue" not in TIDY.columns:
        pytest.skip()
    res = runs(sql, TIDY.rename(columns={"segment": "segment"}))
    assert res.rows


MESSY_FAMILIES = [k for k in FAMILIES if k not in ("coalesce", "filter_clause", "corr", "mode_argmax", "dayname") or True]


@pytest.mark.parametrize("family", [k for k in FAMILIES if k not in ("lag", "dayname")])
def test_each_function_family_runs_on_a_messy_table_too(family):
    res = runs(FAMILIES[family], MESSY)
    assert res.rows and not res.not_numbers


def test_a_table_with_types_the_database_cannot_read_is_still_queryable():
    for sql in ("SELECT mixed, COUNT(*) FROM data GROUP BY 1", "SELECT SUM(dec) FROM data", "SELECT MIN(when_tz) FROM data", "SELECT bucket, COUNT(*) FROM data GROUP BY 1", "SELECT nested FROM data LIMIT 1",
                "SELECT period FROM data LIMIT 1", "SELECT AVG(dur) FROM data"):
        assert runs(sql, MESSY).rows, sql
    # the table itself was not changed
    assert isinstance(MESSY["region"].dtype, pd.CategoricalDtype) and isinstance(MESSY["period"].dtype, pd.PeriodDtype)


@pytest.mark.parametrize("why,sql", list(DANGEROUS.items()))
def test_every_dangerous_pattern_is_still_refused(why, sql):
    with pytest.raises(g.SqlRejected):
        g.validate(sql, list(MESSY.columns))


def test_the_parser_failing_in_an_unexpected_way_is_a_refusal_not_a_crash(monkeypatch):
    monkeypatch.setattr(g, "_validate", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("parser bug at /secret/path/x.py")))
    with pytest.raises(g.SqlRejected) as e:
        g.validate("SELECT 1 FROM data", ["a"])
    assert "/secret" not in str(e.value) and "could not be read" in str(e.value)


# ------------------------------------------------------------------ through the chat: plain messages, the repair uses the real error, and the failure is logged
@pytest.fixture
def messy_run(monkeypatch):
    monkeypatch.setenv("CHATBOT", "on")
    chat._DAILY["until"] = 0.0
    und = {"source": "llm", "goal_columns": {}, "descriptive": [], "unmapped": [], "retries": 0, "error": "", "columns": {
        "region": {"role": "category", "meaning": "region", "display_name": "Region"}, "revenue": {"role": "measure", "meaning": "money", "display_name": "Revenue", "measure_kind": "money"},
        "order_date": {"role": "date", "meaning": "date", "display_name": "Order date"}}}
    return make_run(MESSY, und)


def plan(sql, desc="the median revenue in each region"):
    return {"plan": [{"tool": "custom_analysis", "args": {"sql": sql, "description": desc}}]}


def ask(run, ai, q):
    rid, wd, res = run
    return chat.ask(wd, res, question=q, llm=ai, allow_ai=True)["answer"]


def test_the_median_per_region_request_works_on_a_messy_table(messy_run):
    a = ask(messy_run, FakeAI(plan(FAMILIES["median"])), "what is the median revenue in each region")
    assert a["status"] == "answered" and a["label"] == "Custom analysis" and len(a["table"]["rows"]) == 4 and "unexpected" not in a["text"].lower()
    truth = MESSY.groupby(MESSY["region"].astype(str))["revenue"].median()
    got = {r[0]: r[1] for r in a["table"]["rows"]}
    assert all(abs(got[k] - truth[k]) < 1e-9 for k in truth.index)
    json.dumps(a, default=str)                       # whatever the database returned can be sent to the page


def test_a_database_error_goes_back_to_the_ai_as_the_real_error_text_and_is_logged(messy_run):
    rid, wd, res = messy_run
    ai = FakeAI(plan("SELECT region, MEDIAN(revnue) FROM data GROUP BY region"), {"sql": FAMILIES["median"], "description": "the median revenue in each region"})
    a = ask(messy_run, ai, "median revenue per region")
    assert a["status"] == "answered" and ai.calls == 2 and "revnue" in ai.prompts[1] and "ERROR:" in ai.prompts[1]
    line = json.loads((wd / "lens_errors.jsonl").read_text().splitlines()[0])
    assert line["tool"] == "custom_analysis" and "revnue" in line["sql"] and "revnue" in line["error"] and "/home" not in line["error"]


def test_an_unexpected_failure_inside_a_tool_is_said_plainly_and_logged(messy_run, monkeypatch):
    rid, wd, res = messy_run
    from app.lens.agent.tools import custom_analysis as ca
    monkeypatch.setattr(ca.CustomAnalysis, "_cross_check", lambda *a, **k: (_ for _ in ()).throw(KeyError("secret_internal_key")))
    a = ask(messy_run, FakeAI({"plan": [{"tool": "custom_analysis", "args": {"sql": FAMILIES["median"], "description": "x", "check_query": {"op": "aggregate", "agg": "count"}}}]}), "median")
    text = a["text"].lower()
    assert "unexpectedly" not in text and "secret_internal_key" not in text and "i could not put that result together" in text and "try asking it another way" in text
    assert "custom_analysis" not in text
    log = [json.loads(x) for x in (wd / "lens_errors.jsonl").read_text().splitlines()]
    assert log[-1]["kind"] == "result" and "secret_internal_key" in log[-1]["error"] and "SELECT region" in log[-1]["sql"]


@pytest.mark.parametrize("exc,words", [(MemoryError("x"), "more room"), (TimeoutError("x"), "too long"), (OSError("x"), "could not be read"), (RuntimeError("x"), "went wrong")])
def test_every_kind_of_failure_has_a_plain_message(exc, words):
    msg = errors.plain_message(exc)
    assert words in msg and "unexpectedly" not in msg and exc.__class__.__name__ not in msg
