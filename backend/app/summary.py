"""Executive-summary logic: which finding answers which goal question, the group rankings that answer "which X is most Y", the headlines and the key numbers.

  * ranking facts: for every goal-named or hierarchy group column and every KPI, the top and bottom group by total (additive KPI, with the top group's share
    of the total) or by ratio of sums (ratio KPI, naming loss-making groups). Built from the cleaned data, so they exist even when no difference was significant
  * growth facts: with a date column spanning two or more full periods, every group's total in the first full period against the last, as an absolute and a
    % change, every group listed, each marked statistically reliable or not
  * answers: the LLM sees the questions and a closed list of finding ids and returns the ids that answer each one (or "none"); ids not in the list are rejected.
    Without an LLM, or when its answer is unusable, the same choice is made by matching the question's words to a finding's measure and group column
  * the text of an answer is always the text of the chosen findings, so every number in it was computed by the tools
Nothing here knows a column name or an industry."""
from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd
from scipy import stats

from . import derived, wording
from . import understanding as und
from .brief import hierarchy_pairs, kinds
from .clarify import named_in_goal
from .llm import ask_json, capture, note_failure
from .measures import is_money, measure_kind
from .retail_roles import is_quantity
from .tools import _fmt

HEADLINES_MAX = 5
MIN_GROUP_ROWS = 5            # a group with fewer rows is too small to be called the top or the bottom
MAX_GROUPS = 50
MAX_ALL_GROUPS = 30           # the main KPI is ranked by every category column up to this many groups, not only the ones the goal names
MAX_RANK_GROUPS = 500         # ... and up to this many, with only the top few groups written out
TOP_SHOWN = 5
DOMINANT_VALUE_SHARE = 0.8    # a category column whose main value holds at least this share of the rows gets a "rate outside the main value" fact
OUTSIDE_SHOWN = 3
MAX_OUTSIDE_COLUMNS = 3
DOMINANT_SHARE = 0.4          # a group holding more of the total than this is called dominant
MAX_ADDITIVE = 8
MIN_SUB_PERIODS = 3           # a change between two periods is only tested when each period holds this many sub-periods (months, weeks)
GROWTH_UNITS = (("Y", "M", "month"), ("Q", "M", "month"), ("M", "W", "week"))     # (period freq, sub-period freq, sub-period word): the longest unit with 2+ full periods wins
PICKS_MAX = 2
NOT_A_FINDING = ("robust_compare", "summary_table", "group_table", "distribution", "outliers", "category_share")
TIME_TOOLS = ("trend", "segment_trend", "seasonality")
GROUP_PARAMS = ("group", "group_a", "group_b", "by")
TIME_Q = re.compile(r"\b(?:month|year|week|quarter|day|time|trend|season)\w*", re.I)
ACTION_Q = re.compile(r"what (?:should|next|to do|do we do)|should we|next steps?|recommend|\bactions?\b|do next", re.I)
MAX_NAMED = 4                 # a question names at most this many columns
MAIN_PICKS = 3                # a question naming several columns is answered with the top few groups of each ranking
VALUES_MAX = 30
MIN_VALUE_LEN = 4
TRIVIAL_GROUPS = 3            # a column with this many groups or fewer, whose top group holds more than TRIVIAL_SHARE, is not a headline unless the goal names it
TRIVIAL_SHARE = 0.9
GENERIC = {"the", "and", "for", "which", "what", "how", "most", "are", "our", "does", "with", "that", "this", "from", "have", "has", "its", "each", "all", "best", "worst", "top", "generate", "generates", "earn", "earns"}


# ------------------------------------------------------------------ small helpers
def _stem(w: str) -> str:
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    return w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w


