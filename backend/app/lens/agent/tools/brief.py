"""brief: a short document for any reader, in a fixed layout, written from verified facts only.

The AI infers what the reader cares about from the request, the company context in the run, the understanding record and the data, and returns a brief plan in this step's arguments: audience,
length, focus, and up to 6 evidence queries (data_query, custom_analysis or report_facts). Code validates and runs the queries; each result becomes a fact with its own id. A second AI call writes the
brief from those facts only; every figure must be in the fact list and pass the fact-id checks, and a line that fails is dropped. If too few lines survive, the facts' own computed sentences are used.
Layout: a headline, up to 3 key figures, up to 3 recommendations, up to 2 charts, then a data-quality note that always shows the trust label and any gaps (built by code, never by the AI).
If the data has no columns that bear on the focus, the brief says so and invents nothing. Each brief is saved with the run."""
from __future__ import annotations

import json
import re

from .... import llm as llmmod
from .... import understanding as und
from ... import briefs as bf
from ... import howtext, records, reports, store
from .. import registry, sqlguard, verify
from ..registry import Tool, ToolResult, register

MAX_BRIEFS, MAX_EVIDENCE = 4, 6
EVIDENCE_TOOLS = ("data_query", "custom_analysis", "report_facts")
CAUSAL = re.compile(r"\b(because|due to|caused?|driven by|as a result of|thanks to|owing to|the reason)\b", re.I)
WRITE_SYSTEM = """You write a brief for ONE reader from checked facts only. The reader, purpose, length and focus are given; decide yourself which facts matter to this reader and say them plainly.
Reply with exactly one JSON object: {"headline":"<one sentence>","key_figures":["<one sentence with one figure>", ... at most 3],"recommendations":["<one action sentence>", ... at most 3]}
Rules: use ONLY the numbers in FACTS, written exactly as in the fact, and put the id of the fact in square brackets right after each figure, like [q1]. Every recommendation must cite the id of the fact it rests on.
Never state a cause, never use a number from your own knowledge, never invent a column, a value or a fact. If the facts do not support a recommendation, write fewer. Return the JSON object only."""


def _no_info_text(focus: str) -> str:
    f = " ".join(str(focus).split()).strip(" .")
    return f"The data has no {f} information." if f else "The data has nothing that bears on this request."


def bears_on(env, focus: str, columns: list) -> bool:
    """Whether any column of the data bears on the focus: a column the AI names that exists, or a word of the focus found in a column's name, meaning, or its values."""
    if not terms_of(focus):
        return True
    if any(env.ctx.find(c) for c in columns or [] if isinstance(c, str)):
        return True
    words = set(terms_of(focus))
    report_text = " ".join([str(env.result.get("goal") or "")] + [f["text"] for f in records.fact_sheet(env.result)] + [str(q) for q in (env.result.get("plan") or {}).get("questions") or []])
    stem = lambda ws: {w[:4] if len(w) >= 5 else w for w in ws}
    seen = stem(bf.terms(report_text))
    if stem(words) & seen:
        return True                    # the report itself speaks about it
    for c in env.ctx.cols.values():
        hay = set(bf.terms(f"{c.name} {c.display} {c.meaning}")) | {t[:5] for t in bf.terms(f"{c.name} {c.display} {c.meaning}")}
        if words & hay or {w[:5] for w in words} & hay:
            return True
        if c.role in ("category", "flag") and c.name in env.df.columns and env.df[c.name].nunique(dropna=True) <= 200:
            vals = " ".join(map(str, env.df[c.name].dropna().unique()[:200])).lower()
            if any(w in vals for w in words if len(w) > 3):
                return True
    return False


def terms_of(text: str) -> list[str]:
    return bf.terms(text)


def _context_for_writer(env) -> dict:
    c = env.result.get("context") or {}
    return {k: records._cut(c.get(k), 200) for k in ("company", "industry", "description", "kpis", "audience", "currency") if c.get(k)}


