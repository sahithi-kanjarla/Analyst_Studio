"""Reporter agent. Numbers, charts and statistics are assembled deterministically from findings.
The LLM (if available) only writes the executive summary and top recommendations from those findings."""
from __future__ import annotations

import json

from . import context as ctxmod
from .insights import allowed_numbers, numbers_ok
from .llm import ask_json
from .propose import derive_mode


def _score(f: dict) -> float:
    return f.get("insight", {}).get("score", 0)


def _rank(findings: list[dict]) -> list[dict]:
    return sorted(findings, key=lambda f: -_score(f))


def _backed(state: dict) -> list[dict]:
    return [s for s in state.get("strategies", []) if s["kind"] == "data"]


def rule_summary(state: dict) -> tuple[str, list[str]]:
    findings = state["findings"]
    prof, plan, ctx = state["profile"], state["plan"], state.get("context") or {}
    sig = [f for f in findings if f.get("significant") and f["tool"] != "robust_compare"]
    surv = [f for f in sig if f["stats"].get("survives_correction", True)]
    who = f" for **{ctx['company']}**" if ctx.get("company") else ""
    head = (f"**Mode:** exploratory. No specific goal was given, so the agents looked for the most important patterns{who}."
            if plan.get("explore") else f"**Goal:** {state['goal']}")
    summary = (f"{head}\n\nThe dataset has {prof['rows']:,} rows and {prof['cols']} columns. The agents focused on **{plan.get('target') or 'general profiling'}** and ran {len(findings)} analyses. "
               f"{len(sig)} showed statistically significant patterns; {len(surv)} {'remains' if len(surv) == 1 else 'remain'} significant after correcting for multiple comparisons.")
    top = [f for f in _rank(findings) if f["tool"] not in ("robust_compare",)][:3]
    if top:
        summary += "\n\n**Headline results**\n" + "\n".join(f"- **{f['insight']['headline']}.** {f['text']}" for f in top)
    backed = _backed(state)
    recs = [f"{s['title']}: {s['objective']}" for s in backed[:3]]
    if not recs:
        recs = ["No strong, data-backed lever was found in this data. Collect more data or additional variables, or refine the goal, before acting."]
    return summary, recs


def llm_summary(llm, state: dict) -> tuple[str, list[str]] | None:
    ctx_txt = ctxmod.context_text(state.get("context"))
    payload = [{"headline": f["insight"]["headline"], "facts": f["text"].replace("**", ""), "confidence": f["insight"]["confidence"]["label"],
                "survives_correction": f["stats"].get("survives_correction")} for f in _rank(state["findings"])[:12]]
    out = ask_json(
        llm,
        "You are a senior data analyst writing for a business reader. Use ONLY the facts and numbers in the findings provided; do not invent numbers. "
        "Be plain and concise, distinguish association from causation, and respect the caveats. If company context is given, use its vocabulary. "
        "Return JSON: {\"summary\": \"3-5 sentence executive summary answering the goal\", \"recommendations\": [3-5 concrete, prioritized actions tied to specific findings]}",
        (f"COMPANY CONTEXT (user-written, unverified):\n{ctx_txt}\n" if ctx_txt else "") +
        f"GOAL: {state['goal']}\nCAVEATS: {json.dumps([i['text'] for i in state['critique']['issues']][:8])}\nFINDINGS: {json.dumps(payload, default=str)}",
    )
    if out and isinstance(out.get("summary"), str) and isinstance(out.get("recommendations"), list) and out["recommendations"]:
        allowed = allowed_numbers(json.dumps(payload, default=str), ctx_txt, str(state["profile"]["rows"]))
        text = out["summary"] + " " + " ".join(str(r) for r in out["recommendations"])
        if numbers_ok(text, allowed):
            return out["summary"], [str(r) for r in out["recommendations"]][:6]
    return None


def _badge(f: dict) -> str:
    c = f["insight"]["confidence"]
    return f" · confidence: **{c['label']}**" + (" · your KPI" if f["insight"].get("kpi_related") else "")


SOURCE_LABEL = {"user": "You", "default": "Default (rules)", "glossary": "Saved glossary", "goal": "Your goal"}


def _cell(v) -> str:
    return str(v).replace("|", "/").replace("\n", " ").strip()


