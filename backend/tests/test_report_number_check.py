"""LLM report summary: numbers are verified per sentence; only unverified sentences are dropped, and every fallback is logged."""
import os

os.environ["LLM_PROVIDER"] = "none"

from app import reporter  # noqa: E402
from app.insights import allowed_numbers, numbers_ok, unverified_numbers  # noqa: E402


def make_state():
    finding = {"id": "f1", "tool": "category_share", "text": "**Month-to-month** is 38.5% of customers.", "stats": {"share": 0.385},
               "insight": {"headline": "Month-to-month dominates", "score": 5, "confidence": {"label": "Descriptive"}}}
    return {"goal": "why do customers leave", "findings": [finding], "critique": {"issues": []}, "profile": {"rows": 7043, "cols": 12},
            "plan": {"explore": False, "target": "Churn"}, "context": None, "decisions": []}


def run(monkeypatch, out):
    monkeypatch.setattr(reporter, "ask_json", lambda *a, **k: out)
    state = make_state()
    return reporter.llm_summary(object(), state), state


def test_rounded_share_is_kept_and_invented_number_drops_only_its_sentence(monkeypatch):
    res, state = run(monkeypatch, {"summary": "Month-to-month is 38% of customers. Churn would fall 52% if fixed. Contracts matter.", "recommendations": ["Review the month-to-month plan."]})
    summary, recs, partial = res
    assert "38%" in summary and "Contracts matter." in summary and "52%" not in summary
    assert recs == ["Review the month-to-month plan."] and partial
    entry = state["decisions"][-1]
    assert entry["source"] == "mixed" and "52%" in entry["errors"][0] and "Churn would fall 52%" in entry["raw_output"]


def test_unverified_recommendation_is_dropped_but_the_rest_stays(monkeypatch):
    res, _ = run(monkeypatch, {"summary": "Month-to-month is 38.5% of customers.", "recommendations": ["Cut churn by 83%.", "Pilot a discount."]})
    assert res[1] == ["Pilot a discount."] and res[2]


def test_clean_summary_is_not_partial(monkeypatch):
    res, state = run(monkeypatch, {"summary": "Month-to-month is 38.5% of customers.", "recommendations": ["Pilot a discount."]})
    assert res[2] is False and not state["decisions"]


def test_empty_after_dropping_falls_back_and_logs_reason(monkeypatch):
    res, state = run(monkeypatch, {"summary": "Churn is 52%.", "recommendations": ["Pilot a discount."]})
    assert res is None
    assert "numbers_ok" in state["decisions"][-1]["why"] and "52%" in state["decisions"][-1]["errors"][0]


def test_bad_json_and_missing_fields_are_logged(monkeypatch):
    res, state = run(monkeypatch, None)
    assert res is None and "bad JSON" in state["decisions"][-1]["why"]
    res, state = run(monkeypatch, {"summary": "x"})
    assert res is None and "missing fields" in state["decisions"][-1]["why"]


def test_write_report_mode_is_mixed_when_something_was_dropped(monkeypatch):
    monkeypatch.setattr(reporter, "llm_summary", lambda llm, state: ("Kept sentence.", ["Do it."], True))
    state = {**make_state(), "goal_answer": None}
    monkeypatch.setattr(reporter, "build_report", lambda *a, **k: "report")
    monkeypatch.setattr(reporter.wording, "question_answers", lambda *a, **k: [])
    assert reporter.write_report(state, object())[1] == "mixed"


def test_number_formats_and_no_free_small_integers():
    allowed = allowed_numbers("share 0.385, revenue 1,234,567, year 2024, count 7043")
    for ok in ("38.5%", "38%", "39%", "0.39", "0.385", "1.2M", "1.23 million", "1,234,567", "12.3 lakh", "2024", "7,043", "7K"):
        assert numbers_ok(ok, allowed), ok
    for bad in ("52%", "3 steps", "2031", "9"):
        assert not numbers_ok(bad, allowed), bad
    assert unverified_numbers("38% and 52%", allowed) == ["52%"]


def test_currency_symbol_does_not_swallow_the_number(monkeypatch):
    state = make_state()
    state["findings"][0]["text"] = "Average **amount** is highest for Gold (\u20b98,216)."
    monkeypatch.setattr(reporter, "ask_json", lambda *a, **k: {"summary": "Gold averages \u20b98,216.", "recommendations": ["Pilot a discount."]})
    res = reporter.llm_summary(object(), state)
    assert res and res[2] is False and "8,216" in res[0]
