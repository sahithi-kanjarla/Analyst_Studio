"""The closed list of things Lens can calculate, and the code that checks a query against the run before anything runs.
The AI proposes a query as JSON; this module decides whether it is acceptable: the columns exist, their roles fit the operation, the values and periods exist in the data.
Whatever fails is rejected with a plain sentence. Nothing here knows a column name or a domain word."""
from __future__ import annotations

import calendar
import json
import re
from dataclasses import dataclass, field

import pandas as pd

from .context import Ctx

OPS = ("aggregate", "top", "share", "compare", "period_change", "trend", "distribution", "relationship")
AGGS = ("sum", "average", "count", "count_distinct")
GRAINS = ("day", "week", "month", "quarter", "year")
CHARTS = ("auto", "bar", "line", "stacked_bar", "share", "histogram", "scatter", "table")
FILTER_OPS = ("eq", "neq", "in", "gt", "gte", "lt", "lte", "between", "period")
MAX_FILTERS, MAX_N, DEFAULT_N = 6, 50, 5

# What the AI is told about each operation. A later stage adds its operations here and in engine.RUNNERS; nothing else changes.
OP_HELP = {
    "aggregate": '{"op":"aggregate","measure":<col>,"agg":"sum|average|count|count_distinct","group_by":[<category col>],"split_by":[<category col>],"n":<groups shown>,"sort":{"by":"value|label","dir":"asc|desc"},"filters":[...],"chart":...}  one number, or one number per group. group_by and split_by are optional. count needs no measure; count_distinct takes the column to count in "measure".',
    "top": '{"op":"top","measure":<col>,"agg":"sum|average|count|count_distinct","group_by":[<category col>],"n":<how many>,"direction":"top|bottom","filters":[...]}  the highest (top) or lowest (bottom) N groups.',
    "share": '{"op":"share","measure":<col>,"agg":"sum|count","group_by":[<category col>],"n":<groups shown>,"filters":[...]}  each group\'s share of the total.',
    "compare": '{"op":"compare","measure":<col>,"agg":"sum|average|count","by":<category col>,"a":<value>,"b":<value>,"filters":[...]}  two groups of one column side by side.',
    "period_change": '{"op":"period_change","measure":<col>,"agg":...,"date":<date col, optional>,"a":<period>,"b":<period>,"group_by":[<category col, optional>],"filters":[...]}  the change from period a to period b. A period is "2023", "2023-Q2", "2023-05" or "2023-05-14".',
    "trend": '{"op":"trend","measure":<col>,"agg":...,"date":<date col, optional>,"grain":"day|week|month|quarter|year","split_by":[<category col, optional>],"filters":[...]}  the number over time.',
    "distribution": '{"op":"distribution","column":<measure col, or category col to count its values>,"bins":<5-30, optional>,"filters":[...]}  how the values are spread.',
    "relationship": '{"op":"relationship","x":<measure col>,"y":<measure col>,"filters":[...]}  whether two measures move together.',
}
FILTER_HELP = '{"column":<col>,"op":"eq|neq|in|gt|gte|lt|lte|between|period","value":<value, list of values, or [low,high] for between>}  for a date column use op "period" with a period such as "2023-Q2", or "between" two dates "YYYY-MM-DD".'


@dataclass
class Checked:
    query: dict | None = None
    errors: list[str] = field(default_factory=list)          # plain sentences for the user
    hints: dict[str, list[str]] = field(default_factory=dict)   # column -> some of its real values, for a second try
    ambiguity: dict | None = None      # {"question": str, "choices": [{"label", "query"}]}


class Reject(Exception):
    def __init__(self, msg: str, hint_col: str | None = None):
        super().__init__(msg)
        self.msg, self.hint_col = msg, hint_col


class Ambiguous(Exception):
    def __init__(self, question: str, choices: list[dict], value: str):
        super().__init__(question)
        self.question, self.choices, self.value = question, choices, value


# ------------------------------------------------------------------ periods
_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m} | {m.lower(): i for i, m in enumerate(calendar.month_abbr) if m}


