"""Two regressions. (1) A category column the goal does not name still gets its main-KPI ranking (flagged focus=False, shown in the report) and its growth fact.
(2) A ratio KPI is always inside a planned correlation, even when the plan already holds a correlation whose column list does not contain it."""
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app import planner, summary  # noqa: E402
from app.profiler import profile_df  # noqa: E402
from test_planner_coverage import GOAL, OV, data, spec  # noqa: E402


def test_unnamed_category_is_ranked_with_growth():
    rng = np.random.default_rng(1)
    n = 600
    place = rng.choice(["a", "b", "c"], n)
    t = pd.Timestamp("2023-01-01") + pd.to_timedelta(rng.integers(0, 730, n), unit="D")
    df = pd.DataFrame({"place": place, "kind": rng.choice(["x", "y"], n), "when": t, "amount": rng.uniform(50, 150, n).round(2)})
    cols = [{"name": "place", "role": "categorical", "unique": 3}, {"name": "kind", "role": "categorical", "unique": 2}, {"name": "amount", "role": "numeric", "unique": 500}]
    st = {"goal": "How is amount doing?", "findings": [], "brief": {}, "profile": {"rows": n, "cols": 4, "columns": cols},
          "plan": {"target": "amount", "date_col": "when", "questions": [], "relevant_columns": ["amount"], "analyses": []}, "context": {}}
    facts = summary.ranking_facts(st, df)
    for g in ("place", "kind"):
        assert any(r["group"] == g and r.get("kind") != "growth" and r["focus"] is False for r in facts)
        assert any(r["group"] == g and r.get("kind") == "growth" for r in facts)


def test_ratio_kpi_correlation_is_not_crowded_out():
    df = data()
    roles, uniques = planner.roles_and_uniques(profile_df(df))
    crowded = spec("correlation", cols=["amount", "profit", "discount_pct"])       # a plan's own correlation that leaves the ratio KPI out
    out = planner.ensure_required(df, roles, uniques, GOAL, False, OV, "amount", "order_date", [crowded])
    cors = [a["params"]["cols"] for a in out if a["tool"] == "correlation"]
    assert any("margin_ratio" in c and "discount_pct" in c for c in cors)
