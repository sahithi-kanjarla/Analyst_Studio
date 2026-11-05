"""Slice 3: the model proposes the whole analysis plan through propose(); the code enforces the cap and adds what is required. Fake model, no network."""
import json
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np
import pandas as pd

from app import planner
from app.profiler import profile_df
from test_propose import Seq

GOAL = "How is amount changing over time?"
SUBS = {"Tech": ["Phones", "Laptops"], "Office": ["Paper", "Pens"], "Home": ["Chairs", "Lamps"]}


def data(years=3):
    n = 600
    rng = np.random.default_rng(5)
    cat = rng.choice(list(SUBS), n)
    days = rng.integers(0, 365 * years - 5, n)
    return pd.DataFrame({"order_id": [f"ORD-{i}" for i in range(n)], "order_date": pd.Timestamp("2021-01-01") + pd.to_timedelta(days, unit="D"),
                         "category": cat, "sub_category": [SUBS[c][i % 2] for i, c in enumerate(cat)], "region": rng.choice(["N", "S", "E", "W"], n),
                         "amount": rng.uniform(10, 500, n).round(2), "profit": rng.uniform(1, 90, n).round(2), "discount_pct": rng.uniform(0, 0.4, n).round(2)})


def spec(tool, why="because", **params):
    return {"tool": tool, "params": params, "why": why}


def good_plan(**kw):
    base = {"objective": "See how amount changes", "questions": ["Is amount growing?"], "target": "amount", "date_col": "order_date", "relevant_columns": ["amount", "order_date", "region"],
            "analyses": [spec("trend", target="amount", date="order_date"), spec("group_compare", target="amount", group="region"), spec("distribution", col="amount")],
            "skipped": [], "why": "A trend first, then the main split."}
    base.update(kw)
    return json.dumps(base)


def plan_with(llm, df=None, goal=GOAL, ov=None, explore=False):
    df = data() if df is None else df
    st = {}
    plan, _ = planner.propose_plan(llm, df, profile_df(df), goal, {}, explore, ov or {}, st)
    return plan, st


def tools(plan):
    return [(a["tool"], tuple(str(v) if isinstance(v, list) else v for v in a["params"].values()), a["origin"]) for a in plan["analyses"]]


def test_a_good_plan_is_used_in_the_models_order():
    llm = Seq(good_plan())
    plan, st = plan_with(llm)
    assert plan["source"] == "llm" and len(llm.prompts) == 1
    ai = [(t, p) for t, p, o in tools(plan) if o == "ai"]
    assert ai == [("trend", ("amount", "order_date")), ("group_compare", ("amount", "region")), ("distribution", ("amount",))]
    assert next(a for a in plan["analyses"] if a["origin"] == "ai")["why"] == "because" and plan["questions"] == ["Is amount growing?"]
    d = st["decisions"][0]
    assert d["decision"] == "analysis plan" and d["source"] == "llm" and d["why"] == "A trend first, then the main split." and d["chosen"]["analyses chosen by the AI"] == 3


def test_the_prompt_states_the_real_cap():
    system = planner.plan_system(planner.MAX_ANALYSES)
    assert f"at most {planner.MAX_ANALYSES} analyses" in system and "at most 10" not in system
    assert planner.plan_cap(True) == planner.MAX_ANALYSES_EXPLORE and planner.plan_cap(False) == planner.MAX_ANALYSES


def test_a_bad_plan_is_repaired_once():
    bad = good_plan(analyses=[spec("trend", target="amount", date="order_date"), spec("magic_forecast"), spec("group_compare", target="ghost", group="region")])
    llm = Seq(bad, good_plan())
    plan, st = plan_with(llm)
    assert plan["source"] == "repaired" and len(llm.prompts) == 2
    assert "magic_forecast" in llm.prompts[1] and "ghost" in llm.prompts[1]
    assert st["decisions"][0]["source"] == "repaired" and len(st["decisions"][0]["errors"]) == 2


def test_a_plan_that_stays_bad_falls_back_to_the_rules_plan():
    bad = good_plan(analyses=[spec("trend", target="amount", date="order_date"), spec("group_compare", target="amount", group="order_id")])
    llm = Seq(bad)
    plan, st = plan_with(llm)
    assert len(llm.prompts) == 2 and plan["source"] == "rules" and st["decisions"][0]["source"] == "fallback"
    assert any("order_id" in e for e in st["decisions"][0]["errors"])
    assert plan["analyses"] and {o for _, _, o in tools(plan)} <= {"rules", "code"}


def test_unparseable_thin_and_wrongly_typed_replies_are_rejected():
    for reply in ("no json here", good_plan(analyses=[spec("distribution", col="amount")]), good_plan(target="region")):
        plan, st = plan_with(Seq(reply))
        assert st["decisions"][0]["source"] == "fallback", reply


