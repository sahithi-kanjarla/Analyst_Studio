"""Multi-table behaviour: key detection, safe joins, grain choice, false-positive resistance, API + Excel sheets."""
import io
import json
import os
import zipfile

os.environ["LLM_PROVIDER"] = "none"

import numpy as np
import pandas as pd
import pytest

from app import llm as llm_mod
from app import relations, runner
from app.cleaner import coerce_types
from app.config import RUNS_DIR
from app.profiler import profile_df


def make_shop(seed=2, n_cust=400):
    """customers (dimension), orders (fact, FK -> customers/products), products (dimension).
    Planted: Enterprise orders are bigger; customers who churn placed fewer orders."""
    rng = np.random.default_rng(seed)
    cid = [f"C{i:04d}" for i in range(n_cust)]
    seg = rng.choice(["Enterprise", "SMB", "Consumer"], n_cust, p=[.2, .3, .5])
    churn = rng.random(n_cust) < 0.3
    customers = pd.DataFrame({"customer_id": cid, "segment": seg, "tenure_months": rng.integers(1, 60, n_cust),
                              "churn": np.where(churn, "Yes", "No")})
    rows = []
    base = {"Enterprise": 500, "SMB": 250, "Consumer": 100}
    for i, c in enumerate(cid):
        for _ in range(rng.poisson(3 if churn[i] else 9)):
            rows.append((c, seg[i]))
    orders = pd.DataFrame(rows, columns=["customer_id", "_seg"])
    orders.insert(0, "order_id", range(1, len(orders) + 1))
    orders["product_id"] = rng.integers(101, 121, len(orders)).astype(float)
    orders.loc[rng.choice(len(orders), 20), "product_id"] = np.nan          # NaN makes the column float: 101.0 vs 101
    orders["revenue"] = [base[s] * rng.lognormal(0, .4) for s in orders.pop("_seg")]
    orders["order_date"] = pd.Timestamp("2025-01-01") + pd.to_timedelta(rng.integers(0, 300, len(orders)), unit="D")
    products = pd.DataFrame({"product_id": range(101, 121), "category": rng.choice(["Hardware", "Software", "Services"], 20),
                             "list_price": rng.uniform(10, 500, 20).round(2)})
    return customers, orders, products


def typed(tables):
    out = {}
    for k, df in tables.items():
        out[k] = coerce_types(df, profile_df(df))[0]
    return out


def profiles_of(t):
    return {k: profile_df(v) for k, v in t.items()}


# --------------------------------------------------------------------------- detection
def test_detects_links_despite_float_vs_int_keys():
    c, o, p = make_shop()
    edges = relations.detect_relationships(typed({"customers": c, "orders": o, "products": p}))
    found = {(e["child_table"], e["child_col"], e["parent_table"], e["parent_col"]) for e in edges}
    assert ("orders", "customer_id", "customers", "customer_id") in found
    assert ("orders", "product_id", "products", "product_id") in found      # 101.0 matched 101
    assert all(e["cardinality"] == "many-to-one" for e in edges)


def test_no_false_links_between_coincidental_ids():
    rng = np.random.default_rng(0)
    a = pd.DataFrame({"id": range(1, 301), "name": [f"a{i}" for i in range(300)]})       # both just "id" = 1..N
    b = pd.DataFrame({"id": range(1, 301), "price": rng.normal(10, 2, 300)})
    q = pd.DataFrame({"qty": rng.integers(1, 10, 300), "x": rng.normal(0, 1, 300)})      # small ints overlap ids
    assert relations.detect_relationships(typed({"a": a, "b": b, "q": q})) == []


def test_name_similarity_rules():
    ns = relations.name_similarity
    assert ns("customer_id", "id", "customers") == 1.0
    assert ns("cust_id", "id", "customers") == 0.5
    assert ns("id", "id", "customers") == 0.0
    assert ns("region", "region", "regions") == 1.0


