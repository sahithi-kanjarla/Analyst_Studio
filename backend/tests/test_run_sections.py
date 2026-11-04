"""Lens can read what the run saved about itself, through three read-only tools the model chooses from their descriptions. Fake model, no network, no rows read."""
import os

os.environ["LLM_PROVIDER"] = "none"

from app.lens import chat
from app.lens.agent import registry
from app.lens.agent.tools import run_sections as rs
from tests.test_agent_custom import shop  # noqa: F401  (fixture)
from tests.test_agent_loop import P, ask
from tests.test_lens import FakeAI


def test_the_three_tools_are_in_the_closed_registry_and_read_only():
    for name in ("run_sections", "run_section", "run_search"):
        t = registry.REGISTRY[name]
        assert not t.needs_ai and "Read-only" in t.description


def test_sections_are_built_from_the_saved_run_only(shop):
    rid, wd, result, df = shop
    secs = rs.build_sections(wd, result)
    for k in ("business_context", "relations", "cleaning_log", "understanding", "questions_and_answers", "definitions", "plan", "findings", "headlines", "key_figures", "ideas", "charts", "hidden_and_pinned", "ai_usage"):
        assert k in secs and secs[k][0]
    assert secs["cleaning_log"][1] == [l for l in secs["cleaning_log"][1] if l.strip()]
    assert len(secs["cleaning_log"][1]) == len(result.get("cleaning_log") or [])


def test_the_model_lists_then_reads_a_section_about_how_the_data_was_set_up(shop):
    ai = FakeAI(P(("run_sections", {}), ("run_section", {"section": "cleaning_log"})))
    a = ask(shop, ai, "summarise how the data was set up (business, sources, cleaning)")["answer"]
    assert ai.calls == 1 and a["agent"]["tools"] == ["run_sections", "run_section"] and a["status"] == "answered"
    assert "cleaning_log" in " ".join(a["lines"]) or a["lines"] or a["text"]


def test_an_unknown_section_is_refused_with_the_list(shop):
    ai = FakeAI(P(("run_section", {"section": "passwords"})), P(("run_section", {"section": "relations"})))
    a = ask(shop, ai, "what is in the passwords section")["answer"]
    assert "There is no section" in ai.prompts[1] and a["agent"]["tools"] == ["run_section"]


def test_search_finds_lines_across_sections(shop):
    rid, wd, result, df = shop
    word = next(w for _, ls in rs.build_sections(wd, result).values() for line in ls for w in line.lower().split() if w.isalpha() and len(w) > 5)
    ai = FakeAI(P(("run_search", {"query": word})))
    a = ask(shop, ai, f"anything about {word}")["answer"]
    assert a["agent"]["tools"] == ["run_search"] and a["lines"] and all(l.startswith("[") for l in a["lines"])
