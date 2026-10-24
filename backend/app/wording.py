"""Shared wording rules for the report, so every section says the same thing about the same findings.

  * segment trends: direction words follow the signs, and a percentage is shown only when it means something
  * counts: one function counts the findings; the summary, key numbers and the critic all read it
  * entity names: a value is named after its own column ("sub-category Laptops"), never with an industry noun attached
  * answers: one line per planned question, taken from the findings
  * limitation statements written by an LLM are dropped when the findings contradict them
Nothing here knows an industry or a column name."""
from __future__ import annotations

import re

SIG = 0.05
NEAR_ZERO = 0.1          # a starting level below this share of the average level is "near zero"
COMPARISON_TOOLS = ("group_compare", "outcome_compare", "scatter", "composition", "seasonality")


def _g(v) -> str:
    return f"{v:,.3g}"


# ------------------------------------------------------------------ names
def human(col: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"_+", " ", re.sub(r"([a-z])([A-Z])", r"\1 \2", str(col)))).strip().lower()


def entity(group: str, value) -> str:
    """A value named after its own column: 'sub-category Laptops'."""
    return f"{human(group)} {value}"


def other_columns(state: dict, *skip: str, limit: int = 3) -> list[str]:
    plan = state.get("plan") or {}
    skipped = set(skip) | {plan.get("target"), plan.get("date_col")}
    return [c for c in plan.get("relevant_columns") or [] if c not in skipped][:limit]


def columns_phrase(cols: list[str], fallback: str = "the other columns in the data") -> str:
    cols = [f"`{c}`" for c in cols]
    return fallback if not cols else cols[0] if len(cols) == 1 else ", ".join(cols[:-1]) + " or " + cols[-1]


# ------------------------------------------------------------------ segment trends
def _delta(e: dict):
    d = e.get("change_abs")
    return d if d is not None else e.get("relative_change")


def pct_ok(e: dict) -> bool:
    """A percentage change is only honest when the start is clear of zero and the series does not change sign."""
    return e.get("pct_ok", True) and e.get("relative_change") is not None


def change_phrase(e: dict, fmt=_g) -> str:
    if pct_ok(e):
        return f"{e['relative_change']:+.0%}"
    d = _delta(e) or 0
    if e.get("change_abs") is None:
        return "no percentage shown"
    return f"{'up' if d > 0 else 'down' if d < 0 else 'flat'} {fmt(abs(d))}, from {fmt(e['start'])} to {fmt(e['end'])}"


def trend_shape(st: dict) -> str:
    """'mixed' only when one group goes up and another goes down; otherwise 'up' or 'down' for all."""
    gs = st.get("groups") or {}
    ds = [_delta(e) or 0 for e in gs.values()]
    if any(d > 0 for d in ds) and any(d < 0 for d in ds):
        return "mixed"
    return "down" if ds and all(d <= 0 for d in ds) and any(d < 0 for d in ds) else "up"


def lead_group(st: dict) -> str:
    """The group that moves fastest in the shared direction."""
    return st["slowest"] if trend_shape(st) == "down" else st["fastest"]


def _is_sig(e: dict) -> bool:
    return (e.get("p_adjusted") if e.get("p_adjusted") is not None else 1.0) < SIG


def _clause(st: dict, g: str, fmt) -> str:
    e = st["groups"][g]
    return f"{g} ({change_phrase(e, fmt)}{'' if _is_sig(e) else ', not a reliable trend'})"


def trend_headline(st: dict, group: str, target: str, fmt=_g) -> str:
    if not st.get("survives_correction"):
        return f"Trends in {target} do not differ reliably across {group}"
    shape = trend_shape(st)
    if shape == "mixed":
        return f"{_clause(st, st['fastest'], fmt)} is moving up fastest while {_clause(st, st['slowest'], fmt)} moves the other way"
    lead = lead_group(st)
    if shape == "down":
        return f"All {group} values are moving down; {_clause(st, lead, fmt)} is falling fastest"
    return f"All {group} values are moving the same way (up); {_clause(st, lead, fmt)} is rising fastest"


def trend_why(st: dict, group: str, target: str) -> str:
    if not st.get("survives_correction"):
        return f"No segment of {group} is moving differently enough to single out."
    if trend_shape(st) == "mixed":
        return f"The overall trend hides diverging segments: {st['fastest']} and {st['slowest']} are heading in different directions, so the average movement is a poor guide to where to act."
    return f"Every {human(group)} is moving the same way, so the overall movement in {target} is broad-based rather than driven by one segment; {lead_group(st)} moves most."


