"""What Lens knows about the report itself: the checked fact list (with fact ids), the insights, the actions, the strategies, the cleaning log, the definitions used
and the understanding record. All of it is read from the run's saved result, never from the table's rows. Two uses:
  * `app_context` is the compact description sent to the AI with each question;
  * the quote functions answer questions about the app and the report straight from these records (the AI only chooses the topic)."""
from __future__ import annotations

import json
import re

from .. import cited
from .. import summary as sm

TAB_HELP = {
    "report": "A short report with the answer and what to do. You can print it or save it as a PDF.",
    "dashboard": "Charts for the main numbers. Click any bar or month to filter every chart at once.",
    "insights": "Each finding, how far to trust it, and the actions it supports.",
    "ideas to try": "Ideas to test, ranked by how much they could help and how hard they are.",
    "your data": "The cleaned table behind every number. You can search it, sort it and download it.",
    "how files connect": "How your files were linked together, and how well they matched.",
    "what we cleaned": "Every change made to your data, with the rows it touched.",
    "how we analysed": "The questions we set out to answer, and the checks we ran.",
    "power bi export": "Everything you need to rebuild the charts in Power BI.",
    "chat": "Ask Lens anything about your data, in full width.",
}
TAB_ALIASES = {"strategy": "ideas to try", "data": "your data", "model": "how files connect", "cleaning": "what we cleaned", "plan": "how we analysed", "powerbi": "power bi export",
               "ideas": "ideas to try", "files": "how files connect", "analysis": "how we analysed"}
MAX_FACTS_TO_AI, MAX_ITEM_CHARS = 36, 170


def _rv(result: dict) -> dict:
    return result.get("report_view") or {}


def _plain(text) -> str:
    return " ".join(str(text or "").replace("**", "").split())


def _cut(text: str, n: int = MAX_ITEM_CHARS) -> str:
    t = _plain(text)
    if len(t) <= n:
        return t
    cut = t[:n]
    return (cut[:cut.rfind(" ")] if " " in cut else cut).rstrip(",;: ") + "…"


def tab_key(name: str) -> str | None:
    k = _plain(name).lower()
    k = TAB_ALIASES.get(k, k)
    return k if k in TAB_HELP else None


def tab_in(question: str) -> str | None:
    """A tab named in the question, by its plain name."""
    q = " " + _plain(question).lower() + " "
    best = None
    for k in list(TAB_HELP) + list(TAB_ALIASES):
        if f" {k} " in q or f" {k} tab" in q:
            cand = tab_key(k)
            if cand and (best is None or len(k) > len(best[0])):
                best = (k, cand)
    return best[1] if best else None


# ------------------------------------------------------------------ the checked facts
def headline_items(result: dict) -> list[dict]:
    return [{"id": h["id"], "text": _plain(h["text"])} for h in _rv(result).get("headlines") or [] if h.get("id") and h.get("text")]


def insight_items(result: dict) -> list[dict]:
    """The report's insights: each finding it shows, with its checked headline, what it says and how far to trust it."""
    out = []
    for e in _rv(result).get("evidence") or []:
        if e.get("id") and e.get("headline"):
            out.append({"id": e["id"], "text": _plain(e["headline"]), "detail": _plain(e.get("observation")), "why": _plain(e.get("why_it_matters")),
                        "trust": _plain((e.get("confidence") or {}).get("label")), "title": _plain(e.get("title")), "tier": e.get("tier")})
    return out


def action_items(result: dict) -> list[dict]:
    return [{"text": _plain(a["text"]), "evidence": list(a.get("evidence") or []), "figure": _plain(a.get("figure"))} for a in _rv(result).get("actions") or [] if a.get("text")]


def strategy_items(result: dict) -> list[dict]:
    return [s for s in result.get("strategies") or [] if isinstance(s, dict) and s.get("title")]


def fact_sheet(result: dict) -> list[dict]:
    """[{id, text}]: the report's own headline facts first (the ones it answers the goal with), then its insights. Short, for the AI to cite by id."""
    seen, out = set(), []
    for it in headline_items(result) + [{"id": e["id"], "text": e["text"]} for e in insight_items(result)]:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        out.append({"id": it["id"], "text": _cut(it["text"])})
    return out[:MAX_FACTS_TO_AI]


