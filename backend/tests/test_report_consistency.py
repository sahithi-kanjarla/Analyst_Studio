"""Wrong and contradictory statements in the report: AI figures must be tied to the fact they cite; AI shares that disagree with computed ones are replaced;
a data gap a computed fact answers is not listed; unconfirmed dates are flagged at the top; every grouping column the goal names is answered and kept in the plan."""
import json
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from test_summary_answers import make_state, view_for  # noqa: E402

from app import cited, reporter, strategy, summary  # noqa: E402
from app.insights import DATE_CAUTION, DATE_TAG, dates_unconfirmed  # noqa: E402
from app.planner import goal_groups  # noqa: E402
from app.profiler import profile_df  # noqa: E402

B2B = {"id": "r1", "tool": "ranking", "kind": "outside_rate", "basis": "ratio", "group": "B2B", "measure": "Amount", "top": "False", "bottom": "False", "values": ["True"],
       "text": "Amount outside False: ₹126,321 (0.6% of all Amount); 186 of 31,047 rows (0.6%) are outside False, which holds 99.4% of the rows in B2B.",
       "rate_amount": 0.006, "rate_rows": 0.006, "outside": 126321.0, "by": [], "focus": True}
STATUS = {"id": "r2", "tool": "ranking", "kind": "outside_rate", "basis": "ratio", "group": "Status", "measure": "Amount", "top": "Delivered", "bottom": "Delivered",
          "values": ["Returned", "Refunded", "Cancelled"], "outside": 1478226.0, "by": [], "focus": True,
          "text": "Amount outside Delivered: ₹1,478,226 (6.9% of all Amount); 2,150 of 31,047 rows (6.9%) are outside Delivered, which holds 93.1% of the rows in Status.",
          "rate_amount": 0.069, "rate_rows": 0.069}


def ai_state(extra_findings=()):
    state = make_state()
    state["findings"] += list(extra_findings)
    state["summary_facts"] = {"rankings": [B2B, STATUS], "picks": {}, "source": "rules"}
    return state


def ask(monkeypatch, out):
    monkeypatch.setattr(reporter, "ask_json", lambda *a, **k: out)


# ------------------------------------------------------------------ 1. a figure is tied to the fact it came from
def test_figure_of_one_fact_is_not_attributed_to_another_subject(monkeypatch):
    """The real failure: the B2B amount outside 'False' (126,321) was written as the loss from cancellations, returns and refunds."""
    state = ai_state()
    ask(monkeypatch, {
        "summary": "About 6.9% of the amount sits outside Delivered [r2]. Orders have no cost column.",
        "recommendations": [
            "Cut the combined ₹126 k loss from cancellations, returns and refunds [r1].",     # right figure, wrong subject
            "Cut the ₹126,321 loss from returns and refunds.",                                # no fact id
            "Cut returns by ₹126,321 [r2].",                                                  # id exists, figure is not in that fact
            "Cut returns by ₹126,321 [r99].",                                                 # id does not exist
            "Review the ₹1,478,226 held in returns, refunds and cancellations [r2].",         # right figure, right subject
        ]})
    summary_text, recs, partial = reporter.llm_summary(object(), state)
    assert recs == ["Review the ₹1,478,226 held in returns, refunds and cancellations."] and partial
    assert "[" not in summary_text and "6.9%" in summary_text and "Orders have no cost column." in summary_text      # ids are removed from the report text
    why = " | ".join(state["decisions"][-1]["errors"])
    assert "no fact id given" in why and "not a figure of fact r2" in why and "unknown fact id r99" in why and "not about the subject of fact r1" in why
    assert "126 k loss from cancellations" in state["decisions"][-1]["raw_output"]           # the model's own words are kept in the log


def test_prompt_requires_the_fact_id_and_sends_the_ids(monkeypatch):
    seen = {}
    monkeypatch.setattr(reporter, "ask_json", lambda llm, system, user: seen.update(system=system, user=user) or {"summary": "Fine.", "recommendations": ["Do it."]})
    reporter.llm_summary(object(), ai_state())
    assert "square brackets" in seen["system"] and "Every number you write needs an id" in seen["system"]
    assert '"id": "r1"' in seen["user"] and '"id": "f1"' in seen["user"]


def test_ids_may_come_before_or_after_the_full_stop_and_several_may_be_listed():
    facts = {"r2": cited.make_fact(STATUS["text"], STATUS["text"] + " Status Returned"), "counts": cited.make_fact("12 analyses", "")}
    assert cited.verify("6.9% of the amount is outside Delivered [r2].", facts)[1] is None
    assert cited.verify("6.9% of the amount is outside Delivered. [r2]", facts)[1] is None
    assert cited.verify("6.9% of the amount is outside Delivered [r2, counts].", facts)[1] is None
    assert cited.verify("12 analyses were run [counts].", facts)[1] is None
    assert cited.verify("No figure here at all.", facts)[1] is None


