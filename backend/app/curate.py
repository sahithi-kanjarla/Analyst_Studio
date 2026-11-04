"""Selection engine: decides what is important enough to show on each tab.

After analysis, builds a compact candidate list (one entry per finding, idea,
chart, key figure and filter), asks the AI for a priority order, computes an
importance score from goal relevance, effect size, share of total and
reliability, applies a cut per tab and kind, and groups the rest.

The whole decision (kept ids per tab, more list, left-out count and reasons) is
saved alongside the other results so the tabs can read it later.

Nothing here knows a column name, an industry or an audience.  The AI call sees
ids and plain titles only; every number comes from the code.
"""
from __future__ import annotations

import json
import math
import re
from typing import Sequence

from .clarify import named_in_goal
from .insights import effect_label
from .llm import ask_json, capture, note_failure
from .measures import measure_kind

# ------------------------------------------------------------------ constants
# All thresholds in one place.
SCORE_FLOOR_FRAC = 0.35       # keep items scoring >= this fraction of the top score
MASS_TARGET = 0.85            # ... or until kept items carry this share of total importance
CEILING_BASE = 12             # loose safety ceiling: max(CEILING_BASE, ceil(n * CEILING_SCALE))
CEILING_SCALE = 0.55          # (rarely binds)
MIN_KEEP = 3                  # always keep at least this many per kind
AI_TOP_BONUS = 0.5            # per-position bonus for AI-ranked items (decays)
GOAL_BONUS = 2.0
CAUTION_BONUS = 5.0           # data cautions are always kept
NULL_PENALTY = 1.5            # subtracted from null-result scores
OVERLAP_PENALTY = 0.8         # a candidate that adds nothing new keeps (1 - this) of its score
PARTIAL_OVERLAP = 0.5         # same dimension and same leader, different measure and different last place
COVERAGE_PTS = 0.4            # overview mode only: points for a well-filled measure column
NOTE_NO_GOAL = ("No goal was given, so this is an overview of the data: the main figure, "
                "who contributes most, how it changes over time and how concentrated it is.")

EFFECT_PTS = {"large": 1.0, "medium": 0.6, "small": 0.2, "negligible": 0.05, None: 0.0}
RELIABILITY_PTS = {"high": 1.0, "medium": 0.6, "low": 0.2,
                   "exploratory": 0.1, "descriptive": 0.3}

# Item kinds
KIND_FINDING = "finding"
KIND_IDEA = "idea"
KIND_CHART = "chart"
KIND_KEY_FIGURE = "key_figure"
KIND_FILTER = "filter"
KINDS = (KIND_KEY_FIGURE, KIND_FINDING, KIND_IDEA, KIND_CHART, KIND_FILTER)

# Tabs that receive curated content
TAB_REPORT = "report"
TAB_INSIGHTS = "insights"
TAB_IDEAS = "ideas"
TAB_DASHBOARD = "dashboard"
TABS = (TAB_REPORT, TAB_INSIGHTS, TAB_IDEAS, TAB_DASHBOARD)

# Which (tab, kind) combinations exist
TAB_KINDS: dict[str, tuple[str, ...]] = {
    TAB_REPORT:    (KIND_KEY_FIGURE, KIND_FINDING, KIND_CHART),
    TAB_INSIGHTS:  (KIND_FINDING,),
    TAB_IDEAS:     (KIND_IDEA,),
    TAB_DASHBOARD: (KIND_CHART, KIND_FINDING, KIND_FILTER),
}

ROBUST_TOOLS = ("robust_compare",)
TABLE_TOOLS = ("group_table", "summary_table")
NULL_KEYWORDS = re.compile(
    r"does not differ|no reliable|not statistically|no evidence|"
    r"no significant|broadly flat|too noisy|no real difference",
    re.I,
)
CARD_PARAMS = ("target", "col", "cols", "group", "group_a", "group_b", "by")


# ------------------------------------------------------------------ helpers
def _measure(f: dict) -> str:
    p = f.get("params") or {}
    return str(p.get("target") or p.get("col") or p.get("y") or "")


def _dimension(f: dict) -> str:
    p = f.get("params") or {}
    return str(p.get("group") or p.get("by") or p.get("dim") or "")


