"""Ranking facts cover every category column for the main additive KPI and name a group above 40% of the total as dominant; a column judged to be a quantity
is never a group or segment. Synthetic table, no column names in the code under test."""
import os

os.environ["LLM_PROVIDER"] = "none"

import pandas as pd  # noqa: E402

from app import planner, summary  # noqa: E402
from app.retail_roles import is_quantity  # noqa: E402


def make_df():
    rows = []
    for i in range(200):
        rows.append({"kind": "big" if i < 100 else ("mid" if i < 150 else "small_a" if i < 175 else "small_b"),     # 50% / 25% / 12.5% / 12.5% of the rows
                     "region": ("n", "s", "e")[i % 3], "units": 1 + (i % 4), "amount": 100.0})
    return pd.DataFrame(rows)


def make_state():
    cols = [{"name": "kind", "role": "categorical", "unique": 4}, {"name": "region", "role": "categorical", "unique": 3},
            {"name": "units", "role": "categorical", "unique": 4}, {"name": "amount", "role": "numeric", "unique": 1}]
    return {"goal": "How is amount doing?", "findings": [], "brief": {"roles": {"units": "measure"}}, "profile": {"rows": 200, "cols": 4, "columns": cols},
            "plan": {"target": "amount", "date_col": None, "questions": [], "relevant_columns": ["amount"], "analyses": []}, "context": {}}


def test_quantity_is_never_a_group():
    df, st = make_df(), make_state()
    assert is_quantity(df["units"], "measure") and is_quantity(df["units"]) and not is_quantity(df["units"], "dimension") and not is_quantity(df["kind"])
    assert "units" not in summary.group_columns(st, df, every=True)
    assert all(r["group"] != "units" for r in summary.ranking_facts(st, df))
    roles = planner.demote_quantities(df, {"kind": "categorical", "units": "categorical", "amount": "numeric"}, {"units": "measure"})
    assert roles == {"kind": "categorical", "units": "numeric", "amount": "numeric"}


def test_every_category_ranked_and_dominant_named():
    df, st = make_df(), make_state()
    facts = [r for r in summary.ranking_facts(st, df) if r["measure"] == "amount"]
    assert {r["group"] for r in facts} == {"kind", "region"}        # neither is named in the goal
    kind = next(r for r in facts if r["group"] == "kind")
    assert kind["top"] == "big" and kind["dominant"] == "big" and "50.0% of all records, dominant" in kind["text"]
    assert next(r for r in facts if r["group"] == "region")["dominant"] is None
    heads = summary.headlines(st, {"rankings": facts}, [])
    assert any("dominant" in h["text"] for h in heads)
