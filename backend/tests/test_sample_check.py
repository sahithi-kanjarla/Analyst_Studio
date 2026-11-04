"""Tests for scripts/make_sample_expected.py and scripts/check_sample_run.py on a tiny fixture.

No app code, no AI, no network: a hand-made set of files and a hand-made result.json.
"""
import copy
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import check_sample_run as chk  # noqa: E402
import make_sample_expected as mk  # noqa: E402


@pytest.fixture()
def files(tmp_path):
    d = tmp_path / "files"
    d.mkdir()
    pd.DataFrame({"customer_id": ["C1", "C2", "C3"], "region": ["North", "South", "North"]}).to_csv(d / "customers.csv", index=False)
    pd.DataFrame({"sku": ["A", "B"], "category": ["Hats", "Mugs"]}).to_csv(d / "products.csv", index=False)
    pd.DataFrame({"order_id": ["O1", "O2", "O3", "O4", "O4"], "customer_ref": ["C1", "C2", "C1", None, None],
                  "order_date": ["2025-01-05", "2025-02-10", "2025-02-12", "15/02/2025", "15/02/2025"]}).to_csv(d / "orders.csv", index=False)
    pd.DataFrame({"line_id": ["L1", "L2", "L3", "L4", "L4"], "order_id": ["O1", "O2", "O3", "O4", "O4"],
                  "item_code": ["A", "B", "A", "A", "A"], "quantity": [1, 2, None, 4, 4],
                  "unit_price": [100.0, 10.0, 100.0, 100.0, 100.0]}).to_excel(d / "order_lines.xlsx", index=False)
    pd.DataFrame({"return_id": ["R1"], "line_id": ["L1"]}).to_csv(d / "returns.csv", index=False)
    return d


@pytest.fixture()
def expected(files, tmp_path):
    out = tmp_path / "expected.json"
    assert mk.main([str(files), str(out)]) == 0
    return out


def fig(exp, id_):
    return next(f for f in json.loads(exp.read_text())["figures"] if f["id"] == id_)


def test_expected_figures_are_the_true_ones(expected):
    d = json.loads(expected.read_text())
    assert fig(expected, "orders_total")["expected"] == 4          # O4 twice counts once
    assert fig(expected, "revenue_total")["expected"] == 100 + 20 + 400  # L3 has no quantity; L4 duplicate removed
    assert fig(expected, "top_category")["expected"] == "Hats"
    assert fig(expected, "bottom_category")["expected"] == "Mugs"
    assert fig(expected, "top_region")["expected"] == "North"
    assert fig(expected, "highest_return_category")["expected"] == "Hats"
    assert fig(expected, "trend_direction")["expected"] == "up"
    assert d["details"]["duplicate_rows_removed"] == {"orders": 1, "order_lines": 1}
    assert d["details"]["missing_values"]["order_lines"] == {"quantity": 1}
    assert {(l["child"], l["child_col"], l["parent"]) for l in d["details"]["links"]} >= {("orders", "customer_ref", "customers")}
    assert all(f["must_show"] in (True, False) for f in d["figures"])


GOOD = {
    "report_md": "We analysed 4 orders. Total revenue 520. Hats is the top category and Mugs the lowest. "
                 "North leads regions, South trails. Returns: Hats has the highest return rate at 33%. 2 customers ordered. Orders show an upward trend.",
    "key_metrics": [], "summary_facts": {}, "strategies": [], "report_view": {},
    "findings": [{"id": "f1", "tool": "trend", "title": "Orders over time", "text": "Orders rise from 1 to 2 a month.",
                  "params": {"a": 1}, "chart": "charts/a.png", "series": [["2025-01", 1.0], ["2025-02", 2.0]]},
                 {"id": "f2", "tool": "ranking", "title": "Revenue by category", "text": "Hats 520 of 520.",
                  "params": {"a": 2}, "chart": "charts/b.png", "series": [["Hats", 520.0]]}],
    "profile": {"columns": [{"name": "quantity", "missing_pct": 5.0}, {"name": "region", "missing_pct": 0}]},
    "cleaning_log": [{"step": "Remove duplicates", "detail": "exact copies", "count": 2}],
    "data_model": {"relationships": [
        {"child_table": "orders", "child_col": "customer_ref", "parent_table": "customers", "parent_col": "customer_id", "status": "used"},
        {"child_table": "order_lines", "child_col": "order_id", "parent_table": "orders", "parent_col": "order_id", "status": "used"},
        {"child_table": "order_lines", "child_col": "item_code", "parent_table": "products", "parent_col": "sku", "status": "used"},
        {"child_table": "returns", "child_col": "line_id", "parent_table": "order_lines", "parent_col": "line_id", "status": "used"}]},
}


def make_run(tmp_path, res):
    run = tmp_path / "run"
    (run / "charts").mkdir(parents=True, exist_ok=True)
    for f in res["findings"]:
        (run / f["chart"]).write_bytes(b"x")
    (run / "result.json").write_text(json.dumps(res), encoding="utf-8")
    return run / "result.json"


def test_good_run_passes(tmp_path, expected, capsys):
    assert chk.run(make_run(tmp_path, GOOD), expected) == 0
    out = capsys.readouterr().out
    assert "Total orders" in out and "PASSED" in out


def test_missing_must_show_figure_fails(tmp_path, expected, capsys):
    res = copy.deepcopy(GOOD)
    res["report_md"] = res["report_md"].replace("North leads regions, South trails.", "")
    assert chk.run(make_run(tmp_path, res), expected) == 1
    assert "FAILED" in capsys.readouterr().out


def test_wrong_number_is_not_accepted(tmp_path, expected):
    res = copy.deepcopy(GOOD)
    res["report_md"] = res["report_md"].replace("4 orders", "7 orders").replace("about 4 orders", "about 7 orders")
    res["findings"][0]["text"] = "Orders rise."
    assert chk.run(make_run(tmp_path, res), expected) == 1


@pytest.mark.parametrize("change,msg", [
    (lambda r: r["findings"][0].update(series=[["2025-01", None]]), "empty or null"),
    (lambda r: r["findings"][1].update(chart="charts/a.png", params={"a": 2}), "same thing"),
    (lambda r: r["findings"][1].update(params={"a": 1}, tool="trend"), "same thing"),
    (lambda r: r["findings"][0].update(title="discount_pct over time"), "underscore"),
    (lambda r: r["findings"][0].update(text="Orders rise by 91.7 percent."), "not in the run"),
    (lambda r: r["cleaning_log"][0].update(count=3), "duplicate rows"),
    (lambda r: r["data_model"]["relationships"].pop(), "real key not used"),
    (lambda r: r["data_model"]["relationships"][0].update(child_col="payment_method"), "not a real key"),
    (lambda r: r.update(data_model=None), "not joined"),
])
def test_each_extra_check_catches_its_problem(tmp_path, expected, capsys, change, msg):
    res = copy.deepcopy(GOOD)
    change(res)
    assert chk.run(make_run(tmp_path, res), expected) == 1
    assert msg in capsys.readouterr().out


def test_number_matching_handles_rounding_and_units():
    assert chk.has_number("revenue 174.0K in total", 174321.0, 0.02)
    assert chk.has_number("1,234 orders", 1234)
    assert not chk.has_number("1,240 orders", 1234)
    assert chk.has_number("returned 26% of the time", 0.2587, tol_abs=0.01)
    assert chk.has_number("25.9% returned", 0.2587, tol_abs=0.01)