def _is_null(f: dict) -> bool:
    """True when the finding says 'no reliable difference/trend/pattern'."""
    if f.get("significant") is False:
        return True
    text = (f.get("insight") or {}).get("headline") or f.get("title") or ""
    return bool(NULL_KEYWORDS.search(text))


def _is_caution(f: dict) -> bool:
    """True for data-quality warnings (gaps, coverage, date swaps)."""
    return (f.get("insight") or {}).get("confidence", {}).get("level") == "descriptive" and \
        f.get("tool") in ("distribution", "outliers", "category_share")


def _leader(f: dict) -> tuple[str, str]:
    """(best, worst) group of a comparison, '' when the finding has none."""
    st = f.get("stats") or {}
    return str(st.get("best") or ""), str(st.get("worst") or "")


def _chart_key(f: dict) -> tuple[str, str, str]:
    """(chart_type, measure, dimension) for dedup: two charts with the same key are redundant."""
    return (f.get("chart_type") or f.get("tool", ""), _measure(f), _dimension(f))


def _effect_num(f: dict) -> float:
    st = f.get("stats") or {}
    raw = st.get("effect")
    if raw is not None:
        return float(raw)
    # trends: use relative_change magnitude
    rc = st.get("relative_change")
    if rc is not None:
        return min(abs(float(rc)), 1.0)
    return 0.0


def _share(f: dict) -> float:
    """What fraction of the total outcome this finding explains (0-1)."""
    st = f.get("stats") or {}
    # eta_squared *is* the variance explained
    if st.get("effect_name") == "eta_squared" and st.get("effect") is not None:
        return float(st["effect"])
    # pareto top_share
    if st.get("top_share") is not None:
        return float(st["top_share"])
    return 0.0


def _reliability(f: dict) -> str:
    return ((f.get("insight") or {}).get("confidence") or {}).get("level", "descriptive")


# ------------------------------------------------------------------ candidate list
def _candidates_from_findings(findings: list[dict], goal: str) -> list[dict]:
    """One 'finding' candidate per non-auxiliary finding, plus one 'chart' candidate
    per finding that has a chart (robustness and table tools are excluded as charts)."""
    out: list[dict] = []
    seen_charts: set[tuple] = set()
    for f in findings:
        tool = f.get("tool", "")
        if tool in ROBUST_TOOLS or tool in TABLE_TOOLS:
            continue  # folded into their parent, not standalone candidates
        fid = f["id"]
        cols: list[str] = []
        for k in CARD_PARAMS:
            v = (f.get("params") or {}).get(k)
            if v:
                cols += [str(x) for x in v] if isinstance(v, list) else [str(v)]
        goal_rel = any(named_in_goal(c, goal) for c in cols)
        null = _is_null(f)
        caution = _is_caution(f)
        eff = _effect_num(f)
        share = _share(f)
        rel = _reliability(f)
        score_val = (f.get("insight") or {}).get("score", 0)
        best, worst = _leader(f)
        vals = (f.get("stats") or {}).get("values")
        out.append({
            "leader": best,
            "worst": worst,
            "value_sig": tuple(sorted(round(float(v), 9) for v in vals.values()
                                      if isinstance(v, (int, float)))) if isinstance(vals, dict) else (),
            "top_share": (f.get("stats") or {}).get("top_share"),
            "top_k": (f.get("stats") or {}).get("top_k"),
            "leader_value": vals.get(best) if isinstance(vals, dict) else None,
            "id": fid,
            "title": (f.get("insight") or {}).get("headline") or f.get("title", ""),
            "kind": KIND_FINDING,
            "measure": _measure(f),
            "dimension": _dimension(f),
            "effect_size": eff,
            "effect_label": effect_label(f.get("stats") or {}),
            "share_of_total": share,
            "reliability": rel,
            "goal_relevant": goal_rel,
            "null_result": null,
            "caution": caution,
            "base_score": float(score_val),
            "tool": tool,
            "within": (str((f.get("params") or {}).get("within") or ""), str((f.get("params") or {}).get("within_value") or "")),
        })
        # chart candidate (if the finding has a chart, is not a robustness/table
        # tool, and is not a null result — null results never get their own chart)
        if f.get("chart") and not null:
            ck = _chart_key(f)
            if ck not in seen_charts:
                seen_charts.add(ck)
                out.append({
                    "id": f"ch_{fid}",
                    "title": f.get("title", ""),
                    "kind": KIND_CHART,
                    "measure": _measure(f),
                    "dimension": _dimension(f),
                    "effect_size": eff,
                    "effect_label": effect_label(f.get("stats") or {}),
                    "share_of_total": share,
                    "reliability": rel,
                    "goal_relevant": goal_rel,
                    "null_result": null,
                    "caution": False,
                    "base_score": float(score_val),
                    "finding_id": fid,
                    "chart_type": f.get("chart_type") or tool,
                    "chart_key": ck,
                    "tool": tool,
                })
    return out


