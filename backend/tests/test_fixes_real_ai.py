"""Fixes from real-AI testing: selector-based view changes and their bulk preview, plain-language messages, suggestions that fit the request, the scope of a summary, claims of highest and lowest,
and the number of suggestions. Fake AI only; every example request is in this file, none in the code."""
import ast
import copy
import json
import os
import re
from pathlib import Path

os.environ["LLM_PROVIDER"] = "none"

import pytest

from app import viewedit as ve
from app import viewselect as vs
from app.lens import chat, store
from app.lens.agent import planner, suggest, superlatives
from tests.test_agent_loop import P
from tests.test_lens import FakeAI, make_run
from tests.test_lens_stage2a import REPORT, UND, shop_df
from tests.test_partb_view import run  # noqa: F401  (a copy of the sample run)

APP = Path(__file__).resolve().parents[1] / "app"


# ------------------------------------------------------------------ a run with charts about different measures
def chart_run(monkeypatch):
    monkeypatch.setenv("CHATBOT", "on")
    chat._DAILY["until"] = 0.0
    measures = [("net profit", "category"), ("net profit", "region"), ("net profit", "tier"), ("amount", "category"), ("amount", "region"), ("amount", "tier"), ("discount", "region")]
    findings, cards = [], []
    for i, (m, d) in enumerate(measures, 1):
        findings.append({"id": f"g{i}", "tool": "group_compare", "title": f"{m} by {d}", "text": f"{m} by {d}", "params": {"target": m, "group": d}, "chart": f"charts/g{i}.png", "significant": True,
                         "stats": {"best": "A", "worst": "B"}, "insight": {"headline": f"{m} by {d}", "score": 10 - i, "confidence": {"level": "high"}}})
        cards.append({"id": f"g{i}", "members": [f"g{i}"], "headline": f"{m.capitalize()} differs by {d}", "title": f"{m} by {d}", "observation": "obs", "chart": f"charts/g{i}.png", "tier": 2, "confidence": {"label": "High", "level": "high"}})
    rv = {**REPORT["report_view"], "evidence": cards, "appendix": [], "actions": [{"text": "Act on net profit by category", "evidence": ["g1"]}, {"text": "Act on amount by region", "evidence": ["g5"]}],
          "gallery": {"key": [], "more": []}}
    und = {**UND, "columns": {**UND["columns"], "net profit": {"role": "measure", "meaning": "profit", "display_name": "Net profit", "measure_kind": "money"}, "discount": {"role": "measure", "meaning": "d", "display_name": "Discount"}}}
    df = shop_df()
    return make_run(df, und, {**REPORT, "report_view": rv, "findings": findings})


def charts_in(res, tab="insights"):
    return [i for i, it in ve.catalogue(res)["items"].items() if it["tab"] == tab and it["kind"] == "chart"]


def visible(rid_wd, res, tab="insights"):
    rid, wd = rid_wd
    hid = set(ve.normalise(store.get_view_state(wd))["hidden"])
    return [i for i in charts_in(res, tab) if i not in hid]


# ------------------------------------------------------------------ 1. selectors
def test_every_item_carries_type_measure_dimension_rank_and_cited(monkeypatch):
    rid, wd, res = chart_run(monkeypatch)
    cat = ve.catalogue(res)
    it = cat["items"]["insights:chart:g1"]
    assert it["type"] == "chart" and it["measure"] == ["Net profit"] and it["dimension"] == ["Category"] and it["rank"] == 1 and it["cited"] is True and it["title"]
    assert cat["items"]["insights:chart:g2"]["cited"] is False and cat["items"]["insights:chart:g5"]["cited"] is True
    assert {i["type"] for i in cat["items"].values()} >= {"chart", "card", "action", "section", "insight"}


