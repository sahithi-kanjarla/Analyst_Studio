"""View edits: what the user sees of a finished run (which items are shown, short or full, the order of sections, titles, pins), by hand and by chat, through ONE mechanism.

    command (closed list, one small JSON object)  ->  validate against the run's items  ->  apply to the saved view state  ->  resolve what each tab shows

A view edit is presentation only. It never changes a figure, a trust label, a calculation or a sentence of the report: nothing here writes result.json or report.md, hidden items stay in the
saved run (and can be printed), and a renamed title is the user's own text, labelled "Edited by you". Every command is idempotent (applying it twice leaves the same state), is validated
(an item or section id that does not exist is refused with a plain sentence) and can be undone: each applied change pushes the previous state onto a history that Undo pops.

State (saved in lens.json under "view_edits", or held by the browser for the read-only sample run):
  hidden [ids] · short {tab: bool} · top_n {tab: n} · only_supporting {tab: bool} · chart {id: {chart, grain, n}} · order {tab: [section ids]} · titles {id: text} · pinned [ids] · history [older states]
Item ids: "<tab>:<kind>:<n>", for example insights:card:f4, report:action:0, report:chart:cmp_revenue_segment_none_none, strategy:s1; sections are "<tab>:<name>", for example report:actions."""
from __future__ import annotations

import copy
import json
import os
import re

TABS = ("report", "insights", "strategy", "dashboard", "data", "model", "cleaning", "plan", "powerbi", "chat")      # every tab of the results page (frontend/src/tabs.ts)
TAB_NAMES = {"report": "Report", "insights": "Insights", "strategy": "Ideas to try", "dashboard": "Dashboard", "data": "Your data", "model": "How files connect", "cleaning": "What we cleaned",
             "plan": "How we analysed", "powerbi": "Power BI export", "chat": "Chat"}
DEFAULT_SHORT = ("report", "insights", "dashboard")      # these tabs open in the short view; "Show all" switches a tab to the full view
SHORT_INSIGHTS = 5            # the short Insights view: the top 5, ranked by the report's own order
MAX_SHORT_CHARTS = 6          # the short Report and Dashboard: at most 6 charts, and only those an action or the summary cites
MAX_N = 50
MAX_TITLE = 120
HISTORY = 20
CHART_TYPES = ("bar", "line", "share", "table", "histogram")
GRAINS = ("day", "week", "month", "quarter", "year")
COMMANDS = ("hide", "show", "keep_only", "set_mode", "set_details", "top_n", "only_supporting", "chart", "move", "rename", "pin", "unpin", "reset", "undo")


class ViewError(Exception):
    """A request that cannot be applied; the message is a plain sentence for the user."""


def empty() -> dict:
    return {"hidden": [], "short": {}, "top_n": {}, "only_supporting": {}, "chart": {}, "order": {}, "titles": {}, "pinned": [], "details": False, "history": []}


def normalise(state) -> dict:
    """A state read from disk or from the browser, with every key present and only well-formed values kept."""
    s = empty()
    if not isinstance(state, dict):
        return s
    s["hidden"] = [i for i in state.get("hidden") or [] if isinstance(i, str)][:2000]
    s["pinned"] = [i for i in state.get("pinned") or [] if isinstance(i, str)][:200]
    s["short"] = {t: bool(v) for t, v in (state.get("short") or {}).items() if t in TABS}           # True or False, only where it differs from the tab's default
    s["only_supporting"] = {t: True for t, v in (state.get("only_supporting") or {}).items() if t in TABS and v}
    s["top_n"] = {t: int(n) for t, n in (state.get("top_n") or {}).items() if t in TABS and isinstance(n, int) and 1 <= n <= MAX_N}
    s["chart"] = {i: {k: v for k, v in c.items() if k in ("chart", "grain", "n") and v is not None} for i, c in (state.get("chart") or {}).items() if isinstance(c, dict)}
    s["order"] = {t: [i for i in o if isinstance(i, str)] for t, o in (state.get("order") or {}).items() if t in TABS and isinstance(o, list)}
    s["titles"] = {i: str(t)[:MAX_TITLE] for i, t in (state.get("titles") or {}).items() if isinstance(i, str) and isinstance(t, str) and t.strip()}
    s["details"] = bool(state.get("details"))                 # every folded section of every page open (the supporting detail, never the main content)
    s["history"] = [normalise({**h, "history": []}) for h in (state.get("history") or [])[-HISTORY:] if isinstance(h, dict)]
    return s


def _core(state: dict) -> dict:
    return {k: copy.deepcopy(v) for k, v in state.items() if k != "history"}


def is_original(state: dict) -> bool:
    return _core(normalise(state)) == _core(empty())


# ------------------------------------------------------------------ the run's items
def _plain(t) -> str:
    return " ".join(re.sub(r"[*_`]", "", str(t or "")).split())


def _clip(t, n=90) -> str:
    t = _plain(t)
    return t if len(t) <= n else t[: n - 1].rstrip() + "…"


def _action_title(text: str) -> str:
    i = str(text).find(":")
    return _clip(text[:i] if i > 0 else text, 80)


