"""Each goal question is linked to the facts that answer it: no measure named means the main KPI, plural/hyphenated column words match column names, words that are
values of a status column select its rate-outside-the-main-value fact, time words select the trend, action questions list the recommendations.
Synthetic table: 40 groups, a status column with 92% on one value, a two-group column with 95% on one value."""
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app import summary  # noqa: E402

QS = ["Which categories and states bring in the most sales?", "How do sales change month to month?", "How much value do we lose to cancelled, returned and refunded orders, and where is it worst?",
      "What should we do?"]
GOAL = " ".join(QS)


def make_df():
    rng = np.random.default_rng(3)
    rows = []
    for k in range(40):
        for j in range(30):
            r = rng.random()
            rows.append({"Category": f"C{k:02d}", "ship-state": f"S{k % 6}", "when": pd.Timestamp("2024-01-01") + pd.Timedelta(days=int(rng.integers(0, 360))),
                         "status": "delivered" if r < 0.92 else ["cancelled", "returned", "refunded"][int(r * 100) % 3], "tier": "std" if rng.random() < 0.95 else "vip",
                         "amount": float(rng.integers(50, 150)) * (1 + k / 20)})
    return pd.DataFrame(rows)


def finding(i, tool, headline, text="t", **params):
    return {"id": f"f{i}", "tool": tool, "title": headline, "params": params, "stats": {}, "significant": True, "text": text, "action": None,
            "insight": {"headline": headline, "observation": text, "why_it_matters": "x", "score": 1.0, "confidence": {"level": "high", "label": "High", "reasons": []},
                        "hypotheses": [], "check_next": [], "kpi_related": False}}


def make_state():
    df = make_df()
    cols = [{"name": "Category", "role": "categorical", "unique": 40, "missing_pct": 0}, {"name": "ship-state", "role": "categorical", "unique": 6, "missing_pct": 0},
            {"name": "status", "role": "categorical", "unique": 4, "missing_pct": 0}, {"name": "tier", "role": "categorical", "unique": 2, "missing_pct": 0},
            {"name": "amount", "role": "numeric", "unique": 900, "missing_pct": 0}, {"name": "when", "role": "date", "unique": 300, "missing_pct": 0}]
    fs = [finding(1, "trend", "amount is trending up", "**amount** (sum per month) shows an upward trend. Best month: 2024-11-01 (9,000); weakest: 2024-02-01 (5,000).", target="amount", date="when"),
          finding(2, "group_compare", "Category drives amount", target="amount", group="Category")]
    strategies = [{"id": f"s{k}", "kind": "data", "theme": f"Theme{k}", "title": f"Strategy {k}", "objective": "o", "evidence": ["f2"], "impact": "High", "effort": "Low", "confidence": "high",
                   "actions": ["a"], "how_to_test": "t", "kpis": ["k"], "risks": ["r"], "horizon": "Quick win (0-30 days)", "rank": k + 1} for k in range(3)]
    state = {"goal": GOAL, "findings": fs, "decisions": [], "context": {"industry_key": "ecommerce", "kpis": []}, "brief": None, "key_metrics": [], "strategies": strategies, "critique": {"issues": []},
             "profile": {"rows": len(df), "cols": len(cols), "columns": cols},
             "plan": {"target": "amount", "date_col": "when", "questions": ["What explains amount?"], "objective": "o", "relevant_columns": [c["name"] for c in cols], "analyses": [], "explore": False, "derived": []}}
    return state, df


def run():
    state, df = make_state()
    facts = summary.pick(None, state, summary.ranking_facts(state, df))
    state["summary_facts"] = facts
    answered = summary.answers(state, facts)
    return state, df, facts, answered, {a["question"]: a for a in answered}


def test_no_question_is_left_unanswered():
    _, _, _, answered, _ = run()
    assert [a["question"] for a in answered] == QS
    assert not [a for a in answered if a["answer"].startswith("Not answered")]


def test_group_words_match_columns_by_stem_and_each_ranking_gives_its_top_three():
    _, _, facts, _, by_q = run()
    a = by_q[QS[0]]["answer"]
    assert "Top 3 Category by total amount" in a and "Top 3 ship-state by total amount" in a and "category C39" in a and "ship-state S" in a
    assert all(f["group"] in ("Category", "ship-state") for f in facts["rankings"] if f["id"] in by_q[QS[0]]["ids"])


def test_words_that_are_status_values_select_the_outside_rate_fact():
    _, _, facts, _, by_q = run()
    r = next(f for f in facts["rankings"] if f.get("kind") == "outside_rate" and f["group"] == "status")
    assert by_q[QS[2]]["ids"] == [r["id"]] and "outside delivered" in by_q[QS[2]]["answer"] and "the highest rate outside delivered" in by_q[QS[2]]["answer"]


def test_time_words_select_the_main_trend_with_best_and_worst_period():
    _, _, _, _, by_q = run()
    assert by_q[QS[1]]["ids"] == ["f1"] and "Best month" in by_q[QS[1]]["answer"] and "weakest" in by_q[QS[1]]["answer"]


def test_an_action_question_lists_the_recommendations():
    _, _, _, _, by_q = run()
    a = by_q[QS[3]]["answer"]
    assert a.startswith("Recommended:") and all(f"Strategy {k}" in a for k in range(3))


def test_headlines_keep_the_trend_and_drop_trivial_dominance(monkeypatch):
    monkeypatch.setattr(summary, "HEADLINES_MAX", 50)
    state, _, facts, answered, _ = run()
    tier = next(r for r in facts["rankings"] if r["group"] == "tier" and r.get("kind") is None)
    assert tier["dominant"] and tier["groups_total"] == 2 and tier["top_share"] > 0.9            # the fixture really has a 95% two-group column
    heads = summary.headlines(state, facts, answered)
    assert tier["id"] not in [h["id"] for h in heads] and "f1" in [h["id"] for h in heads]
    named = summary.headlines({**state, "goal": GOAL + " Which tier sells most?"}, facts, answered)
    assert tier["id"] in [h["id"] for h in named]                 # a column the goal names keeps its headline


def test_no_data_gap_for_a_kind_an_outside_rate_fact_already_answers():
    state, _, facts, _, _ = run()
    gaps = [{"title": "No returned data", "severity": "info"}, {"title": "No date column", "severity": "info"}, {"title": "No shipping data", "severity": "info"}]
    assert [g["title"] for g in summary.covered_gaps(facts, gaps)] == ["No date column", "No shipping data"]