def _candidates_from_strategies(strategies: list[dict], goal: str,
                                findings_by_id: dict) -> list[dict]:
    out: list[dict] = []
    for s in strategies:
        if s.get("kind") != "data":
            continue
        ev_ids = s.get("evidence") or []
        goal_rel = any(
            any(named_in_goal(c, goal)
                for c in [str((findings_by_id.get(e) or {}).get("params", {}).get(k, ""))
                          for k in CARD_PARAMS])
            for e in ev_ids if e in findings_by_id
        )
        impact = {"High": 1.0, "Medium": 0.6, "Low": 0.2}.get(
            s.get("impact", ""), 0.3)
        out.append({
            "id": s["id"],
            "title": s.get("title", ""),
            "kind": KIND_IDEA,
            "measure": "",
            "dimension": "",
            "effect_size": impact,
            "effect_label": None,
            "share_of_total": 0.0,
            "reliability": s.get("confidence", "medium"),
            "goal_relevant": goal_rel,
            "null_result": False,
            "caution": False,
            "base_score": impact * 3,
            "tool": "strategy",
        })
    return out


def _candidates_from_metrics(metrics: list[dict], goal: str) -> list[dict]:
    out: list[dict] = []
    for i, m in enumerate(metrics):
        label = m.get("label", "")
        words = re.findall(r"\w{3,}", goal or "")
        goal_rel = bool(re.search("|".join(re.escape(w) for w in words), label, re.I)) if words else False   # no usable word: matches nothing
        out.append({
            "id": f"kf_{i}",
            "title": label,
            "kind": KIND_KEY_FIGURE,
            "measure": label,
            "dimension": "",
            "effect_size": 0.0,
            "effect_label": None,
            "share_of_total": 0.0,
            "reliability": "descriptive",
            "goal_relevant": goal_rel,
            "null_result": False,
            "caution": False,
            "base_score": 1.0 + (1.0 if goal_rel else 0.0),
            "tool": "metric",
        })
    return out


def _candidates_from_filters(dims: list[dict], goal: str) -> list[dict]:
    out: list[dict] = []
    for d in dims:
        col = d.get("col", "")
        out.append({
            "id": f"dim_{col}",
            "title": col,
            "kind": KIND_FILTER,
            "measure": "",
            "dimension": col,
            "effect_size": d.get("score", 0.0),
            "effect_label": None,
            "share_of_total": 0.0,
            "reliability": "descriptive",
            "goal_relevant": bool(d.get("goal")),
            "null_result": False,
            "caution": False,
            "base_score": d.get("score", 0.0) + (1.0 if d.get("goal") else 0.0),
            "tool": "filter",
        })
    return out


def build_candidates(state: dict) -> list[dict]:
    """Build the full candidate list from the run state."""
    goal = state.get("goal", "")
    findings = state.get("findings") or []
    strategies = state.get("strategies") or []
    metrics = state.get("key_metrics") or []
    dims = (state.get("dashboard") or {}).get("dims") or []

    by_id = {f["id"]: f for f in findings}
    cands = []
    cands += _candidates_from_findings(findings, goal)
    cands += _candidates_from_strategies(strategies, goal, by_id)
    cands += _candidates_from_metrics(metrics, goal)
    cands += _candidates_from_filters(dims, goal)
    return cands


