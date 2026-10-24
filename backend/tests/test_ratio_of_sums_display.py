"""A ratio KPI is total A over total B per group and per period, never a mean of row ratios, and it never shows a currency sign."""
from __future__ import annotations

import pandas as pd

from app import graph, tools

SPEC = {"name": "num_to_den_ratio", "a": "num", "b": "den", "kind": "ratio_of_sums", "part_of": True}


def table() -> pd.DataFrame:
    # group "big": one huge row at 10% and one tiny row at 90%  -> mean of row ratios 0.50, ratio of sums 0.1089
    # group "small": rows at 20% and 30% of equal size          -> both measures 0.25
    rows = []
    for month in ("2024-01-15", "2024-02-15", "2024-03-15", "2024-04-15", "2024-05-15", "2024-06-15"):
        rows += [("big", month, 100.0, 1000.0), ("big", month, 9.0, 10.0), ("small", month, 20.0, 100.0), ("small", month, 30.0, 100.0)]
    df = pd.DataFrame(rows, columns=["grp", "when", "num", "den"])
    df["when"] = pd.to_datetime(df["when"])
    df[SPEC["name"]] = df["num"] / df["den"]
    return df


CTX = {"currency": "$", "derived": {SPEC["name"]: SPEC}}
BIG = 109.0 / 1010.0


def test_group_table_uses_ratio_of_sums_without_currency_sign():
    out = tools.group_table(table(), CTX, SPEC["name"], "grp")
    cells = {r[0]: r[2] for r in out["table"]["rows"]}
    assert cells["big"] == tools._fmt(BIG) and cells["big"] != tools._fmt(0.5)
    assert cells["small"] == tools._fmt(0.25)
    assert "$" not in "".join(map(str, out["table"]["rows"]))


def test_monthly_trend_uses_ratio_of_sums_without_currency_sign(tmp_path):
    df = table()
    out = tools.trend(df, {**CTX, "charts_dir": tmp_path}, SPEC["name"], "when")
    assert out["stats"]["agg"] == "ratio of sums"
    assert "$" not in out["text"]
    expected = (df.groupby("when")["num"].sum() / df.groupby("when")["den"].sum())
    assert tools._fmt(expected.max()) in out["text"]            # a month's value is its total A over total B (0.2179 here), not its mean of row ratios


def test_group_compare_text_reports_ratio_of_sums_without_currency_sign(tmp_path):
    out = tools.group_compare(table(), {**CTX, "charts_dir": tmp_path}, SPEC["name"], "grp")
    assert "$" not in out["text"] and "ratio of sums" in out["text"]
    assert tools._fmt(BIG) in out["text"]


def test_analyze_node_hands_the_derived_specs_to_the_tools(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(graph, "_wd", lambda state: tmp_path)
    monkeypatch.setattr(graph, "emit", lambda *a, **k: None)
    monkeypatch.setitem(tools.REGISTRY, "probe", lambda df, ctx, **p: seen.update(ctx=ctx))
    table().to_pickle(tmp_path / "clean.pkl")
    state = {"run_id": "r", "iteration": 0, "plan": {"analyses": [{"tool": "probe", "params": {}}], "derived": [SPEC]}, "context": {"currency": "$"}, "findings": []}
    graph.analyze_node(state)
    assert seen["ctx"]["derived"] == {SPEC["name"]: SPEC}
