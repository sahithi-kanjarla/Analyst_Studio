"""A derived ratio A/B is total A over total B per group / month / cell, shown as % or a plain number (never money); data gaps do not deny margin or cost data that exists."""
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from app import dashboard, derived, insights, powerbi, tools  # noqa: E402

SPEC = {"kind": "ratio_of_sums", "a": "profit", "b": "sales", "name": "margin_ratio", "part_of": True}


def skewed(n=900, seed=3):
    """In group x many small sales have a high ratio and a few big sales a low one, so the mean of row ratios (high) disagrees with total profit / total sales (low).
    Group y is steady at 0.3 either way."""
    rng = np.random.default_rng(seed)
    t = pd.Timestamp("2024-01-01") + pd.to_timedelta(np.sort(rng.integers(0, 700, n)), unit="D")
    g = rng.choice(["x", "y"], n)
    sales = np.where(g == "x", np.where(rng.random(n) < 0.05, 5000.0, rng.uniform(5, 15, n)), rng.uniform(50, 150, n))
    ratio = np.where(g == "x", np.where(sales > 1000, 0.05, 0.55), 0.3)
    df = pd.DataFrame({"when": t, "grp": g, "region": rng.choice(["n", "s"], n), "sales": sales, "profit": sales * ratio})
    df["margin_ratio"] = df["profit"] / df["sales"]
    return df


CTX = lambda tmp: {"charts_dir": tmp, "derived": {"margin_ratio": SPEC}, "currency": "$"}      # noqa: E731


def ros(d):
    return float(d["profit"].sum() / d["sales"].sum())


def test_row_mean_and_ratio_of_sums_really_disagree_in_the_fixture():
    df = skewed()
    x = df[df.grp == "x"]
    assert x["margin_ratio"].mean() > 0.4 > ros(x)


# ------------------------------------------------------------------ 1. every view uses sum A / sum B
def test_group_compare_ranks_and_tests_the_ratio_of_sums(tmp_path):
    df = skewed()
    f = tools.group_compare(df, CTX(tmp_path), "margin_ratio", "grp")
    assert f["stats"]["values"] == pytest.approx({"x": ros(df[df.grp == "x"]), "y": ros(df[df.grp == "y"])}, abs=1e-5)
    assert f["stats"]["best"] == "y" and "permutation test on the ratio of sums" in f["text"] and "ANOVA" not in f["text"]


def test_permutation_test_separates_real_from_shuffled_differences():
    df = skewed()
    p, eff = derived.ratio_permutation_test(df, SPEC, df["grp"])
    assert p < 0.01 and 0 < eff <= 1
    shuffled = pd.Series(np.random.default_rng(0).permutation(df["grp"].values), index=df.index)
    p2, _ = derived.ratio_permutation_test(df, SPEC, shuffled)
    assert p2 > 0.05
    assert derived.ratio_permutation_test(df, SPEC, df["grp"]) == (p, eff)           # seeded: the same answer every time


def test_group_table_trend_and_heatmap_use_the_ratio_of_sums(tmp_path):
    df = skewed()
    ctx = CTX(tmp_path)
    tbl = tools.group_table(df, ctx, "margin_ratio", "grp")
    rows = {r[0]: r for r in tbl["table"]["rows"]}
    assert "ratio of sums" in tbl["table"]["columns"][2].lower() and float(rows["x"][2]) == pytest.approx(round(ros(df[df.grp == "x"]), 2), abs=0.011)
    tr = tools.trend(df, ctx, "margin_ratio", "when")
    assert "ratio of sums" in tr["text"]
    h = tools.heatmap(df, ctx, "margin_ratio", "grp", "region")
    cell = df[(df.grp == h["stats"]["top_cell"][0]) & (df.region == h["stats"]["top_cell"][1])]
    assert h["stats"]["top_value"] == pytest.approx(ros(cell), abs=1e-5)


def test_seasonality_shows_total_over_total_per_weekday(tmp_path):
    df = skewed(n=1500)
    f = tools.seasonality(df, CTX(tmp_path), "margin_ratio", "when")
    if f is None:
        pytest.skip("no weekday pattern testable on this fixture")
    assert f["stats"]["basis"] == "ratio of sums" and "total profit over total sales" in f["text"]
    top = df[df["when"].dt.day_name().str[:3] == f["stats"]["best"]]
    assert f["stats"]["values"][f["stats"]["best"]] == pytest.approx(ros(top), abs=1e-5)


