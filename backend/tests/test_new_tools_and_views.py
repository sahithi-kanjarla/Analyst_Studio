"""New chart/table tools, data viewer endpoints, ER data, loader fixes, and a run across very different dataset shapes."""
import json
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np
import pandas as pd
import pytest

from app import runner, tools
from app.config import RUNS_DIR
from app.profiler import load_table, profile_df


def run(df, goal="Understand this data", name="d.csv", context=None):
    rid = runner.create_run(goal, name, df.to_csv(index=False).encode(), context=context)
    runner.start_run(rid).join(timeout=240)
    assert runner.read_status(rid)["status"] == "done", runner.read_status(rid)
    return rid, json.loads((RUNS_DIR / rid / "result.json").read_text())


def sales(n=900, seed=1):
    rng = np.random.default_rng(seed)
    d = pd.date_range("2023-01-01", periods=n, freq="D")
    region = rng.choice(list("NSEW"), n)
    chan = np.where((region == "N") & (rng.random(n) < 0.7), "web", rng.choice(["web", "store", "app"], n))
    age = rng.integers(18, 70, n)
    rev = 50 + age * 2 + np.where(d.dayofweek >= 5, 40, 0) + rng.normal(0, 15, n)
    return pd.DataFrame({"date": d, "region": region, "channel": chan, "age": age, "revenue": rev.round(2)})


# ------------------------------------------------------------------ tools
def test_scatter_detects_relationship_and_handles_extremes(tmp_path):
    df = sales()
    df.loc[0, "revenue"] = 1e6   # one absurd point must not squash the chart or the verdict
    f = tools.scatter(df, {"charts_dir": tmp_path}, "age", "revenue")
    assert f["significant"] is True and f["stats"]["rho"] > 0.5 and (tmp_path / f["chart"].split("/")[-1]).exists()
    assert tools.scatter(df, {"charts_dir": tmp_path}, "age", "age") is None
    assert tools.scatter(df.head(10), {"charts_dir": tmp_path}, "age", "revenue") is None   # too little data


def test_composition_detects_mix_difference(tmp_path):
    f = tools.composition(sales(), {"charts_dir": tmp_path}, "region", "channel")
    assert f["significant"] is True and f["stats"]["biggest"][0] == "N" and f["stats"]["biggest"][1] == "web"
    rng = np.random.default_rng(2)
    flat = pd.DataFrame({"a": rng.choice(list("XYZ"), 600), "b": rng.choice(list("PQR"), 600)})
    assert tools.composition(flat, {"charts_dir": tmp_path}, "a", "b")["stats"]["effect"] < 0.15   # pure noise: whatever the p-value, the effect is tiny


def test_seasonality_weekday_found_and_month_needs_two_years(tmp_path):
    f = tools.seasonality(sales(900), {"charts_dir": tmp_path}, "revenue", "date")
    assert f["significant"] is True and f["stats"]["cycle"] == "weekday" and f["stats"]["best"] in ("Sat", "Sun")
    short = sales(200)
    assert tools.seasonality(short, {"charts_dir": tmp_path}, "revenue", "date")["stats"]["cycle"] == "weekday"   # <2 years: no month claim
    assert tools.seasonality(sales(30), {"charts_dir": tmp_path}, "revenue", "date") is None


def test_tables_have_rows_and_no_chart(tmp_path):
    s = tools.summary_table(sales(), {"charts_dir": tmp_path}, ["age", "revenue"])
    assert s["chart"] is None and s["table"]["columns"][0] == "Column" and len(s["table"]["rows"]) == 2 and s["significant"] is None
    g = tools.group_table(sales(), {"charts_dir": tmp_path, "currency": "₹", "money": {"revenue"}}, "revenue", "region")
    assert len(g["table"]["rows"]) == 4 and "₹" in str(g["table"]["rows"][0]) and "Share of total" in g["table"]["columns"]


def test_validate_spec_new_tools():
    roles = {"a": "numeric", "b": "numeric", "c": "categorical", "d": "categorical", "t": "date"}
    u = {"a": 100, "b": 100, "c": 4, "d": 3, "t": 100}
    assert tools.validate_spec({"tool": "scatter", "params": {"x": "a", "y": "b"}}, roles, u)[0]
    assert tools.validate_spec({"tool": "scatter", "params": {"x": "a", "y": "a"}}, roles, u)[0] is None
    assert tools.validate_spec({"tool": "scatter", "params": {"x": "a", "y": "c"}}, roles, u)[0] is None
    assert tools.validate_spec({"tool": "composition", "params": {"group": "c", "by": "d"}}, roles, u)[0]
    assert tools.validate_spec({"tool": "composition", "params": {"group": "c", "by": "c"}}, roles, u)[0] is None
    assert tools.validate_spec({"tool": "seasonality", "params": {"target": "a", "date": "t"}}, roles, u)[0]
    assert tools.validate_spec({"tool": "seasonality", "params": {"target": "a", "date": "c"}}, roles, u)[0] is None
    assert tools.validate_spec({"tool": "group_table", "params": {"target": "a", "group": "ghost"}}, roles, u)[0] is None


# ------------------------------------------------------------------ loader / profiler fixes
def test_single_column_of_decimals_is_not_split(tmp_path):
    p = tmp_path / "x.csv"
    pd.DataFrame({"x": np.random.default_rng(0).normal(size=100)}).to_csv(p, index=False)
    assert list(load_table(p).columns) == ["x"]


def test_semicolon_and_tab_files_still_load(tmp_path):
    df = pd.DataFrame({"a": range(20), "b": list("xy") * 10})
    for sep, ext in ((";", "csv"), ("\t", "tsv"), ("|", "txt")):
        p = tmp_path / f"f.{ext}"
        df.to_csv(p, sep=sep, index=False)
        assert list(load_table(p).columns) == ["a", "b"]