# --------------------------------------------------------------------------- joins
def test_parent_join_keeps_row_count_and_adds_columns():
    c, o, p = make_shop()
    t = typed({"customers": c, "orders": o, "products": p})
    prof = profiles_of(t)
    edges = relations.detect_relationships(t)
    m = relations.build_model(t, prof, edges, "orders")
    assert len(m["df"]) == len(t["orders"])
    assert {"segment", "category"} <= set(m["df"].columns)
    assert m["df"]["segment"].notna().all()
    assert not m["unused_tables"]


def test_child_is_aggregated_not_multiplied():
    c, o, p = make_shop()
    t = typed({"customers": c, "orders": o, "products": p})
    edges = relations.detect_relationships(t)
    m = relations.build_model(t, profiles_of(t), edges, "customers")
    df = m["df"]
    assert len(df) == len(t["customers"])
    assert "orders_count" in df.columns and "orders_revenue_total" in df.columns and "orders_days_since_order_date" in df.columns
    assert int(df["orders_count"].sum()) == len(t["orders"])        # nothing lost, nothing double counted
    assert not any(c.startswith("orders_product_id") for c in df.columns)   # foreign keys are never averaged
    assert "orders_order_date_last" not in df.columns                       # no fake time axis from aggregated dates
    assert m["origin"]["orders_count"] == "orders (aggregated)"


def test_orphans_are_measured_and_bad_links_skipped():
    c, o, p = make_shop()
    t = typed({"customers": c, "orders": o, "products": p})
    prof = profiles_of(t)
    edges = relations.detect_relationships(t)
    # 25% of orders reference unknown customers -> still used, with a warning
    o2 = t["orders"].copy()
    idx = o2.sample(frac=0.25, random_state=1).index
    o2.loc[idx, "customer_id"] = "GHOST" + o2.loc[idx, "customer_id"]
    t2 = {**t, "orders": o2}
    e2 = relations.detect_relationships(t2)
    m = relations.build_model(t2, prof, e2, "orders")
    assert any("match" in w["text"].lower() for w in m["warnings"])
    # 80% orphans -> the link is rejected rather than producing a mostly-empty column set
    o3 = t["orders"].copy()
    idx = o3.sample(frac=0.8, random_state=1).index
    o3.loc[idx, "customer_id"] = "ZZZ" + o3.loc[idx, "customer_id"]
    e3 = [dict(e) for e in edges]
    m3 = relations.build_model({**t, "orders": o3}, prof, e3, "orders")
    assert "segment" not in m3["df"].columns
    assert any(s["status"] == "skipped" for s in m3["status"].values())


def test_duplicate_parent_keys_never_multiply_rows():
    c, o, p = make_shop()
    c = pd.concat([c, c.head(5)])           # duplicated dimension rows
    t = typed({"customers": c, "orders": o, "products": p})
    t["customers"] = c.reset_index(drop=True)  # keep duplicates (typed() would dedupe)
    edges = relations.detect_relationships(t)
    m = relations.build_model(t, profiles_of(t), edges, "orders")
    assert len(m["df"]) == len(t["orders"])


def test_base_table_is_always_the_finest_grain_whatever_the_goal():
    c, o, p = make_shop()
    t = typed({"customers": c, "orders": o, "products": p})
    edges = relations.detect_relationships(t)
    for goal in ("Why do customers churn?", "Which segment drives revenue?"):
        assert relations.choose_base(t, edges)[0] == "orders"


