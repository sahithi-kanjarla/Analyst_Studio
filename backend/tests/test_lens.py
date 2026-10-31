"""Lens, Stage 1: the AI chooses (here a fake one), code validates, computes, writes the sentence and draws the chart. Every figure is compared with pandas."""
import hashlib
import json
import re
import uuid

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import lens, llm, runner
from app.lens import chat, chips as chipmod, context as ctxmod, engine, query as Q, store
from app.main import app

client = TestClient(app)


class Msg:
    def __init__(self, content):
        self.content = content


class FakeAI:
    """Replies with the next item of `replies` (a dict, a string, or an Exception to raise), and keeps every prompt it was given."""
    kwargs = {}

    def __init__(self, *replies):
        self.replies, self.prompts, self.calls = list(replies), [], 0

    def invoke(self, messages):
        self.calls += 1
        self.prompts.append(messages[1][1])
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(r, Exception):
            raise r
        return Msg(r if isinstance(r, str) else json.dumps(r))


def q(**kw):
    return {"action": "query", "query": kw}


def make_df():
    rng = np.random.default_rng(7)
    n = 600
    city = rng.choice(["Delhi", "Mumbai", "Pune", "Chennai"], n, p=[0.4, 0.3, 0.2, 0.1])
    tier = rng.choice(["Gold", "Silver", "Bronze"], n)
    df = pd.DataFrame({
        "order_date": pd.to_datetime("2023-01-01") + pd.to_timedelta(rng.integers(0, 546, n), unit="D"),
        "city": city, "ship_city": city, "tier": tier,
        "payment": rng.choice(["UPI", "Card", "Cash"], n),
        "amount": rng.gamma(2.0, 400.0, n).round(2),
        "quantity": rng.integers(1, 6, n).astype(float),
        "customer_id": ["C%03d" % i for i in rng.integers(0, 90, n)],
    })
    df["discount"] = (df["amount"] * rng.uniform(0.0, 0.2, n)).round(2)
    df.loc[rng.choice(n, 12, replace=False), "amount"] = np.nan          # rows with no amount: left out, and counted
    df["ship_city"] = df["ship_city"].where(df["city"] != "Pune", "Pune hub")
    return df


UNDERSTANDING = {"source": "llm", "goal_columns": {}, "descriptive": [], "unmapped": [], "retries": 0, "error": "", "columns": {
    "order_date": {"role": "date", "meaning": "when the order was placed", "display_name": "Order date"},
    "city": {"role": "category", "meaning": "city the customer lives in", "display_name": "City"},
    "ship_city": {"role": "category", "meaning": "city the parcel was sent from", "display_name": "Ship city"},
    "tier": {"role": "category", "meaning": "membership level", "display_name": "Tier"},
    "payment": {"role": "category", "meaning": "how it was paid", "display_name": "Payment"},
    "amount": {"role": "measure", "meaning": "order value", "display_name": "Amount", "measure_kind": "money"},
    "quantity": {"role": "measure", "meaning": "units in the order", "display_name": "Quantity", "measure_kind": "quantity"},
    "discount": {"role": "measure", "meaning": "money taken off", "display_name": "Discount", "measure_kind": "money"},
    "customer_id": {"role": "id", "meaning": "customer", "display_name": "Customer"}}}


def make_run(df=None, understanding=UNDERSTANDING, extra=None):
    rid = uuid.uuid4().hex[:12]
    wd = runner.run_dir(rid)
    wd.mkdir(parents=True)
    (df if df is not None else make_df()).to_pickle(wd / "clean.pkl")
    result = {"run_id": rid, "goal": "test", "plan": {"target": "amount", "date_col": "order_date"}, "understanding": understanding, "context": {"currency": ""},
              "profile": {"columns": []}, "report_md": "# Report\nBody", "derived_metrics": {"created": []}, **(extra or {})}
    (wd / "result.json").write_text(json.dumps(result), encoding="utf-8")
    (wd / "report.md").write_text(result["report_md"], encoding="utf-8")
    runner.write_status(rid, "done", goal="test")
    return rid, wd, result


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setenv("CHATBOT", "on")
    chat._DAILY["until"] = 0.0
    rid, wd, result = make_run()
    df = pd.read_pickle(wd / "clean.pkl")
    return rid, wd, result, df


