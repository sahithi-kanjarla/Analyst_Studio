"""The summary answers the goal's own questions, ranks a column with many groups by its top five, reports the rate outside a dominant value, and a data gap
for a kind of data the brief already has a column for is dropped. Synthetic table: 50 groups, a status column with one value on 92% of the rows."""
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app import insights, summary  # noqa: E402

Q1, Q2, Q3 = "Which store earns the most revenue?", "Which stores lose the most revenue outside the normal status?", "What is our total revenue?"
GOAL = f"Review our sales. 1. {Q1}\n2. {Q2} {Q3}"


def make_df():
    rng = np.random.default_rng(1)
    rows = []
    for k in range(50):
        for j in range(50):
            rows.append({"store": f"S{k:02d}", "status": "ok" if rng.random() > 0.08 + 0.002 * k else "void", "revenue": float(rng.integers(50, 150)) * (1 + k / 25)})
    return pd.DataFrame(rows)


def make_state(df, brief=None):
    cols = [{"name": "store", "role": "categorical", "unique": 50, "missing_pct": 0}, {"name": "status", "role": "categorical", "unique": 2, "missing_pct": 0},
            {"name": "revenue", "role": "numeric", "unique": 900, "missing_pct": 0}]
    return {"goal": GOAL, "findings": [], "decisions": [], "context": {"industry_key": "ecommerce", "kpis": []}, "brief": brief, "key_metrics": [], "strategies": [],
            "critique": {"issues": []}, "profile": {"rows": len(df), "cols": 3, "columns": cols},
            "plan": {"target": "revenue", "date_col": None, "questions": ["What explains revenue?"], "objective": "o", "relevant_columns": ["store", "status", "revenue"], "analyses": [], "explore": False, "derived": []}}


def test_goal_questions_are_the_summary_questions_verbatim():
    state = make_state(make_df())
    assert summary.questions(state) == [Q1, Q2, Q3]
    facts = summary.pick(None, state, summary.ranking_facts(state, make_df()))
    assert list(facts["picks"]) == [Q1, Q2, Q3]
    assert [a["question"] for a in summary.answers(state, facts)] == [Q1, Q2, Q3]
    assert summary.questions({**state, "goal": "Understand the business."}) == ["What explains revenue?"]      # no question in the goal: the planner's stay


def test_a_column_with_fifty_groups_is_ranked_by_its_top_five():
    df = make_df()
    r = next(x for x in summary.ranking_facts(make_state(df), df) if x["tool"] == "ranking" and x["group"] == "store" and x["measure"] == "revenue" and x.get("kind") != "growth")
    assert r["groups_total"] == 50 and "top 5" in r["text"] and "the other 45 groups" in r["text"] and r["text"].count("%") >= 6
    assert "store S49" in r["text"]


def test_rate_outside_the_dominant_value_has_an_amount_and_a_percent_overall_and_by_group():
    df = make_df()
    state = make_state(df)
    r = next(x for x in summary.ranking_facts(state, df) if x.get("kind") == "outside_rate")
    amt = df.loc[df["status"] != "ok", "revenue"].sum()
    assert r["group"] == "status" and r["measure"] == "revenue"
    assert f"revenue outside ok: {amt:,.0f} ({amt / df['revenue'].sum():.1%} of the total revenue)" in r["text"]
    assert "By store, the highest rate outside ok:" in r["text"] and "the lowest:" in r["text"] and len(r["by"][0]["highest"]) == 3 and len(r["by"][0]["lowest"]) == 3


def test_a_gap_for_data_the_brief_already_maps_is_suppressed():
    df = make_df()
    gaps = lambda brief: [g["title"] for g in insights.data_gaps(make_state(df, brief))]
    assert any("cost" in t.lower() for t in gaps(None)) and "No date column" in gaps(None)
    mapped = gaps({"roles": {"placed": "date", "outlay": "measure"}, "kpis": {"cost": "outlay"}})
    assert not any("cost" in t.lower() for t in mapped) and "No date column" not in mapped
