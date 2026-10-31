"""Runs a checked query on the cleaned table and writes the answer. Every figure comes from here: the sentence is built from the computed numbers, formatted by one
function, and each figure is recorded in `figures` so that what is shown can be compared with what was computed.
Adding an operation (a later stage: summaries, ranking, ...) means one function in RUNNERS and one entry in query.OPS / query.OP_HELP."""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import query as qmod
from .context import Ctx, fmt_num, fmt_value

MAX_POINTS = 400
MAX_SPLITS = 6
SCATTER_SAMPLE = 2000
FREQ = {"day": "D", "week": "W-SUN", "month": "M", "quarter": "Q", "year": "Y"}
NEXT_GRAIN = {"day": "week", "week": "month", "month": "quarter", "quarter": "year"}
CHART_TYPES = {"scalar": ["table"], "groups": ["bar", "share", "line", "table"], "split": ["stacked_bar", "bar", "line", "table"], "time": ["line", "bar", "table"],
               "time_split": ["line", "stacked_bar", "bar", "table"], "change": ["bar", "table"], "pair": ["bar", "table"], "hist": ["histogram", "table"],
               "categories": ["bar", "share", "table"], "scatter": ["scatter", "table"], "shares": ["share", "bar", "table"]}
DEFAULT_CHART = {"scalar": "table", "groups": "bar", "split": "stacked_bar", "time": "line", "time_split": "line", "change": "bar", "pair": "bar", "hist": "histogram",
                 "categories": "bar", "scatter": "scatter", "shares": "share"}


class Cannot(Exception):
    """The query is valid but the data cannot answer it (for example no rows are left after the filters). The message is shown to the user."""


@dataclass
class Result:
    query: dict
    shape: str = "scalar"
    columns: list[str] = field(default_factory=list)
    rows: list[list] = field(default_factory=list)
    shown: list[list[str]] = field(default_factory=list)
    sentence: str = ""
    figures: list[dict] = field(default_factory=list)
    how: dict = field(default_factory=dict)
    plot: dict = field(default_factory=dict)
    chart: str = "table"
    chart_note: str = ""
    controls: dict = field(default_factory=dict)
    additive: bool = False

    def fig(self, label: str, value, shown: str) -> str:
        self.figures.append({"label": label, "value": None if value is None else float(value), "shown": shown})
        return shown

    def add(self, raw: list, shown: list[str]) -> None:
        self.rows.append(raw)
        self.shown.append(shown)


# ------------------------------------------------------------------ the filtered frame, with every row accounted for
class Frame:
    def __init__(self, df: pd.DataFrame, ctx: Ctx, q: dict):
        self.ctx, self.q = ctx, q
        self.total = len(df)
        self.left_out: list[dict] = []
        self.notes: list[str] = []
        self.filters: list[str] = []
        mask = pd.Series(True, index=df.index)
        for f in q.get("filters") or []:
            mask &= self._mask(df, f)
            self.filters.append(self._describe(f))
        self.df = df[mask]
        cut = self.total - len(self.df)
        if cut:
            self.left_out.append({"reason": "removed by the filters", "rows": int(cut)})
        if not len(self.df):
            raise Cannot("No rows are left after those filters" + (f" ({'; '.join(self.filters)})" if self.filters else "") + ". Try removing a filter.")

    def _mask(self, df, f):
        s, op, v = df[f["column"]], f["op"], f["value"]
        if op in ("eq", "in", "neq"):
            hit = s.astype(str).isin(v if isinstance(v, list) else [v]) & s.notna()
            return ~hit if op == "neq" else hit
        if op == "period":
            a, b, _ = qmod.parse_period(v)
            return (s >= a) & (s < b)
        if op == "between":
            if isinstance(v[0], str):
                return (s >= pd.Timestamp(v[0])) & (s < pd.Timestamp(v[1]) + pd.Timedelta(days=1))
            return (s >= v[0]) & (s <= v[1])
        return {"gt": s > v, "gte": s >= v, "lt": s < v, "lte": s <= v}[op]

    def _describe(self, f) -> str:
        lab, op, v = self.ctx.label(f["column"]), f["op"], f["value"]
        if op in ("eq", "period"):
            return f"{lab} is {v}" if op == "eq" else f"{lab} in {v}"
        if op == "neq":
            return f"{lab} is not {v}"
        if op == "in":
            return f"{lab} is one of {', '.join(map(str, v))}"
        if op == "between":
            return f"{lab} between {fmt_num(v[0]) if not isinstance(v[0], str) else v[0]} and {fmt_num(v[1]) if not isinstance(v[1], str) else v[1]}"
        return f"{lab} {({'gt': 'above', 'gte': 'at least', 'lt': 'below', 'lte': 'at most'})[op]} {fmt_num(v)}"

    def need(self, cols: list[str], why: str) -> pd.DataFrame:
        """The rows that have a value in every one of `cols`; the rest are counted as left out."""
        d = self.df
        for c in cols:
            miss = int(d[c].isna().sum())
            if miss:
                self.left_out.append({"reason": f"no {self.ctx.label(c)} value ({why})", "rows": miss})
                d = d[d[c].notna()]
        if not len(d):
            raise Cannot(f"Every remaining row is missing {', '.join(self.ctx.label(c) for c in cols)}, so there is nothing to calculate.")
        self.df = d
        return d

    def how(self, operation: str, cols: list[str], extra: list[str] | None = None, hidden: int = 0) -> dict:
        used = len(self.df)
        out = int(self.total - used)
        return {"operation": operation, "columns": [{"name": c, "label": self.ctx.label(c), "role": self.ctx.cols[c].role} for c in dict.fromkeys(cols) if c in self.ctx.cols],
                "filters": self.filters, "rows_total": int(self.total), "rows_used": int(used), "rows_left_out": out, "left_out": self.left_out,
                "groups_not_shown": int(hidden), "notes": self.notes + (extra or [])}