def ask(run, ai=None, **kw):
    rid, wd, result, df = run
    return chat.ask(wd, result, llm=ai, allow_ai=ai is not None, **kw)


def numbers(text: str) -> list[str]:
    t = re.sub(r"\b\d{4}-(?:Q\d|\d{2}(?:-\d{2})?)\b|\b\d{4}\b", " ", text)       # periods and years are labels, not figures
    return [m.replace(",", "") for m in re.findall(r"\d[\d,]*\.?\d*", t)]


def assert_figures_are_computed(turn):
    """Every number in the sentence is one of the recorded figures, and each recorded figure's text is the formatted computed value."""
    a = turn["answer"]
    shown = {re.sub(r"[^\d.]", "", f["shown"]) for f in a["figures"]}
    allowed = shown | {str(a["query"].get("n", ""))}
    for n in numbers(a["text"]):
        assert n.rstrip(".") in allowed or n in allowed, (n, a["text"], shown)
    for f in a["figures"]:           # the text of each figure is its computed value, rounded the way it is written
        digits = re.sub(r"[^\d.]", "", f["shown"])
        tol = 0.5 * 10 ** -(len(digits.split(".")[1]) if "." in digits else 0)
        assert any(abs(float(digits) - c) <= tol + 1e-9 for c in (abs(f["value"]), abs(f["value"]) * 100)), f
    return a


# ------------------------------------------------------------------ each operation, with the fake AI choosing and pandas checking
def test_aggregate_sum_matches_pandas(run):
    df = run[3]
    t = ask(run, FakeAI(q(op="aggregate", measure="amount", agg="sum")), question="what is the total amount?")
    a = assert_figures_are_computed(t)
    assert a["status"] == "answered" and a["figures"][0]["value"] == pytest.approx(df["amount"].sum())
    assert a["how"]["rows_total"] == 600 and a["how"]["rows_left_out"] == 12 and any("no Amount" in r["reason"] for r in a["how"]["left_out"])


def test_group_by_average_count_and_count_distinct(run):
    df = run[3]
    t = ask(run, FakeAI(q(op="aggregate", measure="amount", agg="average", group_by=["tier"])), question="average amount per tier")
    exp = df.groupby("tier")["amount"].mean().sort_values(ascending=False)
    assert t["answer"]["table"]["rows"][0][0] == exp.index[0] and t["answer"]["table"]["rows"][0][1] == pytest.approx(exp.iloc[0])
    assert_figures_are_computed(t)
    t = ask(run, FakeAI(q(op="aggregate", agg="count", group_by=["city"])), question="orders per city")
    assert dict((r[0], r[1]) for r in t["answer"]["table"]["rows"]) == df["city"].value_counts().to_dict()
    t = ask(run, FakeAI(q(op="aggregate", measure="customer_id", agg="count_distinct", filters=[{"column": "city", "op": "eq", "value": "delhi"}])), question="how many customers in delhi?")
    assert t["answer"]["figures"][0]["value"] == df[df.city == "Delhi"]["customer_id"].nunique()
    assert t["answer"]["how"]["filters"] == ["City is Delhi"]


def test_top_and_bottom_n(run):
    df = run[3]
    exp = df.groupby("city")["amount"].sum().sort_values(ascending=False)
    t = ask(run, FakeAI(q(op="top", measure="amount", agg="sum", group_by=["city"], n=2)), question="top 2 cities")
    assert [r[0] for r in t["answer"]["table"]["rows"]] == list(exp.index[:2]) and t["answer"]["text"].startswith("Top 2 City")
    assert_figures_are_computed(t)
    t = ask(run, FakeAI(q(op="top", measure="amount", agg="sum", group_by=["city"], n=2, direction="bottom")), question="bottom 2 cities")
    assert [r[0] for r in t["answer"]["table"]["rows"]] == list(exp.index[::-1][:2])


