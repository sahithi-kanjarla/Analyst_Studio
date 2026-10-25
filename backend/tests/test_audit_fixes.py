"""Audit fixes: camelCase id names, blanks kept blank in counted columns, NA tokens kept as labels in text columns."""
import numpy as np
import pandas as pd

from app.cleaner import coerce_types, scoped_clean
from app.profiler import ID_NAME, profile_df


def _table():
    n = 100
    rng = np.random.default_rng(1)
    sales = pd.Series(rng.integers(10, 500, n).astype(float))
    sales[:10] = np.nan
    return pd.DataFrame({
        "customerId": [f"C{i:04d}" for i in range(n)],
        "Amount Paid": rng.integers(5, 400, n).astype(float),
        "Sales Amount": sales,
        "coupon": ["None"] * 30 + ["SAVE10", "FREE5"] * 35,
        "region": ["North", "South", "East", "West"] * 25,
    })


def test_id_name_is_camel_case_only():
    for ok in ("customerId", "orderId", "user_id", "order id", "id_code", "ID"):
        assert ID_NAME.search(ok), ok
    for bad in ("Paid", "Unpaid", "Valid", "Void", "Android", "Amount Paid"):
        assert not ID_NAME.search(bad), bad


def test_blanks_stay_blank_and_none_stays_a_label():
    raw = _table()
    prof = profile_df(raw)
    roles = {c["name"]: c["role"] for c in prof["columns"]}
    assert roles["Amount Paid"] != "id"
    assert roles["customerId"] == "id"
    typed, log1 = coerce_types(raw, prof)
    assert (typed["coupon"] == "None").sum() == 30
    assert any("kept 'None' as a label in coupon (30 rows)" in e["detail"] for e in log1)
    plan = {"relevant_columns": ["Sales Amount", "coupon", "region"], "target": None}
    clean, log2 = scoped_clean(typed, prof, plan, {"kpis": {}, "kinds": {}})
    assert clean["Sales Amount"].isna().sum() == 10
    assert clean["Sales Amount"].sum() == raw["Sales Amount"].sum()
    assert any("10 blank values in Sales Amount left blank, not counted in totals" in e["detail"] for e in log2)
    assert (clean["coupon"] == "None").sum() == 30