# ------------------------------------------------------------------ aggregation
def agg_by(d: pd.DataFrame, keys: list, q: dict, ctx: Ctx):
    """The query's calculation per group (a Series indexed by `keys`), or one number when there are no keys."""
    agg, m = q["agg"], q.get("measure")
    g = d.groupby(keys, observed=True, dropna=True) if keys else None
    if agg == "count":
        return g.size() if g is not None else float(len(d))
    if agg == "count_distinct":
        return g[m].nunique() if g is not None else float(d[m].nunique())
    if agg == "ratio":
        a, b = ctx.cols[m].ratio
        if g is None:
            sb = float(d[b].sum())
            return float(d[a].sum()) / sb if sb else float("nan")
        sa, sb = g[a].sum(), g[b].sum()
        return (sa / sb.where(sb != 0)).astype(float)
    if agg == "average":
        return g[m].mean() if g is not None else float(d[m].mean())
    return g[m].sum() if g is not None else float(d[m].sum())


def needed(q: dict, ctx: Ctx) -> list[str]:
    """Columns that must have a value for a row to count."""
    m, agg = q.get("measure"), q.get("agg")
    if agg == "ratio":
        return list(ctx.cols[m].ratio)
    return [m] if m and agg in ("sum", "average", "count_distinct") or (m and agg == "count") else []


def phrase(q: dict, ctx: Ctx) -> str:
    agg, m = q.get("agg"), q.get("measure")
    lab = ctx.label(m) if m else ""
    return {"count": f"number of {lab + ' values' if m else 'rows'}", "count_distinct": f"number of different {lab} values", "ratio": lab, "average": f"average {lab}", "sum": f"total {lab}"}[agg]


def vfmt(q: dict, ctx: Ctx):
    """How one calculated value of this query is written."""
    col = ctx.cols.get(q.get("measure")) if q.get("agg") in ("sum", "average", "ratio") else None
    return lambda v: fmt_value(v, col, ctx)


def cap(s: str) -> str:
    return s[:1].upper() + s[1:]


def is_additive(q: dict) -> bool:
    return q.get("agg") in ("sum", "count")


def how_op(q: dict, ctx: Ctx, tail: str = "") -> str:
    agg, m = q.get("agg"), q.get("measure")
    lab = ctx.label(m) if m else "rows"
    base = {"sum": f"Added up {lab}", "average": f"Averaged {lab}", "count": f"Counted {lab if m else 'rows'}", "count_distinct": f"Counted the different values of {lab}",
            "ratio": f"Divided the total of {ctx.label(ctx.cols[m].ratio[0])} by the total of {ctx.label(ctx.cols[m].ratio[1])}" if m and ctx.cols[m].ratio else lab}[agg]
    return base + tail


# ------------------------------------------------------------------ time
def grain_for(span_days: int) -> str:
    return "day" if span_days <= 62 else "month" if span_days <= 2200 else "quarter" if span_days <= 5500 else "year"


def plabel(p: pd.Period, grain: str) -> str:
    return {"day": str(p), "week": f"{p.start_time:%Y-%m-%d}", "month": f"{p.year}-{p.month:02d}", "quarter": f"{p.year}-Q{p.quarter}", "year": str(p.year)}[grain]