def test_share_of_total(run):
    df = run[3]
    t = ask(run, FakeAI(q(op="share", measure="amount", agg="sum", group_by=["tier"])), question="share by tier")
    exp = df.groupby("tier")["amount"].sum()
    exp = (exp / exp.sum() * 100).sort_values(ascending=False)
    assert t["answer"]["table"]["rows"][0][0] == exp.index[0] and t["answer"]["table"]["rows"][0][2] == pytest.approx(exp.iloc[0])
    assert sum(r[2] for r in t["answer"]["table"]["rows"]) == pytest.approx(100)
    assert t["answer"]["chart"]["type"] == "share" and t["answer"]["chart"]["path"]
    assert_figures_are_computed(t)


def test_compare_two_groups(run):
    df = run[3]
    t = ask(run, FakeAI(q(op="compare", measure="amount", agg="sum", by="city", a="delhi", b="Mumbai")), question="delhi vs mumbai")
    d, m = df[df.city == "Delhi"]["amount"].sum(), df[df.city == "Mumbai"]["amount"].sum()
    rows = t["answer"]["table"]["rows"]
    assert rows[0][1] == pytest.approx(d) and rows[1][1] == pytest.approx(m) and rows[2][1] == pytest.approx(d - m)
    assert f"{abs(d - m) / m * 100:.1f}%" in t["answer"]["text"]
    assert_figures_are_computed(t)


def test_change_between_two_periods(run):
    df = run[3]
    t = ask(run, FakeAI(q(op="period_change", measure="amount", agg="sum", a="2023-Q1", b="2023-Q2")), question="change from Q1 to Q2")
    a = df[(df.order_date >= "2023-01-01") & (df.order_date < "2023-04-01")]["amount"].sum()
    b = df[(df.order_date >= "2023-04-01") & (df.order_date < "2023-07-01")]["amount"].sum()
    r = t["answer"]["table"]["rows"][0]
    assert r[1] == pytest.approx(a) and r[2] == pytest.approx(b) and r[3] == pytest.approx(b - a) and r[4] == pytest.approx((b - a) / a * 100)
    assert_figures_are_computed(t)
    t = ask(run, FakeAI(q(op="period_change", measure="amount", agg="sum", a="2023", b="2024", group_by=["city"])), question="by city 2023 to 2024")
    assert t["answer"]["status"] == "answered" and any("part of 2024" in n for n in t["answer"]["how"]["notes"])        # the data stops in mid 2024: said, not hidden


def test_trend_over_time_and_grain(run):
    df = run[3]
    t = ask(run, FakeAI(q(op="trend", measure="amount", agg="sum", grain="month")), question="monthly amount")
    exp = df.groupby(df.order_date.dt.to_period("M"))["amount"].sum()
    assert len(t["answer"]["table"]["rows"]) == len(exp) and t["answer"]["table"]["rows"][3][1] == pytest.approx(exp.iloc[3])
    assert t["answer"]["chart"]["type"] == "line" and any("incomplete" in n for n in t["answer"]["how"]["notes"])
    t = ask(run, FakeAI(q(op="trend", measure="amount", agg="sum", grain="quarter", split_by=["tier"])), question="quarterly by tier")
    assert t["answer"]["table"]["columns"][0] == "Period" and len(t["answer"]["table"]["columns"]) == 4


def test_distribution(run):
    df = run[3]
    t = ask(run, FakeAI(q(op="distribution", column="amount", bins=8)), question="how is amount spread?")
    v = df["amount"].dropna()
    assert sum(r[1] for r in t["answer"]["table"]["rows"]) == len(v) and len(t["answer"]["table"]["rows"]) == 8
    assert f"{v.median():,.0f}" in t["answer"]["text"] or f"{v.median():,.2f}" in t["answer"]["text"]
    t = ask(run, FakeAI(q(op="distribution", column="payment")), question="payment methods")
    assert t["answer"]["table"]["rows"][0][1] == df["payment"].value_counts().iloc[0]


def test_relationship_between_two_measures(run):
    df = run[3]
    t = ask(run, FakeAI(q(op="relationship", x="amount", y="discount")), question="do amount and discount move together?")
    d = df.dropna(subset=["amount"])
    assert t["answer"]["table"]["rows"][0][1] == pytest.approx(np.corrcoef(d["amount"], d["discount"])[0, 1]) and t["answer"]["chart"]["type"] == "scatter"
    assert t["answer"]["how"]["rows_used"] == len(d)


