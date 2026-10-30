"""'Ask about this report' (CHATBOT=on): the fake AI picks one operation, code checks it and computes every figure. No real model is called."""
import json

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
    """Answers with the operation it was given, as the model would."""
    kwargs = {}

    def __init__(self, choice):
        self.choice = choice

    def invoke(self, messages):
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
    wd, result = run
    return ask.answer(wd, result, q, llm=FakeAsk(choice))


def test_top_n_figures_come_from_code(run, clean):
    out = _ask(run, {"op": "top_n", "measure": "revenue", "group": "Segment", "n": 2})
    assert out["status"] == "answered" and out["op"] == "top_n"
    expected = clean.groupby("Segment")["revenue"].sum().sort_values(ascending=False).head(2)
    top_name, top_value = expected.index[0], expected.iloc[0]
    assert top_name in out["text"] and f"{top_value:,.0f}" in out["text"]
    assert (run[0] / out["chart"]).exists()


def test_compare_two_groups(run, clean):
    a, b = sorted(clean["region"].dropna().unique())[:2]
    out = _ask(run, {"op": "compare", "measure": "revenue", "group": "region", "a": a.lower(), "b": b})
    assert out["status"] == "answered"
    assert f"{clean[clean.region == a].revenue.sum():,.0f}" in out["text"]
    assert f"{clean[clean.region == b].revenue.sum():,.0f}" in out["text"]


def test_trend_over_time_uses_the_date_column(run):
    out = _ask(run, {"op": "trend", "measure": "revenue", "freq": "Q"})
    assert out["status"] == "answered" and out["op"] == "trend" and "by quarter" in out["text"]


def test_share_and_filter_total(run, clean):
    share = _ask(run, {"op": "share", "measure": "revenue", "group": "Segment", "value": "Consumer"})
    assert share["status"] == "answered" and "accounts for" in share["text"]
    flt = _ask(run, {"op": "filter_total", "measure": "revenue", "column": "region", "value": clean["region"].dropna().iloc[0]})
    assert flt["status"] == "answered" and "rows with a total revenue" in flt["text"]


def test_invented_column_is_not_run(run):
    out = _ask(run, {"op": "top_n", "measure": "profit", "group": "Store", "n": 3})
    assert out["status"] == "no_fit" and "not a number column" in out["text"]
    assert len(out["suggestions"]) == 2                  # two questions it can answer


def test_unknown_operation_is_not_run(run):
    out = _ask(run, {"op": "forecast_next_year", "measure": "revenue"})
    assert out["status"] == "no_fit" and len(out["suggestions"]) == 2


def test_value_that_is_not_in_the_data_is_refused(run):
    out = _ask(run, {"op": "share", "measure": "revenue", "group": "Segment", "value": "Atlantis"})
    assert out["status"] == "no_fit" and "not a value" in out["text"]


def test_no_model_says_so(run, monkeypatch):
    monkeypatch.setattr(llm, "get_llm", lambda: (None, "rules-only"))
    wd, result = run
    out = ask.answer(wd, result, "which segment earns the most?")
    assert out["status"] == "unavailable" and "None is configured" in out["text"]


def test_off_by_default_is_not_found(monkeypatch):
    monkeypatch.delenv("CHATBOT", raising=False)
    runner.ensure_demo_run()
    assert client.post(f"/api/runs/{runner.DEMO_RUN_ID}/ask", json={"question": "top stores?"}).status_code == 404
    assert client.get("/api/health").json()["chatbot"] is False


def test_on_answers_real_runs_and_refuses_the_sample(monkeypatch):
    monkeypatch.setenv("CHATBOT", "on")
    monkeypatch.setattr(llm, "get_llm", lambda: (FakeAsk({"op": "top_n", "measure": "revenue", "group": "Segment", "n": 1}), "fake"))
    runner.ensure_demo_run()
    # a copy that is not the sample: the same finished run under a new id
    import shutil
    rid = "5a1e5d3e0002"
    shutil.copytree(runner.run_dir(runner.DEMO_RUN_ID), runner.run_dir(rid), dirs_exist_ok=True)
    r = client.post(f"/api/runs/{rid}/ask", json={"question": "which segment earns the most?"})
    assert r.status_code == 200 and r.json()["status"] == "answered"
    assert r.json()["chart_url"].startswith(f"/api/runs/{rid}/files/charts/ask_")
    assert client.post(f"/api/runs/{runner.DEMO_RUN_ID}/ask", json={"question": "x y z"}).status_code == 403
    assert client.get("/api/health").json()["chatbot"] is True
