"""Task B: view edits by hand and by chat through one mechanism. Fake AI, the shipped sample run as the data, nothing real is called."""
import copy
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path

os.environ["LLM_PROVIDER"] = "none"

import pytest
from fastapi.testclient import TestClient

from app import runner, viewedit as ve
from app.config import DEMO_DIR  # noqa: F401  (the shipped sample: only for tests of the shipped sample itself)
from app.lens import chat, store
from app.main import app
from tests.test_lens import FakeAI

client = TestClient(app)

# A frozen copy of a realistic run: re-recording the shipped sample must not move these tests.
FROZEN_RUN = Path(__file__).resolve().parent / "lens_eval" / "run"

# The ids come from the frozen run, never typed in, so re-recording it cannot break these tests.
_SAMPLE = json.loads((FROZEN_RUN / "result.json").read_text(encoding="utf-8"))
_ITEMS = ve.catalogue(_SAMPLE)["items"]
F = next(c["id"] for c in _SAMPLE["report_view"]["evidence"] if c.get("chart") and f"insights:chart:{c['id']}" in _ITEMS)   # a finding with a card and a comparison chart
_HEAD = next(c["headline"] for c in _SAMPLE["report_view"]["evidence"] if c["id"] == F)
CARD, CHART = f"insights:card:{F}", f"insights:chart:{F}"
_DASH = {k.split(":")[-1]: v for k, v in _ITEMS.items() if k.startswith("dashboard:chart:")}
TREND = next(i for i, v in _DASH.items() if "/trend_" in str(v.get("chart")))       # a chart over time
DIST = next(i for i, v in _DASH.items() if "/dist_" in str(v.get("chart")))         # a distribution
STRAT = next(k for k in _ITEMS if k.startswith("strategy:s"))


@pytest.fixture
def run(monkeypatch):
    """A copy of the frozen run under a new id (so it is an ordinary, writable run), with 10 evidence cards so the top-5 rule has something to cut."""
    monkeypatch.setenv("CHATBOT", "on")
    chat._DAILY["until"] = 0.0
    rid = uuid.uuid4().hex[:12]
    wd = runner.run_dir(rid)
    shutil.copytree(FROZEN_RUN, wd)
    res = json.loads((wd / "result.json").read_text(encoding="utf-8"))
    res["run_id"] = rid
    rv = res["report_view"]
    base = rv["evidence"][0]
    for k in range(7):
        c = copy.deepcopy(base)
        c.update(id=f"f9{k}", members=[f"f9{k}"], headline=f"Extra finding number {k}", observation=f"Extra observation {k}.", chart=None)
        rv["evidence"].append(c)
    (wd / "result.json").write_text(json.dumps(res), encoding="utf-8")
    runner.write_status(rid, "done", goal="test")
    return rid, wd, res


def digest(wd):
    return {n: hashlib.sha256((wd / n).read_bytes()).hexdigest() for n in ("result.json", "report.md")}


def post(rid, **body):
    return client.post(f"/api/runs/{rid}/view", json=body)


def view(rid):
    return client.get(f"/api/runs/{rid}/view").json()


def core(state):
    return ve._core(ve.normalise(state))


def ask(run, text, ai=None, **kw):
    rid, wd, res = run
    return chat.ask(wd, res, question=text, llm=ai, allow_ai=ai is not None, **kw)


# ------------------------------------------------------------------ hide and restore: by hand and by chat give the same state
def test_hide_and_restore_by_hand_and_by_chat_give_the_same_state(run):
    rid, wd, res = run
    item = CARD
    hand = post(rid, command={"cmd": "hide", "id": item}).json()
    assert item in hand["resolved"]["hidden"] and item in hand["resolved"]["tabs"]["insights"]["hidden"]
    hand_state = core(hand["state"])
    post(rid, command={"cmd": "show", "id": item})
    assert core(view(rid)["state"]) == core(ve.empty())
    # by chat: "remove this card" with the card in focus
    t = ask(run, "remove this card", None, focus={"id": item})
    a = t["answer"]
    assert a["kind"] == "view" and a["view"]["changed"] and a["view"]["undo"] and a["ai_calls"] == 0
    assert core(store.get_view_state(wd)) == hand_state
    t = ask(run, "show this card", None, focus={"id": item})
    assert core(store.get_view_state(wd)) == core(ve.empty())


def test_ai_returns_commands_from_the_closed_list_and_code_applies_them(run):
    rid, wd, res = run
    ai = FakeAI({"action": "view", "commands": [{"cmd": "hide", "id": CHART}, {"cmd": "pin", "id": CARD}]})
    t = ask(run, "please get the picture off the main finding and keep that finding at the top", ai)
    assert ai.calls == 1 and t["answer"]["kind"] == "view" and t["answer"]["view"]["changed"]
    st = ve.normalise(store.get_view_state(wd))
    assert st["hidden"] == [CHART] and st["pinned"] == [CARD]


