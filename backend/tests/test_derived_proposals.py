"""Fix F: an AI-proposed derived measure is checked in code before it is built; every rejection reaches the cleaning log. Fake AI only."""
import json
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app import derived, derived_check, runner  # noqa: E402
from tests.fixtures_bakery import bakery  # noqa: E402


def test_a_total_over_its_own_unit_price_is_rejected_because_it_is_the_quantity():
    ok, why, _ = derived.check_proposal(bakery(), "amount", "unit_price")
    assert not ok and ("same values as the column quantity" in why)


def test_a_total_over_a_per_unit_cost_is_rejected():
    ok, why, _ = derived.check_proposal(bakery(), "cost_amount", "unit_cost")
    assert not ok and "quantity" in why


def test_a_total_over_a_per_unit_rate_that_is_not_its_own_factor_is_rejected():
    df = bakery()
    ok, why, _ = derived.check_proposal(df, "amount", "unit_cost")
    assert not ok


def test_revenue_over_cost_totals_that_only_restate_price_over_unit_cost_is_rejected():
    ok, why, _ = derived.check_proposal(bakery(), "amount", "cost_amount")
    assert not ok and "simple calculation on other columns" in why           # amount / cost_amount is unit_price / unit_cost on every row


def test_a_ratio_that_is_a_small_whole_number_count_is_rejected():
    rng = np.random.default_rng(0)
    base = rng.uniform(10, 20, 200)
    k = rng.integers(2, 6, 200)
    df = pd.DataFrame({"a": np.round(base * k, 6), "b": np.round(base, 6), "other": rng.uniform(1, 9, 200)})
    ok, why, _ = derived.check_proposal(df, "a", "b")
    assert not ok and ("count" in why or "same values" in why)


def test_a_ratio_equal_to_a_simple_calculation_on_other_columns_is_rejected():
    rng = np.random.default_rng(1)
    p, q = rng.uniform(5, 20, 300), rng.uniform(1.5, 9, 300)
    df = pd.DataFrame({"p": p, "q": q, "pq": np.round(p * q, 4), "num": np.round(p * q * 3.7, 4), "den": np.round(3.7 * np.ones(300) * rng.uniform(0.9, 1.1, 300), 4)})
    df["x"] = rng.uniform(1, 2, 300)
    ok, why, _ = derived.check_proposal(df.assign(den=df["den"]), "pq", "p")
    assert not ok                                              # pq / p is q


def test_the_briefs_derived_measures_get_the_same_checks():
    df = bakery()
    typed = df.copy()
    plan = derived_check.judge([{"name": "n", "op": "div", "left": "amount", "right": "unit_price", "meaning": "m", "confidence": "high", "reason": "r"}], None, typed, {}, {}, {}, [], {})
    assert not plan["accepted"] and "same values as the column 'quantity'" in plan["rejected"][0]["verdict"]


def test_a_rejected_proposal_is_in_the_cleaning_log_and_no_derived_number_is_typed_as_a_category(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("GLOSSARY_PATH", str(tmp_path / "g.json"))
    monkeypatch.setattr(derived, "propose", lambda *a, **k: ([], [{"a": "amount", "b": "unit_price", "meaning": "m", "reason": "gives the same values as the column quantity"}]))
    rid = runner.create_run("", [("bakery.csv", bakery().to_csv(index=False).encode())])
    runner._execute(rid)
    wd = runner.run_dir(rid)
    if (runner.read_status(rid) or {}).get("status") == "needs_input":
        (wd / "answers.json").write_text(json.dumps({"answers": {}, "skip": True, "notes": ""}))
        runner._execute(rid)
    res = json.loads((wd / "result.json").read_text(encoding="utf-8"))
    rows = [r for r in res["cleaning_log"] if r["step"] == "Derived measure rejected"]
    assert rows and "amount / unit_price was not created" in rows[0]["detail"] and "quantity" in rows[0]["detail"]
