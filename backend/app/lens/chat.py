"""One question, start to finish. The AI (when there is one) only turns the question into a query from the closed list, in at most 2 calls; code validates the query,
runs it, writes the sentence and draws the chart. Without an AI, suggested questions map straight to queries and typed text answers only when it clearly matches one."""
from __future__ import annotations

import difflib
import json
import re
import threading
import time
from pathlib import Path

import pandas as pd

from .. import llm as llmmod
from .. import viewedit
from . import chips as chipmod
from . import context as ctxmod
from . import agent, engine, howtext, reasoning, records, render, reports, rules, store, viewchat
from . import max_questions
from . import query as qmod

MAX_AI_CALLS = 2
REPORT_KINDS = ("app", "summary", "priority", "strategy", "general", "view")      # answers about the report and the app, not calculations on the table
HISTORY_TO_AI = 6
MAX_TABLE_ROWS = 60
CHIP_MATCH = 0.95          # typed text runs a suggested question only when it is that question (same words, or a typo away): a near miss with another column name must not be answered with the wrong one
DAILY_PAUSE_S = 600
_DAILY = {"until": 0.0}        # a per-day AI limit stops every run's AI questions in this process for a while: waiting a minute will not help

SYSTEM = """You are Lens, the part of a data report that answers questions. You never calculate and never write a number: you choose ONE action as JSON and code does everything else.
Reply with exactly one of:
{"action":"query","query":{...}}                    a question answered by calculating on the data
{"action":"clarify","question":"<one short question>","choices":[{"label":"<2-4 words>","query":{...}},{"label":"...","query":{...}}]}   (2 or 3 choices, each a complete query)
{"action":"unsupported","reason":"<one plain sentence, no numbers>"}
{"action":"app","topic":"tab|trust|cleaning|definitions|understanding|goal|data_gaps|insights|strategies"}   about the app or the report itself ("what does this tab show?", "why is trust Medium?", "what did you clean?")
{"action":"summary","length":"short|standard"}      "summarise" or "shorten" the report
{"action":"priority","priority":"<what the user cares about, 1-4 words>","terms":["<up to 5 words or close synonyms a report would use for it>"]}   "what matters most for me?", "I care about returns" (terms empty if no priority is named)
{"action":"strategy","focus":"<what to act on, 1-4 words, or empty>"}   "what should we do about X?"
{"action":"general","answer":"<at most 3 short sentences, no numbers>"}   a question that is not about this data or report (how a term is defined, how something works in general)
{"action":"view","commands":[<at most 4 commands>]}   a request to change what the report shows ("make the ideas tab shorter", "remove this chart", "show only the top 3"). Commands are from this closed list; code checks every id and applies them:
""" + viewchat.COMMAND_HELP + """

"action" is exactly one of those words (never an operation name). Example: {"action":"query","query":{"op":"top","measure":"<col>","agg":"sum","group_by":["<col>"],"n":3,"direction":"top"}}
A query is one of these operations. Every "filters" entry looks like: """ + qmod.FILTER_HELP + """
""" + "\n".join(f"- {h}" for h in qmod.OP_HELP.values()) + """
"chart" is optional: auto|bar|line|stacked_bar|share|histogram|scatter|table. "n" is how many groups to show.

Rules:
- Use column names exactly as written in COLUMNS ("name"). Never invent a column or a value. Only "measure" columns can be added up or averaged; group by "category", "flag" or "id" columns only; time comes from "date" columns.
- A value the user names (a city, a tier) goes in a filter or in "a"/"b" exactly as the user wrote it. Code finds the real value.
- A follow-up ("and for Delhi?", "make it monthly", "as a line", "top 5") changes only what the user asked in the previous query (see PREVIOUS TURNS) and keeps the rest.
- "last month", "this year" and similar mean the latest period in DATE SPANS, written as a period such as "2023-11" or "2023".
- A "why" question ("why is X the top?"): return the "query" for the number being asked about (use the main measure when none is named; "revenue" means the money column). Never clarify a "why" question and never explain a cause yourself: code shows what is behind the number. If the question assumes something ("why is X the top?"), still return the query; code checks the assumption.
- Ask to clarify only when two readings give different answers (for example two columns could be "sales"); one short question with 2 or 3 choices. Never ask to confirm something obvious.
- "Any sudden drop or rise?", "any spikes?", "anything unusual over time?" is the unusual_changes operation (use the main measure when none is named).
- "Which groups lose money?" is a top query with direction "bottom" and "only_negative":true.
- A request for the best idea or strategy, with or without "and why", is {"action":"strategy"}. A request to change what a tab, a chart or the report shows is a "view" action using only the listed commands; a request for how it looks (colours, fonts, layout) is "unsupported".
- Use "unsupported" for forecasts, opinions, anything that needs a column that is not listed, or changing data. If margin or profit is asked and no column holds it, that is unsupported.
- For app and report questions use REPORT RECORDS; for anything else about the data use COLUMNS. Never put a number from your own knowledge in any field.
- Return the JSON object only."""


