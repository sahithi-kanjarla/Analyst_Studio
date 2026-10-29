"""AI-led data concerns. The model reads a compact FACT SHEET about each column (no raw rows) and proposes concerns; code verifies each one against that
same fact sheet and the real data; only verified concerns become clarifying questions. Nothing here knows what a column means: no column names, no domain words.

    fact_sheet(df, profile, goal) --> ask_concerns(llm, sheet, goal) --> verify(concerns, sheet, df, profile, goal) --> (questions, rejections, gap notes)

A concern is {column, concern_type, values, proposed_action, confidence, reason}. concern_type comes from a closed list; the action must be one the toolbox
(directives.py) already has and must fit the type. A type with no action yet (number_is_category, other) becomes a data-gap note. A concern is rejected when
the column does not exist, a cited value is not in that column's fact sheet, the action is not allowed for the type, or its parameters fail directives.validate_action.
Every rejection is kept with its reason ("AI suggestion not applied: ..."). The model is only ever asked to propose; the question defaults leave the data as
it is, except a label merge, whose default is to merge (like the rule version)."""
from __future__ import annotations

import json
import re

import pandas as pd

from . import directives as dv
from .llm import ask_json, last_error
from .signals import _sig

CONCERN_TYPES = ("label_variants", "date_order", "missing_code", "number_is_category", "other")
# the one action each concern type may propose; a type missing here has no action yet and becomes a data-gap note
TYPE_ACTION = {"label_variants": "merge_labels", "date_order": "swap_day_month", "missing_code": "treat_value_as_missing"}
TYPE_SIGNAL = {"label_variants": "label_merge", "date_order": "date_swap", "missing_code": "sentinel"}      # the rule signal kind whose id (and answer rules) the question shares
RULE_SIGNALS = {"label_merge", "date_swap", "rare_labels"}      # rule signals that only run when the AI part did not
TOP_N, RARE_N, HIGH_CARD, VALUE_CHARS = 15, 15, 100, 40
FACT_MAX_CHARS = 24000        # the whole fact sheet, serialised
MAX_CONCERNS = 20             # concerns read from one reply


def _py(v):
    """A JSON-safe plain value."""
    if hasattr(v, "item"):
        v = v.item()
    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:
        return int(v)
    return v if isinstance(v, (int, float, bool)) else str(v)[:VALUE_CHARS]


def norm(v) -> str:
    """The key two spellings of the same cited value share: numbers by value, text trimmed."""
    if isinstance(v, bool):
        return str(v)
    try:
        return f"{float(v):g}"
    except (TypeError, ValueError):
        return str(v).strip()


def _counts(vc: pd.Series) -> list[list]:
    return [[_py(k), int(n)] for k, n in vc.items()]


def _column_record(s: pd.Series, role: str, compact: bool = False) -> dict:
    n = len(s)
    miss = int(s.isna().sum())
    rec = {"dtype": str(s.dtype), "role": role, "distinct": int(s.nunique(dropna=True)), "missing": miss}
    live = s.dropna()
    if live.empty:
        return rec
    if role == "date" or pd.api.types.is_datetime64_any_dtype(s):
        d = pd.to_datetime(live, errors="coerce").dropna()
        if not d.empty:
            rec["range"] = [d.min().date().isoformat(), d.max().date().isoformat()]
            rec["distinct_days"], rec["distinct_months"], rec["distinct_years"] = int(d.dt.day.nunique()), int(d.dt.month.nunique()), int(d.dt.year.nunique())
            rec["distinct_months_overall"] = int(d.dt.to_period("M").nunique())
        return rec
    numeric = pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)
    if numeric:
        q = live.quantile([0, .25, .5, .75, 1])
        rec["min"], rec["q1"], rec["median"], rec["q3"], rec["max"] = (_py(x) for x in q.values)
    if compact:
        return rec
    if rec["distinct"] > HIGH_CARD:            # an id, a free-text field or a continuous measure: stats only
        if numeric:
            vc = live.value_counts()
            rec["mode"], rec["mode_count"] = _py(vc.index[0]), int(vc.iloc[0])
        return rec
    vc = live.astype(str).str.strip().value_counts() if not numeric else live.value_counts()
    rec["top"] = _counts(vc.head(TOP_N))
    if len(vc) > TOP_N:
        rec["rarest"] = _counts(vc.iloc[::-1].head(min(RARE_N, len(vc) - TOP_N)))      # least common first; never repeats a value already in `top`
    return rec


