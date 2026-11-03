"""chart_edit: change a chart. For a chart Lens drew in this chat (target=answer): its type, time grain, top N, sort, direction and filters, re-run through the same checked query.
For a chart of the report (target=item): its type, time grain or top N, redrawn by Lens as a separate picture (the report's own chart is unchanged)."""
from __future__ import annotations

import re

from ... import query as qmod
from ... import store
from ..registry import Tool, ToolResult, register

SET_KEYS = ("chart", "grain", "n", "sort", "direction")


def tab_of(env) -> str:
    from .. import context
    return context.tab_id(env.tab)


class ChartEdit(Tool):
    name = "chart_edit"
    description = 'Use when the user wants a chart drawn another way: its type, time grain, top N, sort, direction or filters. target=answer changes the latest chart Lens drew, which includes the result of an earlier step in the same plan (so \\"show X, then make it a line\\" is data_query followed by chart_edit); target=item changes a chart of the report (`item` id). Charts can be created and changed.'
    args = {"target": {"type": "str", "enum": ["answer", "item"]}, "turn": {"type": "str"}, "item": {"type": "str"}, "chart": {"type": "str"}, "grain": {"type": "str"}, "n": {"type": "int"},
            "sort": {"type": "dict"}, "direction": {"type": "str", "enum": ["top", "bottom"]}, "filters": {"type": "list", "help": "filter objects to add"}}
    soft_validation = True

    def validate(self, args, env):
        if (args.get("target") or ("item" if args.get("item") else "answer")) == "item":
            it = env.cat["items"].get(args.get("item") or "")
            if not it or it["kind"] != "chart":
                return [f"“{str(args.get('item'))[:40]}” is not a chart of this report."]
        return []

    def run(self, args, env, prior):
        from ... import chat
        target = args.get("target") or ("item" if args.get("item") else "answer")
        if target == "item":
            from . import view_edit
            cmd = {"cmd": "chart", "id": args["item"], **{k: args[k] for k in ("chart", "grain", "n") if k in args}}
            r = view_edit.ViewEdit().run({"commands": [cmd]}, env, prior)
            r.summary = "Redrew a report chart another way"
            return r
        turns = store.load(env.wd)["turns"]
        earlier = next((r.answer for r in reversed(prior) if r.ok and r.answer and r.answer.get("query") and (r.answer.get("chart") or {}).get("path") is not None), None)       # a chart drawn by an earlier step of this request
        t = {"id": "this-request", "answer": earlier} if earlier and not args.get("turn") else next((x for x in reversed(turns) if (not args.get("turn") or x["id"] == args["turn"]) and x["answer"].get("status") == "answered" and x["answer"].get("query")), None)
        if t is None:
            kind = args.get("chart") or next((m for m in re.findall(r"\b(bar|line|share|pie|table|histogram)\b", env.question or "", re.I)), "") or "bar"
            kind = {"pie": "share"}.get(kind.lower(), kind.lower())
            able = [i for i in env.cat["items"].values() if i["kind"] == "chart" and i.get("changeable")]          # the charts Lens can redraw
            charts = ([i for i in able if i["tab"] == tab_of(env)] + [i for i in able if i["tab"] != tab_of(env)])[:3]
            choices = [{"label": str(c["title"])[:50], "commands": [{"cmd": "chart", "id": c["id"], "chart": kind}]} for c in charts]
            ans, nxt = chat._clarify("Which chart do you mean? I have not drawn one in this chat yet.", choices, env.wd, env.ctx, env.df, env.result, 0, env.question)
            return ToolResult(ok=ans.get("status") in ("answered", "clarify"), summary="Asked which chart to change", answer=ans, chips=nxt, error="" if ans.get("status") in ("answered", "clarify") else ans.get("text", ""))
        q = {**t["answer"]["query"], **{k: args[k] for k in SET_KEYS if args.get(k) is not None}}
        if args.get("filters"):
            q["filters"] = list(q.get("filters") or []) + [f for f in args["filters"] if isinstance(f, dict)]
        ans, nxt = chat._run_checked(env.wd, qmod.check(q, env.ctx, env.df), env.ctx, env.df, env.result, 0, env.question)
        ok = ans.get("status") == "answered"
        return ToolResult(ok=ok, summary="Changed the latest chart and re-ran its calculation", answer=ans, chips=nxt, error="" if ok else ans.get("text", ""))


register(ChartEdit)
