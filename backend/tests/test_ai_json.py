"""Every AI call that expects JSON: JSON mode where the provider has one, the first valid object taken from fenced or prose-wrapped replies, one repair retry that shows the model what was wrong,
the stage that fell back named in the report, and rate limits waited out (retry-after, capped, twice) before a stage falls back."""
import json
import os

os.environ["LLM_PROVIDER"] = "none"

import pytest

from app import llm
from app.lens import records  # noqa: F401


class Msg:
    def __init__(self, content):
        self.content = content


class Scripted:
    """A fake model that answers each call with the next reply; an Exception in the list is raised."""
    kwargs = {}

    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def invoke(self, messages):
        self.prompts.append(messages[1][1])
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(r, Exception):
            raise r
        return Msg(r)


@pytest.fixture(autouse=True)
def fresh():
    llm.begin_run(cache=False)
    llm.JSON_MODE_OFF.clear()
    yield


def test_fenced_json_is_read():
    m = Scripted('```json\n{"plan": ["a"]}\n```')
    assert llm.ask_json_ex(m, "s", "u") == ({"plan": ["a"]}, "") and len(m.prompts) == 1


def test_json_after_prose_or_reasoning_is_read():
    for raw in ('Sure, here is the plan you asked for:\n{"plan": [1]}\nLet me know!', '<think>maybe {"plan": 0}</think>\n{"plan": [1]}', 'Thinking it over...\n```\n{"plan": [1]}\n```\nDone.'):
        assert llm.ask_json_ex(Scripted(raw), "s", "u")[0] == {"plan": [1]}


def test_truncated_json_says_it_was_cut_off_and_is_asked_again_once_with_the_error():
    m = Scripted('{"plan": [1, 2, {"x":', '{"plan": [1, 2]}')
    obj, why = llm.ask_json_ex(m, "s", "u", repair=True)
    assert obj == {"plan": [1, 2]} and why == "" and len(m.prompts) == 2
    assert "cut off" in m.prompts[1] and '{"plan": [1, 2, {"x":' in m.prompts[1]            # the retry shows the model its error and what it sent


def test_valid_json_on_the_second_try_is_used_and_never_a_third_try():
    m = Scripted("I cannot do that.", "still not json", '{"ok": true}')
    obj, why = llm.ask_json_ex(m, "s", "u", repair=True)
    assert obj is None and len(m.prompts) == 2 and "bad JSON" in why
    m2 = Scripted("no", '{"ok": true}')
    assert llm.ask_json_ex(m2, "s", "u", repair=True)[0] == {"ok": True}


def test_the_reply_shape_is_validated_and_the_problems_are_shown_on_the_retry():
    need = lambda o: [] if isinstance(o.get("items"), list) else ["'items' must be a list"]
    m = Scripted('{"items": "x"}', '{"items": [1]}')
    obj, why = llm.ask_json_ex(m, "s", "u", repair=True, validate=need)
    assert obj == {"items": [1]} and "'items' must be a list" in m.prompts[1]
    m = Scripted('{"items": "x"}')
    obj, why = llm.ask_json_ex(m, "s", "u", repair=True, validate=need)
    assert obj is None and why.startswith("unexpected shape") and len(m.prompts) == 2
    assert llm.ask_json_ex(Scripted('{"items": "x"}'), "s", "u", repair=False, validate=need)[1].startswith("unexpected shape")


def test_a_failed_reply_is_not_saved_and_a_good_one_is(monkeypatch):
    saved = {}
    monkeypatch.setattr(llm.aicache, "put", lambda k, v: saved.update({k: v}))
    monkeypatch.setattr(llm.aicache, "get", lambda k: saved.get(k))
    llm.ask_json_ex(Scripted("nope"), "s", "u")
    assert not saved
    llm.ask_json_ex(Scripted('{"a": 1}'), "s", "u")
    assert len(saved) == 1