def time_series(d: pd.DataFrame, date: str, grain: str, q: dict, ctx: Ctx, keys: list[str] | None = None, notes: list[str] | None = None):
    """(grain used, Series or DataFrame by period). Missing periods are filled with 0 for totals and counts, left empty for averages and ratios."""
    span = int((d[date].max() - d[date].min()).days) + 1
    grain = grain_for(span) if grain == "auto" else grain
    while True:
        n = len(pd.period_range(d[date].min().to_period(FREQ[grain]), d[date].max().to_period(FREQ[grain]), freq=FREQ[grain]))
        if n <= MAX_POINTS or grain not in NEXT_GRAIN:
            break
        if notes is not None:
            notes.append(f"{n} {grain}s would be too many to read, so I grouped by {NEXT_GRAIN[grain]} instead.")
        grain = NEXT_GRAIN[grain]
    per = d[date].dt.to_period(FREQ[grain])
    full = pd.period_range(per.min(), per.max(), freq=FREQ[grain])
    s = agg_by(d.assign(_p=per), ["_p"] + (keys or []), q, ctx)
    fill = 0 if q["agg"] in ("sum", "count", "count_distinct") else np.nan
    if keys:
        s = s.unstack(keys[0]).reindex(full)
        return grain, (s.fillna(0) if fill == 0 else s)
    return grain, s.reindex(full, fill_value=fill) if fill == 0 else s.reindex(full)


def partial_note(d: pd.DataFrame, date: str, last: pd.Period, first: pd.Period, grain: str) -> list[str]:
    out = []
    if grain in ("week", "month", "quarter", "year"):
        if d[date].max() < last.end_time.normalize():
            out.append(f"The last {grain} ({plabel(last, grain)}) is incomplete: the data ends on {d[date].max():%Y-%m-%d}.")
        if d[date].min() > first.start_time:
            out.append(f"The first {grain} ({plabel(first, grain)}) is incomplete: the data starts on {d[date].min():%Y-%m-%d}.")
    return out


# ------------------------------------------------------------------ the operations
def _ordered(s: pd.Series, q: dict, default_desc=True) -> pd.Series:
    srt = q.get("sort") or {"by": "value", "dir": "desc" if default_desc else "asc"}
    if srt["by"] == "label":
        return s.sort_index(ascending=srt["dir"] == "asc")
    return s.sort_values(ascending=srt["dir"] == "asc", kind="mergesort")


