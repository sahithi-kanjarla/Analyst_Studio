"""Suggestions after a refusal must fit the kind of request that was refused: a request to change what is shown gets examples of changes to what is shown, a question about the data gets
close questions about the data, and anything else gets the general things Lens can do with the report. Never an unrelated question."""
from __future__ import annotations

import re

from ... import viewedit as ve
from ... import viewselect as vs
from .. import chips as chipmod, reports, rules, viewchat

VIEWISH = re.compile(r"\b(?:hide|unhide|restore|bring back|collapse|declutter|simplif\w+|shorter|shorten|condense|rename|retitle|pin|unpin|reorder|rearrange|keep only|only keep|tidy|less busy|remove (?:the|this|that|these|those)|get rid of|take (?:it|that|this|them) (?:off|away)|undo|reset)\b", re.I)
DATAISH = re.compile(r"\b(?:total|sum|average|mean|median|count|how many|how much|number of|top|bottom|best|worst|highest|lowest|biggest|smallest|most|least|per|by|share|percent|trend|compare|versus|vs|over time|growth|change|difference|rank|spread|distribution|correlat\w+)\b", re.I)


def request_kind(question: str, ctx=None, df=None) -> str:
    """"view" for a request to change what is shown, "data" for a question about the numbers, "other" for the rest. Decided from the request's own words and the report's columns and values."""
    q = question or ""
    if rules.wants_view_edit(q) or rules.wants_view_change(q) or viewchat.TOGGLE_RE.match(q) or VIEWISH.search(q):
        return "view"
    if DATAISH.search(q):
        return "data"
    if ctx is not None:
        w = vs.words(q)
        labels = set()
        for c in ctx.cols.values():
            labels |= vs.words(f"{c.name} {c.display}")
        if w & labels:
            return "data"
        if df is not None:
            for c in ctx.groupable():
                if c.name in df.columns and df[c.name].nunique(dropna=True) <= 200 and w & vs.words(" ".join(map(str, df[c.name].dropna().unique()[:200]))):
                    return "data"
    return "other"


def for_request(question: str, ctx, df, result: dict, wd, tab: str, n: int = 2) -> list[dict]:
    """Exactly n concrete chips (two) that relate to the request that was refused: of its own kind first, then the general ones. A refusal always shows them."""
    kind = request_kind(question, ctx, df)
    prev = reports.prev_chip_ids(wd)
    if kind == "view":
        pool = viewchat.chips_for(result, tab) or reports.next_chips(result, "view", "", prev)
    elif kind == "data":
        from .. import chat
        pool = chipmod.relevant(ctx, df, result, question, chat._last_query(wd), prev, n)
    else:
        pool = []
    general = [reports.ichip("Summarise the whole report", {"kind": "summary", "length": "standard", "scope": ""}), reports.ichip("What should we do next?", {"kind": "strategy", "focus": ""}),
            reports.ichip("What matters most for me?", {"kind": "priority", "terms": []})]
    out, seen = [], set()
    for c in list(pool) + general + list(chipmod.start(ctx, df, result)):
        if c["id"] not in seen:
            seen.add(c["id"])
            out.append(c)
        if len(out) == n:
            break
    return out
