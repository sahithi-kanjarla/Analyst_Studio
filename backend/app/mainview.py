"""The main view of the report, one screen: an answer card per goal question, what to do, key numbers. Presentation only: every figure is a computed fact or a
fact-id-checked AI sentence; nothing is analysed here. Everything else goes into collapsed sections (see reporter).

  A  title, mode, at most WARNINGS_MAX warning lines (built in reporter)
  B  Answers: one card per goal question: the answer in at most ANSWER_LINES lines, a trust label, at most one "so what" sentence, a link to the details
  C  What to do: at most ACTIONS_MAX actions, each for a goal question; a question without one says "No action suggested: <reason>"
  D  Key numbers: TILES_MIN to TILES_MAX tiles"""
from __future__ import annotations

import re

from . import summary
from .insights import DATE_TAG, effect_label
from . import tiering
from .tiering import goal_named, merge_strategies, reliable

ACTIONS_MAX, ANSWER_LINES, TILES_MIN, TILES_MAX, WARNINGS_MAX = 4, 2, 4, 6, 3
LINE_CHARS = 170
TRUST = {"high": "High", "medium": "Medium", "low": "Low", "exploratory": "Low", "descriptive": "High"}
STRONG = ("medium", "large")
TAG = re.compile(r"\s*[\[(]\s*[rfs]\d+(?:\s*[,;]\s*[rfs]\d+)*\s*[\])]")
STAT = re.compile(r"\s*\((?:[^()]*\b(?:p\s*[=<]|eta|η|Cram|Kruskal|Welch|ANOVA|chi-?square|rho|effect size)[^()]*)\)|,\s*p\s*[=<]\s*[\d.e-]+", re.I)


def plain(text: str) -> str:
    """Text for the main view: no fact-id tags, no p-values, effect sizes or test names (those are in the details)."""
    return re.sub(r"\s{2,}", " ", STAT.sub("", TAG.sub("", str(text)))).strip()


def clip(text: str, n: int = LINE_CHARS) -> str:
    text = plain(text).replace("**", "")
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0].rstrip(",;: ") + "…"


def is_action_question(q: str) -> bool:
    return bool(summary.ACTION_Q.search(q))


# ------------------------------------------------------------------ what a fact id stands for
def lookup(state: dict, view: dict) -> tuple[dict, dict, dict]:
    strat = {s["id"]: s for s in merge_strategies(state)["strategies"]}
    return {f["id"]: f for f in state.get("findings") or []}, {r["id"]: r for r in view.get("rankings") or []}, strat


def trust_of(ids: list[str], by_id: dict, r_by_id: dict) -> str:
    levels = [TRUST.get(((by_id[i].get("insight") or {}).get("confidence") or {}).get("level", "descriptive"), "Low") for i in ids if i in by_id]
    if levels:
        return min(levels, key=["Low", "Medium", "High"].index)
    return "High" if any(i in r_by_id for i in ids) else "Medium"


def group_cols(ids: list[str], by_id: dict, r_by_id: dict) -> set[str]:
    """The group columns the facts split by: what ties a strategy to the question its facts answer."""
    out = {r_by_id[i]["group"] for i in ids if i in r_by_id}
    for i in ids:
        if i in by_id:
            out |= {str(by_id[i]["params"][k]) for k in tiering.GROUP_PARAMS if (by_id[i].get("params") or {}).get(k)}
    return out


def action_basis(i: str, state: dict, by_id: dict, r_by_id: dict) -> bool:
    """A fact an action may rest on: a finding with a medium or larger effect, or a descriptive fact (no effect size) on a group the goal names. Never a small or negligible effect."""
    if i in r_by_id:
        return bool(r_by_id[i].get("focus", True))
    f = by_id.get(i)
    if not f:
        return False
    eff = effect_label(f.get("stats") or {})
    if eff is not None:
        return eff in STRONG and reliable(f)
    return goal_named(f, state.get("goal", "")) and f.get("significant") is not False


def _line(i: str, by_id: dict, r_by_id: dict) -> str:
    if i in r_by_id:
        r = r_by_id[i]
        return clip(r.get("short") or r.get("lead") or r["text"])
    f = by_id[i]
    return clip((f.get("insight") or {}).get("headline") or f.get("title", ""))


