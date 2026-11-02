"""Scoped, fully-logged cleaning. Nothing is silently changed; every step is recorded."""
from __future__ import annotations

import numpy as np
import pandas as pd

from .grain import count_order_amounts_once, detect as detect_grain
from .profiler import date_order_conflict, infer_date_order, normalize_strings, parse_dates, try_datetime, try_numeric


def merge_labels(df: pd.DataFrame, log: list[dict]) -> pd.DataFrame:
    """In every text column, values that differ only by case or spacing become one label (the most frequent spelling). Each merge is logged."""
    for c in df.columns:
        s = df[c]
        if pd.api.types.is_numeric_dtype(s) or pd.api.types.is_datetime64_any_dtype(s) or pd.api.types.is_bool_dtype(s):
            continue
        txt = s.dropna()
        if not len(txt) or not all(isinstance(v, str) for v in txt.head(200)):
            continue
        key = txt.str.replace(r"\s+", " ", regex=True).str.strip().str.lower()
        counts = txt.value_counts()
        mapping: dict[str, str] = {}
        for variants in txt.groupby(key).unique():
            if len(variants) > 1:
                keep = max(variants, key=lambda v: (v == " ".join(v.split()), counts[v], v))
                mapping.update({v: keep for v in variants if v != keep})
        if not mapping:
            continue
        n = int(txt.isin(list(mapping)).sum())
        df[c] = s.replace(mapping)
        ex = "; ".join(f"{v!r} → {k!r}" for v, k in list(mapping.items())[:3])
        log.append({"step": "Merge labels", "detail": f"merged {len(mapping)} spelling variants in {c} that differed only by case/spacing ({ex}); {n:,} values relabelled", "count": n})
    return df


def coerce_types(df: pd.DataFrame, profile: dict, date_order: dict | None = None) -> tuple[pd.DataFrame, list[dict]]:
    """Generic, safe fixes that do not depend on the analysis goal. `date_order` is {column: dayfirst} for the columns whose d/m/y order the user answered."""
    log: list[dict] = []
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    df = count_order_amounts_once(df, log, {c['name']: c['role'] for c in profile['columns']})
    df, n_na = normalize_strings(df, log)
    if n_na:
        log.append({"step": "Standardise missing values", "detail": "Trimmed whitespace and converted placeholders like 'N/A', 'null', '-' to real missing values", "count": n_na})
    for col in profile["columns"]:
        name, conv = col["name"], col.get("convert")
        if name not in df.columns or not conv:
            continue
        if conv == "numeric":
            conv_s = try_numeric(df[name])
            if conv_s is not None:
                was_text = int(df[name].notna().sum())
                bad = int(conv_s.isna().sum() - df[name].isna().sum())
                df[name] = conv_s
                log.append({"step": "Convert to number", "detail": f"converted {was_text:,} values in {name} (currency words/symbols, thousands separators and spaces stripped)" + (f"; {bad} unparseable values set to missing" if bad else ""), "count": int(df[name].notna().sum())})
        elif conv == "date":
            raw_dates = df[name].dropna()
            dayfirst, ambiguous = infer_date_order(raw_dates)
            conv_s = try_datetime(df[name])
            given = (date_order or {}).get(name)
            if conv_s is not None and given is not None and dayfirst is not None:
                conv_s, dayfirst, ambiguous = parse_dates(df[name], given), given, False
            if conv_s is not None:
                df[name] = conv_s
                order = {True: "day-first (dd/mm/yyyy)", False: "month-first (mm/dd/yyyy)", None: "ISO / year-first"}[dayfirst]
                order += " (your answer)" if (date_order or {}).get(name) is not None and dayfirst is not None else ""
                warn = " ALL values were ambiguous (day and month both ≤ 12), so day-first was assumed - please verify." if ambiguous else ""
                span = f" Each value was parsed on its own, so mixed formats are handled. Range: {conv_s.min():%d %b %Y} to {conv_s.max():%d %b %Y}."
                log.append({"step": "Convert to date", "detail": f"'{name}' parsed as dates, order inferred as {order}.{warn}{span}", "count": int(df[name].notna().sum())})
                d1, m1, both = date_order_conflict(raw_dates)
                if d1 and m1:
                    log.append({"step": "Dates written in two orders", "detail": f"'{name}' has {d1:,} dates that can only be day/month and {m1:,} that can only be month/day, so the file mixes the two orders. Each was read the way it can only be read; {both:,} that fit both ways were read {'day-first' if dayfirst is not False else 'month-first'} and may have day and month swapped - please check them.", "count": both})
    df = merge_labels(df, log)
    before = len(df)
    # Exact duplicates are only safely "errors" when rows could plausibly be unique (an ID, a number, a date or free text exists).
    # In a purely categorical table, identical rows are legitimate repeated observations - removing them would destroy data.
    # Runs last, so rows that only differed by formatting (a currency prefix, a label's case) count as duplicates too.
    can_identify = any(c["role"] in ("id", "numeric", "date", "text") for c in profile["columns"])
    n_dup = int(df.duplicated().sum())
    if n_dup and detect_grain(df, {c['name']: c['role'] for c in profile['columns']}):
        log.append({"step": "Kept duplicates", "detail": f"{n_dup} identical rows were KEPT: in a file with several rows per order, the same product listed twice in one order is two lines, not a repeated record", "count": n_dup})
    elif can_identify:
        df = df.drop_duplicates().reset_index(drop=True)
        if before - len(df):
            log.append({"step": "Remove duplicates", "detail": f"Dropped {before - len(df):,} exact duplicate rows", "count": before - len(df)})
    elif n_dup:
        log.append({"step": "Kept duplicates", "detail": f"{n_dup} identical rows were KEPT: with only categorical columns they are probably genuine repeated records, not errors", "count": n_dup})
    return df, log


