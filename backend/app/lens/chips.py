"""The suggested questions ("chips"), written from this run's own columns and values. Each chip carries the complete query it stands for, so it runs without any AI.
`start` gives the opening set; `follow_ups` gives 2 or 3 follow-ups after an answer. Nothing here knows a column name or a domain word."""
from __future__ import annotations

import hashlib

import pandas as pd

from . import query as qmod
from .context import Ctx

MAX_START = 8


def soft(label: str) -> str:
    """'Order Date' -> 'order date'; an acronym such as 'SKU count' keeps its capitals."""
    return label if any(w.isupper() and len(w) > 1 for w in label.split()) else label.lower()


def chip(text: str, query: dict) -> dict:
    return {"id": "c_" + hashlib.sha1(qmod.signature(query).encode()).hexdigest()[:10], "text": text, "query": query}


def main_measure(ctx: Ctx, result: dict):
    plan = (result.get("plan") or {}).get("target")
    ms = [c for c in ctx.measures() if not c.ratio]
    return ctx.cols[plan] if plan in ctx.cols and ctx.cols[plan] in ms else (next((c for c in ms if c.kind == "money"), None) or (ms[0] if ms else None))


def groups(ctx: Ctx, df: pd.DataFrame) -> list:
    return [c for c in ctx.groupable() if 2 <= df[c.name].nunique(dropna=True) <= 40]


def _periods(ctx: Ctx, df: pd.DataFrame):
    """The last two years, quarters or months of the date column that the data has (whichever pair exists first), or None."""
    if not ctx.date_col:
        return None
    s = df[ctx.date_col].dropna()
    if not len(s):
        return None
    for freq, fmt in (("Y", lambda p: str(p.year)), ("Q", lambda p: f"{p.year}-Q{p.quarter}"), ("M", lambda p: f"{p.year}-{p.month:02d}")):
        ps = sorted(s.dt.to_period(freq).unique())
        if len(ps) >= 2:
            return fmt(ps[-2]), fmt(ps[-1])
    return None


def start(ctx: Ctx, df: pd.DataFrame, result: dict) -> list[dict]:
    if "start" not in ctx.memo:
        ctx.memo["start"] = _start(ctx, df, result)
    return ctx.memo["start"]


def _start(ctx: Ctx, df: pd.DataFrame, result: dict) -> list[dict]:
    m = main_measure(ctx, result)
    gs = groups(ctx, df)
    out: list[dict] = []
    base = {"agg": "sum", "measure": m.name} if m else {"agg": "count"}
    ml = soft(m.display) if m else "rows"
    ph = f"total {ml}" if m else "number of rows"
    if gs:
        g = gs[0]
        out.append(chip(f"Which {soft(g.display)} has the highest {ph}?", {"op": "top", **base, "group_by": [g.name], "n": 3, "direction": "top"}))
    if ctx.date_col:
        out.append(chip(f"How has the {ph} changed over time?", {"op": "trend", **base, "date": ctx.date_col, "grain": "auto"}))
    if gs and (not m or m.kind != "rate"):
        out.append(chip(f"How is the {ph} split by {soft(gs[0].display)}?", {"op": "share", **{**base, "agg": "sum" if m else "count"}, "group_by": [gs[0].name], "n": 6}))
    pr = _periods(ctx, df)
    if pr:
        out.append(chip(f"How did the {ph} change from {pr[0]} to {pr[1]}?", {"op": "period_change", **base, "date": ctx.date_col, "a": pr[0], "b": pr[1]}))
    if gs and m:
        top2 = df.groupby(gs[0].name, observed=True)[m.name].sum().sort_values(ascending=False).head(2)
        if len(top2) == 2:
            a, b = str(top2.index[0]), str(top2.index[1])
            out.append(chip(f"Compare {a} and {b} on {ph}", {"op": "compare", **base, "by": gs[0].name, "a": a, "b": b}))
    if m:
        out.append(chip(f"How are {ml} values spread?", {"op": "distribution", "column": m.name}))
        others = [c for c in ctx.measures() if not c.ratio and c.name != m.name]
        if others:
            out.append(chip(f"Do {ml} and {soft(others[0].display)} move together?", {"op": "relationship", "x": m.name, "y": others[0].name}))
    if len(gs) > 1:
        g2 = gs[1]
        out.append(chip(f"What is the {ph} by {soft(g2.display)}?", {"op": "aggregate", **base, "group_by": [g2.name], "n": 8}))
    return [c for c in out if qmod.check(c["query"], ctx, df).query][:MAX_START]


