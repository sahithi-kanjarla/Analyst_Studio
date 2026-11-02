"""Insights agent: turns raw findings into well-described insights.

Every insight gets:
    headline            one sentence a busy reader can act on
    observation         what the data shows (numbers come from the analysis tools)
    why_it_matters      the business "so what", in the company's own vocabulary when context was given
    confidence          high / medium / low / exploratory / descriptive, with the reasons
    hypotheses          possible explanations, clearly labelled as things to TEST, not conclusions
    check_next          what to look at before acting
    kpi_related         True when the outcome is one of the KPIs the user named

All of that is deterministic. An optional LLM pass can rewrite why_it_matters / hypotheses / check_next with the company's context,
but every number in its text must already appear in the findings (see `numbers_ok`); otherwise its text is discarded.

Also produced here: key metrics (the numbers every report should open with) and data gaps (what extra data would sharpen the answers)."""
from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

from . import context as ctxmod
from .clarify import option_for
from . import polarity as polmod, understanding as und, wording
from .llm import ask_json
from .brief import kinds, kpi_columns, kpi_found
from .measures import ROWS_LABEL, has_sum_word
from .derived import ratio_of_sums
from .tools import _fmt, to_binary

FINANCIAL = re.compile(r"margin|profit|cost|cogs", re.I)
NUM_RE = re.compile(r"\d[\d,]*\.?\d*")


# ------------------------------------------------------------------ small helpers
def polarity(target: str, pol: dict | None = None) -> str:
    """'bad' when a HIGH value of the measure is undesirable (lower is better), 'good' when higher is better, 'neutral' when neither (ids, quantities).
    `pol` is the run's resolved {measure: higher|lower|neutral} map; without it the name heuristic decides."""
    return {polmod.LOWER: "bad", polmod.HIGHER: "good"}.get(polmod.direction(target, pol), "neutral")


def _pct(x) -> str:
    return f"{float(x):.0%}"


def effect_label(st: dict) -> str | None:
    e, name = st.get("effect"), st.get("effect_name")
    if e is None:
        return None
    if name == "eta_squared":
        x, cuts = e, (0.01, 0.06, 0.14)
    elif name == "cramers_v":
        x, cuts = e * e, (0.01, 0.06, 0.14)
    elif name == "rank_biserial":
        x, cuts = e, (0.1, 0.3, 0.5)
    elif name == "spearman_rho":
        x, cuts = e, (0.1, 0.3, 0.5)
    else:
        return None
    return "negligible" if x < cuts[0] else "small" if x < cuts[1] else "medium" if x < cuts[2] else "large"


def _numbers(text: str) -> list[float]:
    out = []
    for m in NUM_RE.finditer(str(text)):
        try:
            out.append(float(m.group(0).replace(",", "").rstrip(".")))
        except ValueError:
            pass
    return out


def allowed_numbers(*texts) -> list[float]:
    vals: list[float] = []
    for t in texts:
        vals += _numbers(t)
    extra = []
    for v in vals:  # a rate 0.689 may legitimately be quoted as 69%
        if abs(v) <= 1:
            extra.append(v * 100)
    return vals + extra


SUFFIX_RE = re.compile(r"\s?(Cr|crore|lakh|million|billion|thousand|bn|K|k|M|L)\b")
SUFFIX_SCALE = {"K": 1e3, "k": 1e3, "thousand": 1e3, "L": 1e5, "lakh": 1e5, "M": 1e6, "million": 1e6, "Cr": 1e7, "crore": 1e7, "bn": 1e9, "billion": 1e9}


def _matches(v: float, decimals: int, scale: float, allowed: list[float]) -> bool:
    """True when the written number `v` (with `decimals` shown and a K/M/L/Cr `scale`) is a rounding of an allowed value,
    read as written, as a percent of a fraction (0.385 = 38.5%) or as a fraction of a percent (38.5 = 0.385)."""
    half = 0.5 * 10 ** -decimals * scale + 1e-9
    target = v * scale
    for a in allowed:
        for c in (a, a * 100 if abs(a) <= 1 else a, a / 100 if abs(a) > 1 else a):
            if abs(target - c) <= half:
                return True
    return False


def unverified_numbers(text: str, allowed: list[float]) -> list[str]:
    """Numbers in LLM prose that match nothing we gave it. Accepted: a rounding to 0-2 decimals, fraction/percent, K/M/L/Cr suffixes,
    thousands separators, and years that appear in the data. Small integers get no free pass."""
    text, bad = str(text), []
    for m in NUM_RE.finditer(text):
        raw = m.group(0).replace(",", "").rstrip(".")
        try:
            v = float(raw)
        except ValueError:
            continue
        suf = SUFFIX_RE.match(text, m.end())
        scale = SUFFIX_SCALE[suf.group(1)] if suf else 1.0
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        if not _matches(v, decimals, scale, allowed):
            bad.append(m.group(0).rstrip(".,") + (suf.group(0) if suf else "") + ("%" if text[m.end():m.end() + 1] == "%" else ""))
    return bad


