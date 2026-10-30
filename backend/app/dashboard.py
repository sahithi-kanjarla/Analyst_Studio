"""Interactive dashboard data: a compact slice of the cleaned table plus a spec saying what is worth plotting.

The browser aggregates this slice itself, so clicking a month or a segment can re-filter every visual instantly.
Only columns that matter are sent: the outcome(s), the date, and the few categories that explain the outcome best.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from . import insights, understanding as und
from .measures import is_money, measure_kind
from .tools import to_binary

MAX_ROWS = 50_000
MAX_DIMS = 4
MAX_MEASURES = 3
GOAL_TOP = 10      # a column the goal names shows its top values as bars, however many values it has
MAX_DIM_VALUES = 40


def _eta_squared(y: pd.Series, g: pd.Series) -> float:
    d = pd.DataFrame({"y": y, "g": g}).dropna()
    if len(d) < 5 or d["g"].nunique() < 2:
        return 0.0
    tot = ((d["y"] - d["y"].mean()) ** 2).sum()
    if tot <= 0:
        return 0.0
    grp = d.groupby("g")["y"].agg(["mean", "count"])
    return float((grp["count"] * (grp["mean"] - d["y"].mean()) ** 2).sum() / tot)


def _measure(df: pd.DataFrame, col: str, currency: str, kinds: dict | None = None, ratios: dict | None = None):
    spec = (ratios or {}).get(col)
    if spec is not None and spec["b"] in df.columns:      # a derived ratio: the page weights each row by its denominator, so groups show sum A / sum B, never a mean of row ratios
        x = pd.to_numeric(df[col], errors="coerce")
        return x, {"col": col, "label": col, "agg": "ratio", "den": f"__w_{col}", "den_col": spec["b"], "fmt": "pct" if spec.get("part_of") else "num",
                   "note": f"total {spec['a']} over total {spec['b']}"}
    s = df[col]
    b, pos = to_binary(s) if s.dropna().nunique() == 2 else (None, None)
    if b is not None:
        return b, {"col": col, "label": f"{col} rate", "agg": "avg", "fmt": "pct", "note": f"share of rows where it is '{pos}'"}
    x = pd.to_numeric(s, errors="coerce")
    if x.notna().sum() == 0:
        return None, None
    additive = measure_kind(col, overrides=kinds) == "additive"
    money = is_money(col, narrow=True) and bool(currency)
    return x, {"col": col, "label": col if additive else f"Average {col}", "agg": "sum" if additive else "avg",
               "fmt": "money" if money else "num", "note": ""}


def build_dashboard(wd: Path, result: dict) -> dict:
    df = pd.read_pickle(wd / "clean.pkl")
    plan, prof = result.get("plan") or {}, result.get("profile") or {}
    roles = {c["name"]: c["role"] for c in prof.get("columns", [])}
    currency = (result.get("context") or {}).get("currency", "")
    relevant = [c for c in (plan.get("relevant_columns") or list(df.columns)) if c in df.columns]
    target = plan.get("target") if plan.get("target") in df.columns else None

    # --- measures: the outcome first, then up to two other numeric columns that are not ids
    cols, measures = {}, []
    cands = ([target] if target else []) + [c for c in relevant if c != target and roles.get(c) == "numeric"]
    for c in cands:
        if len(measures) >= MAX_MEASURES:
            break
        vals, spec = _measure(df, c, currency, (result.get("brief") or {}).get("kinds"), {d["name"]: d for d in plan.get("derived") or []})
        if spec is not None:
            cols[c] = vals
            measures.append(spec)

    # --- date column
    date = plan.get("date_col") if plan.get("date_col") in df.columns else None
    if date and not (pd.api.types.is_datetime64_any_dtype(df[date]) and df[date].notna().sum() > 1):
        date = None

    # --- dimensions: only categories that help explain the outcome, best first. A column the goal names (per the data understanding record) always gets a chart
    # (its top GOAL_TOP values when it has many); a column the record calls a measure is never a grouping.
    rec = result.get("understanding")
    quantities, asked = und.measure_columns(rec), und.goal_columns(rec)
    cats = und.categories(rec)
    dims = []
    for c in dict.fromkeys(relevant + [a for a in asked if a in df.columns]):
        if c == target or c in quantities or c not in df.columns:
            continue
        goal = c in asked and (roles.get(c) in ("categorical", "binary") or c in cats)
        if not goal and roles.get(c) not in ("categorical", "binary"):
            continue
        n = int(df[c].nunique(dropna=True))
        if n < 2 or (n > MAX_DIM_VALUES and not goal):
            continue
        eta = _eta_squared(cols[measures[0]["col"]], df[c].astype(str)) if measures else 0.0
        dims.append({"col": c, "unique": n, "score": eta, **({"top": GOAL_TOP, "goal": True} if goal else {})})
    dims.sort(key=lambda d: (-d["score"], d["unique"]))
    keep = [d for d in dims if d.get("goal")][:MAX_DIMS]
    keep += [d for d in dims if not d.get("goal") and d["score"] >= 0.02][:MAX_DIMS - len(keep)]
    for d in dims:  # always show at least two so there is something to slice by
        if len(keep) >= min(2, len(dims)):
            break
        if d not in keep:
            keep.append(d)
    dims = keep

    # --- rows
    out = pd.DataFrame(index=df.index)
    for d in dims:
        out[d["col"]] = df[d["col"]].astype(str).where(df[d["col"]].notna())
    for m in measures:
        out[m["col"]] = cols[m["col"]].round(4)
        if m.get("den"):
            out[m["den"]] = pd.to_numeric(df[m["den_col"]], errors="coerce").round(4)
    if date:
        out["__date"] = df[date].dt.strftime("%Y-%m-%d")
    total = int(len(out))
    if total > MAX_ROWS:
        out = out.sample(MAX_ROWS, random_state=0).sort_index()
    out = out.astype(object).where(out.notna(), None)
    columns = list(out.columns)
    rows = [[(v.item() if hasattr(v, "item") else v) for v in r] for r in out.itertuples(index=False, name=None)]

    return {"title": (result.get("context") or {}).get("company") or "", "objective": plan.get("objective", ""),
            "currency": currency, "date": "__date" if date else None, "date_label": date, "date_coverage": insights.coverage_fraction(df[date]) if date else None,
            "measures": [{k: v for k, v in m.items() if k != "den_col"} for m in measures], "dims": [{"col": d["col"], "unique": d["unique"], "score": round(d["score"], 3), **({"top": d["top"]} if d.get("top") else {})} for d in dims],
            "columns": columns, "rows": rows, "total_rows": total, "sampled": total > MAX_ROWS}


def list_sources(wd: Path) -> list[dict]:
    """Every table the user can inspect: each uploaded table as it came in, the joined table (if several), and the cleaned table."""
    out = []
    tbls = sorted(wd.glob("tbl_*.pkl"))
    for p in tbls:
        name = p.stem[4:]
        out.append({"id": f"tbl:{name}", "label": f"{name} (original)", "kind": "original", "rows": int(len(pd.read_pickle(p)))})
    if (wd / "raw.pkl").exists():
        n = int(len(pd.read_pickle(wd / "raw.pkl")))
        out.append({"id": "raw", "label": "Joined table (before cleaning)" if tbls else "Original (before cleaning)", "kind": "joined" if tbls else "original", "rows": n})
    if (wd / "clean.pkl").exists():
        out.append({"id": "clean", "label": "Cleaned (used for analysis)", "kind": "cleaned", "rows": int(len(pd.read_pickle(wd / "clean.pkl")))})
    # cleaned first, then originals
    order = {"cleaned": 0, "joined": 1, "original": 2}
    return sorted(out, key=lambda s: order[s["kind"]])


def valid_source(source: str) -> bool:
    return source in ("clean", "raw") or (source.startswith("tbl:") and re.fullmatch(r"[A-Za-z0-9_\-]+", source[4:]) is not None)


def source_path(wd: Path, source: str) -> Path | None:
    if not valid_source(source):
        return None
    p = wd / (f"{source}.pkl" if source in ("clean", "raw") else f"tbl_{source[4:]}.pkl")
    return p if p.exists() else None
