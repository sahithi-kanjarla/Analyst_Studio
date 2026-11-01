"""One AI call turns the request into a plan; code decides whether the plan is acceptable.

A plan is {"plan": [{"tool": <name>, "args": {...}}, ...], "reply": <optional short framing>, "unsupported": <optional reason>} with at most MAX_STEPS steps. Code validates every step against the registry's
schema and the tool's own checks (the tool exists, the arguments are valid, the ids and columns exist). A plan that fails gets ONE repair attempt with the problems listed, then the rule router (the
no-AI fallback), then "here is what I can do" with chips. A reply in the single-action form this module used to ask for is still read, as a plan of one step."""
from __future__ import annotations

import json
import re
from collections import Counter

from ... import llm as llmmod
from .. import query as qmod
from . import registry

MAX_STEPS = 4
LEGACY = {"query": "data_query", "clarify": "clarify", "app": "explain", "summary": "summarise", "priority": "rank", "strategy": "strategies", "general": "general_knowledge", "view": "view_edit"}


DATA_TOOLS = {"data_query", "custom_analysis", "why_breakdown"}
VIEW_TOOLS = {"view_edit"}
OTHER_TOOLS = {"explain", "glossary", "general_knowledge", "summarise", "strategies", "rank", "report_facts", "brief", "export"}


def relevant_tools(env) -> set[str]:
    """The tools the request could use, shown with their full description and arguments: those of its kind (numbers, a change of view, anything else), those whose own description shares a word with it,
    those the previous turn used (a follow-up), and always the one that asks a question. The other tools are still listed by name with their first sentence; a retry sends everything."""
    from ... import viewselect as vs
    from . import suggest
    tools = registry.load_tools()
    kind = suggest.request_kind(env.question, env.ctx, env.df)
    keep = set(DATA_TOOLS if kind == "data" else VIEW_TOOLS if kind == "view" else OTHER_TOOLS)
    w = vs.words(env.question)
    own = {n: vs.words(n.replace("_", " ") + " " + t.description.split(".")[0]) for n, t in tools.items()}
    spread = Counter(x for ws in own.values() for x in ws)
    for n, ws in own.items():
        if w & {x for x in ws if spread[x] <= 2}:           # a word that points to this tool and few others
            keep.add(n)
    try:
        from . import context
        for turn in context.history(env.wd, 1):
            keep |= set(turn.get("tools") or [])
    except Exception:           # noqa: BLE001  a missing history only means no follow-up hint
        pass
    return (keep | {"clarify"}) & set(tools)


CORE_OPS = {"aggregate", "top"}


def relevant_ops(env) -> set[str]:
    """The operations written out in full: the two general ones, the one the previous query used, and those whose own words (name and description) the request shares and few other operations do."""
    from ... import viewselect as vs
    w = vs.words(env.question)
    own = {k: vs.words(k.replace("_", " ") + " " + re.sub(r"[{}\[\]\"<>|:,]", " ", h)) for k, h in qmod.OP_HELP.items()}
    spread = Counter(x for ws in own.values() for x in ws)
    keep = set(CORE_OPS)
    for k, ws in own.items():
        if w & {x for x in ws if spread[x] <= 2}:
            keep.add(k)
    try:
        from ..chat import _last_query
        keep.add((_last_query(env.wd) or {}).get("op", ""))
    except Exception:           # noqa: BLE001  no previous query is fine
        pass
    return keep & set(qmod.OP_HELP)


def compact_help(t) -> str:
    first = t.description.split(". ")[0].rstrip(".")
    return f'{t.name}: {first if len(first) <= 70 else first[:67].rsplit(" ", 1)[0] + "…"}'