def numbers_ok(text: str, allowed: list[float]) -> bool:
    """Anti-hallucination guard for LLM prose: every number must come from the facts we gave it (see `unverified_numbers`)."""
    return not unverified_numbers(text, allowed)


# ------------------------------------------------------------------ why this chart
CHART_CHOICE = {
    "distribution": ("Histogram", "One numeric column: a histogram shows its shape, typical value and skew."),
    "outliers": ("Box plot", "One numeric column: a box plot makes unusual values visible."),
    "category_share": ("Bar chart", "One categorical column: bars show how often each value occurs."),
    "group_compare": ("Bar chart with spread", "A numeric or yes/no outcome across categories: bars compare groups and the error bars show uncertainty."),
    "outcome_compare": ("Box plot", "A numeric driver split by a yes/no outcome: box plots compare the two groups' distributions."),
    "robust_compare": ("Box plot of medians", "Same comparison as the bar chart, but resistant to extreme values."),
    "pareto": ("Pareto chart", "Which categories make up most of a total: bars plus a cumulative line."),
    "trend": ("Line chart", "A value over time: a line with a fitted trend shows direction."),
    "segment_trend": ("Multi-line chart", "A value over time for several groups: one line per group shows who diverges."),
    "seasonality": ("Bar chart by weekday / month", "A value over a repeating cycle: bars compare each weekday or month."),
    "correlation": ("Correlation heatmap", "Many numeric columns at once: a colour grid shows which move together."),
    "scatter": ("Scatter plot", "Two numeric columns: points show the shape of the relationship, a line shows the trend."),
    "heatmap": ("Heatmap", "A value across two categories at once: a colour grid shows combinations that stand out."),
    "composition": ("100% stacked bar", "How one category is made up across another: stacked shares make mixes comparable."),
    "summary_table": ("Table", "Exact figures for many columns are clearer in a table than in a chart."),
    "group_table": ("Table", "Exact per-group figures to quote and check."),
}


# ------------------------------------------------------------------ confidence
def confidence(f: dict) -> dict:
    st, tool, sig = f["stats"], f["tool"], f.get("significant")
    if tool in ("distribution", "outliers", "category_share", "pareto", "summary_table", "group_table"):
        return {"level": "descriptive", "label": "Descriptive", "reasons": ["Describes the data as it is; no statistical claim is made."]}
    if tool == "heatmap":
        return {"level": "exploratory", "label": "Exploratory", "reasons": ["Individual cells are not statistically tested; use them as leads to check.",
                                                                              f"Based on {st.get('n', 0):,} rows split into {st.get('cells', 0)} cells."]}
    if tool == "correlation":
        pairs = st.get("top_pairs") or {}
        r = max((abs(v) for v in pairs.values() if v is not None), default=0)
        lvl = "medium" if r >= 0.5 and st.get("n", 0) >= 100 else "low"
        return {"level": lvl, "label": lvl.capitalize(), "reasons": [f"Strongest correlation is r = {r:.2f}.", "Correlation does not show cause and effect."]}
    reasons: list[str] = []
    if sig is not True:
        return {"level": "low", "label": "Low", "reasons": ["The result is not statistically significant, so it could be random variation." if sig is False else "No significance test applies."]}
    survives = st.get("survives_correction", True)
    pts = 2
    if survives:
        pts += 1
        if "survives_correction" in st:
            reasons.append("Still significant after correcting for the number of comparisons made.")
    else:
        pts -= 1
        reasons.append("Significant on its own but NOT after correcting for multiple comparisons: treat as a lead.")
    lab = effect_label(st)
    if lab in ("medium", "large"):
        pts += 1
        reasons.append(f"{lab.capitalize()} effect size, so the difference is big enough to matter.")
    elif lab in ("small", "negligible"):
        reasons.append(f"Only a {lab} effect: detectable but may not matter in practice.")
    n = st.get("n", 0)
    pv = st.get("p")
    if pv is not None and 0.02 <= pv < 0.05:
        pts -= 1
        reasons.append(f"Only just significant (p = {pv:.3f}); a little more or less data could change the verdict.")
    if tool in ("trend", "segment_trend"):
        unit = {"D": "daily", "W": "weekly", "MS": "monthly"}.get(st.get("freq"), "")
        reasons.append(f"Based on {st.get('periods', 0)} {unit} data points ({n:,} rows).")
    elif n and n < 100:
        pts -= 1
        reasons.append(f"Small sample ({n} rows).")
    elif n:
        reasons.append(f"Based on {n:,} rows.")
    if st.get("min_group_n", 999) < 30:
        pts -= 1
        reasons.append(f"The smallest group has only {st['min_group_n']} rows.")
    if tool == "trend" and st.get("periods", 99) < 6:
        pts -= 1
        reasons.append(f"Only {st['periods']} time periods.")
    lvl = "high" if pts >= 4 else "medium" if pts >= 2 else "low"
    return {"level": lvl, "label": lvl.capitalize(), "reasons": reasons}


