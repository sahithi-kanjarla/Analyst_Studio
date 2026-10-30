"""One place that decides what kind of measure a column is: additive (sum it), a rate/average (mean it), or other;."""
from __future__ import annotations

import re

SUM_WORDS = re.compile(r"revenue|sales|amount|total|count|qty|quantity|spend|cost|profit|units|orders", re.I)
AVG_WORDS = re.compile(r"pct|percent|rate|ratio|discount|avg|average|score|margin|price|per_|_per|age$", re.I)  # never add these up

ROWS_LABEL = "records"         # what a share of rows is a share of, and what any share falls back to when the kind of the measure is unknown

KINDS = ("additive", "rate", "other")


def measure_kind(column_name: str, profile: dict | None = None, overrides: dict | None = None) -> str:
    """'additive' (totals make sense), 'rate' (average it, never add up) or 'other'.
    An entry in overrides {column: kind} wins over the word lists. profile is accepted for callers that have it; unused so far."""
    if overrides:
        k = overrides.get(column_name)
        if k in KINDS:
            return k
    if SUM_WORDS.search(column_name):
        return "rate" if AVG_WORDS.search(column_name) else "additive"
    return "rate" if AVG_WORDS.search(column_name) else "other"


def has_sum_word(column_name: str, profile: dict | None = None, overrides: dict | None = None) -> bool:
    """Looser legacy test: the name looks summable even if it also looks like a rate (e.g. 'total_pct'). An override of 'additive' says yes, any other override no."""
    if overrides:
        k = overrides.get(column_name)
        if k in KINDS:
            return k == "additive"
    return bool(SUM_WORDS.search(column_name))

