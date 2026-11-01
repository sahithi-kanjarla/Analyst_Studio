"""Answers about the report and the app, as opposed to calculations on the table: quoting the saved records, a short version, ranking by what matters to the user, a
suggestion from the report's strategies, a general-knowledge reply, and the reply to a request to change the view.
The AI (when there is one) only chooses the kind of answer, the topic, the framing words, or words a short version from the checked facts. Code quotes, ranks, checks
every figure against the report's fact list (the same citation checks as the report) and drops whatever fails. Every answer here carries a non-empty "how"."""
from __future__ import annotations

import hashlib
import json
import re
import time

from .. import cited, llm as llmmod
from .. import strategy as strat
from .. import summary as sm
from . import howtext, records, store

SUMMARY_LINES = {"short": 3, "standard": 5}
MAX_ACTIONS = 3
CAUSAL = re.compile(r"\b(because|due to|caused?|driven by|as a result of|thanks to|owing to|the reason)\b", re.I)
GENERAL_LABEL = "General knowledge, not from your data"
VIEW_TEXT = ("Changing what a tab or chart shows from here is coming soon, so I haven't changed anything. What works now: on a short version of the report, “Use as my short version”; "
             "“Pin to report” or “Pin to dashboard” under any answer; and “Change the chart” under an answer, to switch the chart type, the time grouping or how many rows are shown. "
             "I can also answer questions about your data, explain what each tab shows, summarise the report and rank its findings by what matters to you.")
MAX_LINE_WORDS = 20

SUMMARY_SYSTEM = """You shorten a data report for a busy reader. You may use ONLY the checked facts given, each with an id. Write at most {n} lines.
Each line is ONE plain sentence of at most 20 words about one fact, with that fact's figure, e.g. "Gold customers bring in the most: 17.5M, 38% of the total [r23]."
Say names the way a person would: "Gold customers", "Electronics sales", never a column label or code word such as "tier Gold" or "category Electronics", and never repeat a label or a word in the same line.
Write the id of the fact a figure comes from in square brackets right after the figure, before any full stop. Every number needs an id,
you may not write a number that is not in the fact you cite, and the words around a figure must be about that fact's subject. Do not claim a cause and do not recommend anything.
A percent change must name what it is a change of (the measure as the fact words it, for example "amount") and its two periods right beside the figure, e.g. "Gold amount grew 41.0% from 2023 to 2024 [r24]." Every line must state at least one figure from a fact and cite that fact's id; leave out anything that has no figure.
Return JSON: {"lines": ["...", "..."]}"""


def ichip(text: str, intent: dict) -> dict:
    return {"id": "i_" + hashlib.sha1((text + json.dumps(intent, sort_keys=True)).encode()).hexdigest()[:10], "text": text, "intent": intent}


def next_chips(result: dict, kind: str, topic: str = "", exclude: set[str] | None = None, n: int = 3) -> list[dict]:
    """Two or three 'what to ask next' questions that fit the answer just given and run without any AI."""
    lvl = records.trust_of(result)
    defs = {
        "summary": ichip("Summarise this report", {"kind": "summary", "length": "standard"}),
        "short": ichip("Make it shorter", {"kind": "summary", "length": "short"}),
        "matters": ichip("What matters most for me?", {"kind": "priority", "terms": []}),
        "trust": ichip(f"Why is trust {lvl}?" if lvl else "How far can I trust this report?", {"kind": "app", "topic": "trust"}),
        "clean": ichip("What did you clean?", {"kind": "app", "topic": "cleaning"}),
        "tab": ichip("What does this tab show?", {"kind": "app", "topic": "tab"}),
        "ideas": ichip("What should we do next?", {"kind": "strategy", "focus": ""}),
        "defs": ichip("Which definitions did you use?", {"kind": "app", "topic": "definitions"}),
        "why": ichip("Why did you choose it?", {"kind": "strategy_why"}),
    }
    order = {"summary": ["matters", "ideas", "trust"], "priority": ["ideas", "summary", "trust"], "strategy": ["why", "matters", "summary"], "strategy_why": ["matters", "summary", "trust"], "general": ["summary", "matters", "tab"],
             "view": ["tab", "summary", "matters"], "app:trust": ["clean", "summary", "matters"], "app:cleaning": ["defs", "trust", "summary"], "app:tab": ["summary", "trust", "clean"],
             "app:definitions": ["clean", "trust", "summary"]}.get(f"{kind}:{topic}" if kind == "app" else kind, ["summary", "clean", "trust"])
    skip = exclude or set()
    out = [defs[k] for k in order if defs[k]["id"] not in skip]
    out += [defs[k] for k in order if defs[k]["id"] in skip]
    return out[:max(2, min(3, n))]


