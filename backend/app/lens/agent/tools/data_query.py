"""data_query: the closed list of pre-verified operations (filter, group by, aggregate, top or bottom N, share, compare, change between periods, trend, distribution, relationship, unusual changes,
and the why-breakdown behind a number). Preferred whenever one fits. The query is checked against the run's real columns, roles, values and periods before anything runs."""
from __future__ import annotations

from .. import verify
from ..registry import Tool, ToolResult, register
from ... import query as qmod


class DataQuery(Tool):
    name = "data_query"
    description = 'Use when the request asks for a number, a ranking, a split, a comparison, a trend, a change between periods, a spread or a relationship that one of the closed operations fits: code checks the query and runs it on the whole cleaned table. Preferred over custom_analysis whenever an operation fits. It also draws the chart of its result.'
    args = {"query": {"type": "dict", "required": True, "help": "one operation object with an \"op\""}}
    soft_validation = True          # a query code rejects is repaired once, then explained, not treated as a broken plan

    def validate(self, args, env):
        ch = qmod.check(args.get("query"), env.ctx, env.df)
        if ch.query or ch.ambiguity:
            return []
        hints = "".join(f" Real values of {env.ctx.label(c)} include: {', '.join(v)}." for c, v in ch.hints.items())
        return [" ".join(ch.errors) + hints]

    def run(self, args, env, prior):
        from ... import chat
        ch = qmod.check(args.get("query"), env.ctx, env.df)
        out = chat._data_reply(env.wd, env.question, ch, env.ctx, env.df, env.result, 0)
        ans, nxt = out if out else chat._run_checked(env.wd, ch, env.ctx, env.df, env.result, 0, env.question)
        ok = ans.get("status") == "answered"
        facts = verify.facts_from_answer("q", ans, env) if ok else {}
        return ToolResult(ok=ok, summary="Ran the closed operation " + str((ans.get("query") or {}).get("op", "")) if ok else "The query could not be run", answer=ans, chips=nxt, facts=facts,
                          error="" if ok else ans.get("text", ""))


register(DataQuery)
