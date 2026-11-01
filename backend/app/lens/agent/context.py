"""The context sent with each request: small, checked descriptions only, never raw rows. The last 6 turns, the current tab, the visible items with ids and titles, the focused item, the understanding
record (display names, roles, kinds), the user's saved definitions, and the report's own checked fact sheet, insights, strategies and cleaning log."""
from __future__ import annotations

import json

from ... import viewedit
from .. import context as ctxmod
from .. import records, store

HISTORY_TO_AI = 6
MAX_ITEMS_SHOWN = 80


def visible_items(env) -> list[dict]:
    """The items of the current tab that are not hidden, as {id, kind, title}. When the tab is unknown, the first tabs with items."""
    cat = env.cat
    tab = tab_id(env.tab)
    state = env.view_state if env.view_state is not None else store.get_view_state(env.wd)
    hidden = set(viewedit.normalise(state)["hidden"])
    out = [{"id": i, "kind": it["kind"], "title": it["title"]} for i, it in cat["items"].items() if it["tab"] == tab and i not in hidden and it.get("section") not in hidden]
    return out[:MAX_ITEMS_SHOWN]


def tab_id(label: str) -> str:
    """The tab id for a tab's label as the page names it ("Ideas to try" -> strategy), else the lower-cased label if it is a tab id, else ''."""
    low = (label or "").strip().lower()
    for t, n in viewedit.TAB_NAMES.items():
        if low == n.lower() or low == t:
            return t
    key = records.tab_key(low)
    return {"ideas to try": "strategy", "your data": "data", "how files connect": "model", "what we cleaned": "cleaning", "how we analysed": "plan", "power bi export": "powerbi"}.get(key or "", key or "")


def _matching(env) -> dict:
    from .tools.custom_analysis import matching_values
    try:
        return matching_values(env)
    except Exception:           # noqa: BLE001  a value lookup must never stop a request
        return {}


def _card(env) -> str:
    from . import datacard
    card = datacard.get(env.ctx, env.df)
    if "datacard_facts" not in env.ctx.memo:
        env.ctx.memo["datacard_facts"] = datacard.facts(card)
    env.facts.update(env.ctx.memo["datacard_facts"])          # a sentence about the data can cite a card line, and its figures are checked against it
    text, _ = datacard.render(card, env.question, str(env.result.get("goal") or ""))
    return text


def history(wd, n: int = HISTORY_TO_AI) -> list[dict]:
    out = []
    for t in store.load(wd)["turns"][-n:]:
        a = t["answer"]
        item = {"question": t["question"], "outcome": a["status"]}
        if a.get("query"):
            item["query"] = a["query"]
        if a.get("agent"):
            item["tools"] = a["agent"].get("tools")
        if a["status"] == "answered":
            item["answer"] = str(a["text"])[:240]
        elif a["status"] == "clarify":
            item["asked"] = a["text"]
            item["choices"] = [c["label"] for c in a["clarify"]["choices"]]
        out.append(item)
    return out


def saved_definitions(result: dict) -> list[dict]:
    return [{"question": d.get("question"), "answer": d.get("answer_label") or d.get("answer"), "effect": records._cut(d.get("effect"), 120)} for d in (result.get("applied_definitions") or [])[:12]]


def build(env, tools_help: str = "") -> str:
    """The user part of the plan prompt."""
    ctx = env.ctx
    spans = {ctx.label(k): list(v) for k, v in ctx.spans.items()}
    parts = [
        "CURRENT TAB: " + (env.tab or "unknown"),
        f"TABLE: {ctx.rows} rows" + (f", amounts in {ctx.currency}" if ctx.currency else ""),
        "DATE SPANS: " + json.dumps(spans, ensure_ascii=False),
        "COLUMNS: " + json.dumps(ctxmod.for_prompt(ctx), ensure_ascii=False),
        "SQL VIEW \"data\" (for custom_analysis; column = exact name): " + json.dumps([{"column": c.name, "label": c.display, "role": c.role, "kind": c.kind} for c in ctx.cols.values()], ensure_ascii=False),
        "MATCHING VALUES (real stored values for words in the request): " + json.dumps(_matching(env), ensure_ascii=False),
        "DATA CARD (what the table holds, no rows): " + _card(env),
        "VISIBLE ITEMS (id, kind, title): " + json.dumps(visible_items(env), ensure_ascii=False),
    ]
    if env.focus:
        parts.append("FOCUS ITEM (the user clicked it; \"this\" means it; use only this text and the data): " + json.dumps({k: env.focus[k] for k in ("id", "title", "kind", "text")}, ensure_ascii=False))
    d = saved_definitions(env.result)
    if d:
        parts.append("SAVED DEFINITIONS: " + json.dumps(d, ensure_ascii=False))
    parts.append("REPORT RECORDS (no rows): " + records.for_prompt(env.result, env.tab))
    parts.append("PREVIOUS TURNS (oldest first, at most 6): " + json.dumps(history(env.wd), ensure_ascii=False, default=str))
    parts.append("\nREQUEST: " + env.question)
    return "\n".join(parts)