def test_three_table_chain_defaults_to_fact_table_and_keeps_its_columns():
    rng = np.random.default_rng(7)
    regions = pd.DataFrame({"region_id": [f"R{i}" for i in range(8)], "region_name": [f"Region {i}" for i in range(8)]})
    customers = pd.DataFrame({"customer_id": [f"C{i:03d}" for i in range(120)], "region_id": rng.choice(regions["region_id"], 120),
                              "segment": rng.choice(["Enterprise", "SMB", "Consumer"], 120)})
    n = 900
    orders = pd.DataFrame({"order_id": range(1, n + 1), "customer_id": rng.choice(customers["customer_id"], n),
                           "category": rng.choice(["Hardware", "Software", "Services"], n), "amount": rng.lognormal(4, .5, n).round(2),
                           "order_date": pd.Timestamp("2025-01-01") + pd.to_timedelta(rng.integers(0, 300, n), unit="D")})
    t = typed({"regions": regions, "customers": customers, "orders": orders})
    edges = relations.detect_relationships(t)
    base, _ = relations.choose_base(t, edges)
    assert base == "orders"
    m = relations.build_model(t, profiles_of(t), edges, base)
    df = m["df"]
    assert len(df) == n
    assert {"order_date", "category", "amount", "segment", "region_name"} <= set(df.columns)
    assert not any(v.endswith("(aggregated)") for v in m["origin"].values())
    assert not m["unused_tables"]


# --------------------------------------------------------------------------- end to end
def run_files(files: dict[str, bytes], goal: str):
    rid = runner.create_run(goal, list(files.items()))
    runner.start_run(rid).join(timeout=180)
    assert runner.read_status(rid)["status"] == "done", runner.read_status(rid)
    return rid, json.loads((RUNS_DIR / rid / "result.json").read_text())


def csv_bytes(df):
    return df.to_csv(index=False).encode()


def test_pipeline_revenue_by_segment_across_three_files():
    c, o, p = make_shop()
    rid, res = run_files({"customers.csv": csv_bytes(c), "orders.csv": csv_bytes(o), "products.csv": csv_bytes(p)}, "Which customer segment drives revenue?")
    dm = res["data_model"]
    assert dm["base_table"] == "orders" and len(dm["tables"]) == 3
    assert sum(r["status"] == "used" for r in dm["relationships"]) == 2
    assert res["plan"]["target"] == "revenue"
    seg = next(f for f in res["findings"] if f["tool"] == "group_compare" and f["params"]["group"] == "segment")   # column came from ANOTHER file
    assert seg["significant"] and seg["stats"]["best"] == "Enterprise"
    assert "Data sources and how they were connected" in res["report_md"]
    with zipfile.ZipFile(RUNS_DIR / rid / "powerbi_pack.zip") as z:
        names = z.namelist()
    assert "relationships.csv" in names and "tables/orders.csv" in names and "tables/customers.csv" in names


def test_pipeline_churn_goal_still_uses_finest_grain_base():
    c, o, p = make_shop()
    rid, res = run_files({"customers.csv": csv_bytes(c), "orders.csv": csv_bytes(o)}, "Why do customers churn?")
    dm = res["data_model"]
    assert dm["base_table"] == "orders"                       # the goal does not coarsen the grain
    assert not any(v.endswith("(aggregated)") for v in dm["origin"].values())
    assert {"churn", "segment", "order_date"} <= set(res["profile"]["columns"].keys() if isinstance(res["profile"]["columns"], dict) else [x["name"] for x in res["profile"]["columns"]])


def test_unrelated_files_are_flagged_not_forced():
    rng = np.random.default_rng(4)
    a = pd.DataFrame({"sku": [f"S{i}" for i in range(80)], "stock": rng.integers(0, 90, 80)})
    b = pd.DataFrame({"employee": [f"E{i}" for i in range(80)], "salary": rng.normal(50, 9, 80)})
    rid, res = run_files({"stock.csv": csv_bytes(a), "staff.csv": csv_bytes(b)}, "What is in these files?")
    assert res["data_model"]["relationships"] == []
    # changed: with only one table analysed the "no reliable key links" warning is no longer printed; the other table is listed as ignored instead
    assert not any("no reliable key link" in w["text"].lower() for w in res["data_model"]["warnings"])
    assert [t["name"] for t in res["data_model"]["ignored_tables"]] and "Ignored (" in res["report_md"] and "no reliable key link" not in res["report_md"].lower()


