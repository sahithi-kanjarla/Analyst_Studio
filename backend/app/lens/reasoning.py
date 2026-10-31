"""Two things code does when a question carries more than a lookup:
  premise check   a question that assumes something the data contradicts ("why is Electronics the top?" when it is second) is corrected first, with the computed figures;
  why breakdown   the data alone cannot show the real-world cause of a number, so a "why" question gets what is behind the number instead (shares, a split by the most
                  relevant other column, a change between two periods), drawn and calculated by code. No cause is ever claimed.
Nothing here knows a column name or a domain word."""
from __future__ import annotations

import calendar
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
WISH_BEFORE = re.compile(r"(?:should be|ought to be|supposed to be|expected|expect|thought|assumed|guessed|hoped|figured)(?: it| that| to be| was| would be| is| the)*$")
HIGHER = {"higher", "greater", "bigger", "larger", "more", "better", "above", "ahead"}
LOWER = {"lower", "less", "smaller", "worse", "below", "behind", "fewer"}
COMPARE_FILL = {"is", "are", "was", "were", "much", "far", "way", "a", "lot", "slightly", "even", "always", "really", "actually", "definitely", "clearly", "still", "just", "somewhat", "quite", "also"}
MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m} | {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m} | {"sept": 9}
DOWN_WORDS = r"went down|go(?:es)? down|fell|fall(?:s|en)?|dropped|drops?|declined?|decreased?|dipped|slumped|shrank|shrunk|(?:was|were|is|are) down|sank|plunged|tumbled"
UP_WORDS = r"went up|go(?:es)? up|rose|rise[ns]?|increased?|grew|grown|grow|jumped|climbed|surged|(?:was|were|is|are) up|spiked|improved"
DIRECTION_RE = re.compile(rf"\b(?P<dn>{DOWN_WORDS})\b|\b(?P<up>{UP_WORDS})\b", re.I)
PERIOD_RE = re.compile(r"\b(?:(?P<mon>" + "|".join(sorted(MONTHS, key=len, reverse=True)) + r")\b\.?(?:\s+(?P<myr>(?:19|20)\d\d))?|(?P<q>[Qq][1-4])\s*-?\s*(?P<qy>(?:19|20)\d\d)|(?P<y>(?:19|20)\d\d))\b", re.I)


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


def wish_claims(question: str, df: pd.DataFrame, ctx: Ctx) -> list[dict]:
    """[{column, value, direction}]: a group the question says SHOULD be the top ("it should be Clothing", "I expected Clothing"): a claim that it is, to be checked."""
    toks = _toks(question)
    low = any(w in DOWN for w in toks)
    out = []
    for col, v, i, j in _mentions(toks, df, ctx):
        if WISH_BEFORE.search(" ".join(toks[max(0, i - 5):i])) and not any(f["column"] == col and f["value"] == v for f in out):
            out.append({"column": col, "value": v, "direction": "down" if low else "up", "wish": True})
    return out[:2]


def comparisons(question: str, df: pd.DataFrame, ctx: Ctx) -> list[dict]:
    """[{column, a, b, direction: "up"|"down"}]: "A is higher than B" claims about two values of one column ("up": A above B)."""
    toks = _toks(question)
    ms = sorted(_mentions(toks, df, ctx), key=lambda m: m[2])
    out = []
    for x, y in zip(ms, ms[1:]):
        if x[0] != y[0] or x[1] == y[1]:
            continue
        between = toks[x[3]:y[2]]
        if "not" in between or "isn" in between or "aren" in between or "wasn" in between:
            continue
        words = [w for w in between if w not in COMPARE_FILL and w not in ("than", "the", "then", "t")]
        if len(words) == 1 and (words[0] in HIGHER or words[0] in LOWER) and len(between) <= 6:
            out.append({"column": x[0], "a": x[1], "b": y[1], "direction": "up" if words[0] in HIGHER else "down"})
    return out[:2]


