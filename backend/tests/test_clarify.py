import os
import tempfile

os.environ["LLM_PROVIDER"] = "none"
os.environ["GLOSSARY_PATH"] = os.path.join(tempfile.mkdtemp(), "glossary.json")

import json  # noqa: E402
import time  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import clarify, glossary, llm as llm_mod, runner  # noqa: E402
from app.cleaner import apply_definitions  # noqa: E402
from app.config import RUNS_DIR  # noqa: E402
from app.main import app  # noqa: E402
from app.profiler import profile_df  # noqa: E402

cl = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_glossary():
    p = glossary.glossary_path()
    if p.exists():
        p.unlink()
    yield


def orders(n=400, seed=1):
    """Two date columns, a refund column, a status column with test/cancelled rows and two revenue-like numbers."""
    rng = np.random.default_rng(seed)
    od = pd.Timestamp("2025-01-01") + pd.to_timedelta(rng.integers(0, 300, n), unit="D")
    status = rng.choice(["completed", "completed", "completed", "cancelled", "test"], n)
    revenue = rng.gamma(2, 50, n).round(2)
    refund = np.where(rng.random(n) < 0.15, (revenue * 0.5).round(2), 0.0)
    return pd.DataFrame({"order_date": od.strftime("%Y-%m-%d"), "ship_date": (od + pd.to_timedelta(rng.integers(1, 9, n), unit="D")).strftime("%Y-%m-%d"),
                         "region": rng.choice(["N", "S", "E", "W"], n), "status": status, "revenue": revenue, "refund_amount": refund,
                         "profit": (revenue * rng.uniform(0.1, 0.4, n)).round(2)})


def csv(df):
    return df.to_csv(index=False).encode()


def detect(df, goal="What is going on?", ctx=None, explore=False):
    return clarify.detect_questions(df, profile_df(df), goal, ctx or {}, explore)


def ids(qs):
    return [q["id"] for q in qs if not q.get("auto")]


# ------------------------------------------------------------------ question detection

def test_two_date_columns_ask_which_to_use():
    df = orders()
    q = next(q for q in detect(df) if q["id"] == "date_col")
    assert q["text"] == "Which date should be used for time analysis?"
    assert [o["value"] for o in q["options"]] == ["order_date", "ship_date"] and q["default"] in ("order_date", "ship_date") and q["why"]


def test_single_date_asks_nothing_about_dates():
    assert "date_col" not in ids(detect(orders().drop(columns=["ship_date"])))


def test_goal_naming_a_date_skips_the_date_question():
    qs = detect(orders(), goal="How do revenue trends change by ship date?")
    assert "date_col" not in ids(qs)
    auto = next(q for q in qs if q["id"] == "date_col")
    assert auto["auto"] == "goal" and auto["default"] == "ship_date"


def test_refund_column_asks_how_to_handle_it():
    q = next(q for q in detect(orders()) if q["id"] == "refund:refund_amount")
    assert q["text"] == "How should refund_amount be handled?"
    assert [o["value"] for o in q["options"]] == ["net", "ignore", "separate"] and q["default"] == "net"
    assert q["meta"]["amount"] == "revenue" and q["meta"]["net_col"] == "revenue_net"


def test_negative_amounts_ask_include_or_exclude():
    df = orders().drop(columns=["refund_amount"])
    df.loc[:19, "revenue"] = -df.loc[:19, "revenue"]
    q = next(q for q in detect(df) if q["id"] == "neg:revenue")
    assert [o["value"] for o in q["options"]] == ["include", "exclude"] and q["default"] == "include" and "20" in q["text"]


def test_status_values_cancelled_failed_test():
    qs = {q["id"]: q for q in detect(orders())}
    assert qs["status:status:test"]["text"] == "Include rows where status is test?" and qs["status:status:test"]["default"] == "exclude"   # only test rows are excluded by default
    assert qs["status:status:cancelled"]["default"] == "include"
    assert "status:status:completed" not in qs


def test_main_outcome_asked_when_several_candidates_and_goal_is_silent():
    q = next(q for q in detect(orders(), goal="Explore this data") if q["id"] == "main_outcome")
    assert q["text"] == "Which is the main number you care about?" and {"revenue", "profit"} <= {o["value"] for o in q["options"]}


def test_goal_or_kpi_naming_one_column_skips_the_outcome_question():
    assert "main_outcome" not in ids(detect(orders(), goal="What drives profit?"))
    assert "main_outcome" not in ids(detect(orders(), goal="Explore this data", ctx={"kpis": ["profit"]}))


