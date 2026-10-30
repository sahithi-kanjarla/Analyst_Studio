"""MAX_ROWS (large files are refused with a clear message) and RETENTION_DAYS (old finished runs and their uploads are deleted)."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from app import main, profiler, runner
from app.main import app

client = TestClient(app)
RUN_ID = "abcdef012345"


def _make_run(run_id: str, status: str, age_days: float) -> None:
    d = runner.run_dir(run_id)
    (d / "inputs").mkdir(parents=True, exist_ok=True)
    (d / "inputs" / "upload.csv").write_text("a\n1\n")
    stamp = time.time() - age_days * 86400
    (d / "status.json").write_text(json.dumps({"status": status, "created": stamp, "updated": stamp}))


def test_count_delimited_rows_ignores_header_and_quoted_breaks():
    assert profiler.count_delimited_rows(b"a,b\n1,2\n3,\"x\ny\"\n") == 2
    assert profiler.count_delimited_rows(b"a;b\n1;2\n3;4\n5;6\n") == 3
    assert profiler.count_delimited_rows(b"a,b\n") == 0


def test_load_table_backstop_refuses_too_many_rows(tmp_path, monkeypatch):
    f = tmp_path / "big.csv"
    f.write_text("a,b\n" + "1,2\n" * 5)
    monkeypatch.setattr("app.config.MAX_ROWS", 3)
    with pytest.raises(ValueError, match="has 5 rows"):
        profiler.load_tables(f)


def test_upload_over_max_rows_is_rejected_with_clear_message(monkeypatch):
    monkeypatch.setattr(main, "MAX_ROWS", 3)
    body = "a,b\n" + "1,2\n" * 5
    r = client.post("/api/runs", data={"goal": "Find the biggest seller"}, files={"files": ("sales.csv", body, "text/csv")})
    assert r.status_code == 413
    assert "has 5 rows" in r.json()["detail"] and "up to 3 rows" in r.json()["detail"]


def test_retention_deletes_only_old_finished_runs():
    old_done, recent, old_running = "aaaaaaaaaaa1", "aaaaaaaaaaa2", "aaaaaaaaaaa3"
    _make_run(old_done, "done", 40)
    _make_run(recent, "done", 2)
    _make_run(old_running, "running", 40)
    removed = runner.purge_expired(days=30)
    assert removed == [old_done]
    assert not runner.run_dir(old_done).exists()           # the uploads go with it
    assert runner.run_dir(recent).exists()
    assert runner.run_dir(old_running).exists()            # a run still in progress is never removed


def test_retention_zero_keeps_everything():
    _make_run("bbbbbbbbbbb1", "done", 400)
    assert runner.purge_expired(days=0) == []
    assert runner.run_dir("bbbbbbbbbbb1").exists()