def tokens(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", wording.human(text)) if len(w) > 2 and w not in GENERIC}


def mentions(col: str, text: str) -> bool:
    """All of the column's words (singular or plural) appear in the text."""
    ws = tokens(col)
    return bool(ws) and ws <= tokens(text)


def _same(a: str, b: str) -> bool:
    """Two stemmed words are the same word: equal, or sharing their first five letters (return/returned, cancel/cancelled)."""
    return a == b or (min(len(a), len(b)) >= 5 and a[:5] == b[:5])


def names(col: str, text: str) -> int:
    """2: every word of the column appears in the text (plural or singular); 1: the column's last word does ('states' for ship-state); 0: neither."""
    ws = [_stem(w) for w in re.findall(r"[a-z0-9]+", wording.human(col)) if len(w) > 2 and w not in GENERIC]
    qt = tokens(text)
    if not ws:
        return 0
    hit = [any(_same(w, t) for t in qt) for w in ws]
    return 2 if all(hit) else 1 if hit[-1] else 0


def full(x: float, cur: str = "") -> str:
    """A total written out in full (2,635,797); small values keep their significant digits."""
    return ("-" if x < 0 else "") + cur + (f"{abs(x):,.0f}" if abs(x) >= 1000 else _fmt(abs(x)))


def short(x: float, cur: str = "") -> str:
    """A figure rounded for reading (7.5M, 12K, 2M); exact figures stay in the details."""
    a = abs(x)
    units = ((1e3, "K"), (1e6, "M"), (1e9, "B"))
    k = max((i for i, (lim, _) in enumerate(units) if a >= lim), default=-1)
    if k >= 0 and k + 1 < len(units) and round(a / units[k][0], 1) >= 1000:
        k += 1
    if k >= 0:
        return ("-" if x < 0 else "") + cur + f"{a / units[k][0]:.1f}".rstrip("0").rstrip(".") + units[k][1]
    return ("-" if x < 0 else "") + cur + (f"{a:,.0f}" if a >= 100 else _fmt(a))


def _ratio_text(x: float, overall: float) -> str:
    """A ratio KPI as a percentage; a ratio far above 1 (a per-unit value, not a share) stays a plain number."""
    return f"{x:.1%}" if abs(overall) <= 5 else full(x)


def _ratios(state: dict) -> dict[str, dict]:
    plan = state.get("plan") or {}
    return {d["name"]: d for d in list(plan.get("derived") or []) + list((state.get("derived_metrics") or {}).get("created") or [])}


def kpi_measures(state: dict, df) -> tuple[list[str], dict[str, dict]]:
    """(additive KPI columns, ratio KPI specs) present in the cleaned data: the target, the measures of the brief, the parts of every ratio and the measures of the findings."""
    ratios = {n: s for n, s in _ratios(state).items() if n in df.columns and s.get("a") in df.columns and s.get("b") in df.columns}
    ov = kinds(state.get("brief"))
    roles = (state.get("brief") or {}).get("roles") or {}
    used = [(state.get("plan") or {}).get("target")] + [c for c, r in roles.items() if r == "measure"] + [x for s in ratios.values() for x in (s["a"], s["b"])] \
        + [(f.get("params") or {}).get("target") for f in state.get("findings") or []]
    cols: list[str] = []
    for c in used:
        if c and c in df.columns and c not in cols and c not in ratios and measure_kind(c, overrides=ov) == "additive":
            cols.append(c)
    return cols[:MAX_ADDITIVE], ratios


def _cur(col: str, state: dict) -> str:
    return (state.get("context") or {}).get("currency", "") if is_money(col, narrow=True) else ""


# ------------------------------------------------------------------ the question list
def goal_questions(goal: str) -> list[str]:
    """The explicit questions in the goal text, verbatim (list markers removed), in order. Empty when the goal asks none."""
    out: list[str] = []
    for part in re.split(r"(?<=[.!?:;])\s+|\n+", goal or ""):
        part = re.sub(r"^\s*(?:\d+[.)]|[-*\u2022])\s*", "", part).strip()
        if part.endswith("?") and re.search(r"[A-Za-z]", part) and part not in out:
            out.append(part)
    return out


def questions(state: dict) -> list[str]:
    """The questions the summary answers: those written in the goal, else the planner's."""
    return goal_questions(state.get("goal", "")) or list((state.get("plan") or {}).get("questions") or [])


# ------------------------------------------------------------------ ranking facts
def group_columns(state: dict, df, every: bool = False) -> list[str]:
    """Category columns the goal or a question names, and both levels of every verified hierarchy. every=True: every category column with 2-MAX_RANK_GROUPS groups.
    A column judged to be a quantity is never a group."""
    prof = {c["name"]: c for c in (state.get("profile") or {}).get("columns", [])}
    asked = " ".join([state.get("goal", "")] + questions(state))
    hier = [c for pair in hierarchy_pairs(state.get("brief")) for c in pair]
    brief_roles = (state.get("brief") or {}).get("roles") or {}
    out: list[str] = []
    rec, measures = state.get("understanding"), und.measure_columns(state.get("understanding"))
    mapped = set(und.goal_columns(rec))        # the data understanding record maps the goal's words to these columns, whatever their names
    for name, c in prof.items():
        if name not in df.columns or c.get("role") not in ("categorical", "binary") or name in measures or is_quantity(df[name], brief_roles.get(name)):
            continue
        n = c.get("unique") or 0
        named = name in hier or name in mapped or named_in_goal(name, asked) or mentions(name, asked)
        if (2 <= n <= MAX_RANK_GROUPS) if every else (2 <= n <= MAX_GROUPS and named):
            out.append(name)
    return out


def _clean_groups(df, group: str) -> pd.Series:
    g = df[group].astype(str)
    return g.where(~g.isin(["nan", "None", ""]))


def _rank_additive(df, state: dict, group: str, measure: str, n: int) -> dict | None:
    y = pd.to_numeric(df[measure], errors="coerce")
    d = pd.DataFrame({"g": _clean_groups(df, group), "y": y}).dropna()
    if d["g"].nunique() < 2:
        return None
    s = d.groupby("g")["y"].agg(["sum", "count"])
    s = s[s["count"] >= MIN_GROUP_ROWS].sort_values("sum", ascending=False)
    if len(s) < 2:
        return None
    cur, total = _cur(measure, state), float(d["y"].sum())
    top, bot = s.index[0], s.index[-1]
    frac = s["sum"].iloc[0] / total if total > 0 and s["sum"].iloc[0] > 0 else None
    share = "" if frac is None else f" ({frac:.1%} of all {measure})" if frac <= 1 else f" ({frac:.1%} of the net total {measure}, because other groups lose money)"
    dominant = frac is not None and DOMINANT_SHARE < frac <= 1       # above 100% the total is net of losses and 'dominant' would mislead
    if dominant:
        share = share[:-1] + ", dominant)" if share.endswith(")") else share
    text = (f"By total {measure}, {wording.entity(group, top)} is highest at {full(s['sum'].iloc[0], cur)}{share} and {wording.entity(group, bot)} is lowest at {full(s['sum'].iloc[-1], cur)}.")
    if len(s) > MAX_ALL_GROUPS:      # too many groups to list: the top few with their shares, and how many are left
        rest = s.iloc[TOP_SHOWN:]
        pct = lambda v: f" ({v / total:.1%})" if total > 0 else ""
        text = (f"By total {measure} across {len(s)} {group} groups, the top {TOP_SHOWN} are "
                + ", ".join(f"{wording.entity(group, g)} {full(v, cur)}{pct(v)}" for g, v in s["sum"].head(TOP_SHOWN).items())
                + f"; the other {len(rest)} groups hold {full(float(rest['sum'].sum()), cur)}{pct(float(rest['sum'].sum()))}, and {wording.entity(group, bot)} is lowest at {full(s['sum'].iloc[-1], cur)}.")
    loss = [g for g, v in s["sum"].items() if v < 0]
    if loss:
        text += " Loss-making (negative total): " + ", ".join(f"{wording.entity(group, g)} ({full(s.loc[g, 'sum'], cur)})" for g in loss[:5]) + "."
    pct3 = lambda v: f" ({v / total:.1%})" if total > 0 else ""
    lead = f"Top {min(MAIN_PICKS, len(s))} {group} by total {measure}: " + ", ".join(f"{wording.entity(group, g)} {full(v, cur)}{pct3(v)}" for g, v in s["sum"].head(MAIN_PICKS).items())
    brief_top = ", ".join(f"{wording.entity(group, g)} {short(v, cur)}" + (f" ({v / total:.0%})" if total > 0 and 0 < v <= total else "") for g, v in s["sum"].head(MAIN_PICKS).items())
    return {"id": f"r{n}", "tool": "ranking", "basis": "total", "group": group, "measure": measure, "top": top, "bottom": bot, "dominant": top if dominant else None, "groups_total": len(s),
            "top_share": frac, "lead": lead, "text": text, "short": f"Top {min(MAIN_PICKS, len(s))} {group} by total {measure}: {brief_top}"}


def _rank_ratio(df, spec: dict, group: str, measure: str, n: int) -> dict | None:
    g = _clean_groups(df, group)
    counts = g.value_counts()
    r = derived.group_ratio(df, spec, g)
    r = r[r.index.map(lambda k: counts.get(k, 0) >= MIN_GROUP_ROWS)].sort_values(ascending=False)
    overall = derived.ratio_of_sums(df, spec)
    if len(r) < 2 or overall is None:
        return None
    text = (f"By {measure} (total {spec['a']} over total {spec['b']}), {wording.entity(group, r.index[0])} is highest at {_ratio_text(r.iloc[0], overall)} "
            f"and {wording.entity(group, r.index[-1])} is lowest at {_ratio_text(r.iloc[-1], overall)}; all together it is {_ratio_text(overall, overall)}.")
    loss = [k for k, v in r.items() if v < 0]
    if loss:
        text += " Loss-making (negative " + measure + "): " + ", ".join(f"{wording.entity(group, k)} ({_ratio_text(r[k], overall)})" for k in loss[:5]) + "."
    brief = f"{measure} by {group}: highest {wording.entity(group, r.index[0])} {_ratio_text(r.iloc[0], overall)}, lowest {wording.entity(group, r.index[-1])} {_ratio_text(r.iloc[-1], overall)} (overall {_ratio_text(overall, overall)})"
    return {"id": f"r{n}", "tool": "ranking", "basis": "ratio", "group": group, "measure": measure, "top": str(r.index[0]), "bottom": str(r.index[-1]), "text": text, "short": brief}


# ------------------------------------------------------------------ rate outside the main value
def _outside_rate(df, state: dict, col: str, main: str, measure: str | None, focus: list[str], n: int) -> dict:
    """How much of the main additive KPI (or, with none, how many rows) sits outside the one value that holds most rows of `col`, overall and by each goal-named group column."""
    g = _clean_groups(df, col)
    valid = g.notna()
    out = valid & (g != main)
    y = pd.to_numeric(df[measure], errors="coerce").fillna(0.0) if measure else pd.Series(1.0, index=df.index)
    label, cur = measure or "rows", _cur(measure, state) if measure else ""
    total, amt = float(y[valid].sum()), float(y[out].sum())
    rows_out = int(out.sum())
    rate = f" ({amt / total:.1%} of all {label})" if total > 0 else ""
    text = (f"{label} outside {main}: {full(amt, cur)}{rate}; {rows_out:,} of {int(valid.sum()):,} rows ({rows_out / valid.sum():.1%}) are outside {main}, "
            f"which holds {1 - rows_out / valid.sum():.1%} of the rows in {col}.")
    by, clauses = [], []
    for grp in focus:
        if grp == col:
            continue
        d = pd.DataFrame({"g": _clean_groups(df, grp), "y": y, "out": out}).dropna(subset=["g"])
        d["o"] = d["y"].where(d["out"], 0.0)
        s = d.groupby("g").agg(total=("y", "sum"), outside=("o", "sum"), rows=("y", "size"))
        s = s[(s["rows"] >= MIN_GROUP_ROWS) & (s["total"] > 0)]
        if len(s) < 2:
            continue
        s["rate"] = s["outside"] / s["total"]
        s = s.sort_values("rate", ascending=False)
        top, bottom = s.head(OUTSIDE_SHOWN), s.tail(OUTSIDE_SHOWN)
        bottom = bottom[~bottom.index.isin(top.index)].iloc[::-1]
        fmt = lambda t: ", ".join(f"{wording.entity(grp, k)} {full(r['outside'], cur)} ({r['rate']:.1%})" for k, r in t.iterrows())
        text += f" By {grp}, the highest rate outside {main}: {fmt(top)}" + (f"; the lowest: {fmt(bottom)}" if len(bottom) else "") + "."
        by.append({"group": grp, "highest": [str(k) for k in top.index], "lowest": [str(k) for k in bottom.index]})
        clauses.append(f"highest rate by {grp}: " + ", ".join(f"{wording.entity(grp, k)} {r['rate']:.0%}" for k, r in top.iterrows()))
    values = [str(v) for v in g.value_counts().index if str(v) != main][:VALUES_MAX]
    brief = f"{label} outside {main}: {short(amt, cur)}" + (f" ({amt / total:.1%} of all {label})" if total > 0 else f" ({rows_out / valid.sum():.1%} of rows)") + ("; " + clauses[0] if clauses else "")
    return {"id": f"r{n}", "short": brief, "tool": "ranking", "kind": "outside_rate", "basis": "ratio", "group": col, "measure": label, "top": main, "bottom": main, "outside": amt, "by": by, "values": values, "text": text,
            "rate_amount": amt / total if total > 0 else None, "rate_rows": rows_out / int(valid.sum())}


def outside_facts(state: dict, df, start: int) -> list[dict]:
    """One fact per category column (at most MAX_OUTSIDE_COLUMNS) in which a single value holds DOMINANT_VALUE_SHARE of the rows or more."""
    cols, _ = kpi_measures(state, df)
    prof = {c["name"]: c for c in (state.get("profile") or {}).get("columns", [])}
    brief_roles = (state.get("brief") or {}).get("roles") or {}
    focus = group_columns(state, df)
    out: list[dict] = []
    for name, c in prof.items():
        if len(out) >= MAX_OUTSIDE_COLUMNS:
            break
        if name not in df.columns or c.get("role") not in ("categorical", "binary") or is_quantity(df[name], brief_roles.get(name)):
            continue
        counts = _clean_groups(df, name).value_counts()
        if len(counts) < 2 or counts.iloc[0] / counts.sum() < DOMINANT_VALUE_SHARE:
            continue
        out.append(_outside_rate(df, state, name, str(counts.index[0]), cols[0] if cols else None, focus, start + len(out)))
    return out


# ------------------------------------------------------------------ growth facts
def _full_periods(t: pd.Series, freq: str) -> list:
    """Periods of `freq` that the dates cover from start to end (give or take a twelfth of the period), in order."""
    lo, hi = t.min(), t.max()
    out = []
    for p in pd.period_range(lo.to_period(freq), hi.to_period(freq), freq=freq):
        start, end = p.start_time, p.end_time
        slack = (end - start) / 12
        if start >= lo - slack and end <= hi + slack:
            out.append(p)
    return out


def _asked_text(state: dict) -> str:
    return " ".join([state.get("goal", "")] + questions(state))


def _growth_fact(df, state: dict, group: str, measure: str, date: str, n: int) -> dict | None:
    d = pd.DataFrame({"t": pd.to_datetime(df[date], errors="coerce"), "g": _clean_groups(df, group), "y": pd.to_numeric(df[measure], errors="coerce")}).dropna()
    if len(d) < 2 * MIN_GROUP_ROWS or d["g"].nunique() < 2:
        return None
    for freq, sub, sub_word in GROWTH_UNITS:
        periods = _full_periods(d["t"], freq)
        if len(periods) >= 2:
            break
    else:
        return None
    first, last = periods[0], periods[-1]
    d["p"] = d["t"].dt.to_period(freq)
    d = d[d["p"].isin([first, last])]
    totals = d.pivot_table(index="g", columns="p", values="y", aggfunc="sum", fill_value=0.0).reindex(columns=[first, last], fill_value=0.0)
    totals = totals[(totals != 0).any(axis=1)]
    if len(totals) < 2:
        return None
    mean_first, k, cur = float(totals[first].abs().mean()), len(totals), _cur(measure, state)
    rows = []
    for g, r in totals.iterrows():
        a, b = float(r[first]), float(r[last])
        subs = []
        for p in (first, last):
            part = d[(d["g"] == g) & (d["p"] == p)]
            idx = pd.period_range(p.start_time.to_period(sub), p.end_time.to_period(sub), freq=sub)
            subs.append(part.groupby(part["t"].dt.to_period(sub))["y"].sum().reindex(idx, fill_value=0.0).values)
        p_adj = None
        if all(len(x) >= MIN_SUB_PERIODS for x in subs):
            pv = stats.ttest_ind(subs[0], subs[1], equal_var=False).pvalue
            p_adj = min(1.0, (1.0 if np.isnan(pv) else float(pv)) * k)
        pct_ok = a > 0 and a >= wording.NEAR_ZERO * mean_first          # a start at or near zero, or below it, makes a percentage meaningless
        rows.append({"group": str(g), "first": a, "last": b, "change": b - a, "pct": (b - a) / a if pct_ok else None, "p_adjusted": p_adj, "reliable": bool(p_adj is not None and p_adj < 0.05)})
    rows.sort(key=lambda x: -x["change"])
    parts = []
    for x in rows:
        move = f"from {full(x['first'], cur)} to {full(x['last'], cur)}"
        ch = (f"{x['pct']:+.1%} ({move})" if x["pct"] is not None
              else f"{'+' if x['change'] >= 0 else '-'}{full(abs(x['change']), cur)} ({move})")
        x["line"] = f"{wording.entity(group, x['group'])} {ch}, {'statistically reliable' if x['reliable'] else 'not statistically reliable'}"
        parts.append(x["line"])
    big = max(rows, key=lambda x: abs(x["change"]))
    move = f"{short(big['first'], cur)} to {short(big['last'], cur)}"
    brief = (f"Biggest change in {measure} by {group}, {first} vs {last}: {wording.entity(group, big['group'])} "
             + (f"{big['pct']:+.0%}" if big["pct"] is not None else f"{'+' if big['change'] >= 0 else '-'}{short(abs(big['change']), cur)}") + f" ({move}), {'statistically reliable' if big['reliable'] else 'not statistically reliable'}")
    head = f"Growth in total {measure} by {group}, {first} vs {last} (first and last full periods): "
    tail = f". Reliability is a Welch test on {sub_word}ly totals, corrected for {k} groups."
    text = head + "; ".join(parts) + tail
    return {"id": f"r{n}", "tool": "ranking", "kind": "growth", "basis": "total", "group": group, "measure": measure, "date": date, "periods": [str(first), str(last)],
            "top": rows[0]["group"], "bottom": rows[-1]["group"], "groups": rows, "text": text, "text_head": head, "text_tail": tail, "short": brief}


def ranking_facts(state: dict, df) -> list[dict]:
    """One fact per (group column, KPI), whether or not the difference between the groups was significant, plus a growth fact per (group column, additive KPI) when there is a date."""
    if df is None:
        return []
    cols, ratios = kpi_measures(state, df)
    out: list[dict] = []
    date = (state.get("plan") or {}).get("date_col")
    focus = group_columns(state, df)
    for group in group_columns(state, df, every=True):
        for m in (cols if group in focus else cols[:1]):       # the main additive KPI, and its growth, by every category column; the other KPIs and ratios only by the focus columns
            if m != group and (fact := _rank_additive(df, state, group, m, len(out) + 1)):
                out.append({**fact, "focus": group in focus})
            if date in df.columns and m != group and _clean_groups(df, group).nunique() <= MAX_GROUPS and (fact := _growth_fact(df, state, group, m, date, len(out) + 1)):
                out.append({**fact, "focus": group in focus})
        for name, spec in (ratios.items() if group in focus else ()):
            if name != group and (fact := _rank_ratio(df, spec, group, name, len(out) + 1)):
                out.append(fact)
    return out + outside_facts(state, df, len(out) + 1)


# ------------------------------------------------------------------ the closed list of ids
def _measure_of(f: dict) -> str:
    p = f.get("params") or {}
    return str(p.get("target") or p.get("y") or p.get("col") or "")


def _columns_of(f: dict) -> list[str]:
    return [str(f["params"][k]) for k in GROUP_PARAMS if (f.get("params") or {}).get(k)]


def basis(f: dict) -> str:
    """total | ratio | average: what the number in a finding is. A per-row average is the weakest thing to headline."""
    st = f.get("stats") or {}
    if f.get("tool") == "ranking":
        return f["basis"]
    if f["tool"] == "group_compare":
        return "ratio" if st.get("ratio_of_sums") or st.get("kind") == "binary" else "average"
    if f["tool"] == "seasonality":
        return {"period totals": "total", "ratio of sums": "ratio"}.get(st.get("basis"), "average")
    if f["tool"] == "trend":
        return {"mean": "average", "ratio of sums": "ratio"}.get(st.get("agg"), "total")
    return "total"


def catalog(state: dict, rankings: list[dict]) -> list[dict]:
    """[{id, text, column, measure}] for every ranking fact and finding that can answer a question. Nothing else may be cited."""
    out = [{"id": r["id"], "text": r["text"], "column": r["group"], "measure": r["measure"], "tool": "ranking", "kind": r.get("kind", "")} for r in rankings]
    for f in state.get("findings") or []:
        if f["tool"] in NOT_A_FINDING:
            continue
        line = (f.get("insight") or {}).get("headline") or f.get("title", "")
        out.append({"id": f["id"], "text": line, "column": ", ".join(_columns_of(f) or [str((f.get("params") or {}).get("date") or "")]).strip(", "), "measure": _measure_of(f), "tool": f["tool"]})
    return out


# ------------------------------------------------------------------ rules: measure and group column against the question
def _fit(q: str, item: dict) -> tuple[float, bool] | None:
    """(share of the measure's words in the question, group column named) or None when the item cannot answer the question."""
    qt, mt = tokens(q), tokens(item["measure"])
    if not mt or not (mt & qt):
        return None
    frac = len(mt & qt) / len(mt)
    cols = [c.strip() for c in item["column"].split(",") if c.strip()]
    if item["tool"] in TIME_TOOLS:
        if wording.TREND_Q.search(q) or (qt & tokens(item["text"])) - mt:
            return frac, any(mentions(c, q) for c in cols)
        return None
    if item["tool"] == "ranking" or cols:
        named = any(mentions(c, q) for c in cols)
        if item.get("kind") == "growth":       # a growth fact answers a question about growth or change, and then beats the plain ranking
            return (frac + 1, True) if named and wording.TREND_Q.search(q) else None
        return (frac, True) if named else None
    return (frac, False) if (qt & tokens(item["text"])) - mt else None


def rules_pick(q: str, items: list[dict], main: str = "") -> list[str]:
    if main and not any(tokens(it["measure"]) & tokens(q) for it in items if it["measure"]):
        q = f"{q} {main}"       # the question names no measure: it is about the main KPI
    scored = []
    for k, it in enumerate(items):
        fit = _fit(q, it)
        if fit:
            scored.append((fit[0] + fit[1] + 0.01 * len(tokens(it["measure"])), it["tool"] == "ranking", -k, it))
    scored.sort(key=lambda t: t[:3], reverse=True)
    if not scored:
        return []
    top = scored[0]
    picks = [top[3]["id"]]
    if top[1]:       # the ranking is the answer; add the best test that is about the same measure and column
        same = next((t for t in scored[1:] if not t[1] and t[3]["measure"] == top[3]["measure"] and t[3]["column"] == top[3]["column"]), None)
        if same:
            picks.append(same[3]["id"])
    return picks[:PICKS_MAX]


# ------------------------------------------------------------------ LLM: ids from the closed list
ITEMS_MAX, ITEM_CHARS_MAX = 40, 160       # the prompt never grows with the data: a free-tier per-minute limit fails a call that is too large


def _trim_items(questions: list[str], items: list[dict], state: dict) -> list[dict]:
    """At most ITEMS_MAX items, those a question's words fit first; the rest are matched by the rules only. Says in the decision log what was cut."""
    cut = sum(len(it["text"]) > ITEM_CHARS_MAX for it in items)
    kept = items
    if len(items) > ITEMS_MAX:
        kept = sorted(items, key=lambda it: not any(_fit(q, it) for q in questions))[:ITEMS_MAX]       # sorted() is stable
    if len(kept) < len(items) or cut:
        state.setdefault("decisions", []).append({"decision": "summary_answers prompt trimmed", "source": "rules", "chosen": f"sent {len(kept)} of {len(items)} results",
                                                  "why": f"the prompt is capped at {ITEMS_MAX} results of {ITEM_CHARS_MAX} characters",
                                                  "errors": [f"{len(items) - len(kept)} results not sent (rules match those)", f"{cut} texts shortened"], "raw_output": ""})
    return kept


def llm_pick(llm, questions: list[str], items: list[dict], state: dict) -> dict[int, list[str]] | None:
    """{question index: ids} with ids outside the list removed; None when the model gave nothing usable. A question mapped to 'none' maps to []."""
    if not llm or not questions or not items:
        return None
    items = _trim_items(questions, items, state)
    valid = {it["id"] for it in items}
    with capture() as cap:
        out = ask_json(
            llm,
            "You match business questions to analysis results. For each question return the ids of the results that directly answer it, or [\"none\"] when no result does. "
            "A result answers a question only if it is about the measure and the grouping the question asks about. Use only ids from the list; never invent one. "
            "Return JSON: {\"answers\": [{\"question\": <index>, \"ids\": [\"<id>\", ...]}]}",
            f"QUESTIONS: {json.dumps(list(enumerate(questions)), ensure_ascii=False)}\nRESULTS: "
            + json.dumps([{**{k: it[k] for k in ("id", "column", "measure")}, "text": it["text"][:ITEM_CHARS_MAX]} for it in items], ensure_ascii=False))
    why = cap.reason
    rows = out.get("answers") if isinstance(out, dict) else None
    if not isinstance(rows, list):
        if not why:
            note_failure("validation (the model's answer had no usable 'answers' list)")
        state.setdefault("decisions", []).append({"decision": "summary_answers", "source": "fallback", "chosen": "rules matching", "why": why or "validation (the model's answer had no usable 'answers' list)", "errors": [why] if why else [], "raw_output": str(out)[:500]})
        return None
    picks: dict[int, list[str]] = {}
    rejected: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("question"), int) or not 0 <= row["question"] < len(questions):
            continue
        ids = [str(i) for i in (row.get("ids") or []) if isinstance(i, (str, int))]
        good = [i for i in dict.fromkeys(ids) if i in valid]
        rejected += [i for i in ids if i not in valid and i.lower() != "none"]
        if good or [i.lower() for i in ids] in (["none"], []):
            picks[row["question"]] = good[:PICKS_MAX]
    if rejected:
        state.setdefault("decisions", []).append({"decision": "summary_answers", "source": "mixed", "chosen": "LLM ids that exist in the list", "why": "ids not in the list were rejected",
                                                  "errors": [f"rejected ids: {', '.join(rejected)}"], "raw_output": str(out)[:500]})
    return picks


