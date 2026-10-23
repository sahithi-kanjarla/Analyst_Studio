"""Planner agent: understand the purpose of the analysis and decide what is (and isn't) needed.

LLM path: the model reads the goal + optional business context + a compact profile and proposes a plan as JSON.
Everything the model proposes is validated against the real columns; invalid items are dropped.
Rules path: a data-driven heuristic planner so the system works with no LLM at all.

Business context (optional) steers the choice of outcome column: columns that match the KPIs the user named, or the
industry's typical outcomes, are preferred. In explore mode (no goal given) the plan is broader."""
from __future__ import annotations

import json
import re

import pandas as pd

from . import context as ctxmod
from . import directives as dv
from . import goal as goalmod
from .llm import ask_json
from .profiler import compact_profile
from .measures import has_sum_word
from .tools import CATALOG, assoc_strength, spec_signature, validate_spec, target_values  # noqa: F401

PRIOR_TARGET = re.compile(r"revenue|sales|profit|income|price|amount|churn|conversion|score|rating|salary|spend|cost|margin|target|label|outcome|default|fraud|survived|satisfaction", re.I)
MAX_ANALYSES = 14
MAX_ANALYSES_EXPLORE = 18
MAX_EXTRA_MEASURES = 3      # measures besides the target that get their own charts: the derived ratio first, then the others (all of them are in the summary table)
MIN_MEASURE_UNIQUE = 10     # fewer distinct numbers than this is a code or a scale, not a measure


def _name_tokens(name: str) -> set[str]:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
    return set(re.findall(r"[a-z0-9]+", spaced.lower()))


def roles_and_uniques(profile: dict) -> tuple[dict, dict]:
    return ({c["name"]: c["role"] for c in profile["columns"]}, {c["name"]: c["unique"] for c in profile["columns"]})


def _apply_exclusions(roles: dict, ov: dict) -> dict:
    """Columns the user chose to ignore (or folded into a net amount) are not offered to the planner, unless they are the confirmed target/date."""
    keep = {ov.get("target"), ov.get("date_col")}
    return {c: r for c, r in roles.items() if c not in set(ov.get("exclude") or []) or c in keep}


SKIP_REASON = {"id": "identifier: it labels rows and carries no business meaning", "text": "free text: not analysed as a measure or a segment", "empty": "no values to analyse"}


def rule_skipped(profile: dict, relevant: list[str], ov: dict) -> list[dict]:
    """Columns the rules planner leaves out and why, most notable first (identifiers, free text, empty or user-excluded columns), at most 10."""
    used, excluded = set(relevant), set(ov.get("exclude") or [])
    out = []
    for c in profile["columns"]:
        n, r = c["name"], c["role"]
        if n in used:
            continue
        if n in excluded:
            out.append((0, {"column": n, "reason": "left out because of your answer to a clarifying question"}))
        elif r in SKIP_REASON:
            out.append((1, {"column": n, "reason": SKIP_REASON[r]}))
        elif c.get("unique", 2) <= 1:
            out.append((1, {"column": n, "reason": "the same value on every row"}))
        else:
            out.append((2, {"column": n, "reason": "not needed to answer this goal"}))
    return [x for _, x in sorted(out, key=lambda t: t[0])][:10]


def _include_extras(roles: dict, date: str | None, ov: dict, target: str | None = None) -> list[dict]:
    """Columns the user wants analysed separately (e.g. refunds) get their own distribution and trend."""
    out = []
    for c in ov.get("include") or []:
        if roles.get(c) == "numeric" and c != target:
            out.append({"tool": "distribution", "params": {"col": c}, "why": f"You asked for {c} to be analysed separately."})
            if date:
                out.append({"tool": "trend", "params": {"target": c, "date": date}, "why": f"See how {c} changes over time (analysed separately, as you asked)."})
    return out


def plausible_dimensions(df: pd.DataFrame, roles: dict, uniques: dict, target: str | None, keep: list[str]) -> list[str]:
    """Low-cardinality categorical columns (category, region, segment...) are plausible drivers of any KPI the goal names, so the scope step keeps them
    even when they are not among the few the planner chose to chart: dropping one hides a split the user may well ask about next."""
    if not target:
        return []
    return [c for c, r in roles.items() if r == "categorical" and 2 <= uniques.get(c, 0) <= 30 and c in df.columns and c not in keep]


