"""Clarifying questions that work on any tabular data.

   data --> signals.py (neutral facts: two date columns, a spike of one value, a column that is the sum of two others, ...)
        --> question writer: an LLM turns the signals + goal + context + saved glossary into questions; with no LLM (or an unusable
            answer) one generic template per signal type does the same job
        --> validator: no backing signal, unknown columns, actions outside the closed list (directives.py), questions whose answers change nothing, duplicates are dropped
        --> the user answers (or skips); cleaner.apply_directives executes the chosen actions.

Nothing here knows what a column MEANS: no word lists about any business domain. A question is only ever asked when the answer
changes what a number means, and the default answer of every question leaves the data untouched, so "Skip, use defaults" changes nothing.

A question is {id, kind, text, why, options:[{value, label, action}], default, columns, signal, strong} (+ "auto" when the goal already settles it).
Every question is anchored to a signal and its id IS the signal id (type + columns hash), so a saved glossary answer matches the same ambiguity on the next run.
The writer may only reword questions for signals that exist; a question with no backing signal is dropped. Several candidate measures are NOT a question:
the planner analyses all of them (totals, averages per record), and so it does for segment columns, growth periods and total-versus-average."""
from __future__ import annotations

import hashlib
import json
import re
import time

import pandas as pd

from . import context as ctxmod
from . import directives as dv
from .llm import note_failure, ask_json
from .profiler import compact_profile
from .retail_roles import product_question
from .signals import compute_signals

MAX_QUESTIONS = 3        # at most this many questions are asked, in the LLM and the rules path alike
MAX_CANDIDATES = 12      # valid questions considered before the cap picks the ones that matter most
# How much an answer changes the analysis, most first: a placeholder or a repeated value moves every total and average; a date choice moves every trend; and so on.
IMPACT = ("placeholder", "sentinel", "product_basis", "date_order", "multi_date", "flag_rows", "blank_values", "negatives", "near_sum", "duplicate_columns", "rare_labels", "high_missing")
MAX_OPTIONS = 6
UNASKED = {"multi_measure"}      # signals that feed the planner instead of the user
CODE_OWNED = {"product_basis", "flag_rows", "blank_values", "date_order"}      # questions code always writes itself: the answer decides how a number is built, so the wording is not left to a model
# What an answer may do about each kind of ambiguity. A question that borrows a real signal id but offers other actions is not about that signal.
SIGNAL_ACTIONS = {"multi_date": {"set_date_column"}, "negatives": {"handle_negatives", "filter_rows", "note_only"},
                  "sentinel": {"treat_value_as_missing", "filter_rows", "note_only"}, "near_sum": {"drop_column", "define_derived_metric", "merge_columns", "note_only"},
                  "rare_labels": {"filter_rows", "note_only"}, "high_missing": {"drop_column", "note_only"}, "duplicate_columns": {"drop_column", "merge_columns", "note_only"},
                  "flag_rows": {"filter_rows", "note_only"}, "blank_values": {"fill_missing", "filter_rows", "note_only"}, "date_order": {"set_date_order"}}
DATE_FILLER = {"date", "time", "at", "on", "dt", "ts", "timestamp", "day"}


def option_values(q: dict) -> list[str]:
    return [o["value"] for o in q["options"]]


def option_for(q: dict, value) -> dict:
    return next((o for o in q["options"] if o["value"] == value), None) or next(o for o in q["options"] if o["value"] == q["default"])


def chosen(questions: list[dict], answers: dict):
    """(question, option, action) for every question, using the answer or else the default."""
    for q in questions:
        o = option_for(q, answers.get(q["id"]))
        yield q, o, o["action"]


# ---------------------------------------------------------------- validation (shared by the LLM writer and the templates)

def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _col_words(name: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", re.sub(r"([a-z])([A-Z])", r"\1 \2", name).lower())


def named_in_goal(col: str, goal: str) -> bool:
    """The user's own words in the goal point at this column (all of the column's distinctive words appear in it)."""
    ws = _col_words(col)
    distinct = set(ws) - DATE_FILLER
    return bool(ws) and bool(distinct) and (" ".join(ws) in " ".join(re.findall(r"[a-z0-9]+", goal.lower())) or distinct <= _words(goal))


def _value_for(action: dict, label: str) -> str:
    key = json.dumps(action, sort_keys=True, default=str) + (label if action["type"] == "note_only" else "")
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]