def main_measure(state: dict, rankings: list[dict]) -> str:
    """The additive KPI a question without a measure is about: a KPI the goal names (through the brief's KPI map), the plan's target, any mapped KPI, else the first ranked one."""
    ranked = [r["measure"] for r in rankings if r.get("basis") == "total" and r.get("kind") not in ("growth", "outside_rate")]
    kpis = (state.get("brief") or {}).get("kpis") or {}
    named = [c for k, c in kpis.items() if c and mentions(k, state.get("goal", ""))]
    return next((c for c in named + [(state.get("plan") or {}).get("target")] + [c for c in kpis.values() if c] if c in ranked), ranked[0] if ranked else "")


def goal_pick(q: str, state: dict, items: list[dict], rankings: list[dict]) -> list[str]:
    """Rules that link a question to the facts that answer it: action questions to the recommendations, words that are values of a status-like column to its
    rate-outside-the-main-value fact, column names (stemmed) to the rankings of the question's measure, time words to the main KPI's trend. [] when none applies."""
    main, qt = main_measure(state, rankings), tokens(q)
    if ACTION_Q.search(q):
        from .tiering import merge_strategies
        ids = [s["id"] for s in merge_strategies(state)["strategies"][:MAIN_PICKS]]
        if ids:
            return ids
    for r in rankings:
        if r.get("kind") == "outside_rate" and any(len(v) >= MIN_VALUE_LEN and any(_same(w, t) for w in tokens(v) for t in qt) for v in r.get("values") or []):
            return [r["id"]]
    totals = [r for r in rankings if r.get("basis") == "total" and r.get("kind") not in ("growth", "outside_rate")]
    measure = next((m for m in dict.fromkeys(r["measure"] for r in totals) if tokens(m) & qt), main)
    from_record = set(und.columns_for_text(state.get("understanding"), q))
    scored = [(3 if r["group"] in from_record else names(r["group"], q), r) for r in totals if r["measure"] == measure]
    cols = [r for n, r in sorted(scored, key=lambda t: -t[0]) if n][:MAX_NAMED]
    if len(cols) > 1:
        return [r["id"] for r in cols]
    if cols and not TIME_Q.search(q):
        return rules_pick(q, items, main) or [cols[0]["id"]]
    if TIME_Q.search(q):
        trends = [f for f in state.get("findings") or [] if f["tool"] == "trend"]
        t = next((f for f in trends if _measure_of(f) == measure), trends[0] if trends else None)
        if t:
            return [t["id"]]
    return []


