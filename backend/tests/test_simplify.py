"""The calm interface, backend side: the default short view, the slim bar's state, chat edits (hide, show, short) on every tab by the same mechanism, and brief rename and edit. The browser side is test_ui_simplify.py."""
import json
from pathlib import Path
import os

os.environ["LLM_PROVIDER"] = "none"

import pytest
from fastapi.testclient import TestClient

from app import viewedit as ve
from app.lens import briefs as bf
from app.lens import chat, store, viewchat
from app.main import app
from tests.test_agent_loop import P
from tests.test_lens import FakeAI
from tests.test_partb_view import core, post, run, view  # noqa: F401

client = TestClient(app)


def test_every_tab_opens_showing_everything(run):
    rid, wd, res = run
    r = view(rid)["resolved"]
    for t in ("report", "insights", "dashboard", "strategy", "data", "cleaning", "plan", "chat"):
        assert r["tabs"][t]["short"] is False and not r["tabs"][t]["folded"], t
    assert r["original"] and view(rid)["bar"]["show"] is False


def test_show_all_and_show_less_are_ordinary_changes_that_reset_removes(run):
    rid, wd, res = run
    short = post(rid, command={"cmd": "set_mode", "tab": "insights", "mode": "short"}).json()
    assert short["resolved"]["tabs"]["insights"]["short"] is True and short["resolved"]["tabs"]["insights"]["folded"]
    assert short["bar"]["show"] and "short view on Insights" in short["bar"]["text"]
    back = post(rid, command={"cmd": "set_mode", "tab": "insights", "mode": "full"}).json()
    assert back["resolved"]["original"] and back["bar"]["show"] is False                    # full is the default: nothing to report
    assert "already" in post(rid, command={"cmd": "set_mode", "tab": "insights", "mode": "full"}).json()["message"]


def test_the_slim_bar_appears_for_a_hidden_item_and_disappears_with_undo_and_with_reset(run):
    rid, wd, res = run
    fid = res["report_view"]["evidence"][0]["id"]                                           # an id taken from the run itself
    a = post(rid, commands=[{"cmd": "hide", "id": f"insights:card:{fid}"}, {"cmd": "hide", "id": "insights:appendix"}]).json()
    assert a["bar"] == {"show": True, "text": "2 items hidden", "hidden": 2}
    b = post(rid, command={"cmd": "rename", "id": "strategy:s1", "title": "Mine"}).json()
    assert b["bar"]["text"] == "2 items hidden · 1 title edited"
    c = post(rid, command={"cmd": "undo"}).json()
    assert c["bar"]["text"] == "2 items hidden"
    d = post(rid, command={"cmd": "undo"}).json()
    assert d["bar"]["show"] is False and d["resolved"]["original"]
    post(rid, command={"cmd": "hide", "id": "insights:appendix"})
    assert post(rid, command={"cmd": "reset"}).json()["bar"]["show"] is False


def test_hide_and_show_by_name_without_an_ai():
    cat = ve.catalogue(json.loads((Path(__file__).resolve().parent / "lens_eval" / "run" / "result.json").read_text(encoding="utf-8")))
    assert viewchat.toggle_cmds("Hide the cleaning log", cat, []) == [{"cmd": "hide", "id": "cleaning:steps"}]
    assert viewchat.toggle_cmds("hide the appendix", cat, []) == [{"cmd": "hide", "id": "insights:appendix"}]
    assert viewchat.toggle_cmds("show the appendix", cat, ["insights:appendix"]) == [{"cmd": "show", "id": "insights:appendix"}]
    assert viewchat.toggle_cmds("show the appendix", cat, []) == []                          # nothing hidden to show: ask which
    assert viewchat.toggle_cmds("hide something that does not exist anywhere", cat, []) == []
    assert viewchat.toggle_cmds("show only the top 3 actions", cat, []) is None             # not a hide or show: a top N
    assert viewchat.toggle_cmds("what is revenue by region", cat, []) is None


# phrase to type, the section or item id that must end up hidden, the tab it lives on
EVERY_TAB = [("hide the cleaned table", "data:table:clean", "data"), ("hide the cleaning log", "cleaning:steps", "cleaning"), ("hide how we analysed", "plan:questions", "plan"),
             ("hide the conversation", "chat:conversation", "chat"), ("hide the appendix", "insights:appendix", "insights"),
             ("hide the key figures", "report:numbers", "report"), ("hide what stands out", "dashboard:takeaways", "dashboard"), ("hide the ideas", "strategy:list", "strategy")]


@pytest.mark.parametrize("phrase,item,tab", EVERY_TAB)
def test_a_chat_hide_applies_on_every_tab_through_the_same_mechanism_and_a_chat_show_restores_it(run, phrase, item, tab):
    rid, wd, res = run
    t = chat.ask(wd, res, question=phrase, llm=None, allow_ai=False, tab=ve.TAB_NAMES[tab])
    if t["answer"]["view"].get("preview"):                      # a change to more than 3 items is shown first, then applied with the Apply chip
        t = chat.ask(wd, res, chip=t["answer"]["chips"][0]["id"], llm=None, allow_ai=False, tab=ve.TAB_NAMES[tab])
    assert t["answer"]["view"]["changed"], t["answer"]["text"]
    r = view(rid)["resolved"]
    assert item in r["tabs"][tab]["hidden"] and view(rid)["bar"]["show"]
    # the hand-made command gives the same state
    chat.ask(wd, res, question="undo", llm=None, allow_ai=False)
    post(rid, command={"cmd": "hide", "id": item})
    assert core(view(rid)["state"]) == core(store.get_view_state(wd))
    shown = chat.ask(wd, res, question=phrase.replace("hide", "show", 1), llm=None, allow_ai=False, tab=ve.TAB_NAMES[tab])
    if shown["answer"]["view"].get("preview"):
        shown = chat.ask(wd, res, chip=shown["answer"]["chips"][0]["id"], llm=None, allow_ai=False, tab=ve.TAB_NAMES[tab])
    assert shown["answer"]["view"]["changed"] and view(rid)["resolved"]["original"]


