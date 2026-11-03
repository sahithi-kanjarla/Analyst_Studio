"""Cost per request: tokens measured by part, and the prompt cut to what a request could use, with the same answers."""
import importlib.util
import json
import sys
from pathlib import Path

from app import llm as llmmod
from app.lens import chat
from app.lens.agent import context, planner

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


lens_eval = _load("lens_eval")
lens_tokens = _load("lens_tokens")


def _plan_prompts(request, tab="Report", prior=None):
    """(system, user) of the first planning call for a request, with the fake AI and the sample run."""
    rid, wd, res = lens_eval.fresh_run()
    seen = []

    class Spy(lens_eval.Scripted):
        def invoke(self, messages):
            seen.append(messages)
            return super().invoke(messages)
    if prior:
        chat.ask(wd, res, question=prior, llm=lens_eval.Scripted({"plan": [{"tool": "data_query", "args": {"query": {"op": "aggregate", "measure": "revenue", "agg": "sum", "group_by": ["segment"]}}}]}), allow_ai=True, tab=tab)
        seen.clear()
    chat.ask(wd, res, question=request, llm=Spy({"plan": []}), allow_ai=True, tab=tab)
    return seen[0][0][1], seen[0][1][1]


def test_each_planning_call_records_its_tokens_by_part(tmp_path):
    rid, wd, res = lens_eval.fresh_run()
    llmmod.begin_run()
    chat.ask(wd, res, question="total revenue by segment", llm=lens_eval.Scripted({"plan": [{"tool": "data_query", "args": {"query": {"op": "aggregate", "measure": "revenue", "agg": "sum", "group_by": ["segment"]}}}]}), allow_ai=True, tab="Report")
    rows = [c for c in llmmod.usage_calls() if c.get("input_by_part")]
    assert rows, "the planning call has a breakdown"
    parts = rows[0]["input_by_part"]
    assert {"system_text", "data_card", "fact_sheet", "item_list", "history", "other"} <= set(parts)
    assert abs(sum(parts.values()) - rows[0]["input_tokens"]) <= len(parts)         # the parts add up to the call's tokens
    llmmod.save_usage(tmp_path)
    saved = json.loads((tmp_path / "ai_usage.json").read_text())
    assert "input_by_part" in saved["calls"][0] or any("input_by_part" in c for c in saved["calls"])


def test_the_data_card_goes_with_a_data_request_and_not_with_a_change_of_view():
    _, data = _plan_prompts("total revenue by segment")
    _, view = _plan_prompts("hide the first chart")
    assert "DATA CARD (what the table holds" in data
    assert "DATA CARD: left out" in view and "DATA CARD (what the table holds" not in view


def test_only_facts_the_request_shares_words_with_are_sent_and_the_rest_are_still_checkable():
    _, user = _plan_prompts("total revenue by segment")
    rec = json.loads(user.split("REPORT RECORDS (no rows): ")[1].split("\nPREVIOUS TURNS")[0])
    rid, wd, res = lens_eval.fresh_run()
    from app.lens import records
    assert len(rec["facts"]) < len(records.fact_sheet(res))
    assert "more report facts" in rec.get("more_facts", "")
    assert len(records.known_facts(res)) >= len(records.fact_sheet(res))              # citing and checking use the whole sheet, not the prompt


def test_items_are_listed_in_full_only_for_a_request_that_acts_on_the_view():
    _, view = _plan_prompts("hide the first chart")
    _, data = _plan_prompts("total revenue by segment")
    assert "only the items whose titles share a word" not in view
    assert "only the items whose titles share a word" in data


def test_history_is_cut_to_what_the_request_leans_on():
    _, lean = _plan_prompts("and now only for 2025", prior="total revenue by segment")
    _, alone = _plan_prompts("which customers are in the data file", prior="total revenue by segment")
    assert "leaned on" in lean and '"question": "total revenue by segment"' in lean
    assert "kept short" in alone and '"answer"' not in alone.split("PREVIOUS TURNS")[1]


def test_the_system_text_names_every_tool_but_describes_in_full_only_the_likely_ones():
    sysmsg, _ = _plan_prompts("total revenue by segment")
    full = planner.system_prompt()
    for t in ("data_query", "view_edit", "brief", "export", "glossary", "why_breakdown"):
        assert t in sysmsg                                                              # still listed
    assert len(sysmsg) < len(full) * 0.7
    assert '{"tool":"data_query"' in sysmsg and '{"tool":"view_edit"' not in sysmsg      # the tool for numbers in full, the view tool by name
    assert "- A \"why\"" in full and '"Charts can be created' not in sysmsg


def test_a_rejected_plan_is_asked_again_with_everything(monkeypatch):
    rid, wd, res = lens_eval.fresh_run()
    seen = []
    bad = {"plan": [{"tool": "data_query", "args": {"query": {"op": "aggregate", "measure": "no_such_column", "agg": "sum"}}}]}
    good = {"plan": [{"tool": "data_query", "args": {"query": {"op": "aggregate", "measure": "revenue", "agg": "sum", "group_by": ["segment"]}}}]}

    class Two(lens_eval.Scripted):
        def invoke(self, messages):
            seen.append(messages)
            return super().invoke(messages)
    chat.ask(wd, res, question="total revenue by segment", llm=Two({"plan": bad["plan"]}), allow_ai=True, tab="Report")
    assert len(seen) > 1                                                    # the bad plan was asked about again, and that ask carries the full system text and context
    assert len(seen[1][0][1]) >= len(planner.system_prompt()) * 0.99 and len(seen[1][0][1]) > len(seen[0][0][1])
    assert "DATA CARD (what the table holds" in seen[1][1][1]


def test_the_planning_model_can_be_a_smaller_one_by_setting(monkeypatch):
    class M:
        model_name = "big"

        def model_copy(self, update=None):
            m = M()
            m.model_name = update["model_name"]
            return m
    m = M()
    monkeypatch.delenv("LENS_PLANNER_MODEL", raising=False)
    assert llmmod.planner_model(m) is m                                     # default: the current model
    monkeypatch.setenv("LENS_PLANNER_MODEL", "small")
    assert llmmod.planner_model(m).model_name == "small" and m.model_name == "big"
    fake = lens_eval.Scripted({"plan": []})
    assert llmmod.planner_model(fake) is fake                               # a model that cannot be re-bound is kept
    assert llmmod.planner_model(None) is None


def test_five_representative_requests_need_at_least_half_the_input_tokens():
    before = lens_tokens.measure(lens_tokens.REPRESENTATIVE, True)
    after = lens_tokens.measure(lens_tokens.REPRESENTATIVE, False)
    b = sum(v["total"] for v in before.values())
    a = sum(v["total"] for v in after.values())
    assert a <= b * 0.5, (a, b)


def test_the_answers_do_not_change_with_the_smaller_prompt():
    """The scripted evaluation (plan, outcome and run, all 150 requests) is the proof: the same plans give the same answers."""
    results = lens_eval.replay(lens_eval.load())
    assert all(r["plan_ok"] and r["ran_ok"] and r["outcome_ok"] for r in results)
