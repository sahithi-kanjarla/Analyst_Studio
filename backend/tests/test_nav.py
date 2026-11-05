"""Navigation and layout: the details switch ("Expand all details") in the shared view mechanism (hand control and chat), and the tab structure the interface declares (five tabs, the Data sub-views, old names)."""
import json
import os
import re
from pathlib import Path

os.environ["LLM_PROVIDER"] = "none"

from app import viewedit as ve
from app.lens import chat, store, viewchat
from tests.test_partb_view import run  # noqa: F401  (a copy of the sample run, with a report page)

SRC = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "tabs.ts").read_text(encoding="utf-8")


# ------------------------------------------------------------------ the details switch
def test_set_details_is_a_view_command_with_undo_and_reset(run):
    rid, wd, res = run
    cat = ve.catalogue(res)
    st, msg = ve.apply(res, ve.empty(), {"cmd": "set_details", "on": True}, cat)
    assert st["details"] is True and "open" in msg and ve.resolve(res, st, cat)["details"] is True
    assert ve.bar(res, st, cat)["show"] and "all details open" in ve.bar(res, st, cat)["text"]
    off, _ = ve.apply(res, st, {"cmd": "set_details", "on": False}, cat)
    assert off["details"] is False and ve.resolve(res, off, cat)["details"] is False
    back, _ = ve.apply(res, st, {"cmd": "undo"}, cat)
    assert back["details"] is False
    reset, _ = ve.apply(res, st, {"cmd": "reset"}, cat)
    assert reset["details"] is False and ve.is_original(reset)
    assert ve.normalise({"details": 1})["details"] is True and ve.normalise({"details": "x"})["details"] is True and ve.normalise(None)["details"] is False
    assert ve.is_original(ve.empty()) and not ve.is_original(st)


def test_the_details_switch_changes_no_item_and_no_figure(run):
    rid, wd, res = run
    cat = ve.catalogue(res)
    base = ve.resolve(res, ve.empty(), cat)
    st, _ = ve.apply(res, ve.empty(), {"cmd": "set_details", "on": True}, cat)
    now = ve.resolve(res, st, cat)
    assert {k: v for k, v in now.items() if k not in ("details", "original")} == {k: v for k, v in base.items() if k not in ("details", "original")}


def test_chat_sets_the_details_through_the_same_mechanism(run):
    rid, wd, res = run
    a = chat.ask(wd, res, question="show all details", llm=None, allow_ai=False, tab="Report")["answer"]
    assert a["view"]["changed"] and a["view"]["commands"] == [{"cmd": "set_details", "on": True}]
    assert store.get_view_state(wd)["details"] is True                                        # kept with the report, so the page shows it after a reload
    b = chat.ask(wd, res, question="collapse details", llm=None, allow_ai=False, tab="Insights")["answer"]
    assert b["view"]["changed"] and store.get_view_state(wd)["details"] is False
    c = chat.ask(wd, res, question="expand all details", llm=None, allow_ai=False, tab="Data")["answer"]
    assert store.get_view_state(wd)["details"] is True
    u = chat.ask(wd, res, question="undo", llm=None, allow_ai=False, tab="Data")["answer"]
    assert store.get_view_state(wd)["details"] is False


def test_asking_for_the_details_of_something_is_a_question_not_a_view_change():
    assert viewchat.parse_rules("show me the details of Enterprise", "Report", {"items": {}}, None) in (None, [])
    assert viewchat.parse_rules("what are the details behind this number", "Report", {"items": {}}, None) in (None, [])
    assert viewchat.parse_rules("open all the details", "Report", {"items": {}}, None) == [{"cmd": "set_details", "on": True}]
    assert "set_details" in viewchat.COMMAND_HELP


# ------------------------------------------------------------------ the tabs the interface declares
def test_five_tabs_in_order_and_four_data_subviews():
    nav = re.search(r"NAV_TABS: NavTab\[\] = \[([^\]]*)\]", SRC)[1]
    assert re.findall(r'"(\w+)"', nav) == ["report", "insights", "strategy", "dashboard", "data"]
    labels = dict(re.findall(r"(\w+): \"([^\"]+)\"", re.search(r"NAV_LABEL[^=]*= \{([^}]*)\}", SRC)[1]))
    assert [labels[t] for t in ("report", "insights", "strategy", "dashboard", "data")] == ["Report", "Insights", "Ideas to try", "Dashboard", "Data"]
    views = re.findall(r'"(\w+)"', re.search(r"DATA_VIEWS: DataView\[\] = \[([^\]]*)\]", SRC)[1])
    assert views == ["data", "model", "cleaning", "plan"]
    dl = dict(re.findall(r"(\w+): \"([^\"]+)\"", re.search(r"DATA_LABEL[^=]*= \{([^}]*)\}", SRC)[1]))
    assert [dl[v] for v in views] == ["Your data", "How files connect", "What we cleaned", "How we analysed"]


def test_every_backend_tab_is_still_declared_and_the_sub_views_are_real_tabs():
    block = SRC.split("TAB_LABEL")[1].split("};")[0]
    declared = re.findall(r"^\s*(\w+):", block, re.M)
    assert set(declared) == set(ve.TABS)                                       # the agent's tab coverage: nothing was dropped or renamed
    assert ve.TAB_NAMES["data"] == "Your data" and ve.TAB_NAMES["plan"] == "How we analysed"        # what Lens says for a sub-view is its own name


def test_old_tab_names_resolve_to_their_new_place():
    body = SRC.split("export function resolveTarget")[1]
    for old, where in (("powerbi", 'nav: "report"'), ("print", 'exportItem: "print"'), ("chat", "lens: true"), ("model", 'data: "model"'), ("cleaning", 'data: "cleaning"'), ("plan", 'data: "plan"'),
                       ("howfilesconnect", 'data: "model"'), ("whatwecleaned", 'data: "cleaning"'), ("howweanalysed", 'data: "plan"'), ("powerbiexport", 'nav: "report"'), ("yourdata", 'data: "data"')):
        assert re.search(rf"\b{old}: \{{[^}}]*{re.escape(where)}", body), old


def test_the_chat_view_ids_stay_in_the_catalogue_and_power_bi_is_not_a_tab(run):
    rid, wd, res = run
    cat = ve.catalogue(res)
    assert any(i["tab"] == "chat" for i in cat["items"].values())
    assert not any(i["tab"] == "powerbi" for i in cat["items"].values()) and "powerbi" not in ve.TABS
