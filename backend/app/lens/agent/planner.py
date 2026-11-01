"""One AI call turns the request into a plan; code decides whether the plan is acceptable.

A plan is {"plan": [{"tool": <name>, "args": {...}}, ...], "reply": <optional short framing>, "unsupported": <optional reason>} with at most MAX_STEPS steps. Code validates every step against the registry's
schema and the tool's own checks (the tool exists, the arguments are valid, the ids and columns exist). A plan that fails gets ONE repair attempt with the problems listed, then the rule router (the
no-AI fallback), then "here is what I can do" with chips. A reply in the single-action form this module used to ask for is still read, as a plan of one step."""
from __future__ import annotations

import json

from ... import llm as llmmod
from .. import query as qmod
from . import registry

MAX_STEPS = 4
LEGACY = {"query": "data_query", "clarify": "clarify", "app": "explain", "summary": "summarise", "priority": "rank", "strategy": "strategies", "general": "general_knowledge", "view": "view_edit"}


def system_prompt() -> str:
    tools = registry.load_tools()
    lines = "\n".join("- " + t.help() for t in tools.values())
    return ("You are Lens, the part of a data report that answers requests. You never calculate and never write a number yourself: you choose a PLAN of tools and code validates, runs and checks it.\n"
            'Reply with exactly one JSON object: {"plan":[{"tool":<name>,"args":{...}}, ...],"reply":<optional one short sentence of framing, no figures unless you cite a fact id like [r1]>,"unsupported":<optional, plain reason, when no tool can do it>}\n'
            f"At most {MAX_STEPS} steps; later steps can build on earlier results. Steps run in order and stop at the first failure.\n\nTOOLS:\n{lines}\n\n"
            "A data_query \"query\" is one of these operations. Every \"filters\" entry looks like: " + qmod.FILTER_HELP + "\n"
            + "\n".join(f"- {h}" for h in qmod.OP_HELP.values()) + "\n\n"
            "Rules:\n"
            "- Prefer data_query whenever a closed operation fits; use custom_analysis only for what the operations cannot express.\n"
            "- Use column names exactly as written in COLUMNS (\"name\"). Never invent a column, a value or an item id. Only \"measure\" columns can be added up or averaged; group by \"category\", \"flag\" or \"id\" columns; time comes from \"date\" columns.\n"
            "- A value the user names goes in a filter or in \"a\"/\"b\" exactly as written; code finds the real value.\n"
            "- A follow-up changes only what was asked in the previous query (see PREVIOUS TURNS) and keeps the rest. \"Last month\" and similar mean the latest period in DATE SPANS.\n"
            "- A \"why\" question: return the query for the number being asked about; never explain a cause yourself and never ask a clarifying question for it.\n"
            "- Ask to clarify (the clarify tool) only when two readings give different answers; one short question with 2 or 3 choices, each a complete query or a list of view commands. Item titles may be the choices when a reference to an item is ambiguous.\n"
            "- A request about what a tab or item shows (hide, show, order, short or full, top N, rename, pin, which charts) is view_edit, using ids from VISIBLE ITEMS; undo goes back one request.\n"
            "- A request for a document for a reader (a brief) is brief; a file to keep is export. Free-form styling (colours, fonts, layout) is not available: use an empty plan and \"unsupported\".\n"
            "- A summary covers the WHOLE report unless the request names a part (a tab, a section or a topic): put only the words the user used in \"scope\". The open tab is never a scope.\n"
            "- Never put a number from your own knowledge in any field. Return the JSON object only.")


def _legacy(obj: dict) -> dict | None:
    a = str(obj.get("action") or "").lower()
    if a == "unsupported":
        return {"plan": [], "unsupported": str(obj.get("reason") or "")}
    if a in LEGACY:
        tool = LEGACY[a]
        args = {k: v for k, v in obj.items() if k != "action"}
        if a == "view" and "commands" in args:
            pass
        return {"plan": [{"tool": tool, "args": args}]}
    if a in ("why",) and isinstance(obj.get("query"), dict):
        return {"plan": [{"tool": "data_query", "args": {"query": obj["query"]}}]}
    if isinstance(obj.get("query"), dict):
        return {"plan": [{"tool": "data_query", "args": {"query": obj["query"]}}]}
    if obj.get("op") in qmod.OPS:
        return {"plan": [{"tool": "data_query", "args": {"query": {k: v for k, v in obj.items() if k != "action"}}}]}
    if a == "query":
        return {"plan": [{"tool": "data_query", "args": {"query": obj.get("query")}}]}
    return None


