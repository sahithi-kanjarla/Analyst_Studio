"""Answer the goal directly. When the goal asks whether something is growing (or falling, or changing over time), the report opens with one sentence
that states the result, its size and its significance, taken from the trend analysis, and the dashboard shows the per-group trend next to it."""
from __future__ import annotations

import re

from . import polarity as polmod, wording
from .tools import _fmt

CHANGE_RX = re.compile(r"\b(grow\w*|increas\w*|declin\w*|decreas\w*|trend\w*|rising|rise|rises|falling|fall|falls|shrink\w*|over time|improv\w*|expand\w*|slump\w*)\b", re.I)


def asks_about_change(goal: str, explore: bool = False, intent: str | None = None) -> bool:
    """The brief's intent decides when there is one; the keyword match is the fallback."""
    if explore or not goal:
        return False
    return intent == "change" if intent else bool(CHANGE_RX.search(goal))


def _p(p) -> str:
    return "p<0.001" if p is not None and p < 0.001 else f"p={p:.2f}" if p is not None else "p unknown"


def answer(state: dict) -> dict | None:
    """{"sentence", "groups_sentence", "finding_ids"} or None when the goal does not ask about change."""
    plan = state.get("plan") or {}
    if not asks_about_change(state.get("goal", ""), bool(plan.get("explore")), (state.get("brief") or {}).get("intent")):
        return None
    target = plan.get("target")
    findings = state.get("findings") or []
    tr = next((f for f in findings if f["tool"] == "trend" and f.get("params", {}).get("target") == target), None)
    seg = next((f for f in findings if f["tool"] == "segment_trend" and f.get("params", {}).get("target") == target), None)
    if not target or tr is None:
        why = "there is no date column" if not plan.get("date_col") else "there are too few time periods or rows to test a trend"
        return {"sentence": f"The goal asks about change over time, but it cannot be answered from this data: {why}.", "groups_sentence": "", "finding_ids": [f["id"] for f in (seg,) if f]}
    st = tr["stats"]
    up, sig = (st.get("slope") or 0) > 0, bool(tr.get("significant"))
    unit = {"D": "day", "W": "week", "MS": "month"}.get(st.get("freq"), "period")
    basis = f"{st.get('periods', 0)} {unit}s"
    d = polmod.direction(target, state.get("polarity"))
    verdict = "" if d == polmod.NEUTRAL else f" This is {'favourable' if (d == polmod.HIGHER) == up else 'worrying'}, as {'higher' if d == polmod.HIGHER else 'lower'} is better for {target}."
    fell = wording.trend_falls_steeply(st)
    if sig and up:
        sentence = f"Yes, {target} is growing: it rose about {wording.trend_size(st, target, state, fmt=_fmt)} over the period, a statistically significant upward trend ({_p(st.get('p'))}, {basis}).{verdict}"
    elif sig:
        sentence = f"No, {target} is falling: it {'is ' if fell else 'fell about '}{wording.trend_size(st, target, state, fmt=_fmt)} over the period, a statistically significant downward trend ({_p(st.get('p'))}, {basis}).{verdict}"
    else:
        sentence = f"There is no reliable growth trend in {target}: it {'is ' if fell else 'changed about '}{wording.trend_size(st, target, state, signed=True, fmt=_fmt)} over the period, which is not statistically significant ({_p(st.get('p'))}, {basis})."
    groups = ""
    if seg is not None:
        g = seg["stats"]
        gp = seg["params"]["group"]
        if g.get("survives_correction"):
            groups = f"By {gp}, {wording.trend_headline(g, gp, target, _fmt)}, significant after correcting for {g.get('total_groups', len(g['groups']))} groups."
        else:
            groups = f"By {gp}, no group shows a reliable trend after correcting for {g.get('total_groups', len(g['groups']))} groups."
    return {"sentence": sentence, "groups_sentence": groups, "finding_ids": [f["id"] for f in (tr, seg) if f]}