def run_groups(df, ctx, q, ranked: bool = False) -> Result:
    """aggregate (with or without a group) and top/bottom N."""
    F = Frame(df, ctx, q)
    cols = needed(q, ctx)
    g = (q.get("group_by") or [None])[0]
    s2 = (q.get("split_by") or [None])[0]
    F.need(cols + ([g] if g else []) + ([s2] if s2 else []), "left out")
    d, V, ph = F.df, vfmt(q, ctx), phrase(q, ctx)
    R = Result(q, additive=is_additive(q))
    if not g:
        v = agg_by(d, [], q, ctx)
        if pd.isna(v):
            raise Cannot("That can't be worked out: the total it is divided by is zero.")
        R.shape, R.columns = "scalar", ["Measure", "Value"]
        R.add([cap(ph), v], [cap(ph), V(v)])
        R.sentence = f"The {ph} is {R.fig(ph, v, V(v))}" + (f" across {R.fig('rows used', len(d), fmt_num(len(d)))} rows." if q["agg"] != "count" else ".")
        R.how = F.how(how_op(q, ctx), cols)
        return R
    allv = agg_by(d, [g], q, ctx).dropna()
    total_all = float(allv.sum()) if R.additive else None
    if ranked:
        shown_s = allv.sort_values(ascending=q["direction"] == "bottom", kind="mergesort").head(q["n"])
    else:
        shown_s = _ordered(allv, q).head(q["n"])
    hidden = len(allv) - len(shown_s)
    lab_g = ctx.label(g)
    if s2:
        keep = list(shown_s.index)
        cells = agg_by(d[d[g].isin(keep)], [g, s2], q, ctx).unstack(s2)
        order = (cells.fillna(0).sum() if R.additive else d[d[g].isin(keep)][s2].value_counts()).sort_values(ascending=False).index[:MAX_SPLITS] if len(cells.columns) else []
        cells = cells.reindex(index=keep, columns=list(order))
        extra_splits = d[s2].nunique() - len(order)
        R.shape = "split"
        R.columns = [lab_g] + [str(c) for c in cells.columns] + (["Total"] if R.additive else [])
        for k in keep:
            raw = [str(k)] + [None if pd.isna(x) else float(x) for x in cells.loc[k]] + ([float(allv[k])] if R.additive else [])
            R.add(raw, [str(k)] + ["–" if x is None else V(x) for x in raw[1:1 + len(cells.columns)]] + ([V(allv[k])] if R.additive else []))
        flat = cells.stack()
        top = flat.idxmax()
        lead = shown_s.index[0]
        R.sentence = (f"{cap(ph)} by {lab_g} and {ctx.label(s2)}: {lead} is highest overall at {R.fig(ph, shown_s.iloc[0], V(shown_s.iloc[0]))}; "
                      f"the largest single combination is {top[0]} / {top[1]} at {R.fig('largest combination', flat.max(), V(flat.max()))}.")
        R.plot = {"kind": "bars", "labels": [str(k) for k in keep], "series": [{"name": str(c), "values": [None if pd.isna(x) else float(x) for x in cells[c]]} for c in cells.columns],
                  "fmt": V, "title": f"{cap(ph)} by {lab_g}, split by {ctx.label(s2)}", "stackable": R.additive}
        R.how = F.how(how_op(q, ctx, f" for each {lab_g} and {ctx.label(s2)}"), cols + [g, s2], ([f"{extra_splits} other {ctx.label(s2)} values are not shown."] if extra_splits > 0 else []), hidden)
        return R
    R.shape = "groups"
    heads = [lab_g, cap(ph)] + (["Share of total"] if R.additive else [])
    R.columns = heads
    for k, v in shown_s.items():
        share = v / total_all * 100 if total_all else None
        R.add([str(k), float(v)] + ([share] if R.additive else []), [str(k), V(v)] + ([f"{share:.1f}%"] if R.additive and share is not None else ["–"] if R.additive else []))
    names = list(shown_s.index)
    parts = []
    for i, (k, v) in enumerate(shown_s.head(3).items()):
        t = f"{k} ({R.fig(ph, v, V(v))}"
        if R.additive and total_all and i == 0:
            t += f", {R.fig('share of total', v / total_all * 100, f'{v / total_all * 100:.1f}%')} of the total"
        parts.append(t + ")")
    if ranked:
        word = "Top" if q["direction"] == "top" else "Bottom"
        R.sentence = f"{word} {lab_g if len(names) == 1 else str(len(names)) + ' ' + lab_g} by {ph}: " + ", ".join(parts) + (f", and {R.fig('more groups', len(names) - 3, fmt_num(len(names) - 3))} more." if len(names) > 3 else ".")
    else:
        R.sentence = f"{cap(ph)} by {lab_g}: " + ", ".join(parts) + (f", and {R.fig('more groups', len(names) - 3, fmt_num(len(names) - 3))} more shown." if len(names) > 3 else ".")
    R.plot = {"kind": "bars", "labels": [str(k) for k in names], "series": [{"name": cap(ph), "values": [float(v) for v in shown_s.values]}], "fmt": V, "title": f"{cap(ph)} by {lab_g}", "horizontal": True,
              "shares": ([float(v / total_all * 100) for v in shown_s.values] if total_all else None)}
    R.how = F.how(how_op(q, ctx, f" for each {lab_g}" + (", then kept the " + ("highest" if q.get("direction") != "bottom" else "lowest") + f" {q['n']}" if ranked else "")), cols + [g], None, hidden)
    return R


def run_share(df, ctx, q) -> Result:
    F = Frame(df, ctx, q)
    g = q["group_by"][0]
    F.need(needed(q, ctx) + [g], "left out")
    d, V, ph = F.df, vfmt(q, ctx), phrase(q, ctx)
    allv = agg_by(d, [g], q, ctx).dropna()
    if (allv < 0).any():
        raise Cannot(f"{cap(ph)} has negative values, so shares of the total would be misleading. Try a table of totals instead.")
    total = float(allv.sum())
    if not total:
        raise Cannot("The total is zero, so there are no shares to show.")
    srt = allv.sort_values(ascending=False, kind="mergesort")
    top = srt.head(q["n"])
    rest = float(srt.iloc[q["n"]:].sum())
    R = Result(q, shape="shares", additive=True, columns=[ctx.label(g), cap(ph), "Share of total"])
    for k, v in top.items():
        R.add([str(k), float(v), v / total * 100], [str(k), V(v), f"{v / total * 100:.1f}%"])
    if len(srt) > len(top):
        R.add(["All others", rest, rest / total * 100], [f"All others ({len(srt) - len(top)})", V(rest), f"{rest / total * 100:.1f}%"])
    lead = [f"{k} {R.fig('share', v / total * 100, f'{v / total * 100:.1f}%')}" for k, v in top.head(3).items()]
    R.sentence = (f"{top.index[0]} makes up {R.fig('share of total', top.iloc[0] / total * 100, f'{top.iloc[0] / total * 100:.1f}%')} of the {ph}"
                  + (", followed by " + " and ".join([", ".join(lead[1:-1])] + [lead[-1]] if len(lead) > 2 else lead[1:]) if len(lead) > 1 else "") + ".")
    labels = [str(k) for k in top.index] + (["All others"] if len(srt) > len(top) else [])
    vals = [float(v) for v in top.values] + ([rest] if len(srt) > len(top) else [])
    R.plot = {"kind": "share", "labels": labels, "values": vals, "fmt": V, "title": f"Share of {ph} by {ctx.label(g)}", "bar_series": {"name": cap(ph), "values": vals}}
    R.how = F.how(how_op(q, ctx, f" for each {ctx.label(g)} and divided each by the total of all groups"), needed(q, ctx) + [g], None, max(0, len(srt) - len(top)))
    return R


