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
from . import derived as derivedmod
from . import directives as dv
from . import goal as goalmod
from . import polarity as polmod
from .clarify import named_in_goal
from .profiler import compact_profile
from .measures import has_sum_word, measure_kind
from .propose import AI_SOURCES, propose
from .signals import flag_values
from .tools import CATALOG, MONTH_CYCLE_MIN_DAYS, additive, assoc_strength, spec_signature, validate_spec, target_values  # noqa: F401

PRIOR_TARGET = re.compile(r"revenue|sales|profit|income|price|amount|churn|conversion|score|rating|salary|spend|cost|margin|target|label|outcome|default|fraud|survived|satisfaction", re.I)
MAX_ANALYSES = 14
MAX_ANALYSES_EXPLORE = 18
MAX_EXTRA_MEASURES = 3      # measures besides the target that get their own charts: the derived ratio first, then the others (all of them are in the summary table)
MIN_YEAR_MONTHS = 12        # dates covering this many calendar months always get a month-of-year pattern for the main total and each ratio KPI
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
        for i, g in enumerate(groups):
            out.append({"tool": "group_compare", "params": {"target": m, "group": g}, "why": f"Compare the average {m} per record across {g}."})
            if i == 0:     # the exact table is kept for the first group; the comparison itself covers every planned group
                out.append({"tool": "group_table", "params": {"target": m, "group": g}, "why": f"Give the exact totals and averages of {m} by {g}."})
    return out


ID_LIKE_SHARE = 0.05      # an integer column with at least this many distinct values per row, and no declared kind, is a code
ID_LIKE_MIN_UNIQUE = 50
ID_LIKE_FILL = 0.5      # share of the min..max range the distinct values occupy; below it the integers are labels, not quantities


def id_like_columns(df: pd.DataFrame, roles: dict, uniques: dict, ov: dict) -> list[str]:
    """Numeric columns that label rows rather than measure anything: the brief called them an id or a code, the rules saw an id, or they are integers with
    many distinct values whose kind (additive or rate) nobody declared, so a total or an average of them means nothing (postal codes, phone numbers)."""
    brief = ov.get("brief_roles") or {}
    kinds = ov.get("kinds") or {}
    out = []
    for c, r in roles.items():
        if r not in ("numeric", "id") or c not in df.columns or c in {d["name"] for d in ov.get("derived") or []}:
            continue
        if brief.get(c) == "id" or r == "id":
            out.append(c)
        elif r == "numeric" and measure_kind(c, None, kinds) == "other" and uniques.get(c, 0) >= max(ID_LIKE_MIN_UNIQUE, ID_LIKE_SHARE * len(df)) and _integer_valued(df[c]):
            out.append(c)
    return out


def _integer_valued(s: pd.Series) -> bool:
    nn = pd.to_numeric(s, errors="coerce").dropna()
    return bool(len(nn)) and bool((nn % 1 == 0).all()) and nn.nunique() < ID_LIKE_FILL * (nn.max() - nn.min() + 1)      # a code is sparse in its range; tenure or age fills it


def candidate_measures(df: pd.DataFrame, roles: dict, uniques: dict, ov: dict) -> list[str]:
    derived = [d["name"] for d in ov.get("derived") or [] if d["name"] in roles]
    ids = set(id_like_columns(df, roles, uniques, ov))
    rest = [c for c, r in roles.items() if r == "numeric" and uniques[c] >= MIN_MEASURE_UNIQUE and c in df.columns and c not in derived and c not in ids]
    return derived + rest


def _pairs(ov: dict) -> list[tuple[str, str]]:
    return [tuple(p) for p in ov.get("hierarchy") or []]


def parent_first(groups: list[str], pairs: list[tuple[str, str]], eligible) -> list[str]:
    """For each verified (parent, child) pair whose child is a chosen group, the parent is analysed too and comes straight before it."""
    out = list(groups)
    for p, c in pairs:
        if c in out and eligible(p):
            if p in out:
                out.remove(p)
            out.insert(out.index(c), p)
    return out