def fact_sheet(df: pd.DataFrame, profile: dict, goal: str = "") -> dict:
    """{"rows", "columns": {name: record}, "omitted": [names]}: small, bounded, no raw rows. Columns are kept most important first: the ones the goal names,
    dates, low-cardinality text, numbers, then high-cardinality columns; a column that no longer fits gets stats only, then is omitted by name."""
    from .clarify import named_in_goal
    roles = {c["name"]: c["role"] for c in profile["columns"]}
    names = [c for c in df.columns if c in roles]

    def importance(c):
        card = df[c].nunique(dropna=True)
        return (0 if goal and named_in_goal(c, goal) else 1, 0 if roles[c] == "date" else 1 if card <= HIGH_CARD else 2, 0 if roles[c] == "categorical" else 1)

    order = sorted(names, key=importance)       # sorted() is stable: equal importance keeps the table's column order
    sheet = {"rows": int(len(df)), "columns": {}, "omitted": []}
    used = len(json.dumps(sheet))
    for c in order:
        for compact in (False, True):
            rec = _column_record(df[c], roles[c], compact)
            cost = len(json.dumps({c: rec}, ensure_ascii=False)) + 2
            if used + cost <= FACT_MAX_CHARS - 40 * (len(order) - len(sheet["columns"])):       # keep room to name every column left out
                sheet["columns"][c] = rec
                used += cost
                break
        else:
            sheet["omitted"].append(c)
    return sheet


def listed_values(sheet: dict) -> dict[str, dict[str, object]]:
    """{column: {norm(value): value as listed}} for every value the fact sheet shows. The only values a concern may cite."""
    out = {}
    for c, rec in sheet["columns"].items():
        vals = [v for v, _ in (rec.get("top") or []) + (rec.get("rarest") or [])] + ([rec["mode"]] if "mode" in rec else [])
        out[c] = {norm(v): v for v in vals}
    return out


# ---------------------------------------------------------------- the AI call

SYSTEM = f"""You are the data-quality reviewer of a data-analysis team. You get a FACT SHEET about each column of a table (type, distinct and missing counts, the most and least common values with counts,
quartiles of numbers, the range of dates) and the user's goal. You never see raw rows. Propose concerns: things in the data that would make a number mean something other than what it looks like.

concern_type is exactly one of:
- "label_variants": several values of one column are the same thing written differently (spelling, case, abbreviation, a typo). Cite them in "values".
- "date_order": the day and month of a date column look swapped (very few distinct days or months across a long range).
- "missing_code": a value that stands for "no value" (a repeated filler or code) rather than a real value. Cite it in "values".
- "number_is_category": a numeric column is really a code or category, not a quantity.
- "other": anything else the sheet shows that a person should know about.
Only use values that appear in the fact sheet, spelled exactly as listed. Only name columns that exist. Do not invent concerns; an empty list is a good answer.

proposed_action is chosen ONLY from this closed list (code checks it; anything else is discarded):
- label_variants -> {{"type":"merge_labels","column":C,"mapping":{{"variant":"common label",...}}}}  (every variant and target must be listed values of C)
- date_order -> {{"type":"swap_day_month","column":C}}
- missing_code -> {{"type":"treat_value_as_missing","column":C,"values":[V,...]}}
- number_is_category and other -> null (no action exists yet; it is reported as a note)

Reply with JSON: {{"concerns": [{{"column": str, "concern_type": str, "values": [the values involved], "proposed_action": object or null, "confidence": "high"|"medium"|"low", "reason": one short line}}]}}
At most {MAX_CONCERNS} concerns, the ones that matter most for the goal first."""


def ask_concerns(llm, sheet: dict, goal: str) -> tuple[list | None, str]:
    """(concerns as the model wrote them, "") or (None, why the AI part did not work)."""
    if llm is None:
        return None, "no AI configured"
    out = ask_json(llm, SYSTEM, f"GOAL: {goal}\n\nFACT SHEET ({sheet['rows']} rows):\n{json.dumps(sheet, ensure_ascii=False, default=str)}")
    if not isinstance(out, dict) or not isinstance(out.get("concerns"), list):
        return None, last_error() or ("the reply had no 'concerns' list" if isinstance(out, dict) else "no usable reply")
    return out["concerns"][:MAX_CONCERNS], ""


# ---------------------------------------------------------------- verification

