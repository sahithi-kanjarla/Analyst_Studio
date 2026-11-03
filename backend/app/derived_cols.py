"""Columns that are exact arithmetic results of other columns, found from the values (never from names).

A relation is {"result", "op", "inputs"}: op "product" (result = a * b, which also covers a ratio whose result is a small fraction) or "sum" (result = a + b, which also covers a difference).
Tolerance is a rounding error (cents), so a total written to two decimals still matches. Used to keep derived columns out of correlations and ideas (D), to tell a per-unit rate from a
quantity (B) and to judge a proposed derived measure (F)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

ATOL = 0.011                # a value rounded to two decimals is off by at most 0.005 per factor
RTOL = 1e-3
MATCH = 0.98                # share of the rows that must satisfy the equation
MIN_ROWS = 10
MAX_COLS = 40
SAMPLE = 3000
FRACTION = 1.5              # a factor whose values all stay within +-1.5 is a ratio/share, not an amount


def numeric_columns(df: pd.DataFrame) -> list[str]:
    out = []
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_bool_dtype(s) or not pd.api.types.is_numeric_dtype(s):
            continue
        if s.notna().sum() >= MIN_ROWS and s.nunique(dropna=True) >= 2:
            out.append(c)
    return out[:MAX_COLS]


def _close(a: np.ndarray, b: np.ndarray) -> float:
    ok = ~(np.isnan(a) | np.isnan(b))
    n = int(ok.sum())
    if n < MIN_ROWS:
        return 0.0
    return float((np.abs(a[ok] - b[ok]) <= ATOL + RTOL * np.abs(b[ok])).mean())


def is_close_to(a: pd.Series, b: pd.Series) -> bool:
    """True when two columns are equal within a rounding error on nearly every row both have."""
    return _close(pd.to_numeric(a, errors="coerce").to_numpy(float), pd.to_numeric(b, errors="coerce").to_numpy(float)) >= MATCH


_cache: dict = {}


def relations(df: pd.DataFrame) -> list[dict]:
    key = (id(df), df.shape, tuple(map(str, df.columns)))
    if key in _cache:
        return _cache[key]
    cols = numeric_columns(df)
    d = df[cols]
    if len(d) > SAMPLE:
        d = d.sample(SAMPLE, random_state=0)
    X = d.to_numpy(float)
    out: list[dict] = []
    seen: set = set()
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            for op, comb in (("product", X[:, i] * X[:, j]), ("sum", X[:, i] + X[:, j])):
                for k in range(len(cols)):
                    if k in (i, j):
                        continue
                    if _close(comb, X[:, k]) < MATCH:
                        continue
                    if _close(X[:, i], X[:, k]) >= MATCH or _close(X[:, j], X[:, k]) >= MATCH:
                        continue                                  # the other factor is 1 (or 0): the result is just a copy of one input
                    result, ins = cols[k], [cols[i], cols[j]]
                    if op == "product":
                        # a ratio is the result of a division; its value is a small fraction, so the amount it divides is the real column
                        frac = [c for c in ins if float(np.nanmax(np.abs(d[c].to_numpy(float)))) <= FRACTION]
                        if frac and float(np.nanmax(np.abs(X[:, k]))) > FRACTION:
                            part = frac[0]
                            result, ins = part, [cols[k], next(c for c in ins if c != part)]
                            op = "ratio"
                    sig = (op, result, tuple(sorted(ins)))
                    if sig not in seen:
                        seen.add(sig)
                        out.append({"result": result, "op": op, "inputs": ins})
    _cache.clear()
    _cache[key] = out
    return out


def derived_columns(df: pd.DataFrame) -> dict[str, dict]:
    """{column: its relation} for every column that is an exact function of two others. A column that is the result of one relation and an input of another keeps the first relation."""
    out: dict[str, dict] = {}
    for r in relations(df):
        out.setdefault(r["result"], r)
    return out


def related_pairs(df: pd.DataFrame) -> set[frozenset]:
    """Every pair of columns inside one relation (the result with an input, and the two inputs with each other are NOT included: two independent inputs may truly move together)."""
    pairs: set[frozenset] = set()
    derived = set(derived_columns(df))
    for r in relations(df):
        for c in r["inputs"]:
            pairs.add(frozenset((r["result"], c)))
    for a in derived:
        for b in derived:
            if a < b:
                pairs.add(frozenset((a, b)))
    return pairs


def per_unit_factors(df: pd.DataFrame, hints: dict | None = None) -> set[str]:
    """Columns that are a rate per unit: one input of a product whose other input is a count. The count is the integer-valued factor; `hints` {column: "quantity"|"count"|"rate"|"money"|"cost"} breaks a tie."""
    hints = hints or {}
    out: set[str] = set()
    for r in relations(df):
        if r["op"] != "product":
            continue
        a, b = r["inputs"]
        ints = {c: bool((df[c].dropna() % 1 == 0).all()) for c in (a, b)}
        for x, y in ((a, b), (b, a)):
            if hints.get(x) in ("quantity", "count") or (ints[x] and not ints[y] and hints.get(y) not in ("quantity", "count")):
                out.add(y)
                break
    return out


def one_to_one_groups(df: pd.DataFrame, cols: list[str]) -> list[list[str]]:
    """Groups of text/category columns that map one to one (each value of one has exactly one value of the other, and back): the same dimension written twice."""
    cols = [c for c in cols if c in df.columns]
    parent = {c: c for c in cols}

    def find(c):
        while parent[c] != c:
            parent[c] = parent[parent[c]]
            c = parent[c]
        return c

    for i, a in enumerate(cols):
        for b in cols[i + 1:]:
            both = df[[a, b]].dropna()
            if len(both) < MIN_ROWS:
                continue
            na, nb = both[a].nunique(), both[b].nunique()
            if na < 2 or na != nb or na >= len(both):
                continue
            if both.drop_duplicates().shape[0] == na:
                parent[find(a)] = find(b)
    groups: dict[str, list[str]] = {}
    for c in cols:
        groups.setdefault(find(c), []).append(c)
    return [g for g in groups.values() if len(g) > 1]


def describe(rel: dict) -> str:
    """'amount = quantity x unit_price'."""
    a, b = rel["inputs"]
    sign = {"product": "×", "sum": "+", "ratio": "÷"}[rel["op"]]
    return f"{rel['result']} = {a} {sign} {b}"


def canonical_dimensions(df: pd.DataFrame, cols: list[str]) -> dict[str, str]:
    """{column: the column that stands for its dimension} for columns that map one to one (a code and its label). The one with the longer values reads better, so it stands for the pair."""
    out: dict[str, str] = {}
    for g in one_to_one_groups(df, cols):
        best = max(g, key=lambda c: float(df[c].dropna().astype(str).str.len().mean()))
        out.update({c: best for c in g})
    return out


def spec_key(tool, params: dict) -> str:
    return json.dumps([tool, sorted((k, v) for k, v in (params or {}).items())], default=str)


def collapse_specs(specs: list[dict], canon: dict[str, str], seen: set[str]) -> tuple[list[dict], list[tuple[dict, str]]]:
    """Analyses asked of two columns that are one dimension written twice are asked once, of the canonical column. (kept specs, [(dropped spec, the column it repeats)]). `seen` holds the keys already run."""
    kept, dropped = [], []
    seen = set(seen)
    for spec in specs:
        params = {k: (canon.get(v, v) if isinstance(v, str) else v) for k, v in (spec.get("params") or {}).items()}
        key = spec_key(spec.get("tool"), params)
        if key in seen:
            repeated = [v for v in (spec.get("params") or {}).values() if isinstance(v, str) and v in canon]
            same = canon[repeated[0]] if repeated else ""
            dropped.append((spec, str(same)))
            continue
        seen.add(key)
        kept.append({**spec, "params": params})
    return kept, dropped


def pair_reason(df: pd.DataFrame, a: str, b: str) -> str:
    """'' or why a pair of columns must not be compared: one is calculated from the other, or both are calculated."""
    if frozenset((a, b)) not in related_pairs(df):
        return ""
    d = derived_columns(df)
    c = a if a in d else b
    return f"{describe(d[c])}, so a relationship between them is arithmetic, not a finding" if c in d else "they are linked by an exact calculation"