# ------------------------------------------------------------------ B. answer cards
def cards(state: dict, view: dict) -> list[dict]:
    """One card per goal question: {n, question, text, trust, ids, so_what, action_question}. A fact is written once: a later card that rests only on facts an earlier card used points back to it."""
    by_id, r_by_id, strat = lookup(state, view)
    so_what = state.get("so_what") or {}
    growth = [r for r in r_by_id.values() if r.get("kind") == "growth" and r.get("focus")]
    main = summary.main_measure(state, list(r_by_id.values()))
    out, used, first_card = [], set(), {}
    for n, a in enumerate(view.get("answers") or [], 1):
        ids = [i for i in a["ids"] if i in by_id or i in r_by_id]
        card = {"n": n, "question": a["question"], "ids": a["ids"], "gcols": sorted(group_cols(ids, by_id, r_by_id)), "trust": trust_of(ids, by_id, r_by_id), "so_what": so_what.get(a["question"], ""), "action_question": is_action_question(a["question"]),
                "text": "", "date_tag": DATE_TAG in a["answer"]}
        fresh = [i for i in ids if i not in used]
        sids = [i for i in a["ids"] if i in strat]
        lines: list[str] = []
        if not ids and sids and card["action_question"]:
            lines = ["Answered under 'What to do' below."]
            card["trust"] = next((str(strat[i]["confidence"]).capitalize() for i in sids if strat[i].get("confidence")), "Medium")
        elif not ids:
            lines = [clip(a["answer"]) if a["answer"].startswith("Not answered") else "Not answered: no analysis in this run answers it."]
            card["trust"] = ""
        elif not fresh:
            earlier = next(first_card[i] for i in ids if i in first_card)
            lines = [f"Same figures as question {earlier}."]
        else:
            lines = [_line(i, by_id, r_by_id) for i in fresh if i not in strat]
            if not any(r_by_id.get(i, {}).get("kind") == "growth" for i in ids):          # the biggest change of the measure this card is about: one line, from the growth table in the details
                base = next((r_by_id[i] for i in fresh if i in r_by_id and r_by_id[i].get("kind") not in ("growth", "outside_rate")), None)
                g = next((r for r in growth if base and r["group"] == base["group"] and r["measure"] == base["measure"] and r["id"] not in used), None) or \
                    next((r for r in growth if base and r["measure"] == base["measure"] and r["id"] not in used), None) if base else None
                if g and g["measure"] == main:
                    lines.append(clip(g.get("short") or g["text"]))
                    used.add(g["id"])
        for i in ids:
            used.add(i)
            first_card.setdefault(i, n)
        card["text"] = lines[:ANSWER_LINES]
        out.append(card)
    return out


def card_md(c: dict) -> list[str]:
    tag = f" [{DATE_TAG}]" if c["date_tag"] and c["text"] and not c["text"][0].startswith("Not answered") else ""
    L = [f"**{c['n']}. {plain(c['question'])}**", ""] + [f"{t}{tag if k == len(c['text']) - 1 else ''}  " for k, t in enumerate(c["text"])]
    foot = []
    if c["trust"]:
        foot.append(f"Trust: **{c['trust']}**")
    if c["so_what"]:
        foot.append(f"So what: {c['so_what']}")
    foot.append(f"[details](#answer-q{c['n']})")
    return L + [" · ".join(foot), ""]


# ------------------------------------------------------------------ C. actions
def _support(s: dict, state: dict, by_id: dict, r_by_id: dict) -> list[str]:
    return [e for e in s.get("evidence") or [] if action_basis(e, state, by_id, r_by_id)]


def _link(s: dict, cs: list[dict], gcols: set[str]) -> list[int]:
    """The goal questions a strategy is for, best first: its evidence is cited by the question's answer, or it splits by a column the answer splits by or the question names.
    An action question ('what should we do?') takes any strategy. A strategy tied to no question is not an action."""
    ev, scored = set(s.get("evidence") or []), []
    for c in cs:
        score = 3 * bool(ev & set(c["ids"])) + 2 * bool(gcols & set(c["gcols"])) + bool(any(summary.names(col, c["question"]) for col in gcols)) or (0.5 if c["action_question"] else 0)
        if score:
            scored.append((score, c["n"]))
    return [n for _, n in sorted(scored, key=lambda t: -t[0])]


