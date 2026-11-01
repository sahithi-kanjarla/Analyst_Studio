"""The agent loop: a valid plan, an invalid plan then repair then fallback, a failure partway, the call budget, multi-step requests, verification, the registry, tab coverage, the unmet log and the no-AI fallback."""
import json
import os
import re

os.environ["LLM_PROVIDER"] = "none"

import pytest

from app import viewedit as ve
from app.lens import chat, store
from app.lens.agent import planner, registry, verify
from app.lens.agent.registry import Tool, ToolResult
from tests.test_agent_custom import plan, shop  # noqa: F401  (fixtures)
from tests.test_lens import FakeAI, q
from tests.test_partb_view import run  # noqa: F401  (a copy of the sample run, with a report page)


def ask(sh, ai, text, **kw):
    rid, wd, result, df = sh
    return chat.ask(wd, result, question=text, llm=ai, allow_ai=ai is not None, **kw)


def P(*steps, **kw):
    return {"plan": [{"tool": t, "args": a} for t, a in steps], **kw}


# ------------------------------------------------------------------ the plan
def test_a_valid_plan_runs_each_step_and_the_box_lists_the_steps_and_tools(shop):
    ai = FakeAI(P(("data_query", {"query": {"op": "top", "measure": "amount", "agg": "sum", "group_by": ["city"], "n": 2, "direction": "top"}}), ("explain", {"topic": "trust"})))
    a = ask(shop, ai, "top two cities by amount, and how sure are we")["answer"]
    assert ai.calls == 1 and a["agent"]["tools"] == ["data_query", "explain"] and a["status"] == "answered"
    assert a["table"] and "Delhi" in a["text"] and "saved with this run" in a["text"]            # both steps' sentences, the table of the data step
    box = " ".join(a["how"]["lines"])
    assert "Step 1, data_query" in box and "Step 2, explain" in box


def test_an_invalid_plan_gets_one_repair_then_runs(shop):
    ai = FakeAI(P(("no_such_tool", {})), P(("explain", {"topic": "trust"})))
    a = ask(shop, ai, "something")["answer"]
    assert ai.calls == 2 and a["agent"]["tools"] == ["explain"]
    assert "not a tool" in ai.prompts[1] and "Tools:" in ai.prompts[1]            # the repair prompt lists the problems


def test_a_plan_that_stays_invalid_falls_back_to_the_rule_router_then_to_chips(shop):
    ai = FakeAI(P(("no_such_tool", {})))
    a = ask(shop, ai, "please summarise this report")["answer"]
    assert ai.calls == 2 and a["kind"] == "summary"                                  # the rule router (the no-AI fallback) reads it
    ai = FakeAI(P(("no_such_tool", {})))
    a = ask(shop, ai, "blorp zxqv wibble")["answer"]
    assert ai.calls == 2 and a["status"] == "no_fit" and len(a["chips"]) >= 2 and a.get("unmet")   # nothing reads it: what I can do, with chips
    rid, wd, _, _ = shop
    lines = [json.loads(x) for x in (wd / "lens_unmet.jsonl").read_text().splitlines()]
    assert lines[-1]["request"] == "blorp zxqv wibble" and set(lines[-1]) == {"request", "tab", "at"}


def test_bad_arguments_are_caught_by_the_schema(shop):
    ai = FakeAI(P(("explain", {"topic": "not a topic"})), P(("explain", {"topic": "cleaning"})))
    a = ask(shop, ai, "what did you clean")["answer"]
    assert a["agent"]["tools"] == ["explain"] and ai.calls == 2
    errs, hard = planner.validate(P(("explain", {"topic": "x", "extra": 1})), chat.agent.make_env(shop[1], shop[2], shop[3], chat.load_data(shop[1], shop[2])[1], "x", "", None, True, None, None))
    assert hard and any("no argument" in e for e in errs) and any("must be one of" in e for e in errs)


def test_more_than_four_steps_is_not_a_plan(shop):
    five = P(*[("explain", {"topic": "tab"})] * 5)
    ai = FakeAI(five, P(("explain", {"topic": "tab"})))
    a = ask(shop, ai, "x")["answer"]
    assert a["agent"]["steps"].__len__() == 1 and "at most 4 steps" in ai.prompts[1]


def test_a_failure_partway_stops_and_says_what_was_done(shop):
    ai = FakeAI(P(("explain", {"topic": "trust"}), ("chart_edit", {"target": "answer", "chart": "line"}), ("explain", {"topic": "cleaning"})))
    a = ask(shop, ai, "do three things")["answer"]
    assert a["agent"]["tools"] == ["explain", "chart_edit"] and a["agent"]["steps"][-1]["ok"] is False
    assert "I stopped at step 2 (chart_edit)" in a["text"] and "Done so far: explain" in a["text"]


