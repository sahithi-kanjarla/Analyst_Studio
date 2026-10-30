"""Answer-card trust follows the weakest figure; the definitions table says whether an AI-asked default was set by the AI's question; the two AI calls that describe the data
(understanding record, concerns) run at temperature 0 and are cached on disk by a hash of everything that can change the answer. Fake AI output and synthetic data."""
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from app import aicache, clarify, concerns, mainview, reporter, understanding as und  # noqa: E402
from app.cleaner import coerce_types  # noqa: E402
from app.profiler import profile_df  # noqa: E402

GOAL = "How do sales differ by group?"


@pytest.fixture(autouse=True)
def cache_on(monkeypatch):
    monkeypatch.setenv("AI_CACHE", "on")


# ------------------------------------------------------------------ 1. trust follows the weakest figure
def ranking(short, kind="growth"):
    return {"id": "r1", "tool": "ranking", "kind": kind, "basis": "total", "group": "g", "measure": "m", "short": short, "text": short}


def fact(level):
    return {"id": "f1", "insight": {"confidence": {"level": level}}}


def test_a_growth_figure_marked_not_reliable_cannot_give_a_high_card():
    assert mainview.trust_of(["r1"], {}, {"r1": ranking("a +5% (1 to 2), statistically reliable")}) == "High"
    assert mainview.trust_of(["r1"], {}, {"r1": ranking("a +5% (1 to 2), not statistically reliable")}) == "Medium"


def test_a_descriptive_figure_cannot_give_a_high_card():
    assert mainview.trust_of(["f1"], {"f1": fact("descriptive")}, {}) == "Medium"
    assert mainview.trust_of(["f1"], {"f1": fact("high")}, {}) == "High"
    assert mainview.trust_of(["f1", "r1"], {"f1": fact("high")}, {"r1": ranking("x, not statistically reliable")}) == "Medium"


def test_the_shown_lines_can_lower_trust_but_never_raise_it():
    assert mainview.cap_by_text("High", ["g1 +3%, Not Statistically Reliable"]) == "Medium"
    assert mainview.cap_by_text("High", ["g1 +3%, statistically reliable"]) == "High"
    assert mainview.cap_by_text("Low", ["g1 +3%, not statistically reliable"]) == "Low"


# ------------------------------------------------------------------ 2. "Set by"
def question(qid, **kw):
    return {"id": qid, "text": "t", "default": "keep", "options": [{"value": "keep", "label": "Keep", "action": {"type": "note_only"}}], **kw}


def test_a_default_for_an_ai_asked_question_says_ai_suggested_and_a_rules_one_says_rules():
    qs = [question("a", asked_by="ai"), question("b")]
    rows = clarify.summarise(qs, {}, {"a": "default", "b": "default"}, {})
    assert [reporter.source_label(r) for r in rows] == ["Default (AI-suggested)", "Default (rules)"]
    md = "\n".join(reporter._definitions_section({"applied_definitions": rows}))
    assert "| Default (AI-suggested) |" in md and "| Default (rules) |" in md


def test_an_answer_that_is_not_a_default_keeps_its_label_even_for_an_ai_question():
    rows = clarify.summarise([question("a", asked_by="ai")], {"a": "keep"}, {"a": "user"}, {})
    assert reporter.source_label(rows[0]) == "You"


# ------------------------------------------------------------------ 3 and 4. temperature 0 and the cache
class Model:
    """A chat model that can be re-bound; the bound copy remembers the setting and the model name."""
    def __init__(self, model="m1", bound=None):
        self.model, self.settings, self.bound = model, bound or {}, None

    def bind(self, **kw):
        b = Model(self.model, {**self.settings, **kw})
        b.bound = self
        return b


def sheet_df(n=400, seed=3):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"group": rng.choice(["a", "b", "c"], n), "amount": rng.integers(100, 900, n).astype(float)})


def prep(df):
    prof = profile_df(df)
    typed, _ = coerce_types(df, prof)
    return typed, profile_df(typed)


class Concerns:
    """A fake concerns call: counts calls, remembers the model it got, can fail on demand."""
    def __init__(self, monkeypatch, reply=None):
        self.calls, self.llms, self.reply = 0, [], {"concerns": []} if reply is None else reply
        monkeypatch.setattr(concerns, "ask_json", self.ask)

    def ask(self, llm, system, user):
        self.calls += 1
        self.llms.append(llm)
        return self.reply


def run_concerns(df=None, goal=GOAL, description="", saved=None, llm=None):
    typed, prof = prep(df if df is not None else sheet_df())
    return concerns.run(llm or Model(), typed, prof, goal, description, saved)


def test_the_concerns_call_runs_at_temperature_zero(monkeypatch):
    ai = Concerns(monkeypatch)
    run_concerns()
    assert ai.llms[0].settings == {"temperature": 0}


def test_same_inputs_reuse_the_concerns_result_and_make_no_call(monkeypatch):
    ai = Concerns(monkeypatch)
    first = run_concerns()
    second = run_concerns()
    assert ai.calls == 1 and first["cached"] is False and second["cached"] is True and second["worked"]


def test_a_reused_concerns_result_is_still_verified_against_the_data(monkeypatch):
    bad = {"column": "nope", "concern_type": "other", "values": [], "proposed_action": None, "confidence": "low", "reason": "r"}
    Concerns(monkeypatch, {"concerns": [bad]})
    run_concerns()
    again = run_concerns()
    assert again["cached"] and again["accepted"] == [] and len(again["rejected"]) == 1


