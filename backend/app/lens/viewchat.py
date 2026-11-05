"""Changing the view by chat. The AI (when there is one) returns commands from the closed list in viewedit; code validates every id and applies them with the same function the Hide and Change
buttons use, as one undoable step. Without an AI, plain phrases ("make this tab shorter", "undo") are read by simple rules, and everything else is answered with the available changes as chips.
No free-form styling or layout command exists: a request for one gets the chips."""
from __future__ import annotations

import re

from .. import llm as llmmod
from .. import viewedit as ve
from . import howtext, reports, rules, store

COMMAND_HELP = """Commands (each a small JSON object; "tab" is report|insights|strategy|dashboard; "id" is an item or section id from ITEMS):
{"cmd":"hide","id":<id>}  {"cmd":"show","id":<id>}  hide or show one item or section
{"cmd":"set_mode","tab":<tab>,"mode":"short|full"}  the short view (top 5 insights one line each plus their chart; at most 6 charts on the report and dashboard) or the full view
{"cmd":"set_details","on":true|false}  open every folded section on every page (the supporting detail: how a figure was calculated, long tables, step-by-step logs), or fold them away again
{"cmd":"top_n","tab":<tab>,"n":<1-50>}  show only the top N items of the tab (the rest sit under Show more)
{"cmd":"only_supporting","tab":<tab>,"on":true|false}  keep only the charts that support an action or the summary
{"cmd":"chart","id":<chart id>,"chart":"bar|line|share|table|histogram","grain":"day|week|month|quarter|year","n":<1-50>}  change one chart's type, time grain or top N (only the keys given are changed)
{"cmd":"move","id":<section id>,"dir":"up|down"}  move a section
{"cmd":"rename","id":<id>,"title":<the user's own text>}  rename a title
{"cmd":"pin","id":<id>}  {"cmd":"unpin","id":<id>}  {"cmd":"reset"}  {"cmd":"undo"}
Instead of an id, hide, show and pin take a SELECTOR the code resolves to ids, so a command can touch any number of items: {"cmd":"hide","select":{...}}.
A selector may have: "type" (chart|card|insight|action|idea|section), "tab", "section" (a section id), "measure" and "dimension" (words, matched to the item's labels; every item has a measure and a dimension, shown in ITEMS),
"title" (words), "position" ("first"|"last"|n), "rank" (n or {"lte":n,"gte":n}), "cited" (true when an action or the summary cites it), "ids" (a list).
{"cmd":"keep_only","scope":<selector for what to look at>,"keep":[{"select":<selector>,"best":<keep only the best n of these, optional>}, ...]}  hide everything in scope that is not kept (for example keep one chart about one measure and every chart about another)
A change to more than 3 items is shown to the user first and applied only when they confirm, so do not split a big change into small ones."""

SYSTEM = ("You turn a request to change what a data report shows into commands. You never calculate and never write report text. Reply with exactly one JSON object:\n"
          '{"action":"view","commands":[<at most 4 commands>]}   or   {"action":"unsupported","reason":"<one plain sentence, no numbers>"}\n' + COMMAND_HELP +
          '\nRules: use ids exactly as written in ITEMS; "this item" / "this chart" means FOCUS when one is given. A request for anything else (colours, fonts, layout, moving an item, new charts) is "unsupported". Return the JSON object only.')

