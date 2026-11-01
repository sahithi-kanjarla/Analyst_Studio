"""general_knowledge: a short reply from general knowledge, labelled as not from the data. A sentence with a figure is dropped."""
from __future__ import annotations

from ... import reports
from ..registry import Tool, ToolResult, register


class General(Tool):
    name = "general_knowledge"
    description = 'Use when the question is not about this data or report at all (what a term means in general, how something works): a short answer, no numbers.'
    args = {"answer": {"type": "str", "required": True, "max_len": 700}}

    def run(self, args, env, prior):
        ans, chips = reports.answer_general(args.get("answer"), env.result, env.wd, 0)
        return ToolResult(summary="Answered from general knowledge, not from your data", answer=ans, chips=chips)


register(General)