def _definitions_section(state: dict) -> list[str]:
    """Every answer, who set it, and what it did to the data. Free-text definitions are listed as unverified."""
    applied, free = state.get("applied_definitions") or [], state.get("definitions") or {}
    ignored = state.get("ignored_definitions") or []
    terms, notes = free.get("terms") or {}, free.get("notes") or ""
    dm = state.get("derived_metrics") or {}
    created, rejected, removed = dm.get("created") or [], dm.get("rejected") or [], dm.get("removed") or []
    if not applied and not terms and not notes and not ignored and not created and not rejected and not removed:
        return []
    L = ["", "## Definitions used", ""]
    if created or rejected or removed:
        L += ["**Derived metrics** (new columns the analysis created; each is a ratio of sums, never an average of row ratios; remove one with the Remove button in the app and the analysis re-runs without it):", ""]
        L += [f"- `{d['name']}` = {d['a']} / {d['b']}: {_cell(d['meaning'])}" for d in created]
        L += [f"- Not created: {r['a']} / {r['b']}: {_cell(r['reason'])}" for r in rejected]
        L += [f"- Removed by you: `{d['name']}` = {d['a']} / {d['b']}" for d in removed]
        L.append("")
    if applied:
        L += ["| Question | Answer | Set by | Effect on the data | Rows affected |", "|---|---|---|---|---|"]
        for d in applied:
            rows = "-" if d.get("rows") is None else f"{d['rows']:,}"
            L.append(f"| {_cell(d['question'])} | {_cell(d['answer_label'])} | {SOURCE_LABEL.get(d['source'], d['source'])}{' (' + _cell(d['set_by']) + ')' if d.get('set_by') else ''} | {_cell(d['effect'])} | {rows} |")
    if ignored:
        L += ["", "**Saved answers not applied** (the glossary has an answer, but it no longer fits this data, so the default was used):", ""]
        L += [f"- {_cell(i['question'])} -> {_cell(i['answer'])} ({_cell(i['reason'])})" for i in ignored]
    if terms or notes:
        L += ["", "**Your own definitions** (free text: not applied to the data, not verified; passed to the language model as context only):", ""]
        L += [f"- **{_cell(t)}:** {_cell(x)}" for t, x in terms.items()]
        if notes:
            L.append(f"- *Notes:* {_cell(notes)}")
    return L