RULES: list[tuple] = [          # (tools the rule is about, or None = always, or "card"; the rule). A rule is sent when one of its tools is shown in full (the first ask) or always (a retry).
    ({"data_query", "chart_edit"}, "- Charts can be created and changed: a request for a number or a split comes with its chart, and a request to draw a result another way is data_query then chart_edit in one plan (chart_edit acts on the result of the step before it). Never say a chart cannot be made."),
    ({"why_breakdown", "view_edit", "data_catalog", "glossary"}, "- A \"why\", \"how come\" or cause question is why_breakdown (never an empty plan); \"keep only the charts that support an action or the summary\" is view_edit (only_supporting); what is IN the data is data_catalog; which definitions were used is glossary."),
    ({"data_query", "custom_analysis"}, "- Prefer data_query whenever a closed operation fits; use custom_analysis only for what the operations cannot express."),
    ({"data_query", "custom_analysis"}, "- Use column names exactly as written in COLUMNS (\"name\"). Never invent a column, a value or an item id. Only \"measure\" columns can be added up or averaged; group by \"category\", \"flag\" or \"id\" columns; time comes from \"date\" columns."),
    ({"data_query"}, "- A value the user names goes in a filter or in \"a\"/\"b\" exactly as written; code finds the real value."),
    (None, "- A follow-up changes only what was asked in the previous query (see PREVIOUS TURNS) and keeps the rest. \"Last month\" and similar mean the latest period in DATE SPANS."),
    ({"why_breakdown"}, "- A \"why\" question: return the query for the number being asked about; never explain a cause yourself and never ask a clarifying question for it."),
    (None, "- Ask to clarify (the clarify tool) only when two readings give different answers; one short question with 2 or 3 choices, each a complete query or a list of view commands. Item titles may be the choices when a reference to an item is ambiguous."),
    ({"view_edit"}, "- A request about what a tab or item shows (hide, show, order, short or full, top N, rename, pin, which charts) is view_edit, using ids from VISIBLE ITEMS; undo goes back one request."),
    ({"brief", "export"}, "- A request for a document for a reader (a brief) is brief; a file to keep is export."),
    (None, "- Colours, fonts and layout cannot be styled: use an empty plan and \"unsupported\" for those only."),
    ("card", "- DATA CARD says what each column holds (type, role, meaning, share missing, range, distinct values, the most common values with counts). A question about what is in the data (is something recorded, what values a column has, how many are missing) can be answered from it: use an empty plan and put the answer in \"reply\", citing the line it comes from by fact id like [d3]. For a concept that may be spelled or named differently, or when the card is not enough, use data_catalog with the words the user used plus synonyms, local usage and spelling variants."),
    ({"summarise"}, "- A summary covers the WHOLE report unless the request names a part (a tab, a section or a topic): put only the words the user used in \"scope\". The open tab is never a scope."),
    (None, "- Never put a number from your own knowledge in any field. Return the JSON object only."),
]


def system_prompt(env=None, full: bool = True) -> str:
    """The instructions. env given and full False: the tools the request could use in full and the rest in one line each; the operation list only when a data tool is in full."""
    tools = registry.load_tools()
    show = relevant_tools(env) if (env is not None and not full) else set(tools)
    lines = "\n".join("- " + (t.help() if n in show else compact_help(t)) for n, t in tools.items())
    from . import context
    card = env is None or full or context.needs_card(env)
    ops = ""
    if {"data_query", "custom_analysis"} & show:
        keep_ops = set(qmod.OP_HELP) if (env is None or full) else relevant_ops(env)
        ops = ("A data_query \"query\" is one of these operations. Every \"filters\" entry looks like: " + qmod.FILTER_HELP + "\n" + "\n".join(f"- {h}" for k, h in qmod.OP_HELP.items() if k in keep_ops) + "\n"
               + (f"Other operations (written out when the request is about what they do): {', '.join(k for k in qmod.OP_HELP if k not in keep_ops)}.\n" if keep_ops != set(qmod.OP_HELP) else "") + "\n")
    return ("You are Lens, the part of a data report that answers requests. You never calculate and never write a number yourself: you choose a PLAN of tools and code validates, runs and checks it.\n"
            'Reply with exactly one JSON object: {"plan":[{"tool":<name>,"args":{...}}, ...],"reply":<optional one short sentence of framing, no figures unless you cite a fact id like [r1]>,"unsupported":<optional, plain reason, when no tool can do it>}\n'
            f"At most {MAX_STEPS} steps; later steps can build on earlier results. Steps run in order and stop at the first failure.\n\nTOOLS (a tool shown as name: sentence is described briefly; use it if it is the right one, the checks will tell you what an argument needs):\n{lines}\n\n"
            + ops +
            "Rules:\n" + "\n".join(r for tags, r in RULES if tags is None or (tags == "card" and card) or (isinstance(tags, set) and tags & show)))


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
    model = llmmod.deterministic(llmmod.planner_model(env.llm))
    from . import context
    system = system_prompt(env, full=bool(feedback))
    user = context.build(env, full=bool(feedback)) + feedback          # the first ask sends what the request could use; a retry sends everything
    env.spend(1)
    parts = dict(getattr(env, "prompt_parts", None) or {})
    parts["system_text"] = len(system)
    llmmod.note_parts({**parts, "other": parts.get("other", 0) + len(feedback)})
    obj, why = llmmod.ask_json_ex(model, system, user, repair=False)
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
