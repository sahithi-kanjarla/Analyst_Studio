"""Neutral, data-driven ambiguity signals. They look only at values, types and distributions, never at what a column is called.

Each signal is {id, type, columns, evidence, strong}. `strong` means a wrong guess would silently change a number (so the run may pause
to ask); weak signals are only worth asking about when the run pauses anyway. The ids are a hash of type + columns so a saved
glossary answer matches the same ambiguity on the next run.

multi_measure is never asked about: the planner analyses every such column (totals, averages per record).

Types: multi_date, multi_measure, negatives, sentinel, near_sum, label_merge (a rare label that is the start of a common one), rare_labels, date_swap (day and month look swapped), high_missing, duplicate_columns, and four that need no word list either:
product_basis (two columns whose product no column already holds, found by retail_roles), flag_rows (a yes/no column), blank_values (blanks in the
count-like column) and date_order (d/m/y dates whose day and month cannot be told apart)."""
from __future__ import annotations

import hashlib
import itertools

import numpy as np
import pandas as pd

from .retail_roles import derive_product_measure, is_quantity as is_count_column
from .profiler import _DMY_FULL, infer_date_order, parse_dates

SAMPLE_ROWS = 5000
MIN_ROWS = 30
CONVENTIONAL_NUMERIC = {-1, -9, -99, -999, -9999, -99999, 99, 999, 9999, 99999, 999999}    # numbers people type in to mean "no value"
PLACEHOLDER_LABELS = {"unknown", "n/a", "na", "none", "null", "nil", "missing", "undefined", "tbd", "?", "-", "--", "not available", "not specified", "unspecified", "blank", "other/unknown"}
MAX_SIGNALS = 16


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


RARE_SHARE = 0.01        # a label under this share of the rows is rare enough to be a spelling of a common one
MAX_LABEL_VARIETY = 0.1  # a column with more distinct values than this share of its rows is not a set of labels


def label_merges(df, cols, brief=None) -> list[dict]:
    """A rare label that is a prefix (or the initial) of a more common label of the same column: the same thing written short. {rare label: common label}."""
    out = []
    for n, c in cols.items():
        if c["role"] != "categorical" or n not in df.columns or is_count_column(df[n], ((brief or {}).get("roles") or {}).get(n)):
            continue      # ids carry another role, so only categorical columns get here
        vc = df[n].dropna().astype(str).value_counts()
        total = int(vc.sum())
        if len(vc) > MAX_LABEL_VARIETY * total:
            continue      # a code-like column with many distinct values: one value starting another is chance, not a spelling
        common = vc[vc >= RARE_SHARE * total]
        pairs = {}
        for label, k in vc[vc < RARE_SHARE * total].items():
            low = label.strip().lower()
            into = [m for m, km in common.items() if km > k and len(m) > len(label) and low and m.strip().lower().startswith(low)]
            if into:
                pairs[label] = into[0]      # value_counts is sorted, so the most common one
        if pairs:
            out.append(_sig("label_merge", [n], {"merge": dict(list(pairs.items())[:8]), "n_merge": len(pairs), "rows": int(sum(vc[k] for k in pairs))}, strong=True))
    return out[:2]


def rare_labels(df, cols, brief=None) -> list[dict]:
    out = []
    n_rows = len(df)
    if n_rows < 100:
        return out
    merged = {n: {k for k in s["evidence"]["merge"]} for s in label_merges(df, cols, brief) for n in s["columns"]}
    for n, c in cols.items():
        if c["role"] != "categorical" or n not in df.columns or not 3 <= c["unique"] <= 80 or is_count_column(df[n], ((brief or {}).get("roles") or {}).get(n)):
            continue      # a count of units is never a label with rare values
        vc = df[n].dropna().astype(str).value_counts()
        rare = vc[(vc <= max(2, 0.003 * n_rows)) & ~vc.index.isin(merged.get(n, ()))]
        if len(rare) >= 2 and (vc >= 0.05 * n_rows).any():
            out.append(_sig("rare_labels", [n], {"rare": {k: int(v) for k, v in rare.head(8).items()}, "n_rare": int(len(rare)), "n_labels": int(len(vc))}))
    return out[:2]


def date_swap(df, cols) -> list[dict]:
    """Day and month that look swapped: the day of the month takes 2 to 4 values over 6 or more months, or the month takes 2 to 4 values over 6 or more years. A single repeated value (every date on the 1st) is not a swap."""
    out = []
    for n, c in cols.items():
        if c["role"] != "date" or n not in df.columns:
            continue
        d = pd.to_datetime(df[n], errors="coerce").dropna()
        if d.empty:
            continue
        days, months = int(d.dt.day.nunique()), int(d.dt.month.nunique())
        spans_months, spans_years = int(d.dt.to_period("M").nunique()), int(d.dt.year.nunique())
        if (2 <= days <= 4 and spans_months >= 6) or (2 <= months <= 4 and spans_years >= 6):
            out.append(_sig("date_swap", [n], {"days": days, "months": months, "spans_months": spans_months, "spans_years": spans_years}, strong=True))
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


# ------------------------------------------------------------------ products, flags, blanks, date order
YES_WORDS = {"y", "yes", "true", "t", "1", "1.0"}
NO_WORDS = {"n", "no", "false", "f", "0", "0.0"}

