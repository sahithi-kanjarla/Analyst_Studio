"""The main view of the report, one screen: an answer card per goal question, what to do, key numbers. Presentation only: every figure is a computed fact or a
fact-id-checked AI sentence; nothing is analysed here. Everything else goes into collapsed sections (see reporter).

  A  title, mode, at most WARNINGS_MAX warning lines (built in reporter)
  B  Answers: one card per goal question: the answer in at most ANSWER_LINES lines, a trust label, at most one "so what" sentence, a link to the details
  C  What to do: at most ACTIONS_MAX actions, each for a goal question; a question without one says "No action suggested: <reason>"
  D  Key numbers: TILES_MIN to TILES_MAX tiles"""
from __future__ import annotations

import re

from . import summary, wording
from . import understanding as und
from .insights import DATE_TAG, effect_label
from . import tiering
from .tiering import goal_named, merge_strategies, reliable

ACTIONS_MAX, ANSWER_LINES, TILES_MIN, TILES_MAX, WARNINGS_MAX = 4, 2, 4, 6, 3
PART_LINES = 3          # a question with several parts shows the best fact for each, at most this many lines
RANK_WORDS = re.compile(r"\b(?:which|who|whose|most|least|top|bottom|best|worst|highest|lowest|biggest|largest|smallest|rank\w*)\b", re.I)
CLAUSES = re.compile(r",|;|\band\b|\bor\b|\bbut\b", re.I)
LINE_CHARS = 170
TRUST = {"high": "High", "medium": "Medium", "low": "Low", "exploratory": "Low", "descriptive": "Medium"}      # a descriptive figure makes no statistical claim, so it cannot lift a card to High
NOT_RELIABLE = "not statistically reliable"
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


def part_kinds(q: str) -> list[str]:
    """The kinds of fact a question asks for, one per clause, in order: 'ranking' (which, most...), 'growth' (a change over time), 'ratio' (any other clause: how well the group
    does against a base). Fewer than two kinds means the question has one part."""
    kinds: list[str] = []
    for clause in CLAUSES.split(q.rstrip("? ")):
        if len(re.findall(r"[A-Za-z]+", clause)) < 2:
            continue
        kind = "growth" if wording.TREND_Q.search(clause) else "ranking" if RANK_WORDS.search(clause) else "ratio"
        if kind not in kinds:
            kinds.append(kind)
    return kinds if len(kinds) >= 2 else []


def part_facts(kinds: list[str], ids: list[str], by_id: dict, r_by_id: dict, main: str) -> list[str]:
    """The best fact for each part, picked by the kind of the fact (never by its words): the ranking of a total, the growth table (else the overall trend) of the same measure, the
    ratio ranking of the same group. Ids, in the order of the parts; a part with no fact has none."""
    rankings = list(r_by_id.values())
    plain_r = [r for r in rankings if not r.get("kind") and r.get("focus", True)]
    anchor = next((r_by_id[i] for i in ids if i in r_by_id and not r_by_id[i].get("kind") and r_by_id[i]["basis"] == "total"), None) \
        or next((r_by_id[i] for i in ids if i in r_by_id and not r_by_id[i].get("kind")), None)
    group = anchor["group"] if anchor else None
    measure = anchor["measure"] if anchor and anchor["basis"] == "total" else main

    def first(pool: list[dict], *tests) -> str | None:
        for test in tests:
            for r in pool:
                if test(r):
                    return r["id"]
        return None
    out: list[str] = []
    for kind in kinds:
        if kind == "ranking":
            got = first([r_by_id[i] for i in ids if i in r_by_id] + plain_r, lambda r: r.get("kind") is None and r["basis"] == "total" and (group is None or r["group"] == group) and r["measure"] == measure,
                        lambda r: not r.get("kind") and r["basis"] == "total" and (group is None or r["group"] == group))
        elif kind == "growth":
            growth = [r for r in rankings if r.get("kind") == "growth" and r.get("focus", True)]
            got = first([r_by_id[i] for i in ids if i in r_by_id and r_by_id[i].get("kind") == "growth"] + growth,
                        lambda r: r["group"] == group and r["measure"] == measure, lambda r: r["group"] == group, lambda r: group is None and r["measure"] == measure)
            if not got:      # no growth table: the overall trend of the measure
                got = next((f["id"] for f in by_id.values() if f["tool"] == "trend" and (f.get("params") or {}).get("target") == measure), None)
        else:
            ratio = [r for r in rankings if not r.get("kind") and r["basis"] == "ratio" and r.get("focus", True)]
            got = first([r_by_id[i] for i in ids if i in r_by_id and r_by_id[i]["basis"] == "ratio" and not r_by_id[i].get("kind")] + ratio, lambda r: group is None or r["group"] == group)
        if got and got not in out:
            out.append(got)
    return out


