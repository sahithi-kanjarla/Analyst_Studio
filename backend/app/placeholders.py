"""Placeholder numbers (999, 9999, -1 ...) found on the SOURCE tables, before anything is joined or summarised.

A placeholder that is joined first and found later has already been averaged into a child-table summary, so it is looked for table by table.
Two patterns count, both on numeric columns only:
  - far above:   one value that is the column's maximum (or minimum), repeated many times, and sits far outside the rest of the column
  - blank twin:  a repeated extreme or conventional value whose rows are blank in another column of the same table, while that column is filled elsewhere
                 (for an extreme value that is not a conventional number, the blank column must be a date)
A column the analysis uses gets one question (default: as missing). A column it does not use is set to missing and logged, without asking."""
from __future__ import annotations

import pandas as pd

from .signals import CONVENTIONAL_NUMERIC, MIN_ROWS, _round, signal_id

MIN_COUNT = 5
MIN_SHARE = 0.01
BLANK_TWIN = 0.8        # share of the placeholder rows that are blank in the twin column
FILLED_ELSEWHERE = 0.8  # share of the other rows that are filled in the twin column
FAR = 2.0               # distances beyond the rest's 99th percentile, in units of (99th percentile - median)


def _far_from_rest(x: pd.Series, v: float) -> bool:
    rest = x[x != v]
    if rest.empty:
        return False
    med, q99, q01 = (float(t) for t in (rest.median(), rest.quantile(0.99), rest.quantile(0.01)))
    if v > rest.max():
        spread = q99 - med
        return spread > 0 and v - q99 > FAR * spread
    if v < rest.min():
        spread = med - q01
        return spread > 0 and q01 - v > FAR * spread
    return False


def _blank_twin(df: pd.DataFrame, col: str, mask: pd.Series, dates_only: bool = False) -> str | None:
    """A column that is blank on the placeholder rows and filled on most others."""
    k, others = int(mask.sum()), len(df) - int(mask.sum())
    if k == 0 or others == 0:
        return None
    for other in df.columns:
        if other == col or (dates_only and not pd.api.types.is_datetime64_any_dtype(df[other])):
            continue
        blank = df[other].isna()
        if blank[mask].mean() >= BLANK_TWIN and 1 - blank[~mask].mean() >= FILLED_ELSEWHERE:
            return str(other)
    return None


def detect(df: pd.DataFrame) -> list[dict]:
    """One candidate per numeric column of one source table (already type-fixed): {column, value, count, share, reason, twin}."""
    out = []
    for col in df.columns:
        s = df[col]
        if not pd.api.types.is_numeric_dtype(s) or pd.api.types.is_bool_dtype(s):
            continue
        x = s.dropna()
        if len(x) < MIN_ROWS or x.nunique() < 3:        # a flag has no "far above"; a coded scale never passes the distance test below
            continue
        counts = x.value_counts()
        for v in dict.fromkeys([x.max(), x.min()]):      # a placeholder is the top or the bottom of the column, however rare it is next to the ordinary values
            k = int(counts[v])
            if k < max(MIN_COUNT, MIN_SHARE * len(x)) or k > 0.5 * len(x):
                continue
            far = _far_from_rest(x, v)
            twin = _blank_twin(df, col, s == v, dates_only=not (v in CONVENTIONAL_NUMERIC))
            if far:
                reason = "far above the rest of the column" if v == x.max() else "far below the rest of the column"
                reason += f", and {twin} is blank on those rows" if twin else ""
            elif twin:
                reason = f"lines up with blanks in {twin}"
            else:
                continue
            out.append({"column": str(col), "value": float(v), "count": int(k), "share": _round(k / len(x)), "reason": reason, "twin": twin})
            break
    return out


def scan(tables: dict[str, pd.DataFrame]) -> list[dict]:
    """Candidates for every source table, each tagged with its table."""
    return [{**c, "table": t} for t, df in tables.items() for c in detect(df)]


def candidate_id(c: dict) -> str:
    return signal_id("placeholder", [f"{c['table']}.{c['column']}", repr(c["value"])])


def _fmt(v) -> str:
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def question(c: dict, shown: str, at_source: bool) -> dict:
    """The one question for a used column. With `at_source` the value was already set to missing before the join, so the option only reports it;
    otherwise (a single table) the option is an ordinary treat_value_as_missing action."""
    n, v = c["count"], _fmt(c["value"])
    miss = ({"type": "note_only"} if at_source else {"type": "treat_value_as_missing", "column": shown, "values": [c["value"]]})
    effect = f"{n:,} values of {v} in {shown} were set to missing before the tables were joined." if at_source else f"{n:,} values of {v} in {shown} were treated as missing."
    return {"id": candidate_id(c), "kind": "placeholder", "signal": candidate_id(c), "strong": True, "columns": [shown], "default": "missing",
            "text": f"How should the value {v} in {shown} be treated?",
            "why": f"{n:,} rows hold {v} ({c['reason']}" + (f", in table {c['table']}" if at_source else "") + "); a placeholder like this distorts averages and totals.",
            "options": [{"value": "missing", "label": "As missing (default)", "action": miss, "effect": effect, "rows": n},
                        {"value": "keep", "label": "Keep as a real value", "action": {"type": "note_only"}}],
            "table": c["table"]}


def log_line(c: dict, how: str = "default", note: str = "") -> dict:
    """The cleaning-log entry for a column set to missing: 'treated N values of <value> in <col> as missing (default)'."""
    return {"step": "Placeholder values", "detail": f"[{c['table']}] treated {c['count']:,} values of {_fmt(c['value'])} in {c['column']} as missing ({how}); {c['reason']}" + (f"; {note}" if note else ""),
            "count": c["count"]}


def mask_values(tables: dict[str, pd.DataFrame], cands: list[dict]) -> dict[str, pd.DataFrame]:
    """Copies of the tables with each candidate value set to missing. Tables that are not touched are shared, not copied."""
    out = dict(tables)
    for c in cands:
        df = out[c["table"]]
        if df is tables[c["table"]]:
            df = out[c["table"]] = df.copy()
        df[c["column"]] = df[c["column"]].where(df[c["column"]] != c["value"])
    return out


def reaches_analysis(c: dict, joined: pd.DataFrame, table: str, base: str, in_model: bool) -> bool:
    """Does a column of a source table end up in the analysis table, as itself, as a prefixed copy, or inside a summary (`<table>_<column>_avg`)?"""
    if not in_model:
        return False
    col = c["column"]
    if table == base:
        return col in joined.columns
    return col in joined.columns or f"{table}_{col}" in joined.columns or any(str(j).startswith(f"{table}_{col}_") for j in joined.columns)