# ------------------------------------------------------------------ the checks
@pytest.mark.parametrize("bad,words", [
    ({"op": "aggregate", "measure": "profit", "agg": "sum"}, "don't have a column"),
    ({"op": "aggregate", "measure": "city", "agg": "sum"}, "can't be added up"),
    ({"op": "top", "measure": "amount", "agg": "sum", "group_by": ["amount"]}, "can't be used for grouping"),
    ({"op": "aggregate", "measure": "amount", "agg": "sum", "filters": [{"column": "city", "value": "Atlantis"}]}, "couldn't find “Atlantis”"),
    ({"op": "period_change", "measure": "amount", "agg": "sum", "a": "2019", "b": "2023"}, "no data for 2019"),
    ({"op": "forecast", "measure": "amount"}, "one of the calculations"),
    ({"op": "trend", "measure": "amount", "agg": "sum", "date": "city"}, "can't be used for time"),
])
def test_invalid_queries_are_rejected_with_a_friendly_message(run, bad, words):
    t = ask(run, FakeAI(q(**bad)), question="something")
    a = t["answer"]
    assert a["status"] == "no_fit" and words in a["text"] and len(a["chips"]) == 2 and not a.get("table")


def test_a_rejected_query_gets_one_corrected_try_then_stops(run):
    ai = FakeAI(q(op="aggregate", measure="sales", agg="sum"), q(op="aggregate", measure="amount", agg="sum"))
    t = ask(run, ai, question="total sales")
    assert t["answer"]["status"] == "answered" and ai.calls == 2 and "don't have a column called “sales”" in ai.prompts[1]
    ai = FakeAI(q(op="aggregate", measure="sales", agg="sum"))
    t = ask(run, ai, question="total sales")
    assert t["answer"]["status"] == "no_fit" and ai.calls == 2        # never a third call


def test_at_most_two_ai_calls_per_question(run):
    ai = FakeAI("this is not json", "still not json", "never reached")
    t = ask(run, ai, question="anything")
    assert ai.calls == 2 and t["answer"]["status"] == "unavailable"
    ai = FakeAI("not json", q(op="aggregate", measure="amount", agg="sum"))
    assert ask(run, ai, question="total")["answer"]["status"] == "answered" and ai.calls == 2


def test_a_reply_with_the_wrong_action_label_is_still_read_by_what_it_carries(run):
    body = dict(op="top", measure="amount", agg="sum", group_by=["city"], n=1, direction="top")
    for reply in ({"action": "top", "query": body}, dict(body), {"query": body}):
        ai = FakeAI(reply)
        t = ask(run, ai, question="which city is best?")
        assert t["answer"]["status"] == "answered" and ai.calls == 1 and t["answer"]["text"].startswith("Top City by total Amount")


def test_unsupported_question_says_so_and_offers_two(run):
    t = ask(run, FakeAI({"action": "unsupported", "reason": "I cannot predict the future"}), question="what will sales be next year?")
    a = t["answer"]
    assert a["status"] == "no_fit" and "can't answer that from this data" in a["text"] and len(a["chips"]) == 2


# ------------------------------------------------------------------ follow-ups, clarifying, chips
def test_follow_up_changes_only_what_was_asked(run):
    df = run[3]
    first = ask(run, FakeAI(q(op="top", measure="amount", agg="sum", group_by=["tier"], n=3)), question="top tiers by amount")
    ai = FakeAI(q(op="top", measure="amount", agg="sum", group_by=["tier"], n=3, filters=[{"column": "city", "op": "eq", "value": "delhi"}]))
    second = ask(run, ai, question="and for Delhi?")
    prev = ai.prompts[0].split("PREVIOUS TURNS")[1]
    assert first["answer"]["query"]["group_by"] == ["tier"] and '"top"' in prev and "top tiers by amount" in prev        # the model is shown the earlier validated query
    exp = df[df.city == "Delhi"].groupby("tier")["amount"].sum().sort_values(ascending=False)
    assert second["answer"]["status"] == "answered" and second["answer"]["table"]["rows"][0][1] == pytest.approx(exp.iloc[0])
    assert second["answer"]["how"]["filters"] == ["City is Delhi"]
    third = ask(run, FakeAI(q(op="trend", measure="amount", agg="sum", grain="month")), question="make it monthly")
    assert third["answer"]["query"]["grain"] == "month"


