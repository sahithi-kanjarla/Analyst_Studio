"""The five demo files (different key names on purpose) must connect through all four real links, found by value overlap, and the join must carry product and return columns into the analysis table."""
from pathlib import Path

import pandas as pd

from app import relations
from app.profiler import profile_df

FILES = Path(__file__).resolve().parents[2] / "sample_data" / "demo" / "files"
LINKS = {("order_lines", "order_id", "orders", "order_id"), ("orders", "customer_ref", "customers", "customer_id"),
         ("order_lines", "item_code", "products", "sku"), ("returns", "line_id", "order_lines", "line_id")}


def load():
    return {"orders": pd.read_csv(FILES / "orders.csv"), "customers": pd.read_csv(FILES / "customers.csv"), "products": pd.read_csv(FILES / "products.csv"),
            "returns": pd.read_csv(FILES / "returns.csv"), "order_lines": pd.read_excel(FILES / "order_lines.xlsx")}


def test_all_four_links_are_found():
    edges = relations.detect_relationships(load())
    found = {(e["child_table"], e["child_col"], e["parent_table"], e["parent_col"]) for e in edges}
    assert LINKS <= found, f"missing: {LINKS - found}"
    assert len(found) == 4, f"unexpected extra links: {found - LINKS}"


def test_join_brings_category_and_returns_into_the_analysis():
    tables = load()
    tables["order_lines"] = tables["order_lines"].drop_duplicates()                # the pipeline removes exact duplicate rows before joining
    edges = relations.detect_relationships(tables)
    base, _ = relations.choose_base(tables, edges)
    assert base == "order_lines"
    m = relations.build_model(tables, {t: profile_df(df) for t, df in tables.items()}, edges, base)
    assert m["unused_tables"] == []
    assert all(s["status"] == "used" for s in m["status"].values())
    df = m["df"]
    assert len(df) == len(tables["order_lines"])                          # nothing multiplied
    assert "category" in df.columns and df["category"].notna().mean() > 0.99
    assert "returns_count" in df.columns
    assert df["returns_count"].sum() == len(tables["returns"])                    # every return reaches exactly one order line


def test_value_overlap_does_not_link_unrelated_numbers():
    ids = pd.DataFrame({"customer_id": range(1, 1401), "x": 1})
    ages = pd.DataFrame({"age": [18 + i % 60 for i in range(3000)], "y": 1})        # 60 distinct values, all inside 1..1400
    assert not relations.detect_relationships({"c": ids, "p": ages})
