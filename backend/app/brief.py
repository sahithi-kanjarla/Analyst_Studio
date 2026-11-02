"""The analysis brief: one LLM call per run that says what each column means and which column each KPI the user named is.

It works from a compact view (names, roles, dtypes, distinct counts, up to 3 sample values; never raw rows) and answers with closed words and column names only.
Every part is checked on its own. A part that fails is dropped and the existing rules decide it, so a bad answer never blocks a run.
"""
from __future__ import annotations

import re

import pandas as pd

from . import context as ctxmod, derived, derived_check
from .cleaner import coerce_types
from .llm import deterministic, last_error
from .context import INDUSTRIES
from .measures import KINDS
from .profiler import ID_NAME
from .propose import propose

ROLES = ("id", "measure", "dimension", "date", "text")
INTENTS = ("change", "compare", "drivers", "summary")
MAX_COLUMNS = 80
MAX_HIERARCHIES = 5

SYSTEM = """You are a data analyst reading the column list of a dataset before any analysis starts. Decide what each column means and which column each KPI the user named is.
role: id (labels a row or an entity, including codes such as pincode, zip, phone or account numbers even when they are numbers), measure (a quantity you can add or average),
dimension (a category to group by), date, text (free text).
kind, only for measures: additive (totals make sense: revenue, units, cost), rate (average it, never add it up: percentages, ratios, prices, scores, ages), other.
kpis: for each KPI name given, the single numeric measure column that holds it, or null when no column does.
ratios: for each KPI name given that is one measure divided by another (a margin, a rate, a share), its numerator column and its denominator column, both numeric measure columns. Leave out KPIs that are not ratios.
hierarchy: pairs of category columns where each child value belongs to exactly one parent value (a broad category and its sub-category, a country and its city). Empty list when there are none.
derived_measures: calculations the data needs because no column holds them, each {name, op, left, right, meaning, confidence, reason}. op is mul, div, sub or add; left and right are numeric measure columns
(never an id or a code); confidence is high, medium or low. Typical use: a count of units times the price of one unit gives the line total. Propose one only when it makes business sense for the KPIs named; an empty list when no calculation is needed.
already_total: the numeric column that already holds the total for each line (the amount for the main KPI), or null when none does. Never propose multiplying that column.
intent: what the goal is after. change (growth, decline, trend over time), compare (groups or segments against each other), drivers (what explains or influences an outcome), summary (an overview or description).
industry: the one that best fits the business, one of: """ + ", ".join(INDUSTRIES) + """ (general when none fits or it is unclear).
Use only the column names, KPI names and words given, exactly as written. Never write a number.
Return JSON: {"columns": {"<column>": {"role": "id|measure|dimension|date|text", "kind": "additive|rate|other"}}, "kpis": {"<kpi name>": "<column>" | null},
"ratios": {"<kpi name>": {"numerator": "<column>", "denominator": "<column>"}},
"hierarchy": [{"parent": "<column>", "child": "<column>"}],
"derived_measures": [{"name": "<new column name>", "op": "mul|div|sub|add", "left": "<column>", "right": "<column>", "meaning": "<what it is>", "confidence": "high|medium|low", "reason": "<why>"}], "already_total": "<column>" | null, "intent": "change|compare|drivers|summary", "industry": "<industry>", "why": "one short sentence"}"""


def _sample(s: pd.Series) -> list[str]:
    return [str(v)[:30] for v in s.dropna().astype(str).drop_duplicates().head(3)]


def build_view(df: pd.DataFrame, profile: dict, goal: str, ctx: dict | None) -> str:
    lines = []
    for c in profile["columns"][:MAX_COLUMNS]:
        name = c["name"]
        samples = _sample(df[name]) if name in df.columns else []
        lines.append(f"- {name} | role by rules: {c['role']} | dtype: {c['dtype']} | distinct: {c['unique']} | samples: {samples}")
    kpis = (ctx or {}).get("kpis") or []
    return (f"GOAL: {goal or 'none given (exploratory)'}\nBUSINESS CONTEXT: {ctxmod.context_text(ctx) or 'none'}\nKPIS NAMED BY THE USER: {kpis if kpis else 'none'}\n"
            "COLUMNS:\n" + "\n".join(lines))


def _id_like(name: str, rule_role: str) -> bool:
    return rule_role == "id" or bool(ID_NAME.search(name))