def parse_period(text) -> tuple[pd.Timestamp, pd.Timestamp, str] | None:
    """'2023' | '2023-Q2' | 'Q2 2023' | '2023-05' | 'May 2023' | '2023-05-14' -> (start, end exclusive, label). None when it is not a period."""
    t = str(text or "").strip()
    try:
        if m := re.fullmatch(r"(\d{4})", t):
            y = int(m[1])
            return pd.Timestamp(y, 1, 1), pd.Timestamp(y + 1, 1, 1), str(y)
        if m := re.fullmatch(r"(?:(\d{4})\s*-?\s*[Qq]([1-4])|[Qq]([1-4])\s*-?\s*(\d{4}))", t):
            y, q = int(m[1] or m[4]), int(m[2] or m[3])
            s = pd.Timestamp(y, 3 * q - 2, 1)
            return s, s + pd.DateOffset(months=3), f"{y}-Q{q}"
        if m := re.fullmatch(r"(\d{4})-(\d{1,2})", t):
            y, mo = int(m[1]), int(m[2])
            s = pd.Timestamp(y, mo, 1)
            return s, s + pd.DateOffset(months=1), f"{y}-{mo:02d}"
        if m := re.fullmatch(r"([A-Za-z]+)\.?\s+(\d{4})", t):
            mo = _MONTHS.get(m[1].lower())
            if mo:
                s = pd.Timestamp(int(m[2]), mo, 1)
                return s, s + pd.DateOffset(months=1), f"{int(m[2])}-{mo:02d}"
        if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", t):
            s = pd.Timestamp(int(m[1]), int(m[2]), int(m[3]))
            return s, s + pd.Timedelta(days=1), t
    except (ValueError, OverflowError):
        return None
    return None


def parse_date(text) -> pd.Timestamp | None:
    p = parse_period(text)
    return p[0] if p else None


# ------------------------------------------------------------------ values
def _norm(s) -> str:
    return " ".join(str(s).lower().split())


def resolve_value(df: pd.DataFrame, col: str, raw) -> list[str]:
    """The real value of `col` that `raw` means: an exact (case-insensitive) match, else the one value that contains it. [] when none."""
    vals = df[col].dropna().astype(str).unique()
    key = _norm(raw)
    exact = [v for v in vals if _norm(v) == key]
    if exact:
        return exact[:1]
    if len(key) >= 3:
        part = [v for v in vals if key in _norm(v)]
        if len(part) == 1:
            return part
    return []


def real_values(df: pd.DataFrame, col: str, k: int = 15) -> list[str]:
    return [str(v) for v in df[col].dropna().astype(str).value_counts().head(k).index]


def _as_list(v) -> list:
    return [x for x in v if x not in (None, "")] if isinstance(v, list) else [] if v in (None, "") else [v]


# ------------------------------------------------------------------ validation
def _role_phrase(role: str) -> str:
    return {"measure": "a number column", "category": "a category column", "flag": "a yes/no column", "date": "a date column", "id": "an identifier column", "free_text": "a free-text column"}.get(role, "not used for calculations")


def _col(ctx: Ctx, name, roles: tuple[str, ...], what: str):
    c = ctx.find(name)
    if c is None:
        raise Reject(f"I don't have a column called “{str(name)[:40]}”, so I can't use it for {what}.")
    if c.role not in roles:
        raise Reject(f"{c.display} is {_role_phrase(c.role)}, so it can't be used for {what}.")
    return c


def _filter(ctx: Ctx, df: pd.DataFrame, f: dict) -> dict:
    if not isinstance(f, dict):
        raise Reject("A filter was not written in a form I can use.")
    op = f.get("op") if f.get("op") in FILTER_OPS else ("in" if isinstance(f.get("value"), list) else "eq")
    c = ctx.find(f.get("column"))
    if c is None:
        raise Reject(f"I don't have a column called “{str(f.get('column'))[:40]}”, so I can't filter on it.")
    if c.role in ("category", "flag", "id"):
        if op not in ("eq", "neq", "in"):
            op = "in" if isinstance(f.get("value"), list) else "eq"
        vals, missing = [], []
        for raw in _as_list(f.get("value")):
            hit = resolve_value(df, c.name, raw)
            if hit:
                vals.append(hit[0])
                continue
            elsewhere = [o for o in ctx.groupable() if o.name != c.name and resolve_value(df, o.name, raw)]
            if len(elsewhere) == 1:                     # the value is a real value of exactly one other column: use that column
                o = elsewhere[0]
                v = resolve_value(df, o.name, raw)[0]
                return {"column": o.name, "op": op, "value": [v] if op == "in" else v}
            if len(elsewhere) > 1:
                raise Ambiguous(f"“{str(raw)[:40]}” appears in more than one column. Which do you mean?",
                                [{"label": f"{o.display}: {resolve_value(df, o.name, raw)[0]}", "filter": {"column": o.name, "op": op, "value": resolve_value(df, o.name, raw)[0]}} for o in elsewhere[:3]], str(raw))
            missing.append(str(raw))
        if missing or not vals:
            raise Reject(f"I couldn't find {', '.join('“' + m[:40] + '”' for m in missing) or 'that value'} in {c.display}.", c.name)
        return {"column": c.name, "op": op, "value": vals if op == "in" else vals[0]}
    if c.role == "measure":
        if op not in ("gt", "gte", "lt", "lte", "between", "eq", "neq"):
            raise Reject(f"{c.display} is a number, so it can be filtered with “more than”, “less than” or “between”.")
        try:
            if op == "between":
                lo, hi = (float(x) for x in _as_list(f.get("value"))[:2])
                return {"column": c.name, "op": op, "value": [min(lo, hi), max(lo, hi)]}
            return {"column": c.name, "op": op, "value": float(f.get("value"))}
        except (TypeError, ValueError):
            raise Reject(f"I couldn't read the number to filter {c.display} by.")
    if c.role == "date":
        vals = _as_list(f.get("value"))
        if op == "between" and len(vals) >= 2:
            lo, hi = parse_date(vals[0]), parse_date(vals[1])
            if lo is None or hi is None:
                raise Reject(f"I couldn't read those dates for {c.display}.")
            return {"column": c.name, "op": "between", "value": [str(lo.date()), str(hi.date())]}
        p = parse_period(vals[0]) if vals else None
        if p is None:
            raise Reject(f"I couldn't read “{str(vals[0] if vals else '')[:30]}” as a date or period (for example 2023, 2023-Q2 or 2023-05).")
        _in_span(ctx, c.name, p)
        return {"column": c.name, "op": "period", "value": p[2]}
    raise Reject(f"{c.display} can't be used to filter.")


