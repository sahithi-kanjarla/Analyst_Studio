"""The suggestions Lens opens with. Few, and chosen for the tab that is open: at most 3 when an AI model is available (one example of changing what is shown, one question about the data, one summary),
at most 4 without one (the model is not there to take a typed request, so the chips carry more of the load, picked by what the open tab is about and what was just asked). The rest are behind a small
"More ideas" link. Every chip carries its own complete request, so none of them needs an AI to run."""
from __future__ import annotations

from .. import viewedit as ve
from .. import viewselect as vs
from . import chips as chipmod
from . import reports, store

MAX_AI, MAX_NO_AI, MAX_MORE = 3, 4, 8


def _tab_id(tab: str) -> str:
    from .agent.context import tab_id
    return tab_id(tab) if tab else ""


def view_example(result: dict, tab: str, cat: dict | None = None) -> dict | None:
    """One example of changing what is shown on this tab, written from what the tab has: the top 3 of its main items, or hiding its first section, or the short view."""
    t = _tab_id(tab) or next((x for x in ve.TABS if any(i["tab"] == x for i in (cat or ve.catalogue(result))["items"].values())), "")
    cat = cat or ve.catalogue(result)
    if not t or t not in cat["tabs"]:
        return None
    prim = cat["tabs"][t]["primary"]
    secs = cat["tabs"][t]["sections"]
    if len(prim) > 3:
        kind = cat["items"][prim[0]]["kind"]
        word = {"strategy": "ideas", "card": "findings"}.get(kind, kind + "s")
        return reports.ichip(f"Show only the top 3 {word}", {"kind": "view_cmd", "cmds": [{"cmd": "top_n", "tab": t, "n": 3}]})
    if len(secs) > 1:
        title = cat["items"][secs[0]]["title"]
        return reports.ichip(f"Hide {title[0].lower() + title[1:]}", {"kind": "view_cmd", "cmds": [{"cmd": "hide", "id": secs[0]}]})
    return reports.ichip("Make this tab shorter", {"kind": "view_cmd", "cmds": [{"cmd": "set_mode", "tab": t, "mode": "short"}]})


def summary_chip(tab: str) -> dict:
    if _tab_id(tab) == "strategy":
        return reports.ichip("Summarise the ideas to try", {"kind": "summary", "length": "standard", "scope": "ideas to try"})
    return reports.ichip("Summarise the whole report", {"kind": "summary", "length": "standard", "scope": ""})


def _tab_words(result: dict, tab: str, cat: dict) -> set[str]:
    t = _tab_id(tab)
    w: set[str] = set()
    for it in cat["items"].values():
        if it["tab"] == t:
            w |= vs.words(" ".join((it.get("measure") or []) + (it.get("dimension") or [])))
    return w


def for_tab(ctx, df, result: dict, tab: str, ai: bool, wd=None) -> tuple[list[dict], list[dict]]:
    """(the chips to open with, the "More ideas" chips). Each chip is {"id", "text", ...}; at most 3 (an AI) or 4 (none) to open with."""
    cat = ve.catalogue(result)
    start = chipmod.start(ctx, df, result)
    tw = _tab_words(result, tab, cat)
    scored = sorted(start, key=lambda c: -len(tw & vs.words(c["text"])))
    last = None
    if wd is not None:
        for t in reversed(store.load(wd)["turns"]):
            if t["answer"].get("status") == "answered" and t["answer"].get("query"):
                last = t["answer"]["query"]
                break
    n_data = 1 if ai else 2
    if last is not None and not ai:
        data = chipmod.relevant(ctx, df, result, "", last, reports.prev_chip_ids(wd), n_data)       # follow-ups to what was just asked
    else:
        data = scored[:n_data]
    view = view_example(result, tab, cat)
    head = ([view] if view else []) + data + [summary_chip(tab)]
    cap = MAX_AI if ai else MAX_NO_AI
    head = head[:cap]
    ids = {c["id"] for c in head}
    pool = [c for c in scored if c["id"] not in ids] + [c for c in reports.starter_intents(result) if c["id"] not in ids]
    pool += [reports.ichip(x["text"], {"kind": "view_cmd", "cmds": x["cmds"]}) for x in ve.available_changes(result, _tab_id(tab))[:3]]
    seen, more = set(ids), []
    for c in pool:
        if c["id"] not in seen:
            seen.add(c["id"])
            more.append(c)
    return head, more[:MAX_MORE]
