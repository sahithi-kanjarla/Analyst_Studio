"""Three read-only tools that let Lens answer anything about the saved run: list its sections, read one section, search across all of them.

The model reads the question and chooses among these tools from their descriptions; there is no keyword routing here. A section is built from what the run
already saved (the cleaning log, how the files link, what each column means, the questions asked and the answers given, the definitions, the findings,
the key figures, the ideas, the headlines, what is hidden or pinned, the AI usage, the chart list). Nothing is recalculated and nothing is written: a line
here is copied from the saved record, in display names, so the answer can quote it and the figure checks still apply. No row of data is ever read."""
from __future__ import annotations

import json
import re

from .... import understanding as und
from ... import howtext, records, reports, store
from ..registry import Tool, ToolResult, register

MAX_LINES = 40
MAX_HITS = 12


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except (OSError, ValueError):
        return None


def build_sections(wd, result: dict, env=None) -> dict[str, tuple[str, list[str]]]:
    """{section name: (one-line description, lines)} for the run, from its saved files only."""
    rv = result.get("report_view") or {}
    out: dict[str, tuple[str, list[str]]] = {}

    ctx = result.get("context") or {}
    out["business_context"] = ("What was typed about the business: goal, company, industry, KPIs, notes",
                               ([f"Goal: {result.get('goal')}"] if result.get("goal") else []) + [f"{k.replace('_', ' ').capitalize()}: {v}" for k, v in ctx.items() if v and k not in ("explore", "industry_key")])
    dm = result.get("data_model") or {}
    rel = [f"{r.get('child_table')}.{r.get('child_col')} links to {r.get('parent_table')}.{r.get('parent_col')} ({r.get('cardinality', '')}, {r.get('reason', '')}, {r.get('status', '')})" for r in dm.get("relationships") or []]
    files = [f"File {t.get('name')}: {t.get('rows'):,} rows, {t.get('cols')} columns" if isinstance(t.get("rows"), int) and isinstance(t.get("cols"), int) else f"File {t.get('name')}" for t in dm.get("tables") or []]
    out["relations"] = ("How the uploaded files link together: the files, the links found, the join steps and any warnings",
                        files + ([f"Base file: {dm['base_table']} ({dm.get('base_reason', '')})"] if dm.get("base_table") else []) + rel
                        + [s.get("detail", "") for s in dm.get("steps") or []] + [f"Warning: {w}" for w in dm.get("warnings") or []])
    out["cleaning_log"] = ("Every change made to the data before analysis, with how many values or rows it touched",
                           [f"{s.get('step')}: {s.get('detail')} ({int(s.get('count') or 0):,})" for s in result.get("cleaning_log") or []])
    cols = (result.get("understanding") or {}).get("columns") or {}
    out["understanding"] = ("What each column means (display name, role, meaning) as worked out from the file",
                            [f"{c}: shown as {v.get('display_name') or c}; {v.get('role', '')}; {v.get('meaning', '')}" for c, v in cols.items() if isinstance(v, dict)])
    qs, ans = _read_json(wd / "questions.json"), _read_json(wd / "answers.json") or {}
    given = ans.get("answers") or {}
    qa = []
    for q in qs if isinstance(qs, list) else []:
        label = next((o.get("label") for o in q.get("options") or [] if o.get("value") == given.get(q.get("id"))), None)
        qa.append(f"Asked: {q.get('text')} Answer: {label or ('skipped' if ans.get('skip') else 'not answered, the default was used')}")
    out["questions_and_answers"] = ("The clarifying questions the app asked and the answers given (or skipped)", qa)
    out["definitions"] = ("The definitions and assumptions used, and saved glossary terms", records.definitions_lines(result)[0])
    plan = result.get("plan") or {}
    out["plan"] = ("The analysis plan: the objective, the number focused on, the date used and the checks chosen",
                   ([f"Objective: {plan.get('objective')}", f"Number focused on: {plan.get('target')}", f"Date used: {plan.get('date_col')}"] if plan else [])
                   + [f"Check: {a.get('tool')} ({a.get('why', '')})" for a in plan.get("analyses") or []])
    out["findings"] = ("The checked findings, each as its sentence with its fact id", [f"[{f['id']}] {f['text']}" for f in records.fact_sheet(result)])
    out["headlines"] = ("The headline findings on the Insights tab", [h.get("text", "") for h in rv.get("headlines") or []])
    out["key_figures"] = ("The key figures shown on the Report", [f"{m.get('label')}: {m.get('value')}" + (f" ({m['note']})" if m.get("note") else "") for m in (rv.get("numbers") or result.get("key_metrics") or [])])
    out["ideas"] = ("The ideas to try: title and objective of each", [f"{s.get('title')}: {s.get('objective', '')}" for s in (rv.get("strategies") or {}).get("strategies") or []])
    charts = [c.get("title") or c.get("headline") for c in rv.get("evidence") or [] if c.get("chart")] or [f.get("title") for f in result.get("findings") or [] if f.get("chart")]
    out["charts"] = ("The list of charts in the report, by title", [str(c) for c in charts if c])
    state = (env.view_state if env is not None and env.view_state is not None else store.get_view_state(wd)) or {}
    items = (getattr(env, "cat", None) or {}).get("items") or {}
    out["hidden_and_pinned"] = ("What has been hidden or pinned in the views",
                                [f"Hidden: {(items.get(i) or {}).get('title') or i}" for i in state.get("hidden") or []] + [f"Pinned: {(items.get(i) or {}).get('title') or i}" for i in state.get("pinned") or []])
    t = (_read_json(wd / "ai_usage.json") or {}).get("totals") or {}
    out["ai_usage"] = ("How much AI this run used: calls, tokens, answers reused from saved results",
                       [f"AI calls sent: {t.get('sent', 0)}; answered from saved results: {t.get('cached', 0)}; tokens: {(t.get('input_tokens') or 0) + (t.get('output_tokens') or 0):,}"] if t else [])
    return {k: (d, [und.readable(str(x), result) for x in ls if str(x).strip()]) for k, (d, ls) in out.items()}


