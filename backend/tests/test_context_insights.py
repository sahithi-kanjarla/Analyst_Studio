"""Optional business context, explore mode, insights, strategies, new chart tools. No network, no API key."""
import json
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np
import pandas as pd
import pytest

from app import context as ctxmod
from app import insights as ins
from app import runner, tools
from app.config import RUNS_DIR


# ----------------------------------------------------------------- data
def churn_df(n=800, seed=3):
    rng = np.random.default_rng(seed)
    tenure = rng.integers(1, 72, n)
    contract = rng.choice(["Month-to-month", "One year", "Two year"], n, p=[0.55, 0.25, 0.2])
    p = np.where(contract == "Month-to-month", 0.40, 0.07) * np.where(tenure < 12, 1.5, 0.7)
    churn = np.where(rng.random(n) < p, "Yes", "No")
    return pd.DataFrame({"customer_id": range(n), "tenure_months": tenure, "contract": contract, "monthly_charges": rng.normal(70, 15, n).round(2), "churn": churn})


def ticket_df(n=500, seed=4):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"tickets": rng.poisson(5, n), "handle_time": rng.normal(12, 3, n).round(1), "channel": rng.choice(["email", "chat", "phone"], n)})


def noise_df(n=400, seed=5):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"a": rng.normal(size=n), "b": rng.normal(size=n), "grp": rng.choice(list("ABC"), n), "grp2": rng.choice(list("XYZ"), n)})


def go(df, goal, context=None, name="d.csv"):
    rid = runner.create_run(goal, name, df.to_csv(index=False).encode(), context=context)
    runner.start_run(rid).join(timeout=180)
    assert runner.read_status(rid)["status"] == "done"
    return json.loads((RUNS_DIR / rid / "result.json").read_text())


# ----------------------------------------------------------------- context
def test_context_normalisation_and_industry():
    c = ctxmod.normalise_context({"company": "  Acme  ", "kpis": "revenue, churn ;  nps", "currency": "₹", "description": "We sell subscription software to small businesses"})
    assert c["company"] == "Acme" and c["kpis"] == ["revenue", "churn", "nps"] and c["currency"] == "₹"
    assert c["industry_key"] == "saas"
    assert ctxmod.normalise_context({"currency": "XYZ"})["currency"] == ""
    assert not ctxmod.has_context(ctxmod.normalise_context({})) and ctxmod.has_context(c)
    assert len(ctxmod.normalise_context({"notes": "x" * 5000})["notes"]) <= 1500


def test_context_steers_target_choice():
    df = ticket_df()
    base = go(df, "Understand this data")
    steered = go(df, "Understand this data", {"kpis": "average handle time"}, name="e.csv")
    assert steered["plan"]["target"] == "handle_time"
    assert base["plan"]["target"] in ("tickets", "handle_time")


def test_explore_mode_without_goal():
    rng = np.random.default_rng(11)
    n = 600
    df = pd.DataFrame({"region": rng.choice(list("NSEW"), n), "tier": rng.choice(["gold", "silver", "bronze"], n), "channel": rng.choice(["web", "store", "app"], n),
                       "date": pd.date_range("2024-01-01", periods=n, freq="D"), "age": rng.integers(18, 70, n), "visits": rng.poisson(4, n),
                       "revenue": rng.normal(100, 20, n) + rng.choice([0, 30], n)})
    res = go(df, "", {"explore": True})
    assert res["plan"]["explore"] is True
    assert len(res["plan"]["analyses"]) >= 6
    assert "exploratory" in res["report_md"].lower()


# ----------------------------------------------------------------- insights
@pytest.fixture(scope="module")
def churn_run():
    return go(churn_df(), "Why are customers leaving?", {"company": "Nimbus", "kpis": "churn", "currency": "$"})


def test_every_finding_has_a_complete_insight(churn_run):
    assert churn_run["findings"]
    for f in churn_run["findings"]:
        i = f["insight"]
        assert i["headline"] and i["observation"]
        assert i["confidence"]["level"] in {"high", "medium", "low", "exploratory", "descriptive"} and i["confidence"]["reasons"]
        assert isinstance(i["hypotheses"], list) and isinstance(i["check_next"], list)


def test_descriptive_tools_are_not_called_low_confidence(churn_run):
    for f in churn_run["findings"]:
        if f["tool"] in ("pareto", "distribution", "outliers", "category_share"):
            assert f["insight"]["confidence"]["level"] == "descriptive"


def test_numbers_guard():
    allowed = ins.allowed_numbers("Churn was 37.6% versus 6.2%, n=800, p=0.0004")
    assert ins.numbers_ok("Churn is 37.6% for month-to-month.", allowed)
    assert not ins.numbers_ok("Churn would fall by 83% if fixed.", allowed)
    # Stricter rule: small integers get no free pass, and a year passes only if it appears in the facts.
    assert not ins.numbers_ok("Review within 3 steps.", allowed)        # "3" is a claim about the data unless it was given
    assert not ins.numbers_ok("Review it in 2026.", allowed)            # 2026 is not in the facts, so it could be invented
    assert ins.numbers_ok("Review it in 2026.", ins.allowed_numbers("Churn was 37.6% in 2026"))


