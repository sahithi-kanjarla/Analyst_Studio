"""Reporter agent. Numbers, charts and statistics are assembled deterministically from findings.
The LLM (if available) only writes the executive summary and top recommendations from those findings."""
from __future__ import annotations

import json
import re

from . import cited, context as ctxmod, mainview, summary as summary_mod, tiering, wording
from . import understanding as und
from .clarify import named_in_goal
from .llm import ask_json, capture
from .insights import DATE_CAUTION, DATE_TAG, dates_unconfirmed
from .propose import derive_mode


def _score(f: dict) -> float:
    return f.get("insight", {}).get("score", 0)


def _rank(findings: list[dict]) -> list[dict]:
    return sorted(findings, key=lambda f: -_score(f))


def rule_summary(state: dict) -> tuple[str, list[str]]:
    findings = state["findings"]
    prof, plan, ctx = state["profile"], state["plan"], state.get("context") or {}
    who = f" for **{ctx['company']}**" if ctx.get("company") else ""
    head = (f"**Mode:** exploratory. No specific goal was given, so the agents looked for the most important patterns{who}."
            if plan.get("explore") else f"**Goal:** {state['goal']}")
    summary = (f"{head}\n\nThe dataset has {prof['rows']:,} rows and {prof['cols']} columns. The agents focused on **{plan.get('target') or 'general profiling'}**. "
               f"{wording.counts_sentence(wording.counts(findings))}")
    strategies = tiering.merge_strategies(state)["strategies"]
    recs = [f"{x['title']}: {x['objective']} ({', '.join(x['evidence'])})" for x in strategies[:tiering.ACTIONS_MAX]]
    if not recs:
        recs = ["No strong, data-backed lever was found in this data. Collect more data or additional variables, or refine the goal, before acting."]
    return summary, recs


def _log(state: dict, reason: str, rejected: list[str], raw, dropped: list[str] | None = None) -> None:
    state.setdefault("decisions", []).append({
        "decision": "report_summary", "source": "fallback" if dropped is None else "mixed", "chosen": "rules summary" if dropped is None else "LLM summary with unverified sentences dropped",
        "why": reason, "errors": [f"rejected numbers: {', '.join(rejected) or 'none'}"] + (dropped or []), "raw_output": str(raw)[:500]})


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+|\n+", text) if s.strip()]


FACTS_MAX, GROUPS_MAX = 12, 10        # the prompt never grows with the data (a growth fact lists every group)


def _trim_rankings(rankings: list[dict], state: dict) -> list[dict]:
    """At most FACTS_MAX ranking facts of at most GROUPS_MAX groups each (the biggest risers and fallers); logs what was cut."""
    kept = sorted(rankings, key=lambda r: r.get("focus") is False)[:FACTS_MAX]
    out, shortened = [], 0
    for r in kept:
        groups = r.get("groups") or []
        if len(groups) > GROUPS_MAX and r.get("text_head"):
            half = GROUPS_MAX // 2
            lines = [g["line"] for g in groups[:half]] + [g["line"] for g in groups[-half:]]
            r = {**r, "text": r["text_head"] + "; ".join(lines) + f" ({len(groups) - GROUPS_MAX} groups in between left out)" + r.get("text_tail", "")}
            shortened += 1
        out.append(r)
    if len(kept) < len(rankings) or shortened:
        state.setdefault("decisions", []).append({"decision": "report_summary prompt trimmed", "source": "rules", "chosen": f"sent {len(kept)} of {len(rankings)} ranking facts",
                                                  "why": f"the prompt is capped at {FACTS_MAX} ranking facts of {GROUPS_MAX} groups each",
                                                  "errors": [f"{len(rankings) - len(kept)} facts not sent", f"{shortened} facts shortened to {GROUPS_MAX} groups"], "raw_output": ""})
    return out


