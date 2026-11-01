"""Strategist agent: turns evidence into prioritised, testable strategies.

Rules that keep it honest:
  * A data-backed strategy must cite at least one finding, and only findings that are statistically significant AND have a
    meaningful effect (the analysis tools only attach an `action` in that case) can create one.
  * Strategies are framed as things to TEST (pilot design included), never as proven cause and effect.
  * If the evidence is thin the output says so, instead of inventing a plan.
  * An optional LLM may rewrite the wording using the company's context. It cannot change evidence, impact, effort or add new strategies,
    and every number in its text must already exist in the findings.
"""
from __future__ import annotations

import json
import re

from . import cited, context as ctxmod, understanding as undmod, wording
from .measures import measure_kind
from .insights import allowed_numbers, effect_label, polarity
from .llm import ask_json
from .tools import _fmt

IMPACT_PTS = {"High": 3, "Medium": 2, "Low": 1}
EFFORT_PTS = {"Low": 1, "Medium": 2, "High": 3}
CONF_PTS = {"high": 2, "medium": 1, "low": 0, "exploratory": 0, "descriptive": 0}
HORIZON = {"Low": "Quick win (0-30 days)", "Medium": "Next quarter (30-90 days)", "High": "Strategic (3-12 months)"}


def _impact(f: dict) -> str:
    st, tool = f["stats"], f["tool"]
    lab = effect_label(st)
    if lab:
        return {"large": "High", "medium": "Medium"}.get(lab, "Low")
    if tool == "trend":
        r = abs(st.get("relative_change") or 0)
        return "High" if r >= 0.3 else "Medium" if r >= 0.1 else "Low"
    if tool == "pareto":
        return "High" if (st.get("top_share") or 0) >= 0.7 else "Medium"
    if tool == "correlation":
        r = max((abs(v) for v in (st.get("top_pairs") or {}).values() if v is not None), default=0)
        return "High" if r >= 0.7 else "Medium"
    if tool == "segment_trend":
        g = st.get("groups", {})
        spread = abs(g.get(st.get("fastest"), {}).get("relative_change", 0) - g.get(st.get("slowest"), {}).get("relative_change", 0))
        return "High" if spread >= 0.6 else "Medium"
    return "Medium"


def _additive(state: dict, name: str) -> bool:
    """A share of the total only makes sense for a measure that adds up; never for a ratio, a rate or a derived ratio."""
    if name in {d["name"] for d in (state.get("plan") or {}).get("derived") or []}:
        return False
    return measure_kind(name, overrides=((state.get("brief") or {}).get("kinds") or {})) == "additive"


# a strategy pushes one way: reducing a measure and promoting it are opposite directions
DOWN_WORDS = re.compile(r"\b(reduc\w*|lower\w*|cut|decreas\w*|curb\w*|fix)\b", re.I)
UP_WORDS = re.compile(r"\b(promot\w*|boost\w*|grow\w*|increas\w*|upsell\w*|expand\w*|invest\w*)\b", re.I)


def pushes_both_ways(*texts: str) -> bool:
    blob = " ".join(texts)
    return bool(DOWN_WORDS.search(texts[0]) and UP_WORDS.search(blob)) or bool(UP_WORDS.search(texts[0]) and DOWN_WORDS.search(" ".join(texts[1:])))


# ------------------------------------------------------------------ what a recommendation may not say
INVEST_WORDS = re.compile(r"\b(invest\w*|put more (?:behind|into)|more behind|double down|expand\w*|scale up|allocate more|direct more|(?:increase|raise) (?:the )?(?:spend|budget|investment|funding))\b", re.I)
RANK_WORDS = re.compile(r"\b(top|best|highest|leading|largest|biggest|lowest|worst|smallest|rank\w*)\b", re.I)
MONITOR_ONLY = re.compile(r"^\W*(?:monitor|keep (?:monitoring|watching)|watch|no action|do not act|don't act)\b", re.I)
CONCENTRATION_RULE = ("A group is never recommended for extra investment only because it holds the largest share of a total: a concentration finding supports only 'protect' or 'diversify'. "
                      "If a fact says the differences between groups are not statistically significant, no action may rely on ranking those groups. ")


