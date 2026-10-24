"""A loss-making ratio group is worded as such (in %), and an integer code column is never a candidate measure."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app import planner, tools

SPEC = {"name": "num_to_den_ratio", "a": "num", "b": "den", "kind": "ratio_of_sums", "part_of": True}
CTX = {"derived": {SPEC["name"]: SPEC}}


def test_negative_ratio_group_says_loss_making_in_percent(tmp_path):
    rows = []
    for i in range(20):
        rows += [("gain", 10.0 + i % 3, 100.0), ("loss", -5.0 - i % 2, 100.0)]
    df = pd.DataFrame(rows, columns=["grp", "num", "den"])
    df[SPEC["name"]] = df["num"] / df["den"]
    out = tools.group_compare(df, {**CTX, "charts_dir": tmp_path}, SPEC["name"], "grp")
    assert "loss-making (negative num_to_den_ratio)" in out["text"]
    assert "%" in out["text"] and "-0.0" not in out["text"]


def test_six_digit_code_column_is_not_a_candidate_measure():
    rng = np.random.default_rng(0)
    n = 2000
    df = pd.DataFrame({"code": rng.integers(400000, 600000, n), "amount": rng.normal(100, 20, n).round(2)})
    roles = {"code": "numeric", "amount": "numeric"}
    uniques = {c: int(df[c].nunique()) for c in df}
    ov = {"kinds": {"amount": "additive"}}
    assert planner.candidate_measures(df, roles, uniques, ov) == ["amount"]
    assert planner.id_like_columns(df, roles, uniques, ov) == ["code"]
    # a column the brief marked as an id is excluded whatever its values look like
    assert "amount" not in planner.candidate_measures(df, roles, uniques, {**ov, "brief_roles": {"amount": "id"}})