def _in_span(ctx: Ctx, col: str, p) -> None:
    lo, hi = ctx.spans.get(col, (None, None))
    if lo and (p[1] <= pd.Timestamp(lo) or p[0] > pd.Timestamp(hi)):
        raise Reject(f"There is no data for {p[2]}: {ctx.label(col)} runs from {lo} to {hi}.")


def _measure(ctx: Ctx, raw: dict, out: dict, allowed=AGGS) -> None:
    agg = raw.get("agg") if raw.get("agg") in AGGS else None
    m = raw.get("measure")
    if m in (None, "", "rows", "row", "records"):
        if agg in ("sum", "average", "count_distinct"):
            raise Reject(f"Which column should I {'add up' if agg == 'sum' else 'average' if agg == 'average' else 'count the distinct values of'}?")
        out.update(agg="count", measure=None)
        return
    if agg == "count_distinct":
        c = _col(ctx, m, ("id", "category", "flag", "free_text"), "counting distinct values")
        out.update(agg=agg, measure=c.name)
        return
    c = ctx.find(m)
    if c is None:
        raise Reject(f"I don't have a column called “{str(m)[:40]}”, so I can't calculate with it.")
    if agg == "count" and c.role != "measure":
        out.update(agg="count", measure=None)
        return
    if c.role != "measure":
        raise Reject(f"{c.display} is {_role_phrase(c.role)}, so it can't be added up or averaged. Try a number column.")
    if c.ratio:
        out.update(agg="ratio", measure=c.name)
        return
    out.update(agg=agg or "sum", measure=c.name)
    if out["agg"] not in allowed:
        raise Reject(f"That calculation can't be used here. Try one of: {', '.join(allowed)}.")


def _groups(ctx: Ctx, raw: dict, key: str = "group_by") -> list[str]:
    return [_col(ctx, n, ("category", "flag", "id"), "grouping").name for n in _as_list(raw.get(key))[:2]]


def _int(v, lo, hi, default):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _date(ctx: Ctx, raw: dict) -> str:
    name = raw.get("date")
    if name in (None, ""):
        if not ctx.date_col:
            raise Reject("This data has no date column, so I can't show anything over time.")
        return ctx.date_col
    return _col(ctx, name, ("date",), "time").name


def _sort(raw: dict, default_by: str) -> dict:
    s = raw.get("sort")
    by, d = default_by, "desc" if default_by == "value" else "asc"
    if isinstance(s, dict):
        by = s.get("by") if s.get("by") in ("value", "label") else by
        d = s.get("dir") if s.get("dir") in ("asc", "desc") else d
    elif isinstance(s, str) and s.lower() in ("asc", "desc"):
        d = s.lower()
    elif isinstance(s, str) and s.lower() in ("value", "label"):
        by = s.lower()
    return {"by": by, "dir": d}