NUMBER_WORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten".split())}
TAB_WORDS = (("strategy", r"\b(?:ideas?(?: to try)?|strateg(?:y|ies)|suggestions?)\b"), ("insights", r"\binsights?\b|\bfindings?\b"), ("dashboard", r"\bdashboard\b"), ("report", r"\breport\b"))
SHORTEN = re.compile(r"\b(?:short(?:er|en)?|simpl(?:e|er|ify)|declutter|tidy|condense|trim|compact|concise|brief(?:er)?|less busy|cut down|reduce)\b", re.I)
FULLER = re.compile(r"\b(?:full|everything|expand|longer|complete)\b[^.?]{0,20}\b(?:view|version|tab|report)?\b|\bshow (?:me )?(?:all|everything)\b", re.I)
NEEDED = re.compile(r"\b(?:only|just)\b[^.?]{0,25}\b(?:needed|necessary|useful|supporting|relevant|important|essential)\b[^.?]{0,15}\bcharts?\b|\bneeded charts?\b|\bcharts? (?:that|which) support", re.I)
TOPN = re.compile(r"\b(?:top|first|best|biggest|leading)\s+(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\b|\bonly\s+(?:the\s+)?(?:best\s+)?(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)\b", re.I)
DETAILS_ON = re.compile(r"\b(?:show|open|expand|unfold|reveal)\s+(?:me\s+)?(?:all\s+)?(?:of\s+)?(?:the\s+)?(?:folded\s+)?details\b(?!\s+(?:of|for|about|on|behind|from)\b)|\bexpand\s+(?:everything|all)\b", re.I)
DETAILS_OFF = re.compile(r"\b(?:collapse|fold(?:\s+away)?|close|hide)\s+(?:all\s+)?(?:of\s+)?(?:the\s+)?details\b|\bcollapse\s+(?:everything|all)\b", re.I)
UNDO = re.compile(r"^\s*(?:please\s+)?undo(?:\s+that|\s+it|\s+the last(?: change)?)?\s*[.!]?\s*$|\bundo (?:the |that |my )?(?:last )?(?:view )?change\b", re.I)
RESET = re.compile(r"\b(?:reset|restore|bring back)\b[^.?]{0,25}\b(?:view|everything|original|all)\b|\bshow everything again\b", re.I)
HIDE = re.compile(r"\b(?:hide|remove|get rid of|delete|drop|take away|collapse)\s+(?:this|it|that)\b|\b(?:hide|remove|get rid of|delete|drop)\s+(?:this|that|the)\s+(?:\w+\s+){0,2}(?:chart|graph|card|insight|idea|action|section|tile|tab|page)\b", re.I)
SHOW = re.compile(r"\b(?:show|restore|unhide|bring back)\s+(?:this|it|that)\b", re.I)
PIN = re.compile(r"\b(un)?pin\s+(?:this|it|that)\b", re.I)
RENAME = re.compile(r"\b(?:rename|retitle|call)\s+(?:this|it|that)(?:\s+(?:to|as))?\s*[:\-]?\s*[\"“'‘]?(?P<t>[^\"”'’\n]{2,100})", re.I)
CHART_TYPE = re.compile(r"\b(?:as|to|into)\s+(?:a\s+)?(bar|line|pie|share|table|histogram)(?:\s+chart)?\b", re.I)
GRAIN = re.compile(r"\b(daily|weekly|monthly|quarterly|yearly|by (?:day|week|month|quarter|year))\b", re.I)
GRAIN_MAP = {"daily": "day", "weekly": "week", "monthly": "month", "quarterly": "quarter", "yearly": "year"}


def _a(kind, text, label, how, chips, ai_calls, **extra):
    return reports._base(kind, text, label, how, chips, ai_calls, **extra)[0]


def _tab(question: str, current: str) -> str:
    for t, rx in TAB_WORDS:
        if re.search(rx, question, re.I):
            return t
    c = (current or "").strip().lower()
    for t, n in ve.TAB_NAMES.items():
        if c in (t, n.lower()):
            return t
    return "strategy" if "idea" in c else "report"


def _num(tok: str) -> int | None:
    return int(tok) if tok.isdigit() else NUMBER_WORDS.get(tok.lower())