def llm_summary(llm, state: dict) -> tuple[str, list[str], bool] | None:
    """(summary, recommendations, partial). Sentences/recommendations with numbers not in the findings are dropped; None only if nothing is left."""
    ctx_txt = ctxmod.context_text(state.get("context"))
    asked = summary_mod.questions(state)
    c = wording.counts(state["findings"])
    rankings = _trim_rankings((state.get("summary_facts") or {}).get("rankings") or [], state)
    ranked = _rank(state["findings"])[:12]
    payload = [{"id": f["id"], "headline": f["insight"]["headline"], "facts": f["text"].replace("**", ""), "confidence": f["insight"]["confidence"]["label"],
                "survives_correction": f["stats"].get("survives_correction")} for f in ranked] + [{"id": r["id"], "headline": r["text"], "facts": r["text"], "confidence": "computed from the data", "survives_correction": None} for r in rankings]
    scopes = {x["id"]: cited.scope_of(x) for x in [*ranked, *rankings]}
    facts = {p["id"]: cited.make_fact(p["headline"] + " " + p["facts"], p["headline"] + " " + p["facts"], scopes.get(p["id"], ("", ""))) for p in payload}
    for r in rankings:
        facts[r["id"]]["subject"] |= cited.tokens(f"{r['group']} {r['measure']} {r['top']} {r['bottom']} " + " ".join(r.get("values") or []))
    for f in ranked:
        facts[f["id"]]["subject"] |= cited.tokens(" ".join(str(v) for v in (f.get("params") or {}).values() if isinstance(v, str)) + " " + f.get("title", ""))
    facts["counts"] = cited.make_fact(json.dumps(c, ensure_ascii=False), "")
    facts["data"] = cited.make_fact(f"{state['profile']['rows']} {state['profile'].get('cols', '')}", "")
    if ctx_txt:
        facts["context"] = cited.make_fact(ctx_txt, "")
    computed = cited.claims((state.get("summary_facts") or {}).get("rankings") or [])
    with capture() as cap:
        out = ask_json(
            llm,
            "You are a senior data analyst writing for a business reader. Use ONLY the facts and numbers in the findings provided; do not invent numbers. "
            "Be plain and concise, distinguish association from causation, and respect the caveats. If company context is given, use its vocabulary. "
            + cited.CITE_RULE + "Use the id 'counts' for a number from COUNTS, 'data' for the row or column count and 'context' for a number from the company context. "
            "Return JSON: {\"summary\": \"3-5 sentence executive summary answering the goal\", \"recommendations\": [3-5 concrete, prioritized actions tied to specific findings], "
            "\"so_what\": [{\"question\": <number of a goal question below>, \"text\": \"ONE plain sentence on why that answer matters; no statistics terms\"}]}",
            (f"COMPANY CONTEXT (user-written, unverified):\n{ctx_txt}\n" if ctx_txt else "") +
            f"GOAL: {state['goal']}\nGOAL QUESTIONS: {json.dumps(dict(enumerate(asked)), ensure_ascii=False)}\nCOUNTS (quote these exactly, never recount): {json.dumps(c, ensure_ascii=False)}\nCAVEATS: {json.dumps([i['text'] for i in state['critique']['issues']][:8], ensure_ascii=False)}\nFINDINGS: {json.dumps(payload, default=str, ensure_ascii=False)}",
        )
    why = cap.reason
    if not out:
        _log(state, why or "bad JSON: the model returned nothing parseable", [], out)
        return None
    if not (isinstance(out.get("summary"), str) and isinstance(out.get("recommendations"), list) and out["recommendations"]):
        _log(state, "missing fields: need a string 'summary' and a non-empty 'recommendations' list", [], json.dumps(out, default=str))
        return None
    kept, dropped, rejected, used = [], [], [], []

    def judge(text: str, what: str) -> tuple[str, bool, dict | None]:
        """(text without ids, kept?, the computed fact it contradicts). A dropped text is recorded in `dropped` and `rejected`."""
        clean, bad = cited.verify(text, facts)
        wrong = None if bad else cited.contradicts(clean, computed)
        if wording.unsupported_profit_claim(clean, state):
            dropped.append(f"{what} dropped (no cost or profit column in the data): {clean}")
        elif bad:
            dropped.append(f"{what} dropped ({bad}): {clean}")
            rejected.append(bad)
        elif wrong:
            dropped.append(f"{what} dropped (disagrees with computed fact {wrong['id']}): {clean}")
            rejected.append(f"contradicts {wrong['id']}")
        else:
            return clean, True, None
        return clean, False, wrong

    for sent in _sentences(out["summary"]):
        clean, ok, wrong = judge(sent, "summary sentence")
        if ok:
            kept.append(clean)
        elif wrong and wrong["id"] not in used:       # the computed fact takes the place of the contradicting sentence
            used.append(wrong["id"])
            kept.append(wrong["text"].rstrip(".") + ".")
    recs = []
    for r in (str(r) for r in out["recommendations"]):
        clean, ok, _ = judge(r, "recommendation")
        if ok:
            recs.append(clean)
    so_what: dict[str, str] = {}
    for item in out.get("so_what") if isinstance(out.get("so_what"), list) else []:       # at most one sentence per goal question, every figure tied to the fact it cites
        try:
            q = asked[int(item["question"])]
            sent = _sentences(str(item["text"]))[:1]
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if sent and q not in so_what:
            clean, ok, _ = judge(sent[0], "so-what sentence")
            if ok:
                so_what[q] = clean
    state["so_what"] = so_what
    raw = json.dumps(out, default=str)
    if not kept:
        _log(state, "numbers_ok: no summary sentence survived verification", rejected, raw, None)
        return None
    if dropped:
        _log(state, "numbers_ok: some sentences/recommendations had figures not tied to the fact they cite and were dropped", rejected, raw, dropped)
    return " ".join(kept), recs[:6], bool(dropped)


