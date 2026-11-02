"""The main view of the report, one screen: an answer card per goal question, what to do, key numbers. Presentation only: every figure is a computed fact or a
fact-id-checked AI sentence; nothing is analysed here. Everything else goes into collapsed sections (see reporter).

  A  title, mode, at most WARNINGS_MAX warning lines (built in reporter)
  B  Answers: one card per goal question: the answer in at most ANSWER_LINES lines (a question naming several columns: one line per column, up to GROUP_LINES), a trust label,
     at most one "so what" sentence, and a Details toggle that opens in place (never a link)
  C  What to do: at most ACTIONS_MAX actions, each for a goal question; a question without one says "No action suggested: <reason>"
  D  Key numbers: TILES_MIN to TILES_MAX tiles"""
from __future__ import annotations

import re

from . import strategy as strat_mod, summary, wording
from . import understanding as und
from .insights import DATE_TAG, effect_label
from . import tiering
from .tiering import goal_named, merge_strategies, reliable

ACTIONS_MAX, ANSWER_LINES, TILES_MIN, TILES_MAX, WARNINGS_MAX = 4, 2, 4, 6, 3
PART_LINES = 3          # a question with several parts shows the best fact for each, at most this many lines
RANK_WORDS = re.compile(r"\b(?:which|who|whose|most|least|top|bottom|best|worst|highest|lowest|biggest|largest|smallest|rank\w*)\b", re.I)
CLAUSES = re.compile(r",|;|\band\b|\bor\b|\bbut\b", re.I)
LINE_CHARS = 170
GROUP_LINES = 4         # a question naming several group columns shows one line for each, at most this many
TREND_LINES = 3         # a trend question: the period totals, what is too short about them, and one more line
MIN_TREND_PERIODS, MIN_DATE_COVERAGE = 6, 0.7          # fewer periods than this, or dates on fewer rows than this, cannot show a trend
LOSS_Q = re.compile(r"\b(?:lose|losing|lost|loss(?:es)?|leak\w*|waste\w*)\b", re.I)
WORST_Q = re.compile(r"\b(?:lose|losing|lost|loss(?:es)?|leak\w*|waste\w*|worst)\b", re.I)          # "where is it worst", "how much do we lose"
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


FIGURE_WORD = {"growth": "growth", "outside_rate": "share"}
LEVEL_WHY = {"low": "the {w} result could be random variation", "exploratory": "the {w} figures are leads that were not statistically tested",
             "descriptive": "the {w} figures describe the data without a statistical test", "medium": "the {w} result is real but only moderately strong"}


def figure_word(i: str, by_id: dict, r_by_id: dict) -> str:
    """What kind of figure a fact is, in a word: growth, share, ranking, or the name of the finding's analysis ('trend')."""
    if i in r_by_id:
        return FIGURE_WORD.get(r_by_id[i].get("kind"), "ranking")
    return str((by_id.get(i) or {}).get("tool") or "").replace("_", " ") or "one"


def trust_reason(trust: str, ids: list[str], by_id: dict, r_by_id: dict, lines: list[str] | None = None, origin: dict | None = None) -> str:
    """One plain line for why the trust label is what it is, from the weakest figure: a figure marked 'not statistically reliable', else the finding with the lowest confidence,
    else (computed figures only) that they are computed straight from the data."""
    if not trust:
        return ""
    for t in lines or []:
        if NOT_RELIABLE in t.lower():
            i = (origin or {}).get(t)
            return f"the {figure_word(i, by_id, r_by_id)} figure is {NOT_RELIABLE}" if i else f"a figure shown here is {NOT_RELIABLE}"
    for i in ids:
        if i in r_by_id and NOT_RELIABLE in str(r_by_id[i].get("short") or "").lower():
            return f"the {figure_word(i, by_id, r_by_id)} figure is {NOT_RELIABLE}"
    rank = ["low", "exploratory", "descriptive", "medium", "high"]
    found = [(rank.index(lv), i) for i in ids if i in by_id and (lv := ((by_id[i].get("insight") or {}).get("confidence") or {}).get("level", "descriptive")) in rank]
    if found:
        level, i = min(found)
        if rank[level] == "high":
            return "every figure passed its statistical checks"
        return LEVEL_WHY[rank[level]].format(w=figure_word(i, by_id, r_by_id))
    return "the figures are computed directly from the data" if any(i in r_by_id for i in ids) else "no analysis in this run backs it directly"


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


