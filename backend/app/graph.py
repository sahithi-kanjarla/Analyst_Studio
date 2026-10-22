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

from . import clarify, glossary
from . import critic as critic_mod
from . import insights, planner, powerbi, relations, reporter, strategy, tools
from .cleaner import apply_directives, coerce_types, scoped_clean
from .config import MAX_ITERATIONS
from .events import emit, set_quiet
from .llm import get_llm
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
    awaiting_input: bool


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


def relate_node(state: RunState) -> dict:
    """Joiner agent. No-op for a single table."""
    if not state.get("tables"):
        return {}
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

    if review and review["base_table"]:
        base, why = review["base_table"], "chosen by the reviewing model: " + review["reason"]
    else:
        base, why = relations.choose_base(typed, profiles, [e for e in edges if e.get("status") != "rejected"], state["goal"])
    emit(rid, "Joiner", f"Analysis grain: one row per '{base}' row ({why}).")

    if not edges:
        model = {"df": typed[base], "steps": [], "origin": {c: base for c in typed[base].columns}, "status": {}, "unused_tables": [t for t in typed if t != base],
                 "warnings": [{"severity": "warn", "text": "No reliable key links were found between the uploaded tables, so only '" + base + "' is analysed. Check that the files share an ID column (e.g. customer_id)."}]}
    else:
        model = relations.build_model(typed, profiles, edges, base)
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
    return {"profile": prof, "data_model": data_model, "pre_clean_log": pre_log}


SOURCE_WORDS = {"user": "your answers", "default": "defaults", "glossary": "the saved glossary", "goal": "your goal"}


def clarify_node(state: RunState) -> dict:
    """Ask about ambiguities before planning. Pauses the run (ends the graph) when there are questions nobody has answered yet."""
    rid, wd = state["run_id"], _wd(state)
    set_quiet(rid, False)  # a resumed run replays profile/relate silently; from here on events are recorded again
    ctx = state.get("context") or {}
    ws = glossary.get_workspace(glossary.workspace_key(ctx))
    qfile, afile = wd / "questions.json", wd / "answers.json"
    given = json.loads(afile.read_text(encoding="utf-8")) if afile.exists() else None
    if qfile.exists():
        questions = json.loads(qfile.read_text(encoding="utf-8"))["questions"]  # the same questions as before the pause (the LLM part is not deterministic)
    else:
        llm, _ = get_llm()
        typed, _ = coerce_types(pd.read_pickle(wd / "raw.pkl"), state["profile"])
        questions = clarify.build_questions(typed, state["profile"], state["goal"], ctx, bool(state.get("explore")), llm, ws)
        for q in questions:
            saved = (ws["answers"].get(q["id"]) or {}).get("value")
            q["prefill"], q["prefill_source"] = (saved, "glossary") if saved in clarify.option_values(q) else (q["default"], "default")
        if questions:
            qfile.write_text(json.dumps({"workspace": glossary.workspace_key(ctx), "questions": questions}), encoding="utf-8")
    asked = [q for q in questions if not q.get("auto")]
    final, sources = clarify.resolve(questions, (given or {}).get("answers"), ws["answers"], skip=bool((given or {}).get("skip")))
    covered = all(sources[q["id"]] == "glossary" for q in asked)
    waiting = bool(asked) and given is None and not covered

    out: dict = {"questions": questions, "answers": final, "answer_sources": sources, "awaiting_input": waiting,
                 "definitions": {"terms": ws["definitions"], "notes": ws["notes"]}}
    text = glossary.definitions_text(ws)
    notes = clarify.note_lines(questions, final)
    if notes:
        text = (text + "\n" if text else "") + "Answers to clarifying questions:\n" + "\n".join(notes)
    if text:
        out["context"] = {**ctx, "definitions_text": text}
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
    ov = None
    if questions:  # plan on the data as the user defined it (rows excluded, net amount added)
        typed, _, effects, new_cols = apply_directives(typed, questions, answers)
        if new_cols:
            prof = {**prof, "columns": prof["columns"] + [profile_df(typed[[c]])["columns"][0] for c in new_cols], "cols": prof["cols"] + len(new_cols)}
            extra["profile"] = prof
        ov = clarify.plan_overrides(questions, answers)
        extra["applied_definitions"] = clarify.summarise(questions, answers, state.get("answer_sources") or {}, effects)
        shown = [f"{k}: {v}" for k, v in (("main number", ov["target"]), ("date column", ov["date_col"])) if v]
        if shown:
            emit(rid, "Planner", "Applying your definitions (" + "; ".join(shown) + ").")
    raw_plan = planner.llm_plan(llm, prof, state["goal"], ctx, explore, ov) if llm else None
    if llm and raw_plan is None:
        emit(rid, "Planner", "The LLM answer was unusable, so rule-based planning was used instead.")
    plan, notes = planner.finalize_plan(raw_plan, typed, prof, state["goal"], ctx, explore, ov)
    for n in notes:
        emit(rid, "Planner", n)
    emit(rid, "Planner", f"Target: {plan['target'] or 'none (descriptive analysis)'}. Using {len(plan['relevant_columns'])} of {prof['cols']} columns, {len(plan['analyses'])} analyses planned.",
         plan=plan)
    return {"plan": plan, "llm_mode": label if llm else "rules-only", **extra}