def _concentration(i: str, by_id: dict, r_by_id: dict) -> bool:
    """A fact that says only where the total sits: a concentration finding, or the top group's share of a total."""
    if i in by_id:
        return by_id[i].get("tool") == "pareto"
    r = r_by_id.get(i)
    return bool(r) and r.get("basis") == "total" and not r.get("kind")


def blocked(text: str, ids: list[str], state: dict) -> str:
    """Why this action may not stand ('' when it may): it recommends investing in a group on a share-only finding, or it ranks groups whose differences the report itself calls not significant."""
    by_id = {f["id"]: f for f in state.get("findings") or []}
    r_by_id = {r["id"]: r for r in (state.get("summary_facts") or {}).get("rankings") or []}
    ids = [i for i in ids if i in by_id or i in r_by_id]
    if ids and INVEST_WORDS.search(text) and all(_concentration(i, by_id, r_by_id) for i in ids):
        return "extra investment recommended only because a group holds the largest share"
    if RANK_WORDS.search(text):
        from .summary import names
        for f in state.get("findings") or []:
            g = (f.get("params") or {}).get("group")
            if f.get("tool") == "group_compare" and f.get("significant") is False and g and names(str(g), text) == 2:
                return f"ranks {g} groups although their differences are not statistically significant"
    return ""


def monitor_only(s: dict) -> bool:
    """A strategy that only says to watch or explain a movement: not a recommendation."""
    return bool(s.get("monitor")) or bool(MONITOR_ONLY.match(str(s.get("title", ""))))


def _cand(**kw) -> dict:
    kw.setdefault("kind", "data")
    kw.setdefault("source", "rules")
    return kw


