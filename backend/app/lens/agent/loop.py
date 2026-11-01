"""The loop: context -> plan (one AI call) -> validate -> act (steps in order) -> verify -> answer.

 1. Context: the last 6 turns, the tab, the visible items, the focus, the understanding record, the saved definitions, the report's fact sheet. Never raw rows.
 2. Plan: one AI call returns a plan of at most 4 steps; code validates it against the registry. An invalid plan gets ONE repair attempt, then the rule router (the no-AI fallback), then
    "here is what I can do" with chips. A request no tool can do is written to lens_unmet.jsonl in the run folder.
 3. Act: steps run in order, later steps can use earlier results; the first failure stops the run and the answer says what was done.
 4. Verify: a sentence the AI wrote (the plan's reply, a brief's lines) is kept only if every figure in it comes from a tool result or the fact sheet and passes the fact-id citation check.
 5. Answer: every tool phrases its own result in code, so there is no separate wording call; the answer carries the sentence, any chart or table, "How this was calculated" listing each step
    and tool, and (for view changes) one Undo for the whole request.
Normally at most 2 AI calls per request (the plan, and one repair); custom analysis may use up to 2 extra to repair its query."""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path

from ... import viewedit
from .. import howtext, records, reports, rules, store, viewchat
from . import errors, planner, registry, suggest, verify
from .env import Env

UNMET_FILE = "lens_unmet.jsonl"
UNUSABLE = ("the reply was not a plan", "no usable reply", "bad JSON")        # the model answered, but not with a plan (as against: it could not be reached)
CAN_DO = ("I can answer questions about your data, explain the report, summarise it, give you a brief for any reader, change what is shown, redraw charts, "
          "look up definitions and export a PDF.")


@dataclass
class Out:
    ans: dict | None = None
    chips: list[dict] = field(default_factory=list)
    calls: int = 0
    fail: str = ""
    tools: list[str] = field(default_factory=list)


def make_env(wd, result, df, ctx, question, tab, llm, ai_ok, focus_ctx, view_state) -> Env:
    env = Env(wd=Path(wd), result=result, df=df, ctx=ctx, question=question, tab=tab, llm=llm, ai_ok=ai_ok, focus=focus_ctx, view_state=view_state)
    env.cat = viewedit.catalogue(result)
    env.facts = dict(records.known_facts(result))
    env.view_cur = None
    return env


# ------------------------------------------------------------------ requests clear enough to skip the plan call
_UNDO = re.compile(r"^\s*(?:please\s+)?undo(?:\s+(?:that|it|the last(?: change)?))?\s*[.!]?\s*$", re.I)
_RESET = re.compile(r"^\s*(?:please\s+)?reset(?:\s+(?:the|my))?\s+view\s*[.!]?\s*$", re.I)
_TOGGLE = re.compile(r"^\s*(?:please\s+)?(hide|remove|show|restore|unhide)\s+(?:the\s+)?(.{3,120}?)\s*[.!]?\s*$", re.I)


def shortcut_plan(question: str, env: Env) -> dict | None:
    """A plan for a request that is exactly one of: undo, reset the view, hide or show an item named by its exact title. No AI call is needed for these."""
    if _UNDO.match(question or ""):
        return {"plan": [{"tool": "view_edit", "args": {"commands": [{"cmd": "undo"}]}}], "reply": "", "unsupported": ""}
    if _RESET.match(question or ""):
        return {"plan": [{"tool": "view_edit", "args": {"commands": [{"cmd": "reset"}]}}], "reply": "", "unsupported": ""}
    m = _TOGGLE.match(question or "")
    if m:
        want = " ".join(m.group(2).lower().split())
        hits = [i for i, it in env.cat["items"].items() if " ".join(str(it["title"]).lower().split()) == want and it["kind"] != "section" or (it["kind"] == "section" and " ".join(str(it["title"]).lower().split()) == want)]
        if len(hits) == 1:
            cmd = "hide" if m.group(1).lower() in ("hide", "remove") else "show"
            return {"plan": [{"tool": "view_edit", "args": {"commands": [{"cmd": cmd, "id": hits[0]}]}}], "reply": "", "unsupported": ""}
    return None


