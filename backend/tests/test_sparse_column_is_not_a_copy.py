"""A column that is empty on most rows and matches a calculation only on its few filled rows is not a copy of it (the demo lost its revenue figure this way:
quantity x unit price was rejected as 'the same values as' a refund column that is filled on the returned lines only)."""
from pathlib import Path

import numpy as np
import pandas as pd

from app import derived_cols as dc
from app import derived_check

FILES = Path(__file__).resolve().parents[2] / "sample_data" / "demo" / "files"


def demo_lines():
    lines = pd.read_excel(FILES / "order_lines.xlsx").drop_duplicates()
    ret = pd.read_csv(FILES / "returns.csv")
    lines = lines.merge(ret.groupby("line_id", as_index=False)["refund_amount"].sum(), on="line_id", how="left")
    return lines


def test_demo_revenue_is_proposed_and_accepted_with_the_real_total():
    lines = demo_lines()
    # a refund that equals the line value on returned lines only: the shape of the column that used to hide the revenue
    lines["refund_like"] = np.where(lines["refund_amount"].notna(), lines["quantity"] * lines["unit_price"], np.nan)
    assert lines["refund_like"].notna().mean() < 0.15
    verdict = derived_check.judge([{"name": "total", "op": "mul", "left": "quantity", "right": "unit_price", "meaning": "", "confidence": "high", "reason": "line value"}],
                                  None, lines, {}, {}, {"quantity": "additive", "unit_price": "rate", "refund_like": "additive"}, [], {})
    assert not verdict["rejected"], verdict["rejected"]
    total = float((lines["quantity"] * lines["unit_price"]).sum())
    assert round(total) == 356964                                         # expected.json: plain pandas, lines with a missing quantity left out


def test_a_full_copy_is_still_caught_and_a_sparse_match_is_not():
    rng = np.random.default_rng(1)
    a = pd.Series(rng.uniform(1, 100, 500).round(2))
    assert dc.is_close_to(a, a.copy())
    sparse = a.copy()
    sparse[50:] = np.nan                                                  # filled on 10% of the rows, equal where filled
    assert not dc.is_close_to(a, sparse)
    assert dc.is_close_to(a, a.where(np.arange(500) < 300))               # filled on 60%: still a copy


def test_seasonality_saves_the_effect_size_of_every_cycle_it_prints(tmp_path):
    from app import tools
    rng = np.random.default_rng(3)
    n = 900
    d = pd.DataFrame({"when": pd.Timestamp("2023-01-01") + pd.to_timedelta(np.arange(n), unit="D"), "v": rng.uniform(5, 15, n)})
    f = tools.seasonality(d, {"charts_dir": tmp_path}, "v", "when")
    cycles = f["stats"]["cycles"]
    assert {"weekday", "month"} <= set(cycles)
    for name, c in cycles.items():
        assert isinstance(c["effect"], float), name
        assert f"eta²={c['effect']:.3f}" in f["text"], name        # every figure in the text is in the saved stats

