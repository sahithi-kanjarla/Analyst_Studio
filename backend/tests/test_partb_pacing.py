"""Task D: calls are spaced to fit a per-minute token window. Fake clock, fake AI: nothing really waits."""
import os

os.environ["LLM_PROVIDER"] = "none"

import json

import pytest

from app import llm, pacing


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


class FakeAI:
    kwargs = {}

    def __init__(self, clock, log):
        self.clock, self.log = clock, log

    def invoke(self, messages):
        self.log.append((self.clock.t, (len(messages[0][1]) + len(messages[1][1])) // 4 + pacing.DEFAULT_REPLY_TOKENS))
        return type("R", (), {"content": json.dumps({"ok": 1})})()


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(pacing, "_now", c.now)
    monkeypatch.setattr(pacing, "_sleep", c.sleep)
    monkeypatch.setenv("AI_TPM_LIMIT", "6000")
    monkeypatch.setenv("AI_TPM_WINDOW_S", "60")
    pacing.reset()
    return c


def ask(model, i, size=8000):
    return llm.ask_json(model, "system prompt", f"call {i} " + "x" * size)       # ~2000 input tokens + the 600 reserve each


def test_a_burst_is_spaced_out_and_never_exceeds_the_window(clock):
    log = []
    model = FakeAI(clock, log)
    outs = [ask(model, i) for i in range(10)]
    assert all(o == {"ok": 1} for o in outs)           # every call completed
    assert len(log) == 10 and clock.sleeps               # and some had to wait
    # in any 60 s window the tokens sent stay at or under the limit
    for start, _ in log:
        in_window = sum(t for at, t in log if start <= at < start + 60)
        assert in_window <= 6000, (start, in_window)
    assert pacing.STATS["waits"] >= 1 and pacing.STATS["waited_s"] > 0


def test_no_wait_when_the_window_has_room(clock):
    model = FakeAI(clock, [])
    ask(model, 0, 400)
    ask(model, 1, 400)
    assert clock.sleeps == []


def test_a_call_bigger_than_the_window_still_goes(clock):
    log = []
    model = FakeAI(clock, log)
    assert llm.ask_json(model, "s", "y" * 60000) == {"ok": 1}      # ~15 000 tokens against a 6 000 window
    assert len(log) == 1


def test_pacing_off_and_provider_default(monkeypatch):
    monkeypatch.setenv("AI_TPM_LIMIT", "0")
    assert pacing.wait_for_room(10**6) == 0.0
    monkeypatch.delenv("AI_TPM_LIMIT")
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    assert pacing.limit() == pacing.DEFAULT_LIMIT
    monkeypatch.setenv("LLM_PROVIDER", "ollama")
    assert pacing.limit() == 0


def test_prompts_are_unchanged_by_pacing(clock):
    seen = []

    class Spy:
        kwargs = {}

        def invoke(self, messages):
            seen.append(messages)
            return type("R", (), {"content": "{}"})()
    assert llm._invoke(Spy(), "SYS", "USER") == ("{}", "")
    assert seen == [[("system", "SYS"), ("human", "USER")]]