# ------------------------------------------------------------------ the rule router: the no-AI fallback
def fallback_plan(env: Env) -> dict | None:
    q = env.question
    low = q.lower()
    making = bool(re.search(r"\b(?:write|make|create|draft|prepare|put together|give me|generate)\b", low))
    exporting = bool(rules.EXPORT_RE.search(q)) and not making
    if not exporting:
        state = env.view_state if env.view_state is not None else store.get_view_state(env.wd)
        tg = viewchat.toggle_cmds(q, env.cat, viewedit.normalise(state)["hidden"])
        if tg:
            return {"plan": [{"tool": "view_edit", "args": {"commands": tg}}]}
    mg = None if exporting else rules.BRIEF_MANAGE_RE.search(q)
    if mg and (mg.group("op") or "list"):
        op = {"show": "list", "list": "list", "open": "open", "reopen": "open", "rename": "rename", "delete": "delete", "remove": "delete", "discard": "delete"}.get((mg.group("op") or "list").lower(), "list")
        if op == "delete" and re.search(r"\ball\b", low):
            op = "delete_all"
        ref = re.sub(r"\b(?:list|show|open|reopen|rename|delete|remove|discard|my|the|all|saved|brief|briefs|please|me|to)\b", " ", low).strip()
        return {"plan": [{"tool": "brief_manage", "args": {"op": op, **({"id": ref} if ref and op in ("open", "rename", "delete") else {}), **({"title": ref} if op == "rename" and ref else {})}}]}
    if exporting:
        if "brief" in low and (re.search(r"\ball\b", low) or "briefs" in low):
            return {"plan": [{"tool": "export", "args": {"what": "all_briefs"}}]}
        if "text" in low:
            return {"plan": [{"tool": "export", "args": {"what": "text", **({"id": "last"} if "brief" in low else {})}}]}
        if "brief" in low:
            return {"plan": [{"tool": "export", "args": {"what": "brief", "id": "last"}}]}
        return {"plan": [{"tool": "export", "args": {"what": "short_report" if re.search(r"short|summary", low) else "report"}}]}
    br = rules.brief_request(q)
    if br:
        return {"plan": [{"tool": "brief", "args": {"briefs": [{"audience": br["audience"], "length": "short", "focus": br["focus"], "columns": [], "evidence": []}]}}]}
    r = rules.route(q)
    if not r:
        if env.focus:
            cmds = viewchat.parse_rules(q, env.tab, env.cat, env.focus["id"])
            if cmds:
                return {"plan": [{"tool": "view_edit", "args": {"commands": cmds}}]}
        return None
    k = r["kind"]
    if k == "view":
        cmds = viewchat.parse_rules(q, env.tab, env.cat, (env.focus or {}).get("id"))
        return {"plan": [{"tool": "view_edit", "args": {"commands": cmds}}]} if cmds else None
    if k == "recheck":
        return {"plan": [{"tool": "recheck_chart", "args": {}}]}
    if k == "summary":
        sc = rules.summary_scope(q)
        return {"plan": [{"tool": "summarise", "args": {"length": r.get("length", "standard"), **({"scope": sc} if sc else {})}}]}
    if k == "app":
        return {"plan": [{"tool": "explain", "args": {"topic": r["topic"]}}]}
    if k == "priority":
        return {"plan": [{"tool": "rank", "args": {"terms": r.get("terms") or []}}]}
    return None


# ------------------------------------------------------------------ unmet requests
def log_unmet(wd, question: str, tab: str) -> None:
    """One line per request no tool could do: the request text, the tab and the time. No data values."""
    try:
        with open(Path(wd) / UNMET_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({"request": " ".join((question or "").split())[:300], "tab": tab, "at": time.time()}) + "\n")
    except OSError:
        pass


STOP = set("the a an and or of to in on for is are was were be been do does did can could would should will may might have has had having any some there this that these those with about from into over under what which who whom whose how why when where whether if then than so such it its my our your you we i me us they them their his her as at by not no yes please tell show give me find look see know want need like also still just only more most very much many".split())
ABOUT_DATA = re.compile(r"\b(?:data|dataset|file|sheet|table|columns?|fields?|records?|recorded|tracked|stored|information|info|is there|are there|do you have|does it (?:have|record|track|include|contain)|have any|has any)\b", re.I)


def content_terms(question: str) -> list[str]:
    """The words of a request that name what it is about, and pairs of neighbouring ones ("return packages"), without the little words around them."""
    ws = [w for w in re.findall(r"[A-Za-z][A-Za-z'-]{2,}", question or "") if w.lower() not in STOP and w.lower() not in ("data", "dataset", "information", "info", "records", "record", "columns", "column", "recorded", "tracked", "stored")]
    pairs = [f"{a} {b}" for a, b in zip(ws, ws[1:])]
    return list(dict.fromkeys(pairs + ws))[:12]


