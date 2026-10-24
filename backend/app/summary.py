"""Executive-summary logic: which finding answers which goal question, the group rankings that answer "which X is most Y", the headlines and the key numbers.

  * ranking facts: for every goal-named or hierarchy group column and every KPI, the top and bottom group by total (additive KPI, with the top group's share
    of the total) or by ratio of sums (ratio KPI, naming loss-making groups). Built from the cleaned data, so they exist even when no difference was significant
  * answers: the LLM sees the questions and a closed list of finding ids and returns the ids that answer each one (or "none"); ids not in the list are rejected.
    Without an LLM, or when its answer is unusable, the same choice is made by matching the question's words to a finding's measure and group column
  * the text of an answer is always the text of the chosen findings, so every number in it was computed by the tools
Nothing here knows a column name or an industry."""
from __future__ import annotations

import json
import re

import pandas as pd

from . import derived, wording
from .brief import hierarchy_pairs, kinds
from .clarify import named_in_goal
from .llm import ask_json
from .measures import is_money, measure_kind
from .tools import _fmt

HEADLINES_MAX = 5
MIN_GROUP_ROWS = 5            # a group with fewer rows is too small to be called the top or the bottom
MAX_GROUPS = 50
MAX_ADDITIVE = 8
PICKS_MAX = 2
NOT_A_FINDING = ("robust_compare", "summary_table", "group_table", "distribution", "outliers", "category_share")
TIME_TOOLS = ("trend", "segment_trend", "seasonality")
GROUP_PARAMS = ("group", "group_a", "group_b", "by")
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


def full(x: float, cur: str = "") -> str:
    """A total written out in full (2,635,797); small values keep their significant digits."""
    return ("-" if x < 0 else "") + cur + (f"{abs(x):,.0f}" if abs(x) >= 1000 else _fmt(abs(x)))


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


# ------------------------------------------------------------------ ranking facts
def group_columns(state: dict, df) -> list[str]:
    """Category columns the goal or a question names, and both levels of every verified hierarchy."""
    prof = {c["name"]: c for c in (state.get("profile") or {}).get("columns", [])}
    asked = " ".join([state.get("goal", "")] + list((state.get("plan") or {}).get("questions") or []))
    hier = [c for pair in hierarchy_pairs(state.get("brief")) for c in pair]
    out: list[str] = []
    for name, c in prof.items():
        if name in df.columns and c.get("role") in ("categorical", "binary") and 2 <= (c.get("unique") or 0) <= MAX_GROUPS \
                and (name in hier or named_in_goal(name, asked) or mentions(name, asked)):
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
    text = (f"By total {measure}, {wording.entity(group, top)} is highest at {full(s['sum'].iloc[0], cur)}{share} and {wording.entity(group, bot)} is lowest at {full(s['sum'].iloc[-1], cur)}.")
    loss = [g for g, v in s["sum"].items() if v < 0]
    if loss:
        text += " Loss-making (negative total): " + ", ".join(f"{wording.entity(group, g)} ({full(s.loc[g, 'sum'], cur)})" for g in loss[:5]) + "."
    return {"id": f"r{n}", "tool": "ranking", "basis": "total", "group": group, "measure": measure, "top": top, "bottom": bot, "text": text}


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
    return {"id": f"r{n}", "tool": "ranking", "basis": "ratio", "group": group, "measure": measure, "top": str(r.index[0]), "bottom": str(r.index[-1]), "text": text}


def ranking_facts(state: dict, df) -> list[dict]:
    """One fact per (group column, KPI), whether or not the difference between the groups was significant."""
    if df is None:
        return []
    cols, ratios = kpi_measures(state, df)
    out: list[dict] = []
    for group in group_columns(state, df):
        for m in cols:
            if m != group and (fact := _rank_additive(df, state, group, m, len(out) + 1)):
                out.append(fact)
        for name, spec in ratios.items():
            if name != group and (fact := _rank_ratio(df, spec, group, name, len(out) + 1)):
                out.append(fact)
    return out


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
    out = [{"id": r["id"], "text": r["text"], "column": r["group"], "measure": r["measure"], "tool": "ranking"} for r in rankings]
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
        return (frac, True) if named else None
    return (frac, False) if (qt & tokens(item["text"])) - mt else None


