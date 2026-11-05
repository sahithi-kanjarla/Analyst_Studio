"""Revenue must be the same for the same data: quantity x the price charged, never quantity x what the sales cost (the lowest rate-like column)."""
from pathlib import Path

import pandas as pd

from app.profiler import profile_df
from app.retail_roles import derive_product_measure

DEMO = Path(__file__).resolve().parents[2] / "sample_data" / "demo"


def _lines():
    """The demo's order lines with the product columns joined on, built with plain pandas (no app code)."""
    lines = pd.read_excel(DEMO / "files" / "order_lines.xlsx")
    products = pd.read_csv(DEMO / "files" / "products.csv")
    df = lines.merge(products, left_on="item_code", right_on="sku", how="left")
    return df.drop(columns=["sku", "product_name", "category"])


def _cols(df):
    return {c["name"]: c for c in profile_df(df)["columns"]} if isinstance(profile_df(df).get("columns"), list) else profile_df(df)["columns"]


def test_cost_column_is_never_the_price_of_the_revenue():
    df = _lines()
    cols = _cols(df)
    cost = [c for c in df.columns if "cost" in c.lower()]
    assert cost, df.columns
    # no AI brief (an exploratory run): the understanding only says which column is the cost
    hinted = derive_product_measure(df, cols, None, {"cost_columns": cost})
    assert hinted is not None and hinted["rate"] not in cost
    # the revenue is the plain-pandas figure the checked sample holds
    q, r = hinted["count"], hinted["rate"]
    assert abs((df[q] * df[r]).sum() - 356964.0) / 356964.0 < 0.06       # raw lines, before the cleaner's duplicate removal


def test_per_unit_columns_are_averaged_never_summed():
    from app.measures import measure_kind
    from app.retail_roles import per_unit_columns
    df = _lines()
    units = per_unit_columns(df, _cols(df))
    assert {"unit_price", "unit_cost", "list_price"} <= units and "quantity" not in units
    kinds = {c: "rate" for c in units}
    assert all(measure_kind(c, overrides=kinds) == "rate" for c in units)
