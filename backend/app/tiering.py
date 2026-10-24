"""Report size: three tiers over the same findings. Presentation only; no analysis or statistic is computed or changed here.

  Tier 1  the answer: one line per goal question, key numbers, up to 5 headline findings, up to 3 actions
  Tier 2  evidence: up to 8 cards (goal-named, or significant after correction with a medium or larger effect)
  Tier 3  appendix: everything else, collapsed. Not significant -> one "Checked, nothing found" line each; composition, distribution and outliers -> data profile

A robustness check, a per-group table and a ratio of a measure on the same group column are folded into the card of the finding they belong to."""
from __future__ import annotations

import re

from . import wording
from .clarify import named_in_goal
from .insights import effect_label
from .tools import _fmt

HEADLINES_MAX, EVIDENCE_MAX, ACTIONS_MAX, STRATEGIES_MAX, BULLETS_MAX = 5, 8, 3, 4, 2
PROFILE_TOOLS = ("distribution", "outliers", "category_share", "summary_table")
GROUP_PARAMS = ("group", "group_a", "group_b", "by")
CARD_FIELDS = ("target", "date", "col", "cols", "group", "group_a", "group_b", "by", "within")


# ------------------------------------------------------------------ small helpers
def _ins(f: dict) -> dict:
    return f.get("insight") or {}


def _score(f: dict) -> float:
    return _ins(f).get("score", 0)


def _headline(f: dict) -> str:
    return _ins(f).get("headline") or f.get("title", "")


def _cols(f: dict) -> list[str]:
    out = []
    for k in CARD_FIELDS:
        v = (f.get("params") or {}).get(k)
        out += [str(x) for x in v] if isinstance(v, list) else [str(v)] if v else []
    return out


def _p_text(f: dict) -> str:
    st = f.get("stats") or {}
    p = st.get("p")
    return "" if p is None else "p<0.001" if p < 0.001 else f"p={p:.2g}" if p < 0.1 else f"p={p:.2f}"


def _within(f: dict) -> str:
    p = f.get("params") or {}
    return f", within {p['within']} = {p['within_value']}" if p.get("within") else ""


def _ratios(state: dict) -> dict[str, dict]:
    plan = state.get("plan") or {}
    return {d["name"]: d for d in list(plan.get("derived") or []) + list((state.get("derived_metrics") or {}).get("created") or [])}


def reliable(f: dict) -> bool:
    return bool(f.get("significant")) and (f.get("stats") or {}).get("survives_correction", True) is not False


def goal_named(f: dict, goal: str) -> bool:
    return any(named_in_goal(c, goal) for c in _cols(f))


def kind_of(f: dict) -> str:
    """robust | table | profile | checked | candidate"""
    tool = f["tool"]
    if tool == "robust_compare":
        return "robust"
    if tool == "group_table":
        return "table"
    if tool in PROFILE_TOOLS or (tool == "composition" and f.get("significant")):
        return "profile"
    if f.get("significant") is False:
        return "checked"
    return "candidate"


def evidence_eligible(f: dict, goal: str) -> bool:
    return goal_named(f, goal) or (reliable(f) and effect_label(f.get("stats") or {}) in ("medium", "large"))


# ------------------------------------------------------------------ percent change and outlier fences
PCT_NEAR = re.compile(r"[+\-−]?\d+(?:\.\d+)?%(?=\s+(?:across|over) the period)")
ABOUT_PCT = re.compile(r"(about\s+)[+\-−]?\d+(?:\.\d+)?%(\s+(?:across|over) the period)")


def scrub_change(f: dict, text: str, state: dict) -> str:
    """Replace a % change in a trend sentence with the absolute change whenever a % would mislead (a ratio, or a series that crosses zero)."""
    if f["tool"] != "trend" or not text or not PCT_NEAR.search(text):
        return text
    st, target = f.get("stats") or {}, (f.get("params") or {}).get("target")
    if wording.pct_change_ok(st, target, state):
        return text
    size = wording.trend_size(st, target, state, signed=True, fmt=_fmt)
    return ABOUT_PCT.sub(lambda m: f"a fitted change of {size}{m.group(2)}", text)


NUM = r"([+\-−]?\d[\d,]*\.?\d*)\s?([KMB])?"
RANGE = re.compile(rf"usual range \({NUM} to {NUM}\)")


def _num(v: str, suffix: str | None) -> float:
    return float(v.replace("−", "-").replace(",", "").rstrip(".")) * {"K": 1e3, "M": 1e6, "B": 1e9}.get(suffix or "", 1)