def _shown(vals: list, k: int = 3) -> str:
    return ", ".join(f"'{v}'" for v in vals[:k]) + (f" and {len(vals) - k} more" if len(vals) > k else "")


def _reject(c, why: str) -> dict:
    col = c.get("column") if isinstance(c, dict) else None
    kind = c.get("concern_type") if isinstance(c, dict) else None
    return {"column": col if isinstance(col, str) else None, "concern_type": kind if isinstance(kind, str) else None,
            "reason": why, "line": f"AI suggestion not applied: {kind or 'concern'} on '{col}': {why}"}


def _check(c, sheet: dict, listed: dict, df: pd.DataFrame, roles: dict) -> tuple[dict | None, str]:
    """(verified concern {column, concern_type, values, action, rows, ...} or None, reason it was rejected)."""
    if not isinstance(c, dict):
        return None, "not an object"
    col, kind = c.get("column"), c.get("concern_type")
    if not isinstance(col, str) or col not in df.columns:
        return None, "the column does not exist"
    if kind not in CONCERN_TYPES:
        return None, f"concern type '{kind}' is not in the allowed list"
    if col not in sheet["columns"]:
        return None, "the column was not in the fact sheet"
    vals = c.get("values") if isinstance(c.get("values"), list) else []
    base = {"column": col, "concern_type": kind, "confidence": str(c.get("confidence") or "")[:10], "reason": re.sub(r"\s+", " ", str(c.get("reason") or "")).strip()[:200]}
    act = c.get("proposed_action")
    if kind not in TYPE_ACTION:
        return {**base, "values": [], "action": None, "rows": int(df[col].notna().sum())}, ""
    if not isinstance(act, dict) or act.get("type") not in dv.ACTION_TYPES:
        return None, f"action '{act.get('type') if isinstance(act, dict) else act}' is not in the toolbox"
    if act["type"] != TYPE_ACTION[kind]:
        return None, f"action '{act['type']}' does not fit a {kind} concern"
    if act.get("column", col) != col:
        return None, "the action is about a different column"
    known = listed[col]

    def canon(v):
        return known.get(norm(v)) if isinstance(v, (str, int, float)) and not isinstance(v, bool) else None

    for v in vals:
        if canon(v) is None:
            return None, f"the value '{v}' is not in the {col} fact sheet"
    if kind == "label_variants":
        m = act.get("mapping")
        if not isinstance(m, dict) or not m:
            return None, "the merge has no mapping"
        mapping = {}
        for k, v in m.items():
            ck, cv = canon(k), canon(v)
            if ck is None or cv is None:
                return None, f"the label '{k if ck is None else v}' is not in the {col} fact sheet"
            if str(ck) != str(cv):
                mapping[str(ck)] = str(cv)
        if not mapping:
            return None, "the merge changes nothing"
        action, why = dv.validate_action({"type": "merge_labels", "column": col, "mapping": mapping}, roles)
        shown_vals = list(mapping) + [v for v in dict.fromkeys(mapping.values()) if v not in mapping]
        rows = int(df[col].astype(str).str.strip().isin(list(mapping)).sum())
    elif kind == "missing_code":
        cited = [canon(v) for v in (act.get("values") if isinstance(act.get("values"), list) else vals)]
        if not cited or any(v is None for v in cited):
            return None, "no value to treat as missing, or one that is not in the fact sheet"
        action, why = dv.validate_action({"type": "treat_value_as_missing", "column": col, "values": cited}, roles)
        shown_vals = cited
        s = df[col]
        rows = int((s.astype(str).str.strip().map(norm).isin({norm(v) for v in cited})).sum())
    else:
        if roles.get(col) != "date":
            return None, "the column is not a date column"
        action, why = dv.validate_action({"type": "swap_day_month", "column": col}, roles)
        d = pd.to_datetime(df[col], errors="coerce").dropna()
        rows = int((d.dt.day <= 12).sum())
        if action and not rows:
            return None, "no date has a day of 12 or less, so nothing can be swapped"
        shown_vals = []
    if action is None:
        return None, why
    return {**base, "values": shown_vals, "action": action, "rows": rows}, ""


