"""recheck_chart: re-check the most recent chart Lens drew against its computed table, value by value."""
from __future__ import annotations

from ..registry import Tool, ToolResult, register


class RecheckChart(Tool):
    name = "recheck_chart"
    description = "Re-check the latest drawn chart against the table it was drawn from (when the user says a chart looks wrong)."
    args: dict = {}

    def run(self, args, env, prior):
        from ... import chat
        ans, nxt = chat._recheck(env.wd, env.ctx, env.df, env.result)
        return ToolResult(ok=ans.get("status") == "answered", summary="Re-checked the latest chart against its table", answer=ans, chips=nxt)


register(RecheckChart)