def test_a_later_step_can_use_an_earlier_result(shop):
    class Needs(Tool):
        name = "dummy_needs_prior"
        description = "dummy"
        args: dict = {}

        def run(self, args, env, prior):
            return ToolResult(summary="saw " + str(len(prior)) + " earlier step", data={"n": len(prior)})
    registry.register(Needs)
    try:
        ai = FakeAI(P(("explain", {"topic": "trust"}), ("dummy_needs_prior", {})))
        a = ask(shop, ai, "two steps")["answer"]
        assert a["agent"]["steps"][1]["summary"] == "saw 1 earlier step"
    finally:
        registry.unregister("dummy_needs_prior")


# ------------------------------------------------------------------ budget
def test_at_most_two_calls_normally_and_chips_undo_and_exact_titles_skip_the_plan(run):
    rid, wd, res = run
    ai = FakeAI(P(("explain", {"topic": "trust"})))
    chat.ask(wd, res, question="why is trust what it is", llm=ai, allow_ai=True)
    assert ai.calls == 1
    ai = FakeAI(P(("explain", {"topic": "trust"})))
    chips = chat.info(wd, res, False)["chips"]
    chat.ask(wd, res, chip=chips[0]["id"], llm=ai, allow_ai=True)
    assert ai.calls == 0                                                              # a chip carries its own query
    title = ve.catalogue(res)["items"]["strategy:s1"]["title"]
    t = chat.ask(wd, res, question=f"hide {title}", llm=ai, allow_ai=True)
    assert ai.calls == 0 and t["answer"]["view"]["changed"] and "strategy:s1" in ve.normalise(store.get_view_state(wd))["hidden"]     # exact title: no plan call
    t = chat.ask(wd, res, question="undo", llm=ai, allow_ai=True)
    assert ai.calls == 0 and not ve.normalise(store.get_view_state(wd))["hidden"]


def test_the_per_run_question_cap_still_applies(shop, monkeypatch):
    monkeypatch.setenv("LENS_MAX_QUESTIONS", "1")
    ask(shop, FakeAI(P(("explain", {"topic": "trust"}))), "one")
    a = ask(shop, FakeAI(P(("explain", {"topic": "trust"}))), "two")["answer"]
    assert a["status"] == "limit"


# ------------------------------------------------------------------ one Undo for a multi-step view edit
def test_one_undo_reverts_a_multi_step_view_edit(run):
    rid, wd, res = run
    ai = FakeAI(P(("view_edit", {"commands": [{"cmd": "set_mode", "tab": "insights", "mode": "short"}, {"cmd": "hide", "id": "insights:card:f4"}]}),
                  ("view_edit", {"commands": [{"cmd": "pin", "id": "strategy:s1"}, {"cmd": "rename", "id": "strategy:s1", "title": "Mine"}]})))
    a = chat.ask(wd, res, question="tidy things up in three ways", llm=ai, allow_ai=True)["answer"]
    st = ve.normalise(store.get_view_state(wd))
    assert a["view"]["changed"] and st["short"] == {"insights": True} and st["hidden"] == ["insights:card:f4"] and st["pinned"] == ["strategy:s1"] and st["titles"]
    assert len(st["history"]) == 1                                                    # the whole request is ONE step
    chat.ask(wd, res, question="undo", llm=ai, allow_ai=True)
    assert ve._core(ve.normalise(store.get_view_state(wd))) == ve._core(ve.empty())


def test_a_group_can_be_hidden_by_type(run):
    rid, wd, res = run
    ai = FakeAI(P(("view_edit", {"commands": [{"cmd": "hide", "kind": "chart", "tab": "insights"}]})))
    a = chat.ask(wd, res, question="drop every picture on the insights page", llm=ai, allow_ai=True)["answer"]
    hidden = ve.normalise(store.get_view_state(wd))["hidden"]
    assert hidden and all(h.startswith("insights:chart:") for h in hidden) and a["view"]["changed"]
    a = chat.ask(wd, res, question="and the visual groups that do not exist", llm=FakeAI(P(("view_edit", {"commands": [{"cmd": "hide", "kind": "widgets"}]}))), allow_ai=True)["answer"]
    assert a["label"] == "Not changed"


# ------------------------------------------------------------------ verification
def test_a_framing_sentence_with_an_unverified_figure_is_dropped(shop):
    ai = FakeAI(P(("explain", {"topic": "trust"}), reply="Revenue grew 47% last year [r1]."))
    a = ask(shop, ai, "how sure are we")["answer"]
    assert "47" not in a["text"] and any("left out" in n for n in a["how"]["notes"])
    ai = FakeAI(P(("explain", {"topic": "trust"}), reply="Here is what the report says about trust."))
    a = ask(shop, ai, "how sure are we")["answer"]
    assert a["text"].startswith("Here is what the report says")


def test_verify_checks_figures_against_tool_facts(shop):
    from app.lens.agent import make_env
    rid, wd, result, df = shop
    d, ctx = chat.load_data(wd, result)
    env = make_env(wd, result, d, ctx, "x", "", None, False, None, None)
    fid, _ = verify.facts_from_table("q", env, ["City", "Total"], [["Pune", "1,570"]], "Total amount by city")
    assert verify.check_sentence(f"Pune totals 1,570 in amount [{fid}].", env.facts)[1] is None
    assert verify.check_sentence(f"Pune totals 1,571 in amount [{fid}].", env.facts)[1]
    assert verify.check_sentence("Pune totals 1,570 in amount.", env.facts)[1]            # a figure with no fact id
    assert verify.check_sentence("Pune is a city.", env.facts)[1] is None