def test_clarifying_question_has_choices_and_a_choice_runs_without_the_ai(run):
    df = run[3]
    c1, c2 = (dict(op="aggregate", measure=m, agg="sum") for m in ("amount", "discount"))
    ai = FakeAI({"action": "clarify", "question": "Do you mean the order amount or the discount?", "choices": [{"label": "Order amount", "query": c1}, {"label": "Discount", "query": c2}]})
    t = ask(run, ai, question="what are the sales?")
    a = t["answer"]
    assert a["status"] == "clarify" and a["clarify"]["question"].endswith("?") and [c["label"] for c in a["clarify"]["choices"]] == ["Order amount", "Discount"]
    follow = ask(run, None, choice={"turn": t["id"], "choice": a["clarify"]["choices"][1]["id"]})
    assert follow["answer"]["status"] == "answered" and follow["answer"]["figures"][0]["value"] == pytest.approx(df["discount"].sum()) and follow["answer"]["ai_calls"] == 0


def test_a_value_found_in_two_columns_is_a_clarifying_question(run):
    t = ask(run, FakeAI(q(op="aggregate", measure="amount", agg="sum", filters=[{"column": "city", "op": "eq", "value": "Zzz"}])), question="x")
    assert t["answer"]["status"] == "no_fit"
    df = run[3].copy()
    df["warehouse"] = df["city"]
    rid, wd, result = make_run(df, {**UNDERSTANDING, "columns": {**UNDERSTANDING["columns"], "warehouse": {"role": "category", "meaning": "w", "display_name": "Warehouse"}}})
    t = chat.ask(wd, result, llm=FakeAI(q(op="aggregate", measure="amount", agg="sum", filters=[{"column": "payment", "op": "eq", "value": "Mumbai"}])), question="mumbai total")
    assert t["answer"]["status"] == "clarify" and len(t["answer"]["clarify"]["choices"]) == 3
    pick = t["answer"]["clarify"]["choices"][0]
    follow = chat.ask(wd, result, choice={"turn": t["id"], "choice": pick["id"]}, llm=None, allow_ai=False)
    assert follow["answer"]["status"] == "answered" and len(follow["answer"]["how"]["filters"]) == 1


def test_a_suggested_question_is_answered_without_the_ai(run, monkeypatch):
    monkeypatch.setattr(llm, "get_llm", lambda: (_ for _ in ()).throw(AssertionError("a chip called the model")))
    rid, wd, result, df = run
    cs = chat.info(wd, result, False)["chips"]
    assert len(cs) >= 6
    for c in cs:
        t = chat.ask(wd, result, chip=c["id"], llm=None, allow_ai=False)
        assert t["answer"]["status"] == "answered", (c, t["answer"]["text"])
        assert t["answer"]["how"]["columns"] and t["answer"]["ai_calls"] == 0 and 2 <= len(t["answer"]["chips"]) <= 3
        assert_figures_are_computed(t)
    t = chat.ask(wd, result, chip=cs[0]["id"], llm=None, allow_ai=False)
    exp = df.groupby("city")["amount"].sum().sort_values(ascending=False)
    assert t["answer"]["table"]["rows"][0][0] == exp.index[0]


def test_typed_text_that_matches_a_chip_runs_it_and_other_text_offers_the_chips(run):
    rid, wd, result, df = run
    chip = chat.info(wd, result, False)["chips"][0]
    ai = FakeAI({"action": "unsupported", "reason": ""})
    t = chat.ask(wd, result, question=chip["text"].lower().rstrip("?"), llm=ai, allow_ai=True)
    assert t["answer"]["status"] == "answered" and ai.calls == 0
    t = chat.ask(wd, result, question="what is the weather like", llm=None, allow_ai=False)
    assert t["answer"]["status"] == "unavailable" and len(t["answer"]["chips"]) >= 6 and "suggested questions" in t["answer"]["text"]