def follow_ups(ctx: Ctx, df: pd.DataFrame, result: dict, last: dict | None) -> list[dict]:
    """Two or three follow-ups to the answer just given: a change of the same query first, then the opening questions it has not already asked."""
    cands: list[dict] = []
    if last:
        gs = groups(ctx, df)
        lab = lambda col: soft(ctx.label(col))
        base = {k: last[k] for k in ("measure", "agg", "filters") if k in last}
        ph = f"total {lab(last['measure'])}" if last.get("measure") and last.get("agg") == "sum" else "this"
        g = (last.get("group_by") or [None])[0]
        others = [c for c in gs if c.name != g and c.name not in (last.get("split_by") or [])]
        op = last["op"]
        if op in ("aggregate", "top", "share") and g:
            if ctx.date_col:
                cands.append(chip(f"Show {ph if ph != 'this' else 'it'} over time", {"op": "trend", **base, "date": ctx.date_col, "grain": "auto"}))
            if others and op == "aggregate":
                cands.append(chip(f"Split it by {lab(others[0].name)}", {**last, "split_by": [others[0].name], "chart": "auto"}))
            if others:
                cands.append(chip(f"Break it down by {lab(others[0].name)} instead", {**last, "group_by": [others[0].name], "split_by": [], "chart": "auto"}))
        elif op in ("trend", "period_change"):
            if gs:
                cands.append(chip(f"Which {lab(gs[0].name)} contributes most?", {"op": "top", **base, "group_by": [gs[0].name], "n": 3, "direction": "top"}))
                if op == "trend" and not last.get("split_by"):
                    cands.append(chip(f"Split it by {lab(gs[0].name)}", {**last, "split_by": [gs[0].name], "chart": "auto"}))
            if op == "trend" and last.get("grain") in ("day", "week", "month"):
                cands.append(chip("Show it by quarter" if last["grain"] == "month" else "Show it by month", {**last, "grain": "quarter" if last["grain"] == "month" else "month", "chart": "auto"}))
        elif op == "distribution":
            ms = [c for c in ctx.measures() if not c.ratio and c.name != last.get("column")]
            if ms and last.get("column") in ctx.cols:
                cands.append(chip(f"Does {lab(last['column'])} move with {lab(ms[0].name)}?", {"op": "relationship", "x": last["column"], "y": ms[0].name}))
        elif op == "compare" and gs:
            cands.append(chip(f"How is it split across {lab(last['by'])}?", {"op": "share", "measure": last.get("measure"), "agg": "sum" if last.get("agg") != "count" else "count", "group_by": [last["by"]], "n": 6}))
    seen = {qmod.signature(last)} if last else set()
    ops_used = {last["op"]} if last else set()
    pool = start(ctx, df, result)
    out: list[dict] = []
    for c in cands + [c for c in pool if c["query"]["op"] not in ops_used] + pool:
        ch = qmod.check(c["query"], ctx, df)
        if not ch.query or qmod.signature(ch.query) in seen:
            continue
        seen.add(qmod.signature(ch.query))
        out.append(c)
        if len(out) == 3:
            break
    return out


def _stems(text: str) -> set[str]:
    from .records import stems
    return stems(text)


def relevant(ctx: Ctx, df: pd.DataFrame, result: dict, question: str, last: dict | None, exclude: set[str], n: int = 2, extra: list[dict] | None = None) -> list[dict]:
    """The n suggested questions that best fit what the user was just talking about, never one that was just offered (unless nothing else is left).
    A question fits when it shares words with what was typed, or uses the measure or grouping of the last answer."""
    q_words = _stems(question)
    topic = {c for c in (last.get("measure"), *(last.get("group_by") or []), *(last.get("split_by") or []), last.get("by"), last.get("column")) if c} if last else set()
    pool = (follow_ups(ctx, df, result, last) if last else []) + start(ctx, df, result) + list(extra or [])
    scored, seen = [], set()
    for i, c in enumerate(pool):
        if c["id"] in seen or (c.get("query") and not qmod.check(c["query"], ctx, df).query):
            continue
        seen.add(c["id"])
        qry = c.get("query") or {}
        cols = {x for x in (qry.get("measure"), qry.get("by"), qry.get("column"), *(qry.get("group_by") or []), *(qry.get("split_by") or [])) if x}
        words = _stems(c["text"]) | {w for x in cols for w in _stems(ctx.label(x))}
        score = 3 * len(q_words & words) + 2 * len(topic & cols) + (1 if i < 3 and last else 0)
        scored.append((-score, i, c))
    scored.sort(key=lambda t: (t[0], t[1]))
    fresh = [c for _, _, c in scored if c["id"] not in exclude]
    stale = [c for _, _, c in scored if c["id"] in exclude]
    return (fresh + stale)[:n]
