"""glossary: look up the saved definitions this run used (and the company's saved glossary), or add one. A new definition applies to future analyses; this report is not changed."""
from __future__ import annotations

import re

from .... import glossary as gl
from ... import howtext, reports
from ..registry import Tool, ToolResult, register


class Glossary(Tool):
    name = "glossary"
    description = "Look up a saved definition (op=lookup, optional term) or save a new one for future analyses (op=add, with term and text)."
    args = {"op": {"type": "str", "required": True, "enum": ["lookup", "add"]}, "term": {"type": "str", "max_len": 80}, "text": {"type": "str", "max_len": 400}}

    def validate(self, args, env):
        if args.get("op") == "add" and not (str(args.get("term") or "").strip() and str(args.get("text") or "").strip()):
            return ["To save a definition give both the term and what it means."]
        return []

    def run(self, args, env, prior):
        ws_key = gl.workspace_key(env.result.get("context"))
        if args["op"] == "add":
            term, text = str(args["term"]).strip(), str(args["text"]).strip()
            entry = {**gl.get_workspace(ws_key)}
            entry["definitions"] = {**entry["definitions"], term: text}
            err = gl.validate_entry(entry)
            if err:
                return ToolResult(ok=False, error=err, summary="A definition could not be saved")
            gl.merge_answers(ws_key, {}, {}, "", {term: text})
            how = howtext.record_how("Saved a definition in your glossary", ["the glossary"], [], ["It applies to analyses you run from now on; this report and its figures are unchanged."])
            ans, chips = reports._base("app", f"Saved: “{term}” means {text}. It will be used in future analyses; this report is unchanged.", "Glossary", how, [], 0)
            return ToolResult(summary="Saved a definition", answer=ans, chips=chips)
        words = {w for w in re.findall(r"[a-z0-9]+", str(args.get("term") or env.question).lower()) if len(w) > 2}
        rows = [f"{d.get('question')} → {d.get('answer_label') or d.get('answer')}. {d.get('effect', '')}".strip() for d in env.result.get("applied_definitions") or []]
        rows += [f"{t}: {x}" for t, x in gl.get_workspace(ws_key)["definitions"].items()]
        hit = [r for r in rows if not words or words & set(re.findall(r"[a-z0-9]+", r.lower()))] or rows
        how = howtext.record_how("Looked up the saved definitions", ["this run's definitions", "your saved glossary"], [], ["Nothing was recalculated."])
        text = "These are the definitions on record:" if hit else "No definitions are saved for this run."
        ans, chips = reports._base("app", text, "From your report", how, [], 0, lines=hit[:8])
        return ToolResult(summary="Looked up saved definitions", answer=ans, chips=chips)


register(Glossary)