def test_chart_controls_are_applied_by_code(run):
    first = ask(run, None, chip=chat.info(run[1], run[2], False)["chips"][0]["id"])
    a = first["answer"]
    assert a["chart"]["type"] == "bar" and "table" in a["controls"]["chart_types"] and "line" in a["controls"]["chart_types"]
    t = ask(run, None, patch={"turn": first["id"], "set": {"chart": "table"}})
    assert t["answer"]["chart"]["type"] == "table" and t["answer"]["chart"]["path"] is None and t["answer"]["table"]["rows"] == a["table"]["rows"]
    t = ask(run, None, patch={"turn": first["id"], "set": {"chart": "scatter"}})
    assert t["answer"]["chart"]["type"] == "bar" and "scatter" in t["answer"]["chart"]["note"]       # asked for something that does not fit: a plain note, not an error
    tr = ask(run, FakeAI(q(op="trend", measure="amount", agg="sum", grain="month")), question="monthly amount")
    t = ask(run, None, patch={"turn": tr["id"], "set": {"grain": "quarter"}})
    assert t["answer"]["query"]["grain"] == "quarter" and len(t["answer"]["table"]["rows"]) < len(tr["answer"]["table"]["rows"])


@pytest.mark.parametrize("chart,shape_ok", [("bar", 1), ("line", 1), ("stacked_bar", 1), ("share", 1), ("table", 1)])
def test_every_chart_type_draws(run, chart, shape_ok):
    for raw in (q(op="aggregate", measure="amount", agg="sum", group_by=["city"], chart=chart), q(op="trend", measure="amount", agg="sum", grain="month", split_by=["tier"], chart=chart),
                q(op="aggregate", measure="amount", agg="sum", group_by=["city"], split_by=["tier"], chart=chart)):
        t = ask(run, FakeAI(raw), question="draw")
        c = t["answer"]["chart"]
        assert t["answer"]["status"] == "answered" and (c["type"] == "table") == (c["path"] is None)
        if c["path"]:
            assert (run[1] / c["path"]).read_bytes()[:4] == b"\x89PNG"


# ------------------------------------------------------------------ the margin rule
def test_margin_without_a_cost_column_is_not_computed(run):
    ai = FakeAI(q(op="aggregate", measure="amount", agg="sum"))
    t = ask(run, ai, question="What is our profit margin by city?")
    assert t["answer"]["status"] == "no_fit" and t["answer"]["text"].startswith("Margin can't be computed because the data has no cost column.") and ai.calls == 0 and not t["answer"].get("table")
    t = ask(run, ai, question="How much profit did we make?")
    assert "margin can't be computed either" in t["answer"]["text"] and "no cost column" in t["answer"]["text"] and ai.calls == 0


def test_margin_is_answered_when_the_data_has_a_profit_column():
    df = make_df()
    df["profit"] = (df["amount"] * 0.2).round(2)
    rid, wd, result = make_run(df, {**UNDERSTANDING, "columns": {**UNDERSTANDING["columns"], "profit": {"role": "measure", "meaning": "profit on the order", "display_name": "Profit", "measure_kind": "money"}}},
                               {"derived_metrics": {"created": [{"kind": "ratio_of_sums", "name": "profit_margin", "a": "profit", "b": "amount", "meaning": "profit as a share of amount", "kpi": "profit margin"}]}})
    d2 = pd.read_pickle(wd / "clean.pkl")
    d2["profit_margin"] = d2["profit"] / d2["amount"]
    d2.to_pickle(wd / "clean.pkl")
    t = chat.ask(wd, result, llm=FakeAI(q(op="aggregate", measure="profit_margin", agg="sum", group_by=["city"])), question="profit margin by city")
    a = t["answer"]
    exp = d2.groupby("city").apply(lambda g: g["profit"].sum() / g["amount"].sum(), include_groups=False).sort_values(ascending=False)
    assert a["status"] == "answered" and a["table"]["rows"][0][1] == pytest.approx(exp.iloc[0])       # a ratio of totals, never a sum or an average of row ratios
    assert a["table"]["shown"][0][1] == f"{exp.iloc[0] * 100:.1f}%"


# ------------------------------------------------------------------ what the AI sees, and how much it may cost
def test_the_ai_gets_the_understanding_record_and_tab_but_never_rows(run):
    ai = FakeAI(q(op="aggregate", measure="amount", agg="sum"))
    ask(run, ai, question="total?", tab="Dashboard")
    p = ai.prompts[0]
    assert "CURRENT TAB: Dashboard" in p and "membership level" in p and '"label": "Tier"' in p and "money" in p
    df = run[3]
    for city in df.city.unique():
        assert city not in p
    assert df["customer_id"].iloc[0] not in p and "Gold" not in p


