"""Fix C: an incomplete last period is judged per day, never as a total against full periods. Fake AI only, synthetic series."""
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app import partial, tools  # noqa: E402


def daily(start, end, low_from=None, low=40.0, seed=1):
    """One row per day, around 100 a day; from `low_from` on the day's value is around `low`."""
    days = pd.date_range(start, end)
    v = np.random.default_rng(seed).normal(100, 8, len(days))
    if low_from is not None:
        v = np.where(days >= pd.Timestamp(low_from), np.random.default_rng(seed + 1).normal(low, 4, len(days)), v)
    return pd.DataFrame({"when": days, "amount": v})


def run(tmp_path, df):
    return tools.trend(df, {"charts_dir": tmp_path}, "amount", "when")


def test_a_series_ending_on_day_one_with_a_normal_rate_is_not_called_a_fall(tmp_path):
    f = run(tmp_path, daily("2024-12-01", "2025-10-01"))
    pt = f["stats"]["partial"]
    assert (pt["days"], pt["days_in_period"], pt["verdict"]) == (1, 31, "normal")
    assert "1 of 31 days" in f["text"] and "incomplete" in f["text"] and "not from a fall" in f["text"]
    assert f["series"][-1][0] == "2025-09-01"                                  # the incomplete month is not a point of the report's series
    assert abs(f["stats"]["relative_change"]) < 0.2 and f["stats"]["periods"] == 10
    assert f["stats"]["with_partial"]["relative_change"] < -0.4                # with it, the totals alone would have read as a collapse
    assert "-86%" not in f["text"] and "trending down" not in f["title"]


def test_a_series_ending_on_day_ten_with_a_really_lower_rate_says_the_rate_fell(tmp_path):
    f = run(tmp_path, daily("2024-12-01", "2025-10-10", low_from="2025-10-01"))
    pt = f["stats"]["partial"]
    assert pt["days"] == 10 and pt["verdict"] == "fell"
    assert "per day fell" in f["text"]


def test_a_series_ending_on_day_three_with_a_lower_rate_is_too_short_to_judge(tmp_path):
    f = run(tmp_path, daily("2024-12-01", "2025-10-03", low_from="2025-10-01"))
    assert f["stats"]["partial"]["verdict"] == "too_short"
    assert "too short to say" in f["text"] and "per day fell" not in f["text"] and "not from a fall" not in f["text"]


def test_a_series_ending_on_the_last_day_of_a_month_is_unchanged(tmp_path):
    f = run(tmp_path, daily("2024-12-01", "2025-10-31"))
    assert "partial" not in f["stats"] and f["stats"]["periods"] == 11 and f["series"][-1][0] == "2025-10-01"
    assert "The first and last months may be partial." in f["text"]


def test_weekly_series_ending_on_the_first_day_of_a_week_with_a_normal_rate(tmp_path):
    f = run(tmp_path, daily("2025-01-06", "2025-05-05"))            # a Monday: 1 of 7 days
    pt = f["stats"]["partial"]
    assert f["stats"]["freq"] == "W" and (pt["days"], pt["days_in_period"], pt["verdict"]) == (1, 7, "normal")
    assert "1 of 7 days" in f["text"]


def test_weekly_series_ending_mid_week_with_a_lower_rate_is_too_short(tmp_path):
    f = run(tmp_path, daily("2025-01-06", "2025-05-07", low_from="2025-05-05"))            # a Wednesday: 3 of 7 days
    assert f["stats"]["partial"]["verdict"] == "too_short" and "too short to say" in f["text"]


def test_weekly_series_ending_on_the_last_day_of_a_week_is_unchanged(tmp_path):
    f = run(tmp_path, daily("2025-01-06", "2025-05-04"))            # a Sunday
    assert "partial" not in f["stats"] and f["series"][-1][0] == "2025-05-04"


def test_a_mean_measure_is_not_judged_per_day(tmp_path):
    df = daily("2024-12-01", "2025-10-01").rename(columns={"amount": "avg_score"})
    f = tools.trend(df, {"charts_dir": tmp_path}, "avg_score", "when")
    assert f is not None and "partial" not in f["stats"]


