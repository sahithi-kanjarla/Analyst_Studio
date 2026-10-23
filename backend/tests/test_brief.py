"""The analysis brief: one LLM call that says what columns mean and which column each KPI is. Fake model, no network."""
import json
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np
import pandas as pd

from app import brief
from app.context import normalise_context
from app.insights import data_gaps
from app.planner import heuristic_plan
from app.profiler import profile_df
from app.tools import additive
from test_propose import Seq


def data():
    n = 120
    rng = np.random.default_rng(3)
    return pd.DataFrame({"order_no": range(1, n + 1), "pincode": rng.integers(500001, 500999, n), "amount": rng.uniform(10, 500, n).round(2),
                         "qty_score": rng.uniform(1, 5, n).round(1), "region": rng.choice(["N", "S", "E"], n)})


def reply(**kw):
    base = {"columns": {"pincode": {"role": "id"}, "amount": {"role": "measure", "kind": "additive"}, "region": {"role": "dimension"}},
            "kpis": {"revenue": "amount"}, "why": "amount is the sales value"}
    base.update(kw)
    return json.dumps(base)


def run(llm, kpis=("revenue",), st=None):
    df = data()
    return brief.resolve(llm, df, profile_df(df), "grow revenue", normalise_context({"kpis": list(kpis)}), st)


def test_revenue_maps_to_amount_and_it_is_logged():
    st = {}
    b = run(Seq(reply()), st=st)
    assert b["source"] == "llm" and b["kpis"] == {"revenue": "amount"} and b["roles"]["pincode"] == "id"
    d = st["decisions"][0]
    assert d["decision"] == "analysis brief" and d["why"] == "amount is the sales value"


def test_the_view_has_no_rows_only_a_few_samples():
    llm = Seq(reply())
    run(llm)
    view = llm.prompts[0]
    assert "amount" in view and "revenue" in view and view.count("samples:") == 5


def test_pincode_becomes_an_id_in_the_profile():
    df = data()
    prof = profile_df(df)
    assert next(c for c in prof["columns"] if c["name"] == "pincode")["role"] == "numeric"
    new = brief.apply_roles(prof, run(Seq(reply()))["roles"])
    assert next(c for c in new["columns"] if c["name"] == "pincode")["role"] == "id"
    plan = heuristic_plan(df, new, "pincode", {}, False, {})
    assert plan["target"] != "pincode"


def test_an_id_like_column_is_never_a_measure():
    b = run(Seq(reply(columns={"order_no": {"role": "measure", "kind": "additive"}}, kpis={})))
    assert b["roles"]["order_no"] == "id" and "order_no" not in b["kinds"]


def test_kind_is_kept_for_measures_only_and_changes_sum_vs_mean():
    b = run(Seq(reply(columns={"amount": {"role": "measure", "kind": "rate"}, "region": {"role": "dimension", "kind": "additive"}})))
    assert b["kinds"] == {"amount": "rate"}
    assert additive("amount", {}) is True and additive("amount", {"kinds": b["kinds"]}) is False
    assert additive("qty_score", {"kinds": {"qty_score": "additive"}}) is True


def test_a_kpi_mapped_to_a_missing_column_is_rejected_and_the_rule_decides():
    b = run(Seq(reply(kpis={"revenue": "turnover"})))
    assert b["source"] == "llm" and "revenue" not in b["kpis"] and any("turnover" in d for d in b["dropped"])
    ctx = normalise_context({"kpis": ["revenue"]})
    prof = profile_df(data())
    st = {"context": ctx, "plan": {"date_col": None, "target": None, "relevant_columns": []}, "profile": prof, "brief": b}
    assert any("revenue" in g["title"] for g in data_gaps(st))       # the rule finds no column called revenue either


def test_a_kpi_mapped_to_a_non_measure_is_rejected():
    assert "revenue" not in run(Seq(reply(kpis={"revenue": "region"})))["kpis"]
    assert "revenue" not in run(Seq(reply(kpis={"revenue": "pincode"})))["kpis"]


def test_a_mapped_kpi_is_not_a_data_gap_and_steers_the_target():
    b = run(Seq(reply()))
    ctx = normalise_context({"kpis": ["revenue"]})
    prof = profile_df(data())
    st = {"context": ctx, "plan": {"date_col": None, "target": "amount", "relevant_columns": ["amount"]}, "profile": prof, "brief": b}
    assert not any("revenue" in g["title"] for g in data_gaps(st))
    plan = heuristic_plan(data(), prof, "", ctx, False, {"kpi_map": b["kpis"]})
    assert plan["target"] == "amount"


def test_null_mapping_with_nothing_found_is_a_gap():
    b = run(Seq(reply(kpis={"revenue": None})))
    assert b["kpis"] == {"revenue": None}
    ctx = normalise_context({"kpis": ["revenue"]})
    st = {"context": ctx, "plan": {"date_col": None, "target": None, "relevant_columns": []}, "profile": profile_df(data()), "brief": b}
    assert any("revenue" in g["title"] for g in data_gaps(st))


def test_a_reply_with_a_number_is_rejected_and_falls_back():
    st = {}
    b = run(Seq(reply(confidence=0.9)), st=st)
    assert b["source"] == "fallback" and b["roles"] == {} and b["kinds"] == {} and b["kpis"] == {}
    assert st["decisions"][0]["source"] == "fallback"


def test_one_bad_part_does_not_discard_the_good_parts():
    b = run(Seq(reply(columns={"ghost": {"role": "id"}, "amount": {"role": "banana"}, "pincode": {"role": "id"}})))
    assert b["source"] == "llm" and b["roles"] == {"pincode": "id"} and len(b["dropped"]) == 2