def catalogue(result: dict) -> dict:
    """{"items": {id: {...}}, "tabs": {tab: {"sections": [ids in default order], "primary": [ids in the report's own order]}}}. Built from the saved result only."""
    items: dict[str, dict] = {}
    tabs: dict[str, dict] = {t: {"sections": [], "primary": []} for t in TABS}
    rv = result.get("report_view") or {}
    page = rv.get("page") or {}
    findings = {f.get("id"): f for f in result.get("findings") or [] if f.get("id")}
    by_chart = {}
    for f in findings.values():
        if f.get("chart"):
            by_chart.setdefault(f["chart"], []).append(f["id"])
    for c in (rv.get("evidence") or []) + (rv.get("appendix") or []):
        if c.get("chart"):
            by_chart.setdefault(c["chart"], [])
            by_chart[c["chart"]] += [m for m in c.get("members") or [] if m not in by_chart[c["chart"]]]
    action_ev = {m for a in rv.get("actions") or [] for m in a.get("evidence") or []}
    action_ev |= {m for s in (rv.get("strategies") or {}).get("strategies") or [] if s.get("kind", "data") == "data" for m in s.get("evidence") or []}
    action_charts = {a.get("chart") for a in page.get("actions") or [] if a.get("chart")}
    summary_ev = {h.get("id") for h in rv.get("headlines") or []} | set((result.get("goal_answer") or {}).get("finding_ids") or [])
    for a in rv.get("answers") or []:
        summary_ev |= set(a.get("fact_ids") or [])

    def score(path: str | None, fids: list[str]) -> int:
        """2 when an action cites the chart, plus 1 when the summary does: a chart that nothing cites scores 0."""
        s = 0
        if (path and path in action_charts) or any(f in action_ev for f in fids):
            s += 2
        if any(f in summary_ev for f in fids):
            s += 1
        return s

    def section(tab, name, title):
        sid = f"{tab}:{name}"
        items[sid] = {"id": sid, "tab": tab, "kind": "section", "title": title, "section": None}
        tabs[tab]["sections"].append(sid)
        return sid

    def add(tab, sec, kind, iid, title, **kw):
        items[iid] = {"id": iid, "tab": tab, "kind": kind, "title": _clip(title), "section": sec, **kw}
        return iid

    def chart_item(tab, sec, iid, title, path, fids, tool=None):
        ch = add(tab, sec, "chart", iid, title, chart=path, finding_ids=fids, score=score(path, fids), changeable=changeable(findings, fids, tool))
        return ch

    # ---- report
    if page:
        s_short, s_num = section("report", "short", "The short answer"), section("report", "numbers", "Key figures")
        s_act, s_ev, s_beh = section("report", "actions", "What to do"), section("report", "evidence", "The evidence"), section("report", "behind", "Behind the numbers")
        items[s_short]["text"] = _plain(page.get("headline"))
        for i, t in enumerate(page.get("tiles") or []):
            add("report", s_num, "card", f"report:tile:{i}", t.get("label", "Figure"), text=f"{t.get('label')}: {t.get('value')}")
        for i, a in enumerate(page.get("actions") or []):
            iid = add("report", s_act, "action", f"report:action:{i}", _action_title(a.get("text", "")), text=_plain(a.get("text")), finding_ids=(rv.get("actions") or [{}] * (i + 1))[i].get("evidence", []) if i < len(rv.get("actions") or []) else [])
            tabs["report"]["primary"].append(iid)
        for c in (page.get("charts") or [])[:MAX_SHORT_CHARTS]:
            path = c.get("path") or ""
            base = re.sub(r"\.\w+$", "", path.rsplit("/", 1)[-1])
            fids = by_chart.get(path, [])
            ch = chart_item("report", s_ev, f"report:chart:{base}", c.get("title", "Chart"), path, fids)
            items[ch]["text"] = _plain(c.get("caption"))
        items[s_beh]["text"] = ""
    # ---- insights
    if rv:
        a_sec, e_sec = section("insights", "answer", "The answer"), section("insights", "evidence", "Evidence")
        k_sec, p_sec = section("insights", "checked", "Checked, nothing found"), section("insights", "appendix", "Appendix")
        for h in rv.get("headlines") or []:
            add("insights", a_sec, "insight", f"insights:headline:{h['id']}", h.get("text", ""), text=_plain(h.get("text")), finding_ids=[h["id"]])
        for i, a in enumerate(rv.get("actions") or []):
            add("insights", a_sec, "action", f"insights:action:{i}", _action_title(a.get("text", "")), text=_plain(a.get("text")), finding_ids=a.get("evidence") or [])
        for c in rv.get("evidence") or []:
            iid = add("insights", e_sec, "card", f"insights:card:{c['id']}", c.get("headline", ""), text=_plain(" ".join([c.get("headline", ""), c.get("observation", ""), c.get("why_it_matters", "")])), finding_ids=list(c.get("members") or [c["id"]]))
            tabs["insights"]["primary"].append(iid)
            if c.get("chart"):
                ch = chart_item("insights", e_sec, f"insights:chart:{c['id']}", c.get("title") or c.get("headline", ""), c["chart"], list(c.get("members") or [c["id"]]), c.get("tool"))
                items[ch]["card"] = iid
        for c in rv.get("checked") or []:
            add("insights", k_sec, "insight", f"insights:checked:{c['id']}", c.get("line", ""), text=_plain(c.get("line")), finding_ids=[c["id"]])
        for c in rv.get("appendix") or []:
            add("insights", p_sec, "card", f"insights:card:{c['id']}", c.get("headline", ""), text=_plain(" ".join([c.get("headline", ""), c.get("observation", "")])), finding_ids=list(c.get("members") or [c["id"]]))
    # ---- ideas to try
    strategies = (rv.get("strategies") or {}).get("strategies") or result.get("strategies") or []
    if strategies:
        l_sec = section("strategy", "list", "Ideas, ranked")
        for s in strategies:
            iid = add("strategy", l_sec, "strategy", f"strategy:{s['id']}", s.get("title", ""), text=_plain(" ".join([s.get("title", ""), s.get("objective", ""), s.get("rationale", "")])), finding_ids=s.get("evidence") or [])
            tabs["strategy"]["primary"].append(iid)
    # ---- dashboard
    if findings:
        v_sec, g_sec, t_sec = section("dashboard", "visuals", "Interactive charts"), section("dashboard", "gallery", "Charts from the findings"), section("dashboard", "takeaways", "What stands out")
        gal = rv.get("gallery") or {}
        order = list(dict.fromkeys(list((result.get("goal_answer") or {}).get("finding_ids") or []) + list(gal.get("key") or []) + list(gal.get("more") or [])
                                   + [f for f, x in sorted(findings.items(), key=lambda kv: -((kv[1].get("insight") or {}).get("score") or 0))]))
        for fid in order:
            f = findings.get(fid)
            if not f or not f.get("insight") or not f.get("chart"):
                continue
            iid = chart_item("dashboard", g_sec, f"dashboard:chart:{fid}", (f.get("insight") or {}).get("headline") or f.get("title", ""), f["chart"], [fid], f.get("tool"))
            items[iid]["text"] = _plain((f.get("insight") or {}).get("headline"))
            tabs["dashboard"]["primary"].append(iid)
    _other_tabs(result, items, tabs, section, add)
    from . import viewselect
    viewselect.enrich(result, items, tabs, findings, action_ev | summary_ev, action_charts)
    return {"items": items, "tabs": tabs}


