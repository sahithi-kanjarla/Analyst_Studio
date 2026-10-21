"""Edge cases that previously broke or misled the agents."""
import json
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np
import pandas as pd

from app import runner
from app.config import RUNS_DIR


def run(df: pd.DataFrame, goal: str, tmp_path) -> dict:
    p = tmp_path / "d.csv"
    df.to_csv(p, index=False)
    rid = runner.create_run(goal, "d.csv", p.read_bytes())
    runner.start_run(rid).join(timeout=120)
    assert runner.read_status(rid)["status"] == "done"
    return json.loads((RUNS_DIR / rid / "result.json").read_text())


def test_binary_outcome_uses_numeric_drivers(tmp_path):
    rng = np.random.default_rng(3)
    n = 500
    tenure = rng.integers(1, 72, n)
    churn = np.where(rng.random(n) < 1 / (1 + np.exp((tenure - 20) / 8)), "Yes", "No")  # short tenure -> churn
    df = pd.DataFrame({"Customer ID": range(n), "Contract": rng.choice(["Month", "Year"], n),
                       "Tenure (months)": tenure, "Monthly Charges": rng.normal(70, 20, n).round(2), "Churn": churn})
    res = run(df, "Why do customers churn?", tmp_path)
    assert res["plan"]["target"] == "Churn"
    oc = next(f for f in res["findings"] if f["tool"] == "outcome_compare" and f["params"]["col"] == "Tenure (months)")
    assert oc["significant"] and oc["stats"]["median_outcome"] < oc["stats"]["median_other"]  # churners have shorter tenure
    assert any("DIVIDE(CALCULATE" in m for m in res["powerbi"]["measures"])  # binary target -> rate measure


def test_pure_noise_is_not_oversold(tmp_path):
    rng = np.random.default_rng(5)
    df = pd.DataFrame({"score": rng.normal(0, 1, 400), "grp": rng.choice(list("ABCD"), 400), "x": rng.normal(0, 1, 400)})
    res = run(df, "Find drivers of score", tmp_path)
    texts = " ".join(i["text"] for i in res["critique"]["issues"])
    assert "No statistically significant effects" in texts or not any(f.get("survives_correction") for f in res["findings"])
    assert "no strong, defensible lever" in res["report_md"].lower() or "No strong" in res["report_md"]


def test_categorical_only_keeps_repeated_rows(tmp_path):
    rng = np.random.default_rng(3)
    df = pd.DataFrame({"city": rng.choice(["P", "Q", "R"], 50), "type": rng.choice(["u", "v"], 50)})
    res = run(df, "describe this", tmp_path)
    assert any(s["step"] == "Kept duplicates" for s in res["cleaning_log"])
    assert res["plan"]["target"] is None  # no evidence of an outcome, so no guessed target
    assert len(res["findings"]) >= 1


def test_tiny_file_does_not_crash(tmp_path):
    run(pd.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]}), "what is in here", tmp_path)


def test_messy_headers_and_symbols(tmp_path):
    rng = np.random.default_rng(7)
    n = 200
    df = pd.DataFrame({" Sales Amount ": [f"₹{v:,.0f}" for v in rng.gamma(3, 500, n)], "Region ": rng.choice(["N", "S", "N/A"], n),
                       "Date": pd.date_range("2025-01-01", periods=n).strftime("%Y-%m-%d")})
    res = run(df, "How are sales doing?", tmp_path)
    assert res["plan"]["target"] == "Sales Amount"
    assert any("Convert to number" == s["step"] for s in res["cleaning_log"])
