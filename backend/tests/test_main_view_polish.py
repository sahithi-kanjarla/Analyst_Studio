"""Main view polish, with fake data: a question with several parts shows the best fact of each kind, action questions have no card, a trend never shows a fall of more than 100%,
the main view uses readable names (the details keep the internal ones), a KPI the plan serves with another column is not 'not found'."""
import os
import re

os.environ["LLM_PROVIDER"] = "none"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from test_main_view import Q1, growth, main_of, render, state_with, what_to_do  # noqa: E402
from test_report_tiers import finding  # noqa: E402

from app import goal, insights, mainview, summary, tiering, tools, understanding as und, wording  # noqa: E402

Q = "Which segment earns the most revenue, is it growing, and are we making money on it?"


def ratio_fact(group="g0", measure="margin", fid="r_ratio"):
    return {"id": fid, "tool": "ranking", "basis": "ratio", "group": group, "measure": measure, "top": "a", "bottom": "b", "focus": True,
            "text": f"By {measure}, a is highest at 40.0%.", "short": f"{measure} by {group}: highest a 40%, lowest b 10% (overall 25%)"}


def multi_part_state(rankings):
    st = state_with(rankings)
    st["goal"] = Q
    st["plan"]["questions"] = [Q]
    st["summary_facts"]["picks"] = {Q: ["r1"]}
    return st


# ------------------------------------------------------------------ 1. a question with several parts shows the best fact of each kind
def test_a_multi_part_question_shows_the_ranking_the_growth_and_the_ratio_fact():
    decoys = [growth(group="other", measure="cost"), ratio_fact(group="other", fid="r_other")]            # same kinds, wrong group or measure: never picked
    main = main_of(render(multi_part_state([growth(group="g0", measure="revenue"), ratio_fact()] + decoys)))
    card = main.split(f"**1. {Q}**")[1].split("Trust:")[0]
    lines = [l.strip() for l in card.split("\n") if l.strip() and not l.startswith("Trust")]
    assert len(lines) == 3 and len(lines) <= mainview.PART_LINES
    assert lines[0].startswith("By total revenue, segment a is highest") and "Biggest change in revenue by g0" in lines[1] and lines[2].startswith("margin by g0")
    assert "cost" not in card and "other" not in card


def test_a_part_without_a_fact_is_left_out_and_a_one_part_question_is_unchanged():
    main = main_of(render(multi_part_state([growth(group="g0", measure="revenue")])))            # no ratio fact exists
    card = main.split(f"**1. {Q}**")[1].split("Trust:")[0]
    assert "Biggest change" in card and "margin" not in card
    assert mainview.part_kinds("Which segment earns the most revenue?") == [] and mainview.part_kinds("Is revenue growing?") == []
    assert mainview.part_kinds(Q) == ["ranking", "growth", "ratio"]


def test_the_part_facts_are_picked_by_kind_not_by_words():
    rk = {"id": "r1", "tool": "ranking", "basis": "total", "group": "g0", "measure": "revenue", "text": "t", "short": "s"}
    rg = {**growth(group="g0", measure="revenue"), "id": "rg"}
    rr = ratio_fact()
    r_by_id = {r["id"]: r for r in (rk, rg, rr)}
    # the facts' own texts say nothing about the question's words: only their kind decides
    assert mainview.part_facts(["ranking", "growth", "ratio"], ["r1"], {}, r_by_id, "revenue") == ["r1", "rg", "r_ratio"]
    assert mainview.part_facts(["ratio", "growth"], ["r1"], {}, r_by_id, "revenue") == ["r_ratio", "rg"]


# ------------------------------------------------------------------ 2. an action question is the 'What to do' section only
def test_an_action_question_is_not_a_card_and_the_others_are_numbered_from_one():
    st = state_with()
    st["goal"] = f"What should we do? {Q1}"
    st["plan"]["questions"] = ["What should we do?", Q1]
    st["summary_facts"].pop("picks")
    main = main_of(render(st))
    before = main.split("### What to do")[0]
    assert f"**1. {Q1}**" in before and "What should we do" not in before and "**2." not in before
    todo = what_to_do(main)
    assert re.search(r"^\d+\. ", todo, re.M) and "*(question 2)*" not in todo and "for question 2" not in todo
    cs = mainview.cards(st, tiering.build_view(st))
    assert [c["num"] for c in cs] == [None, 1] and cs[0]["action_question"]


