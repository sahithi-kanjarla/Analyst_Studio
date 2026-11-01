"""The data card: what the table holds, written once per run by code and sent with every plan request, so a question about what is IN the data can be answered without any tool.
For each column: its display name, type, role, meaning (from the understanding record), the share missing, the range of a number or a date, the distinct count, and, for a low-cardinality
category or yes/no column, the most common values with counts and shares (at most 15). No raw rows are ever in it.

Privacy: no value is listed for a column whose role is an identifier or free text, or that looks personal (names, e-mail addresses, phone numbers, addresses, ID-like codes, long text), or whose values
are nearly all different; for those the card gives only the distinct count, the share missing and a plain description.
Size: about 1,500 tokens. A wide table keeps the columns that matter most to the request and the goal, and says how many it left out."""
from __future__ import annotations

import re

import pandas as pd

from ... import cited
from ... import viewselect as vs

MAX_TOKENS = 1500
CHARS_PER_TOKEN = 4
TOP_VALUES = 15
MAX_LIST_CARDINALITY = 50          # a category column with more distinct values than this gets its top values only if it is not nearly all different
EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"^\+?[\d\s().-]{7,}$")
PERSON = re.compile(r"^[A-Z][a-zA-Z'’.-]+(?: [A-Z][a-zA-Z'’.-]+){1,2}$")
CODE = re.compile(r"^(?=.*\d)[A-Za-z0-9_./-]{5,}$")
PII_WORDS = re.compile(r"\b(?:name|names|e-?mail|mail|phone|mobile|tel|telephone|contact|address|street|ssn|passport|birth|dob|aadhaar|pan)\b", re.I)


def personal_looking(name: str, display: str, values: pd.Series, n_rows: int) -> str:
    """"" when the column's values may be listed, else a short reason. Looks at the column's name and at what its values look like."""
    if PII_WORDS.search(f"{name} {display}".replace("_", " ")):
        return "it may hold personal details"
    s = values.dropna().astype(str)
    if s.empty:
        return ""
    sample = s.sample(min(len(s), 400), random_state=0) if len(s) > 400 else s
    if sample.str.contains(EMAIL).mean() > 0.2:
        return "it looks like e-mail addresses"
    if sample.map(lambda v: bool(PHONE.match(v)) and sum(ch.isdigit() for ch in v) >= 7).mean() > 0.5:
        return "it looks like phone numbers"
    if sample.str.len().mean() > 40:
        return "it holds long text"
    ratio = s.nunique() / max(1, len(s))
    if sample.map(lambda v: bool(PERSON.match(v))).mean() > 0.7 and (ratio > 0.2 or s.nunique() >= 5):          # two or three capitalised words, in more than a few kinds: people's names (the cost is a category with several two-word values; the lookup tool can still count them)
        return "it looks like people's names"
    if sample.map(lambda v: bool(CODE.match(v))).mean() > 0.6:
        return "it looks like codes or identifiers"
    if ratio > 0.5 and len(s) > 20:
        return "nearly every value is different"
    return ""


def _type_of(s: pd.Series, role: str) -> str:
    if pd.api.types.is_datetime64_any_dtype(s):
        return "date"
    if pd.api.types.is_bool_dtype(s) or role == "flag":
        return "yes/no"
    if pd.api.types.is_numeric_dtype(s):
        return "number"
    return "text"


def _fmt(v) -> str:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return f"{f:,.0f}" if abs(f) >= 1000 or f == int(f) else f"{f:,.3g}"


