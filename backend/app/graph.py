"""The multi-agent workflow, expressed as a LangGraph state machine.

   START -> profile -> relate -> clarify -> plan -> clean -> analyze -> critic --(needs a follow-up?)--> analyze (loop)
                                    |                                       \\--(approved)--> insights -> strategy -> report -> powerbi -> END
                                    \\--(questions, no answers yet)--> END with status "needs_input"; the run restarts once answers arrive

`clarify` asks short questions about ambiguities (which date, how refunds count, which rows to include, the main number) and the answers
change the plan and the cleaning. It pauses a run by ending the graph rather than blocking a thread.
`relate` (the Joiner) only does work when several tables were uploaded: it finds how they connect and
builds one analysis table. For a single table it is a no-op.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TypedDict

import pandas as pd
from langgraph.graph import END, START, StateGraph

from . import brief as briefmod, clarify, derived, glossary, placeholders
from . import goal as goalmod
from . import context as ctxmod
from . import critic as critic_mod
from . import insights, planner, polarity as polmod, powerbi, relations, reporter, signals, strategy, summary, tiering, tools
from .cleaner import add_derived, apply_directives, coerce_types, scoped_clean
from .config import MAX_ITERATIONS
from .events import emit, set_quiet
from . import llm as llmmod
from .llm import get_llm
from .propose import derive_mode
from .profiler import load_tables, profile_df


class RunState(TypedDict, total=False):
    run_id: str
    goal: str
    data_paths: list
    workdir: str
    tables: list
    table_profiles: dict
    data_model: dict
    pre_clean_log: list
    profile: dict
    plan: dict
    cleaning_log: list
    findings: list
    critique: dict
    iteration: int
    report_md: str
    report_view: dict
    powerbi: dict
    llm_mode: str
    context: dict
    explore: bool
    strategies: list
    strategy_summary: str
    data_gaps: list
    key_metrics: list
    questions: list
    answers: dict
    answer_sources: dict
    definitions: dict
    applied_definitions: list
    ignored_definitions: list
    awaiting_input: bool
    remove_derived: list
    derived_metrics: dict
    goal_answer: dict
    summary_facts: dict
    polarity: dict
    workspace: str
    decisions: list
    stage_sources: dict
    run_mode: str
    brief: dict
    placeholder_questions: list


def _wd(state) -> Path:
    return Path(state["workdir"])


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_\-]+", "_", name).strip("_") or "table"


def profile_node(state: RunState) -> dict:
    rid = state["run_id"]
    emit(rid, "Profiler", "Reading the data and detecting what each column is…")
    tables: dict[str, pd.DataFrame] = {}
    for path in state["data_paths"]:
        for name, df in load_tables(path).items():
            name = _safe_name(name)
            base, n = name, 2
            while name in tables:
                name, n = f"{base}_{n}", n + 1
            tables[name] = df

    if len(tables) == 1:
        df = next(iter(tables.values()))
        df.to_pickle(_wd(state) / "raw.pkl")
        prof = profile_df(df)
        counts: dict[str, int] = {}
        for c in prof["columns"]:
            counts[c["role"]] = counts.get(c["role"], 0) + 1
        emit(rid, "Profiler", f"{prof['rows']:,} rows × {prof['cols']} columns, {prof['duplicates']} duplicate rows. Column types found: " + ", ".join(f"{v} {k}" for k, v in counts.items()), roles=counts)
        return {"profile": prof, "tables": []}

    profs, meta = {}, []
    for name, df in tables.items():
        df.to_pickle(_wd(state) / f"tbl_{name}.pkl")
        profs[name] = profile_df(df)
        meta.append({"name": name, "rows": int(len(df)), "cols": int(df.shape[1])})
    emit(rid, "Profiler", f"Found {len(tables)} tables: " + ", ".join(f"{m['name']} ({m['rows']:,} × {m['cols']})" for m in meta))
    return {"tables": meta, "table_profiles": profs}


def _answers_for(state, qs: list[dict]) -> dict:
    """The answer to each placeholder question as the rest of the run will resolve it: the user's, else the saved glossary's, else the default."""
    wd = _wd(state)
    afile = wd / "answers.json"
    given = json.loads(afile.read_text(encoding="utf-8")) if afile.exists() else None
    ws = glossary.get_workspace(glossary.workspace_key(state.get("context") or {}))
    return clarify.resolve(qs, (given or {}).get("answers"), ws["answers"], skip=bool((given or {}).get("skip")))[0]


def _user_set(state, qid: str) -> bool:
    """Did the user (not the default or the glossary) answer this question?"""
    afile = _wd(state) / "answers.json"
    given = json.loads(afile.read_text(encoding="utf-8")) if afile.exists() else None
    return bool(given) and not given.get("skip") and qid in (given.get("answers") or {})


def _placeholders_before_join(state, typed: dict, profiles: dict, model: dict, base: str, pre_log: list) -> tuple[dict, list]:
    """Look for placeholder numbers in each source table. Used columns become one question each (default: missing); unused ones are set to missing and logged."""
    cands = placeholders.scan(typed)
    out = {"questions": [], "masked": [], "before": dict(typed)}
    if not cands:
        return out, pre_log
    joined, unused = model["df"], set(model["unused_tables"])
    used = [c for c in cands if placeholders.reaches_analysis(c, joined, c["table"], base, c["table"] not in unused)]
    idle = [c for c in cands if c not in used]
    qs = [placeholders.question(c, c["column"], at_source=True) for c in used]
    final = _answers_for(state, qs)
    out["questions"] = qs
    out["masked"] = idle + [c for c, q in zip(used, qs) if final[q["id"]] == "missing"]
    log = list(pre_log)
    log += [placeholders.log_line(c, "default", "the column is not used by the analysis, so you were not asked") for c in idle]
    for c, q in zip(used, qs):
        if final[q["id"]] == "missing":
            log.append(placeholders.log_line(c, "default" if q["default"] == "missing" and not _user_set(state, q["id"]) else "your answer", "set to missing before the tables were joined"))
    return out, log


def _single_table_placeholders(state) -> dict:
    """One table: nothing to join, so the question's default is an ordinary treat_value_as_missing directive that runs with the other definitions."""
    raw = pd.read_pickle(_wd(state) / "raw.pkl")
    typed, _ = coerce_types(raw, state["profile"])
    cands = placeholders.scan({"data": typed})
    return {"placeholder_questions": [placeholders.question(c, c["column"], at_source=False) for c in cands]}


