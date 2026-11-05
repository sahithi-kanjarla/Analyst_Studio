"""The data understanding record: one AI call that says, for every column, what it means, which role it plays and which goal terms it answers, and maps each term of
the goal to column(s). Every step reads this one record instead of guessing from column names. Nothing here knows a column name or a domain word.

    fact_sheet(df, profile, goal) --> ask(llm, sheet, goal, description) --> validate(obj, columns) --> record
                                           ^------ errors sent back, at most MAX_RETRIES times ------'

The structure test is code: every column appears exactly once, every role is from ROLES, every mapped column exists. When it still fails after the retries, `resolve`
returns a record with source "fallback" and empty maps, and every consumer keeps its existing word-matching rules.
"""
from __future__ import annotations

import json
import re

import pandas as pd

from . import aicache
from . import context as ctxmod
from .concerns import fact_sheet as full_fact_sheet
from .llm import ask_json_ex, deterministic
from .measures import ROWS_LABEL

ROLES = ("measure", "category", "date", "id", "flag", "free_text", "other")
MEASURE_KINDS = ("money", "cost", "quantity", "count", "rate")      # what a measure column holds; any other label is dropped (unknown)
MAX_RETRIES = 1          # one repair, showing the model what failed
MAX_COLUMNS = 120          # a wider table is not sent: the reply would not fit
MAX_TERMS = 20
REASON_CHARS = 110
DISPLAY_CHARS = 40
NONE = "none"

LEAN_DROP = ("q1", "q3", "rarest", "distinct_days", "distinct_months", "distinct_years", "distinct_months_overall", "mode_count")
LEAN_TOP = 5


def fact_sheet(df: pd.DataFrame, profile: dict, goal: str = "") -> dict:
    """What this stage needs of each column: type, role, distinct and missing counts, a number's min, median and max, a date's range and the most common values. Not the quartiles, the rare values or the date parts the
    data-quality stage reads from the full sheet. Same columns, same order, same omitted list."""
    sheet = full_fact_sheet(df, profile, goal)
    for rec in sheet["columns"].values():
        for k in LEAN_DROP:
            rec.pop(k, None)
        if rec.get("top"):
            rec["top"] = [v for v, _ in rec["top"][:LEAN_TOP]]          # the values, not their counts: the meaning of a column does not depend on how often each value occurs
        rec.pop("dtype", None) if rec.get("dtype") in ("str", "object") else None
    return sheet


SYSTEM = """You are a data analyst reading a FACT SHEET about each column of a table (type, distinct and missing counts, the most and least common values, quartiles, date range), the user's GOAL and a short business description. You never see raw rows.
Write the data understanding record that every later step will read.

columns: one entry for EVERY column of the table, each exactly once (the sheet's "omitted" columns too), as {"column": <name exactly as written>, "meaning": <one short line: what a value of this column is>,
"role": one of measure|category|date|id|flag|free_text|other, "display_name": <a short readable name for a report, 1-3 words, capitalised like a label, no underscores>, "goal_terms": [the words of the goal this column answers, or an empty list], "expected": <optional one line, only when something odd is normal, for example many blanks that are expected>, "measure_kind": <only for a measure: one of money|cost|quantity|count|rate; cost is money spent to make or buy what was sold (cost of goods, unit cost), money is everything else paid or earned>}.
role: measure (a quantity you can add or average), category (a group to split by), date, id (labels a row or an entity, including codes), flag (yes/no), free_text, other.
goal_columns: every noun or term in the goal that points at data, mapped to the column(s) holding it, as {"<term as written in the goal>": ["<column>", ...] or "none"}. A plural or a different wording still maps (a goal word "states" can be a column called ship-state).
Use "none" only when no column holds that term. Do not list verbs or filler words.
goal_term_kinds: for every term in goal_columns, "data" when it refers to data (a column, a group, a measure or a time period) or "descriptive" when it only describes the question (a word such as best, worst, where or how much). Descriptive terms are ignored.
order_key: the one column that identifies a single order, invoice or transaction (a label that names one order, not a product, customer or row number you merely guess at), or "none" when no column does.
Use only the column names given, exactly as written. Never write a number.
Return JSON: {"columns": [{"column": str, "meaning": str, "role": str, "display_name": str, "goal_terms": [str], "expected": str, "measure_kind": str}], "goal_columns": {"<term>": [str] | "none"}, "goal_term_kinds": {"<term>": "data" | "descriptive"}, "order_key": str}"""


