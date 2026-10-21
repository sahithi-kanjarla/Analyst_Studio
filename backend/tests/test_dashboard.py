"""Interactive dashboard data and the multi-dataset viewer endpoints."""
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from app.main import app
from test_multitable import csv_bytes, make_shop, run_files
from test_new_tools_and_views import run, sales


def test_dashboard_selects_only_what_matters_and_never_sums_rates():
    df = sales()
    df["discount_pct"] = np.random.default_rng(3).uniform(0, 30, len(df))
    rid, _ = run(df, "What drives revenue?")
    d = TestClient(app).get(f"/api/runs/{rid}/dashboard").json()
    by = {m["col"]: m for m in d["measures"]}
    assert d["measures"][0]["col"] == "revenue" and d["measures"][0]["agg"] == "sum"
    if "discount_pct" in by:
        assert by["discount_pct"]["agg"] == "avg"           # 'discount' contains 'count' but is a rate
    assert d["date"] == "__date" and d["total_rows"] == len(d["rows"]) == 900
    assert 1 <= len(d["dims"]) <= 4 and "date" not in [x["col"] for x in d["dims"]]
    assert set(d["columns"]) == {x["col"] for x in d["dims"]} | set(by) | {"__date"}


def test_all_uploaded_tables_are_listed_viewable_and_downloadable():
    c, o, p = make_shop()
    rid, _ = run_files({"customers.csv": csv_bytes(c), "orders.csv": csv_bytes(o), "products.csv": csv_bytes(p)}, "Which customer segment drives revenue?")
    cl = TestClient(app)
    src = {s["id"]: s for s in cl.get(f"/api/runs/{rid}/sources").json()}
    assert {"clean", "raw", "tbl:customers", "tbl:orders", "tbl:products"} <= set(src)
    assert src["tbl:orders"]["rows"] == len(o) and src["tbl:customers"]["kind"] == "original"
    page = cl.get(f"/api/runs/{rid}/data", params={"source": "tbl:orders", "limit": 5}).json()
    assert page["total"] == len(o) and "order_id" in page["columns"]
    dl = cl.get(f"/api/runs/{rid}/data/download", params={"source": "tbl:products"})
    assert dl.status_code == 200 and dl.content.startswith(bytes([0xEF, 0xBB, 0xBF])) and len(dl.text.splitlines()) == len(p) + 1
    assert cl.get(f"/api/runs/{rid}/data", params={"source": "tbl:../x"}).status_code == 400
    assert cl.get(f"/api/runs/{rid}/data", params={"source": "tbl:nope"}).status_code == 404