def _record_rankings(q: str, ids: list[str], state: dict, rankings: list[dict]) -> list[str]:
    """Ids of the total rankings, for the measure the chosen ranking is about, of every column the data understanding record maps the question's goal words to and the
    chosen ids leave out: the model cites at most PICKS_MAX results, a question naming three columns needs three."""
    cols = und.columns_for_text(state.get("understanding"), q)
    totals = [r for r in rankings if r.get("basis") == "total" and r.get("kind") not in ("growth", "outside_rate")]
    if len(cols) < 2 or not any(r["id"] in ids for r in totals):
        return []
    measure = next(r["measure"] for r in totals if r["id"] in ids)
    have = {r["group"] for r in totals if r["id"] in ids}
    return [r["id"] for c in cols[:MAX_NAMED] if c not in have for r in totals if r["group"] == c and r["measure"] == measure][:MAX_NAMED]


def pick(llm, state: dict, rankings: list[dict]) -> dict:
    """{"rankings", "picks": {question: [ids]}, "source"} for every planned question."""
    asked = questions(state)
    items = catalog(state, rankings)
    chosen = llm_pick(llm, asked, items, state) or {}
    picks, used_llm = {}, False
    for i, q in enumerate(asked):
        if chosen.get(i):
            picks[q], used_llm = chosen[i] + _record_rankings(q, chosen[i], state, rankings), True
        else:
            picks[q] = goal_pick(q, state, items, rankings) or rules_pick(q, items, main_measure(state, rankings))
    return {"rankings": rankings, "picks": picks, "source": "llm" if used_llm else "rules"}