def _period_label(m, df: pd.DataFrame, ctx: Ctx):
    """(a, b, size): the period named in the question and the one before it (same size), or None. A month without a year means the latest year that has that month."""
    if m.group("y"):
        y = int(m.group("y"))
        return str(y - 1), str(y), "year"
    if m.group("q"):
        y, q = int(m.group("qy")), int(m.group("q")[1])
        py, pq = (y, q - 1) if q > 1 else (y - 1, 4)
        return f"{py}-Q{pq}", f"{y}-Q{q}", "quarter"
    mon = MONTHS.get(m.group("mon").lower().rstrip("."))
    if not mon:
        return None
    if m.group("myr"):
        years = [int(m.group("myr"))]
    else:
        s = df[ctx.date_col].dropna()
        years = sorted({int(y) for y in s.dt.year.unique()}, reverse=True)
        years = [y for y in years if ((s.dt.year == y) & (s.dt.month == mon)).any()]
    for y in years:
        py, pm = (y, mon - 1) if mon > 1 else (y - 1, 12)
        return f"{py}-{pm:02d}", f"{y}-{mon:02d}", "month"
    return None


def directions(question: str, df: pd.DataFrame, ctx: Ctx) -> list[dict]:
    """[{direction: "up"|"down", a, b, grain}]: "sales went down in March" claims the number fell from the period before to that one."""
    if not ctx.date_col:
        return []
    out = []
    for dm in DIRECTION_RE.finditer(question):
        best = None
        for pm in PERIOD_RE.finditer(question):
            if pm.group("mon") and pm.group("mon").lower() == "may" and not pm.group("mon")[0].isupper() and not pm.group("myr"):
                continue
            dist = min(abs(pm.start() - dm.end()), abs(dm.start() - pm.end()))
            if dist <= 40 and (best is None or dist < best[0]):
                best = (dist, pm)
        if best:
            lab = _period_label(best[1], df, ctx)
            if lab:
                out.append({"direction": "down" if dm.group("dn") else "up", "a": lab[0], "b": lab[1], "grain": lab[2]})
    return out[:1]


def asserts(question: str, df: pd.DataFrame, ctx: Ctx) -> bool:
    """True when the question takes something for granted that the data can check."""
    return bool(claims(question, df, ctx) or wish_claims(question, df, ctx) or comparisons(question, df, ctx) or directions(question, df, ctx))


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


def _group_values(df: pd.DataFrame, ctx: Ctx, mq: dict, col: str):
    """(checked query, formatter, Series of the calculation per group) or None."""
    ch = qmod.check({"op": "top", **mq, "group_by": [col], "n": 5, "direction": "top"}, ctx, df)
    if not ch.query:
        return None
    try:
        F = engine.Frame(df, ctx, ch.query)
        d = F.need(engine.needed(ch.query, ctx) + [col], "left out")
        s = engine.agg_by(d, [col], ch.query, ctx).dropna()
    except engine.Cannot:
        return None
    return ch.query, engine.vfmt(ch.query, ctx), s


def cap_first(t: str) -> str:
    return t[:1].upper() + t[1:]