def _other_tabs(result: dict, items: dict, tabs: dict, section, add) -> None:
    """The tabs that list the run's own records (the cleaned table, how files connect, the cleaning log, definitions and how the data was set up, the plan, the Power BI export, the chat).
    Each exposes at least one item with a stable id and a title, so questions, focus, view edits and briefs work from any tab."""
    prof = result.get("profile") or {}
    sec = section("data", "tables", "Tables")
    add("data", sec, "card", "data:table:clean", f"Cleaned table ({prof.get('rows', 0):,} rows)" if isinstance(prof.get("rows"), int) else "Cleaned table",
        text=f"The cleaned table behind every number: {prof.get('rows', 'n/a')} rows and {prof.get('cols', 'n/a')} columns.")
    dm = result.get("data_model") or {}
    sec = section("model", "links", "Tables and links")
    tables = dm.get("tables") if isinstance(dm, dict) else None
    if tables:
        for i, t in enumerate(tables):
            iid = add("model", sec, "card", f"model:table:{i}", str(t.get("name") or f"Table {i + 1}"), text=_plain(json.dumps(t, default=str))[:400])
            tabs["model"]["primary"].append(iid)
        for i, l in enumerate(dm.get("links") or []):
            add("model", sec, "card", f"model:link:{i}", f"Link {i + 1}", text=_plain(json.dumps(l, default=str))[:400])
    else:
        add("model", sec, "card", "model:summary", "One table, no links to show", text="This run used a single table, so there are no links between files.")
    sec = section("cleaning", "steps", "Changes made to your data")
    steps = result.get("cleaning_log") or []
    for i, st in enumerate(steps):
        iid = add("cleaning", sec, "card", f"cleaning:step:{i}", st.get("step", f"Step {i + 1}"), text=f"{st.get('step')}: {st.get('detail')} ({st.get('count')} rows or values)")
        tabs["cleaning"]["primary"].append(iid)
    if not steps:
        add("cleaning", sec, "card", "cleaning:none", "No changes were needed", text="No changes were needed to the data.")
    plan = result.get("plan") or {}
    sq, sa, sd, ss = section("plan", "questions", "Questions we set out to answer"), section("plan", "analyses", "What we checked, and why"), section("plan", "definitions", "Definitions used"), section("plan", "setup", "How the data was set up")
    for i, q in enumerate(plan.get("questions") or []):
        add("plan", sq, "card", f"plan:question:{i}", q, text=q)
    for i, a in enumerate(plan.get("analyses") or []):
        iid = add("plan", sa, "card", f"plan:analysis:{i}", str(a.get("tool", "analysis")).replace("_", " ").capitalize(), text=_plain(f"{a.get('tool')} {json.dumps(a.get('params'), default=str)}: {a.get('why', '')}"))
        tabs["plan"]["primary"].append(iid)
    for i, d in enumerate(result.get("applied_definitions") or []):
        add("plan", sd, "card", f"plan:definition:{i}", d.get("question") or f"Definition {i + 1}", text=_plain(f"{d.get('question')} {d.get('answer_label') or d.get('answer')}. {d.get('effect', '')}"))
    add("plan", ss, "card", "plan:setup", "How the data was set up", text=_plain(f"Number we focus on: {plan.get('target')}. Date used: {plan.get('date_col')}. Objective: {plan.get('objective')}"))
    sec = section("powerbi", "pack", "Power BI pack")
    pb = result.get("powerbi") or {}
    add("powerbi", sec, "card", "powerbi:pack", "Power BI pack", text="Files: " + ", ".join(pb.get("files") or []))
    for i, m in enumerate(pb.get("measures") or []):
        iid = add("powerbi", sec, "card", f"powerbi:measure:{i}", str(m).split("=")[0].strip() or f"Measure {i + 1}", text=_plain(m))
        tabs["powerbi"]["primary"].append(iid)
    sec = section("chat", "conversation", "Conversation")
    add("chat", sec, "card", "chat:conversation", "The conversation with Lens", text="The questions asked and the answers given in this run.")