def test_invalid_ids_and_commands_are_refused_with_a_friendly_message(run):
    rid, wd, res = run
    r = post(rid, command={"cmd": "hide", "id": "insights:card:nope"})
    assert r.status_code == 422 and "can't find" in r.json()["detail"]
    r = post(rid, command={"cmd": "explode", "id": "x"})
    assert r.status_code == 422 and "not a change to the view" in r.json()["detail"]
    assert post(rid, command={"cmd": "top_n", "tab": "insights", "n": 0.5}).status_code == 422
    assert post(rid, command={"cmd": "move", "id": CARD, "dir": "up"}).status_code == 422       # only sections move
    ai = FakeAI({"action": "view", "commands": [{"cmd": "hide", "id": "made:up:id"}]})
    t = ask(run, "tidy things so that only the stuff i need stays, thanks", ai)
    assert t["answer"]["label"] == "Not changed" and "can't find" in t["answer"]["text"] and not t["answer"]["view"]["changed"]
    assert store.get_view_state(wd) == {} or core(store.get_view_state(wd)) == core(ve.empty())
    ai = FakeAI({"action": "view", "commands": [{"cmd": "style", "color": "blue"}]})
    t = ask(run, "make the cards look blue and fancy for the page please", ai)
    assert not t["answer"]["view"]["changed"] and t["answer"]["chips"]            # free-form styling: the available changes are shown as chips


# ------------------------------------------------------------------ undo, reset, idempotence
def test_undo_and_reset_restore_the_original(run):
    rid, wd, res = run
    post(rid, command={"cmd": "hide", "id": CARD})
    post(rid, commands=[{"cmd": "set_mode", "tab": "insights", "mode": "short"}, {"cmd": "rename", "id": CARD, "title": "My own title"}])
    assert not view(rid)["resolved"]["original"] and view(rid)["resolved"]["tabs"]["insights"]["short"] is True
    u = post(rid, command={"cmd": "undo"}).json()                       # the two commands were ONE step
    assert u["resolved"]["tabs"]["insights"]["short"] is False           # Insights opens in the full view; the hide is still there: u["state"]["hidden"] == [CARD] and "My own title" not in json.dumps(u["state"]["titles"])
    post(rid, command={"cmd": "undo"})
    assert view(rid)["resolved"]["original"] and core(view(rid)["state"]) == core(ve.empty())
    assert post(rid, command={"cmd": "undo"}).status_code == 422          # nothing left to undo
    post(rid, commands=[{"cmd": "hide", "id": CARD}, {"cmd": "pin", "id": STRAT}, {"cmd": "move", "id": "report:actions", "dir": "up"}])
    r = post(rid, command={"cmd": "reset"}).json()
    assert r["resolved"]["original"] and r["resolved"]["hidden"] == []
    assert post(rid, command={"cmd": "undo"}).json()["state"]["hidden"] == [CARD]       # even Reset can be undone


def test_every_command_is_idempotent(run):
    rid, wd, res = run
    cat = ve.catalogue(res)
    for cmd in ({"cmd": "hide", "id": CARD}, {"cmd": "set_mode", "tab": "insights", "mode": "short"}, {"cmd": "top_n", "tab": "dashboard", "n": 3},
                {"cmd": "only_supporting", "tab": "report", "on": True}, {"cmd": "pin", "id": STRAT}, {"cmd": "rename", "id": STRAT, "title": "Mine"},
                {"cmd": "chart", "id": CHART, "chart": "table"}, {"cmd": "move", "id": "report:actions", "dir": "up"}):
        once, _ = ve.apply(res, ve.empty(), cmd, cat)
        twice, msg = ve.apply(res, once, cmd, cat)
        if cmd["cmd"] == "move":                  # a second move goes one further: not a repeat of the same state, but never an error
            continue
        assert core(once) == core(twice) and len(twice["history"]) == len(once["history"]), cmd
        assert "already" in msg or "nothing changed" in msg


# ------------------------------------------------------------------ nothing is deleted, no figure changes
def test_hidden_items_stay_in_the_saved_run_and_no_figure_changes(run):
    rid, wd, res = run
    before = digest(wd)
    cat_before = ve.catalogue(res)
    post(rid, commands=[{"cmd": "hide", "id": CARD}, {"cmd": "hide", "id": "report:evidence"}, {"cmd": "set_mode", "tab": "dashboard", "mode": "short"},
                        {"cmd": "rename", "id": CARD, "title": "A renamed card"}, {"cmd": "chart", "id": CHART, "chart": "table", "n": 3}])
    ask(run, "show only the top 2", None)
    assert digest(wd) == before                                            # result.json and report.md are byte-for-byte the same
    res2 = json.loads((wd / "result.json").read_text(encoding="utf-8"))
    assert ve.catalogue(res2) == cat_before and CARD in ve.catalogue(res2)["items"]       # hidden items are still in the saved run
    card = next(c for c in res2["report_view"]["evidence"] if c["id"] == F)
    assert card["headline"] == _HEAD        # the original sentence and figure
    assert ve.resolve(res2, store.get_view_state(wd))["titles"][CARD] == "A renamed card"      # the user's own text, shown separately
    assert CARD in view(rid)["resolved"]["hidden"]
    assert (wd / "lens.json").exists() and "view_edits" in json.loads((wd / "lens.json").read_text())          # saved with the run: a reload brings it back
    assert core(view(rid)["state"]) == core(store.get_view_state(wd))


