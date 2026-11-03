"""report_facts: look up the report's own checked facts (headline findings, insights) by id or by words, to answer from or to use as evidence for a later step. Nothing is calculated."""
from __future__ import annotations

import re

from ... import records, reports
from ..registry import Tool, ToolResult, register


class ReportFacts(Tool):
    name = "report_facts"
    description = "Use to quote the report's own checked facts (by fact id or by matching words) when the answer is already in the report, or as evidence for a brief; nothing is recalculated."
    args = {"ids": {"type": "list", "help": "fact ids from the report's fact sheet"}, "match": {"type": "str", "help": "words to match"}, "limit": {"type": "int"}}

    def validate(self, args, env):
        known = {f["id"] for f in records.fact_sheet(env.result)}
        bad = [str(i) for i in args.get("ids") or [] if i not in known]
        return [f"No fact with id {', '.join(bad[:3])} exists in this report."] if bad else []

    def run(self, args, env, prior):
        sheet = records.fact_sheet(env.result)
        ids = [i for i in args.get("ids") or []]
        words = {w for w in re.findall(r"[a-z0-9]+", str(args.get("match") or "").lower()) if len(w) > 2}
        pick = [f for f in sheet if f["id"] in ids] if ids else [f for f in sheet if words and words & set(re.findall(r"[a-z0-9]+", f["text"].lower()))] if words else sheet
        pick = pick[: max(1, min(int(args.get("limit") or 5), 12))]
        known = records.known_facts(env.result)
        for f in pick:
            if f["id"] in known:
                env.facts.setdefault(f["id"], known[f["id"]])
        if not pick:
            return ToolResult(ok=True, summary="No report fact matched", data={"facts": []}, answer=None)
        how = reports.howtext.record_how("Quoted the report's checked facts", ["the report's checked fact list"], [f["id"] for f in pick], ["Nothing was recalculated; the wording is copied from the saved report."])
        ans, chips = reports._base("app", "From the report's checked facts:", "From your report", how, [], 0, lines=[f["text"] for f in pick], fact_ids=[f["id"] for f in pick])
        return ToolResult(summary=f"Looked up {len(pick)} checked fact{'s' if len(pick) != 1 else ''} in the report", answer=ans, data={"facts": pick}, facts={f["id"]: known[f["id"]] for f in pick if f["id"] in known})


register(ReportFacts)