def measure_extras(df: pd.DataFrame, roles: dict, uniques: dict, measures: list[str], skip: set, date: str | None, groups: list[str]) -> list[dict]:
    """Every candidate measure other than the target is analysed too: its shape, its trend, its average per record by segment, and its totals by segment.
    This replaces asking the user to pick one."""
    out = []
    rest = [c for c in measures if c not in skip]
    if rest:     # one overview of every candidate measure; the first few also get their own charts
        out.append({"tool": "summary_table", "params": {"cols": measures[:12]}, "why": "Every candidate measure is analysed too, so nothing was left for you to pick: here is each one at a glance."})
    for m in rest[:MAX_EXTRA_MEASURES]:
        if date:
            out.append({"tool": "trend", "params": {"target": m, "date": date}, "why": f"{m} is also a candidate measure. See how it changes over time (totals or averages, whichever suits it)."})
        for g in groups[:1]:
            out.append({"tool": "group_compare", "params": {"target": m, "group": g}, "why": f"Compare the average {m} per record across {g}."})
            out.append({"tool": "group_table", "params": {"target": m, "group": g}, "why": f"Give the exact totals and averages of {m} by {g}."})
    return out


def candidate_measures(df: pd.DataFrame, roles: dict, uniques: dict, ov: dict) -> list[str]:
    derived = [d["name"] for d in ov.get("derived") or [] if d["name"] in roles]
    rest = [c for c, r in roles.items() if r == "numeric" and uniques[c] >= MIN_MEASURE_UNIQUE and c in df.columns and c not in derived]
    return derived + rest