def validate_question(item, profile: dict, sig_by_id: dict, keep_values: bool = False) -> tuple[dict | None, str]:
    """Check one proposed question. Returns (clean question, "") or (None, reason it was dropped)."""
    if not isinstance(item, dict):
        return None, "not an object"
    roles = {c["name"]: c["role"] for c in profile["columns"]}
    text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()[:220]
    if not text:
        return None, "no question text"
    mentioned = item.get("columns")
    if mentioned is not None and (not isinstance(mentioned, list) or any(not isinstance(c, str) or c not in roles for c in mentioned)):
        return None, "mentions a column that does not exist"
    raw_opts = item.get("options")
    if not isinstance(raw_opts, list) or not 2 <= len(raw_opts) <= MAX_OPTIONS:
        return None, "needs 2-6 options"
    opts, seen_vals = [], set()
    for o in raw_opts:
        if not isinstance(o, dict) or not str(o.get("label") or "").strip():
            return None, "an option has no label"
        label = str(o["label"]).strip()[:120]
        action, why = dv.validate_action(o.get("action"), roles)
        if action is None:
            return None, "invalid option: " + why
        value = str(o.get("value")) if keep_values and o.get("value") else _value_for(action, label)
        while value in seen_vals:
            value += "_"
        seen_vals.add(value)
        opts.append({"value": value, "label": label, "action": action})
    raw_default = item.get("default")
    default = next((o["value"] for o in opts if raw_default in (o["value"], o["label"])), None)
    if default is None or not dv.is_harmless(next(o["action"] for o in opts if o["value"] == default)):
        default = next((o["value"] for o in opts if dv.is_harmless(o["action"])), None)   # skipping must never touch the data
    if default is None:
        return None, "no option that leaves the data unchanged to use as the default"
    if not any(dv.changes_meaning(o["action"]) for o in opts):
        return None, f"no answer changes the data or what a number means: {text[:60]}"       # note-only options, or just alternative columns/views the app analyses anyway
    windows: dict[str, int] = {}
    for o in opts:
        a = o["action"]
        if a["type"] == "filter_rows" and a["operator"] in ("lt", "le", "gt", "ge"):
            windows[a["column"]] = windows.get(a["column"], 0) + 1
    if any(n >= 2 for n in windows.values()):
        return None, f"alternative windows or thresholds of the same column, which the app analyses anyway: {text[:60]}"
    sig = sig_by_id.get(item.get("signal"))
    if sig is None:
        return None, f"no signal in the data backs this question: {text[:60]}"
    if sig["type"] in UNASKED:
        return None, f"a {sig['type']} signal is analysed, never asked about: {text[:60]}"
    allowed = SIGNAL_ACTIONS.get(sig["type"])
    for o in opts:
        a = o["action"]
        touched = dv.action_columns(a)
        if allowed is not None and a["type"] not in allowed:
            return None, f"an option does something this kind of signal does not call for: {text[:60]}"
        if a["type"] != "note_only" and touched and not set(touched) & set(sig["columns"]):
            return None, f"an option acts on columns the signal is not about: {text[:60]}"
    cols = list(dict.fromkeys([c for o in opts for c in dv.action_columns(o["action"])] + (mentioned or []) + sig["columns"]))
    return {"id": sig["id"], "kind": sig["type"], "text": text, "why": str(item.get("why") or "").strip()[:260] or "This changes how a number is calculated.",
            "options": opts, "default": default, "columns": cols, "signal": sig["id"], "strong": sig["strong"]}, ""


def _action_signature(q: dict) -> frozenset:
    """What a question can do, ignoring its wording: the (action type, columns) of every option that is not a plain note."""
    return frozenset((o["action"]["type"], tuple(sorted(dv.action_columns(o["action"])))) for o in q["options"] if o["action"]["type"] != "note_only")