def candidates(state: dict) -> list[dict]:
    ctx = state.get("context") or {}
    lens = ctxmod.lens(ctx)
    cust, levers = lens["customer"], lens["levers"]
    plan = state["plan"]
    pol = state.get("polarity")
    out: list[dict] = []
    seen_titles: set[str] = set()

    def add(c: dict | None):
        if c and c["title"] not in seen_titles:
            seen_titles.add(c["title"])
            out.append(c)

    for f in state["findings"]:
        if not f.get("significant") or not f.get("action"):
            continue
        if f["stats"].get("survives_correction") is False:
            continue  # a lead that did not survive multiple-testing correction is not a basis for a strategy
        tool, st, p = f["tool"], f["stats"], f.get("params", {})
        conf = f["insight"]["confidence"]["level"]
        impact = _impact(f)
        ev = [f["id"]]
        base = {"evidence": ev, "impact": impact, "confidence": conf}

        if tool == "group_compare":
            target, group, top, bot = p["target"], p["group"], st["best"], st["worst"]
            pv = polarity(target, pol)
            bad = pv == "bad"
            noun = f"{target} rate" if st.get("kind") == "binary" else target
            e_top, e_bot = wording.entity(group, top), wording.entity(group, bot)
            what = wording.columns_phrase(wording.other_columns(state, group))
            share_kpi = [f"Share of total {target} coming from {e_top}"] if _additive(state, target) else []
            if bad:
                add(_cand(**base, id="", title=f"Run a focused {target} reduction programme for {group} = {top}", theme="Targeting",
                          objective=f"Reduce {target} where it is concentrated ({e_top}) instead of spreading effort across every {wording.human(group)}.",
                          rationale=f.get("insight", {}).get("headline", ""),
                          actions=[f"Compare {e_top} with {e_bot}: where do they differ in {what}?",
                                   f"Design a targeted intervention to lower {noun} for {e_top}, keeping {e_bot} as a comparison group.",
                                   f"Set a baseline {noun} for {top}, agree a target and name an owner."],
                          effort="Medium",
                          how_to_test=f"Run a 4-8 week pilot: apply the intervention to a random half of {e_top} and keep the rest as control. Compare {noun} between the halves; the random split separates the effect of the change from the pre-existing gap.",
                          kpis=[f"{noun} for {top}", f"Gap between {top} and {bot}", "Pilot vs control difference"],
                          risks=["The gap may reflect other differences between the groups, not the group itself (association, not cause).", f"Focusing on {e_top} can neglect other groups; keep watching them."]))
            elif pv == "neutral":
                add(_cand(**base, monitor=True, id="", title=f"Explain why {target} differs by {group}: {top} vs {bot}", theme="Understanding",
                          objective=f"Find out why {target} is highest for {group} = {top} and lowest for {bot}, before deciding whether either is a problem.",
                          rationale=f.get("insight", {}).get("headline", ""),
                          actions=[f"Agree with the business whether a higher or lower {target} is better, and for which {wording.human(group)} values.",
                                   f"Compare {e_top} and {e_bot} on {what}.",
                                   f"Track {target} by {group} monthly to see whether the gap persists."],
                          effort="Low", how_to_test=f"Check whether the {top} vs {bot} gap holds inside other splits (e.g. region, product) before any change.",
                          kpis=[f"{target} for {top} and for {bot}"], risks=["Without a clear good direction for this measure, a gap is a pattern to explain, not a problem or a win."]))
            else:
                add(_cand(**base, id="", title=f"Put more behind what works: {group} = {top}", theme="Growth",
                          objective=f"Grow overall {target} by investing where it is strongest ({e_top}) while diagnosing why {e_bot} lags.",
                          rationale=f.get("insight", {}).get("headline", ""),
                          actions=[f"Direct more {levers[0]} to {e_top} and track {target} for it.",
                                   f"Find out why {e_bot} lags: compare it with {e_top} on {what}.",
                                   f"Decide whether to lift {e_bot} or deliberately shift resources toward {e_top}."],
                          effort="Medium",
                          how_to_test=f"Pilot the extra investment on part of {top} (or a comparable subset) for 4-8 weeks with a hold-out group, and compare {target} between them.",
                          kpis=[f"{target} for {top} and for {bot}", *share_kpi, "Pilot vs hold-out difference"],
                          risks=[f"{e_top} may already be near its ceiling, so extra spend could show diminishing returns.", "The gap may partly reflect differences in mix rather than the group itself."]))
        elif tool == "outcome_compare":
            target, col, pos, dirn = p["target"], p["col"], st.get("positive", "yes"), st.get("direction", "higher")
            if polarity(target, pol) == "bad":
                add(_cand(**base, id="", title=f"Build an early-warning signal on {col} for {target} = {pos}", theme="Early warning",
                          objective=f"Spot at-risk {cust} early and act before {target} = {pos} happens.",
                          rationale=f.get("insight", {}).get("headline", ""),
                          actions=[f"Define a threshold on {col}: affected rows typically have a median of {_fmt(st['median_outcome'])} versus {_fmt(st['median_other'])} for the rest ({dirn} values go with the outcome).",
                                   f"Flag {cust} past the threshold on a regular schedule and route them to a named owner.",
                                   f"Track how many flagged {cust} actually reach {target} = {pos}, and how many outcomes the flag misses."],
                          effort="Low",
                          how_to_test=f"For 4-6 weeks, contact a random half of the flagged {cust} (retention offer, check-in or fix) and leave the other half alone; compare how often each half reaches {target} = {pos}.",
                          kpis=[f"{target} = {pos} rate among flagged vs unflagged", "Share of outcomes the flag catches", "Cost per saved case"],
                          risks=[f"{col} may be a symptom rather than a cause, so changing {col} itself may not help.", "Some flagged cases will be false alarms; keep the intervention cheap."]))
            else:
                add(_cand(**base, id="", title=f"Use {col} to prioritise {cust} likely to reach {target} = {pos}", theme="Prioritisation",
                          objective=f"Focus effort on the {cust} most likely to reach {target} = {pos}.",
                          rationale=f.get("insight", {}).get("headline", ""),
                          actions=[f"Rank {cust} by {col} and give the top group priority.", "Compare results against a random sample to confirm the ranking really helps."],
                          effort="Low", how_to_test="Treat a prioritised sample and a random sample the same way for 4 weeks and compare outcomes.",
                          kpis=[f"{target} = {pos} rate in the prioritised group vs random", "Effort saved"], risks=["The relationship may weaken outside this dataset."]))
        elif tool == "trend":
            target = p["target"]
            up = (st.get("slope") or 0) > 0
            pv = polarity(target, pol)
            good = up != (pv == "bad")
            if pv == "neutral":
                title, verb = f"Understand the {'rise' if up else 'fall'} in {target}", "explain"
            else:
                title, verb = ((f"Understand and sustain the {'rise' if up else 'fall'} in {target}") if good else f"Find and fix what is driving {target} the wrong way"), ("extend" if good else "reverse")
            add(_cand(**base, monitor=pv == "neutral", id="", title=title, theme="Monitoring",
                      objective=f"Explain the {'upward' if up else 'downward'} movement in {target}" + ("." if pv == "neutral" else f" and decide whether to {verb} it."),
                      rationale=f.get("insight", {}).get("headline", ""),
                      actions=["Find when the movement began and list what changed then, in the business or in how the data is recorded.",
                               "Break the trend down by segment to see where it comes from.",
                               f"Decide whether to {'invest further in what is working' if good else 'intervene'} and agree how you will measure it." if pv != "neutral" else "Agree with the business whether this movement is good or bad, and how you will measure it."],
                      effort="Low", how_to_test="Compare periods before and after the suspected cause, and check the same calendar period in previous years to rule out seasonality.",
                      kpis=[f"{target} by month", "Segment contribution to the change"], risks=["The first and last periods may be partial.", "Seasonality can mimic a trend."]))
        elif tool == "segment_trend":
            target, group, fast, slow = p["target"], p["group"], st["fastest"], st["slowest"]
            if wording.trend_shape(st) != "mixed":
                continue                  # everything moves the same way: there is nothing to rebalance
            moves = [(g, wording.significant_direction(st, g)) for g in (fast, slow)]
            moves = [(g, d) for g, d in moves if d]
            if not moves:
                continue                  # no group's own trend is reliable, so no group is called rising or falling
            named = ", ".join(f"{g} {d}" for g, d in moves)
            subj = " and ".join(wording.entity(group, g) for g, _ in moves)
            add(_cand(**base, id="", title=f"Rebalance attention across {group}: {named}", theme="Allocation",
                      objective=f"Respond to the diverging trends in {target}: {named}.",
                      rationale=f.get("insight", {}).get("headline", ""),
                      actions=[f"Identify what changed for {wording.entity(group, g)} and whether it can be repeated." if d == "rising" else
                               f"Diagnose why {wording.entity(group, g)} is slipping before more effort flows there." for g, d in moves]
                              + [f"Review how effort is allocated across the {wording.human(group)} values."],
                      effort="Medium", how_to_test=f"Keep the current plan for one set of {subj} and change it for another; compare the next 8 weeks.",
                      kpis=[f"{target} by {group} by month"] + ([f"Share of {target} from {fast}"] if _additive(state, target) else []),
                      risks=["Per-segment trends rest on fewer data points and are less certain than the overall trend."]))
        elif tool == "pareto":
            target, group, k = p["target"], p["group"], st["top_k"]
            add(_cand(**base, id="", title=f"Protect the {k} {group} values that drive {target}, and grow the tail", theme="Concentration",
                      objective=f"Reduce dependence on a few {group} values while keeping the leaders healthy.",
                      rationale=f.get("insight", {}).get("headline", ""),
                      actions=[f"Assign clear ownership and a health check for each of the top {k}.", "Watch them monthly for early signs of decline.",
                               f"Pilot growth tactics in mid-ranked {group} values that look similar to the leaders."],
                      effort="Medium", how_to_test="Track the top contributors' share each month; test one growth tactic on a few mid-ranked values with a comparison set.",
                      kpis=[f"Share of {target} from the top {k}", f"Number of {group} values contributing meaningfully"],
                      risks=["Concentration may reflect real advantages of the leaders that cannot be copied."]))
        elif tool == "scatter":
            a, b = st["x"], st["y"]
            add(_cand(**base, id="", title=f"Test whether {a} is a lever for {b}", theme="Experiment",
                      objective=f"Find out whether changing {a} actually moves {b}, or whether it is only an indicator.",
                      rationale=f.get("insight", {}).get("headline", ""),
                      actions=[f"Check whether {a} is something the business can change.", f"Run a controlled change in {a} on part of the base.", f"Track both {a} and {b} monthly."],
                      effort="Medium", how_to_test=f"Change {a} for a random subset, keep the rest as control, and compare {b}.",
                      kpis=[a, b], risks=["Correlation can come from a hidden common cause; the test is what shows whether it is a lever."]))
        elif tool == "seasonality":
            target, hi, lo = p["target"], st["best"], st["worst"]
            if polarity(target, pol) == "bad":    # when lower is better the period with the highest value is the weak one
                hi, lo = lo, hi
            add(_cand(**base, id="", title=f"Plan around the {st.get('cycle', 'weekday')} pattern in {target}", theme="Timing",
                      objective=f"Match effort and resources to when {target} is strong ({hi}) and weak ({lo}).",
                      rationale=f.get("insight", {}).get("headline", ""),
                      actions=[f"Plan capacity and resources for {hi}.", f"Test one change in the weak period ({lo}) to lift it.", "Review the pattern each quarter in case it shifts."],
                      effort="Low", how_to_test=f"Shift one planned activity from {hi} to {lo} for a few weeks and compare {target} against the usual pattern.",
                      kpis=[f"{target} by {'day' if st.get('cycle') == 'weekday' else 'month'}"], risks=["The pattern may come from one-off events in the period covered."]))
        elif tool == "correlation":
            pairs = st.get("top_pairs") or {}
            key, r = max(pairs.items(), key=lambda kv: abs(kv[1] or 0), default=(None, 0))
            if key:
                a, b = key.split("|")
                add(_cand(**base, id="", title=f"Test whether {a} is a lever for {b}", theme="Experiment",
                          objective=f"Find out whether changing {a} actually moves {b}, or whether it is only an indicator.",
                          rationale=f.get("insight", {}).get("headline", ""),
                          actions=[f"Check whether {a} is something the business can change.", f"Run a controlled change in {a} on part of the base.", f"Track both {a} and {b} monthly."],
                          effort="Medium", how_to_test=f"Change {a} for a random subset, keep the rest as control, and compare {b}.",
                          kpis=[a, b], risks=["Correlation can come from a hidden common cause; the test is what shows whether it is a lever."]))

    data_backed = out[:8]
    # foundations (not derived from a finding; labelled as such)
    gaps = state.get("data_gaps") or []
    important = [g for g in gaps if g["severity"] == "warn"][:3] or gaps[:2]
    foundations: list[dict] = []
    if important:
        listed = "; ".join(g["title"].rstrip(".") for g in important)
        foundations.append(_cand(id="", kind="foundation", title="Close the most important data gaps", theme="Data foundation",
                                 objective=f"Close these gaps from this run: {listed}.", rationale="Gaps found while checking the data against your goal and KPIs.",
                                 actions=[g["suggestion"] for g in important], effort="Low", impact="Medium", confidence="", evidence=[],
                                 how_to_test="Re-run this analysis once the data is added and compare what changes.", kpis=["Share of your KPIs measurable from the data"], risks=["Collecting data has a cost; add only what changes a decision."]))
    foundations.append(_cand(id="", kind="foundation", title="Make this repeatable: track the main KPI in Power BI", theme="Data foundation",
                             objective="Turn a one-off analysis into a regular review so changes are seen early.", rationale="Insights lose value if nobody looks at them again.",
                             actions=["Load the Power BI pack, paste the DAX measures and build the suggested visuals.", "Agree an owner and a monthly review of the headline KPI and the top segments.", "Re-run this analysis on fresh data each quarter."],
                             effort="Low", impact="Medium", confidence="", evidence=[], how_to_test="Check after two review cycles whether decisions actually changed.",
                             kpis=["Review held on schedule", "Time from change to detection"], risks=["Dashboards that nobody owns go stale."]))
    return data_backed + foundations