def build_report(state: dict, summary: str, recs: list[str], run_mode: str = "") -> str:
    plan, prof, ctx = state["plan"], state["profile"], state.get("context") or {}
    title = "# Analysis report" + (f": {ctx['company']}" if ctx.get("company") else "")
    L = [title, "", f"**Mode:** {run_mode or derive_mode(state.get('stage_sources'))}", "", "## Executive summary", "", summary, "", "**Top recommendations**", ""] + [f"- {r}" for r in recs]

    km = state.get("key_metrics") or []
    if km:
        L += ["", "## Key numbers", ""] + [f"- **{m['label']}:** {m['value']}" + (f" ({m['note']})" if m.get("note") else "") for m in km]

    if ctxmod.has_context(ctx):
        lens = ctxmod.lens(ctx)
        L += ["", "## About the business (as you described it)", ""]
        if ctx.get("company"):
            L.append(f"- **Company:** {ctx['company']}")
        L.append(f"- **Industry lens used:** {ctx.get('industry') or lens['label']}")
        if ctx.get("description"):
            L.append(f"- **What they do:** {ctx['description']}")
        if ctx.get("kpis"):
            L.append(f"- **KPIs named:** {', '.join(ctx['kpis'])}. Columns matching these were prioritised; any KPI with no matching column is listed under data gaps.")
        if ctx.get("audience"):
            L.append(f"- **Audience:** {ctx['audience']}")
        L.append("- This context steers what is analysed and how it is worded. It is not verified against the data.")

    L += ["", "## What we set out to answer", "", f"**Objective:** {plan['objective']}", ""]
    L += [f"- {q}" for q in plan["questions"]]

    dm = state.get("data_model")
    if dm:
        L += ["", "## Data sources and how they were connected", "",
              f"{len(dm['tables'])} tables were uploaded: " + ", ".join(f"**{t['name']}** ({t['rows']:,} rows)" for t in dm["tables"]) + ".",
              f"The analysis uses **one row per `{dm['base_table']}` row** ({dm['base_reason']}).", ""]
        for r in dm["relationships"]:
            mark = {"used": "✅", "skipped": "⏭️", "rejected": "🚫"}.get(r["status"], "•")
            L.append(f"- {mark} `{r['child_table']}.{r['child_col']}` → `{r['parent_table']}.{r['parent_col']}` ({r['cardinality']}, {r['child_row_match']:.0%} of rows match)" + (f": {r['reason']}" if r.get("reason") else ""))
        L += [""] + [f"- {s['detail']}" for s in dm["steps"]]
        if not dm["relationships"]:
            L.append("- No key links were found between the tables.")

    L += ["", "## Data and cleaning", "",
          f"{prof['rows']:,} rows × {prof['cols']} columns. Columns used: {', '.join(f'`{c}`' for c in plan['relevant_columns']) or 'n/a'}.", ""]
    L += [f"- **{s['step']}**: {s['detail']}" for s in state["cleaning_log"]] or ["- No cleaning changes were needed."]
    L += _definitions_section(state)

    L += ["", "## Insights", "", "Each insight shows what the data says, why it matters, how much to trust it, and what to check next. Ordered by importance.", ""]
    for f in _rank(state["findings"]):
        ins = f["insight"]
        sig = f.get("significant")
        flag = " ✅" if ins["confidence"]["level"] == "high" else ""
        L += [f"### {ins['headline']}{flag}", "", f"*{f['title']}*{_badge(f)}", ""]
        if f.get("chart"):
            L += [f"![{f['title']}]({f['chart']})", ""]
        if f.get("table"):
            t = f["table"]
            total = t.get("total") or len(t["rows"])
            L += ["| " + " | ".join(t["columns"]) + " |", "|" + "---|" * len(t["columns"])] + ["| " + " | ".join(str(c) for c in r) + " |" for r in t["rows"][:10]]
            L += [f"*Showing 10 of {total} rows; the Data and Dashboard tabs show all of them.*" if total > 10 else "", ""]
        L += [f"**What we see.** {ins['observation']}", ""]
        if ins.get("why_it_matters"):
            L += [f"**Why it matters.** {ins['why_it_matters']}", ""]
        L += ["**How much to trust it.** " + " ".join(ins["confidence"]["reasons"]), ""]
        if ins.get("hypotheses"):
            L += ["**Possible explanations to test (not conclusions):**"] + [f"- {h}" for h in ins["hypotheses"]] + [""]
        if ins.get("check_next"):
            L += ["**Check next:**"] + [f"- {c}" for c in ins["check_next"]] + [""]

    L += ["## Strategy and Recommendations", "", state.get("strategy_summary", ""), ""]
    for s in state.get("strategies", []):
        tag = "Data-backed" if s["kind"] == "data" else "Foundation (not derived from a finding)"
        L += [f"### {s['rank']}. {s['title']}", "",
              f"*{tag} · impact: **{s['impact']}** · effort: **{s['effort']}** · timeline: **{s['horizon']}**" + (f" · confidence: **{str(s['confidence']).capitalize()}**" if s["confidence"] else "") + "*", "",
              f"**Objective.** {s['objective']}", ""]
        if s["evidence"]:
            L += ["**Based on.** " + "; ".join(f"{f['insight']['headline']}" for f in state["findings"] if f["id"] in s["evidence"]), ""]
        L += ["**Actions**"] + [f"1. {a}" if i == 0 else f"{i + 1}. {a}" for i, a in enumerate(s["actions"])] + [""]
        L += [f"**How to test it first.** {s['how_to_test']}", "", "**KPIs to track:** " + "; ".join(s["kpis"]), ""]
        L += ["**Risks**"] + [f"- {r}" for r in s["risks"]] + [""]

    gaps = state.get("data_gaps") or []
    if gaps:
        L += ["## Data gaps and what would sharpen this", ""] + [f"- {'⚠️ ' if g['severity'] == 'warn' else ''}**{g['title']}.** {g['detail']} *{g['suggestion']}*" for g in gaps] + [""]

    L += ["## Reliability and limitations", ""]
    L += [f"- {'⚠️ ' if i['severity'] == 'warn' else ''}{i['text']}" for i in state["critique"]["issues"]] or ["- No issues flagged."]
    L += ["- These are statistical associations in observational data; they do not prove cause and effect. Strategies are hypotheses to test."]
    L += ["", "## Power BI", "", "A ready-to-load Power BI pack (cleaned data, data model, DAX measures and visual specs) was generated alongside this report."]
    return "\n".join(L)


def write_report(state: dict, llm) -> tuple[str, str]:
    res = llm_summary(llm, state) if llm is not None else None
    mode = "llm" if res else "rules"
    summary, recs = res if res else rule_summary(state)
    ans = state.get("goal_answer")
    if ans:      # the goal is answered first, in the app's own words and numbers, whether or not an LLM wrote the rest
        summary = f"**Answer to your goal.** {ans['sentence']}" + (f" {ans['groups_sentence']}" if ans.get("groups_sentence") else "") + "\n\n" + summary
    return build_report(state, summary, recs, derive_mode({**(state.get("stage_sources") or {}), "report": mode})), mode