def _norm(t: str) -> str:
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in t).split())


# ------------------------------------------------------------------ data, cached per run folder until its files change
_FRAMES: dict[str, tuple[float, pd.DataFrame, ctxmod.Ctx]] = {}
_FRAMES_LOCK = threading.Lock()


def load_data(wd: Path, result: dict):
    p = Path(wd) / "clean.pkl"
    mt = p.stat().st_mtime
    key = str(wd)
    with _FRAMES_LOCK:
        hit = _FRAMES.get(key)
        if hit and hit[0] == mt:
            return hit[1], hit[2]
    df = pd.read_pickle(p)
    ctx = ctxmod.build(result, df)
    with _FRAMES_LOCK:
        if len(_FRAMES) >= 4:
            _FRAMES.pop(next(iter(_FRAMES)))
        _FRAMES[key] = (mt, df, ctx)
    return df, ctx


# ------------------------------------------------------------------ building an answer
def _table(R: engine.Result) -> dict:
    return {"columns": R.columns, "rows": R.rows[:MAX_TABLE_ROWS], "shown": R.shown[:MAX_TABLE_ROWS], "total_rows": len(R.rows), "truncated": len(R.rows) > MAX_TABLE_ROWS}


def _answered(wd: Path, R: engine.Result, ctx, df, result, source_ai_calls: int, chips: list[dict] | None = None) -> tuple[dict, list[dict]]:
    png = render.chart_file(wd, R.query, R.chart)
    drawn = render.draw(R, png)
    nxt = chips if chips else chipmod.follow_ups(ctx, df, result, R.query)
    lose = chipmod.loses_money(R.query, ctx, df) if R.negative and not R.query.get("only_negative") else None
    if lose:
        nxt = [lose] + [c for c in nxt if c["id"] != lose["id"]][:2]
    ans = {"status": "answered", "text": R.sentence, "query": R.query, "table": _table(R), "chart": {"type": R.chart, "path": f"charts/{png.name}" if drawn else None, "note": R.chart_note},
           "controls": R.controls, "how": R.how, "figures": R.figures, "chips": [{"id": c["id"], "text": c["text"]} for c in nxt], "ai_calls": source_ai_calls}
    return ans, nxt


def _last_query(wd: Path) -> dict | None:
    for t in reversed(store.load(wd)["turns"]):
        if t["answer"].get("status") == "answered" and t["answer"].get("query"):
            return t["answer"]["query"]
    return None


def _last_turn_kind(wd: Path) -> str:
    t = store.load(wd)["turns"]
    return (t[-1]["answer"].get("kind") or "") if t else ""


def _last_strategy_focus(wd: Path) -> str:
    for t in reversed(store.load(wd)["turns"]):
        if t["answer"].get("kind") == "strategy":
            return str(t["answer"].get("focus") or "")
    return ""


def _starter_intents(result: dict) -> list[dict]:
    """The two report questions offered next to the opening data questions; they run without any AI."""
    return [reports.ichip("Summarise this report", {"kind": "summary", "length": "standard"}), reports.ichip("What matters most for me?", {"kind": "priority", "terms": []})]


def _plain(status: str, text: str, wd, ctx, df, result, ai_calls: int = 0, question: str = "") -> tuple[dict, list[dict]]:
    """A reply that is not a calculation (cannot answer, AI not available, limit reached): the message and two questions that fit the topic and were not just offered."""
    if status == "unavailable":
        pick = chipmod.start(ctx, df, result) + _starter_intents(result)
    else:
        pick = chipmod.relevant(ctx, df, result, question, _last_query(wd), reports.prev_chip_ids(wd), 2)
    return {"status": status, "text": text, "chips": [{"id": c["id"], "text": c["text"]} for c in pick], "ai_calls": ai_calls}, pick