# ------------------------------------------------------------------ changing a chart: which charts Lens can redraw
def changeable(findings: dict, fids: list[str], tool: str | None = None) -> list[str]:
    """What may be changed about a chart: ["chart", "n"] for a ranking of groups, ["chart", "grain"] for a trend over time; [] when the chart cannot be redrawn from a simple query."""
    f = next((findings[i] for i in fids if i in findings), None)
    t = tool or (f or {}).get("tool")
    if t == "group_compare" and (f or {}).get("params", {}).get("group"):
        return ["chart", "n"]
    if t == "trend":
        return ["chart", "grain"]
    return []


# ------------------------------------------------------------------ resolving what each tab shows
def resolve(result: dict, state: dict, cat: dict | None = None) -> dict:
    """{tab: {"order": [section ids], "hidden": [ids hidden by the user], "folded": [ids under 'Show more'], "pinned": [ids], "titles": {id: text}, "short": bool,
    "counts": {...}}} plus "titles_edited" and "chart" overrides. Pure: the same state and result always give the same answer."""
    cat = cat or catalogue(result)
    st = normalise(state)
    items = cat["items"]
    hidden_all = {i for i in st["hidden"] if i in items}
    pinned = [i for i in st["pinned"] if i in items]
    out: dict = {"tabs": {}, "titles": {i: t for i, t in st["titles"].items() if i in items}, "chart": {i: c for i, c in st["chart"].items() if i in items}, "pinned": pinned,
                 "hidden": sorted(hidden_all), "original": is_original(st), "details": st["details"]}
    for tab in TABS:
        tinfo = cat["tabs"][tab]
        mine = [i for i, it in items.items() if it["tab"] == tab]
        if not mine:
            continue
        short = bool(st["short"].get(tab, tab in DEFAULT_SHORT))
        sections = tinfo["sections"]
        order = [s for s in st["order"].get(tab, []) if s in sections] + [s for s in sections if s not in st["order"].get(tab, [])]
        hid = {i for i in mine if i in hidden_all or (items[i].get("section") in hidden_all)}
        folded: set[str] = set()
        chart_ids = [i for i in mine if items[i]["kind"] == "chart" and i not in hid]
        top_n = st["top_n"].get(tab) or (SHORT_INSIGHTS if short and tab not in ("report", "dashboard") and len(tinfo["primary"]) > SHORT_INSIGHTS else None)
        if top_n:
            prim = [i for i in tinfo["primary"] if i not in hid]
            keep = set(prim[:top_n]) | {i for i in prim if i in pinned}
            folded |= {i for i in prim if i not in keep}
            folded |= {i for i in chart_ids if items[i].get("card") in folded}          # a card's chart goes with its card
        if st["only_supporting"].get(tab) or (short and tab in ("report", "dashboard")):
            folded |= {i for i in chart_ids if items[i].get("score", 0) == 0 and i not in pinned}
        if short and tab in ("report", "dashboard"):
            live = [i for i in chart_ids if i not in folded]
            ranked = sorted(live, key=lambda i: (i not in pinned, -items[i].get("score", 0), live.index(i)))
            keep = set(ranked[:MAX_SHORT_CHARTS])
            folded |= {i for i in live if i not in keep}
        if short and tab == "insights":
            folded |= {i for i in mine if items[i].get("section") in ("insights:checked", "insights:appendix")}
        if short and tab == "report":
            folded |= {s for s in ("report:behind",) if s in items}
        folded -= hid
        out["tabs"][tab] = {"order": order, "hidden": sorted(hid), "folded": sorted(folded), "short": short, "top_n": st["top_n"].get(tab), "only_supporting": bool(st["only_supporting"].get(tab)),
                            "items": [i for i in mine if items[i]["kind"] != "section"], "sections": sections}
    return out


# ------------------------------------------------------------------ the commands
KINDS = ("card", "chart", "insight", "action", "strategy", "section")


BULK = 3          # a change that hides or shows more than this many items is previewed first


def _select(cmd: dict, cat: dict, default_tab: str | None = None) -> list[str]:
    from . import viewselect as vs
    sel = cmd.get("select")
    try:
        ids = vs.select(cat, sel, default_tab)
    except ValueError as e:
        raise ViewError(str(e))
    if cmd.get("best") is not None:
        ids = vs.best(cat, ids, cmd["best"])
    if not ids:
        raise ViewError(f"Nothing matches the {vs.what_it_asks(sel)}. " + vs.describe_available(cat, sel, default_tab))
    return ids


