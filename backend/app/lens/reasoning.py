"""Two things code does when a question carries more than a lookup:
  premise check   a question that assumes something the data contradicts ("why is Electronics the top?" when it is second) is corrected first, with the computed figures;
  why breakdown   the data alone cannot show the real-world cause of a number, so a "why" question gets what is behind the number instead (shares, a split by the most
                  relevant other column, a change between two periods), drawn and calculated by code. No cause is ever claimed.
Nothing here knows a column name or a domain word."""
from __future__ import annotations

import re

import pandas as pd

from . import chips as chipmod
from . import engine, howtext, query as qmod
from .context import Ctx, fmt_num

WHY_INTRO = "The data alone can't show why this is the case, only what is behind the number. Here is that breakdown."
UP = {"top", "highest", "best", "biggest", "largest", "leading", "first", "1", "#1", "one", "lead", "leads", "dominate", "dominates", "wins"}
DOWN = {"lowest", "bottom", "worst", "smallest", "last", "least", "weakest"}
FILLER = {"is", "are", "was", "were", "the", "our", "a", "an", "currently", "always", "really", "actually", "ranked", "ranking", "rank", "at", "as", "number", "no", "in", "by", "of",
          "does", "do", "so", "still", "even", "now", "also", "just", "category", "city", "region", "tier", "segment", "product", "group"}
WISH = {"be", "instead", "rather", "expected", "expect", "thought", "think", "supposed", "should", "would", "assumed", "guess", "hoped"}
ORDINAL = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"]
MAX_VALUES_PER_COLUMN = 300


def _toks(text: str) -> list[str]:
    return re.findall(r"[a-z0-9&#]+", str(text).lower())


def _mentions(question_toks: list[str], df: pd.DataFrame, ctx: Ctx):
    """[(column, value, start, end)]: real values of the data's category columns that the question names (whole words)."""
    out = []
    for c in ctx.groupable():
        vals = df[c.name].dropna().astype(str).unique()
        if len(vals) > MAX_VALUES_PER_COLUMN:
            continue
        for v in vals:
            vt = _toks(v)
            if not vt or len("".join(vt)) < 2:
                continue
            n = len(vt)
            for i in range(len(question_toks) - n + 1):
                if question_toks[i:i + n] == vt:
                    out.append((c.name, v, i, i + n))
    # a value found in several columns is ambiguous: no claim is made about it
    seen: dict[tuple, set] = {}
    for col, v, i, j in out:
        seen.setdefault((v.lower(), i, j), set()).add(col)
    return [(col, v, i, j) for col, v, i, j in out if len(seen[(v.lower(), i, j)]) == 1]


def claims(question: str, df: pd.DataFrame, ctx: Ctx) -> list[dict]:
    """[{column, value, direction: "up"|"down"}]: what the question takes for granted about a group ("why is X the top?" claims X is the top). A group the question only wants
    ("it should be Y") or asks about ("X or Y?") is not a claim."""
    toks = _toks(question)
    found = []
    for col, v, i, j in _mentions(toks, df, ctx):
        before, after = toks[max(0, i - 4):i], toks[j:j + 7]
        if WISH & set(before[-3:]):
            continue
        direction = None
        for w in after:
            if w in UP:
                direction = "up"
                break
            if w in DOWN:
                direction = "down"
                break
            if w not in FILLER:
                break
        if direction and not any(f["column"] == col and f["value"] == v for f in found):
            found.append({"column": col, "value": v, "direction": direction})
    return found[:2]


def _measure_query(base: dict | None, ctx: Ctx, result: dict) -> dict | None:
    base = base or {}
    if base.get("agg") and (base.get("measure") or base.get("agg") == "count"):
        return {k: base[k] for k in ("agg", "measure", "filters") if k in base}
    m = chipmod.main_measure(ctx, result)
    return {"agg": "sum", "measure": m.name, "filters": base.get("filters") or []} if m else {"agg": "count", "measure": None, "filters": base.get("filters") or []}


def _ordinal(i: int, n: int) -> str:
    if i == n:
        return "last of " + str(n)
    return ORDINAL[i - 1] if i <= len(ORDINAL) else f"number {i}"