def relate_node(state: RunState) -> dict:
    """Joiner agent. For a single table it only looks for placeholder values."""
    if not state.get("tables"):
        return _single_table_placeholders(state)
    rid, wd = state["run_id"], _wd(state)
    llm, _ = get_llm()
    emit(rid, "Joiner", "Working out how the tables connect (key columns, value overlap)…")

    typed, profiles, pre_log = {}, {}, []
    for m in state["tables"]:
        name = m["name"]
        df = pd.read_pickle(wd / f"tbl_{name}.pkl")
        t, log = coerce_types(df, state["table_profiles"][name])
        typed[name] = t
        profiles[name] = profile_df(t)
        pre_log += [{**s, "step": f"[{name}] {s['step']}"} for s in log]

    edges = relations.detect_relationships(typed)
    for e in edges:
        emit(rid, "Joiner", "Found link: " + relations.describe_edge(e))
    review = relations.llm_review(llm, profiles, edges, state["goal"]) if (llm and edges) else None
    if review:
        for i in review["reject_links"]:
            edges[i]["status"], edges[i]["reason"] = "rejected", "rejected by the reviewing model: " + review["reason"]
            emit(rid, "Joiner", f"Reviewer vetoed a link: {relations.describe_edge(edges[i])}")

    base, why = relations.choose_base(typed, [e for e in edges if e.get("status") != "rejected"])
    if review and review["base_table"] and review["base_table"] != base:
        emit(rid, "Joiner", f"Ignored the reviewer's coarser base '{review['base_table']}': the base is always the finest-grain table '{base}' so no columns are aggregated away.")
    emit(rid, "Joiner", f"Analysis grain: one row per '{base}' row ({why}).")

    def build(tt, pp):
        if not edges:
            return {"df": tt[base], "steps": [], "origin": {c: base for c in tt[base].columns}, "status": {}, "unused_tables": [t for t in tt if t != base],
                    "warnings": [{"severity": "warn", "text": "No reliable key links were found between the uploaded tables, so only '" + base + "' is analysed. Check that the files share an ID column (e.g. customer_id)."}]}
        return relations.build_model(tt, pp, edges, base)

    model = build(typed, profiles)
    pq, pre_log = _placeholders_before_join(state, typed, profiles, model, base, pre_log)
    if pq["masked"]:
        typed = placeholders.mask_values(typed, pq["masked"])
        profiles = {n: (profile_df(typed[n]) if typed[n] is not t0 else profiles[n]) for n, t0 in pq["before"].items()}
        model = build(typed, profiles)
    for s in model["steps"]:
        emit(rid, "Joiner", s["detail"])
    for w in model["warnings"]:
        emit(rid, "Joiner", ("⚠ " if w["severity"] == "warn" else "") + w["text"])

    joined = model["df"]
    joined.to_pickle(wd / "raw.pkl")
    prof = profile_df(joined)
    emit(rid, "Joiner", f"Analysis table ready: {prof['rows']:,} rows × {prof['cols']} columns from {len(typed) - len(model['unused_tables'])} of {len(typed)} tables.")

    rels = []
    for i, e in enumerate(edges):
        st = model["status"].get(i, {"status": e.get("status", "unused"), "reason": e.get("reason", ""), "method": ""})
        rels.append({**{k: e[k] for k in ("child_table", "child_col", "parent_table", "parent_col", "cardinality", "score", "child_row_match")}, **st})
    tcols = {m["name"]: [{"name": c["name"], "role": c["role"]} for c in profiles[m["name"]]["columns"]] for m in state["tables"]}
    data_model = {"tables": [{**m, "columns": tcols.get(m["name"], [])} for m in state["tables"]], "base_table": base, "base_reason": why, "relationships": rels, "steps": model["steps"],
                  "warnings": model["warnings"], "unused_tables": model["unused_tables"], "origin": model["origin"]}
    return {"profile": prof, "data_model": data_model, "pre_clean_log": pre_log, "placeholder_questions": pq["questions"]}


