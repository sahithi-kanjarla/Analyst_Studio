"""AI usage accounting: every call is recorded with its stage, tokens (estimated when the provider gives no count), cache hit and time, and saved as ai_usage.json."""
import json

from app import llm


class Msg:
    def __init__(self, content, usage=None):
        self.content, self.usage_metadata = content, usage


class CountingLLM:
    """A fake model: answers with JSON, optionally with the provider's own token counts."""
    kwargs = {}

    def __init__(self, usage=None):
        self.usage, self.sent = usage, 0

    def invoke(self, messages):
        self.sent += 1
        return Msg('{"ok": true}', self.usage)


class RaisingLLM:
    kwargs = {}

    def invoke(self, messages):
        raise RuntimeError("bad request: invalid input")


def test_call_is_recorded_with_stage_and_estimated_tokens():
    llm.begin_run(cache=True)
    obj, reason = llm.ask_json_ex(CountingLLM(), "You are the Planner.", "x" * 400)
    assert obj == {"ok": True} and reason == ""
    (call,) = llm.usage_calls()
    assert call["stage"].endswith("test_call_is_recorded_with_stage_and_estimated_tokens")
    assert call["tokens_estimated"] and call["input_tokens"] > 100 and call["output_tokens"] >= 1
    assert call["cache_hit"] is False and call["ok"] is True and call["seconds"] >= 0


def test_provider_token_counts_are_used_as_given():
    llm.begin_run(cache=True)
    llm.ask_json_ex(CountingLLM(usage={"input_tokens": 120, "output_tokens": 9}), "sys", "user")
    (call,) = llm.usage_calls()
    assert (call["input_tokens"], call["output_tokens"], call["tokens_estimated"]) == (120, 9, False)


def test_repeated_call_in_one_run_is_a_cache_hit_and_sends_nothing():
    llm.begin_run(cache=True)
    model = CountingLLM()
    llm.ask_json_ex(model, "sys", "same prompt")
    llm.ask_json_ex(model, "sys", "same prompt")
    assert model.sent == 1
    first, second = llm.usage_calls()
    assert first["cache_hit"] is False and second["cache_hit"] is True
    assert second["input_tokens"] == 0 and second["output_tokens"] == 0
    totals = llm.usage_summary()
    assert (totals["calls"], totals["sent"], totals["cached"]) == (2, 1, 1)


def test_failed_call_is_recorded_with_its_reason():
    llm.begin_run(cache=True)
    obj, reason = llm.ask_json_ex(RaisingLLM(), "sys", "user")
    assert obj is None and reason
    failed = [c for c in llm.usage_calls() if not c["ok"]]
    assert failed and failed[0]["reason"] == reason


def test_save_usage_writes_ai_usage_json_and_appends_on_resume(tmp_path):
    llm.begin_run(cache=True)
    llm.ask_json_ex(CountingLLM(), "sys", "first part")
    llm.save_usage(tmp_path)
    llm.begin_run(cache=True)                       # the resumed part of the same run
    llm.ask_json_ex(CountingLLM(), "sys", "second part")
    llm.save_usage(tmp_path)
    data = json.loads((tmp_path / "ai_usage.json").read_text(encoding="utf-8"))
    assert len(data["calls"]) == 2
    assert data["totals"]["calls"] == 2 and data["totals"]["sent"] == 2 and data["totals"]["estimated"] is True


def test_per_stage_table_rows_add_up():
    calls = [
        {"stage": "planner.plan", "ok": True, "cache_hit": False, "input_tokens": 100, "output_tokens": 10, "tokens_estimated": True, "seconds": 1.0},
        {"stage": "planner.plan", "ok": True, "cache_hit": True, "input_tokens": 0, "output_tokens": 0, "tokens_estimated": False, "seconds": 0.0},
        {"stage": "reporter.summary", "ok": False, "cache_hit": False, "input_tokens": 50, "output_tokens": 0, "tokens_estimated": False, "seconds": 2.5},
    ]
    rows = {r["stage"]: r for r in llm.by_stage(calls)}
    assert list(rows) == ["planner.plan", "reporter.summary"]
    assert rows["planner.plan"]["calls"] == 2 and rows["planner.plan"]["cached"] == 1 and rows["planner.plan"]["estimated"] is True
    assert rows["reporter.summary"]["failed"] == 1 and rows["reporter.summary"]["seconds"] == 2.5
    assert llm.totals(calls)["input_tokens"] == 150