def _near_duplicate(a: dict, b: dict) -> bool:
    """Same wording (word overlap of 70% or more) about at least one shared column, or the same actions on the same columns."""
    wa, wb = _words(a["text"]), _words(b["text"])
    same_words = bool(wa and wb) and len(wa & wb) / len(wa | wb) >= 0.7 and bool(set(a["columns"]) & set(b["columns"]))
    return a["id"] == b["id"] or same_words or _action_signature(a) == _action_signature(b)


def validate_all(items, profile: dict, signals: list[dict], limit: int = MAX_QUESTIONS, keep_values: bool = False) -> tuple[list[dict], list[str]]:
    """(valid questions, reasons for every drop). Duplicates (same id, near-identical wording, same actions on the same columns) are merged into the first."""
    sig_by = {s["id"]: s for s in signals}
    out, dropped = [], []
    for item in items if isinstance(items, list) else []:
        q, why = validate_question(item, profile, sig_by, keep_values)
        if q is None:
            dropped.append(why)
            continue
        if any(_near_duplicate(q, x) for x in out):
            dropped.append("duplicate: " + q["text"][:60])
            continue
        out.append(q)
    return out[:limit], dropped


# ---------------------------------------------------------------- no-LLM templates: one per signal type

def _o(key: str, label: str, **action) -> dict:
    return {"value": key, "label": label, "action": action}


def _fmt(v) -> str:
    return f"{v:g}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)


def _pct(x) -> str:
    return f"{100 * x:.0f}%"


