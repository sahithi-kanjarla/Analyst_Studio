"""Lens, Stage 2A: premise check, 'why' breakdowns, top-N of fewer, relevant suggestions, a 'How this was calculated' box that is never empty, questions about the app and the
report, the short version, ranking by what matters to the user, suggestions from the report's strategies, and general knowledge.
The AI is a fake one that chooses; every figure is compared with pandas or with the report's checked fact list."""
import json
import re

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import llm
from app.lens import chat, howtext, reasoning, records, reports, store
from app.main import app
from tests.test_lens import FakeAI, UNDERSTANDING, assert_figures_are_computed, make_run, numbers, q

client = TestClient(app)
CAUSAL = re.compile(r"\b(because|due to|caused?|driven by|as a result of|the reason is|thanks to)\b", re.I)

BASE = {"Clothing": 1500.0, "Electronics": 1300.0, "Home": 600.0}


def shop_df():
    rng = np.random.default_rng(3)
    n = 480
    cat = rng.choice(list(BASE), n, p=[0.4, 0.35, 0.25])
    df = pd.DataFrame({
        "order_date": pd.to_datetime("2023-01-01") + pd.to_timedelta(rng.integers(0, 600, n), unit="D"),
        "category": cat,
        "region": rng.choice(["North", "South"], n),
        "tier": rng.choice(["Gold", "Silver"], n),
        "amount": (np.array([BASE[c] for c in cat]) * rng.uniform(0.8, 1.2, n)).round(2),
        "customer_id": ["C%03d" % i for i in rng.integers(0, 90, n)],
    })
    return df


UND = {"source": "llm", "goal_columns": {}, "descriptive": [], "unmapped": [], "retries": 0, "error": "", "columns": {
    "order_date": {"role": "date", "meaning": "when the order was placed", "display_name": "Order date"},
    "category": {"role": "category", "meaning": "product category", "display_name": "Category"},
    "region": {"role": "category", "meaning": "sales region", "display_name": "Region"},
    "tier": {"role": "category", "meaning": "membership level", "display_name": "Tier"},
    "amount": {"role": "measure", "meaning": "order value", "display_name": "Amount", "measure_kind": "money"},
    "customer_id": {"role": "id", "meaning": "customer", "display_name": "Customer"}}}

REPORT = {
    "goal": "Which category earns most, and what should we do about returns?",
    "context": {"kpis": ["revenue", "returns"]},
    "report_md": "# Report\nThe original report.",
    "report_view": {
        "page": {"trust": "Medium", "cards": [{"question": "Which category earns most?", "trust": "Medium", "trust_why": "the growth figure is not statistically reliable"}]},
        "headlines": [
            {"id": "r1", "text": "By total amount, category Clothing is highest at 1,000,000 (40.0% of the total amount) and category Home is lowest at 300,000"},
            {"id": "r2", "text": "Return rate by region: South 12.0%, North 8.0%"}],
        "evidence": [
            {"id": "f1", "headline": "Returns rose 12% over the period", "observation": "Return rate rose 12% from the first to the last month.", "why_it_matters": "Returns cost money.",
             "title": "return rate over time", "tier": 1, "confidence": {"label": "Medium"}},
            {"id": "f2", "headline": "Discounts are higher in the North (average 8.5 against 6.1)", "observation": "Average discount 8.5 in the North and 6.1 in the South.", "why_it_matters": "Discounts reduce income.",
             "title": "discount by region", "tier": 2, "confidence": {"label": "High"}}],
        "actions": [
            {"text": "Review the returns policy for Home", "evidence": ["f1"], "figure": "Returns +12%"},
            {"text": "Cap discounts in the North", "evidence": ["f2"], "figure": ""},
            {"text": "Keep Clothing in stock", "evidence": ["r1"], "figure": ""},
            {"text": "A fourth action", "evidence": [], "figure": ""}]},
    "summary_facts": {"rankings": [
        {"id": "r1", "tool": "ranking", "basis": "total", "group": "category", "measure": "amount", "top": "Clothing", "bottom": "Home", "top_share": 0.4,
         "text": "By total amount, category Clothing is highest at 1,000,000 (40.0% of the total amount) and category Home is lowest at 300,000", "short": "Clothing leads on total amount with 40.0% of the total"},
        {"id": "r2", "tool": "ranking", "basis": "rate", "group": "region", "measure": "return rate", "top": "South", "bottom": "North", "text": "Return rate by region: South 12.0%, North 8.0%"}]},
    "findings": [
        {"id": "f1", "tool": "trend", "title": "return rate over time", "text": "**return rate** rose 12% over the period", "stats": {"kind": "trend", "pct": 12.0}, "params": {"target": "return rate"}},
        {"id": "f2", "tool": "group_compare", "title": "discount by region", "text": "Average **discount** is highest in the North (8.5) and lowest in the South (6.1)", "stats": {"kind": "numeric", "best": "North"}, "params": {"group": "region"}}],
    "strategies": [
        {"id": "s1", "kind": "data", "title": "Reduce returns in Home", "objective": "Cut the return rate, which rose over the period", "actions": ["Audit the product pages", "Check the sizing information"], "risks": [],
         "kpis": ["return rate"], "impact": "High", "confidence": "medium", "effort": "Low", "evidence": ["f1"], "rationale": "return rate rose 12%", "monitor": False},
        {"id": "s2", "kind": "data", "title": "Tighten discounts in the North", "objective": "Bring North discounts closer to the South", "actions": ["Set a discount cap"], "risks": [], "kpis": ["average discount"],
         "impact": "Medium", "confidence": "high", "effort": "Low", "evidence": ["f2"], "rationale": "North discounts are higher", "monitor": False}],
    "strategy_summary": "2 data-backed strategies follow from the evidence.",
    "cleaning_log": [{"step": "Merge labels", "detail": "merged 3 spelling variants in region; 18 values relabelled", "count": 18},
                     {"step": "Convert to date", "detail": "'order_date' parsed as dates", "count": 480}],
    "applied_definitions": [{"id": "d1", "question": "How should the value 999 in delivery_days be treated?", "answer": "missing", "answer_label": "As missing (default)",
                             "effect": "45 values of 999 in delivery_days were set to missing.", "rows": 45}],
}