def _check_hierarchy(df: pd.DataFrame, parent: str, child: str) -> str | None:
    """None when every child value maps to exactly one parent value and the child is the finer level; otherwise the reason it is not a hierarchy."""
    sub = df[[parent, child]].dropna().astype(str)
    if sub.empty:
        return "there are no rows with both values"
    if sub[parent].nunique() < 2:
        return f"'{parent}' has a single value"
    if sub[child].nunique() <= sub[parent].nunique():
        return f"'{child}' does not have more values than '{parent}', so it is not a finer level"
    if (sub.groupby(child)[parent].nunique() > 1).any():
        return f"some '{child}' values appear under more than one '{parent}' value"
    return None


def _make_validator(profile: dict, kpis: list[str], df: pd.DataFrame):
    rule = {c["name"]: c["role"] for c in profile["columns"][:MAX_COLUMNS]}
    numeric = {c["name"] for c in profile["columns"][:MAX_COLUMNS] if "int" in c["dtype"] or "float" in c["dtype"] or c.get("convert") == "numeric"}

    def validate(obj):
        if not isinstance(obj, dict):
            return None, ["the reply must be a JSON object"]
        cols, kp = obj.get("columns"), obj.get("kpis")
        if not isinstance(cols, dict) and not isinstance(kp, dict):
            return None, ["the reply needs a 'columns' object and a 'kpis' object"]
        out = {"roles": {}, "kinds": {}, "kpis": {}, "ratios": {}, "hierarchy": [], "intent": None, "industry": None, "answered": [], "dropped": [], "derived_raw": [], "already_total": None}
        for col, spec in (cols if isinstance(cols, dict) else {}).items():
            if col not in rule:
                out["dropped"].append(f"column '{col}' does not exist")
                continue
            role = spec.get("role") if isinstance(spec, dict) else None
            if role not in ROLES:
                out["dropped"].append(f"column '{col}' has an invalid role")
                continue
            if role == "measure" and _id_like(col, rule[col]):
                role = "id"
            out["roles"][col] = role
            kind = spec.get("kind")
            if kind in (None, ""):
                continue
            if role != "measure":
                out["dropped"].append(f"kind for '{col}' ignored: it is not a measure")
            elif kind not in KINDS or col not in numeric:
                out["dropped"].append(f"kind '{kind}' for '{col}' is not usable")
            else:
                out["kinds"][col] = kind
        for k, col in (kp if isinstance(kp, dict) else {}).items():
            if k not in kpis:
                out["dropped"].append(f"'{k}' is not a KPI the user named")
            elif col is None:
                out["kpis"][k] = None
            elif not isinstance(col, str) or col not in rule:
                out["dropped"].append(f"KPI '{k}' maps to '{col}', a column that does not exist")
            else:
                role = out["roles"].get(col)
                if col not in numeric or _id_like(col, rule[col]) or not (role == "measure" or (role is None and rule[col] == "numeric")):
                    out["dropped"].append(f"KPI '{k}' maps to '{col}', which is not a numeric measure")
                else:
                    out["kpis"][k] = col
        rt = obj.get("ratios")
        for k, m in (rt if isinstance(rt, dict) else {}).items():
            a, b = (m.get("numerator"), m.get("denominator")) if isinstance(m, dict) else (None, None)
            if k not in kpis:
                out["dropped"].append(f"ratio for '{k}' ignored: it is not a KPI the user named")
            elif not (isinstance(a, str) and isinstance(b, str) and a != b and a in numeric and b in numeric and not _id_like(a, rule[a]) and not _id_like(b, rule[b])):
                out["dropped"].append(f"ratio for '{k}' needs two different numeric measure columns")
            else:
                out["ratios"][k] = {"numerator": a, "denominator": b}
        if "hierarchy" in obj:
            out["answered"].append("hierarchy")
            pairs = obj["hierarchy"]
            if not isinstance(pairs, list):
                out["dropped"].append("hierarchy must be a list of parent/child pairs")
                pairs = []
            for pr in pairs[:MAX_HIERARCHIES]:
                p, c = (pr.get("parent"), pr.get("child")) if isinstance(pr, dict) else (None, None)
                if not (isinstance(p, str) and isinstance(c, str) and p != c and p in rule and c in rule and p in df.columns and c in df.columns):
                    out["dropped"].append(f"hierarchy {p!r} > {c!r} names a column that does not exist")
                elif any(out["roles"].get(x, "dimension" if rule[x] == "categorical" else None) != "dimension" for x in (p, c)):
                    out["dropped"].append(f"hierarchy '{p}' > '{c}' dropped: both must be category columns")
                elif (why := _check_hierarchy(df, p, c)):
                    out["dropped"].append(f"hierarchy '{p}' > '{c}' dropped: {why}")
                elif {"parent": p, "child": c} not in out["hierarchy"]:
                    out["hierarchy"].append({"parent": p, "child": c})
        if "derived_measures" in obj:
            out["answered"].append("derived")
            out["derived_raw"], problems = derived_check.parse(obj["derived_measures"])
            out["dropped"] += [f"derived measures: {p}" for p in problems]
            at = obj.get("already_total")
            out["already_total"] = at if isinstance(at, str) and at in rule else None
        for key, allowed, label in (("intent", INTENTS, "intent"), ("industry", INDUSTRIES, "industry")):
            if key in obj:
                if obj[key] in allowed:
                    out[key] = obj[key]
                    out["answered"].append(key)
                else:
                    out["dropped"].append(f"{label} '{obj[key]}' is not one of the allowed words")
        return out, []

    return validate