def _effect_size(f: dict) -> float:
    """How large the checked effect of one finding is, on a 0..1 scale that is comparable across the effect measures (Cramer's V is squared to be eta-squared-like)."""
    st = f.get("stats") or {}
    e, name = st.get("effect"), st.get("effect_name")
    if e is None:
        return 0.0
    try:
        e = abs(float(e))
    except (TypeError, ValueError):
        return 0.0
    return e * e if name == "cramers_v" else e


def effect_of(c: dict, findings: list[dict] | None) -> float:
    """The largest checked effect among the findings a strategy cites (0 when it cites none, as foundation steps do)."""
    by_id = {f.get("id"): f for f in findings or []}
    return max((_effect_size(by_id[i]) for i in c.get("evidence") or [] if i in by_id), default=0.0)


def prioritise(cands: list[dict], findings: list[dict] | None = None) -> list[dict]:
    """Ranks by impact, confidence and effort; a tie goes to the larger checked effect in the data the strategy cites, then to the order the candidates were found in (never by name)."""
    for pos, c in enumerate(cands):
        c["horizon"] = HORIZON[c["effort"]]
        c["priority_score"] = 2 * IMPACT_PTS[c["impact"]] + CONF_PTS.get(c["confidence"], 0) - EFFORT_PTS[c["effort"]]
        c["effect_size"] = round(effect_of(c, findings), 6)
        c["_pos"] = pos
    cands.sort(key=lambda c: (c["kind"] != "data", -c["priority_score"], -c["effect_size"], c["_pos"]))
    for c in cands:
        c.pop("_pos", None)
    for i, c in enumerate(cands, 1):
        c["id"] = f"s{i}"
        c["rank"] = i
    return cands