def _card_reply(env: Env, plan: dict | None):
    """An answer written from the data card: the plan's reply, kept only when it cites a line of the card and every figure in it checks against that line."""
    from ... import cited
    reply = str((plan or {}).get("reply") or "").strip()
    if not reply:
        return None
    _, marks = cited.split_cites(reply)
    ids = [i for _, g in marks for i in g if i.startswith("d") and i in env.facts]
    if not ids:
        return None
    clean, why = verify.check_sentence(reply, env.facts)
    if why:
        return None
    how = howtext.record_how("Answered from what the table holds (its column names, meanings and most common values)", ["the description of your data"], [], ["Every figure is quoted from that description, which was written by code from the whole cleaned table."])
    ans, chips = reports._base("catalog", clean, "About your data", how, [], 0, fact_ids=ids)
    return ans, chips


def data_fallback(env: Env, plan: dict | None) -> tuple[dict, list, str] | None:
    """Before refusing: the data card (when the plan came with an answer from it), then the data catalog on the words of the request. A yes is always an answer; a no is an answer when the request asks
    about the data. None when neither applies."""
    if suggest.request_kind(env.question, env.ctx, env.df) == "view":
        return None                                  # a request to change what is shown is not a question about the data
    got = _card_reply(env, plan)
    if got:
        return got[0], got[1], "card"
    terms = content_terms(env.question)
    if not terms:
        return None
    from .tools.data_catalog import DataCatalog
    if not ABOUT_DATA.search(env.question or ""):
        return None                                  # only a question about what the data holds is answered by looking; any other request that fits no tool is refused
    r = DataCatalog().run({"lookup": "find", "terms": terms}, env, [])
    if r.answer:
        env.facts.update(r.facts)
        return r.answer, r.chips, "catalog"
    return None


def _fallback_out(env: Env, ans: dict, nxt: list, via: str) -> "Out":
    steps = [{"n": 1, "tool": "data_catalog", "summary": "Looked at what the data holds", "ok": True}] if via == "catalog" else []
    ans["agent"] = {"plan": [], "tools": ["data_catalog"] if via == "catalog" else [], "steps": steps, "calls": env.calls}
    _add_steps_to_how(ans, steps, ["The request was not turned into a step, so the description of your data and a search of its columns and values were used before refusing."] if via == "catalog" else [])
    return Out(ans, nxt, env.calls, tools=["data_catalog"] if via == "catalog" else [])


def _unmet_answer(env: Env, reason: str):
    from .. import chat
    if rules.wants_view_change(env.question):          # colours, fonts, layout: not available; the view changes that are available are shown as chips
        ans, nxt, _, _ = viewchat.answer(env.wd, env.result, {}, env.question, env.tab, None, False, env.calls, (env.focus or {}).get("id"), env.view_state)
        ans["unmet"] = True
        log_unmet(env.wd, env.question, env.tab)
        return ans, nxt
    lead = "I can't answer that from this data" + (f": {reason.rstrip('.')}." if reason and not any(ch.isdigit() for ch in reason) else ".")
    kind = suggest.request_kind(env.question, env.ctx, env.df)
    offer = {"view": "Here are two changes I can make.", "data": "Here are two questions I can answer."}.get(kind, "Here are two things you could ask.")
    ans, nxt = chat._plain("no_fit", f"{lead} {offer}", env.wd, env.ctx, env.df, env.result, 0, env.question)
    nxt = suggest.for_request(env.question, env.ctx, env.df, env.result, env.wd, env.tab)         # chips that fit the kind of request that was refused
    ans["chips"] = [{"id": c["id"], "text": c["text"]} for c in nxt]
    ans["unmet"] = True
    log_unmet(env.wd, env.question, env.tab)
    return ans, nxt


# ------------------------------------------------------------------ acting, verifying, answering
def _step_summary(i: int, st: dict, r: registry.ToolResult) -> dict:
    return {"n": i, "tool": st["tool"], "summary": r.summary or st["tool"], "ok": r.ok}


