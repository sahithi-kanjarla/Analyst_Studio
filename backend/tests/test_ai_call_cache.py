"""Every AI call is cached on disk by the full prompt text, model name and temperature: the same call is answered from disk, any change is a miss, only successful results are
stored, entries expire, AI_CACHE=off turns it off, and the report says which stages used a saved result. A fake chat model counts the calls."""
import json
import os

os.environ["LLM_PROVIDER"] = "none"

import pytest  # noqa: E402
from test_main_view import main_of, render, state_with  # noqa: E402

from app import aicache, llm as llm_mod  # noqa: E402

REUSE_LINE = "Reused saved AI result:"


@pytest.fixture(autouse=True)
def cache_on(monkeypatch):
    monkeypatch.setenv("AI_CACHE", "on")


class Msg:
    def __init__(self, content):
        self.content = content


class FakeModel:
    """A chat model that counts its calls; `replies` is a list used in order (the last one repeats). A reply that is an Exception is raised."""
    def __init__(self, replies=None, model_name="fake-1", temperature=0.2):
        self.replies, self.calls, self.model_name, self.temperature = replies, 0, model_name, temperature

    def invoke(self, messages):
        self.calls += 1
        if self.replies is not None:
            r = self.replies[min(self.calls, len(self.replies)) - 1]
            if isinstance(r, Exception):
                raise r
            return Msg(r)
        human = messages[1][1]
        if "GOAL QUESTIONS AND THEIR FACTS" in human:
            return Msg(json.dumps({"actions": [{"question": 1, "text": "Review how segment a reaches 62.5% of all revenue before shifting budget [r1]."}]}))
        return Msg(json.dumps({"summary": "Fine [counts].", "recommendations": ["Do it."], "so_what": []}))


def pipeline(model):
    """One report run with the real AI layer (summary, so-what and the extra action call) on a fresh state."""
    llm_mod.begin_run()
    return render(state_with(strategies=False), model)


def test_the_same_pipeline_twice_calls_the_model_only_the_first_time_and_the_second_report_says_so():
    model = FakeModel()
    first = pipeline(model)
    calls = model.calls
    assert calls >= 2 and REUSE_LINE not in main_of(first) + first
    second = pipeline(model)
    assert model.calls == calls                                                      # nothing was sent again
    assert f"- {REUSE_LINE} " in second and "reporter.llm_summary" in second and "reporter.llm_actions" in second


def test_ai_cache_off_calls_the_model_every_time_and_reports_no_reuse(monkeypatch):
    monkeypatch.setenv("AI_CACHE", "off")
    model = FakeModel()
    pipeline(model)
    calls = model.calls
    second = pipeline(model)
    assert model.calls == 2 * calls and REUSE_LINE not in second


def test_a_changed_prompt_model_or_temperature_is_a_miss():
    model = FakeModel(['{"a": 1}'])
    assert llm_mod.ask_json(model, "sys", "user") == {"a": 1}
    assert llm_mod.ask_json(model, "sys", "user") == {"a": 1} and model.calls == 1
    llm_mod.ask_json(model, "sys", "other user")
    llm_mod.ask_json(model, "other sys", "user")
    assert model.calls == 3
    llm_mod.ask_json(FakeModel(['{"a": 1}'], model_name="fake-2"), "sys", "user")
    other_temp = FakeModel(['{"a": 1}'], temperature=0.0)
    llm_mod.ask_json(other_temp, "sys", "user")
    assert other_temp.calls == 1


def test_entries_expire_after_cache_days(monkeypatch):
    model = FakeModel(['{"a": 1}'])
    llm_mod.ask_json(model, "sys", "user")
    llm_mod.ask_json(model, "sys", "user")
    assert model.calls == 1
    now = aicache.time.time()
    monkeypatch.setattr(aicache.time, "time", lambda: now + 6 * 86400)
    llm_mod.ask_json(model, "sys", "user")
    assert model.calls == 1                                                          # six days old: still fresh with the default of 7
    monkeypatch.setattr(aicache.time, "time", lambda: now + 8 * 86400)
    llm_mod.ask_json(model, "sys", "user")
    assert model.calls == 2
    monkeypatch.setenv("CACHE_DAYS", "20")
    monkeypatch.setattr(aicache.time, "time", lambda: now + 9 * 86400)
    llm_mod.ask_json(model, "sys", "user")
    assert model.calls == 2                                                          # the entry from the second call is fresh


def test_failed_and_unusable_calls_are_not_cached():
    model = FakeModel([RuntimeError("boom"), RuntimeError("boom"), RuntimeError("boom"), "no json at all", "still no json", '{"ok": true}'])
    assert llm_mod.ask_json(model, "sys", "user") is None                           # raised on every try
    before = model.calls
    assert llm_mod.ask_json(model, "sys", "user") is None and model.calls > before  # asked again: the failure was not stored
    assert llm_mod.ask_json(model, "sys", "user") == {"ok": True}
    n = model.calls
    assert llm_mod.ask_json(model, "sys", "user") == {"ok": True} and model.calls == n      # the success is stored


def test_ask_text_is_cached_too_and_noted_as_reused():
    model = FakeModel(["hello"])
    assert llm_mod.ask_text(model, "s", "u") == "hello" and llm_mod.ask_text(model, "s", "u") == "hello"
    assert model.calls == 1 and aicache.reused_stages() == ["test_ai_call_cache.test_ask_text_is_cached_too_and_noted_as_reused"]