# ------------------------------------------------------------------ AI ranking
_RANK_SYSTEM = (
    "You are a data analyst choosing which findings, charts, metrics and ideas "
    "are most important for a business user to see first.\n"
    "Return JSON: {\"ranked\": [{\"id\": \"<id>\", \"reason\": \"<one line>\"}]}.\n"
    "Use ONLY ids from the given list. Do not add, repeat or invent ids."
)


def _rank_prompt(candidates: list[dict], goal: str) -> str:
    lines = [f"GOAL: {goal}", "", "CANDIDATES (id | kind | title):"]
    for c in candidates:
        lines.append(f"  {c['id']} | {c['kind']} | {c['title']}")
    lines += [
        "",
        "Order them from most important to least important for the user to see.",
        "Return every id exactly once.",
    ]
    return "\n".join(lines)


def _validate_ranking(obj: dict) -> list[str]:
    if not isinstance(obj, dict) or not isinstance(obj.get("ranked"), list):
        return ["expected {\"ranked\": [...]}"]
    for item in obj["ranked"]:
        if not isinstance(item, dict) or "id" not in item:
            return ["each entry needs an 'id'"]
    return []


def ai_rank(llm, candidates: list[dict], goal: str) -> tuple[list[str], str]:
    """Ask the AI for a priority order.  Returns (ordered ids, reason) where
    reason is '' on success or why it fell back to rules."""
    if not candidates:
        return [], ""
    with capture() as cap:
        obj = ask_json(llm, _RANK_SYSTEM, _rank_prompt(candidates, goal),
                       validate=_validate_ranking, cache_tag="curate_v1")
    if cap.reasons:
        return [], cap.reason
    if obj is None or not isinstance(obj.get("ranked"), list):
        return [], "unusable AI response"
    valid_ids = {c["id"] for c in candidates}
    seen: set[str] = set()
    ordered: list[str] = []
    for item in obj["ranked"]:
        iid = item.get("id") if isinstance(item, dict) else None
        if iid in valid_ids and iid not in seen:
            ordered.append(iid)
            seen.add(iid)
    if not ordered:
        return [], "AI returned no valid ids"
    return ordered, ""


# ------------------------------------------------------------------ importance score
def importance(c: dict, ai_rank_pos: int | None = None, n_total: int = 1) -> float:
    """Compute the importance score for one candidate.  All factors come from the
    data (goal relevance, effect size, share of total, reliability).  The AI
    ranking adds a small position bonus but cannot override the data."""
    s = c.get("base_score", 0.0)
    s += EFFECT_PTS.get(c.get("effect_label"), c.get("effect_size", 0.0))
    s += c.get("share_of_total", 0.0) * 2
    s += RELIABILITY_PTS.get(c.get("reliability", "descriptive"), 0.0)
    if c.get("coverage") is not None:          # set in overview mode only
        s += COVERAGE_PTS * float(c["coverage"])
    if c.get("goal_relevant"):
        s += GOAL_BONUS
    if c.get("caution"):
        s += CAUTION_BONUS
    if c.get("null_result"):
        s -= NULL_PENALTY
    # AI rank bonus: highest-ranked gets the most, decaying linearly
    if ai_rank_pos is not None and n_total > 0:
        s += AI_TOP_BONUS * max(0, 1 - ai_rank_pos / n_total)
    return round(max(s, 0.0), 4)



# ------------------------------------------------------------------ goal mode
def _profile_cols(state: dict) -> dict[str, dict]:
    return {c["name"]: c for c in ((state.get("profile") or {}).get("columns") or []) if c.get("name")}


def _goal_columns(state: dict, goal: str) -> set[str]:
    """Columns the goal points at: every column whose own words appear in the goal, plus the
    columns the understanding record maps the goal's terms to (when that record is usable)."""
    pool = set(_profile_cols(state))
    for f in state.get("findings") or []:
        for k in CARD_PARAMS:
            v = (f.get("params") or {}).get(k)
            pool.update(str(x) for x in (v if isinstance(v, list) else [v]) if x)
    named = {c for c in pool if named_in_goal(c, goal)}
    rec = state.get("understanding") or {}
    if rec.get("source") == "llm":
        for cols in (rec.get("goal_columns") or {}).values():
            named.update(x for x in cols if isinstance(x, str))
    return named


