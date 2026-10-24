"""LLM-first decisions with a rules safety net.

`propose` asks the model for structured JSON, checks it with the caller's validator, sends the errors back once for one repair, and otherwise uses the
caller's rule-based fallback. The model never supplies a number: any numeric value in its reply is rejected. Every proposal is logged so the app can show
which decisions the AI made, which needed a repair and which fell back to rules.
"""
from __future__ import annotations

import json
from typing import Callable

from .llm import ask_json

AI_SOURCES = ("llm", "repaired")
RULE_SOURCES = ("rules", "fallback", "rules-only")
AI_ASSISTED, MIXED, RULES_ONLY = "AI-assisted", "mixed", "rules-only mode"


def _numbers(obj) -> int:
    if isinstance(obj, bool):
        return 0
    if isinstance(obj, (int, float)):
        return 1
    if isinstance(obj, dict):
        return sum(_numbers(v) for v in obj.values())
    if isinstance(obj, list):
        return sum(_numbers(v) for v in obj)
    return 0


def _check(obj, validator: Callable) -> tuple[object, list[str]]:
    if obj is None:
        return None, ["the reply was not valid JSON"]
    if _numbers(obj):
        return None, ["the reply contains numbers; numbers are never accepted from the model, answer with names and words only"]
    return validator(obj)


def _why(obj) -> str:
    w = obj.get("why") if isinstance(obj, dict) else None
    return " ".join(str(w).split())[:300] if isinstance(w, str) else ""


def propose(llm, name: str, system: str, view: str, validator: Callable, fallback: Callable, state: dict | None = None) -> dict:
    """{value, source: llm | repaired | fallback | rules-only, why, errors}. `validator(obj) -> (clean, errors)`; `fallback() -> value`."""
    if llm is None:
        out = {"value": fallback(), "source": "rules-only", "why": "No LLM is configured, so the rule was used.", "errors": []}
    else:
        obj = ask_json(llm, system, view)
        clean, errors = _check(obj, validator)
        if not errors:
            out = {"value": clean, "source": "llm", "why": _why(obj), "errors": []}
        else:
            again = view + "\n\nYour previous answer was rejected:\n" + (json.dumps(obj, default=str)[:2000] if obj is not None else "(not valid JSON)") \
                + "\nProblems:\n" + "\n".join(f"- {e}" for e in errors) + "\nAnswer again with corrected JSON. Fix only these problems and use no numbers."
            obj2 = ask_json(llm, system, again)
            clean2, errors2 = _check(obj2, validator)
            if not errors2:
                out = {"value": clean2, "source": "repaired", "why": _why(obj2), "errors": errors}
            else:
                out = {"value": fallback(), "source": "fallback", "why": "The model's answer was not usable after one repair, so the rule was used.", "errors": errors2}
    if state is not None:
        state.setdefault("decisions", []).append({"decision": name, "source": out["source"], "chosen": out["value"], "why": out["why"], "errors": out["errors"]})
    return out


def derive_mode(stage_sources: dict | None) -> str:
    """One label from the per-stage sources: AI-assisted (every stage used the model), rules-only mode (none did) or mixed."""
    srcs = [s for s in (stage_sources or {}).values() if s]
    ai = sum(s in AI_SOURCES or s == "mixed" for s in srcs)
    if not ai:
        return RULES_ONLY
    return AI_ASSISTED if ai == len(srcs) and "mixed" not in srcs else MIXED
