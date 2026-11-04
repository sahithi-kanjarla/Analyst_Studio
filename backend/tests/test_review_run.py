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


def _goal_exp(goal):
    return {"files": {"a.csv": {}}, "answers": {"row_count": {"a.csv": 2}, "main_total": {"file": "a.csv", "column": "x", "means": "revenue", "value": 3.0, "goal": goal}}}


def _shown(tmp_path, goal, text, rtext):
    pd.DataFrame({"x": [1, 2]}).to_pickle(tmp_path / "clean.pkl")
    out = rr.check_figures(_goal_exp(goal), {}, tmp_path, text, rtext)
    return next(f for f in out if f["id"] == "total-shown")


def test_goal_figure_must_be_in_the_report_tab(tmp_path):
    f = _shown(tmp_path, True, "revenue 3 appears in Insights", "nothing here")
    assert f["ok"] is False and f["goal"] is True


def test_other_figure_passes_if_it_is_anywhere(tmp_path):
    f = _shown(tmp_path, False, "revenue 3 appears in Insights", "nothing here")
    assert f["ok"] is True and f["goal"] is False


def test_goal_figure_in_report_text_passes(tmp_path):
    assert _shown(tmp_path, True, "", "Total revenue: 3")["ok"] is True


def test_chart_stats_counts_duplicates_and_null_charts():
    c = lambda t, m, d, n=False: {"type": t, "measure": m, "dimension": d, "null_result": n}
    tabs = {"report": {"charts": [c("bar", "rev", "city"), c("bar", "rev", "city"), c("line", "rev", "")]}, "insights": {"charts": [c("bar", "rev", "city", True)]}}
    s = rr.chart_stats(tabs)
    assert s["per_tab"] == {"report": 3, "insights": 1} and s["duplicates"] == 1 and s["null_charts"] == 1


def test_scoreboard_keeps_old_rows_and_adds_columns(tmp_path):
    old = "date_time,dataset\r\n2026-11-03,R01,x\r\n"
    (tmp_path / "scoreboard.csv").write_bytes(old.encode())
    rr.extend_header(tmp_path / "scoreboard.csv", lambda ln: ln + ",left_out_count", lambda ln: ln.startswith("date_time,") and "left_out_count" not in ln)
    got = (tmp_path / "scoreboard.csv").read_bytes().decode()
    assert got == "date_time,dataset,left_out_count\r\n2026-11-03,R01,x\r\n"