def normalise(obj) -> dict | None:
    """{"plan": [...], "reply": str, "unsupported": str} from a model reply, or None when it is not a plan at all."""
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get("plan"), list) or "unsupported" in obj and "action" not in obj:
        plan = obj.get("plan") if isinstance(obj.get("plan"), list) else []
        return {"plan": plan, "reply": str(obj.get("reply") or "")[:400], "unsupported": str(obj.get("unsupported") or "")[:300]}
    leg = _legacy(obj)
    if leg is None:
        return None
    leg.setdefault("reply", "")
    leg.setdefault("unsupported", "")
    return leg


def validate(plan: dict, env) -> tuple[list[str], bool]:
    """(problems, hard). `hard` is True when something is wrong beyond a data query that code rejected (an unknown tool, a bad argument): such a plan cannot be run as it is."""
    tools = registry.load_tools()
    steps = plan.get("plan") or []
    env.plan_steps = [st.get("tool") for st in steps if isinstance(st, dict)]          # a step may depend on what an earlier step creates (an export of a brief written in the same request)
    errs: list[str] = []
    hard = False
    if len(steps) > MAX_STEPS:
        return [f"A plan has at most {MAX_STEPS} steps."], True
    for i, st in enumerate(steps, 1):
        if not isinstance(st, dict) or st.get("tool") not in tools:
            errs.append(f"Step {i}: “{str(st.get('tool') if isinstance(st, dict) else st)[:40]}” is not a tool. Tools: {', '.join(tools)}.")
            hard = True
            continue
        tool = tools[st["tool"]]
        if tool.needs_ai and not env.ai_ok:
            errs.append(f"Step {i}: {tool.name} needs the AI, which is not available.")
            hard = True
            continue
        bad = registry.check_schema(tool, st.get("args") if st.get("args") is not None else {})
        if bad:
            errs += [f"Step {i}: {b}" for b in bad]
            hard = True
            continue
        soft = tool.validate(st.get("args") or {}, env)
        if soft:
            errs += [f"Step {i}: {b}" for b in soft]
            hard = hard or not getattr(tool, "soft_validation", False)
    return errs, hard


def ask_plan(env, feedback: str = "") -> tuple[dict | None, str]:
    """(plan or None, failure reason). One call; counts against the budget."""
    model = llmmod.deterministic(env.llm)
    from . import context
    user = context.build(env) + feedback
    env.spend(1)
    obj, why = llmmod.ask_json_ex(model, system_prompt(), user, repair=False)
    if obj is None:
        return None, why or "no usable reply"
    plan = normalise(obj)
    if plan is None:
        return None, "the reply was not a plan"
    return plan, ""


def plan_for(env) -> tuple[dict | None, list[str], bool, str]:
    """(plan, remaining problems, hard, failure reason). Asks, and when the plan is not acceptable asks once more with the problems listed."""
    plan, why = ask_plan(env)
    feedback_for_bad_json = "\n\nYour previous reply was not a valid plan. Answer again with only the JSON object."
    if plan is None:
        if env.can_call() and why.startswith("bad JSON"):
            plan, why = ask_plan(env, feedback_for_bad_json)
        if plan is None:
            return None, [], True, why
    errs, hard = validate(plan, env)
    if errs and env.can_call():
        again, why2 = ask_plan(env, "\n\nYour previous plan was rejected by the checks: " + " ".join(errs)[:900] + " Answer again with a corrected JSON plan, or an empty plan with \"unsupported\" if it cannot be done.")
        if again is not None:
            plan = again
            errs, hard = validate(plan, env)
    return plan, errs, hard, ""