def _template(sig: dict, goal: str, ctx: dict, explore: bool) -> dict | None:
    t, c, ev = sig["type"], sig["columns"], sig["evidence"]
    base = {"signal": sig["id"]}
    if t == "multi_date":
        opts = [_o(d, f"{d} ({ev['ranges'][d][0]} to {ev['ranges'][d][1]})", type="set_date_column", column=d) for d in c[:MAX_OPTIONS]]
        named = [d for d in c if not explore and named_in_goal(d, goal)]
        return {**base, "text": "Which date should be used for time analysis?", "options": opts, "default": named[0] if len(named) == 1 else c[0], "_auto": len(named) == 1,
                "why": f"The data has {len(c)} date columns ({', '.join(c)}); trends and seasonality can only follow one."}
    if t == "negatives":
        n = c[0]
        return {**base, "text": f"How should the {ev['count']:,} negative values in {n} be handled?", "default": "keep",
                "options": [_o("keep", "Keep them as they are", type="handle_negatives", column=n, mode="keep"),
                            _o("exclude", "Exclude those rows", type="handle_negatives", column=n, mode="exclude")],
                "why": f"{n} has {ev['count']:,} negative values ({_pct(ev['share'])} of rows, down to {ev['min']:g}); they pull totals and averages down."}
    if t == "sentinel":
        n, vals = c[0], [h["value"] for h in ev["values"]]
        shown = ", ".join(_fmt(v) for v in vals[:4])
        rows = sum(h["count"] for h in ev["values"])
        return {**base, "text": f"{rows:,} rows in {n} hold {shown}. Is that a real value or a placeholder for 'no value'?", "default": "real",
                "options": [_o("real", "A real value, keep it", type="note_only"), _o("missing", "A placeholder, treat it as missing", type="treat_value_as_missing", column=n, values=vals[:dv.MAX_LIST])],
                "why": f"One value ({shown}) repeats far more often than the rest of {n}; placeholders like this distort averages and totals."}
    if t == "near_sum":
        a = c[0]
        return {**base, "text": f"{a} matches {ev['relation'].split('~', 1)[1].strip()} almost exactly. How should that be treated?", "default": "independent",
                "options": [_o("independent", "Treat them as independent columns", type="note_only"),
                            _o("derived", f"{a} is calculated from the others (note it)", type="note_only"),
                            _o("drop", f"Leave {a} out so the same information is not counted twice", type="drop_column", column=a)],
                "why": f"In {_pct(ev['match_share'])} of rows {ev['relation']}, so analysing all three can double count."}
    if t == "rare_labels":
        n, rare = c[0], list(ev["rare"])
        return {**base, "text": f"{n} has {ev['n_rare']} very rare labels (such as {', '.join(rare[:3])}). Are they valid?", "default": "valid",
                "options": [_o("valid", "Yes, they are valid labels", type="note_only"),
                            _o("exclude", "No, they are noise: exclude those rows", type="filter_rows", column=n, operator="not_in", value=rare[:dv.MAX_LIST])],
                "why": f"Out of {ev['n_labels']} labels in {n}, {ev['n_rare']} appear only a handful of times each; they may be typos."}
    if t == "flag_rows":
        n, pos = c[0], ev["positive"]
        return {**base, "text": f"{ev['count']:,} rows ({_pct(ev['share'])}) are flagged '{pos}' in {n}. If they are reversed or voided rows, should they be taken out of the totals (net) or counted as they are (gross)?",
                "default": "gross", "options": [_o("gross", "Gross: count every row", type="note_only"),
                                                 _o("net", f"Net: leave out the rows flagged '{pos}'", type="filter_rows", column=n, operator="ne", value=pos)],
                "why": f"{n} marks {_pct(ev['share'])} of the rows; whether they count changes every total and every ranking."}
    if t == "blank_values":
        n = c[0]
        return {**base, "text": f"{ev['count']:,} rows ({_pct(ev['share'])}) have no {n}. How should they count?", "default": "leave",
                "options": [_o("leave", "Leave them blank: they add nothing to the totals", type="note_only"),
                            _o("one", f"Count each as 1 unit", type="fill_missing", column=n, value=1),
                            _o("drop", "Drop those rows", type="filter_rows", column=n, operator="not_null")],
                "why": f"Without {n} the value of those rows cannot be worked out, so they are missing from every total unless you decide otherwise."}
    if t == "date_order":
        n, r = c[0], ev["ranges"]
        return {**base, "text": f"Are the dates in {n} written day first (dd/mm/yyyy) or month first (mm/dd/yyyy)?", "default": "day",
                "options": [_o("day", f"Day first: {r['day_first'][0]} to {r['day_first'][1]}", type="set_date_order", column=n, dayfirst=True),
                            _o("month", f"Month first: {r['month_first'][0]} to {r['month_first'][1]}", type="set_date_order", column=n, dayfirst=False)],
                "why": f"All {ev['values']:,} such dates have a day and a month of 12 or less, so both readings fit; the period they cover differs."}
    if t == "high_missing":
        n = c[0]
        return {**base, "text": f"{n} is {ev['missing_pct']:g}% empty. Should it still be used?", "default": "keep",
                "options": [_o("keep", "Use it, with caution", type="note_only"), _o("drop", "Leave it out of the analysis", type="drop_column", column=n)],
                "why": f"With this much missing, any average or comparison of {n} describes only part of the rows."}
    if t == "duplicate_columns":
        a, b = c[0], c[1]
        return {**base, "text": f"{a} and {b} look like the same information. Count them as one?", "default": "keep",
                "options": [_o("keep", "Keep both as they are", type="note_only"), _o("drop", f"Leave {b} out", type="drop_column", column=b),
                            _o("merge", "Merge them into one column", type="merge_columns", columns=[a, b], name=f"{a}_{b}_merged", how="first_non_null")],
                "why": f"{ev['relation'].capitalize()}, so using both would repeat the same signal."}
    return None


def template_questions(signals: list[dict], profile: dict, goal: str, ctx: dict | None, explore: bool) -> list[dict]:
    raw, autos = [], {}
    for s in signals:
        t = _template(s, goal, ctx or {}, explore)
        if t:
            autos[s["id"]] = t.pop("_auto", False)
            raw.append(t)
    qs, _ = validate_all(raw, profile, signals, limit=MAX_CANDIDATES, keep_values=True)
    for q in qs:
        if autos.get(q["signal"]):
            q["auto"] = "goal"
    return qs


# ---------------------------------------------------------------- the LLM question writer

