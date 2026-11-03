"""Selectors: addressing the items of a report by what they are instead of by id.

Every item carries metadata: `type` (chart, card, insight, action, strategy, section), `measure` and `dimension` (the columns of the finding behind it, as display names), `title`, `rank` (its position
among the items of the same tab and type, in the report's own order), and `cited` (an action or the summary cites it). A selector is a small object over those fields:
    {"type": "chart", "tab": "insights", "section": "<section id>", "measure": "net profit", "dimension": "category", "title": "words", "position": "first" | "last" | 3,
     "rank": 2 | {"lte": 3, "gte": 1}, "cited": true | false, "ids": [...]}
Code resolves a selector to ids; the AI never has to list them, so how many items a command touches is not limited. Nothing here knows a column, a measure or a topic: it matches the user's
words against the labels in the saved report."""
from __future__ import annotations

import re

TYPE_ALIASES = {"chart": "chart", "charts": "chart", "graph": "chart", "graphs": "chart", "picture": "chart", "pictures": "chart", "visual": "chart", "visuals": "chart",
                "card": "card", "cards": "card", "finding": "card", "findings": "card", "tile": "card", "tiles": "card",
                "insight": "insight", "insights": "insight", "headline": "insight", "headlines": "insight",
                "action": "action", "actions": "action", "recommendation": "action", "recommendations": "action",
                "strategy": "strategy", "strategies": "strategy", "idea": "strategy", "ideas": "strategy",
                "section": "section", "sections": "section", "part": "section", "parts": "section"}
FILLER = {"the", "a", "an", "of", "and", "about", "on", "for", "by", "in", "to", "my", "all", "tab", "page", "per"}


def _stem(w: str) -> str:
    return w[:4] if len(w) >= 5 else w


def words(text) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", str(text).lower()) if w not in FILLER and len(w) > 1}


def norm_type(t) -> str | None:
    t = str(t or "").strip().lower()
    return TYPE_ALIASES.get(t, TYPE_ALIASES.get(t.rstrip("s")))


# ------------------------------------------------------------------ item metadata
def _label(result: dict, col) -> str:
    from . import understanding as und
    rec = ((result.get("understanding") or {}).get("columns") or {}).get(col) or {}
    return str(rec.get("display_name") or und.readable_name(str(col)))


def enrich(result: dict, items: dict, tabs: dict, findings: dict, cited_ids: set, action_charts: set) -> None:
    """Adds type, measure, dimension, rank and cited to every item of the catalogue (in place)."""
    counters: dict[tuple, int] = {}
    for iid, it in items.items():
        it["type"] = it["kind"]
        fids = [f for f in it.get("finding_ids") or [] if f in findings]
        measures, dims = [], []
        for f in fids:
            p = findings[f].get("params") or {}
            for k in ("target", "col", "y", "x"):
                if p.get(k):
                    measures.append(str(p[k]))
            for k in ("group", "by", "dim"):
                if p.get(k):
                    dims.append(str(p[k]))
        it["measure"] = [_label(result, c) for c in dict.fromkeys(measures)]
        it["dimension"] = [_label(result, c) for c in dict.fromkeys(dims)]
        key = (it["tab"], it["kind"])
        counters[key] = counters.get(key, 0) + 1
        it["rank"] = counters[key]
        if it["kind"] in ("action", "insight", "strategy"):
            it["cited"] = True                          # actions and headline facts are what cites the rest
        else:
            it["cited"] = bool(any(f in cited_ids for f in it.get("finding_ids") or []) or (it.get("chart") in action_charts) or (it.get("score") or 0) > 0)


# ------------------------------------------------------------------ resolving a selector
def _rank_ok(rank: int, want) -> bool:
    if isinstance(want, bool):
        return False
    if isinstance(want, int):
        return rank == want
    if isinstance(want, dict):
        return (want.get("lte") is None or rank <= want["lte"]) and (want.get("gte") is None or rank >= want["gte"])
    return False