def column_stats(state: dict, col: str) -> dict:
    return next((c.get("stats") or {} for c in (state.get("profile") or {}).get("columns", []) if c.get("name") == col), {})


def outlier_line(f: dict, state: dict) -> str:
    """One line for a column. A lower fence below the smallest value in the column is not shown: nothing can sit below it."""
    col, st = (f.get("params") or {}).get("col", ""), f.get("stats") or {}
    n, k = st.get("n"), st.get("outliers")
    pct = st.get("outlier_pct")
    head = f"**{col}:** " + (f"{k:,} of {n:,} values ({pct:.1%})" if None not in (n, k, pct) else _ins(f).get("headline", f["title"]))
    m = RANGE.search(f.get("text") or "")
    if not m or None in (n, k, pct):
        return head + "."
    lo, hi = _num(m.group(1), m.group(2)), _num(m.group(3), m.group(4))
    lo_txt, hi_txt = f"{m.group(1)}{m.group(2) or ''}", f"{m.group(3)}{m.group(4) or ''}"
    mn = column_stats(state, col).get("min")
    if mn is not None and lo < mn:
        return f"{head} lie above the upper fence ({hi_txt})." if k else f"{head}; none below or above the fences."
    return f"{head} fall outside {lo_txt} to {hi_txt}."


# ------------------------------------------------------------------ cards
def _part(f: dict, state: dict) -> dict:
    ins = _ins(f)
    return {"id": f["id"], "tool": f["tool"], "target": (f.get("params") or {}).get("target"), "headline": _headline(f), "observation": scrub_change(f, ins.get("observation") or f.get("text", ""), state),
            "checks": [], "tables": [], "chart": f.get("chart"), "members": [f["id"]]}


def _table(f: dict) -> dict | None:
    t = f.get("table")
    if not t:
        return None
    return {"id": f["id"], "title": f.get("title", ""), "columns": t["columns"], "rows": t["rows"][:10], "total": t.get("total") or len(t["rows"])}


def _median_line(f: dict) -> str:
    st = f.get("stats") or {}
    p = _p_text(f)
    return ("Median check (Kruskal-Wallis): the difference holds" if f.get("significant") else "Median check (Kruskal-Wallis): the difference does NOT hold") + (f", {p}" if p else "") + "."


def _card(f: dict, state: dict, tier: int) -> dict:
    ins, st = _ins(f), f.get("stats") or {}
    part = _part(f, state)
    conf = ins.get("confidence") or {"level": "descriptive", "label": "Descriptive", "reasons": []}
    return {"id": f["id"], "members": [f["id"]], "tool": f["tool"], "title": f.get("title", ""), "tier": tier, "headline": part["headline"], "observation": part["observation"],
            "why_it_matters": ins.get("why_it_matters", ""), "confidence": conf, "kpi_related": bool(ins.get("kpi_related")), "chart": f.get("chart"), "score": _score(f),
            "goal_named": False, "effect": effect_label(st), "stats": {k: st[k] for k in ("n", "p", "p_adjusted", "survives_correction") if k in st},
            "checks": [], "tables": [], "also": [], "hypotheses": [], "check_next": [], "significant": f.get("significant")}


def _bullets(items) -> list[str]:
    return [str(x) for x in (items or [])][:BULLETS_MAX]


def _fold(cards: dict[str, dict], ratios: dict, findings: list[dict]) -> None:
    """Merge a measure and its ratio on the same group column into one card (the ratio becomes a sub-section of the measure's card)."""
    groups = [f for f in findings if f["tool"] == "group_compare" and f["id"] in cards]

    def key(f):
        p = f.get("params") or {}
        return (p.get("group"), p.get("within"), p.get("within_value"))
    for r in groups:
        spec = ratios.get((r.get("params") or {}).get("target"))
        if not spec or r["id"] not in cards:
            continue
        parents = [c for c in groups if c is not r and c["id"] in cards and key(c) == key(r) and (c.get("params") or {}).get("target") in (spec["a"], spec["b"])
                   and (c.get("params") or {}).get("target") not in ratios]
        if not parents:
            continue
        parents.sort(key=lambda c: (c["params"]["target"] != spec["a"], -_score(c)))
        host, child = cards[parents[0]["id"]], cards.pop(r["id"])
        host["also"].append({"id": r["id"], "target": r["params"]["target"], "headline": child["headline"], "observation": child["observation"], "checks": child["checks"], "tables": child["tables"]})
        host["members"] += child["members"]
        host["score"] = max(host["score"], child["score"])
        host["goal_named"] = host["goal_named"] or child["goal_named"]


