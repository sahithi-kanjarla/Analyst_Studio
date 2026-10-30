"""Run safety and honesty: a stage that throws never ends the run, an AI failure says exactly why, prompts are capped, and the plan source is the true one.
All with fake chat models (no network, no key)."""
import json
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np
import pandas as pd
import pytest

from app import insights, llm as llm_mod, planner, reporter, runner, summary
from app.config import RUNS_DIR
from app.profiler import profile_df
from app.propose import AI_ASSISTED, MIXED, RULES_ONLY, derive_mode


class Msg:
    def __init__(self, content):
        self.content = content


class FakeLLM:
    """Answers nothing useful (valid empty JSON) except where `raises` matches the system prompt: that call raises `error`."""
    def __init__(self, raises=None, error=RuntimeError("boom"), everywhere=False):
        self.raises, self.error, self.everywhere = raises, error, everywhere

    def invoke(self, messages):
        system = messages[0][1]
        if self.everywhere or (self.raises and self.raises in system):
            raise self.error
        return Msg("{}")


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(llm_mod.time, "sleep", lambda s: None)       # the gentle retry between attempts
    yield
    llm_mod.reset_cache()


@pytest.fixture
def data(tmp_path):
    rng = np.random.default_rng(1)
    n = 300
    seg = rng.choice(["Enterprise", "SMB", "Consumer"], n)
    base = {"Enterprise": 400, "SMB": 220, "Consumer": 120}
    df = pd.DataFrame({"Segment": seg, "region": rng.choice(list("NSEW"), n), "revenue": np.array([base[s] for s in seg]) * rng.lognormal(0, 0.4, n)})
    p = tmp_path / "d.csv"
    df.to_csv(p, index=False)
    return p


def go(path, goal="Which segment drives revenue?"):
    rid = runner.create_run(goal, "d.csv", path.read_bytes())
    runner.start_run(rid).join(timeout=300)
    wd = RUNS_DIR / rid
    status = runner.read_status(rid)
    res = json.loads((wd / "result.json").read_text()) if (wd / "result.json").exists() else None
    return status, res


# ---------------------------------------------------------------- error isolation
def test_llm_that_raises_in_the_insights_stage_still_completes_with_a_reason(data):
    llm_mod._cache["llm"] = (FakeLLM(raises="business analyst explaining analysis results"), "fake:model")
    status, res = go(data)
    assert status["status"] == "done" and res is not None
    assert "AI unavailable for insights: provider error" in res["ai_notes"] and not any("boom" in n for n in res["ai_notes"])      # a short phrase, never the raw error text
    assert any(d["decision"] == "AI unavailable: insights" and d["why"] == "provider error" for d in res["decisions"])
    assert "AI unavailable for insights:" in res["report_md"].split("## Executive summary")[0]
    assert res["findings"] and all("insight" in f for f in res["findings"])        # the rules version of the insights was used


def test_insights_stage_that_crashes_is_replaced_by_its_rules_version(data, monkeypatch):
    real = insights.enrich_with_llm
    calls = {"n": 0}

    def crash(llm, state):
        calls["n"] += 1
        if llm is not None:
            raise ValueError("enrich blew up")
        return real(llm, state)

    llm_mod._cache["llm"] = (FakeLLM(), "fake:model")
    monkeypatch.setattr(insights, "enrich_with_llm", crash)
    status, res = go(data)
    assert status["status"] == "done" and res is not None and (RUNS_DIR / res["run_id"] / "result.json").exists()
    assert any(w.startswith("stage failed: insights") and "enrich blew up" in w for w in res["warnings"])
    assert status["warnings"] == res["warnings"]
    assert "stage failed: insights" in res["report_md"].split("## Executive summary")[0]
    assert any(d["decision"] == "stage failed: insights" and "enrich blew up" in d["why"] for d in res["decisions"])
    assert res["stage_sources"]["insights"] == "fallback" and all("insight" in f for f in res["findings"])


def test_stage_whose_rules_version_fails_too_is_skipped_and_the_result_is_kept(data, monkeypatch):
    from app import powerbi, strategy

    def boom(*a, **k):
        raise RuntimeError("no pack today")

    monkeypatch.setattr(powerbi, "build_pack", boom)
    monkeypatch.setattr(strategy, "build_strategies", boom)
    status, res = go(data)
    assert status["status"] == "done" and res is not None
    warns = " | ".join(res["warnings"])
    assert "stage failed: powerbi" in warns and "stage failed: strategy" in warns
    assert res["strategies"] == [] and res["powerbi"] == {} and res["findings"] and res["report_md"]
    assert "stage failed: powerbi" in res["report_md"] and "stage failed: strategy" in res["report_md"]       # the powerbi warning is added after the report was written


# ---------------------------------------------------------------- the reason an AI stage fell back
@pytest.mark.parametrize("error,word", [(RuntimeError("429 Too Many Requests: rate limit exceeded"), "rate limit"),
                                        (RuntimeError("401 Unauthorized: invalid api key"), "auth"),
                                        (TimeoutError("request timed out"), "timeout")])
def test_provider_errors_are_named_in_the_log_and_the_report_header(data, error, word):
    llm_mod._cache["llm"] = (FakeLLM(error=error, everywhere=True), "fake:model")
    status, res = go(data)
    assert status["status"] == "done" and res is not None
    header = res["report_md"].split("## Executive summary")[0]
    assert f"AI unavailable for the analysis plan: {word}" in header and f"AI unavailable for insights: {word}" in header
    logged = [d for d in res["decisions"] if d["decision"].startswith("AI unavailable:")]
    assert logged and all(d["why"].startswith(word) for d in logged)
    assert any(word in str(d["errors"]) for d in res["decisions"] if d["decision"] == "analysis plan")
    assert res["run_mode"] == RULES_ONLY


