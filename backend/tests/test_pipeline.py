"""End-to-end tests in rules-only mode (no LLM, no network): `pytest backend/tests`"""
import os
import time
import zipfile

os.environ["LLM_PROVIDER"] = "none"

import numpy as np
import pandas as pd
import pytest

from app import runner
from app.config import RUNS_DIR


def make_data(path):
    rng = np.random.default_rng(1)
    n = 600
    seg = rng.choice(["Enterprise", "SMB", "Consumer"], n, p=[0.2, 0.3, 0.5])
    base = {"Enterprise": 400, "SMB": 220, "Consumer": 120}
    revenue = np.array([base[s] for s in seg]) * rng.lognormal(0, 0.5, n)
    dates = pd.to_datetime("2025-01-01") + pd.to_timedelta(rng.integers(0, 330, n), unit="D")
    revenue = revenue * (1 + (dates - dates.min()).days / 600)
    df = pd.DataFrame({
        "order_id": range(1, n + 1),
        "Segment": seg,
        "region": rng.choice(["N", "S", "E", "W"], n),
        "order_date": dates.strftime("%d/%m/%Y"),
        "revenue": np.round(revenue, 2).astype(str),
        "discount_pct": rng.uniform(0, 30, n).round(1),
        "notes": rng.choice(["ok", "N/A", " "], n),
    })
    df.loc[rng.choice(n, 25), "discount_pct"] = np.nan
    df.loc[rng.choice(n, 15), "revenue"] = "$" + df["revenue"].iloc[:15].values
    df = pd.concat([df, df.head(8)])
    df.to_csv(path, index=False)


@pytest.fixture(scope="module")
def finished_run(tmp_path_factory):
    p = tmp_path_factory.mktemp("d") / "sales.csv"
    make_data(p)
    run_id = runner.create_run("Which customer segment drives revenue and is it growing?", "sales.csv", p.read_bytes())
    t = runner.start_run(run_id)
    t.join(timeout=180)
    return run_id


def test_run_completes(finished_run):
    st = runner.read_status(finished_run)
    assert st["status"] == "done", st


def test_agents_understood_goal_and_scoped(finished_run):
    import json
    res = json.loads((RUNS_DIR / finished_run / "result.json").read_text())
    plan = res["plan"]
    assert plan["target"] == "revenue"
    assert plan["date_col"] == "order_date"
    assert "Segment" in plan["relevant_columns"]
    assert "notes" not in plan["relevant_columns"]  # irrelevant column excluded


def test_cleaning_logged(finished_run):
    import json
    res = json.loads((RUNS_DIR / finished_run / "result.json").read_text())
    steps = {s["step"] for s in res["cleaning_log"]}
    assert "Remove duplicates" in steps and "Convert to number" in steps


def test_finds_real_signal_and_critic_ran(finished_run):
    import json
    res = json.loads((RUNS_DIR / finished_run / "result.json").read_text())
    seg = next(f for f in res["findings"] if f["tool"] == "group_compare" and f["params"]["group"] == "Segment")
    assert seg["significant"] and seg["stats"]["best"] == "Enterprise"
    assert any(f["tool"] == "robust_compare" for f in res["findings"])  # follow-up loop triggered by skew
    tr = next(f for f in res["findings"] if f["tool"] == "trend")
    assert tr["stats"]["slope"] > 0                    # growth was planted in the data
    assert tr["stats"]["periods"] in (11, 12)          # Jan-Nov: dd/mm dates parsed correctly (not swapped)
    assert any("day-first" in s["detail"] for s in res["cleaning_log"])


def test_report_and_powerbi_pack(finished_run):
    wd = RUNS_DIR / finished_run
    assert "Recommendations" in (wd / "report.md").read_text()
    with zipfile.ZipFile(wd / "powerbi_pack.zip") as z:
        names = z.namelist()
    for need in ("cleaned_data.csv", "measures.dax", "POWERBI_GUIDE.md", "theme.json", "data_dictionary.csv", "report.md"):
        assert need in names
    assert any(n.startswith("charts/") for n in names)


def test_api(finished_run):
    from fastapi.testclient import TestClient
    from app.main import app
    c = TestClient(app)
    assert c.get("/api/health").json()["status"] == "ok"
    r = c.get(f"/api/runs/{finished_run}").json()
    assert r["status"] == "done" and "/api/runs/" in r["report_md"]
    assert c.get(r["findings"][0]["chart_url"]).status_code == 200
    assert c.get(f"/api/runs/{finished_run}/powerbi").status_code == 200
    assert c.get(f"/api/runs/{finished_run}/files/../../etc/passwd").status_code == 404
    assert c.get("/api/runs/zzzzzzzzzzzz").status_code == 404
    bad = c.post("/api/runs", data={"goal": "something useful"}, files={"files": ("x.exe", b"abc")})
    assert bad.status_code == 400