def _base(kind: str, text: str, label: str, how: dict, chips: list[dict], ai_calls: int, **extra) -> tuple[dict, list[dict]]:
    ans = {"status": "answered", "kind": kind, "label": label, "text": text, "lines": [], "actions": [], "how": how, "figures": [], "chips": [{"id": c["id"], "text": c["text"]} for c in chips], "ai_calls": ai_calls}
    ans.update(extra)
    return ans, chips


def prev_chip_ids(wd) -> set[str]:
    return {c["id"] for t in store.load(wd)["turns"][-2:] for c in t["answer"].get("chips") or []}


# ------------------------------------------------------------------ questions about the app and the report: quoted from the saved records
def answer_app(topic: str, result: dict, tab: str, question: str, wd, ai_calls: int = 0) -> tuple[dict, list[dict]]:
    q = records.quote(topic, result, tab, question)
    chips = next_chips(result, "app", topic if topic in ("trust", "cleaning", "tab", "definitions") else "", prev_chip_ids(wd))
    if q is None:
        return _base("app", "The records saved with this run don't include that, so I can't answer it from them.", "From your report", howtext.record_how("Looked in the run's saved records and found nothing on this", ["this run's saved records"]),
                     chips, ai_calls)
    how = howtext.record_how(f"Quoted the run's saved records on {topic.replace('_', ' ')}", q["sources"], q["fact_ids"], ["Nothing was recalculated; the wording is copied from the saved record."])
    return _base("app", q["text"], "From your report", how, chips, ai_calls, lines=q["lines"], fact_ids=q["fact_ids"])


# ------------------------------------------------------------------ the short version
def _first_sentence(t: str, limit: int = 230) -> str:
    t = " ".join(str(t).split())
    cut = re.split(r"(?<=[.!?])\s+(?=[A-Z])", t, maxsplit=1)[0]
    if len(cut) <= limit:
        return cut.rstrip(".") + "."
    part = cut[:limit]
    return part[:max(part.rfind(","), part.rfind(";"), part.rfind(" "))].rstrip(",; ") + "."


def code_lines(result: dict, n: int) -> list[dict]:
    """The report's own checked headline sentences, as plain lines of at most MAX_LINE_WORDS words. Used when there is no AI or its wording did not pass the checks."""
    ranks = {r["id"]: r for r in (result.get("summary_facts") or {}).get("rankings") or [] if r.get("id")}
    groups = {str(r["group"]) for r in ranks.values() if r.get("group")}
    out = []
    for h in records.headline_items(result):
        r = ranks.get(h["id"]) or {}
        text = plain_line(r.get("short") or "", groups) or plain_line(_first_sentence(h["text"]), groups)
        if text:
            out.append({"text": text, "ids": [h["id"]]})
    if len(out) < n:
        for e in records.insight_items(result):
            if e["tier"] in (1, 2, None):
                text = plain_line(e["text"], groups)
                if text:
                    out.append({"text": text, "ids": [e["id"]]})
    seen, uniq = set(), []
    for o in out:
        if o["text"] not in seen:
            seen.add(o["text"])
            uniq.append(o)
    return uniq[:n]


_WORD = re.compile(r"[A-Za-z][A-Za-z'‐-―-]*")
_SMALL = set("the a an of and or to in on for is are was be by with at as it its that this from than more most per vs".split())


def words_in(text: str) -> int:
    return len(re.findall(r"\S+", text))


def repeats_a_label(text: str) -> bool:
    """The same word twice within three words ("tier tier", "Gold customers: Gold"): a label written out twice."""
    ws = [(m.group(0).lower(), m.start()) for m in _WORD.finditer(text)]
    last: dict[str, int] = {}
    for i, (w, _) in enumerate(ws):
        if len(w) > 2 and w not in _SMALL:
            if w in last and i - last[w] <= 3:
                return True
            last[w] = i
    return False


