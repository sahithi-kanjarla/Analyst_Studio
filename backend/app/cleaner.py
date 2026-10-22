"""Scoped, fully-logged cleaning. Nothing is silently changed; every step is recorded."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .profiler import infer_date_order, normalize_strings, try_datetime, try_numeric


def coerce_types(df: pd.DataFrame, profile: dict) -> tuple[pd.DataFrame, list[dict]]:
    """Generic, safe fixes that do not depend on the analysis goal."""
    log: list[dict] = []
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    df, n_na = normalize_strings(df)
    if n_na:
        log.append({"step": "Standardise missing values", "detail": "Trimmed whitespace and converted placeholders like 'N/A', 'null', '-' to real missing values", "count": n_na})
    before = len(df)
    # Exact duplicates are only safely "errors" when rows could plausibly be unique (an ID, a number, a date or free text exists).
    # In a purely categorical table, identical rows are legitimate repeated observations - removing them would destroy data.
    can_identify = any(c["role"] in ("id", "numeric", "date", "text") for c in profile["columns"])
    n_dup = int(df.duplicated().sum())
    if can_identify:
        df = df.drop_duplicates().reset_index(drop=True)
        if before - len(df):
            log.append({"step": "Remove duplicates", "detail": "Dropped exact duplicate rows", "count": before - len(df)})
    elif n_dup:
        log.append({"step": "Kept duplicates", "detail": f"{n_dup} identical rows were KEPT: with only categorical columns they are probably genuine repeated records, not errors", "count": n_dup})
    for col in profile["columns"]:
        name, conv = col["name"], col.get("convert")
        if name not in df.columns or not conv:
            continue
        if conv == "numeric":
            conv_s = try_numeric(df[name])
            if conv_s is not None:
                bad = int(conv_s.isna().sum() - df[name].isna().sum())
                df[name] = conv_s
                log.append({"step": "Convert to number", "detail": f"'{name}' stored as text (symbols/commas stripped)" + (f"; {bad} unparseable values set to missing" if bad else ""), "count": int(df[name].notna().sum())})
        elif conv == "date":
            dayfirst, ambiguous = infer_date_order(df[name].dropna())
            conv_s = try_datetime(df[name])
            if conv_s is not None:
                df[name] = conv_s
                order = {True: "day-first (dd/mm/yyyy)", False: "month-first (mm/dd/yyyy)", None: "ISO / year-first"}[dayfirst]
                warn = " ALL values were ambiguous (day and month both ≤ 12), so day-first was assumed - please verify." if ambiguous else ""
                log.append({"step": "Convert to date", "detail": f"'{name}' parsed as dates, order inferred as {order}.{warn}", "count": int(df[name].notna().sum())})
    return df, log


def scoped_clean(df: pd.DataFrame, profile: dict, plan: dict) -> tuple[pd.DataFrame, list[dict]]:
    """Cleaning limited to the columns the plan actually needs."""
    log: list[dict] = []
    df = df.copy()
    roles = {c["name"]: c["role"] for c in profile["columns"]}
    relevant = [c for c in plan.get("relevant_columns", []) if c in df.columns]
    target = plan.get("target")

    unused = [c for c in df.columns if c not in relevant]
    if unused:
        log.append({"step": "Scope to the goal", "detail": f"Columns not needed for this goal were left untouched and excluded from analysis: {', '.join(unused[:12])}{'…' if len(unused) > 12 else ''}", "count": len(unused)})

    for c in relevant:
        s = df[c]
        miss = float(s.isna().mean())
        if miss == 0:
            continue
        role = roles.get(c)
        if c == target:
            log.append({"step": "Missing target values", "detail": f"'{c}' is the target; {miss:.1%} missing rows were NOT imputed and are ignored in calculations", "count": int(s.isna().sum())})
        elif role == "numeric" and pd.api.types.is_numeric_dtype(s):
            if miss <= 0.30:
                med = float(s.median())
                df[c] = s.fillna(med)
                log.append({"step": "Impute numeric", "detail": f"'{c}': {miss:.1%} missing filled with the median ({med:.4g})", "count": int(s.isna().sum())})
            else:
                log.append({"step": "Too much missing", "detail": f"'{c}' is {miss:.0%} missing; left as-is (not imputed) and used with caution", "count": int(s.isna().sum())})
        elif role in ("categorical", "binary") and not pd.api.types.is_numeric_dtype(s):
            df[c] = s.fillna("Unknown")
            log.append({"step": "Impute category", "detail": f"'{c}': {miss:.1%} missing labelled 'Unknown'", "count": int(s.isna().sum())})
        elif role == "date":
            log.append({"step": "Missing dates", "detail": f"'{c}': {miss:.1%} rows have no date and are skipped in time-trend analysis", "count": int(s.isna().sum())})

    for c in relevant:
        if roles.get(c) == "numeric" and pd.api.types.is_numeric_dtype(df[c]):
            x = df[c].dropna()
            if len(x) < 8:
                continue
            q1, q3 = x.quantile([0.25, 0.75])
            iqr = q3 - q1
            if iqr == 0:
                continue
            n_out = int(((x < q1 - 1.5 * iqr) | (x > q3 + 1.5 * iqr)).sum())
            if n_out:
                log.append({"step": "Flag outliers", "detail": f"'{c}': {n_out} values ({n_out / len(x):.1%}) outside 1.5×IQR. Flagged, NOT removed - they may be real", "count": n_out})
    return df, log


def apply_definitions(df: pd.DataFrame, questions: list[dict], answers: dict) -> tuple[pd.DataFrame, list[dict], dict, list[str]]:
    """Apply the user's definitions to the data. Returns (data, log entries, effects per question id, new column names).
    Row filters run first, then the net-amount column. Values are never edited in place: rows are removed or a new column is added,
    and every change is logged with the number of rows it touched."""
    df = df.copy()
    log: list[dict] = []
    effects: dict[str, dict] = {}
    new_cols: list[str] = []

    def lowered(col: str) -> pd.Series:
        return df[col].astype(str).str.strip().str.lower()

    for q in questions:  # 1) row filters
        a, m = answers.get(q["id"]), q.get("meta") or {}
        if q["kind"] == "status" and m.get("column") in df.columns:
            mask = lowered(m["column"]) == str(m["value"]).strip().lower()
            n = int(mask.sum())
            if a == "exclude":
                df = df[~mask].reset_index(drop=True)
                effects[q["id"]] = {"rows": n, "effect": f"Removed {n:,} rows where {m['column']} is '{m['value']}'."}
                if n:
                    log.append({"step": "Definition applied", "detail": f"Excluded rows where '{m['column']}' is '{m['value']}' (your definition)", "count": n})
            else:
                effects[q["id"]] = {"rows": n, "effect": f"Kept the {n:,} rows where {m['column']} is '{m['value']}'."}
        elif q["kind"] == "negatives" and m.get("column") in df.columns:
            mask = pd.to_numeric(df[m["column"]], errors="coerce") < 0
            n = int(mask.sum())
            if a == "exclude":
                df = df[~mask].reset_index(drop=True)
                effects[q["id"]] = {"rows": n, "effect": f"Removed {n:,} rows with a negative {m['column']}."}
                if n:
                    log.append({"step": "Definition applied", "detail": f"Excluded rows where '{m['column']}' is negative (your definition)", "count": n})
            else:
                effects[q["id"]] = {"rows": n, "effect": f"Kept the {n:,} rows with a negative {m['column']}; they lower the totals."}

    for q in questions:  # 2) refund / return / discount columns, then the columns the plan will use
        a, m = answers.get(q["id"]), q.get("meta") or {}
        if q["kind"] == "refund" and m.get("column") in df.columns:
            col, amount, net = m["column"], m.get("amount"), m.get("net_col")
            r = pd.to_numeric(df[col], errors="coerce").fillna(0).abs()
            n = int((r != 0).sum())
            if a == "net" and amount in df.columns and net:
                df[net] = pd.to_numeric(df[amount], errors="coerce") - r
                new_cols.append(net)
                effects[q["id"]] = {"rows": n, "effect": f"Created '{net}' = {amount} minus {col}; {n:,} rows had a non-zero {col}. '{net}' is the main number."}
                log.append({"step": "Definition applied", "detail": f"Created net amount '{net}' = '{amount}' - '{col}' (original columns unchanged)", "count": n})
            elif a == "ignore":
                effects[q["id"]] = {"rows": n, "effect": f"{col} was left out of the analysis ({n:,} non-zero rows ignored)."}
                log.append({"step": "Definition applied", "detail": f"'{col}' left out of the analysis (your definition)", "count": n})
            else:
                effects[q["id"]] = {"rows": n, "effect": f"{col} is analysed on its own, not netted against the amount ({n:,} non-zero rows)."}
                log.append({"step": "Definition applied", "detail": f"'{col}' kept as a separate measure, not subtracted from the amount", "count": n})
        elif q["kind"] == "date_col" and answers.get(q["id"]) in df.columns:
            col = answers[q["id"]]
            effects[q["id"]] = {"rows": int(df[col].notna().sum()), "effect": f"Time analysis uses '{col}' ({int(df[col].notna().sum()):,} rows have a date)."}
        elif q["kind"] == "outcome" and answers.get(q["id"]) in df.columns:
            col = answers[q["id"]]
            effects[q["id"]] = {"rows": int(pd.to_numeric(df[col], errors="coerce").notna().sum()), "effect": f"'{col}' is the main number the analysis is built around."}
    return df, log, effects, new_cols
