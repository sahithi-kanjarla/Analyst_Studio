"""LLM integration logic, tested with a fake chat model (no network, no API key).
Verifies: valid plans are used, hallucinated tools/columns are dropped, garbage output falls back to rules,
and the LLM only writes prose while numbers still come from the deterministic tools."""
import json
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np
import pandas as pd
import pytest

from app import llm as llm_mod
from app import runner
from app.config import RUNS_DIR


class Msg:
    def __init__(self, content):
        self.content = content


class FakeLLM:
    def __init__(self, plan, summary="FAKE SUMMARY from the model.", garbage=False):
        self.plan, self.summary, self.garbage = plan, summary, garbage

    def invoke(self, messages):
        system = messages[0][1]
        if self.garbage:
            return Msg("Sure! Here is my thinking, but no JSON at all.")
        if "Planner" in system:
            return Msg("```json\n" + json.dumps(self.plan) + "\n```")
        if "statistician" in system:
            return Msg(json.dumps({"cautions": ["Segment may be confounded with company size."], "followups": [{"tool": "hack_the_planet", "params": {}}]}))
        return Msg(json.dumps({"summary": self.summary, "recommendations": ["Prioritise Enterprise accounts.", "Pilot a pricing change in SMB."]}))


@pytest.fixture
def data(tmp_path):
    rng = np.random.default_rng(1)
    n = 400
    seg = rng.choice(["Enterprise", "SMB", "Consumer"], n)
    base = {"Enterprise": 400, "SMB": 220, "Consumer": 120}
    df = pd.DataFrame({"Segment": seg, "region": rng.choice(list("NSEW"), n), "revenue": np.array([base[s] for s in seg]) * rng.lognormal(0, 0.4, n)})
    p = tmp_path / "d.csv"
    df.to_csv(p, index=False)
    return p


def go(path, goal="Which segment drives revenue?"):
    rid = runner.create_run(goal, "d.csv", path.read_bytes())
    runner.start_run(rid).join(timeout=120)
    res = json.loads((RUNS_DIR / rid / "result.json").read_text()) if runner.read_status(rid)["status"] == "done" else None
    events = [json.loads(line) for line in (RUNS_DIR / rid / "events.jsonl").read_text().splitlines()]
    return res, events


def test_valid_plan_with_hallucinations(data):
    plan = {"objective": "Find which segment drives revenue", "questions": ["Which segment earns most?"], "target": "revenue", "date_col": None,
            "relevant_columns": ["revenue", "Segment", "ghost_column"],
            "analyses": [{"tool": "group_compare", "params": {"target": "revenue", "group": "Segment"}, "why": "main question"},
                         {"tool": "group_compare", "params": {"target": "revenue", "group": "does_not_exist"}, "why": "hallucinated column"},
                         {"tool": "magic_forecast", "params": {}, "why": "hallucinated tool"},
                         {"tool": "trend", "params": {"target": "revenue", "date": "Segment"}, "why": "wrong column type"}],
            "skipped": [{"column": "region", "reason": "not relevant to the question"}]}
    llm_mod._cache["llm"] = (FakeLLM(plan), "fake:model")
    try:
        res, events = go(data)
    finally:
        llm_mod.reset_cache()
    # invalid steps are no longer dropped silently: the model gets one repair, then the rule-based plan is used
    assert res["plan"]["source"] == "rules"
    assert "ghost_column" not in res["plan"]["relevant_columns"]
    tools_run = [(a["tool"], a["params"].get("group")) for a in res["plan"]["analyses"]]
    assert ("group_compare", "Segment") in tools_run
    assert all(t != "magic_forecast" for t, _ in tools_run) and ("group_compare", "does_not_exist") not in tools_run
    plan_decision = next(d for d in res["decisions"] if d["decision"] == "analysis plan")
    assert plan_decision["source"] == "fallback" and any("does_not_exist" in e for e in plan_decision["errors"])
    assert any("rule-based planning" in e["message"] for e in events)
    # prose from the LLM, numbers from the tools; hallucinated critic follow-up ignored
    assert "FAKE SUMMARY" in res["report_md"] and "Prioritise Enterprise accounts." not in res["report_md"]      # one recommendation list: the strategy-based "What to do", not the free-text recommendations
    assert "confounded with company size" in res["report_md"]
    seg = next(f for f in res["findings"] if f["tool"] == "group_compare")
    assert seg["stats"]["best"] == "Enterprise" and "Enterprise" in seg["text"]


def test_garbage_llm_output_falls_back_to_rules(data):
    llm_mod._cache["llm"] = (FakeLLM({}, garbage=True), "fake:model")
    try:
        res, events = go(data)
    finally:
        llm_mod.reset_cache()
    assert res is not None and res["plan"]["source"] == "rules"
    assert any("unusable" in e["message"] for e in events)
    assert "Recommendations" in res["report_md"]


def test_groq_default_model_matches_one_this_account_can_use():
    """The Groq default must be a model that exists for us (llama-3.3-70b-versatile returned model_not_found); it must also agree with the documented .env LLM_MODEL."""
    assert llm_mod.DEFAULTS["groq"] == "openai/gpt-oss-120b"
    assert "llama-3.3-70b-versatile" not in llm_mod.DEFAULTS.values()
