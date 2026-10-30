"""Descriptive goal words never give the 'no column matches' line, and a date column with gaps gives the coverage warning and 'no data' weeks. Small fake data."""
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app import insights, reporter, tools, understanding as und  # noqa: E402


def _reply(**extra):
    cols = ["a", "b"]
    return {"columns": [{"column": c, "meaning": "m", "role": "measure" if c == "b" else "category", "goal_terms": []} for c in cols],
            "goal_columns": {"groups": ["a"], "worst": "none", "how much": "none", "warehouses": "none"}, **extra}


def test_descriptive_terms_give_no_unmapped_line():
    kinds = {"groups": "data", "worst": "descriptive", "how much": "descriptive", "warehouses": "data"}
    parts, errors = und.validate(_reply(goal_term_kinds=kinds), ["a", "b"])
    assert not errors
    rec = und._record("llm", parts)
    assert rec["unmapped"] == ["warehouses"] and set(rec["descriptive"]) == {"worst", "how much"}
    assert und.unmapped_lines(rec) == ["Your goal mentions warehouses, but no column matches."]
    assert "worst" not in "\n".join(und.setup_lines(rec))


def test_a_term_without_a_kind_is_still_treated_as_data():
    parts, _ = und.validate(_reply(), ["a", "b"])
    assert len(und.unmapped_lines(und._record("llm", parts))) == 3


def test_only_the_ai_marks_a_term_descriptive_no_word_list_in_code():
    assert "worst" not in open(und.__file__, encoding="utf-8").read().split('SYSTEM = """')[0]


def gappy(days_present):
    days = pd.date_range("2024-01-01", periods=100)
    return days[days_present]


def test_coverage_warning_under_70_percent():
    d = gappy(np.arange(100) % 2 == 0)                      # 50 of 100 days
    assert insights.coverage_warning(d) == "Only 50 of 99 days in this period have data, so trends and weekly or monthly comparisons may mislead."
    assert insights.coverage_warning(gappy(np.arange(100) % 10 != 0)) == ""      # 90 of 100: no warning
    assert insights.coverage_warning(pd.Series([], dtype="datetime64[ns]")) == ""


def test_warning_is_at_the_top_of_the_report(monkeypatch):
    for n in ("_evidence_section", "_checked_section", "_definitions_section", "_appendix_section", "_details", "_setup_section"):
        monkeypatch.setattr(reporter, n, lambda *a, **k: [])
    monkeypatch.setattr(reporter, "_strategy_section", lambda *a, **k: [])
    monkeypatch.setattr(reporter, "_growth_split", lambda v: ([], 0))
    warn = "Only 5 of 20 days in this period have data, so trends and weekly or monthly comparisons may mislead."
    view = {"headlines": [], "numbers": [], "rankings": [], "answers": []}
    state = {"context": {}, "critique": {"issues": []}, "date_coverage_warning": warn}
    md = reporter.build_report(state, "S", ["r"], "rules", view)
    assert md.split(chr(10))[:3] == ["# Analysis report", "", f"> ⚠ {warn}"]
    assert warn not in reporter.build_report({**state, "date_coverage_warning": ""}, "S", ["r"], "rules", view)


def test_weeks_without_rows_say_no_data_and_do_not_count_as_best_or_weakest(tmp_path):
    days = pd.date_range("2024-01-01", periods=70)
    days = days[~((days >= "2024-02-05") & (days < "2024-02-19"))]          # two empty weeks
    df = pd.DataFrame({"d": days, "v": np.arange(len(days)) * 1.0 + 100})
    f = tools.trend(df, {"charts_dir": tmp_path}, "v", "d")
    assert "No data in 2 weeks" in f["text"]
    assert f["stats"]["empty_periods"] == 2
    assert "weakest: 2024-02-05" not in f["text"] and "weakest: 2024-02-12" not in f["text"]
    assert "(0)" not in f["text"] and "(0.0)" not in f["text"]