def significant_direction(st: dict, g: str) -> str | None:
    """'rising' or 'falling' only when this group's own trend is significant after correction."""
    e = (st.get("groups") or {}).get(g) or {}
    return None if not _is_sig(e) else "rising" if (_delta(e) or 0) > 0 else "falling" if (_delta(e) or 0) < 0 else None


# ------------------------------------------------------------------ overall trend: percent or absolute
def _ratio_names(state: dict) -> set[str]:
    plan = state.get("plan") or {}
    return {d["name"] for d in plan.get("derived") or []} | {d["name"] for d in (state.get("derived_metrics") or {}).get("created") or []}


def pct_change_ok(st: dict, target, state: dict) -> bool:
    """A % change in a fitted trend is shown only when it means something: never for a ratio, and not when the series is, or may be, on both sides of zero
    (the column has negative and positive values, or the fitted line itself passes through zero)."""
    rel = st.get("relative_change")
    if rel is None or abs(rel) > 2:           # the fit moves by more than twice the level: it starts and ends on opposite sides of zero
        return False
    if st.get("agg") == "ratio of sums" or target in _ratio_names(state):
        return False
    col = next((c for c in (state.get("profile") or {}).get("columns", []) if c.get("name") == target), None)
    lo, hi = ((col or {}).get("stats") or {}).get("min"), ((col or {}).get("stats") or {}).get("max")
    return not (lo is not None and hi is not None and lo < 0 < hi)


def trend_size(st: dict, target, state: dict, signed: bool = False, fmt=_g) -> str:
    """The size of the fitted change across the period: a percentage when that is honest, otherwise the absolute change in the measure's own units."""
    if pct_change_ok(st, target, state):
        r = st["relative_change"]
        return f"{r:+.0%}" if signed else f"{abs(r):.0%}"
    d = (st.get("slope") or 0) * max((st.get("periods") or 1) - 1, 0)
    return (("+" if d > 0 else "-" if d < 0 else "") if signed else "") + fmt(abs(d))


# ------------------------------------------------------------------ counts
def comparison_family(findings: list[dict]) -> list[dict]:
    return [f for f in findings if f["tool"] in COMPARISON_TOOLS and f["stats"].get("p") is not None]


def counts(findings: list[dict]) -> dict:
    """The one set of numbers the report quotes: analyses run, tested for significance, significant, reliable (still significant after correction), comparisons corrected for."""
    tested = [f for f in findings if f.get("significant") is not None and f["tool"] != "robust_compare"]
    sig = [f for f in tested if f["significant"]]
    return {"analyses": len(findings), "tested": len(tested), "significant": len(sig),
            "reliable": sum(1 for f in sig if f["stats"].get("survives_correction", True)), "comparisons": len(comparison_family(findings))}


def counts_sentence(c: dict) -> str:
    return (f"{c['analyses']} analyses were run and {c['tested']} of them were tested for significance. {c['significant']} showed statistically significant patterns; "
            f"{c['reliable']} {'remains' if c['reliable'] == 1 else 'remain'} significant after correcting for {c['comparisons']} comparisons.")


# ------------------------------------------------------------------ one line per planned question
TREND_Q = re.compile(r"trend|over time|up or down|grow|declin|chang\w* over|rising|falling", re.I)
SEGMENT_Q = re.compile(r"segment|perform|best and worst|differ\w* (?:by|across)|compare|which (?:group|categor)", re.I)
EXPLAIN_Q = re.compile(r"explain|driv|why|cause|reason|what (?:affects|influences)", re.I)
KPI_Q = re.compile(r"\bKPIs?\b", re.I)
WORD = re.compile(r"[a-z0-9]+")


def _tokens(s: str) -> set[str]:
    return {w for w in WORD.findall(human(s)) if len(w) > 2}


def _line(f: dict) -> str:
    return f["insight"]["headline"] if f.get("insight") else f["title"]


def _rank(fs: list[dict]) -> list[dict]:
    return sorted(fs, key=lambda f: -(f.get("insight", {}).get("score", 0)))