def check(raw, ctx: Ctx, df: pd.DataFrame) -> Checked:
    """Validate and normalise one query. A good query comes back with every name resolved to a real column and every value to a real value."""
    if not isinstance(raw, dict) or raw.get("op") not in OPS:
        return Checked(errors=["I couldn't turn that into one of the calculations I can run."])
    op, q = raw["op"], {"op": raw["op"]}
    ch = Checked()
    try:
        q["filters"] = [_filter(ctx, df, f) for f in _as_list(raw.get("filters"))[:MAX_FILTERS]]
        q["chart"] = raw.get("chart") if raw.get("chart") in CHARTS else "auto"
        if op == "aggregate":
            names = _as_list(raw.get("group_by"))
            if len(names) == 1 and (c := ctx.find(names[0])) is not None and c.role == "date" and not _as_list(raw.get("split_by")):
                return check({**raw, "op": "trend", "date": c.name, "group_by": None}, ctx, df)       # "revenue by order date" is a trend
        if op in ("aggregate", "top", "share", "trend", "period_change"):
            _measure(ctx, raw, q, ("sum", "count") if op == "share" else AGGS)
        if op == "compare":
            _measure(ctx, raw, q, ("sum", "average", "count", "ratio"))
        if op == "aggregate":
            g = _groups(ctx, raw)
            q["group_by"] = g[:1]
            second = (_groups(ctx, raw, "split_by") + g[1:])[:1] if g else []
            q["split_by"] = [x for x in second if x != g[0]]
            q["n"] = _int(raw.get("n"), 1, MAX_N, 10)
            q["sort"] = _sort(raw, "value")
        elif op == "top":
            g = _groups(ctx, raw)
            if not g:
                raise Reject("Which column should I rank? For example the top 5 cities.")
            q.update(group_by=g[:1], n=_int(raw.get("n"), 1, MAX_N, DEFAULT_N),
                     direction="bottom" if str(raw.get("direction")).lower() in ("bottom", "lowest", "asc") else "top")
        elif op == "share":
            g = _groups(ctx, raw)
            if not g:
                raise Reject("Which column should I split the total by?")
            q.update(group_by=g[:1], n=_int(raw.get("n"), 2, MAX_N, 8))
        elif op == "compare":
            by = raw.get("by") or (_as_list(raw.get("group_by")) or [None])[0]
            c = _col(ctx, by, ("category", "flag", "id"), "comparing")
            vals = []
            for k in ("a", "b"):
                hit = resolve_value(df, c.name, raw.get(k))
                if not hit:
                    raise Reject(f"I couldn't find {'“' + str(raw.get(k))[:40] + '”' if raw.get(k) else 'a value'} in {c.display}.", c.name)
                vals.append(hit[0])
            if vals[0] == vals[1]:
                raise Reject("Those are the same value, so there is nothing to compare. Pick two different ones.")
            q.update(by=c.name, a=vals[0], b=vals[1])
        elif op == "period_change":
            d = _date(ctx, raw)
            ps = []
            for k in ("a", "b"):
                p = parse_period(raw.get(k))
                if p is None:
                    raise Reject(f"I couldn't read “{str(raw.get(k))[:30]}” as a period (for example 2023, 2023-Q2 or 2023-05).")
                _in_span(ctx, d, p)
                ps.append(p[2])
            if ps[0] == ps[1]:
                raise Reject("Those are the same period, so there is no change to show. Pick two different ones.")
            q.update(date=d, a=ps[0], b=ps[1], group_by=_groups(ctx, raw)[:1], n=_int(raw.get("n"), 1, MAX_N, 10))
        elif op == "trend":
            grain = str(raw.get("grain") or raw.get("time_grain") or "").lower().strip()
            grain = {"daily": "day", "weekly": "week", "monthly": "month", "quarterly": "quarter", "yearly": "year", "annual": "year", "days": "day", "weeks": "week", "months": "month", "quarters": "quarter", "years": "year"}.get(grain, grain)
            q.update(date=_date(ctx, raw), grain=grain if grain in GRAINS else "auto", split_by=(_groups(ctx, raw, "split_by") or _groups(ctx, raw))[:1])
        elif op == "distribution":
            c = _col(ctx, raw.get("column") or raw.get("measure"), ("measure", "category", "flag"), "a distribution")
            if c.ratio:
                raise Reject(f"{c.display} is worked out from two totals, so it has no row-by-row spread. Try one of its parts.")
            q.update(column=c.name, bins=_int(raw.get("bins"), 5, 30, 10))
        elif op == "relationship":
            x = _col(ctx, raw.get("x"), ("measure",), "a relationship")
            y = _col(ctx, raw.get("y"), ("measure",), "a relationship")
            if x.name == y.name or x.ratio or y.ratio:
                raise Reject("A relationship needs two different number columns (not a ratio).")
            q.update(x=x.name, y=y.name)
    except Reject as e:
        ch.errors.append(e.msg)
        if e.hint_col:
            ch.hints[e.hint_col] = real_values(df, e.hint_col)
        return ch
    except Ambiguous as a:
        base = {k: v for k, v in raw.items() if k != "filters"}
        kept = [f for f in _as_list(raw.get("filters")) if not (isinstance(f, dict) and _norm(f.get("value")) == _norm(a.value))]
        ch.ambiguity = {"question": a.question, "choices": [{"label": c["label"], "query": {**base, "filters": kept + [c["filter"]]}} for c in a.choices]}
        return ch
    ch.query = q
    return ch


def signature(q: dict) -> str:
    return json.dumps(q, sort_keys=True, default=str)