def test_selectors_resolve_by_type_measure_dimension_title_position_rank_and_cited(monkeypatch):
    rid, wd, res = chart_run(monkeypatch)
    cat = ve.catalogue(res)
    pick = lambda **s: vs.select(cat, {"tab": "insights", **s})
    assert len(pick(type="graphs", measure="net profit")) == 3 and len(pick(type="charts", measure="amount")) == 3 and len(pick(type="chart")) == 7
    assert pick(type="chart", dimension="region", measure="amount") == ["insights:chart:g5"]
    assert pick(type="chart", position="last") == ["insights:chart:g7"] and pick(type="chart", position=2) == ["insights:chart:g2"]
    assert pick(type="chart", rank={"lte": 2}) == ["insights:chart:g1", "insights:chart:g2"] and pick(type="chart", rank=3) == ["insights:chart:g3"]
    assert pick(type="chart", cited=True) == ["insights:chart:g1", "insights:chart:g5"]
    assert pick(type="chart", title="discount") == ["insights:chart:g7"]
    assert vs.best(cat, pick(type="chart", measure="net profit"), 1) == ["insights:chart:g1"]             # a chart an action cites comes before one nothing cites
    with pytest.raises(ValueError):
        vs.select(cat, {"type": "gizmo"})


def test_keep_one_graph_about_one_measure_and_the_others_about_another(monkeypatch):
    """"keep only one graph about net profit and the others by amount": a selector command, not a list of ids."""
    rid, wd, res = chart_run(monkeypatch)
    cmd = {"cmd": "keep_only", "scope": {"type": "chart", "tab": "insights"}, "keep": [{"select": {"measure": "net profit"}, "best": 1}, {"select": {"measure": "amount"}}]}
    new, msgs = ve.apply(res, ve.empty(), cmd)
    shown = [i for i in charts_in(res) if i not in new["hidden"]]
    assert shown == ["insights:chart:g1", "insights:chart:g4", "insights:chart:g5", "insights:chart:g6"] and len(new["hidden"]) == 3
    assert "Kept 4 and hid 3" in msgs
    again, m2 = ve.apply(res, new, cmd)
    assert ve._core(again) == ve._core(new) and "already" in m2                                         # idempotent
    # through the planner, with a fake AI, preview first (3 charts are hidden: not more than 3, so it applies at once)
    ai = FakeAI(P(("view_edit", {"commands": [cmd]})))
    a = chat.ask(wd, res, question="keep only one graph about net profit and the others by amount", llm=ai, allow_ai=True, tab="Insights")["answer"]
    assert ai.calls == 1 and a["view"]["changed"] and set(visible((rid, wd), res)) == set(shown)


def test_a_selector_that_matches_nothing_says_what_exists(monkeypatch):
    rid, wd, res = chart_run(monkeypatch)
    with pytest.raises(ve.ViewError) as e:
        ve.apply(res, ve.empty(), {"cmd": "hide", "select": {"type": "chart", "measure": "returns"}})
    msg = str(e.value)
    assert "Nothing matches" in msg and "Net profit" in msg and "Amount" in msg and "Category" in msg
    with pytest.raises(ve.ViewError) as e:
        ve.apply(res, ve.empty(), {"cmd": "keep_only", "scope": {"type": "chart", "tab": "insights"}, "keep": [{"select": {"measure": "returns"}}]})
    assert "Nothing among the charts" in str(e.value)
    a = chat.ask(wd, res, question="hide the returns charts", llm=FakeAI(P(("view_edit", {"commands": [{"cmd": "hide", "select": {"type": "chart", "measure": "returns"}}]}))), allow_ai=True)["answer"]
    assert a["label"] == "Not changed" and "Net profit" in a["text"]


