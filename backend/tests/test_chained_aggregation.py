"""3-table chain customers <- orders <- order_lines must build at every base without changing the row count."""
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np
import pandas as pd
import pytest

from app import relations
from app.cleaner import coerce_types
from app.profiler import profile_df


def make_chain(seed=3):
    rng = np.random.default_rng(seed)
    customers = pd.DataFrame({"customer_id": range(1, 51), "tier": rng.choice(["Gold", "Silver", "Bronze"], 50),
                              "tenure_years": rng.uniform(0.5, 9, 50).round(2)})
    orders = pd.DataFrame({"order_id": range(1, 201), "customer_id": rng.integers(1, 51, 200),
                           "discount": rng.uniform(0, 0.3, 200).round(3)})
    lines = pd.DataFrame({"line_id": range(1, 601), "order_id": rng.integers(1, 201, 600),
                          "quantity": rng.integers(1, 10, 600), "amount": rng.uniform(5, 500, 600).round(2)})
    return {"customers": customers, "orders": orders, "order_lines": lines}


def typed(t):
    return {k: coerce_types(v, profile_df(v))[0] for k, v in t.items()}


@pytest.mark.parametrize("base", ["customers", "orders", "order_lines"])
def test_chain_keeps_rows_and_connects_every_table(base):
    t = typed(make_chain())
    prof = {k: profile_df(v) for k, v in t.items()}
    edges = relations.detect_relationships(t)
    m = relations.build_model(t, prof, edges, base)
    assert len(m["df"]) == len(t[base])
    assert m["unused_tables"] == []


def test_grandchild_amount_reaches_customers():
    t = typed(make_chain())
    prof = {k: profile_df(v) for k, v in t.items()}
    m = relations.build_model(t, prof, relations.detect_relationships(t), "customers")
    assert [c for c in m["df"].columns if "amount" in c]
    assert any("(via orders)" in s["detail"] for s in m["steps"])


def test_chain_failure_falls_back_to_single_level(monkeypatch):
    t = typed(make_chain())
    prof = {k: profile_df(v) for k, v in t.items()}
    edges = relations.detect_relationships(t)
    real = relations.profile_df
    monkeypatch.setattr(relations, "profile_df", lambda df: (_ for _ in ()).throw(RuntimeError("boom")))
    m = relations.build_model(t, prof, edges, "customers")
    assert len(m["df"]) == 50
    assert any("Could not chain" in w["text"] for w in m["warnings"])
    assert "orders" not in m["unused_tables"]