def plan_actions(state: dict, view: dict, cs: list[dict]) -> dict:
    """{"actions": [{n, text, ai}], "none": {n: reason}, "open": [n]}: the actions (strategies first), the reason for each question left without one, and the questions with a computed
    answer that an AI proposal could still cover (they have a fact an action may rest on)."""
    by_id, r_by_id, _ = lookup(state, view)
    ai = state.get("ai_actions") or {}
    strategies = merge_strategies(state)["strategies"]
    cands = []
    for s in strategies:
        sup = _support(s, state, by_id, r_by_id)
        if not sup:
            continue
        links = _link(s, cs, group_cols(sup, by_id, {}))
        if links:
            cands.append({"s": s, "links": links})
    chosen: list[tuple[int, dict]] = []
    taken: set[str] = set()
    order = [c["n"] for c in sorted(cs, key=lambda c: c["action_question"])]
    for n in order:        # first pass: the strongest strategy for each question, specific questions first
        pick = next((c for c in cands if n in c["links"] and c["s"]["id"] not in taken), None)
        if pick and len(chosen) < ACTIONS_MAX:
            chosen.append((n, pick["s"]))
            taken.add(pick["s"]["id"])
    for c in cands:        # second pass: the next strongest, up to the cap
        if c["s"]["id"] not in taken and len(chosen) < ACTIONS_MAX:
            chosen.append((c["links"][0], c["s"]))
            taken.add(c["s"]["id"])
    actions = [{"n": n, "text": plain(f"{s['title']}: {s['objective']}"), "ai": False, "evidence": s["evidence"]} for n, s in chosen]
    for c in cs:        # a verified AI proposal for a question with an answer but no action
        if len(actions) < ACTIONS_MAX and c["question"] in ai and not any(a["n"] == c["n"] for a in actions):
            actions.append({"n": c["n"], "text": plain(ai[c["question"]]), "ai": True, "evidence": []})
    actions.sort(key=lambda a: a["n"])
    covered = {a["n"] for a in actions}
    none, open_ = {}, []
    for c in cs:
        if c["n"] in covered:
            continue
        ids = [i for i in c["ids"] if i in by_id or i in r_by_id]
        if not ids:
            none[c["n"]] = "not enough data to answer this question."
        elif any(action_basis(i, state, by_id, r_by_id) for i in ids):
            if len(actions) >= ACTIONS_MAX:
                none[c["n"]] = f"the {ACTIONS_MAX} strongest actions are shown; the full strategy is under 'view more'."
            else:
                open_.append(c["n"])
                none[c["n"]] = "the data supports no specific change for it."
        else:
            none[c["n"]] = "the data shows no medium or larger effect to act on."
    return {"actions": actions, "none": none, "open": open_}


def actions_md(cs: list[dict], plan: dict) -> list[str]:
    q = {c["n"]: c for c in cs}
    L = [f"{i}. {a['text']} *(question {a['n']})*" for i, a in enumerate(plan["actions"], 1)]
    L += [f"- No action suggested for question {n}: {reason}" for n, reason in sorted(plan["none"].items())]
    return L or ["- No action suggested: no goal question has a finding with a medium or larger effect to act on."]


# ------------------------------------------------------------------ D. key numbers
def tiles(state: dict, view: dict) -> list[tuple[str, str]]:
    """4 to 6 (label, value): the goal's main measures first, then the period and the row count."""
    nums = view.get("numbers") or state.get("key_metrics") or []
    goal, target = state.get("goal", ""), (state.get("plan") or {}).get("target") or ""
    period = next((m for m in nums if m["label"].startswith("Period")), None)
    kpis = [m for m in nums if m is not period]

    def rank(m):
        label = m["label"]
        return (0 if target and target in label else 1 if summary.mentions(label.replace("Total ", ""), goal) else 2)
    kpis = sorted(kpis, key=rank)
    out = [(m["label"], m["value"]) for m in kpis[:TILES_MAX - 2]]
    if period:
        out.append((period["label"], period["value"]))
    prof = state.get("profile") or {}
    if prof.get("rows"):
        out.append(("Rows", f"{prof['rows']:,}"))
    if len(out) < TILES_MIN and prof.get("cols"):
        out.append(("Columns", f"{prof['cols']:,}"))
    return out[:TILES_MAX]


def tiles_md(ts: list[tuple[str, str]]) -> list[str]:
    if not ts:
        return []
    cell = lambda s: str(s).replace("|", "/").replace("\n", " ")
    return ["| " + " | ".join(cell(t[0]) for t in ts) + " |", "|" + "---|" * len(ts), "| " + " | ".join(f"**{cell(t[1])}**" for t in ts) + " |"]


# ------------------------------------------------------------------ the dashboard's key findings
def gallery_order(state: dict, top: int = 4) -> dict:
    """{"key": ids, "more": ids}: findings that name the goal's columns lead; a small or negligible effect, or a weak correlation, is never a key finding. Score decides within a group."""
    goal = state.get("goal", "")
    fs = [f for f in state.get("findings") or [] if f.get("insight")]

    def weak(f):
        return effect_label(f.get("stats") or {}) in ("negligible", "small")
    ranked = sorted(fs, key=lambda f: (weak(f), not goal_named(f, goal), -(f["insight"].get("score") or 0)))
    strong = [f["id"] for f in ranked if not weak(f)]
    return {"key": strong[:top], "more": [f["id"] for f in ranked if f["id"] not in strong[:top]]}