def goal_mode(state: dict) -> tuple[str, str]:
    """('goal' | 'overview', note).  Only the explore flag (the person ticked "I'm not sure what
    to look for") gives the overview mode.  Anything typed is a real goal, however short, and
    goes through the normal goal matching."""
    if state.get("explore") or (state.get("plan") or {}).get("explore") or (state.get("context") or {}).get("explore"):
        return "overview", NOTE_NO_GOAL
    return "goal", ""


def main_measure(state: dict, cands: list[dict]) -> str:
    """The measure an overview is about.  Code verifies it is a numeric measure that was
    analysed.  With a usable understanding record: the record's measures by kind (money,
    then count, then quantity, then the rest), then best filled.  Without it: additive
    measures first, then the planner's target, then best filled.  The name is only the
    last tiebreak, so the result is deterministic."""
    prof = _profile_cols(state)
    analysed = sorted({c["measure"] for c in cands if c["kind"] == KIND_FINDING and c.get("measure")
                       and c["tool"] in ("group_compare", "pareto", "trend", "outcome_compare",
                                         "segment_trend", "heatmap")})
    verified = [m for m in analysed if (prof.get(m) or {}).get("role") == "numeric"] if prof else analysed
    if not verified:
        return ""

    def miss(m):
        return float((prof.get(m) or {}).get("missing_pct") or 0.0)

    rec = state.get("understanding") or {}
    if rec.get("source") == "llm":
        rank = {"money": 0, "count": 1, "quantity": 2, "rate": 3, "cost": 4}
        cols = rec.get("columns") or {}
        labelled = [m for m in verified if (cols.get(m) or {}).get("role") == "measure"
                    and (cols[m].get("measure_kind") or "") in rank]
        if labelled:
            return min(labelled, key=lambda m: (rank[cols[m]["measure_kind"]], miss(m), m))
    kinds = (state.get("brief") or {}).get("kinds") or {}
    target = (state.get("plan") or {}).get("target")
    return min(verified, key=lambda m: ((kinds.get(m) or measure_kind(m)) != "additive",
                                        m != target, miss(m), m))


def _mark_direct(cands: list[dict], state: dict, goal: str) -> None:
    """Flag the findings that directly answer the goal: the measure and the dimension the goal
    asks about.  A goal that names a measure but no usable dimension gets its single best
    finding on that measure, so a goal always has one answer."""
    named = _goal_columns(state, goal)
    target = (state.get("plan") or {}).get("target") or ""
    findings = [c for c in cands if c["kind"] == KIND_FINDING]
    any_measure_named = any(c["measure"] in named for c in findings if c["measure"])

    def measure_ok(c):
        return c["measure"] in named if any_measure_named else (bool(target) and c["measure"] == target)

    pair = [c for c in findings if measure_ok(c) and c["dimension"] and c["dimension"] in named]
    if not pair:
        rest = sorted((c for c in findings if measure_ok(c)),
                      key=lambda c: (-(c["base_score"] + c["effect_size"]), c["id"]))
        pair = rest[:1]
    for c in pair:
        c["direct"] = True


def _mark_overview(cands: list[dict], main: str) -> None:
    """Overview mode: flag the standard overview findings for the main measure: top
    contributors by the main dimension, the trend over time, and concentration."""
    fs = [c for c in cands if c["kind"] == KIND_FINDING and c["measure"] == main]
    by_dim = sorted((c for c in fs if c["tool"] in ("group_compare", "pareto") and c["dimension"]),
                    key=lambda c: (-(c["base_score"] + c["effect_size"]), c["id"]))
    dim = by_dim[0]["dimension"] if by_dim else ""

    def first(tool, need_dim):
        pool = [c for c in fs if c["tool"] == tool and (not need_dim or c["dimension"])]
        pool.sort(key=lambda c: (c["dimension"] != dim, -(c["base_score"] + c["effect_size"]), c["id"]))
        return pool[0] if pool else None

    for c in (first("group_compare", True), first("trend", False), first("pareto", True)):
        if c:
            c["core"] = True


# ------------------------------------------------------------------ overlap
def _num_text(v) -> str:
    v = float(v)
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.2f}" if abs(v) >= 1 else f"{v:.3f}"