def _ids(cmd: dict, cat: dict | None = None) -> list[str]:
    """The ids a command names: a selector ("select": what the items are, resolved by code), or "id" or "ids", or a whole group by "kind" (every chart, every card, ...) optionally within a "tab" and a "section"."""
    if isinstance(cmd.get("select"), dict) and cat is not None:
        return _select(cmd, cat)
    if cmd.get("kind") is not None and cat is not None:
        kind = str(cmd["kind"]).lower().rstrip("s")
        kind = {"graph": "chart", "idea": "strategy", "finding": "card", "insight": "insight", "recommendation": "action"}.get(kind, kind)
        if kind not in KINDS:
            raise ViewError("I can hide or show a whole group of: " + ", ".join(k + "s" for k in KINDS) + ".")
        tab = str(cmd.get("tab") or "").lower() or None
        ids = [i for i, it in cat["items"].items() if it["kind"] == kind and (tab is None or it["tab"] == tab) and (not cmd.get("section") or it.get("section") == cmd["section"])]
        if not ids:
            raise ViewError(f"There are no {kind}s" + (f" on the {TAB_NAMES.get(tab, tab)} tab" if tab else "") + " to change.")
        return ids
    raw = cmd.get("ids") if isinstance(cmd.get("ids"), list) else [cmd.get("id")]
    return [i for i in raw if i is not None]


def _need(cat: dict, ids: list[str]) -> None:
    if not ids:
        raise ViewError("Tell me which item or section to change: name it, or click it first and then say what to do.")
    bad = [str(i)[:40] for i in ids if not isinstance(i, str) or i not in cat["items"]]
    if bad:
        raise ViewError("I can't find " + ("that item" if len(bad) == 1 else "those items") + " in this report, so nothing was changed. Name the item by its title, click it first, or tap one of the examples below.")


def _tab(cmd: dict, cat: dict | None = None) -> str:
    t = str(cmd.get("tab") or "").lower()
    t = {"ideas": "strategy", "ideas to try": "strategy", "strategies": "strategy"}.get(t, t)
    if t not in TABS:
        raise ViewError("Which tab do you mean: Report, Insights, Ideas to try or Dashboard?")
    if cat is not None and not any(i["tab"] == t for i in cat["items"].values()):
        raise ViewError(f"The {TAB_NAMES[t]} tab of this run has nothing that can be changed, so nothing was changed.")
    return t


def _label(cat: dict, i: str) -> str:
    return cat["items"][i]["title"] or i


def _push(prev: dict, new: dict) -> dict:
    new["history"] = (prev.get("history") or [])[-(HISTORY - 1):] + [_core(prev)]
    return new


