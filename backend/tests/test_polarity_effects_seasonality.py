"""Metric direction, effect-size wording, additive seasonality and scope. No network, no API key."""
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np
import pandas as pd
import pytest

from app import insights as ins
from app import polarity as pol
from app import strategy, tools
from app.planner import heuristic_plan, plausible_dimensions
from app.profiler import profile_df


# ----------------------------------------------------------------- helpers
def _trend(target, slope=1.0, rel=0.3):
    return {"id": "f1", "tool": "trend", "title": f"{target} over time", "text": "x", "params": {"target": target, "date": "d"},
            "stats": {"slope": slope, "relative_change": rel, "p": 0.001, "n": 500, "periods": 24, "freq": "MS", "effect": 0.5, "effect_name": "spearman_rho"},
            "significant": True, "action": "act"}


def _group(target, eta=0.2, top="North", bot="South", hv=12.0, lv=8.0):
    return {"id": "f2", "tool": "group_compare", "title": f"{target} by region", "text": "x", "params": {"target": target, "group": "region"},
            "stats": {"kind": "numeric", "p": 0.0001, "effect": eta, "effect_name": "eta_squared", "n": 900, "min_group_n": 200, "best": top, "worst": bot,
                      "values": {top: hv, bot: lv}, "survives_correction": True},
            "significant": True, "action": "act"}


def _state(findings, polarity=None):
    s = {"findings": findings, "context": {}, "plan": {"target": None}, "goal": "g", "data_gaps": []}
    if polarity:
        s["polarity"] = polarity
    return s


class FakeLLM:
    def __init__(self, content):
        self.content, self.calls = content, 0

    def invoke(self, msgs):
        self.calls += 1
        return type("R", (), {"content": self.content})()


# ----------------------------------------------------------------- 1. polarity
def test_heuristic_directions():
    assert pol.heuristic("revenue") == pol.HIGHER and pol.heuristic("Profit") == pol.HIGHER
    for lower in ("discount", "delivery_time", "shipping_cost", "return_rate", "churn", "DeliveryDays"):
        assert pol.heuristic(lower) == pol.LOWER, lower
    for neutral in ("order_id", "quantity", "customer_age"):
        assert pol.heuristic(neutral) == pol.NEUTRAL, neutral
    assert pol.heuristic("calculated_margin") == pol.HIGHER     # 'late' inside 'calculated' is not a delay


def test_llm_answer_is_validated():
    names = ["revenue", "discount", "order_id", "weird", "made_up_extra"]
    raw = {"measures": {"revenue": "higher", "discount": "Lower is better", "order_id": "higher", "weird": "banana", "not_asked": "lower"}}
    out = pol.validate(raw, names)
    assert out["revenue"] == pol.HIGHER and out["discount"] == pol.LOWER
    assert out["order_id"] == pol.NEUTRAL          # identifiers are always neutral, whatever the model says
    assert out["weird"] == pol.heuristic("weird")  # invalid value -> heuristic
    assert "not_asked" not in out and set(out) == set(names)


def test_one_llm_call_and_fallback():
    llm = FakeLLM('{"measures": {"revenue": "higher", "discount": "lower"}}')
    out, src = pol.resolve(llm, ["revenue", "discount"], "goal")
    assert llm.calls == 1 and src == "llm" and out == {"revenue": pol.HIGHER, "discount": pol.LOWER}
    out, src = pol.resolve(FakeLLM("not json"), ["discount"], "goal")
    assert src == "rules" and out["discount"] == pol.LOWER
    assert pol.resolve(None, ["discount"], "goal")[1] == "rules"


def test_rising_discount_is_worrying_not_favourable():
    st = _state([_trend("discount")])
    ins.build_insights(st)
    why = st["findings"][0]["insight"]["why_it_matters"]
    assert "worrying" in why and "favourable" not in why
    st = _state([_trend("revenue")])
    ins.build_insights(st)
    assert "favourable" in st["findings"][0]["insight"]["why_it_matters"]
    st = _state([_trend("discount", slope=-1.0, rel=-0.2)])
    ins.build_insights(st)
    assert "favourable" in st["findings"][0]["insight"]["why_it_matters"]


def test_llm_polarity_overrides_the_name_rules():
    st = _state([_trend("widgets", slope=1.0)], polarity={"widgets": pol.LOWER})
    ins.build_insights(st)
    assert "worrying" in st["findings"][0]["insight"]["why_it_matters"]


def test_neutral_measure_gets_no_verdict_and_no_sustain_strategy():
    st = _state([_trend("quantity")])
    ins.build_insights(st)
    why = st["findings"][0]["insight"]["why_it_matters"]
    assert "favourable" not in why and "worrying" not in why
    st["plan"] = {"target": "quantity"}
    titles = [c["title"] for c in strategy.candidates(st)]
    assert not any("sustain" in t or "wrong way" in t for t in titles)


def test_never_recommend_increasing_a_lower_is_better_metric():
    st = _state([_trend("discount")])
    ins.build_insights(st)
    st["plan"] = {"target": "discount"}
    cand = [c for c in strategy.candidates(st) if c["kind"] == "data"][0]
    assert "wrong way" in cand["title"] and "sustain" not in cand["title"]
    assert "reverse" in cand["objective"] and "extend" not in cand["objective"]