def _question(v: dict, goal: str) -> dict:
    """The clarify question for a verified concern (checked once more by clarify.validate_all)."""
    col, kind, act, rows = v["column"], v["concern_type"], v["action"], v["rows"]
    why = (f"Suggested by the AI: {v['reason']} " if v["reason"] else "Suggested by the AI. ") + f"{rows:,} rows are affected."
    if kind == "label_variants":
        mp = act["mapping"]
        shown = _shown([f"'{k}' → '{t}'" for k, t in mp.items()], 3)
        return {"text": f"The AI thinks some labels in {col} are the same thing written differently ({shown}). Merge them?", "why": why, "default": "apply",
                "options": [{"value": "apply", "label": f"Apply the AI's suggestion: merge {_shown(list(mp), 2)} into the common label", "action": act},
                            {"value": "keep", "label": "Keep as is: leave the labels separate", "action": {"type": "note_only"}}]}
    if kind == "date_order":
        return {"text": f"The AI thinks day and month may be swapped in {col}. Swap them?", "why": why, "default": "keep",
                "options": [{"value": "keep", "label": "Keep as is: no swap", "action": {"type": "note_only"}},
                            {"value": "apply", "label": f"Apply the AI's suggestion: swap day and month in {col}", "action": act}]}
    return {"text": f"The AI thinks {_shown(act['values'], 3)} in {col} stand for 'no value' rather than a real value. Treat as missing?", "why": why, "default": "keep",
            "options": [{"value": "keep", "label": "Keep as is: a real value", "action": {"type": "note_only"}},
                        {"value": "apply", "label": "Apply the AI's suggestion: treat as missing", "action": act}]}


def verify(concerns: list, sheet: dict, df: pd.DataFrame, profile: dict, goal: str) -> dict:
    """{"accepted": [verified concerns], "questions": [clarify questions], "gaps": [data-gap notes], "rejected": [{column, concern_type, reason, line}]}."""
    from . import clarify
    roles = {c["name"]: c["role"] for c in profile["columns"]}
    listed = listed_values(sheet)
    accepted, rejected, gaps, raw, sigs, seen = [], [], [], [], [], set()
    for c in concerns:
        v, why = _check(c, sheet, listed, df, roles)
        key = (v["column"], v["concern_type"]) if v else None
        if v and key in seen:
            v, why = None, "the same concern was already proposed"
        if v is None:
            rejected.append(_reject(c, why))
            continue
        seen.add(key)
        if v["action"] is None:
            gaps.append({"severity": "info", "column": v["column"], "concern_type": v["concern_type"],
                         "title": f"The AI flagged {v['column']}" + (" as a number that may be a category" if v["concern_type"] == "number_is_category" else ""),
                         "detail": v["reason"] or "The AI noticed something in this column that the app cannot act on yet.",
                         "suggestion": "No automatic fix exists for this yet; check the column before relying on it."})
            accepted.append(v)
            continue
        sig = _sig(TYPE_SIGNAL[v["concern_type"]], [v["column"]], {"ai": True}, strong=True)
        q = _question(v, goal)
        raw.append({**q, "signal": sig["id"], "columns": [v["column"]]})
        sigs.append(sig)
        accepted.append(v)
    questions, _ = clarify.validate_all(raw, profile, sigs, limit=len(raw) or 1, keep_values=True)
    ok_ids = {q["id"] for q in questions}
    by_id = {s["id"]: v for s, v in zip(sigs, [a for a in accepted if a["action"] is not None])}
    for q in questions:
        v = by_id[q["id"]]
        q["ai"], q["confidence"], q["rows"], q["relevant"] = True, v["confidence"], v["rows"], clarify.named_in_goal(v["column"], goal)
        for o in q["options"]:
            if o["value"] == "apply":
                o["rows"] = v["rows"]
    for sid, v in by_id.items():
        if sid not in ok_ids:
            accepted.remove(v)
            rejected.append(_reject(v, "the question failed the existing checks"))
    return {"accepted": accepted, "questions": questions, "gaps": gaps, "rejected": rejected}


def run(llm, df: pd.DataFrame, profile: dict, goal: str) -> dict:
    """The whole AI part: {"worked": bool, "why_not": str, "proposed": [...], plus verify()'s result}. `worked` is False when the model gave no usable answer, so the caller uses the rules."""
    sheet = fact_sheet(df, profile, goal)
    proposed, why = ask_concerns(llm, sheet, goal)
    if proposed is None:
        return {"worked": False, "why_not": why, "proposed": [], "accepted": [], "questions": [], "gaps": [], "rejected": [], "sheet_chars": len(json.dumps(sheet, default=str))}
    return {"worked": True, "why_not": "", "proposed": proposed, "sheet_chars": len(json.dumps(sheet, default=str)), **verify(proposed, sheet, df, profile, goal)}
