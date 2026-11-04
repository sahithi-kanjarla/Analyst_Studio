"""Runs left queued or running by a stopped server are marked as errors at start; the progress stream gives up on a silent run nobody is working on."""
import asyncio
import json
import os
import time

os.environ["LLM_PROVIDER"] = "none"

from fastapi.testclient import TestClient

from app import main, runner


def _run(status, age=0.0):
    rid = runner.create_run("g", "a.csv", b"x\n1\n")
    runner.write_status(rid, status)
    if age:
        p = runner.run_dir(rid) / "status.json"
        st = json.loads(p.read_text())
        st["updated"] = time.time() - age
        p.write_text(json.dumps(st))
    return rid


def test_start_marks_queued_and_running_runs_as_interrupted_and_leaves_the_rest():
    q, r, d, e, n = _run("queued"), _run("running"), _run("done"), _run("error"), _run("needs_input")
    changed = runner.mark_interrupted()
    assert q in changed and r in changed
    for rid in (q, r):
        st = runner.read_status(rid)
        assert st["status"] == "error" and st["error"] == "interrupted by a restart"
    assert runner.read_status(d)["status"] == "done" and runner.read_status(n)["status"] == "needs_input"
    assert d not in changed and e not in changed and n not in changed


def test_an_interrupted_run_can_then_be_deleted_by_retention():
    rid = _run("running", age=10 * 86400)
    runner.mark_interrupted()
    # mark_interrupted refreshed 'updated': age it again, then the sweep may delete the run
    p = runner.run_dir(rid) / "status.json"
    st = json.loads(p.read_text())
    st["updated"] = time.time() - 10 * 86400
    p.write_text(json.dumps(st))
    assert rid in runner.purge_expired(days=5)
    assert not runner.run_dir(rid).exists()


def test_the_server_start_hook_runs_the_marking():
    rid = _run("running")
    with TestClient(main.app):          # entering the client runs the lifespan
        pass
    assert runner.read_status(rid)["status"] == "error"


async def _drain(resp, limit=10):
    out = []
    async for chunk in resp.body_iterator:
        out.append(chunk)
        if "event: end" in chunk or len(out) >= limit:
            break
    return out


def test_the_stream_ends_with_an_error_for_a_silent_run_nobody_is_working_on(monkeypatch):
    monkeypatch.setenv("STREAM_STALE_S", "1")
    rid = _run("running", age=60)
    chunks = asyncio.run(asyncio.wait_for(_drain(asyncio.run(main.stream_events(rid))), 15))
    assert any("event: end" in c for c in chunks)
    st = runner.read_status(rid)
    assert st["status"] == "error" and "stopped responding" in st["error"]


def test_the_stream_keeps_waiting_for_a_run_whose_thread_is_alive(monkeypatch):
    monkeypatch.setenv("STREAM_STALE_S", "1")
    rid = _run("running", age=60)
    runner._active.add(rid)
    try:
        async def go():
            resp = await main.stream_events(rid)
            return await asyncio.wait_for(_drain(resp, 100), 3)
        try:
            chunks = asyncio.run(go())
            assert not any("event: end" in c for c in chunks)
        except asyncio.TimeoutError:
            pass                                       # still open after 3 s: correct
        assert runner.read_status(rid)["status"] == "running"
    finally:
        runner._active.discard(rid)


def test_a_queued_run_waiting_for_a_slot_is_not_given_up_on(monkeypatch):
    monkeypatch.setenv("STREAM_STALE_S", "1")
    rid = _run("queued", age=60)
    runner._active.add(rid)             # its thread is alive, waiting for a slot
    try:
        async def go():
            return await asyncio.wait_for(_drain(await main.stream_events(rid), 100), 3)
        try:
            assert not any("event: end" in c for c in asyncio.run(go()))
        except asyncio.TimeoutError:
            pass
        assert runner.read_status(rid)["status"] == "queued"
    finally:
        runner._active.discard(rid)