def _empty() -> dict:
    return {"roles": {}, "kinds": {}, "kpis": {}, "ratios": {}, "hierarchy": [], "intent": None, "industry": None, "answered": [], "dropped": [], "derived_raw": [], "already_total": None}


def _numeric_measures(profile: dict, roles: dict) -> list[str]:
    return [c["name"] for c in profile["columns"][:MAX_COLUMNS] if ("int" in c["dtype"] or "float" in c["dtype"] or c.get("convert") == "numeric")
            and roles.get(c["name"], c["role"]) != "id" and not _id_like(c["name"], c["role"])]


def resolve(llm, df: pd.DataFrame, profile: dict, goal: str, ctx: dict | None, state: dict | None = None) -> dict:
    """{roles, kinds, kpis, ratios, hierarchy, intent, industry, dropped, source}. Empty maps / None (the rules decide) when there is no LLM or its answer is unusable.

    A KPI worded as a ratio (a margin, a rate) always ends up with a numerator and a denominator when the data allows one: from the brief, else from one retry of the
    brief, else from the rules. Each ratio records which of the three gave it in `ratios[kpi]["path"]` (llm, llm-retry, rules), and the choice is in the decision log."""
    kpis = list((ctx or {}).get("kpis") or [])
    llm = deterministic(llm)
    if llm is not None and (ctx or {}).get("explore") and not kpis and not ctxmod.description_hint(ctx):
        llm = None          # an exploratory run that names no KPI and describes no business has nothing for the brief to map: the data understanding record describes the columns, the rules do the rest (no AI call)
        if state is not None:
            state.setdefault("decisions", []).append({"decision": "analysis brief", "source": "rules-only", "chosen": "the rules", "why": "exploratory run with no KPI named and no business description: nothing for the brief to resolve, so no AI call was made", "errors": [], "raw_output": ""})
    view = build_view(df, profile, goal, ctx)
    validator = _make_validator(profile, kpis, df)
    res = propose(llm, "analysis brief", SYSTEM, view, validator, _empty, state)
    out = {**res["value"], "source": res["source"]}
    ratio_kpis = [k for k in kpis if derived.kpi_numerator_tokens(k)]
    ai = lambda r: r["source"] in ("llm", "repaired")
    for k in out["ratios"]:
        out["ratios"][k] = {**out["ratios"][k], "path": "llm"}
    if llm is not None and ratio_kpis and any(k not in out["ratios"] for k in ratio_kpis):
        missing = ", ".join(k for k in ratio_kpis if k not in out["ratios"])
        hint = f"\n\nThese KPIs are ratios of two measures; give each one's numerator and denominator under \"ratios\": {missing}."
        again = propose(llm, "analysis brief (retry for ratio KPIs)", SYSTEM, view + hint, validator, _empty, state)
        if ai(again):
            if not ai(res):
                out = {**again["value"], "source": again["source"]}
            for k, m in again["value"]["ratios"].items():
                out["ratios"].setdefault(k, {**m, "path": "llm-retry"})
    if ratio_kpis:
        measures = _numeric_measures(profile, out["roles"])
        for k in ratio_kpis:
            if k not in out["ratios"] and (m := derived.rules_ratio(df, k, measures, out["kpis"])):
                out["ratios"][k] = {**m, "path": "rules"}
        if state is not None:
            for k in ratio_kpis:
                m = out["ratios"].get(k)
                state["decisions"].append({"decision": f"ratio KPI '{k}'", "source": {"llm": "llm", "llm-retry": "repaired", "rules": "rules"}.get(m["path"], "rules") if m else "rules",
                                           "chosen": f"{m['numerator']} / {m['denominator']} (path: {m['path']})" if m else "no ratio: no numerator and denominator found in the data",
                                           "why": "The user named this KPI and it is a ratio of two measures." + (f" LLM problem: {last_error()}" if llm is not None and not ai(res) and last_error() else ""),
                                           "errors": []})
    out["derived"] = _judge_derived(out, df, profile, ctx) if out["source"] in ("llm", "repaired") and "derived" in out["answered"] else None
    if state is not None:
        _log_parts(state, out, res, ctx)
        _log_derived(state, out["derived"])
    out.pop("derived_raw", None)
    out.pop("already_total", None)
    return out