def test_key_metrics_and_report_sections(churn_run):
    labels = [m["label"] for m in churn_run["key_metrics"]]
    assert any("Rows" in l for l in labels)
    md = churn_run["report_md"]
    for h in ("Executive summary", "Evidence", "Strategy and Recommendations", "About the business"):
        assert h in md
    assert "Nimbus" in md


# ----------------------------------------------------------------- strategies
def test_strategies_are_grounded_in_findings(churn_run):
    ids = {f["id"] for f in churn_run["findings"]}
    backed = [s for s in churn_run["strategies"] if s["kind"] == "data"]
    assert backed
    for s in backed:
        assert s["evidence"] and set(s["evidence"]) <= ids
        assert s["actions"] and s["how_to_test"] and s["kpis"]
        assert s["impact"] in ("High", "Medium", "Low") and s["effort"] in ("High", "Medium", "Low")
    assert [s["rank"] for s in churn_run["strategies"]] == list(range(1, len(churn_run["strategies"]) + 1))
    assert any(("tenure" in s["title"]) or ("contract" in s["title"]) for s in backed)  # the real drivers in the churn sample


def test_noise_gives_no_data_backed_strategy():
    res = go(noise_df(), "What drives a?")
    assert not [s for s in res["strategies"] if s["kind"] == "data"]
    assert "No strong" in res["report_md"]


def test_missing_kpi_is_flagged_as_data_gap():
    res = go(churn_df(), "Why do customers leave?", {"kpis": "net promoter score"}, name="g.csv")
    assert any("net promoter" in (g["title"] + g["detail"]).lower() for g in res["data_gaps"])


# ----------------------------------------------------------------- LLM paths (fake model)
class Msg:
    def __init__(self, c):
        self.content = c


class EnrichLLM:
    """Answers only the new insight/strategy prompts; one answer is good, one invents a number."""
    def __init__(self, state_ids, strat_ids):
        self.fi, self.si = state_ids, strat_ids

    def invoke(self, messages):
        system = messages[0][1]
        if "explaining analysis results" in system:
            return Msg(json.dumps({"insights": [
                {"id": self.fi[0], "why_it_matters": "Shorter-tenure accounts are the group to watch for retention work.", "possible_explanations": ["Onboarding may be weaker in the first months."], "check_next": ["Review onboarding for accounts under a year."]},
                {"id": self.fi[1], "why_it_matters": "Fixing this would raise revenue by 912% across the whole base.", "possible_explanations": [], "check_next": []},
                {"id": "ghost", "why_it_matters": "This finding does not exist at all, ignore it.", "possible_explanations": [], "check_next": []}]}))
        if "senior strategy consultant" in system:
            return Msg(json.dumps({"strategies": [
                {"id": self.si[0], "title": "Pilot an early-warning programme for new subscribers", "objective": "Catch at-risk new subscribers before they cancel.",
                 "actions": ["Flag accounts under a year old each week.", "Assign a retention owner to each flagged account."], "risks": ["Flags may be noisy at first."], "kpis": ["Churn rate in flagged accounts"],
                 "how_to_test": "Contact a random half of flagged accounts for six weeks and compare churn with the untouched half."},
                {"id": "ghost", "title": "Imaginary strategy that is not a candidate", "objective": "Should be ignored entirely."}]}))
        return Msg("not json")


def test_llm_enrichment_is_validated(churn_run):
    from app import strategy
    state = {"goal": "Why churn?", "context": churn_run["context"], "findings": [json.loads(json.dumps(f)) for f in churn_run["findings"]],
             "plan": churn_run["plan"], "profile": {}, "data_gaps": []}
    fi = [f["id"] for f in state["findings"] if f["tool"] != "robust_compare"]
    strategies = [json.loads(json.dumps(s)) for s in churn_run["strategies"]]
    si = [s["id"] for s in strategies if s["kind"] == "data"]
    llm = EnrichLLM(fi, si)
    before = state["findings"][1]["insight"]["why_it_matters"]
    n = ins.enrich_with_llm(llm, state)
    assert n == 1 and state["findings"][0]["insight"]["source"] == "llm"
    assert state["findings"][1]["insight"]["why_it_matters"] == before          # invented 912% rejected
    m = strategy.refine_with_llm(llm, state, strategies)
    assert m == 1 and strategies[0]["source"] == "llm" and strategies[0]["title"].startswith("Pilot an early-warning")
    assert ins.enrich_with_llm(None, state) == 0


def _rupee_state(churn_run):
    """Findings whose only source for the amount is `stats` (as a rupee string), so a \u20b9 escape in the dumped stats would hide it."""
    state = {"goal": "g", "context": {}, "findings": [json.loads(json.dumps(f)) for f in churn_run["findings"]], "plan": churn_run["plan"], "profile": {}, "data_gaps": []}
    state["findings"][0]["stats"] = {"avg_label": "₹8,216"}
    return state


