"""Neutral, data-driven ambiguity signals. They look only at values, types and distributions, never at what a column is called.

Each signal is {id, type, columns, evidence, strong}. `strong` means a wrong guess would silently change a number (so the run may pause
to ask); weak signals are only worth asking about when the run pauses anyway. The ids are a hash of type + columns so a saved
glossary answer matches the same ambiguity on the next run.

multi_measure is never asked about: the planner analyses every such column (totals, averages per record, ratios of sums).

Types: multi_date, multi_measure, negatives, sentinel, near_sum, rare_labels, high_missing, duplicate_columns."""
from __future__ import annotations

import hashlib
import itertools

import numpy as np
import pandas as pd

SAMPLE_ROWS = 5000
MIN_ROWS = 30
CONVENTIONAL_NUMERIC = {-1, -9, -99, -999, -9999, -99999, 99, 999, 9999, 99999, 999999}    # numbers people type in to mean "no value"
PLACEHOLDER_LABELS = {"unknown", "n/a", "na", "none", "null", "nil", "missing", "undefined", "tbd", "?", "-", "--", "not available", "not specified", "unspecified", "blank", "other/unknown"}
MAX_SIGNALS = 12


def signal_id(kind: str, columns: list[str]) -> str:
    return f"{kind}:" + hashlib.sha1((kind + "|" + "|".join(sorted(columns))).encode("utf-8")).hexdigest()[:8]


def _sig(kind: str, columns: list[str], evidence: dict, strong: bool = False) -> dict:
    return {"id": signal_id(kind, columns), "type": kind, "columns": list(columns), "evidence": evidence, "strong": strong}


def _roles(profile: dict) -> dict[str, dict]:
    return {c["name"]: c for c in profile["columns"]}


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


def _round(x, nd=4):
    return None if x is None or (isinstance(x, float) and not np.isfinite(x)) else round(float(x), nd)


def _measures(df: pd.DataFrame, cols: dict[str, dict]) -> list[str]:
    return [n for n, c in cols.items() if c["role"] == "numeric" and n in df.columns and c["unique"] >= 10]


def measure_columns(df: pd.DataFrame, profile: dict) -> list[str]:
    """The numeric columns that can be measures (many distinct values). Used by the planner, which analyses all of them."""
    return _measures(df, _roles(profile))


def multi_date(df, cols) -> list[dict]:
    dates = [n for n, c in cols.items() if c["role"] == "date" and n in df.columns]
    if len(dates) < 2:
        return []
    return [_sig("multi_date", dates, {"ranges": {d: [cols[d]["stats"].get("min"), cols[d]["stats"].get("max")] for d in dates},
                                       "filled": {d: int(df[d].notna().sum()) for d in dates}}, strong=True)]


def multi_measure(df, cols) -> list[dict]:
    ms = _measures(df, cols)
    if len(ms) < 2:
        return []
    return [_sig("multi_measure", ms[:8], {"measures": {m: {k: _round(cols[m]["stats"].get(k)) for k in ("min", "median", "mean", "max")} for m in ms[:8]}})]


def negatives(df, cols) -> list[dict]:
    out = []
    for n, c in cols.items():
        if c["role"] != "numeric" or n not in df.columns:
            continue
        s = _num(df, n).dropna()
        neg = int((s < 0).sum())
        if neg >= max(3, 0.005 * len(s)) and neg / len(s) <= 0.4:   # when most values are negative the column is simply signed, not suspicious
            out.append(_sig("negatives", [n], {"count": neg, "share": _round(neg / len(s)), "min": _round(s.min()), "max": _round(s.max())}, strong=True))
    return out


def sentinels(df, cols) -> list[dict]:
    out = []
    for n, c in cols.items():
        if n not in df.columns:
            continue
        s = df[n].dropna()
        if len(s) < MIN_ROWS:
            continue
        if c["role"] == "numeric" and c["unique"] >= 15:
            x = _num(df, n).dropna()
            vc = x.value_counts()
            hits = []
            for v, k in vc.head(6).items():
                if k < max(10, 0.05 * len(x)):
                    continue
                if v in CONVENTIONAL_NUMERIC:
                    kind = "conventional"
                elif v == 0 and k >= 0.15 * len(x):
                    kind = "zero_spike"
                elif v in (x.min(), x.max()) and k >= 0.10 * len(x):
                    kind = "edge_spike"
                else:
                    continue
                hits.append({"value": _round(v, 6), "count": int(k), "share": _round(k / len(x)), "kind": kind})
            if hits:
                out.append(_sig("sentinel", [n], {"values": hits}, strong=any(h["kind"] == "conventional" for h in hits)))
        elif c["role"] in ("numeric", "categorical", "binary") and pd.api.types.is_numeric_dtype(s) and 3 <= c["unique"] <= 30:
            # a coded scale (few distinct numbers): only a conventional "no value" code sitting well outside the real range counts
            x = _num(df, n).dropna()
            hits = []
            for v, k in x.value_counts().items():
                rest = x[x != v]
                if v not in CONVENTIONAL_NUMERIC or k < max(10, 0.05 * len(x)) or rest.empty:
                    continue
                span = max(float(rest.max() - rest.min()), 1.0)
                if v > rest.max() + 0.5 * span or v < rest.min() - 0.5 * span:
                    hits.append({"value": _round(v, 6), "count": int(k), "share": _round(k / len(x)), "kind": "conventional"})
            if hits:
                out.append(_sig("sentinel", [n], {"values": hits}, strong=True))
        elif c["role"] in ("categorical", "text", "binary") and not pd.api.types.is_numeric_dtype(s):
            lab = s.astype(str).str.strip().str.lower()
            vc = lab[lab.isin(PLACEHOLDER_LABELS)].value_counts()
            hits = [{"value": v, "count": int(k), "share": _round(k / len(s))} for v, k in vc.items() if k >= max(5, 0.03 * len(s))]
            if hits:
                out.append(_sig("sentinel", [n], {"values": hits}, strong=True))
    return out