def _item(c: dict) -> dict:
    """One line of a merged finding, built by code from the original finding's own figures."""
    m = c["measure"]
    if c["tool"] == "pareto" and c.get("top_share") is not None:
        fig = float(c["top_share"])
        k = c.get("top_k")
        text = f"{m}: the top {k if k else 'few'} {c['dimension']} values carry {fig:.0%}"
    elif c.get("leader_value") is not None:
        fig = float(c["leader_value"])
        text = f"{m}: {c['leader']} at {_num_text(fig)}"
    else:
        fig, text = None, (c.get("title") or f"{m}")      # no single figure to quote: the finding's own headline, never a bare column name
    return {"id": c["id"], "measure": m, "figure": fig, "text": text}


def _relation(c: dict, p: dict) -> tuple[str, float]:
    """How much of c is already said by p: (kind, overlap) with kind 'merge', 'same', 'partial' or ''."""
    same_dim = bool(c["dimension"]) and c["dimension"] == p["dimension"]
    if c.get("within") != p.get("within"):        # a slice (within category = X) and the whole table, or two different slices, are different populations: never one card
        both = c["tool"] == p["tool"] == "group_compare"
        return ("partial", PARTIAL_OVERLAP) if both and same_dim and c["measure"] != p["measure"] and c["leader"] and c["leader"] == p["leader"] else ("", 0.0)
    if same_dim and c["measure"] == p["measure"] and c["tool"] != p["tool"]:
        return "merge", 1.0                       # the same measure and dimension drawn another way
    both_cmp = c["tool"] == p["tool"] == "group_compare"
    if both_cmp and c["measure"] == p["measure"] and c["dimension"] != p["dimension"] \
            and len(c["value_sig"]) >= 2 and c["value_sig"] == p["value_sig"]:
        return "same", 1.0                        # the dimension maps one-to-one onto a standing one
    if both_cmp and same_dim and c["measure"] != p["measure"] and c["leader"] and c["leader"] == p["leader"]:
        if c["worst"] == p["worst"]:
            return "merge", 1.0                   # the same ordering at both ends, another measure
        return "partial", PARTIAL_OVERLAP
    return "", 0.0


def apply_overlap(cands: list[dict]) -> tuple[dict, dict]:
    """Walk the ranked findings; each one's score is reduced by how much it repeats what is
    already standing, or it is merged into the finding that already says it.  Returns
    (merges {primary_id: {...}}, absorbed {absorbed_id: primary_id}).  Deterministic."""
    fs = sorted((c for c in cands if c["kind"] == KIND_FINDING), key=lambda c: (-c["importance"], c["id"]))
    merges: dict[str, dict] = {}
    absorbed: dict[str, str] = {}
    standing: list[dict] = []
    top = fs[0]["importance"] if fs else 0.0
    for c in fs:
        c["overlap"] = 0.0
        if c.get("caution") or c.get("null_result") or c.get("pinned"):
            standing.append(c)
            continue
        best_kind, best_o, best_p = "", 0.0, None
        for p in standing:
            if p.get("caution") or p.get("null_result"):
                continue
            kind, o = _relation(c, p)
            if kind == "merge" and (c.get("direct") or c.get("core")) and c["measure"] != p["measure"]:
                kind, o = "partial", PARTIAL_OVERLAP     # an item the goal or the overview needs is not folded into another measure's
            if o > best_o:
                best_kind, best_o, best_p = kind, o, p
        if best_kind == "merge":
            entry = merges.setdefault(best_p["id"], {"dimension": best_p["dimension"],
                                                      "leader": best_p["leader"], "items": [_item(best_p)]})
            entry["items"].append(_item(c))
            absorbed[c["id"]] = best_p["id"]
            continue
        if best_o:
            c["overlap"] = best_o
            c["importance"] = round(c["importance"] * (1 - OVERLAP_PENALTY * best_o), 4)
        if _always_keep(c) or c["importance"] >= top * SCORE_FLOOR_FRAC:
            standing.append(c)
    # a chart follows its finding; two charts of one measure and dimension are one chart
    chart_seen: dict[tuple, str] = {}
    for c in sorted((c for c in cands if c["kind"] == KIND_CHART), key=lambda c: (-c["importance"], c["id"])):
        fid = c.get("finding_id")
        if fid in absorbed:
            absorbed[c["id"]] = absorbed[fid]
            continue
        key = (c["measure"], c["dimension"])
        if c["dimension"] and key in chart_seen:
            absorbed[c["id"]] = chart_seen[key]
        else:
            chart_seen[key] = c["id"]
    return merges, absorbed