def rules_pick(q: str, items: list[dict]) -> list[str]:
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
def llm_pick(llm, questions: list[str], items: list[dict], state: dict) -> dict[int, list[str]] | None:
    """{question index: ids} with ids outside the list removed; None when the model gave nothing usable. A question mapped to 'none' maps to []."""
    if not llm or not questions or not items:
        return None
    valid = {it["id"] for it in items}
    out = ask_json(
        llm,
        "You match business questions to analysis results. For each question return the ids of the results that directly answer it, or [\"none\"] when no result does. "
        "A result answers a question only if it is about the measure and the grouping the question asks about. Use only ids from the list; never invent one. "
        "Return JSON: {\"answers\": [{\"question\": <index>, \"ids\": [\"<id>\", ...]}]}",
        f"QUESTIONS: {json.dumps(list(enumerate(questions)), ensure_ascii=False)}\nRESULTS: "
        + json.dumps([{k: it[k] for k in ("id", "text", "column", "measure")} for it in items], ensure_ascii=False))
    rows = out.get("answers") if isinstance(out, dict) else None
    if not isinstance(rows, list):
        state.setdefault("decisions", []).append({"decision": "summary_answers", "source": "fallback", "chosen": "rules matching", "why": "the model's answer had no usable 'answers' list", "errors": [], "raw_output": str(out)[:500]})
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


def pick(llm, state: dict, rankings: list[dict]) -> dict:
    """{"rankings", "picks": {question: [ids]}, "source"} for every planned question."""
    questions = list((state.get("plan") or {}).get("questions") or [])
    items = catalog(state, rankings)
    chosen = llm_pick(llm, questions, items, state) or {}
    picks, used_llm = {}, False
    for i, q in enumerate(questions):
        if i in chosen:
            picks[q], used_llm = chosen[i], True
        else:
            picks[q] = rules_pick(q, items)
    return {"rankings": rankings, "picks": picks, "source": "llm" if used_llm else "rules"}


# ------------------------------------------------------------------ answers and headlines
def _text_of(i: str, by_id: dict, facts: dict) -> str:
    if i in facts:
        return facts[i]["text"]
    f = by_id[i]
    return (f.get("insight") or {}).get("headline") or f.get("title", "")


def not_answered_reason(q: str, items: list[dict]) -> str:
    if not any(tokens(it["measure"]) & tokens(q) for it in items if it["measure"]):
        return "no measure named in the question was analysed"
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
    out = []
    for q in (state.get("plan") or {}).get("questions") or []:
        ids = [i for i in facts["picks"].get(q, []) if i in by_id or i in r_by_id]
        ids.sort(key=lambda i: i not in r_by_id)
        line = " ".join(t.rstrip(".") + "." for t in (_text_of(i, by_id, r_by_id) for i in ids)) or f"Not answered: {not_answered_reason(q, items)}."
        out.append({"question": q, "answer": line, "ids": ids})
    return out


def headlines(state: dict, facts: dict | None, answered: list[dict]) -> list[dict]:
    """Up to 5: what answers a goal question first (rankings before tests), then reliable large-effect (and, to fill the list, medium-effect) findings. Totals and ratios of sums before per-row averages."""
    from .insights import effect_label
    from .tiering import reliable
    by_id = {f["id"]: f for f in state.get("findings") or []}
    rankings = {r["id"]: r for r in (facts or {}).get("rankings") or []}
    ranked_cells = {(r["group"], r["measure"]) for r in rankings.values()}
    seen, out = set(), []

    def add(i: str):
        if i in seen or len(out) >= HEADLINES_MAX:
            return
        seen.add(i)
        out.append({"id": i, "members": [i], "text": _text_of(i, by_id, rankings).rstrip(".")})
    for a in answered:
        for i in a["ids"]:
            f = by_id.get(i)
            superseded = f is not None and basis(f) == "average" and ((f.get("params") or {}).get("group"), _measure_of(f)) in ranked_cells
            if not superseded:
                add(i)
    size = {"large": 0, "medium": 1}
    strong = [f for f in state.get("findings") or [] if f["tool"] not in NOT_A_FINDING and reliable(f) and effect_label(f.get("stats") or {}) in size]
    for f in sorted(strong, key=lambda f: (size[effect_label(f.get("stats") or {})], basis(f) == "average", -((f.get("insight") or {}).get("score", 0)))):
        add(f["id"])
    return out
