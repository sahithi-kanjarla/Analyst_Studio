"""Placeholder numbers are found on the source tables before joining. A used column is asked about once (default: missing); an unused one is set to missing and logged."""
import os
import tempfile

os.environ["LLM_PROVIDER"] = "none"
os.environ["GLOSSARY_PATH"] = os.path.join(tempfile.mkdtemp(), "glossary.json")

import json  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import glossary, llm as llm_mod, placeholders  # noqa: E402
from app.config import RUNS_DIR  # noqa: E402
from app.main import app  # noqa: E402

from test_clarify import wait  # noqa: E402

cl = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_glossary():
    p = glossary.glossary_path()
    if p.exists():
        p.unlink()
    yield
    llm_mod.reset_cache()


def customers_orders(n_cust=200, seed=1, extra_tables=False, tickets=False):
    """orders.wait_days has 999 for 'not recorded'; customers.score is clean."""
    rng = np.random.default_rng(seed)
    cust = pd.DataFrame({"customer_id": [f"C{i:03d}" for i in range(n_cust)], "segment": rng.choice(["A", "B", "C"], n_cust), "score": rng.normal(50, 10, n_cust).round(1)})
    rows = []
    for c in cust["customer_id"]:
        rows += [c] * int(rng.integers(3, 8))
    orders = pd.DataFrame({"order_id": range(1, len(rows) + 1), "customer_id": rows})
    orders["revenue"] = rng.gamma(3, 100, len(orders)).round(2)
    orders["wait_days"] = rng.integers(1, 15, len(orders)).astype(float)
    orders.loc[rng.random(len(orders)) < 0.10, "wait_days"] = 999
    out = {"customers": cust, "orders": orders}
    if tickets:      # a smaller child of customers: its numbers reach the analysis only as per-customer summaries
        t = pd.DataFrame({"ticket_id": range(1, n_cust + 1), "customer_id": rng.choice(cust["customer_id"], n_cust)})
        t["resolution_days"] = rng.integers(1, 10, n_cust).astype(float)
        t.loc[rng.random(n_cust) < 0.15, "resolution_days"] = 999
        out["tickets"] = t
    if extra_tables:      # shares no key with anything, so the analysis never uses it
        out["audit"] = pd.DataFrame({"audit_ref": [f"Z{i}" for i in range(120)], "seconds": np.where(rng.random(120) < 0.2, 9999, rng.integers(1, 90, 120)).astype(float)})
    return out


def start(tables, goal="Explore this data", company="Acme"):
    files = [("files", (f"{k}.csv", df.to_csv(index=False).encode())) for k, df in tables.items()]
    r = cl.post("/api/runs", data={"goal": goal, "company": company}, files=files)
    assert r.status_code == 200
    return r.json()["run_id"]


def questions(rid):
    return cl.get(f"/api/runs/{rid}/questions").json()["questions"]


def result(rid):
    return json.loads((RUNS_DIR / rid / "result.json").read_text())


# ------------------------------------------------------------------ detection
def test_detects_repeated_maximum_far_above_the_rest():
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"x": rng.normal(100, 15, 400).round(1)})
    df.loc[:39, "x"] = 9999
    (c,) = placeholders.detect(df)
    assert c["column"] == "x" and c["value"] == 9999 and c["count"] == 40 and "far above" in c["reason"]


def test_detects_value_that_lines_up_with_a_blank_in_a_related_column():
    rng = np.random.default_rng(1)
    n = 300
    df = pd.DataFrame({"duration": rng.integers(1, 91, n).astype(float), "ended_on": pd.date_range("2024-01-01", periods=n).astype(str)})
    df.loc[:29, "duration"] = 99             # just above the real range (1-90): not far enough to stand out alone, but those rows have no end date
    df.loc[:29, "ended_on"] = None
    (c,) = placeholders.detect(df)
    assert c["value"] == 99 and c["twin"] == "ended_on" and "lines up with blanks" in c["reason"]
    assert placeholders.detect(df.assign(ended_on="2024-01-01")) == []         # same value, nothing blank beside it: just a number


def test_ordinary_skewed_columns_are_not_flagged():
    rng = np.random.default_rng(2)
    df = pd.DataFrame({"revenue": rng.lognormal(5, 1.2, 500), "capped": np.minimum(rng.normal(80, 20, 500), 100).round(0), "qty": rng.integers(0, 40, 500).astype(float)})
    assert placeholders.detect(df) == []