# ------------------------------------------------------------------ the "so what" code writes (the AI never picks a name or a share)
def code_so_what(ids: list[str], by_id: dict, r_by_id: dict) -> tuple[str, bool]:
    """(sentence, replaces the AI sentence). A loss-style answer (a measure outside its main value) says its largest part by computed share; an average comparison between groups
    says each group's share of the total when the comparison has it. ('', False) when neither applies."""
    for i in ids:
        r = r_by_id.get(i) or {}
        if r.get("kind") == "outside_rate" and r.get("largest") and r.get("largest_share") is not None:
            return (f"The largest part of the {r['measure']} outside {r['top']} is {wording.entity(r['group'], r['largest'])}, "
                    f"{r['largest_share']:.0%} of the outside total."), True
    for i in ids:
        f = by_id.get(i) or {}
        st, p = f.get("stats") or {}, f.get("params") or {}
        if f.get("tool") == "group_compare" and st.get("kind") != "binary" and not st.get("ratio_of_sums") and len(st.get("shares") or {}) == 2:
            parts = [f"{wording.entity(p['group'], g)} holds {v:.0%}" for g, v in st["shares"].items() if v is not None]
            if len(parts) == 2:
                return f"Share of total {p['target']}: " + " and ".join(parts) + ".", False
    return "", False


def with_shares(ai: str, extra: str) -> str:
    """The AI sentence, then the code's share sentence, unless the AI sentence already carries those percentages."""
    pcts = re.findall(r"\d+%", extra)
    if not extra or (ai and pcts and all(x in ai for x in pcts)):
        return ai
    return f"{ai.rstrip()} {extra}".strip()


# ------------------------------------------------------------------ what a card shows for a question that names several columns, asks about a trend, or asks where it is worst
def named_groups(q: str, ids: list[str], r_by_id: dict, main: str, state: dict) -> list[str]:
    """Ids of the total rankings, one per group column the question names (at most GROUP_LINES), for the measure the question's ranking is about. Fewer than two columns: []."""
    totals = [r for r in r_by_id.values() if r.get("basis") == "total" and not r.get("kind")]
    lead = next((r_by_id[i] for i in ids if i in r_by_id and r_by_id[i] in totals), None)
    measure = lead["measure"] if lead else main
    mapped = set(und.columns_for_text(state.get("understanding"), q))
    scored = [(3 if r["group"] in mapped else summary.names(r["group"], q), r) for r in totals if r["measure"] == measure]
    out = [r["id"] for n, r in sorted(scored, key=lambda t: -t[0]) if n][:GROUP_LINES]
    return out if len(out) >= 2 else []


PERIOD_ADJ = {"D": "Daily", "W": "Weekly", "MS": "Monthly"}
AGG_WORD = {"sum": "totals", "mean": "averages", "ratio of sums": "ratio"}