def build_cards(state: dict) -> dict:
    """{"candidates": [card], "checked": [{id, line}], "profile": {...}} before tiers are assigned."""
    findings, goal, ratios = state.get("findings") or [], state.get("goal", ""), _ratios(state)
    by_kind: dict[str, list[dict]] = {}
    for f in findings:
        by_kind.setdefault(kind_of(f), []).append(f)

    cards = {f["id"]: _card(f, state, 3) for f in by_kind.get("candidate", [])}
    for f in by_kind.get("candidate", []):
        cards[f["id"]]["goal_named"] = goal_named(f, goal)
    checked = {f["id"]: {"id": f["id"], "members": [f["id"]], "target": (f.get("params") or {}).get("target"), "group": (f.get("params") or {}).get("group"), "tool": f["tool"],
                         "line": _headline(f) + _within(f) + (f" ({_p_text(f)})" if _p_text(f) else ""), "check": ""} for f in by_kind.get("checked", [])}
    by_id = {f["id"]: f for f in findings}

    def parent_of(f: dict, tool: str = "group_compare") -> dict | None:
        p = f.get("params") or {}
        return next((g for g in findings if g["tool"] == tool and (g.get("params") or {}).get("target") == p.get("target") and (g.get("params") or {}).get("group") == p.get("group")
                     and not (g.get("params") or {}).get("within")), None)

    orphans: list[dict] = []
    tables: list[tuple[dict, dict]] = []
    for f in by_kind.get("table", []):
        par = parent_of(f)
        t = _table(f)
        if par is not None and par["id"] in cards and t:
            cards[par["id"]]["tables"].append(t)
            cards[par["id"]]["members"].append(f["id"])
        elif t:
            orphans.append(t)
    medians: list[dict] = by_kind.get("robust", [])
    for f in medians:
        par = parent_of(f)
        if par is not None and par["id"] in cards:
            cards[par["id"]]["checks"].append(_median_line(f))
            cards[par["id"]]["members"].append(f["id"])
        elif par is not None and par["id"] in checked:
            checked[par["id"]]["check"] = _median_line(f)
            checked[par["id"]]["members"].append(f["id"])
        # a median check whose parent is not shown at all is dropped
    _fold(cards, ratios, by_kind.get("candidate", []))

    profile = {"distributions": [], "outliers": [], "composition": [], "tables": orphans}
    for f in by_kind.get("profile", []):
        if f["tool"] == "outliers":
            profile["outliers"].append({"id": f["id"], "line": outlier_line(f, state)})
        elif f["tool"] in ("category_share", "composition"):
            profile["composition"].append({"id": f["id"], "line": _headline(f) + "."})
        else:
            profile["distributions"].append({"id": f["id"], "line": _headline(f) + "."})
    return {"cards": list(cards.values()), "checked": list(checked.values()), "profile": profile}


# ------------------------------------------------------------------ strategies
def _template(s: dict, by_id: dict) -> tuple:
    tools = sorted({by_id[e]["tool"] for e in s.get("evidence") or [] if e in by_id})
    return (s.get("theme"), tuple(tools))


def merge_strategies(state: dict) -> dict:
    """{"strategies": up to 4 data-backed, each citing its finding ids; "foundation": one line each; "dropped": how many fell below the cap}.
    Strategies built from the same template (same theme, same kind of finding) become one."""
    by_id = {f["id"]: f for f in state.get("findings") or []}
    backed = [s for s in state.get("strategies") or [] if s.get("kind") == "data" and s.get("evidence")]
    merged: list[dict] = []
    for s in backed:
        same = next((m for m in merged if _template(m, by_id) == _template(s, by_id)), None)
        if same is None:
            merged.append({**s, "evidence": list(s["evidence"]), "merged_from": [s["id"]], "subjects": [s["title"]]})
            continue
        same["evidence"] += [e for e in s["evidence"] if e not in same["evidence"]]
        same["merged_from"].append(s["id"])
        same["subjects"].append(s["title"])
        same["impact"] = max((same["impact"], s["impact"]), key=lambda v: {"High": 3, "Medium": 2, "Low": 1}.get(v, 0))
    for m in merged:
        if len(m["subjects"]) > 1:
            heads = [t.split(": ", 1) for t in m["subjects"]]
            if all(len(h) == 2 and h[0] == heads[0][0] for h in heads):
                m["title"] = f"{heads[0][0]}: " + "; ".join(h[1] for h in heads[:3]) + (f" (+{len(heads) - 3} more)" if len(heads) > 3 else "")
            else:
                m["title"] = f"{m['subjects'][0]} (+{len(m['subjects']) - 1} similar)"
            m["actions"] = list(m["actions"])[:3]
    for i, m in enumerate(merged[:STRATEGIES_MAX], 1):
        m["rank"] = i
    foundation = [{"id": s["id"], "title": s["title"], "line": f"{s['title']}: {s['objective']}"} for s in state.get("strategies") or [] if s.get("kind") != "data"]
    return {"strategies": merged[:STRATEGIES_MAX], "foundation": foundation, "dropped": max(len(merged) - STRATEGIES_MAX, 0)}