def brief_node(state: RunState) -> dict:
    """One LLM call that decides what each column means and which column each named KPI is. Reused (not asked again) when a paused run restarts."""
    rid, wd = state["run_id"], _wd(state)
    llm, _ = get_llm()
    prof, ctx = state["profile"], state.get("context") or {}
    bfile = wd / "brief.json"
    if bfile.exists():
        stored = json.loads(bfile.read_text(encoding="utf-8"))
        brief = stored["brief"]
        decisions = [*(state.get("decisions") or []), *(stored.get("decisions") or [stored["decision"]])]
    else:
        st: dict = {"decisions": list(state.get("decisions") or [])}
        emit(rid, "Analyst brief", "Working out what each column means and which column each KPI is…" if llm else "No LLM configured, so the name and data rules decide what each column means.")
        brief = briefmod.resolve(llm, pd.read_pickle(wd / "raw.pkl"), prof, state["goal"], ctx, st)
        decisions = st["decisions"]
        bfile.write_text(json.dumps({"brief": brief, "decisions": decisions[len(state.get("decisions") or []):]}, default=str), encoding="utf-8")
    new_prof = briefmod.apply_roles(prof, brief["roles"])
    if brief["source"] in ("llm", "repaired"):
        changed = briefmod.role_changes(prof, new_prof)
        emit(rid, "Analyst brief", "Column meanings from the LLM" + (" (after one repair)" if brief["source"] == "repaired" else "") + (": " + "; ".join(changed) if changed else ", matching the rules.")
             + (" KPIs: " + "; ".join(f"{k} -> {v or 'no column'}" for k, v in brief["kpis"].items()) + "." if brief["kpis"] else ""))
        for d in brief["dropped"]:
            emit(rid, "Analyst brief", f"Ignored part of the answer ({d}); the rules decide that part.")
        if brief.get("hierarchy"):
            emit(rid, "Analyst brief", "Hierarchy (checked against the data): " + "; ".join(f"{h['parent']} > {h['child']}" for h in brief["hierarchy"]) + ".")
        bits = [f"goal intent: {brief['intent']}" if brief.get("intent") else "", f"industry lens: {ctxmod.INDUSTRIES[brief['industry']]['label']}" if brief.get("industry") else ""]
        if any(bits):
            emit(rid, "Analyst brief", "From the LLM, " + "; ".join(b for b in bits if b) + ".")
    elif brief["source"] == "fallback":
        emit(rid, "Analyst brief", "The LLM answer was unusable, so the rules decide what each column means." + (f" ({llmmod.last_error()})" if llmmod.last_error() else ""))
    for k, m in (brief.get("ratios") or {}).items():
        emit(rid, "Analyst brief", f"Ratio KPI '{k}' = {m['numerator']} / {m['denominator']} (from {'the LLM' if m['path'] == 'llm' else 'a second LLM call' if m['path'] == 'llm-retry' else 'the rules'}).")
    out_ctx = {"context": {**ctx, "industry_key": brief["industry"]}} if brief.get("industry") and ctx else {}
    return {**out_ctx, "brief": brief, "profile": new_prof, "decisions": decisions, "stage_sources": {**(state.get("stage_sources") or {}), "brief": brief["source"]}}


