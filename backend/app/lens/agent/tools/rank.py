"""rank: order the report's own insights and actions by how closely they match what the user cares about. The AI picks the framing words; code does the ranking."""
from __future__ import annotations

from ... import reports, rules
from ..registry import Tool, ToolResult, register


class Rank(Tool):
    name = "rank"
    description = "Use when the user says what matters to them and wants the report's findings and actions ordered by it (the top 3 with their figures)."
    args = {"priority": {"type": "str", "help": "what they care about, 1-4 words"}, "terms": {"type": "list", "help": "up to 5 words or close synonyms a report would use"}}

    def run(self, args, env, prior):
        terms = [str(t) for t in (args.get("terms") or []) if isinstance(t, (str, int))] or rules.priority_terms(env.question)
        phrase = str(args.get("priority") or "").strip() or " ".join(terms)
        ans, chips = reports.answer_priority(env.result, env.ctx, terms, phrase, env.wd, 0)
        return ToolResult(summary="Ranked the report's findings and actions by what matters to the user", answer=ans, chips=chips)


register(Rank)
