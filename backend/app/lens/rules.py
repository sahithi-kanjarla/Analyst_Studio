"""Plain rules that run before any AI call, so they cost nothing and cannot be talked round. The words here are the user's own question words (margin, profit),
never column names: whether the data can answer is decided from the understanding record."""
from __future__ import annotations

import re

from .context import Ctx

ASKS_MARGIN = re.compile(r"\bmargins?\b", re.I)
ASKS_PROFIT = re.compile(r"\b(profits?|profitab\w+|earnings?|net income)\b", re.I)
NAMED = re.compile(r"profit|margin", re.I)


def margin_gap(question: str, ctx: Ctx) -> str | None:
    """The reply when the question is about margin or profit and the data cannot give it, else None. A profit or margin column (named so, or a ratio the run derived) means the
    question goes on to be answered normally; a data set with no cost column and no such column cannot give a margin, and Lens says so instead of guessing."""
    margin, profit = ASKS_MARGIN.search(question or ""), ASKS_PROFIT.search(question or "")
    if not (margin or profit):
        return None
    if any(NAMED.search(f"{c.name} {c.display} {c.meaning}") for c in ctx.measures()):
        return None
    costs = ctx.cost_columns()
    if costs:
        return (f"The data has a cost column ({costs[0].display}), but I can't work out margin from it yet, because I can't tell whether it is a cost per unit or a total. "
                "I can show the cost and the sales separately.")
    return ("Margin can't be computed because the data has no cost column." if margin
            else "Profit can't be worked out because the data has no cost column, so margin can't be computed either.")