def trend_lines(f: dict, state: dict) -> tuple[str | None, str | None]:
    """(the period totals in plain words, the plain-words warning that the data is too short to call a trend). Too short: fewer than MIN_TREND_PERIODS periods, or dates on under
    MIN_DATE_COVERAGE of the rows. Either is None when the finding has no such figure."""
    st = f.get("stats") or {}
    unit, periods, cov = FREQ_WORD.get(st.get("freq")), st.get("periods"), f.get("date_coverage")
    if not unit or periods is None:
        return None, None
    target = str((f.get("params") or {}).get("target") or "")
    cur = summary._cur(target, state)
    fmt = (lambda v: f"{v:.3g}") if st.get("agg") == "ratio of sums" else (lambda v: summary.short(v, cur))
    label = lambda d: d[:7] if st.get("freq") == "MS" else d
    vals = [(label(str(d)), v) for d, v in f.get("series") or [] if v is not None]
    body = None
    if vals and len(vals) <= MIN_TREND_PERIODS:
        body = ", ".join(f"{d} {fmt(v)}" for d, v in vals)
    elif vals:
        best, weak = max(vals, key=lambda x: x[1]), min(vals, key=lambda x: x[1])
        body = f"{vals[0][0]} {fmt(vals[0][1])} to {vals[-1][0]} {fmt(vals[-1][1])} ({len(vals)} {unit}s; best {best[0]} {fmt(best[1])}, weakest {weak[0]} {fmt(weak[1])})"
    shown = f"{PERIOD_ADJ[st['freq']]} {AGG_WORD.get(st.get('agg'), 'totals')} of {target}: {body}" if body else None
    why = ([f"there are only {periods} {unit}s of data"] if periods < MIN_TREND_PERIODS else []) + ([f"dates are on only {cov:.0%} of the rows"] if cov is not None and cov < MIN_DATE_COVERAGE else [])
    return shown, (f"The data is too short to call a trend: {' and '.join(why)}." if why else None)


def breakdown_lines(ids: list[str], r_by_id: dict) -> tuple[str | None, list[str]]:
    """(the fact the lines replace, lines) for a 'where is it worst / how much do we lose' question: the breakdown with the largest spread between its groups. When every breakdown's
    spread is small the line says so ('similar across <group> (low to high): no real difference'). The lead fact of a loss answer (the amount outside the main value) comes first."""
    outside = [r_by_id[i] for i in ids if i in r_by_id and r_by_id[i].get("kind") == "outside_rate"]
    lead = outside[0] if outside else next((r_by_id[i] for i in ids if i in r_by_id and not r_by_id[i].get("kind")), None)
    if lead is None:
        return None, []
    if outside:
        pool = [{"group": b["group"], "range": b["range"], "spread": b["spread"], "similar": b["similar"], "line": b["clause"]} for r in outside for b in r.get("by") or [] if "spread" in b]
    else:
        pool = [{"group": r["group"], "range": r["range"], "spread": r["spread"], "similar": r["similar"], "line": r["short"]} for r in r_by_id.values()
                if not r.get("kind") and r["measure"] == lead["measure"] and r["basis"] == lead["basis"] and r.get("focus", True) and "spread" in r]
    if not pool:
        return None, []
    pool.sort(key=lambda b: -b["spread"])
    head = [clip(lead.get("head") or lead.get("short") or lead["text"])] if outside else []
    if all(b["similar"] for b in pool):
        return lead["id"], head + ["Similar across " + " and ".join(f"{b['group']} ({b['range']})" for b in pool[:3]) + ": no real difference."]
    best = pool[0]
    return lead["id"], head + [clip(f"Biggest spread is across {best['group']} ({best['range']}): {best['line']}")]