def test_insight_enrichment_accepts_rupee_amount_from_stats(churn_run):
    state = _rupee_state(churn_run)
    f = state["findings"][0]
    why = "Accounts average ₹8,216 each, so the group is worth watching."
    assert "8,216" not in f["text"] + f["title"]
    class L:
        def invoke(self, m):
            return Msg(json.dumps({"insights": [{"id": f["id"], "why_it_matters": why, "possible_explanations": [], "check_next": []}]}, ensure_ascii=False))
    assert ins.enrich_with_llm(L(), state) == 1 and f["insight"]["why_it_matters"] == why


def test_strategy_refinement_accepts_rupee_amount_from_stats(churn_run):
    from app import strategy
    state = _rupee_state(churn_run)
    strategies = [json.loads(json.dumps(s)) for s in churn_run["strategies"]]
    s = next(x for x in strategies if x["kind"] == "data")
    state["findings"][0]["id"] = s["evidence"][0]
    state["findings"][0]["text"] = "No amount here."
    class L:
        def invoke(self, m):
            return Msg(json.dumps({"strategies": [{"id": s["id"], "title": f"Protect churn accounts averaging ₹8,216 [{s['evidence'][0]}] each", "objective": "x" * 20, "actions": [], "risks": [], "kpis": []}]}, ensure_ascii=False))
    assert strategy.refine_with_llm(L(), state, strategies) == 1 and "8,216" in s["title"]


def test_garbage_llm_leaves_rules_output_untouched(churn_run):
    class Bad:
        def invoke(self, m):
            return Msg("Sorry, no JSON here.")
    state = {"goal": "g", "context": {}, "findings": [json.loads(json.dumps(f)) for f in churn_run["findings"]], "plan": churn_run["plan"], "profile": {}, "data_gaps": []}
    assert ins.enrich_with_llm(Bad(), state) == 0


# ----------------------------------------------------------------- new chart tools
def test_heatmap_and_segment_trend(tmp_path):
    rng = np.random.default_rng(7)
    n = 900
    df = pd.DataFrame({"region": rng.choice(list("NSEW"), n), "tier": rng.choice(["gold", "silver", "bronze"], n),
                       "date": pd.date_range("2024-01-01", periods=n, freq="D"), "revenue": rng.normal(100, 20, n)})
    df.loc[df.region.eq("N") & df.tier.eq("gold"), "revenue"] += 60
    df["revenue"] += np.where(df.region.eq("N"), np.arange(n) * 0.08, 0)
    ctx = {"charts_dir": tmp_path, "currency": "$"}
    h = tools.heatmap(df, ctx, "revenue", "region", "tier")
    assert h and h["significant"] is None                      # cell gaps are not individually tested, so it is exploratory
    assert (tmp_path / h["chart"].split("/")[-1]).exists()
    s = tools.segment_trend(df, ctx, "revenue", "date", "region")
    assert s and (tmp_path / s["chart"].split("/")[-1]).exists() and "survives_correction" in json.dumps(s, default=str)
    assert tools.heatmap(df, ctx, "revenue", "region", "region") is None


def test_validate_spec_for_new_tools():
    roles = {"revenue": "numeric", "region": "categorical", "tier": "categorical", "date": "date"}
    uniq = {"revenue": 500, "region": 4, "tier": 3, "date": 900}
    ok, _ = tools.validate_spec({"tool": "heatmap", "params": {"target": "revenue", "group_a": "region", "group_b": "tier"}}, roles, uniq)
    assert ok
    bad, why = tools.validate_spec({"tool": "heatmap", "params": {"target": "revenue", "group_a": "region", "group_b": "region"}}, roles, uniq)
    assert bad is None
    bad, _ = tools.validate_spec({"tool": "segment_trend", "params": {"target": "revenue", "date": "region", "group": "tier"}}, roles, uniq)
    assert bad is None


# ----------------------------------------------------------------- API
def test_api_accepts_context_fields_and_explore():
    import time
    from fastapi.testclient import TestClient
    from app.main import app
    cl = TestClient(app)
    csv = churn_df().to_csv(index=False).encode()
    r = cl.post("/api/runs", data={"goal": "", "explore": "true", "company": "Nimbus", "industry": "SaaS / Software", "kpis": "churn, nps", "currency": "₹"}, files=[("files", ("c.csv", csv))])
    assert r.status_code == 200
    rid = r.json()["run_id"]
    for _ in range(120):
        st = cl.get(f"/api/runs/{rid}").json()
        if st["status"] in ("done", "error"):
            break
        time.sleep(0.5)
    assert st["status"] == "done" and st["context"]["company"] == "Nimbus" and st["context"]["explore"] is True
    assert st["context"]["kpis"] == ["churn", "nps"]
    assert cl.post("/api/runs", data={"goal": ""}, files=[("files", ("c.csv", csv))]).status_code in (200, 400)