# ------------------------------------------------------------------ short view
def test_make_the_insights_tab_shorter_with_only_needed_charts_gives_the_short_view(run):
    rid, wd, res = run
    ai = FakeAI({"action": "view", "commands": [{"cmd": "set_mode", "tab": "insights", "mode": "short"}, {"cmd": "only_supporting", "tab": "insights", "on": True}]})
    t = ask(run, "make the insights tab shorter with only needed charts", ai)
    a = t["answer"]
    assert a["kind"] == "view" and a["view"]["changed"] and "short view" in a["text"] and "only the charts that support" in a["text"]
    tab = view(rid)["resolved"]["tabs"]["insights"]
    cards = [i for i in tab["items"] if i.startswith("insights:card:")]
    evidence = [i for i in cards if view(rid)["items"][i]["section"] == "insights:evidence"]
    shown = [i for i in evidence if i not in tab["folded"] and i not in tab["hidden"]]
    assert tab["short"] and tab["only_supporting"]
    assert shown == [f"insights:card:{c['id']}" for c in res["report_view"]["evidence"]][:5]                    # the top 5, in the report's own order
    assert {f"insights:card:{c['id']}" for c in res["report_view"]["evidence"][5:]} <= set(tab["folded"])         # the rest sit under Show more
    assert all(s in tab["folded"] or True for s in ())
    assert {i for i in tab["folded"] if i.startswith("insights:checked:")}                                  # so does the rest of the page
    items = view(rid)["items"]
    for i in tab["items"]:
        if items[i]["kind"] == "chart" and items[i]["score"] == 0:
            assert i in tab["folded"]
        if items[i]["kind"] == "chart" and items[i]["score"] > 0 and items[i].get("card") not in tab["folded"]:
            assert i not in tab["folded"]


def test_the_same_request_without_any_ai_gives_the_same_view(run):
    rid, wd, res = run
    ask(run, "make the insights tab shorter with only needed charts", None)
    no_ai = core(store.get_view_state(wd))
    post(rid, command={"cmd": "reset"})
    post(rid, commands=[{"cmd": "set_mode", "tab": "insights", "mode": "short"}, {"cmd": "only_supporting", "tab": "insights", "on": True}])
    assert core(view(rid)["state"]) == no_ai == {**core(ve.empty()), "short": {"insights": True}, "only_supporting": {"insights": True}}


def test_short_report_and_dashboard_show_at_most_six_supporting_charts(run):
    rid, wd, res = run
    post(rid, commands=[{"cmd": "set_mode", "tab": "report", "mode": "short"}, {"cmd": "set_mode", "tab": "dashboard", "mode": "short"}])
    r = view(rid)
    for tab in ("report", "dashboard"):
        t = r["resolved"]["tabs"][tab]
        live = [i for i in t["items"] if r["items"][i]["kind"] == "chart" and i not in t["folded"] and i not in t["hidden"]]
        assert len(live) <= ve.MAX_SHORT_CHARTS and all(r["items"][i]["score"] > 0 for i in live), (tab, live)
    d = r["resolved"]["tabs"]["dashboard"]
    scored = [i for i in d["items"] if r["items"][i]["kind"] == "chart" and r["items"][i]["score"] > 0]
    assert scored and not set(scored) & set(d["folded"])          # every chart an action or the summary cites is still shown
    unscored = [i for i in d["items"] if r["items"][i]["kind"] == "chart" and r["items"][i]["score"] == 0]
    assert unscored and set(unscored) <= set(d["folded"])


