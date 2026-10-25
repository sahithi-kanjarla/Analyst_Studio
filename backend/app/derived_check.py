"""Code's check of the derived calculations the brief proposes. The model decides which calculations make sense; nothing here knows a column name or a domain word.

A proposal is {name, op (mul, div, sub, add), left, right, meaning, confidence, reason}. It is kept only when both columns exist and are numeric, neither is an id or a code,
it does not multiply the column the brief says already holds the line total, and (for mul, sub, add) its total is not wildly larger than the largest additive column.
Every proposal gets a verdict line: "proposed by AI, accepted/rejected by code: <reason>"."""
from __future__ import annotations

import pandas as pd

from .measures import measure_kind
from .profiler import id_like

OPS = {"mul": "x", "div": "/", "sub": "-", "add": "+"}
CONFIDENCE = ("high", "medium", "low")
MAX_PROPOSALS = 5
TOTAL_LIMIT = 20      # a derived column's total may be at most this many times the largest existing additive column's total


def parse(raw) -> tuple[list[dict], list[str]]:
    """(well-formed proposals, problems). Only the shape is checked here; the data checks are in judge()."""
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], ["derived_measures must be a list"]
    out, problems = [], []
    for item in raw[:MAX_PROPOSALS]:
        if not isinstance(item, dict) or item.get("op") not in OPS or not isinstance(item.get("left"), str) or not isinstance(item.get("right"), str):
            problems.append("a derived measure needs an op (mul, div, sub or add) and a left and a right column")
            continue
        conf = item.get("confidence") if item.get("confidence") in CONFIDENCE else "low"
        out.append({"name": str(item.get("name") or f"{item['left']}_{item['op']}_{item['right']}")[:60], "op": item["op"], "left": item["left"], "right": item["right"],
                    "meaning": str(item.get("meaning") or "")[:200], "confidence": conf, "reason": str(item.get("reason") or "")[:200]})
    return out, problems


def _derive(op: str, a: pd.Series, b: pd.Series) -> pd.Series:
    if op == "mul":
        return a * b
    if op == "add":
        return a + b
    if op == "sub":
        return a - b
    return a / b.where(b != 0)


def judge(proposals: list[dict], already_total, typed: pd.DataFrame, rule_roles: dict, roles: dict, kinds: dict, kpis: list[str], kpi_map: dict) -> dict:
    """{accepted, rejected, already_total, answered: True}. `typed` has its numbers as numbers; `rule_roles` are the profiler's roles, `roles` and `kinds` the brief's."""
    numeric = {c for c in typed.columns if pd.api.types.is_numeric_dtype(typed[c]) and not pd.api.types.is_bool_dtype(typed[c])}
    is_id = lambda c: id_like(c, typed[c], rule_roles.get(c), roles.get(c), kinds.get(c) or measure_kind(c, None, kinds))
    total_col = already_total if isinstance(already_total, str) and already_total in numeric and not is_id(already_total) else None
    additive = [c for c in numeric if ((kinds.get(c) or measure_kind(c, None, kinds)) == "additive" or c == total_col) and not is_id(c)]
    accepted, rejected = [], []

    def verdict(p: dict, ok: bool, why: str) -> None:
        (accepted if ok else rejected).append({**p, "verdict": f"proposed by AI, {'accepted' if ok else 'rejected'} by code: {why}"})

    for p in proposals:
        a, b = p["left"], p["right"]
        if a not in typed.columns or b not in typed.columns or a == b:
            verdict(p, False, f"'{a}' and '{b}' must be two different columns that exist")
        elif a not in numeric or b not in numeric:
            verdict(p, False, f"'{a if a not in numeric else b}' is not numeric")
        elif is_id(a) or is_id(b):
            verdict(p, False, f"'{a if is_id(a) else b}' is an id or a code, not a quantity")
        elif p["op"] == "mul" and total_col in (a, b):
            verdict(p, False, f"'{total_col}' already holds the line total, so multiplying it again double counts")
        else:
            series = _derive(p["op"], typed[a], typed[b])
            base = {c: float(typed[c].sum()) for c in additive if c not in (a, b)}
            top = max(base, key=lambda c: abs(base[c])) if base else None
            total = float(series.sum())
            exempt = any(k.lower() == p["name"].lower() and kpi_map.get(k) is None for k in kpis)      # the user's KPI map says no column holds it, so this one is the KPI
            if p["op"] != "div" and top and abs(total) > TOTAL_LIMIT * abs(base[top]) and not exempt:
                verdict(p, False, f"its total ({total:,.0f}) is more than {TOTAL_LIMIT}x the largest additive column '{top}' ({base[top]:,.0f})")
            elif p["op"] != "mul" and p["confidence"] == "low":
                verdict(p, False, "low confidence and there is no question to settle it")
            else:
                verdict(p, True, p["reason"] or p["meaning"] or "both columns are numeric measures and the total is plausible")
    return {"accepted": accepted, "rejected": rejected, "already_total": total_col, "answered": True}
