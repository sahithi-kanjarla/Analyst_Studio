"""strategies: the report's own ideas (strategies), best first, each with the checks it already passed; or why the first one ranks first."""
from __future__ import annotations

from ... import reports, rules
from ..registry import Tool, ToolResult, register


class Strategies(Tool):
    name = "strategies"
    description = "Suggest from the report's strategies (labelled Suggestion, or No suggestion with the reason); with why=true, explain the ranking."
    args = {"focus": {"type": "str", "help": "what to act on, 1-4 words, or empty"}, "why": {"type": "bool"}}

    def run(self, args, env, prior):
        focus = str(args.get("focus") or "")
        if args.get("why") and not focus:
            from ... import chat
            ans, chips = reports.answer_strategy_why(env.result, env.ctx, chat._last_strategy_focus(env.wd), env.wd, 0)
        else:
            ans, chips = reports.answer_strategy(env.result, env.ctx, focus, env.wd, 0, explain=bool(args.get("why")) or rules.is_why(env.question))
        return ToolResult(summary="Picked from the report's own strategies", answer=ans, chips=chips)


register(Strategies)
