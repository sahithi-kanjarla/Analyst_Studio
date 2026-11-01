"""chart_edit: change a chart. For a chart Lens drew in this chat (target=answer): its type, time grain, top N, sort, direction and filters, re-run through the same checked query.
For a chart of the report (target=item): its type, time grain or top N, redrawn by Lens as a separate picture (the report's own chart is unchanged)."""
from __future__ import annotations

from ... import query as qmod
from ... import store
from ..registry import Tool, ToolResult, register

SET_KEYS = ("chart", "grain", "n", "sort", "direction")


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
            ans, nxt = chat._plain("no_fit", "Which chart do you mean? I haven't drawn one in this chat yet. Ask a question about your data first.", env.wd, env.ctx, env.df, env.result)
            return ToolResult(ok=False, summary="There was no chart to change", answer=ans, chips=nxt, error=ans["text"])
        q = {**t["answer"]["query"], **{k: args[k] for k in SET_KEYS if args.get(k) is not None}}
        if args.get("filters"):
            q["filters"] = list(q.get("filters") or []) + [f for f in args["filters"] if isinstance(f, dict)]
        ans, nxt = chat._run_checked(env.wd, qmod.check(q, env.ctx, env.df), env.ctx, env.df, env.result, 0, env.question)
        ok = ans.get("status") == "answered"
        return ToolResult(ok=ok, summary="Changed the latest chart and re-ran its calculation", answer=ans, chips=nxt, error="" if ok else ans.get("text", ""))


register(ChartEdit)
