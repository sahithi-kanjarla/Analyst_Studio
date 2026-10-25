"""Quantity x unit price becomes revenue (the main additive KPI), a yes/no flag gets a rate per goal-named group with the overall rate, the clarifier asks about the
flag and the blank quantities, and with no cost column nothing says margin or profit except the one line in the data gaps."""
import json
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from app import glossary, reporter, wording  # noqa: E402
from app.config import RUNS_DIR  # noqa: E402
from test_clarify import cl, result, start, wait  # noqa: E402

GOAL = "Which shop earns the most, and which shop has the highest share of flagged rows?"
GAP = "Margin: not computable, no cost column"


@pytest.fixture(autouse=True)
def fresh_glossary():
    p = glossary.glossary_path()
    if p.exists():
        p.unlink()
    yield


def table(n=900, seed=5):
    """Neutral column names on purpose: nothing may depend on what a column is called. No cost column."""
    rng = np.random.default_rng(seed)
    shop = rng.choice(["North", "South", "East", "West"], n, p=[0.4, 0.3, 0.2, 0.1])
    pcs = rng.choice([1, 2, 3, 4], n, p=[0.5, 0.25, 0.15, 0.1]).astype(float)
    each = np.round(rng.lognormal(6.5, 0.4, n) * np.where(shop == "West", 2.0, 1.0), 2)       # the price per unit does not depend on the quantity
    flag = rng.random(n) < np.where(shop == "South", 0.4, 0.05)
    when = pd.Timestamp("2024-01-01") + pd.to_timedelta(rng.integers(0, 400, n), unit="D")
    df = pd.DataFrame({"shop": shop, "pcs": pcs, "each": each, "flag": np.where(flag, "Y", "N"), "when": when.strftime("%Y-%m-%d")})
    df.loc[:7, "pcs"] = np.nan                                                                # blank quantities
    return df


@pytest.fixture(scope="module")
def run():
    df = table()
    rid = start(df, goal=GOAL)
    assert wait(rid) == "needs_input"                                                         # the flag and the blank quantities are asked about
    qs = json.loads((RUNS_DIR / rid / "questions.json").read_text())["questions"]                # asked ones and the ones the data settled
    assert cl.post(f"/api/runs/{rid}/answers", json={"skip": True}).status_code == 200      # defaults: gross, blanks left blank
    assert wait(rid) == "done"
    return df, rid, qs


def test_the_clarifier_asks_about_the_flag_and_the_blank_quantities_at_most_three_questions(run):
    _, _, qs = run
    asked = [q for q in qs if not q.get("auto")]
    kinds = {q["kind"] for q in asked}
    assert {"flag_rows", "blank_values"} <= kinds and len(asked) <= 3
    assert next(q for q in asked if q["kind"] == "flag_rows")["default"] == "gross"
    auto = [q for q in qs if q["kind"] == "product_basis"]
    assert len(auto) == 1 and auto[0]["auto"] == "data" and auto[0]["default"] == "unit"      # the values show the price is per unit, so it is not asked


def test_revenue_is_quantity_times_unit_price_and_is_the_main_kpi(run):
    df, rid, _ = run
    clean = pd.read_pickle(RUNS_DIR / rid / "clean.pkl")
    assert "revenue" in clean.columns
    expected = df["pcs"] * df["each"]
    assert np.allclose(clean["revenue"], expected, equal_nan=True) and clean["revenue"].isna().sum() == 8      # blank quantity: no revenue, nothing invented
    res = result(rid)
    assert res["plan"]["target"] == "revenue"
    assert any(s["step"] == "Definition applied" and "pcs x each" in s["detail"] for s in res["cleaning_log"])
    top = expected.groupby(df["shop"]).sum().idxmax()
    table_ = next(f for f in res["findings"] if f["tool"] == "group_table" and f["params"]["target"] == "revenue" and f["params"]["group"] == "shop")
    assert table_["table"]["rows"][0][0] == top and table_["table"]["columns"][-2:] == ["Total", "Share of total"]        # ranked by total revenue, largest first


def test_flag_rate_by_group_with_the_overall_rate(run):
    df, rid, _ = run
    f = next(f for f in result(rid)["findings"] if f["tool"] == "group_compare" and f["params"] == {"target": "flag", "group": "shop"})
    truth = (df["flag"] == "Y").groupby(df["shop"]).mean()
    assert {k: round(v, 4) for k, v in f["stats"]["values"].items()} == {k: round(v, 4) for k, v in truth.items()}
    assert abs(f["stats"]["overall_rate"] - (df["flag"] == "Y").mean()) < 1e-3 and "across all rows" in f["text"]


def test_no_margin_or_profit_wording_without_a_cost_column(run, monkeypatch):
    _, rid, _ = run
    res = result(rid)
    report = res["report_md"]
    assert GAP in report and any(g["title"] == GAP for g in res["data_gaps"])
    body = "\n".join(l for l in report.splitlines() if GAP not in l)
    assert not wording.PROFIT_CLAIM.search(body), wording.PROFIT_CLAIM.search(body)

    # sentences a model might add are dropped in code
    monkeypatch.setattr(reporter, "ask_json", lambda *a, **k: {
        "summary": "West sells the dearest items. The profit margin looks healthy. Profitability will rise.",
        "recommendations": ["Grow the North shop.", "Improve margin in the West.", "Find the profit drivers."]})
    summary, recs, partial = reporter.llm_summary(object(), res)
    assert partial and "West sells the dearest items." in summary and not wording.PROFIT_CLAIM.search(summary + " ".join(recs)) and recs == ["Grow the North shop."]


def test_a_percentage_column_is_never_chosen_as_price_or_quantity():
    from app import retail_roles as rr
    rng = np.random.default_rng(3)
    n = 300
    df = pd.DataFrame({"qty": rng.choice([1, 2, 3, 4], n).astype(float),
                       "unit_price": np.round(rng.lognormal(5, 0.4, n), 2),
                       "discount_pct": np.round(rng.uniform(0, 30, n), 1),
                       "share": np.round(rng.uniform(0.01, 0.99, n), 3)})
    cols = {c: {"role": "numeric"} for c in df.columns}
    d = rr.derive_product_measure(df, cols)
    assert d and d["count"] == "qty" and d["rate"] == "unit_price"
    assert not rr.is_rate_like(df["share"])