def run_compare(df, ctx, q) -> Result:
    F = Frame(df, ctx, q)
    by = q["by"]
    F.need(needed(q, ctx) + [by], "left out")
    d, V, ph = F.df, vfmt(q, ctx), phrase(q, ctx)
    parts = {}
    for k in ("a", "b"):
        sub = d[d[by].astype(str) == q[k]]
        parts[k] = (agg_by(sub, [], q, ctx) if len(sub) else float("nan"), len(sub))
    (va, na), (vb, nb) = parts["a"], parts["b"]
    if pd.isna(va) or pd.isna(vb):
        raise Cannot(f"One of the two groups has no rows after the filters ({q['a'] if not na else q['b']}).")
    R = Result(q, shape="pair", columns=[ctx.label(by), cap(ph), "Rows"])
    R.add([q["a"], va, na], [q["a"], V(va), fmt_num(na)])
    R.add([q["b"], vb, nb], [q["b"], V(vb), fmt_num(nb)])
    diff = va - vb
    R.add(["Difference", diff, None], ["Difference", V(diff), "–"])
    if vb:
        pc = diff / abs(vb) * 100
        R.sentence = (f"{cap(ph)}: {q['a']} is {R.fig('a', va, V(va))} against {q['b']}'s {R.fig('b', vb, V(vb))}, so {q['a']} is "
                      f"{R.fig('difference %', abs(pc), f'{abs(pc):.1f}%')} {'higher' if pc >= 0 else 'lower'}.")
    else:
        R.sentence = f"{cap(ph)}: {q['a']} is {R.fig('a', va, V(va))} against {q['b']}'s {R.fig('b', vb, V(vb))}; the second is zero, so no percentage difference can be worked out."
    R.plot = {"kind": "bars", "labels": [q["a"], q["b"]], "series": [{"name": cap(ph), "values": [float(va), float(vb)]}], "fmt": V, "title": f"{cap(ph)}: {q['a']} vs {q['b']}"}
    R.how = F.how(how_op(q, ctx, f" for {q['a']} and for {q['b']} in {ctx.label(by)}, then compared the two"), needed(q, ctx) + [by])
    return R


