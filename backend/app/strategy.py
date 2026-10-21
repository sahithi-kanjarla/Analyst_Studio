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

from . import context as ctxmod
from .insights import allowed_numbers, effect_label, numbers_ok, polarity
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


def _cand(**kw) -> dict:
    kw.setdefault("kind", "data")
    kw.setdefault("source", "rules")
    return kw


def candidates(state: dict) -> list[dict]:
    ctx = state.get("context") or {}
    lens = ctxmod.lens(ctx)
    cust, levers = lens["customer"], lens["levers"]
    plan = state["plan"]
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
            bad = polarity(target) == "bad"
            noun = f"{target} rate" if st.get("kind") == "binary" else target
            if bad:
                add(_cand(**base, id="", title=f"Run a focused {target} reduction programme for {group} = {top}", theme="Targeting",
                          objective=f"Reduce {target} where it is concentrated ({group} = {top}) instead of spreading effort across all {cust}.",
                          rationale=f.get("insight", {}).get("headline", ""),
                          actions=[f"Profile {cust} with {group} = {top}: what do they share that {bot} {cust} do not (contract, channel, usage, price)?",
                                   f"Design a targeted intervention around {levers[0]} for {top}, keeping {bot} as a comparison group.",
                                   f"Set a baseline {noun} for {top}, agree a target and name an owner."],
                          effort="Medium",
                          how_to_test=f"Run a 4-8 week pilot: apply the intervention to a random half of the {top} {cust} and keep the rest as control. Compare {noun} between the halves; the random split separates the effect of the change from the pre-existing gap.",
                          kpis=[f"{noun} for {top}", f"Gap between {top} and {bot}", "Pilot vs control difference"],
                          risks=["The gap may reflect other differences between the groups, not the group itself (association, not cause).", f"Focusing on {top} can neglect other groups; keep watching them."]))
            else:
                add(_cand(**base, id="", title=f"Put more behind what works: {group} = {top}", theme="Growth",
                          objective=f"Grow overall {target} by investing where it is strongest ({top}) while diagnosing why {bot} lags.",
                          rationale=f.get("insight", {}).get("headline", ""),
                          actions=[f"Direct more {levers[0]} to {top} and track {target} per {cust[:-1] if cust.endswith('s') else cust}.",
                                   f"Find out why {bot} lags: compare pricing, channel, product mix and service levels against {top}.",
                                   f"Decide whether to lift {bot} or deliberately shift resources toward {top}."],
                          effort="Medium",
                          how_to_test=f"Pilot the extra investment on part of {top} (or a comparable subset) for 4-8 weeks with a hold-out group, and compare {target} between them.",
                          kpis=[f"{target} for {top} and for {bot}", f"Share of total {target} coming from {top}", "Pilot vs hold-out difference"],
                          risks=[f"{top} may already be near its ceiling, so extra spend could show diminishing returns.", "The gap may partly reflect differences in mix rather than the group itself."]))
        elif tool == "outcome_compare":
            target, col, pos, dirn = p["target"], p["col"], st.get("positive", "yes"), st.get("direction", "higher")
            if polarity(target) == "bad":
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
            good = up != (polarity(target) == "bad")
            add(_cand(**base, id="", title=(f"Understand and sustain the rise in {target}" if good else f"Find and fix what is driving {target} the wrong way"), theme="Monitoring",
                      objective=f"Explain the {'upward' if up else 'downward'} movement in {target} and decide whether to {'extend' if good else 'reverse'} it.",
                      rationale=f.get("insight", {}).get("headline", ""),
                      actions=["Find when the movement began and list what changed then (pricing, campaigns, product, staffing, data collection).",
                               "Break the trend down by segment to see where it comes from.",
                               f"Decide whether to {'invest further in what is working' if good else 'intervene'} and agree how you will measure it."],
                      effort="Low", how_to_test="Compare periods before and after the suspected cause, and check the same calendar period in previous years to rule out seasonality.",
                      kpis=[f"{target} by month", "Segment contribution to the change"], risks=["The first and last periods may be partial.", "Seasonality can mimic a trend."]))
        elif tool == "segment_trend":
            target, group, fast, slow = p["target"], p["group"], st["fastest"], st["slowest"]
            add(_cand(**base, id="", title=f"Rebalance attention across {group}: {fast} rising, {slow} falling", theme="Allocation",
                      objective=f"Respond to diverging trends in {target} between {fast} and {slow}.",
                      rationale=f.get("insight", {}).get("headline", ""),
                      actions=[f"Identify what changed for {fast} (campaign, product, pricing) and whether it can be repeated.",
                               f"Diagnose why {slow} is slipping before more budget flows there.",
                               f"Review how {levers[0]} are allocated across the {group} groups."],
                      effort="Medium", how_to_test=f"Hold {slow}'s current plan for one group of accounts and change it for another; compare the next 8 weeks.",
                      kpis=[f"{target} by {group} by month", f"Share of {target} from {fast}"], risks=["Per-segment trends rest on fewer data points and are less certain than the overall trend."]))
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
            add(_cand(**base, id="", title=f"Plan around the {st.get('cycle', 'weekday')} pattern in {target}", theme="Timing",
                      objective=f"Match staffing, stock and promotions to when {target} is strong ({hi}) and weak ({lo}).",
                      rationale=f.get("insight", {}).get("headline", ""),
                      actions=[f"Schedule capacity and inventory for {hi}.", f"Test a promotion or offer in the weak period ({lo}) to lift it.", "Review the pattern each quarter in case customer habits shift."],
                      effort="Low", how_to_test=f"Move one promotion from {hi} to {lo} for a few weeks and compare {target} against the usual pattern.",
                      kpis=[f"{target} by {'day' if st.get('cycle') == 'weekday' else 'month'}", "Capacity utilisation"], risks=["The pattern may come from one-off events in the period covered."]))
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
        foundations.append(_cand(id="", kind="foundation", title="Close the most important data gaps", theme="Data foundation",
                                 objective="Make the next analysis answer more of your questions with more certainty.", rationale="Gaps found while checking the data against your goal and KPIs.",
                                 actions=[g["suggestion"] for g in important], effort="Low", impact="Medium", confidence="", evidence=[],
                                 how_to_test="Re-run this analysis once the data is added and compare what changes.", kpis=["Share of your KPIs measurable from the data"], risks=["Collecting data has a cost; add only what changes a decision."]))
    foundations.append(_cand(id="", kind="foundation", title="Make this repeatable: track the main KPI in Power BI", theme="Data foundation",
                             objective="Turn a one-off analysis into a regular review so changes are seen early.", rationale="Insights lose value if nobody looks at them again.",
                             actions=["Load the Power BI pack, paste the DAX measures and build the suggested visuals.", "Agree an owner and a monthly review of the headline KPI and the top segments.", "Re-run this analysis on fresh data each quarter."],
                             effort="Low", impact="Medium", confidence="", evidence=[], how_to_test="Check after two review cycles whether decisions actually changed.",
                             kpis=["Review held on schedule", "Time from change to detection"], risks=["Dashboards that nobody owns go stale."]))
    return data_backed + foundations