# ------------------------------------------------------------------ what a fact id stands for
def lookup(state: dict, view: dict) -> tuple[dict, dict, dict]:
    strat = {s["id"]: s for s in merge_strategies(state)["strategies"]}
    return {f["id"]: f for f in state.get("findings") or []}, {r["id"]: r for r in view.get("rankings") or []}, strat


def trust_of(ids: list[str], by_id: dict, r_by_id: dict) -> str:
    levels = [TRUST.get(((by_id[i].get("insight") or {}).get("confidence") or {}).get("level", "descriptive"), "Low") for i in ids if i in by_id]
    if levels:
        trust = min(levels, key=["Low", "Medium", "High"].index)
    else:
        trust = "High" if any(i in r_by_id for i in ids) else "Medium"
    if trust == "High" and any(NOT_RELIABLE in str(r_by_id[i].get("short") or "").lower() for i in ids if i in r_by_id):
        trust = "Medium"      # the card is only as trustworthy as its weakest figure
    return trust


def cap_by_text(trust: str, lines: list[str]) -> str:
    """A card that shows a figure marked 'not statistically reliable' cannot say High."""
    return "Medium" if trust == "High" and any(NOT_RELIABLE in t.lower() for t in lines) else trust


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


# ------------------------------------------------------------------ labels, units and concrete figures
GROWTH_SHORT = re.compile(r"^Biggest change in .+?, (?P<a>\S+) vs (?P<b>\S+): (?P<what>.+?) \([^()]*\), (?P<rel>not statistically reliable|statistically reliable)$")
FREQ_WORD = {"D": "day", "W": "week", "MS": "month"}
GROWTH_CLAIM = re.compile(r"\b(?:high|strong|rapid|fast|significant)[- ]growth\b|\bgrowing (?:fast|strongly|rapidly)\b|\bfastest[- ]growing\b", re.I)
SAYS_UNIT = r"(?![\d.,]*\d|%|\s?[%×]|\s?[xKMB]\b|\s?(?:rows|times|days|months|weeks|years|groups|customers|orders)\b)"
BARE_AFTER = re.compile(r"\b(?P<lead>averages?|mean|median|stands at|is|at|was|to)\s+(?P<num>-?\d[\d,]*(?:\.\d+)?)" + SAYS_UNIT)
PERCENT_WORDS = {"pct", "percent", "percentage", "rate", "share", "ratio"}


def growth_label(r: dict) -> str | None:
    """The change between the first and last full period, said in plain words with its own reliability: '2023 vs 2024: group g3 +41% (not statistically reliable)'."""
    m = GROWTH_SHORT.match(str(r.get("short") or ""))
    return f"{m['a']} vs {m['b']}: {m['what']} ({m['rel']})" if m else None


def trend_label(f: dict) -> str | None:
    """The fitted trend over the whole period, in plain words with its own reliability: 'monthly trend over 24 months: rising (statistically reliable)'."""
    st = f.get("stats") or {}
    unit = FREQ_WORD.get(st.get("freq"))
    if not unit or st.get("periods") is None or st.get("slope") is None:
        return None
    rel = "statistically reliable" if f.get("significant") else NOT_RELIABLE
    return f"{unit}ly trend over {st['periods']} {unit}s: {'rising' if st['slope'] > 0 else 'falling'} ({rel})"


def growth_reliable(i: str, by_id: dict, r_by_id: dict) -> bool | None:
    """Is the growth figure behind this fact id statistically reliable? None when the fact is not a growth figure."""
    if i in r_by_id and r_by_id[i].get("kind") == "growth":
        m = GROWTH_SHORT.match(str(r_by_id[i].get("short") or ""))
        return bool(m) and m["rel"] == "statistically reliable"
    if i in by_id and by_id[i].get("tool") == "trend":
        return bool(by_id[i].get("significant"))
    return None


def measure_of(i: str, by_id: dict, r_by_id: dict) -> str:
    if i in r_by_id:
        return str(r_by_id[i].get("measure") or "")
    return str(((by_id.get(i) or {}).get("params") or {}).get("target") or "")


def unit_of(measure: str, state: dict) -> tuple[str, str]:
    """(prefix, suffix) the fact's measure is written with: a currency symbol for money, % for a rate or share, else nothing."""
    cur = summary._cur(measure, state)
    if cur:
        return cur, ""
    words = set(re.findall(r"[a-z]+", re.sub(r"([a-z])([A-Z])", r" ", measure).lower()))
    spec = summary._ratios(state).get(measure)
    if words & PERCENT_WORDS or (spec is not None and (state.get("profile") or {}).get("rows") and "%" in str(spec.get("meaning", ""))):
        return "", "%"
    return "", ""


