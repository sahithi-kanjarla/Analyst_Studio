"""Metric direction ("polarity"): is a higher value of a measure good, bad, or neither?

The LLM is asked once per run about the measures actually used, and its answer is validated (known names only, known values only; id-like columns are
always neutral). Anything missing or invalid falls back to a name-based heuristic, so a run without an LLM still gets a direction for every measure.
Wording elsewhere (insights, strategies, the goal answer) reads the result through `direction()`: it never calls a rise in a lower-is-better measure
favourable and never recommends increasing it.
"""
from __future__ import annotations

import re

from . import context as ctxmod
from .propose import propose

HIGHER, LOWER, NEUTRAL = "higher", "lower", "neutral"
VALUES = (HIGHER, LOWER, NEUTRAL)

LOWER_STEMS = ("churn", "default", "fraud", "cancel", "return", "refund", "complaint", "defect", "attrition", "turnover", "loss", "late", "delay", "cost", "expense",
               "error", "incident", "downtime", "absen", "dropout", "readmission", "mortality", "scrap", "discount", "waste", "penalt", "overdue", "backlog", "debt", "risk", "emission")
LOWER_PHRASE = re.compile(r"\b(delivery|lead|response|resolution|processing|handling|shipping|wait\w*|cycle)\s+(time|days?|duration)\b|\bdays\s+to\b|\bdowntime\b", re.I)
NEUTRAL_TOKENS = {"id", "ids", "code", "num", "number", "no", "index", "rank", "zip", "postal", "phone", "latitude", "longitude", "lat", "lon", "lng", "year", "month", "day", "row",
                  "quantity", "qty", "units", "unit", "count", "age", "sequence", "seq"}
ID_TOKENS = {"id", "ids", "code", "zip", "postal", "phone", "index", "row", "latitude", "longitude", "lat", "lon", "lng"}
SYNONYMS = {"higher": HIGHER, "higher_is_better": HIGHER, "high": HIGHER, "up": HIGHER, "increase": HIGHER, "good": HIGHER, "maximize": HIGHER, "maximise": HIGHER,
            "lower": LOWER, "lower_is_better": LOWER, "low": LOWER, "down": LOWER, "decrease": LOWER, "bad": LOWER, "minimize": LOWER, "minimise": LOWER,
            "neutral": NEUTRAL, "none": NEUTRAL, "n/a": NEUTRAL, "either": NEUTRAL, "neither": NEUTRAL, "unknown": NEUTRAL}


def _tokens(name: str) -> list[str]:
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(name))
    return [t for t in re.split(r"[^A-Za-z0-9]+", s.lower()) if t]


def heuristic(name: str) -> str:
    toks = _tokens(name)
    if any(len(st) >= 4 and t.startswith(st) or t == st for t in toks for st in LOWER_STEMS) or LOWER_PHRASE.search(" ".join(toks)):
        return LOWER
    if any(t in NEUTRAL_TOKENS for t in toks):
        return NEUTRAL
    return HIGHER


def _forced_neutral(name: str) -> bool:
    return any(t in ID_TOKENS for t in _tokens(name))


def validate(raw, names: list[str]) -> dict[str, str]:
    """Keep only well-formed answers for the names asked about; fill the rest from the heuristic."""
    out: dict[str, str] = {}
    answers = raw.get("measures") if isinstance(raw, dict) and isinstance(raw.get("measures"), (dict, list)) else raw
    if isinstance(answers, list):
        answers = {a.get("name"): a.get("direction") for a in answers if isinstance(a, dict)}
    if not isinstance(answers, dict):
        answers = {}
    lower_keys = {str(k).strip().lower(): v for k, v in answers.items()}
    for n in names:
        v = lower_keys.get(str(n).strip().lower())
        if isinstance(v, dict):
            v = v.get("direction")
        v = SYNONYMS.get(str(v).strip().lower().replace(" ", "_").replace("-", "_")) if v is not None else None
        if _forced_neutral(n):
            v = NEUTRAL
        out[n] = v if v in VALUES else heuristic(n)
    return out


def _check(raw, names: list[str]) -> tuple[dict[str, str], list[str]]:
    """validate() plus the problems a repair call can fix: a measure left out, or a direction that is not higher/lower/neutral."""
    answers = raw.get("measures") if isinstance(raw, dict) and isinstance(raw.get("measures"), (dict, list)) else raw
    if isinstance(answers, list):
        answers = {a.get("name"): a.get("direction") for a in answers if isinstance(a, dict)}
    if not isinstance(answers, dict):
        return {}, ['expected {"measures": {"<name>": "higher" | "lower" | "neutral"}}']
    given = {str(k).strip().lower(): v for k, v in answers.items()}
    errors = []
    for n in names:
        if _forced_neutral(n):
            continue
        v = given.get(str(n).strip().lower())
        if isinstance(v, dict):
            v = v.get("direction")
        if v is None:
            errors.append(f"no direction given for '{n}'")
        elif SYNONYMS.get(str(v).strip().lower().replace(" ", "_").replace("-", "_")) not in VALUES:
            errors.append(f"'{v}' is not a valid direction for '{n}' (use higher, lower or neutral)")
    return validate(raw, names), errors


SYSTEM = """You are a business analyst. For each numeric measure name, say whether a HIGHER value is better for the business, a LOWER value is better, or the direction is neutral.
higher: revenue, sales, profit, margin, satisfaction, retention. lower: discount, cost, expense, delivery time, returns, churn, defects, delays, errors.
neutral: identifiers, codes, quantities or counts of items, ages, years, anything where neither direction is clearly good.
Use the company context when it changes the answer. Return JSON: {"measures": {"<name>": "higher" | "lower" | "neutral"}} with exactly the names given, plus an optional "why": one short sentence on the reasoning."""


def resolve(llm, names: list[str], goal: str = "", context: dict | None = None, state: dict | None = None) -> tuple[dict[str, str], str]:
    """({measure: higher|lower|neutral}, source) - one LLM call for all measures (one more if it needs a repair); source is 'llm' or 'rules'.
    With `state`, the decision is logged in state['decisions']."""
    names = list(dict.fromkeys(n for n in names if n))
    if not names:
        return {}, "rules"
    res = propose(llm, "metric direction", SYSTEM, f"GOAL: {goal}\nCOMPANY CONTEXT: {ctxmod.context_text(context) or 'none'}\nMEASURES: {names}",
                  lambda raw: _check(raw, names), lambda: {n: (NEUTRAL if _forced_neutral(n) else heuristic(n)) for n in names}, state)
    return res["value"], "llm" if res["source"] in ("llm", "repaired") else "rules"


def measures_used(state: dict) -> list[str]:
    names: list[str] = []
    plan = state.get("plan") or {}
    if plan.get("target"):
        names.append(plan["target"])
    for f in state.get("findings") or []:
        p = f.get("params", {})
        for k in ("target", "col", "x", "y"):
            v = p.get(k)
            if isinstance(v, str):
                names.append(v)
    return list(dict.fromkeys(names))


def direction(name: str, pol: dict | None = None) -> str:
    """higher | lower | neutral for a measure, using the run's resolved map when it has the name, else the heuristic."""
    if pol and name in pol and pol[name] in VALUES:
        return pol[name]
    return NEUTRAL if _forced_neutral(name) else heuristic(name)