# ------------------------------------------------------------------ per-tool narrative
def _narrative(f: dict, ctx: dict, pol: dict | None = None, cols: list[str] | None = None) -> dict:
    lens = ctxmod.lens(ctx)
    cust, lever = lens["customer"], lens["levers"][0]
    tool, st, p, sig = f["tool"], f["stats"], f.get("params", {}), f.get("significant")
    h = why = ""
    hyp: list[str] = []
    nxt: list[str] = []

    if tool == "group_compare":
        target, group = p["target"], p["group"]
        top, bot, vals = st.get("best"), st.get("worst"), st.get("values", {})
        hv, lv = vals.get(top), vals.get(bot)
        pv = polarity(target, pol)
        bad, neutral = pv == "bad", pv == "neutral"
        lab = effect_label(st)              # the size wording comes from the test's effect size, never from the gap between the group means
        small = lab in ("small", "negligible")
        eff = f" ({lab} effect)" if sig and lab else ""
        note = f"; lower is better, so {bot} performs best" if bad and sig else ""
        if st.get("kind") == "binary":
            h = (f"{group} = {top} has the highest {target} rate ({_pct(hv)} of {ROWS_LABEL}) versus {_pct(lv)} for {bot}{eff}{note}" if sig
                 else f"The {target} rate does not differ reliably across {group}")
        else:
            ratio = hv / lv if (hv and lv and lv > 0 and hv > 0) else None
            cur = ctx.get("currency", "") if target in ctx.get("money", ()) else ""
            fig = "" if hv is None or lv is None else (f" (overall {cur}{_fmt(hv)} vs {cur}{_fmt(lv)} for {bot}, total over total)" if st.get("ratio_of_sums")
                                                       else f" (average {cur}{_fmt(hv)} per record vs {cur}{_fmt(lv)} for {bot})")
            h = (f"{group} = {top} averages {ratio:.1f}× the {target} of {bot}{fig}{eff}{note}" if sig and ratio and ratio >= 1.15
                 else f"{group} = {top} has the highest {target}{fig}, ahead of {bot}{eff}{note}" if sig else f"{target} does not differ reliably across {group}")
        ent_top, ent_bot = wording.entity(group, top), wording.entity(group, bot)
        what = wording.columns_phrase(cols, "the other columns")
        if sig and not small:
            why = (f"{target} is not spread evenly: the problem is concentrated in {ent_top}, so a one-size-fits-all programme would spend effort where it is not needed. Starting with {ent_top} gives the best chance of moving the overall number."
                   if bad else
                   f"{target} is highest in {top} and lowest in {bot}. Whether that is good or bad depends on what {target} means for the business, so read it as a pattern to explain rather than a performance ranking."
                   if neutral else
                   f"{top} is where {target} is strongest and {bot} where it is weakest. That points to putting more {lever} behind what already works, and finding out what is holding {bot} back.")
        elif sig:
            why = f"The gap between {top} and {bot} is real but small, so on its own it is unlikely to justify a change."
        else:
            why = f"{group} does not look like a useful way to split {target} here; there is no evidence yet for treating these groups differently."
        hyp = [f"{ent_top} may differ in mix, for example in {what}", f"Another column could be driving both {group} and {target} (a confounder)",
               f"{ent_bot} may be handled differently from {ent_top}"] if sig else \
              ["The groups may genuinely behave the same", "The sample may be too small to show a real but modest difference"]
        nxt = ([f"Check whether the {top} vs {bot} gap holds inside other splits (e.g. region, product) before acting",
                "Pilot any change on part of the group, with the rest as a control"] if sig and not small else
               ["Gather more data or a longer period before drawing conclusions"])
    elif tool == "outcome_compare":
        target, col, pos = p["target"], p["col"], st.get("positive", "yes")
        dirn = st.get("direction", "higher")
        h = (f"{col} is {dirn} where {target} = {pos} (median {col}: {_fmt(st['median_outcome'])} vs {_fmt(st['median_other'])} for the rest)" if sig
             else f"{col} does not differ reliably between rows with and without {target} = {pos}")
        bad = polarity(target, pol) == "bad"
        why = (f"{col} behaves differently for {cust} with {target} = {pos}, which makes it a candidate early-warning signal: watching it could flag at-risk {cust} before the outcome happens."
               if sig and bad else
               f"{col} moves with {target} = {pos}, so it may help identify which {cust} are most likely to reach that outcome." if sig else
               f"{col} does not help tell {cust} with and without {target} = {pos} apart.")
        hyp = [f"{col} may be a symptom of the outcome rather than a cause", "A third factor (plan, segment, season) could drive both"] if sig else ["The two may be unrelated"]
        nxt = [f"Pick a threshold on {col} and measure how well it flags {target} = {pos} (how many flags are right, how many are missed)",
               "Test an intervention on flagged rows against a control group"] if sig else ["Try other drivers or more data"]
    elif tool == "trend":
        target = p["target"]
        up = (st.get("slope") or 0) > 0
        rel = st.get("relative_change") or 0
        pv = polarity(target, pol)
        good = up != (pv == "bad")
        verdict = "a favourable" if good else "a worrying"
        move = wording.trend_size(st, target, {}, signed=True, fmt=_fmt) if wording.trend_falls_steeply(st) else f"{rel:+.0%}"
        h = (f"{target} is trending {'up' if up else 'down'} ({move} over the period)" if sig else f"No reliable trend in {target} over time")
        why = ((f"This is {verdict} movement that is unlikely to be random: it deserves an owner and an explanation rather than a wait-and-see." if pv != "neutral"
                else f"This movement is unlikely to be random. Whether it is good or bad depends on what {target} means for the business, but it deserves an owner and an explanation.") if sig
               else f"{target} has been broadly flat or too noisy to call a direction, so changes in it should not be read as trends yet.")
        hyp = ["Seasonality or a one-off event (campaign, price change, outage)", "A change in the mix of segments or products over time",
               "Partial first or last periods can exaggerate or hide the direction"]
        nxt = ["Look at the same periods in earlier years if available", "Break the trend down by segment to see where it comes from"]
    elif tool == "segment_trend":
        target, group = p["target"], p["group"]
        fast, slow = st["fastest"], st["slowest"]
        g = st.get("groups", {})
        h = wording.trend_headline(st, group, target, _fmt)
        why = wording.trend_why(st, group, target)
        what = wording.columns_phrase(cols, "the business or how the data is recorded")
        mixed = wording.trend_shape(st) == "mixed"
        hyp = [f"{wording.entity(group, fast)} may be changing because of a recent change in {what}"] + ([f"{wording.entity(group, slow)} may have a seasonal pattern or a shift in its mix"] if mixed else [])
        nxt = [f"Investigate what changed for {fast}" + (f" and {slow}" if mixed else "") + " over the period"]
    elif tool == "pareto":
        target, group = p["target"], p["group"]
        k, share = st["top_k"], st["top_share"]
        h = f"The top {k} {group} values account for {_pct(share)} of total {target}"
        why = (f"Results lean heavily on a few {group} values: that is both where to protect and a concentration risk if one of them slips." if share > 0.5
               else f"{target} is spread fairly evenly across {group}, so there is no single dominant source.")
        hyp = ["The long tail may be under-served rather than genuinely low-value", "Concentration could be seasonal or driven by a few large deals"]
        nxt = [f"See whether the top {k} are growing or shrinking over time", "Look for what the best performers have in common"]
    elif tool == "heatmap":
        target = p["target"]
        (a1, b1), (a2, b2) = st["top_cell"], st["bottom_cell"]
        cur = ctx.get("currency", "") if target in ctx.get("money", ()) else ""
        fmtv = (lambda v: f"{_pct(v)} of {ROWS_LABEL}") if st.get("target_kind") == "binary" else (lambda v: f"average {cur}{_fmt(v)} per record")
        h = f"{a1} × {b1} stands out ({fmtv(st['top_value'])}); {a2} × {b2} is weakest ({fmtv(st['bottom_value'])})"
        why = "Segments can behave differently in combination, and single-column averages hide it. This shows where to look first, not what to do."
        hyp = ["A combination may reflect a specific product, channel or customer type", "Small cells can swing widely by chance"]
        nxt = ["Check the best and worst combinations with a proper test before building a plan around them"]
    elif tool == "correlation":
        pairs = st.get("top_pairs") or {}
        key, r = max(pairs.items(), key=lambda kv: abs(kv[1] or 0), default=(None, 0))
        if key and abs(r or 0) >= 0.3:
            a, b = key.split("|")
            h = f"{a} and {b} move together (r = {r:+.2f})"
            why = f"If {a} is something the business can influence, it may be a lever for {b}; if not, it may still be an early indicator. Correlation alone does not say which."
        else:
            h = "No strong relationships between the numeric columns"
            why = "None of the numeric columns tracks another closely, so no obvious single lever or shortcut indicator shows up."
        hyp = ["A hidden common cause may drive both", "The relationship may only hold in some segments"]
        nxt = ["Test whether changing one actually moves the other (a controlled pilot) before treating it as a lever"]
    elif tool == "scatter":
        x, y = st["x"], st["y"]
        rho = st.get("rho") or 0
        lab = effect_label(st)
        h = (f"{y} {'rises' if rho > 0 else 'falls'} as {x} rises (rho = {rho:+.2f})" if sig and lab in ("medium", "large")
             else f"{x} and {y} are related, but only weakly (rho = {rho:+.2f})" if sig else f"No reliable relationship between {x} and {y}")
        why = (f"If {x} is something the business can change, it may be a lever for {y}; if not, it can still serve as an early indicator. The data alone cannot say which." if sig and lab in ("medium", "large")
               else f"{x} explains only a small part of the variation in {y}, so it is not a dependable predictor or lever on its own." if sig
               else f"Knowing {x} tells you little about {y} in this data.")
        hyp = ["A hidden third factor may drive both", "The relationship may hold only for part of the data (a segment or a range)"] if sig else ["The two may be genuinely unrelated"]
        nxt = [f"Check whether the pattern holds inside segments, and look at unusual points far from the line", f"Run a controlled change in {x} to see whether {y} moves"] if sig and lab in ("medium", "large") else ["Look for other columns that explain more of the variation"]
    elif tool == "composition":
        group, by = st["group"], st["by"]
        g, b, diff = st["biggest"]
        h = (f"The {by} mix depends on {group}: {g} has {abs(diff):.0%} {'more' if diff > 0 else 'less'} {b} than average" if sig else f"The {by} mix is about the same across {group}")
        why = (f"{group} groups buy or behave differently in terms of {by}, so one approach for all of them will fit some groups badly." if sig else f"{group} does not change the {by} mix, so there is no reason to treat the groups differently on this.")
        hyp = [f"{g} may be served through different channels, products or pricing", "Group sizes differ, so small groups can look more extreme"] if sig else []
        nxt = [f"Check the {g} / {b} difference against a business explanation before acting"] if sig else []
    elif tool == "seasonality":
        target, cyc = p["target"], st.get("cycle", "weekday")
        unit = "day of the week" if cyc == "weekday" else "month of the year"
        small = effect_label(st) in ("small", "negligible")
        h = (f"{target} has a clear {cyc} pattern: highest {st['best']}, lowest {st['worst']}" if sig and not small
             else f"{target} varies slightly by {unit} ({st['best']} highest), a small effect" if sig else f"No reliable {cyc} pattern in {target}")
        pv = polarity(target, pol)
        hi_w, lo_w = ("high", "low") if pv == "neutral" else ("strong", "weak") if pv == "good" else ("weak", "strong")
        why = (f"The timing is predictable, so staffing, stock and promotions can be planned around {st['best']} ({hi_w}) and {st['worst']} ({lo_w})." if sig and not small
               else "The pattern is real but small, so it should not drive scheduling decisions on its own." if sig else f"{target} does not depend on the {unit}, so there is no timing pattern to plan around.")
        hyp = ["Customer routines (work week, paydays, holidays)", "Operating hours, shifts or campaigns that run on a fixed calendar"] if sig else []
        nxt = ["Check whether the pattern is the same in every month or year", "Compare with promotions and holidays in the same periods"] if sig else []
    elif tool == "summary_table":
        h = f"Summary of {st.get('columns', 0)} numeric columns"
        why = "A quick reference for typical values, spread and missing data before reading the charts."
        hyp, nxt = [], ["Look for columns where the maximum is far above the median, or where many values are missing"]
    elif tool == "group_table":
        h = f["title"].replace(" (table)", "") + ": per-group figures"
        why = "The exact numbers behind the group charts, so you can quote or check them."
        hyp, nxt = [], ["Check the Rows column: groups with few rows give unreliable averages"]
    elif tool == "robust_compare":
        h = ("The group difference holds up with a median-based test" if sig else "The group difference does NOT hold up with a median-based test")
        why = ("This makes the result more trustworthy: it is not just a few extreme values pulling an average." if sig
               else "The apparent difference may be driven by a handful of extreme values, so be cautious about acting on it.")
        hyp, nxt = ["Extreme values may be real big accounts or data errors"], ["Check the largest values individually"]
    elif tool == "distribution":
        col = p["col"]
        h = f"{col}: typical value {_fmt(st['median'])}, with half of rows between {_fmt(st.get('q1', 0))} and {_fmt(st.get('q3', 0))}"
        skew = abs(st.get("skew") or 0) > 1
        why = (f"{col} is skewed by a few very large values, so the average overstates the typical case: use the median for planning." if skew
               else f"{col} is fairly evenly spread around its typical value, so averages are a fair summary.")
        hyp, nxt = [], ["Check whether the top end is a few big customers or data errors"] if skew else []
    elif tool == "outliers":
        col = p["col"]
        h = f"{st['outliers']} unusual values in {col} ({_pct(st['outlier_pct'])} of rows)"
        why = f"Extreme values can distort averages. They are either errors to fix or your biggest cases to understand; the data alone cannot say which."
        hyp, nxt = ["Data entry errors", "Genuine large accounts or one-off events"], [f"Review the largest values of {col} individually"]
    elif tool == "category_share":
        col = p["col"]
        top = next(iter(st.get("top", {})), "")
        h = f"{col}: {st.get('distinct', 0)} distinct values, led by '{top}'"
        why = f"Shows how {col} is composed, which sets the context for comparing groups: small groups give noisy averages."
        hyp, nxt = [], []
    else:
        h, why = f["title"], ""
    return {"headline": h, "why_it_matters": why, "hypotheses": hyp[:3], "check_next": nxt[:3]}