def with_unit(text: str, measure: str, state: dict) -> str:
    """A bare figure the line gives for the measure ('averages 15.1') gets the measure's unit ('averages 15.1%'). Figures that already carry one are left alone."""
    pre, suf = unit_of(measure, state)
    if not (pre or suf) or not measure:
        return text
    return BARE_AFTER.sub(lambda m: f"{m['lead']} {pre}{m['num']}{suf}", text)


def names_measure(text: str, measures) -> bool:
    flat = lambda s: re.sub(r"[\s_\-]+", " ", str(s)).strip().lower()
    return any(flat(m) and flat(m) in flat(text) for m in measures)


def concrete(text: str, measures) -> bool:
    """An action is concrete when it has a figure and names the measure that figure is about."""
    return bool(re.search(r"\d", plain(text))) and names_measure(text, measures)


def fact_line(i: str, by_id: dict, r_by_id: dict, state: dict) -> str:
    """The computed fact in its own wording and figures, with the measure's unit."""
    return with_unit(_line(i, by_id, r_by_id), measure_of(i, by_id, r_by_id), state)


def growth_safe(text: str, ids: list[str], by_id: dict, r_by_id: dict) -> str:
    """An action that calls growth high or strong says which measure supports it. If that figure is not statistically reliable, the claim is dropped and the figure is quoted instead."""
    if not GROWTH_CLAIM.search(text):
        return text
    shown = []
    for i in ids:
        ok = growth_reliable(i, by_id, r_by_id)
        if ok is None:
            continue
        shown.append((ok, growth_label(r_by_id[i]) if i in r_by_id else trend_label(by_id[i])))
    shown = [(ok, lab) for ok, lab in shown if lab]
    if not shown:
        return GROWTH_CLAIM.sub("growth", text).rstrip(". ") + f" (growth: {NOT_RELIABLE}, no figure supports a strong claim)."
    out = text if any(ok for ok, _ in shown) else GROWTH_CLAIM.sub("growth", text)
    return out.rstrip(". ") + " (based on " + "; ".join(lab for _, lab in shown) + ")."


def label_growth_measures(lines: list[str], origin: dict, by_id: dict, r_by_id: dict) -> list[str]:
    """When a card shows a growth table figure (first vs last period) and a fitted trend (whole period), each is labelled in plain words, with its own reliability."""
    kinds = {origin.get(t) for t in lines}
    has_growth = any(i in r_by_id and r_by_id[i].get("kind") == "growth" for i in kinds)
    has_trend = any(i in by_id and by_id[i].get("tool") == "trend" for i in kinds)
    if not (has_growth and has_trend):
        return lines
    out = []
    for t in lines:
        i = origin.get(t)
        lab = growth_label(r_by_id[i]) if i in r_by_id and r_by_id[i].get("kind") == "growth" else trend_label(by_id[i]) if i in by_id and by_id[i].get("tool") == "trend" else None
        out.append(lab or t)
    return out


def readable(state: dict, text: str) -> str:
    return und.readable(text, state)


def _line(i: str, by_id: dict, r_by_id: dict) -> str:
    if i in r_by_id:
        r = r_by_id[i]
        return clip(r.get("short") or r.get("lead") or r["text"])
    f = by_id[i]
    return clip((f.get("insight") or {}).get("headline") or f.get("title", ""))


