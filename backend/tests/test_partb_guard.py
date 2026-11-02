"""Task C: public-use protections (access code, daily caps, per-visitor upload rate limit). Fake AI, no network; no analysis is really run."""
import os

os.environ["LLM_PROVIDER"] = "none"

import pytest
from fastapi.testclient import TestClient

from app import guard, main
from app.lens import chat
from tests.test_lens import FakeAI, make_run, q

client = TestClient(main.app)
CSV = ("sales.csv", b"order_date,amount\n2024-01-01,10\n2024-01-02,20\n", "text/csv")


@pytest.fixture(autouse=True)
def quiet(monkeypatch, tmp_path):
    started = []
    monkeypatch.setattr(main, "start_run", lambda rid: started.append(rid))
    monkeypatch.setattr(main, "get_llm", lambda: (FakeAI({}), "fake"))
    monkeypatch.setattr(guard, "RUNS_DIR", tmp_path / "runs")        # usage_daily.json goes to a throw-away folder
    guard.reset_rate()
    for k in ("UPLOADS_REQUIRE_CODE", "DAILY_AI_RUNS", "DAILY_LENS_QUESTIONS", "UPLOAD_RATE_LIMIT", "TRUST_PROXY_HEADERS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("UPLOAD_RATE_LIMIT", "0")
    monkeypatch.setenv("DAILY_AI_RUNS", "0")
    monkeypatch.setenv("DAILY_LENS_QUESTIONS", "0")
    return started


def upload(**data):
    return client.post("/api/runs", files={"files": CSV}, data={"goal": "", **data})


# ------------------------------------------------------------------ access code
def test_open_by_default_and_health_says_no_code_needed(quiet):
    assert upload().status_code == 200 and client.get("/api/config").json()["requires_code"] is False


def test_code_is_required_when_set_and_the_sample_stays_open(monkeypatch, quiet):
    monkeypatch.setenv("UPLOADS_REQUIRE_CODE", "open-sesame")
    assert client.get("/api/config").json()["requires_code"] is True
    r = upload()
    assert r.status_code == 403 and "access code" in r.json()["detail"].lower() and not quiet
    r = upload(access_code="wrong")
    assert r.status_code == 403 and "not accepted" in r.json()["detail"] and not quiet
    assert upload(access_code="open-sesame").status_code == 200 and len(quiet) == 1
    assert client.post("/api/runs", files={"files": CSV}, data={"goal": ""}, headers={"X-Access-Code": "open-sesame"}).status_code == 200
    assert client.post("/api/demo").status_code == 200                # the sample report and demo are open to everyone
    assert client.get("/api/health").status_code == 200


def test_code_is_needed_for_typed_lens_questions_but_not_suggestions(monkeypatch):
    monkeypatch.setenv("CHATBOT", "on")
    monkeypatch.setenv("UPLOADS_REQUIRE_CODE", "abc")
    rid, wd, result = make_run()
    ai = FakeAI(q(op="aggregate", agg="count"))
    monkeypatch.setattr(main, "lens_llm", lambda: ai)
    chat._DAILY["until"] = 0.0
    assert client.post(f"/api/runs/{rid}/lens/ask", json={"question": "how many rows are there?"}).status_code == 403 and ai.calls == 0
    info = client.get(f"/api/runs/{rid}/lens").json()
    assert client.post(f"/api/runs/{rid}/lens/ask", json={"chip": info["chips"][0]["id"]}).status_code == 200      # a suggestion never uses the AI
    ok = client.post(f"/api/runs/{rid}/lens/ask", json={"question": "how many rows are there?"}, headers={"X-Access-Code": "abc"})
    assert ok.status_code == 200 and ai.calls >= 1


# ------------------------------------------------------------------ daily caps
def test_daily_ai_run_cap_gives_a_friendly_message_and_resets_next_day(monkeypatch, quiet):
    monkeypatch.setenv("DAILY_AI_RUNS", "2")
    assert upload().status_code == 200 and upload().status_code == 200
    r = upload()
    assert r.status_code == 429 and "Today's limit of AI analyses" in r.json()["detail"] and len(quiet) == 2
    assert client.post("/api/demo").status_code == 200                # the sample is still open
    monkeypatch.setattr(guard, "_now", lambda: __import__("time").time() + 86400 * 1.01)
    assert upload().status_code == 200                                # a new UTC day


def test_runs_without_ai_do_not_count_against_the_cap(monkeypatch, quiet):
    monkeypatch.setenv("DAILY_AI_RUNS", "1")
    monkeypatch.setattr(main, "get_llm", lambda: (None, "rules-only"))
    for _ in range(3):
        assert upload().status_code == 200
    assert guard.used("ai_runs") == 0


def test_daily_lens_cap_counts_only_ai_questions_and_suggestions_still_work(monkeypatch):
    monkeypatch.setenv("CHATBOT", "on")
    monkeypatch.setenv("DAILY_LENS_QUESTIONS", "2")
    rid, wd, result = make_run()
    ai = FakeAI(q(op="aggregate", agg="count"))
    monkeypatch.setattr(main, "lens_llm", lambda: ai)
    chat._DAILY["until"] = 0.0
    ask = lambda text: client.post(f"/api/runs/{rid}/lens/ask", json={"question": text})
    assert ask("how many rows are there?").json()["answer"]["status"] == "answered"
    assert ask("how many records in total?").json()["answer"]["status"] == "answered"
    calls = ai.calls
    r = ask("count the rows again").json()
    assert r["answer"]["status"] == "limit" and "Today's limit of AI questions" in r["answer"]["text"] and ai.calls == calls
    chip = client.get(f"/api/runs/{rid}/lens").json()["chips"][0]["id"]
    assert client.post(f"/api/runs/{rid}/lens/ask", json={"chip": chip}).json()["answer"]["status"] == "answered"       # suggestions never count
    assert guard.used("lens_questions") == 2


def test_the_cap_survives_a_restart(monkeypatch, quiet):
    monkeypatch.setenv("DAILY_AI_RUNS", "1")
    assert upload().status_code == 200
    guard._uploads.clear()                                             # a fresh process has no memory, only the saved count
    assert upload().status_code == 429


# ------------------------------------------------------------------ per-visitor upload rate limit
def test_upload_rate_limit_is_per_visitor(monkeypatch, quiet):
    monkeypatch.setenv("UPLOAD_RATE_LIMIT", "2")
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "1")
    a, b = {"X-Forwarded-For": "1.1.1.1"}, {"X-Forwarded-For": "2.2.2.2"}
    post = lambda h: client.post("/api/runs", files={"files": CSV}, data={"goal": ""}, headers=h)
    assert post(a).status_code == 200 and post(a).status_code == 200
    r = post(a)
    assert r.status_code == 429 and "minute" in r.json()["detail"] and len(quiet) == 2
    assert post(b).status_code == 200                                  # another visitor is not affected
    t0 = __import__("time").time()
    monkeypatch.setattr(guard, "_now", lambda: t0 + 3601)
    assert post(a).status_code == 200                                  # the window has passed


def test_forwarded_header_is_ignored_unless_trusted(monkeypatch, quiet):
    monkeypatch.setenv("UPLOAD_RATE_LIMIT", "1")
    post = lambda ip: client.post("/api/runs", files={"files": CSV}, data={"goal": ""}, headers={"X-Forwarded-For": ip})
    assert post("1.1.1.1").status_code == 200
    assert post("9.9.9.9").status_code == 429                          # spoofing the header does not buy a new allowance