def _run_checked(wd: Path, ch: qmod.Checked, ctx, df, result, ai_calls: int, asked: str = ""):
    if ch.ambiguity:
        return _clarify(ch.ambiguity["question"], ch.ambiguity["choices"], wd, ctx, df, result, ai_calls, asked)
    if not ch.query:
        return _plain("no_fit", "I can't run that: " + " ".join(ch.errors[:2]) + " Here are two questions I can answer.", wd, ctx, df, result, ai_calls, asked)
    try:
        R = engine.run(ch.query, df, ctx)
    except engine.Cannot as e:
        return _plain("no_fit", f"{e} Here are two questions I can answer.", wd, ctx, df, result, ai_calls, asked)
    return _answered(wd, R, ctx, df, result, ai_calls)


def _clarify(question: str, choices: list[dict], wd, ctx, df, result, ai_calls: int, asked: str = ""):
    good = []
    for c in choices[:3]:
        ch = qmod.check(c.get("query"), ctx, df)
        if ch.query and isinstance(c.get("label"), str) and c["label"].strip():
            good.append({"id": "k" + store.new_id()[:6], "label": c["label"].strip()[:60], "query": c["query"]})
        elif isinstance(c.get("commands"), list) and isinstance(c.get("label"), str) and c["label"].strip():        # a choice that is a view change (an ambiguous reference to an item: the titles are the choices)
            try:
                viewedit.apply_many(result, store.get_view_state(wd), c["commands"], viewedit.catalogue(result))
            except viewedit.ViewError:
                continue
            good.append({"id": "k" + store.new_id()[:6], "label": c["label"].strip()[:60], "commands": c["commands"]})
    if len(good) == 1 and "query" in good[0]:
        return _run_checked(wd, qmod.check(good[0]["query"], ctx, df), ctx, df, result, ai_calls, asked)
    if len(good) < 2:
        return _plain("no_fit", "I wasn't sure what you meant, and I can't narrow it down. Here are two questions I can answer.", wd, ctx, df, result, ai_calls, asked)
    q = " ".join(str(question).split())[:200]
    return {"status": "clarify", "text": q, "clarify": {"question": q, "choices": good}, "chips": [], "ai_calls": ai_calls}, []


# ------------------------------------------------------------------ the AI step
def _history(wd: Path) -> list[dict]:
    out = []
    for t in store.load(wd)["turns"][-HISTORY_TO_AI:]:
        a = t["answer"]
        item = {"question": t["question"], "outcome": a["status"]}
        if a.get("query"):
            item["query"] = a["query"]
        if a["status"] == "answered":
            item["answer"] = a["text"][:240]
        elif a["status"] == "clarify":
            item["asked"] = a["text"]
            item["choices"] = [c["label"] for c in a["clarify"]["choices"]]
        out.append(item)
    return out


def build_prompt(ctx, question: str, hist: list[dict], tab: str, result: dict | None = None, focus: dict | None = None) -> str:
    spans = {ctx.label(k): list(v) for k, v in ctx.spans.items()}
    return ("CURRENT TAB: " + (tab or "unknown") + f"\nTABLE: {ctx.rows} rows" + (f", amounts in {ctx.currency}" if ctx.currency else "")
            + "\nDATE SPANS: " + json.dumps(spans, ensure_ascii=False) + "\nCOLUMNS: " + json.dumps(ctxmod.for_prompt(ctx), ensure_ascii=False)
            + ("\nREPORT RECORDS (no rows): " + records.for_prompt(result, tab) if result is not None else "")
            + (f"\nFOCUS ITEM (the user pressed \"Ask Lens about this\" on it; answer about it, using only this text and the data): {json.dumps(focus, ensure_ascii=False)}" if focus else "")
            + "\nPREVIOUS TURNS (oldest first, at most 6): " + json.dumps(hist, ensure_ascii=False, default=str) + "\n\nQUESTION: " + question)