def test_bad_json_and_validation_reasons():
    assert llm_mod.classify_error(RuntimeError("Error code: 429 - rate_limit_exceeded")).startswith("rate limit")
    assert llm_mod.classify_error(Exception("API key not valid")).startswith("auth")
    assert llm_mod.classify_error(Exception("Read timed out")).startswith("timeout")

    class Prose:
        def invoke(self, messages):
            return Msg("no json here")

    obj, reason = llm_mod.ask_json_ex(Prose(), "s", "u")
    assert obj is None and reason.startswith("bad JSON")


def test_reasons_belong_to_the_call_not_to_a_shared_global():
    import threading
    seen = {}

    def worker(name, error):
        with llm_mod.capture() as cap:
            llm_mod.ask_json(FakeLLM(error=error, everywhere=True), "s", "u")
        seen[name] = cap.reason

    ts = [threading.Thread(target=worker, args=("a", RuntimeError("429 rate limit"))), threading.Thread(target=worker, args=("b", TimeoutError("timed out")))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert seen["a"].startswith("rate limit") and seen["b"].startswith("timeout")


# ---------------------------------------------------------------- prompt caps
def test_summary_prompt_sends_at_most_12_facts_of_10_groups_and_logs_the_cut():
    groups = [{"group": f"g{i}", "line": f"g{i} +{i}%"} for i in range(25)]
    facts = [{"id": f"r{i}", "kind": "growth", "text": "full", "text_head": "Growth: ", "text_tail": ".", "groups": groups, "focus": i % 2 == 0} for i in range(20)]
    state = {"decisions": []}
    kept = reporter._trim_rankings(facts, state)
    assert len(kept) == 12 and all(k["focus"] for k in kept[:10])
    assert all(k["text"].count("+") <= 10 for k in kept) and "15 groups in between left out" in kept[0]["text"] and "g24" in kept[0]["text"] and "g12" not in kept[0]["text"]
    assert state["decisions"][0]["decision"] == "report_summary prompt trimmed" and "8 facts not sent" in state["decisions"][0]["errors"][0]
    small = [{"id": "r1", "text": "t", "groups": groups[:3]}]
    assert reporter._trim_rankings(small, {"decisions": []}) == small


def test_pick_prompt_sends_at_most_40_items_of_160_characters():
    items = [{"id": f"i{k}", "text": "x" * 300, "column": "seg", "measure": "revenue", "tool": "ranking"} for k in range(70)]
    seen = {}

    class Spy:
        def invoke(self, messages):
            seen["user"] = messages[1][1]
            return Msg(json.dumps({"answers": []}))

    state = {"decisions": []}
    summary.llm_pick(Spy(), ["What drives revenue?"], items, state)
    sent = json.loads(seen["user"].split("RESULTS: ", 1)[1])
    assert len(sent) == 40 and all(len(s["text"]) <= 160 for s in sent)
    assert any(d["decision"] == "summary_answers prompt trimmed" and "40 of 70" in d["chosen"] for d in state["decisions"])


# ---------------------------------------------------------------- plan source and run label
def test_override_that_replaces_the_ai_plan_makes_the_plan_rules_and_names_the_true_source():
    rng = np.random.default_rng(3)
    df = pd.DataFrame({"seg": rng.choice(list("abc"), 200), "revenue": rng.lognormal(5, 0.5, 200), "cost": rng.lognormal(4, 0.5, 200)})
    prof = profile_df(df)
    ai_plan = {"objective": "o", "questions": ["q"], "target": "cost", "date_col": None, "relevant_columns": ["cost", "seg"],
               "analyses": [{"tool": "group_compare", "params": {"target": "cost", "group": "seg"}, "why": "w"},
                            {"tool": "distribution", "params": {"col": "cost"}, "why": "w"}], "skipped": []}

    class Planner:
        def invoke(self, messages):
            return Msg(json.dumps(ai_plan))

    for source in ("the analyst brief", "a derived measure you defined"):
        state = {"decisions": []}
        plan, notes = planner.propose_plan(Planner(), df, prof, "revenue by seg", None, False, {"target": "revenue", "target_source": source}, state)
        assert plan["target"] == "revenue" and plan["source"] == "rules"
        assert any(source in n and "you chose" not in n for n in notes)
        assert state["decisions"][-1]["source"] == "rules"
    state = {"decisions": []}
    plan, _ = planner.propose_plan(Planner(), df, prof, "cost by seg", None, False, {}, state)
    assert plan["target"] == "cost" and plan["source"] == "llm"


def test_run_label_counts_clarifier_critic_and_summary_pick():
    ai = {"brief": "llm", "clarifier": "llm", "planner": "llm", "polarity": "llm", "critic": "llm", "summary_pick": "llm", "insights": "llm", "strategy": "llm", "report": "llm"}
    assert derive_mode(ai) == AI_ASSISTED
    for stage in ("clarifier", "critic", "summary_pick"):
        assert derive_mode({**ai, stage: "fallback"}) == MIXED
    assert derive_mode({k: "rules" for k in ai}) == RULES_ONLY
