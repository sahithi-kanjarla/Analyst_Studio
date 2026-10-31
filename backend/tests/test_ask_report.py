"""'Ask about this report': suggested questions run without an AI model, free text goes to a (fake) model, and every figure comes from code."""
import json
import shutil

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import ask, llm, runner
from app.main import app

client = TestClient(app)


class Msg:
    def __init__(self, content):
        self.content = content


class FakeAsk:
    """Answers with the operation it was given, as the model would, and counts its calls."""
    kwargs = {}

    def __init__(self, choice):
        self.choice, self.calls = choice, 0

    def invoke(self, messages):
        self.calls += 1
        return Msg(json.dumps(self.choice))


@pytest.fixture
def run():
    runner.ensure_demo_run()
    wd = runner.run_dir(runner.DEMO_RUN_ID)
    return wd, json.loads((wd / "result.json").read_text(encoding="utf-8"))


@pytest.fixture
def clean(run):
    return pd.read_pickle(run[0] / "clean.pkl")


def _ask(run, choice, q="a question"):
    return ask.answer(run[0], run[1], q, llm=FakeAsk(choice))


def test_chips_are_written_from_this_report(run):
    cs = ask.chips(*run)
    assert 4 <= len(cs) <= 6 and len({c["id"] for c in cs}) == len(cs)
    assert any(c["text"].startswith("Which Segment earns the most revenue") for c in cs)
    assert any(c["text"] == "Is revenue growing?" for c in cs)


def test_every_chip_runs_without_an_ai_model(run, monkeypatch):
    monkeypatch.setattr(llm, "get_llm", lambda: (_ for _ in ()).throw(AssertionError("a chip called the model")))
    for c in ask.chips(*run):
        out = ask.answer(run[0], run[1], "", chip=c["id"], allow_ai=False)
        assert out["status"] == "answered", c
        assert out["how"]["columns"] and (run[0] / out["chart"]).exists()


def test_chip_figure_matches_the_computed_value(run, clean):
    chip = next(c for c in ask.chips(*run) if c["op"]["op"] == "top_n" and c["op"]["group"] == "Segment")
    out = ask.answer(run[0], run[1], "", chip=chip["id"], allow_ai=False)
    expected = clean.groupby("Segment")["revenue"].sum().sort_values(ascending=False)
    assert f"{expected.iloc[0]:,.0f}" in out["text"] and expected.index[0] in out["text"]
    assert out["rows"][0][1] == pytest.approx(expected.iloc[0])


def test_typed_text_that_clearly_matches_a_chip_runs_it_without_the_model(run):
    cs = ask.chips(*run)
    fake = FakeAsk({"op": "none"})
    out = ask.answer(run[0], run[1], cs[0]["text"].lower().rstrip("?"), llm=fake)
    assert out["status"] == "answered" and fake.calls == 0


def test_free_text_without_a_model_offers_the_chips(run):
    out = ask.answer(run[0], run[1], "what is the weather like", allow_ai=False)
    assert out["status"] == "unavailable" and len(out["suggestions"]) >= 4 and "suggested questions" in out["text"]


def test_free_text_with_a_model_is_checked_by_code(run, clean):
    out = _ask(run, {"op": "top_n", "measure": "revenue", "group": "region", "n": 2}, "best two regions?")
    top = clean.groupby("region")["revenue"].sum().sort_values(ascending=False)
    assert out["status"] == "answered" and f"{top.iloc[0]:,.0f}" in out["text"]


def test_unsupported_question_is_refused_politely(run):
    out = _ask(run, {"op": "forecast_next_year", "measure": "revenue"}, "what will sales be next year?")
    assert out["status"] == "no_fit" and len(out["suggestions"]) == 2 and "can't answer" in out["text"]
    out = _ask(run, {"op": "top_n", "measure": "profit", "group": "Store", "n": 3})
    assert out["status"] == "no_fit"
    out = _ask(run, {"op": "share", "measure": "revenue", "group": "Segment", "value": "Atlantis"})
    assert out["status"] == "no_fit" and "not a value" in out["text"]


def test_flag_modes(monkeypatch):
    monkeypatch.setattr(llm, "get_llm", lambda: (None, "rules-only"))
    monkeypatch.delenv("CHATBOT", raising=False)
    assert ask.mode() == "auto" and ask.enabled(demo=True) and not ask.enabled(demo=False)      # auto: the sample always, real runs only with a model
    monkeypatch.setattr(llm, "get_llm", lambda: (object(), "fake"))
    assert ask.enabled(demo=False)
    monkeypatch.setenv("CHATBOT", "off")
    assert not ask.enabled(demo=True) and not ask.enabled(demo=False)
    monkeypatch.setenv("CHATBOT", "on")
    monkeypatch.setattr(llm, "get_llm", lambda: (None, "rules-only"))
    assert ask.enabled(demo=False)


def test_sample_chat_works_for_real_through_the_api(monkeypatch):
    monkeypatch.delenv("CHATBOT", raising=False)
    monkeypatch.setattr(llm, "get_llm", lambda: (None, "rules-only"))      # no API key
    runner.ensure_demo_run()
    rid = runner.DEMO_RUN_ID
    info = client.get(f"/api/runs/{rid}/ask").json()
    assert info["enabled"] and not info["ai"] and len(info["chips"]) >= 4
    r = client.post(f"/api/runs/{rid}/ask", json={"chip": info["chips"][0]["id"]})
    assert r.status_code == 200 and r.json()["answer"]["status"] == "answered" and r.json()["answer"]["chart_url"].startswith(f"/api/runs/{rid}/files/charts/ask_")
    hist = client.get(f"/api/runs/{rid}/ask").json()["history"]
    assert hist and hist[-1]["question"] == info["chips"][0]["text"]
    assert client.patch(f"/api/runs/{rid}/ask/pin", json={"id": hist[-1]["id"], "pinned": True}).json()["pinned"] is True


def test_real_run_with_no_model_and_flag_auto_has_no_chat(monkeypatch):
    monkeypatch.delenv("CHATBOT", raising=False)
    monkeypatch.setattr(llm, "get_llm", lambda: (None, "rules-only"))
    runner.ensure_demo_run()
    rid = "5a1e5d3e0002"
    shutil.copytree(runner.run_dir(runner.DEMO_RUN_ID), runner.run_dir(rid), dirs_exist_ok=True)
    assert client.get(f"/api/runs/{rid}/ask").json()["enabled"] is False
    assert client.post(f"/api/runs/{rid}/ask", json={"question": "top segment?"}).status_code == 404


def test_the_sample_never_calls_the_model_even_when_one_exists(monkeypatch):
    fake = FakeAsk({"op": "top_n", "measure": "revenue", "group": "Segment", "n": 1})
    monkeypatch.setattr(llm, "get_llm", lambda: (fake, "fake"))
    runner.ensure_demo_run()
    r = client.post(f"/api/runs/{runner.DEMO_RUN_ID}/ask", json={"question": "something unrelated entirely"})
    assert r.status_code == 200 and r.json()["answer"]["status"] == "unavailable" and fake.calls == 0
