"""brief_manage: list, reopen, rename, lightly edit and delete the briefs saved with this run. An edited line is labelled "Edited by you"; the data-quality note cannot be edited or removed."""
from __future__ import annotations

from ... import briefs as bf
from ... import howtext, reports, store
from ..registry import Tool, ToolResult, register

SECTIONS = ("headline", "key_figures", "recommendations")


def _answer(env, text: str, label: str, briefs: list[dict], chips=None):
    how = howtext.record_how("Managed the briefs saved with this run", ["the briefs saved with this run"], [], ["The original report was not changed."])
    ans, _ = reports._base("brief", text, label, how, chips or [], 0, briefs=briefs)
    return ans


class BriefManage(Tool):
    name = "brief_manage"
    description = 'Use when the user wants to list, open, rename, edit or delete a brief that was already saved. op=list|open|rename|edit|delete|delete_all; id=<brief id or a title/audience that names one>; rename needs title; edit needs section (headline|key_figures|recommendations), index (0-based, for lists) and text.'
    args = {"op": {"type": "str", "required": True, "enum": ["list", "open", "rename", "edit", "delete", "delete_all"]}, "id": {"type": "str"}, "title": {"type": "str", "max_len": 90},
            "section": {"type": "str", "enum": list(SECTIONS)}, "index": {"type": "int"}, "text": {"type": "str", "max_len": 300}}
    soft_validation = True

    def validate(self, args, env):
        op = args.get("op")
        if "brief" in (getattr(env, "plan_steps", None) or []):
            return []
        if op in ("open", "rename", "edit", "delete") and not bf.find(env.wd, str(args.get("id") or "")):
            return ["I can't tell which brief you mean. Say its title or audience, or ask me to list your briefs."]
        if op == "rename" and not str(args.get("title") or "").strip():
            return ["Say the new title."]
        if op == "edit" and (not args.get("section") or not str(args.get("text") or "").strip()):
            return ["Say which part to change and the new text."]
        return []

    def run(self, args, env, prior):
        op = args["op"]
        bs = store.get_briefs(env.wd)
        if op == "list":
            if not bs:
                return ToolResult(summary="There are no saved briefs", answer=_answer(env, "You have no saved briefs yet. Ask me for one and say who it is for.", "Briefs", []))
            return ToolResult(summary="Listed the saved briefs", answer=_answer(env, f"You have {len(bs)} saved brief{'s' if len(bs) != 1 else ''}: " + "; ".join(f"{b['title']}" for b in bs) + ".", "Briefs", bs))
        if op == "delete_all":
            store.put_briefs(env.wd, [])
            return ToolResult(summary="Deleted every saved brief", answer=_answer(env, f"Deleted {len(bs)} brief{'s' if len(bs) != 1 else ''}. The report itself is unchanged.", "Briefs", []))
        b = bf.find(env.wd, str(args.get("id") or ""), bs)
        if b is None:
            return ToolResult(ok=False, error="I can't tell which brief you mean.", summary="No such brief")
        if op == "open":
            return ToolResult(summary=f"Opened “{b['title']}”", answer=_answer(env, b["no_info"] or (b.get("headline") or {}).get("text", b["title"]), "Brief", [b]))
        if op == "delete":
            bf.delete(env.wd, b["id"])
            return ToolResult(summary=f"Deleted “{b['title']}”", answer=_answer(env, f"Deleted “{b['title']}”. The report itself is unchanged.", "Briefs", []))
        try:
            if op == "rename":
                b = bf.rename(env.wd, b["id"], args["title"])
            else:
                b = bf.edit(env.wd, b["id"], args["section"], args.get("index"), args["text"])
        except ValueError as e:
            return ToolResult(ok=False, error=str(e), summary="The brief could not be changed")
        msg = f"Renamed to “{b['title']}” (Edited by you)." if op == "rename" else "Updated. Your own lines are labelled Edited by you; the data-quality note stays as it is."
        return ToolResult(summary=f"{'Renamed' if op == 'rename' else 'Edited'} “{b['title']}”", answer=_answer(env, msg, "Brief", [b]))


register(BriefManage)