def test_excel_workbook_sheets_become_tables():
    c, o, p = make_shop()
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        c.to_excel(w, sheet_name="Customers", index=False)
        o.to_excel(w, sheet_name="Orders", index=False)
    rid, res = run_files({"shop.xlsx": buf.getvalue()}, "Which segment drives revenue?")
    assert {t["name"] for t in res["data_model"]["tables"]} == {"shop_Customers", "shop_Orders"}
    assert any(r["status"] == "used" for r in res["data_model"]["relationships"])


def test_uploads_cannot_overwrite_run_files_and_names_are_unique():
    df = pd.DataFrame({"a": range(30), "b": np.random.default_rng(0).normal(size=30)})
    rid = runner.create_run("test goal here", [("status.json", csv_bytes(df)), ("data.csv", csv_bytes(df)), ("data.csv", csv_bytes(df))])
    assert (RUNS_DIR / rid / "status.json").read_text().startswith("{")        # not clobbered by the upload
    assert len(list((RUNS_DIR / rid / "inputs").iterdir())) == 3


def test_api_multi_upload_and_limits():
    from fastapi.testclient import TestClient
    from app.main import app
    c, o, p = make_shop()
    cl = TestClient(app)
    r = cl.post("/api/runs", data={"goal": "Which segment drives revenue?"},
                files=[("files", ("customers.csv", csv_bytes(c))), ("files", ("orders.csv", csv_bytes(o)))])
    assert r.status_code == 200
    rid = r.json()["run_id"]
    import time
    for _ in range(120):
        st = cl.get(f"/api/runs/{rid}").json()
        if st["status"] in ("done", "error"):
            break
        time.sleep(0.5)
    assert st["status"] == "done" and st["data_model"]["base_table"] == "orders"
    assert cl.post("/api/runs", data={"goal": "something useful"}).status_code == 400   # no files
    many = [("files", (f"f{i}.csv", b"a,b\n1,2\n")) for i in range(9)]
    assert cl.post("/api/runs", data={"goal": "something useful"}, files=many).status_code == 400
    # legacy single-file field still works
    assert cl.post("/api/runs", data={"goal": "something useful"}, files={"file": ("one.csv", csv_bytes(c))}).status_code == 200


# --------------------------------------------------------------------------- optional LLM review
class Msg:
    def __init__(self, content):
        self.content = content


class FakeReviewer:
    def __init__(self, answer):
        self.answer = answer

    def invoke(self, messages):
        if "data modeller" in messages[0][1]:
            return Msg(json.dumps(self.answer))
        return Msg("not json")      # every other agent falls back to rules


@pytest.mark.parametrize("answer, expect_base, expect_rejected", [
    ({"base_table": "ghost_table", "reject_links": [99, "x"], "reason": "hallucinated"}, "orders", 0),   # invalid -> ignored
    ({"base_table": "customers", "reject_links": [], "reason": "one row per customer"}, "orders", 0),   # coarser base -> ignored
])
def test_llm_review_is_validated(answer, expect_base, expect_rejected):
    c, o, p = make_shop()
    llm_mod._cache["llm"] = (FakeReviewer(answer), "fake")
    try:
        rid, res = run_files({"customers.csv": csv_bytes(c), "orders.csv": csv_bytes(o)}, "Which customer segment drives revenue?")
    finally:
        llm_mod.reset_cache()
    assert res["data_model"]["base_table"] == expect_base
    assert sum(r["status"] == "rejected" for r in res["data_model"]["relationships"]) == expect_rejected


def test_llm_can_veto_a_link():
    c, o, p = make_shop()
    llm_mod._cache["llm"] = (FakeReviewer({"base_table": "orders", "reject_links": [0], "reason": "looks coincidental"}), "fake")
    try:
        rid, res = run_files({"customers.csv": csv_bytes(c), "orders.csv": csv_bytes(o)}, "Which customer segment drives revenue?")
    finally:
        llm_mod.reset_cache()
    rel = res["data_model"]["relationships"][0]
    assert rel["status"] == "rejected" and "segment" not in res["profile"]["columns"].__str__()