def llm_actions(llm, state: dict, view: dict, cs: list[dict], wanted: list[int], slots: int) -> dict[str, str]:
    """One AI call: an action for each goal question that has a computed answer but no action. {question: sentence}. The model sees only facts an action may rest on
    (a medium or larger effect, or a descriptive fact on a group the goal names); every figure must keep its fact id, and a sentence citing no offered fact is dropped."""
    by_id, r_by_id, _ = mainview.lookup(state, view)
    offered: dict[int, dict[str, str]] = {}
    for c in cs:
        if c["n"] in wanted:
            ids = [i for i in c["ids"] if mainview.action_basis(i, state, by_id, r_by_id)]
            offered[c["n"]] = {i: (r_by_id[i]["text"] if i in r_by_id else f"{by_id[i]['insight']['headline']} {by_id[i].get('text', '')}".replace("**", "")) for i in ids}
    offered = {n: v for n, v in list(offered.items())[:slots] if v}
    if not offered:
        return {}
    facts = {}
    for n, fs in offered.items():
        for i, text in fs.items():
            facts[i] = cited.make_fact(text, text, cited.scope_of(r_by_id.get(i) or by_id[i]))
            if i in r_by_id:
                r = r_by_id[i]
                facts[i]["subject"] |= cited.tokens(f"{r['group']} {r['measure']} {r['top']} {r['bottom']} " + " ".join(r.get("values") or []))
    q_of = {c["n"]: c["question"] for c in cs}
    with capture() as cap:
        out = ask_json(
            llm,
            "You are a senior data analyst. For each goal question below propose ONE concrete business action that follows from the facts listed for that question. "
            "Use ONLY those facts and numbers; do not invent numbers. The action is a hypothesis to test, not a proven cause. " + cited.CITE_RULE +
            "Every action must end with the id of the fact it rests on in square brackets, even when it has no number. "
            "Return JSON: {\"actions\": [{\"question\": <question number>, \"text\": \"one plain sentence\"}]}",
            "GOAL QUESTIONS AND THEIR FACTS: " + json.dumps({n: {"question": q_of[n], "facts": {i: t for i, t in fs.items()}} for n, fs in offered.items()}, ensure_ascii=False))
    got, dropped, rejected = {}, [], []
    computed = cited.claims((state.get("summary_facts") or {}).get("rankings") or [])
    for item in (out or {}).get("actions") if isinstance((out or {}).get("actions"), list) else []:
        try:
            n, text = int(item["question"]), str(item["text"])
        except (KeyError, TypeError, ValueError):
            continue
        if n not in offered or q_of[n] in got:
            continue
        clean, bad = cited.verify(text, facts)
        marks = {i for _, ids in cited.split_cites(text)[1] for i in ids}
        wrong = None if bad else cited.contradicts(clean, computed)
        if bad or wrong or not marks & set(offered[n]) or wording.unsupported_profit_claim(clean, state):
            why = bad or (f"disagrees with computed fact {wrong['id']}" if wrong else "cites no fact offered for this question" if not marks & set(offered[n]) else "no cost or profit column in the data")
            dropped.append(f"action dropped ({why}): {clean}")
            rejected.append(why)
            continue
        got[q_of[n]] = _sentences(clean)[0]
    if dropped or not out or not got:
        state.setdefault("decisions", []).append({"decision": "report_actions", "source": "fallback" if not got else "mixed", "chosen": "no AI action" if not got else "AI actions that passed the fact-id check",
                                                  "why": cap.reason or "the model's actions were checked against the facts offered", "errors": dropped or ["the model returned no usable actions"], "raw_output": json.dumps(out, default=str)[:500]})
    return got


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
    L: list[str] = []
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


GROUP_PARAMS = ("group", "group_a", "group_b", "by")


def _group_cols(f: dict) -> list[str]:
    return [f["params"][k] for k in GROUP_PARAMS if f.get("params", {}).get(k)]