def test_only_the_last_six_turns_are_passed(run):
    ai = FakeAI(q(op="aggregate", measure="amount", agg="sum"))
    for i in range(8):
        ask(run, ai, question=f"question number {i + 1:02d} about totals")
    prev = ai.prompts[-1].split("PREVIOUS TURNS")[1].split("QUESTION:")[0]
    assert prev.count('"question"') == 6 and "number 07" in prev and "number 02" in prev and "number 01" not in prev


def test_question_cap_per_run(run, monkeypatch):
    monkeypatch.setenv("LENS_MAX_QUESTIONS", "2")
    ai = FakeAI(q(op="aggregate", measure="amount", agg="sum"))
    assert ask(run, ai, question="one")["answer"]["status"] == "answered"
    assert ask(run, ai, question="two please")["answer"]["status"] == "answered"
    t = ask(run, ai, question="three please")
    assert t["answer"]["status"] == "limit" and "limit of 2 questions" in t["answer"]["text"] and ai.calls == 2
    assert ask(run, None, chip=chat.info(run[1], run[2], False)["chips"][0]["id"])["answer"]["status"] == "answered"     # suggested questions never cost anything
    assert chat.info(run[1], run[2], False)["limit"] == {"asked": 2, "max": 2}


def test_rate_limit_and_daily_limit_are_said_plainly(run):
    ai = FakeAI(Exception("RateLimitError 429 Too Many Requests: rate limit reached"))
    t = ask(run, ai, question="total?")
    assert t["answer"]["status"] == "unavailable" and "busy right now" in t["answer"]["text"] and ai.calls == llm.RATE_LIMIT_RETRIES + 1
    ai = FakeAI(Exception("RateLimitError 429: tokens per day (TPD) limit reached"))
    t = ask(run, ai, question="total again?")
    assert "daily limit" in t["answer"]["text"] and ai.calls == 1
    ai2 = FakeAI(q(op="aggregate", measure="amount", agg="sum"))
    t = ask(run, ai2, question="and once more?")
    assert "daily limit" in t["answer"]["text"] and ai2.calls == 0       # nothing is sent until the limit has reset


def test_the_ai_cache_is_used(run, monkeypatch, tmp_path):
    monkeypatch.setenv("AI_CACHE", "on")
    ai = FakeAI(q(op="aggregate", measure="amount", agg="sum"))
    ask(run, ai, question="a question that is asked twice")
    rid, wd, result, df = run
    store_path = wd / "lens.json"
    store_path.unlink()
    ask(run, ai, question="a question that is asked twice")
    assert ai.calls == 1


def test_flag_modes(monkeypatch):
    monkeypatch.setattr(llm, "get_llm", lambda: (None, "rules-only"))
    monkeypatch.delenv("CHATBOT", raising=False)
    assert lens.mode() == "auto" and lens.enabled(demo=True) and not lens.enabled(demo=False)
    monkeypatch.setattr(llm, "get_llm", lambda: (object(), "fake"))
    assert lens.enabled(demo=False)
    monkeypatch.setenv("CHATBOT", "off")
    assert not lens.enabled(demo=True) and not lens.enabled(demo=False)
    monkeypatch.setenv("CHATBOT", "on")
    monkeypatch.setattr(llm, "get_llm", lambda: (None, "rules-only"))
    assert lens.enabled(demo=False)


# ------------------------------------------------------------------ saved with the run: history, pins, the original report untouched
def test_history_survives_a_reload(run):
    rid, wd, result, df = run
    ask(run, FakeAI(q(op="aggregate", measure="amount", agg="sum")), question="total amount")
    ask(run, None, chip=chat.info(wd, result, False)["chips"][0]["id"])
    chat._FRAMES.clear()                      # a new process: nothing in memory
    info = client.get(f"/api/runs/{rid}/lens").json()
    assert [t["question"] for t in info["history"]][0] == "total amount" and len(info["history"]) == 2
    assert info["history"][0]["answer"]["chart"]["url"] == "" and info["history"][1]["answer"]["chart"]["url"].startswith(f"/api/runs/{rid}/files/charts/lens_")
    assert info["limit"]["asked"] == 1 and info["history"][1]["answer"]["table"]["rows"]