def apply(result: dict, state: dict, cmd: dict, cat: dict | None = None) -> tuple[dict, str]:
    """(new state, a plain sentence saying what changed). Raises ViewError for anything not on the closed list or not valid for this run. A command that changes nothing returns the same state
    (nothing is added to the history) and says so."""
    cat = cat or catalogue(result)
    prev = normalise(state)
    if not isinstance(cmd, dict) or str(cmd.get("cmd")) not in COMMANDS:
        raise ViewError("That is not a change to the view that I can make. I can hide or show things, keep only the ones you name, switch between the short and full view, show only the top few, keep only the charts that support an action or the summary, redraw a chart another way, move a section, rename a title, or pin an item.")
    name = str(cmd["cmd"])
    new = copy.deepcopy(prev)
    msg = ""
    if name == "undo":
        if not prev["history"]:
            raise ViewError("There is nothing to undo.")
        back = normalise({**prev["history"][-1], "history": prev["history"][:-1]})
        return back, "Undone: the view is back to how it was before the last change."
    if name == "reset":
        if is_original(prev):
            return prev, "The view is already the original."
        new = empty()
        msg = "The view is back to the original: everything is shown, in the original order, with the original titles."
    elif name in ("hide", "show"):
        ids = _ids(cmd, cat)
        _need(cat, ids)
        for i in ids:
            if name == "hide" and i not in new["hidden"]:
                new["hidden"].append(i)
            if name == "show":
                new["hidden"] = [h for h in new["hidden"] if h != i]
        names = ", ".join(f"“{_label(cat, i)}”" for i in ids[:3]) + (f" and {len(ids) - 3} more" if len(ids) > 3 else "")
        if name == "show" and not any(i in prev["hidden"] for i in ids):
            return prev, "Nothing was hidden there, so nothing changed."
        msg = f"{'Hidden' if name == 'hide' else 'Shown again'}: {names}." + (" It stays in your saved report and can be restored from Hidden items." if name == "hide" else "")
    elif name == "keep_only":
        from . import viewselect as vs
        scope, keep = cmd.get("scope"), cmd.get("keep")
        if not isinstance(scope, dict) or not isinstance(keep, list) or not keep:
            raise ViewError("To keep only some items, say which kind of item to look at and which of them to keep.")
        dtab = str(scope.get("tab") or "").lower() or None
        scope_ids = _select({"select": scope}, cat)
        kept: list[str] = []
        for k in keep[:8]:
            if not isinstance(k, dict):
                raise ViewError("Each thing to keep must say what it is.")
            sel = k["select"] if isinstance(k.get("select"), dict) else {kk: vv for kk, vv in k.items() if kk != "best"}
            try:
                ids = [i for i in vs.select(cat, sel, dtab) if i in scope_ids]
            except ValueError as e:
                raise ViewError(str(e))
            ids = vs.best(cat, ids, k.get("best")) if k.get("best") is not None else ids
            if not ids:
                raise ViewError(f"Nothing among the {vs.what_it_asks(scope)} matches the {vs.what_it_asks(sel)}. " + vs.describe_available(cat, {**scope, **{kk: vv for kk, vv in sel.items() if kk in ("type", "kind")}}, dtab))
            kept += [i for i in ids if i not in kept]
        drop = [i for i in scope_ids if i not in kept]
        new["hidden"] = [h for h in new["hidden"] if h not in kept] + [i for i in drop if i not in new["hidden"]]
        msg = f"Kept {len(kept)} and hid {len(drop)} of the {vs.what_it_asks(scope)}. They stay in your saved report and can be restored."
    elif name == "set_mode":
        tab, mode = _tab(cmd, cat), str(cmd.get("mode") or "").lower()
        if mode not in ("full", "short"):
            raise ViewError("Choose the full view or the short view.")
        default = tab in DEFAULT_SHORT
        if mode == "short":
            if default:
                new["short"].pop(tab, None)
            else:
                new["short"][tab] = True
        else:
            if default:
                new["short"][tab] = False
            else:
                new["short"].pop(tab, None)
            new["top_n"].pop(tab, None)
            new["only_supporting"].pop(tab, None)
        msg = f"{TAB_NAMES[tab]} now shows the {mode} view." + (" The rest is under Show more." if mode == "short" else "")
    elif name == "set_details":
        on = cmd.get("on", True) is not False
        new["details"] = on
        msg = "Every folded section is now open (the supporting detail on each page)." if on else "The supporting detail is folded away again."
    elif name == "top_n":
        tab = _tab(cmd, cat)
        n = cmd.get("n")
        if n is None or n == 0:
            new["top_n"].pop(tab, None)
            msg = f"{TAB_NAMES[tab]} shows all its items again."
        else:
            if isinstance(n, bool) or not isinstance(n, (int, float)) or int(n) != n or not 1 <= int(n) <= MAX_N:
                raise ViewError(f"Choose a number from 1 to {MAX_N}.")
            new["top_n"][tab] = int(n)
            msg = f"{TAB_NAMES[tab]} shows only the top {int(n)}. The rest is under Show more."
    elif name == "only_supporting":
        tab = _tab(cmd, cat)
        on = cmd.get("on", True) is not False
        if on:
            new["only_supporting"][tab] = True
        else:
            new["only_supporting"].pop(tab, None)
        msg = (f"{TAB_NAMES[tab]} shows only the charts that support an action or the summary. The others are under Show more." if on else f"{TAB_NAMES[tab]} shows all its charts again.")
    elif name == "chart":
        ids = _ids(cmd, cat)
        _need(cat, ids)
        item = cat["items"][ids[0]]
        if item["kind"] != "chart":
            raise ViewError(f"“{item['title']}” is not a chart, so its type can't be changed.")
        allowed = item.get("changeable") or []
        if not allowed:
            raise ViewError(f"This chart can't be redrawn another way: it comes from a calculation that only has one sensible picture. You can still hide it.")
        ch = dict(new["chart"].get(ids[0], {}))
        if "chart" in cmd:
            if cmd["chart"] not in CHART_TYPES or "chart" not in allowed:
                raise ViewError("Choose a chart type from: " + ", ".join(CHART_TYPES) + ".")
            ch["chart"] = cmd["chart"]
        if "grain" in cmd:
            if cmd["grain"] not in GRAINS or "grain" not in allowed:
                raise ViewError("This chart can't be grouped by that. " + ("Choose from: " + ", ".join(GRAINS) + "." if "grain" in allowed else "It is not a chart over time."))
            ch["grain"] = cmd["grain"]
        if "n" in cmd:
            n = cmd["n"]
            if "n" not in allowed or isinstance(n, bool) or not isinstance(n, (int, float)) or int(n) != n or not 1 <= int(n) <= MAX_N:
                raise ViewError(f"Top N applies to charts of groups, with a number from 1 to {MAX_N}.")
            ch["n"] = int(n)
        if not ch:
            raise ViewError("Say what to change about the chart: its type, time grain or top N.")
        new["chart"][ids[0]] = ch
        msg = f"“{item['title']}” is redrawn by Lens from your data: " + ", ".join(f"{k.replace('n', 'top N') if k == 'n' else k} {str(v).replace('_', ' ')}" for k, v in ch.items()) + ". The figures in the report text are unchanged."
    elif name == "move":
        ids = _ids(cmd, cat)
        _need(cat, ids)
        sid = ids[0]
        it = cat["items"][sid]
        if it["kind"] != "section":
            raise ViewError("Only whole sections can be moved. Sections are the headed parts of a tab.")
        direction = str(cmd.get("dir") or cmd.get("direction") or "").lower()
        if direction not in ("up", "down"):
            raise ViewError("Say whether to move it up or down.")
        tab = it["tab"]
        order = [s for s in new["order"].get(tab, []) if s in cat["tabs"][tab]["sections"]]
        order += [s for s in cat["tabs"][tab]["sections"] if s not in order]
        i = order.index(sid)
        j = i - 1 if direction == "up" else i + 1
        if not 0 <= j < len(order):
            return prev, f"“{it['title']}” is already {'first' if direction == 'up' else 'last'}."
        order[i], order[j] = order[j], order[i]
        new["order"][tab] = order
        msg = f"Moved “{it['title']}” {direction}."
    elif name == "rename":
        ids = _ids(cmd, cat)
        _need(cat, ids)
        title = " ".join(str(cmd.get("title") or "").split())
        if not title:
            new["titles"].pop(ids[0], None)
            msg = f"The original title of “{_label(cat, ids[0])}” is back."
        else:
            if len(title) > MAX_TITLE:
                raise ViewError(f"Please keep the title under {MAX_TITLE} characters.")
            if re.search(r"[<>]", title):
                raise ViewError("A title can contain plain text only.")
            new["titles"][ids[0]] = title
            msg = f"Renamed to “{title}”. It is shown as Edited by you; the report itself is unchanged."
    elif name in ("pin", "unpin"):
        ids = _ids(cmd, cat)
        _need(cat, ids)
        for i in ids:
            if name == "pin" and i not in new["pinned"]:
                new["pinned"].append(i)
            if name == "unpin":
                new["pinned"] = [p for p in new["pinned"] if p != i]
        msg = (f"Pinned “{_label(cat, ids[0])}”: it stays first in its part and is never folded away." if name == "pin" else f"Unpinned “{_label(cat, ids[0])}”.")
    if _core(new) == _core(prev):
        return prev, "That is already how it is shown, so nothing changed."
    return _push(prev, new), msg


