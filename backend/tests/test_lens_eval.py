"""The Lens evaluation set (tests/lens_eval/requests.jsonl) and the replay script, with the scripted fake planner. The real-AI run is `python scripts/lens_eval.py --real` (not run here)."""
import importlib.util
import json
import os
import sys
from collections import Counter
from pathlib import Path

os.environ["LLM_PROVIDER"] = "none"

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("lens_eval", ROOT / "scripts" / "lens_eval.py")
lens_eval = importlib.util.module_from_spec(spec)
sys.modules["lens_eval"] = lens_eval
spec.loader.exec_module(lens_eval)
LINES = lens_eval.load()
REQUIRED = {"data_closed", "custom", "followup", "why", "premise", "summary", "brief", "view_edit", "strategy", "glossary", "general", "impossible", "informal", "chart_edit", "export", "brief_manage", "clarify", "multi", "explain", "catalog"}


def test_the_set_is_big_varied_and_well_formed():
    c = Counter(x["category"] for x in LINES)
    assert len(LINES) >= 80 and c["custom"] >= 25 and c["catalog"] >= 10 and REQUIRED <= set(c)
    assert len({x["id"] for x in LINES}) == len(LINES)
    for x in LINES:
        assert x["request"].strip() and x["tab"] and isinstance(x["visible"], list) and isinstance(x["expected"], list) and "reply" in x, x["id"]
    assert sum(1 for x in LINES if x["category"] == "view_edit" and len(x["expected"][0][1]["commands"]) > 1) >= 4         # multi-step view edits
    assert len({tuple(sorted(b["audience"] for b in x["expected"][0][1]["briefs"])) for x in LINES if x["category"] == "brief"}) >= 8           # many different readers
    assert any(x["category"] == "impossible" and x["expected"] == [] for x in LINES)


def test_every_scripted_plan_is_valid_and_runs_on_the_sample_run():
    results = lens_eval.replay(LINES)
    bad = [r for r in results if not (r["plan_ok"] and r["ran_ok"] and r["outcome_ok"])]
    assert not bad, bad[:3]
    by_id = {x["id"]: x for x in LINES}
    for r in results:         # normally at most 2 AI calls; a brief adds one writing call per brief; a custom analysis may add 2 repairs
        n_briefs = sum(len(a["briefs"]) for t, a in by_id[r["id"]]["expected"] if t == "brief")
        assert r["calls"] <= 2 + max(0, n_briefs - 1), r


def test_the_report_names_categories_and_failures(capsys):
    rs = [{"id": "x", "category": "a", "request": "r", "plan_ok": True, "ran_ok": True, "outcome_ok": True, "expected": [], "planned": [], "status": "answered", "text": "", "calls": 1},
          {"id": "y", "category": "a", "request": "r2", "plan_ok": False, "ran_ok": False, "outcome_ok": False, "outcome": {"kind": "text", "bad_numbers": [7], "internal": []}, "expected": ["t"], "planned": ["u"], "status": "answered", "text": "", "calls": 1}]
    assert lens_eval.report(rs) == 1
    out = capsys.readouterr().out
    assert "50%" in out and "1 failure" in out and "expected ['t']" in out


def test_the_real_ai_mode_refuses_without_a_model():
    with pytest.raises(SystemExit):
        lens_eval.replay(LINES[:1], real=True)


def test_every_request_states_the_outcome_it_expects_and_several_valid_plans_are_marked():
    for x in LINES:
        o = x["outcome"]
        assert o["kinds"] and set(o["kinds"]) <= {"chart", "table", "text", "refusal", "view_change", "brief", "export", "question"}, x["id"]
    several = [x for x in LINES if x.get("acceptable")]
    assert len(several) >= 10 and all(x["several_valid"] for x in several)


def test_a_different_valid_plan_passes_and_a_wrong_outcome_is_caught():
    x = next(x for x in LINES if x.get("acceptable"))
    alt = x["acceptable"][0]
    one = dict(x, expected=[list(s) for s in alt], acceptable=[], reply={"plan": [{"tool": t, "args": a} for t, a in alt]})
    assert lens_eval.replay([one])[0]["plan_ok"]
    wrong = dict(x, outcome={"kinds": ["export"], "figures": True, "no_internal": True})
    r = lens_eval.replay([wrong])[0]
    assert r["plan_ok"] and not r["outcome_ok"] and not r["outcome"]["kind_ok"]


def test_outcome_checks_find_untraceable_figures_and_internal_terms():
    res = {}
    a = {"text": "Revenue is 123,456 in total, found with data_query.", "table": {"shown": [["a", "9"]], "rows": [[1]]}}
    oc = lens_eval.outcome_check(a, {"request": "q", "outcome": {"kinds": ["text"], "figures": True, "no_internal": True}}, res)
    assert oc["bad_numbers"] == ["123456"] and oc["internal"] == ["data_query"] and not oc["ok"]


def test_sample_is_round_robin_across_categories_and_list_calls_no_ai(capsys, monkeypatch):
    pick = lens_eval.choose(LINES, 20)
    assert len(pick) == 20 and len({x["category"] for x in pick}) == 20
    assert lens_eval.choose(LINES, 5) == lens_eval.choose(LINES, 5)
    monkeypatch.setattr(lens_eval, "replay", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    assert lens_eval.main(["--sample", "6", "--list"]) == 0
    out = capsys.readouterr().out
    assert "6 request(s); no AI was called." in out