def test_the_helper_measures_coverage_and_judges_per_day():
    info = partial.last_period(pd.date_range("2025-09-01", "2025-10-20"), "MS", pd.Timestamp("2025-10-01"))
    assert (info["days"], info["days_in_period"], info["partial"]) == (20, 31, True)
    assert partial.last_period(pd.date_range("2025-09-01", "2025-10-25"), "MS", pd.Timestamp("2025-10-01"))["partial"] is False       # 25 of 31 days: 81%
    assert partial.last_period(pd.date_range("2025-09-01", "2025-10-25"), "D", pd.Timestamp("2025-10-01")) is None


# ------------------------------------------------------------------ ideas and the question
from app import cleaner, clarify, directives, signals  # noqa: E402
from app import strategy  # noqa: E402
from tests.test_report_wording import fake, state_with, with_insights  # noqa: E402


def trend_state(slope, verdict):
    f = fake("trend", True, target="value", date="when")
    f["stats"] = {"slope": slope, "relative_change": 0.4 if slope > 0 else -0.4, "p": 0.001, "periods": 10, "freq": "MS", "n": 300, "partial": {"verdict": verdict, "days": 10, "days_in_period": 31, "start": "2025-10-01", "end": "2025-10-31", "data_end": "2025-10-10", "per_day": 50.0, "low": 90.0, "high": 110.0}}
    f["action"] = "act"
    return with_insights(state_with([f]))


def test_no_idea_rests_on_a_trend_the_newest_per_day_rate_contradicts():
    titles = lambda st: [c["title"] for c in strategy.candidates(st) if c["kind"] == "data"]
    assert not titles(trend_state(+1.0, "fell")) and not titles(trend_state(-1.0, "rose"))
    assert titles(trend_state(-1.0, "fell")) and titles(trend_state(+1.0, "normal")) and titles(trend_state(+1.0, "too_short"))


def profile_for(df):
    return {"columns": [{"name": "when", "role": "date", "unique": df["when"].nunique()}, {"name": "amount", "role": "numeric", "unique": df["amount"].nunique(), "missing_pct": 0.0}]}


def test_the_question_is_asked_when_the_per_day_rate_is_not_clearly_normal_and_not_otherwise():
    low = daily("2024-12-01", "2025-10-03", low_from="2025-10-01")
    sigs = [s for s in signals.compute_signals(low, profile_for(low)) if s["type"] == "partial_period"]
    assert len(sigs) == 1 and sigs[0]["evidence"]["data_end"] == "2025-10-03" and sigs[0]["strong"] is False
    ok = daily("2024-12-01", "2025-10-01")
    assert not [s for s in signals.compute_signals(ok, profile_for(ok)) if s["type"] == "partial_period"]
    full = daily("2024-12-01", "2025-10-31", low_from="2025-10-01")
    assert not [s for s in signals.compute_signals(full, profile_for(full)) if s["type"] == "partial_period"]


def test_the_question_defaults_to_exported_and_the_other_answer_marks_the_end_as_real(tmp_path):
    low = daily("2024-12-01", "2025-10-03", low_from="2025-10-01")
    sigs = [s for s in signals.compute_signals(low, profile_for(low)) if s["type"] == "partial_period"]
    q = clarify.template_questions(sigs, profile_for(low), "", {}, True)[0]
    assert q["text"] == "Your data ends on 2025-10-03. Is that when the data was exported, or did amount really stop?" and q["default"] == "exported"
    assert clarify.option_for(q, None)["action"]["type"] == "note_only"
    answers = {q["id"]: "stopped"}
    out, log, effects, _ = cleaner.apply_directives(low, [q], answers)
    assert out.attrs.get("data_end_is_real") is True
    f = run(tmp_path, out)
    assert "You said the data ends because it really stopped there." in f["text"]
    assert directives.validate_action({"type": "set_data_end", "column": "nope"}, {"when": "date"}, set())[0] is None