def plain_line(text: str, groups: set[str] | None = None) -> str:
    """A report sentence as one plain line: the column word in front of a value ("tier Gold") dropped, then cut at a clause so it fits MAX_LINE_WORDS and still carries a figure. "" when that is not possible."""
    t = " ".join(str(text).split())
    for g in sorted(groups or (), key=len, reverse=True):
        t = re.sub(rf"\b{re.escape(g)}\s+(?=[A-Z0-9])", "", t)
    t = re.sub(r"\s*\([^)]*\b(?:statistically|not reliable|full periods)[^)]*\)", "", t)
    if words_in(t) > MAX_LINE_WORDS:
        for sep in (", ", "; ", " and ", ": ", " - ", " while ", " but ", " which ", " in the ", " from "):
            head = t
            while sep in head and words_in(head) > MAX_LINE_WORDS:
                head = head[:head.rindex(sep)]
            if words_in(head) <= MAX_LINE_WORDS and re.search(r"\d", head):
                t = head
                break
        else:
            return ""
    t = t.rstrip(" ,;:.")
    return t + "." if t and re.search(r"\d", t) and not repeats_a_label(t) else ""


def numbers_in(text: str) -> bool:
    """A line of the short version states at least one figure (a line with none could say anything about the facts it cites, and nothing would check it)."""
    return bool(re.search(r"\d", re.sub(r"\b(?:19|20)\d\d\b", " ", text)))


def ai_lines(llm, result: dict, n: int) -> tuple[list[dict], str, int]:
    """(verified lines, failure reason, how many were dropped). Each line must cite a fact, every figure must match that fact, and no cause may be claimed."""
    facts = records.fact_sheet(result)
    sheet = [{"id": f["id"], "text": f["text"]} for f in facts[:18]]
    full = {h["id"]: h["text"] for h in records.headline_items(result)}
    for f in sheet:
        if f["id"] in full:
            f["text"] = records._cut(full[f["id"]], 300)
    out, why = llmmod.ask_json_ex(llmmod.deterministic(llm), SUMMARY_SYSTEM.replace("{n}", str(n)), f"GOAL: {records._cut(result.get('goal'), 200)}\nCHECKED FACTS:\n" + json.dumps(sheet, ensure_ascii=False), repair=False)
    if not out or not isinstance(out.get("lines"), list):
        return [], why or "no usable reply", 0
    known, computed = records.known_facts(result), records.computed_claims(result)
    good, dropped = [], 0
    for raw in out["lines"]:
        if not isinstance(raw, str):
            dropped += 1
            continue
        text, reason = cited.verify(raw, known)
        _, marks = cited.split_cites(raw)
        ids = [i for _, group in marks for i in group if i in known]
        if reason or not ids or not numbers_in(text) or CAUSAL.search(text) or cited.contradicts(text, computed) or not 20 <= len(text.strip()) <= 300 \
                or words_in(text) > MAX_LINE_WORDS or repeats_a_label(text):
            dropped += 1
            continue
        good.append({"text": text.strip(), "ids": list(dict.fromkeys(ids))})
    return good[:n], "", dropped