def question_answers(state: dict, named_in_goal=None) -> list[tuple[str, str]]:
    """[(question, one-line answer)] for every planned question, from the findings, including 'no reliable difference' answers."""
    plan, goal = state["plan"], state.get("goal", "")
    target = plan.get("target")
    fs = [f for f in state["findings"] if f["tool"] not in ("robust_compare", "summary_table", "group_table", "distribution", "outliers", "category_share")]
    mine = [f for f in fs if f.get("params", {}).get("target") in (None, target)]
    out = []
    for q in plan.get("questions") or []:
        if TREND_Q.search(q):
            hit = [f for f in mine if f["tool"] == "trend"] + [f for f in mine if f["tool"] == "segment_trend"]
            line = "; ".join(_line(f) for f in hit[:2]) or "Not answered: no trend could be tested (no usable date or too few periods)."
        elif SEGMENT_Q.search(q):
            hit = [f for f in mine if f["tool"] == "group_compare"]
            if named_in_goal and not plan.get("explore"):
                hit.sort(key=lambda f: not named_in_goal(f["params"]["group"], goal))     # the segment the goal names comes first (sort is stable)
            line = "; ".join(_line(f) for f in hit[:3]) or "Not answered: no segment comparison could be run."
        elif KPI_Q.search(q):
            hit = [f for f in _rank(mine) if f["insight"].get("kpi_related")] or _rank(mine)
            line = _line(hit[0]) if hit else "Not answered: nothing was found for this."
        else:
            qt = _tokens(q)
            hit = _rank([f for f in mine if f.get("significant") is not None]) or _rank(mine)

            def shared(f):
                return len(qt & _tokens(f["title"] + " " + " ".join(str(v) for v in f.get("params", {}).values() if isinstance(v, str))))
            reliable = [f for f in hit if f.get("significant") and f["stats"].get("survives_correction", True)]
            related = sorted([f for f in hit if shared(f) > 0], key=lambda f: -shared(f))      # a result is only offered if it shares words with the question
            if EXPLAIN_Q.search(q) and reliable:
                line = _line(reliable[0])
            elif EXPLAIN_Q.search(q) and hit:
                line = f"No reliable driver was found; the closest result is: {_line(related[0] if related else hit[0])}"
            elif related:
                line = _line(related[0])
            else:
                line = "Not answered: no analysis in this run addresses it."
        out.append((q, line.rstrip(".") + "."))
    return out


# ------------------------------------------------------------------ LLM limitation statements
NEG_SIG = re.compile(r"\b(no|none of the|not any|zero)\b[^.]{0,40}\b(statistically )?(significant|reliable)\b|\bnothing (is )?(statistically )?significant\b|\bfailed to find (any )?(significant|reliable)", re.I)
POS_SIG = re.compile(r"\b(findings? (is|are) (all )?(statistically )?significant|all (findings|results|comparisons) (are|were) (statistically )?significant|strong(ly)? significant)\b", re.I)
SMALL_SAMPLE = re.compile(r"\b(small|tiny|limited|insufficient)\b[^.]{0,20}\b(sample|dataset|data set|number of (rows|records|observations))\b|\b(sample|dataset|data set)\b[^.]{0,20}\b(is|are) (too |very |quite )?(small|limited|tiny)\b|\btoo (few|little)\b[^.]{0,20}\b(rows|records|observations|data)\b", re.I)
MANY_MISSING = re.compile(r"\b(high|large|substantial|significant|considerable|many)\b[^.]{0,25}\b(missing|null|incomplete)\b", re.I)
NO_DATES = re.compile(r"\b(no|lack of|without|missing)\b[^.]{0,25}\b(date|time|temporal)\b", re.I)
NO_CORRECTION = re.compile(r"\b(no|without|not (been )?(applied|performed))\b[^.]{0,30}\b(multiple[- ](testing|comparison)|correction|bonferroni)\b|\bnot corrected\b", re.I)
MIN_ROWS_BIG = 1000


def contradicts(text: str, state: dict) -> str | None:
    """Why a limitation statement is contradicted by the findings, or None. Only claims the data can check are checked."""
    c = counts(state["findings"])
    prof, plan = state["profile"], state["plan"]
    if NEG_SIG.search(text) and c["reliable"] > 0:
        return f"says nothing is significant, but {c['reliable']} finding(s) are"
    if POS_SIG.search(text) and c["reliable"] < c["tested"]:
        return "says every finding is significant, but not all are"
    if SMALL_SAMPLE.search(text) and prof["rows"] >= MIN_ROWS_BIG:
        return f"says the sample is small, but there are {prof['rows']:,} rows"
    used = [x["missing_pct"] for x in prof["columns"] if x["name"] in (plan.get("relevant_columns") or [])]
    if MANY_MISSING.search(text) and used and max(used) < 10:
        return "says much data is missing, but no column used is more than 10% missing"
    if NO_DATES.search(text) and plan.get("date_col"):
        return "says there is no date, but a date column was used"
    if NO_CORRECTION.search(text) and c["comparisons"] >= 2:
        return f"says no multiple-comparison correction was made, but {c['comparisons']} comparisons were corrected"
    return None