# ------------------------------------------------------------------ answers and headlines
def _text_of(i: str, by_id: dict, facts: dict, detail: bool = False) -> str:
    if i in facts:
        return facts[i]["text"]
    f = by_id[i]
    if detail and f.get("text"):       # the tool's own sentence: the trend with its best and worst period
        return f["text"].replace("**", "")
    return (f.get("insight") or {}).get("headline") or f.get("title", "")


def not_answered_reason(q: str, items: list[dict]) -> str:
    return "no analysis in this run splits that measure by what the question asks about"


def answers(state: dict, facts: dict | None) -> list[dict]:
    """[{question, answer, ids}] for every planned question. A question with no matching result says 'Not answered: <reason>'."""
    facts = facts or {"rankings": [], "picks": None}
    rankings = facts.get("rankings") or []
    if facts.get("picks") is None:
        facts = {**facts, **pick(None, state, rankings)}
    by_id = {f["id"]: f for f in state.get("findings") or []}
    r_by_id = {r["id"]: r for r in rankings}
    items = catalog(state, rankings)
    strat: dict[str, dict] = {}
    if any(i not in by_id and i not in r_by_id for ids in facts["picks"].values() for i in ids):
        from .tiering import merge_strategies
        strat = {s["id"]: s for s in merge_strategies(state)["strategies"]}
    out = []
    for q in questions(state):
        ids = [i for i in facts["picks"].get(q, []) if i in by_id or i in r_by_id or i in strat]
        ids.sort(key=lambda i: i not in r_by_id)
        if ids and all(i in strat for i in ids):
            line = "Recommended: " + "; ".join(strat[i]["title"].rstrip(".") for i in ids) + "."
        else:
            lead = sum(1 for i in ids if "lead" in r_by_id.get(i, {})) > 1       # several columns named: the top few groups of each
            texts = (r_by_id[i]["lead"] if lead and "lead" in r_by_id.get(i, {}) else _text_of(i, by_id, r_by_id, bool(TIME_Q.search(q))) for i in ids if i not in strat)
            line = " ".join(t.rstrip(".") + "." for t in texts) or f"Not answered: {not_answered_reason(q, items)}."
        out.append({"question": q, "answer": line, "ids": ids})
    return out