def answer_summary(result: dict, wd, llm, length: str, budget: int, allow_ai: bool, ai_calls_before: int = 0, scope_label: str = "the whole report", lines_override: list[dict] | None = None) -> tuple[dict, list[dict], int, str]:
    """(answer, chips, AI calls used here, failure reason). The short version has at most 5 lines (3 when asked to shorten) plus the top 3 actions."""
    n = SUMMARY_LINES.get(length, 5)
    used, fail, dropped, lines, source = 0, "", 0, [], "code"
    if lines_override is not None:
        lines = lines_override[:n]
    elif llm is not None and allow_ai and budget >= 1:
        with llmmod.capture() as cap:
            lines, fail, dropped = ai_lines(llm, result, n)
            used = cap.calls
        source = "ai"
        if len(lines) < 2:                     # the AI's wording did not survive the checks: use the report's own checked sentences
            lines, source = [], "code"
    if not lines:
        lines = code_lines(result, n)
    acts = records.action_items(result)[:MAX_ACTIONS]
    ids = list(dict.fromkeys([i for ln in lines for i in ln["ids"]] + [i for a in acts for i in a["evidence"]]))
    notes = [f"{dropped} line{'s' if dropped != 1 else ''} the AI wrote did not pass the figure checks and {'was' if dropped == 1 else 'were'} left out."] if dropped else []
    notes.append("The actions are the report's own top actions, copied as written." if acts else "The report lists no actions.")
    how = howtext.record_how("Shortened the report using only its checked facts" + (" (the AI chose the wording; code checked every figure against the fact it cites)" if source == "ai" else " (the report's own sentences, shortened by code)"),
                             ["the report's headline facts", "the report's insights", "the report's actions"], ids, notes)
    lead = f"Summary of {scope_label}." if lines else f"There is nothing to summarise in {scope_label}."
    chips = next_chips(result, "summary", "", prev_chip_ids(wd))
    ans, chips = _base("summary", lead, "Summary from the report's checked facts", how, chips, used, lines=[ln["text"] for ln in lines], actions=[a["text"] for a in acts], fact_ids=ids,
                       view_offer={"name": "short", "label": "Use as my short version"}, source_of_words=source)
    return ans, chips, used, fail


def save_view(wd, turn_id: str) -> dict | None:
    t = store.get_turn(wd, turn_id)
    a = (t or {}).get("answer") or {}
    if a.get("kind") != "summary" or not a.get("lines"):
        return None
    v = {"name": "short", "title": "Short version", "lines": a["lines"], "actions": a.get("actions") or [], "fact_ids": a.get("fact_ids") or [], "from_turn": turn_id, "created": time.time()}
    store.set_view(wd, "short", v)
    return v


# ------------------------------------------------------------------ what matters most to me
def _hits(terms: set[str], text: str) -> int:
    ws = {sm._stem(w) for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}
    return sum(1 for t in terms if any(sm._same(t, w) for w in ws))


def expand_terms(terms: list[str], ctx) -> set[str]:
    base = {sm._stem(w.lower()) for t in terms for w in re.findall(r"[A-Za-z0-9]+", str(t)) if len(w) > 2 and w.lower() not in records._STOP}
    extra = set()
    for c in ctx.cols.values() if ctx is not None else []:
        cs = sm.tokens(c.display) | sm.tokens(c.name)
        if cs and any(sm._same(b, x) for b in base for x in cs):
            extra |= cs
    return base | extra