def prioritise(cands: list[dict]) -> list[dict]:
    for c in cands:
        c["horizon"] = HORIZON[c["effort"]]
        c["priority_score"] = 2 * IMPACT_PTS[c["impact"]] + CONF_PTS.get(c["confidence"], 0) - EFFORT_PTS[c["effort"]]
    cands.sort(key=lambda c: (c["kind"] != "data", -c["priority_score"], c["title"]))
    for i, c in enumerate(cands, 1):
        c["id"] = f"s{i}"
        c["rank"] = i
    return cands


REFINE_SYSTEM = """You are a senior strategy consultant adapting candidate strategies to a specific company.
Rewrite wording so it fits the company's industry and vocabulary. You may change: title, objective, actions (3-5), risks (2-3), kpis, how_to_test.
You may NOT add strategies, change ids, evidence, impact or effort, or introduce any number that is not already in the candidate or the facts.
Keep each action concrete and specific; do not claim cause and effect.
Return JSON: {"strategies": [{"id": "...", "title": "...", "objective": "...", "actions": ["..."], "risks": ["..."], "kpis": ["..."], "how_to_test": "..."}]}"""


def refine_with_llm(llm, state: dict, strategies: list[dict]) -> int:
    if llm is None:
        return 0
    ctx_txt = ctxmod.context_text(state.get("context"))
    cands = [s for s in strategies if s["kind"] == "data"]
    if not cands:
        return 0
    payload = [{"id": s["id"], "title": s["title"], "objective": s["objective"], "actions": s["actions"], "risks": s["risks"], "kpis": s["kpis"], "how_to_test": s["how_to_test"],
                "facts": [f["text"].replace("**", "") for f in state["findings"] if f["id"] in s["evidence"]]} for s in cands]
    out = ask_json(llm, REFINE_SYSTEM, (f"COMPANY CONTEXT (user-written, unverified):\n{ctx_txt}\n\n" if ctx_txt else "") + f"GOAL: {state['goal']}\nCANDIDATES:\n{json.dumps(payload, default=str)}")
    if not out or not isinstance(out.get("strategies"), list):
        return 0
    by_id = {s["id"]: s for s in cands}
    improved = 0
    for item in out["strategies"]:
        s = by_id.get(item.get("id")) if isinstance(item, dict) else None
        if not s:
            continue
        facts = [f for f in state["findings"] if f["id"] in s["evidence"]]
        allowed = allowed_numbers(json.dumps(payload, default=str), ctx_txt, *[json.dumps(f["stats"], default=str) for f in facts])
        changed = False

        def ok_text(v, lo=8, hi=260):
            return isinstance(v, str) and lo <= len(v.strip()) <= hi and numbers_ok(v, allowed)

        for key, hi in (("title", 140), ("objective", 300), ("how_to_test", 420)):
            if ok_text(item.get(key), 8, hi):
                s[key], changed = item[key].strip(), True
        for key, mx in (("actions", 5), ("risks", 3), ("kpis", 4)):
            lst = [x.strip() for x in (item.get(key) or []) if ok_text(x)][:mx]
            if len(lst) >= (2 if key != "kpis" else 1):
                s[key], changed = lst, True
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
    strategies = prioritise(candidates(state))
    improved = refine_with_llm(llm, state, strategies)
    return strategies, summary_text(strategies, state), improved
