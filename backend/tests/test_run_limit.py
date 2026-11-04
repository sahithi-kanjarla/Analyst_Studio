"""MAX_CONCURRENT_RUNS: extra runs wait with the status "queued" and a plain message, then run when a slot is free."""
import asyncio
import json
import os
import threading
import time

os.environ["LLM_PROVIDER"] = "none"

import pytest
from fastapi.testclient import TestClient

from app import config, main, runner

client = TestClient(main.app)


def _wait(cond, secs=10):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def slow_runs(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_RUNS", "2")
    runner._reset_slots()
    gate = threading.Event()
    active, peak, lock = [0], [0], threading.Lock()

    def fake(run_id):
        runner.write_status(run_id, "running", message=None)
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        gate.wait(10)
        with lock:
            active[0] -= 1
        runner.write_status(run_id, "done")

    monkeypatch.setattr(runner, "_execute_scoped", fake)
    yield gate, peak
    gate.set()
    runner._reset_slots()


def test_the_limit_defaults_to_two_and_reads_the_environment(monkeypatch):
    monkeypatch.delenv("MAX_CONCURRENT_RUNS", raising=False)
    assert config.max_concurrent_runs() == 2
    monkeypatch.setenv("MAX_CONCURRENT_RUNS", "5")
    assert config.max_concurrent_runs() == 5
    monkeypatch.setenv("MAX_CONCURRENT_RUNS", "0")
    assert config.max_concurrent_runs() == 1
    monkeypatch.setenv("MAX_CONCURRENT_RUNS", "abc")
    assert config.max_concurrent_runs() == 2


def test_a_third_run_waits_queued_with_a_message_and_starts_when_a_slot_frees(slow_runs):
    gate, peak = slow_runs
    ids = [runner.create_run("g", "a.csv", b"x\n1\n") for _ in range(3)]
    threads = [runner.start_run(i) for i in ids]
    assert _wait(lambda: sum(1 for i in ids if runner.read_status(i)["status"] == "running") == 2)
    assert _wait(lambda: runner.read_status(ids[2]).get("message") == runner.QUEUED_MESSAGE)
    st = runner.read_status(ids[2])
    assert st["status"] == "queued"
    api = client.get(f"/api/runs/{ids[2]}").json()                   # the status API says it
    assert api["status"] == "queued" and api["message"] == "Waiting for other runs to finish"
    from app.events import get_events                                  # the progress stream carries it
    assert any(e["message"] == "Waiting for other runs to finish" for e in get_events(ids[2]))
    resp = asyncio.run(main.stream_events(ids[2]))

    async def first():
        async for chunk in resp.body_iterator:
            return chunk
    assert "Waiting for other runs to finish" in asyncio.run(asyncio.wait_for(first(), 5))
    assert peak[0] == 2                                                # never three at once
    gate.set()
    [t.join(10) for t in threads]
    assert all(runner.read_status(i)["status"] == "done" for i in ids)
    assert peak[0] == 2
    assert "message" not in client.get(f"/api/runs/{ids[2]}").json()


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_run_that_fails_still_frees_its_slot(monkeypatch):
    monkeypatch.setenv("MAX_CONCURRENT_RUNS", "1")
    runner._reset_slots()

    def boom(run_id):
        raise RuntimeError("x")
    monkeypatch.setattr(runner, "_execute_scoped", boom)
    rid = runner.create_run("g", "a.csv", b"x\n1\n")
    t = runner.start_run(rid)
    t.join(5)
    assert runner._run_slots().acquire(blocking=False)                 # the slot came back
    runner._reset_slots()