def split_insights(state: dict) -> tuple[list[dict], list[dict]]:
    """(on the goal's own terms, what drives the result). A finding on a column the goal names comes first. What the code added for coverage, and the splits
    by columns the goal does not name, come later. When the goal names no column the plan splits by, only the code-added findings come later."""
    goal = state.get("goal", "")
    plan_groups = {c for a in state["plan"]["analyses"] for k, c in a["params"].items() if k in GROUP_PARAMS}
    goal_named = any(named_in_goal(c, goal) for c in plan_groups)
    first, later = [], []
    for f in _rank(state["findings"]):
        cols = _group_cols(f)
        named = any(named_in_goal(c, goal) for c in cols)
        coverage = (f.get("origin") == "code" and not named) or (goal_named and bool(cols) and not named)
        (later if coverage else first).append(f)
    return first, later


def _details(summary: str, body: list[str]) -> list[str]:
    return ["<details>", f"<summary>{summary}</summary>", ""] + body + ["", "</details>", ""]


def _table_md(t: dict) -> list[str]:
    rows = ["| " + " | ".join(t["columns"]) + " |", "|" + "---|" * len(t["columns"])] + ["| " + " | ".join(str(c) for c in r) + " |" for r in t["rows"]]
    return rows + ([f"*Showing {len(t['rows'])} of {t['total']} rows; the Data and Dashboard tabs show all of them.*"] if t["total"] > len(t["rows"]) else [])


def _badge(c: dict) -> str:
    return f" · confidence: **{c['confidence']['label']}**" + (" · your KPI" if c.get("kpi_related") else "") + (f" · {DATE_TAG}" if c.get("date_dependent") else "") + f" · {', '.join(c['members'])}"


def _card_md(c: dict, level: str = "###") -> list[str]:
    """One card: the finding, its median check on one line, a ratio of the same measure as a sub-section, and per-group tables collapsed."""
    flag = " ✅" if c["confidence"]["level"] == "high" else ""
    L = [f"{level} {c['headline']}{flag}", "", f"*{c['title']}*{_badge(c)}", ""]
    if c.get("chart"):
        L += [f"![{c['title']}]({c['chart']})", ""]
    L += [f"**What we see.** {c['observation']}", ""]
    if c.get("why_it_matters"):
        L += [f"**Why it matters.** {c['why_it_matters']}", ""]
    L += ["**How much to trust it.** " + " ".join(c["confidence"]["reasons"]), ""]
    L += [f"- {x}" for x in c["checks"]] + ([""] if c["checks"] else [])
    for a in c["also"]:
        L += [f"**Same split, {a['target']}.** {a['headline']}. {a['observation']}"] + [f"- {x}" for x in a["checks"]] + [""]
    for t in c["tables"] + [t for a in c["also"] for t in a["tables"]]:
        L += _details(f"Per-group figures: {t['title']}", _table_md(t))
    if c.get("hypotheses"):
        L += ["**Possible explanations to test (not conclusions):**"] + [f"- {h}" for h in c["hypotheses"]] + [""]
    if c.get("check_next"):
        L += ["**Check next:**"] + [f"- {x}" for x in c["check_next"]] + [""]
    return L


def _evidence_section(view: dict) -> list[str]:
    ev = view["evidence"]
    if not ev:
        return ["No finding named by the goal, or reliable with a medium or larger effect, was found.", ""]
    L = [f"The {len(ev)} findings that matter most: named by your goal, or still significant after correction with a medium or larger effect. Ordered by importance.", ""]
    for c in ev:
        L += _card_md(c)
    return L


def _checked_section(view: dict) -> list[str]:
    ch = view["checked"]
    if not ch:
        return []
    return ["Checked, nothing found: tested and not statistically significant, so no reliable pattern.", ""] + [f"- {c['line']}" + (f" {c['check']}" if c.get("check") else "") + (f" [{DATE_TAG}]" if c.get("date_dependent") else "") + f" ({c['id']})" for c in ch] + [""]