def test_pin_adds_a_section_item_and_never_changes_the_original_report(run):
    rid, wd, result, df = run
    t = ask(run, None, chip=chat.info(wd, result, False)["chips"][0]["id"])
    before = {f: hashlib.sha256((wd / f).read_bytes()).hexdigest() for f in ("result.json", "report.md")}
    r = client.post(f"/api/runs/{rid}/lens/pins", json={"turn": t["id"], "target": "report"})
    assert r.status_code == 200 and r.json()["text"] == t["answer"]["text"]
    assert client.post(f"/api/runs/{rid}/lens/pins", json={"turn": t["id"], "target": "dashboard"}).status_code == 200
    assert client.post(f"/api/runs/{rid}/lens/pins", json={"turn": t["id"], "target": "report"}).json()["id"] == r.json()["id"]       # pinning twice is one pin
    info = client.get(f"/api/runs/{rid}/lens").json()
    assert sorted(p["target"] for p in info["pins"]) == ["dashboard", "report"] and info["pins"][0]["chart"]["url"]
    assert before == {f: hashlib.sha256((wd / f).read_bytes()).hexdigest() for f in before}
    assert client.get(f"/api/runs/{rid}").json()["report_md"] == result["report_md"]
    assert client.delete(f"/api/runs/{rid}/lens/pins/{r.json()['id']}").status_code == 200
    assert [p["target"] for p in client.get(f"/api/runs/{rid}/lens").json()["pins"]] == ["dashboard"]
    assert before == {f: hashlib.sha256((wd / f).read_bytes()).hexdigest() for f in before}
    bad = ask(run, FakeAI(q(op="aggregate", measure="sales", agg="sum")), question="nothing")
    assert client.post(f"/api/runs/{rid}/lens/pins", json={"turn": bad["id"], "target": "report"}).status_code == 404       # only an answer can be pinned


def test_api_end_to_end_with_a_fake_model(run, monkeypatch):
    rid, wd, result, df = run
    import app.main as main
    monkeypatch.setattr(main, "lens_llm", lambda: FakeAI(q(op="top", measure="amount", agg="sum", group_by=["city"], n=2)))
    r = client.post(f"/api/runs/{rid}/lens/ask", json={"question": "best two cities by amount", "tab": "Report"})
    assert r.status_code == 200
    a = r.json()["answer"]
    assert a["status"] == "answered" and a["chart"]["url"].startswith(f"/api/runs/{rid}/files/charts/lens_") and client.get(a["chart"]["url"]).status_code == 200
    assert client.get(f"/api/health").json()["chatbot"] == "on"


def test_off_flag_hides_lens(run, monkeypatch):
    rid = run[0]
    monkeypatch.setenv("CHATBOT", "off")
    assert client.get(f"/api/runs/{rid}/lens").json()["enabled"] is False
    assert client.post(f"/api/runs/{rid}/lens/ask", json={"question": "x"}).status_code == 404


# ------------------------------------------------------------------ the sample report: always on, no AI
def test_sample_report_chat_works_without_a_key(monkeypatch):
    monkeypatch.delenv("CHATBOT", raising=False)
    monkeypatch.setattr(llm, "get_llm", lambda: (None, "rules-only"))
    runner.ensure_demo_run()
    rid = runner.DEMO_RUN_ID
    info = client.get(f"/api/runs/{rid}/lens").json()
    assert info["enabled"] and not info["ai"] and len(info["chips"]) >= 4
    for c in info["chips"]:
        r = client.post(f"/api/runs/{rid}/lens/ask", json={"chip": c["id"]})
        assert r.status_code == 200 and r.json()["answer"]["status"] == "answered", c
    r = client.post(f"/api/runs/{rid}/lens/ask", json={"question": "what is the weather like"})
    assert r.json()["answer"]["status"] == "unavailable"
    fake = FakeAI(q(op="aggregate", measure="revenue", agg="sum"))
    import app.main as main
    monkeypatch.setattr(main, "lens_llm", lambda: fake)
    client.post(f"/api/runs/{rid}/lens/ask", json={"question": "something unrelated entirely"})
    assert fake.calls == 0                               # the sample never reaches the model, even when one exists