def known_facts(result: dict) -> dict[str, dict]:
    """id -> the fact in the form `cited.verify` checks a figure against. Built from the same records the report's own checks use: the ranking facts and the findings."""
    out: dict[str, dict] = {}
    for r in (result.get("summary_facts") or {}).get("rankings") or []:
        if r.get("id"):
            blob = json.dumps(r, default=str, ensure_ascii=False)
            out[r["id"]] = cited.make_fact(blob, " ".join(str(r.get(k) or "") for k in ("text", "lead", "group", "measure", "top", "bottom")), cited.scope_of(r))
    for f in result.get("findings") or []:
        if f.get("id"):
            text = _plain(f.get("text")) + " " + _plain(f.get("title"))
            fact = cited.make_fact(text + " " + json.dumps(f.get("stats") or {}, default=str, ensure_ascii=False), text + " " + " ".join(str(v) for v in (f.get("params") or {}).values() if isinstance(v, str)),
                                   cited.scope_of(f))
            out[f["id"]] = fact
    for it in headline_items(result) + insight_items(result):
        base = out.get(it["id"])
        extra = it["text"] + " " + (it.get("detail") or "")
        if base is None:
            out[it["id"]] = cited.make_fact(extra, extra)
        else:
            base["numbers"] = list(base["numbers"]) + cited.allowed_numbers(extra)
            base["subject"] = set(base["subject"]) | sm.tokens(extra)
    return out


def computed_claims(result: dict) -> list[dict]:
    return cited.claims((result.get("summary_facts") or {}).get("rankings") or [])


# ------------------------------------------------------------------ what the AI is given with each question (never rows)
def trust_of(result: dict) -> str:
    return _plain((_rv(result).get("page") or {}).get("trust"))


def app_context(result: dict, tab: str) -> dict:
    cl = [f"{_plain(c.get('step'))}: {_cut(c.get('detail'), 110)}" + (f" ({c['count']:,} rows)" if isinstance(c.get("count"), int) else "") for c in (result.get("cleaning_log") or [])[:12]]
    defs = [f"{_cut(d.get('question'), 90)} -> {_plain(d.get('answer_label'))}" for d in (result.get("applied_definitions") or [])[:8]]
    und = result.get("understanding") or {}
    return {
        "current_tab": tab or "unknown",
        "tabs": TAB_HELP,
        "report_trust": trust_of(result),
        "goal": _cut(result.get("goal"), 200),
        "facts": fact_sheet(result),
        "insights": [{"id": e["id"], "trust": e["trust"]} for e in insight_items(result)[:12]],
        "strategies": [{"id": s.get("id"), "title": _cut(s.get("title"), 100), "impact": s.get("impact")} for s in strategy_items(result) if s.get("kind", "data") == "data"][:8],
        "cleaning_log": cl,
        "definitions_used": defs,
        "understanding": {"source": und.get("source"), "columns_described": len(und.get("columns") or {})},
    }


def for_prompt(result: dict, tab: str) -> str:
    return json.dumps(app_context(result, tab), ensure_ascii=False, default=str)