def _compose(env: Env, steps: list[dict], results: list[registry.ToolResult], plan: dict) -> tuple[dict, list[dict]]:
    meta = [_step_summary(i, st, r) for i, (st, r) in enumerate(zip(steps, results), 1)]
    answered = [r for r in results if r.answer]
    failed = next((m for m in meta if not m["ok"]), None)
    if not answered:
        err = next((r.error for r in results if not r.ok and r.error), "")
        if err:
            from .. import chat
            ans, nxt = chat._plain("no_fit", f"{err} Here are two questions I can answer.", env.wd, env.ctx, env.df, env.result, 0, env.question)
            return ans, nxt, meta
        ans, nxt = _unmet_answer(env, "")
        return ans, nxt, meta
    if len(answered) == 1 and not failed:
        base = answered[0]
        ans, chips = dict(base.answer), base.chips
    else:
        primary = next((r for r in reversed(answered) if r.answer.get("table") or r.answer.get("chart")), answered[-1])
        ans = dict(primary.answer)
        texts = []
        for r in answered:
            t = str(r.answer.get("text") or "").strip()
            if t and t not in texts:
                texts.append(t)
        ans["text"] = " ".join(texts)
        ans["lines"] = [x for r in answered for x in (r.answer.get("lines") or [])]
        ans["fact_ids"] = list(dict.fromkeys(i for r in answered for i in (r.answer.get("fact_ids") or [])))
        view = next((r.answer["view"] for r in answered if r.answer.get("view")), None)
        if view:
            ans["view"] = view
        ans["status"] = "answered" if any(r.answer.get("status") == "answered" for r in answered) else ans.get("status", "answered")
        ans["label"] = ans.get("label") or "Answer"
        chips = answered[-1].chips
    if failed:
        err = next((r.error for r in results if not r.ok and r.error), "")
        ans["text"] = (ans.get("text", "") + f" I stopped at step {failed['n']} ({registry.plain(failed['tool'])})" + (f": {err}" if err and err not in ans.get("text", "") else ".")).strip()
        done = [registry.plain(m["tool"]) for m in meta if m["ok"]]
        ans["text"] += (" Done so far: " + ", ".join(done) + ".") if done else ""
    return ans, chips, meta


def _add_steps_to_how(ans: dict, meta: list[dict], notes: list[str]) -> None:
    how = dict(ans.get("how") or {})
    lines = [f"Step {m['n']}, {registry.plain(m['tool'])}: {m['summary']}" + ("" if m["ok"] else " (stopped here)") for m in meta]
    how["steps"] = meta
    how["notes"] = list(how.get("notes") or []) + ["Steps: " + " | ".join(lines)] + notes
    if not how.get("operation"):
        how["operation"] = "Answered with " + ", ".join(registry.plain(m["tool"]) for m in meta)
    ans["how"] = howtext.with_lines({k: v for k, v in how.items() if k != "lines"})


def execute(env: Env, plan: dict) -> tuple[dict, list[dict], list[dict]]:
    tools = registry.load_tools()
    steps = [s for s in plan.get("plan") or []][: planner.MAX_STEPS]
    results: list[registry.ToolResult] = []
    for st in steps:
        tool = tools[st["tool"]]
        try:
            r = tool.run(st.get("args") or {}, env, results)
        except Exception as e:           # noqa: BLE001  a tool must never end the request with a stack trace
            errors.log(env.wd, tool.name, e, getattr(env, "last_sql", None))
            r = registry.ToolResult(ok=False, summary=f"{registry.plain(tool.name)} could not be completed", error=errors.plain_message(e))
        results.append(r)
        env.facts.update(r.facts)
        if not r.ok:
            break
    ran = steps[: len(results)]
    ans, chips, meta = _compose(env, ran, results, plan)
    return ans, chips, meta


def _verified_reply(env: Env, plan: dict) -> tuple[str, str]:
    """(the plan's framing sentence if it passes the figure checks, else ''; the reason it was dropped)."""
    reply = str(plan.get("reply") or "").strip()
    if not reply:
        return "", ""
    clean, why = verify.check_sentence(reply, env.facts)
    if why:
        return "", why
    return clean, ""


