"""Planner coverage: whatever the model's plan skips, the code adds the top level of every hierarchy for each KPI, the drill-down into the two parents that matter,
the month-of-year pattern of the main total and each ratio KPI, and the correlation check for a ratio KPI. Fake model, no network, no column names in the code under test."""
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np
import pandas as pd
import pytest

from app import planner, reporter, tools
from app.profiler import profile_df
from test_plan_proposal import good_plan, spec
from test_propose import Seq

GOAL = "Which region sells the most, and is it growing?"
RATIO = {"name": "margin_ratio", "a": "profit", "b": "amount", "kind": "ratio_of_sums", "meaning": "profit over amount"}
OV = {"hierarchy": [("category", "sub_category")], "derived": [RATIO], "intent": "compare"}     # a brief that read the goal as "compare", as in run ac1d17f8e13d
SUBS = {"Tech": ["Phones", "Laptops"], "Office": ["Paper", "Pens"]}


def data(months=24):
    """Office has the larger revenue and a healthy margin; Tech is smaller and loss-making."""
    n, rng = 800, np.random.default_rng(3)
    cat = rng.choice(list(SUBS), n)
    amount = np.where(cat == "Office", rng.uniform(200, 600, n), rng.uniform(50, 300, n)).round(2)
    profit = (amount * np.where(cat == "Office", 0.2, -0.1) + rng.normal(0, 5, n)).round(2)
    days = rng.integers(0, int(months * 30.4) - 1, n)
    df = pd.DataFrame({"order_id": [f"O{i}" for i in range(n)], "order_date": pd.Timestamp("2023-01-01") + pd.to_timedelta(days, unit="D"),
                       "category": cat, "sub_category": [SUBS[c][i % 2] for i, c in enumerate(cat)], "region": rng.choice(["N", "S", "E", "W"], n),
                       "amount": amount, "profit": profit, "discount_pct": rng.uniform(0, 0.4, n).round(2)})
    df["margin_ratio"] = df["profit"] / df["amount"]
    return df


def model_plan(**kw):
    """What a model scoped to the column the goal names would draw up: nothing on category, nothing on months."""
    return good_plan(analyses=[spec("trend", target="amount", date="order_date"), spec("group_compare", target="amount", group="region"), spec("distribution", col="amount")], **kw)


def plan_for(llm, df=None, ov=None):
    df = data() if df is None else df
    return planner.propose_plan(llm, df, profile_df(df), GOAL, {}, False, OV if ov is None else ov, {})[0]


def sigs(plan, tool):
    return {(a["params"]["target"], a["params"]["group"], a["params"].get("within_value")) if tool == "group_compare" else (a["params"]["target"],)
            for a in plan["analyses"] if a["tool"] == tool}


@pytest.fixture(params=["llm", "none"])
def plan(request):
    return plan_for(Seq(model_plan()) if request.param == "llm" else None)


def test_the_top_level_is_broken_down_for_every_kpi_even_when_the_plan_skips_it(plan):
    got = sigs(plan, "group_compare")
    assert {("amount", "category", None), ("profit", "category", None), ("margin_ratio", "category", None)} <= got
    assert all(a["origin"] != "ai" for a in plan["analyses"] if a["params"].get("group") == "category")


def test_code_logs_why_it_added_the_top_level():
    df = data()
    roles, uniques = planner.roles_and_uniques(profile_df(df))
    out = planner.ensure_required(df, roles, uniques, GOAL, False, OV, "amount", "order_date", [spec("group_compare", target="amount", group="region")])
    top = next(a for a in out if a["params"].get("target") == "margin_ratio" and a["params"].get("group") == "category")
    assert top["origin"] == "code" and "added by code: required coverage" in top["why"]


def test_the_drill_down_goes_into_the_largest_parent_and_the_worst_ratio_parent(plan):
    drill = {(a["params"]["target"], a["params"]["within_value"]) for a in plan["analyses"] if a["tool"] == "group_compare" and a["params"].get("within") == "category"}
    assert drill == {("amount", "Office"), ("margin_ratio", "Office"), ("amount", "Tech"), ("margin_ratio", "Tech")}      # Office: largest share of amount; Tech: lowest margin
    a = next(a for a in plan["analyses"] if a["params"].get("within_value") == "Tech")
    assert a["params"]["group"] == "sub_category" and a["origin"] == "code" and ("worst margin_ratio" in a["why"] or "largest share" in a["why"])


