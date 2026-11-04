"""Fix B: no sum of a rate as a key figure; gross profit and gross margin computed by code. Fake AI only."""
import os

os.environ["LLM_PROVIDER"] = "none"

import pandas as pd  # noqa: E402

from app import insights, summary, tiering  # noqa: E402
from tests.fixtures_bakery import bakery  # noqa: E402


def state_for(df, target, derived=None):
    cols = list(df.columns)
    roles = {c: "measure" for c in ("quantity", "unit_price", "unit_cost", "amount", "cost_amount")}
    return {"goal": "", "plan": {"target": target, "relevant_columns": cols, "derived": derived or [], "date_col": None},
            "profile": {"rows": len(df), "cols": len(cols), "columns": [{"name": c, "missing_pct": 0.0} for c in cols]},
            "brief": {"roles": roles, "kinds": {}}, "findings": [{"id": f"r{i}", "tool": "describe", "stats": {}, "significant": False, "params": {"target": c}} for i, c in enumerate(roles)], "key_metrics": []}


def test_a_rate_is_never_summed_as_a_key_figure():
    df = bakery()
    cols, _ = summary.kpi_measures(state_for(df, "amount"), df)
    assert "amount" in cols and "cost_amount" in cols and "quantity" in cols
    assert "unit_price" not in cols and "unit_cost" not in cols
    labels = [m["label"] for m in tiering.key_numbers(state_for(df, "amount"), df)]
    assert "Total unit_price" not in labels and "Total unit_cost" not in labels and "Total amount" in labels


def test_a_rate_as_the_target_has_an_average_and_no_total():
    df = bakery()
    labels = [m["label"] for m in insights.key_metrics(state_for(df, "unit_cost"), df)]
    assert "Average unit_cost" in labels and "Total unit_cost" not in labels


def test_gross_profit_and_margin_are_computed_by_code_from_revenue_and_cost():
    df = bakery()
    figs = {m["label"]: m for m in summary.profit_figures(state_for(df, "amount"), df)}
    profit = df["amount"].sum() - df["cost_amount"].sum()
    assert figs["Gross profit"]["value"] == summary.full(profit)
    assert figs["Gross margin"]["value"] == f"{profit / df['amount'].sum():.1%}"
    shown = {m["label"]: m["value"] for m in tiering.key_numbers(state_for(df, "amount"), df)}
    assert shown["Gross margin"] == figs["Gross margin"]["value"]


def test_revenue_over_cost_is_not_used_as_a_margin():
    df = bakery()
    df["amount_to_cost"] = df["amount"] / df["cost_amount"]
    spec = {"name": "amount_to_cost", "a": "amount", "b": "cost_amount", "kpi": "margin"}
    st = state_for(df, "amount", [spec])
    assert "amount_to_cost" not in summary.kpi_measures(st, df)[1]
    labels = [m["label"] for m in tiering.key_numbers(st, df)]
    assert "Gross margin" in labels and not any("amount_to_cost" in x for x in labels)


def test_no_profit_figures_without_a_cost_column():
    df = bakery().drop(columns=["cost_amount", "unit_cost"])
    assert summary.profit_figures(state_for(df, "amount"), df) == []