@pytest.fixture
def shop(monkeypatch):
    monkeypatch.setenv("CHATBOT", "on")
    chat._DAILY["until"] = 0.0
    rid, wd, result = make_run(shop_df(), UND, REPORT)
    return rid, wd, result, pd.read_pickle(wd / "clean.pkl")


def ask(shop, ai=None, **kw):
    rid, wd, result, df = shop
    return chat.ask(wd, result, llm=ai, allow_ai=ai is not None, **kw)


def top_cat(df):
    return df.groupby("category")["amount"].sum().sort_values(ascending=False)


def lines_ok(a):
    """The 'How this was calculated' box has content: what was done, where it came from or which columns/filters/rows, and the closing line."""
    ls = howtext.lines(a["how"])
    assert len(ls) >= 3 and all(isinstance(x, str) and x.strip() for x in ls), ls
    assert a["how"].get("lines") == ls
    if "rows_total" in a["how"]:
        joined = " ".join(ls)
        assert "Columns used" in joined and "Filters" in joined and "Rows:" in joined and "left out" in joined
    else:
        assert any(x.startswith("Taken from") for x in ls)
    return ls


# ------------------------------------------------------------------ fix 1: a premise the data contradicts is corrected first
def test_premise_is_corrected_first_with_the_computed_figures(shop):
    df = shop[3]
    s = top_cat(df)
    assert list(s.index[:2]) == ["Clothing", "Electronics"]
    ai = FakeAI(q(op="top", measure="amount", agg="sum", group_by=["category"], n=3, direction="top"))
    t = ask(shop, ai, question="why is Electronics the top, it should be Clothing?")
    a = assert_figures_are_computed(t)
    assert a["status"] == "answered"
    assert a["text"].startswith("Correction first: Electronics is not the top category by total amount: Clothing is, at ")
    assert f"{s['Clothing']:,.0f}" in a["text"] and f"{s['Electronics']:,.0f}" in a["text"] and "Electronics is second" in a["text"]
    assert a["premise"]["claims"][0]["leader"] == "Clothing"
    assert "The data alone can't show why" in a["text"] and not CAUSAL.search(a["text"])        # then the rest is answered: the breakdown, no cause
    assert ai.calls == 1 and lines_ok(a)
    assert any("Checked your question's assumption" in n for n in a["how"]["notes"])