def run_period_change(df, ctx, q) -> Result:
    F = Frame(df, ctx, q)
    date, g = q["date"], (q.get("group_by") or [None])[0]
    F.need(needed(q, ctx) + [date] + ([g] if g else []), "left out")
    d, V, ph = F.df, vfmt(q, ctx), phrase(q, ctx)
    pa, pb = qmod.parse_period(q["a"]), qmod.parse_period(q["b"])
    wa, wb = d[(d[date] >= pa[0]) & (d[date] < pa[1])], d[(d[date] >= pb[0]) & (d[date] < pb[1])]
    if not len(wa) or not len(wb):
        raise Cannot(f"There are no rows in {q['a'] if not len(wa) else q['b']} after the filters, so the change can't be worked out.")
    notes = []
    for p, w in ((pa, wa), (pb, wb)):
        lo, hi = d[date].min(), d[date].max()
        if lo > p[0] or hi < p[1] - pd.Timedelta(days=1):
            notes.append(f"The data covers only part of {p[2]} ({lo:%Y-%m-%d} to {hi:%Y-%m-%d}), so the comparison is not like for like.")
    F.left_out.append({"reason": "outside the two periods", "rows": int(len(d) - len(wa) - len(wb))})
    F.df = pd.concat([wa, wb])
    R = Result(q, shape="change", columns=([ctx.label(g)] if g else ["Period"]) + [q["a"], q["b"], "Change", "Change %"])
    if not g:
        va, vb = agg_by(wa, [], q, ctx), agg_by(wb, [], q, ctx)
        ch = vb - va
        pc = ch / abs(va) * 100 if va else None
        R.add([cap(ph), va, vb, ch, pc], [cap(ph), V(va), V(vb), V(ch), f"{pc:+.1f}%" if pc is not None else "–"])
        sent = f"{cap(ph)} went from {R.fig('a', va, V(va))} in {q['a']} to {R.fig('b', vb, V(vb))} in {q['b']}"
        if va:
            sent += f": {'up' if ch >= 0 else 'down'} {R.fig('change', abs(ch), V(abs(ch)))} ({R.fig('change %', abs(pc), f'{abs(pc):.1f}%')})."
        else:
            sent += f": a change of {R.fig('change', ch, V(ch))} (no percentage, because the first period is zero)."
        R.plot = {"kind": "bars", "labels": [q["a"], q["b"]], "series": [{"name": cap(ph), "values": [float(va), float(vb)]}], "fmt": V, "title": f"{cap(ph)}: {q['a']} vs {q['b']}"}
        hidden = 0
    else:
        sa, sb = agg_by(wa, [g], q, ctx), agg_by(wb, [g], q, ctx)
        fill = 0 if q["agg"] in ("sum", "count", "count_distinct") else np.nan
        t = pd.concat([sa.rename("a"), sb.rename("b")], axis=1).fillna(fill).dropna()
        t["ch"] = t["b"] - t["a"]
        t = t.reindex(t["ch"].abs().sort_values(ascending=False, kind="mergesort").index)
        hidden = max(0, len(t) - q["n"])
        t = t.head(q["n"])
        for k, r in t.iterrows():
            pc = r["ch"] / abs(r["a"]) * 100 if r["a"] else None
            R.add([str(k), r["a"], r["b"], r["ch"], pc], [str(k), V(r["a"]), V(r["b"]), V(r["ch"]), f"{pc:+.1f}%" if pc is not None else "–"])
        k0 = t.index[0]
        r0 = t.iloc[0]
        sent = (f"{cap(ph)} changed most for {k0}: {'up' if r0['ch'] >= 0 else 'down'} {R.fig('change', abs(r0['ch']), V(abs(r0['ch'])))} "
                f"from {R.fig('a', r0['a'], V(r0['a']))} in {q['a']} to {R.fig('b', r0['b'], V(r0['b']))} in {q['b']}.")
        R.plot = {"kind": "bars", "labels": [str(k) for k in t.index], "series": [{"name": q["a"], "values": [float(x) for x in t["a"]]}, {"name": q["b"], "values": [float(x) for x in t["b"]]}],
                  "fmt": V, "title": f"{cap(ph)} by {ctx.label(g)}: {q['a']} vs {q['b']}"}
    R.sentence = sent
    R.how = F.how(how_op(q, ctx, f" in {q['a']} and in {q['b']}" + (f" for each {ctx.label(g)}" if g else "") + ", then took the difference"), needed(q, ctx) + [date] + ([g] if g else []), notes, hidden)
    R.how["rows_used"], R.how["rows_left_out"] = int(len(wa) + len(wb)), int(F.total - len(wa) - len(wb))
    return R