def clean_node(state: RunState) -> dict:
    rid = state["run_id"]
    emit(rid, "Cleaner", "Fixing types and cleaning only what the plan needs…")
    raw = pd.read_pickle(_wd(state) / "raw.pkl")
    typed, log1 = coerce_types(raw, state["profile"])
    log_def: list = []
    if state.get("questions"):
        typed, log_def, _, _ = apply_directives(typed, state["questions"], state.get("answers") or {})
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
    ctx = {"charts_dir": _wd(state) / "charts", "currency": (state.get("context") or {}).get("currency", "")}
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
        f.update({"id": f"f{len(findings) + 1}", "params": spec["params"], "why": spec.get("why", ""), "round": it})
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
    return {"findings": state["findings"], "key_metrics": km, "data_gaps": gaps}


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
    return {"strategies": strategies, "strategy_summary": summary}


def report_node(state: RunState) -> dict:
    rid = state["run_id"]
    llm, _ = get_llm()
    emit(rid, "Reporter", "Writing the report…")
    md, mode = reporter.write_report(state, llm)
    (_wd(state) / "report.md").write_text(md, encoding="utf-8")
    emit(rid, "Reporter", f"Report written ({'summary by LLM, numbers from the analysis tools' if mode == 'llm' else 'rule-based summary'}).")
    return {"report_md": md}


def powerbi_node(state: RunState) -> dict:
    rid = state["run_id"]
    emit(rid, "Power BI", "Packaging cleaned data, DAX measures and visual specs for Power BI…")
    df = pd.read_pickle(_wd(state) / "clean.pkl")
    info = powerbi.build_pack(_wd(state), df, state["profile"], state["plan"], state["findings"], state["goal"], state.get("data_model"))
    emit(rid, "Power BI", f"Pack ready: {len(info['measures'])} DAX measures, {len(info['visuals'])} visual specs" + (", plus the table relationships." if state.get("data_model") else "."))
    return {"powerbi": info}


def build_graph():
    g = StateGraph(RunState)
    for name, fn in (("profile", profile_node), ("relate", relate_node), ("clarify", clarify_node), ("plan", plan_node), ("clean", clean_node), ("analyze", analyze_node),
                     ("critic", critic_node), ("insights", insights_node), ("strategy", strategy_node), ("report", report_node), ("powerbi", powerbi_node)):
        g.add_node(name, fn)
    g.add_edge(START, "profile")
    g.add_edge("profile", "relate")
    g.add_edge("relate", "clarify")
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