def rank_for(result: dict, terms: set[str]) -> list[dict]:
    """The report's insights and actions that mention the priority, best match first (a match in the headline counts double). Existing order breaks ties, so the result is repeatable."""
    pool = []
    for i, e in enumerate(records.insight_items(result)):
        pool.append({"kind": "insight", "id": e["id"], "text": e["text"], "score": 2 * _hits(terms, e["text"]) + _hits(terms, e["title"] + " " + e["detail"] + " " + e["why"]), "order": i, "ids": [e["id"]]})
    for i, h in enumerate(records.headline_items(result)):
        pool.append({"kind": "insight", "id": h["id"], "text": records._cut(_first_sentence(h["text"], 260), 260), "score": 2 * _hits(terms, h["text"]), "order": 100 + i, "ids": [h["id"]]})
    for i, a in enumerate(records.action_items(result)):
        pool.append({"kind": "action", "id": "", "text": a["text"] + (f" ({a['figure']})" if a.get("figure") else ""), "score": 2 * _hits(terms, a["text"] + " " + a.get("figure", "")), "order": 200 + i, "ids": a["evidence"]})
    pool = [p for p in pool if p["score"] > 0]
    pool.sort(key=lambda p: (-p["score"], p["order"]))
    seen, out = set(), []
    for p in pool:
        key = re.sub(r"\W+", " ", p["text"].lower())[:80]
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def answer_priority(result: dict, ctx, terms: list[str], phrase: str, wd, ai_calls: int = 0) -> tuple[dict, list[dict]]:
    chips = next_chips(result, "priority", "", prev_chip_ids(wd))
    cleaned = [str(t) for t in terms if str(t).strip()][:6]
    if not cleaned:
        base = [{"text": e["text"], "ids": [e["id"]]} for e in sorted(records.insight_items(result), key=lambda e: (e["tier"] or 9))[:3]]
        kp = [str(k) for k in (result.get("context") or {}).get("kpis") or [] if str(k).strip()][:3]
        pri = [ichip(f"I care about {k}", {"kind": "priority", "terms": [k]}) for k in kp]
        chips = (pri + chips)[:3]
        how = howtext.record_how("No priority was named, so the report's own top findings are shown", ["the report's insights"], [i for b in base for i in b["ids"]], ["Tell me what you care about, for example “I care about returns”, and I will rank the findings for it."])
        return _base("priority", "Tell me what matters to you, for example “I care about returns”, and I will rank the report's findings for it. Until then, these are the report's own top findings." if base else
                     "Tell me what matters to you, for example “I care about returns”.", "Ranked by the report", how, chips, ai_calls, lines=[f"{b['text']} [{b['ids'][0]}]" for b in base], fact_ids=[i for b in base for i in b["ids"]])
    stems = expand_terms(cleaned, ctx)
    ranked = rank_for(result, stems)[:3]
    label = phrase.strip()[:60] or ", ".join(cleaned)
    if not ranked:
        how = howtext.record_how(f"Looked through the report's insights and actions for “{label}”", ["the report's insights", "the report's actions"], [], ["Matched on the words: " + ", ".join(sorted(stems)) + "."])
        return _base("priority", f"Nothing in this report is about “{label}”. It may not be in the data, or no finding on it was strong enough to include.", f"Ranked for: {label}", how, chips, ai_calls, lines=[])
    ids = list(dict.fromkeys(i for r in ranked for i in r["ids"]))
    how = howtext.record_how(f"Ranked the report's checked insights and actions by how closely they match “{label}” (a match in the headline counts double; ties keep the report's order)", ["the report's insights", "the report's actions"], ids,
                             ["Matched on the words: " + ", ".join(sorted(stems)) + ".", "The sentences and figures are copied from the report; none was recalculated."])
    lines = [r["text"].rstrip(".") + "." + (f" [{r['id']}]" if r["id"] else (f" [{r['ids'][0]}]" if r["ids"] else "")) for r in ranked]
    return _base("priority", f"For “{label}”, these are the report's top {len(ranked)} findings and actions:", f"Ranked for: {label}", how, chips, ai_calls, lines=lines, fact_ids=ids)


# ------------------------------------------------------------------ strategy: the report's own strategies, same checks
def _pick_strategies(result: dict, ctx, focus: str) -> dict:
    """The report's data-backed strategies that fit the focus, best first (the report's own order), and what was left out and why."""
    state = {"findings": result.get("findings") or [], "summary_facts": result.get("summary_facts") or {}}
    data = [s for s in records.strategy_items(result) if s.get("kind", "data") == "data"]
    terms = expand_terms(re.findall(r"[A-Za-z0-9]+", focus or ""), ctx) if (focus or "").strip() else set()
    pool = [s for s in data if not strat.monitor_only(s)] or data
    kept, blocked = [], []
    for i, s in enumerate(pool):
        text = " ".join([s.get("title", ""), s.get("objective", ""), *s.get("actions", [])])
        why = strat.blocked(text, list(s.get("evidence") or []), state)
        if why:
            blocked.append(why)
            continue
        score = _hits(terms, text + " " + str(s.get("rationale", "")) + " " + " ".join(s.get("kpis") or [])) if terms else 1
        if score > 0:
            kept.append((-score, i, s))
    kept.sort(key=lambda t: (t[0], t[1]))
    return {"data": data, "terms": terms, "kept": [s for _, _, s in kept], "blocked": blocked}


def _fact_text(result: dict, fid: str) -> str:
    for f in result.get("findings") or []:
        if f.get("id") == fid and f.get("text"):
            return records._cut(f["text"], 200)
    for h in records.headline_items(result) + records.insight_items(result):
        if h["id"] == fid:
            return records._cut(h["text"], 200)
    return ""