def check_premise(question: str, df: pd.DataFrame, ctx: Ctx, result: dict, base: dict | None = None) -> dict | None:
    """The correction to put first, or None when nothing the question assumes is wrong. {text, figures, claims, query} where `query` is a calculation that shows the correction.
    What is checked: a group named as the top or lowest, a group the question says it should be, "A is higher than B", and "it went down in March"."""
    found = claims(question, df, ctx)
    wishes = [w for w in wish_claims(question, df, ctx) if not any(f["column"] == w["column"] and f["value"] == w["value"] for f in found)]
    comps, dirs = comparisons(question, df, ctx), directions(question, df, ctx)
    if not (found or wishes or comps or dirs):
        return None
    mq = _measure_query(base, ctx, result)
    wrong, figs, parts, rank_q = [], [], [], None
    for cl in found + wishes:
        got = _group_values(df, ctx, mq, cl["column"])
        if not got:
            continue
        cq, V, s = got
        if len(s) < 2 or cl["value"] not in s.index:
            continue
        order = s.sort_values(ascending=cl["direction"] == "down", kind="mergesort")      # best-first for the claim made
        pos = list(order.index).index(cl["value"]) + 1
        if pos == 1:
            continue
        ph, lab = chipmod.soft(engine.phrase(cq, ctx)), ctx.label(cl["column"])
        word = "top" if cl["direction"] == "up" else "lowest"
        lead = order.index[0]
        actual = _ordinal(pos, len(s)) if cl["direction"] == "up" else (ORDINAL[pos - 1] if pos <= len(ORDINAL) else f"number {pos}") + " lowest"
        a, b = V(order.iloc[0]), V(s[cl["value"]])
        figs += [{"label": f"{lead} {ph}", "value": float(order.iloc[0]), "shown": a}, {"label": f"{cl['value']} {ph}", "value": float(s[cl["value"]]), "shown": b}]
        parts.append(f"{cl['value']} is not the {word} {lab.lower()} by {ph}: {lead} is, at {a}. {cl['value']} is {actual}, at {b}.")
        wrong.append({**cl, "actual_position": pos, "leader": str(lead), "note": f"Checked your question's assumption that {cl['value']} is the {word} {lab.lower()}: ranked every group by the same calculation."})
        rank_q = rank_q or {"op": "top", **mq, "group_by": [cl["column"]], "n": 5, "direction": "top" if cl["direction"] == "up" else "bottom"}
    for cp in comps:
        got = _group_values(df, ctx, mq, cp["column"])
        if not got:
            continue
        cq, V, s = got
        if cp["a"] not in s.index or cp["b"] not in s.index:
            continue
        va, vb = float(s[cp["a"]]), float(s[cp["b"]])
        if va != vb and (va > vb) == (cp["direction"] == "up"):
            continue
        ph = chipmod.soft(engine.phrase(cq, ctx))
        word = "higher" if cp["direction"] == "up" else "lower"
        big = cp["a"] if va > vb else cp["b"]
        verdict = f"{big} is higher." if va != vb else "They are equal."
        figs += [{"label": f"{cp['a']} {ph}", "value": va, "shown": V(va)}, {"label": f"{cp['b']} {ph}", "value": vb, "shown": V(vb)}]
        parts.append(f"{cp['a']} is not {word} than {cp['b']} by {ph}: {cp['a']} is {V(va)} and {cp['b']} is {V(vb)}. {verdict}")
        wrong.append({**cp, "value": cp["a"], "note": f"Checked your question's claim that {cp['a']} is {word} than {cp['b']}: calculated both the same way."})
        rank_q = rank_q or {"op": "compare", **mq, "by": cp["column"], "a": cp["a"], "b": cp["b"]}
    for dr in dirs:
        pq = qmod.check({"op": "period_change", **mq, "date": ctx.date_col, "a": dr["a"], "b": dr["b"]}, ctx, df)
        if not pq.query:
            continue
        try:
            R = engine.run(pq.query, df, ctx)
        except engine.Cannot:
            continue
        va, vb, ch, pc = R.rows[0][1], R.rows[0][2], R.rows[0][3], R.rows[0][4]
        if ch is None or (ch != 0 and (ch < 0) == (dr["direction"] == "down")):
            continue
        V, ph = engine.vfmt(pq.query, ctx), chipmod.soft(engine.phrase(pq.query, ctx))
        said = "go down" if dr["direction"] == "down" else "go up"
        did = "stayed the same" if ch == 0 else ("went up" if ch > 0 else "went down")
        figs += [{"label": f"{ph} {dr['a']}", "value": float(va), "shown": V(va)}, {"label": f"{ph} {dr['b']}", "value": float(vb), "shown": V(vb)}]
        tail = ""
        if pc is not None and ch != 0:
            figs.append({"label": f"{ph} change %", "value": abs(float(pc)), "shown": f"{abs(pc):.1f}"})
            tail = f" ({'+' if ch > 0 else '-'}{abs(pc):.1f}%)"
        parts.append(f"{cap_first(ph)} did not {said} in {dr['b']}: it {did}, from {V(va)} in {dr['a']} to {V(vb)} in {dr['b']}{tail}.")
        wrong.append({"column": "period", "value": dr["b"], "direction": dr["direction"], "note": f"Checked your question's claim that the number {'fell' if dr['direction'] == 'down' else 'rose'} in {dr['b']}: compared {dr['b']} with {dr['a']}, calculated the same way."})
        rank_q = rank_q or pq.query
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