def test_a_bulk_change_is_previewed_applied_with_the_chip_and_undone(monkeypatch):
    rid, wd, res = chart_run(monkeypatch)
    cmd = {"cmd": "hide", "select": {"type": "chart", "tab": "insights"}}                # 7 charts
    ai = FakeAI(P(("view_edit", {"commands": [cmd]})))
    t = chat.ask(wd, res, question="drop all the pictures", llm=ai, allow_ai=True, tab="Insights")
    a = t["answer"]
    assert a["view"]["preview"] and not a["view"]["changed"] and a["label"] == "Check before I change this"
    assert a["text"].startswith("I will hide:") and "7 items" in a["text"] and ve.normalise(store.get_view_state(wd))["hidden"] == []       # nothing was changed yet
    apply_chip = a["chips"][0]
    assert apply_chip["text"] == "Apply this change"
    done = chat.ask(wd, res, chip=apply_chip["id"], llm=None, allow_ai=False, tab="Insights")["answer"]
    assert done["view"]["changed"] and len(ve.normalise(store.get_view_state(wd))["hidden"]) == 7
    chat.ask(wd, res, question="undo", llm=None, allow_ai=False)
    assert ve.normalise(store.get_view_state(wd))["hidden"] == []                           # one Undo puts everything back
    small = chat.ask(wd, res, question="x", llm=FakeAI(P(("view_edit", {"commands": [{"cmd": "hide", "select": {"type": "chart", "measure": "amount", "dimension": "category"}}]}))), allow_ai=True)["answer"]
    assert small["view"]["changed"] and not small["view"].get("preview")                       # 3 or fewer: applied at once


def test_a_keep_only_preview_names_what_is_kept_and_what_is_hidden(monkeypatch):
    rid, wd, res = chart_run(monkeypatch)
    cmd = {"cmd": "keep_only", "scope": {"type": "chart", "tab": "insights"}, "keep": [{"select": {"measure": "discount"}}]}
    pv = ve.preview(res, ve.empty(), [cmd])
    assert pv["text"].startswith("I will keep:") and "and hide:" in pv["text"] and pv["count"] == 6 and len(pv["explicit"]) == 1 and pv["explicit"][0]["cmd"] == "hide"


# ------------------------------------------------------------------ 2. plain language
FORBIDDEN = re.compile(r"\b(?:view_edit|data_query|custom_analysis|report_facts|chart_edit|brief_manage|premise_check|recheck_chart|general_knowledge|registry|schema|json|commands?|plans?)\b|\b[a-z]+(?:_[a-z]+)+\b", re.I)
SINKS = {"ViewError", "ToolResult", "_plain", "HTTPException", "ExportError", "_a", "_unmet_answer", "record_how", "_base", "preview_answer"}


def _text(node) -> str:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(v.value if isinstance(v, ast.Constant) else "X" for v in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mod)):
        return _text(node.left) + _text(node.right)
    return ""


def test_no_user_facing_string_in_the_code_names_an_internal_thing():
    """Every string passed to a place that shows a message (a refusal, an error, a record of how an answer was made) is scanned."""
    bad = []
    skip = {"graph.py", "planner.py", "sqlguard.py"}                       # the AI's own prompt and the messages given back to the AI
    for p in APP.rglob("*.py"):
        if p.name in skip or "__pycache__" in str(p):
            continue
        for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Call):
                name = n.func.id if isinstance(n.func, ast.Name) else n.func.attr if isinstance(n.func, ast.Attribute) else ""
                if name in SINKS:
                    for a in list(n.args[1:] if name in ("_plain", "_base") else n.args) + [k.value for k in n.keywords if k.arg in ("error", "summary", "detail")]:
                        t = _text(a)
                        if t and FORBIDDEN.search(t) and not re.fullmatch(r"[a-z_]+", t):             # a bare word such as a status ("no_fit") is not shown
                            bad.append(f"{p.name}:{n.lineno}: {t[:100]}")
    assert not bad, bad


def _all_text(a: dict) -> str:
    parts = [str(a.get("text", "")), str(a.get("label", ""))] + [str(x) for x in a.get("lines") or []] + [str(x) for x in (a.get("how") or {}).get("lines") or []]
    return "\n".join(parts)


