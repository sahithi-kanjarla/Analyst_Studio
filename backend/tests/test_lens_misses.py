"""Lens misses from a real-AI run, with scripted planners (no AI): "only needed charts" on a tab already in the short view, "show that as a line" with and without a chart in the conversation, and a two-step request planned as two steps."""
import os

os.environ["LLM_PROVIDER"] = "none"

from app import viewedit as ve
from app.lens import chat, store
from app.lens.agent import planner
from app.lens.agent.tools import view_edit
from tests.test_lens import FakeAI
from tests.test_partb_view import run  # noqa: F401


def P(*steps):
    return {"plan": [{"tool": t, "args": a} for t, a in steps]}


# ------------------------------------------------------------------ (a) "make the insights tab shorter with only needed charts"
def test_only_needed_charts_is_applied_even_when_the_short_view_is_already_on(run):
    rid, wd, res = run
    assert ve.resolve(res, store.get_view_state(wd))["tabs"]["insights"]["short"]                        # the Insights tab opens in the short view
    ai = FakeAI(P(("view_edit", {"commands": [{"cmd": "set_mode", "tab": "insights", "mode": "short"}]})))          # the model planned only the short view
    a = chat.ask(wd, res, question="make the insights tab shorter with only needed charts", llm=ai, allow_ai=True, tab="Insights")["answer"]
    cmds = a["view"]["commands"]
    assert {"cmd": "only_supporting", "tab": "insights", "on": True} in cmds and a["view"]["changed"]
    assert a["text"].startswith("Insights shows only the charts that support") and "already how it is shown, so nothing changed." not in a["text"].split(" The rest")[0]
    assert store.get_view_state(wd)["only_supporting"].get("insights") is True
    again = chat.ask(wd, res, question="make the insights tab shorter with only needed charts", llm=FakeAI(P(("view_edit", {"commands": [{"cmd": "set_mode", "tab": "insights", "mode": "short"}]}))), allow_ai=True, tab="Insights")["answer"]
    assert again["text"] == view_edit.NOOP and not again["view"]["changed"]                                  # nothing left to change: said once


def test_the_message_says_what_changed_and_what_was_already_so():
    noop = view_edit.NOOP
    assert view_edit.plain_messages([noop, "Insights shows only the charts that support an action."]) == "Insights shows only the charts that support an action. The rest of what you asked was already how it is shown."
    assert view_edit.plain_messages([noop, noop]) == noop
    assert view_edit.plain_messages(["A", "B"]) == "A B"
    cmds = view_edit.with_requested_extras("only the needed charts please", [{"cmd": "set_mode", "tab": "dashboard", "mode": "short"}], "Insights")
    assert cmds[-1] == {"cmd": "only_supporting", "tab": "dashboard", "on": True}
    assert view_edit.with_requested_extras("hide the first chart", [{"cmd": "hide", "id": "x"}], "Insights") == [{"cmd": "hide", "id": "x"}]


# ------------------------------------------------------------------ (b) "show that as a line"
def test_show_that_as_a_line_changes_the_chart_in_the_conversation(run):
    rid, wd, res = run
    first = FakeAI(P(("data_query", {"query": {"op": "aggregate", "measure": "revenue", "agg": "sum", "group_by": ["Segment"]}})))
    a1 = chat.ask(wd, res, question="revenue by segment", llm=first, allow_ai=True, tab="Report")["answer"]
    assert a1["status"] == "answered" and a1["chart"]["path"]
    ai = FakeAI(P(("chart_edit", {"target": "answer", "chart": "line"})))
    a = chat.ask(wd, res, question="show that as a line", llm=ai, allow_ai=True, tab="Report")["answer"]
    assert a["status"] == "answered" and a["query"]["chart"] == "line" and a["agent"]["tools"] == ["chart_edit"]


def test_with_no_chart_yet_it_asks_which_one_instead_of_refusing(run):
    rid, wd, res = run
    ai = FakeAI(P(("chart_edit", {"target": "answer", "chart": "line"})))
    a = chat.ask(wd, res, question="show that as a line", llm=ai, allow_ai=True, tab="Report")["answer"]
    assert a["status"] == "clarify" and "Which chart" in a["text"] and 2 <= len(a["clarify"]["choices"]) <= 3
    assert all(c["commands"][0]["chart"] == "line" for c in a["clarify"]["choices"])
    assert "I haven't drawn one" not in a["text"]


def test_a_plan_that_skips_the_chart_change_is_sent_back_once_with_the_reason(run):
    rid, wd, res = run
    ai = FakeAI(P(("explain", {"topic": "tab"})), P(("chart_edit", {"target": "answer", "chart": "line"})))
    chat.ask(wd, res, question="show that as a line", llm=ai, allow_ai=True, tab="Report")
    assert ai.calls == 2 and "chart_edit" in ai.prompts[1] and "Request:" in ai.prompts[1]
    assert planner.request_problems("show that as a line", [{"tool": "chart_edit", "args": {}}]) == []
    assert planner.request_problems("show a line of revenue over time", [{"tool": "data_query", "args": {}}]) == []          # no "that": a new chart, not a change


# ------------------------------------------------------------------ (c) two steps
def test_a_two_step_request_is_planned_as_two_steps_not_a_question(run):
    rid, wd, res = run
    two = P(("data_query", {"query": {"op": "aggregate", "measure": "revenue", "agg": "sum", "group_by": ["region"]}}), ("chart_edit", {"target": "answer", "chart": "share"}))
    ai = FakeAI(P(("clarify", {"question": "Which do you mean?", "choices": [{"label": "A", "query": {"op": "aggregate", "measure": "revenue", "agg": "sum", "group_by": ["region"]}}, {"label": "B", "commands": [{"cmd": "reset"}]}]})), two)
    a = chat.ask(wd, res, question="show revenue by region and then make that chart a share chart", llm=ai, allow_ai=True, tab="Report")["answer"]
    assert ai.calls == 2 and a["agent"]["tools"] == ["data_query", "chart_edit"] and a["status"] == "answered" and a["query"]["chart"] == "share"
    assert planner.request_problems("show revenue by region and then make that chart a share chart", [{"tool": "data_query", "args": {}}]) != []
    assert planner.request_problems("show revenue by region and then make that chart a share chart", [{"tool": "data_query", "args": {}}, {"tool": "chart_edit", "args": {}}]) == []