def build_view(sheet: dict, goal: str, ctx: dict | None) -> str:
    hint = ctxmod.description_hint(ctx)
    about = f"BUSINESS DESCRIPTION (user-written, unverified): {hint}\n\n" if hint else ""
    return f"GOAL: {goal or 'none given (exploratory)'}\n\n{about}FACT SHEET ({sheet['rows']} rows):\n{json.dumps(sheet, ensure_ascii=False, default=str)}"


def _short(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= REASON_CHARS else text[:REASON_CHARS - 1].rstrip() + "…"


def validate(obj, columns: list[str]) -> tuple[dict | None, list[str]]:
    """(record parts, []) when the structure is right, else (None, every problem found). The structure test is code, not trust."""
    if not isinstance(obj, dict):
        return None, ["the reply must be a JSON object with 'columns' and 'goal_columns'"]
    rows, gc = obj.get("columns"), obj.get("goal_columns")
    errors: list[str] = []
    if not isinstance(rows, list):
        errors.append("'columns' must be a list with one entry per column")
        rows = []
    if not isinstance(gc, dict):
        errors.append("'goal_columns' must be an object mapping each goal term to a list of columns or \"none\"")
        gc = {}
    known, seen, cols = set(columns), {}, {}
    for i, row in enumerate(rows, 1):
        if not isinstance(row, dict) or not isinstance(row.get("column"), str):
            errors.append(f"columns entry {i} needs a 'column' name")
            continue
        name = row["column"]
        if name not in known:
            errors.append(f"column '{name}' does not exist")
            continue
        seen[name] = seen.get(name, 0) + 1
        if row.get("role") not in ROLES:
            errors.append(f"column '{name}' has role {row.get('role')!r}; the role must be one of {', '.join(ROLES)}")
        meaning = row.get("meaning")
        if not isinstance(meaning, str) or not meaning.strip():
            errors.append(f"column '{name}' needs a one-line meaning")
        terms = row.get("goal_terms") or []
        if not isinstance(terms, list) or any(not isinstance(t, str) for t in terms):
            errors.append(f"column '{name}': goal_terms must be a list of words")
            terms = []
        exp, shown = row.get("expected"), row.get("display_name")
        shown = " ".join(shown.split())[:DISPLAY_CHARS] if isinstance(shown, str) and re.search(r"[A-Za-z]", shown) and "_" not in shown else ""      # a name that still has an underscore is not readable: the code makes one
        mk = str(row.get("measure_kind") or "").strip().lower()
        mk = mk if row.get("role") == "measure" and mk in MEASURE_KINDS else ""      # checked against the list: an unknown label is no label
        cols[name] = {**({"display_name": shown} if shown else {}), **({"measure_kind": mk} if mk else {}), "meaning": " ".join(str(meaning or "").split())[:200], "role": row.get("role"), "goal_terms": [t.strip() for t in terms if t.strip()][:MAX_TERMS],
                      **({"expected": " ".join(exp.split())[:200]} if isinstance(exp, str) and exp.strip() else {})}
    missing = [c for c in columns if c not in seen]
    if missing:
        errors.append("these columns are missing from 'columns': " + ", ".join(f"'{c}'" for c in missing[:15]) + (f" and {len(missing) - 15} more" if len(missing) > 15 else ""))
    errors += [f"column '{c}' appears {n} times; list each column exactly once" for c, n in seen.items() if n > 1]
    goal_columns: dict[str, list[str]] = {}
    for term, v in gc.items():
        if not isinstance(term, str) or not term.strip():
            continue
        if v == NONE or (isinstance(v, list) and [str(x).lower() for x in v] in ([], [NONE])):
            goal_columns[term.strip()] = []
        elif isinstance(v, list) and all(isinstance(x, str) for x in v):
            bad = [x for x in v if x not in known]
            if bad:
                errors.append(f"goal term '{term}' maps to {', '.join(repr(b) for b in bad)}, which does not exist")
            else:
                goal_columns[term.strip()] = list(dict.fromkeys(v))
        else:
            errors.append(f"goal term '{term}' must map to a list of columns or \"none\"")
    kinds = obj.get("goal_term_kinds")
    descriptive = [t.strip() for t, k in kinds.items() if isinstance(t, str) and str(k).strip().lower() == "descriptive"] if isinstance(kinds, dict) else []
    goal_columns = {t: c for t, c in goal_columns.items() if t not in descriptive}      # a descriptive word is not data: code ignores it
    if len(goal_columns) > MAX_TERMS:
        goal_columns = dict(list(goal_columns.items())[:MAX_TERMS])
    ok = obj.get("order_key")
    order_key = ok.strip() if isinstance(ok, str) and ok.strip() in known else NONE if isinstance(ok, str) and ok.strip().lower() == NONE else ""      # a column that does not exist is no answer (the order key is never a reason to fail the record)
    return ({"columns": cols, "goal_columns": goal_columns, "descriptive": descriptive[:MAX_TERMS], "order_key": order_key}, []) if not errors else (None, errors)


def check_flags(cols: dict, df: pd.DataFrame) -> dict:
    """A column the model calls a yes/no flag but whose values are not two values (a whole-number count, an amount) is a measure: code reads the values, the model's wording is dropped.
    Its meaning and display name are replaced, so no card calls a count a flag, an indicator or a reason."""
    out = {}
    for name, v in cols.items():
        x = pd.to_numeric(df[name], errors="coerce").dropna() if v.get("role") == "flag" and name in df.columns and pd.api.types.is_numeric_dtype(df[name]) else None
        if x is not None and len(x) and x.nunique() > 2:
            whole = bool((x % 1 == 0).all())
            v = {**{k: val for k, val in v.items() if k != "display_name"}, "role": "measure", "measure_kind": "count" if whole else "rate",
                 "meaning": f"A {'whole-number count' if whole else 'number'} with {x.nunique()} different values, not a yes/no flag", "display_name": readable_name(name)}
        out[name] = v
    return out


def _record(source: str, parts: dict | None = None, retries: int = 0, error: str = "") -> dict:
    parts = parts or {"columns": {}, "goal_columns": {}, "descriptive": []}
    return {"source": source, "columns": parts["columns"], "goal_columns": parts["goal_columns"],
            "descriptive": parts.get("descriptive", []), "order_key": parts.get("order_key", ""), "unmapped": [t for t, c in parts["goal_columns"].items() if not c], "retries": retries, "error": error}


def resolve(llm, df: pd.DataFrame, profile: dict, goal: str, ctx: dict | None, state: dict | None = None, saved: dict | None = None) -> dict:
    """The record. source "llm" (valid at the first try or after retries) or "fallback" (no usable answer: `error` says why in one short line; the consumers use their rules).
    With no LLM configured the source is "rules-only" and there is no error to report. A successful record is saved on disk (aicache) and reused, with "cached": True,
    when the data, goal, description, saved glossary answers, model and prompt are all the same. `saved` is the workspace's glossary."""
    if llm is None:
        return _record("rules-only")
    columns = [c for c in df.columns if c in {p["name"] for p in profile["columns"]}]
    if len(columns) > MAX_COLUMNS:
        return _fallback(state, f"the table has {len(columns)} columns, more than the {MAX_COLUMNS} it can describe", 0, [])
    sheet = fact_sheet(df, profile, goal)
    view = build_view(sheet, goal, ctx)
    ck = aicache.key("understanding", sheet, goal, ctxmod.description_hint(ctx), aicache.glossary_answers(saved), aicache.model_name(llm), SYSTEM)
    if (hit := aicache.get(ck)) is not None:
        aicache.note_reused("understanding")
        if state is not None:
            state.setdefault("decisions", []).append({"decision": "data understanding", "source": "cache", "chosen": aicache.REUSED, "why": "same data, goal, description, glossary, model and prompt as a saved run",
                                                      "errors": [], "raw_output": ""})
        return {**hit, "columns": check_flags(hit.get("columns") or {}, df), "cached": True}
    llm = deterministic(llm)
    ask, retries, errors = view, 0, []
    for attempt in range(MAX_RETRIES + 1):
        obj, why = ask_json_ex(llm, SYSTEM, ask)
        if obj is None:
            return _fallback(state, why or "no usable reply", retries, errors)       # the call itself failed (rate limit, timeout, bad JSON): the retries were already spent inside it
        parts, errors = validate(obj, columns)
        if not errors:
            if state is not None:
                state.setdefault("decisions", []).append({"decision": "data understanding", "source": "llm" if not retries else "repaired",
                                                          "chosen": f"{len(parts['columns'])} columns described; {len(parts['goal_columns'])} goal terms mapped ({sum(not c for c in parts['goal_columns'].values())} without a column)",
                                                          "why": f"structure test passed after {retries} retr{'y' if retries == 1 else 'ies'}", "errors": [], "raw_output": ""})
            parts = {**parts, "columns": check_flags(parts["columns"], df)}
            rec = _record("llm", parts, retries)
            aicache.put(ck, rec)
            return rec
        if attempt < MAX_RETRIES:
            retries += 1
            ask = view + "\n\nYour previous answer failed the structure test:\n" + json.dumps(obj, ensure_ascii=False, default=str)[:1500] + "\nProblems:\n" + "\n".join(f"- {e}" for e in errors[:20]) \
                + "\nAnswer again with the complete corrected JSON. List every column exactly once and use only existing column names."
    return _fallback(state, errors[0] if errors else "the structure test failed", retries, errors)


def _fallback(state: dict | None, why: str, retries: int, errors: list[str]) -> dict:
    if state is not None:
        state.setdefault("decisions", []).append({"decision": "data understanding", "source": "fallback", "chosen": "the existing word-matching rules", "why": _short(why),
                                                  "errors": errors[:10] or [why], "raw_output": ""})
    return _record("fallback", None, retries, _short(why))


def notice(rec: dict | None) -> str:
    """The report-header line when the AI part did not work, else ''."""
    return f"AI unavailable for data understanding: {rec['error']}" if rec and rec.get("source") == "fallback" and rec.get("error") else ""


# ---------------------------------------------------------------- what the consumers read

def usable(rec: dict | None) -> bool:
    return bool(rec) and rec.get("source") == "llm"


def goal_columns(rec: dict | None) -> list[str]:
    """Every column some goal term maps to, in the order of the terms."""
    return list(dict.fromkeys(c for cs in ((rec or {}).get("goal_columns") or {}).values() for c in cs)) if usable(rec) else []


def measure_columns(rec: dict | None) -> set[str]:
    """Columns the record calls a measure: never used as a grouping category."""
    return {c for c, v in ((rec or {}).get("columns") or {}).items() if v.get("role") == "measure"} if usable(rec) else set()


def measure_kind_of(rec: dict | None, col: str | None) -> str:
    """money | quantity | count | rate as the record labels the column, '' when unknown."""
    return str((((rec or {}).get("columns") or {}).get(col) or {}).get("measure_kind") or "") if usable(rec) and col else ""


def money_columns(rec: dict | None) -> set[str]:
    """Columns the record labels money; empty when there is no usable record, so no currency symbol is ever guessed from a name."""
    return {c for c in ((rec or {}).get("columns") or {}) if measure_kind_of(rec, c) in ("money", "cost")}


def cost_columns(rec: dict | None) -> set[str]:
    """Columns the record labels as what the sales cost; empty when there is no usable record."""
    return {c for c in ((rec or {}).get("columns") or {}) if measure_kind_of(rec, c) == "cost"}


def share_label(rec: dict | None, col: str | None) -> str:
    """What a share of this measure is 'of': 'all units' for a quantity, 'all <column>' for money or a count, 'the total <column>' when the kind is unknown, 'all records' only for a count of rows (no column)."""
    if not col:
        return f"all {ROWS_LABEL}"
    kind = measure_kind_of(rec, col)
    return "all units" if kind == "quantity" else f"all {col}" if kind in ("money", "count") else f"the total {col}"


def categories(rec: dict | None) -> set[str]:
    """Columns the record calls a category or a flag."""
    return {c for c, v in ((rec or {}).get("columns") or {}).items() if v.get("role") in ("category", "flag")} if usable(rec) else set()


def _stem(w: str) -> str:
    return w[:-3] + "y" if len(w) > 4 and w.endswith("ies") else w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def _words(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", str(text).lower()) if len(w) > 2}


def columns_for_text(rec: dict | None, text: str) -> list[str]:
    """The columns mapped from the goal terms that the text (a goal question) uses: every word of the term appears in it, plural or singular."""
    if not usable(rec):
        return []
    tw = _words(text)
    return list(dict.fromkeys(c for term, cs in rec["goal_columns"].items() if (w := _words(term)) and w <= tw for c in cs))


# ---------------------------------------------------------------- readable names for the main view
def readable_name(name: str) -> str:
    """'profit_to_amount_ratio' -> 'Profit to amount ratio': the code's own readable name when the record has none."""
    text = re.sub(r"\s+", " ", re.sub(r"[_\-]+", " ", re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(name)))).strip()
    return text[:1].upper() + text[1:].lower() if text else str(name)


def derived_display(d: dict) -> str:
    """A derived measure is named after the KPI the user wrote ('profit margin' -> 'Profit margin'); without one, after its name."""
    kpi = " ".join(str(d.get("kpi") or "").split())
    return kpi[:1].upper() + kpi[1:] if kpi else readable_name(d["name"])


def with_derived(rec: dict | None, derived: list[dict]) -> dict | None:
    """The record with a short display name for every derived measure (made after the record was written)."""
    if not rec or not derived:
        return rec
    return {**rec, "derived": {**(rec.get("derived") or {}), **{d["name"]: derived_display(d) for d in derived}}}


def display_names(state: dict) -> dict[str, str]:
    """{internal name: readable name} for every column and derived measure whose readable name differs from the name itself (underscores, camelCase, or words the record shows apart: 'costamount' -> 'Cost amount').
    A name that already reads well, apart from its capitals, is left alone."""
    rec = state.get("understanding") or {}
    plan = state.get("plan") or {}
    dm = list(plan.get("derived") or []) + list((state.get("derived_metrics") or {}).get("created") or [])
    names = [c["name"] for c in (state.get("profile") or {}).get("columns", [])] + [d["name"] for d in dm]
    out = {}
    for n in names:
        d = next((x for x in dm if x["name"] == n), None)
        shown = ((rec.get("derived") or {}).get(n) or (derived_display(d) if d else "")
                 or ((rec.get("columns") or {}).get(n) or {}).get("display_name") or readable_name(n))
        if shown and shown.casefold() != str(n).casefold():
            out[n] = shown
    return out


def apply_names(text: str, names: dict[str, str]) -> str:
    """The text with every internal name replaced by its display name (one pass, so a display name is never replaced again).
    Case does not matter (a model capitalises a name that starts a sentence), and neither do the underscores: 'Returnsrefundamounttotal' is the same name squashed."""
    if not names or not text:
        return text
    look: dict[str, str] = {}
    for n, shown in names.items():
        look[n.casefold()] = shown
        flat = re.sub(r"[\s_]+", "", n).casefold()
        if flat != n.casefold() and len(flat) >= 6:
            look.setdefault(flat, shown)
    rx = re.compile(r"(?<![A-Za-z0-9_])(" + "|".join(re.escape(n) for n in sorted(look, key=len, reverse=True)) + r")(?![A-Za-z0-9_])", re.I)
    return rx.sub(lambda m: look[m.group(1).casefold()], text)


def readable_md(text: str, state: dict) -> str:
    """`readable` for markdown: the targets of links and images (file paths) are never changed."""
    names = display_names(state)
    if not names or not text:
        return text
    parts = re.split(r"(\]\([^)\n]*\))", text)
    return "".join(p if k % 2 else apply_names(p, names) for k, p in enumerate(parts))


def readable(text: str, state: dict) -> str:
    """The text with every internal column or derived-measure name replaced by its display name. Used for everything a reader sees; ids, parameters and file names keep the internal names."""
    return apply_names(text, display_names(state)) if text else text


def unmapped_lines(rec: dict | None) -> list[str]:
    return [f"Your goal mentions {t}, but no column matches." for t in (rec or {}).get("unmapped") or []] if usable(rec) else []


def setup_lines(rec: dict | None) -> list[str]:
    """A short version of the record for 'How the data was set up': roles in one line each, the goal terms and what they map to."""
    if not usable(rec):
        return []
    by_role: dict[str, list[str]] = {}
    for c, v in rec["columns"].items():
        by_role.setdefault(v["role"], []).append(c)
    L = ["", "### What each column is (the data understanding record)", ""]
    L += [f"- **{role}**: {', '.join(f'`{c}`' for c in cs[:12])}{f' and {len(cs) - 12} more' if len(cs) > 12 else ''}" for role in ROLES if (cs := by_role.get(role))]
    expected = [f"`{c}`: {v['expected']}" for c, v in rec["columns"].items() if v.get("expected")]
    if expected:
        L += ["- **Odd but expected**: " + "; ".join(expected[:6])]
    if rec["goal_columns"]:
        L += ["- **Your goal's words**: " + "; ".join(f"“{t}” → {', '.join(f'`{c}`' for c in cs) if cs else 'no column'}" for t, cs in rec["goal_columns"].items())]
    return L