def test_every_failure_on_the_way_is_said_in_plain_words(run, monkeypatch):
    rid, wd, res = run
    cases = [
        (P(("no_such_tool", {})), "do something odd"), (P(("explain", {"topic": "nonsense"})), "explain"), (P(*[("explain", {"topic": "tab"})] * 6), "six things"),
        (P(("chart_edit", {"target": "answer", "chart": "line"})), "make it a line"), (P(("view_edit", {"commands": [{"cmd": "hide", "id": "zzz:zzz"}]})), "hide zzz"),
        (P(("view_edit", {"commands": [{"cmd": "hide", "select": {"type": "chart", "measure": "nothing here"}}]})), "hide the nothing charts"),
        (P(("view_edit", {"commands": [{"cmd": "teleport"}]})), "teleport it"), (P(("brief_manage", {"op": "open", "id": "nothing"})), "open that brief"),
        (P(("export", {"what": "all_briefs"})), "download my briefs"), (P(("glossary", {"op": "add"})), "remember this"), (P(("summarise", {"scope": "zebras"})), "summarise the zebras"),
        (P(("custom_analysis", {"sql": "DROP TABLE data", "description": "x"})), "run my own query"), (P(("clarify", {"question": "Which?", "choices": [{"label": "a"}, {"label": "b"}]})), "which one"),
        ({"plan": [], "unsupported": "that is not something this report can do"}, "email this to my boss"), ({"plan": [], "unsupported": ""}, "hide the sparkles"),
        ({"plan": "garbage"}, "garbage in"), (RuntimeError("boom"), "an error from the model"), (Exception("Error code: 429 rate limit reached"), "busy"), (Exception("401 invalid api key auth"), "no key")]
    for reply, q in cases:
        a = chat.ask(wd, res, question=q, llm=FakeAI(reply), allow_ai=True, tab="Insights")["answer"]
        text = _all_text(a)
        hit = [m.group(0) for m in FORBIDDEN.finditer(text) if m.group(0).lower() not in {c.lower() for c in ("order_date", "customer_id")}]
        assert not hit, (q, hit, text[:300])
    # without a model
    for q in ("hide the nonexistent thing", "write me a poem", "export it", "summarise the zebras", "make it pink"):
        a = chat.ask(wd, res, question=q, llm=None, allow_ai=False, tab="Report")["answer"]
        assert not FORBIDDEN.search(_all_text(a)), (q, _all_text(a)[:300])


# ------------------------------------------------------------------ 3. suggestions that fit the request
def test_refusals_suggest_things_of_the_same_kind(run):
    rid, wd, res = run
    ai = lambda: FakeAI({"plan": [], "unsupported": "cannot do that"})
    view = chat.ask(wd, res, question="tidy up and simplify this page for me", llm=ai(), allow_ai=True, tab="Insights")["answer"]
    data = chat.ask(wd, res, question="what is the average revenue per segment in march", llm=ai(), allow_ai=True, tab="Insights")["answer"]
    other = chat.ask(wd, res, question="please book me a flight to the moon", llm=ai(), allow_ai=True, tab="Insights")["answer"]
    info = store.load(wd)["chips"]
    kinds = lambda a: {("view" if (info.get(c["id"], {}).get("intent") or {}).get("kind") == "view_cmd" else "data" if info.get(c["id"], {}).get("query") else "report") for c in a["chips"]}
    assert 1 <= len(view["chips"]) <= 2 and kinds(view) == {"view"} and "changes I can make" in view["text"]
    assert 1 <= len(data["chips"]) <= 2 and kinds(data) == {"data"} and "questions I can answer" in data["text"]
    assert kinds(other) == {"report"} and "things you could ask" in other["text"]
    assert suggest.request_kind("hide the first chart", None) == "view" and suggest.request_kind("top 5 cities by amount", None) == "data" and suggest.request_kind("send an email", None) == "other"