def test_a_true_premise_is_not_corrected_and_wishes_are_not_claims(shop):
    df = shop[3]
    assert reasoning.claims("why is Electronics the top, it should be Clothing?", df, chat.load_data(shop[1], shop[2])[1]) == [
        {"column": "category", "value": "Electronics", "direction": "up"}]
    ai = FakeAI(q(op="top", measure="amount", agg="sum", group_by=["category"], n=3, direction="top"))
    t = ask(shop, ai, question="why is Clothing the top category?")
    assert "Correction" not in t["answer"]["text"] and "premise" not in t["answer"]


def test_a_premise_is_corrected_without_the_ai_too(shop):
    t = ask(shop, None, question="Electronics is the top category by amount, right?")
    a = t["answer"]
    assert a["status"] == "answered" and a["text"].startswith("Correction first: Electronics is not the top category")
    assert a["ai_calls"] == 0 and lines_ok(a)


# ------------------------------------------------------------------ fix 2: 'why' is answered with what is behind the number, never a cause
def test_why_gives_a_breakdown_and_never_a_cause(shop):
    df = shop[3]
    ai = FakeAI(q(op="top", measure="amount", agg="sum", group_by=["category"], n=3, direction="top"))
    t = ask(shop, ai, question="why is Clothing the biggest category?")
    a = assert_figures_are_computed(t)
    assert a["status"] == "answered" and a["kind"] == "why"
    assert a["text"].startswith("The data alone can't show why this is the case") and not CAUSAL.search(a["text"])
    s = top_cat(df)
    share = s["Clothing"] / s.sum() * 100
    assert f"{share:.1f}%" in a["text"]                                      # share of total, from code
    assert a["query"]["op"] == "aggregate" and a["query"]["split_by"] and a["query"]["split_by"][0] in ("region", "tier")
    assert a["chart"]["path"]                                                 # drawn
    assert 2 <= len(a["chips"]) <= 3                                          # offered follow-ups from the breakdown
    texts = " ".join(c["text"] for c in a["chips"])
    assert "change from" in texts or "contributes most" in texts
    assert ai.calls == 1 and lines_ok(a)


def test_why_still_works_when_the_ai_says_unsupported_but_never_for_an_unrelated_question(shop):
    ask(shop, FakeAI(q(op="top", measure="amount", agg="sum", group_by=["category"], n=3, direction="top")), question="which category is top?")
    t = ask(shop, FakeAI({"action": "unsupported", "reason": "causes cannot be known"}), question="why is Clothing ahead?")
    assert t["answer"]["status"] == "answered" and t["answer"]["kind"] == "why"
    fresh = make_run(shop_df(), UND, REPORT)
    t2 = chat.ask(fresh[1], fresh[2], question="why is the sky blue?", llm=FakeAI({"action": "unsupported", "reason": "not about this data"}), allow_ai=True)
    assert t2["answer"]["status"] == "no_fit"


# ------------------------------------------------------------------ fix 3: fewer groups than asked
def test_top_n_with_fewer_groups_says_how_many_there_are(shop):
    df = shop[3]
    t = ask(shop, FakeAI(q(op="top", measure="amount", agg="sum", group_by=["category"], n=10, direction="top")), question="top 10 categories by amount")
    a = assert_figures_are_computed(t)
    assert a["text"].startswith("There are only 3 categories")
    assert len(a["table"]["rows"]) == 3 and a["table"]["rows"][0][0] == top_cat(df).index[0]
    t2 = ask(shop, FakeAI(q(op="top", measure="amount", agg="sum", group_by=["category"], n=3, direction="top")), question="top 3 categories")
    assert "There are only" not in t2["answer"]["text"]


def test_plural_words():
    from app.lens.engine import plural
    assert [plural(x) for x in ("Category", "City", "Tier", "Sub category", "Status", "Day")] == ["categories", "cities", "tiers", "sub categories", "statuses", "days"]


# ------------------------------------------------------------------ fix 4: refusals fit the topic and differ from what was just offered; a view change is not a data refusal
def test_refusal_suggestions_fit_the_topic_and_are_new(shop):
    first = ask(shop, FakeAI(q(op="top", measure="amount", agg="sum", group_by=["category"], n=3, direction="top")), question="top 3 categories")
    offered = {c["id"] for c in first["answer"]["chips"]}
    t = ask(shop, FakeAI({"action": "unsupported", "reason": "no such column"}), question="Which supplier ships fastest by region?")
    a = t["answer"]
    assert a["status"] == "no_fit" and len(a["chips"]) == 2
    assert not offered & {c["id"] for c in a["chips"]}, "the same suggestions were offered twice in a row"
    assert any("region" in c["text"].lower() for c in a["chips"]), a["chips"]
    t2 = ask(shop, FakeAI({"action": "unsupported", "reason": "no such column"}), question="Which supplier ships fastest by region?")
    assert not {c["id"] for c in a["chips"]} & {c["id"] for c in t2["answer"]["chips"]}


