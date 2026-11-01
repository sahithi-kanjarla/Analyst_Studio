"""Task E: ties between strategies are broken by the size of the checked effect, never by name; display names, never raw column names, in strategy and Lens text. Fake data, no AI."""
import os

os.environ["LLM_PROVIDER"] = "none"

import re

import pandas as pd
import pytest

from app import strategy
from app.lens import chat
from tests.test_lens import FakeAI, make_run
from tests.test_lens_stage2a import REPORT, UND, shop_df
from tests.test_polarity_effects_seasonality import _group, _state

RAW = re.compile(r"\b\w+_\w+\b")


def _cand(title, evidence):
    return strategy._cand(id="", title=title, theme="t", objective="o", rationale="r", actions=["a"], effort="Low", impact="High", confidence="medium",
                          evidence=evidence, how_to_test="h", kpis=["k"], risks=["r"])


def test_tied_strategies_rank_by_effect_size_then_stable_order_never_by_name():
    findings = [{"id": "f1", "stats": {"effect": 0.08, "effect_name": "eta_squared"}}, {"id": "f2", "stats": {"effect": 0.31, "effect_name": "eta_squared"}}]
    # "Alpha" sorts first by name but cites the smaller effect: the larger checked effect must come first
    out = strategy.prioritise([_cand("Alpha lever", ["f1"]), _cand("Zulu lever", ["f2"])], findings)
    assert [c["title"] for c in out] == ["Zulu lever", "Alpha lever"]
    assert out[0]["priority_score"] == out[1]["priority_score"] and out[0]["effect_size"] > out[1]["effect_size"]
    # exact tie on effect: the order the candidates were found in, not the name
    same = [{"id": "f1", "stats": {"effect": 0.2, "effect_name": "eta_squared"}}, {"id": "f2", "stats": {"effect": 0.2, "effect_name": "eta_squared"}}]
    out = strategy.prioritise([_cand("Zulu", ["f1"]), _cand("Alpha", ["f2"])], same)
    assert [c["title"] for c in out] == ["Zulu", "Alpha"] and [c["id"] for c in out] == ["s1", "s2"]


def test_impact_still_beats_effect_size():
    findings = [{"id": "f1", "stats": {"effect": 0.9, "effect_name": "eta_squared"}}]
    low = _cand("Big effect, low impact", ["f1"])
    low["impact"] = "Low"
    out = strategy.prioritise([low, _cand("High impact", [])], findings)
    assert out[0]["title"] == "High impact"


def test_strategy_text_uses_display_names_not_raw_column_names():
    f = _group("discount_pct")
    f["text"] = "Average discount_pct is highest in the North"
    f["insight"] = {"confidence": {"level": "high"}}
    st = _state([f])
    st["understanding"] = {"source": "llm", "columns": {"discount_pct": {"role": "measure", "meaning": "discount", "display_name": "Discount rate"},
                                                       "region": {"role": "category", "meaning": "region", "display_name": "Region"}}, "goal_columns": {}}
    st["profile"] = {"columns": [{"name": "discount_pct"}, {"name": "region"}]}
    strategies, summary, _ = strategy.build_strategies(st, None)
    assert strategies
    for s in strategies:
        blob = " ".join([s["title"], s["objective"], s["rationale"], *s["actions"], *s["kpis"], *s["risks"], s["how_to_test"]])
        assert "discount_pct" not in blob, blob
    assert "Discount rate" in " ".join(s["title"] for s in strategies)


def test_lens_strategy_sentences_use_display_names(monkeypatch):
    monkeypatch.setenv("CHATBOT", "on")
    chat._DAILY["until"] = 0.0
    und = {**UND, "columns": {**UND["columns"], "discount_pct": {"role": "measure", "meaning": "discount given", "display_name": "Discount rate", "measure_kind": "rate"}}}
    df = shop_df()
    df["discount_pct"] = 5.0
    rep = {**REPORT, "strategies": [{**REPORT["strategies"][1], "title": "Tighten discount_pct in the North", "kpis": ["average discount_pct"], "actions": ["Cap discount_pct at 10"]}, REPORT["strategies"][0]]}
    rid, wd, result = make_run(df, und, rep)
    t = chat.ask(wd, result, question="what should we do next?", llm=FakeAI({"action": "strategy", "focus": ""}), allow_ai=True)
    a = t["answer"]
    text = " ".join([a["text"], *a.get("lines", [])])
    assert a.get("kind") == "strategy" and "Discount rate" in text and "discount_pct" not in text, text