def build_insights(state: dict) -> None:
    """Adds f['insight'] to every finding (in place)."""
    ctx = {**(state.get("context") or {}), "money": und.money_columns(state.get("understanding"))}
    brief = state.get("brief")
    kt, kcols = ctxmod.kpi_tokens(ctx, (brief or {}).get("kpis")), kpi_columns(brief)
    for f in state["findings"]:
        n = _narrative(f, ctx, state.get("polarity"), wording.other_columns(state, f.get("params", {}).get("group") or ""))
        conf = confidence(f)
        target = f.get("params", {}).get("target") or f.get("params", {}).get("col") or ""
        name_tokens = ctxmod.tokens(re.sub(r"([a-z])([A-Z])", r"\1 \2", target).replace("_", " "))
        kpi = bool(kt & name_tokens) or target in kcols
        st = f["stats"]
        eff = {"large": 2, "medium": 1.2, "small": 0.4}.get(effect_label(st) or "", 0.5 if f["tool"] in ("trend", "pareto", "segment_trend", "seasonality") else 0)
        score = ((3 if st.get("survives_correction", True) and f.get("significant") else 2 if f.get("significant") else 0)
                 + eff + (1 if kpi else 0) + (1 if f.get("action") else 0) - (0.5 if conf["level"] == "descriptive" else 0) - (1 if f["tool"] in ("summary_table", "group_table") else 0) - (2 if f["tool"] == "robust_compare" else 0))
        f["chart_type"], f["chart_why"] = CHART_CHOICE.get(f["tool"], ("Chart", ""))
        f["insight"] = {**n, "observation": f["text"], "confidence": conf, "kpi_related": kpi, "score": round(score, 2), "source": "rules"}


