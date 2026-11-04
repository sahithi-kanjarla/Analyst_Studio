"""Figures that are not real numbers. A constant column has no spread and no skew, an empty one has no statistics at all, and pandas answers NaN for each of them.
NaN is a valid Python float but not valid JSON, so one of them anywhere in a report used to fail the whole response with a 500 that the logs could not explain.
Two things are checked here: such a statistic is left out of the profile rather than written as 0, and the API can serialise any report whatever it holds."""
import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi.testclient import TestClient

from app import profiler, runner
from app.main import app
from app.web import SafeJSON


def nonfinite_paths(o, path="") -> list[str]:
    """Where a value that JSON cannot carry sits, so a failure names the column instead of only the row."""
    if isinstance(o, float) and not math.isfinite(o):
        return [f"{path} = {o}"]
    if isinstance(o, dict):
        return [p for k, v in o.items() for p in nonfinite_paths(v, f"{path}.{k}")]
    if isinstance(o, (list, tuple)):
        return [p for i, v in enumerate(o) for p in nonfinite_paths(v, f"{path}[{i}]")]
    return []


def awkward_frame(n=60) -> pd.DataFrame:
    """A frame with the three shapes that have no spread: every value the same, no value at all, and one value among blanks."""
    rng = np.random.default_rng(5)
    return pd.DataFrame({
        "region": list("NSEW") * (n // 4),
        "revenue": np.round(rng.uniform(100, 900, n), 2),
        "taxable": [1] * n,                                              # constant: no spread, no skew
        "fee": [np.nan] * n,                                             # nothing to measure at all
        "one_off": [5.0] + [np.nan] * (n - 1),                           # a single value: no spread
        "two_only": [2.0, 4.0] + [np.nan] * (n - 2),                     # two values: a spread, but no skew
    })


def test_a_statistic_that_is_undefined_is_left_out_not_written_as_zero():
    cols = {c["name"]: c for c in profiler.profile_df(awkward_frame())["columns"]}

    assert nonfinite_paths(cols) == []
    const = cols["taxable"]["stats"]
    assert const["min"] == const["max"] == const["mean"] == 1.0 and const["std"] == 0.0        # a constant column really has no spread
    assert "skew" not in const                                                                # but its skew does not exist, and 0 would claim it is symmetric
    assert cols["fee"]["stats"] == {}                                                          # no values: no statistics
    one = cols["one_off"]["stats"]
    assert one["min"] == one["max"] == one["median"] == one["mean"] == 5.0
    assert "std" not in one and "skew" not in one                                              # one value has neither
    two = cols["two_only"]["stats"]
    assert two["std"] > 0 and "skew" not in two                                                # two values have a spread; skew needs three
    assert cols["revenue"]["stats"].keys() >= {"min", "median", "mean", "max", "std", "skew"}  # an ordinary column keeps every statistic


def test_a_report_whose_figures_include_undefined_statistics_is_served_as_json():
    rid = runner.create_run("Which region brings the most revenue?", "awkward.csv", awkward_frame().to_csv(index=False).encode())
    runner.start_run(rid).join(timeout=240)
    status = runner.read_status(rid)
    assert status["status"] in ("done", "needs_input"), status

    r = TestClient(app).get(f"/api/runs/{rid}")
    assert r.status_code == 200, r.text
    body = json.loads(r.text)                      # strict: a bare NaN or Infinity in the body would not parse here
    assert nonfinite_paths(body) == []
    assert body["run_id"] == rid and body["profile"]["rows"] == 60


def test_the_shopify_orders_file_opens_over_the_api(monkeypatch):
    """The file that showed this up. Two of its columns ("Lineitem requires shipping", "Lineitem taxable") never change, so their skew is NaN, and that alone
    made the whole report fail with a 500: the report page never opened for it. The figures it reports are the same ones as before."""
    f = Path(__file__).parent / "fixtures" / "varied" / "a_shopify_orders.csv"
    from app import grain
    monkeypatch.setattr(grain, "ai_order_key", lambda rec: ("Name", "named"))          # a fake understanding record names the order column
    rid = runner.create_run("Which products and cities bring the most revenue?", [(f.name, f.read_bytes())])
    runner.start_run(rid).join(timeout=600)
    wd = runner.run_dir(rid)
    if (runner.read_status(rid) or {}).get("status") == "needs_input":          # it asks which date to use: take the defaults, as "Skip" does
        qs = json.loads((wd / "questions.json").read_text(encoding="utf-8"))["questions"]
        assert runner.resume_with_answers(rid, {"answers": {q["id"]: q["default"] for q in qs}, "skip": False, "notes": ""})
        for _ in range(200):
            if (runner.read_status(rid) or {}).get("status") not in ("running", "queued"):
                break
            time.sleep(3)
    assert (runner.read_status(rid) or {}).get("status") == "done", runner.read_status(rid)

    r = TestClient(app).get(f"/api/runs/{rid}")
    assert r.status_code == 200, r.text
    body = json.loads(r.text)
    assert nonfinite_paths(body) == []
    stats = {c["name"]: c.get("stats") or {} for c in body["profile"]["columns"]}
    assert "skew" not in stats["Lineitem taxable"] and stats["Lineitem taxable"]["max"] == 1.0
    assert body["profile"]["rows"] == 640 and body["profile"]["grain"]["orders"] == 320      # the figures this file is known for are unchanged
    assert {t["label"]: t["value"] for t in body["report_view"]["page"]["tiles"]}["Orders"] == "320"


def test_any_figure_that_is_not_a_real_number_is_sent_as_null():
    body = SafeJSON({"ok": 1.5, "skew": float("nan"), "ratio": float("inf"), "down": float("-inf"),
                     "deep": {"rows": [{"v": float("nan")}, {"v": 2}]}, "text": "nan", "flag": True, "none": None}).body
    assert json.loads(body) == {"ok": 1.5, "skew": None, "ratio": None, "down": None,
                                "deep": {"rows": [{"v": None}, {"v": 2}]}, "text": "nan", "flag": True, "none": None}


def test_a_report_with_ordinary_figures_is_serialised_exactly_as_before():
    content = {"a": [1, 2.5, "x", None, True], "b": {"c": 0.0}}
    assert SafeJSON(content).body == json.dumps(content, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


def test_skew_of_a_column_without_spread_is_none_whatever_the_library_says():
    """pandas 2.x returns NaN for a constant column, pandas 3.x returns 0.0, and a repeated decimal like 0.1 can get +-1.0 from rounding noise.
    The answer must not depend on any of that: no spread means no skew."""
    for v in (1, 5.0, 0.1, 1 / 3, 1e8 + 0.1):
        for n in (3, 10, 100):
            assert profiler.safe_skew(pd.Series([v] * n)) is None, (v, n)
            st = profiler._col_stats(pd.Series([v] * n), "numeric", None)
            assert "skew" not in st and st["min"] == st["max"]
    assert profiler.safe_skew(pd.Series([], dtype=float)) is None
    assert profiler.safe_skew(pd.Series([5.0])) is None
    assert profiler.safe_skew(pd.Series([1.0, 2.0, 10.0])) > 0
