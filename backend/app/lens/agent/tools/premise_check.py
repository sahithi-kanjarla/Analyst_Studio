"""premise_check: when a request assumes something about the data (a rank, a comparison or a direction), check it by computing, and correct it first. Runs before the answer."""
from __future__ import annotations

from ... import reasoning
from ..registry import Tool, ToolResult, register


class PremiseCheck(Tool):
    name = "premise_check"
    description = "Check what the request assumes (rank, comparison and direction claims) against the data and say what is true."
    args: dict = {}

    def run(self, args, env, prior):
        from ... import chat
        if not reasoning.asserts(env.question, env.df, env.ctx):
            return ToolResult(summary="The request assumes nothing that needed checking")
        out = chat._data_reply(env.wd, env.question, None, env.ctx, env.df, env.result, 0)
        if not out:
            return ToolResult(summary="Nothing the request assumes could be checked")
        ans, nxt = out
        return ToolResult(ok=ans.get("status") == "answered", summary="Checked what the request assumes, by computing it", answer=ans, chips=nxt)


register(PremiseCheck)