def _related(a: str, b: str, pairs: list[tuple[str, str]]) -> bool:
    return (a, b) in pairs or (b, a) in pairs


def plan_cap(explore: bool) -> int:
    return MAX_ANALYSES_EXPLORE if explore else MAX_ANALYSES


def _span_days(s: pd.Series) -> int:
    t = pd.to_datetime(s, errors="coerce").dropna()
    return int((t.max() - t.min()).days) if len(t) else 0


def _span_months(s: pd.Series) -> int:
    t = pd.to_datetime(s, errors="coerce").dropna()
    return int((t.max().year - t.min().year) * 12 + t.max().month - t.min().month + 1) if len(t) else 0


def _code(spec: dict, reason: str) -> dict:
    return {**spec, "why": reason, "origin": "code"}


def kpi_measures(df: pd.DataFrame, roles: dict, uniques: dict, ov: dict, target: str | None) -> tuple[list[str], list[dict]]:
    """(additive measures, ratio-of-sums KPI specs): the target first, then the KPIs the user named, then a few other additive candidates."""
    derived = ov.get("derived") or []
    kpis = [c for c in (ov.get("kpi_map") or {}).values() if c]
    others = [c for c in candidate_measures(df, roles, uniques, ov) if c != target and c not in kpis][:MAX_EXTRA_MEASURES]
    names = {d["name"] for d in derived}
    adds = [m for m in dict.fromkeys(([target] if target else []) + kpis + others) if roles.get(m) == "numeric" and m not in names and additive(m, {"kinds": ov.get("kinds")})]
    return adds, [d for d in derived if d.get("kind") == "ratio_of_sums" and d["name"] in roles and d["name"] in df.columns]