# ------------------------------------------------------------------ 3. a trend never shows a fall of more than 100%
def fmt(v):
    return f"{v:,.0f}"


def test_a_segment_change_that_falls_100_percent_or_more_says_falling_steeply_with_real_values():
    e = {"relative_change": -1.08, "start": 1500.0, "end": 420.0, "change_abs": -1080.0, "first": 1600.0, "last": 400.0, "pct_ok": True}
    text = wording.change_phrase(e, fmt)
    assert text == "falling steeply, from 1,600 to 400" and "%" not in text and not wording.pct_ok(e)
    below = {"relative_change": -0.6, "start": 100.0, "end": -20.0, "change_abs": -120.0, "first": 95.0, "last": 3.0, "pct_ok": True}      # the fitted end is below zero
    assert wording.change_phrase(below, fmt) == "falling steeply, from 95 to 3"
    fine = {"relative_change": -0.3, "start": 100.0, "end": 70.0, "change_abs": -30.0, "first": 99.0, "last": 71.0, "pct_ok": True}
    assert wording.change_phrase(fine, fmt) == "-30%"
    assert "falling steeply" not in wording.change_phrase({"relative_change": 0.5, "start": -50.0, "end": -10.0, "change_abs": 40.0, "pct_ok": False}, fmt)       # rising towards zero is not a fall


def test_the_overall_trend_sentences_say_falling_steeply_and_show_no_percentage():
    st = {"slope": -10.0, "relative_change": -1.4, "periods": 12, "first": 1000.0, "last": 50.0, "fit_end": 40.0, "p": 0.001, "freq": "MS"}
    assert wording.trend_size(st, "sales", {}, fmt=fmt) == "falling steeply, from 1,000 to 50" and not wording.pct_change_ok(st, "sales", {})
    neg_end = {"slope": -5.0, "relative_change": -0.8, "periods": 12, "first": 100.0, "last": 2.0, "fit_end": -6.0}
    assert "falling steeply" in wording.trend_size(neg_end, "sales", {}, fmt=fmt)
    state = {"goal": "Is sales falling?", "plan": {"target": "sales", "date_col": "d"}, "profile": {"columns": []},
             "findings": [{"id": "f1", "tool": "trend", "params": {"target": "sales"}, "stats": st, "significant": True}]}
    sentence = goal.answer(state)["sentence"]
    assert "falling steeply, from" in sentence and "%" not in sentence.split("(")[0] and "fell about" not in sentence


def test_a_real_falling_series_is_never_reported_as_more_than_minus_100_percent(tmp_path):
    rng = np.random.default_rng(0)
    days = pd.date_range("2023-01-01", periods=240, freq="D")
    df = pd.DataFrame({"d": days, "sales": np.linspace(1000, 20, len(days)) + rng.normal(0, 5, len(days))})
    f = tools.trend(df, {"charts_dir": tmp_path}, "sales", "d")
    assert f["stats"]["relative_change"] <= -1 and f["stats"]["first"] > 10 * f["stats"]["last"]
    assert "falling steeply, from" in f["text"] and not re.search(r"-\d{3,}%", f["text"])
    head = insights._narrative({**f, "params": {"target": "sales", "date": "d"}}, {})["headline"]
    assert f["significant"] and "falling steeply" in head and "%" not in head


def test_a_growth_table_line_never_shows_a_percentage_below_minus_100():
    df = pd.DataFrame({"seg": ["x"] * 12 + ["y"] * 12, "when": list(pd.date_range("2023-01-01", periods=12, freq="MS")) * 2,
                       "v": [10.0] * 6 + [-8.0] * 6 + [10.0] * 6 + [8.0] * 6})
    fact = summary._growth_fact(df, {"plan": {}, "profile": {"columns": []}}, "seg", "v", "when", 1)
    line = next(g["line"] for g in fact["groups"] if g["group"] == "x")
    assert "falling steeply" in line and not re.search(r"-\d{3,}(\.\d)?%", fact["text"] + fact["short"])