def run_request(wd, result, df, ctx, question: str, tab: str, llm, ai_ok: bool, focus_ctx: dict | None, view_state: dict | None) -> Out:
    from .. import chat
    env = make_env(wd, result, df, ctx, question, tab, llm, ai_ok, focus_ctx, view_state)
    plan, errs, hard, fail = None, [], False, ""
    if ai_ok:
        plan = shortcut_plan(question, env)
        if plan is None:
            plan, errs, hard, fail = planner.plan_for(env)
    else:
        plan = fallback_plan(env)
    soft_only = bool(errs) and not hard
    if plan is not None and errs and hard:
        alt = fallback_plan(env)
        plan = alt                                   # an unusable plan: the rule router, then what I can do
        errs = []
    if plan is None and fail and fail.startswith("the reply was not a plan"):
        fail = ""                                  # valid, but not a plan: not an outage, so the rules and the data lookups get their turn
    if plan is None:
        plan = fallback_plan(env)                  # the rule router, the no-AI fallback
    if plan is None:
        fb = data_fallback(env, None) if ai_ok else None          # then the data card and the data catalog, before refusing
        if fb:
            return _fallback_out(env, *fb)
        if fail:
            return Out(calls=env.calls, fail=fail)                # the model could not be reached or gave nothing usable
        ans, nxt = _unmet_answer(env, "")
        _add_steps_to_how(ans, [], ["Nothing I can do fits this request."])
        ans["agent"] = {"plan": [], "tools": [], "steps": [], "calls": env.calls}
        return Out(ans, nxt, env.calls)
    if not plan.get("plan"):
        from .. import reasoning, rules as _rules
        if _rules.is_why(question) or reasoning.asserts(question, df, ctx):        # a "why" or a wrong premise is still answered by what is behind the number
            from .. import chat
            out = chat._data_reply(wd, question, None, ctx, df, result, 0, ai_said_no=True)
            if out:
                a2, n2 = out
                a2["agent"] = {"plan": [], "tools": ["data_query"], "steps": [], "calls": env.calls}
                _add_steps_to_how(a2, [{"n": 1, "tool": "data_query", "summary": "Checked what the request assumes and showed what is behind the number", "ok": True}], [])
                return Out(a2, n2, env.calls, tools=["data_query"])
        fb = data_fallback(env, plan)
        if fb:
            return _fallback_out(env, *fb)
        ans, nxt = _unmet_answer(env, plan.get("unsupported") or "")
        ans["agent"] = {"plan": [], "tools": [], "steps": [], "calls": env.calls}
        _add_steps_to_how(ans, [], ["Nothing I can do fits this request."])
        return Out(ans, nxt, env.calls)
    # the premise a request assumes is checked before anything is answered, unless a step already does it
    if env.ai_ok and not any(s.get("tool") in ("data_query", "premise_check") for s in plan["plan"]) and len(plan["plan"]) < planner.MAX_STEPS:
        from .. import reasoning
        if reasoning.asserts(question, df, ctx):
            plan = {**plan, "plan": [{"tool": "premise_check", "args": {}}] + plan["plan"]}
    ans, chips, meta = execute(env, plan)
    notes = list(env.notes)
    reply, why = _verified_reply(env, plan)
    if reply and ans.get("status") == "answered":
        ans["text"] = f"{reply} {ans.get('text', '')}".strip()
    elif why:
        notes.append(f"A framing sentence the AI wrote was left out: {why}.")
    chat._readable_names(ans, ctx)
    ans["agent"] = {"plan": [{"tool": s["tool"], "args": _short_args(s.get("args"))} for s in plan["plan"]], "tools": [m["tool"] for m in meta], "steps": meta, "calls": env.calls}
    _add_steps_to_how(ans, meta, notes)
    return Out(ans, chips, env.calls, tools=[m["tool"] for m in meta])


def _short_args(args) -> dict:
    """The arguments as saved with the answer (for the feedback file and the history): kept whole up to 4000 characters, cut as text beyond that."""
    out = {}
    for k, v in (args or {}).items():
        s = json.dumps(v, default=str)
        out[k] = v if len(s) <= 4000 else s[:4000] + "…"
    return out


def run_fallback(wd, result, df, ctx, question: str, tab: str, focus_ctx: dict | None, view_state: dict | None) -> Out | None:
    """No AI: the rule router's plan run through the same tools, or None when no rule reads the request."""
    env = make_env(wd, result, df, ctx, question, tab, None, False, focus_ctx, view_state)
    plan = fallback_plan(env)
    if not plan:
        fb = data_fallback(env, None)               # no AI: a question about what the data holds is still answered by looking
        return _fallback_out(env, *fb) if fb else None
    errs, hard = planner.validate(plan, env)
    if errs and hard:
        return None
    ans, chips, meta = execute(env, plan)
    from .. import chat
    chat._readable_names(ans, ctx)
    ans["agent"] = {"plan": [{"tool": s["tool"], "args": _short_args(s.get("args"))} for s in plan["plan"]], "tools": [m["tool"] for m in meta], "steps": meta, "calls": 0}
    _add_steps_to_how(ans, meta, list(env.notes))
    return Out(ans, chips, 0, tools=[m["tool"] for m in meta])
