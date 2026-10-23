"""Derived metrics: a ratio of two measures, proposed by the LLM and accepted only if the numbers behave.

Rules alone never invent a ratio. With an LLM, every proposal needs a one-line business meaning and must pass checks that look at values only,
never at what a column is called:

  * the denominator is positive in nearly every row;
  * the ratio is not almost entirely a function of its denominator (|spearman(A/B, B)| <= 0.8);
  * the two columns are on comparable scales (their typical sizes are within about 1.7 orders of magnitude);
  * an id or count-like column is never divided by a continuous magnitude.

"Part of" ratios (A mostly within 0..B, a component over its total) rank first. A ratio is always reported as a ratio of sums
(sum of A over sum of B), never as the mean of row-level ratios."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from . import directives as dv
from .llm import ask_json

MAX_DERIVED = 2
MIN_ROWS = 30
RHO_LIMIT = 0.8
MAX_ORDERS = 1.7            # typical sizes of A and B may differ by at most 10 ** 1.7 (about 50x)
PART_OF_SHARE = 0.8         # a "part of" ratio has A within 0..B in at least this share of rows
MIN_POSITIVE = 0.95
COUNT_MAX_DISTINCT = 50
MEANING_MIN, MEANING_MAX = 8, 200

PROPOSE_SYSTEM = """You design derived metrics for a data-analysis team. Given the measures of a dataset and the user's goal, propose at most 2 ratios
A / B that a business reader would act on, where A is a part or outcome of B (for example a component over its total). Propose none if nothing is clearly useful.
Use only measure names that appear in the list. Each ratio needs "meaning": one plain sentence saying what the ratio tells the business.
Return JSON: {"metrics": [{"a": measure, "b": measure, "meaning": "..."}]}"""


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


def count_like(x: pd.Series) -> bool:
    """Non-negative whole numbers that are either few distinct values (a count, a code) or almost all different (an id)."""
    x = x.dropna()
    if len(x) < MIN_ROWS or (x < 0).any() or not np.allclose(x, np.round(x)):
        return False
    return x.nunique() <= COUNT_MAX_DISTINCT or x.nunique() >= 0.9 * len(x)


def continuous(x: pd.Series) -> bool:
    """A magnitude rather than a count: decimals are common, or many distinct values that are not an id."""
    x = x.dropna()
    if len(x) < MIN_ROWS:
        return False
    frac = float((~np.isclose(x, np.round(x))).mean())
    return frac > 0.05 or (x.nunique() > COUNT_MAX_DISTINCT and x.nunique() < 0.9 * len(x))


def check_ratio(df: pd.DataFrame, a: str, b: str) -> tuple[bool, str, dict]:
    """(accepted, reason, facts). The reason is plain language, for the report and the timeline."""
    if a == b or a not in df.columns or b not in df.columns:
        return False, "needs two different numeric columns", {}
    x, y = _num(df, a), _num(df, b)
    both = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(both) < MIN_ROWS:
        return False, "too few rows have both values", {}
    if (both["y"] > 0).mean() < MIN_POSITIVE:
        return False, f"{b} is not positive in nearly every row, so dividing by it is unstable", {}
    both = both[both["y"] > 0]
    if count_like(both["x"]) and continuous(both["y"]):
        return False, f"{a} looks like a count or id and {b} like a continuous amount; dividing one by the other mostly reproduces the inverse of {b}", {}
    mx, my = float(both["x"].abs().median()), float(both["y"].abs().median())
    if mx > 0 and my > 0 and abs(np.log10(mx / my)) > MAX_ORDERS:
        return False, f"{a} and {b} are not on comparable scales (typical sizes {mx:.3g} and {my:.3g})", {}
    ratio = both["x"] / both["y"]
    rho = float(ratio.corr(both["y"], method="spearman") or 0.0)
    if abs(rho) > RHO_LIMIT:
        return False, f"the ratio is almost entirely a function of {b} (|rank correlation with {b}| = {abs(rho):.2f}), so it adds no information", {"rho": rho}
    within = float(((both["x"] >= 0) & (both["x"] <= both["y"])).mean())
    return True, "ok", {"rho": round(rho, 3), "part_of": within >= PART_OF_SHARE, "within_share": round(within, 3)}


def _clean_meaning(m) -> str | None:
    m = " ".join(str(m or "").split())
    return m if MEANING_MIN <= len(m) <= MEANING_MAX else None


def propose(llm, df: pd.DataFrame, measures: list[str], goal: str, ctx_text: str = "") -> tuple[list[dict], list[dict]]:
    """(accepted, rejected). No LLM, or an unusable answer, gives ([], [])."""
    measures = [m for m in measures if m in df.columns][:8]
    if llm is None or len(measures) < 2:
        return [], []
    stats = {m: {k: round(float(v), 4) for k, v in _num(df, m).describe()[["min", "50%", "max"]].items()} for m in measures}
    out = ask_json(llm, PROPOSE_SYSTEM, (f"BUSINESS CONTEXT (unverified):\n{ctx_text}\n" if ctx_text else "") + f"GOAL: {goal}\nMEASURES: {json.dumps(stats)}")
    items = out.get("metrics") if isinstance(out, dict) else None
    if not isinstance(items, list):
        return [], []
    accepted: list[dict] = []
    rejected: list[dict] = []
    taken = set(df.columns)
    seen: set[tuple] = set()
    for it in items[:6]:
        if not isinstance(it, dict):
            continue
        a, b = it.get("a"), it.get("b")
        if a not in measures or b not in measures or (a, b) in seen:
            continue
        seen.add((a, b))
        meaning = _clean_meaning(it.get("meaning"))
        if meaning is None:
            rejected.append({"a": a, "b": b, "reason": "the proposal came without a one-line business meaning"})
            continue
        ok, why, facts = check_ratio(df, a, b)
        if not ok:
            rejected.append({"a": a, "b": b, "meaning": meaning, "reason": why})
            continue
        accepted.append({"kind": "ratio_of_sums", "a": a, "b": b, "meaning": meaning, "part_of": facts["part_of"], "rho": facts["rho"], "source": "llm"})
    accepted.sort(key=lambda d: (not d["part_of"], abs(d["rho"])))       # "part of" ratios first, then the ones least tied to the denominator
    accepted = accepted[:MAX_DERIVED]
    for d in accepted:
        d["name"] = dv.clean_name(f"{d['a']}_to_{d['b']}_ratio", taken)
        taken.add(d["name"])
    return accepted, rejected


def ratio_of_sums(df: pd.DataFrame, spec: dict, mask=None) -> float | None:
    """sum(A) / sum(B) over the rows where both exist (optionally restricted to `mask`), or None when B sums to zero."""
    x, y = _num(df, spec["a"]), _num(df, spec["b"])
    ok = x.notna() & y.notna() if mask is None else (x.notna() & y.notna() & mask)
    den = float(y[ok].sum())
    return float(x[ok].sum()) / den if den else None


def group_ratio(df: pd.DataFrame, spec: dict, groups: pd.Series) -> pd.Series:
    """Per-group ratio of sums (never a mean of row ratios)."""
    x, y = _num(df, spec["a"]), _num(df, spec["b"])
    d = pd.DataFrame({"g": groups, "x": x, "y": y}).dropna()
    s = d.groupby("g")[["x", "y"]].sum()
    return (s["x"] / s["y"].where(s["y"] != 0)).dropna()
