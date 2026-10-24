"""A ratio KPI the user names (profit margin) always becomes a derived ratio when its two columns exist and are numeric: from the brief, else from one retry
of the brief, else from the rules. Fake model that omits the ratio on its first answer; no network."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from app import brief, derived, llm as llm_mod
from app.context import normalise_context
from app.profiler import profile_df

GOAL = "Which segment makes money?"
KPIS = ["revenue", "profit margin"]
COLUMNS = {"amount": {"role": "measure", "kind": "additive"}, "profit": {"role": "measure", "kind": "additive"}, "region": {"role": "dimension"}}


class Msg:
    def __init__(self, content):
        self.content = content


def data(n=300, seed=4):
    rng = np.random.default_rng(seed)
    amount = rng.uniform(50, 500, n).round(2)
    return pd.DataFrame({"amount": amount, "profit": (amount * rng.normal(0.2, 0.05, n)).round(2), "region": rng.choice(["N", "S", "E"], n)})


class Brief:
    """Answers the brief. The first answer leaves out the ratio; `retry_gives` says what a later answer contains (None: it omits the ratio again; "bad": not JSON)."""

    def __init__(self, retry_gives=None):
        self.retry_gives, self.calls = retry_gives, []

    def invoke(self, messages):
        user = messages[1][1]
        self.calls.append(user)
        if "These KPIs are ratios" in user:
            if self.retry_gives == "bad":
                return Msg("not json")
            ratios = {"profit margin": {"numerator": "profit", "denominator": "amount"}} if self.retry_gives == "ratio" else {}
            return Msg(json.dumps({"columns": COLUMNS, "kpis": {"revenue": "amount", "profit margin": None}, "ratios": ratios, "why": "retry"}))
        return Msg(json.dumps({"columns": COLUMNS, "kpis": {"revenue": "amount", "profit margin": None}, "why": "first answer, no ratios"}))


def resolve(llm, st=None):
    df = data()
    return brief.resolve(llm, df, profile_df(df), GOAL, normalise_context({"kpis": KPIS}), st), df


def test_a_brief_that_omits_the_ratio_is_retried_once_and_the_ratio_is_created():
    llm = Brief(retry_gives="ratio")
    st = {"decisions": []}
    b, df = resolve(llm, st)
    assert len(llm.calls) == 2
    assert b["ratios"]["profit margin"] == {"numerator": "profit", "denominator": "amount", "path": "llm-retry"}
    created, rejected = derived.kpi_ratios(df, b["ratios"])
    assert [(d["a"], d["b"], d["kpi"], d["path"]) for d in created] == [("profit", "amount", "profit margin", "llm-retry")] and not rejected
    assert any(d["decision"] == "ratio KPI 'profit margin'" and "llm-retry" in d["chosen"] for d in st["decisions"])


def test_a_brief_that_never_gives_the_ratio_falls_back_to_the_rules_mapping():
    for retry in (None, "bad"):
        llm = Brief(retry_gives=retry)
        st = {"decisions": []}
        b, df = resolve(llm, st)
        assert len(llm.calls) >= 2
        assert b["ratios"]["profit margin"] == {"numerator": "profit", "denominator": "amount", "path": "rules"}
        created, _ = derived.kpi_ratios(df, b["ratios"])
        assert [(d["a"], d["b"]) for d in created] == [("profit", "amount")]
        assert any("path: rules" in str(d["chosen"]) for d in st["decisions"])


def test_the_ratio_is_created_when_the_whole_brief_fails_and_when_there_is_no_llm():
    class Dead:
        def invoke(self, messages):
            raise RuntimeError("429 rate limit")
    for llm in (Dead(), None):
        b, df = resolve(llm)
        assert b["ratios"]["profit margin"]["path"] == "rules"
        assert [d["name"] for d in derived.kpi_ratios(df, b["ratios"])[0]] == ["profit_to_amount_ratio"]


def test_a_working_first_answer_is_not_retried():
    class Full(Brief):
        def invoke(self, messages):
            self.calls.append(messages[1][1])
            return Msg(json.dumps({"columns": COLUMNS, "kpis": {"revenue": "amount"}, "ratios": {"profit margin": {"numerator": "profit", "denominator": "amount"}}, "why": "ok"}))
    llm = Full()
    b, _ = resolve(llm)
    assert len(llm.calls) == 1 and b["ratios"]["profit margin"]["path"] == "llm"


def test_no_ratio_when_a_column_is_not_numeric_or_the_name_is_not_a_ratio():
    df = data().assign(profit="n/a")
    created, rejected = derived.kpi_ratios(df, {"profit margin": {"numerator": "profit", "denominator": "amount"}})
    assert not created and "not both numeric" in rejected[0]["reason"]
    assert derived.kpi_numerator_tokens("revenue") == set() and derived.rules_ratio(data(), "revenue", ["amount", "profit"]) is None


def test_temperature_zero_is_requested_when_the_model_supports_it():
    class Bindable:
        def bind(self, **kw):
            return ("bound", kw)
    assert llm_mod.deterministic(Bindable()) == ("bound", {"temperature": 0})
    fake = Brief()
    assert llm_mod.deterministic(fake) is fake and llm_mod.deterministic(None) is None


def test_full_run_creates_the_ratio_although_the_llm_omits_it():
    import time
    from app import runner
    from app.config import RUNS_DIR

    class Everything(Brief):
        def invoke(self, messages):
            if "derived metrics" in messages[0][1]:
                return Msg(json.dumps({"metrics": []}))
            if "analysis" in messages[0][1] and "kpis" in messages[0][1]:
                return super().invoke(messages)
            return Msg("not json")
    df = data(600)
    llm_mod._cache["llm"] = (Everything(), "fake:model")
    try:
        rid = runner.create_run(GOAL, "d.csv", df.to_csv(index=False).encode(), context={"kpis": KPIS})
        runner.start_run(rid).join(timeout=300)
        if runner.read_status(rid)["status"] == "needs_input":
            runner.resume_with_answers(rid, {"answers": {}, "skip": True, "notes": ""})
            for _ in range(600):
                if runner.read_status(rid)["status"] in ("done", "error"):
                    break
                time.sleep(0.5)
    finally:
        llm_mod.reset_cache()
    res = json.loads((RUNS_DIR / rid / "result.json").read_text())
    created = res["derived_metrics"]["created"]
    assert [(d["a"], d["b"], d["kpi"]) for d in created] == [("profit", "amount", "profit margin")]
    assert created[0]["path"] == "rules"
    assert created[0]["name"] in pd.read_pickle(RUNS_DIR / rid / "clean.pkl").columns
    assert not any("profit margin" in g["title"] for g in res.get("data_gaps", []))
