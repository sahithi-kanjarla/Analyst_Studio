"""propose(): LLM-first decisions with one repair and a rules fallback, the decision log, and the run-mode label. Fake model, no network."""
import json
import os

os.environ["LLM_PROVIDER"] = "none"

from app import polarity as pol
from app.config import RUNS_DIR
from app.planner import heuristic_plan
from app.profiler import profile_df
from app.propose import derive_mode, propose
from test_pipeline import finished_run, make_data  # noqa: F401  (finished_run is a fixture)

import pandas as pd


class Seq:
    """A fake chat model that answers with the given replies in order (the last one repeats) and remembers what it was asked."""
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def invoke(self, msgs):
        self.prompts.append(msgs[1][1])
        r = self.replies[min(len(self.prompts) - 1, len(self.replies) - 1)]
        return type("R", (), {"content": r})()


def _validator(obj):
    v = obj.get("pick") if isinstance(obj, dict) else None
    return (v, []) if v in ("a", "b") else (None, [f"'{v}' is not one of a, b"])


def run(llm, state=None):
    return propose(llm, "pick", "choose", "VIEW", _validator, lambda: "rule", state)


def test_good_proposal_is_used():
    llm = Seq('{"pick": "a", "why": "because"}')
    r = run(llm)
    assert (r["value"], r["source"], r["why"], r["errors"]) == ("a", "llm", "because", []) and len(llm.prompts) == 1


def test_bad_proposal_is_repaired_once_with_the_errors_sent_back():
    llm = Seq('{"pick": "zzz"}', '{"pick": "b"}')
    r = run(llm)
    assert r["value"] == "b" and r["source"] == "repaired" and len(llm.prompts) == 2
    assert "'zzz' is not one of a, b" in llm.prompts[1] and llm.prompts[1].startswith("VIEW")
    assert r["errors"]


def test_still_bad_falls_back_after_one_repair():
    llm = Seq("not json at all")
    r = run(llm)
    assert r["value"] == "rule" and r["source"] == "fallback" and len(llm.prompts) == 2 and r["errors"]


def test_no_llm_is_rules_only():
    r = run(None)
    assert r["value"] == "rule" and r["source"] == "rules-only" and r["errors"] == []


def test_a_number_from_the_model_is_never_accepted():
    llm = Seq('{"pick": "a", "score": 7}')
    r = run(llm)
    assert r["source"] == "fallback" and r["value"] == "rule"
    assert run(Seq('{"pick": "a", "ok": true}'))["source"] == "llm"      # booleans are not numbers


def test_every_proposal_is_logged():
    st = {}
    run(Seq('{"pick": "a"}'), st)
    run(None, st)
    assert [d["source"] for d in st["decisions"]] == ["llm", "rules-only"]
    assert set(st["decisions"][0]) == {"decision", "source", "chosen", "why", "errors"}


def test_polarity_goes_through_propose_and_is_logged():
    st = {}
    out, src = pol.resolve(Seq('{"measures": {"revenue": "higher", "discount": "lower"}, "why": "discounts cost margin"}'), ["revenue", "discount"], "g", None, st)
    assert src == "llm" and out["discount"] == pol.LOWER
    d = st["decisions"][0]
    assert d["decision"] == "metric direction" and d["source"] == "llm" and d["why"] == "discounts cost margin"
    llm = Seq('{"measures": {"revenue": "banana"}}', '{"measures": {"revenue": "higher", "discount": "lower"}}')
    out, src = pol.resolve(llm, ["revenue", "discount"], "g", None, st)
    assert src == "llm" and st["decisions"][1]["source"] == "repaired"
    out, src = pol.resolve(Seq("nope"), ["discount"], "g", None, st)
    assert src == "rules" and out["discount"] == pol.LOWER and st["decisions"][2]["source"] == "fallback"
    assert pol.resolve(None, ["discount"], "g", None, st)[1] == "rules" and st["decisions"][3]["source"] == "rules-only"


def test_mode_label_from_stage_sources():
    assert derive_mode({"planner": "llm", "polarity": "repaired", "report": "llm"}) == "AI-assisted"
    assert derive_mode({"planner": "rules", "polarity": "fallback", "report": "rules-only"}) == "rules-only mode"
    assert derive_mode({"planner": "llm", "polarity": "fallback", "report": "rules"}) == "mixed"
    assert derive_mode({}) == "rules-only mode"


def test_rules_plan_lists_what_it_skipped():
    df = pd.DataFrame({"order_id": range(100), "revenue": [float(i) for i in range(100)], "note": [f"free text {i}" for i in range(100)]})
    plan = heuristic_plan(df, profile_df(df), "revenue", {}, False, {})
    skipped = {s["column"]: s["reason"] for s in plan["skipped"]}
    assert "order_id" in skipped and skipped["order_id"]


def test_a_rules_only_run_saves_its_mode_and_decisions(finished_run):  # noqa: F811
    res = json.loads((RUNS_DIR / finished_run / "result.json").read_text())
    assert res["run_mode"] == "rules-only mode"
    assert any(d["decision"] == "metric direction" and d["source"] == "rules-only" for d in res["decisions"])
    assert "**Mode:** rules-only mode" in res["report_md"]
    assert res["plan"]["skipped"]
