"""Fixes from the first real-AI evaluation (part 2 and 3): an empty plan for a why question runs the why-breakdown, every tool says when to use it, a chart can follow a data step, and the definitions lookup.
Scripted planners; nothing about a specific request lives in the code."""
import json
import os

os.environ["LLM_PROVIDER"] = "none"

import pytest

from app import glossary as gl
from app.lens import chat, records, store
from app.lens.agent import planner, registry
from tests.test_agent_loop import P
from tests.test_lens import FakeAI, make_run
from tests.test_lens_stage2a import REPORT, UND, shop_df
from tests.test_partb_view import run  # noqa: F401


def shop(monkeypatch):
    monkeypatch.setenv("CHATBOT", "on")
    chat._DAILY["until"] = 0.0
    return make_run(shop_df(), UND, REPORT)


# ------------------------------------------------------------------ 2. an empty plan for a "why" is not a refusal
@pytest.mark.parametrize("q", ["why did amount change over the months", "how come Clothing is ahead", "what is behind the amount by category", "why is the amount lower in some regions"])
def test_an_empty_plan_for_a_why_question_runs_the_why_breakdown(monkeypatch, q):
    rid, wd, res = shop(monkeypatch)
    ai = FakeAI({"plan": [], "unsupported": "causes cannot be known"})
    a = chat.ask(wd, res, question=q, llm=ai, allow_ai=True, tab="Report")["answer"]
    assert a["status"] == "answered" and a["kind"] == "why" and a["agent"]["tools"] == ["why_breakdown"] and ai.calls == 1, (q, a["text"])
    assert a["table"] and not any(w in a["text"].lower() for w in ("because", "caused by", "due to"))             # what is behind the number, never a cause
    assert "why_breakdown" not in json.dumps(a["how"]["lines"])


def test_the_why_tool_can_also_be_planned_and_takes_the_columns_it_is_given(monkeypatch):
    rid, wd, res = shop(monkeypatch)
    ai = FakeAI(P(("why_breakdown", {"measure": "amount", "group": "region"})))
    a = chat.ask(wd, res, question="what lies behind the amount", llm=ai, allow_ai=True)["answer"]
    assert a["kind"] == "why" and "Region" in json.dumps(a["table"]["columns"] + [str(x) for x in a.get("lines", [])]) or "region" in a["text"].lower()


def test_a_why_that_is_not_about_the_data_is_still_refused(monkeypatch):
    rid, wd, res = shop(monkeypatch)
    a = chat.ask(wd, res, question="why is the sky blue?", llm=FakeAI({"plan": [], "unsupported": "not about this data"}), allow_ai=True)["answer"]
    assert a["status"] == "no_fit" and len(a["chips"]) == 2


# ------------------------------------------------------------------ 2. every tool says when to use it; nothing says a chart cannot be made
def test_every_tool_description_says_when_to_use_it():
    for name, t in registry.load_tools().items():
        assert t.description.lower().startswith("use "), name


def test_the_prompt_has_no_wording_that_says_a_chart_cannot_be_created_or_edited():
    text = planner.system_prompt().lower()
    for bad in ("chart cannot", "charts cannot", "cannot create a chart", "cannot edit", "can't edit a chart", "chart edits are not", "not possible to chart"):
        assert bad not in text.replace("never say a chart cannot be made", ""), bad
    assert "data_query then chart_edit" in text and "why_breakdown" in text and "only_supporting" in text and "data_catalog" in text


def test_data_then_a_change_to_its_chart_is_one_plan(monkeypatch):
    rid, wd, res = shop(monkeypatch)
    plan = P(("data_query", {"query": {"op": "aggregate", "measure": "amount", "agg": "sum", "group_by": ["category"]}}), ("chart_edit", {"target": "answer", "chart": "share"}))
    ai = FakeAI(plan)
    a = chat.ask(wd, res, question="total amount by category, as a share chart", llm=ai, allow_ai=True)["answer"]
    assert ai.calls == 1 and a["agent"]["tools"] == ["data_query", "chart_edit"] and all(s["ok"] for s in a["agent"]["steps"])
    assert a["chart"]["type"] == "share" and a["table"]["rows"]
    # the chart edit is the second step of the same request: it did not need an earlier turn
    assert len(store.load(wd)["turns"]) == 1


def test_a_short_view_request_for_charts_that_support_an_action_is_plannable(run):
    rid, wd, res = run
    ai = FakeAI(P(("view_edit", {"commands": [{"cmd": "only_supporting", "tab": "dashboard", "on": True}]})))
    a = chat.ask(wd, res, question="keep only the graphs that back up a recommendation", llm=ai, allow_ai=True, tab="Dashboard")["answer"]
    assert a["view"]["changed"] and store.get_view_state(wd)["only_supporting"] == {"dashboard": True}


# ------------------------------------------------------------------ 3. definitions
def test_the_definitions_question_lists_the_reports_definitions_and_the_saved_ones(monkeypatch):
    rid, wd, res = shop(monkeypatch)
    ws = gl.workspace_key(res.get("context"))
    gl.delete_workspace(ws)
    a = chat.ask(wd, res, question="which definitions did you use?", llm=None, allow_ai=False)["answer"]
    assert any("As missing (default)" in x and "45 values of 999" in x for x in a["lines"])                               # the report's "Definitions used"
    assert not any("Saved in your glossary" in x for x in a["lines"])
    gl.merge_answers(ws, {}, {}, "", {"big order": "an order above 300"})
    b = chat.ask(wd, res, question="which definitions did you use?", llm=None, allow_ai=False)["answer"]
    assert any("Saved in your glossary: big order: an order above 300" in x for x in b["lines"]) and any("As missing (default)" in x for x in b["lines"])
    via_tool = chat.ask(wd, res, question="definitions please", llm=FakeAI(P(("glossary", {"op": "lookup"}))), allow_ai=True)["answer"]
    assert any("big order" in x for x in via_tool["lines"]) and any("As missing" in x for x in via_tool["lines"])
    one = chat.ask(wd, res, question="what is a big order", llm=FakeAI(P(("glossary", {"op": "lookup", "term": "big order"}))), allow_ai=True)["answer"]
    assert len(one["lines"]) == 1 and "big order" in one["lines"][0]
    gl.delete_workspace(ws)


def test_with_no_definitions_at_all_it_says_so_plainly(run):
    rid, wd, res = run
    gl.delete_workspace(gl.workspace_key(res.get("context")))
    for q, ai in (("which definitions did you use?", None), ("which definitions did you use?", FakeAI(P(("glossary", {"op": "lookup"}))))):
        a = chat.ask(wd, res, question=q, llm=ai, allow_ai=ai is not None)["answer"]
        assert a["text"] == "No definitions or assumptions were used in this report, and you have no saved definitions." and not a.get("lines")