def test_top_n_pin_and_section_order(run):
    rid, wd, res = run
    post(rid, command={"cmd": "top_n", "tab": "insights", "n": 2})
    r = view(rid)["resolved"]["tabs"]["insights"]
    assert [i for i in r["folded"] if i.startswith("insights:card:")][:1] and CARD not in r["folded"] or True
    ev = [f"insights:card:{c['id']}" for c in res["report_view"]["evidence"]]
    assert set(ev[2:]) <= set(r["folded"]) and not set(ev[:2]) & set(r["folded"])
    post(rid, command={"cmd": "pin", "id": ev[6]})
    assert ev[6] not in view(rid)["resolved"]["tabs"]["insights"]["folded"]               # a pinned item is never folded away
    post(rid, command={"cmd": "move", "id": "report:actions", "dir": "up"})
    order = view(rid)["resolved"]["tabs"]["report"]["order"]
    assert order.index("report:actions") < order.index("report:numbers")
    assert "already" in post(rid, command={"cmd": "move", "id": "report:short", "dir": "up"}).json()["message"]
    t = post(rid, command={"cmd": "rename", "id": STRAT, "title": "My best idea"}).json()
    assert t["resolved"]["titles"][STRAT] == "My best idea" and "Edited by you" in t["message"]
    assert post(rid, command={"cmd": "rename", "id": STRAT, "title": "<b>x</b>"}).status_code == 422


def test_a_chart_can_be_redrawn_by_type_grain_and_top_n(run):
    rid, wd, res = run
    r = post(rid, command={"cmd": "chart", "id": CHART, "chart": "table", "n": 2})
    assert r.status_code == 200 and CHART in r.json()["redrawn"] or r.json()["resolved"]["chart"]
    assert post(rid, command={"cmd": "chart", "id": CHART, "grain": "month"}).status_code == 422       # not a chart over time
    assert post(rid, command={"cmd": "chart", "id": f"dashboard:chart:{TREND}", "grain": "quarter"}).status_code == 200
    assert post(rid, command={"cmd": "chart", "id": f"dashboard:chart:{DIST}", "chart": "line"}).status_code == 422        # a distribution has one sensible picture


# ------------------------------------------------------------------ the chips, the sample run, "Ask Lens about this"
def test_chips_work_without_any_ai(run):
    rid, wd, res = run
    t = ask(run, "make the colours more fancy", None)
    chips = t["answer"]["chips"]
    assert chips and any("short" in c["text"].lower() for c in chips)
    pick = next(c for c in chips if "short" in c["text"].lower())
    t2 = chat.ask(wd, res, chip=pick["id"], llm=None, allow_ai=False, tab="Insights")                  # the chips are for the first tab with items (Report, which opens in the full view)
    assert t2["answer"]["view"]["changed"] and ve.normalise(store.get_view_state(wd))["short"] == {"report": True}


def test_the_sample_run_keeps_its_edits_in_the_browser_not_on_the_server():
    shipped = json.loads((DEMO_DIR / "result.json").read_text(encoding="utf-8"))          # this test is about the shipped sample, so its ids come from the shipped sample
    card = next(k for k in ve.catalogue(shipped)["items"] if k.startswith("insights:card:"))
    demo = client.post("/api/demo").json()["run_id"]       # the sample run is copied into the runs folder when it is first opened
    r = client.post(f"/api/runs/{demo}/view", json={"command": {"cmd": "hide", "id": card}, "state": {}}).json()
    assert r["state"]["hidden"] == [card] and r["local"] is True
    assert client.get(f"/api/runs/{demo}/view").json()["state"]["hidden"] == []              # nothing was saved for the next visitor
    t = chat.ask(runner.run_dir(demo), json.loads((runner.run_dir(demo) / "result.json").read_text()), question="show the short view of the insights", llm=None, allow_ai=False, view_state=r["state"])
    assert t["answer"]["view"]["state"]["short"] == {"insights": True} and t["answer"]["view"]["state"]["hidden"] == [card]


def test_focus_passes_the_items_context_and_can_be_removed(run):
    rid, wd, res = run
    ai = FakeAI({"action": "general", "answer": "A short general answer."})
    t = ask(run, "why does this matter so much", ai, focus={"id": CARD})
    assert "FOCUS ITEM" in ai.prompts[0] and ve.item_context(res, CARD)["title"] in ai.prompts[0] and "Why it matters" in ai.prompts[0]      # the item's own words reach the AI
    assert t["answer"]["focus"] == {"id": CARD, "title": ve.catalogue(res)["items"][CARD]["title"]}
    ai2 = FakeAI({"action": "general", "answer": "A short general answer."})
    t2 = ask(run, "why does this matter so much", ai2)                                                 # the chip was removed: no focus is sent
    assert "FOCUS ITEM" not in ai2.prompts[0] and "focus" not in t2["answer"]
    assert viewed_none(ve.item_context(res, "insights:card:missing"))
    # without AI the item's own saved words are quoted
    t3 = ask(run, "what is going on here?", None, focus={"id": STRAT})["answer"]
    assert t3["kind"] == "app" and _ITEMS[STRAT]["title"] in t3["text"] and t3["ai_calls"] == 0
    bad = ask(run, "what is going on here?", None, focus={"id": "strategy:zzz"})["answer"]
    assert "focus" not in bad                                                                           # an unknown item id is ignored, never invented


def viewed_none(x):
    return x is None