def _fail_text(reason: str) -> str:
    low = (reason or "").lower()
    if "daily" in low:
        return "The AI has reached its daily limit, so only the suggested questions work for now. It resets on its own."
    if "rate limit" in low:
        return "The AI is busy right now (rate limit). Please try again in a minute, or pick one of the suggested questions."
    if "timeout" in low:
        return "The AI took too long to answer. Please try again, or pick one of the suggested questions."
    if "auth" in low:
        return "The AI could not be used because its key was refused. The suggested questions still work."
    return "I couldn't get an answer from the AI just now. Please try again, or pick one of the suggested questions."


def _safe_reason(text) -> str:
    t = " ".join(str(text or "").split())
    return t[:160] if t and not any(ch.isdigit() for ch in t) else ""


def translate(llm, wd: Path, ctx, df, question: str, tab: str, result: dict | None = None, focus: dict | None = None):
    """(action, ai_calls, failure reason). At most MAX_AI_CALLS calls: the second is used only to repair a reply that was not JSON or a query that failed the checks."""
    model = llmmod.deterministic(llm)
    user = build_prompt(ctx, question, _history(wd), tab, result, focus)
    feedback, calls = "", 0
    with llmmod.capture() as cap:
        for attempt in range(MAX_AI_CALLS):
            obj, why = llmmod.ask_json_ex(model, SYSTEM, user + feedback, repair=False)
            calls = cap.calls
            if obj is None:
                if why.startswith("bad JSON") and attempt == 0 and calls < MAX_AI_CALLS:
                    feedback = "\n\nYour previous reply was not valid JSON. Answer again with only the JSON object."
                    continue
                return None, calls, why
            act = _action_of(obj)
            if act == "query":
                ch = qmod.check(obj.get("query"), ctx, df)
                if ch.query or ch.ambiguity or attempt == 1 or calls >= MAX_AI_CALLS:
                    return {"kind": "checked", "checked": ch}, calls, ""
                hints = "".join(f" Real values of {ctx.label(c)} include: {', '.join(v)}." for c, v in ch.hints.items())
                feedback = f"\n\nYour previous query was rejected by the checks: {' '.join(ch.errors)}{hints} Answer again with a corrected JSON object, or action \"unsupported\" if it cannot be done."
                continue
            if act == "clarify":
                return {"kind": "clarify", "question": obj.get("question"), "choices": obj.get("choices") if isinstance(obj.get("choices"), list) else []}, calls, ""
            if act == "unsupported":
                return {"kind": "unsupported", "reason": _safe_reason(obj.get("reason"))}, calls, ""
            if act in REPORT_KINDS:
                return {"kind": act, "obj": obj}, calls, ""
            if attempt == 0 and calls < MAX_AI_CALLS:
                feedback = "\n\nYour previous reply did not have a valid \"action\". Answer again with only the JSON object."
                continue
            return {"kind": "unsupported", "reason": ""}, calls, ""
    return None, calls, "no usable reply"


def _action_of(obj: dict) -> str | None:
    """query | clarify | unsupported. A reply that carries a query (or is one) is a query even when its "action" is an operation name or missing: the checks decide, not the label."""
    a = str(obj.get("action") or "").lower()
    if a in ("clarify", "unsupported") or a in REPORT_KINDS:
        return a
    if a == "why" and isinstance(obj.get("query"), dict):
        return "query"
    if isinstance(obj.get("query"), dict):
        return "query"
    if obj.get("op") in qmod.OPS:
        obj["query"] = {k: v for k, v in obj.items() if k != "action"}
        return "query"
    return "query" if a == "query" else None


# ------------------------------------------------------------------ typed text that is really a suggested question
def match_chip(question: str, pool: list[dict]) -> dict | None:
    """The suggested question the typed text IS: the same words in any order, or a typo away. Anything looser goes to the AI (or, without one, gets the chips offered)."""
    q = _norm(question)
    best, score = None, 0.0
    for c in pool:
        t = _norm(c["text"])
        r = 1.0 if sorted(q.split()) == sorted(t.split()) else difflib.SequenceMatcher(None, q, t).ratio()
        if r > score:
            best, score = c, r
    return best if score >= CHIP_MATCH else None


PATCH_KEYS = {"chart", "grain", "n", "sort", "direction"}