def test_a_view_change_is_not_refused_as_a_data_question(shop):
    ai = FakeAI({"plan": [], "unsupported": "colours and layout cannot be changed"})      # with an AI the planner is asked once (plain-word routing is now the no-AI fallback)
    for text in ("change the view to dark mode", "Can you switch the layout?", "go to the dashboard tab"):
        t = ask(shop, ai, question=text)
        a = t["answer"]
        assert a["status"] == "answered" and a["kind"] == "view" and a["ai_calls"] == 1, text
        assert "coming soon" not in a["text"] and "can't answer" not in a["text"] and "how the app looks" in a["text"]      # styling is declined; the view changes that exist are offered as chips
        assert 2 <= len(a["chips"]) <= 5 and lines_ok(a)
    assert ai.calls == 3
    for text in ("show revenue as a line chart", "make it monthly", "top 5 cities by amount"):
        assert not chat.rules.wants_view_change(text), text


# ------------------------------------------------------------------ fix 5: the calculation toggle always has content
def test_the_toggle_is_never_empty_for_any_kind_of_answer(shop):
    rid, wd, result, df = shop
    ask(shop, FakeAI(q(op="aggregate", measure="amount", agg="sum", group_by=["category"])), question="total amount by category")
    for question, ai in (("why is Electronics the top?", FakeAI(q(op="top", measure="amount", agg="sum", group_by=["category"], n=3))),
                         ("what did you clean?", None), ("why is trust Medium?", None), ("change the view", None), ("summarise this report", None),
                         ("I care about returns", None), ("what should we do about returns?", FakeAI({"action": "strategy", "focus": "returns"})),
                         ("what is a P/E ratio?", FakeAI({"action": "general", "answer": "It compares a share price with earnings."}))):
        t = ask(shop, ai, question=question)
        assert t["answer"]["status"] == "answered", (question, t["answer"]["text"])
        lines_ok(t["answer"])
    for t in store.load(wd)["turns"]:                      # and every saved answer reaches the page with its lines
        if t["answer"]["status"] == "answered":
            assert lines_ok(t["answer"])
    info = client.get(f"/api/runs/{rid}/lens").json()
    for t in info["history"]:
        if t["answer"]["status"] == "answered":
            assert t["answer"]["how"]["lines"] and all(x.strip() for x in t["answer"]["how"]["lines"])


def test_an_empty_record_still_says_something():
    ls = howtext.lines({})
    assert len(ls) >= 2 and all(x.strip() for x in ls)


# ------------------------------------------------------------------ what Lens is given (never rows)
def test_the_ai_gets_the_report_records_and_no_rows(shop):
    ai = FakeAI({"action": "app", "topic": "trust"})
    ask(shop, ai, question="is the evidence here any good, in plain words?", tab="Insights")
    p = ai.prompts[0]
    assert "REPORT RECORDS" in p and "CURRENT TAB: Insights" in p
    for needle in ("r1", "f1", "s1", "Merge labels", "As missing", "Medium", "Which category earns most"):
        assert needle in p, needle
    assert not re.search(r"C\d{3}", p), "a customer id (a row value) reached the AI"
    assert not re.search(r"2023-\d\d-\d\d \d", p)


# ------------------------------------------------------------------ capability 1: questions about the app and the report, quoted from the saved records
def test_app_questions_are_quoted_from_the_saved_records(shop):
    t = ask(shop, None, question="what did you clean?")
    a = t["answer"]
    assert a["kind"] == "app" and a["ai_calls"] == 0
    assert "Merge labels: merged 3 spelling variants in region; 18 values relabelled (18 rows)" in a["lines"]
    assert "Convert to date: 'order_date' parsed as dates (480 rows)" in a["lines"]
    assert a["how"]["sources"] == ["the cleaning log saved with this run"] and lines_ok(a) and 2 <= len(a["chips"]) <= 3
    t = ask(shop, None, question="why is trust Medium?")
    a = t["answer"]
    assert "overall trust is Medium" in a["text"] and any("the growth figure is not statistically reliable" in x for x in a["lines"])
    t = ask(shop, None, question="what does this tab show?", tab="Insights")
    assert records.TAB_HELP["insights"] in t["answer"]["text"] and t["answer"]["text"].startswith("The Insights tab")
    t = ask(shop, None, question="what does the Dashboard tab show?", tab="Insights")
    assert records.TAB_HELP["dashboard"] in t["answer"]["text"]
    t = ask(shop, None, question="which definitions did you use?")
    assert any("As missing (default)" in x and "45 values of 999" in x for x in t["answer"]["lines"])


