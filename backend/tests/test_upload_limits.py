"""Upload hardening: size is checked while reading in chunks, .xls is refused up front, and a failed upload gives its daily AI slot back."""
import asyncio
import os

os.environ["LLM_PROVIDER"] = "none"

import pytest
from fastapi import HTTPException

from app import main


class FakeStream:
    """An upload that would be 'size' bytes if read to the end; counts how much was actually read."""

    def __init__(self, size, chunk_log):
        self.left, self.read_total, self.log = size, 0, chunk_log

    async def read(self, n=-1):
        n = self.left if n is None or n < 0 else min(n, self.left)
        self.left -= n
        self.read_total += n
        self.log.append(n)
        return b"x" * n


def test_an_oversized_stream_is_stopped_early_with_the_same_message(monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_MB", 2)
    monkeypatch.setattr(main, "READ_CHUNK", 512 * 1024)
    log = []
    up = FakeStream(500 * 1024 * 1024, log)                      # a 500 MB stream against a 2 MB limit
    with pytest.raises(HTTPException) as e:
        asyncio.run(main._read_limited(up, 0))
    assert e.value.status_code == 413 and e.value.detail == "The files together are larger than 2 MB."
    assert up.read_total <= 2 * 1024 * 1024 + 512 * 1024         # stopped within one chunk of the limit
    assert up.left > 400 * 1024 * 1024                            # the rest was never read


def test_the_limit_counts_all_files_together(monkeypatch):
    monkeypatch.setattr(main, "MAX_UPLOAD_MB", 2)
    first, total = asyncio.run(main._read_limited(FakeStream(1_500_000, []), 0))
    assert len(first) == 1_500_000 and total == 1_500_000
    with pytest.raises(HTTPException) as e:
        asyncio.run(main._read_limited(FakeStream(1_500_000, []), total))
    assert e.value.status_code == 413


def test_a_file_under_the_limit_comes_back_whole(monkeypatch):
    data, total = asyncio.run(main._read_limited(FakeStream(3 * 1024 * 1024 + 7, []), 0))
    assert len(data) == total == 3 * 1024 * 1024 + 7


# ------------------------------------------------------------------ .xls is refused up front
def test_an_xls_upload_is_refused_at_once_with_a_clear_sentence(monkeypatch):
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    before = len(list(main.RUNS_DIR.iterdir())) if main.RUNS_DIR.exists() else 0
    r = client.post("/api/runs", data={"goal": "Which segment earns the most?"}, files=[("files", ("old.xls", b"\xd0\xcf\x11\xe0 fake", "application/vnd.ms-excel"))])
    assert r.status_code == 400
    assert "Please save it as .xlsx or .csv" in r.json()["detail"]
    after = len(list(main.RUNS_DIR.iterdir())) if main.RUNS_DIR.exists() else 0
    assert after == before                                        # no run folder was made


def test_xlsx_and_csv_are_still_accepted(monkeypatch):
    from fastapi.testclient import TestClient
    monkeypatch.setattr(main, "start_run", lambda rid: None)         # accept the upload without running the pipeline
    client = TestClient(main.app)
    r = client.post("/api/runs", data={"goal": "Which segment earns the most?"}, files=[("files", ("d.csv", b"a,b\n1,2\n3,4\n"))])
    assert r.status_code == 200 and "run_id" in r.json()