def test_analyses_beyond_the_cap_are_dropped_and_the_first_valid_ones_kept():
    many = [spec("group_compare", target=t, group=g) for t in ("amount", "profit", "discount_pct") for g in ("region", "category", "sub_category")] \
        + [spec(k, col=c) for k in ("distribution", "outliers") for c in ("amount", "profit", "discount_pct")]
    assert len(many) > planner.MAX_ANALYSES
    llm = Seq(good_plan(analyses=many))
    plan, st = plan_with(llm)
    assert plan["source"] == "llm" and len(llm.prompts) == 1 and sum(a["origin"] == "ai" for a in plan["analyses"]) == planner.MAX_ANALYSES
    assert len(plan["dropped"]) == len(many) - planner.MAX_ANALYSES and all("over the limit" in d["reason"] for d in plan["dropped"])


def test_numbers_from_the_model_are_rejected():
    plan, st = plan_with(Seq(good_plan(questions=["Is amount up?", 5])))
    assert st["decisions"][0]["source"] == "fallback"


def test_seasonality_is_added_for_each_additive_measure_when_the_goal_asks_about_change():
    plan, _ = plan_with(Seq(good_plan()), ov={"kpi_map": {"profit": "profit"}})
    seas = {p[0]: o for t, p, o in tools(plan) if t == "seasonality"}
    assert seas == {"amount": "code", "profit": "code"}        # discount_pct is a rate: it keeps the per-row average and gets none
    s = next(a for a in plan["analyses"] if a["tool"] == "seasonality")
    assert "month-of-year" in s["why"] and s["origin"] == "code"


def test_a_seasonality_the_model_chose_stays_its_own():
    plan, _ = plan_with(Seq(good_plan(analyses=[spec("trend", target="amount", date="order_date"), spec("seasonality", target="amount", date="order_date"), spec("distribution", col="amount")])))
    assert [o for t, p, o in tools(plan) if t == "seasonality" and p[0] == "amount"] == ["ai"]


def test_no_added_seasonality_under_a_year_and_no_extra_totals_without_a_question_about_change():
    df = data()
    short, _ = plan_with(Seq(good_plan()), df=df[df["order_date"] < pd.Timestamp("2021-09-01")])
    assert not any(a["tool"] == "seasonality" and a["origin"] == "code" for a in short["analyses"])
    flat, _ = plan_with(Seq(good_plan()), goal="Which region sells the most?", ov={"kpi_map": {"profit": "profit"}})
    assert {p[0] for t, p, o in tools(flat) if t == "seasonality" and o == "code"} == {"amount", "profit"}      # every additive measure when dates span 2+ years, whether or not the goal asks about change


def test_the_parent_of_a_verified_hierarchy_comes_before_its_child():
    ov = {"hierarchy": [("category", "sub_category")]}
    only_child = good_plan(analyses=[spec("trend", target="amount", date="order_date"), spec("group_compare", target="amount", group="sub_category"), spec("distribution", col="amount")])
    plan, _ = plan_with(Seq(only_child), ov=ov)
    seq = [(a["params"]["group"], a["origin"]) for a in plan["analyses"] if a["tool"] == "group_compare" and a["params"]["target"] == "amount" and "within" not in a["params"]]
    assert seq == [("category", "code"), ("sub_category", "ai")]
    parent = next(a for a in plan["analyses"] if a["tool"] == "group_compare" and a["params"].get("group") == "category")
    assert "broader level" in parent["why"]
    later = good_plan(analyses=[spec("trend", target="amount", date="order_date"), spec("group_compare", target="amount", group="sub_category"), spec("group_compare", target="amount", group="category")])
    plan2, _ = plan_with(Seq(later), ov=ov)
    order = [(a["params"]["group"], a["origin"]) for a in plan2["analyses"] if a["tool"] == "group_compare" and a["params"]["target"] == "amount" and "within" not in a["params"]]
    assert order == [("category", "ai"), ("sub_category", "ai")]


def test_kpi_measures_the_model_left_out_are_added():
    plan, _ = plan_with(Seq(good_plan()), ov={"kpi_map": {"profit": "profit", "revenue": "amount"}})
    added = [(t, p) for t, p, o in tools(plan) if o == "code" and "profit" in p]
    assert ("trend", ("profit", "order_date")) in added and any(t == "group_compare" and p[0] == "profit" for t, p in added)
    df = data()
    prof = profile_df(df)
    roles, uniques = planner.roles_and_uniques(prof)
    mine = planner.ensure_required(df, roles, uniques, GOAL, False, {"kpi_map": {"profit": "profit"}}, "amount", "order_date", [spec("group_compare", target="amount", group="region")])
    kpi = [a for a in mine if a.get("origin") == "code" and a["params"].get("target") == "profit" and a["tool"] != "seasonality"]
    assert [a["tool"] for a in kpi] == ["trend", "group_compare"] and kpi[0]["why"].startswith("You named profit as a KPI")