# ------------------------------------------------------------------ optional LLM enrichment (grounded)
SENT_FINDINGS, SENT_CHARS = 8, 260          # what is sent to the model per run: the leading findings, each cut to its first sentences
ENRICH_SYSTEM = """You are a senior business analyst explaining analysis results to the company's decision makers.
For each insight, write fields that help them understand and act, using the company context when given.
Rules: use ONLY the facts provided; never introduce a number that is not in the facts; do not claim cause and effect (use 'may', 'could', 'worth testing');
write plain business English; why_it_matters is 1-2 sentences; possible_explanations are up to 3 short hypotheses to test; check_next up to 2 short actions.
Return JSON: {"insights": [{"id": "...", "why_it_matters": "...", "possible_explanations": ["..."], "check_next": ["..."]}]}"""


def enrich_with_llm(llm, state: dict) -> int:
    """Returns how many insights were improved. Anything that fails validation is silently kept in its rule-based form."""
    if llm is None:
        return 0
    findings = [f for f in state["findings"] if f["tool"] != "robust_compare"][:SENT_FINDINGS]
    ctx_txt = ctxmod.context_text(state.get("context"))
    payload = [{"id": f["id"], "headline": f["insight"]["headline"], "facts": f["text"].replace("**", "")[:SENT_CHARS], "confidence": f["insight"]["confidence"]["label"]} for f in findings]
    out = ask_json(llm, ENRICH_SYSTEM, (f"COMPANY CONTEXT (user-written, unverified):\n{ctx_txt}\n\n" if ctx_txt else "") + f"GOAL: {state['goal']}\nINSIGHTS:\n{json.dumps(payload, default=str, ensure_ascii=False)}")
    if not out or not isinstance(out.get("insights"), list):
        return 0
    by_id = {f["id"]: f for f in findings}
    improved = 0
    for item in out["insights"]:
        f = by_id.get(item.get("id")) if isinstance(item, dict) else None
        if not f:
            continue
        allowed = allowed_numbers(f["text"], f["title"], json.dumps(f["stats"], default=str, ensure_ascii=False), ctx_txt)
        ins, changed = f["insight"], False
        why = item.get("why_it_matters")
        if isinstance(why, str) and 20 <= len(why) <= 500 and numbers_ok(why, allowed) and not wording.unsupported_profit_claim(why, state):
            ins["why_it_matters"], changed = why.strip(), True
        hyp = [h.strip() for h in (item.get("possible_explanations") or []) if isinstance(h, str) and 8 <= len(h) <= 220 and numbers_ok(h, allowed) and not wording.unsupported_profit_claim(h, state)][:3]
        if hyp:
            ins["hypotheses"], changed = hyp, True
        chk = [c.strip() for c in (item.get("check_next") or []) if isinstance(c, str) and 8 <= len(c) <= 220 and numbers_ok(c, allowed) and not wording.unsupported_profit_claim(c, state)][:2]
        if chk:
            ins["check_next"], changed = chk, True
        if changed:
            ins["source"] = "llm"
            improved += 1
    return improved