def headlines(state: dict, facts: dict | None, answered: list[dict]) -> list[dict]:
    """Up to 5: what answers a goal question first (rankings before tests), then reliable large-effect (and, to fill the list, medium-effect) findings. Totals and ratios of sums before per-row averages."""
    from .insights import effect_label
    from .tiering import reliable
    by_id = {f["id"]: f for f in state.get("findings") or []}
    rankings = {r["id"]: r for r in (facts or {}).get("rankings") or []}
    ranked_cells = {(r["group"], r["measure"]) for r in rankings.values() if r.get("kind") != "growth"}
    seen, out = set(), []

    def add(i: str):
        if i in seen or len(out) >= HEADLINES_MAX:
            return
        seen.add(i)
        out.append({"id": i, "members": [i], "text": _text_of(i, by_id, rankings).rstrip(".")})
    asked = _asked_text(state)
    for a in answered:
        for i in a["ids"]:
            if i not in by_id and i not in rankings:
                continue
            f = by_id.get(i)
            superseded = f is not None and basis(f) == "average" and ((f.get("params") or {}).get("group"), _measure_of(f)) in ranked_cells
            if not superseded:
                add(i)
    main = main_measure(state, list(rankings.values()))
    trend = next((f for f in state.get("findings") or [] if f["tool"] == "trend" and _measure_of(f) == main), None)
    if trend:       # the main KPI's overall trend is always among the headlines
        add(trend["id"])
    for r in rankings.values():       # a group holding more than DOMINANT_SHARE of the main KPI is worth a headline, unless its column has 2-3 groups, one holds nearly all and the goal does not name it
        trivial = r.get("groups_total", 99) <= TRIVIAL_GROUPS and (r.get("top_share") or 0) > TRIVIAL_SHARE and names(r["group"], asked) != 2
        if r.get("dominant") and not trivial:
            add(r["id"])
    size = {"large": 0, "medium": 1}
    strong = [f for f in state.get("findings") or [] if f["tool"] not in NOT_A_FINDING and reliable(f) and effect_label(f.get("stats") or {}) in size]
    for f in sorted(strong, key=lambda f: (size[effect_label(f.get("stats") or {})], basis(f) == "average", -((f.get("insight") or {}).get("score", 0)))):
        add(f["id"])
    return out