def apply_many(result: dict, state: dict, cmds: list, cat: dict | None = None) -> tuple[dict, list[str]]:
    """Applies a short list of commands as ONE undoable step. All are validated first: if one is refused nothing is applied."""
    cat = cat or catalogue(result)
    base = normalise(state)
    cur, msgs = copy.deepcopy(base), []
    for c in cmds[:6]:
        cur, m = apply(result, cur, c, cat)
        msgs.append(m)
    if any(isinstance(c, dict) and c.get("cmd") == "undo" for c in cmds[:6]):
        return cur, msgs
    if cur is not base and _core(cur) != _core(base):
        cur["history"] = (base.get("history") or [])[-(HISTORY - 1):] + [_core(base)]
    return cur, msgs


def available_changes(result: dict, tab: str = "") -> list[dict]:
    """The view changes that can be offered as chips: [{"text", "cmd": [...]}]. Used when a request is not a view change Lens can make, and without any AI."""
    cat = catalogue(result)
    tab = tab if tab in TABS and any(i["tab"] == tab for i in cat["items"].values()) else next((t for t in TABS if any(i["tab"] == t for i in cat["items"].values())), "")
    if not tab:
        return []
    name = TAB_NAMES[tab]
    out = [{"text": f"Show a short {name} view", "cmds": [{"cmd": "set_mode", "tab": tab, "mode": "short"}]},
           {"text": f"Show the full {name} view", "cmds": [{"cmd": "set_mode", "tab": tab, "mode": "full"}]},
           {"text": f"Show only the top 3 on {name}", "cmds": [{"cmd": "top_n", "tab": tab, "n": 3}]}]
    if any(i["kind"] == "chart" for i in cat["items"].values() if i["tab"] == tab):
        out.append({"text": f"Keep only charts that support an action or the summary on {name}", "cmds": [{"cmd": "only_supporting", "tab": tab, "on": True}]})
    out.append({"text": "Reset the view", "cmds": [{"cmd": "reset"}]})
    return out


# ------------------------------------------------------------------ one item's own words, for "Ask Lens about this"
def item_context(result: dict, item_id: str, cat: dict | None = None) -> dict | None:
    """{"id", "title", "kind", "text", "fact_ids"}: what the AI may be shown about the focused item, taken from the saved report only. None when the id does not exist."""
    cat = cat or catalogue(result)
    it = cat["items"].get(item_id)
    if not it:
        return None
    rv = result.get("report_view") or {}
    parts: list[str] = []
    cards = {c["id"]: c for c in (rv.get("evidence") or []) + (rv.get("appendix") or [])}
    fids = list(it.get("finding_ids") or [])
    if it["kind"] == "card" and it["id"].rsplit(":", 1)[-1] in cards:
        c = cards[it["id"].rsplit(":", 1)[-1]]
        conf = c.get("confidence") or {}
        parts = [c.get("headline", ""), c.get("observation", ""), "Why it matters: " + str(c.get("why_it_matters") or ""), (f"How much to trust it: {conf.get('label')}. " + " ".join(conf.get("reasons") or [])) if conf else "", " ".join(c.get("checks") or [])]
    elif it["kind"] == "chart":
        c = next((x for x in (rv.get("page") or {}).get("charts") or [] if (x.get("path") or "").endswith("/" + it["id"].rsplit(":", 1)[-1] + ".png")), None)
        card = cards.get((it.get("card") or "").rsplit(":", 1)[-1]) if it.get("card") else next((x for x in cards.values() if x.get("chart") == it.get("chart")), None)
        f = next((x for x in result.get("findings") or [] if x.get("id") in fids), None)
        parts = [it["title"], (c or {}).get("caption", ""), " ".join((c or {}).get("details") or []), (card or {}).get("observation", ""), (f or {}).get("text", "")]
    elif it["kind"] == "strategy":
        s = next((x for x in (rv.get("strategies") or {}).get("strategies") or result.get("strategies") or [] if x.get("id") == it["id"].split(":", 1)[1]), None) or {}
        parts = [s.get("title", ""), s.get("objective", ""), s.get("rationale", ""), "Actions: " + "; ".join(s.get("actions") or []), f"Impact {s.get('impact')}, confidence {s.get('confidence')}, effort {s.get('effort')}."]
    else:
        parts = [it.get("text") or it["title"]]
        if it["kind"] == "section" and it["tab"] == "report" and it["id"].endswith(":behind"):
            parts = ["Behind the numbers: how the data was set up, what was cleaned and how things were calculated."]
    text = _plain(" ".join(p for p in parts if p))[:1200]
    return {"id": item_id, "title": it["title"], "kind": it["kind"], "tab": it["tab"], "text": text, "fact_ids": fids}


