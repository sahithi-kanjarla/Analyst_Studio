"""AI rate limits: one retry layer, retry-after honoured (capped), per-day limit stops the AI for the run, identical calls cached, short header reasons.
Fake clients only: no network, no key."""
import os

os.environ["LLM_PROVIDER"] = "none"

import pytest

from app import graph, llm as llm_mod
from app.propose import propose


class Msg:
    def __init__(self, content):
        self.content = content


class Resp:
    def __init__(self, headers):
        self.headers = headers


class RateLimited(Exception):
    """Looks like the SDKs' 429: a message and a response with headers."""
    def __init__(self, message="Error code: 429 - rate limit reached", headers=None):
        super().__init__(message)
        self.response = Resp(headers or {})


class Flaky:
    """Raises `error` for the first `fails` invocations, then answers."""
    def __init__(self, error, fails=10**6):
        self.error, self.fails, self.requests = error, fails, 0

    def invoke(self, messages):
        self.requests += 1
        if self.requests <= self.fails:
            raise self.error
        return Msg('{"ok": 1}')


@pytest.fixture
def sleeps(monkeypatch):
    waited = []
    monkeypatch.setattr(llm_mod, "_sleep", waited.append)
    return waited


def test_clients_are_built_without_their_own_retries():
    assert llm_mod.MAX_RETRIES == 0       # the SDK default of 2 turned one 429 into three requests, and our retry doubled that


def test_a_429_with_retry_after_waits_that_long_and_retries_twice(sleeps):
    llm = Flaky(RateLimited(headers={"retry-after": "7"}))
    out, reason = llm_mod.ask_json_ex(llm, "s", "u")
    assert out is None and reason == "rate limit"
    assert llm.requests == 3 and sleeps == [7.0, 7.0]       # 1 request + 2 retries, nothing multiplied


def test_a_429_without_retry_after_still_retries_with_a_default_wait(sleeps):
    llm = Flaky(RateLimited())
    out, reason = llm_mod.ask_json_ex(llm, "s", "u")
    assert out is None and reason == "rate limit"
    assert llm.requests == 3 and sleeps == [llm_mod.RATE_LIMIT_BACKOFF_S] * 2


def test_retry_after_is_capped_at_60_seconds(sleeps):
    llm_mod.ask_json_ex(Flaky(RateLimited(headers={"retry-after": "900"})), "s", "u")
    assert sleeps == [60, 60]


def test_retry_after_ms_is_understood(sleeps):
    llm_mod.ask_json_ex(Flaky(RateLimited(headers={"retry-after-ms": "2500"})), "s", "u")
    assert sleeps == [2.5, 2.5]


def test_a_retry_that_succeeds_ends_the_waiting(sleeps):
    llm = Flaky(RateLimited(headers={"retry-after": "3"}), fails=2)
    out, reason = llm_mod.ask_json_ex(llm, "s", "u")
    assert out == {"ok": 1} and reason == "" and llm.requests == 3 and sleeps == [3.0, 3.0]


DAILY = RateLimited("Rate limit reached for model `m` in organization `org_SECRET123` on tokens per day (TPD): Limit 200000, Used 199965. key gsk_SECRET", {"retry-after": "242"})


def test_a_per_day_limit_is_not_retried_and_stops_the_ai_for_the_rest_of_the_run(sleeps):
    llm = Flaky(DAILY)
    assert llm_mod.ask_json_ex(llm, "s", "first") == (None, llm_mod.DAILY_LIMIT)
    assert llm.requests == 1 and sleeps == []
    other = Flaky(RateLimited(), fails=0)
    assert llm_mod.ask_json_ex(other, "s", "second") == (None, llm_mod.DAILY_LIMIT)       # a different call is not even sent
    assert other.requests == 0 and llm_mod.daily_limit_hit()
    llm_mod.begin_run()                                                                  # the next run tries again
    assert llm_mod.ask_json_ex(other, "s", "third")[0] == {"ok": 1}


def test_identical_calls_in_a_run_are_sent_once(sleeps):
    llm_mod.begin_run(cache=True)
    llm = Flaky(RateLimited(), fails=0)
    for _ in range(3):
        assert llm_mod.ask_json_ex(llm, "s", "same")[0] == {"ok": 1}
    assert llm.requests == 1
    llm_mod.ask_json_ex(llm, "s", "different")
    assert llm.requests == 2
    llm_mod.begin_run(cache=True)                                                        # a new run starts empty
    llm_mod.ask_json_ex(llm, "s", "same")
    assert llm.requests == 3


def test_without_a_run_nothing_is_cached(sleeps):
    llm = Flaky(RateLimited(), fails=0)
    llm_mod.ask_json_ex(llm, "s", "same")
    llm_mod.ask_json_ex(llm, "s", "same")
    assert llm.requests == 2


def test_a_failed_call_is_not_cached(sleeps):
    llm_mod.begin_run(cache=True)
    llm = Flaky(RateLimited(), fails=4)
    assert llm_mod.ask_json_ex(llm, "s", "u")[0] is None
    assert llm_mod.ask_json_ex(llm, "s", "u")[0] == {"ok": 1}


def test_propose_does_not_ask_again_after_a_rate_limit(sleeps):
    llm = Flaky(RateLimited(headers={"retry-after": "1"}))
    out = propose(llm, "x", "s", "v", lambda o: (o, []), lambda: "rules")
    assert out["source"] == "fallback" and out["value"] == "rules" and llm.requests == 3     # one ask with its 2 retries, no repair ask


@pytest.mark.parametrize("error,header", [
    (RateLimited(headers={"retry-after": "5"}), "AI unavailable for insights: rate limit"),
    (DAILY, "AI unavailable for the rest of the run: daily rate limit reached"),
])
def test_the_header_reason_is_short_and_leaks_nothing(sleeps, error, header):
    llm = Flaky(error)
    with llm_mod.capture() as cap:
        llm_mod.ask_json(llm, "s", "u")
    out = graph._ai_failed({}, {}, "insights", llm, cap)
    assert out["ai_notes"] == [header]
    shown = " ".join(out["ai_notes"]) + " " + cap.reason + " " + str(out["decisions"])
    for secret in ("org_", "gsk_", "SECRET", "Limit 200000", "Error code", "429"):
        assert secret not in shown