def top_pairs(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """The (parent, child) pairs whose parent is not itself the child of another pair: the top level of each hierarchy."""
    children = {c for _, c in pairs}
    return [(p, c) for p, c in pairs if p not in children]


def drill_parents(df: pd.DataFrame, parent: str, main: str | None, ratios: list[dict]) -> list[tuple[str, str]]:
    """(parent value, why) computed from the data: the parent holding the largest share of the main additive KPI, and the parent with the worst ratio KPI
    (the lowest ratio, or the highest when lower is better for it)."""
    key = df[parent].astype(str).where(df[parent].notna())
    out: list[tuple[str, str]] = []
    if main and main in df.columns:
        sums = pd.to_numeric(df[main], errors="coerce").groupby(key).sum()
        if len(sums) and sums.max() > 0:
            out.append((str(sums.idxmax()), f"{parent} = {sums.idxmax()} holds the largest share of {main}"))
    for spec in ratios:
        try:
            r = derivedmod.group_ratio(df, spec, key)
        except Exception:
            continue
        if len(r) >= 2:
            worst = r.idxmax() if polmod.direction(spec["name"]) == polmod.LOWER else r.idxmin()
            if str(worst) not in {v for v, _ in out}:
                out.append((str(worst), f"{parent} = {worst} has the worst {spec['name']}"))
    return out


def flag_rate_specs(df: pd.DataFrame, roles: dict, goal: str, explore: bool, target: str | None) -> list[tuple[dict, str]]:
    """(spec, reason) for the share of rows flagged per group: every yes/no column (by its values) against every category column the goal names."""
    if explore:
        return []
    flags = [c for c, r in roles.items() if r == "binary" and c in df.columns and c != target and flag_values(df[c])]
    groups = [c for c, r in roles.items() if r == "categorical" and c in df.columns and c not in flags and named_in_goal(c, goal)][:3]
    out = []
    for f in flags:
        for g in groups:
            why = f"The goal names {g}, and {f} is a yes/no flag, so the share of rows flagged in each {g} (with the overall rate) is always shown (added by code: required coverage)."
            out += [({"tool": "group_compare", "params": {"target": f, "group": g}}, why), ({"tool": "group_table", "params": {"target": f, "group": g}}, why)]
    return out


def ensure_required(df: pd.DataFrame, roles: dict, uniques: dict, goal: str, explore: bool, ov: dict, target: str | None, date: str | None, analyses: list[dict]) -> list[dict]:
    """The items every plan must have, whoever drew it up. Code adds what is missing (origin "code", with the reason) and never removes anything:
    a month-of-year seasonality for the main total and each ratio KPI when the dates cover 12 months (and for the other additive measures when the dates span two years); the parent level of a verified hierarchy right before its child, and for every hierarchy the top level for each KPI plus
    a drill-down into its child level inside the parent with the largest share and the parent with the worst ratio; the measures the user named as KPIs."""
    out = list(analyses)

    def has(spec: dict) -> dict | None:
        return next((a for a in out if spec_signature(a) == spec_signature(spec)), None)

    def add(spec: dict, reason: str, before: dict | None = None) -> None:
        ok, _ = validate_spec(spec, roles, uniques)
        if ok is None or has(ok):
            return
        out.insert(out.index(before), _code(ok, reason)) if before is not None else out.append(_code(ok, reason))

    for p, c in _pairs(ov):
        for a in [a for a in out if a["params"].get("group") == c and a["params"].get("target")]:
            mate = {"tool": "group_compare", "params": {"target": a["params"]["target"], "group": p}}
            found = has(mate)
            if found is None:
                add(mate, f"{p} is the broader level above {c}, so it is analysed before {c}.", a)
            elif out.index(found) > out.index(a):
                out.remove(found)
                out.insert(out.index(a), found)
    groups = [a["params"]["group"] for a in out if a["tool"] == "group_compare"]
    for kpi, col in (ov.get("kpi_map") or {}).items():
        if not col or col == target or roles.get(col) != "numeric":
            continue
        if any(col in (a["params"].get(k) for k in ("target", "col", "x", "y")) for a in out):
            continue
        why = f"You named {kpi} as a KPI, and it is the column {col}, so it is always analysed."
        if date:
            add({"tool": "trend", "params": {"target": col, "date": date}}, why)
        if groups:
            add({"tool": "group_compare", "params": {"target": col, "group": groups[0]}}, why)
        if not date and not groups:
            add({"tool": "distribution", "params": {"col": col}}, why)
    adds, ratios = kpi_measures(df, roles, uniques, ov, target)
    ratio_kpis = [d["name"] for d in ratios]
    for p, c in top_pairs(_pairs(ov)):      # the broad level of every hierarchy is analysed for each KPI, then the fine level inside the two parents that matter most
        for m in adds + ratio_kpis:
            add({"tool": "group_compare", "params": {"target": m, "group": p}}, f"{m} by {p}, the top level of the {p} > {c} hierarchy (added by code: required coverage).")
        if p in df.columns and c in df.columns:
            for value, why in drill_parents(df, p, adds[0] if adds else None, ratios):
                if df.loc[df[p].astype(str) == value, c].nunique() < 2:
                    continue
                for m in adds[:1] + ratio_kpis:
                    add({"tool": "group_compare", "params": {"target": m, "group": c, "within": p, "within_value": value}}, f"Drill-down into {c} for {m}: {why} (added by code: required coverage).")
    for spec, why in flag_rate_specs(df, roles, goal, explore, target):
        add(spec, why)
    if not explore and adds:      # the goal names a category column and there is a main total: each group's share of that total is always shown, whichever analyses the plan drew up
        for g in [c for c, r in roles.items() if r == "categorical" and c in df.columns and named_in_goal(c, goal)][:3]:
            add({"tool": "group_table", "params": {"target": adds[0], "group": g}}, f"The goal names {g}, so each {g}'s share of {adds[0]} is always shown (added by code: required coverage).")
    if ratio_kpis and not any(a["tool"] == "correlation" for a in out):     # a ratio KPI is always checked against the numeric drivers (discount, price...), whatever the plan drew up
        cols = candidate_measures(df, roles, uniques, ov)
        if len(cols) >= 2:
            add({"tool": "correlation", "params": {"cols": cols}}, f"{ratio_kpis[0]} is a ratio KPI, so how it moves with the numeric drivers is always checked.")
    if date and date in df.columns:
        if _span_months(df[date]) >= MIN_YEAR_MONTHS:      # a year or more of dates: the month-of-year pattern of the main total and of each ratio KPI (ratio of sums per month) is always checked
            for m in adds[:1] + ratio_kpis:
                add({"tool": "seasonality", "params": {"target": m, "date": date}},
                    f"The dates cover {MIN_YEAR_MONTHS} months or more, so the month-of-year pattern of {m} ({'a ratio of sums' if m in ratio_kpis else 'a total'}) is always checked (added by code: required coverage).")
        if _span_days(df[date]) >= MONTH_CYCLE_MIN_DAYS:       # the other additive measures too, whatever the intent a model gave the brief: a seasonal total must not depend on that label
            for m in adds[1:]:
                add({"tool": "seasonality", "params": {"target": m, "date": date}},
                    f"The dates span two years or more, so the month-of-year pattern of {m} (a total) is always checked (added by code: required coverage).")
    return out


def heuristic_plan(df: pd.DataFrame, profile: dict, goal: str, context: dict | None = None, explore: bool = False, overrides: dict | None = None) -> dict:
    context = context or {}
    ov = overrides or {}
    roles, uniques = roles_and_uniques(profile)
    roles = _apply_exclusions(roles, ov)
    # In explore mode the "goal" is a placeholder sentence, so it must not influence column choice.
    goal_tokens = set() if explore else set(re.findall(r"[a-z0-9]+", goal.lower()))
    kpi_tokens = ctxmod.kpi_tokens(context, ov.get("kpi_map"))
    kpi_cols = {c for c in (ov.get("kpi_map") or {}).values() if c}
    kinds = ov.get("kinds")
    desc_tokens = ctxmod.description_tokens(context)
    hint = ctxmod.lens(context)["target_rx"] if ctxmod.has_context(context) else None
    miss = {c["name"]: c["missing_pct"] for c in profile["columns"]}

    cands = [c for c, r in roles.items() if r in ("numeric", "binary")]

    def evidence(c: str) -> int:  # does the goal, a named KPI, the industry or the column name point at this being the outcome?
        nt = _name_tokens(c)
        return (3 * len(nt & goal_tokens) + 5 * len(nt & kpi_tokens) + (5 if c in kpi_cols else 0) + len(nt & desc_tokens)
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
        return sorted(gs, key=lambda g: (not (not explore and named_in_goal(g, goal)), -(assoc_strength(df, t, g) + (0.1 if _name_tokens(g) & boost else 0))))[:limit]   # the segment the goal names comes first

    hier = _pairs(ov)
    groups = rank_groups(target, 4)
    if target and hier:      # a verified hierarchy: the broad level is analysed before the fine one
        groups = parent_first(groups, hier, lambda p: roles.get(p) == "categorical" and 2 <= uniques[p] <= 30 and p in df.columns)
    pair_ab = next(((a, b) for i, a in enumerate(groups) for b in groups[i + 1:] if not _related(a, b, hier)), None)   # a parent and its child always line up, so they make a poor pair
    pair_ac = next(((groups[0], b) for b in groups[2:] if not _related(groups[0], b, hier)), None) if groups else None

    nums = [c for c, r in roles.items() if r == "numeric" and c != target and c not in id_like_columns(df, roles, uniques, ov)]
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
        if pair_ab:
            analyses.append({"tool": "heatmap", "params": {"target": target, "group_a": pair_ab[0], "group_b": pair_ab[1]},
                             "why": f"See whether combinations of {pair_ab[0]} and {pair_ab[1]} behave differently from either alone."})
        if date and groups:
            analyses.append({"tool": "segment_trend", "params": {"target": target, "date": date, "group": groups[0]},
                             "why": f"Check whether {target} is moving differently over time for different {groups[0]} values."})
        if roles[target] == "numeric" and has_sum_word(target, overrides=kinds):
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
            if pair_ac:
                analyses.append({"tool": "composition", "params": {"group": pair_ac[0], "by": pair_ac[1]}, "why": f"See whether the mix of {pair_ac[1]} changes across {pair_ac[0]}."})
        elif len(groups) == 2 and explore and pair_ab:
            analyses.append({"tool": "composition", "params": {"group": pair_ab[0], "by": pair_ab[1]}, "why": f"See whether the mix of {pair_ab[1]} changes across {pair_ab[0]}."})
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
        if pair_ab:
            analyses.append({"tool": "composition", "params": {"group": pair_ab[0], "by": pair_ab[1]}, "why": f"See whether the mix of {pair_ab[1]} changes across {pair_ab[0]}."})
        if nums:
            analyses.insert(0, {"tool": "summary_table", "params": {"cols": nums[:12]}, "why": "No clear outcome column was found, so start with a factual overview."})
    if target and goalmod.asks_about_change(goal, explore, ov.get("intent")):     # the goal asks about change: the overall trend and the per-group trends are never cut
        first = [a for a in analyses if a["tool"] in ("trend", "segment_trend") and a["params"].get("target") == target]
        analyses = first + [a for a in analyses if a not in first]
    analyses = analyses[:(MAX_ANALYSES_EXPLORE if explore else MAX_ANALYSES)] + _include_extras(roles, date, ov, target)
    extras = measure_extras(df, roles, uniques, candidate_measures(df, roles, uniques, ov), {target, second}, date, groups) if target else []
    have = {spec_signature(a) for a in analyses}
    analyses += [a for a in extras if spec_signature(a) not in have]
    analyses = [{**a, "origin": "rules"} for a in analyses]
    analyses = ensure_required(df, roles, uniques, goal, explore, ov, target, date, analyses)

    relevant = list(dict.fromkeys(([target] if target else []) + ([second] if second else []) + groups + ([date] if date else []) + drivers + [c for c in ov.get("include") or [] if c in roles]
                                  + [c for a in extras for v in a["params"].values() for c in (v if isinstance(v, list) else [v]) if c in roles]
                                  + [c for s, _ in flag_rate_specs(df, roles, goal, explore, target) for c in s["params"].values()]))
    relevant += plausible_dimensions(df, roles, uniques, target, relevant)
    questions = [f"What explains differences or changes in {target}?" if target else "What does this dataset contain?"]
    if target and groups:
        questions.append(f"Which segments ({', '.join(groups[:3])}) perform best and worst on {target}?")
    if date and target:
        questions.append(f"Is {target} trending up or down over time?")
    if context.get("kpis") and target and (_name_tokens(target) & kpi_tokens or target in kpi_cols):
        questions.append(f"How is {target}, one of the KPIs you named, behaving and what is it linked to?")
    objective = goal if not explore else "Explore the data and surface the most important patterns for the business (no specific goal was given)."
    return {"objective": objective, "questions": questions, "target": target, "date_col": date, "relevant_columns": relevant,
            "analyses": analyses, "skipped": rule_skipped(profile, relevant, ov), "source": "rules", "explore": bool(explore), "derived": list(ov.get("derived") or [])}


def plan_system(cap: int) -> str:
    return f"""You are the Planner of a data-analysis team. A user gives a GOAL, optional BUSINESS CONTEXT and a dataset profile.
Decide what is needed to serve the goal and NOTHING more: pick the outcome column (target), the relevant columns, and the analyses to run, in the order they should run.
Use the business context (industry, what they sell, the KPIs they named) to choose the outcome that matters to THEM.
Available analysis tools (use exact names and parameter names; no other tool exists):
{json.dumps(CATALOG, indent=1)}

Rules:
- Use only column names that appear in the profile. Match column role to tool requirements. Never use an identifier column.
- Choose at most {cap} analyses, each with a short "why" tied to the goal or the business context. More than {cap} is rejected.
- The code adds these itself when you leave them out, so do not spend your {cap} on them: a month-of-year seasonality for the main total and each ratio KPI when the dates cover a year, the broader level of a verified hierarchy for each KPI with a drill-down into its child level, and the measures the user named as KPIs.
- If the goal says exploratory mode: choose the 1-2 most business-relevant outcome columns and a broad, varied set of analyses.
- Skip identifier and free-text columns. List notable skipped columns with a reason.
- Answer with names and words only, never numbers.
Return JSON with keys: objective (string), questions (list of 2-4 strings), target (column or null), date_col (column or null),
relevant_columns (list), analyses (list of {{tool, params, why}}), skipped (list of {{column, reason}})."""


def plan_view(profile: dict, goal: str, context: dict | None = None, explore: bool = False, overrides: dict | None = None) -> str:
    ctx_txt = ctxmod.context_text(context)
    ov = overrides or {}
    confirmed = "".join(f"- {k}: {v}\n" for k, v in (("target (main number)", ov.get("target")), ("date_col", ov.get("date_col"))) if v)
    confirmed += "".join(f"- do not use column: {c}\n" for c in ov.get("exclude") or [])
    mode = "EXPLORATORY MODE: the user does not know what to look for.\n" if explore else ""
    return (f"{mode}GOAL: {goal}\n\n" + (f"CONFIRMED BY THE USER (use exactly):\n{confirmed}\n" if confirmed else "") + (f"BUSINESS CONTEXT (user-written, unverified):\n{ctx_txt}\n\n" if ctx_txt else "")
            + f"PROFILE ({profile['rows']} rows):\n{json.dumps(compact_profile(profile), default=str)}")


def plan_validator(roles: dict, uniques: dict, ids: set, cap: int):
    """The checks on a proposed plan: only tools of the catalog, columns that exist with fitting types, no identifier columns, within the cap. Any problem is an error
    (the model gets one repair); the clean plan has the analyses in the model's order without duplicates."""
    def validate(obj):
        if not isinstance(obj, dict) or not isinstance(obj.get("analyses"), list):
            return None, ["the reply must be a JSON object with an 'analyses' list"]
        specs, errors, good, seen = obj["analyses"], [], [], set()
        if len(specs) > cap:
            errors.append(f"{len(specs)} analyses were proposed but at most {cap} are allowed")
        for i, spec in enumerate(specs, 1):
            spec = spec if isinstance(spec, dict) else {}
            used = [c for v in (spec.get("params") or {}).values() for c in (v if isinstance(v, list) else [v])]
            ok, why = validate_spec(spec, roles, uniques)
            if bad := [c for c in used if isinstance(c, str) and c in ids]:
                errors.append(f"analysis {i} ({spec.get('tool')}): '{bad[0]}' is an identifier column and cannot be analysed")
            elif ok is None:
                errors.append(f"analysis {i}: {why}")
            elif spec_signature(ok) not in seen:
                seen.add(spec_signature(ok))
                good.append({**ok, "origin": "ai"})
        if len(good) < 2 and not errors:
            errors.append("a plan needs at least 2 different analyses")
        for key, role in (("target", ("numeric", "binary")), ("date_col", ("date",))):
            v = obj.get(key)
            if v is not None and roles.get(v) not in role:
                errors.append(f"{key} '{v}' is not a column with the role {' or '.join(role)}")
        return ({**obj, "analyses": good}, []) if not errors else (None, errors)
    return validate


def propose_plan(llm, df: pd.DataFrame, profile: dict, goal: str, context: dict | None = None, explore: bool = False, overrides: dict | None = None,
                 state: dict | None = None) -> tuple[dict, list[str]]:
    """The model proposes the whole plan through propose() (one repair, then the rules plan); the code then adds what is required. Every analysis says who chose it."""
    ov = overrides or {}
    roles, uniques = roles_and_uniques(profile)
    ids = {c for c, r in roles.items() if r == "id"}
    cap = plan_cap(explore)
    res = propose(llm, "analysis plan", plan_system(cap), plan_view(profile, goal, context, explore, ov), plan_validator(_apply_exclusions(roles, ov), uniques, ids, cap), lambda: None)
    ai = res["source"] in AI_SOURCES
    plan, notes = finalize_plan(res["value"] if ai else None, df, profile, goal, context, explore, ov)
    plan["source"] = "llm" if res["source"] == "llm" else "repaired" if ai else "rules"
    by = sum(a.get("origin") == "ai" for a in plan["analyses"])
    if state is not None:
        state.setdefault("decisions", []).append({
            "decision": "analysis plan", "source": res["source"], "chosen": {"analyses chosen by the AI": by, "analyses added by code": len(plan["analyses"]) - by if ai else 0},
            "why": res["why"] if ai else "The rule-based plan was used." if res["source"] != "fallback" else "The model's plan was not usable after one repair, so the rule-based plan was used.", "errors": res["errors"]})
    return plan, notes


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
            analyses.append({**ok, "origin": "ai"})
    if len(analyses) < 2:
        notes.append("LLM plan was too thin; filled in with rule-based steps.")
        for spec in fallback["analyses"]:
            sig = spec_signature(spec)
            if sig not in seen:
                seen.add(sig)
                analyses.append(_code(spec, f"{spec['why']} (added by code: the AI's plan had fewer than 2 usable analyses)"))
    target = raw.get("target") if raw.get("target") in roles and roles[raw["target"]] in ("numeric", "binary") else fallback["target"]
    date = raw.get("date_col") if raw.get("date_col") in roles and roles[raw["date_col"]] == "date" else fallback["date_col"]
    if ov.get("target") and target != fallback["target"]:
        notes.append(f"The main number you chose ({fallback['target']}) replaced the model's choice ({target}); analyses were rebuilt around it.")
        target, analyses = fallback["target"], [_code(a, a["why"]) for a in fallback["analyses"]]
    if ov.get("date_col") and date != fallback["date_col"]:
        notes.append(f"Time analysis uses {fallback['date_col']}, the date column you chose.")
        date = fallback["date_col"]
        analyses = [{**a, "params": {**a["params"], "date": date}} if "date" in a["params"] else a for a in analyses]
    if target and goalmod.asks_about_change(goal, explore, ov.get("intent")):
        have ={spec_signature(a) for a in analyses}
        need = [a for a in fallback["analyses"] if a["tool"] in ("trend", "segment_trend") and a["params"].get("target") == target and spec_signature(a) not in have]
        analyses = [_code(a, f"The goal asks about change over time, so the {'overall' if a['tool'] == 'trend' else 'per-group'} trend of {target} is always included.") for a in need] + analyses
    seen_sigs = {spec_signature(a) for a in analyses}
    groups = list(dict.fromkeys([a["params"]["group"] for a in analyses + fallback["analyses"] if a["tool"] == "group_compare" and "within" not in a["params"]
                                 and a["params"].get("target") == target]))
    more = _include_extras(roles, date, ov, target) + (measure_extras(df, roles, uniques, candidate_measures(df, roles, uniques, ov), {target}, date, groups) if target else [])
    analyses += [_code(a, a["why"]) for a in more if spec_signature(a) not in seen_sigs]
    analyses = ensure_required(df, roles, uniques, goal, explore, ov, target, date, analyses)
    analyses = [a if a.get("origin") == "ai" else {**a, "origin": "code"} for a in analyses]
    used ={c for a in analyses for v in a["params"].values() for c in (v if isinstance(v, list) else [v])}
    relevant = list(dict.fromkeys([c for c in (raw.get("relevant_columns") or []) if c in roles] + [c for c in used if c in roles] + ([target] if target else []) + ([date] if date else [])
                                  + [c for c in ov.get("include") or [] if c in roles]))
    relevant += plausible_dimensions(df, roles, uniques, target, relevant)
    questions = [str(q) for q in (raw.get("questions") or [])][:4] or fallback["questions"]
    skipped = [s for s in (raw.get("skipped") or []) if isinstance(s, dict) and s.get("column") in roles][:10]
    objective = fallback["objective"] if explore else str(raw.get("objective") or goal)
    return {"objective": objective, "questions": questions, "target": target, "date_col": date,
            "relevant_columns": relevant, "analyses": analyses, "skipped": skipped, "source": "llm", "explore": bool(explore), "derived": list(ov.get("derived") or [])}, notes