def test_the_required_items_are_there_with_no_llm_and_after_a_fallback():
    ov = {"hierarchy": [("category", "sub_category")], "kpi_map": {"profit": "profit"}}
    for llm in (None, Seq("garbage")):
        plan, st = plan_with(llm, ov=ov)
        assert plan["source"] == "rules"
        sigs = {(t, p) for t, p, _ in tools(plan)}
        assert ("seasonality", ("amount", "order_date")) in sigs and ("seasonality", ("profit", "order_date")) in sigs and ("trend", ("profit", "order_date")) in sigs
        groups = [p[1] for t, p, _ in tools(plan) if t == "group_compare" and p[0] == "amount"]
        if "sub_category" in groups:
            assert groups.index("category") < groups.index("sub_category")
        assert st["decisions"][0]["source"] in ("rules-only", "fallback")


# ---- invalid analyses are dropped one by one; the plan survives while enough valid ones remain

def ten_analyses(bad_first=2):
    ok = [spec("trend", target="amount", date="order_date"), spec("group_compare", target="amount", group="region"), spec("distribution", col="amount"),
          spec("outliers", col="amount"), spec("category_share", col="region"), spec("distribution", col="profit"), spec("group_compare", target="profit", group="region"),
          spec("scatter", x="amount", y="profit")]
    bad = [spec("outcome_compare", target="discount_pct", col="amount"), spec("magic_forecast")]       # a numeric column as a binary outcome; an unknown tool
    return (bad[:bad_first] + ok)


def test_two_bad_analyses_among_ten_keep_the_eight_valid_ones():
    llm = Seq(good_plan(analyses=ten_analyses()))
    plan, st = plan_with(llm, explore=True)
    assert len(llm.prompts) == 1                                  # no repair was needed
    ai = [a for a in plan["analyses"] if a["origin"] == "ai"]
    assert len(ai) == 8 and plan["source"] == "llm" and len(plan["dropped"]) == 2
    assert {d["tool"] for d in plan["dropped"]} == {"outcome_compare", "magic_forecast"}
    assert all(d["reason"] for d in plan["dropped"]) and any("expected ['binary']" in d["reason"] for d in plan["dropped"])
    d = st["decisions"][0]
    assert d["source"] == "llm" and d["chosen"]["analyses dropped"] == 2 and "2 analyses dropped" in d["why"] and len(d["dropped"]) == 2


def test_a_plan_with_nothing_valid_still_falls_back_and_says_why():
    all_bad = good_plan(analyses=[spec("outcome_compare", target="amount", col="profit"), spec("magic_forecast"), spec("trend", target="ghost", date="order_date")])
    plan, st = plan_with(Seq(all_bad, all_bad))
    d = st["decisions"][0]
    assert d["source"] == "fallback" and plan["source"] == "rules" and plan["dropped"] == []
    assert "not usable after one repair" in d["why"] and "outcome_compare" in d["why"] + " ".join(d["errors"])


def test_an_unparseable_plan_falls_back():
    plan, st = plan_with(Seq("this is not json", "{broken"))
    d = st["decisions"][0]
    assert d["source"] == "fallback" and plan["source"] == "rules" and d["errors"]


def test_too_few_valid_analyses_are_repaired_then_kept_if_the_repair_is_good():
    few = good_plan(analyses=[spec("trend", target="amount", date="order_date"), spec("magic_forecast"), spec("outcome_compare", target="amount", col="profit")])
    llm = Seq(few, good_plan())
    plan, st = plan_with(llm)
    assert plan["source"] == "repaired" and len(llm.prompts) == 2 and "magic_forecast" in llm.prompts[1]


def test_the_prompt_states_limits_and_offers_only_fitting_candidates():
    df = data()
    llm = Seq(good_plan())
    plan_with(llm, df=df)
    prompt = llm.prompts[0]
    system = planner.plan_system(8)
    assert "outcome_compare: the target must be a binary column" in system and "2-30 distinct values" in system
    prof = profile_df(df)
    roles, uniq = planner.roles_and_uniques(prof)
    c = planner.candidate_lists(roles, uniq)
    assert all(roles[x] == "numeric" for x in c["numeric"]) and all(roles[x] == "binary" for x in c["binary"])
    assert all(roles[x] in ("categorical", "binary") and 2 <= uniq[x] <= 30 for x in c["group"]) and all(roles[x] == "date" for x in c["date"])
    assert "CANDIDATE COLUMNS" in prompt and "order_date" in prompt