# ------------------------------------------------------------------ the cut
def _ceiling(n: int) -> int:
    """Loose safety ceiling that scales with n and rarely binds."""
    return max(CEILING_BASE, math.ceil(n * CEILING_SCALE))


def _always_keep(c: dict) -> bool:
    """Items that are never cut: the direct answer to the goal (or, in overview mode, the
    standard overview findings), data cautions, pinned/edited.  Merely being about a
    measure the goal names is only a score boost."""
    return bool(c.get("direct")) or bool(c.get("core")) or bool(c.get("caution")) or bool(c.get("pinned"))


def apply_cut(scored: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Apply the cut to a scored list (one tab+kind group).
    Returns (kept, more, left_out)."""
    if not scored:
        return [], [], []
    # sort descending by importance
    items = sorted(scored, key=lambda c: -c["importance"])
    top_score = items[0]["importance"] if items else 1
    floor = top_score * SCORE_FLOOR_FRAC
    total_mass = sum(c["importance"] for c in items)
    ceil = _ceiling(len(items))

    kept, more, left_out = [], [], []
    mass_so_far = 0.0

    for c in items:
        if _always_keep(c):
            kept.append(c)
            mass_so_far += c["importance"]
            continue
        # the stricter of: score floor AND mass target
        above_floor = c["importance"] >= floor
        below_mass = total_mass > 0 and (mass_so_far / total_mass) < MASS_TARGET
        within_ceiling = len(kept) < ceil
        if above_floor and below_mass and within_ceiling:
            kept.append(c)
            mass_so_far += c["importance"]
        elif c.get("null_result") or c["importance"] <= 0:
            left_out.append(c)
        else:
            more.append(c)
    # ensure at least MIN_KEEP
    while len(kept) < MIN_KEEP and more:
        kept.append(more.pop(0))
    return kept, more, left_out


# ------------------------------------------------------------------ chart dedup
def dedup_charts(charts: list[dict]) -> list[dict]:
    """Never two charts with the same (chart_type, measure, dimension)."""
    seen: set[tuple] = set()
    out: list[dict] = []
    for c in charts:
        ck = c.get("chart_key") or (c.get("chart_type", ""), c.get("measure", ""), c.get("dimension", ""))
        if ck in seen:
            continue
        seen.add(ck)
        out.append(c)
    return out


# ------------------------------------------------------------------ null-result grouping
def group_nulls(nulls: list[dict], goal: str, main_measure: str) -> dict:
    """Null results become short one-line entries for the goal and main measures;
    the rest are counted."""
    lines: list[dict] = []
    counted = 0
    for c in nulls:
        is_goal = c.get("goal_relevant")
        is_main = c.get("measure", "") == main_measure
        if is_goal or is_main:
            lines.append({
                "id": c["id"],
                "title": c["title"],
                "kind": c["kind"],
                "reason": "goal" if is_goal else "main measure",
            })
        else:
            counted += 1
    return {"lines": lines, "counted": counted}


# ------------------------------------------------------------------ robustness attachment
def attach_robust(findings: list[dict]) -> dict[str, str]:
    """Map each robustness-check finding to the parent finding it supports.
    Returns {robust_id: parent_id}."""
    by_key: dict[tuple, str] = {}
    for f in findings:
        if f.get("tool") not in ROBUST_TOOLS:
            p = f.get("params") or {}
            key = (p.get("target"), p.get("group"))
            if key[0] and key[1]:
                by_key[key] = f["id"]
    mapping: dict[str, str] = {}
    for f in findings:
        if f.get("tool") in ROBUST_TOOLS:
            p = f.get("params") or {}
            key = (p.get("target"), p.get("group"))
            if key in by_key:
                mapping[f["id"]] = by_key[key]
    return mapping


# ------------------------------------------------------------------ main entry
def curate(state: dict, llm=None) -> dict:
    """Run the full selection engine.  Returns the decision dict that is saved
    in result.json under the key 'curation'."""
    candidates = build_candidates(state)
    goal = state.get("goal", "")
    mode, mode_note = goal_mode(state)
    main = main_measure(state, candidates)
    if mode == "overview":
        # the goal is a placeholder or too general: nothing is goal-relevant, the overview findings are kept
        for c in candidates:
            c["goal_relevant"] = False
        _mark_overview(candidates, main)
        prof = _profile_cols(state)
        for c in candidates:
            if c["kind"] in (KIND_FINDING, KIND_CHART):
                c["coverage"] = 1.0 - float((prof.get(c.get("measure")) or {}).get("missing_pct") or 0.0)
    else:
        _mark_direct(candidates, state, goal)
    by_cand = {c["id"]: c for c in candidates}
    for c in candidates:                                   # a chart carries the flags of its finding
        if c["kind"] == KIND_CHART:
            parent = by_cand.get(c.get("finding_id")) or {}
            c["direct"], c["core"] = bool(parent.get("direct")), bool(parent.get("core"))
    main_measure_name = main or (state.get("plan") or {}).get("target", "")

    # AI ranking (one call)
    ai_ids, ai_reason = ([], "no LLM configured") if llm is None \
        else ai_rank(llm, candidates, goal)
    ai_pos = {iid: pos for pos, iid in enumerate(ai_ids)}
    n_total = len(candidates)

    # score every candidate
    for c in candidates:
        c["importance"] = importance(c, ai_pos.get(c["id"]), n_total)

    # overlap: merge findings that say the same thing, reduce the score of repeats
    merges, absorbed = apply_overlap(candidates)

    # assign candidates to (tab, kind) groups
    groups: dict[tuple[str, str], list[dict]] = {}
    for tab, ks in TAB_KINDS.items():
        for kind in ks:
            items = [c for c in candidates if c["kind"] == kind and c["id"] not in absorbed]
            if items:
                groups[(tab, kind)] = items

    # apply the cut per group
    decision: dict[str, dict] = {}
    for (tab, kind), items in groups.items():
        # chart dedup before the cut
        if kind == KIND_CHART:
            items = dedup_charts(items)
        kept, more, left_out = apply_cut(items)
        # separate null results from left_out
        real_left = [c for c in left_out if not c.get("null_result")]
        null_left = [c for c in left_out if c.get("null_result")]
        # null results that were kept but are null -> move them to null grouping
        null_kept = [c for c in kept if c.get("null_result")]
        real_kept = [c for c in kept if not c.get("null_result")]
        # only goal/main-measure null results get one-line entries
        null_group = group_nulls(null_kept + null_left, goal, main_measure_name)
        key = f"{tab}:{kind}"
        decision[key] = {
            "kept": [c["id"] for c in real_kept] + [n["id"] for n in null_group["lines"]],
            "more": [c["id"] for c in more],
            "left_out_count": len(real_left) + null_group["counted"],
            "null_lines": null_group["lines"],
            "null_counted": null_group["counted"],
        }

    # robustness mapping (charts attached to findings, not separate)
    robust_map = attach_robust(state.get("findings") or [])

    # flat lists for quick lookup
    all_kept = set()
    all_more = []
    all_left_count = 0
    for v in decision.values():
        all_kept.update(v["kept"])
        all_more += [i for i in v["more"] if i not in all_kept]
        all_left_count += v["left_out_count"]

    return {
        "candidates": [{k: c[k] for k in ("id", "title", "kind", "measure",
                                            "dimension", "effect_size",
                                            "effect_label", "share_of_total",
                                            "reliability", "goal_relevant",
                                            "null_result", "importance", "overlap", "direct", "core")
                        if k in c}
                       for c in candidates],
        "mode": mode,
        "mode_note": mode_note,
        "main_measure": main,
        "merges": merges,
        "absorbed": absorbed,
        "ai_ranking": ai_ids,
        "ai_reason": ai_reason,
        "tabs": decision,
        "robust_map": robust_map,
        "kept_ids": sorted(all_kept),
        "more_ids": sorted(set(all_more)),
        "left_out_count": all_left_count,
    }