def check_premise(question: str, df: pd.DataFrame, ctx: Ctx, result: dict, base: dict | None = None) -> dict | None:
    """The correction to put first, or None when nothing the question assumes is wrong. {text, figures, claims, query} where `query` is the ranking behind the correction."""
    found = claims(question, df, ctx)
    if not found:
        return None
    mq = _measure_query(base, ctx, result)
    wrong, figs, parts, rank_q = [], [], [], None
    for cl in found:
        q = {"op": "top", **mq, "group_by": [cl["column"]], "n": 5, "direction": "top"}
        ch = qmod.check(q, ctx, df)
        if not ch.query:
            continue
        try:
            F = engine.Frame(df, ctx, ch.query)
            d = F.need(engine.needed(ch.query, ctx) + [cl["column"]], "left out")
            s = engine.agg_by(d, [cl["column"]], ch.query, ctx).dropna()
        except engine.Cannot:
            continue
        if len(s) < 2 or cl["value"] not in s.index:
            continue
        order = s.sort_values(ascending=cl["direction"] == "down", kind="mergesort")      # best-first for the claim made
        pos = list(order.index).index(cl["value"]) + 1
        if pos == 1:
            continue
        V = engine.vfmt(ch.query, ctx)
        ph, lab = chipmod.soft(engine.phrase(ch.query, ctx)), ctx.label(cl["column"])
        word = "top" if cl["direction"] == "up" else "lowest"
        lead = order.index[0]
        actual = _ordinal(pos, len(s)) if cl["direction"] == "up" else (ORDINAL[pos - 1] if pos <= len(ORDINAL) else f"number {pos}") + " lowest"
        a, b = V(order.iloc[0]), V(s[cl["value"]])
        figs += [{"label": f"{lead} {ph}", "value": float(order.iloc[0]), "shown": a}, {"label": f"{cl['value']} {ph}", "value": float(s[cl["value"]]), "shown": b}]
        parts.append(f"{cl['value']} is not the {word} {lab.lower()} by {ph}: {lead} is, at {a}. {cl['value']} is {actual}, at {b}.")
        wrong.append({**cl, "actual_position": pos, "leader": str(lead)})
        rank_q = rank_q or {"op": "top", **mq, "group_by": [cl["column"]], "n": 5, "direction": "top" if cl["direction"] == "up" else "bottom"}
    if not wrong:
        return None
    return {"text": "Correction first: " + " ".join(parts), "figures": figs, "claims": wrong, "query": rank_q}


# ------------------------------------------------------------------ why: what is behind the number
def _additive(q: dict) -> bool:
    return q.get("agg") in ("sum", "count")


def _split_columns(q: dict, ctx: Ctx, df: pd.DataFrame, skip: list[str]) -> list[str]:
    """The other category columns, the one whose groups differ most first (spread of the number across the column's groups, relative to its average)."""
    used = set(skip) | {f["column"] for f in q.get("filters") or [] if f.get("op") in ("eq", "in")}
    scored = []
    try:
        F = engine.Frame(df, ctx, q)
        d = F.df
    except engine.Cannot:
        return []
    for c in chipmod.groups(ctx, df)[:12]:
        if c.name in used:
            continue
        try:
            dd = d[d[c.name].notna()]
            s = engine.agg_by(dd, [c.name], q, ctx).dropna()
        except Exception:
            continue
        if len(s) < 2 or not abs(float(s.mean())):
            continue
        scored.append((float(s.std(ddof=0) / abs(s.mean())), c.name))
    return [n for _, n in sorted(scored, key=lambda t: -t[0])]