# ------------------------------------------------------------------ 4. the scope of a summary
def test_a_summary_covers_the_whole_report_unless_the_user_names_a_part(run):
    rid, wd, res = run
    whole = chat.ask(wd, res, question="summarise it", llm=None, allow_ai=False, tab="Ideas to try")["answer"]            # the open tab is Ideas to try: it must not limit the summary
    assert whole["text"] == "Summary of the whole report." and whole["scope"] == "the whole report"
    assert any("Enterprise" in x for x in whole["lines"]) and not any("Put more behind what works" in x and "Grow overall" in x for x in whole["lines"][:0])
    named = chat.ask(wd, res, question="summarise the ideas to try", llm=None, allow_ai=False, tab="Report")["answer"]
    assert named["text"] == "Summary of the ideas to try." and named["lines"] and all("Put more behind what works" in x or "Close the most important" in x or "Make this repeatable" in x for x in named["lines"])
    assert named["lines"] != whole["lines"]
    both = chat.ask(wd, res, question="summarise the report", llm=None, allow_ai=False, tab="Ideas to try")["answer"]
    assert both["scope"] == "the whole report"
    ai = FakeAI(P(("summarise", {"length": "short", "scope": "what we cleaned"})))
    cleaned = chat.ask(wd, res, question="a summary of what we cleaned please", llm=ai, allow_ai=True, tab="Report")["answer"]
    assert cleaned["scope"] == "what we cleaned" and len(cleaned["lines"]) >= 1 and "Convert" in " ".join(cleaned["lines"])
    ai = FakeAI(P(("summarise", {"length": "standard"})))                                         # the AI names no scope while the Ideas tab is open
    a = chat.ask(wd, res, question="give me a summary", llm=ai, allow_ai=True, tab="Ideas to try")["answer"]
    assert a["scope"] == "the whole report" and "CURRENT TAB: Ideas to try" in ai.prompts[0] and "never a scope" in planner.system_prompt()
    none = chat.ask(wd, res, question="summarise the zebras", llm=None, allow_ai=False, tab="Report")["answer"]
    assert none["status"] == "no_fit" and "nothing about “zebras”" in none["text"]


# ------------------------------------------------------------------ 5. claims of highest and lowest
RANKS = {"summary_facts": {"rankings": [{"id": "r1", "group": "category", "measure": "amount", "top": "Clothing", "bottom": "Home"}, {"id": "r2", "group": "region", "measure": "amount", "top": "South", "bottom": "North"}]}}


def L(text, *ids):
    return {"text": text, "ids": list(ids)}


def test_a_wrong_superlative_is_dropped_a_missing_level_is_added_and_a_conflict_loses_the_later_line():
    kept, dropped = superlatives.check([L("Home is the highest on total amount at 300,000 [r1]", "r1")], RANKS)
    assert kept == [] and "is highest" in dropped[0] and "category" in dropped[0]
    kept, dropped = superlatives.check([L("Clothing brings in the most amount: 40.0%.", "r1")], RANKS)
    assert kept[0]["text"] == "Clothing category brings in the most amount: 40.0%." and not dropped                    # the level is named; the figure is untouched
    kept, dropped = superlatives.check([L("Clothing category brings in the most amount.", "r1"), L("South region brings in the most amount.", "r2")], RANKS)
    assert len(kept) == 2 and not dropped                                                                             # different levels, each named: fine
    conflicting = RANKS | {"summary_facts": {"rankings": RANKS["summary_facts"]["rankings"] + [{"id": "r3", "group": "category", "measure": "amount", "top": "Garden", "bottom": "Home"}]}}
    kept, dropped = superlatives.check([L("Clothing category has the highest amount.", "r1"), L("Garden category has the highest amount.", "r3")], conflicting)
    assert [k["text"] for k in kept] == ["Clothing category has the highest amount."] and "conflicts with an earlier line" in dropped[0]
    kept, dropped = superlatives.check([L("Revenue rose 12%.", "r1")], RANKS)
    assert kept and not dropped                                                                                       # no claim: untouched