def test_no_llm_gives_the_old_behaviour():
    st = {}
    b = run(None, st=st)
    assert b["source"] == "rules-only" and b["roles"] == {} and b["kinds"] == {} and b["kpis"] == {}
    prof = profile_df(data())
    assert brief.apply_roles(prof, b["roles"]) is prof
    assert additive("amount", {"kinds": b["kinds"]}) is True and additive("qty_score", {"kinds": b["kinds"]}) is False
    assert st["decisions"][0]["source"] == "rules-only"


# ---- hierarchy, goal intent, industry lens -----------------------------------------------------------------------------------------------------

from app import goal as goalmod
from app.context import detect_industry


def hdata():
    n = 240
    rng = np.random.default_rng(5)
    sub = rng.choice(["phones", "laptops", "shirts", "shoes", "sofas", "tables"], n)
    parent = {"phones": "tech", "laptops": "tech", "shirts": "fashion", "shoes": "fashion", "sofas": "home", "tables": "home"}
    return pd.DataFrame({"category": [parent[s] for s in sub], "sub_category": sub, "region": rng.choice(["N", "S", "E", "W"], n),
                         "amount": rng.uniform(10, 500, n).round(2), "day": pd.date_range("2024-01-01", periods=n).astype(str)})


def hrun(llm, st=None, goal="grow revenue"):
    df = hdata()
    return brief.resolve(llm, df, profile_df(df), goal, normalise_context({"kpis": ["revenue"]}), st)


def hreply(**kw):
    base = {"columns": {}, "kpis": {}, "hierarchy": [{"parent": "category", "child": "sub_category"}], "intent": "change", "industry": "ecommerce", "why": "a catalogue"}
    base.update(kw)
    return json.dumps(base)


def test_a_real_hierarchy_is_accepted_and_all_three_decisions_are_logged():
    st = {}
    b = hrun(Seq(hreply()), st=st)
    assert b["hierarchy"] == [{"parent": "category", "child": "sub_category"}] and b["intent"] == "change" and b["industry"] == "ecommerce"
    assert brief.hierarchy_pairs(b) == [("category", "sub_category")]
    by = {d["decision"]: d for d in st["decisions"]}
    assert set(by) == {"analysis brief", "column hierarchy", "goal intent", "industry lens"}
    assert by["goal intent"]["chosen"] == "change" and by["industry lens"]["source"] == "llm" and by["column hierarchy"]["chosen"] == b["hierarchy"]


def test_a_false_hierarchy_is_rejected_and_the_rest_is_kept():
    b = hrun(Seq(hreply(hierarchy=[{"parent": "region", "child": "sub_category"}, {"parent": "sub_category", "child": "category"}])))
    assert b["source"] == "llm" and b["hierarchy"] == [] and b["intent"] == "change"
    assert any("region" in d and "more than one" in d for d in b["dropped"])
    assert any("sub_category" in d and "finer level" in d for d in b["dropped"])


def test_a_hierarchy_over_a_missing_or_non_category_column_is_dropped():
    b = hrun(Seq(hreply(hierarchy=[{"parent": "ghost", "child": "sub_category"}, {"parent": "category", "child": "amount"}])))
    assert b["hierarchy"] == [] and len(b["dropped"]) == 2


def test_the_plan_analyses_the_parent_before_the_child():
    df = hdata()
    prof = profile_df(df)
    flat = heuristic_plan(df, prof, "amount by product", {}, False, {})
    plan = heuristic_plan(df, prof, "amount by product", {}, False, {"hierarchy": [("category", "sub_category")]})
    order = [a["params"]["group"] for a in plan["analyses"] if a["tool"] == "group_compare" and a["params"]["target"] == "amount"]
    assert "category" in order and "sub_category" in order and order.index("category") < order.index("sub_category")
    hm = [a for a in plan["analyses"] if a["tool"] == "heatmap"]
    assert all({a["params"]["group_a"], a["params"]["group_b"]} != {"category", "sub_category"} for a in hm)
    assert [a["tool"] for a in flat["analyses"]]       # no hierarchy: the plan is still built the old way


def test_an_invalid_intent_or_industry_falls_back_to_the_rules():
    st = {}
    b = hrun(Seq(hreply(intent="banana", industry="spaceships")), st=st, goal="is revenue growing?")
    assert b["intent"] is None and b["industry"] is None and b["hierarchy"]
    assert goalmod.asks_about_change("is revenue growing?", False, b["intent"]) is True          # the keyword rule decides
    assert goalmod.asks_about_change("compare regions", False, b["intent"]) is False
    by = {d["decision"]: d for d in st["decisions"]}
    assert by["goal intent"]["source"] == "rules" and by["industry lens"]["chosen"] == detect_industry(normalise_context({"kpis": ["revenue"]}))


def test_the_intent_overrides_the_keyword_match_when_there_is_one():
    assert goalmod.asks_about_change("grow the business", False, "summary") is False
    assert goalmod.asks_about_change("what happened to sales", False, "change") is True
    assert goalmod.asks_about_change("grow", True, "change") is False


def test_no_llm_keeps_the_old_behaviour_for_the_new_parts():
    st = {}
    b = hrun(None, st=st)
    assert b["hierarchy"] == [] and b["intent"] is None and b["industry"] is None and b["source"] == "rules-only"
    assert {d["decision"]: d["source"] for d in st["decisions"]} == {"analysis brief": "rules-only", "column hierarchy": "rules-only", "goal intent": "rules-only", "industry lens": "rules-only"}
    assert brief.hierarchy_pairs(b) == []