# ------------------------------------------------------------------ the prompt
def test_the_plan_prompt_has_the_context_and_no_rows(run):
    rid, wd, res = run
    ai = FakeAI(P(("explain", {"topic": "tab"})))
    chat.ask(wd, res, question="what is here", llm=ai, allow_ai=True, tab="Insights", focus={"id": "insights:card:f4"})
    pr = ai.prompts[0]
    for part in ("CURRENT TAB: Insights", "VISIBLE ITEMS", "FOCUS ITEM", "COLUMNS:", "REPORT RECORDS", "PREVIOUS TURNS", "SQL VIEW", "MATCHING VALUES", "insights:card:f4"):
        assert part in pr, part
    import pandas as pd
    df = pd.read_pickle(wd / "clean.pkl")
    sample = df.iloc[0]
    assert not any(str(v) in pr for v in df["revenue"].head(5) if len(str(v)) > 5)          # row values are not in the prompt
    sysmsg = planner.system_prompt()
    for t in registry.load_tools():
        assert f'"tool":"{t}"' in sysmsg


# ------------------------------------------------------------------ the registry
def test_adding_a_tool_needs_only_a_module_and_a_registry_entry(shop):
    class Echo(Tool):
        name = "dummy_echo"
        description = "Repeats a word (a test tool)."
        args = {"word": {"type": "str", "required": True}}

        def run(self, args, env, prior):
            from app.lens import reports
            ans, chips = reports._base("app", f"Echo: {args['word']}", "Echo", {"operation": "echo"}, [], 0)
            return ToolResult(summary="echoed", answer=ans)
    registry.register(Echo)
    try:
        assert "dummy_echo" in planner.system_prompt()
        a = ask(shop, FakeAI(P(("dummy_echo", {"word": "hello"}))), "echo hello")["answer"]
        assert a["text"].startswith("Echo: hello") and a["agent"]["tools"] == ["dummy_echo"]
        assert planner.validate(P(("dummy_echo", {})), chat.agent.make_env(shop[1], shop[2], shop[3], chat.load_data(shop[1], shop[2])[1], "x", "", None, True, None, None))[1]       # its schema is enforced
    finally:
        registry.unregister("dummy_echo")
    assert "dummy_echo" not in planner.system_prompt()


def test_every_tool_has_a_name_description_and_schema():
    for name, t in registry.load_tools().items():
        assert t.name == name and t.description and isinstance(t.args, dict) and t.help().startswith("{")


# ------------------------------------------------------------------ the tabs
def tab_ids_in_code() -> list[str]:
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "tabs.ts").read_text(encoding="utf-8")
    block = src.split("TAB_LABEL")[1].split("};")[0]
    return re.findall(r"^\s*(\w+):", block, re.M)


def test_every_tab_exposes_items_with_stable_ids_and_titles(run):
    rid, wd, res = run
    cat = ve.catalogue(res)
    tabs = tab_ids_in_code()
    assert len(tabs) >= 10 and set(tabs) <= set(ve.TABS), set(tabs) - set(ve.TABS)         # a tab added to the interface must be added to the catalogue
    for t in tabs:
        mine = [i for i in cat["items"].values() if i["tab"] == t]
        assert mine, f"tab {t} exposes no items"
        assert all(i["id"].startswith(t + ":") and i["title"] for i in mine), t
    assert ve.catalogue(res) == cat                                                   # stable


def test_every_tab_works_for_focus_view_edits_and_questions(run):
    rid, wd, res = run
    cat = ve.catalogue(res)
    for t in ve.TABS:
        first = next(i for i in cat["items"].values() if i["tab"] == t and i["kind"] != "section")
        ctx = ve.item_context(res, first["id"], cat)
        assert ctx and ctx["text"] and ctx["title"]
        new, _ = ve.apply(res, ve.empty(), {"cmd": "hide", "id": first["id"]}, cat)
        assert first["id"] in ve.resolve(res, new, cat)["tabs"][t]["hidden"]


# ------------------------------------------------------------------ the no-AI fallback keeps the earlier behaviour
def test_without_an_ai_the_rule_router_still_answers_and_chips_run(shop):
    a = ask(shop, None, "summarise the report")["answer"]
    assert a["kind"] == "summary" and a["ai_calls"] == 0
    a = ask(shop, None, "what did you clean")["answer"]
    assert a["kind"] == "app"
    a = ask(shop, None, "something nobody can answer without a model")["answer"]
    assert a["status"] == "unavailable" and "suggested questions only" in a["text"]
    rid, wd, _, _ = shop
    assert "something nobody can answer" in (wd / "lens_unmet.jsonl").read_text()