def look_into(i: str, by_id: dict, r_by_id: dict, state: dict) -> str | None:
    """'Look into <top group> first: <figure with measure>', built from a computed fact. None when the fact names no top group, or has no figure that names its measure."""
    if i in r_by_id:
        r = r_by_id[i]
        top = wording.entity(r["group"], r["largest"]) if r.get("kind") == "outside_rate" and r.get("largest") else None if r.get("kind") else wording.entity(r["group"], r["top"])
    else:
        f = by_id.get(i) or {}
        top = wording.entity(f["params"]["group"], f["stats"]["best"]) if (f.get("params") or {}).get("group") and (f.get("stats") or {}).get("best") else None
    line = fact_line(i, by_id, r_by_id, state) if top else ""
    return f"Look into {top} first: {line.rstrip('. ')}." if top and concrete(line, {measure_of(i, by_id, r_by_id)}) else None


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
        groups = named_groups(a["question"], ids, r_by_id, main, state) if ids and not action else []
        mine, replaces = code_so_what(ids, by_id, r_by_id)
        card = {"n": n, "num": None if action else shown, "question": a["question"], "ids": a["ids"], "gcols": sorted(group_cols(ids, by_id, r_by_id)), "trust": trust_of(ids, by_id, r_by_id), "trust_why": "",
                "so_what": readable(state, mine if replaces else with_shares(so_what.get(a["question"], ""), mine)), "so_what_by": "code" if replaces else "ai", "action_question": action, "text": "", "date_tag": DATE_TAG in a["answer"]}
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
        elif len([i for i in groups if i not in used]) >= 2:        # a question naming several columns: one line for each
            lines = [ln(i) for i in groups if i not in used]
            card["ids"] = a["ids"] + [i for i in groups if i not in a["ids"]]
            card["trust"] = trust_of(ids + groups, by_id, r_by_id)
            ids = ids + [i for i in groups if i not in ids]
            limit = GROUP_LINES
        elif not ids and sids and card["action_question"]:
            lines = ["Answered under 'What to do' below."]
            card["trust"] = next((str(strat[i]["confidence"]).capitalize() for i in sids if strat[i].get("confidence")), "Medium")
            card["trust_why"] = f"the strategy's own evidence is rated {card['trust'].lower()}"
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
        if not action and wording.TREND_Q.search(a["question"]):
            tid = next((i for i in ids if (by_id.get(i) or {}).get("tool") == "trend" and any(origin.get(t) == i for t in lines)), None)
            totals_line, caution = trend_lines(by_id[tid], state) if tid else (None, None)
            if tid and (totals_line or caution):
                at = next(k for k, t in enumerate(lines) if origin.get(t) == tid)
                lines[at:at + 1] = ([] if caution else [lines[at]]) + [x for x in (totals_line, caution) if x]      # a trend that is too short to call is not stated, only its figures and why
                limit = max(limit, TREND_LINES)
        if not action and WORST_Q.search(a["question"]):
            lead, found = breakdown_lines(ids, r_by_id)
            at = next((k for k, t in enumerate(lines) if origin.get(t) == lead), None) if found else None
            if at is not None:
                lines[at:at + 1] = found
                limit = max(limit, len(found))
        before = lines[:limit]
        lines = label_growth_measures(before, origin, by_id, r_by_id)
        card["text"] = [readable(state, t) for t in lines]
        shown_from = {t: origin.get(b) for t, b in zip(card["text"], before)}          # each line as shown -> the fact it came from
        card["trust"] = cap_by_text(card["trust"], card["text"])
        card["trust_why"] = card["trust_why"] or trust_reason(card["trust"], ids, by_id, r_by_id, card["text"], shown_from)
        out.append(card)
    return out


def card_md(c: dict, detail: list[str] | None = None, charts: list[dict] | None = None) -> list[str]:
    tag = f" [{DATE_TAG}]" if c["date_tag"] and c["text"] and not c["text"][0].startswith("Not answered") else ""
    L = [f"**{c['num']}. {plain(c['question'])}**", ""] + [f"{t}{tag if k == len(c['text']) - 1 else ''}  " for k, t in enumerate(c["text"])]
    for ch in charts or []:        # 1 to 2 charts drawn from the facts the card uses, each with a "how to read this" caption
        L += ["", f"![{ch['title']}]({ch['path']})", "", f"*How to read this: {ch['caption']}*", ""]
    foot = []
    if c["trust"]:
        foot.append(f"Trust: **{c['trust']}**" + (f": {c['trust_why']}" if c.get("trust_why") else ""))
    if c["so_what"]:
        foot.append(f"So what: {c['so_what']}")
    L += [" · ".join(foot), ""]
    if detail:        # opens in place: no link, no navigation, no change to the address
        L += ['<details class="card-details">', "<summary>Details</summary>", ""] + detail + ["", "</details>", ""]
    return L


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
        if not sup or strat_mod.monitor_only(s) or strat_mod.blocked(f"{s['title']}: {s['objective']}", s["evidence"], state):      # 'monitor, do not act' is not a recommendation
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
            if strat_mod.monitor_only({"title": ai[c["question"]]}) or strat_mod.blocked(ai[c["question"]], cited_ids, state):
                continue
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


