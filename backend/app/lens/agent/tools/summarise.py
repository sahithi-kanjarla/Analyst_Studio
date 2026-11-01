"""summarise: a short version of the report, from its checked facts only. The AI may choose the wording; every figure must cite the fact it came from and pass the citation check.
The scope follows what the user names: nothing named means the whole report (and the answer says so); a tab, a section or a topic named means only that. The open tab never limits a summary."""
from __future__ import annotations

from ... import chat, reports
from ..env import MAX_CALLS
from .. import summary_scope as ss
from ..registry import Tool, ToolResult, register


class Summarise(Tool):
    name = "summarise"
    description = "A short version of the report (5 lines, or 3 when shortened) plus its top actions. `scope` is what the user named to summarise (a tab, a section or a topic, in their words); leave it empty for the whole report. The open tab is never a scope."
    args = {"length": {"type": "str", "enum": ["short", "standard"]}, "scope": {"type": "str", "max_len": 120, "help": "only what the user named; empty means the whole report"}}

    def run(self, args, env, prior):
        length = args.get("length") if args.get("length") in reports.SUMMARY_LINES else "standard"
        sc = ss.resolve(env, str(args.get("scope") or ""))
        if sc is None:
            what = ss.clean(args.get("scope"))
            ans, chips = chat._plain("no_fit", f"The report has nothing about “{what}” to summarise. Here are two things I can summarise instead.", env.wd, env.ctx, env.df, env.result, 0, env.question)
            return ToolResult(ok=False, summary="Nothing in the report matched what you asked to summarise", answer=ans, chips=chips, error=ans["text"])
        result = env.result if sc["whole"] else ss.scoped_result(env.result, sc["ids"] or set())
        ans, chips, used, fail = reports.answer_summary(result, env.wd, env.llm, length, MAX_CALLS - env.calls, env.ai_ok, env.calls, scope_label=sc["label"], lines_override=sc["lines"])
        env.spend(used)
        ans["scope"] = sc["label"]
        return ToolResult(summary=f"Wrote a {length} summary of {sc['label']} from the checked facts", answer=ans, chips=chips, ai_calls=used, error=fail if not ans.get("lines") else "")


register(Summarise)