def near_sum(df, cols) -> list[dict]:
    ms = _measures(df, cols)
    ms = sorted(ms, key=lambda m: cols[m]["missing_pct"])[:10]
    if len(ms) < 3 or len(df) < MIN_ROWS:
        return []
    data = pd.DataFrame({m: _num(df, m) for m in ms})
    if len(data) > SAMPLE_ROWS:
        data = data.sample(SAMPLE_ROWS, random_state=0)
    out, seen = [], set()
    for a in ms:
        for b, c in itertools.combinations([m for m in ms if m != a], 2):
            key = frozenset((a, b, c))
            if key in seen:
                continue
            sub = data[[a, b, c]].dropna()
            if len(sub) < MIN_ROWS or (sub[c] != 0).mean() < 0.1 or (sub[b] != 0).mean() < 0.1:
                continue
            for sign, symbol in ((1, "+"), (-1, "-")):
                err = (sub[a] - (sub[b] + sign * sub[c])).abs()
                tol = 0.01 * sub[a].abs() + 1e-9
                share = float((err <= tol).mean())
                if share >= 0.97 and not ((sub[a] - sub[b]).abs() <= tol).mean() > 0.9 and not ((sub[a] - sub[c]).abs() <= tol).mean() > 0.9:
                    seen.add(key)
                    out.append(_sig("near_sum", [a, b, c], {"relation": f"{a} ~ {b} {symbol} {c}", "match_share": _round(share), "rows": int(len(sub))}))
                    break
    return out[:3]


def rare_labels(df, cols) -> list[dict]:
    out = []
    n_rows = len(df)
    if n_rows < 100:
        return out
    for n, c in cols.items():
        if c["role"] != "categorical" or n not in df.columns or not 3 <= c["unique"] <= 80:
            continue
        vc = df[n].dropna().astype(str).value_counts()
        rare = vc[vc <= max(2, 0.003 * n_rows)]
        if len(rare) >= 2 and (vc >= 0.05 * n_rows).any():
            out.append(_sig("rare_labels", [n], {"rare": {k: int(v) for k, v in rare.head(8).items()}, "n_rare": int(len(rare)), "n_labels": int(len(vc))}))
    return out[:2]


def high_missing(df, cols) -> list[dict]:
    hits = [(n, c["missing_pct"]) for n, c in cols.items() if n in df.columns and c["role"] != "empty" and c["missing_pct"] >= 40]
    return [_sig("high_missing", [n], {"missing_pct": _round(p, 1)}) for n, p in sorted(hits, key=lambda x: -x[1])[:3]]


def duplicate_columns(df, cols) -> list[dict]:
    cand = [n for n, c in cols.items() if n in df.columns and c["role"] in ("numeric", "categorical", "binary") and c["unique"] > 1][:40]
    sample = df[cand].sample(SAMPLE_ROWS, random_state=0) if len(df) > SAMPLE_ROWS else df[cand]
    out = []
    for a, b in itertools.combinations(cand, 2):
        sub = sample[[a, b]].dropna()
        if len(sub) < MIN_ROWS:
            continue
        if (sub[a].astype(str) == sub[b].astype(str)).mean() >= 0.98:
            out.append(_sig("duplicate_columns", [a, b], {"relation": "identical values", "rows": int(len(sub))}))
        elif cols[a]["role"] != "numeric" and cols[b]["role"] != "numeric" and sub.groupby(a)[b].nunique().max() == 1 and sub.groupby(b)[a].nunique().max() == 1:
            out.append(_sig("duplicate_columns", [a, b], {"relation": "one label in each column always matches one label in the other", "rows": int(len(sub))}))
    return out[:3]


def compute_signals(df: pd.DataFrame, profile: dict) -> list[dict]:
    """Strong signals first. `df` should already have its types fixed (numbers as numbers, dates as dates)."""
    cols = _roles(profile)
    sigs: list[dict] = []
    for fn in (multi_date, negatives, sentinels, multi_measure, near_sum, duplicate_columns, rare_labels, high_missing):
        try:
            sigs += fn(df, cols)
        except Exception:      # a signal is advice, never a reason to fail a run
            continue
    sigs.sort(key=lambda s: not s["strong"])
    return sigs[:MAX_SIGNALS]