def why_plan(q: dict, ctx: Ctx, df: pd.DataFrame, result: dict) -> dict | None:
    """What to calculate for a 'why' question about the checked query `q`: the main breakdown, one extra figure set, and two follow-up chips. None when the table has nothing to break down by."""
    mq = _measure_query(q, ctx, result)
    if mq is None:
        return None
    g = (q.get("group_by") or [None])[0] or (q.get("by") if q.get("op") == "compare" else None)
    others = _split_columns({**mq}, ctx, df, [g] if g else [])
    base = {k: mq[k] for k in ("agg", "measure", "filters") if k in mq}
    pr = chipmod._periods(ctx, df)
    extra = None
    if g:
        primary = {"op": "aggregate", **base, "group_by": [g], "split_by": others[:1], "n": 6, "chart": "auto"}
        if _additive(mq):
            extra = {"op": "share", **base, "group_by": [g], "n": 6}
    elif others and q.get("op") == "period_change" and q.get("a") and q.get("b"):           # "why did it change between A and B?": the same two periods, split by what changed most
        span = {"date": q.get("date") or ctx.date_col, "a": q["a"], "b": q["b"]}
        primary = {"op": "period_change", **base, **span, "group_by": [others[0]], "n": 6}
        extra = {"op": "period_change", **base, **span}
    elif others and q.get("op") == "trend" and pr and ctx.date_col:
        primary = {"op": "period_change", **base, "date": ctx.date_col, "a": pr[0], "b": pr[1], "group_by": [others[0]], "n": 6}
        extra = {"op": "period_change", **base, "date": ctx.date_col, "a": pr[0], "b": pr[1]}
    elif others:
        primary = {"op": "share", **base, "group_by": [others[0]], "n": 6} if _additive(mq) else {"op": "aggregate", **base, "group_by": [others[0]], "n": 8}
        extra = {"op": "trend", **base, "date": ctx.date_col, "grain": "auto"} if ctx.date_col else None
    elif ctx.date_col:
        primary = {"op": "trend", **base, "date": ctx.date_col, "grain": "auto"}
    else:
        return None
    by = g or (others[0] if others else None)
    chips = []
    if pr and ctx.date_col and primary["op"] != "period_change":
        chips.append(chipmod.chip(f"How did it change from {pr[0]} to {pr[1]}" + (f", by {chipmod.soft(ctx.label(by))}?" if by else "?"),
                                  {"op": "period_change", **base, "date": ctx.date_col, "a": pr[0], "b": pr[1], **({"group_by": [by]} if by else {})}))
    if by:
        pivot = others[0] if g else (others[1] if len(others) > 1 else None)
        if pivot:
            lead_filter = []
            try:
                dd = engine.Frame(df, ctx, base).df
                s = engine.agg_by(dd[dd[by].notna()], [by], mq, ctx).dropna().sort_values(ascending=False)
                if len(s):
                    lead_filter = [{"column": by, "op": "eq", "value": str(s.index[0])}]
            except Exception:
                pass
            chips.append(chipmod.chip(f"Which {chipmod.soft(ctx.label(pivot))} contributes most" + (f" within {lead_filter[0]['value']}?" if lead_filter else "?"),
                                      {"op": "top", **{**base, "filters": list(base.get("filters") or []) + lead_filter}, "group_by": [pivot], "n": 3, "direction": "top"}))
    if ctx.date_col and primary["op"] != "trend":
        chips.append(chipmod.chip("How has it moved over time?", {"op": "trend", **base, "date": ctx.date_col, "grain": "auto"}))
    if by and _additive(mq) and primary["op"] != "share":
        chips.append(chipmod.chip(f"How is it split by {chipmod.soft(ctx.label(by))}?", {"op": "share", **base, "group_by": [by], "n": 6}))
    if len(others) > 1:
        chips.append(chipmod.chip(f"What is it by {chipmod.soft(ctx.label(others[-1]))}?", {"op": "aggregate", **base, "group_by": [others[-1]], "n": 8}))
    return {"primary": primary, "extra": extra, "chips": [c for c in chips if qmod.check(c["query"], ctx, df).query][:2], "split": others[:1]}


def run_why(q: dict, df: pd.DataFrame, ctx: Ctx, result: dict, premise_text: str = ""):
    """(Result, chips). The Result's sentence says plainly that the data cannot show the cause, then gives the computed breakdown. None when there is nothing to break down."""
    plan = why_plan(q, ctx, df, result)
    if plan is None:
        return None
    ch = qmod.check(plan["primary"], ctx, df)
    if not ch.query:
        return None
    R = engine.run(ch.query, df, ctx)
    sentences = [R.sentence]
    notes = []
    if plan["extra"]:
        ch2 = qmod.check(plan["extra"], ctx, df)
        if ch2.query:
            try:
                R2 = engine.run(ch2.query, df, ctx)
                sentences.insert(0, R2.sentence)
                R.figures = R2.figures + R.figures
                notes.append("Also worked out: " + R2.how.get("operation", "").rstrip(".").lower() + ".")
            except engine.Cannot:
                pass
    if plan["split"]:
        notes.append(f"The breakdown is split by {ctx.label(plan['split'][0])}, the other column whose groups differ most in this number.")
    notes.append("This shows what is behind the number. It does not show why it happened.")
    R.sentence = WHY_INTRO + " " + " ".join(sentences)
    R.how = howtext.with_lines({**R.how, "notes": list(R.how.get("notes") or []) + notes})
    return R, plan["chips"]