# ------------------------------------------------------------------ B. answer cards
def cards(state: dict, view: dict) -> list[dict]:
    """One card per goal question: {n, num, question, text, trust, ids, so_what, action_question}. `n` is the question's place in the goal; `num` is the number shown (None for an action question:
    'what should we do?' is answered by the 'What to do' section only, it gets no answer card). A fact is written once: a later card that rests only on facts an earlier card used points back to it."""
    by_id, r_by_id, strat = lookup(state, view)
    so_what = state.get("so_what") or {}
    growth = [r for r in r_by_id.values() if r.get("kind") == "growth" and r.get("focus")]
    main = summary.main_measure(state, list(r_by_id.values()))
    out, used, first_card, shown = [], set(), {}, 0
    for n, a in enumerate(view.get("answers") or [], 1):
        origin: dict[str, str] = {}      # shown line -> the fact id it came from

        def ln(i: str) -> str:
            origin[text := fact_line(i, by_id, r_by_id, state)] = i
            return text

        limit = ANSWER_LINES
        ids = [i for i in a["ids"] if i in by_id or i in r_by_id]
        action = is_action_question(a["question"])
        shown += not action
        card = {"n": n, "num": None if action else shown, "question": a["question"], "ids": a["ids"], "gcols": sorted(group_cols(ids, by_id, r_by_id)), "trust": trust_of(ids, by_id, r_by_id),
                "so_what": readable(state, so_what.get(a["question"], "")), "action_question": action, "text": "", "date_tag": DATE_TAG in a["answer"]}
        parts = part_facts(part_kinds(a["question"]), ids, by_id, r_by_id, main) if ids and not action else []
        parts = [i for i in parts if i not in used] if len(parts) > 1 else []
        fresh = [i for i in ids if i not in used]
        sids = [i for i in a["ids"] if i in strat]
        lines: list[str] = []
        if parts:        # a question with several parts: the best fact for each part, picked by the kind of the fact
            lines = [ln(i) for i in parts]
            card["ids"] = a["ids"] + [i for i in parts if i not in a["ids"]]
            card["trust"] = trust_of(ids + parts, by_id, r_by_id)
            ids = ids + [i for i in parts if i not in ids]
            limit = PART_LINES
        elif not ids and sids and card["action_question"]:
            lines = ["Answered under 'What to do' below."]
            card["trust"] = next((str(strat[i]["confidence"]).capitalize() for i in sids if strat[i].get("confidence")), "Medium")
        elif not ids:
            lines = [clip(a["answer"]) if a["answer"].startswith("Not answered") else "Not answered: no analysis in this run answers it."]
            card["trust"] = ""
        elif not fresh and any(i in first_card for i in ids):
            earlier = next(first_card[i] for i in ids if i in first_card)
            lines = [f"Same figures as question {earlier}."]
        else:
            fresh = fresh or ids
            lines = [ln(i) for i in fresh if i not in strat]
            if not any(r_by_id.get(i, {}).get("kind") == "growth" for i in ids):          # the biggest change of the measure this card is about: one line, from the growth table in the details
                base = next((r_by_id[i] for i in fresh if i in r_by_id and r_by_id[i].get("kind") not in ("growth", "outside_rate")), None)
                g = next((r for r in growth if base and r["group"] == base["group"] and r["measure"] == base["measure"] and r["id"] not in used), None) or \
                    next((r for r in growth if base and r["measure"] == base["measure"] and r["id"] not in used), None) if base else None
                if g and g["measure"] == main:
                    lines.append(ln(g["id"]))
                    used.add(g["id"])
        for i in ids:
            used.add(i)
            if card["num"]:
                first_card.setdefault(i, card["num"])
        lines = label_growth_measures(lines[:limit], origin, by_id, r_by_id)
        card["text"] = [readable(state, t) for t in lines]
        card["trust"] = cap_by_text(card["trust"], card["text"])
        out.append(card)
    return out


def card_md(c: dict) -> list[str]:
    tag = f" [{DATE_TAG}]" if c["date_tag"] and c["text"] and not c["text"][0].startswith("Not answered") else ""
    L = [f"**{c['num']}. {plain(c['question'])}**", ""] + [f"{t}{tag if k == len(c['text']) - 1 else ''}  " for k, t in enumerate(c["text"])]
    foot = []
    if c["trust"]:
        foot.append(f"Trust: **{c['trust']}**")
    if c["so_what"]:
        foot.append(f"So what: {c['so_what']}")
    foot.append(f"[details](#answer-q{c['num']})")
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
    actions = [{"n": n, "text": readable(state, growth_safe(plain(f"{s['title']}: {s['objective']}"), s["evidence"], by_id, r_by_id)), "ai": False, "evidence": s["evidence"]} for n, s in chosen]
    for c in cs:        # a verified AI proposal for a question with an answer but no action
        if len(actions) < ACTIONS_MAX and c["question"] in ai and not any(a["n"] == c["n"] for a in actions):
            cited_ids = (state.get("ai_action_ids") or {}).get(c["question"], [])
            actions.append({"n": c["n"], "text": readable(state, growth_safe(plain(ai[c["question"]]), cited_ids, by_id, r_by_id)), "ai": True, "evidence": cited_ids})
    actions.sort(key=lambda a: a["n"])
    covered = {a["n"] for a in actions}
    none, open_ = {}, []
    for c in cs:
        if c["n"] in covered:
            continue
        ids = [i for i in c["ids"] if i in by_id or i in r_by_id]
        if c["question"] in (state.get("action_none") or {}):
            none[c["n"]] = state["action_none"][c["question"]]
        elif not ids:
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
    num = {c["n"]: c["num"] for c in cs}
    L = [f"{i}. {a['text']}" + (f" *(question {num[a['n']]})*" if num.get(a["n"]) else "") for i, a in enumerate(plan["actions"], 1)]      # an action question has no number: its actions carry no tag
    L += [f"- No action suggested for question {num[n]}: {reason}" for n, reason in sorted(plan["none"].items()) if num.get(n)]
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
    out = [(readable(state, m["label"]), m["value"]) for m in kpis[:TILES_MAX - 2]]
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
