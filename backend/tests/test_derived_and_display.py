"""Derived metrics are proposed by an LLM and validated on the data; charts show a trimmed view while the statistics use all the data."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from app import derived, tools


class Msg:
    def __init__(self, content):
        self.content = content


class Proposer:
    def __init__(self, metrics):
        self.metrics = metrics

    def invoke(self, messages):
        return Msg(json.dumps({"metrics": self.metrics}))


def sales(n=600, seed=5):
    rng = np.random.default_rng(seed)
    quantity = rng.integers(1, 13, n)
    price = rng.lognormal(4, 0.7, n)
    amount = (quantity * price).round(2)
    profit = (amount * rng.normal(0.2, 0.08, n)).round(2)
    return pd.DataFrame({"quantity": quantity, "amount": amount, "profit": profit})


# ---------------------------------------------------------------------------------------- A: derived metrics

def test_rules_alone_never_invent_a_ratio():
    assert derived.propose(None, sales(), ["quantity", "amount", "profit"], "goal") == ([], [])


def test_quantity_over_amount_is_rejected_and_profit_over_amount_is_created():
    df = sales()
    ok, why, _ = derived.check_ratio(df, "quantity", "amount")
    assert not ok and why
    ok, _, facts = derived.check_ratio(df, "profit", "amount")
    assert ok and facts["part_of"]
    llm = Proposer([{"a": "quantity", "b": "amount", "meaning": "Units per currency unit of sales."},
                    {"a": "profit", "b": "amount", "meaning": "Share of sales kept as profit."}])
    accepted, rejected = derived.propose(llm, df, ["quantity", "amount", "profit"], "goal")
    assert [(d["a"], d["b"]) for d in accepted] == [("profit", "amount")] and accepted[0]["name"] == "profit_to_amount_ratio"
    assert accepted[0]["meaning"] == "Share of sales kept as profit." and accepted[0]["part_of"]
    assert [(r["a"], r["b"]) for r in rejected] == [("quantity", "amount")]


def test_a_proposal_without_a_meaning_is_rejected():
    accepted, rejected = derived.propose(Proposer([{"a": "profit", "b": "amount", "meaning": ""}]), sales(), ["quantity", "amount", "profit"], "goal")
    assert accepted == [] and "meaning" in rejected[0]["reason"]


def test_a_ratio_that_is_a_function_of_its_denominator_is_rejected():
    rng = np.random.default_rng(1)
    b = rng.lognormal(3, 0.8, 500)
    df = pd.DataFrame({"a": 50 + rng.normal(0, 1, 500), "b": b})            # a is almost constant, so a / b is just the inverse of b
    ok, why, _ = derived.check_ratio(df, "a", "b")
    assert not ok and "function of" in why


def test_columns_on_different_scales_are_rejected():
    rng = np.random.default_rng(2)
    b = rng.lognormal(3, 0.5, 500)
    df = pd.DataFrame({"a": b * rng.uniform(0.0001, 0.0003, 500), "b": b})
    ok, why, _ = derived.check_ratio(df, "a", "b")
    assert not ok and "comparable" in why


def test_part_of_ratios_rank_before_others():
    rng = np.random.default_rng(3)
    amount = rng.lognormal(5, 0.5, 500)
    df = pd.DataFrame({"amount": amount, "profit": amount * rng.uniform(0.1, 0.3, 500), "other": amount * rng.uniform(1.5, 2.5, 500)})
    llm = Proposer([{"a": "other", "b": "amount", "meaning": "Other as a multiple of amount."}, {"a": "profit", "b": "amount", "meaning": "Profit as a share of amount."}])
    accepted, _ = derived.propose(llm, df, ["amount", "profit", "other"], "goal")
    assert accepted and accepted[0]["a"] == "profit"


def test_derived_values_are_ratios_of_sums_in_charts(tmp_path):
    rng = np.random.default_rng(4)
    n = 600
    amount = rng.lognormal(5, 0.6, n)
    g = rng.choice(["a", "b", "c"], n)
    profit = amount * np.where(g == "a", 0.3, 0.1) * rng.uniform(0.5, 1.5, n)
    df = pd.DataFrame({"g": g, "amount": amount, "profit": profit})
    spec = {"kind": "ratio_of_sums", "a": "profit", "b": "amount", "name": "p_ratio"}
    df["p_ratio"] = df["profit"] / df["amount"]
    f = tools.group_compare(df, {"charts_dir": tmp_path, "derived": {"p_ratio": spec}}, "p_ratio", "g")
    want = {k: float(s["profit"].sum() / s["amount"].sum()) for k, s in df.groupby("g")}
    assert f["stats"]["ratio_of_sums"] and f["stats"]["values"] == pytest.approx(want, abs=1e-5)
    assert f["stats"]["best"] == "a"


# ---------------------------------------------------------------------------------------- B: trimmed charts, full statistics

def groups_df(k, n_per=30, seed=0):
    rng = np.random.default_rng(seed)
    names = [f"group_{i:02d}" for i in range(k)]
    sizes = np.linspace(n_per * 2, n_per, k).astype(int)
    g = np.concatenate([[nm] * s for nm, s in zip(names, sizes)])
    base = {nm: 100 + 7 * i for i, nm in enumerate(names)}
    y = np.array([base[x] for x in g]) + rng.normal(0, 25, len(g))
    return pd.DataFrame({"g": g, "y": y})


@pytest.mark.parametrize("k,shown_n", [(17, 8), (40, 8), (3, 3)])
def test_group_compare_shows_top_n_with_a_correct_other_bar_and_unchanged_statistics(tmp_path, k, shown_n):
    df = groups_df(k)
    f = tools.group_compare(df, {"charts_dir": tmp_path}, "y", "g")
    st = f["stats"]
    means = df.groupby("g")["y"].mean().sort_values(ascending=False)
    # statistics use every group
    assert st["groups"] == k and st["n"] == len(df)
    assert st["p"] == pytest.approx(float(stats.f_oneway(*[s["y"].values for _, s in df.groupby("g")])[1]), rel=1e-4, abs=1e-12)
    # the stated rule and the headline groups
    assert st["shown_n"] == shown_n and st["total_groups"] == k
    assert st["best"] == means.index[0] and st["worst"] == means.index[-1]
    assert st["best"] in st["shown"] and st["worst"] in st["shown"]
    if k > 8:
        rest = [g for g in means.index if g not in st["shown"]]
        assert len(rest) == k - 8 and st["other"]["groups"] == k - 8
        assert st["other"]["value"] == pytest.approx(float(df[df["g"].isin(rest)]["y"].mean()), abs=1e-4)      # pooled mean, not a mean of group means
        assert st["other"]["n"] == int(df["g"].isin(rest).sum())
        assert f"top 8 of {k}" in f["text"]
        assert [g for g in st["shown"]] == [g for g in means.index if g in st["shown"]]                      # ranked by the plotted value
    else:
        assert "other" not in st and "top 8" not in f["text"]


def test_group_compare_keeps_the_worst_group_even_when_it_is_not_in_the_top_8(tmp_path):
    df = groups_df(17)
    f = tools.group_compare(df, {"charts_dir": tmp_path}, "y", "g")
    assert len(f["stats"]["shown"]) == 8 and f["stats"]["worst"] in f["stats"]["shown"]


def test_group_table_carries_every_group_and_the_page_trims(tmp_path):
    f = tools.group_table(groups_df(40), {"charts_dir": tmp_path}, "y", "g")
    assert len(f["table"]["rows"]) == 40 and f["table"]["total"] == 40


def test_category_share_top_7_plus_other(tmp_path):
    df = groups_df(17)
    f = tools.category_share(df, {"charts_dir": tmp_path}, "g")
    assert f["stats"]["shown_n"] == 7 and f["stats"]["other"] == {"groups": 10, "rows": int(df["g"].value_counts().iloc[7:].sum())}
    f3 = tools.category_share(groups_df(3), {"charts_dir": tmp_path}, "g")
    assert f3["stats"]["other"] is None and f3["stats"]["shown_n"] == 3


def test_segment_trend_tests_every_group_but_draws_five(tmp_path):
    rng = np.random.default_rng(7)
    n = 3000
    g = rng.choice([f"s{i:02d}" for i in range(17)], n)
    t = pd.Timestamp("2023-01-01") + pd.to_timedelta(rng.integers(0, 700, n), unit="D")
    df = pd.DataFrame({"g": g, "t": t, "y": rng.normal(100, 10, n)})
    f = tools.segment_trend(df, {"charts_dir": tmp_path}, "y", "t", "g")
    st = f["stats"]
    assert st["total_groups"] == len(st["groups"]) == 17 and st["shown_n"] == 5
    assert st["fastest"] in st["shown"] and st["slowest"] in st["shown"]


def test_histogram_is_clipped_at_p99_and_says_how_many_values_are_beyond(tmp_path):
    rng = np.random.default_rng(8)
    x = np.concatenate([rng.normal(50, 5, 1990), rng.uniform(300, 900, 10)])
    f = tools.distribution(pd.DataFrame({"x": x}), {"charts_dir": tmp_path}, "x")
    st = f["stats"]
    assert st["clipped"] and st["beyond_p99"] == int((x > np.quantile(x, 0.99)).sum()) and not st["log_axis"]
    assert st["n"] == 2000 and st["mean"] == pytest.approx(float(x.mean()), rel=1e-4)       # statistics use every value
    heavy = pd.DataFrame({"x": np.random.default_rng(9).lognormal(2, 2.2, 3000)})
    assert tools.distribution(heavy, {"charts_dir": tmp_path}, "x")["stats"]["log_axis"]


def test_heatmap_draws_6_by_6_but_headline_cells_come_from_all_data(tmp_path):
    rng = np.random.default_rng(10)
    n = 6000
    a, b = rng.choice([f"a{i:02d}" for i in range(12)], n), rng.choice([f"b{i:02d}" for i in range(12)], n)
    y = rng.normal(100, 10, n)
    f = tools.heatmap(pd.DataFrame({"a": a, "b": b, "y": y}), {"charts_dir": tmp_path}, "y", "a", "b")
    st = f["stats"]
    assert len(st["shown_rows"]) == 6 and len(st["shown_cols"]) == 6 and st["total_rows"] == 12
    assert st["top_cell"][0] in st["shown_rows"] and st["bottom_cell"][1] in st["shown_cols"]


def test_composition_test_uses_all_rows(tmp_path):
    rng = np.random.default_rng(11)
    n = 4000
    df = pd.DataFrame({"g": rng.choice([f"g{i:02d}" for i in range(17)], n), "b": rng.choice([f"b{i:02d}" for i in range(12)], n)})
    f = tools.composition(df, {"charts_dir": tmp_path}, "g", "b")
    assert f["stats"]["n"] == n and len(f["stats"]["shown_rows"]) == 7 and f["stats"]["total_rows"] == 17


# ---------------------------------------------------------------------------------------- A + C end to end

class ProposeOnly:
    """Answers the derived-metric request; every other agent gets non-JSON and falls back to its rules."""

    def invoke(self, messages):
        if "derived metrics" in messages[0][1]:
            return Msg(json.dumps({"metrics": [{"a": "quantity", "b": "amount", "meaning": "Units sold per currency unit."},
                                               {"a": "profit", "b": "amount", "meaning": "Share of sales kept as profit."}]}))
        return Msg("not json")


def growth_data(n=900, seed=12):
    rng = np.random.default_rng(seed)
    df = sales(n, seed)
    df["order_date"] = (pd.Timestamp("2023-01-01") + pd.to_timedelta(rng.integers(0, 700, n), unit="D")).strftime("%Y-%m-%d")
    df["region"] = rng.choice(["North", "South", "East", "West"], n)
    return df


def test_full_run_creates_only_the_sound_ratio_and_answers_the_growth_goal():
    from app import llm as llm_mod, runner
    from app.config import RUNS_DIR
    df = growth_data()
    llm_mod._cache["llm"] = (ProposeOnly(), "fake:model")
    try:
        rid = runner.create_run("Is amount growing over time?", "d.csv", df.to_csv(index=False).encode())
        runner.start_run(rid).join(timeout=300)
        if runner.read_status(rid)["status"] == "needs_input":
            assert runner.resume_with_answers(rid, {"answers": {}, "skip": True, "notes": ""})
            import time
            for _ in range(600):
                if runner.read_status(rid)["status"] in ("done", "error"):
                    break
                time.sleep(0.5)
    finally:
        llm_mod.reset_cache()
    res = json.loads((RUNS_DIR / rid / "result.json").read_text())
    dm = res["derived_metrics"]
    assert [d["name"] for d in dm["created"]] == ["profit_to_amount_ratio"] and [(r["a"], r["b"]) for r in dm["rejected"]] == [("quantity", "amount")]
    assert "quantity_to_amount_ratio" not in pd.read_pickle(RUNS_DIR / rid / "clean.pkl").columns
    md = res["report_md"]
    assert "<summary>Definitions used" in md and "Share of sales kept as profit." in md and "Not created: quantity / amount" in md
    ans = res["goal_answer"]
    assert "amount" in ans["sentence"] and ("growth trend" in ans["sentence"] or "growing" in ans["sentence"] or "falling" in ans["sentence"])
    assert "**Answer to your goal.**" in md
    ids = set(ans["finding_ids"])
    assert {f["tool"] for f in res["findings"] if f["id"] in ids} >= {"trend"}
    assert any(f["tool"] == "segment_trend" for f in res["findings"])
