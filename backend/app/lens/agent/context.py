"""The context sent with each request: small, checked descriptions only, never raw rows. The last 6 turns, the current tab, the visible items with ids and titles, the focused item, the understanding
record (display names, roles, kinds), the user's saved definitions, and the report's own checked fact sheet, insights, strategies and cleaning log."""
from __future__ import annotations

import json
import re
from collections import Counter

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
    return {"ideas to try": "strategy", "your data": "data", "how files connect": "model", "what we cleaned": "cleaning", "how we analysed": "plan", "export": "powerbi", "lens": "chat"}.get(key or "", key or "")


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


# ------------------------------------------------------------------ what a request could need (the words of the request against the words of each part; nothing is written for one request)
REFERS = re.compile(r"\b(it|its|that|this|these|those|them|they|same|also|again|instead|previous|previously|earlier|before|above|last|first|second|more|less|other|another|now|then|still|but|rather|what about|how about|and)\b", re.I)
SECTION_WORDS = {
    "cleaning_log": {"clean", "cleaned", "cleaning", "log", "step", "steps", "changed", "removed", "fixed", "fix", "duplicates", "duplicate", "quality", "missing", "trust", "reliable", "dropped", "changes"},
    "definitions_used": {"definition", "definitions", "define", "defined", "assumption", "assumptions", "assumed", "used", "mean", "means", "counted"},
    "strategies": {"strategy", "strategies", "idea", "ideas", "action", "actions", "recommend", "recommendation", "recommendations", "should", "improve", "next", "do", "plan", "opportunity", "opportunities", "try"},
    "tabs": {"tab", "tabs", "page", "pages", "section", "sections", "screen", "app", "where", "find", "does", "show", "shows", "mean"},
}
WIDE = 3                 # facts sent when the request names none of them (none for a change of view)
RELEVANT = 8             # at most this many facts, insights and items whose words the request shares


def wants(section: str, env) -> bool:
    from ... import viewselect as vs
    return bool(vs.words(env.question) & vs.words(" ".join(SECTION_WORDS[section]))) or bool(env.focus)


def needs_card(env) -> bool:
    """The data card (what the table holds) is sent when the request is about the numbers or the data's contents, or names a column or a value; not for a pure view change or general talk."""
    from . import suggest
    from ... import viewselect as vs
    kind = suggest.request_kind(env.question, env.ctx, env.df)
    if kind == "data":
        return True
    if kind == "view":
        return False
    return bool(vs.words(env.question) & vs.words("data column columns field fields value values contains contain hold holds rows row file files table dataset missing unique distinct range"))


def relevant_facts(env, facts: list[dict]) -> tuple[list[dict], int]:
    """The facts whose words the request shares (best first), else the first few; and how many were left out. A word found in a quarter or more of the facts says nothing about which one is meant and is ignored."""
    from ... import viewselect as vs
    w = vs.words(env.question)
    own = [vs.words(f["text"]) for f in facts]
    spread = Counter(x for ws in own for x in ws)
    common = {x for x, n in spread.items() if n * 4 >= max(len(facts), 4)}
    w = w - common
    scored = sorted(range(len(facts)), key=lambda i: (-len(w & own[i]), i))
    hit = [i for i in scored if w & own[i]][:RELEVANT]
    from . import suggest
    wide = 0 if suggest.request_kind(env.question, env.ctx, env.df) == "view" else WIDE
    keep = sorted(hit) if hit else list(range(min(wide, len(facts))))
    return [facts[i] for i in keep], len(facts) - len(keep)


def relevant_items(env, items: list[dict]) -> tuple[list[dict], bool]:
    """(the items to list, whether the list is complete). All of them when the request could act on or refer to what is shown; otherwise only those whose titles share a word with it."""
    from ... import viewselect as vs
    from . import suggest
    if env.focus or suggest.request_kind(env.question, env.ctx, env.df) == "view":
        return items, True
    w = vs.words(env.question)
    return [it for it in items if w & vs.words(it["title"])][:RELEVANT], False


def history_for(env) -> tuple[list[dict], str]:
    """The previous turns the request leans on: the last 3 when it refers back (a pronoun, a follow-on word, a very short request), otherwise the last one in brief, otherwise none."""
    n_words = len((env.question or "").split())
    leans = bool(REFERS.search(env.question or "")) or n_words <= 4
    turns = history(env.wd, 3 if leans else 1)
    if not leans:
        turns = [{"question": t["question"], "outcome": t["outcome"]} for t in turns]
    return turns, ("leaned on" if leans else "kept short")


