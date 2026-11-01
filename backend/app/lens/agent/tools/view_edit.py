"""view_edit: change what the user sees, from the closed command set in `viewedit` (hide or show an item, section, group or type; short or full view; top N; only the charts that support an action or the summary;
move a section; rename with "Edited by you"; pin or unpin). Code validates every id and applies the commands with the same function the Hide and Change buttons use.
One request is one Undo: however many view steps it has, undo goes back to how the view was when the request began. Presentation only: no figure, calculation or sentence of the report changes."""
from __future__ import annotations

import copy

from .... import viewedit as ve
from ... import howtext, reports, store, viewchat
from ..registry import Tool, ToolResult, register


def current_state(env) -> dict:
    cur = getattr(env, "view_cur", None)
    if cur is not None:
        return cur
    return env.view_state if env.view_state is not None else store.get_view_state(env.wd)


def apply_commands(env, cmds: list) -> tuple[dict, list[str], bool]:
    """(new state, messages, changed). Raises ve.ViewError. History is rewritten so the whole request is one Undo step."""
    if env.view_base is None:
        env.view_base = ve.normalise(current_state(env))
    base = env.view_base
    new, msgs = ve.apply_many(env.result, current_state(env), cmds, env.cat)
    if not any(isinstance(c, dict) and c.get("cmd") == "undo" for c in cmds):
        changed = ve._core(new) != ve._core(base)
        if changed:
            new = ve.normalise(new)
            new["history"] = (base.get("history") or [])[-(ve.HISTORY - 1):] + [ve._core(base)]
    else:
        changed = ve._core(ve.normalise(new)) != ve._core(base)
    if env.view_state is None:
        store.set_view_state(env.wd, new)
    else:
        env.view_state = new
    env.view_cur = new
    return new, msgs, changed


class ViewEdit(Tool):
    name = "view_edit"
    description = ("Change what is shown, with commands from the closed list. " + viewchat.COMMAND_HELP.split("\n", 1)[1].replace("\n", " "))
    args = {"commands": {"type": "list", "required": True, "max_len": 6, "help": "view commands; ids from VISIBLE ITEMS; hide/show also accept {\"kind\":\"chart|card|insight|action|strategy\",\"tab\":...} for a whole group"}}
    soft_validation = True

    def validate(self, args, env):
        try:
            ve.apply_many(env.result, current_state(env), args["commands"], env.cat)
        except ve.ViewError as e:
            return [str(e)]
        return []

    def run(self, args, env, prior):
        chips = viewchat.chips_for(env.result, env.tab)
        if not getattr(env, "confirmed", False):
            try:
                pv = ve.preview(env.result, current_state(env), args["commands"], env.cat)
            except ve.ViewError as e:
                pv = None
                bad = str(e)
                how = howtext.record_how("Checked the requested change against this report's items and refused it", ["this report's items and sections"], [], ["Nothing was changed."])
                ans, _ = reports._base("view", bad, "Not changed", how, chips, 0, view={"changed": False})
                return ToolResult(ok=False, summary="The change to the view was refused", answer=ans, chips=chips, error=bad)
            if pv:
                ans, pchips = viewchat.preview_answer(env.wd, env.result, pv, 0)
                return ToolResult(summary="Showed what a bigger change would touch and waited for your confirmation", answer=ans, chips=pchips)
        try:
            new, msgs, changed = apply_commands(env, args["commands"])
        except ve.ViewError as e:
            how = howtext.record_how("Checked the requested change against this report's items and refused it", ["this report's items and sections"], [], ["Nothing was changed."])
            ans, _ = reports._base("view", str(e), "Not changed", how, chips, 0, view={"changed": False})
            return ToolResult(ok=False, summary="The view change was refused", answer=ans, chips=chips, error=str(e))
        how = howtext.record_how(f"Applied {len(args['commands'])} view change{'s' if len(args['commands']) != 1 else ''} from the closed list, after checking every id exists in this report",
                                 ["this report's items and sections"], [], ["Only what is shown changed: no figure, trust label, calculation or sentence of the report was touched.",
                                                                           "Everything hidden stays in your saved report and can be restored from Hidden items. One Undo reverts this whole request."])
        extra = {"view": {"changed": changed, "commands": args["commands"], "undo": changed, **({"state": ve.normalise(new)} if env.view_state is not None else {})}}
        ans, _ = reports._base("view", " ".join(msgs), "View changed" if changed else "No change", how, chips, 0, **extra)
        return ToolResult(summary=f"Changed the view ({len(args['commands'])} command{'s' if len(args['commands']) != 1 else ''})", answer=ans, chips=chips, view_changed=changed)


register(ViewEdit)