def run_trend(df, ctx, q) -> Result:
    F = Frame(df, ctx, q)
    date, s2 = q["date"], (q.get("split_by") or [None])[0]
    F.need(needed(q, ctx) + [date] + ([s2] if s2 else []), "left out")
    d, V, ph = F.df, vfmt(q, ctx), phrase(q, ctx)
    notes: list[str] = []
    grain, tot = time_series(d, date, q["grain"], q, ctx, None, notes)
    tot = tot.dropna()
    if not len(tot):
        raise Cannot("There is nothing to plot over time after the filters.")
    labels = [plabel(p, grain) for p in tot.index]
    notes += partial_note(d, date, tot.index[-1], tot.index[0], grain)
    peak, low = tot.idxmax(), tot.idxmin()
    R = Result(q, shape="time", additive=is_additive(q))
    first, last = tot.iloc[0], tot.iloc[-1]
    R.sentence = (f"{cap(ph)} by {grain}: from {R.fig('first', first, V(first))} in {labels[0]} to {R.fig('last', last, V(last))} in {labels[-1]}; "
                  f"highest in {plabel(peak, grain)} at {R.fig('peak', tot.max(), V(tot.max()))} and lowest in {plabel(low, grain)} at {R.fig('low', tot.min(), V(tot.min()))}."
                  + (" The last " + grain + " is incomplete." if any("last" in n and "incomplete" in n for n in notes) else ""))
    if s2:
        _, cells = time_series(d, date, grain, q, ctx, [s2], None)
        order = (cells.sum() if R.additive else d[s2].value_counts().reindex(cells.columns).fillna(0)).sort_values(ascending=False).index[:MAX_SPLITS]
        extra = len(cells.columns) - len(order)
        cells = cells[list(order)]
        R.shape = "time_split"
        R.columns = ["Period"] + [str(c) for c in cells.columns]
        for p, row in cells.iterrows():
            raw = [plabel(p, grain)] + [None if pd.isna(x) else float(x) for x in row]
            R.add(raw, [raw[0]] + ["–" if x is None else V(x) for x in raw[1:]])
        R.plot = {"kind": "series", "x": labels if len(cells) == len(labels) else [plabel(p, grain) for p in cells.index], "series": [{"name": str(c), "values": [None if pd.isna(x) else float(x) for x in cells[c]]} for c in cells.columns],
                  "fmt": V, "title": f"{cap(ph)} by {grain}, split by {ctx.label(s2)}", "stackable": R.additive}
        if extra > 0:
            notes.append(f"{extra} other {ctx.label(s2)} values are not shown.")
    else:
        R.columns = ["Period", cap(ph)]
        for p, v in tot.items():
            R.add([plabel(p, grain), float(v)], [plabel(p, grain), V(v)])
        R.plot = {"kind": "series", "x": labels, "series": [{"name": cap(ph), "values": [float(v) for v in tot.values]}], "fmt": V, "title": f"{cap(ph)} by {grain}", "stackable": False}
    R.how = F.how(how_op(q, ctx, f" for each {grain} of {ctx.label(date)}" + (f", split by {ctx.label(s2)}" if s2 else "")), needed(q, ctx) + [date] + ([s2] if s2 else []), notes)
    R.query = {**q, "grain": grain}
    return R


def run_distribution(df, ctx, q) -> Result:
    F = Frame(df, ctx, q)
    c = ctx.cols[q["column"]]
    d = F.need([c.name], "left out")
    R = Result(q)
    if c.role == "measure":
        v = d[c.name].astype(float)
        V = lambda x: fmt_value(x, c, ctx)
        counts, edges = np.histogram(v, bins=q["bins"])
        R.shape, R.columns = "hist", [f"{c.display} range", "Rows", "Share of rows"]
        for i, n in enumerate(counts):
            lo, hi = edges[i], edges[i + 1]
            R.add([f"{lo:.6g} to {hi:.6g}", int(n), n / len(v) * 100], [f"{V(lo)} to {V(hi)}", fmt_num(n), f"{n / len(v) * 100:.1f}%"])
        q1, med, q3 = np.percentile(v, [25, 50, 75])
        R.sentence = (f"{c.display} runs from {R.fig('min', v.min(), V(v.min()))} to {R.fig('max', v.max(), V(v.max()))}: half of the values are between {R.fig('q1', q1, V(q1))} and {R.fig('q3', q3, V(q3))}, "
                      f"the median is {R.fig('median', med, V(med))} and the average is {R.fig('mean', v.mean(), V(v.mean()))} ({R.fig('values', len(v), fmt_num(len(v)))} values).")
        R.plot = {"kind": "hist", "edges": [float(e) for e in edges], "counts": [int(n) for n in counts], "xlabel": c.display, "title": f"How {c.display} is spread", "fmt": V}
        R.how = F.how(f"Sorted every {c.display} value into {q['bins']} equal-width ranges and counted the rows in each", [c.name])
        return R
    vc = d[c.name].astype(str).value_counts()
    top = vc.head(20)
    R.shape, R.columns = "categories", [c.display, "Rows", "Share of rows"]
    for k, n in top.items():
        R.add([str(k), int(n), n / len(d) * 100], [str(k), fmt_num(n), f"{n / len(d) * 100:.1f}%"])
    R.sentence = (f"{top.index[0]} is the most common {c.display} ({R.fig('rows', top.iloc[0], fmt_num(top.iloc[0]))} rows, {R.fig('share', top.iloc[0] / len(d) * 100, f'{top.iloc[0] / len(d) * 100:.1f}%')} of the rows)"
                  + (f", out of {R.fig('different values', len(vc), fmt_num(len(vc)))} different values." if len(vc) > 1 else "."))
    R.plot = {"kind": "bars", "labels": [str(k) for k in top.index], "series": [{"name": "Rows", "values": [float(n) for n in top.values]}], "fmt": fmt_num, "title": f"Rows per {c.display}", "horizontal": True}
    R.how = F.how(f"Counted the rows for each {c.display} value", [c.name], None, max(0, len(vc) - 20))
    return R


