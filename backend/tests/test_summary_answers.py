"""Executive summary: a goal question is answered by the finding or ranking about its own measure and group column, never by a loose match; a question with no
answer says 'Not answered'; the headlines lead with the answers and prefer totals to per-row averages; key numbers are written in full. Synthetic findings and data."""
import os

os.environ["LLM_PROVIDER"] = "none"

import pandas as pd  # noqa: E402

from app import reporter, summary, tiering  # noqa: E402
from app.insights import allowed_numbers, numbers_ok  # noqa: E402

CONF = {"level": "high", "label": "High", "reasons": ["Still significant after correction."]}
Q_CAT = "Which categories generate the most profit?"
Q_NONE = "Which regions have the lowest churn?"


def finding(i, tool, headline, sig=True, score=5.0, effect=0.3, **params):
    st = {"p": 0.0001, "p_adjusted": 0.001, "survives_correction": sig, "effect": effect, "effect_name": "eta_squared", "n": 90}
    return {"id": f"f{i}", "tool": tool, "title": f"{tool} {i}", "params": params, "stats": st, "significant": sig, "text": headline, "action": None,
            "insight": {"headline": headline, "observation": headline, "why_it_matters": "", "score": score, "confidence": CONF, "hypotheses": [], "check_next": [], "kpi_related": False}}


def make_df():
    rows = []
    for cat, weekdays, profit, sales in (("Tech", "Mon", 70_000, 400_000), ("Office", "Tue", 20_000, 300_000), ("Furniture", "Wed", -30_000, 200_000)):
        for k in range(30):
            rows.append({"category": cat, "weekday": ("Mon", "Tue", "Wed")[k % 3], "profit": profit / 30, "sales": sales / 30})
    return pd.DataFrame(rows)


def make_state():
    fs = [finding(1, "group_compare", "Profit is highest on Mon and lowest on Wed", score=20.0, target="profit", group="weekday"),     # significant, big score, wrong grouping
          finding(2, "group_compare", "Profit does not differ reliably across category", sig=False, score=1.0, effect=0.01, target="profit", group="category"),
          finding(3, "trend", "Sales are flat over time", sig=False, score=1.0, target="sales", date="when")]
    fs[0]["stats"]["kind"] = "numeric"
    cols = [{"name": "category", "role": "categorical", "unique": 3}, {"name": "weekday", "role": "categorical", "unique": 3},
            {"name": "profit", "role": "numeric", "unique": 90}, {"name": "sales", "role": "numeric", "unique": 90}]
    return {"goal": "Which categories generate the most profit?", "findings": fs, "decisions": [], "context": {}, "brief": None, "key_metrics": [], "strategies": [], "critique": {"issues": []},
            "profile": {"rows": 90, "cols": 4, "columns": cols},
            "plan": {"target": "sales", "date_col": None, "questions": [Q_CAT, Q_NONE], "objective": "o", "relevant_columns": ["profit", "sales"], "analyses": [], "explore": False,
                     "derived": [{"name": "margin", "a": "profit", "b": "sales", "kind": "ratio_of_sums"}]}}


def view_for(state, df=None):
    df = make_df() if df is None else df
    df["margin"] = df["profit"] / df["sales"]
    state["summary_facts"] = summary.pick(None, state, summary.ranking_facts(state, df))
    return tiering.build_view(state, df)


def test_category_profit_question_is_answered_by_the_category_ranking_not_the_weekday_finding():
    view = view_for(make_state())
    a = next(x for x in view["answers"] if x["question"] == Q_CAT)
    assert any(i.startswith("r") for i in a["ids"]) and "f1" not in a["ids"]
    assert "category Tech is highest at 70,000 (116.7% of the net total profit, because other groups lose money)" in a["answer"] and "category Furniture is lowest at -30,000" in a["answer"]
    assert "Mon" not in a["answer"]                                               # the weekday pattern is not what was asked
    ranking = next(r for r in view["rankings"] if r["measure"] == "profit")
    assert "Loss-making" in ranking["text"] and "category Furniture" in ranking["text"]
    assert [r["measure"] for r in view["rankings"] if r["group"] == "category"].count("margin") == 1      # a ratio KPI ranks too, by ratio of sums
    assert {r["measure"] for r in view["rankings"] if r["group"] == "weekday"} == {"sales"}      # a column the goal does not name is ranked for the main KPI (the plan target) only


def test_a_question_nothing_answers_says_not_answered():
    a = next(x for x in view_for(make_state())["answers"] if x["question"] == Q_NONE)
    assert a["ids"] == [] and a["answer"].startswith("Not answered: ") and len(a["answer"]) > len("Not answered: .")


def test_headlines_lead_with_the_answer_and_prefer_totals_to_per_row_averages():
    view = view_for(make_state())
    heads = view["headlines"]
    assert len(heads) <= 5 and heads[0]["id"].startswith("r")
    assert "f1" in [h["id"] for h in heads]                                       # still a strong finding, but after the answers


def test_key_numbers_write_totals_in_full_and_ratios_as_percent():
    km = {m["label"]: m["value"] for m in view_for(make_state())["numbers"]}
    assert km["Total profit"] == "60,000" and km["Total sales"] == "900,000"
    assert km["margin (total profit over total sales)"] == "6.7%"


def test_llm_ids_outside_the_list_are_rejected(monkeypatch):
    state = make_state()
    df = make_df()
    df["margin"] = df["profit"] / df["sales"]
    monkeypatch.setattr(summary, "ask_json", lambda llm, system, user: {"answers": [{"question": 0, "ids": ["r1", "f999"]}, {"question": 1, "ids": ["none"]}]})
    facts = summary.pick(object(), state, summary.ranking_facts(state, df))
    assert facts["picks"][Q_CAT] == ["r1"] and facts["picks"][Q_NONE] == [] and facts["source"] == "llm"
    assert "f999" in state["decisions"][-1]["errors"][0]


def test_unusable_llm_answer_falls_back_to_the_rules(monkeypatch):
    state = make_state()
    monkeypatch.setattr(summary, "ask_json", lambda llm, system, user: {"nonsense": 1})
    facts = summary.pick(object(), state, summary.ranking_facts(state, make_df().assign(margin=0.1)))
    assert facts["source"] == "rules" and facts["picks"][Q_CAT]


def test_answer_numbers_pass_the_number_check_and_the_report_carries_them():
    state = make_state()
    view = view_for(state)
    state["report_view"] = view
    md = reporter.write_report({**state, "goal_answer": None, "data_gaps": [], "cleaning_log": []}, None)[0]
    assert "Not answered:" in md and "category Tech is highest" in md
    allowed = allowed_numbers(" ".join(r["text"] for r in view["rankings"]))
    assert numbers_ok(next(a for a in view["answers"] if a["question"] == Q_CAT)["answer"], allowed)