def counted_columns(plan: dict, brief: dict | None, columns) -> set[str]:
    """Columns whose blanks must stay blank because totals and ratios are built from them: the target, the brief's KPI columns, both sides of a derived ratio, and every additive-kind column."""
    from .brief import kinds
    from .measures import measure_kind
    k = kinds(brief)
    keep = {c for c in (brief or {}).get("kpis", {}).values() if c} | {plan.get("target")}
    for d in plan.get("derived") or []:
        keep |= {d.get("a"), d.get("b")}
    keep |= {c for c in columns if measure_kind(c, overrides=k) == "additive"}
    return keep - {None}


def scoped_clean(df: pd.DataFrame, profile: dict, plan: dict, brief: dict | None = None) -> tuple[pd.DataFrame, list[dict]]:
    """Cleaning limited to the columns the plan actually needs. Columns that feed totals or ratios keep their blanks (sums skip them)."""
    log: list[dict] = []
    df = df.copy()
    roles = {c["name"]: c["role"] for c in profile["columns"]}
    relevant = [c for c in plan.get("relevant_columns", []) if c in df.columns]
    target = plan.get("target")
    counted = counted_columns(plan, brief, relevant)

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
        elif c in counted and pd.api.types.is_numeric_dtype(s):
            n_blank = int(s.isna().sum())
            log.append({"step": "Blanks left blank", "detail": f"{n_blank:,} blank values in {c} left blank, not counted in totals", "count": n_blank})
        elif role == "numeric" and pd.api.types.is_numeric_dtype(s):
            if miss <= 0.30:
                med = float(s.median())
                df[c] = s.fillna(med)
                log.append({"step": "Impute numeric", "detail": f"'{c}': {int(s.isna().sum()):,} blank values ({miss:.1%}) filled with the median ({med:.4g})", "count": int(s.isna().sum())})
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


