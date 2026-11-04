"""Small checks for scripts/review_run.py (test tooling): wording kept intact, key figures searched, empty columns not counted as failures. No AI, no network."""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import review_run as rr  # noqa: E402


def test_sentences_keep_underscores_and_words():
    assert "cost_amount" in rr.sentences("Total of cost_amount by month.")
    assert "costamount" not in rr.sentences("Total of `cost_amount` by **month**.")
    assert rr.sentences("**Revenue** grew. _Really_ well.") == "Revenue grew. Really well."


def test_visible_text_includes_key_figure_lines(monkeypatch):
    from app import viewedit
    monkeypatch.setattr(viewedit, "catalogue", lambda res: {"items": {}})
    res = {"report_md": "", "report_view": {"numbers": [{"label": "Total sales", "value": "12,345"}]}}
    assert "Total sales: 12,345" in rr.visible_text(res)


def _exp(tmp_path, col):
    return {"files": {"a.csv": {}}, "answers": {"row_count": {"a.csv": 2}, "missing_values_per_column": {"a.csv": {col: 3}}}}


def _run(tmp_path, res, col):
    pd.DataFrame({"x": [1, 2]}).to_pickle(tmp_path / "clean.pkl")
    out = rr.check_figures(_exp(tmp_path, col), res, tmp_path, "")
    return next(f for f in out if f["id"] == "missing")


def test_blank_cells_column_left_out_as_empty_counts_as_correct(tmp_path):
    res = {"cleaning_log": [{"step": "Left out empty columns", "detail": "left out 1 column with nothing in it ('gone_col')"}]}
    assert _run(tmp_path, res, "gone_col")["ok"] is True


def test_blank_cells_column_missing_without_log_still_fails(tmp_path):
    assert _run(tmp_path, {"cleaning_log": []}, "gone_col")["ok"] is False