# ------------------------------------------------------------------ ratio differences that are not reliable
def difference_lines(state: dict) -> list[str]:
    """One line per goal-named group column and ratio KPI whose comparison across the groups was not significant: '<ratio> does not reliably differ across <column>'."""
    from .tiering import reliable
    ratios, asked, out = _ratios(state), _asked_text(state), []
    for f in state.get("findings") or []:
        p = f.get("params") or {}
        if f.get("tool") != "group_compare" or p.get("target") not in ratios or reliable(f):
            continue
        group = str(p.get("group") or "")
        line = f"{p['target']} does not reliably differ across {group}."
        if group and (named_in_goal(group, asked) or mentions(group, asked)) and line not in out:
            out.append(line)
    return out


# ------------------------------------------------------------------ data gaps already answered
def covered_gaps(facts: dict | None, gaps: list[dict]) -> list[dict]:
    """The gaps, without a 'No <kind> data' line when a computed fact already answers it: a rate-outside-the-main-value fact whose column or labels share a word with
    the kind (the loss facts for "returns"), or one that answers a goal question sharing a word with it."""
    picks, rankings = (facts or {}).get("picks") or {}, (facts or {}).get("rankings") or []
    r_by_id = {r["id"]: r for r in rankings}
    outside = [r for r in rankings if r.get("kind") == "outside_rate"]
    qs = [q for q, ids in picks.items() if any(r_by_id.get(i, {}).get("kind") == "outside_rate" for i in ids)]
    qt = set().union(*(tokens(q) for q in qs)) if qs else set()
    answered = qt | set().union(*(tokens(" ".join([str(r["group"])] + [str(v) for v in r.get("values") or []])) for r in outside)) if outside else qt
    if not answered:
        return gaps
    return [g for g in gaps if not (g["title"].startswith("No ") and g["title"].endswith(" data") and any(_same(w, t) for w in tokens(g["title"][3:-5]) for t in answered))]


# ------------------------------------------------------------------ every column the goal names is answered
def uncovered_groups(state: dict, view: dict) -> list[dict]:
    """One ranking fact for every group column the goal names that no per-question answer in the summary uses (e.g. the goal names categories, channels and states and the answers cover two)."""
    goal = state.get("goal", "")
    rankings, by_id = view.get("rankings") or [], {f["id"]: f for f in state.get("findings") or []}
    used = {i for a in view.get("answers") or [] for i in a["ids"]}
    covered = {r["group"] for r in rankings if r["id"] in used} | {c for i in used if i in by_id for c in (by_id[i].get("params") or {}).values() if isinstance(c, str)}
    out, seen = [], set()
    for r in rankings:
        if r.get("kind") in ("growth", "outside_rate") or r.get("basis") != "total" or r["group"] in covered | seen:
            continue
        if r["group"] in und.goal_columns(state.get("understanding")) or named_in_goal(r["group"], goal) or mentions(r["group"], goal):
            out.append(r)
            seen.add(r["group"])
    return out
