"""Fix D: columns that are exact arithmetic of others are marked derived and kept out of relationship findings and levers; a code and its label are one dimension. Fake AI only."""
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app import derived_cols as dc, strategy, tools  # noqa: E402
from tests.fixtures_bakery import bakery  # noqa: E402
from tests.test_report_wording import fake, state_with, with_insights  # noqa: E402


def test_the_bakerys_totals_are_found_as_products_of_their_inputs():
    d = dc.derived_columns(bakery())
    assert set(d) == {"amount", "cost_amount"}
    assert sorted(d["amount"]["inputs"]) == ["quantity", "unit_price"] and d["amount"]["op"] == "product"
    assert sorted(d["cost_amount"]["inputs"]) == ["quantity", "unit_cost"]
    assert dc.describe(d["amount"]) in ("amount = quantity × unit_price", "amount = unit_price × quantity")


def test_a_sum_and_a_ratio_are_found_and_independent_columns_are_not():
    rng = np.random.default_rng(0)
    a, b = rng.uniform(10, 100, 200), rng.uniform(5, 50, 200)
    df = pd.DataFrame({"a": a, "b": b, "total": np.round(a + b, 2), "share": np.round(a / (a + b), 4), "noise": rng.normal(0, 1, 200)})
    d = dc.derived_columns(df)
    assert d["total"]["op"] == "sum" and "noise" not in d
    assert "share" in d and d["share"]["op"] == "ratio"
    assert not dc.derived_columns(pd.DataFrame({"x": rng.normal(0, 1, 100), "y": rng.normal(0, 1, 100), "z": rng.normal(0, 1, 100)}))


def test_no_correlation_or_scatter_between_a_derived_column_and_its_inputs_or_two_derived_columns(tmp_path):
    df = bakery()
    ctx = {"charts_dir": tmp_path}
    for x, y in (("amount", "quantity"), ("amount", "unit_price"), ("cost_amount", "unit_cost"), ("amount", "cost_amount")):
        assert tools.scatter(df, ctx, x, y) is None, (x, y)
    f = tools.correlation(df, ctx, ["quantity", "unit_price", "unit_cost", "amount", "cost_amount"])
    named = {frozenset(k.split("|")) for k in (f["stats"]["top_pairs"] if f else {})}
    assert not ({frozenset(("amount", "quantity")), frozenset(("amount", "unit_price")), frozenset(("amount", "cost_amount")), frozenset(("cost_amount", "quantity"))} & named)
    assert f is None or "Not reported because one column is calculated" in f["text"]


def test_a_derived_column_is_never_the_lever_in_an_idea():
    def state(x, y):
        f = fake("scatter", True, x=x, y=y)
        f["stats"] = {"x": x, "y": y, "p": 0.001, "rho": 0.5}
        f["action"] = "act"
        return {**with_insights(state_with([f])), "derived_columns": {"amount": {"result": "amount", "op": "product", "inputs": ["quantity", "unit_price"]}}}
    titles = lambda st: [c["title"] for c in strategy.candidates(st) if c["kind"] == "data"]
    assert titles(state("amount", "age")) == ["Test whether age is a lever for amount"]            # the other column leads
    assert titles(state("age", "amount")) == ["Test whether age is a lever for amount"]
    assert titles(state("age", "visits")) == ["Test whether age is a lever for visits"]
    both = state("amount", "amount2")
    both["derived_columns"]["amount2"] = {"result": "amount2", "op": "sum", "inputs": ["a", "b"]}
    assert titles(both) == []


def test_a_code_and_its_label_are_one_dimension_and_are_analysed_once():
    df = bakery()
    canon = dc.canonical_dimensions(df, ["item_code", "item", "category", "payment_mode"])
    assert canon["item_code"] == canon["item"] == "item"                                  # the label reads better than the code
    assert "category" not in canon and "payment_mode" not in canon
    specs = [{"tool": "group_compare", "params": {"target": "amount", "group": "item_code"}}, {"tool": "group_compare", "params": {"target": "amount", "group": "item"}},
             {"tool": "group_compare", "params": {"target": "amount", "group": "category"}}]
    kept, dropped = dc.collapse_specs(specs, canon, set())
    assert [s["params"]["group"] for s in kept] == ["item", "category"] and len(dropped) == 1 and dropped[0][1] == "item"
    again, dropped2 = dc.collapse_specs(specs[:2], canon, {dc.spec_key("group_compare", {"target": "amount", "group": "item"})})        # already run in an earlier round
    assert again == [] and len(dropped2) == 2


def test_two_columns_that_only_partly_overlap_are_not_one_dimension():
    df = bakery()
    df["bucket"] = df["item_code"].where(df.index % 2 == 0, "other")
    assert dc.canonical_dimensions(df, ["item_code", "bucket"]) == {}
