"""Four gaps found by the scorecard, on synthetic data: growth by group (every group, absolute and %, reliable or not), the summary line for a ratio that does not
differ across a goal-named column, at most three clarifying questions in both paths, and placeholder values flagged on a source table with a cleaning-log line."""
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app import clarify, planner, reporter, signals, summary  # noqa: E402,F401
from app import placeholders  # noqa: E402

import test_report_tiers as tiers  # noqa: E402
from test_clarify import Writer, hr_attrition, ideal, prep  # noqa: E402
from test_placeholders import cl, customers_orders, questions, result, start  # noqa: E402,F401
from test_clarify import wait  # noqa: E402


# ------------------------------------------------------------------ 1. growth by group
def growth_df(seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for month in pd.date_range("2023-01-01", "2024-12-01", freq="MS"):
        year = month.year
        for g in "ABCD":
            for _ in range(3):
                day = month + pd.Timedelta(days=int(rng.integers(0, 27)))
                base = {"A": 100 if year == 2023 else 160,              # grows 60% steadily
                        "B": 100 + rng.normal(0, 60),                   # noisy and flat
                        "C": 0.1 if year == 2023 else 50,               # starts near zero
                        "D": -50.0 if year == 2023 else -20.0}[g]       # starts negative
                rows.append({"tier": g, "when": day, "revenue": base + (rng.normal(0, 3) if g in "AD" else 0)})
    # a partial last period must not count: only a few days of 2025
    rows += [{"tier": g, "when": pd.Timestamp("2025-01-03"), "revenue": 1.0} for g in "AB"]
    return pd.DataFrame(rows)


def growth_state(questions):
    cols = [{"name": "tier", "role": "categorical", "unique": 4}, {"name": "revenue", "role": "numeric", "unique": 200}, {"name": "when", "role": "date", "unique": 100}]
    return {"goal": "Which tier is growing fastest?", "findings": [], "decisions": [], "context": {}, "brief": None, "key_metrics": [], "strategies": [], "critique": {"issues": []},
            "profile": {"rows": 200, "cols": 3, "columns": cols},
            "plan": {"target": "revenue", "date_col": "when", "questions": questions, "objective": "o", "relevant_columns": ["revenue"], "analyses": [], "explore": False, "derived": []}}


def test_growth_fact_lists_every_group_with_absolute_and_percent_change_and_marks_reliability():
    state = growth_state(["Which tier is growing fastest?"])
    facts = summary.ranking_facts(state, growth_df())
    (g,) = [f for f in facts if f.get("kind") == "growth"]
    assert g["tool"] == "ranking" and g["group"] == "tier" and g["measure"] == "revenue" and g["periods"] == ["2023", "2024"]      # the 2025 stub is not a full period
    by = {x["group"]: x for x in g["groups"]}
    assert set(by) == {"A", "B", "C", "D"}                                                       # all groups, significant or not
    assert abs(by["A"]["pct"] - 0.6) < 0.05 and abs(by["A"]["change"] - (by["A"]["last"] - by["A"]["first"])) < 1e-6
    assert by["A"]["reliable"] and not by["B"]["reliable"]
    assert by["C"]["pct"] is None and by["D"]["pct"] is None                                      # a near-zero or negative start: absolute change only
    assert by["D"]["change"] > 0 and by["C"]["change"] > 0
    assert f"tier A {by['A']['pct']:+.1%}" in g["text"] and "statistically reliable" in g["text"] and "not statistically reliable" in g["text"]
    for k in "CD":
        assert f"tier {k} +" in g["text"] and "no % because the start" in g["text"]
    assert [r["measure"] for r in facts if r.get("kind") != "growth"] == ["revenue"]              # the plain ranking is still there


def test_growth_question_is_answered_by_the_growth_fact_and_a_ranking_question_is_not():
    q_growth, q_rank = "Which tier is growing fastest in revenue?", "Which tier earns the most revenue?"
    state = growth_state([q_growth, q_rank])
    state["summary_facts"] = summary.pick(None, state, summary.ranking_facts(state, growth_df()))
    by_q = {a["question"]: a for a in summary.answers(state, state["summary_facts"])}
    assert "Growth in total revenue by tier" in by_q[q_growth]["answer"]
    assert "Growth in total revenue" not in by_q[q_rank]["answer"] and "is highest at" in by_q[q_rank]["answer"]


def test_no_growth_fact_without_two_full_periods():
    df = growth_df()
    df = df[df["when"] < "2024-01-01"]
    df = df[df["when"] >= "2023-04-01"]                                                          # nine months: not even one full year, but several full quarters
    facts = summary.ranking_facts(growth_state(["Which tier is growing fastest?"]), df)
    (g,) = [f for f in facts if f.get("kind") == "growth"]
    assert g["periods"][0] != g["periods"][1] and len(g["periods"][0]) == 6                      # quarters, e.g. 2023Q2
    one_period = df[df["when"] < "2023-05-01"]                                                   # one full month only
    assert not [f for f in summary.ranking_facts(growth_state(["Which tier is growing fastest?"]), one_period) if f.get("kind") == "growth"]


# ------------------------------------------------------------------ 2. summary line for a ratio that does not differ
def ratio_state(sig):
    state = tiers.make_state()
    state["goal"] += " Compare by g0."
    f = next(x for x in state["findings"] if x["id"] == "f103")                                  # g0 comparison of the ratio KPI margin
    f["significant"], f["stats"]["survives_correction"] = sig, sig
    return state


def test_summary_says_a_ratio_does_not_reliably_differ_across_a_goal_named_column():
    state = ratio_state(False)
    assert summary.difference_lines(state) == ["margin does not reliably differ across g0."]
    md = reporter.write_report(state, None)[0]
    exec_summary = md.split("## Executive summary")[1].split("## Key numbers")[0]
    assert "margin does not reliably differ across g0" in exec_summary
    assert not summary.difference_lines(ratio_state(True))                                       # a reliable difference gets no such line
    other = ratio_state(False)
    other["goal"] = "Which segment earns the most revenue?"                                      # g0 is not named by the goal or its questions
    other["plan"]["questions"] = ["Which segment earns the most revenue?"]
    assert not summary.difference_lines(other)


# ------------------------------------------------------------------ 3. at most three questions
def fake_q(kind, strong=True, harmless=True, rows=0):
    default = {"type": "note_only"} if harmless else {"type": "treat_value_as_missing", "column": "c", "values": [1]}
    return {"id": f"{kind}:c", "kind": kind, "text": f"Question about {kind}?", "strong": strong, "default": "d", "columns": ["c"],
            "options": [{"value": "d", "label": f"default of {kind}", "action": default, "rows": rows}, {"value": "o", "label": "other", "action": {"type": "drop_column", "column": "c"}}]}


def test_cap_keeps_the_three_that_change_the_analysis_most_and_logs_the_rest_with_their_defaults():
    qs = [fake_q("high_missing", strong=False), fake_q("near_sum"), fake_q("negatives"), fake_q("sentinel"), fake_q("multi_date"), {**fake_q("rare_labels"), "auto": "goal"}]
    kept, dropped = clarify.cap_questions(qs)
    asked = [q["kind"] for q in kept if not q.get("auto")]
    assert asked == ["sentinel", "multi_date", "negatives"] and len(asked) <= clarify.MAX_QUESTIONS == 3
    assert [d["question"] for d in dropped] == ["Question about near_sum?", "Question about high_missing?"]
    assert [d["default"] for d in dropped] == ["default of near_sum", "default of high_missing"]
    assert any(q["kind"] == "rare_labels" and q["auto"] == "goal" for q in kept)                 # a question the goal settled neither counts nor goes


def test_a_dropped_question_whose_default_changes_the_data_still_runs_its_default():
    qs = [fake_q("placeholder", harmless=False, rows=50)] + [fake_q(k, harmless=False, rows=n) for k, n in (("sentinel", 40), ("multi_date", 0), ("negatives", 0))]
    kept, dropped = clarify.cap_questions(qs)
    assert [q["kind"] for q in kept if not q.get("auto")] == ["placeholder", "sentinel", "multi_date"]
    assert [d["question"] for d in dropped] == ["Question about negatives?"]
    assert next(q for q in kept if q["kind"] == "negatives")["auto"] == "capped"


def test_both_paths_ask_at_most_three_and_report_the_dropped_ones():
    df = hr_attrition()
    typed, prof = prep(df)
    sigs = signals.compute_signals(typed, prof)
    assert len(ideal(sigs)) > clarify.MAX_QUESTIONS                                              # the writer has more to say than the cap allows
    for llm in (Writer(lambda s: ideal(s)), None):                                               # the LLM path, then the rules path
        dropped: list = []
        qs, _ = clarify.build_questions_and_ignored(typed, prof, "Explore", {}, True, llm, None, None, dropped)
        assert len([q for q in qs if not q.get("auto")]) <= clarify.MAX_QUESTIONS
        assert all(d["question"] and d["default"] for d in dropped)


# ------------------------------------------------------------------ 4. placeholder values
def table_with_placeholder(value, blank_date=True, n=400, k=25, seed=3):
    rng = np.random.default_rng(seed)
    df = pd.DataFrame({"days": rng.integers(1, 30, n).astype(float), "closed_on": pd.date_range("2024-01-01", periods=n)})
    df.loc[:k - 1, "days"] = value
    if blank_date:
        df.loc[:k - 1, "closed_on"] = pd.NaT
    return df


def test_any_repeated_extreme_value_is_flagged_and_a_blank_date_on_the_same_rows_is_noted():
    for value in (4321, 777777):                                                                 # neither is a conventional "no value" number: nothing is hard-coded
        (c,) = placeholders.detect(table_with_placeholder(value))
        assert c["value"] == value and c["count"] == 25 and c["twin"] == "closed_on" and "closed_on is blank on those rows" in c["reason"]
    (c,) = placeholders.detect(table_with_placeholder(4321, blank_date=False))
    assert c["value"] == 4321 and c["twin"] is None and "far above" in c["reason"]
    close = table_with_placeholder(40, blank_date=True)                                          # just above the real range, no other sign: a date that is blank beside it is the sign
    (c,) = placeholders.detect(close)
    assert c["value"] == 40 and c["twin"] == "closed_on"
    assert placeholders.detect(table_with_placeholder(40, blank_date=False)) == []              # same value, nothing odd beside it: just a number


def test_cleaning_log_always_says_what_was_treated_as_missing():
    tables = customers_orders(extra_tables=True)
    rid = start(tables)
    assert wait(rid) == "needs_input"
    asked = [q for q in questions(rid) if q["kind"] == "placeholder"]
    assert [q["default"] for q in asked] == ["missing"] and "treated as missing" not in asked[0]["text"]
    assert cl.post(f"/api/runs/{rid}/answers", json={"skip": True}).status_code == 200
    assert wait(rid) == "done"
    log = [s["detail"] for s in result(rid)["cleaning_log"] if s["step"] == "Placeholder values"]
    n_used = int((tables["orders"]["wait_days"] == 999).sum())
    n_idle = int((tables["audit"]["seconds"] == 9999).sum())
    assert any(f"treated {n_used:,} values of 999 in wait_days as missing (default)" in d for d in log)          # asked column, default taken
    assert any(f"treated {n_idle:,} values of 9999 in seconds as missing (default)" in d for d in log)           # a column the analysis never uses
    assert "treated" in result(rid)["report_md"] and "as missing (default)" in result(rid)["report_md"]


def test_a_question_the_data_settled_is_recorded_but_not_counted_among_the_three_asked():
    qs = [fake_q(k) for k in ("placeholder", "multi_date", "negatives", "near_sum")] + [{**fake_q("product_basis"), "auto": "data"}]
    kept, dropped = clarify.cap_questions(qs)
    assert [q["kind"] for q in kept if not q.get("auto")] == ["placeholder", "product_basis"][:0] + ["placeholder", "multi_date", "negatives"]
    assert any(q["kind"] == "product_basis" and q["auto"] == "data" for q in kept) and [d["id"] for d in dropped] == ["near_sum:c"]