def test_the_ai_can_choose_an_app_topic(shop):
    ai = FakeAI({"action": "app", "topic": "cleaning"})
    t = ask(shop, ai, question="Tell me about the tidy-up you did to my files")
    assert t["answer"]["kind"] == "app" and "Merge labels" in " ".join(t["answer"]["lines"]) and ai.calls == 1


# ------------------------------------------------------------------ capability 2: the short version
GOOD = "Clothing is 40.0% of the total amount [r1]."
BAD_FIGURE = "Clothing is 55.5% of the total amount [r1]."
NO_ID = "Clothing leads the category ranking."
NO_FIGURE = "Clothing is the leading category and looks promising [r1]."
CAUSE = "Returns rose 12% because customers dislike sizing [f1]."
FROM_ANOTHER = "Returns rose 40.0% over the period [f1]."
GOOD2 = "The return rate trend is up 12% [f1]."


def test_the_short_version_keeps_only_verified_lines(shop):
    rid, wd, result, df = shop
    before = (wd / "result.json").read_bytes(), (wd / "report.md").read_bytes()
    ai = FakeAI({"lines": [GOOD, BAD_FIGURE, NO_ID, NO_FIGURE, CAUSE, FROM_ANOTHER, GOOD2]})
    t = ask(shop, ai, question="Summarise this report")
    a = t["answer"]
    assert a["status"] == "answered" and a["kind"] == "summary" and a["ai_calls"] == 1 and ai.calls == 1
    assert a["lines"] == ["Clothing is 40.0% of the total amount.", "The return rate trend is up 12%."]
    assert len(a["lines"]) <= 5
    assert a["actions"] == [x["text"] for x in REPORT["report_view"]["actions"][:3]]
    assert "left out" in " ".join(a["how"]["notes"]) and "r1" in a["how"]["fact_ids"] and lines_ok(a)
    known = records.known_facts(result)
    for ln in a["lines"]:                                 # every figure in the short version is a figure of a fact in the report
        for n in numbers(ln):
            assert any(abs(float(n) - float(x)) < 0.051 for f in known.values() for x in f["numbers"]), (n, ln)
    assert a["view_offer"]["label"] == "Use as my short version" and 2 <= len(a["chips"]) <= 3
    assert before == ((wd / "result.json").read_bytes(), (wd / "report.md").read_bytes())      # the original report is untouched


def test_the_short_version_falls_back_to_the_reports_own_sentences(shop):
    ai = FakeAI({"lines": [BAD_FIGURE, NO_ID]})
    a = ask(shop, ai, question="shorten the report")["answer"]
    assert a["lines"][0] == "Clothing leads on total amount with 40.0% of the total." and len(a["lines"]) <= 3
    a = ask(shop, None, question="give me a tl;dr")["answer"]
    assert a["ai_calls"] == 0 and a["lines"] and a["actions"]


def test_the_short_version_can_be_kept_as_an_alternative_view(shop):
    rid, wd, result, df = shop
    t = ask(shop, None, question="summarise this report")
    r = client.post(f"/api/runs/{rid}/lens/views", json={"turn": t["id"]})
    assert r.status_code == 200 and r.json()["lines"] == t["answer"]["lines"]
    assert store.load(wd)["views"]["short"]["actions"] == t["answer"]["actions"]
    assert client.get(f"/api/runs/{rid}/lens").json()["views"]["short"]["lines"] == t["answer"]["lines"]
    other = ask(shop, None, question="what did you clean?")
    assert client.post(f"/api/runs/{rid}/lens/views", json={"turn": other["id"]}).status_code == 404       # only a summary can be kept
    assert (wd / "report.md").read_text(encoding="utf-8") == REPORT["report_md"]
    assert client.delete(f"/api/runs/{rid}/lens/views/short").status_code == 200 and "short" not in store.load(wd)["views"]