# ------------------------------------------------------------------ the slim bar: shown only when something is hidden or changed
def bar(result: dict, state: dict, cat: dict | None = None) -> dict:
    """{"show": bool, "text": "2 items hidden · 1 title edited", "hidden": n}. `show` is False exactly when the view is back to its default (so the bar disappears)."""
    st = normalise(state)
    cat = cat or catalogue(result)
    hidden = [i for i in st["hidden"] if i in cat["items"]]
    parts = []
    if hidden:
        parts.append(f"{len(hidden)} item{'s' if len(hidden) != 1 else ''} hidden")
    full = [t for t, v in st["short"].items() if v is False and t in DEFAULT_SHORT]
    short = [t for t, v in st["short"].items() if v is True and t not in DEFAULT_SHORT]
    if full:
        parts.append("showing everything on " + ", ".join(TAB_NAMES[t] for t in full))
    if short:
        parts.append("short view on " + ", ".join(TAB_NAMES[t] for t in short))
    if st["top_n"]:
        parts.append("top " + "/".join(str(n) for n in st["top_n"].values()) + " only")
    if st["only_supporting"]:
        parts.append("only supporting charts")
    if st["titles"]:
        parts.append(f"{len(st['titles'])} title{'s' if len(st['titles']) != 1 else ''} edited")
    if st["pinned"]:
        parts.append(f"{len(st['pinned'])} pinned")
    if st["order"]:
        parts.append("sections moved")
    if st["details"]:
        parts.append("all details open")
    if st["chart"]:
        parts.append(f"{len(st['chart'])} chart{'s' if len(st['chart']) != 1 else ''} redrawn")
    return {"show": not is_original(st), "text": " · ".join(parts) or "view changed", "hidden": len(hidden)}


# ------------------------------------------------------------------ a bulk change is previewed before it is applied
def _names(cat: dict, ids: list[str], limit: int = 8) -> str:
    t = [f"“{cat['items'][i]['title']}”" for i in ids[:limit]]
    return ", ".join(t) + (f" and {len(ids) - limit} more" if len(ids) > limit else "")


def preview(result: dict, state: dict, cmds: list, cat: dict | None = None) -> dict | None:
    """None when the change touches no more than BULK items. Otherwise {"text", "explicit", "count"}: what would be kept, hidden and shown, in plain words, and the same change written as explicit ids
    (applying that is exactly what was previewed, whatever the AI said). Raises ViewError when the commands are not valid."""
    cat = cat or catalogue(result)
    if any(isinstance(c, dict) and c.get("cmd") in ("undo", "reset") for c in cmds):
        return None                      # going back is never previewed: it is the way out
    before = normalise(state)
    new, _ = apply_many(result, state, cmds, cat)
    hid = [i for i in new["hidden"] if i not in before["hidden"]]
    shown = [i for i in before["hidden"] if i not in new["hidden"]]
    if len(hid) + len(shown) <= BULK:
        return None
    kept: list[str] = []
    for c in cmds:
        if isinstance(c, dict) and c.get("cmd") == "keep_only":
            scope_ids = _select({"select": c["scope"]}, cat)
            kept += [i for i in scope_ids if i not in new["hidden"] and i not in kept]
    parts = []
    if kept:
        parts.append("keep: " + _names(cat, kept))
    if hid:
        parts.append("hide: " + _names(cat, hid))
    if shown:
        parts.append("show: " + _names(cat, shown))
    explicit = ([{"cmd": "hide", "ids": hid}] if hid else []) + ([{"cmd": "show", "ids": shown}] if shown else []) + [c for c in cmds if isinstance(c, dict) and c.get("cmd") not in ("hide", "show", "keep_only")]
    return {"text": "I will " + " and ".join(parts) + f". That changes {len(hid) + len(shown)} items, and Undo puts everything back.", "explicit": explicit, "count": len(hid) + len(shown), "hide": hid, "show": shown, "keep": kept}