# ------------------------------------------------------------------ one table: asked, default missing
def test_single_table_placeholder_is_asked_and_defaults_to_missing():
    t = customers_orders()["orders"]
    rid = start({"orders": t})
    assert wait(rid) == "needs_input"
    q = next(x for x in questions(rid) if x["kind"] == "placeholder")
    assert q["text"] == "How should the value 999 in wait_days be treated?" and q["default"] == "missing"
    assert [o["label"] for o in q["options"]] == ["As missing (default)", "Keep as a real value"]
    assert not [x for x in questions(rid) if x["kind"] == "sentinel" and "wait_days" in x["columns"]]       # asked once, not twice
    assert cl.post(f"/api/runs/{rid}/answers", json={"skip": True}).status_code == 200
    assert wait(rid) == "done"
    clean = pd.read_pickle(RUNS_DIR / rid / "clean.pkl")
    assert clean["wait_days"].max() < 15                                  # no 999 left (the cleaner then fills the blanks the way it fills any others)
    d = next(x for x in result(rid)["applied_definitions"] if x["id"] == q["id"])
    assert d["answer"] == "missing" and d["rows"] == (t["wait_days"] == 999).sum()


def test_single_table_keep_leaves_the_value_and_is_not_asked_again():
    t = customers_orders()["orders"]
    rid = start({"orders": t})
    assert wait(rid) == "needs_input"
    q = next(x for x in questions(rid) if x["kind"] == "placeholder")
    assert cl.post(f"/api/runs/{rid}/answers", json={"answers": {q["id"]: "keep"}}).status_code == 200
    assert wait(rid) == "done"
    assert (pd.read_pickle(RUNS_DIR / rid / "clean.pkl")["wait_days"] == 999).sum() == (t["wait_days"] == 999).sum()
    rid2 = start({"orders": t})                                         # same company: the saved answer settles it
    assert wait(rid2) == "done"
    assert (pd.read_pickle(RUNS_DIR / rid2 / "clean.pkl")["wait_days"] == 999).sum() == (t["wait_days"] == 999).sum()


# ------------------------------------------------------------------ several tables: cleaned before the summary, unused ones silently
def test_used_column_is_cleaned_on_the_source_before_it_is_summarised():
    tables = customers_orders(tickets=True)
    rid = start(tables)
    assert wait(rid) == "needs_input"
    q = next(x for x in questions(rid) if x["kind"] == "placeholder" and "resolution_days" in x["columns"])
    assert q["text"] == "How should the value 999 in resolution_days be treated?" and q["default"] == "missing"
    assert cl.post(f"/api/runs/{rid}/answers", json={"skip": True}).status_code == 200
    assert wait(rid) == "done"
    clean = pd.read_pickle(RUNS_DIR / rid / "clean.pkl")
    avg = [c for c in clean.columns if c.startswith("tickets_resolution_days") and c.endswith("_avg")]
    assert avg and clean[avg[0]].max() < 10                              # a 999 inside a customer's average would push it far above 10
    assert clean["wait_days"].max() < 15
    log = " ".join(s["detail"] for s in result(rid)["cleaning_log"])
    assert "resolution_days" in log and "before the tables were joined" in log
    d = next(x for x in result(rid)["applied_definitions"] if x["id"] == q["id"])
    assert d["answer"] == "missing" and d["rows"] == (tables["tickets"]["resolution_days"] == 999).sum()


def test_keeping_the_value_keeps_it_in_the_summary():
    rid = start(customers_orders(tickets=True))
    assert wait(rid) == "needs_input"
    qs = {x["columns"][0]: x for x in questions(rid) if x["kind"] == "placeholder"}
    assert cl.post(f"/api/runs/{rid}/answers", json={"answers": {qs["resolution_days"]["id"]: "keep", qs["wait_days"]["id"]: "keep"}}).status_code == 200
    assert wait(rid) == "done"
    clean = pd.read_pickle(RUNS_DIR / rid / "clean.pkl")
    avg = [c for c in clean.columns if c.startswith("tickets_resolution_days") and c.endswith("_avg")]
    assert avg and clean[avg[0]].max() > 100 and clean["wait_days"].max() == 999


def test_unused_column_is_set_to_missing_without_asking_and_is_logged():
    tables = customers_orders(extra_tables=True)
    rid = start(tables)
    assert wait(rid) == "needs_input"                                    # the used column still pauses the run ...
    qs = questions(rid)
    assert [q["text"] for q in qs if q["kind"] == "placeholder"] == ["How should the value 999 in wait_days be treated?"]      # ... but nothing is asked about audit.seconds
    assert cl.post(f"/api/runs/{rid}/answers", json={"skip": True}).status_code == 200
    assert wait(rid) == "done"
    log = [s for s in result(rid)["cleaning_log"] if s["step"] == "Placeholder values" and "[audit]" in s["detail"]]
    assert len(log) == 1 and "seconds" in log[0]["detail"] and "not used" in log[0]["detail"] and log[0]["count"] == int((tables["audit"]["seconds"] == 9999).sum())


def test_run_with_only_unused_placeholders_never_pauses():
    t = customers_orders(extra_tables=True)
    t["orders"].loc[t["orders"]["wait_days"] == 999, "wait_days"] = 7.0
    rid = start(t)
    assert wait(rid) == "done"
    assert [s for s in result(rid)["cleaning_log"] if s["step"] == "Placeholder values"]
    assert not (RUNS_DIR / rid / "questions.json").exists()