def test_ratings_are_numeric_so_they_can_be_the_outcome():
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"rating": rng.integers(1, 6, 300), "size": rng.integers(1, 5, 300), "who": rng.choice(["a", "b", "c"], 300)})
    roles = {c["name"]: c["role"] for c in profile_df(df)["columns"]}
    assert roles["rating"] == "numeric" and roles["size"] == "categorical"


# ------------------------------------------------------------------ full run: dashboard data, viewer endpoints, ER data
def test_run_produces_new_tools_chart_rationale_and_viewer_endpoints():
    from fastapi.testclient import TestClient
    from app.main import app
    rid, res = run(sales(), "What drives revenue?")
    tl = {f["tool"] for f in res["findings"]}
    assert {"scatter", "seasonality", "group_table"} <= tl
    for f in res["findings"]:
        assert f["chart_type"] and f["chart_why"]
        assert f.get("chart") or f.get("table")
    assert (RUNS_DIR / rid / "cleaned.csv").exists()
    cl = TestClient(app)
    d = cl.get(f"/api/runs/{rid}/data", params={"limit": 10}).json()
    assert d["total"] == 900 and len(d["rows"]) == 10 and "revenue" in d["columns"]
    s = cl.get(f"/api/runs/{rid}/data", params={"q": "web", "limit": 5}).json()
    assert 0 < s["total"] < 900 and all("web" in [str(v) for v in r] for r in s["rows"])
    top = cl.get(f"/api/runs/{rid}/data", params={"sort": "revenue", "desc": "true", "limit": 3}).json()
    vals = [r[top["columns"].index("revenue")] for r in top["rows"]]
    assert vals == sorted(vals, reverse=True)
    assert cl.get(f"/api/runs/{rid}/data", params={"source": "raw"}).status_code == 200
    assert cl.get(f"/api/runs/{rid}/data", params={"source": "../etc"}).status_code == 400
    assert cl.get("/api/runs/..%2f..%2fetc/data").status_code in (400, 404, 422)
    c = cl.get(f"/api/runs/{rid}/data/compare").json()
    assert c["rows_before"] == 900 and {x["name"] for x in c["columns"]} >= {"date", "region", "revenue"}
    assert cl.get(f"/api/runs/{rid}/files/cleaned.csv").status_code == 200


def test_data_model_lists_columns_for_the_er_diagram():
    rng = np.random.default_rng(3)
    cust = pd.DataFrame({"customer_id": [f"C{i}" for i in range(80)], "segment": rng.choice(["A", "B"], 80)})
    orders = pd.DataFrame({"order_id": range(400), "customer_id": rng.choice(cust.customer_id, 400), "revenue": rng.gamma(2, 50, 400)})
    from fastapi.testclient import TestClient
    from app.main import app
    cl = TestClient(app)
    r = cl.post("/api/runs", data={"goal": "Which segment drives revenue?"}, files=[("files", ("customers.csv", cust.to_csv(index=False).encode())), ("files", ("orders.csv", orders.to_csv(index=False).encode()))])
    rid = r.json()["run_id"]
    import time
    for _ in range(120):
        st = cl.get(f"/api/runs/{rid}").json()
        if st["status"] in ("done", "error"):
            break
        time.sleep(0.5)
    dm = st["data_model"]
    cols = {t["name"]: {c["name"] for c in t["columns"]} for t in dm["tables"]}
    assert cols["orders"] >= {"order_id", "customer_id", "revenue"} and "segment" in cols["customers"]
    assert any(x["child_col"] == "customer_id" and x["status"] == "used" for x in dm["relationships"])


# ------------------------------------------------------------------ very different dataset shapes must all complete sensibly
def _shapes():
    rng = np.random.default_rng(0)
    n = 500
    d = pd.date_range("2023-01-01", periods=n, freq="D")
    yield "time_series_only", pd.DataFrame({"date": d, "visits": 1000 + np.arange(n) + rng.normal(0, 50, n)})
    yield "all_categorical", pd.DataFrame({k: rng.choice(list("ABC"), n) for k in ("p", "q", "r")})
    yield "single_column", pd.DataFrame({"x": rng.normal(size=n)})
    yield "wide_60", pd.DataFrame({**{f"f{i}": rng.normal(size=n) for i in range(60)}, "label": rng.choice(["a", "b"], n)})
    yield "tiny", pd.DataFrame({"a": range(8), "b": list("xyxyxyxy")})
    yield "mostly_missing", pd.DataFrame({"a": [None] * 450 + list(range(50)), "b": rng.choice(list("AB"), n), "c": rng.normal(size=n)})
    yield "constant_columns", pd.DataFrame({"k": ["x"] * n, "v": [1] * n, "w": rng.normal(size=n)})
    yield "messy_text_numbers", pd.DataFrame({"Amount ($)": rng.choice(["$1,200.50", "340", "N/A", "99.9"], n), "when": rng.choice(["01/02/2024", "2024-04-05", "N/A"], n), "Status ": rng.choice(["Done", "done ", "Pending"], n)})
    yield "unicode_headers", pd.DataFrame({"रकम": rng.normal(100, 10, n), "ग्रुप": rng.choice(["क", "ख"], n)})


@pytest.mark.parametrize("name,df", list(_shapes()), ids=[n for n, _ in _shapes()])
def test_any_shape_completes_with_a_report(name, df):
    rid, res = run(df, "Explore this data", name=f"{name}.csv", context={"explore": True})
    assert res["report_md"] and res["findings"] is not None
    for f in res["findings"]:
        assert f["insight"]["headline"]
    assert "Strategy and Recommendations" in res["report_md"]