def heuristic_plan(df: pd.DataFrame, profile: dict, goal: str, context: dict | None = None, explore: bool = False, overrides: dict | None = None) -> dict:
    context = context or {}
    ov = overrides or {}
    roles, uniques = roles_and_uniques(profile)
    roles = _apply_exclusions(roles, ov)
    # In explore mode the "goal" is a placeholder sentence, so it must not influence column choice.
    goal_tokens = set() if explore else set(re.findall(r"[a-z0-9]+", goal.lower()))
    kpi_tokens = ctxmod.kpi_tokens(context)
    desc_tokens = ctxmod.description_tokens(context)
    hint = ctxmod.lens(context)["target_rx"] if ctxmod.has_context(context) else None
    miss = {c["name"]: c["missing_pct"] for c in profile["columns"]}

    cands = [c for c, r in roles.items() if r in ("numeric", "binary")]

    def evidence(c: str) -> int:  # does the goal, a named KPI, the industry or the column name point at this being the outcome?
        nt = _name_tokens(c)
        return (3 * len(nt & goal_tokens) + 5 * len(nt & kpi_tokens) + len(nt & desc_tokens)
                + (4 if PRIOR_TARGET.search(c) else 0) + (3 if hint and hint.search(c) else 0))

    scored = sorted(cands, key=lambda c: -(evidence(c) - miss[c] / 100))
    target = scored[0] if scored else None
    if target and evidence(target) == 0:
        # No evidence of an outcome: never guess a binary column; fall back to a numeric one or purely descriptive analysis.
        nums_only = [c for c in scored if roles[c] == "numeric"]
        target = nums_only[0] if nums_only else None
    if ov.get("target") in roles and roles[ov["target"]] in ("numeric", "binary"):
        target = ov["target"]  # confirmed by the user
    second = next((c for c in scored if c != target and evidence(c) > 0), None) if (explore and target) else None
    date = ov["date_col"] if ov.get("date_col") in roles and roles[ov["date_col"]] == "date" else next((c for c, r in roles.items() if r == "date"), None)

    def rank_groups(t: str | None, limit: int) -> list[str]:
        gs = [c for c, r in roles.items() if r in ("categorical", "binary") and c != t and 2 <= uniques[c] <= 30 and c in df.columns]
        if not t:
            return gs[:3]
        boost = goal_tokens | kpi_tokens
        return sorted(gs, key=lambda g: -(assoc_strength(df, t, g) + (0.1 if _name_tokens(g) & boost else 0)))[:limit]

    groups = rank_groups(target, 4)

    nums = [c for c, r in roles.items() if r == "numeric" and c != target]
    drivers = nums[:5]
    if target and nums:
        y = target_values(df[target])[0]
        drivers = sorted(nums, key=lambda c: -abs(pd.to_numeric(df[c], errors="coerce").corr(y, method="spearman") or 0))[:5]

    analyses: list[dict] = []
    if target:
        why_t = "the outcome the goal focuses on" if not explore else "a likely key outcome for the business"
        if roles[target] == "numeric":
            analyses.append({"tool": "distribution", "params": {"col": target}, "why": f"Understand the shape of {target}, {why_t}."})
        if date:
            analyses.append({"tool": "trend", "params": {"target": target, "date": date}, "why": f"See how {target} changes over time."})
        for g in groups:
            analyses.append({"tool": "group_compare", "params": {"target": target, "group": g}, "why": f"Find out whether {target} differs across {g} (chosen because it is among the columns most associated with {target})."})
        if len(groups) >= 2:
            analyses.append({"tool": "heatmap", "params": {"target": target, "group_a": groups[0], "group_b": groups[1]},
                             "why": f"See whether combinations of {groups[0]} and {groups[1]} behave differently from either alone."})
        if date and groups:
            analyses.append({"tool": "segment_trend", "params": {"target": target, "date": date, "group": groups[0]},
                             "why": f"Check whether {target} is moving differently over time for different {groups[0]} values."})
        if roles[target] == "numeric" and has_sum_word(target):
            for g in groups[:2]:
                analyses.append({"tool": "pareto", "params": {"target": target, "group": g}, "why": f"See which {g} values contribute most of the total {target}."})
        if roles[target] == "binary":
            for c in drivers[:3]:
                analyses.append({"tool": "outcome_compare", "params": {"target": target, "col": c}, "why": f"Check whether {c} differs between rows with and without the {target} outcome."})
        if len(drivers) >= 1 and roles[target] == "numeric":
            analyses.append({"tool": "correlation", "params": {"cols": [target] + drivers}, "why": f"Check which numeric columns move together with {target}."})
        if roles[target] == "numeric":
            analyses.append({"tool": "outliers", "params": {"col": target}, "why": f"Make sure extreme values of {target} do not distort conclusions."})
        if roles[target] == "numeric":
            for c in drivers[:2]:
                analyses.append({"tool": "scatter", "params": {"x": c, "y": target}, "why": f"See the shape of the relationship between {c} and {target} (a correlation can hide curves and clusters)."})
        if date:
            analyses.append({"tool": "seasonality", "params": {"target": target, "date": date}, "why": f"Check whether {target} follows a weekday or seasonal pattern."})
        if groups:
            analyses.append({"tool": "group_table", "params": {"target": target, "group": groups[0]}, "why": f"Give the exact per-group figures for {target} by {groups[0]}."})
        if len(groups) >= 3:
            analyses.append({"tool": "composition", "params": {"group": groups[0], "by": groups[2]}, "why": f"See whether the mix of {groups[2]} changes across {groups[0]}."})
        elif len(groups) == 2 and explore:
            analyses.append({"tool": "composition", "params": {"group": groups[0], "by": groups[1]}, "why": f"See whether the mix of {groups[1]} changes across {groups[0]}."})
        if explore or not groups:
            analyses.insert(0, {"tool": "summary_table", "params": {"cols": [c for c in ([target] + drivers) if roles.get(c) == "numeric"]}, "why": "Give a quick factual overview of the numeric columns."})
        if second:  # exploratory: a second candidate outcome gets a lighter pass
            for g in rank_groups(second, 2):
                analyses.append({"tool": "group_compare", "params": {"target": second, "group": g}, "why": f"{second} also looks like an important outcome; check whether it differs across {g}."})
            if date:
                analyses.append({"tool": "trend", "params": {"target": second, "date": date}, "why": f"See how {second} changes over time."})
    else:
        for g in groups:
            analyses.append({"tool": "category_share", "params": {"col": g}, "why": f"Describe the composition of {g}."})
        for c in nums[:3]:
            analyses.append({"tool": "distribution", "params": {"col": c}, "why": f"Describe {c}."})
        if len(nums) >= 2:
            analyses.append({"tool": "scatter", "params": {"x": nums[0], "y": nums[1]}, "why": f"See whether {nums[0]} and {nums[1]} are related."})
        if len(groups) >= 2:
            analyses.append({"tool": "composition", "params": {"group": groups[0], "by": groups[1]}, "why": f"See whether the mix of {groups[1]} changes across {groups[0]}."})
        if nums:
            analyses.insert(0, {"tool": "summary_table", "params": {"cols": nums[:12]}, "why": "No clear outcome column was found, so start with a factual overview."})
    if target and goalmod.asks_about_change(goal, explore):     # the goal asks about change: the overall trend and the per-group trends are never cut
        first = [a for a in analyses if a["tool"] in ("trend", "segment_trend") and a["params"].get("target") == target]
        analyses = first + [a for a in analyses if a not in first]
    analyses = analyses[:(MAX_ANALYSES_EXPLORE if explore else MAX_ANALYSES)] + _include_extras(roles, date, ov, target)
    extras = measure_extras(df, roles, uniques, candidate_measures(df, roles, uniques, ov), {target, second}, date, groups) if target else []
    have = {spec_signature(a) for a in analyses}
    analyses += [a for a in extras if spec_signature(a) not in have]

    relevant = list(dict.fromkeys(([target] if target else []) + ([second] if second else []) + groups + ([date] if date else []) + drivers + [c for c in ov.get("include") or [] if c in roles]
                                  + [c for a in extras for v in a["params"].values() for c in (v if isinstance(v, list) else [v]) if c in roles]))
    relevant += plausible_dimensions(df, roles, uniques, target, relevant)
    questions = [f"What explains differences or changes in {target}?" if target else "What does this dataset contain?"]
    if target and groups:
        questions.append(f"Which segments ({', '.join(groups[:3])}) perform best and worst on {target}?")
    if date and target:
        questions.append(f"Is {target} trending up or down over time?")
    if context.get("kpis") and target and (_name_tokens(target) & kpi_tokens):
        questions.append(f"How is {target}, one of the KPIs you named, behaving and what is it linked to?")
    objective = goal if not explore else "Explore the data and surface the most important patterns for the business (no specific goal was given)."
    return {"objective": objective, "questions": questions, "target": target, "date_col": date, "relevant_columns": relevant,
            "analyses": analyses, "skipped": rule_skipped(profile, relevant, ov), "source": "rules", "explore": bool(explore), "derived": list(ov.get("derived") or [])}


