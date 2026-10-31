"""A missing AI package is said so, in the header and the run log, instead of a silent rules-only run."""
import builtins
from pathlib import Path

from app import llm


def test_missing_groq_package_gives_a_clear_label(monkeypatch):
    real = builtins.__import__

    def fake(name, *a, **k):
        if name.startswith("langchain_groq"):
            raise ImportError("No module named 'langchain_groq'")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "x")
    monkeypatch.setattr(llm, "_cache", {})
    model, label = llm.get_llm()
    assert model is None and "AI package missing" in label and "langchain-groq" in label and "pip install" in label
    assert llm.package_notice(label).startswith("AI unavailable: AI package missing") and llm.package_notice("groq:x") == ""


def test_groq_package_is_a_normal_requirement():
    lines = [l.strip() for l in (Path(__file__).parents[1] / "requirements.txt").read_text().splitlines()]
    assert any(l.startswith("langchain-groq") for l in lines)