def test_strategy_wording_with_a_misattributed_figure_is_dropped_and_recorded():
    finding = {"id": "f1", "text": "Gold accounts average ₹8,216.", "title": "Average amount by tier", "params": {"group": "tier"}, "stats": {}}
    strat = {"id": "s1", "kind": "data", "title": "Protect gold accounts", "objective": "Keep gold accounts happy.", "actions": ["Call them.", "Offer a plan."], "risks": ["Cost.", "Delay."], "kpis": ["Churn"],
             "how_to_test": "Pilot it.", "evidence": ["f1"]}
    out = {"strategies": [{"id": "s1", "title": "Recover ₹8,216 lost to returns and refunds [f1]", "objective": "Protect gold accounts that average ₹8,216 [f1] each."}]}

    class Msg:
        def __init__(self, c):
            self.content = c

    class L:
        def invoke(self, m):
            return Msg(json.dumps(out, ensure_ascii=False))

    state = {"goal": "g", "context": {}, "findings": [finding], "decisions": []}
    assert strategy.refine_with_llm(L(), state, [strat]) == 1
    assert strat["title"] == "Protect gold accounts"                                               # misattributed: rule wording kept
    assert strat["objective"] == "Protect gold accounts that average ₹8,216 each."            # tied and about the subject: kept, id removed
    assert any("not about the subject of fact f1" in d["why"] for d in state["decisions"])
    assert "square brackets" in strategy.REFINE_SYSTEM


# ------------------------------------------------------------------ 2. a share that disagrees with a computed one
def test_prose_share_that_disagrees_with_the_computed_fact_is_replaced(monkeypatch):
    other = {"id": "f9", "tool": "category_share", "title": "returned orders", "params": {"col": "Status"}, "stats": {}, "significant": None, "text": "Returned orders: 1% of refunds were partial.",
             "insight": {"headline": "Returned orders: 1% of refunds were partial", "score": 1, "confidence": {"label": "Descriptive"}}}
    state = ai_state([other])
    ask(monkeypatch, {"summary": "Less than 1% of orders are returned [f9]. Sales are steady.", "recommendations": ["Review the 6.9% outside Delivered [r2]."]})
    text, recs, partial = reporter.llm_summary(object(), state)
    assert "Less than 1%" not in text and STATUS["text"].rstrip(".") in text and "Sales are steady." in text and partial       # the computed fact takes its place
    assert recs == ["Review the 6.9% outside Delivered."]
    assert any("disagrees with computed fact r2" in e for e in state["decisions"][-1]["errors"])


def test_contradiction_rules():
    computed = cited.claims([STATUS, {"id": "r3", "tool": "ranking", "group": "category", "measure": "profit", "top": "Tech", "bottom": "Furniture", "top_share": 0.496, "text": "t", "lead": "Top 3"}])
    assert cited.contradicts("Less than 1% of Status is outside Delivered.", computed)["id"] == "r2"
    assert cited.contradicts("About 7% of Status is outside Delivered.", computed) is None              # a rounding of 6.9%
    assert cited.contradicts("More than 5% of Status is outside Delivered.", computed) is None
    assert cited.contradicts("Less than 10% of Status is outside Delivered.", computed) is None
    assert cited.contradicts("Tech makes 4% of profit.", computed)["id"] == "r3"
    assert cited.contradicts("Tech makes 49.6% of profit.", computed) is None
    assert cited.contradicts("Furniture is 4% of the weekday mix.", computed) is None                  # another subject


# ------------------------------------------------------------------ 3. a gap that a computed fact answers
def test_gap_is_suppressed_when_a_loss_fact_exists():
    gaps = [{"severity": "info", "title": "No returns or refunds data", "detail": "d", "suggestion": "s"},
            {"severity": "info", "title": "No acquisition channel or campaign data", "detail": "d", "suggestion": "s"},
            {"severity": "warn", "title": "Small dataset (50 rows)", "detail": "d", "suggestion": "s"}]
    kept = summary.covered_gaps({"rankings": [STATUS], "picks": {}}, list(gaps))
    assert [g["title"] for g in kept] == ["No acquisition channel or campaign data", "Small dataset (50 rows)"]
    assert summary.covered_gaps({"rankings": [B2B], "picks": {}}, list(gaps)) == gaps                  # B2B labels say nothing about returns
    assert summary.covered_gaps({"rankings": [], "picks": {}}, list(gaps)) == gaps


