"""Briefs: a short, fixed-layout document for any reader, written from verified facts. The reader and purpose are free text; nothing here knows an audience, a topic or a column.

Layout (always): a headline sentence, up to 3 key figures, up to 3 recommendations, up to 2 charts, then the data-quality note. The note is built by code from the saved report: it always shows the
trust label and any data gaps, coverage warning and unreliable-figure notes, and neither the AI nor an edit can remove or soften it.

A brief is saved with the run (lens.json, key "briefs"); the original report is never changed."""
from __future__ import annotations

import re
import time
import uuid

from .. import cited, understanding as und
from . import records, store

MAX_FIGURES, MAX_RECS, MAX_CHARTS = 3, 3, 2
COVERAGE_TEXT = "days in this period have data"
STOP = set("the a an and or of to in on for is are was be my our i we me it its with about most more what how that this these those their his her your you please give make write need want like show tell can could would should do does any some all brief briefs summary one page pager report data sales".split())


def new_id() -> str:
    return "b" + uuid.uuid4().hex[:7]


def terms(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", str(text).lower()) if len(w) > 2 and w not in STOP]


def quality_note(result: dict, fact_ids: list[str], custom: bool = False) -> dict:
    """The data-quality note: {"trust", "notes": [...]}. Always has the trust label; adds data gaps, the coverage warning, and notes on figures that are not statistically reliable or not from the report's own checks."""
    trust = records.trust_of(result)
    notes: list[str] = []
    for g in result.get("data_gaps") or []:
        if g.get("severity") == "warn":
            notes.append(f"Data gap: {records._plain(g.get('title'))}" + (f". {records._plain(g.get('detail'))}" if g.get("detail") else ""))
    for g in [g for g in result.get("data_gaps") or [] if g.get("severity") != "warn"][:2]:
        notes.append(f"Note: {records._plain(g.get('title'))}")
    md = str(result.get("report_md") or "")
    for line in md.splitlines():
        if COVERAGE_TEXT in line:
            notes.append(records._plain(line.lstrip("-*> ")))
            break
    for w in (result.get("warnings") or [])[:3]:
        notes.append(f"Warning: {records._plain(w if isinstance(w, str) else w.get('text') or w.get('message') or w)}")
    by = {f.get("id"): f for f in result.get("findings") or []}
    for fid in fact_ids:
        f = by.get(fid)
        if f and not f.get("significant"):
            notes.append(f"Not statistically reliable: {records._cut(f.get('title') or f.get('text'), 120)}.")
    if custom:
        notes.append("Figures from a custom analysis are calculated by code from a query the AI wrote and have not been through the report's significance checks.")
    seen, out = set(), []
    for n in notes:
        if n and n not in seen:
            seen.add(n)
            out.append(n)
    return {"trust": trust, "notes": out[:10]}


def new_brief(title: str, audience: str, focus: str, plan: dict, headline: dict | None, figures: list[dict], recs: list[dict], charts: list[dict], quality: dict, source: str,
              no_info: str = "", dropped: int = 0, fact_texts: dict | None = None) -> dict:
    now = time.time()
    return {"id": new_id(), "title": title[:90], "audience": audience[:120], "focus": focus[:120], "created": now, "edited": now, "source": source, "no_info": no_info, "headline": headline,
            "key_figures": figures[:MAX_FIGURES], "recommendations": recs[:MAX_RECS], "charts": charts[:MAX_CHARTS], "quality": quality, "dropped": dropped, "plan": plan, "facts": fact_texts or {}}


def find(wd, ref: str, briefs: list[dict] | None = None) -> dict | None:
    """A saved brief by id, or by a title or audience that names exactly one (case-insensitive, words in any order)."""
    bs = briefs if briefs is not None else store.get_briefs(wd)
    ref = (ref or "").strip()
    for b in bs:
        if b["id"] == ref:
            return b
    want = set(terms(ref))
    if not want:
        return None
    hits = [b for b in bs if want <= set(terms(b["title"] + " " + b["audience"]))]
    return hits[0] if len(hits) == 1 else (bs[-1] if ref.lower() in ("last", "latest", "the last one", "it", "that") and bs else None)


def lines_of(b: dict) -> list[str]:
    """The brief as plain lines, for copying as text."""
    out = [b["title"], ""]
    if b.get("no_info"):
        out += [b["no_info"], ""]
    elif b.get("headline"):
        out += [b["headline"]["text"], ""]
    if b.get("key_figures"):
        out += ["Key figures"] + [f"- {x['text']}" for x in b["key_figures"]] + [""]
    if b.get("recommendations"):
        out += ["What to do"] + [f"{i}. {x['text']}" for i, x in enumerate(b["recommendations"], 1)] + [""]
    out += [f"How sure we are: {b['quality']['trust']}"] + [f"- {n}" for n in b["quality"]["notes"]]
    return out


def plain_text(b: dict, result: dict) -> str:
    return "\n".join(lines_of(b))


SECTIONS = ("headline", "key_figures", "recommendations")


def rename(wd, ref: str, title: str) -> dict:
    """Renames a saved brief (the title is the user's own text). Raises ValueError with a plain sentence."""
    bs = store.get_briefs(wd)
    b = find(wd, ref, bs)
    title = " ".join(str(title or "").split())[:90]
    if b is None:
        raise ValueError("I can't tell which brief you mean.")
    if not title:
        raise ValueError("Say the new title.")
    b["title"], b["title_edited"], b["edited"] = title, True, time.time()
    store.put_briefs(wd, [b if x["id"] == b["id"] else x for x in bs])
    return b


def edit(wd, ref: str, section: str, index: int | None, text: str) -> dict:
    """Replaces one line of a saved brief with the user's own text, labelled "Edited by you". The data-quality note and the trust label cannot be edited. Raises ValueError with a plain sentence."""
    bs = store.get_briefs(wd)
    b = find(wd, ref, bs)
    text = " ".join(str(text or "").split())[:300]
    if b is None:
        raise ValueError("I can't tell which brief you mean.")
    if section not in SECTIONS:
        raise ValueError("Only the headline, the key figures and the recommendations can be edited; the data-quality note stays as it is.")
    if not text:
        raise ValueError("Say the new text.")
    part = {"text": text, "ids": [], "edited": True}
    if section == "headline":
        part["ids"] = (b.get("headline") or {}).get("ids", [])
        b["headline"], b["no_info"] = part, ""
    else:
        lst = b[section]
        limit = MAX_FIGURES if section == "key_figures" else MAX_RECS
        if index is None:
            index = len(lst)
        if not 0 <= index <= len(lst):
            raise ValueError(f"There is no line {index + 1} in that part.")
        if index < len(lst):
            part["ids"] = lst[index]["ids"]
            lst[index] = part
        elif len(lst) < limit:
            lst.append(part)
        else:
            raise ValueError("That part is full; edit one of its lines instead.")
    b["edited"] = time.time()
    store.put_briefs(wd, [b if x["id"] == b["id"] else x for x in bs])
    return b


def delete(wd, ref: str) -> dict:
    bs = store.get_briefs(wd)
    b = find(wd, ref, bs)
    if b is None:
        raise ValueError("I can't tell which brief you mean.")
    store.put_briefs(wd, [x for x in bs if x["id"] != b["id"]])
    return b
