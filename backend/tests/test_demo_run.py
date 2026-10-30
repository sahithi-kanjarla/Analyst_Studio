"""The sample run ("Try with sample data"): it opens with every AI call disabled, it is read-only, retention keeps it, and it is the shipped copy with no upload."""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import llm, runner
from app.config import DEMO_DIR
from app.main import app

client = TestClient(app)


def _no_ai(*args, **kwargs):
    raise AssertionError("an AI call was made while opening the sample run")


@pytest.fixture
def ai_off(monkeypatch):
    """Every way into a model fails the test: the sample run must open without one."""
    for name in ("_invoke", "ask_text", "ask_json_ex", "ask_json", "get_llm"):
        monkeypatch.setattr(llm, name, _no_ai)
    monkeypatch.setenv("LLM_PROVIDER", "none")


def test_sample_run_opens_with_all_ai_disabled(ai_off):
    r = client.post("/api/demo")
    assert r.status_code == 200 and r.json() == {"run_id": runner.DEMO_RUN_ID, "read_only": True}
    run = client.get(f"/api/runs/{runner.DEMO_RUN_ID}").json()
    assert run["status"] == "done" and run["report_md"].startswith("# Analysis report")
    assert run["report_view"]["actions"] is not None
    assert client.get(f"/api/runs/{runner.DEMO_RUN_ID}/dashboard").status_code == 200
    data = client.get(f"/api/runs/{runner.DEMO_RUN_ID}/data", params={"source": "clean", "limit": 5}).json()
    assert data["total"] > 0 and len(data["rows"]) == 5
    assert client.get(f"/api/runs/{runner.DEMO_RUN_ID}/sources").status_code == 200


def test_sample_run_is_read_only(ai_off):
    client.post("/api/demo")
    rid = runner.DEMO_RUN_ID
    assert client.post(f"/api/runs/{rid}/answers", json={"answers": {}, "skip": True}).status_code == 403
    assert client.post(f"/api/runs/{rid}/rerun-answers", json={"answers": {}}).status_code == 403
    assert client.post(f"/api/runs/{rid}/remove-derived", json={"names": ["x"]}).status_code == 403


def test_sample_run_survives_retention():
    rid = runner.ensure_demo_run()
    removed = runner.purge_expired(days=0.000001, now=10**12)     # far in the future: every other run would go
    assert rid not in removed and runner.run_dir(rid).exists()


def test_sample_run_passes_every_report_rule():
    import importlib.util
    spec = importlib.util.spec_from_file_location("report_check", Path(__file__).resolve().parents[2] / "scripts" / "report_check.py")
    rc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rc)
    import json
    runner.ensure_demo_run()
    wd = runner.run_dir(runner.DEMO_RUN_ID)
    result = json.loads((wd / "result.json").read_text(encoding="utf-8"))
    rows = rc.check((wd / "report.md").read_text(encoding="utf-8"), result, rc.run_dates(wd, result))
    failed = [(name, detail) for name, ok, detail in rows if not ok]
    assert not failed, failed


def test_shipped_copy_has_no_upload_or_secrets():
    assert DEMO_DIR.is_dir()
    assert not (DEMO_DIR / "inputs").exists()
    assert not list(DEMO_DIR.rglob(".env*"))
    assert (DEMO_DIR / "result.json").exists() and (DEMO_DIR / "clean.pkl").exists()