# ------------------------------------------------------------------ 4. dates that may be swapped
QUESTION = {"id": "q_date", "kind": "date_swap", "text": "Swap?", "default": "keep", "columns": ["when"],
            "options": [{"value": "keep", "label": "No", "action": {"type": "note_only"}}, {"value": "swap", "label": "Yes", "action": {"type": "swap_day_month", "column": "when"}}]}


def report(answers):
    state = make_state()
    state["questions"], state["answers"] = [QUESTION], answers
    state["report_view"] = view_for(state)
    return reporter.write_report({**state, "goal_answer": None, "data_gaps": [], "cleaning_log": []}, None)[0], state


def test_date_caution_is_the_first_line_under_the_header_when_swap_was_not_chosen():
    for answers in ({}, {"q_date": "keep"}):
        md, state = report(answers)
        lines = md.split("\n")
        assert lines[0].startswith("# Analysis report") and lines[4] == f"- ⚠ {DATE_CAUTION}" and dates_unconfirmed(state)
        assert md.count(DATE_CAUTION) == 1
        assert f"[{DATE_TAG}]" in next(l for l in lines if "Sales are flat over time" in l and l.startswith("- "))     # the trend finding is tagged
        assert f"[{DATE_TAG}]" not in next(l for l in lines if l.startswith("- Profit does not differ"))               # a finding that does not use dates is not


def test_no_date_caution_when_swapped_or_when_no_date_question_was_asked():
    md, state = report({"q_date": "swap"})
    assert DATE_CAUTION not in md and DATE_TAG not in md and not dates_unconfirmed(state)
    state = make_state()
    state["report_view"] = view_for(state)
    md = reporter.write_report({**state, "goal_answer": None, "data_gaps": [], "cleaning_log": []}, None)[0]
    assert DATE_CAUTION not in md and DATE_TAG not in md


# ------------------------------------------------------------------ 5. every grouping column the goal names
def test_goal_named_group_column_is_answered_in_the_summary():
    state = make_state()
    state["goal"] = "Compare profit by category and weekday."
    state["plan"]["questions"] = ["Which category has the highest profit?"]
    state["report_view"] = view_for(state)
    assert [r["group"] for r in summary.uncovered_groups(state, state["report_view"])] == ["weekday"]
    md = reporter.write_report({**state, "goal_answer": None, "data_gaps": [], "cleaning_log": []}, None)[0]
    assert "**By weekday** (named in your goal)" in md.split("## Key numbers")[0]
    state["goal"] = "Compare profit by category."
    state["plan"]["questions"] = ["Which category has the highest profit?"]
    assert summary.uncovered_groups(state, view_for(state)) == []


def make_frame(n=300):
    rng = np.random.default_rng(1)
    cols = {f"seg{i}": rng.choice(list("abcd")[:2 + i % 3], n) for i in range(8)}
    return pd.DataFrame({**cols, "amount": rng.lognormal(5, 0.4, n).round(2)})


GOAL = "Which seg1 and seg6 bring in the most amount?"


def test_columns_joined_by_and_in_the_goal_are_kept_in_the_plan_and_not_listed_as_unneeded():
    from app.cleaner import scoped_clean
    from app.planner import finalize_plan
    df = make_frame()
    prof = profile_df(df)
    # an AI plan that forgot one of the two columns and listed it as skipped
    raw = {"objective": GOAL, "target": "amount", "relevant_columns": ["amount", "seg1"], "skipped": [{"column": "seg6", "reason": "not needed to answer this goal"}],
           "analyses": [{"tool": "group_compare", "params": {"target": "amount", "group": "seg1"}, "why": "w"}, {"tool": "category_share", "params": {"col": "seg1"}, "why": "w"}]}
    plan, _ = finalize_plan(raw, df, prof, GOAL, {}, False, {})
    assert {"seg1", "seg6"} <= set(plan["relevant_columns"])
    assert "seg6" not in {s["column"] for s in plan["skipped"]}
    _, log = scoped_clean(df, prof, plan)
    unused = next((x["detail"] for x in log if x["step"] == "Scope to the goal"), "")
    assert "seg1" not in unused and "seg6" not in unused
    roles = {c["name"]: c["role"] for c in prof["columns"]}
    uniques = {c["name"]: c["unique"] for c in prof["columns"]}
    assert goal_groups(roles, uniques, GOAL, ["amount"]) == ["amount", "seg1", "seg6"]
    assert goal_groups(roles, uniques, "Total amount?", ["amount"]) == ["amount"]