def flag_values(s: pd.Series):
    """(the value that reads as yes, its share of the non-blank rows) when the column holds exactly one yes-word and one no-word, else None. Values only."""
    vals = list(pd.unique(s.dropna()))
    if len(vals) != 2:
        return None
    key = {v: str(v).strip().lower() for v in vals}
    pos, neg = [v for v in vals if key[v] in YES_WORDS], [v for v in vals if key[v] in NO_WORDS]
    if len(pos) != 1 or len(neg) != 1:
        return None
    pos_v = pos[0].item() if hasattr(pos[0], "item") else pos[0]
    return pos_v, float((s == pos[0]).sum() / s.notna().sum())


def product_basis(df, cols, kinds=None, brief=None) -> list[dict]:
    pm = derive_product_measure(df, cols, kinds, brief)
    if pm is None or pm["basis"] == "total":
        return []
    return [_sig("product_basis", [pm["count"], pm["rate"]], {k: pm.get(k) for k in ("count", "rate", "basis", "others", "rows", "ratio", "mean_count", "name", "default")}, strong=True)]


def blank_values(df, cols, kinds=None, brief=None) -> list[dict]:
    """Blank counts only matter when a count x rate column is built, so the question is asked only when the data settled that the rate is per unit."""
    pm = derive_product_measure(df, cols, kinds, brief)
    if pm is None or pm["basis"] != "unit":
        return []
    n = int(df[pm["count"]].isna().sum())
    if n < 3 or n > 0.4 * len(df):
        return []
    return [_sig("blank_values", [pm["count"]], {"count": n, "share": _round(n / len(df)), "rate": pm["rate"]}, strong=True)]


def flag_rows(df, cols, kinds=None, brief=None) -> list[dict]:
    """A yes/no column is only worth a question when an amount can be built from the rows (retail_roles reports both roles): that is where flagged rows move the totals."""
    if derive_product_measure(df, cols, kinds, brief) is None:
        return []
    out = []
    for n, c in cols.items():
        if n not in df.columns or c["role"] not in ("binary", "categorical") or pd.api.types.is_bool_dtype(df[n]):
            continue
        fv = flag_values(df[n])
        if fv and 0.005 <= fv[1] <= 0.5:
            out.append(_sig("flag_rows", [n], {"positive": fv[0], "count": int(round(fv[1] * df[n].notna().sum())), "share": _round(fv[1])}, strong=True))
    return out[:2]


def date_order_signals(raw: pd.DataFrame, profile: dict) -> list[dict]:
    """d/m/y dates where no value has a part above 12, so day-first and month-first both read every value: the answer moves every date by up to months."""
    out = []
    for c in profile["columns"]:
        n = c["name"]
        if c.get("convert") != "date" or n not in raw.columns:
            continue
        dayfirst, ambiguous = infer_date_order(raw[n].dropna())
        if not ambiguous:
            continue
        parts = raw[n].dropna().astype(str).str.extract(_DMY_FULL).dropna().astype(int)
        if len(parts[parts[0] != parts[1]].drop_duplicates()) < 3:      # distinct dates, not rows: one date repeated has no period for the order to move
            continue
        rng = {}
        for label, first in (("day_first", True), ("month_first", False)):
            d = parse_dates(raw[n], first).dropna()
            rng[label] = [str(d.min())[:10], str(d.max())[:10]]
        out.append(_sig("date_order", [n], {"ranges": rng, "values": int(len(parts))}, strong=True))
    return out


def order_key_signal(profile: dict) -> list[dict]:
    """One question when the order key is not settled: no AI answer, or the AI's choice and the data disagree. The decision is made once (grain.decide) and carried on the profile."""
    d = (profile or {}).get("grain_decision") or {}
    if d.get("verdict") != "ask" or not d.get("candidates"):
        return []
    return [_sig("order_key", d["candidates"], {"found": d.get("found", ""), "source": d.get("source")}, strong=True)]


def compute_signals(df: pd.DataFrame, profile: dict, raw: pd.DataFrame | None = None, raw_profile: dict | None = None, kinds: dict | None = None, brief: dict | None = None) -> list[dict]:
    """Strong signals first. `df` should already have its types fixed (numbers as numbers, dates as dates). `raw` and `raw_profile` (the data as loaded and its profile)
    are only needed to see which text dates cannot be told apart; `kinds` are the brief's {column: additive|rate|other}; `brief` carries its roles and the derived measures the code accepted."""
    cols = _roles(profile)
    sigs: list[dict] = []
    jobs = [lambda: order_key_signal(profile), lambda: multi_date(df, cols), lambda: negatives(df, cols), lambda: sentinels(df, cols), lambda: multi_measure(df, cols), lambda: near_sum(df, cols),
            lambda: duplicate_columns(df, cols), lambda: label_merges(df, cols, brief), lambda: rare_labels(df, cols, brief), lambda: date_swap(df, cols), lambda: high_missing(df, cols), lambda: product_basis(df, cols, kinds, brief),
            lambda: blank_values(df, cols, kinds, brief), lambda: flag_rows(df, cols, kinds, brief)]
    if raw is not None and raw_profile is not None:
        jobs.append(lambda: date_order_signals(raw, raw_profile))
    for job in jobs:
        try:
            sigs += job()
        except Exception:      # a signal is advice, never a reason to fail a run
            continue
    ordered = {c for s in sigs if s["type"] == "date_order" for c in s["columns"]}
    sigs = [s for s in sigs if not (s["type"] == "date_swap" and set(s["columns"]) <= ordered)]      # the order question already covers that column
    sigs.sort(key=lambda s: not s["strong"])
    return sigs[:MAX_SIGNALS]