def _strategy_section(state: dict, view: dict) -> list[str]:
    st = view["strategies"]
    n_all = len([s for s in state.get("strategies") or [] if s.get("kind") == "data"])
    if st["strategies"]:
        L = [f"{len(st['strategies'])} data-backed strateg{'y' if len(st['strategies']) == 1 else 'ies'}" + (f" (merged from {n_all} candidates of the same kind)" if n_all > len(st["strategies"]) else "")
             + f", ranked by expected impact, confidence and effort. Each is a hypothesis to test with a small pilot first: the analysis shows association, not cause.", ""]
    else:
        L = [state.get("strategy_summary", ""), ""]
    for s in st["strategies"]:
        L += [f"### {s['rank']}. {s['title']}", "",
              f"*Data-backed · impact: **{s['impact']}** · effort: **{s['effort']}** · timeline: **{s['horizon']}**" + (f" · confidence: **{str(s['confidence']).capitalize()}**" if s["confidence"] else "") + f" · based on {', '.join(s['evidence'])}*", "",
              f"**Objective.** {s['objective']}", ""]
        L += ["**Actions**"] + [f"{i + 1}. {a}" for i, a in enumerate(s["actions"])] + [""]
        L += [f"**How to test it first.** {s['how_to_test']}", "", "**KPIs to track:** " + "; ".join(s["kpis"]), ""]
        L += ["**Risks**"] + [f"- {r}" for r in s["risks"][:2]] + [""]
    if st["dropped"]:
        L += [f"*{st['dropped']} lower-ranked strateg{'y' if st['dropped'] == 1 else 'ies'} not shown; the Strategy tab of the app lists all of them.*", ""]
    if st["foundation"]:
        L += ["**Foundations**", ""] + [f"- {f['line']}" for f in st["foundation"]] + [""]
    return L


def _profile_section(view: dict) -> list[str]:
    p = view["profile"]
    body = []
    for title, key in (("Distributions", "distributions"), ("Outliers (one line per column)", "outliers"), ("Composition", "composition")):
        if p[key]:
            body += [f"**{title}**", ""] + [f"- {x['line']}" for x in p[key]] + [""]
    for t in p["tables"]:
        body += _details(f"Per-group figures: {t['title']}", _table_md(t))
    return _details(f"Data profile ({sum(len(p[k]) for k in ('distributions', 'outliers', 'composition'))} lines)", body) if body else []


GROWTH_LINES = 8        # growth lines per table in the details; the rest are in the appendix
NOT_RELIABLE = ", not statistically reliable"


def _growth_title(r: dict) -> str:
    return f"Growth in {r['measure']} by {r['group']}, {r['periods'][0]} vs {r['periods'][1]}" + (f" [{DATE_TAG}]" if r.get("date_dependent") else "")


def _growth_split(view: dict) -> tuple[list[tuple[dict, list[dict], int]], list[tuple[dict, list[dict]]]]:
    """(growth tables as (fact, lines shown, lines moved), appendix tables as (fact, lines)). A table of the goal's main measure by a group column the goal names is a growth table
    (largest absolute changes first); a secondary measure, or a column the goal does not name, goes to the appendix."""
    main, later = [], []
    measure = view.get("main_measure")
    for r in (x for x in view.get("rankings") or [] if x.get("kind") == "growth"):
        rows = sorted(r["groups"], key=lambda g: -abs(g["change"]))
        if r.get("focus") and (not measure or r["measure"] == measure):
            main.append((r, rows[:GROWTH_LINES], max(0, len(rows) - GROWTH_LINES)))
        else:
            later.append((r, rows))
    return main, later


def _growth_rest(r: dict) -> list[dict]:
    return sorted(r["groups"], key=lambda g: -abs(g["change"]))[GROWTH_LINES:]


def _growth_lines(rows: list[dict]) -> list[str]:
    """One line per group; the 'not statistically reliable' note is said once for the table, not on every line."""
    out = [f"- {g['line'].replace(NOT_RELIABLE, '')}" for g in rows]
    return out + (["", "*Lines without 'statistically reliable' are not statistically reliable (Welch test on sub-period totals, corrected for the number of groups).*"] if any(NOT_RELIABLE in g["line"] for g in rows) else [])


def _growth_section(view: dict) -> list[str]:
    L: list[str] = []
    for r, rows, n in _growth_split(view)[0]:
        L += _details(_growth_title(r), _growth_lines(rows) + (["", f"- and {n} more in the appendix"] if n else []))
    return L


def _outside_section(view: dict) -> list[str]:
    return [f"- {x['text']}" for x in view.get("rankings") or [] if x.get("kind") == "outside_rate"]


def _other_section(view: dict) -> tuple[str, list[str]]:
    other = [x for x in view.get("rankings") or [] if x.get("kind") != "growth" and x.get("basis") == "total" and x.get("focus") is False]
    return (f"{other[0]['measure']} by other categories" if other else ""), [f"- {x['text']}" for x in other]


def _appendix_section(view: dict) -> list[str]:
    L: list[str] = []
    main, later = _growth_split(view)
    for r, _, n in main:
        if n:
            L += _details(_growth_title(r) + " (remaining groups)", _growth_lines(_growth_rest(r)))
    for r, rows in later:
        L += _details(_growth_title(r), _growth_lines(rows))
    if view["appendix"]:
        body: list[str] = []
        for c in view["appendix"]:
            body += _card_md(c, "####")
        L += _details(f"More findings ({len(view['appendix'])}): reliable but small, or not named by the goal", body)
    return L + _profile_section(view)


