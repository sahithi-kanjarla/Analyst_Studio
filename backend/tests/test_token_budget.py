"""Tokens per run: a standard 12-column file stays under a budget, counted with a fake model (about 4 characters a token). The fake answers every call with an empty JSON object, so every stage takes its repair and
fallback path: the worst case for calls. The budget is AI_TOKEN_BUDGET (default 40000)."""
import importlib.util
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("ai_tokens", ROOT / "scripts" / "ai_tokens.py")
ai_tokens = importlib.util.module_from_spec(spec)
sys.modules["ai_tokens"] = ai_tokens
spec.loader.exec_module(ai_tokens)
BUDGET = int(os.environ.get("AI_TOKEN_BUDGET", "40000"))


def test_a_standard_twelve_column_run_stays_under_the_token_budget():
    r = ai_tokens.measure(ai_tokens.STANDARD)
    assert r["status"] == "done"
    assert r["total"] <= BUDGET, f"{r['total']:,} tokens over {r['calls']} calls (budget {BUDGET:,}): " + ", ".join(f"{k} {v['input'] + v['output']:,}" for k, v in list(r["stages"].items())[:4])


def test_a_wide_order_line_file_stays_under_twice_the_budget_and_no_stage_sends_the_same_call_twice():
    r = ai_tokens.measure(ai_tokens.SHOPIFY)
    assert r["status"] == "done" and r["total"] <= 2 * BUDGET
    assert r["calls"] <= 20                               # the 112k-token run made 33 calls: stages ask once and repair at most once


def test_the_run_keeps_its_token_totals_by_stage_in_the_log_and_the_report():
    r = ai_tokens.measure(ai_tokens.STANDARD)
    wd = Path(r["run_dir"])
    usage = json.loads((wd / "ai_usage.json").read_text(encoding="utf-8"))
    assert usage["totals"]["input_tokens"] == r["input"] and usage["by_stage"] and all({"stage", "calls", "input_tokens", "output_tokens"} <= set(s) for s in usage["by_stage"])
    events = (wd / "events.jsonl").read_text(encoding="utf-8")
    assert "AI usage:" in events and "input and" in events
    report = (wd / "report.md").read_text(encoding="utf-8")
    assert "AI usage so far:" in report and "Biggest stages:" in report


def test_a_confident_rule_match_skips_the_answer_picking_call(monkeypatch):
    from app import summary
    state = {"goal": "g", "decisions": []}
    monkeypatch.setattr(summary, "questions", lambda s: ["Which category earns the most?"])
    monkeypatch.setattr(summary, "catalog", lambda s, r: [])
    monkeypatch.setattr(summary, "goal_pick", lambda q, st, items, rankings: ["r1"])
    called = []
    monkeypatch.setattr(summary, "llm_pick", lambda *a, **k: called.append(1) or {0: ["r9"]})
    out = summary.pick(object(), state, [])
    assert out["picks"] == {"Which category earns the most?": ["r1"]} and not called and any("no AI call was made" in d["why"] for d in state["decisions"])
    monkeypatch.setattr(summary, "goal_pick", lambda q, st, items, rankings: [])
    summary.pick(object(), state, [])
    assert called                                          # no confident rule match: the model decides, as before


def test_an_exploratory_run_with_no_kpi_and_no_description_makes_no_brief_call():
    from app import brief
    import pandas as pd
    calls = []

    class M:
        kwargs = {}

        def invoke(self, messages):
            calls.append(1)
            raise AssertionError("no call expected")
    df = pd.DataFrame({"a": range(40), "b": list("xy") * 20})
    state = {"decisions": []}
    from app import profiler
    prof = profiler.profile_df(df)
    out = brief.resolve(M(), df, prof, "explore", {"explore": True, "kpis": []}, state)
    assert not calls and out["source"] == "rules-only" and any("no AI call was made" in d["why"] for d in state["decisions"])