# ------------------------------------------------------------------ 4. readable names in the main view, internal names in the details
def named_state():
    st = state_with([ratio_fact(measure="profit_to_amount_ratio")])
    st["profile"]["columns"] += [{"name": "profit_to_amount_ratio", "stats": {"min": -1.0, "max": 1.0}}, {"name": "sub_category", "stats": {}}]
    st["plan"]["derived"] = [{"name": "profit_to_amount_ratio", "a": "profit", "b": "amount", "kind": "ratio_of_sums", "kpi": "profit margin"}]
    st["key_metrics"] = [{"label": "Total revenue", "value": "10,000", "note": ""}, {"label": "profit_to_amount_ratio (total profit over total amount)", "value": "5.8%", "note": ""},
                         {"label": "Period", "value": "2023-01 to 2024-12", "note": ""}]
    st["goal"] = Q
    st["plan"]["questions"] = [Q]
    st["summary_facts"]["picks"] = {Q: ["r1", "r_ratio"]}
    st["understanding"] = {"source": "llm", "columns": {"sub_category": {"display_name": "Product line", "role": "category", "meaning": "m"}}, "goal_columns": {}, "unmapped": [], "derived": {"profit_to_amount_ratio": "Profit margin"}}
    return st


def test_the_main_view_uses_display_names_and_the_details_keep_the_internal_ones():
    md = render(named_state())
    main, details = md.split("<details>", 1)
    assert "Profit margin" in main and "profit_to_amount_ratio" not in main
    assert "| Profit margin (total profit over total amount)" in main                       # the key-number tile
    assert "profit_to_amount_ratio" in details


def test_display_names_come_from_the_record_then_the_kpi_then_the_code():
    st = named_state()
    names = und.display_names(st)
    assert names["profit_to_amount_ratio"] == "Profit margin" and names["sub_category"] == "Product line"
    del st["understanding"]["derived"], st["understanding"]["columns"]
    names = und.display_names(st)
    assert names["profit_to_amount_ratio"] == "Profit margin" and names["sub_category"] == "Sub category"       # the KPI the user wrote, else the code's own readable name
    assert und.readable("sub_category and revenue", st) == "Sub category and revenue"                              # a name that already reads well is left alone
    rec = und.with_derived({"source": "llm", "columns": {}, "goal_columns": {}}, [{"name": "a_per_b", "a": "a", "b": "b"}])
    assert rec["derived"] == {"a_per_b": "A per b"}


def test_the_record_validation_keeps_a_display_name_and_drops_one_with_an_underscore():
    obj = {"columns": [{"column": "a_b", "meaning": "m", "role": "measure", "display_name": "Order value", "goal_terms": []},
                       {"column": "c_d", "meaning": "m", "role": "category", "display_name": "c_d", "goal_terms": []}], "goal_columns": {}}
    parts, errors = und.validate(obj, ["a_b", "c_d"])
    assert not errors and parts["columns"]["a_b"]["display_name"] == "Order value" and "display_name" not in parts["columns"]["c_d"]


# ------------------------------------------------------------------ 5. the key data gaps line
def gap_state(rec):
    return {"context": {"kpis": ["revenue"]}, "brief": {"kpis": {"revenue": None}}, "derived_metrics": {}, "questions": [], "answers": {}, "concerns": {},
            "plan": {"target": "amount", "date_col": "when", "relevant_columns": ["amount", "region"], "derived": []},
            "profile": {"rows": 500, "columns": [{"name": "amount", "role": "numeric", "missing_pct": 0}, {"name": "region", "role": "categorical", "missing_pct": 0}]}, "understanding": rec}


def test_a_kpi_the_plan_serves_with_another_column_is_not_reported_as_not_found():
    rec = {"source": "llm", "columns": {}, "goal_columns": {"revenue": ["amount"]}, "unmapped": []}
    assert not [g for g in insights.data_gaps(gap_state(rec)) if "not found" in g["title"]]
    unserved = {"source": "llm", "columns": {}, "goal_columns": {"revenue": ["notes"]}, "unmapped": []}         # mapped to a column the plan does not use
    assert [g for g in insights.data_gaps(gap_state(unserved)) if "KPI 'revenue' was not found" in g["title"]]
    assert [g for g in insights.data_gaps(gap_state(None)) if "KPI 'revenue' was not found" in g["title"]]        # no record: the old rule stands