def parse_rules(question: str, tab_label: str, cat: dict, focus: str | None, hidden: list[str] | None = None) -> list[dict] | None:
    """Commands for plain phrases, or None. Only phrases that clearly say one of the closed commands are read; anything else is left to the AI or to the chips."""
    q = " ".join((question or "").split())
    if UNDO.search(q):
        return [{"cmd": "undo"}]
    if RESET.search(q) and not re.search(r"\bshort", q, re.I):
        return [{"cmd": "reset"}]
    if DETAILS_OFF.search(q):
        return [{"cmd": "set_details", "on": False}]
    if DETAILS_ON.search(q):
        return [{"cmd": "set_details", "on": True}]
    tab = _tab(q, tab_label)
    f = cat["items"].get(focus or "")
    cmds: list[dict] = []
    if f and f["kind"] == "chart":
        c: dict = {}
        if m := CHART_TYPE.search(q):
            c["chart"] = {"pie": "share"}.get(m[1].lower(), m[1].lower())
        if m := GRAIN.search(q):
            g = m[1].lower()
            c["grain"] = GRAIN_MAP.get(g, g.split()[-1])
        if m := TOPN.search(q):
            c["n"] = _num(m[1] or m[2]) or 0
        if c:
            return [{"cmd": "chart", "id": f["id"], **c}]
    if f:
        if m := RENAME.search(q):
            return [{"cmd": "rename", "id": f["id"], "title": m["t"].strip()}]
        if m := PIN.search(q):
            return [{"cmd": "unpin" if m[1] else "pin", "id": f["id"]}]
        if HIDE.search(q):
            return [{"cmd": "hide", "id": f["id"]}]
        if SHOW.search(q):
            return [{"cmd": "show", "id": f["id"]}]
    elif HIDE.search(q) and re.search(r"\b(?:this|that)\s+(?:\w+\s+){0,2}(?:chart|graph|card|insight|idea|action|section|tile|tab|page)\b", q, re.I):
        return []           # "remove this chart" with no chart in focus: ask which one (the chips explain how)
    if not f:
        tg = toggle_cmds(q, cat, hidden or [])
        if tg is not None:
            return tg                       # hide or show something named in the user's words (a [] means nothing matched: ask which)
    if NEEDED.search(q):
        cmds.append({"cmd": "only_supporting", "tab": tab, "on": True})
    if m := TOPN.search(q):
        n = _num(m[1] or m[2])
        if n:
            cmds.append({"cmd": "top_n", "tab": tab, "n": n})
    if SHORTEN.search(q):
        cmds.insert(0, {"cmd": "set_mode", "tab": tab, "mode": "short"})
    elif FULLER.search(q) and not cmds:
        cmds.append({"cmd": "set_mode", "tab": tab, "mode": "full"})
    return cmds or None


def _items_for_prompt(cat: dict, tab: str) -> list[dict]:
    return [{"id": i, "type": it["kind"], "title": it["title"], "measure": it.get("measure") or [], "dimension": it.get("dimension") or [], "rank": it.get("rank"), "cited": it.get("cited")}
            for i, it in cat["items"].items() if it["tab"] == tab][:70]


def ask_ai(llm, question: str, tab: str, cat: dict, focus: str | None) -> tuple[dict | None, int, str]:
    """(reply object, AI calls used, failure reason). One call; code validates the reply afterwards."""
    model = llmmod.deterministic(llm)
    user = (f"CURRENT TAB: {tab}\nITEMS ON THIS TAB: {_items_for_prompt(cat, tab)}\nSECTIONS: " + ", ".join(f"{s} ({cat['items'][s]['title']})" for t in ve.TABS for s in cat["tabs"][t]["sections"])
            + (f"\nFOCUS: {focus} ({cat['items'][focus]['title']})" if focus in cat["items"] else "") + f"\n\nREQUEST: {question}")
    with llmmod.capture() as cap:
        obj, why = llmmod.ask_json_ex(model, SYSTEM, user, repair=False)
        calls = cap.calls
    return obj, calls, why


def chips_for(result: dict, tab_label: str) -> list[dict]:
    return [reports.ichip(c["text"], {"kind": "view_cmd", "cmds": c["cmds"]}) for c in ve.available_changes(result, _tab("", tab_label))[:5]]