SOURCE_WORDS = {"user": "your answers", "default": "defaults", "glossary": "the saved glossary", "goal": "your goal"}


def clarify_node(state: RunState) -> dict:
    """Ask about ambiguities before planning. Pauses the run (ends the graph) when there are questions nobody has answered yet."""
    rid, wd = state["run_id"], _wd(state)
    set_quiet(rid, False)  # a resumed run replays profile/relate silently; from here on events are recorded again
    ctx = state.get("context") or {}
    ws = glossary.get_workspace(glossary.workspace_key(ctx))
    qfile, afile = wd / "questions.json", wd / "answers.json"
    given = json.loads(afile.read_text(encoding="utf-8")) if afile.exists() else None
    ignored: list = []
    dropped: list = []
    if qfile.exists():
        stored = json.loads(qfile.read_text(encoding="utf-8"))
        questions, ignored, dropped = stored["questions"], stored.get("ignored") or [], stored.get("dropped") or []  # the same questions as before the pause (the LLM part is not deterministic)
    else:
        llm, _ = get_llm()
        typed, _ = coerce_types(pd.read_pickle(wd / "raw.pkl"), state["profile"])
        pq = state.get("placeholder_questions") or []
        skip = {c for q in pq for c in (q["columns"][0], f"{q['table']}_{q['columns'][0]}")}
        questions, ignored = clarify.build_questions_and_ignored(typed, state["profile"], state["goal"], ctx, bool(state.get("explore")), llm, ws, skip, dropped)
        questions, over = clarify.cap_questions(pq + questions)        # placeholder questions and the others share one cap of MAX_QUESTIONS
        dropped += over
        for q in questions:
            saved = (ws["answers"].get(q["id"]) or {}).get("value")
            q["prefill"], q["prefill_source"] = (saved, "glossary") if saved in clarify.option_values(q) else (q["default"], "default")
        if any(not q.get("auto") for q in questions):       # only a run that may pause needs its questions frozen
            qfile.write_text(json.dumps({"workspace": glossary.workspace_key(ctx), "questions": questions, "ignored": ignored, "dropped": dropped}), encoding="utf-8")
    asked = [q for q in questions if not q.get("auto")]
    final, sources = clarify.resolve(questions, (given or {}).get("answers"), ws["answers"], skip=bool((given or {}).get("skip")))
    covered = all(sources[q["id"]] == "glossary" for q in asked)
    waiting = bool(asked) and given is None and not covered

    out: dict = {"questions": questions, "answers": final, "ignored_definitions": ignored, "workspace": glossary.workspace_key(ctx), "answer_sources": sources, "awaiting_input": waiting,
                 "definitions": {"terms": ws["definitions"], "notes": ws["notes"]}}
    text = glossary.definitions_text(ws)
    notes = clarify.note_lines(questions, final)
    if notes:
        text = (text + "\n" if text else "") + "Answers to clarifying questions:\n" + "\n".join(notes)
    if text:
        out["context"] = {**ctx, "definitions_text": text}
    if dropped:
        out["decisions"] = [*(state.get("decisions") or []), {"decision": "clarifying questions capped", "source": "rules", "chosen": f"asked {len(asked)} of {len(asked) + len(dropped)} questions",
                                                              "why": f"at most {clarify.MAX_QUESTIONS} questions are asked, ranked by how much the answer changes the analysis; the rest used their defaults",
                                                              "errors": [f"not asked: {d['question']} -> default used: {d['default']}" for d in dropped], "raw_output": ""}]
        emit(rid, "Clarifier", f"{len(dropped)} lower-priority question{'s' if len(dropped) != 1 else ''} not asked; the default answer was used.")
    if waiting:
        emit(rid, "Clarifier", f"{len(asked)} short question{'s' if len(asked) != 1 else ''} about the data need your answer before the analysis starts (or skip them to use defaults).",
             questions=[q["id"] for q in asked])
    elif asked:
        by: dict[str, int] = {}
        for q in asked:
            by[sources[q["id"]]] = by.get(sources[q["id"]], 0) + 1
        emit(rid, "Clarifier", "Using definitions from " + ", ".join(f"{SOURCE_WORDS.get(k, k)} ({n})" for k, n in by.items()) + ".")
    return out