def test_at_most_six_questions_and_detection_is_pure():
    df = orders()
    for c in ("a_return_value", "b_discount_total"):
        df[c] = np.where(np.arange(len(df)) % 3 == 0, 5.0, 0.0)
    df["status2"] = df["status"]
    before = df.copy()
    qs = detect(df, goal="Explore")
    assert len(ids(qs)) <= clarify.MAX_QUESTIONS == 6
    pd.testing.assert_frame_equal(df, before)


def test_clean_unambiguous_data_has_no_questions():
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"day": pd.date_range("2025-01-01", periods=200).strftime("%Y-%m-%d"), "region": rng.choice(list("NSEW"), 200), "visits": rng.normal(100, 10, 200)})
    assert detect(df, goal="Why do visits change?") == []


# ------------------------------------------------------------------ applying definitions to data

def test_apply_definitions_logs_and_never_edits_original_columns():
    df = orders()
    qs = detect(df, goal="What drives revenue?")
    ans = {q["id"]: q["default"] for q in qs}
    ans["status:status:cancelled"] = "exclude"
    out, log, eff, new = apply_definitions(df, qs, ans)
    n_bad = int(df["status"].isin(["test", "cancelled"]).sum())
    assert len(out) == len(df) - n_bad
    assert new == ["revenue_net"] and "revenue_net" in out.columns
    keep = ~df["status"].isin(["test", "cancelled"]).values
    np.testing.assert_allclose(out["revenue"], df.loc[keep, "revenue"])                      # original values untouched
    np.testing.assert_allclose(out["revenue_net"], (df["revenue"] - df["refund_amount"])[keep])
    assert eff["status:status:test"]["rows"] == int((df["status"] == "test").sum())
    assert eff["refund:refund_amount"]["rows"] == int((out["refund_amount"] != 0).sum())
    assert all(s["step"] == "Definition applied" and s["count"] >= 0 for s in log) and len(log) == 3


# ------------------------------------------------------------------ glossary store

def test_glossary_roundtrip_limits_and_lock_cleanup():
    glossary.merge_answers("Acme Ltd", {"date_col": "ship_date"}, {"date_col": "Which date?"}, notes="fiscal year starts in April", definitions={"active customer": "bought in 90 days"})
    ws = glossary.get_workspace("  ACME   ltd ")
    assert ws["answers"]["date_col"]["value"] == "ship_date" and ws["definitions"] == {"active customer": "bought in 90 days"} and ws["notes"].startswith("fiscal")
    assert not glossary.glossary_path().with_suffix(".json.lock").exists()
    assert glossary.validate_entry({"notes": "x" * 501}) and glossary.validate_entry({"definitions": {"t": "y" * 301}})
    assert glossary.validate_entry({"definitions": {f"t{i}": "d" for i in range(21)}})
    assert glossary.validate_entry({"notes": "ok", "definitions": {"a": "b"}, "answers": {"q": "v"}}) is None
    assert glossary.delete_workspace("acme ltd") and glossary.get_workspace("acme ltd") == {"answers": {}, "definitions": {}, "notes": ""}


# ------------------------------------------------------------------ full runs