def _patch_label(s: dict) -> str:
    if "chart" in s:
        return f"Show as {str(s['chart']).replace('_', ' ')}"
    if "grain" in s:
        return f"Show by {s['grain']}"
    if "n" in s:
        return f"Show {s['n']} rows"
    if "sort" in s or "direction" in s:
        return "Change the order"
    return "Change the chart"


def info(wd: Path, result: dict, demo: bool, enabled: bool = True) -> dict:
    d = store.load(wd)
    out = {"enabled": enabled, "ai": False, "chips": [], "history": d["turns"], "pins": d["pins"], "views": d["views"], "limit": {"asked": d["asked"], "max": max_questions()}}
    if enabled and (Path(wd) / "clean.pkl").exists():
        df, ctx = load_data(wd, result)
        out["chips"] = [{"id": c["id"], "text": c["text"]} for c in chipmod.start(ctx, df, result) + _starter_intents(result)]
    return out


# ------------------------------------------------------------------ a calculation, with the checks and the "why" breakdown
def _with_premise(ans: dict, prem: dict) -> dict:
    """The correction goes first; its figures are added to the answer's own, and the box that says how it was calculated says what was checked."""
    ans["text"] = prem["text"] + " " + ans["text"]
    ans["premise"] = {"text": prem["text"], "claims": prem["claims"]}
    ans["figures"] = prem["figures"] + ans.get("figures", [])
    if ans.get("how"):
        notes = [c.get("note") or f"Checked your question's assumption that {c['value']} is the {'top' if c['direction'] == 'up' else 'lowest'} {ctx_label(c)}: ranked every group by the same calculation." for c in prem["claims"]]
        ans["how"] = howtext.with_lines({**{k: v for k, v in ans["how"].items() if k != "lines"}, "notes": notes + list(ans["how"].get("notes") or [])})
    return ans


def ctx_label(c: dict) -> str:
    return str(c.get("column", "group")).replace("_", " ")


def _fallback_base(wd: Path, ctx, df, result, prem: dict | None) -> dict | None:
    if prem and prem.get("query"):
        return prem["query"]
    return _last_query(wd)


def _data_reply(wd: Path, question: str, ch: qmod.Checked | None, ctx, df, result, ai_calls: int, ai_said_no: bool = False):
    """Runs a checked query for a typed question. A premise the data contradicts is corrected first; a 'why' gets the breakdown behind the number (never a cause)."""
    base = ch.query if ch is not None else None
    prem = reasoning.check_premise(question, df, ctx, result, base)
    why = rules.is_why(question)
    ans = nxt = None
    periods = list(dict.fromkeys(re.findall(r"\b(?:19|20)\d\d(?:-Q[1-4])?\b", question)))
    if why and base and ctx.date_col and len(periods) >= 2 and base.get("op") != "period_change":     # "why did it change between A and B?" is about those two periods
        pc = qmod.check({**{k: base[k] for k in ("measure", "agg", "filters") if k in base}, "op": "period_change", "date": ctx.date_col, "a": periods[0], "b": periods[1]}, ctx, df)
        base = pc.query or base
    named = reasoning.claims(question, df, ctx)
    if why and base and named:        # "why is X the top?" is about X's place among its column: not about X alone, whatever filter the AI chose
        col = named[0]["column"]
        keep = [f for f in base.get("filters") or [] if f.get("column") != col]
        if len(keep) != len(base.get("filters") or []) or col not in (base.get("group_by") or []):
            fixed = qmod.check({**base, "op": "top", "filters": keep, "group_by": [col], "n": 3, "direction": "top", "split_by": []}, ctx, df).query
            base = fixed or base
    if why and not (ch is not None and ch.ambiguity):
        wq = base or (_fallback_base(wd, ctx, df, result, prem) if (ai_said_no or ch is None or not ch.query) and (prem or _last_query(wd)) else None)
        wq = qmod.check(wq, ctx, df).query if wq else None
        out = reasoning.run_why(wq, df, ctx, result) if wq else None
        if out:
            R, wchips = out
            ans, nxt = _answered(wd, R, ctx, df, result, ai_calls, wchips)
            ans["kind"] = "why"
    if ans is None and ch is not None and not ai_said_no:
        ans, nxt = _run_checked(wd, ch, ctx, df, result, ai_calls, question)
    if prem and (ans is None or ans.get("status") != "answered"):
        pq = qmod.check(prem["query"], ctx, df).query
        if pq:
            try:
                ans, nxt = _answered(wd, engine.run(pq, df, ctx), ctx, df, result, ai_calls)
            except engine.Cannot:
                pass
    if ans is None:
        return None
    if prem and ans.get("status") == "answered":
        _with_premise(ans, prem)
    return ans, nxt


