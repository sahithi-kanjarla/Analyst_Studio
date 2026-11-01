"""glossary: look up the saved definitions this run used (and the company's saved glossary), or add one. A new definition applies to future analyses; this report is not changed."""
from __future__ import annotations

import re

from .... import glossary as gl
from ... import howtext, records, reports
from ..registry import Tool, ToolResult, register


class Glossary(Tool):
    name = "glossary"
    description = 'Use when the user asks which definitions or assumptions were used, what a term means in this report, or wants a definition remembered: op=lookup (optional term) lists the report\'s \\"Definitions used\\" and the saved glossary; op=add (with term and text) saves a new definition for future analyses.'
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
        lines, sources = records.definitions_lines(env.result)
        term = str(args.get("term") or "").strip()
        words = {w for w in re.findall(r"[a-z0-9]+", term.lower()) if len(w) > 2}
        hit = [l for l in lines if not words or words & set(re.findall(r"[a-z0-9]+", l.lower()))] or (lines if not words else [])
        how = howtext.record_how("Looked up the definitions used in this report and the ones you saved", sources, [], ["Nothing was recalculated."])
        if not lines:
            text = "No definitions or assumptions were used in this report, and you have no saved definitions."
        elif not hit:
            text = f"None of the {len(lines)} definitions on record is about “{term}”."
        else:
            text = f"{len(hit)} definition{'s' if len(hit) != 1 else ''} or assumption{'s' if len(hit) != 1 else ''} on record, from this report's “Definitions used” and the definitions you saved:"
        ans, chips = reports._base("app", text, "From your report", how, [], 0, lines=hit[:12])
        return ToolResult(summary="Looked up saved definitions", answer=ans, chips=chips)


register(Glossary)