def run_relationship(df, ctx, q) -> Result:
    F = Frame(df, ctx, q)
    x, y = ctx.cols[q["x"]], ctx.cols[q["y"]]
    d = F.need([x.name, y.name], "left out")
    if len(d) < 3 or d[x.name].nunique() < 2 or d[y.name].nunique() < 2:
        raise Cannot(f"There isn't enough variation in {x.display} and {y.display} to compare them.")
    r = float(np.corrcoef(d[x.name].astype(float), d[y.name].astype(float))[0, 1])
    a = abs(r)
    word = "have almost no straight-line relationship" if a < 0.2 else ("tend to rise together" if r > 0 else "tend to move in opposite directions")
    strength = "" if a < 0.2 else "slightly " if a < 0.4 else "fairly " if a < 0.7 else "strongly "
    R = Result(q, shape="scatter", columns=["Measure", "Value"])
    R.add(["Correlation", r], ["Correlation", f"{r:.2f}"])
    R.add(["Rows used", len(d)], ["Rows used", fmt_num(len(d))])
    R.sentence = f"{x.display} and {y.display} {strength}{word}: the correlation is {R.fig('correlation', r, f'{r:.2f}')} across {R.fig('rows', len(d), fmt_num(len(d)))} rows."
    s = d[[x.name, y.name]] if len(d) <= SCATTER_SAMPLE else d[[x.name, y.name]].sample(SCATTER_SAMPLE, random_state=0)
    R.plot = {"kind": "scatter", "x": [float(v) for v in s[x.name]], "y": [float(v) for v in s[y.name]], "xlabel": x.display, "ylabel": y.display, "title": f"{y.display} against {x.display}"}
    notes = [f"The chart shows a sample of {SCATTER_SAMPLE:,} of the {len(d):,} points; the correlation uses all of them."] if len(d) > SCATTER_SAMPLE else []
    notes.append("Correlation measures how closely two numbers move together on a scale from -1 to 1. It does not show that one causes the other.")
    R.how = F.how(f"Worked out the correlation between {x.display} and {y.display} over the rows that have both", [x.name, y.name], notes)
    return R


RUNNERS = {
    "aggregate": lambda df, ctx, q: run_groups(df, ctx, q),
    "top": lambda df, ctx, q: run_groups(df, ctx, q, ranked=True),
    "share": run_share,
    "compare": run_compare,
    "period_change": run_period_change,
    "trend": run_trend,
    "distribution": run_distribution,
    "relationship": run_relationship,
}


def resolve_chart(R: Result) -> None:
    """Pick the chart: the one asked for when it fits this result, else the sensible default with a plain note. Also fills the controls the chat offers."""
    allowed = CHART_TYPES[R.shape]
    want = R.query.get("chart", "auto")
    if R.shape == "split" and not R.additive:
        allowed = [c for c in allowed if c != "stacked_bar"]
    if R.shape in ("groups",) and not R.additive:
        allowed = [c for c in allowed if c != "share"]
    if R.shape == "time_split" and not R.additive:
        allowed = [c for c in allowed if c != "stacked_bar"]
    default = DEFAULT_CHART[R.shape] if DEFAULT_CHART[R.shape] in allowed else allowed[0]
    R.chart = want if want in allowed else default
    if want not in ("auto", R.chart) and want in qmod.CHARTS:
        why = {"line": "A line needs time along the bottom.", "stacked_bar": "A stacked bar needs the numbers split by a second column and added together.", "share": "A share chart needs totals or counts of two or more groups.",
               "histogram": "A histogram shows how one number column is spread.", "scatter": "A scatter chart needs two number columns."}.get(want, "That chart doesn't fit this result.")
        R.chart_note = f"{why} I used a {R.chart.replace('_', ' ')} instead."
    if R.chart == "share" and R.plot.get("kind") == "bars" and len(R.plot["series"]) == 1:
        R.plot = {"kind": "share", "labels": R.plot["labels"], "values": R.plot["series"][0]["values"], "fmt": R.plot["fmt"], "title": R.plot["title"], "bar_series": R.plot["series"][0]}
    R.controls = {"chart_types": allowed, "grains": list(qmod.GRAINS) if R.query["op"] == "trend" else [], "can_top_n": R.query["op"] in ("top", "aggregate", "share", "period_change") and bool(R.query.get("group_by")),
                  "chart": R.chart}


def run(q: dict, df: pd.DataFrame, ctx: Ctx) -> Result:
    R = RUNNERS[q["op"]](df, ctx, q)
    resolve_chart(R)
    if R.chart_note:
        R.how.setdefault("notes", []).append(R.chart_note)
    return R