def _setup_section(state: dict) -> list[str]:
    plan, prof, ctx = state["plan"], state["profile"], state.get("context") or {}
    L: list[str] = []
    cn = state.get("concerns") or {}
    if cn.get("path") == "ai":
        L += [f"**Data concerns:** proposed by the AI, verified by code ({len(cn.get('accepted') or [])} accepted, {len(cn.get('rejected') or [])} not applied).", ""]
    elif cn.get("path") == "rules":
        L += ["**Data concerns:** found by rule checks (" + (f"AI unavailable: {cn['why_not']}" if cn.get("why_not") and cn["why_not"] != "no AI configured" else "no AI configured") + ").", ""]
    if ctxmod.has_context(ctx):
        lens = ctxmod.lens(ctx)
        L += ["### About the business (as you described it)", ""]
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
    L += ["", "### What we set out to answer", "", f"**Objective:** {plan['objective']}", ""] + [f"- {q}" for q in plan["questions"]]

    dm = state.get("data_model")
    if dm:
        L += ["", "### Data sources and how they were connected", "",
              f"{len(dm['tables'])} tables were uploaded: " + ", ".join(f"**{t['name']}** ({t['rows']:,} rows)" for t in dm["tables"]) + ".",
              f"The analysis uses **one row per `{dm['base_table']}` row** ({dm['base_reason']}).", ""]
        for r in dm["relationships"]:
            mark = {"used": "✅", "skipped": "⏭️", "rejected": "🚫"}.get(r["status"], "•")
            L.append(f"- {mark} `{r['child_table']}.{r['child_col']}` → `{r['parent_table']}.{r['parent_col']}` ({r['cardinality']}, {r['child_row_match']:.0%} of rows match)" + (f": {r['reason']}" if r.get("reason") else ""))
        L += [""] + [f"- {s['detail']}" for s in dm["steps"]]
        ignored = dm.get("ignored_tables") or []
        if ignored:
            L.append("- Ignored (not used in the analysis of **" + dm["base_table"] + "**): " + ", ".join(f"**{t['name']}** ({t['rows']:,} rows)" for t in ignored) + ".")
        elif not dm["relationships"]:
            L.append("- No key links were found between the tables.")

    L += ["", "### Data and cleaning", "",
          f"{prof['rows']:,} rows × {prof['cols']} columns. Columns used: {', '.join(f'`{c}`' for c in plan['relevant_columns']) or 'n/a'}.", ""]
    L += [f"- **{s['step']}**: {s['detail']}" for s in state["cleaning_log"]] or ["- No cleaning changes were needed."]
    L += und.setup_lines(state.get("understanding"))
    return L + [f"- {r}" for r in (state.get("concerns") or {}).get("rejected") or []]


GAP_TAG = "Key data gaps:"


def _header_warnings(rows: list[str]) -> tuple[int, int, int]:
    """(index of the Mode line, first, end) of the run of '- ⚠' lines right under it."""
    at = next((i for i, r in enumerate(rows) if r.startswith("**Mode:**")), 0)
    first = end = at + 2
    while end < len(rows) and rows[end].startswith("- ⚠ "):
        end += 1
    return at, first, end


AI_PREFIX = "- ⚠ AI unavailable for "


def _merge_ai(lines: list[str]) -> list[str]:
    """The 'AI unavailable for <stage>: <reason>' lines become one line, stages grouped by reason: 'AI unavailable for a, b: rate limit; AI unavailable for c: timeout'. It takes the place of the first of them."""
    ai = [x for x in lines if x.startswith(AI_PREFIX) and ": " in x]
    if len(ai) < 2:
        return lines
    groups: dict[str, list[str]] = {}
    for x in ai:
        stage, reason = x[len(AI_PREFIX):].rsplit(": ", 1)
        groups.setdefault(reason, []).append(stage)
    one = "; ".join(f"{AI_PREFIX if k == 0 else 'AI unavailable for '}{', '.join(st)}: {reason}" for k, (reason, st) in enumerate(groups.items()))
    first = lines.index(ai[0])
    return [one if i == first else x for i, x in enumerate(lines) if x not in ai[1:]]