def _worse(first: dict, other: dict) -> list[str]:
    """How `other` is worse than `first` on the three things the report ranks ideas by."""
    out = []
    if strat.IMPACT_PTS.get(other.get("impact"), 0) < strat.IMPACT_PTS.get(first.get("impact"), 0):
        out.append(f"its impact is {other.get('impact')} against {first.get('impact')}")
    if strat.CONF_PTS.get(other.get("confidence"), 0) < strat.CONF_PTS.get(first.get("confidence"), 0):
        out.append(f"we are less sure of it (confidence {other.get('confidence')} against {first.get('confidence')})")
    if strat.EFFORT_PTS.get(other.get("effort"), 0) > strat.EFFORT_PTS.get(first.get("effort"), 0):
        out.append(f"it takes more effort ({other.get('effort')} against {first.get('effort')})")
    return out


def explain_lines(result: dict, picked: list[dict], rest: list[dict]) -> tuple[list[str], list[str]]:
    """(plain lines, fact ids): why the first strategy is first, and why the next one ranked lower. Everything is quoted from the report's own records."""
    s = picked[0]
    ev = [e for e in s.get("evidence") or []]
    lines = [f"“{s['title'].rstrip('.')}” comes first because the report ranks ideas by how much they could help (counted double), then how sure we are, then how little effort they take. "
             f"For this one the impact is {s.get('impact')}, the confidence {s.get('confidence')} and the effort {s.get('effort')}."]
    fact = next((t for t in (_fact_text(result, i) for i in ev) if t), "") or records._plain(s.get("rationale"))
    if fact:
        lines.append(f"It rests on this fact from the report: {fact}" + (f" ({', '.join(ev)})." if ev else "."))
    nxt = next((r for r in rest if r is not s), None)
    if nxt is None:
        lines.append("No other idea in the report fits, so there is no runner-up.")
    else:
        why = _worse(s, nxt)
        reason = "; ".join(why) if why else ("it scored the same on impact, confidence and effort, and the data it rests on shows a smaller checked effect" if float(s.get("effect_size") or 0) > float(nxt.get("effect_size") or 0)
                                               else "it scored the same on impact, confidence and effort, so the report's own order put it second")
        lines.append(f"The runner-up is “{nxt['title'].rstrip('.')}” (impact {nxt.get('impact')}, confidence {nxt.get('confidence')}, effort {nxt.get('effort')}). It ranked lower because {reason}.")
        ev += list(nxt.get("evidence") or [])
    return lines, list(dict.fromkeys(ev))


def answer_strategy(result: dict, ctx, focus: str, wd, ai_calls: int = 0, explain: bool = False) -> tuple[dict, list[dict]]:
    chips = next_chips(result, "strategy", "", prev_chip_ids(wd))
    sel = _pick_strategies(result, ctx, focus)
    data, terms, blocked = sel["data"], sel["terms"], sel["blocked"]
    src = ["the report's strategies"]
    picked = sel["kept"][:2]
    if not picked:
        reason = (f"no idea in this report relates to “{focus.strip()[:60]}”" if terms and data else
                  "the data does not show a lever strong and reliable enough to act on" if not data else
                  blocked[0] if blocked else "nothing in the report supports one")
        if not data and (result.get("strategy_summary") or ""):
            reason = records._cut(_first_sentence(result["strategy_summary"], 220).rstrip("."), 220)
        how = howtext.record_how("Looked through the report's strategies for something to suggest", src, [], ["Each strategy in the report was already checked when the report was made: its figures must match the facts it cites."])
        return _base("strategy", f"No suggestion: {reason}.", "No suggestion", how, chips, ai_calls, lines=[])
    lines, ids = [], []
    for s in picked:
        ev = list(s.get("evidence") or [])
        ids += ev
        lines.append(f"{s['title'].rstrip('.')}. {records._plain(s.get('objective'))}")
        if s.get("actions"):
            lines.append("First steps: " + "; ".join(records._plain(a).rstrip(".") for a in s["actions"][:3]) + ".")
        lines.append(f"Impact {s.get('impact')}, confidence {s.get('confidence')}, effort {s.get('effort')}." + (f" Based on {', '.join(ev)}." if ev else ""))
    if explain:
        more, more_ids = explain_lines(result, picked, sel["kept"])
        lines += ["Why this one:"] + more
        ids += more_ids
    how = howtext.record_how("Picked the best-matching strategies from the report and quoted them", src, ids,
                             ["Each strategy was checked when the report was made: its figures must match the facts it cites, and it may not recommend investing in a group only because it is the largest.",
                              "A suggestion is something to test with a small pilot: the analysis shows association, not cause."])
    return _base("strategy", "Suggestion, based on the report's evidence. It is something to test with a small pilot, not a proven cause:", "Suggestion", how, chips, ai_calls, lines=lines,
                 fact_ids=list(dict.fromkeys(ids)), focus=focus, picked=[s.get("id") for s in picked])


