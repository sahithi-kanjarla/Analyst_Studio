"""export: a brief, all briefs (a zip, one PDF each), the full report or the short report as a PDF, or "Copy as text". The file is made on the server when the link is opened."""
from __future__ import annotations

from ... import briefs as bf
from ... import exporter, howtext, reports, store
from ..registry import Tool, ToolResult, register

WHAT = ["brief", "all_briefs", "report", "short_report", "text"]
LABEL = {"brief": "Download the brief as a PDF", "all_briefs": "Download all briefs (zip of PDFs)", "report": "Download the report as a PDF", "short_report": "Download the short report as a PDF", "text": "Copy as text"}


class Export(Tool):
    name = "export"
    description = "Make a file to keep: what=brief (needs id: a brief id or a title/audience), all_briefs (zip), report, short_report (PDFs), or text (plain text of a brief when id is given, else of the report)."
    args = {"what": {"type": "str", "required": True, "enum": WHAT}, "id": {"type": "str"}}
    soft_validation = True

    def validate(self, args, env):
        if "brief" in (getattr(env, "plan_steps", None) or []):
            return []                         # the brief is written by an earlier step of this request
        if args.get("what") == "brief" and not bf.find(env.wd, str(args.get("id") or "last")):
            return ["I can't tell which brief to export. Say its title or audience."]
        if args.get("what") == "all_briefs" and not store.get_briefs(env.wd):
            return ["There are no saved briefs to download yet."]
        return []

    def run(self, args, env, prior):
        what = args["what"]
        b = bf.find(env.wd, str(args.get("id") or "last")) if what in ("brief", "text") and (what == "brief" or args.get("id")) else None
        run_id = str(env.result.get("run_id") or env.wd.name)
        url = f"/api/runs/{run_id}/export/{what}" + (f"?brief={b['id']}" if b else "")
        exp = {"what": what, "label": LABEL[what], "url": url, "browser": bool(exporter.find_browser()) or what == "text"}
        if what == "text":
            exp["text"] = exporter.brief_text(b, env.result) if b else exporter.report_text(env.result)
        how = howtext.record_how(f"Prepared an export: {LABEL[what].lower()}", ["the saved report" if not b else f"the saved brief “{b['title']}”"], [], ["The file is made from the saved content, with no buttons or menus and no internal column names."])
        text = {"text": "Here is the text. Use Copy to put it on your clipboard."}.get(what, "Your file is ready. Use the link to download it.")
        if what != "text" and not exp["browser"]:
            text = "A PDF can't be made on this server (no browser is installed), but you can copy the text instead."
        ans, _ = reports._base("export", text, "Export", how, [], 0, export=exp)
        return ToolResult(summary=f"Prepared {LABEL[what].lower()}", answer=ans)


register(Export)