def preview_answer(wd, result: dict, pv: dict, ai_calls: int):
    """The answer that shows a bulk change before anything is applied: what would be kept, hidden and shown, and an Apply chip that carries the same change as explicit ids."""
    apply_chip = reports.ichip("Apply this change", {"kind": "view_cmd", "cmds": pv["explicit"], "confirmed": True})
    how = howtext.record_how("Worked out which items a change to the view would touch, and waited for you to confirm", ["this report's items and sections"], [],
                             ["Nothing has been changed yet. Tap Apply to make this change; Undo puts everything back."])
    ans, _ = reports._base("view", pv["text"], "Check before I change this", how, [apply_chip], ai_calls, view={"changed": False, "preview": True, "count": pv["count"]})
    return ans, [apply_chip]


def answer(wd, result: dict, spec: dict, question: str, tab_label: str, llm, ai_ok: bool, ai_calls: int, focus: str | None, view_state: dict | None) -> tuple[dict, list[dict], int, str]:
    """(answer, chips, AI calls used here, failure reason). `view_state` is the browser's state for the read-only sample run (never saved on the server); None means use the run's saved state."""
    cat = ve.catalogue(result)
    cmds = spec.get("commands") if isinstance(spec.get("commands"), list) else spec.get("cmds") if isinstance(spec.get("cmds"), list) else None
    used, fail, source = 0, "", "the AI's command" if cmds is not None else ""
    if cmds is None:
        hidden = ve.normalise(view_state if view_state is not None else store.get_view_state(wd))["hidden"]
        cmds, source = parse_rules(question, tab_label, cat, focus, hidden), "a plain-words rule"          # plain phrases need no AI: cheaper, and the same every time
    styling = cmds is None and rules.wants_view_change(question)           # colours, fonts, layout, dark mode: not a view change that exists, and no AI call is needed to say so
    if cmds is None and not styling and ai_ok and llm is not None and question:
        obj, used, fail = ask_ai(llm, question, _tab("", tab_label), cat, focus)
        if obj and isinstance(obj.get("commands"), list) and str(obj.get("action", "view")).lower() == "view":
            cmds, source = obj["commands"], "the AI's command"
    chips = chips_for(result, tab_label) or reports.next_chips(result, "view", "", reports.prev_chip_ids(wd))        # a run with nothing to change (no report page) is offered the usual questions
    unsure = "I can change what is shown, but not how the app looks (colours, fonts, layout). These are the view changes I can make: tap one."
    if not cmds:
        why = "which item?" if cmds == [] else "no match"
        text = ("Which item do you mean? Click it first, or name it by its title. " if cmds == [] else "") + unsure
        how = howtext.record_how("Looked for a view change from the closed list and found none", ["the list of view changes Lens can make"], [], ["Nothing was changed. " + why.capitalize()])
        return _a("view", text, "View changes", how, chips, ai_calls + used, view={"changed": False}), chips, used, fail
    state = view_state if view_state is not None else store.get_view_state(wd)
    if not spec.get("confirmed"):
        try:
            pv = ve.preview(result, state, cmds, cat)
        except ve.ViewError as e:
            how = howtext.record_how("Checked the requested change against this report's items and refused it", ["this report's items and sections"], [], ["Nothing was changed."])
            return _a("view", f"{e}", "Not changed", how, chips, ai_calls + used, view={"changed": False}), chips, used, fail
        if pv:
            a, c = preview_answer(wd, result, pv, ai_calls + used)
            return a, c, used, fail
    try:
        new, msgs = ve.apply_many(result, state, cmds, cat)
    except ve.ViewError as e:
        how = howtext.record_how("Checked the requested view change against this report's items and refused it", ["this report's items and sections"], [], ["Nothing was changed."])
        return _a("view", f"{e}", "Not changed", how, chips, ai_calls + used, view={"changed": False}), chips, used, fail
    changed = ve._core(ve.normalise(new)) != ve._core(ve.normalise(state))          # an undo changes the view too (its history gets shorter)
    if view_state is None and changed:
        store.set_view_state(wd, new)
    text = " ".join(msgs)
    how = howtext.record_how(f"Applied {len(cmds)} view change{'s' if len(cmds) != 1 else ''} from the closed list, after checking every id exists in this report", [f"{source}", "this report's items and sections"], [],
                             ["Only what is shown changed: no figure, trust label, calculation or sentence of the report was touched.", "Everything hidden stays in your saved report and can be restored from Hidden items. Undo goes back one step."])
    extra = {"view": {"changed": changed, "commands": cmds, "undo": changed, **({"state": ve.normalise(new)} if view_state is not None else {})}}
    return _a("view", text, "View changed" if changed else "No change", how, chips, ai_calls + used, **extra), chips, used, fail