def build(df: pd.DataFrame, ctx) -> dict:
    """{"columns": [{...}], "rows": n}. Cached by the caller with the run. Contains no row, only per-column statistics and the allowed value lists."""
    cols = []
    n = len(df)
    for i, (name, c) in enumerate(ctx.cols.items(), 1):
        if name not in df.columns:
            continue
        s = df[name]
        typ = _type_of(s, c.role)
        nn = s.dropna()
        d: dict = {"id": f"d{i}", "name": name, "display": c.display, "type": typ, "role": c.role, "meaning": (c.meaning or "")[:100], "missing": round(float(s.isna().mean()), 4) if n else 0.0,
                   "distinct": int(nn.nunique()), "values": [], "withheld": ""}
        if typ == "number" and len(nn):
            d["min"], d["max"] = _fmt(nn.min()), _fmt(nn.max())
        elif typ == "date" and len(nn):
            d["min"], d["max"] = str(nn.min().date()), str(nn.max().date())
        elif c.role in ("category", "flag") and typ in ("text", "yes/no") and len(nn):
            why = personal_looking(name, c.display, nn, n)
            if why:
                d["withheld"] = why
            else:
                vc = nn.astype(str).value_counts().head(TOP_VALUES)
                d["values"] = [(str(v), int(k), round(k / n, 4)) for v, k in vc.items()]
        elif c.role in ("id", "free_text") or (typ == "text" and c.role not in ("measure", "date")):
            d["withheld"] = "an identifier" if c.role == "id" else "free text" if c.role == "free_text" else "text that is not a category"
        cols.append(d)
    return {"rows": n, "columns": cols}


def line(d: dict) -> str:
    bits = [f"{d['name']} | {d['display']} | {d['type']}, {d['role']}"]
    if d["meaning"]:
        bits.append(d["meaning"])
    bits.append(f"{d['missing'] * 100:.1f}% missing")
    if "min" in d:
        bits.append(f"from {d['min']} to {d['max']}")
    bits.append(f"{d['distinct']:,} distinct")
    if d["values"]:
        bits.append("values: " + ", ".join(f"{v} ({k:,}, {p * 100:.1f}%)" for v, k, p in d["values"]))
    elif d["withheld"]:
        bits.append(f"values not listed ({d['withheld']})")
    return f"[{d['id']}] " + " | ".join(bits)


def facts(card: dict) -> dict[str, dict]:
    """Each column's line as a fact with its id, so a sentence about the data can cite it and every figure in it is checked."""
    out = {}
    for d in card["columns"]:
        text = line(d)
        out[d["id"]] = cited.make_fact(text.split("] ", 1)[1], " ".join([d["name"], d["display"], d["meaning"]] + [v for v, _, _ in d["values"]]))
    return out


def relevance(d: dict, words: set[str]) -> int:
    hay = vs.words(" ".join([d["name"], d["display"], d["meaning"]] + [v for v, _, _ in d["values"]]))
    return len(words & hay)


def render(card: dict, question: str = "", goal: str = "", max_tokens: int = MAX_TOKENS) -> tuple[str, int]:
    """(the text for the prompt, how many columns were left out). Keeps the columns most relevant to the request and the goal when the table is too wide for the budget."""
    budget = max_tokens * CHARS_PER_TOKEN
    cols = card["columns"]
    head = f"{card['rows']:,} rows, {len(cols)} columns. Each line: [fact id] name | display name | type, role | meaning | missing | range | distinct | most common values. Cite a line by its fact id."
    lines = [line(d) for d in cols]
    if len(head) + sum(len(x) + 1 for x in lines) <= budget:
        return head + "\n" + "\n".join(lines), 0
    w = vs.words(question) | vs.words(goal)
    order = sorted(range(len(cols)), key=lambda i: (-relevance(cols[i], w), i))
    keep, used = set(), len(head) + 160
    for i in order:
        if used + len(lines[i]) + 1 > budget:
            continue
        keep.add(i)
        used += len(lines[i]) + 1
    left = [cols[i]["name"] for i in range(len(cols)) if i not in keep]
    shown = [lines[i] for i in range(len(cols)) if i in keep]
    tail = f"{len(left)} more columns are not shown because they matter less to this request: " + ", ".join(left[:12]) + (" and others" if len(left) > 12 else "") + ". Ask for one by name to see it."
    return head + "\n" + "\n".join(shown) + "\n" + tail, len(left)


def get(ctx, df) -> dict:
    """The card, built once per run (it lives in the run's cached context and goes when the run's data changes)."""
    if "datacard" not in ctx.memo:
        ctx.memo["datacard"] = build(df, ctx)
    return ctx.memo["datacard"]