def with_notices(md: str, lines: list[str]) -> str:
    """Puts warnings (stage failed: ..., AI unavailable for ...) under the Mode line, after the date warnings and before the data gaps. A line already in the report is not repeated.
    At most mainview.WARNINGS_MAX warnings are shown; the rest go to a collapsed 'More notices' section at the end."""
    new = [x for x in dict.fromkeys(lines) if x and x not in md]
    if not new:
        return md
    rows = md.split("\n")
    at, first, end = _header_warnings(rows)
    have = rows[first:end]
    combined = [x for x in have if GAP_TAG not in x] + [f"- ⚠ {x}" for x in new] + [x for x in have if GAP_TAG in x]
    combined = _merge_ai(combined)
    shown, over = combined[:mainview.WARNINGS_MAX], combined[mainview.WARNINGS_MAX:]
    out = rows[:at + 1] + [""] + shown + ([""] if end == first else []) + rows[end:] if end > first else rows[:at + 1] + [""] + shown + rows[at + 1:]
    text = "\n".join(out)
    if over:
        text += "\n\n" + "\n".join(_details("More notices", over))
    return text


def _warnings(state: dict) -> list[str]:
    """Date problems first, then the key data gaps (a warning-level gap, or a goal word no column holds) as one line."""
    out = []
    if dates_unconfirmed(state):
        out.append(DATE_CAUTION)
    if state.get("date_coverage_warning"):
        out.append(state["date_coverage_warning"])
    titles = [mainview.readable(state, g["title"]) for g in state.get("data_gaps") or [] if g.get("severity") == "warn"] + [x.rstrip(".") for x in und.unmapped_lines(state.get("understanding"))]
    if titles:
        out.append(f"{GAP_TAG} " + "; ".join(titles[:2]) + (f" (and {len(titles) - 2} more under 'Data gaps')" if len(titles) > 2 else "") + ".")
    return out


def _answer_details(state: dict, view: dict, cs: list[dict], summary: str) -> list[str]:
    """Exact figures behind every card: the full answer, the facts it rests on, why to trust it, and the statistics (kept out of the main view)."""
    by_id, r_by_id, _ = mainview.lookup(state, view)
    answers = {a["question"]: a for a in view.get("answers") or []}
    L: list[str] = []
    for c in cs:
        a = answers[c["question"]]
        L += [f'<a id="answer-q{c["num"]}"></a>', "", f"**{c['num']}. {c['question']}**", "", f"- Answer: {a['answer']}"]
        for i in a["ids"]:
            if i in by_id:
                f = by_id[i]
                ins, st = f.get("insight") or {}, f.get("stats") or {}
                conf = ins.get("confidence") or {}
                eff, p = tiering.effect_label(st), tiering._p_text(f)
                L.append(f"- {i}: {ins.get('headline') or f.get('title', '')} · confidence: {conf.get('label', 'n/a')}" + (f" · {p}" if p else "") + (f" · effect: {eff}" if eff else "")
                         + (" · " + " ".join(conf["reasons"]) if conf.get("reasons") else ""))
            elif i in r_by_id:
                L.append(f"- {i}: {r_by_id[i]['text']}")
        if c["so_what"]:
            L.append(f"- So what (AI, figures checked against the facts): {c['so_what']}")
        L.append("")
    if view.get("headlines"):
        L += ["**Headline findings**", ""] + [f"- {h['text']} ({h['id']})" for h in view["headlines"]] + [""]
    return L + ["**Written summary**", "", summary, ""]


