"""Work out the TRUE figures of the demo sample, with plain pandas only.

Deliberately independent of the app's own code (no import from backend/): if the app and this
script agree, that is two separate computations agreeing.

    python scripts/make_sample_expected.py [files_dir] [out_json]

Reads sample_data/demo/files/, writes sample_data/demo/expected.json.
Each entry in "figures" carries "must_show": true when the Report has to contain it.
Revenue = quantity x unit_price per order line (lines with a missing quantity are left out here).
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
FILES = ROOT / "sample_data" / "demo" / "files"
OUT = ROOT / "sample_data" / "demo" / "expected.json"

# how the files link: (child file, child column, parent file, parent column)
LINKS = [
    ("orders", "customer_ref", "customers", "customer_id"),
    ("order_lines", "order_id", "orders", "order_id"),
    ("order_lines", "item_code", "products", "sku"),
    ("returns", "line_id", "order_lines", "line_id"),
]


def _load(d: Path) -> dict:
    t = {}
    for p in sorted(d.iterdir()):
        if p.suffix.lower() == ".csv":
            t[p.stem] = pd.read_csv(p)
        elif p.suffix.lower() in (".xlsx", ".xls"):
            t[p.stem] = pd.read_excel(p)
    return t


def _dates(s: pd.Series) -> pd.Series:
    """Parse each value on its own: 2025-03-01 is year-first, 01/03/2025 is day-first."""
    def one(v):
        if pd.isna(v):
            return pd.NaT
        v = str(v).strip()
        return pd.to_datetime(v, format="%d/%m/%Y") if "/" in v else pd.to_datetime(v)
    return s.map(one)


def fig(id_, label, value, kind, must_show, tabs, **extra):
    return {"id": id_, "label": label, "expected": value, "kind": kind, "must_show": must_show, "tabs": tabs, **extra}


def compute(files_dir: Path) -> dict:
    t = _load(files_dir)
    customers, products, orders, lines, returns = (t["customers"], t["products"], t["orders"], t["order_lines"], t["returns"])

    dup = {"orders": int(orders.duplicated().sum()), "order_lines": int(lines.duplicated().sum())}
    orders_u, lines_u = orders.drop_duplicates(), lines.drop_duplicates()

    missing = {name: {c: int(n) for c, n in df.isna().sum().items() if n} for name, df in t.items()}

    lines_u = lines_u.assign(revenue=lines_u["quantity"] * lines_u["unit_price"])
    li = lines_u.merge(products[["sku", "category"]], left_on="item_code", right_on="sku", how="left")
    li = li.merge(orders_u[["order_id", "customer_ref", "order_date"]], on="order_id", how="left")
    li = li.merge(customers[["customer_id", "region"]], left_on="customer_ref", right_on="customer_id", how="left")

    rev_cat = li.groupby("category")["revenue"].sum().sort_values(ascending=False)
    rev_reg = li.groupby("region")["revenue"].sum().sort_values(ascending=False)

    ret_lines = set(returns["line_id"])
    li["returned"] = li["line_id"].isin(ret_lines)
    rate = li.groupby("category")["returned"].mean().sort_values(ascending=False)

    # trend: orders per month, first third against last third, and the slope sign
    d = _dates(orders_u["order_date"]).dropna()
    monthly = d.dt.to_period("M").value_counts().sort_index()
    k = max(len(monthly) // 3, 1)
    first, last = float(monthly.iloc[:k].mean()), float(monthly.iloc[-k:].mean())
    slope = float(np.polyfit(np.arange(len(monthly)), monthly.to_numpy(), 1)[0])
    direction = "up" if slope > 0 else "down" if slope < 0 else "flat"

    links = []
    for child, ccol, parent, pcol in LINKS:
        c = t[child][ccol].dropna().drop_duplicates()
        matched = int(c.isin(set(t[parent][pcol].dropna())).sum())
        links.append({"child": child, "child_col": ccol, "parent": parent, "parent_col": pcol,
                      "distinct_child_keys": int(len(c)), "matched_keys": matched,
                      "child_rows_matching": int(t[child][ccol].dropna().isin(set(t[parent][pcol].dropna())).sum()),
                      "child_rows": int(len(t[child]))})

    top_c, bot_c, top_r, bot_r = rev_cat.index[0], rev_cat.index[-1], rev_reg.index[0], rev_reg.index[-1]
    figures = [
        fig("orders_total", "Total orders", int(orders_u["order_id"].nunique()), "number", True, ["Report"], tol_rel=0),
        fig("order_lines_total", "Order lines", int(len(lines_u)), "number", False, ["Report"], tol_rel=0),
        fig("revenue_total", "Total revenue (quantity x unit price)", round(float(li["revenue"].sum()), 2), "number", True,
            ["Report", "Dashboard"], tol_rel=0.02, note="lines with missing quantity excluded here; the app may fill them in"),
        fig("unique_customers", "Customers who ordered", int(orders_u["customer_ref"].nunique()), "number", True, ["Report"], tol_rel=0),
        fig("top_category", "Top category by revenue", top_c, "name", True, ["Report", "Insights", "Dashboard"]),
        fig("top_category_revenue", "Revenue of the top category", round(float(rev_cat.iloc[0]), 2), "number", False, ["Report"], tol_rel=0.02),
        fig("bottom_category", "Bottom category by revenue", bot_c, "name", True, ["Report", "Insights", "Dashboard"]),
        fig("top_region", "Top region by revenue", top_r, "name", True, ["Report", "Insights", "Dashboard"]),
        fig("bottom_region", "Bottom region by revenue", bot_r, "name", True, ["Report", "Insights", "Dashboard"]),
        fig("trend_direction", "Order volume trend over time", direction, "direction", True, ["Report", "Insights", "Dashboard"],
            first_third_monthly_orders=round(first, 1), last_third_monthly_orders=round(last, 1)),
        fig("highest_return_category", "Category with the highest return rate", rate.index[0], "name", True, ["Report", "Insights"]),
        fig("highest_return_rate", f"Return rate of {rate.index[0]}", round(float(rate.iloc[0]), 4), "percent", True, ["Report", "Insights"], tol_abs=0.01),
        fig("duplicates_removed", "Duplicate rows removed (orders + order lines)", sum(dup.values()), "number", False, ["Cleaning log"], tol_rel=0),
    ]
    return {
        "source": "scripts/make_sample_expected.py (plain pandas, no app code)",
        "files": {n: int(len(df)) for n, df in t.items()},
        "figures": figures,
        "details": {
            "revenue_by_category": {k: round(float(v), 2) for k, v in rev_cat.items()},
            "revenue_by_region": {k: round(float(v), 2) for k, v in rev_reg.items()},
            "return_rate_by_category": {k: round(float(v), 4) for k, v in rate.items()},
            "monthly_orders": {str(k): int(v) for k, v in monthly.items()},
            "duplicate_rows_removed": dup,
            "missing_values": missing,
            "links": links,
        },
    }


def main(argv=None) -> int:
    a = list(sys.argv[1:] if argv is None else argv)
    files = Path(a[0]) if a else FILES
    out = Path(a[1]) if len(a) > 1 else OUT
    exp = compute(files)
    out.write_text(json.dumps(exp, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"wrote {out} ({len(exp['figures'])} figures, {sum(f['must_show'] for f in exp['figures'])} must_show)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