# ------------------------------------------------------------------ 2. no currency symbol on a ratio
def test_a_ratio_never_gets_a_currency_symbol(tmp_path):
    ctx = CTX(tmp_path)
    ctx["derived"] = {"profit_ratio": {**SPEC, "name": "profit_ratio"}}
    assert tools._cur("profit_ratio", ctx) == "" and tools._cur("profit", ctx) == "$" and tools._cur("sales", ctx) == "$"
    df = skewed().rename(columns={"margin_ratio": "profit_ratio"})
    f = tools.group_compare(df, ctx, "profit_ratio", "grp")
    assert "$" not in f["text"]
    assert "$" in tools.group_compare(df, ctx, "profit", "grp")["text"]                  # the money column keeps its symbol


def test_key_numbers_show_a_ratio_as_percent_without_currency():
    df = skewed()
    state = {"context": {"currency": "$"}, "plan": {"target": "margin_ratio", "relevant_columns": ["margin_ratio"], "derived": [SPEC], "date_col": None},
             "profile": {"rows": len(df), "cols": 5, "columns": [{"name": "margin_ratio", "missing_pct": 0.0}]}, "findings": []}
    km = {m["label"]: m for m in insights.key_metrics(state, df)}
    label = next(k for k in km if k.startswith("margin_ratio"))
    assert km[label]["value"] == f"{ros(df):.1%}" and "$" not in km[label]["value"]
    assert not any("Average margin_ratio" in k for k in km)


def test_dashboard_weights_ratio_rows_by_their_denominator():
    df = skewed()
    vals, spec = dashboard._measure(df, "margin_ratio", "$", None, {"margin_ratio": SPEC})
    assert spec["agg"] == "ratio" and spec["fmt"] == "pct" and spec["den"] == "__w_margin_ratio" and spec["den_col"] == "sales"
    assert float((df["margin_ratio"] * df["sales"]).sum() / df["sales"].sum()) == pytest.approx(ros(df))      # what the page computes from value x weight
    vals2, spec2 = dashboard._measure(df, "sales", "$", None, {"margin_ratio": SPEC})
    assert spec2["fmt"] == "money"


def test_powerbi_measure_for_a_ratio_is_divide_of_sums():
    df = skewed()
    ms = powerbi.build_measures(df, {"target": "margin_ratio", "date_col": "when", "analyses": [], "derived": [SPEC]}, {"margin_ratio": "numeric"})
    dax = next(m["dax"] for m in ms if m["name"] == "margin_ratio")
    assert dax == "margin_ratio = DIVIDE(SUM('Data'[profit]), SUM('Data'[sales]))" or "DIVIDE(SUM(" in dax
    assert not any("AVERAGE" in m["dax"] and "margin_ratio" in m["dax"] for m in ms)


# ------------------------------------------------------------------ 3. data gaps
def gap_state(columns, derived_specs=()):
    return {"context": {"industry_key": "ecommerce"}, "plan": {"target": "sales", "date_col": "when", "relevant_columns": columns, "derived": list(derived_specs)},
            "profile": {"rows": 5000, "cols": len(columns), "columns": [{"name": c, "role": "numeric", "missing_pct": 0.0} for c in columns]}, "brief": None}


def titles(state):
    return [g["title"] for g in insights.data_gaps(state)]


def test_data_gaps_do_not_deny_margin_or_cost_data_that_exists():
    assert any("no cost column" in t for t in titles(gap_state(["sales", "when", "units"])))                       # genuinely absent: still reported
    assert not any("no cost column" in t for t in titles(gap_state(["sales", "when", "profit"])))                  # a profit measure exists
    assert not any("no cost column" in t for t in titles(gap_state(["sales", "when", "gross_profit"])))
    derived_only = {**SPEC, "a": "earnings", "b": "sales", "name": "earnings_to_sales_ratio"}
    assert not any("no cost column" in t for t in titles(gap_state(["sales", "when", "earnings"], [{**derived_only, "name": "margin_ratio"}])))     # a derived margin ratio exists