REFINE_SYSTEM = """You are a senior strategy consultant adapting candidate strategies to a specific company.
Rewrite wording so it fits the company's industry and vocabulary. You may change: title, objective, actions (3-5), risks (2-3), kpis, how_to_test.
You may NOT add strategies, change ids, evidence, impact or effort, or introduce any number that is not already in the candidate or the facts.
{rule}
Write the id of the fact a figure comes from in square brackets right after the figure (e.g. "[f3]"); use "candidate" for a figure already in the candidate's own text. Every number needs an id, and the words around it must be about that fact's subject.
Keep each action concrete and specific; do not claim cause and effect.
Return JSON: {"strategies": [{"id": "...", "title": "...", "objective": "...", "actions": ["..."], "risks": ["..."], "kpis": ["..."], "how_to_test": "..."}]}""".replace("{rule}", CONCENTRATION_RULE)


def refine_with_llm(llm, state: dict, strategies: list[dict]) -> int:
    if llm is None:
        return 0
    ctx_txt = ctxmod.context_text(state.get("context"))
    cands = [s for s in strategies if s["kind"] == "data"]
    if not cands:
        return 0
    payload = [{"id": s["id"], "title": s["title"], "objective": s["objective"], "actions": s["actions"], "risks": s["risks"], "kpis": s["kpis"], "how_to_test": s["how_to_test"],
                "facts": [{"id": f["id"], "text": f["text"].replace("**", "")} for f in state["findings"] if f["id"] in s["evidence"]]} for s in cands]
    out = ask_json(llm, REFINE_SYSTEM, (f"COMPANY CONTEXT (user-written, unverified):\n{ctx_txt}\n\n" if ctx_txt else "") + f"GOAL: {state['goal']}\nCANDIDATES:\n{json.dumps(payload, default=str, ensure_ascii=False)}")
    if not out or not isinstance(out.get("strategies"), list):
        return 0
    by_id = {s["id"]: s for s in cands}
    improved = 0
    for item in out["strategies"]:
        s = by_id.get(item.get("id")) if isinstance(item, dict) else None
        if not s:
            continue
        facts = [f for f in state["findings"] if f["id"] in s["evidence"]]
        own = " ".join([s["title"], s["objective"], s["how_to_test"], *s["actions"], *s["risks"], *s["kpis"]])
        computed = cited.claims((state.get("summary_facts") or {}).get("rankings") or [])
        known = {"candidate": cited.make_fact(own + " " + ctx_txt, own)}
        for f in facts:
            known[f["id"]] = cited.make_fact(f["text"] + " " + f.get("title", ""), f["text"] + " " + f.get("title", "") + " " + " ".join(str(v) for v in (f.get("params") or {}).values() if isinstance(v, str)), cited.scope_of(f))
            known[f["id"]]["numbers"] += allowed_numbers(json.dumps(f["stats"], default=str, ensure_ascii=False))
            known[f["id"]]["subject"] |= known["candidate"]["subject"]        # the strategy is about its evidence, so its own wording names the subject of that evidence
        changed = False
        before = {k: list(v) if isinstance(v, list) else v for k, v in s.items() if k in ("title", "objective", "actions", "risks", "kpis", "how_to_test")}

        def clean_text(v, lo=8, hi=260):
            """The text without its fact ids when it is the right length and every figure is tied to a fact it matches, else None (the reason is recorded)."""
            if not isinstance(v, str):
                return None
            text, why = cited.verify(v, known)
            wrong = None if why else cited.contradicts(text, computed)
            if wrong:
                why = f"disagrees with computed fact {wrong['id']}"
            if why or wording.unsupported_profit_claim(text, state):
                state.setdefault("decisions", []).append({"decision": "strategy wording", "source": "rules", "chosen": "kept the rule-based wording", "why": f"{s['id']}: " + (why or "no cost or profit column in the data"),
                                                          "errors": [text[:200]], "raw_output": ""})
                return None
            return text.strip() if lo <= len(text.strip()) <= hi else None

        for key, hi in (("title", 140), ("objective", 300), ("how_to_test", 420)):
            if (t := clean_text(item.get(key), 8, hi)) is not None:
                s[key], changed = t, True
        for key, mx in (("actions", 5), ("risks", 3), ("kpis", 4)):
            lst = [t for x in (item.get(key) or []) if (t := clean_text(x)) is not None][:mx]
            if len(lst) >= (2 if key != "kpis" else 1):
                s[key], changed = lst, True
        if changed and (pushes_both_ways(s["title"] + " " + s["objective"], *s["actions"]) or blocked(" ".join([s["title"], s["objective"], *s["actions"]]), s["evidence"], state)):
            s.update({k: v for k, v in before.items()})        # reducing and promoting the same thing in one strategy: keep the rule-based wording
            changed = False
        if changed:
            s["source"] = "llm"
            improved += 1
    return improved