def test_a_conflicting_pair_in_an_ai_summary_is_cut_by_the_existing_fact_machinery(monkeypatch):
    monkeypatch.setenv("CHATBOT", "on")
    rid, wd, res = make_run(shop_df(), UND, REPORT)
    lines = {"lines": ["Clothing brings in the most: 40.0% of the total amount [r1].", "Home category is the highest on total amount at 300,000 [r1].", "The return rate trend is up 12% [f1]."]}
    a = chat.ask(wd, res, question="a summary of the whole thing, please", llm=FakeAI({"plan": [{"tool": "summarise", "args": {"length": "standard"}}]}, lines), allow_ai=True)["answer"]
    txt = " ".join(a["lines"])
    assert "Clothing category brings in the most" in txt and "Home category is the highest" not in txt and "return rate trend is up 12%" in txt
    assert any("did not pass" in n for n in a["how"]["notes"])


# ------------------------------------------------------------------ 6. fewer suggestions
def test_the_opening_chips_are_few_and_chosen_for_the_tab(run):
    rid, wd, res = run
    for tab in ("Report", "Insights", "Ideas to try", "What we cleaned", "Dashboard"):
        ai_on = chat.info(wd, res, False, True, tab, True)
        ai_off = chat.info(wd, res, False, True, tab, False)
        assert len(ai_on["chips"]) <= 3 and len(ai_off["chips"]) <= 4 and ai_on["more"], tab
        kinds = lambda i: [("view" if c["id"] in {x["id"] for x in ai_on["chips"] + ai_off["chips"]} and "i_" in c["id"] and ("Show only" in c["text"] or "Hide" in c["text"] or "shorter" in c["text"]) else "other") for c in i["chips"]]
        assert any(c["text"].startswith(("Show only", "Hide", "Make this tab")) for c in ai_on["chips"]) and any("Summarise" in c["text"] for c in ai_on["chips"])
        assert any(c["id"].startswith("c_") for c in ai_on["chips"])
    assert chat.info(wd, res, False, True, "Ideas to try", True)["chips"][-1]["text"] == "Summarise the ideas to try"
    # the chips are different for different tabs
    assert [c["text"] for c in chat.info(wd, res, False, True, "Report", True)["chips"]] != [c["text"] for c in chat.info(wd, res, False, True, "What we cleaned", True)["chips"]]


def test_after_an_answer_there_are_at_most_three_chips_and_without_an_ai_at_most_four(run):
    rid, wd, res = run
    a = chat.ask(wd, res, question="which segment has the highest total revenue?", llm=None, allow_ai=False, tab="Report")["answer"]
    assert a["status"] == "answered" and len(a["chips"]) <= 3
    for q in ("what is the weather", "tell me a joke about spreadsheets"):
        u = chat.ask(wd, res, question=q, llm=None, allow_ai=False, tab="Insights")["answer"]
        assert u["status"] == "unavailable" and 2 <= len(u["chips"]) <= 4 and u["more"] and not {c["id"] for c in u["chips"]} & {c["id"] for c in u["more"]}
    ai = FakeAI({"plan": [{"tool": "data_query", "args": {"query": {"op": "top", "measure": "revenue", "agg": "sum", "group_by": ["Segment"], "n": 3, "direction": "top"}}}]})
    b = chat.ask(wd, res, question="top segments", llm=ai, allow_ai=True, tab="Report")["answer"]
    assert len(b["chips"]) <= 3
    # a typed request that reads like a view change means the tab that is open now, not the tab a chip was made for
    chat.info(wd, res, False, True, "Your data", False)
    t = chat.ask(wd, res, question="Make this tab shorter", llm=None, allow_ai=False, tab="What we cleaned")["answer"]
    assert t["view"]["changed"] and "What we cleaned" in t["text"]