def add_derived(df: pd.DataFrame, derived: list[dict] | None) -> tuple[pd.DataFrame, list[dict], list[str]]:
    """Columns the planner derived on its own (a ratio of two measures). Same arithmetic as the define_derived_metric action; originals are untouched and the overall ratio of sums is logged."""
    log: list[dict] = []
    added: list[str] = []
    for d in derived or []:
        if d["a"] not in df.columns or d["b"] not in df.columns or d["name"] in df.columns:
            continue
        df = df.copy() if not added else df
        x, y = pd.to_numeric(df[d["a"]], errors="coerce"), pd.to_numeric(df[d["b"]], errors="coerce")
        df[d["name"]] = x / y.where(y != 0)
        tot = y.sum()
        n = int(df[d["name"]].notna().sum())
        log.append({"step": "Derived ratio", "detail": f"Created '{d['name']}' = {d['a']} / {d['b']} for {n:,} rows" + (f"; overall ratio of sums {x.sum() / tot:.4g}" if tot else "")
                    + (f". Meaning: {d['meaning']}" if d.get("meaning") else "") + " (both measures are also analysed on their own; groups are always summed first, then divided)", "count": n})
        added.append(d["name"])
    return df, log, added


def _matches(series: pd.Series, op: str, value) -> pd.Series:
    """Boolean mask of the rows where `series <op> value` holds. Numbers compare as numbers, dates as dates, everything else as trimmed lower-case text."""
    if op == "is_null":
        return series.isna()
    if op == "not_null":
        return series.notna()
    vals = value if isinstance(value, list) else [value]
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        nums = [pd.to_numeric(v, errors="coerce") for v in vals]
        if all(pd.notna(n) for n in nums):
            x = series
            cmp = {"eq": lambda: x == nums[0], "ne": lambda: x != nums[0], "lt": lambda: x < nums[0], "le": lambda: x <= nums[0], "gt": lambda: x > nums[0],
                   "ge": lambda: x >= nums[0], "in": lambda: x.isin(nums), "not_in": lambda: ~x.isin(nums)}
            return cmp[op]().fillna(op in ("ne", "not_in"))
    if pd.api.types.is_datetime64_any_dtype(series):
        ds = [pd.to_datetime(v, errors="coerce") for v in vals]
        if all(pd.notna(d) for d in ds):
            x = series
            cmp = {"eq": lambda: x == ds[0], "ne": lambda: x != ds[0], "lt": lambda: x < ds[0], "le": lambda: x <= ds[0], "gt": lambda: x > ds[0],
                   "ge": lambda: x >= ds[0], "in": lambda: x.isin(ds), "not_in": lambda: ~x.isin(ds)}
            return cmp[op]().fillna(op in ("ne", "not_in"))
    text = series.astype(str).str.strip().str.lower().where(series.notna(), None)
    keys = [str(v).strip().lower() for v in vals]
    if op in ("lt", "le", "gt", "ge"):
        return pd.Series(False, index=series.index)
    hit = text.isin(keys) if op in ("in", "not_in") else (text == keys[0])
    return hit if op in ("eq", "in") else (~hit)