class Brief(Tool):
    name = "brief"
    description = ("Write one or several briefs (any reader, any purpose, free text). Each: {\"audience\":<who reads it, free text>,\"length\":<short phrase>,\"focus\":<what they care about, free text, or empty>,"
                   "\"columns\":[<columns that bear on the focus>],\"evidence\":[up to 6 steps, each {\"tool\":\"data_query|custom_analysis|report_facts\",\"args\":{...}}]}. Up to 4 briefs.")
    args = {"briefs": {"type": "list", "required": True, "max_len": MAX_BRIEFS, "help": "brief plans"}}
    soft_validation = True

    def validate(self, args, env):
        errs = []
        tools = registry.load_tools()
        for i, b in enumerate(args.get("briefs") or [], 1):
            if not isinstance(b, dict) or not str(b.get("audience") or "").strip():
                errs.append(f"Brief {i} needs an audience.")
                continue
            ev = b.get("evidence") or []
            if not isinstance(ev, list) or len(ev) > MAX_EVIDENCE:
                errs.append(f"Brief {i} may use at most {MAX_EVIDENCE} evidence queries.")
                continue
            for j, e in enumerate(ev, 1):
                t = tools.get(e.get("tool")) if isinstance(e, dict) else None
                if t is None or t.name not in EVIDENCE_TOOLS:
                    errs.append(f"Brief {i}, evidence {j}: use one of {', '.join(EVIDENCE_TOOLS)}.")
                    continue
                bad = registry.check_schema(t, e.get("args") if e.get("args") is not None else {})
                if bad:
                    errs.append(f"Brief {i}, evidence {j}: {bad[0]}")
        return errs

    # ------------------------------------------------------------------ evidence
    def _gather(self, env, plan: dict, prior) -> tuple[list[dict], list[dict], list[str], bool, list[str]]:
        """(fact list [{id, text}], charts, report finding ids used, custom analysis used, notes)."""
        tools = registry.load_tools()
        facts, charts, fids, custom, notes = [], [], [], False, []
        for e in (plan.get("evidence") or [])[:MAX_EVIDENCE]:
            t = tools[e["tool"]]
            if t.needs_ai and not env.ai_ok:
                notes.append(f"{t.name} was not available, so one evidence query was skipped.")
                continue
            if t.validate(e.get("args") or {}, env) and t.name != "data_query":
                notes.append(f"One {t.name} evidence query was invalid and was skipped.")
                continue
            try:
                r = t.run(e.get("args") or {}, env, [])
            except Exception:             # noqa: BLE001
                notes.append(f"One {t.name} evidence query failed and was skipped.")
                continue
            env.facts.update(r.facts)
            if not r.ok or not r.answer:
                notes.append(f"One {t.name} evidence query could not be run and was skipped.")
                continue
            a = r.answer
            if t.name == "report_facts":
                for f in r.data.get("facts") or []:
                    facts.append({"id": f["id"], "text": f["text"]})
                    fids.append(f["id"])
            else:
                ids = a.get("fact_ids") or list(r.facts)
                if ids:
                    text = records._cut(a.get("text"), 300)
                    res = r.data.get("result")
                    if t.name == "custom_analysis" and res is not None:
                        ai_view = sqlguard.for_ai(res)           # only an aggregated result, at most 50 rows and 12 columns; rows that are not aggregated are never passed on
                        if ai_view:
                            text = records._cut(text + " Result: " + "; ".join(", ".join(f"{c}={v}" for c, v in zip(ai_view["columns"], row)) for row in ai_view["rows"][:12]), 700)
                    facts.append({"id": ids[0], "text": text})
                if (a.get("chart") or {}).get("path") and len(charts) < bf.MAX_CHARTS:
                    charts.append({"path": a["chart"]["path"], "title": records._cut(a.get("text"), 80)})
            custom = custom or t.name == "custom_analysis"
        return facts, charts, fids, custom, notes

    # ------------------------------------------------------------------ the code-only parts: the report's own sentences
    def _base_facts(self, env, focus: str) -> list[dict]:
        sheet = records.fact_sheet(env.result)
        w = set(terms_of(focus))
        if w:
            sheet = sorted(sheet, key=lambda f: -len(w & set(bf.terms(f["text"]))))
        return [{"id": f["id"], "text": f["text"]} for f in sheet[:8]]

    def _code_parts(self, env, facts: list[dict], focus: str):
        headline = {"text": facts[0]["text"], "ids": [facts[0]["id"]]} if facts else None
        figs = [{"text": f["text"], "ids": [f["id"]]} for f in facts[1:1 + bf.MAX_FIGURES]]
        acts = records.action_items(env.result)
        w = set(terms_of(focus))
        if w:
            acts = sorted(acts, key=lambda a: -len(w & set(bf.terms(a["text"]))))
        recs = [{"text": records._plain(a["text"]), "ids": list(a.get("evidence") or [])} for a in acts[:bf.MAX_RECS]]
        return headline, figs, recs

    def _report_charts(self, env, focus: str) -> list[dict]:
        page = (env.result.get("report_view") or {}).get("page") or {}
        w = set(terms_of(focus))
        cs = sorted(page.get("charts") or [], key=lambda c: -len(w & set(bf.terms(c.get("title", "") + " " + c.get("caption", "")))))
        return [{"path": c["path"], "title": c.get("title", "")} for c in cs[:bf.MAX_CHARTS] if c.get("path")]

    # ------------------------------------------------------------------ the writer
    def _write(self, env, plan: dict, facts: list[dict]):
        """(headline, figures, recs, dropped, reasons) from the AI, verified; None when the AI gave nothing usable."""
        user = ("AUDIENCE: " + str(plan.get("audience")) + "\nLENGTH: " + str(plan.get("length") or "short") + "\nFOCUS: " + str(plan.get("focus") or "(none given)")
                + "\nREQUEST: " + env.question + "\nCOMPANY CONTEXT: " + json.dumps(_context_for_writer(env), ensure_ascii=False)
                + "\nSAVED DEFINITIONS: " + json.dumps([{"question": d.get("question"), "answer": d.get("answer_label") or d.get("answer")} for d in (env.result.get("applied_definitions") or [])[:8]], ensure_ascii=False)
                + "\nFACTS (id, text): " + json.dumps(facts, ensure_ascii=False))
        env.spend(1)
        obj, why = llmmod.ask_json_ex(llmmod.deterministic(env.llm), WRITE_SYSTEM, user, repair=False)
        if not obj:
            return None
        computed = records.computed_claims(env.result)

        def keep(lines, need_figure=False, limit=3):
            good, bad = verify.filter_lines([x for x in (lines if isinstance(lines, list) else [lines]) if isinstance(x, str)], env.facts, computed)
            out, more = [], 0
            for g in good:
                t = g["text"]
                if not g["ids"] or CAUSAL.search(t) or len(t) > 300 or (need_figure and not re.search(r"\d", t)):
                    more += 1
                    continue
                out.append(g)
            return out[:limit], len(bad) + more
        h, d1 = keep(obj.get("headline"), limit=1)
        f, d2 = keep(obj.get("key_figures"), need_figure=True, limit=bf.MAX_FIGURES)
        r, d3 = keep(obj.get("recommendations"), limit=bf.MAX_RECS)
        return (h[0] if h else None), f, r, d1 + d2 + d3

    # ------------------------------------------------------------------ run
    def run(self, args, env, prior):
        saved, answers_lines, all_ids, calls_before = [], [], [], env.calls
        ai_for_writing = env.ai_ok and env.llm is not None
        notes_all: list[str] = []
        for plan in (args.get("briefs") or [])[:MAX_BRIEFS]:
            audience = " ".join(str(plan.get("audience") or "the reader").split())[:120]
            focus = " ".join(str(plan.get("focus") or "").split())[:120]
            title = f"Brief for {audience}"
            if not bears_on(env, focus, plan.get("columns") or []):
                b = bf.new_brief(title, audience, focus, plan, None, [], [], [], bf.quality_note(env.result, []), "code", no_info=_no_info_text(focus))
                saved.append(b)
                continue
            facts, charts, fids, custom, notes = self._gather(env, plan, prior)
            notes_all += notes
            base = self._base_facts(env, focus)
            have = {f["id"] for f in facts}
            facts_all = facts + [f for f in base if f["id"] not in have]
            known = records.known_facts(env.result)
            for f in facts_all:
                if f["id"] in known:
                    env.facts.setdefault(f["id"], known[f["id"]])
            written, source, dropped = None, "code", 0
            if ai_for_writing and facts_all:
                written = self._write(env, plan, facts_all)
            h, figs, recs = None, [], []
            if written:
                h, figs, recs, dropped = written
                source = "ai"
            if not h or (1 if h else 0) + len(figs) + len(recs) < 2:                 # too few lines survived: the facts' own computed sentences
                ch, cf, cr = self._code_parts(env, facts_all, focus)
                h = h or ch
                figs = figs or cf
                recs = recs or cr
                source = "code" if not written else "ai+code"
            used = list(dict.fromkeys(i for x in ([h] if h else []) + figs + recs for i in x["ids"]))
            charts = charts or self._report_charts(env, focus)
            b = bf.new_brief(title, audience, focus, {k: plan.get(k) for k in ("audience", "length", "focus", "columns")}, h, figs, recs, charts, bf.quality_note(env.result, used + fids, custom),
                             source, dropped=dropped, fact_texts={f["id"]: f["text"] for f in facts_all})
            saved.append(b)
            all_ids += used
        # readable names only, in everything that is shown or saved
        for b in saved:
            for part in ([b["headline"]] if b.get("headline") else []) + b["key_figures"] + b["recommendations"]:
                part["text"] = und.readable(part["text"], env.result)
            b["quality"]["notes"] = [und.readable(n, env.result) for n in b["quality"]["notes"]]
        store.put_briefs(env.wd, store.get_briefs(env.wd) + saved)
        n = len(saved)
        dropped_total = sum(b["dropped"] for b in saved)
        how = howtext.record_how(f"Wrote {n} brief{'s' if n != 1 else ''} from verified facts", ["the report's checked facts", "the evidence queries the plan listed"], all_ids,
                                 ["Every figure in a brief is in the fact list and passed the fact-id check; a line that failed was left out." + (f" {dropped_total} line{'s' if dropped_total != 1 else ''} did not pass." if dropped_total else ""),
                                  "The data-quality note is written by code from the saved report and always shows the trust label."] + notes_all)
        text = saved[0]["no_info"] or (saved[0]["headline"] or {}).get("text") or "Here is the brief." if n == 1 else f"Here are {n} briefs: " + "; ".join(b["title"] for b in saved) + "."
        chips = reports.next_chips(env.result, "summary", "", reports.prev_chip_ids(env.wd))
        ans, _ = reports._base("brief", text, "Brief" if n == 1 else "Briefs", how, chips, 0, briefs=saved, lines=[], fact_ids=list(dict.fromkeys(all_ids)))
        return ToolResult(summary=f"Wrote {n} brief{'s' if n != 1 else ''} for " + ", ".join(b["audience"] for b in saved), answer=ans, chips=chips, ai_calls=env.calls - calls_before)


register(Brief)