def _answer(title: str, lines: list[str], sources: list[str]):
    how = howtext.record_how(title, sources, [], ["Nothing was recalculated; each line is copied from what this run saved."])
    return reports._base("app", title + ":" if lines else "Nothing is saved for that.", "From your saved run", how, [], 0, lines=lines[:MAX_LINES])


class RunSections(Tool):
    name = "run_sections"
    description = ("Use first when the question is about the run itself rather than the sales numbers: lists every section of the saved run (business_context, relations, cleaning_log, understanding, "
                   "questions_and_answers, definitions, plan, findings, headlines, key_figures, ideas, charts, hidden_and_pinned, ai_usage), each with a one-line description. Read-only.")
    args: dict = {}

    def run(self, args, env, prior):
        secs = build_sections(env.wd, env.result, env)
        lines = [f"{k}: {d} ({len(ls)} line{'s' if len(ls) != 1 else ''})" for k, (d, ls) in secs.items()]
        ans, chips = _answer("The saved run has these sections", lines, ["the run's saved record"])
        return ToolResult(summary="Listed the sections of the saved run", answer=ans, chips=chips, data={"sections": {k: d for k, (d, _) in secs.items()}})


class RunSection(Tool):
    name = "run_section"
    description = ("Read one section of the saved run (run_sections shows what each holds): its lines, copied from the saved record. Use for questions about how the data was set up, cleaned, "
                   "linked, understood, or what was asked and answered. Read-only.")
    args = {"section": {"type": "str", "required": True, "help": "a section name from run_sections"}}

    def validate(self, args, env):
        known = build_sections(env.wd, env.result, env)
        return [] if args.get("section") in known else [f"There is no section “{str(args.get('section'))[:40]}”; the sections are {', '.join(known)}."]

    def run(self, args, env, prior):
        name = args["section"]
        desc, lines = build_sections(env.wd, env.result, env)[name]
        ans, chips = _answer(desc, lines, [f"the saved {name.replace('_', ' ')}"])
        return ToolResult(summary=f"Read the {name.replace('_', ' ')} of the saved run", answer=ans, chips=chips, data={"section": name, "lines": lines[:MAX_LINES]})


class RunSearch(Tool):
    name = "run_search"
    description = ("Search every section of the saved run for lines containing the given words and return the best matches, each with its section. "
                   "Use when you do not know which section holds the answer. Read-only.")
    args = {"query": {"type": "str", "required": True, "max_len": 120, "help": "a few words"}}

    def run(self, args, env, prior):
        words = {w for w in re.findall(r"[a-z0-9]+", str(args["query"]).lower()) if len(w) > 2}
        hits = []
        for sec, (_, ls) in build_sections(env.wd, env.result, env).items():
            for line in ls:
                n = len(words & set(re.findall(r"[a-z0-9]+", line.lower())))
                if n:
                    hits.append((n, sec, line))
        hits.sort(key=lambda h: -h[0])
        lines = [f"[{s}] {line}" for _, s, line in hits[:MAX_HITS]]
        ans, chips = _answer(f"Matches for “{str(args['query'])[:60]}” in the saved run", lines, ["the run's saved record"])
        return ToolResult(summary="Searched the saved run", answer=ans, chips=chips, data={"hits": lines})


for _t in (RunSections, RunSection, RunSearch):
    register(_t)