@pytest.mark.parametrize("change", ["data", "goal", "description", "glossary", "model", "prompt"])
def test_each_input_changing_is_a_concerns_cache_miss(monkeypatch, change):
    ai = Concerns(monkeypatch)
    run_concerns(saved={"answers": {"q": {"value": "x"}}})
    kw = {"saved": {"answers": {"q": {"value": "x"}}}}
    if change == "data":
        kw["df"] = sheet_df(seed=4)
    elif change == "goal":
        kw["goal"] = "Which group is biggest?"
    elif change == "description":
        kw["description"] = "A shop that sells things"
    elif change == "glossary":
        kw["saved"] = {"answers": {"q": {"value": "y"}}}
    elif change == "model":
        kw["llm"] = Model("m2")
    else:
        monkeypatch.setattr(concerns, "SYSTEM", concerns.SYSTEM + "\nA new rule.")
    out = run_concerns(**kw)
    assert ai.calls == 2 and out["cached"] is False


def test_a_failed_concerns_call_is_not_cached(monkeypatch):
    ai = Concerns(monkeypatch, None)
    ai.reply = None                                 # the call failed: no usable reply
    assert run_concerns()["worked"] is False
    ai.reply = {"concerns": []}
    ok = run_concerns()
    assert ai.calls == 2 and ok["worked"] and ok["cached"] is False
    assert run_concerns()["cached"] is True and ai.calls == 2


def test_an_unusable_concerns_reply_is_not_cached(monkeypatch):
    ai = Concerns(monkeypatch, {"something": "else"})
    assert run_concerns()["worked"] is False
    assert run_concerns()["worked"] is False and ai.calls == 2


def test_entries_expire_after_cache_days(monkeypatch):
    ai = Concerns(monkeypatch)
    run_concerns()
    now = aicache.time.time()
    monkeypatch.setattr(aicache.time, "time", lambda: now + 6 * 86400)
    assert run_concerns()["cached"] is True
    monkeypatch.setattr(aicache.time, "time", lambda: now + 8 * 86400)
    assert run_concerns()["cached"] is False and ai.calls == 2
    monkeypatch.setenv("CACHE_DAYS", "30")
    monkeypatch.setattr(aicache.time, "time", lambda: now + 20 * 86400)
    assert run_concerns()["cached"] is True


def test_the_clarify_stage_reports_a_reused_concerns_result(monkeypatch):
    Concerns(monkeypatch)
    monkeypatch.setattr(clarify, "ask_json", lambda *a, **k: None)
    typed, prof = prep(sheet_df())
    seen = []
    for _ in range(2):
        out = {}
        clarify.build_questions_and_ignored(typed, prof, GOAL, {}, False, Model(), None, None, [], concerns_out=out)
        seen.append(out["cached"])
    assert seen == [False, True]


# ---- the data understanding record
class Understanding:
    def __init__(self, monkeypatch, df):
        self.calls, self.llms, self.df = 0, [], df
        self.reply = {"columns": [{"column": c, "meaning": f"what {c} holds", "role": "measure" if c == "amount" else "category", "goal_terms": []} for c in df.columns],
                      "goal_columns": {"sales": ["amount"]}}
        monkeypatch.setattr(und, "ask_json_ex", self.ask)

    def ask(self, llm, system, user):
        self.calls += 1
        self.llms.append(llm)
        return (self.reply, "") if isinstance(self.reply, dict) else (None, "provider error")


def resolve(df=None, goal=GOAL, description="A shop", saved=None, llm=None, state=None):
    df = df if df is not None else sheet_df()
    return und.resolve(llm or Model(), df, profile_df(df), goal, {"description": description}, state, saved)


def test_the_understanding_call_runs_at_temperature_zero(monkeypatch):
    ai = Understanding(monkeypatch, sheet_df())
    assert resolve()["source"] == "llm"
    assert ai.llms[0].settings == {"temperature": 0}


def test_same_inputs_reuse_the_understanding_record_and_say_so(monkeypatch):
    ai = Understanding(monkeypatch, sheet_df())
    first, st = resolve(), {"decisions": []}
    second = resolve(state=st)
    assert ai.calls == 1 and not first.get("cached") and second["cached"] is True and second["source"] == "llm" and second["columns"] == first["columns"]
    assert st["decisions"][0]["chosen"] == aicache.REUSED


@pytest.mark.parametrize("change", ["data", "goal", "description", "glossary", "model", "prompt"])
def test_each_input_changing_is_an_understanding_cache_miss(monkeypatch, change):
    ai = Understanding(monkeypatch, sheet_df())
    resolve(saved={"answers": {"q": {"value": "x"}}})
    kw = {"saved": {"answers": {"q": {"value": "x"}}}}
    if change == "data":
        kw["df"] = sheet_df(seed=9)
        ai.df = kw["df"]
    elif change == "goal":
        kw["goal"] = "Which group is biggest?"
    elif change == "description":
        kw["description"] = "Something else"
    elif change == "glossary":
        kw["saved"] = {"answers": {"q": {"value": "y"}}}
    elif change == "model":
        kw["llm"] = Model("m2")
    else:
        monkeypatch.setattr(und, "SYSTEM", und.SYSTEM + "\nA new rule.")
    out = resolve(**kw)
    assert ai.calls == 2 and not out.get("cached")


def test_failed_and_fallback_understanding_results_are_not_cached(monkeypatch):
    ai = Understanding(monkeypatch, sheet_df())
    good = ai.reply
    ai.reply = "provider error"
    assert resolve()["source"] == "fallback"
    ai.reply = {"columns": [], "goal_columns": {}}               # fails the structure test, every retry
    assert resolve()["source"] == "fallback"
    calls = ai.calls
    ai.reply = good
    ok = resolve()
    assert ok["source"] == "llm" and not ok.get("cached") and ai.calls > calls
    assert resolve()["cached"] is True