WRITER_SYSTEM = f"""You are the Clarifying-question writer of a data-analysis team. Before the analysis starts you may ask the user a few SHORT questions.
You get a compact profile of the data, neutral SIGNALS found by code, the user's goal, optional business context, and what the saved glossary already answers.

Ask ONLY when the answer changes what a number MEANS (a definition ambiguity): which of two dates is "the" date, whether a repeated value is real or a
placeholder for "no value", whether negative values are real, whether a column is a calculation of other columns, whether two columns are the same thing.
NEVER ask which chart, grouping, how to define a group, column to look at, time frame, growth interval, total-versus-average or view to show, or which number is "the main one":
the app analyses every reasonable option and ranks them. A question is only worth asking if at least one answer changes the data (filters, drops, merges, treats a value as missing,
derives a column) or picks between dates; questions whose answers are all notes or "analyse this column" are discarded by code.
NEVER ask what the glossary already answers. Merge questions about the same thing. Write plain business language, no jargon. At most {MAX_QUESTIONS} questions, the ones whose answer changes the analysis most; none if nothing is ambiguous.

Every option carries an "action" chosen ONLY from this closed list (code executes it; any other action is discarded and the question dropped):
- {{"type":"set_date_column","column":C}}                                  C must be a date column
- {{"type":"define_derived_metric","kind":"ratio_of_sums"|"difference","a":C1,"b":C2,"name":NAME,"use_as_main":true}}
- {{"type":"filter_rows","column":C,"operator":"eq|ne|lt|le|gt|ge|in|not_in|is_null|not_null","value":V}}   KEEPS the rows where the condition holds
- {{"type":"treat_value_as_missing","column":C,"values":[V,...]}}
- {{"type":"handle_negatives","column":C,"mode":"keep"|"exclude"|"net_against_column","against":C2,"name":NAME}}
- {{"type":"drop_column","column":C}}
- {{"type":"merge_columns","columns":[C1,C2],"name":NAME,"how":"first_non_null"|"sum"}}
- {{"type":"note_only"}}                                                    just records the answer as context
The default option must leave the data unchanged (note_only, set_date_column or handle_negatives keep).
Use only column names from the profile. Every question MUST be about one of the SIGNALS and carry its "id" in "signal": a question with no signal is discarded,
so you can only reword what the signals show. The action columns must be among that signal's columns.
Reply with JSON: {{"questions": [{{"text": str, "why": one sentence, "signal": the id of the signal it is about, "columns": [exact column names it is about],
"options": [{{"label": str, "action": {{...}}}}], "default": the label of the default option}}]}}"""


def llm_questions(llm, profile: dict, signals: list[dict], goal: str, ctx: dict | None, saved: dict | None) -> tuple[list[dict] | None, list[str]]:
    """(valid questions, drop reasons). None means the model gave no usable answer, so the caller falls back to the templates."""
    if llm is None:
        return None, []
    ctx_txt = ctxmod.context_text(ctx)
    saved = saved or {}
    live = {sg["id"] for sg in signals}      # a saved answer about an ambiguity this data no longer has must not steer the writer
    known = {"answers": {v.get("question", k): v.get("value") for k, v in (saved.get("answers") or {}).items() if isinstance(v, dict) and k in live},
             "definitions": saved.get("definitions") or {}}
    user = (f"GOAL: {goal}\n\n" + (f"BUSINESS CONTEXT (user-written, unverified):\n{ctx_txt}\n\n" if ctx_txt else "")
            + f"SAVED GLOSSARY (already answered, do not ask again):\n{json.dumps(known, default=str)}\n\n"
            + f"PROFILE ({profile['rows']} rows):\n{json.dumps(compact_profile(profile), default=str)}\n\n"
            + f"SIGNALS:\n{json.dumps([{k: s[k] for k in ('id', 'type', 'columns', 'evidence')} for s in signals], default=str)}")
    out = ask_json(llm, WRITER_SYSTEM, user)
    if not isinstance(out, dict) or not isinstance(out.get("questions"), list):
        if isinstance(out, dict):
            note_failure("validation (the reply had no 'questions' list)")
        return None, []
    qs, dropped = validate_all(out["questions"], profile, signals, limit=MAX_CANDIDATES)
    return (qs if qs or not out["questions"] else None), dropped