# ------------------------------------------------------------------ key metrics
def key_metrics(state: dict, df: pd.DataFrame) -> list[dict]:
    ctx, plan, prof = state.get("context") or {}, state["plan"], state["profile"]
    cur = ctx.get("currency", "")
    m = [{"label": "Rows analysed", "value": f"{prof['rows']:,}", "note": f"{len(plan['relevant_columns'])} of {prof['cols']} columns used"}]
    t = plan.get("target")
    dspec = next((d for d in plan.get("derived") or [] if d["name"] == t), None)
    if dspec is not None and t in df.columns:      # a ratio is total A over total B, shown as % or a plain number, never with a currency symbol
        r = ratio_of_sums(df, dspec)
        if r is not None:
            m.append({"label": f"{t} (total {dspec['a']} over total {dspec['b']})", "value": f"{r:.1%}" if dspec.get("part_of") else _fmt(r), "note": ""})
    elif t and t in df.columns:
        s = df[t]
        b, pos = to_binary(s) if s.dropna().nunique() == 2 else (None, None)
        if b is not None:
            m.append({"label": f"{t} rate ('{pos}')", "value": f"{b.mean():.1%}", "note": f"{int(b.sum()):,} of {int(b.notna().sum()):,} rows"})
        else:
            x = pd.to_numeric(s, errors="coerce").dropna()
            if len(x):
                c = cur if t in und.money_columns(state.get("understanding")) else ""
                if has_sum_word(t, overrides=kinds(state.get("brief"))):
                    m.append({"label": f"Total {t}", "value": f"{c}{_fmt(x.sum())}", "note": ""})
                m.append({"label": f"Average {t}", "value": f"{c}{_fmt(x.mean())}", "note": f"median {c}{_fmt(x.median())}"})
    d = plan.get("date_col")
    if d and d in df.columns and pd.api.types.is_datetime64_any_dtype(df[d]) and df[d].notna().any():
        m.append({"label": "Period covered", "value": f"{df[d].min():%d %b %Y} – {df[d].max():%d %b %Y}", "note": f"{(df[d].max() - df[d].min()).days:,} days"})
    used = [c for c in prof["columns"] if c["name"] in plan["relevant_columns"]]
    if used:
        m.append({"label": "Data completeness", "value": f"{100 - float(np.mean([c['missing_pct'] for c in used])):.1f}%", "note": "of values present in the columns used, before cleaning"})
    c = wording.counts(state["findings"])
    if c["tested"]:
        m.append({"label": "Reliable findings", "value": f"{c['reliable']} of {c['tested']}", "note": f"significant after correcting for {c['comparisons']} comparisons"})
    return m


