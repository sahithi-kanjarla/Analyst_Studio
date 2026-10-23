"""Every question is anchored to a signal found in the data. A model may reword what the signals show, but cannot invent questions: it used to be able to
ask "which is the main number", "which column defines the segment", "total or average", "which time frame" on data where nothing was ambiguous."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from test_clarify import Writer, hr_attrition, hospital_stays, ideal, prep, retail_sales
from app import clarify, derived, planner, signals, tools
from app.cleaner import add_derived
from test_derived_and_display import Proposer


def clean_hr(n=400, seed=31):
    """Same shape as the HR data (a date, a team, a flag, two pay columns) with nothing ambiguous in it."""
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"hired_on": (pd.Timestamp("2020-01-01") + pd.to_timedelta(rng.integers(0, 1500, n), unit="D")).strftime("%Y-%m-%d"),
                         "team": rng.choice(["A", "B", "C", "D"], n), "left_company": rng.choice([0, 1], n, p=[.8, .2]),
                         "base_pay": rng.normal(60000, 9000, n).round(0), "bonus": rng.integers(500, 8000, n).astype(float)})


def clean_hospital(n=400, seed=32):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"admitted": (pd.Timestamp("2024-01-01") + pd.to_timedelta(rng.integers(0, 330, n), unit="D")).strftime("%Y-%m-%d"),
                         "ward": rng.choice(["ICU", "General", "Maternity"], n), "readmitted": rng.choice([0, 1], n, p=[.85, .15]),
                         "stay_days": rng.integers(1, 15, n) + rng.normal(0, .1, n).round(2), "cost": rng.gamma(3, 1500, n).round(2)})


def invented(measures, groups, date, acting):
    """The four questions a careless model writes for any data. `acting` makes their options really remove columns or rows, so only the
    missing signal (not a note-only check) can stop them."""
    note = {"type": "note_only"}
    m1, m2 = measures[:2]

    def q(text, cols, *options):
        return {"text": text, "why": "x", "columns": cols, "options": [{"label": lbl, "action": act} for lbl, act in options], "default": options[0][0]}
    if acting:
        return [
            q("Which is the main number you care about?", [m1, m2], ("Both", note), (f"Only {m1}", {"type": "drop_column", "column": m2}), (f"Only {m2}", {"type": "drop_column", "column": m1})),
            q("Which column should define the group?", groups[:1], ("All groups", note), ("Leave the group out", {"type": "drop_column", "column": groups[0]})),
            q("Should it be measured by total or by average?", [m1], ("Total", note), ("Average", {"type": "drop_column", "column": m1})),
            q("What time frame should be used to evaluate growth?", [date], ("All time", note), ("Recent only", {"type": "filter_rows", "column": date, "operator": "ge", "value": "2099-01-01"})),
        ]
    return [
        q("Which is the main number you care about?", [m1, m2], ("No preference", note), (m1, {"type": "set_measures", "columns": [m1]}), (m2, {"type": "set_measures", "columns": [m2]})),
        q("Which column should define the group?", groups[:1], *[(g, note) for g in groups]),
        q("Should it be measured by total or by average?", [m1], ("Total", note), ("Average", note)),
        q("What time frame should be used to evaluate growth?", [date], ("Month over month", note), ("Year over year", note)),
    ]


# name: (data, measures, groups, date)
CLEAN = {
    "retail": (retail_sales(), ["amount", "profit"], ["region", "channel", "tier"], "order_date"),
    "hr": (clean_hr(), ["base_pay", "bonus"], ["team", "left_company"], "hired_on"),
    "hospital": (clean_hospital(), ["stay_days", "cost"], ["ward", "readmitted"], "admitted"),
}


@pytest.mark.parametrize("acting", [False, True])
@pytest.mark.parametrize("name", list(CLEAN))
def test_nothing_ambiguous_means_zero_questions_with_a_model_that_invents_four_and_with_no_model(name, acting):
    df, measures, groups, date = CLEAN[name]
    typed, prof = prep(df)
    sigs = signals.compute_signals(typed, prof)
    assert not [s for s in sigs if s["strong"]]                                                            # the data really has nothing to ask about
    wrong = invented(measures, groups, date, acting)
    qs, dropped = clarify.validate_all(wrong, prof, sigs)
    assert qs == [] and len(dropped) == 4
    if acting:
        assert all(d.startswith("no signal in the data") for d in dropped)                                # data-changing options get past the structural check, the signal check stops them
    assert clarify.build_questions(typed, prof, "Why did sales change?", {}, False, Writer(lambda s: wrong), None) == []
    assert clarify.build_questions(typed, prof, "Explore", {}, True, Writer(lambda s: wrong), None) == []
    assert clarify.build_questions(typed, prof, "Why did sales change?", {}, False, None, None) == []     # no model at all


@pytest.mark.parametrize("acting", [False, True])
def test_a_model_cannot_borrow_a_real_signal_id_for_a_pick_one_question(acting):
    df, measures, groups, date = CLEAN["retail"]
    typed, prof = prep(df.assign(amount=lambda d: d["amount"].where(np.arange(len(d)) % 10 != 0, 9999.0)))        # a placeholder signal now exists
    sigs = signals.compute_signals(typed, prof)
    sid = next(s["id"] for s in sigs if s["type"] == "sentinel")
    mm = next(s["id"] for s in sigs if s["type"] == "multi_measure")
    wrong = [{**w, "signal": sid} for w in invented(measures, groups, date, acting)] + [{**w, "signal": mm} for w in invented(measures, groups, date, acting)]
    qs, dropped = clarify.validate_all(wrong, prof, sigs)
    assert qs == [] and len(dropped) == 8


# (data, column with the placeholder, the placeholder value, column with negatives or None)
REAL = {
    "retail": (retail_sales(placeholder=True), "amount", 9999, None),
    "hr": (hr_attrition(), "perf_rating", -1, "perf_rating"),
    "hospital": (hospital_stays(), "stay_days", 9999, "cost"),
}


@pytest.mark.parametrize("name", list(REAL))
def test_a_real_placeholder_or_negative_still_gets_its_question_with_no_model(name):
    df, col, value, neg = REAL[name]
    typed, prof = prep(df)
    qs = clarify.build_questions(typed, prof, "Explore", {}, True, None, None)
    sent = next(q for q in qs if q["kind"] == "sentinel" and q["columns"] == [col])
    assert str(value) in sent["text"] or f"{value:g}" in sent["text"]
    if neg:
        assert any(q["kind"] == "negatives" and q["columns"] == [neg] for q in qs)
    assert not any(q["kind"] in ("multi_measure", "extra") for q in qs)


@pytest.mark.parametrize("name", list(REAL))
def test_a_model_that_rewords_the_real_questions_and_adds_invented_ones_keeps_only_the_reworded(name):
    df, col, value, neg = REAL[name]
    typed, prof = prep(df)
    measures = signals.measure_columns(typed, prof)
    groups = [c["name"] for c in prof["columns"] if c["role"] == "categorical"]
    date = next(c["name"] for c in prof["columns"] if c["role"] == "date")
    wrong = invented(measures, groups, date, acting=True) + invented(measures, groups, date, acting=False)
    sigs = signals.compute_signals(typed, prof)
    mine = ideal(sigs)
    qs = clarify.build_questions(typed, prof, "Explore", {}, True, Writer(lambda s: wrong + mine), None)
    texts = {q["text"] for q in qs}
    assert {m["text"] for m in mine} <= texts                                                              # the reworded versions are kept as written
    assert not {w["text"] for w in wrong} & texts                                                          # the invented ones are not
    sent = next(q for q in qs if q["kind"] == "sentinel" and q["columns"] == [col])
    assert sent["id"] == sent["signal"] and sent["text"].startswith("Does ")
    if neg:
        assert any(q["kind"] == "negatives" and q["columns"] == [neg] and q["text"].startswith("Are the negative values") for q in qs)


# ---- what replaces the questions: the planner analyses every measure, with a ratio of sums

def test_the_planner_analyses_every_measure_and_a_ratio_of_sums_instead_of_asking():
    df, measures, groups, date = CLEAN["retail"]
    typed, prof = prep(df)
    assert derived.propose(None, typed, signals.measure_columns(typed, prof), "goal") == ([], [])             # rules alone never invent a ratio
    accepted, _ = derived.propose(Proposer([{"a": "profit", "b": "amount", "meaning": "Share of the amount kept as profit."}]), typed, signals.measure_columns(typed, prof), "goal")
    assert accepted and (accepted[0]["a"], accepted[0]["b"]) == ("profit", "amount") and accepted[0]["name"] == "profit_to_amount_ratio"
    ratio = accepted[0]
    typed, log, added = add_derived(typed, [ratio])
    assert added == [ratio["name"]] and log[0]["step"] == "Derived ratio" and "overall ratio of sums" in log[0]["detail"]
    assert "profit" in typed.columns and "amount" in typed.columns                                           # originals untouched
    prof = {**prof, "columns": prof["columns"] + [dict(c, name=ratio["name"]) for c in prof["columns"] if c["name"] == "profit"], "cols": prof["cols"] + 1}
    plan = planner.heuristic_plan(typed, prof, "Which segment drives revenue and how is it growing?", {}, False, {"derived": [ratio]})
    target = plan["target"]
    analysed = {v for a in plan["analyses"] for k, v in a["params"].items() if k in ("target", "col")}
    assert {"amount", "profit", ratio["name"]} <= analysed and target in analysed
    for m in {"amount", "profit", ratio["name"]} - {target}:
        tools_for = {a["tool"] for a in plan["analyses"] if a["params"].get("target") == m or a["params"].get("col") == m}
        assert {"trend", "group_compare", "group_table"} <= tools_for                                              # over time, average per record by segment, totals and averages by segment
    assert {a["params"]["group"] for a in plan["analyses"] if a["tool"] == "group_compare" and a["params"]["target"] == target} == set(groups)
    assert plan["derived"] == [ratio]
    overview = next(a for a in plan["analyses"] if a["tool"] == "summary_table")
    assert {"amount", "profit", ratio["name"]} <= set(overview["params"]["cols"])                           # and every measure is in the overview table


def test_ratios_and_prices_are_averaged_never_added_up():
    assert tools.additive("amount") and tools.additive("order_total")
    assert not tools.additive("profit_to_amount_ratio") and not tools.additive("unit_price")


def test_unrelated_measures_get_no_ratio():
    rng = np.random.default_rng(3)
    df = pd.DataFrame({"a": rng.gamma(3, 50, 300), "b": rng.gamma(3, 50, 300)})
    assert derived.propose(None, df, ["a", "b"], "goal") == ([], [])