def _impact(q: dict) -> tuple:
    """Sort key, most important first: strong before weak, then by kind of ambiguity, then by the rows the default answer touches (when known)."""
    kind = IMPACT.index(q["kind"]) if q.get("kind") in IMPACT else len(IMPACT)
    rows = max([o.get("rows") or 0 for o in q["options"]] or [0])
    return (not q.get("strong"), kind, -rows)


def cap_questions(questions: list[dict], limit: int = MAX_QUESTIONS) -> tuple[list[dict], list[dict]]:
    """(questions with at most `limit` that need an answer, the dropped ones as {question, default}). Questions the goal already settled ("auto") do not count.
    Ranked by how much the answer changes the analysis. A dropped question whose default changes the data (a placeholder set to missing) stays in the list as
    auto-resolved, so its default still runs; any other dropped question has a default that leaves the data untouched, so it just goes."""
    asked = sorted((q for q in questions if not q.get("auto")), key=_impact)        # sorted() is stable: equal impact keeps the original order
    keep, drop = asked[:limit], asked[limit:]
    dropped = [{"id": q["id"], "question": q["text"], "default": option_for(q, None)["label"]} for q in drop]
    runs = {q["id"] for q in drop if not dv.is_harmless(option_for(q, None)["action"])}
    kept = keep + [{**q, "auto": "capped"} for q in drop if q["id"] in runs] + [q for q in questions if q.get("auto")]
    return kept, dropped


def build_questions_and_ignored(df: pd.DataFrame, profile: dict, goal: str, ctx: dict | None, explore: bool, llm, saved: dict | None = None,
                                skip_columns: set | None = None, dropped: list | None = None, raw: pd.DataFrame | None = None, raw_profile: dict | None = None,
                                kinds: dict | None = None, brief: dict | None = None) -> tuple[list[dict], list[dict]]:
    """(questions, saved glossary answers that were ignored). The model writes the questions when there is one; otherwise (or if its answer is unusable) the templates do.
    Weak signals alone never pause a run: they are only included when something stronger already needs an answer.
    A saved answer is ignored when its ambiguity is in this data but the question was dropped as no longer valid, or the saved option no longer exists.
    At most MAX_QUESTIONS are asked; the others are appended to `dropped` (when given) with the default that was used.
    `skip_columns` are columns whose placeholder values were already asked about on the source tables, so no second "is this a placeholder" question is raised for them."""
    skip = skip_columns or set()
    signals = [sg for sg in compute_signals(df, profile, raw, raw_profile, kinds, brief) if sg["type"] not in UNASKED and not (sg["type"] == "sentinel" and set(sg["columns"]) <= skip)]
    if not signals:
        return [], []
    owned = [sg for sg in signals if sg["type"] in CODE_OWNED]
    open_ = [sg for sg in signals if sg["type"] not in CODE_OWNED]
    qs: list[dict] | None = []
    if open_:
        qs, _ = llm_questions(llm, profile, open_, goal, ctx, saved)
        if qs is None:
            qs = template_questions(open_, profile, goal, ctx, explore)
    taken = set(df.columns)
    qs = qs + [product_question(sg, taken) for sg in owned if sg["type"] == "product_basis"] + template_questions([sg for sg in owned if sg["type"] != "product_basis"], profile, goal, ctx, explore)
    if not any(q.get("strong") and not q.get("auto") for q in qs):
        qs = [q for q in qs if q.get("strong")]
    qs, over = cap_questions(qs)
    if dropped is not None:
        dropped.extend(over)
    by_id = {q["id"]: q for q in qs}
    ignored = []
    for qid, a in ((saved or {}).get("answers") or {}).items():
        if not any(sg["id"] == qid for sg in signals):
            continue
        q, value = by_id.get(qid), a.get("value") if isinstance(a, dict) else a
        if q is None or value not in option_values(q):
            ignored.append({"id": qid, "question": (a.get("question") if isinstance(a, dict) else "") or qid, "answer": a.get("label", value) if isinstance(a, dict) else value,
                            "reason": "the question is no longer asked for this data" if q is None else "the saved option no longer exists"})
    return qs, ignored