# ------------------------------------------------------------------ capability 3: what matters most to me
def test_ranking_follows_the_priority_and_shows_figures(shop):
    ai = FakeAI({"action": "priority", "priority": "returns", "terms": ["return", "refund"]})
    a = ask(shop, ai, question="I care about returns")["answer"]
    assert a["kind"] == "priority" and a["label"] == "Ranked for: returns" and ai.calls == 1
    assert "12%" in a["lines"][0] and "f1" in a["lines"][0] and len(a["lines"]) <= 3
    assert any("Review the returns policy" in x for x in a["lines"])
    b = ask(shop, FakeAI({"action": "priority", "priority": "discounts", "terms": ["discount"]}), question="I care about discounts")["answer"]
    assert "8.5" in b["lines"][0] and b["lines"] != a["lines"]
    assert any("Cap discounts" in x for x in b["lines"])
    for ln in a["lines"] + b["lines"]:
        assert re.search(r"\[[a-z]\d+\]", ln)
    c = ask(shop, FakeAI({"action": "priority", "priority": "shipping speed", "terms": ["shipping", "courier"]}), question="I care about shipping speed")["answer"]
    assert c["lines"] == [] and "Nothing in this report is about" in c["text"] and lines_ok(c)


def test_priority_without_a_named_priority_asks_and_works_without_ai(shop):
    a = ask(shop, None, question="What matters most for me?")["answer"]
    assert "Tell me what matters" in a["text"] and a["lines"] and any(c["text"].startswith("I care about") for c in a["chips"])
    a = ask(shop, None, question="I care about returns")["answer"]
    assert a["ai_calls"] == 0 and "12%" in a["lines"][0]


# ------------------------------------------------------------------ capability 4: strategy questions
def test_strategy_questions_reuse_the_reports_strategies(shop):
    a = ask(shop, FakeAI({"action": "strategy", "focus": "returns"}), question="what should we do about returns?")["answer"]
    assert a["label"] == "Suggestion" and a["lines"][0].startswith("Reduce returns in Home") and "Based on f1" in " ".join(a["lines"]) and lines_ok(a)
    assert "Tighten discounts" not in " ".join(a["lines"])
    b = ask(shop, FakeAI({"action": "strategy", "focus": "weather"}), question="what should we do about the weather?")["answer"]
    assert b["label"] == "No suggestion" and b["text"].startswith("No suggestion: no idea in this report relates to") and lines_ok(b)
    c = ask(shop, FakeAI({"action": "strategy", "focus": ""}), question="what should we do next?")["answer"]
    assert c["label"] == "Suggestion" and c["lines"][0].startswith("Reduce returns")        # the report's top-ranked strategy first


# ------------------------------------------------------------------ capability 5: general knowledge
def test_general_questions_are_labelled_and_carry_no_figures(shop):
    ai = FakeAI({"action": "general", "answer": "A P/E ratio compares a share price with its earnings. A typical value is 15. It is used to judge valuation."})
    a = ask(shop, ai, question="what is a P/E ratio?")["answer"]
    assert a["label"] == "General knowledge, not from your data" and a["kind"] == "general"
    assert not re.search(r"\d", a["text"]) and "compares a share price" in a["text"] and "used to judge valuation" in a["text"]
    assert lines_ok(a) and 2 <= len(a["chips"]) <= 3


# ------------------------------------------------------------------ cost and safety
def test_at_most_two_ai_calls_per_question_and_no_ai_needed_for_chips(shop, monkeypatch):
    ai = FakeAI({"action": "summary", "length": "standard"}, {"lines": [GOOD]})          # one call to choose, one to word
    t = ask(shop, ai, question="Could you boil the findings down for my boss?")
    assert ai.calls == 2 and t["answer"]["ai_calls"] == 2 and t["answer"]["kind"] == "summary"
    assert store.load(shop[1])["asked"] == 1
    ai = FakeAI("not json", "still not json")
    t = ask(shop, ai, question="Could you boil the findings down for my boss, please?")
    assert ai.calls <= 2 and t["answer"]["status"] == "unavailable"
    monkeypatch.setattr(llm, "get_llm", lambda: (_ for _ in ()).throw(AssertionError("a chip called the model")))
    cs = chat.info(shop[1], shop[2], False)["chips"]
    reportq = [c for c in cs if c["id"].startswith("i_")]
    assert len(reportq) == 2
    for c in reportq:
        a = ask(shop, None, chip=c["id"])["answer"]
        assert a["status"] == "answered" and a["ai_calls"] == 0 and lines_ok(a)