def wait(rid, want=("done", "error", "needs_input"), timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = runner.read_status(rid)["status"]
        if st in want:
            return st
        time.sleep(0.3)
    raise AssertionError("timeout")


def start(df, goal="Explore this data", company="Acme", name="orders.csv"):
    r = cl.post("/api/runs", data={"goal": goal, "company": company}, files=[("files", (name, csv(df)))])
    assert r.status_code == 200
    return r.json()["run_id"]


def result(rid):
    return json.loads((RUNS_DIR / rid / "result.json").read_text())


def test_ambiguous_run_pauses_then_answers_change_the_analysis():
    df = orders()
    rid = start(df)
    assert wait(rid) == "needs_input"
    assert not (RUNS_DIR / rid / "result.json").exists()
    q = cl.get(f"/api/runs/{rid}/questions").json()
    qids = [x["id"] for x in q["questions"]]
    assert q["status"] == "needs_input" and q["can_skip"] and {"date_col", "refund:refund_amount", "status:status:test"} <= set(qids)

    assert cl.post(f"/api/runs/{rid}/answers", json={"answers": {"nope": "x"}}).status_code == 400
    assert cl.post(f"/api/runs/{rid}/answers", json={"answers": {"date_col": "bogus"}}).status_code == 400
    assert cl.post(f"/api/runs/{rid}/answers", json={"notes": "x" * 600}).status_code == 422

    body = {"answers": {"date_col": "ship_date", "main_outcome": "profit", "status:status:cancelled": "exclude", "refund:refund_amount": "ignore"},
            "notes": "Revenue is booked at shipment.", "definitions": {"active customer": "ordered in the last 90 days"}}
    assert cl.post(f"/api/runs/{rid}/answers", json=body).status_code == 200
    assert cl.post(f"/api/runs/{rid}/answers", json=body).status_code == 409       # double submit cannot start a second run
    assert wait(rid) == "done"
    res = result(rid)

    assert res["plan"]["date_col"] == "ship_date" and res["plan"]["target"] == "profit"
    assert "refund_amount" not in res["plan"]["relevant_columns"]
    n_cut = int(df["status"].isin(["cancelled", "test"]).sum())
    assert res["profile"]["rows"] == len(df)                                        # profile is of the raw table; the cleaned table is smaller
    clean = pd.read_pickle(RUNS_DIR / rid / "clean.pkl")
    assert len(clean) == len(df) - n_cut
    steps = [s for s in res["cleaning_log"] if s["step"] == "Definition applied"]
    assert any("cancelled" in s["detail"] and s["count"] == int((df["status"] == "cancelled").sum()) for s in steps)
    assert "## Definitions used" in res["report_md"] and "Rows affected" in res["report_md"] and "ordered in the last 90 days" in res["report_md"]
    by = {d["id"]: d for d in res["applied_definitions"]}
    assert by["date_col"]["source"] == "user" and by["status:status:test"]["source"] == "user"   # Continue confirms the pre-filled form
    assert by["status:status:cancelled"]["rows"] == int((df["status"] == "cancelled").sum())
    # timeline: no duplicated stage events after the resume
    evs = [json.loads(line) for line in (RUNS_DIR / rid / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(e["agent"] == "Profiler" for e in evs) == sum(e["agent"] == "Profiler" for e in evs[: next(i for i, e in enumerate(evs) if e["agent"] == "Planner")]) <= 2

    # a second run for the same company is pre-filled from the glossary and is not blocked
    rid2 = start(df)
    assert wait(rid2) == "done"
    by2 = {d["id"]: d for d in result(rid2)["applied_definitions"]}
    assert by2["date_col"]["answer"] == "ship_date" and by2["date_col"]["source"] == "glossary"
    assert result(rid2)["plan"]["target"] == "profit"
    saved = cl.get("/api/glossary", params={"workspace": "acme"}).json()
    assert saved["answers"]["date_col"]["value"] == "ship_date" and saved["definitions"]["active customer"].startswith("ordered")


def test_glossary_prefills_partially_answered_questions():
    df = orders()
    glossary.merge_answers("beta", {"date_col": "order_date"}, {})
    rid = start(df, company="Beta")
    assert wait(rid) == "needs_input"                                              # other questions still open
    q = {x["id"]: x for x in cl.get(f"/api/runs/{rid}/questions").json()["questions"]}
    assert q["date_col"]["prefill"] == "order_date" and q["date_col"]["prefill_source"] == "glossary"
    assert q["status:status:test"]["prefill_source"] == "default"


def test_skip_uses_defaults_and_completes():
    df = orders()
    rid = start(df, company="Gamma")
    assert wait(rid) == "needs_input"
    assert cl.post(f"/api/runs/{rid}/answers", json={"skip": True, "answers": {"date_col": "ship_date"}}).status_code == 200
    assert wait(rid) == "done"
    res = result(rid)
    assert all(d["source"] in ("default", "goal") for d in res["applied_definitions"])
    assert next(d for d in res["applied_definitions"] if d["id"] == "status:status:test")["answer"] == "exclude"
    assert len(pd.read_pickle(RUNS_DIR / rid / "clean.pkl")) == len(df) - int((df["status"] == "test").sum())
    assert glossary.get_workspace("gamma")["answers"] == {}     # skipping saves nothing


def test_unambiguous_run_never_blocks_and_has_no_definitions_section():
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"day": pd.date_range("2025-01-01", periods=240).strftime("%Y-%m-%d"), "region": rng.choice(list("NSEW"), 240), "visits": rng.normal(100, 10, 240)})
    rid = start(df, goal="Why do visits change over time?", company="")
    assert wait(rid) == "done"
    assert not (RUNS_DIR / rid / "questions.json").exists()
    assert "Definitions used" not in result(rid)["report_md"]
    assert cl.get(f"/api/runs/{rid}/questions").status_code == 404
    assert cl.post(f"/api/runs/{rid}/answers", json={}).status_code == 404   # nothing to answer


def test_glossary_endpoints():
    assert cl.get("/api/glossary").json() == {"workspaces": {}}
    r = cl.put("/api/glossary", params={"workspace": "Delta"}, json={"answers": {"date_col": "ship_date"}, "definitions": {"churn": "no order in 6 months"}, "notes": "n"})
    assert r.status_code == 200 and r.json()["workspace"] == "delta" and r.json()["answers"]["date_col"]["value"] == "ship_date"
    assert cl.get("/api/glossary", params={"workspace": "delta"}).json()["definitions"] == {"churn": "no order in 6 months"}
    assert cl.put("/api/glossary", params={"workspace": "delta"}, json={"notes": "x" * 501}).status_code == 422
    edited = cl.get("/api/glossary", params={"workspace": "delta"}).json()
    edited["definitions"].pop("churn")
    assert cl.put("/api/glossary", params={"workspace": "delta"}, json=edited).json()["definitions"] == {}
    assert cl.delete("/api/glossary", params={"workspace": "delta"}).status_code == 200
    assert cl.delete("/api/glossary", params={"workspace": "delta"}).status_code == 404


# ------------------------------------------------------------------ LLM enrichment

class Msg:
    def __init__(self, content):
        self.content = content


class QuestionLLM:
    """Proposes extra questions, one valid and several that mention columns which do not exist; every other agent falls back to rules."""

    def __init__(self, questions):
        self.questions = questions
        self.seen_prompt = ""

    def invoke(self, messages):
        if "Clarifying-question assistant" in messages[0][1]:
            self.seen_prompt = messages[1][1]
            return Msg(json.dumps({"questions": self.questions}))
        return Msg("not json")


GOOD = {"text": "Does region N include the online store?", "options": ["Yes", "No"], "default": "Yes", "why": "Region labels are ambiguous.", "columns": ["region"]}
GHOSTS = [
    {"text": "Is ghost_col in dollars?", "options": ["Yes", "No"], "default": "Yes", "why": "x", "columns": ["ghost_col"]},
    {"text": "Which of region and phantom matters?", "options": ["a", "b"], "default": "a", "why": "x", "columns": ["region", "phantom"]},
    {"text": "How is `unknown_metric` defined?", "options": ["a", "b"], "default": "a", "why": "x", "columns": ["region"]},
    {"text": "No options here?", "options": ["only"], "default": "only", "why": "x", "columns": ["region"]},
    {"text": "No columns listed?", "options": ["a", "b"], "default": "a", "why": "x", "columns": []},
]


def test_llm_question_with_missing_column_is_dropped():
    prof = profile_df(orders())
    out = clarify.validate_llm_questions({"questions": [GHOSTS[0], GOOD, *GHOSTS[1:]]}, prof, [])
    assert [q["text"] for q in out] == [GOOD["text"]] and out[0]["id"].startswith("llm:") and out[0]["default"] == "Yes"


def test_llm_questions_are_capped_and_deduplicated():
    prof = profile_df(orders())
    many = [{**GOOD, "text": f"Question number {i} about region?"} for i in range(6)]
    assert len(clarify.validate_llm_questions({"questions": many}, prof, [])) == clarify.MAX_LLM == 3
    assert clarify.validate_llm_questions({"questions": [GOOD]}, prof, [{"text": GOOD["text"]}]) == []
    assert clarify.validate_llm_questions("garbage", prof, []) == []


def test_run_with_fake_llm_asks_only_valid_extra_questions():
    fake = QuestionLLM([GHOSTS[0], GOOD])
    llm_mod._cache["llm"] = (fake, "fake:model")
    try:
        rid = start(orders(), company="Epsilon")
        assert wait(rid) == "needs_input"
    finally:
        llm_mod.reset_cache()
    qs = cl.get(f"/api/runs/{rid}/questions").json()["questions"]
    extra = [q for q in qs if q["kind"] == "llm"]
    assert [q["text"] for q in extra] == [GOOD["text"]]
    assert "ghost_col" not in json.dumps(qs) and "region" in fake.seen_prompt and "ALREADY ASKED" in fake.seen_prompt
    assert len(qs) <= clarify.MAX_QUESTIONS
    cl.post(f"/api/runs/{rid}/answers", json={"skip": True})
    llm_mod._cache["llm"] = (QuestionLLM([]), "fake:model")
    try:
        assert wait(rid) == "done"
    finally:
        llm_mod.reset_cache()


def test_rules_still_work_with_no_llm():
    assert llm_mod.get_llm()[0] is None
    assert clarify.llm_questions(None, profile_df(orders()), "g", {}, [], 3) == []
    assert ids(detect(orders()))