# ------------------------------------------------------------------ data gaps
DATE_CAUTION = "Dates may be swapped, so trend and month findings below are unreliable until you answer this."
DATE_TAG = "date-dependent"


COVERAGE_MIN = 0.70


def coverage_warning(dates) -> str:
    """'' or the warning when under 70% of the calendar days between the first and last date have a row."""
    d = pd.to_datetime(pd.Series(dates), errors="coerce").dropna()
    if d.empty:
        return ""
    days = int(d.dt.normalize().nunique())
    span = (d.max().normalize() - d.min().normalize()).days + 1
    return (f"Only {days} of {span} days in this period have data, so trends and weekly or monthly comparisons may mislead. "
            "Totals and shares cover only the days present.") if days / span < COVERAGE_MIN else ""


def coverage_fraction(dates) -> float | None:
    """Share of the calendar days between the first and last date that have a row; None without dates."""
    d = pd.to_datetime(pd.Series(dates), errors="coerce").dropna()
    if d.empty:
        return None
    return int(d.dt.normalize().nunique()) / ((d.max().normalize() - d.min().normalize()).days + 1)


def dates_unconfirmed(state: dict) -> bool:
    """True when a day/month swap question was asked and was not answered with the swap."""
    answers = state.get("answers") or {}
    return any(option_for(q, answers.get(q["id"]))["action"]["type"] != "swap_day_month" for q in state.get("questions") or [] if q.get("kind") == "date_swap")


def mapped_roles(brief: dict | None) -> str:
    """Words for every role the brief already has a column for: the roles of its columns (date, measure...) and the KPI names it found a column for."""
    b = brief or {}
    return " ".join([r for r in (b.get("roles") or {}).values() if r not in ("id", "text")] + [k for k, c in (b.get("kpis") or {}).items() if c]).lower()


def kpi_served(state: dict, kpi: str) -> bool:
    """True when a goal term that shares a word with the KPI is mapped (data understanding record) to a column the plan uses: the plan's target, a derived measure's parts or a relevant column.
    The KPI is then measured, under another column name, and is not 'not found'."""
    rec, plan = state.get("understanding"), state.get("plan") or {}
    used = {plan.get("target"), *(plan.get("relevant_columns") or [])} | {c for d in plan.get("derived") or [] for c in (d["name"], d["a"], d["b"])}
    kt = ctxmod.kpi_tokens({"kpis": [kpi]})
    if not kt or not rec or rec.get("source") != "llm":
        return False
    return any(kt & ctxmod.tokens(term) and set(cols) & used for term, cols in (rec.get("goal_columns") or {}).items())


