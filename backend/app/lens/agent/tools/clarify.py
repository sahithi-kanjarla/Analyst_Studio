"""clarify: one short question with 2 or 3 choices, each a complete query (or a list of view commands). Used for an ambiguous reference or an open-ended question."""
from __future__ import annotations

from ..registry import Tool, ToolResult, register


class Clarify(Tool):
    name = "clarify"
    description = "Ask ONE short question when two readings give different answers, with 2 or 3 choices; each choice is {\"label\":<2-4 words>,\"query\":<a data_query query>} or {\"label\":...,\"commands\":[view commands]}."
    args = {"question": {"type": "str", "required": True, "max_len": 300}, "choices": {"type": "list", "required": True}}

    def validate(self, args, env):
        ch = args.get("choices") or []
        if not 2 <= len([c for c in ch if isinstance(c, dict)]) <= 3 and len(ch) < 2:
            return ["Offer 2 or 3 choices."]
        return []

    def run(self, args, env, prior):
        from ... import chat, query as qmod, reasoning, rules
        first = next((qmod.check(c.get("query"), env.ctx, env.df) for c in args["choices"] if isinstance(c, dict) and c.get("query")), None)
        if first is not None and first.query and (rules.is_why(env.question) or reasoning.asserts(env.question, env.df, env.ctx)):       # a "why" is never answered with a question: the first reading is used
            out = chat._data_reply(env.wd, env.question, first, env.ctx, env.df, env.result, 0)
            if out:
                return ToolResult(summary="Used the first reading of the request", answer=out[0], chips=out[1])
        ans, nxt = chat._clarify(args["question"], args["choices"], env.wd, env.ctx, env.df, env.result, 0, env.question)
        return ToolResult(ok=ans.get("status") in ("answered", "clarify"), summary="Asked one question to settle what was meant", answer=ans, chips=nxt, error="" if ans.get("status") in ("answered", "clarify") else ans.get("text", ""))


register(Clarify)
