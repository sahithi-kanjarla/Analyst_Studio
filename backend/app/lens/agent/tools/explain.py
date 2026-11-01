"""explain: questions about the app and the report itself (what a tab shows, why trust is what it is, what was cleaned, which definitions were used), quoted from the run's saved records."""
from __future__ import annotations

from ... import records, reports
from ..registry import Tool, ToolResult, register


class Explain(Tool):
    name = "explain"
    description = "Quote the run's saved records on a topic about the app or the report; nothing is recalculated."
    args = {"topic": {"type": "str", "required": True, "enum": list(records.APP_TOPICS)}}

    def run(self, args, env, prior):
        ans, chips = reports.answer_app(args["topic"], env.result, env.tab, env.question, env.wd, 0)
        return ToolResult(summary=f"Quoted the saved records on {args['topic'].replace('_', ' ')}", answer=ans, chips=chips)


register(Explain)
