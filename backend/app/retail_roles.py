"""The swappable retail layer: the one place that knows a quantity-like column times a price-like column makes an amount.

Everything else (clarify, signals, planner) reaches it through generic names only: derive_product_measure, is_count_like, is_rate_like and
product_question. It is not scanned by the domain-word test; swap this module for another domain's roles and the callers need no change.
All detection is from values (whole numbers with few distinct values, positive numbers with many), never from column names."""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import directives as dv
from .measures import measure_kind

MIN_ROWS = 30
MAX_COUNT_DISTINCT = 30
MIN_RATE_DISTINCT = 15
PER_UNIT_RATIO = 1.25        # rate of multi-unit lines over rate of single-unit lines: below this the rate does not grow with the count
PRICE_MAGNITUDE = 0.2        # a rate candidate whose median is under this share of the largest median is a percentage, not a price
LINE_TOTAL_TOLERANCE = 0.25 # ... and within this of the average count it grows with it, so it is a line total


def _round(x, nd=4):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), nd)


def _number_columns(df: pd.DataFrame, cols: dict) -> dict[str, pd.Series]:
    return {n: df[n] for n, c in cols.items() if n in df.columns and c["role"] in ("numeric", "categorical") and pd.api.types.is_numeric_dtype(df[n]) and not pd.api.types.is_bool_dtype(df[n])}


def is_count_like(x: pd.Series) -> bool:
    """A whole number of at least 1 with few distinct values."""
    nn = x.dropna()
    return len(nn) >= MIN_ROWS and nn.min() >= 1 and nn.max() >= 2 and nn.nunique() <= MAX_COUNT_DISTINCT and nn.median() <= 20 and bool((nn % 1 == 0).all()) and x.isna().mean() <= 0.4


def is_quantity(x: pd.Series, brief_role: str | None = None) -> bool:
    """A column judged to be a quantity: the brief calls it a measure, or its values are whole counts and the brief does not call it a dimension. Never a group or segment column."""
    if not pd.api.types.is_numeric_dtype(x) or pd.api.types.is_bool_dtype(x) or brief_role in ("id", "dimension"):
        return False
    return brief_role == "measure" or is_count_like(x)


def demote_quantities(df: pd.DataFrame, roles: dict, brief_roles: dict | None = None) -> dict:
    """roles with every quantity column that was typed categorical or binary read as numeric instead, so no tool offers it as a group."""
    brief_roles = brief_roles or {}
    return {c: ("numeric" if r in ("categorical", "binary") and c in df.columns and is_quantity(df[c], brief_roles.get(c)) else r) for c, r in roles.items()}


def is_rate_like(x: pd.Series) -> bool:
    """Positive, many distinct values, and not a whole-number percentage-sized column."""
    nn = x.dropna()
    if len(nn) < MIN_ROWS or (nn > 0).mean() < 0.99 or nn.nunique() < MIN_RATE_DISTINCT:
        return False
    if nn.max() <= 100 and (nn % 1 == 0).mean() >= 0.95:
        return False
    return not (nn.max() <= 1)    # a fraction (0-1 with decimals) is a share or a discount, never a price


def _prefer_additive(names: list[str], kinds: dict | None) -> list[str]:
    """The brief's kinds decide first; the existing name rules only break a tie between candidates."""
    if len(names) < 2:
        return names
    declared = [n for n in names if (kinds or {}).get(n) == "additive"]
    if declared:
        return declared
    return [n for n in names if measure_kind(n, None, kinds) == "additive"] or names


def _rate_basis(q: pd.Series, p: pd.Series) -> tuple[str, dict]:
    d = pd.DataFrame({"q": q, "p": p}).dropna()
    one, many = d[d["q"] == 1], d[d["q"] >= 2]
    if len(one) < MIN_ROWS or len(many) < MIN_ROWS or one["p"].median() <= 0:
        return "unknown", {}      # too few single-unit or multi-unit lines to compare: nothing to ask and nothing to derive
    ratio = float(many["p"].median() / one["p"].median())
    mean_q = float(many["q"].mean())
    facts = {"ratio": _round(ratio, 3), "mean_count": _round(mean_q, 3)}
    if ratio < PER_UNIT_RATIO:
        return "unit", facts
    return ("total" if abs(ratio - mean_q) / mean_q <= LINE_TOTAL_TOLERANCE else "unclear"), facts