def apply_directives(df: pd.DataFrame, questions: list[dict], answers: dict) -> tuple[pd.DataFrame, list[dict], dict, list[str]]:
    """Execute the chosen action of every question (see directives.py for the closed list) on a copy of the data.
    Returns (data, log entries, effects per question id, names of the columns that were added).
    Nothing is silent: each effect is logged with the number of rows or values it touched, the original file is untouched, and an action
    that would remove every row, or whose columns are gone, is skipped and logged as such."""
    from .clarify import chosen
    df = df.copy()
    log: list[dict] = []
    effects: dict[str, dict] = {}
    added: list[str] = []
    todo = list(chosen(questions, answers))
    phases = {"treat_value_as_missing": 0, "filter_rows": 1, "handle_negatives": 1, "merge_columns": 2, "define_derived_metric": 2, "drop_column": 3, "fill_missing": 0, "set_date_order": 0, "swap_day_month": 0, "merge_labels": 0}

    def note(q, effect, rows=None, detail=None, count=None):
        effects[q["id"]] = {"rows": rows, "effect": effect}
        if detail:
            log.append({"step": "Definition applied", "detail": detail, "count": int(count if count is not None else (rows or 0))})

    def gone(q, cols):
        lost = [c for c in cols if c not in df.columns]
        if lost:
            note(q, f"Not applied: {', '.join(lost)} is no longer in the data.", None, detail=f"Skipped '{q['text']}': column {', '.join(lost)} not found", count=0)
        return bool(lost)

    for q, o, a in sorted(todo, key=lambda t: phases.get(t[2]["type"], 9)):
        t = a["type"]
        if t == "note_only":
            if o.get("effect"):      # already carried out on the source tables, before the join
                note(q, o["effect"], o.get("rows"))
            continue
        if t == "set_date_column":
            if not gone(q, [a["column"]]):
                n = int(df[a["column"]].notna().sum())
                note(q, f"Time analysis uses '{a['column']}' ({n:,} rows have a value).", n)
        elif t == "set_measures":
            if not gone(q, a["columns"]):
                n = int(pd.to_numeric(df[a["columns"][0]], errors="coerce").notna().sum())
                if not a.get("pin_main", True):
                    note(q, f"{len(a['columns'])} measures ({', '.join(a['columns'][:6])}) are each analysed as their own measure.", n)
                    continue
                also = f", analysed alongside {', '.join(a['columns'][1:])}" if len(a["columns"]) > 1 else ""
                note(q, f"'{a['columns'][0]}' is the main number{also} ({n:,} rows have a value).", n)
        elif t == "treat_value_as_missing":
            if gone(q, [a["column"]]):
                continue
            col, shown = a["column"], ", ".join(f"{v:g}" if isinstance(v, float) else str(v) for v in a["values"][:4])
            mask = _matches(df[col], "in", a["values"])
            n = int(mask.sum())
            if n:
                df[col] = df[col].where(~mask)
            note(q, f"{n:,} values ({shown}) in {col} were treated as missing.", n, detail=f"treated {n:,} values of {shown} in {col} as missing ({'default' if o['value'] == q['default'] else 'your answer'})", count=n)
        elif t == "filter_rows" or (t == "handle_negatives" and a["mode"] == "exclude"):
            if gone(q, [a["column"]]):
                continue
            col = a["column"]
            if t == "filter_rows":
                keep = _matches(df[col], a["operator"], a["value"])
                what = f"kept rows where {col} {a['operator']} {'' if a['value'] is None else a['value']}".strip()
            else:
                keep = ~(pd.to_numeric(df[col], errors="coerce") < 0)
                what = f"removed rows where {col} is negative"
            n = int((~keep).sum())
            if n and not keep.any():
                note(q, f"Not applied: it would remove every row ({what}).", 0, detail=f"Skipped '{q['text']}': it would remove every row", count=0)
                continue
            if n:
                df = df[keep].reset_index(drop=True)
            note(q, f"{n:,} rows removed ({what}).", n, detail=f"Filter: {what}; {n:,} rows removed (your definition)", count=n)
        elif t == "handle_negatives" and a["mode"] == "keep":
            if not gone(q, [a["column"]]):
                n = int((pd.to_numeric(df[a["column"]], errors="coerce") < 0).sum())
                note(q, f"Kept the {n:,} negative values in {a['column']}; they lower totals and averages.", n)
        elif t == "handle_negatives":      # net_against_column
            if gone(q, [a["column"], a["against"]]):
                continue
            x, y = pd.to_numeric(df[a["column"]], errors="coerce"), pd.to_numeric(df[a["against"]], errors="coerce").fillna(0).abs()
            df[a["name"]] = x - y
            added.append(a["name"])
            n = int((y != 0).sum())
            main = " It is the main number." if a["use_as_main"] else ""
            note(q, f"Created '{a['name']}' = {a['column']} minus {a['against']}; {n:,} rows had a non-zero {a['against']}.{main}", n,
                 detail=f"Created '{a['name']}' = '{a['column']}' - '{a['against']}' (original columns unchanged)", count=n)
        elif t == "define_derived_metric":
            if gone(q, [a["a"], a["b"]]):
                continue
            x, y = pd.to_numeric(df[a["a"]], errors="coerce"), pd.to_numeric(df[a["b"]], errors="coerce")
            if a["kind"] == "difference":
                df[a["name"]], formula, extra = x - y, f"{a['a']} - {a['b']}", ""
            elif a["kind"] == "product":
                df[a["name"]], formula, extra = x * y, f"{a['a']} x {a['b']}", " A row with either number blank gets no value and is not counted."
            else:
                df[a["name"]] = x / y.where(y != 0)
                formula, tot = f"{a['a']} / {a['b']}", y.sum()
                extra = f" Overall ratio of sums: {x.sum() / tot:.4g}." if tot else ""
            added.append(a["name"])
            n = int(df[a["name"]].notna().sum())
            note(q, f"Created '{a['name']}' = {formula} for {n:,} rows.{extra}", n, detail=f"Created '{a['name']}' = {formula} ({a['kind'].replace('_', ' ')}).{extra}", count=n)
        elif t == "fill_missing":
            if gone(q, [a["column"]]):
                continue
            n = int(df[a["column"]].isna().sum())
            df[a["column"]] = pd.to_numeric(df[a["column"]], errors="coerce").fillna(a["value"])
            note(q, f"{n:,} blank values in {a['column']} were set to {a['value']:g}.", n, detail=f"set {n:,} blank values in {a['column']} to {a['value']:g}", count=n)
        elif t == "set_date_order":
            if not gone(q, [a["column"]]):       # the dates were already read in this order when the types were fixed
                note(q, f"Day and month of the dates in {a['column']} are read {'day' if a['dayfirst'] else 'month'}-first.", None)
        elif t == "swap_day_month":
            if gone(q, [a["column"]]):
                continue
            d = pd.to_datetime(df[a["column"]], errors="coerce")
            ok = d.notna() & (d.dt.day <= 12)
            swapped = pd.to_datetime(pd.DataFrame({"year": d.dt.year, "month": d.dt.day, "day": d.dt.month}), errors="coerce")
            df[a["column"]] = d.where(~ok, swapped)
            n = int(ok.sum())
            note(q, f"Day and month were swapped in {n:,} dates of {a['column']} (dates with a day above 12 cannot be swapped and stay as they are).", n,
                 detail=f"swapped day and month in {n:,} dates of {a['column']}", count=n)
        elif t == "merge_labels":
            if gone(q, [a["column"]]):
                continue
            col, mp = a["column"], a["mapping"]
            hit = df[col].astype(str).isin(list(mp)) & df[col].notna()
            n = int(hit.sum())
            df[col] = df[col].astype(object).where(~hit, df[col].astype(str).map(mp))
            shown = "; ".join(f"{k!r} → {v!r}" for k, v in list(mp.items())[:3])
            note(q, f"{n:,} values in {col} were relabelled ({shown}).", n, detail=f"merged {len(mp)} rare labels of {col} into the common label they abbreviate ({shown}); {n:,} values relabelled ({'default' if o['value'] == q['default'] else 'your answer'})", count=n)
        elif t == "merge_columns":
            if gone(q, a["columns"]):
                continue
            parts = [df[c] for c in a["columns"]]
            if a["how"] == "sum":
                df[a["name"]] = pd.concat([pd.to_numeric(p, errors="coerce") for p in parts], axis=1).sum(axis=1, min_count=1)
            else:
                merged = parts[0]
                for p in parts[1:]:
                    merged = merged.where(merged.notna(), p)
                df[a["name"]] = merged
            added.append(a["name"])
            n = int(df[a["name"]].notna().sum())
            how = a["how"].replace("_", " ")
            note(q, f"Merged {', '.join(a['columns'])} into '{a['name']}' ({how}); the originals are left out of the analysis.", n,
                 detail=f"Merged {', '.join(a['columns'])} into '{a['name']}' ({how}); {n:,} rows have a value", count=n)
        elif t == "drop_column":
            if gone(q, [a["column"]]):
                continue
            n = int(df[a["column"]].notna().sum())
            note(q, f"'{a['column']}' is left out of the analysis ({n:,} values ignored).", n, detail=f"'{a['column']}' left out of the analysis (your definition); {n:,} values ignored", count=n)
    return df, log, effects, added