def route_after_clarify(state: RunState) -> str:
    return END if state.get("awaiting_input") else "plan"


def plan_node(state: RunState) -> dict:
    rid = state["run_id"]
    llm, label = get_llm()
    raw = pd.read_pickle(_wd(state) / "raw.pkl")
    prof = state["profile"]
    brain = 'using ' + label if llm else 'rule-based planning, no LLM configured'
    if state.get("explore"):
        emit(rid, "Planner", f"No specific goal, so planning an exploratory analysis ({brain})…")
    else:
        emit(rid, "Planner", f"Working out what the goal needs ({brain})…")
    from .context import has_context, lens
    if has_context(state.get("context")):
        c = state["context"]
        emit(rid, "Planner", "Using your business context" + (f" ({c['company']})" if c.get("company") else "") + f" with the {lens(c)['label']} lens" + (f"; KPIs: {', '.join(c['kpis'][:4])}" if c.get("kpis") else "") + ".")
    typed, _ = coerce_types(raw, prof)
    ctx, explore = state.get("context") or {}, bool(state.get("explore"))
    questions, answers = state.get("questions") or [], state.get("answers") or {}
    extra: dict = {}
    ov: dict = {"kinds": briefmod.kinds(state.get("brief")), "kpi_map": (state.get("brief") or {}).get("kpis") or {},
          "hierarchy": briefmod.hierarchy_pairs(state.get("brief")), "intent": (state.get("brief") or {}).get("intent")}
    new_cols: list[str] = []
    if questions:  # plan on the data as the user defined it (rows excluded, net amount added)
        typed, _, effects, new_cols = apply_directives(typed, questions, answers)
        ov = {**ov, **clarify.plan_overrides(questions, answers)}
        extra["applied_definitions"] = clarify.summarise(questions, answers, state.get("answer_sources") or {}, effects,
                                                         glossary.get_workspace(state.get("workspace") or "default")["answers"], state.get("workspace") or "default")
        shown = [f"{k}: {v}" for k, v in (("main number", ov["target"]), ("date column", ov["date_col"])) if v]
        if shown:
            emit(rid, "Planner", "Applying your definitions (" + "; ".join(shown) + ").")
    # several candidate measures are never a question: all are analysed. A ratio is only added when the LLM proposes one with a business meaning and the data passes the checks.
    removed = set(state.get("remove_derived") or [])
    # a ratio the user named as a KPI is created by code whenever the brief (or the rules) found its two columns; the LLM's own ideas only fill the remaining slots
    from_kpi, kpi_rejected = derived.kpi_ratios(typed, (state.get("brief") or {}).get("ratios"))
    proposed, rejected = derived.propose(llm, typed, signals.measure_columns(typed, prof), state["goal"], ctxmod.context_text(ctx))
    have = {(d["a"], d["b"]) for d in from_kpi}
    proposed = from_kpi + [d for d in proposed if (d["a"], d["b"]) not in have][:max(0, derived.MAX_DERIVED - len(from_kpi))]
    rejected = kpi_rejected + rejected
    kept = [d for d in proposed if d["name"] not in removed]
    dropped = [d for d in proposed if d["name"] in removed]
    for r in rejected:
        emit(rid, "Planner", f"Did not create {r['a']} / {r['b']}: {r['reason']}.")
    for d in dropped:
        emit(rid, "Planner", f"Left out {d['name']} ({d['a']} / {d['b']}) because you removed it.")
    if kept:
        typed, _, added = add_derived(typed, kept)
        new_cols += added
        ov = {**ov, "derived": kept}
        for d in kept:
            emit(rid, "Planner", f"Also analysing {d['name']} = {d['a']} / {d['b']} as a ratio of sums: {d['meaning']}")
    extra["derived_metrics"] = {"created": kept, "rejected": rejected, "removed": [{"name": d["name"], "a": d["a"], "b": d["b"], "meaning": d["meaning"]} for d in dropped]}
    if new_cols:
        prof = {**prof, "columns": prof["columns"] + [profile_df(typed[[c]])["columns"][0] for c in new_cols], "cols": prof["cols"] + len(new_cols)}
        extra["profile"] = prof
    ov = {**ov, "brief_roles": (state.get("brief") or {}).get("roles") or {}}
    st: dict = {"decisions": list(state.get("decisions") or [])}
    id_roles, id_uniques = planner.roles_and_uniques(prof)
    for c in planner.id_like_columns(typed, id_roles, id_uniques, ov):
        st["decisions"].append({"decision": "excluded as id-like", "source": "rules", "chosen": f"excluded as id-like: {c}",
                                "why": "An id, a code, or an integer with many distinct values and no declared kind: totals and averages of it mean nothing.", "errors": []})
    plan, notes = planner.propose_plan(llm, typed, prof, state["goal"], ctx, explore, ov, st)
    if llm and st["decisions"][-1]["source"] == "fallback":
        emit(rid, "Planner", "The LLM answer was unusable after one repair, so rule-based planning was used instead.")
    extra["decisions"] = st["decisions"]
    for n in notes:
        emit(rid, "Planner", n)
    emit(rid, "Planner", f"Target: {plan['target'] or 'none (descriptive analysis)'}. Using {len(plan['relevant_columns'])} of {prof['cols']} columns, {len(plan['analyses'])} analyses planned.",
         plan=plan)
    return {"plan": plan, "llm_mode": label if llm else "rules-only", "stage_sources": {**(state.get("stage_sources") or {}), "planner": plan["source"]}, **extra}