def select(cat: dict, sel: dict, default_tab: str | None = None) -> list[str]:
    """The ids a selector names, in the report's own order. Raises ValueError for a selector that is not an object or has an unknown type word."""
    if not isinstance(sel, dict):
        raise ValueError("A selector must say what to look for.")
    typ = norm_type(sel.get("type") or sel.get("kind")) if (sel.get("type") or sel.get("kind")) else None
    if (sel.get("type") or sel.get("kind")) and typ is None:
        raise ValueError(f"“{str(sel.get('type') or sel.get('kind'))[:30]}” is not a kind of item I know: charts, cards, insights, actions, ideas or sections.")
    explicit = sel.get("ids") if isinstance(sel.get("ids"), list) else ([sel["id"]] if sel.get("id") else None)
    tab = str(sel.get("tab") or default_tab or "").lower() or None
    pool = [(i, it) for i, it in cat["items"].items() if (explicit is None or i in explicit)]
    out = []
    for i, it in pool:
        if typ and it["kind"] != typ:
            continue
        if not typ and it["kind"] == "section" and explicit is None:
            continue
        if tab and it["tab"] != tab:
            continue
        if sel.get("section") and it.get("section") != sel["section"]:
            continue
        if sel.get("measure") and not (words(sel["measure"]) and words(sel["measure"]) <= words(" ".join(it.get("measure") or []))):
            continue
        if sel.get("dimension") and not (words(sel["dimension"]) and words(sel["dimension"]) <= words(" ".join(it.get("dimension") or []))):
            continue
        if sel.get("title") and not (words(sel["title"]) and words(sel["title"]) <= words(it["title"])):
            continue
        if sel.get("rank") is not None and not _rank_ok(it.get("rank", 0), sel["rank"]):
            continue
        if sel.get("cited") is not None and bool(it.get("cited")) != bool(sel["cited"]):
            continue
        out.append(i)
    pos = sel.get("position")
    if pos is not None and out:
        if pos == "first":
            out = out[:1]
        elif pos == "last":
            out = out[-1:]
        elif isinstance(pos, int) and not isinstance(pos, bool) and 1 <= pos <= len(out):
            out = [out[pos - 1]]
        else:
            out = []
    return out


def best(cat: dict, ids: list[str], n) -> list[str]:
    """The best n of these items: the ones the report puts first, with a chart that an action or the summary cites ahead of one that nothing cites."""
    if n is None:
        return ids
    n = max(0, int(n))
    order = {i: k for k, i in enumerate(ids)}
    return sorted(ids, key=lambda i: (-(cat["items"][i].get("score") or 0), order[i]))[:n]


def describe_available(cat: dict, sel: dict, default_tab: str | None = None) -> str:
    """What exists, in plain words, for a selector that matched nothing: the kinds of item the selector allows, with the measures and dimensions they are about."""
    base = {k: v for k, v in sel.items() if k in ("type", "kind", "tab", "section")}
    try:
        ids = select(cat, base, default_tab)
    except ValueError as e:
        return str(e)
    if not ids:
        return "Nothing of that kind is in this report."
    ms, ds = {}, {}
    for i in ids:
        for m in cat["items"][i].get("measure") or []:
            ms[m] = ms.get(m, 0) + 1
        for d in cat["items"][i].get("dimension") or []:
            ds[d] = ds.get(d, 0) + 1
    typ = norm_type(sel.get("type") or sel.get("kind")) or "item"
    name = {"strategy": "idea"}.get(typ, typ)
    parts = [f"There {'is' if len(ids) == 1 else 'are'} {len(ids)} {name}{'' if len(ids) == 1 else 's'}" + (f" on the {base['tab']} tab" if base.get("tab") else "")]
    if ms:
        parts.append("about " + ", ".join(f"{m} ({n})" for m, n in sorted(ms.items(), key=lambda kv: -kv[1])[:8]))
    if ds:
        parts.append("by " + ", ".join(d for d, _ in sorted(ds.items(), key=lambda kv: -kv[1])[:8]))
    return " ".join([parts[0]] + parts[1:2]) + ((", " + parts[2]) if len(parts) > 2 else "") + "."


def what_it_asks(sel: dict) -> str:
    """A short plain phrase for a selector, for messages: 'charts about net profit'."""
    typ = norm_type(sel.get("type") or sel.get("kind")) or "items"
    bits = [{"strategy": "ideas"}.get(typ, typ + ("s" if not typ.endswith("s") else ""))]
    if sel.get("measure"):
        bits.append(f"about {sel['measure']}")
    if sel.get("dimension"):
        bits.append(f"by {sel['dimension']}")
    if sel.get("title"):
        bits.append(f"titled like “{sel['title']}”")
    if sel.get("cited") is not None:
        bits.append("that an action or the summary " + ("cites" if sel["cited"] else "does not cite"))
    return " ".join(bits)