# ------------------------------------------------------------------ hide or show something named in the user's own words (the no-AI fallback; with an AI the planner picks the ids)
TOGGLE_RE = re.compile(r"^\s*(?:please\s+|can you\s+|could you\s+)?(?P<v>hide|remove|unhide|restore|show(?!\s+(?:me\s+)?(?:all|everything|only|just|the\s+(?:full|short)))|bring back)\s+(?:the\s+|my\s+|all\s+(?:the\s+)?)?(?P<what>[^.?!]{2,80}?)\s*[.!]?\s*$", re.I)
TAB_VOCAB = {"report": ["report", "answer", "summary"], "insights": ["insights", "insight", "findings", "finding", "evidence"], "strategy": ["ideas", "idea", "strategies", "strategy", "suggestions"],
             "dashboard": ["dashboard", "charts", "visuals"], "data": ["data", "table", "rows"], "model": ["files", "connect", "model", "links", "tables"],
             "cleaning": ["cleaning", "cleaned", "clean", "log", "changes"], "plan": ["plan", "analysed", "analysis", "analyse", "questions", "definitions", "setup"],
             "chat": ["chat", "conversation"]}


def _stem(w: str) -> str:
    return w[:4] if len(w) >= 5 else w


FILLER = ("the", "of", "and", "to", "my", "all", "tab", "page", "section", "part", "we", "how", "what", "your", "our", "this", "that")


def _words(text: str) -> set[str]:
    return {_stem(w) for w in re.findall(r"[a-z0-9]+", str(text).lower()) if len(w) > 1 and w not in FILLER}


def _exact(text: str) -> set[str]:
    return {w.rstrip("s") for w in re.findall(r"[a-z0-9]+", str(text).lower()) if len(w) > 1 and w not in FILLER}


def toggle_cmds(question: str, cat: dict, hidden: list[str]) -> list[dict] | None:
    """hide or show commands for 'hide the cleaning log'-style requests, by matching the words against item and section titles and tab vocabulary. None when the request is not of that form,
    [] when it is but nothing matches (the caller then asks which)."""
    m = TOGGLE_RE.match(question or "")
    if not m:
        return None
    verb = "hide" if m["v"].lower() in ("hide", "remove") else "show"
    want = _words(m["what"])
    if not want:
        return []
    pool = {i: it for i, it in cat["items"].items() if (verb == "hide" and i not in hidden) or (verb == "show" and i in hidden)}
    exact = _exact(m["what"])
    for t, vocab in TAB_VOCAB.items():              # the name of a tab or its part ("the cleaning log", "the conversation"): that tab's sections
        if exact and exact <= {w.rstrip("s") for w in vocab + ve.TAB_NAMES[t].lower().split()}:
            secs = [sc for sc in cat["tabs"][t]["sections"] if (verb == "hide" and sc not in hidden) or (verb == "show" and sc in hidden)]
            if secs:
                return [{"cmd": verb, "id": sc} for sc in secs]
    hits = [i for i, it in pool.items() if want <= _words(it["title"])]
    if len(hits) > 1:
        hits = sorted(hits, key=lambda i: (cat["items"][i]["kind"] != "section", len(cat["items"][i]["title"])))[:1]
    if hits:
        return [{"cmd": verb, "id": hits[0]}]
    return []