PLAN_SYSTEM = f"""You are the Planner of a data-analysis team. A user gives a GOAL, optional BUSINESS CONTEXT and a dataset profile.
Decide what is needed to serve the goal and NOTHING more: pick the outcome column (target), the relevant columns, and the analyses to run.
Use the business context (industry, what they sell, the KPIs they named) to choose the outcome that matters to THEM.
Available analysis tools (use exact names and parameter names):
{json.dumps(CATALOG, indent=1)}

Rules:
- Use only column names that appear in the profile. Match column role to tool requirements.
- Choose at most 10 analyses, each with a short "why" tied to the goal or the business context.
- If the goal says exploratory mode: choose the 1-2 most business-relevant outcome columns and a broad, varied set of analyses.
- Skip identifier and free-text columns. List notable skipped columns with a reason.
Return JSON with keys: objective (string), questions (list of 2-4 strings), target (column or null), date_col (column or null),
relevant_columns (list), analyses (list of {{tool, params, why}}), skipped (list of {{column, reason}})."""


def llm_plan(llm, profile: dict, goal: str, context: dict | None = None, explore: bool = False, overrides: dict | None = None) -> dict | None:
    ctx_txt = ctxmod.context_text(context)
    ov = overrides or {}
    confirmed = "".join(f"- {k}: {v}\n" for k, v in (("target (main number)", ov.get("target")), ("date_col", ov.get("date_col"))) if v)
    confirmed += "".join(f"- do not use column: {c}\n" for c in ov.get("exclude") or [])
    mode = "EXPLORATORY MODE: the user does not know what to look for.\n" if explore else ""
    user = (f"{mode}GOAL: {goal}\n\n" + (f"CONFIRMED BY THE USER (use exactly):\n{confirmed}\n" if confirmed else "") + (f"BUSINESS CONTEXT (user-written, unverified):\n{ctx_txt}\n\n" if ctx_txt else "")
            + f"PROFILE ({profile['rows']} rows):\n{json.dumps(compact_profile(profile), default=str)}")
    return ask_json(llm, PLAN_SYSTEM, user)