def data_gaps(state: dict) -> list[dict]:
    ctx, plan, prof = state.get("context") or {}, state["plan"], state["profile"]
    names = [c["name"] for c in prof["columns"]]
    blob = " ".join(names).lower()
    have = mapped_roles(state.get("brief"))      # a kind of data the brief's role map has a column for is never reported missing
    gaps: list[dict] = []
    for k in ctx.get("kpis", []):
        if kpi_found(state.get("brief"), k) or any(d.get("kpi") == k for d in (state.get("derived_metrics") or {}).get("created", [])) or kpi_served(state, k):
            continue
        kt = ctxmod.kpi_tokens({"kpis": [k]})
        if kt and not any(kt & ctxmod.tokens(re.sub(r"([a-z])([A-Z])", r"\1 \2", n).replace("_", " ")) for n in names):
            gaps.append({"severity": "warn", "title": f"KPI '{k}' was not found in the data",
                         "detail": "You named it as important, but no column looks like it, so nothing here measures it directly.",
                         "suggestion": f"Add a column or file that contains {k}."})
    if not plan.get("date_col") and "date" not in have:
        gaps.append({"severity": "info", "title": "No date column", "detail": "Without dates, trends, seasonality and before/after effects cannot be measured.",
                     "suggestion": "Add a date or period column (order date, signup date, month)."})
    for q in state.get("questions") or []:
        if q.get("kind") == "date_swap" and option_for(q, (state.get("answers") or {}).get(q["id"]))["action"]["type"] == "note_only":
            gaps.append({"severity": "warn", "title": "Dates may have day and month swapped", "detail": f"The dates in {', '.join(q['columns'])} take very few different days or months over a long period, and they were used as they are, so trends and seasonality may be wrong.",
                         "suggestion": "Check how the dates are written in the source and re-run with day and month swapped if needed."})
    for g in (state.get("concerns") or {}).get("gaps") or []:        # concerns the AI raised that no action can fix yet
        gaps.append({k: g[k] for k in ("severity", "title", "detail", "suggestion")})
    if prof["rows"] < 200:
        gaps.append({"severity": "warn", "title": f"Small dataset ({prof['rows']:,} rows)", "detail": "With few rows only large differences can be detected reliably.",
                     "suggestion": "Add more history or more records before making expensive decisions."})
    if plan.get("target"):
        miss = next((c["missing_pct"] for c in prof["columns"] if c["name"] == plan["target"]), 0)
        if miss > 10:
            gaps.append({"severity": "warn", "title": f"'{plan['target']}' is {miss:.0f}% missing", "detail": "Results describe only the rows that have a value.",
                         "suggestion": f"Fix the source so {plan['target']} is recorded consistently."})
    else:
        gaps.append({"severity": "info", "title": "No clear outcome column", "detail": "No numeric or yes/no column stood out as the result to explain, so the analysis was descriptive.",
                     "suggestion": "Tell us the KPI to explain (in the goal or the business context)."})
    if not any(c["role"] in ("categorical", "binary") for c in prof["columns"] if c["name"] in plan["relevant_columns"]):
        gaps.append({"severity": "info", "title": "No segment columns used", "detail": "Without categories (region, product, plan...) results cannot be broken down by segment.",
                     "suggestion": "Add descriptive columns such as segment, region, channel or product."})
    derived_names = " ".join(f"{d['name']} {d['a']} {d['b']}" for d in plan.get("derived") or []).lower()
    rec = state.get("understanding")
    asks_profit = bool(wording.PROFIT_CLAIM.search(str(state.get("goal") or "")))      # the user's own goal talks about margin or profit
    no_cost = not wording.has_profit_measure(state) and not wording.FINANCIAL.search(have) and not und.cost_columns(rec) and bool(und.money_columns(rec) or asks_profit)
    if no_cost:      # a money measure to report, and nothing that says what it cost: said once, in these words, instead of the industry's own "no cost data" line
        gaps.append({"severity": "info", "title": "Margin: not computable, no cost column", "detail": "Nothing in the data records what the sales cost, so only figures on the revenue side are reported.",
                     "suggestion": "Add a cost column (cost per unit or per line) to the data."})
    for rx, what in ctxmod.lens(ctx)["data_wants"]:
        if no_cost and re.search(r"margin|cost", rx):
            continue
        if re.search(rx, have):
            continue
        # a profit or margin measure (a column, or a ratio the analysis derived) answers a want for cost or margin data, and the other way round
        if re.search(rx, blob + " " + derived_names) or (re.search(r"margin|cost", rx) and FINANCIAL.search(blob + " " + derived_names)):
            continue
        gaps.append({"severity": "info", "title": f"No {what} data", "detail": f"Typical for {ctxmod.lens(ctx)['label'].lower()} analysis, and it would let the agents test more explanations.",
                     "suggestion": f"Consider adding {what}."})
    return gaps[:8]