def summary_text(strategies: list[dict], state: dict) -> str:
    backed = [s for s in strategies if s["kind"] == "data"]
    if not backed:
        return ("No strong, data-backed lever was found in this data. That is a result in itself: it means the columns provided do not show a difference big enough, "
                "and reliable enough, to justify acting on. Collect more data or additional variables, or sharpen the question, before committing budget.")
    top = backed[0]
    return (f"{len(backed)} data-backed strateg{'y' if len(backed) == 1 else 'ies'} follow from the evidence, ranked by expected impact, confidence and effort. "
            f"The top priority is: {top['title']}. Every strategy below is a hypothesis to test with a small pilot first, because the analysis shows association, not cause.")


def build_strategies(state: dict, llm) -> tuple[list[dict], str, int]:
    strategies = prioritise(candidates(state), state.get("findings"))
    improved = refine_with_llm(llm, state, strategies)
    for s in strategies:           # wording only: a raw column name (discount_pct) is shown as its display name
        for k in ("title", "objective", "rationale", "how_to_test"):
            if isinstance(s.get(k), str):
                s[k] = undmod.readable(s[k], state)
        for k in ("actions", "kpis", "risks"):
            if isinstance(s.get(k), list):
                s[k] = [undmod.readable(x, state) if isinstance(x, str) else x for x in s[k]]
    return strategies, summary_text(strategies, state), improved