def finalize_plan(raw: dict | None, df: pd.DataFrame, profile: dict, goal: str, context: dict | None = None, explore: bool = False,
                  overrides: dict | None = None) -> tuple[dict, list[str]]:
    """Validate an LLM plan against reality, then top up from the rules planner if it is too thin.
    `overrides` are the user's confirmed answers: they always win over what the model proposed."""
    notes: list[str] = []
    ov = overrides or {}
    roles, uniques = roles_and_uniques(profile)
    roles = _apply_exclusions(roles, ov)
    fallback = heuristic_plan(df, profile, goal, context, explore, ov)
    if not raw:
        return fallback, notes
    analyses, seen = [], set()
    for spec in (raw.get("analyses") or [])[:MAX_ANALYSES_EXPLORE]:
        ok, why = validate_spec(spec if isinstance(spec, dict) else {}, roles, uniques)
        if ok is None:
            notes.append(f"Dropped a proposed step: {why}")
            continue
        sig = spec_signature(ok)
        if sig not in seen:
            seen.add(sig)
            analyses.append(ok)
    if len(analyses) < 2:
        notes.append("LLM plan was too thin; filled in with rule-based steps.")
        for spec in fallback["analyses"]:
            sig = spec_signature(spec)
            if sig not in seen:
                seen.add(sig)
                analyses.append(spec)
    target = raw.get("target") if raw.get("target") in roles and roles[raw["target"]] in ("numeric", "binary") else fallback["target"]
    date = raw.get("date_col") if raw.get("date_col") in roles and roles[raw["date_col"]] == "date" else fallback["date_col"]
    if ov.get("target") and target != fallback["target"]:
        notes.append(f"The main number you chose ({fallback['target']}) replaced the model's choice ({target}); analyses were rebuilt around it.")
        target, analyses = fallback["target"], list(fallback["analyses"])
    if ov.get("date_col") and date != fallback["date_col"]:
        notes.append(f"Time analysis uses {fallback['date_col']}, the date column you chose.")
        date = fallback["date_col"]
        analyses = [{**a, "params": {**a["params"], "date": date}} if "date" in a["params"] else a for a in analyses]
    if target and goalmod.asks_about_change(goal, explore):
        have = {spec_signature(a) for a in analyses}
        need = [a for a in fallback["analyses"] if a["tool"] in ("trend", "segment_trend") and a["params"].get("target") == target and spec_signature(a) not in have]
        analyses = need + analyses
    seen_sigs = {spec_signature(a) for a in analyses}
    groups = [a["params"]["group"] for a in fallback["analyses"] if a["tool"] == "group_compare" and a["params"].get("target") == fallback["target"]]
    more = _include_extras(roles, date, ov, target) + (measure_extras(df, roles, uniques, candidate_measures(df, roles, uniques, ov), {target}, date, groups) if target else [])
    analyses += [a for a in more if spec_signature(a) not in seen_sigs]
    used ={c for a in analyses for v in a["params"].values() for c in (v if isinstance(v, list) else [v])}
    relevant = list(dict.fromkeys([c for c in (raw.get("relevant_columns") or []) if c in roles] + [c for c in used if c in roles] + ([target] if target else []) + ([date] if date else [])
                                  + [c for c in ov.get("include") or [] if c in roles]))
    relevant += plausible_dimensions(df, roles, uniques, target, relevant)
    questions = [str(q) for q in (raw.get("questions") or [])][:4] or fallback["questions"]
    skipped = [s for s in (raw.get("skipped") or []) if isinstance(s, dict) and s.get("column") in roles][:10]
    objective = fallback["objective"] if explore else str(raw.get("objective") or goal)
    return {"objective": objective, "questions": questions, "target": target, "date_col": date,
            "relevant_columns": relevant, "analyses": analyses, "skipped": skipped, "source": "llm", "explore": bool(explore), "derived": list(ov.get("derived") or [])}, notes