def test_month_of_year_seasonality_for_the_main_total_and_the_ratio_kpi(plan):
    assert sigs(plan, "seasonality") == {("amount",), ("profit",), ("margin_ratio",)}      # every additive measure, plus the ratio KPI
    r = next(a for a in plan["analyses"] if a["tool"] == "seasonality" and a["params"]["target"] == "margin_ratio")
    assert r["origin"] == "code" and "ratio of sums" in r["why"]


def test_a_year_of_dates_is_enough_and_less_is_not():
    assert ("amount",) in sigs(plan_for(Seq(model_plan()), data(12)), "seasonality")
    assert not sigs(plan_for(Seq(model_plan()), data(8)), "seasonality")


def test_seasonality_does_not_depend_on_the_goal_asking_about_change():
    df = data()
    p = planner.propose_plan(Seq(model_plan()), df, profile_df(df), "Which region sells the most?", {}, False, OV, {})[0]
    assert sigs(p, "seasonality") == {("amount",), ("profit",), ("margin_ratio",)}


def test_extra_measures_are_compared_across_every_planned_group():
    df = data()
    roles, uniques = planner.roles_and_uniques(profile_df(df))
    out = planner.measure_extras(df, roles, uniques, ["amount", "profit", "discount_pct"], {"amount"}, "order_date", ["region", "category", "sub_category"])
    assert {a["params"]["group"] for a in out if a["tool"] == "group_compare" and a["params"]["target"] == "profit"} == {"region", "category", "sub_category"}


def test_a_ratio_kpi_is_always_checked_against_the_numeric_drivers(plan):
    corr = [a for a in plan["analyses"] if a["tool"] == "correlation"]
    assert corr and all(c in corr[0]["params"]["cols"] for c in ("amount", "profit", "discount_pct"))


def test_the_correlation_rule_adds_one_only_when_missing_and_only_for_a_ratio_kpi():
    df = data()
    roles, uniques = planner.roles_and_uniques(profile_df(df))
    have = [spec("correlation", cols=["amount", "profit"])]
    kept = planner.ensure_required(df, roles, uniques, GOAL, False, OV, "amount", "order_date", have)
    assert sum(a["tool"] == "correlation" for a in kept) == 1 and kept[0] == have[0]
    added = planner.ensure_required(df, roles, uniques, GOAL, False, OV, "amount", "order_date", [spec("distribution", col="amount")])
    c = next(a for a in added if a["tool"] == "correlation")
    assert c["origin"] == "code" and "margin_ratio is a ratio KPI" in c["why"]
    plain = planner.ensure_required(df, roles, uniques, GOAL, False, {"intent": "compare"}, "amount", "order_date", [spec("distribution", col="amount")])
    assert not any(a["tool"] == "correlation" for a in plain)


def test_a_group_comparison_can_be_limited_to_one_parent(tmp_path):
    df = data()
    f = tools.group_compare(df, {"charts_dir": tmp_path}, "amount", "sub_category", within="category", within_value="Tech")
    assert set(f["stats"]["values"]) == {"Phones", "Laptops"} and f["stats"]["within"] == {"column": "category", "value": "Tech"} and "within category = Tech" in f["title"]


def test_the_report_puts_the_goal_named_column_first_and_code_coverage_later():
    def f(i, score, origin, **p):
        return {"id": f"f{i}", "origin": origin, "params": p, "insight": {"score": score}}
    findings = [f(1, 1, "code", target="amount", group="category"), f(2, 5, "code", target="amount", date="order_date"), f(3, 2, "ai", target="amount", group="region"),
                f(4, 3, "code", target="profit", group="region")]
    state = {"goal": GOAL, "findings": findings, "plan": {"analyses": [{"params": {"group": "region"}}, {"params": {"group": "category"}}]}}
    first, later = reporter.split_insights(state)
    assert [x["id"] for x in first] == ["f4", "f3"] and [x["id"] for x in later] == ["f2", "f1"]