def _judge_derived(out: dict, df: pd.DataFrame, profile: dict, ctx: dict | None) -> dict:
    """The model's derived-measure proposals checked against the data. Only what the code accepts is ever built."""
    typed, _ = coerce_types(df, profile)
    rule_roles = {c["name"]: c["role"] for c in profile["columns"]}
    return derived_check.judge(out["derived_raw"], out["already_total"], typed, rule_roles, out["roles"], out["kinds"], list((ctx or {}).get("kpis") or []), out["kpis"])


def _log_derived(state: dict, plan: dict | None) -> None:
    """One decision-log line per proposal. Without a usable answer the value-based rule decides and there is nothing to log here."""
    if plan is None:
        return
    for p in [*plan["accepted"], *plan["rejected"]]:
        state["decisions"].append({"decision": f"derived measure '{p['name']}' = {p['left']} {derived_check.OPS[p['op']]} {p['right']}", "source": "llm", "chosen": "accepted" if p in plan["accepted"] else "rejected",
                                   "why": p["verdict"], "errors": []})
    if not plan["accepted"] and not plan["rejected"]:
        state["decisions"].append({"decision": "derived measures", "source": "llm", "chosen": "none proposed", "why": "The model proposed no calculation" + (f"; '{plan['already_total']}' already holds the line total." if plan["already_total"] else "."), "errors": []})


def _log_parts(state: dict, out: dict, res: dict, ctx: dict | None) -> None:
    """The brief's three extra decisions, each in the decision log. A part the model did not answer validly is decided by the rules."""
    ai = res["source"] in ("llm", "repaired")
    rule_src = res["source"] if not ai else "rules"
    for name, key, rule_choice in (("column hierarchy", "hierarchy", []), ("goal intent", "intent", "keyword match on the goal"), ("industry lens", "industry", ctxmod.detect_industry(ctx or {}))):
        used = ai and key in out["answered"]
        errs = [d for d in out["dropped"] if d.startswith(key) or d.startswith(f"{key} ")] if ai else []
        state["decisions"].append({"decision": name, "source": res["source"] if used else rule_src, "chosen": out[key] if used else rule_choice,
                                   "why": res["why"] if used or not ai else "The rules decide this part.", "errors": errs})


def apply_roles(profile: dict, roles: dict) -> dict:
    """The profile with the brief's roles applied where they correct the rules: a column that is really an identifier is an id (even if numeric), and a
    numeric column the brief calls a dimension becomes a category when it has few values. Date, text and measure only confirm what the rules found."""
    if not roles:
        return profile
    cols = []
    for c in profile["columns"]:
        r, new = roles.get(c["name"]), c["role"]
        if r == "id" and c["role"] in ("numeric", "categorical", "text"):
            new = "id"
        elif r == "dimension" and c["role"] == "numeric" and c["unique"] <= 50:
            new = "categorical"
        cols.append({**c, "role": new} if new != c["role"] else c)
    return {**profile, "columns": cols}


def role_changes(profile: dict, new_profile: dict) -> list[str]:
    old = {c["name"]: c["role"] for c in profile["columns"]}
    return [f"{c['name']}: {old[c['name']]} -> {c['role']}" for c in new_profile["columns"] if old.get(c["name"]) != c["role"]]


def kpi_columns(brief: dict | None) -> set[str]:
    return {c for c in ((brief or {}).get("kpis") or {}).values() if c}


def kpi_found(brief: dict | None, kpi: str) -> bool:
    return bool(((brief or {}).get("kpis") or {}).get(kpi))


def kinds(brief: dict | None) -> dict:
    return (brief or {}).get("kinds") or {}


def hierarchy_pairs(brief: dict | None) -> list[tuple[str, str]]:
    """Verified (parent, child) column pairs."""
    return [(h["parent"], h["child"]) for h in (brief or {}).get("hierarchy") or []]