# ------------------------------------------------------------------ answers quoted from the records
def quote(topic: str, result: dict, tab: str, question: str = "") -> dict | None:
    """{text, lines, sources, fact_ids} for an app or report question, written from the saved records alone. None when the topic has nothing to quote."""
    t = (topic or "").lower()
    rv = _rv(result)
    if t == "tab":
        key = tab_in(question) or tab_key(tab) or None
        if not key:
            return {"text": "These are the tabs: " + "; ".join(f"{k.title()}: {v}" for k, v in TAB_HELP.items()), "lines": [f"{k.title()}: {v}" for k, v in TAB_HELP.items()],
                    "sources": ["the app's tab descriptions"], "fact_ids": []}
        return {"text": f"The {key.title()} tab: {TAB_HELP[key]}", "lines": [], "sources": [f"the app's description of the {key.title()} tab"], "fact_ids": []}
    if t == "trust":
        page = rv.get("page") or {}
        lvl = _plain(page.get("trust"))
        lines, ids = [], []
        for c in page.get("cards") or []:
            if c.get("trust") and c.get("trust_why"):
                lines.append(f"{_cut(c.get('question'), 90)}: {c['trust']}, because {_plain(c['trust_why'])}")
        for e in insight_items(result)[:30]:
            if e["trust"] and e["trust"].lower() != "high" and len(lines) < 5:
                lines.append(f"{_cut(e['text'], 100)} [{e['id']}]: {e['trust']} trust")
                ids.append(e["id"])
        if not lvl and not lines:
            return None
        head = f"The report's overall trust is {lvl}." if lvl else "The report rates each finding separately."
        return {"text": head + (" The reasons saved with the report:" if lines else ""), "lines": lines, "sources": ["the report's trust rating and the reasons saved with it"], "fact_ids": ids}
    if t == "cleaning":
        log = result.get("cleaning_log") or []
        if not log:
            return {"text": "The saved record shows no changes made to your data.", "lines": [], "sources": ["the cleaning log"], "fact_ids": []}
        lines = [f"{_plain(c.get('step'))}: {_cut(c.get('detail'), 200)}" + (f" ({c['count']:,} rows)" if isinstance(c.get("count"), int) else "") for c in log[:10]]
        more = len(log) - len(lines)
        return {"text": f"The cleaning log has {len(log)} step{'s' if len(log) != 1 else ''}." + (" The first ones:" if more > 0 else ""), "lines": lines + ([f"…and {more} more (see the What we cleaned tab)."] if more > 0 else []),
                "sources": ["the cleaning log saved with this run"], "fact_ids": []}
    if t == "definitions":
        defs = result.get("applied_definitions") or []
        if not defs:
            return {"text": "No definitions or assumptions were applied to this run.", "lines": [], "sources": ["the definitions saved with this run"], "fact_ids": []}
        lines = [f"{_plain(d.get('question'))} {_plain(d.get('answer_label'))}." + (f" {_plain(d.get('effect'))}" if d.get("effect") else "") for d in defs[:8]]
        return {"text": f"{len(defs)} definition{'s were' if len(defs) != 1 else ' was'} applied to this run:", "lines": lines, "sources": ["the definitions applied to this run"], "fact_ids": []}
    if t == "understanding":
        und = result.get("understanding") or {}
        cols = und.get("columns") or {}
        if not cols:
            return {"text": "No AI description of your columns was saved with this run; the roles of the columns were worked out by code from the data.", "lines": [], "sources": ["the understanding record"], "fact_ids": []}
        lines = [f"{c.get('display_name') or n}: {c.get('role', '')}" + (f", {_cut(c.get('meaning'), 90)}" if c.get("meaning") else "") for n, c in list(cols.items())[:12]]
        return {"text": f"Lens read {len(cols)} columns this way (from the understanding record):", "lines": lines, "sources": ["the understanding record saved with this run"], "fact_ids": []}
    if t == "goal":
        g = _plain(result.get("goal"))
        return {"text": f"The question this report was built to answer: {g}" if g else "No goal was saved with this run.", "lines": [], "sources": ["the goal saved with this run"], "fact_ids": []}
    if t == "data_gaps":
        gaps = result.get("data_gaps") or []
        if not gaps:
            return {"text": "The report lists no data gaps.", "lines": [], "sources": ["the report's data gaps"], "fact_ids": []}
        return {"text": f"The report lists {len(gaps)} data gap{'s' if len(gaps) != 1 else ''}:", "lines": [f"{_plain(g.get('title'))}: {_cut(g.get('detail'), 160)}" for g in gaps[:6]], "sources": ["the report's data gaps"], "fact_ids": []}
    if t == "insights":
        items = insight_items(result)
        if not items:
            return None
        return {"text": f"The report shows {len(items)} insights. The first ones:", "lines": [f"{_cut(i['text'], 150)} [{i['id']}]" for i in items[:5]], "sources": ["the report's insights"], "fact_ids": [i["id"] for i in items[:5]]}
    if t == "strategies":
        st = [s for s in strategy_items(result) if s.get("kind", "data") == "data"]
        if not st:
            return {"text": _plain(result.get("strategy_summary")) or "The report has no data-backed strategies.", "lines": [], "sources": ["the report's strategies"], "fact_ids": []}
        return {"text": f"The report has {len(st)} data-backed ideas to try, ranked. The first ones:", "lines": [f"{_cut(s['title'], 120)} (impact {s.get('impact')}, effort {s.get('effort')})" for s in st[:5]],
                "sources": ["the report's strategies"], "fact_ids": [i for s in st[:5] for i in (s.get("evidence") or [])][:8]}
    return None


APP_TOPICS = ("tab", "trust", "cleaning", "definitions", "understanding", "goal", "data_gaps", "insights", "strategies")
_STOP = set("a an the of and or to in on for is are was be what which that this these those my our i we me it its with about most more matters matter care really".split())


def stems(text: str) -> set[str]:
    return {sm._stem(w) for w in re.findall(r"[a-z0-9]+", _plain(text).lower()) if w not in _STOP and len(w) > 2}
