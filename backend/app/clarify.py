"""Clarifying questions: spot the ambiguities in the data that would change the numbers, before anything is analysed.

Rules only, no LLM needed (max MAX_QUESTIONS questions). When an LLM is configured it may add up to MAX_LLM extra questions;
every column such a question mentions must exist, otherwise the question is dropped.

A question is NOT asked when the goal or the typed KPIs already settle it (the answer is then recorded as set by "goal").
Each question is a dict: {id, kind, text, options:[{value,label}], default, why, columns, meta}. Ids are stable across runs
(e.g. "date_col", "refund:<col>", "status:<col>:<value>") so saved glossary answers can pre-fill them."""
from __future__ import annotations

import hashlib
import json
import re

import pandas as pd

from . import context as ctxmod
from .llm import ask_json
from .planner import _name_tokens
from .profiler import compact_profile

MAX_QUESTIONS = 6
MAX_LLM = 3

REFUND_RX = re.compile(r"refund|return|cancel|discount", re.I)
NOT_AMOUNT_RX = re.compile(r"count|qty|quantity|num|number|flag|pct|percent|rate|ratio|%|^is_|^has_", re.I)  # a rate or a count cannot be subtracted from money
AMOUNT_RX = re.compile(r"revenue|sales|amount|total|gmv|value|price|income|spend|turnover", re.I)
OUTCOME_RX = re.compile(r"revenue|sales|profit|income|amount|spend|churn|conversion|score|rating|salary|margin|outcome|target|gmv|turnover|default|fraud|satisfaction", re.I)
STATUS_RX = re.compile(r"status|state|stage|condition", re.I)
BAD_STATUS = ("cancel", "fail", "void", "declin", "test", "dummy", "demo", "sandbox")
TEST_TOKENS = {"test", "testing", "dummy", "demo", "sandbox"}
AGG_SUFFIX = re.compile(r"_(total|sum|avg|mean|min|max|count|last|first|median)$", re.I)
DATE_FILLER = {"date", "time", "at", "on", "dt", "ts", "timestamp", "day"}


def _q(qid: str, kind: str, text: str, options: list[tuple[str, str]], default: str, why: str, columns: list[str], **meta) -> dict:
    return {"id": qid, "kind": kind, "text": text, "options": [{"value": v, "label": lab} for v, lab in options], "default": default,
            "why": why, "columns": columns, "meta": meta}


def option_values(q: dict) -> list[str]:
    return [o["value"] for o in q["options"]]


def _num(df: pd.DataFrame, col: str) -> pd.Series:
    return pd.to_numeric(df[col], errors="coerce")


