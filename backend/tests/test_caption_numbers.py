"""Every number a finding's caption shows is saved in that finding's data, so a reader (and scripts/check_sample_run.py) can check it."""
import re

import numpy as np
import pandas as pd

from app import tools


def test_outlier_caption_limits_are_saved(tmp_path):
    rng = np.random.default_rng(1)
    df = pd.DataFrame({"v": np.append(rng.normal(50, 10, 500), [400, -300])})
    f = tools.outliers(df, {"charts_dir": tmp_path}, "v")
    lo, hi = f["stats"]["lower_fence"], f["stats"]["upper_fence"]
    shown = re.search(r"usual range \((.+?) to (.+?)\)", f["text"])
    assert shown and shown.group(1) == tools._fmt(lo) and shown.group(2) == tools._fmt(hi)
    assert lo < 50 < hi


def test_trend_caption_percentage_is_saved(tmp_path):
    days = pd.date_range("2024-01-01", periods=700, freq="D")
    df = pd.DataFrame({"when": days, "amount": np.linspace(100, 96.5, 700) + np.random.default_rng(2).normal(0, 4, 700)})
    f = tools.trend(df, {"charts_dir": tmp_path}, "amount", "when")
    m = re.search(r"about ([+\-−]?\d+)%", f["text"])
    assert m, f["text"]
    assert f["stats"]["change_shown_pct"] == int(m.group(1).replace("−", "-"))