def action_lines(cs: list[dict], plan: dict) -> list[str]:
    """The one recommendation list of the report: 'What to do' shows it, and so does the Strategy section's top recommendations (same items, same order)."""
    num = {c["n"]: c["num"] for c in cs}
    return [f"{i}. {a['text']}" + (f" *(question {num[a['n']]})*" if num.get(a["n"]) else "") for i, a in enumerate(plan["actions"], 1)]      # an action question has no number: its actions carry no tag


def actions_md(cs: list[dict], plan: dict, support: dict | None = None) -> list[str]:
    """The actions, each followed by the chart and figure that support it, indented under its item."""
    num = {c["n"]: c["num"] for c in cs}
    L: list[str] = []
    for k, line in enumerate(action_lines(cs, plan)):
        L.append(line)
        s = (support or {}).get(k)
        if s and s.get("chart"):
            L += ["", f"   ![{s['title']}]({s['chart']})"]
        if s and s.get("figure"):
            L += ["", f"   *Supported by: {s['figure']}*"]
        if s:
            L.append("")
    L += [f"- No action suggested for question {num[n]}: {reason}" for n, reason in sorted(plan["none"].items()) if num.get(n)]
    return L or ["- No action suggested: no goal question has a finding with a medium or larger effect to act on."]


# ------------------------------------------------------------------ D. key numbers
def loss_tile(state: dict, view: dict) -> tuple[str, str] | None:
    """When the goal asks about loss: the amount outside the main value and its share, from the computed fact (the one a loss question's answer cites, else the first)."""
    if not LOSS_Q.search(state.get("goal", "")):
        return None
    outside = [r for r in view.get("rankings") or [] if r.get("kind") == "outside_rate"]
    cited = {i for a in view.get("answers") or [] if LOSS_Q.search(a["question"]) for i in a["ids"]}
    r = next((r for r in outside if r["id"] in cited), outside[0] if outside else None)
    if r is None:
        return None
    share = r.get("rate_amount") if r.get("rate_amount") is not None else r.get("rate_rows")
    amount = f"{r['outside']:,.0f}" if r["measure"] == "rows" else summary.short(r["outside"], summary._cur(r["measure"], state))
    return readable(state, f"{r['measure']} outside {r['top']}"), amount + (f" ({share:.1%})" if share is not None else "")


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
    loss = loss_tile(state, view)
    grain = (state.get("profile") or {}).get("grain")
    first = kpis[:TILES_MAX - 2 - bool(loss) - bool(grain)]
    order_tile = [(grain["label"], f"{grain['orders']:,}")] if grain else []          # several rows per order: the order count (by key), next to the first figure, never the row count
    out = [(readable(state, m["label"]), m["value"]) for m in first[:1]] + order_tile + ([loss] if loss else []) + [(readable(state, m["label"]), m["value"]) for m in first[1:]]
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
def order_key(named: bool, effect: str | None, score: float) -> tuple:
    """The one ordering of findings: goal-named first, a small or negligible effect last, score within a group. Used by the dashboard's key findings and 'What stands out' and by the report's evidence list."""
    return (effect in ("negligible", "small"), not named, -score)


def gallery_order(state: dict, top: int = 4) -> dict:
    """{"key": ids, "more": ids}: findings that name the goal's columns lead; a small or negligible effect, a weak correlation, or (when the goal names some column) a finding on a column
    it does not name is never a key finding and goes to 'More charts'. Score decides within a group."""
    goal = state.get("goal", "")
    fs = [f for f in state.get("findings") or [] if f.get("insight")]
    named = {f["id"]: goal_named(f, goal) for f in fs}
    ranked = sorted(fs, key=lambda f: order_key(named[f["id"]], effect_label(f.get("stats") or {}), f["insight"].get("score") or 0))
    strong = [f["id"] for f in ranked if effect_label(f.get("stats") or {}) not in ("negligible", "small")]
    key = [i for i in strong if named[i]] if any(named[i] for i in strong) else strong
    return {"key": key[:top], "more": [f["id"] for f in ranked if f["id"] not in key[:top]]}