def _refund_like(name: str) -> bool:
    return bool(REFUND_RX.search(name)) and not NOT_AMOUNT_RX.search(name)


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _col_words(name: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", name).lower())]


def _named_in_goal(col: str, goal: str) -> bool:
    ws, gw = _col_words(col), _words(goal)
    if not ws:
        return False
    if " ".join(ws) in " ".join(re.findall(r"[a-z0-9]+", goal.lower())):
        return True
    distinct = set(ws) - DATE_FILLER
    return bool(distinct) and distinct <= gw


def _date_question(df, profile, goal, explore) -> dict | None:
    cols = [c["name"] for c in profile["columns"] if c["role"] == "date" and c["name"] in df.columns]
    if len(cols) < 2:
        return None
    named = [c for c in cols if _named_in_goal(c, "" if explore else goal)]
    stats = {c["name"]: c.get("stats") or {} for c in profile["columns"]}

    def label(c):
        s = stats.get(c, {})
        return f"{c} ({s['min']} to {s['max']})" if s.get("min") else c
    q = _q("date_col", "date_col", "Which date should be used for time analysis?", [(c, label(c)) for c in cols], named[0] if len(named) == 1 else cols[0],
           f"The data has {len(cols)} date columns ({', '.join(cols)}); trends, seasonality and time charts can only use one.", cols)
    if len(named) == 1:
        q["auto"] = "goal"
    return q


def _refund_questions(df, profile) -> list[dict]:
    roles = {c["name"]: c["role"] for c in profile["columns"]}
    num = [c for c, r in roles.items() if r == "numeric" and c in df.columns]
    refunds = [c for c in num if _refund_like(c)]
    amounts = [c for c in num if c not in refunds and AMOUNT_RX.search(c) and not NOT_AMOUNT_RX.search(c)]
    main = next((c for rx in ("revenue", "sales", "amount", "total", "gmv", "value", "income", "spend", "turnover", "price") for c in amounts if rx in c.lower()), None)
    net_col = None
    if main:
        net_col = f"{main}_net"
        while net_col in df.columns:
            net_col += "_2"
    out = []
    for r in refunds[:2]:
        s = _num(df, r)
        n = int((s.fillna(0) != 0).sum())
        if n == 0:
            continue
        main_here = main if not out else None  # only one refund column can be netted into the amount
        opts = ([("net", f"Subtract from {main_here} and use the net amount as the main number")] if main_here else []) + [("ignore", "Ignore it (leave it out of the analysis)"), ("separate", "Analyse it separately")]
        out.append(_q(f"refund:{r}", "refund", f"How should {r} be handled?", opts, "net" if main_here else "ignore",
                      f"'{r}' looks like a refund, return, cancellation or discount amount ({n:,} of {len(df):,} rows are non-zero), so it changes the totals depending on how it is treated.",
                      [r] + ([main_here] if main_here else []), column=r, amount=main_here, net_col=net_col if main_here else None))
    return out


def _negative_questions(df, profile, skip: set[str]) -> list[dict]:
    out = []
    for c in profile["columns"]:
        name = c["name"]
        if c["role"] != "numeric" or name not in df.columns or name in skip or _refund_like(name) or REFUND_RX.search(name):
            continue
        if not AMOUNT_RX.search(name) or NOT_AMOUNT_RX.search(name):
            continue
        n = int((_num(df, name) < 0).sum())
        if n:
            out.append(_q(f"neg:{name}", "negatives", f"How should the {n:,} negative values in {name} be handled?",
                          [("include", "Include them (they are real adjustments, refunds or reversals)"), ("exclude", "Exclude those rows")], "include",
                          f"'{name}' is an amount but {n:,} rows are negative; they lower the totals.", [name], column=name))
    return out[:2]


def _status_questions(df, profile) -> list[dict]:
    out = []
    for c in profile["columns"]:
        name = c["name"]
        if c["role"] not in ("categorical", "binary", "text") or name not in df.columns or not STATUS_RX.search(name):
            continue
        s = df[name].dropna().astype(str).str.strip()
        for val, n in s.value_counts().items():
            low = val.lower()
            if not any(b in low for b in BAD_STATUS):
                continue
            is_test = bool(_words(val) & TEST_TOKENS)
            out.append(_q(f"status:{name}:{val}", "status", f"Include rows where {name} is {val}?", [("include", f"Include them ({n:,} rows)"), ("exclude", f"Exclude them ({n:,} rows)")],
                          "exclude" if is_test else "include",
                          f"{n:,} rows have {name} = '{val}'" + (", which looks like test data." if is_test else "; whether they count depends on your business definition."), [name], column=name, value=val))
    return out[:3]


def _outcome_question(df, profile, goal, ctx, explore, exclude: set[str]) -> dict | None:
    goal_t = set() if explore else _words(goal)
    kpi_t = ctxmod.kpi_tokens(ctx)
    hint = ctxmod.lens(ctx)["target_rx"] if ctxmod.has_context(ctx) else None
    miss = {c["name"]: c["missing_pct"] for c in profile["columns"]}
    cands = [c["name"] for c in profile["columns"] if c["role"] == "numeric" and c["name"] in df.columns and c["name"] not in exclude and not _refund_like(c["name"]) and not REFUND_RX.search(c["name"])]

    def score(c):
        nt = _name_tokens(c)
        return 3 * len(nt & goal_t) + 5 * len(nt & kpi_t)
    # a binary outcome (churn, converted…) named by the goal or KPIs settles it even though only numeric columns are offered as options
    named = [c["name"] for c in profile["columns"] if c["role"] == "binary" and c["name"] in df.columns and score(c["name"]) > 0]
    if named and max(score(c) for c in cands + named) == max(score(c) for c in named) and len([c for c in cands if score(c) >= max(score(n) for n in named)]) == 0:
        return None
    cands = [c for c in cands if score(c) > 0 or OUTCOME_RX.search(c) or (hint and hint.search(c))]
    if len(cands) < 2:
        return None
    hits = [c for c in cands if score(c) > 0]
    if hits:
        best = max(score(c) for c in hits)
        top = [c for c in hits if score(c) == best]
        if len(top) == 1:
            return None  # the goal or KPIs already identify the number
        if len({AGG_SUFFIX.sub("", c) for c in top}) == 1:
            return None  # variants of one measure produced by joining tables (revenue_total / revenue_avg), not a real choice
        shortest = min(top, key=lambda c: len(_name_tokens(c)))
        if all(_name_tokens(shortest) < _name_tokens(c) for c in top if c != shortest):
            return None  # e.g. 'revenue' vs 'orders_revenue_total': the plain one is meant
        cands = top
    default = max(cands, key=lambda c: (score(c), bool(OUTCOME_RX.search(c)), -miss[c]))
    cands = cands[:6]
    if default not in cands:
        cands[-1] = default
    return _q("main_outcome", "outcome", "Which is the main number you care about?", [(c, c) for c in cands], default,
              f"{len(cands)} numeric columns could be the main outcome ({', '.join(cands)}); the analysis is built around one.", cands)


def detect_questions(df: pd.DataFrame, profile: dict, goal: str = "", ctx: dict | None = None, explore: bool = False) -> list[dict]:
    """Rule-based questions (never mutates df). Questions already settled by the goal carry "auto" and are not shown to the user."""
    ctx = ctx or {}
    date_q = _date_question(df, profile, goal, explore)
    refunds = _refund_questions(df, profile)
    skip = {c for q in refunds for c in q["columns"]}
    negs = _negative_questions(df, profile, skip)
    statuses = _status_questions(df, profile)
    outcome = _outcome_question(df, profile, goal, ctx, explore, set())
    asked, auto = [], []
    for q in [date_q] + refunds + negs + statuses + [outcome]:
        if q is None:
            continue
        (auto if q.get("auto") else asked).append(q)
    return asked[:MAX_QUESTIONS] + auto


# ---------------------------------------------------------------- LLM enrichment

ENRICH_SYSTEM = """You are the Clarifying-question assistant of a data-analysis team. Before the analysis starts you may ask the user a few SHORT questions
about things in the data that are ambiguous AND would change the numbers (what a column means, which rows count, which definition of a KPI).
Use only the column names given. Do not repeat questions already asked. Do not ask about anything the goal already answers.
Return JSON: {"questions": [{"text": string, "options": [2-5 short strings], "default": one of the options, "why": one sentence, "columns": [exact column names the question is about]}]}
Propose at most 3 questions; propose none if nothing is ambiguous."""

_QUOTED = re.compile(r"`([^`]{1,60})`|'([^'\s]{1,60})'|\"([^\"\s]{1,60})\"")


def validate_llm_questions(raw, profile: dict, existing: list[dict], limit: int = MAX_LLM) -> list[dict]:
    """Keep only well-formed questions whose every mentioned column exists. Anything else is dropped."""
    names = {c["name"] for c in profile["columns"]}
    seen = {re.sub(r"\W+", " ", q["text"].lower()).strip() for q in existing}
    out: list[dict] = []
    items = raw.get("questions") if isinstance(raw, dict) else None
    for item in items if isinstance(items, list) else []:
        if len(out) >= limit:
            break
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()[:200]
        opts = [str(o).strip()[:80] for o in item.get("options") or [] if isinstance(o, (str, int, float)) and str(o).strip()]
        opts = list(dict.fromkeys(opts))[:5]
        cols = item.get("columns")
        if not text or len(opts) < 2 or not isinstance(cols, list) or not cols or any(not isinstance(c, str) or c not in names for c in cols):
            continue
        quoted = [next(g for g in m.groups() if g) for m in _QUOTED.finditer(text)]
        if any(t not in names and t not in opts and re.search(r"[_\d]|^[A-Za-z]+$", t) and " " not in t and t.lower() not in {o.lower() for o in opts} for t in quoted):
            continue  # the text points at a column that is not in the data
        key = re.sub(r"\W+", " ", text.lower()).strip()
        if key in seen:
            continue
        seen.add(key)
        default = str(item.get("default") or "").strip()
        out.append(_q("llm:" + hashlib.sha1(key.encode()).hexdigest()[:8], "llm", text, [(o, o) for o in opts], default if default in opts else opts[0],
                      str(item.get("why") or "Suggested from your column names, goal and business context.").strip()[:240], list(dict.fromkeys(cols))))
    return out


def llm_questions(llm, profile: dict, goal: str, ctx: dict | None, existing: list[dict], slots: int) -> list[dict]:
    if llm is None or slots <= 0:
        return []
    ctx_txt = ctxmod.context_text(ctx)
    user = (f"GOAL: {goal}\n\n" + (f"BUSINESS CONTEXT (user-written, unverified):\n{ctx_txt}\n\n" if ctx_txt else "")
            + "COLUMNS:\n" + json.dumps([{"name": c["name"], "role": c["role"]} for c in compact_profile(profile)], default=str)
            + "\n\nALREADY ASKED:\n" + json.dumps([q["text"] for q in existing]))
    return validate_llm_questions(ask_json(llm, ENRICH_SYSTEM, user), profile, existing, min(MAX_LLM, slots))


def build_questions(df, profile, goal, ctx, explore, llm) -> list[dict]:
    """Rule questions first, then up to MAX_LLM from the model while the total stays within MAX_QUESTIONS."""
    qs = detect_questions(df, profile, goal, ctx, explore)
    asked = [q for q in qs if not q.get("auto")]
    return qs + llm_questions(llm, profile, goal, ctx, asked, MAX_QUESTIONS - len(asked))


# ---------------------------------------------------------------- answers

def resolve(questions: list[dict], user_answers: dict | None, saved: dict, skip: bool = False) -> tuple[dict, dict]:
    """Final answer and who set it, per question. Order: the user, then the saved glossary, then the default (a skip ignores the user's answers)."""
    final, source = {}, {}
    for q in questions:
        qid, valid = q["id"], option_values(q)
        mine = (user_answers or {}).get(qid)
        gl = (saved.get(qid) or {}).get("value") if isinstance(saved.get(qid), dict) else saved.get(qid)
        if q.get("auto"):
            final[qid], source[qid] = q["default"], q["auto"]
        elif not skip and mine in valid:
            final[qid], source[qid] = mine, "user"
        elif gl in valid:
            final[qid], source[qid] = gl, "glossary"
        else:
            final[qid], source[qid] = q["default"], "default"
    return final, source


def plan_overrides(questions: list[dict], answers: dict) -> dict:
    """What the answers change in the plan: the outcome, the date column, columns to leave out or analyse separately."""
    ov = {"target": None, "date_col": None, "exclude": [], "include": []}
    net = None
    for q in questions:
        a = answers.get(q["id"])
        if q["kind"] == "date_col":
            ov["date_col"] = a
        elif q["kind"] == "outcome":
            ov["target"] = a
        elif q["kind"] == "refund":
            col, amount = q["meta"]["column"], q["meta"].get("amount")
            if a == "net" and amount:
                net = (amount, q["meta"].get("net_col"))
                ov["exclude"] += [col, amount]
            elif a == "ignore":
                ov["exclude"].append(col)
            elif a == "separate":
                ov["include"].append(col)
    if net and net[1] and (ov["target"] in (None, net[0])):
        ov["target"] = net[1]
    return ov


def summarise(questions: list[dict], answers: dict, sources: dict, effects: dict) -> list[dict]:
    """One row per answered question for the report: answer, who set it, and the effect on the data (rows affected)."""
    rows = []
    for q in questions:
        qid = q["id"]
        label = next((o["label"] for o in q["options"] if o["value"] == answers.get(qid)), answers.get(qid))
        eff = effects.get(qid) or {}
        rows.append({"id": qid, "question": q["text"], "answer": answers.get(qid), "answer_label": label, "source": sources.get(qid, "default"),
                     "effect": eff.get("effect", "Recorded as context; the data was not changed."), "rows": eff.get("rows")})
    return rows