def derive_product_measure(df: pd.DataFrame, cols: dict, kinds: dict | None = None) -> dict | None:
    """{count, rate, basis, others, rows, ...} for a count-like and a rate-like column when the data has no column that already holds their product, else None.
    With several rates the one that is at most every other in 95% of rows (the amount actually charged) is used; if none is, or several counts remain, nothing is derived."""
    nums = _number_columns(df, cols)
    qs = [n for n, x in nums.items() if is_count_like(x) and (kinds or {}).get(n, "additive") == "additive"]
    qs = _prefer_additive(qs, kinds)
    if len(qs) != 1:
        return None
    q = qs[0]
    ps = [n for n, x in nums.items() if n != q and is_rate_like(x) and (kinds or {}).get(n) != "additive"]
    if len(ps) > 1:      # percentage-sized columns (0-100 with decimals) sit far below the real price: keep the largest typical magnitude
        top = max(float(nums[n].median()) for n in ps)
        ps = [n for n in ps if float(nums[n].median()) >= PRICE_MAGNITUDE * top]
    if not ps:
        return None
    if len(ps) > 1:
        low = [a for a in ps if all(((nums[a] <= nums[b]) | nums[a].isna() | nums[b].isna()).mean() >= 0.95 for b in ps if b != a)]
        if len(low) != 1:
            return None
        rate = low[0]
    else:
        rate = ps[0]
    prod = nums[q] * nums[rate]
    for n, x in nums.items():
        if n in (q, rate):
            continue
        ok = prod.notna() & x.notna()
        if ok.sum() >= MIN_ROWS and ((x[ok] - prod[ok]).abs() <= 0.01 * prod[ok].abs() + 1e-9).mean() >= 0.9:
            return None          # a column already holds count x rate
    basis, facts = _rate_basis(nums[q], nums[rate])
    if basis == "unknown":
        return None
    return {"count": q, "rate": rate, "basis": basis, "others": [n for n in ps if n != rate], "rows": int(prod.notna().sum()), **facts}


def product_question(sig: dict, taken: set[str]) -> dict:
    """The count x rate question. When the data shows the rate is per unit the question is settled by the data ("auto") and only recorded; otherwise it is asked, with per unit as the default.
    Written by code, never by a model: the default builds a column, which no generic question validator would accept as a default."""
    ev = sig["evidence"]
    q, p = ev["count"], ev["rate"]
    name = dv.clean_name("revenue", taken)
    derive = {"type": "define_derived_metric", "kind": "product", "a": q, "b": p, "name": name, "use_as_main": True}
    others = ev.get("others") or []
    left = f" {', '.join(others)} stays a separate column." if others else ""
    opts = [{"value": "unit", "label": f"{p} is the price of one unit: revenue = {q} x {p}", "action": derive},
            {"value": "total", "label": f"{p} is already the total of the line: use it as revenue", "action": {"type": "set_measures", "columns": [p], "pin_main": True}}]
    base = {"id": sig["id"], "kind": "product_basis", "options": opts, "default": "unit", "columns": [q, p], "signal": sig["id"], "strong": True}
    if ev["basis"] == "unit":
        return {**base, "auto": "data", "text": f"Revenue is {q} x {p}",
                "why": f"No column holds revenue. {p} does not grow with {q} (a line of several units costs {ev['ratio']:.2f}x a single unit), so it is a per-unit price.{left}"}
    return {**base, "text": f"Is {p} the price of one unit or the total of the line?",
            "why": f"No column holds revenue. Whether {p} grows with {q} cannot be told from the values, and it decides if revenue is {q} x {p} or just {p}.{left}"}