def test_lower_is_better_ranking_names_the_lowest_group_as_best():
    st = _state([_group("delivery_time", eta=0.2, top="North", bot="South", hv=12.0, lv=8.0)])
    ins.build_insights(st)
    ins_ = st["findings"][0]["insight"]
    assert "lower is better" in ins_["headline"] and "South performs best" in ins_["headline"]
    assert "strongest" not in ins_["why_it_matters"] and "putting more" not in ins_["why_it_matters"]
    st["plan"] = {"target": "delivery_time"}
    titles = [c["title"] for c in strategy.candidates(st) if c["kind"] == "data"]
    assert titles and all("Put more behind what works" not in t for t in titles) and "reduction" in titles[0]


def test_higher_is_better_ranking_unchanged_and_neutral_is_descriptive():
    st = _state([_group("revenue")])
    ins.build_insights(st)
    assert "strongest" in st["findings"][0]["insight"]["why_it_matters"]
    st = _state([_group("quantity")])
    ins.build_insights(st)
    why = st["findings"][0]["insight"]["why_it_matters"]
    assert "strongest" not in why and "performance ranking" in why
    st["plan"] = {"target": "quantity"}
    assert not any("Put more behind" in c["title"] for c in strategy.candidates(st))


# ----------------------------------------------------------------- 2. effect-size labels
def test_headline_effect_word_comes_from_eta_squared_not_the_mean_gap():
    f = _group("revenue", eta=0.6, hv=10.4, lv=10.0)        # the means are 4% apart, the effect is large
    st = _state([f])
    ins.build_insights(st)
    h = st["findings"][0]["insight"]["headline"]
    assert "large effect" in h and "small" not in h and "slightly" not in h
    f = _group("revenue", eta=0.02, hv=30.0, lv=10.0)       # the means are 3x apart, the effect is small
    st = _state([f])
    ins.build_insights(st)
    assert "small effect" in st["findings"][0]["insight"]["headline"]


def test_effect_label_cutoffs():
    assert ins.effect_label({"effect": 0.6, "effect_name": "eta_squared"}) == "large"
    assert ins.effect_label({"effect": 0.005, "effect_name": "eta_squared"}) == "negligible"
    assert ins.effect_label({"effect": 0.5, "effect_name": "cramers_v"}) == "large"


# ----------------------------------------------------------------- 3. seasonality on totals
def _sales(years=3, busy_month=12, seed=1):
    """Every row has the same amount noise, but the busy month has 4x as many rows: per-row averages are flat, monthly totals are not."""
    rng = np.random.default_rng(seed)
    rows = []
    for y in range(2021, 2021 + years):
        for m in range(1, 13):
            n = 120 if m == busy_month else 30
            days = pd.Timestamp(y, m, 1).days_in_month
            for d in rng.integers(1, days + 1, n):
                rows.append({"order_date": pd.Timestamp(y, m, int(d)), "sales_amount": float(rng.normal(100, 10)), "discount_pct": float(rng.normal(0.1, 0.02))})
    return pd.DataFrame(rows)


def test_additive_month_pattern_is_tested_on_monthly_totals(tmp_path):
    df = _sales()
    f = tools.seasonality(df, {"charts_dir": tmp_path}, "sales_amount", "order_date")
    assert f is not None and f["stats"]["cycle"] == "month" and f["stats"]["basis"] == "period totals"
    assert f["significant"] and f["stats"]["best"] == "Dec"
    assert f["stats"]["min_group_n"] == 3                  # three monthly totals (one per year) behind each calendar month
    assert "monthly total" in f["text"]


def test_rate_measures_keep_per_row_averages(tmp_path):
    f = tools.seasonality(_sales(), {"charts_dir": tmp_path}, "discount_pct", "order_date")
    # a flat rate is not seasonal even though December has four times the rows
    assert f is None or (f["stats"]["basis"] == "row averages" and not f["significant"])


def test_month_pattern_needs_two_years(tmp_path):
    df = _sales(years=2)
    df = df[df["order_date"] < pd.Timestamp(2022, 9, 1)]      # about 20 months
    f = tools.seasonality(df, {"charts_dir": tmp_path}, "sales_amount", "order_date")
    assert f is None or f["stats"]["cycle"] != "month"


# ----------------------------------------------------------------- scope
def test_scope_keeps_categorical_columns_that_could_drive_the_kpi():
    rng = np.random.default_rng(7)
    n = 400
    df = pd.DataFrame({"profit": rng.normal(50, 10, n), "order_date": pd.date_range("2022-01-01", periods=n, freq="D"),
                       **{f"seg{i}": rng.choice(list("ABCD"), n) for i in range(5)}, "category": rng.choice(["Furniture", "Tech", "Office"], n)})
    plan = heuristic_plan(df, profile_df(df), "Why is profit low?")
    for c in ("category", "seg0", "seg4"):
        assert c in plan["relevant_columns"], c
    roles = {c["name"]: c["role"] for c in profile_df(df)["columns"]}
    assert plausible_dimensions(df, roles, {c: df[c].nunique() for c in df.columns}, None, []) == []   # no target, no claim
