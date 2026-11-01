"""What a summary covers is decided by what the user names, never by the tab that happens to be open.
No scope named: the whole report, and the answer says so ("Summary of the whole report"). A tab, a section or a topic named: only that. The scope is found by matching the user's words against the
tab names and vocabulary, and against the titles, measures, dimensions and texts of the report's own items; nothing here knows a topic."""
from __future__ import annotations

import re

from ... import viewedit as ve
from ... import viewselect as vs
from .. import records, viewchat

WHOLE = {"report", "whole report", "everything", "all", "it", "this", "the report", "analysis", "findings", "results", "data", "the whole report", "all of it", "the lot", "overview", "summary"}
FACT_TABS = ("insights", "dashboard")        # facts with figures: the AI may word them, every figure is checked
LABELS = {"strategy": "the ideas to try"}


def clean(scope: str) -> str:
    s = " ".join(str(scope or "").lower().split())
    s = re.sub(r"\b(?:tab|page|section|part|screen)\b", "", s)
    s = re.sub(r"^(?:the|my|our|of|for|on)\s+", "", s.strip()).strip(" .")
    return "" if s in WHOLE or not s else s


def _tab_for(text: str) -> str | None:
    ex = viewchat._exact(text)
    for t, vocab in viewchat.TAB_VOCAB.items():
        if ex and ex <= {w.rstrip("s") for w in vocab + ve.TAB_NAMES[t].lower().split()}:
            return t
    return None


def resolve(env, scope: str) -> dict | None:
    """{"label", "ids" (fact ids), "lines" (computed lines for tabs that are not made of facts) or None}, or None when nothing in the report matches what was named."""
    s = clean(scope)
    if not s:
        return {"label": "the whole report", "ids": None, "lines": None, "whole": True}
    cat = env.cat
    t = _tab_for(s)
    if t == "report":
        return {"label": "the whole report", "ids": None, "lines": None, "whole": True}
    if t:
        items = [i for i in cat["items"].values() if i["tab"] == t and i["kind"] != "section"]
        label = LABELS.get(t) or "the " + ve.TAB_NAMES[t].lower()
        ids = {f for i in items for f in i.get("finding_ids") or []}
        if t in FACT_TABS:
            return {"label": label, "ids": ids, "lines": None, "whole": False}
        if t == "strategy":
            lines = [{"text": f"{x['title'].rstrip('.')}. {records._plain(x.get('objective'))}".strip(), "ids": list(x.get("evidence") or [])} for x in records.strategy_items(env.result) if x.get("kind", "data") == "data"]
            return {"label": label, "ids": ids, "lines": lines, "whole": False}
        return {"label": label, "ids": ids, "lines": [{"text": records._cut(i.get("text") or i["title"], 220), "ids": list(i.get("finding_ids") or [])} for i in items], "whole": False}
    w = vs.words(s)
    hits = [i for i in cat["items"].values() if i["kind"] != "section" and w and w <= vs.words(" ".join([i["title"], i.get("text") or "", " ".join(i.get("measure") or []), " ".join(i.get("dimension") or [])]))]
    if not hits:
        return None
    ids = {f for i in hits for f in i.get("finding_ids") or []}
    return {"label": s, "ids": ids, "lines": None, "whole": False}


def scoped_result(result: dict, ids: set) -> dict:
    """The saved result with only what belongs to `ids` in the places the summary reads from; nothing in it is changed or recalculated."""
    r = dict(result)
    rv = dict(result.get("report_view") or {})
    rv["headlines"] = [h for h in rv.get("headlines") or [] if h.get("id") in ids]
    for k in ("evidence", "appendix"):
        rv[k] = [e for e in rv.get(k) or [] if set(e.get("members") or [e.get("id")]) & ids]
    rv["actions"] = [a for a in rv.get("actions") or [] if set(a.get("evidence") or []) & ids]
    sf = dict(result.get("summary_facts") or {})
    sf["rankings"] = [x for x in sf.get("rankings") or [] if x.get("id") in ids]
    r["report_view"], r["summary_facts"] = rv, sf
    return r