def test_json_mode_is_requested_where_the_provider_has_one_and_dropped_when_refused():
    class ChatGroq:
        kwargs = {}

        def __init__(self, fail=False, bound=None):
            self.fail, self.bound_kwargs, self.calls = fail, bound, 0

        def bind(self, **kw):
            b = ChatGroq(self.fail, kw)
            b.calls_owner = self
            return b

        def invoke(self, messages):
            if self.bound_kwargs and self.fail:
                raise RuntimeError("400 invalid response_format for this model")
            return Msg('{"a": 1}')
    m = ChatGroq()
    assert llm.json_bound(m).bound_kwargs == {"response_format": {"type": "json_object"}}
    bad = ChatGroq(fail=True)
    assert llm.ask_json_ex(bad, "s", "u")[0] == {"a": 1} and "chatgroq" in llm.JSON_MODE_OFF          # refused once, then asked without it
    assert llm.json_bound(bad) is bad
    assert llm.json_bound(Scripted("x")).__class__.__name__ == "Scripted"                              # a model with no JSON mode is left alone


class Limited(Exception):
    def __init__(self, wait=None):
        super().__init__("429 rate limit reached: please try again later")
        self.response = type("R", (), {"headers": {"retry-after": str(wait)} if wait is not None else {}})()


def test_a_rate_limit_waits_for_the_hint_with_a_cap_and_retries_twice(monkeypatch):
    waits = []
    monkeypatch.setattr(llm, "_sleep", waits.append)
    ok = Scripted(Limited(7), Limited(500), '{"a": 1}')
    assert llm.ask_json_ex(ok, "s", "u") == ({"a": 1}, "") and waits == [7, llm.RATE_LIMIT_WAIT_CAP_S]
    waits.clear()
    never = Scripted(Limited(3), Limited(3), Limited(3), '{"a": 1}')
    obj, why = llm.ask_json_ex(never, "s", "u")
    assert obj is None and why.startswith("rate limit") and len(never.prompts) == 3 and len(waits) == 2          # one try and two retries, then the stage falls back
    assert llm.RATE_LIMIT_RETRIES == 2


def test_a_daily_limit_is_not_retried(monkeypatch):
    monkeypatch.setattr(llm, "_sleep", lambda s: (_ for _ in ()).throw(AssertionError("must not wait")))
    class Daily(Exception):
        pass
    m = Scripted(Exception("429 Rate limit reached for tokens per day (TPD)"))
    obj, why = llm.ask_json_ex(m, "s", "u")
    assert obj is None and why == llm.DAILY_LIMIT and len(m.prompts) == 1


def test_the_stage_that_fell_back_is_named_in_the_pages_trust_label():
    from app import reporter
    state = {"decisions": [{"decision": "data understanding", "source": "fallback"}, {"decision": "AI unavailable: the report summary", "source": "fallback"}, {"decision": "the analysis plan", "source": "llm"}, {"decision": "data understanding", "source": "fallback"}],
             "plan": {"actions": [], "none": {}}, "context": {}}
    page = reporter._page(state, "", [], {"actions": [], "none": {}}, [], [], [])
    assert page["ai_partly"] == ["data understanding", "the report summary"]
    assert reporter._page({"decisions": [], "context": {}}, "", [], {"actions": [], "none": {}}, [], [], [])["ai_partly"] == []


def test_a_partly_run_names_every_stage_that_did_not_use_the_ai_and_why():
    from app import reporter
    # the shape of a real run: the AI answered for most stages, but insights kept the rule text and the report summary dropped sentences
    src = {"brief": "llm", "planner": "llm", "insights": "rules", "strategy": "llm", "report": "mixed"}
    state = {"decisions": [{"decision": "report_chart_caption", "source": "fallback"}], "stage_sources": src, "context": {}}
    got = reporter.ai_partly(state)
    assert got[0] == "report_chart_caption"
    assert any(g.startswith("insights (") for g in got) and any(g.startswith("report summary (") for g in got)
    assert len(got) == 3
    # no unnamed gap: a partly run always names at least one stage
    assert reporter.ai_partly({"decisions": [], "stage_sources": src})
    # a stage already named by a fallback decision is not listed twice; a run with no AI at all is "Rules only", not partly
    assert reporter.ai_partly({"decisions": [{"decision": "insights", "source": "fallback"}], "stage_sources": {"brief": "llm", "insights": "rules"}}) == ["insights"]
    assert reporter.ai_partly({"decisions": [], "stage_sources": {"brief": "rules", "insights": "rules"}}) == []