def clean_node(state: RunState) -> dict:
    rid = state["run_id"]
    emit(rid, "Cleaner", "Fixing types and cleaning only what the plan needs…")
    raw = pd.read_pickle(_wd(state) / "raw.pkl")
    typed, log1 = coerce_types(raw, state["profile"])
    log_def: list = []
    if state.get("questions"):
        typed, log_def, _, _ = apply_directives(typed, state["questions"], state.get("answers") or {})
    typed, log_derived, _ = add_derived(typed, state["plan"].get("derived"))
    log_def += log_derived
    clean, log2 = scoped_clean(typed, state["profile"], state["plan"])
    clean.to_pickle(_wd(state) / "clean.pkl")
    clean.to_csv(_wd(state) / "cleaned.csv", index=False, encoding="utf-8-sig")
    log = state.get("pre_clean_log", []) + log1 + log_def + log2
    for s in log:
        emit(rid, "Cleaner", f"{s['step']}: {s['detail']}")
    if not log:
        emit(rid, "Cleaner", "The data was already clean; no changes needed.")
    return {"cleaning_log": log}


def analyze_node(state: RunState) -> dict:
    rid = state["run_id"]
    it = state.get("iteration", 0)
    df = pd.read_pickle(_wd(state) / "clean.pkl")
    todo = state["plan"]["analyses"] if it == 0 else state["critique"]["followups"]
    findings = list(state.get("findings", []))
    ctx = {"charts_dir": _wd(state) / "charts", "currency": (state.get("context") or {}).get("currency", ""), "kinds": briefmod.kinds(state.get("brief")),
           "derived": {d["name"]: d for d in state["plan"].get("derived") or []}}
    emit(rid, "Analyst", f"{'Running the planned analyses' if it == 0 else 'Running follow-up checks requested by the Critic'} ({len(todo)})…")
    for spec in todo:
        try:
            f = tools.REGISTRY[spec["tool"]](df, ctx, **spec["params"])
        except Exception as e:  # one bad analysis must not kill the run
            emit(rid, "Analyst", f"Skipped {spec['tool']} ({type(e).__name__}: {e})")
            continue
        if f is None:
            emit(rid, "Analyst", f"Skipped {spec['tool']} {spec['params']}: not enough usable data.")
            continue
        f.update({"id": f"f{len(findings) + 1}", "params": spec["params"], "why": spec.get("why", ""), "round": it, "origin": spec.get("origin")})
        findings.append(f)
        emit(rid, "Analyst", f"{f['title']}: " + f["text"].replace("**", ""), finding=f["id"], chart=f.get("chart"))
    return {"findings": findings, "iteration": it + 1}


