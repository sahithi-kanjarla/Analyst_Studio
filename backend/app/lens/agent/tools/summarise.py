"""summarise: the short version of the report, from its checked facts only. The AI may choose the wording; every figure must cite the fact it came from and pass the citation check."""
from __future__ import annotations

from ... import reports
from ..env import MAX_CALLS
from ..registry import Tool, ToolResult, register


class Summarise(Tool):
    name = "summarise"
    description = "A short version of the report (5 lines, or 3 when shortened) plus its top actions, from the report's checked facts."
    args = {"length": {"type": "str", "enum": ["short", "standard"]}}

    def run(self, args, env, prior):
        length = args.get("length") if args.get("length") in reports.SUMMARY_LINES else "standard"
        ans, chips, used, fail = reports.answer_summary(env.result, env.wd, env.llm, length, MAX_CALLS - env.calls, env.ai_ok, env.calls)
        env.spend(used)
        return ToolResult(summary=f"Wrote a {length} summary from the checked facts", answer=ans, chips=chips, ai_calls=used, error=fail if not ans.get("lines") else "")


register(Summarise)