def build_questions(df: pd.DataFrame, profile: dict, goal: str, ctx: dict | None, explore: bool, llm, saved: dict | None = None) -> list[dict]:
    return build_questions_and_ignored(df, profile, goal, ctx, explore, llm, saved)[0]


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


def date_orders(questions: list[dict], answers: dict) -> dict[str, bool]:
    """{date column: True when day comes first} for the questions about the order of d/m/y dates, with the answer or else the default."""
    return {a["column"]: a["dayfirst"] for _, _, a in chosen(questions, answers) if a["type"] == "set_date_order"}


def target_source(questions: list[dict], answers: dict) -> str:
    """Where the main number in plan_overrides came from, in words for a note: a derived measure the user defined, or their answer to a question."""
    for _, _, a in chosen(questions, answers):
        if a["type"] in ("define_derived_metric", "handle_negatives") and a.get("use_as_main"):
            return "a derived measure you defined"
        if a["type"] == "set_measures" and a.get("pin_main", True):
            break
    return "your answer to a clarifying question"


def plan_overrides(questions: list[dict], answers: dict) -> dict:
    """What the chosen actions change in the plan: main number, date column, columns to leave out, columns to analyse alongside."""
    ov = {"target": None, "date_col": None, "exclude": [], "include": []}
    for _, _, a in chosen(questions, answers):
        t = a["type"]
        if t == "set_date_column":
            ov["date_col"] = ov["date_col"] or a["column"]
        elif t == "set_measures":
            if a.get("pin_main", True):
                ov["target"] = ov["target"] or a["columns"][0]
                ov["include"] += a["columns"][1:]
            else:
                ov["include"] += a["columns"]
        elif t == "define_derived_metric":
            if a["use_as_main"]:
                ov["target"] = ov["target"] or a["name"]
            else:
                ov["include"].append(a["name"])
        elif t == "handle_negatives" and a["mode"] == "net_against_column":
            ov["exclude"] += [a["column"], a["against"]]
            if a["use_as_main"]:
                ov["target"] = ov["target"] or a["name"]
        elif t == "drop_column":
            ov["exclude"].append(a["column"])
        elif t == "merge_columns":
            ov["exclude"] += a["columns"]
            ov["include"].append(a["name"])
    ov["exclude"] = [c for c in dict.fromkeys(ov["exclude"]) if c != ov["target"] and c != ov["date_col"]]
    ov["include"] = list(dict.fromkeys(ov["include"]))
    return ov


def note_lines(questions: list[dict], answers: dict) -> list[str]:
    """Answers that only record context ("note_only") for the LLM prompts. They are unverified user statements."""
    return [f"{q['text']} -> {o['label']}" for q, o, a in chosen(questions, answers) if a["type"] == "note_only" and o["value"] != q["default"]]


def summarise(questions: list[dict], answers: dict, sources: dict, effects: dict, saved: dict | None = None, workspace: str = "") -> list[dict]:
    """One row per answered question for the report: answer, who set it, and the effect on the data (rows affected).
    For answers taken from the saved glossary, `set_by` says which workspace saved it and when (the app has no user accounts, so that is all it knows)."""
    rows = []
    for q in questions:
        qid = q["id"]
        o = option_for(q, answers.get(qid))
        eff = effects.get(qid) or {}
        src = sources.get(qid, "default")
        entry = (saved or {}).get(qid) if src == "glossary" else None
        when = time.strftime("%Y-%m-%d", time.localtime(entry["updated"])) if isinstance(entry, dict) and entry.get("updated") else ""
        rows.append({"id": qid, "question": q["text"], "answer": o["value"], "answer_label": o["label"], "source": src,
                     "set_by": (f"workspace '{workspace}'" + (f", saved {when}" if when else "")) if src == "glossary" else "",
                     "effect": eff.get("effect", "Recorded as context; the data was not changed."), "rows": eff.get("rows")})
    return rows