def critic_node(state: RunState) -> dict:
    rid = state["run_id"]
    llm, _ = get_llm()
    emit(rid, "Critic", "Challenging the results: multiple-testing, sample sizes, outliers, robustness…")
    crit = critic_mod.review(state, llm)
    for i in crit["issues"]:
        emit(rid, "Critic", ("⚠ " if i["severity"] == "warn" else "") + i["text"])
    if crit["followups"] and state.get("iteration", 1) <= MAX_ITERATIONS:
        emit(rid, "Critic", f"Requesting {len(crit['followups'])} follow-up analysis to verify the result.")
    return {"critique": crit, "findings": state["findings"]}


def route_after_critic(state: RunState) -> str:
    if state["critique"]["followups"] and state.get("iteration", 1) <= MAX_ITERATIONS:
        return "analyze"
    return "insights"


def insights_node(state: RunState) -> dict:
    rid = state["run_id"]
    llm, _ = get_llm()
    emit(rid, "Insights", "Turning findings into insights: what it means, how much to trust it, what to check next…")
    pol, src = polmod.resolve(llm, polmod.measures_used(state), state["goal"], state.get("context"), state)   # one call per run (a second only to repair a bad answer)
    state["polarity"] = pol
    lower = [m for m, d in pol.items() if d == polmod.LOWER]
    if pol:
        emit(rid, "Insights", f"Metric direction ({'LLM' if src == 'llm' else 'name rules'}): " + (f"lower is better for {', '.join(lower)}; " if lower else "") + f"higher is better for {sum(d == polmod.HIGHER for d in pol.values())}, neutral for {sum(d == polmod.NEUTRAL for d in pol.values())}.")
    insights.build_insights(state)
    improved = insights.enrich_with_llm(llm, state) if llm else 0
    df = pd.read_pickle(_wd(state) / "clean.pkl")
    km = insights.key_metrics(state, df)
    gaps = insights.data_gaps(state)
    levels: dict[str, int] = {}
    for f in state["findings"]:
        lv = f["insight"]["confidence"]["label"]
        levels[lv] = levels.get(lv, 0) + 1
    emit(rid, "Insights", f"{len(state['findings'])} insights written ({', '.join(f'{v} {k.lower()}' for k, v in levels.items())})"
         + (f"; {improved} rewritten with your business context." if improved else "."))
    for g in gaps:
        if g["severity"] == "warn":
            emit(rid, "Insights", f"⚠ {g['title']}")
    sources = {**(state.get("stage_sources") or {}), "polarity": src, "insights": "llm" if improved else "rules"}
    return {"findings": state["findings"], "key_metrics": km, "data_gaps": gaps, "polarity": pol, "decisions": state.get("decisions") or [], "stage_sources": sources}