def test_the_question_cap_still_stops_the_ai_but_not_the_report_answers(shop, monkeypatch):
    monkeypatch.setenv("LENS_MAX_QUESTIONS", "1")
    ask(shop, FakeAI(q(op="aggregate", measure="amount", agg="sum")), question="what is the total amount?")
    ai = FakeAI(q(op="aggregate", measure="amount", agg="sum"))
    assert ask(shop, ai, question="and the average amount?")["answer"]["status"] == "limit" and ai.calls == 0
    a = ask(shop, ai, question="summarise this report")["answer"]
    assert a["status"] == "answered" and ai.calls == 0 and a["lines"]


def test_every_figure_of_every_new_data_answer_is_a_computed_value(shop):
    df = shop[3]
    for question, query in (("why is Electronics the top?", q(op="top", measure="amount", agg="sum", group_by=["category"], n=3)),
                            ("why did amount change?", q(op="trend", measure="amount", agg="sum")),
                            ("why is the total what it is?", q(op="aggregate", measure="amount", agg="sum")),
                            ("why is Home the lowest category?", q(op="top", measure="amount", agg="sum", group_by=["category"], n=3, direction="bottom"))):
        t = ask(shop, FakeAI(query), question=question)
        a = assert_figures_are_computed(t)
        assert a["status"] == "answered" and not CAUSAL.search(a["text"]), (question, a["text"])
        if "Correction" in a["text"]:
            s = top_cat(df)
            assert f"{s.iloc[0]:,.0f}" in a["text"]


# ------------------------------------------------------------------ found by the real-AI check
def test_a_why_or_premise_question_is_never_answered_with_a_clarifying_question(shop):
    df = shop[3]
    choices = [{"label": "Revenue", "query": q(op="top", measure="amount", agg="sum", group_by=["category"], n=3)["query"]},
               {"label": "Orders", "query": q(op="top", agg="count", group_by=["category"], n=3)["query"]}]
    ai = FakeAI({"action": "clarify", "question": "Which metric do you mean?", "choices": choices})
    a = ask(shop, ai, question="Why is Electronics the top category by revenue? It should be Clothing.")["answer"]
    assert a["status"] == "answered" and a["text"].startswith("Correction first: Electronics is not the top category") and ai.calls == 1
    ai = FakeAI({"action": "clarify", "question": "Which metric do you mean?", "choices": choices})
    assert ask(shop, ai, question="Which is the top category?")["answer"]["status"] == "clarify"        # an ordinary question may still be clarified


def test_why_did_it_change_between_two_periods_shows_what_changed(shop):
    df = shop[3]
    ai = FakeAI(q(op="trend", measure="amount", agg="sum"))
    t = ask(shop, ai, question="Why did amount change between 2023 and 2024?")
    a = assert_figures_are_computed(t)
    yr = df.groupby(df.order_date.dt.year)["amount"].sum()
    assert a["query"]["op"] == "period_change" and a["query"]["group_by"]
    assert f"{yr[2023]:,.0f}" in a["text"] and f"{yr[2024]:,.0f}" in a["text"] and not CAUSAL.search(a["text"])
    assert all("change from 2023 to 2024" not in c["text"] for c in a["chips"])         # that is already what was shown


def test_why_about_a_group_is_anchored_on_its_column_even_if_the_ai_filtered(shop):
    df = shop[3]
    ai = FakeAI(q(op="top", measure="amount", agg="sum", group_by=["region"], n=3, filters=[{"column": "category", "op": "eq", "value": "Clothing"}]))
    a = ask(shop, ai, question="Why is Clothing the top category?")["answer"]
    s = top_cat(df)
    assert a["query"]["group_by"] == ["category"] and not a["query"]["filters"]
    assert f"{s['Clothing'] / s.sum() * 100:.1f}%" in a["text"]


def test_a_why_that_names_two_periods_is_about_those_periods_whatever_the_ai_chose(shop):
    ai = FakeAI(q(op="top", measure="amount", agg="sum", group_by=["category"], n=3))
    a = ask(shop, ai, question="Why did amount change between 2023 and 2024?")["answer"]
    assert a["query"]["op"] == "period_change" and (a["query"]["a"], a["query"]["b"]) == ("2023", "2024")