@pytest.mark.parametrize("tab", ["cleaning", "plan"])
def test_a_short_view_on_a_tab_that_opens_full_folds_all_but_five_and_show_all_brings_them_back(run, tab):
    rid, wd, res = run
    cat = ve.catalogue(res)
    n = len(cat["tabs"][tab]["primary"])
    t = chat.ask(wd, res, question="make this tab shorter", llm=None, allow_ai=False, tab=ve.TAB_NAMES[tab])
    folded = view(rid)["resolved"]["tabs"][tab]["folded"]
    assert len(folded) == max(0, n - ve.SHORT_INSIGHTS), (tab, n)
    if n > ve.SHORT_INSIGHTS:
        assert view(rid)["bar"]["show"]
        post(rid, command={"cmd": "set_mode", "tab": tab, "mode": "full"})
        assert view(rid)["resolved"]["tabs"][tab]["folded"] == [] and view(rid)["resolved"]["original"]


def test_the_ai_planner_edits_every_tab_with_the_same_commands(run):
    rid, wd, res = run
    for tab, item in (("data", "data:table:clean"), ("cleaning", "cleaning:steps"), ("model", ve.catalogue(res)["tabs"]["model"]["sections"][0]), ("plan", "plan:setup"), ("chat", "chat:conversation")):
        ai = FakeAI(P(("view_edit", {"commands": [{"cmd": "hide", "id": item}]})))
        a = chat.ask(wd, res, question=f"get rid of the {tab} part please", llm=ai, allow_ai=True, tab=ve.TAB_NAMES[tab])["answer"]
        assert a["view"]["changed"], tab
        assert item in view(rid)["resolved"]["tabs"][tab]["hidden"], tab
    assert view(rid)["bar"]["text"] == "5 items hidden"


# ------------------------------------------------------------------ briefs: rename and edit
def make(run):
    rid, wd, res = run
    fid = res["report_view"]["evidence"][0]["id"]
    plan = P(("brief", {"briefs": [{"audience": "the board", "length": "short", "focus": "growth", "columns": ["revenue"], "evidence": [{"tool": "report_facts", "args": {"ids": [fid]}}]}]}))
    chat.ask(wd, res, question="brief", llm=FakeAI(plan, {"headline": f"Segment Enterprise averages 3.2× the revenue of Consumer [{fid}].", "key_figures": [f"Average revenue is 550 for Enterprise [{fid}]."], "recommendations": [f"Put more behind what works [{fid}]."]}), allow_ai=True)
    return store.get_briefs(wd)[0]


def test_a_brief_can_be_renamed_and_its_lines_edited_and_the_quality_note_stays(run):
    rid, wd, res = run
    b = make(run)
    fid = res["report_view"]["evidence"][0]["id"]
    r = client.post(f"/api/runs/{rid}/briefs/{b['id']}/rename", json={"title": "Note for Priya"})
    assert r.status_code == 200 and r.json()["title"] == "Note for Priya" and r.json()["title_edited"] is True
    r = client.post(f"/api/runs/{rid}/briefs/{b['id']}/edit", json={"section": "headline", "text": "My own headline."}).json()
    assert r["headline"] == {"text": "My own headline.", "ids": [fid], "edited": True}
    r = client.post(f"/api/runs/{rid}/briefs/{b['id']}/edit", json={"section": "recommendations", "index": 0, "text": "Ask the regional leads first."}).json()
    assert r["recommendations"][0]["edited"] and r["recommendations"][0]["text"] == "Ask the regional leads first."
    assert r["key_figures"][0].get("edited") is None                                       # only the edited line is labelled
    saved = store.get_briefs(wd)[0]
    assert saved["quality"] == b["quality"] and saved["quality"]["trust"]                  # the trust label and the note are untouched
    assert client.post(f"/api/runs/{rid}/briefs/{b['id']}/edit", json={"section": "quality", "text": "all fine"}).status_code == 400
    assert client.post(f"/api/runs/{rid}/briefs/{b['id']}/edit", json={"section": "headline", "text": "   "}).status_code == 400
    assert client.post(f"/api/runs/{rid}/briefs/nope/rename", json={"title": "x"}).status_code == 400
    assert client.get(f"/api/runs/{rid}/briefs").json()["briefs"][0]["title"] == "Note for Priya"
    html = __import__("app.lens.exporter", fromlist=["x"]).brief_html(saved, res, wd)
    assert "Edited by you" in html and "Note for Priya" in html
    assert client.delete(f"/api/runs/{rid}/briefs/{b['id']}").status_code == 200 and store.get_briefs(wd) == []