def strategy_node(state: RunState) -> dict:
    rid = state["run_id"]
    llm, _ = get_llm()
    emit(rid, "Strategist", "Turning the evidence into prioritised, testable strategies…")
    strategies, summary, improved = strategy.build_strategies(state, llm)
    backed = [s for s in strategies if s["kind"] == "data"]
    if backed:
        emit(rid, "Strategist", f"{len(backed)} data-backed strateg{'y' if len(backed) == 1 else 'ies'}; top priority: {backed[0]['title']}" + (f" ({improved} reworded with your business context)." if improved else "."))
    else:
        emit(rid, "Strategist", "The evidence is too thin for a data-backed strategy; recommending data foundations instead.")
    return {"strategies": strategies, "strategy_summary": summary, "stage_sources": {**(state.get("stage_sources") or {}), "strategy": "llm" if improved else "rules"}}


def report_node(state: RunState) -> dict:
    rid = state["run_id"]
    llm, _ = get_llm()
    emit(rid, "Reporter", "Writing the report…")
    answer = goalmod.answer(state)
    st = {**state, "goal_answer": answer, "decisions": list(state.get("decisions") or [])}
    df = pd.read_pickle(_wd(state) / "clean.pkl")
    st["summary_facts"] = summary.pick(llm, st, summary.ranking_facts(st, df))      # group rankings from the data; which finding answers which question
    view = tiering.build_view(st, df)
    st["report_view"] = view
    md, mode = reporter.write_report(st, llm)
    (_wd(state) / "report.md").write_text(md, encoding="utf-8")
    emit(rid, "Reporter", f"Report written ({ {'llm': 'summary by LLM, numbers from the analysis tools', 'mixed': 'LLM summary with unverified sentences dropped'}.get(mode, 'rule-based summary') }).")
    sources = {**(state.get("stage_sources") or {}), "report": mode}
    return {"report_md": md, "report_view": view, "goal_answer": answer, "summary_facts": st["summary_facts"], "stage_sources": sources, "run_mode": derive_mode(sources), "decisions": st["decisions"]}


def powerbi_node(state: RunState) -> dict:
    rid = state["run_id"]
    emit(rid, "Power BI", "Packaging cleaned data, DAX measures and visual specs for Power BI…")
    df = pd.read_pickle(_wd(state) / "clean.pkl")
    info = powerbi.build_pack(_wd(state), df, state["profile"], state["plan"], state["findings"], state["goal"], state.get("data_model"), briefmod.kinds(state.get("brief")))
    emit(rid, "Power BI", f"Pack ready: {len(info['measures'])} DAX measures, {len(info['visuals'])} visual specs" + (", plus the table relationships." if state.get("data_model") else "."))
    return {"powerbi": info}


def build_graph():
    g = StateGraph(RunState)
    for name, fn in (("profile", profile_node), ("relate", relate_node), ("brief", brief_node), ("clarify", clarify_node), ("plan", plan_node), ("clean", clean_node), ("analyze", analyze_node),
                     ("critic", critic_node), ("insights", insights_node), ("strategy", strategy_node), ("report", report_node), ("powerbi", powerbi_node)):
        g.add_node(name, fn)
    g.add_edge(START, "profile")
    g.add_edge("profile", "relate")
    g.add_edge("relate", "brief")
    g.add_edge("brief", "clarify")
    g.add_conditional_edges("clarify", route_after_clarify, {"plan": "plan", END: END})
    g.add_edge("plan", "clean")
    g.add_edge("clean", "analyze")
    g.add_edge("analyze", "critic")
    g.add_conditional_edges("critic", route_after_critic, {"analyze": "analyze", "insights": "insights"})
    g.add_edge("insights", "strategy")
    g.add_edge("strategy", "report")
    g.add_edge("report", "powerbi")
    g.add_edge("powerbi", END)
    return g.compile()


GRAPH = build_graph()