# ------------------------------------------------------------------ key numbers
def key_numbers(state: dict, df=None) -> list[dict]:
    """Total of each additive KPI, the overall ratio KPI(s), and the period. Computed from the cleaned data when it is given; otherwise from the existing key metrics."""
    existing = [m for m in state.get("key_metrics") or [] if m["label"].startswith(("Total ", "Period")) or "(total " in m["label"]]
    if df is None:
        return existing
    import pandas as pd
    from .brief import kinds
    from .derived import ratio_of_sums
    from .measures import is_money, measure_kind
    plan, cur = state.get("plan") or {}, (state.get("context") or {}).get("currency", "")
    ratios = _ratios(state)
    ov = kinds(state.get("brief"))
    roles = (state.get("brief") or {}).get("roles") or {}
    used = [plan.get("target")] + [c for c, r in roles.items() if r == "measure" and c in (plan.get("relevant_columns") or [])] + [(f.get("params") or {}).get("target") for f in state.get("findings") or []]
    cols, seen = [], set()
    for c in used:
        if c and c in df.columns and c not in seen and c not in ratios and measure_kind(c, overrides=ov) == "additive":
            seen.add(c)
            cols.append(c)
    out = []
    for c in cols[:4]:
        x = pd.to_numeric(df[c], errors="coerce").dropna()
        if len(x):
            out.append({"label": f"Total {c}", "value": f"{cur if is_money(c, narrow=True) else ''}{_fmt(x.sum())}", "note": ""})
    for name, spec in ratios.items():
        if name in df.columns and spec.get("a") in df.columns and spec.get("b") in df.columns:
            r = ratio_of_sums(df, spec)
            if r is not None:
                out.append({"label": f"{name} (total {spec['a']} over total {spec['b']})", "value": f"{r:.1%}" if spec.get("part_of") else _fmt(r), "note": ""})
    per = next((m for m in existing if m["label"].startswith("Period")), None)
    return out + ([per] if per else [])


# ------------------------------------------------------------------ the view
def build_view(state: dict, df=None) -> dict:
    goal = state.get("goal", "")
    built = build_cards(state)
    cards = built["cards"]
    by_id = {f["id"]: f for f in state.get("findings") or []}
    eligible = sorted([c for c in cards if any(evidence_eligible(by_id[i], goal) for i in [c["id"]] + [a["id"] for a in c["also"]])],
                      key=lambda c: (not c["goal_named"], -c["score"]))
    evidence = eligible[:EVIDENCE_MAX]
    ids = {c["id"] for c in evidence}
    appendix = sorted([c for c in cards if c["id"] not in ids], key=lambda c: -c["score"])
    for c in evidence:
        c["tier"] = 2
        f = by_id[c["id"]]
        c["hypotheses"], c["check_next"] = _bullets(_ins(f).get("hypotheses")), _bullets(_ins(f).get("check_next"))
    strat = merge_strategies(state)
    heads = evidence[:HEADLINES_MAX]
    actions = [{"text": f"{s['title']}: {s['objective']}", "evidence": s["evidence"]} for s in strat["strategies"][:ACTIONS_MAX]]
    return {
        "answers": [{"question": q, "answer": a} for q, a in wording.question_answers(state, named_in_goal)],
        "numbers": key_numbers(state, df),
        "headlines": [{"id": c["id"], "members": c["members"], "text": c["headline"]} for c in heads],
        "actions": actions,
        "evidence": evidence,
        "appendix": appendix,
        "checked": built["checked"],
        "profile": built["profile"],
        "strategies": strat,
    }


def card_count(view: dict) -> int:
    """Full cards a reader can open: evidence plus appendix cards."""
    return len(view["evidence"]) + len(view["appendix"])