def answer_strategy_why(result: dict, ctx, focus: str, wd, ai_calls: int = 0) -> tuple[dict, list[dict]]:
    """'Why did you choose it?': the ranking in plain words, the fact it rests on, and the runner-up with why it ranked lower. Quotes the report's records only."""
    chips = next_chips(result, "strategy_why", "", prev_chip_ids(wd))
    sel = _pick_strategies(result, ctx, focus)
    if not sel["kept"]:
        return _base("strategy", "I haven't suggested an idea from this report yet, so there is nothing to explain. Ask “What should we do next?” first.", "From your report",
                     howtext.record_how("Looked for a suggested idea to explain and found none", ["the report's strategies"], [], []), chips, ai_calls, lines=[])
    lines, ids = explain_lines(result, sel["kept"][:2], sel["kept"])
    how = howtext.record_how("Explained the ranking of the report's own strategies, quoting its impact, confidence and effort ratings and the fact each rests on", ["the report's strategies", "the report's findings"], ids,
                             ["The report ranks ideas by a score: impact counts double, confidence adds, effort subtracts. Nothing was recalculated; the ratings are the report's own."])
    return _base("strategy", "Here is why, from the report's own ratings:", "From your report", how, chips, ai_calls, lines=lines, fact_ids=ids, focus=focus)


# ------------------------------------------------------------------ not about the data
def answer_general(text, result: dict, wd, ai_calls: int = 0) -> tuple[dict, list[dict]]:
    """A short reply from the AI's own knowledge, labelled as such. It may not carry a figure (a figure must come from the data or the report's checked facts)."""
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(str(text or "").split()))
    keep = [s for s in sentences if s and not re.search(r"\d", s)][:3]
    body = " ".join(keep).strip()
    if not body:
        body = "That is a general question, not one about your data, and I can't give figures for it here."
    how = howtext.record_how("Answered from the AI's general knowledge, not from your data", ["the AI's general knowledge (not your data, not the report)"], [],
                             ["Sentences with a figure were left out, because a figure must come from your data or the report's checked facts."])
    chips = next_chips(result, "general", "", prev_chip_ids(wd))
    return _base("general", body, GENERAL_LABEL, how, chips, ai_calls)


def answer_focus(result: dict, item_id: str, wd, ai_calls: int = 0) -> tuple[dict, list[dict]]:
    """No AI: a question asked about one item (Ask Lens about this) is answered by quoting the item's own saved words."""
    from .. import viewedit
    it = viewedit.item_context(result, item_id) or {"title": "this item", "text": "", "fact_ids": []}
    body = it["text"] or "The report has no more to say about this item."
    how = howtext.record_how(f"Quoted what the report says about “{it['title']}”", ["the report's own text for this item"], it.get("fact_ids") or [], ["Nothing was recalculated; the wording is copied from the saved report. With an AI model, typed questions about this item are answered using it."])
    chips = next_chips(result, "app", "tab", prev_chip_ids(wd))
    return _base("app", f"About “{it['title']}”: {_first_sentence(body, 600)}", "From your report", how, chips, ai_calls, lines=[x for x in re.split(r"(?<=[.!?])\s+", body)[:6] if x], fact_ids=it.get("fact_ids") or [])


def answer_view(result: dict, wd) -> tuple[dict, list[dict]]:
    how = howtext.record_how("Recognised a request to change the view, which is not available yet", ["what Lens can do today"], [], ["Nothing in your data or report was changed."])
    return _base("view", VIEW_TEXT, "Coming soon", how, next_chips(result, "view", "", prev_chip_ids(wd)), 0)