def app_records(env) -> dict:
    """records.app_context, cut to what the request could use. The tools still check every figure against the whole fact sheet; only the prompt is smaller."""
    full = records.app_context(env.result, env.tab)
    facts, left = relevant_facts(env, full["facts"])
    ids = {f["id"] for f in facts}
    out = {"current_tab": full["current_tab"], "report_trust": full["report_trust"], "goal": full["goal"], "facts": facts}
    if left:
        out["more_facts"] = f"{left} more report facts exist; the tools search all of them (report_facts, rank, summarise)."
    out["insights"] = [i for i in full["insights"] if i["id"] in ids]
    if wants("tabs", env) or not facts:
        out["tabs"] = full["tabs"]
    if wants("strategies", env):
        out["strategies"] = full["strategies"]
    if wants("cleaning_log", env):
        out["cleaning_log"] = full["cleaning_log"]
    if wants("definitions_used", env):
        out["definitions_used"] = full["definitions_used"]
    out["understanding"] = full["understanding"]
    return out


def build(env, tools_help: str = "", full: bool = False) -> str:
    """The user part of the plan prompt. full=True (the second ask, after a rejected plan) sends everything; the first ask sends what the request could use. env.prompt_parts: size of each part in characters."""
    ctx = env.ctx
    spans = {ctx.label(k): list(v) for k, v in ctx.spans.items()}
    card_needed = full or needs_card(env)
    card_text = _card(env)          # always built: its lines stay citable (the facts are registered) whether or not they are sent
    items = visible_items(env)
    shown_items, complete = (items, True) if full else relevant_items(env, items)
    turns, how = (history(env.wd), "all") if full else history_for(env)
    sections: list[tuple[str, str]] = [
        ("other", "CURRENT TAB: " + (env.tab or "unknown")),
        ("other", f"TABLE: {ctx.rows} rows" + (f", amounts in {ctx.currency}" if ctx.currency else "")),
        ("other", "DATE SPANS: " + json.dumps(spans, ensure_ascii=False)),
        ("other", "COLUMNS: " + json.dumps(ctxmod.for_prompt(ctx), ensure_ascii=False)),
    ]
    from . import planner
    if full or "custom_analysis" in planner.relevant_tools(env):          # the table's SQL names are only needed by a request that could use custom_analysis
        sql_cols = [{"column": c.name, "label": c.display, "role": c.role, "kind": c.kind} for c in ctx.cols.values()]
        if not full:           # the columns already listed under COLUMNS are the same names; list only the others
            sql_cols = [d for d, c in zip(sql_cols, ctx.cols.values()) if c.role in ("other", "free_text")]
        sections.append(("other", "SQL VIEW \"data\" (for custom_analysis; column = exact name)" + (": " if full else ": every column under COLUMNS by its \"name\"" + (", and also: " if sql_cols else "")) + (json.dumps(sql_cols, ensure_ascii=False) if sql_cols or full else "")))
    m = _matching(env)
    sections.append(("other", "MATCHING VALUES (real stored values for words in the request): " + json.dumps(m, ensure_ascii=False)))
    sections.append(("data_card", ("DATA CARD (what the table holds, no rows): " + card_text) if card_needed else "DATA CARD: left out because the request does not look like it needs it; the data_catalog tool reads it."))
    sections.append(("item_list", "VISIBLE ITEMS (id, kind, title): " + json.dumps(shown_items, ensure_ascii=False)
                     + ("" if complete else f" (only the items whose titles share a word with the request; {len(items)} items are shown on this tab)")))
    if env.focus:
        sections.append(("other", "FOCUS ITEM (the user clicked it; \"this\" means it; use only this text and the data): " + json.dumps({k: env.focus[k] for k in ("id", "title", "kind", "text")}, ensure_ascii=False)))
    d = saved_definitions(env.result)
    if d:
        sections.append(("other", "SAVED DEFINITIONS: " + json.dumps(d, ensure_ascii=False)))
    rec = json.dumps(records.app_context(env.result, env.tab) if full else app_records(env), ensure_ascii=False, default=str)
    sections.append(("fact_sheet", "REPORT RECORDS (no rows): " + rec))
    sections.append(("history", f"PREVIOUS TURNS (oldest first; {how}): " + json.dumps(turns, ensure_ascii=False, default=str)))
    sections.append(("other", "\nREQUEST: " + env.question))
    parts: dict[str, int] = {}
    for name, text in sections:
        parts[name] = parts.get(name, 0) + len(text) + 1
    env.prompt_parts = parts
    return "\n".join(t for _, t in sections)
