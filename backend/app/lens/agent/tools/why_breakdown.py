"""why_breakdown: what is behind a number. For a "why", "how come" or "what is driving" question: code shows how the number splits (shares, the top groups, a split by another column, the change between periods) and never claims a cause.
The measure and the grouping come from the request's own words and the columns the run has; when the request names neither, the report's main measure and its first usable grouping are used."""
from __future__ import annotations

from ... import chips as chipmod
from ... import query as qmod
from ... import reasoning
from .... import viewselect as vs
from ..registry import Tool, ToolResult, register


def infer_query(env, measure: str | None = None, group: str | None = None) -> dict | None:
    """The query whose breakdown answers the request: a measure and a grouping named by the arguments, else found in the request's words, else the usual ones for this run."""
    from ... import chat
    ctx, df = env.ctx, env.df
    words = vs.words(env.question)

    def named(cols, hint):
        if hint:
            c = ctx.find(hint)
            if c is not None:
                return c
        return next((c for c in cols if (vs.words(f"{c.name} {c.display}") & words) and vs.words(f"{c.name} {c.display}") <= words | vs.words(f"{c.name} {c.display}")), None)
    m = named([c for c in ctx.measures() if not c.ratio], measure) or chipmod.main_measure(ctx, env.result)
    groups = [c for c in chipmod.groups(ctx, df)]
    g = named(groups, group)
    if g is None:                                                  # a group whose value the request names ("why is X the top")
        for cl in reasoning.claims(env.question, df, ctx):
            g = ctx.cols.get(cl["column"])
            if g is not None:
                break
    if g is None:
        last = chat._last_query(env.wd) or {}
        g = next((ctx.cols[x] for x in (last.get("group_by") or []) if x in ctx.cols), None) or (groups[0] if groups else None)
    if g is None:
        return None
    return {"op": "top", "measure": m.name if m else None, "agg": "sum" if m else "count", "group_by": [g.name], "n": 3, "direction": "top"} if m else {"op": "top", "agg": "count", "group_by": [g.name], "n": 3, "direction": "top"}


class WhyBreakdown(Tool):
    name = "why_breakdown"
    description = ("Use for a \"why\", \"how come\" or \"what is behind / driving this\" question, or when a request asks about causes: it shows what is behind a number (how it splits by a grouping, shares, the top groups, the change between periods), "
                   "calculated by code, and never claims a cause. Optional `measure` and `group`: the columns the request is about (leave out when it names none).")
    args = {"measure": {"type": "str", "max_len": 80}, "group": {"type": "str", "max_len": 80}}

    def run(self, args, env, prior):
        from ... import chat
        q = infer_query(env, args.get("measure"), args.get("group"))
        ch = qmod.check(q, env.ctx, env.df) if q else None
        if ch is None or not ch.query:
            ans, nxt = chat._plain("no_fit", "I can't show what is behind that: no number and grouping in your data fit the request. Here are two questions I can answer.", env.wd, env.ctx, env.df, env.result, 0, env.question, env.tab)
            return ToolResult(ok=False, summary="Nothing to break down", answer=ans, chips=nxt, error=ans["text"])
        out = reasoning.run_why(ch.query, env.df, env.ctx, env.result)
        if not out:
            first, nxt = chat._run_checked(env.wd, ch, env.ctx, env.df, env.result, 0, env.question)
            return ToolResult(ok=first.get("status") == "answered", summary="Showed the number and how it splits", answer=first, chips=nxt, error="" if first.get("status") == "answered" else first.get("text", ""))
        R, wchips = out
        ans, nxt = chat._answered(env.wd, R, env.ctx, env.df, env.result, 0, wchips)
        ans["kind"] = "why"
        from .. import verify
        return ToolResult(summary="Showed what is behind the number (never a cause)", answer=ans, chips=nxt, facts=verify.facts_from_answer("q", ans, env))


register(WhyBreakdown)