# ------------------------------------------------------------------ "this graph is wrong": re-check the most recent chart against its computed table
def _recheck(wd: Path, ctx, df, result):
    """(answer, chips) re-checking the latest drawn chart, or a question about which chart when none was drawn."""
    turn = next((t for t in reversed(store.load(wd)["turns"]) if t["answer"].get("status") == "answered" and t["answer"].get("query") and (t["answer"].get("chart") or {}).get("path")), None)
    if turn is None:
        return _plain("no_fit", "Which chart do you mean? I haven't drawn one in this chat yet. Ask a question about your data first, then I can check its chart.", wd, ctx, df, result)
    try:
        R = engine.run(turn["answer"]["query"], df, ctx)
    except engine.Cannot as e:
        return _plain("no_fit", f"I couldn't re-check that chart: {e}", wd, ctx, df, result)
    drawn = sum(1 for sr in (R.plot.get("series") or [{"values": R.plot.get("values") or R.plot.get("counts") or []}]) for v in sr["values"] if v is not None)
    bad = engine.drawn_vs_table(R)
    if bad:
        verdict = (f"I re-checked the chart for “{turn['question']}” and found {len(bad)} mismatch{'es' if len(bad) != 1 else ''} between what is drawn and the computed table: " + "; ".join(bad[:3])
                   + ". I have redrawn it from the computed table below.")
    else:
        verdict = (f"I re-checked the chart for “{turn['question']}”: it draws {R.fig('values drawn', drawn, ctxmod.fmt_num(drawn))} values, and every one matches the computed table. Nothing is plotted wrongly.")
    caveats = [n for n in R.how.get("notes") or [] if any(w in n for w in ("incomplete", "not shown", "too many", "only part"))]
    if R.how.get("groups_not_shown"):
        caveats.append(f"{R.how['groups_not_shown']} more groups are in the table but not drawn.")
    if R.chart_note:
        caveats.append(R.chart_note)
    if caveats:
        verdict += " What may look odd, and why: " + " ".join(dict.fromkeys(caveats))
    verdict += " If something specific still looks off, tell me what (the order, the chart type or the time grouping) or use Change the chart under the answer."
    R.sentence = verdict
    R.how = howtext.with_lines({**R.how, "operation": "Re-ran the same calculation, then compared the values the chart is drawn from with the computed table, value by value",
                                "notes": list(R.how.get("notes") or []) + ["Nothing was recalculated differently: the chart and the table come from the same numbers."]})
    ans, nxt = _answered(wd, R, ctx, df, result, 0)
    ans["kind"], ans["label"] = "recheck", "Chart re-check"
    return ans, nxt


# ------------------------------------------------------------------ the report and app answers
def _report_answer(wd: Path, result: dict, ctx, df, kind: str, spec: dict, question: str, tab: str, llm, ai_ok: bool, ai_calls: int, focus_id: str | None = None, view_state: dict | None = None):
    """(answer, chips, AI calls used here, failure reason). `spec` is what the AI chose, or what a plain rule or a suggested question stands for."""
    if kind in ("view", "view_cmd"):
        return viewchat.answer(wd, result, spec, question, tab, llm, ai_ok, ai_calls, focus_id, view_state)
    if kind == "focus":
        return (*reports.answer_focus(result, focus_id or "", wd, ai_calls), 0, "")
    if kind == "recheck":
        return (*_recheck(wd, ctx, df, result), 0, "")
    if kind == "app":
        topic = spec.get("topic") if spec.get("topic") in records.APP_TOPICS else "tab"
        return (*reports.answer_app(topic, result, tab, question, wd, ai_calls), 0, "")
    if kind == "priority":
        terms = [str(t) for t in (spec.get("terms") or []) if isinstance(t, (str, int))] or rules.priority_terms(question)
        phrase = str(spec.get("priority") or "").strip() or " ".join(terms)
        return (*reports.answer_priority(result, ctx, terms, phrase, wd, ai_calls), 0, "")
    if kind == "strategy":
        return (*reports.answer_strategy(result, ctx, str(spec.get("focus") or ""), wd, ai_calls, explain=rules.is_why(question)), 0, "")
    if kind == "strategy_why":
        return (*reports.answer_strategy_why(result, ctx, _last_strategy_focus(wd), wd, ai_calls), 0, "")
    if kind == "general":
        return (*reports.answer_general(spec.get("answer"), result, wd, ai_calls), 0, "")
    length = spec.get("length") if spec.get("length") in reports.SUMMARY_LINES else "standard"
    return reports.answer_summary(result, wd, llm, length, MAX_AI_CALLS - ai_calls, ai_ok, ai_calls)


