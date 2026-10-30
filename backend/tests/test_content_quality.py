"""Content quality of the main view: a dropped AI action is replaced by the computed fact (or 'No action suggested: <reason>'), two growth measures are labelled in plain words,
growth claims need a reliable figure, and a figure carries its unit. Fake AI text and synthetic facts."""
import os

os.environ["LLM_PROVIDER"] = "none"

from test_main_view import fake_ai, growth, main_of, render, state_with, what_to_do  # noqa: E402
from test_report_tiers import finding  # noqa: E402

from app import mainview, reporter  # noqa: E402


def run_actions(monkeypatch, st, actions):
    monkeypatch.setattr(reporter, "ask_json", fake_ai({"summary": "Fine [counts].", "recommendations": ["Do it."], "so_what": []}, {"actions": actions}, []))
    return what_to_do(main_of(render(st, object())))


# ------------------------------------------------------------------ 1. the replacement comes from the computed fact
def test_an_action_the_check_drops_is_replaced_by_the_computed_fact_line(monkeypatch):
    todo = run_actions(monkeypatch, state_with(strategies=False), [{"question": 1, "text": "Cut 12,345 from revenue [r1]."}])
    assert "12,345" not in todo and "segment a is highest at 5,000" in todo and "*(question 1)*" in todo


def test_an_action_with_no_figure_is_replaced_by_the_computed_fact_line(monkeypatch):
    todo = run_actions(monkeypatch, state_with(strategies=False), [{"question": 1, "text": "Focus on segment a [r1]."}])
    assert "Focus on segment a" not in todo and "5,000" in todo


def test_an_action_with_a_figure_that_names_no_measure_is_replaced(monkeypatch):
    todo = run_actions(monkeypatch, state_with(strategies=False), [{"question": 1, "text": "Grow segment a to 62.5% [r1]."}])
    assert "Grow segment a" not in todo and "5,000" in todo


def test_with_no_computed_figure_to_fall_back_on_the_question_says_no_action_and_why(monkeypatch):
    st = state_with(strategies=False)
    for r in st["summary_facts"]["rankings"]:
        r["text"], r["short"] = "segment a leads revenue", "segment a leads revenue"
    todo = run_actions(monkeypatch, st, [{"question": 1, "text": "Cut 12,345 from revenue [r1]."}])
    assert "12,345" not in todo and "No action suggested for question 1: the AI's action was dropped" in todo


def test_a_good_ai_action_is_kept_as_written(monkeypatch):
    good = "Review how segment a reaches 62.5% of all revenue before shifting budget [r1]."
    todo = run_actions(monkeypatch, state_with(strategies=False), [{"question": 1, "text": good}])
    assert "Review how segment a reaches 62.5% of all revenue before shifting budget." in todo


# ------------------------------------------------------------------ 2. two growth measures, plain labels
def trend(sig=True, slope=1.0):
    return finding(7, "trend", sig, "revenue is trending up", stats={"periods": 24, "freq": "MS", "slope": slope, "p": 0.01}, target="revenue", date="when")


def test_the_two_growth_measures_have_plain_labels_with_their_own_reliability():
    g = growth()
    g["short"] = "Biggest change in revenue by segment, 2023 vs 2024: segment g3 +41% (100 to 141), not statistically reliable"
    assert mainview.growth_label(g) == "2023 vs 2024: segment g3 +41% (not statistically reliable)"
    assert mainview.trend_label(trend()) == "monthly trend over 24 months: rising (statistically reliable)"
    assert mainview.trend_label(trend(sig=False, slope=-1.0)) == "monthly trend over 24 months: falling (not statistically reliable)"


def test_a_card_showing_both_measures_labels_both():
    g, t = growth(), trend()
    g["short"] = "Biggest change in revenue by segment, 2023 vs 2024: segment g3 +41% (100 to 141), not statistically reliable"
    lines = ["Biggest change ...", "revenue is trending up"]
    origin = {lines[0]: g["id"], lines[1]: t["id"]}
    out = mainview.label_growth_measures(lines, origin, {t["id"]: t}, {g["id"]: g})
    assert out == ["2023 vs 2024: segment g3 +41% (not statistically reliable)", "monthly trend over 24 months: rising (statistically reliable)"]
    assert mainview.label_growth_measures(lines[:1], {lines[0]: g["id"]}, {}, {g["id"]: g}) == lines[:1]      # one measure alone keeps its line


def test_an_action_cannot_call_growth_strong_when_the_figure_is_not_reliable_and_names_its_measure():
    g, t = growth(), trend(sig=True)
    g["short"] = "Biggest change in revenue by segment, 2023 vs 2024: segment g3 +41% (100 to 141), not statistically reliable"
    by_id, r_by_id = {t["id"]: t}, {g["id"]: g}
    weak = mainview.growth_safe("Invest in the high-growth segment g3.", [g["id"]], by_id, r_by_id)
    assert "high-growth" not in weak and "2023 vs 2024: segment g3 +41% (not statistically reliable)" in weak
    solid = mainview.growth_safe("Invest in the high-growth segment g3.", [t["id"]], by_id, r_by_id)
    assert "high-growth" in solid and "monthly trend over 24 months: rising (statistically reliable)" in solid
    bare = mainview.growth_safe("Back the strong growth in g3.", [], by_id, r_by_id)
    assert "strong growth" not in bare
    assert mainview.growth_safe("Review segment g3.", [g["id"]], by_id, r_by_id) == "Review segment g3."


# ------------------------------------------------------------------ 3. units
def test_a_bare_rate_figure_gets_its_percent_sign():
    st = {"context": {"currency": ""}}
    assert mainview.with_unit("segment a averages 15.1 and segment b 9", "discount_pct", st) == "segment a averages 15.1% and segment b 9"
    assert mainview.with_unit("segment a averages 15.1%", "discount_pct", st) == "segment a averages 15.1%"
    assert mainview.with_unit("segment a averages 2.1× the discount_pct of b", "discount_pct", st) == "segment a averages 2.1× the discount_pct of b"
    assert mainview.with_unit("segment a averages 15.1", "quantity", st) == "segment a averages 15.1"


def test_a_bare_money_figure_gets_its_currency():
    st = {"context": {"currency": "₹"}}
    assert mainview.with_unit("segment a averages 1,200", "revenue", st) == "segment a averages ₹1,200"
    assert mainview.with_unit("segment a averages ₹1,200", "revenue", st) == "segment a averages ₹1,200"