def build_report(state: dict, summary: str, recs: list[str], run_mode: str = "", view: dict | None = None) -> str:
    ctx = state.get("context") or {}
    view = view or state.get("report_view") or tiering.build_view(state)
    title = "# Analysis report" + (f": {ctx['company']}" if ctx.get("company") else "")
    warns = _warnings(state)
    cs = mainview.cards(state, view)
    plan = mainview.plan_actions(state, view, cs)
    answer_cards = [c for c in cs if c["num"]]          # an action question has no card: the 'What to do' section answers it
    L = [title, "", f"**Mode:** {run_mode or derive_mode(state.get('stage_sources'))}", ""] + [f"- ⚠ {w}" for w in warns[:mainview.WARNINGS_MAX]] + ([""] if warns else [])

    L += ["## Executive summary", "", "### Answers", ""]          # B. one card per goal question
    for c in answer_cards:
        L += mainview.card_md(c)
    if not cs:
        L += ["No question was asked in the goal; the key numbers and the sections under 'view more' describe the data.", ""]
    also = [mainview.readable(state, x) for x in [f"**By {r['group']}** (named in your goal). {mainview.clip(r.get('short') or r.get('lead') or r['text'])}" for r in summary_mod.uncovered_groups(state, view)] + summary_mod.difference_lines(state)]
    if also:
        L += ["**Also named in your goal**", ""] + [f"- {x}" for x in also[:3]] + [""]
    L += ["### What to do", ""] + mainview.actions_md(cs, plan) + [""]          # C

    ts = mainview.tiles(state, view)          # D
    if ts:
        L += ["## Key numbers", ""] + mainview.tiles_md(ts) + [""]

    L += ["---", "", "**View more**", ""]          # everything else, collapsed, always in this order
    L += _details("Answer details: exact figures, facts and statistics", _answer_details(state, view, answer_cards, summary))
    L += _details(f"Evidence ({len(view.get('evidence') or [])} findings)", _evidence_section(view))
    L += _growth_section(view)
    outside = _outside_section(view)
    L += _details("Rate outside the main value", outside) if outside else []
    other_title, other = _other_section(view)
    L += _details(other_title, other) if other else []
    ch = _checked_section(view)
    L += _details(f"Checked, nothing found ({len(view['checked'])})", ch) if ch else []
    L += _details("Strategy and Recommendations", (["**Top recommendations**", ""] + [f"- {r}" for r in recs[:tiering.ACTIONS_MAX]] + [""] if recs else []) + _strategy_section(state, view))
    L += _details("How the data was set up (business, sources, cleaning)", _setup_section(state))
    defs = _definitions_section(state)
    L += _details("Definitions used", defs) if defs else []
    stats = []
    gaps = state.get("data_gaps") or []
    if gaps:
        stats += ["**Data gaps and what would sharpen this**", ""] + [f"- {'⚠️ ' if g['severity'] == 'warn' else ''}**{g['title']}.** {g['detail']} *{g['suggestion']}*" for g in gaps] + [""]
    stats += ["**Reliability and limitations**", ""]
    stats += [f"- {'⚠️ ' if i['severity'] == 'warn' else ''}{i['text']}" for i in state["critique"]["issues"]] or ["- No issues flagged."]
    stats += ["- These are statistical associations in observational data; they do not prove cause and effect. Strategies are hypotheses to test.", ""]
    km = view.get("numbers") or state.get("key_metrics") or []
    if len(km) > len(ts):
        stats += ["**All key numbers**", ""] + [f"- **{m['label']}:** {m['value']}" + (f" ({m['note']})" if m.get("note") else "") for m in km] + [""]
    L += _details("Statistics and limitations", stats)
    L += _details("Appendix", _appendix_section(view))
    L += _details("Power BI", ["A ready-to-load Power BI pack (cleaned data, data model, DAX measures and visual specs) was generated alongside this report."])
    if len(warns) > mainview.WARNINGS_MAX:
        L += _details("More notices", [f"- ⚠ {w}" for w in warns[mainview.WARNINGS_MAX:]])
    return "\n".join(L)


def write_report(state: dict, llm) -> tuple[str, str]:
    res = llm_summary(llm, state) if llm is not None else None
    mode = ("mixed" if res[2] else "llm") if res else "rules"
    if res:
        summary, recs = res[0], res[1]
        if not recs:
            recs, mode = rule_summary(state)[1], "mixed"
    else:
        summary, recs = rule_summary(state)
    ans = state.get("goal_answer")
    view = state.get("report_view") or tiering.build_view(state)
    # a column the goal names that no answer uses is still answered, from the computed ranking
    lines = [f"- **By {r['group']}** (named in your goal). {r.get('lead') or r['text']}" for r in summary_mod.uncovered_groups(state, view)] + [f"- {x}" for x in und.unmapped_lines(state.get("understanding"))]      # a word of the goal that no column holds is said, not silently dropped
    if lines:
        summary = "**Also named in your goal**\n" + "\n".join(lines) + "\n\n" + summary
    diff = summary_mod.difference_lines(state)
    if diff:      # a ratio KPI that does not reliably differ across a group the goal names is said in the summary, one line each
        summary = "\n".join(diff) + "\n\n" + summary
    if ans:      # the goal is answered first, in the app's own words and numbers, whether or not an LLM wrote the rest
        summary = f"**Answer to your goal.** {ans['sentence']}" + (f" {ans['groups_sentence']}" if ans.get("groups_sentence") else "") + "\n\n" + summary
    if llm is not None:      # one more AI call, only for a goal question that has a computed answer and a fact an action may rest on but no action
        cs = mainview.cards(state, view)
        plan = mainview.plan_actions(state, view, cs)
        if plan["open"]:
            state["ai_actions"] = llm_actions(llm, state, view, cs, plan["open"], mainview.ACTIONS_MAX - len(plan["actions"]))
    return build_report(state, summary, recs, derive_mode({**(state.get("stage_sources") or {}), "report": mode}), view), mode