def _readable_names(ans: dict, ctx) -> None:
    """Report answers quote the report's own wording, which may carry a raw column name (discount_pct): show the display name instead. Wording only."""
    if ans.get("kind") not in ("strategy", "priority", "summary", "general"):        # records quoted word for word (cleaning log, definitions) stay as saved
        return
    for k in ("text",):
        if isinstance(ans.get(k), str):
            ans[k] = ctx.readable(ans[k])
    if isinstance(ans.get("lines"), list):
        ans["lines"] = [ctx.readable(x) if isinstance(x, str) else x for x in ans["lines"]]


def ask(wd: Path, result: dict, *, question: str = "", chip: str | None = None, choice: dict | None = None, patch: dict | None = None, tab: str = "",
        llm=None, allow_ai: bool = True, focus: dict | None = None, view_state: dict | None = None) -> dict:
    """Answers one question and saves the turn. Exactly one of question / chip / choice / patch is used (in that order of the checks below)."""
    question = " ".join((question or "").split())[:300]
    df, ctx = load_data(wd, result)
    focus_ctx = viewedit.item_context(result, str((focus or {}).get("id") or "")) if focus else None       # "Ask Lens about this": the item's own words, from the saved report
    ai_calls, count_ai, label, raw, route = 0, False, question, None, None
    ans, nxt = None, []
    asked = store.load(wd)["asked"]
    ai_ok = llm is not None and allow_ai and asked < max_questions() and time.time() >= _DAILY["until"]

    if chip:
        known = chipmod.start(ctx, df, result) + _starter_intents(result)
        hit = next((c for c in known if c["id"] == chip), None) or store.known_chip(wd, chip) and {**store.known_chip(wd, chip), "id": chip}
        if not hit:
            ans, nxt = _plain("no_fit", "That suggestion is no longer available. Here are two questions I can answer.", wd, ctx, df, result)
        elif hit.get("intent"):
            label, route = hit["text"], hit["intent"]
        else:
            label, raw = hit["text"], hit["query"]
    elif choice:
        t = store.get_turn(wd, str(choice.get("turn")))
        c = next((x for x in ((t or {}).get("answer", {}).get("clarify") or {}).get("choices", []) if x["id"] == choice.get("choice")), None)
        if not c:
            ans, nxt = _plain("no_fit", "That choice is no longer available. Please ask again.", wd, ctx, df, result)
        elif c.get("commands"):
            label, route = c["label"], {"kind": "view_cmd", "cmds": c["commands"]}
        else:
            label, raw = c["label"], c["query"]
    elif patch:
        t = store.get_turn(wd, str(patch.get("turn")))
        base = ((t or {}).get("answer") or {}).get("query")
        s = {k: v for k, v in (patch.get("set") or {}).items() if k in PATCH_KEYS}
        if not base or not s:
            ans, nxt = _plain("no_fit", "I can't change that chart. Here are two questions I can answer.", wd, ctx, df, result)
        else:
            label, raw = _patch_label(s), {**base, **s}
    elif question:
        gap = rules.margin_gap(question, ctx)
        pool = chipmod.start(ctx, df, result) + _starter_intents(result) + [{"id": k, **v} for k, v in store.load(wd)["chips"].items()]
        hit = match_chip(question, pool)
        found = (hit or {}).get("intent") or (None if ai_ok else rules.route(question))          # plain-word routing is the no-AI fallback; with an AI the planner decides
        if not found and not ai_ok and focus_ctx and viewchat.parse_rules(question, tab, viewedit.catalogue(result), focus_ctx["id"]) is not None:
            found = {"kind": "view"}          # "hide this", "pin it", "rename this to ...", "show it as a line": a change to the item in focus
        if not found and not ai_ok and rules.asks_choice(question) and _last_turn_kind(wd) in ("strategy",):
            found = {"kind": "strategy_why"}
        if found and found["kind"] == "priority" and ai_ok and not (hit or {}).get("intent"):
            found = None          # the AI picks the framing of "what matters to me": it is asked below
        if gap:
            ans, nxt = _plain("no_fit", gap + " Here are two questions I can answer.", wd, ctx, df, result, 0, question)
        elif found:
            route = found
        elif hit and hit.get("query"):
            raw = hit["query"]
        elif (llm is None or not allow_ai) and ctx.date_col and rules.asks_unusual(question):
            m = chipmod.main_measure(ctx, result)
            raw = {"op": "unusual_changes", "agg": "sum" if m else "count", "measure": m.name if m else None, "date": ctx.date_col, "grain": "month"}
        elif (llm is None or not allow_ai) and focus_ctx:
            route = {"kind": "focus"}
        elif llm is None or not allow_ai:
            fb = agent.run_fallback(wd, result, df, ctx, question, tab, focus_ctx, view_state)
            out = _data_reply(wd, question, None, ctx, df, result, 0) if fb is None and reasoning.asserts(question, df, ctx) else None
            if fb is not None:
                ans, nxt = fb.ans, fb.chips
            elif out:
                ans, nxt = out
            else:
                agent.log_unmet(wd, question, tab)
                ans, nxt = _plain("unavailable", "Without an AI model I can answer the suggested questions only. Pick one of these.", wd, ctx, df, result)
        elif asked >= max_questions():
            ans, nxt = _plain("limit", f"You've reached this run's limit of {max_questions()} questions that use the AI. The suggested questions still work.", wd, ctx, df, result)
        elif time.time() < _DAILY["until"]:
            ans, nxt = _plain("unavailable", _fail_text("daily limit"), wd, ctx, df, result)
        else:
            llmmod.begin_run()       # this request starts clean: no stop or call record left over from another request on the same thread
            out = agent.run_request(wd, result, df, ctx, question, tab, llm, True, focus_ctx, view_state)
            ai_calls, count_ai = out.calls, out.calls > 0
            if out.ans is None:
                if "daily" in out.fail:
                    _DAILY["until"] = time.time() + DAILY_PAUSE_S
                ans, nxt = _plain("unavailable", _fail_text(out.fail), wd, ctx, df, result, ai_calls)
            else:
                ans, nxt = out.ans, out.chips
                ans["ai_calls"] = ai_calls
    else:
        ans, nxt = _plain("no_fit", "Type a question about your data, or pick one of these.", wd, ctx, df, result)

    if ans is None and route is not None:
        if route["kind"] == "summary" and ai_ok and not count_ai:
            llmmod.begin_run()
        ans, nxt, used, fail = _report_answer(wd, result, ctx, df, route["kind"], route, label or question, tab, llm, ai_ok, ai_calls, (focus_ctx or {}).get("id"), view_state)
        ai_calls += used
        if used:
            count_ai = True
        if "daily" in fail:
            _DAILY["until"] = time.time() + DAILY_PAUSE_S
        ans["ai_calls"] = ai_calls
        _readable_names(ans, ctx)
        ans["source"] = "chip" if chip else "ai" if count_ai else "code"
    if ans is None:
        ans, nxt = _run_checked(wd, qmod.check(raw, ctx, df), ctx, df, result, ai_calls, label)
        ans["source"] = "chip" if chip else "choice" if choice else "patch" if patch else "typed-chip"
    ans.setdefault("source", "ai" if count_ai else "code")
    if focus_ctx:
        ans["focus"] = {"id": focus_ctx["id"], "title": focus_ctx["title"]}
    turn = {"id": store.new_id(), "at": time.time(), "question": label or "(no question)", "tab": tab, "answer": ans}
    store.add_turn(wd, turn, nxt, count_ai)
    return turn
