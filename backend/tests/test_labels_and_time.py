"""Labels from a column's own name and role (a column of states is a status, not "customer segments"), question text with the real display names, and a time question answered by a trend or not at all."""
import numpy as np
import pandas as pd
import pytest

from app import labels, planner, profiler, summary


def frame(n=90):
    g = np.random.RandomState(5)
    return pd.DataFrame({"Financial Status": g.choice(["paid", "pending", "refunded"], n), "payment_state": g.choice(["settled", "failed"], n), "Region": g.choice(list("NSEW"), n), "Tier": g.choice(["gold", "silver"], n),
                         "net_revenue": np.round(g.gamma(3, 100, n), 2), "order_date": pd.date_range("2024-01-01", periods=n)})


def test_a_column_of_states_is_a_status_by_its_name_or_its_values():
    assert labels.is_status_like("Financial Status", ()) and labels.is_status_like("order_stage", ())
    assert labels.is_status_like("x", ["paid", "pending", "refunded"]) and labels.is_status_like("x", ["Open", "Closed", "On hold"])
    assert not labels.is_status_like("Region", ["North", "South", "East", "West"]) and not labels.is_status_like("Tier", ["gold", "silver"]) and not labels.is_status_like("State", ["Kerala", "Goa", "Assam"])
    assert labels.shown("net_revenue") == "net revenue" and labels.shown("Financial Status") == "Financial Status"


def test_the_questions_call_a_status_column_a_status_and_use_real_names():
    df = frame()
    plan = planner.heuristic_plan(df, profiler.profile_df(df), "", {}, explore=True)
    qs = plan["questions"]
    seg = next(q for q in qs if q.startswith("Which segments"))
    stat = next(q for q in qs if "by status" in q)
    assert "Region" in seg and "Financial Status" not in seg and "Financial Status" in stat and "payment state" in stat       # the underscore name as words
    assert "customer" not in " ".join(qs).lower() and not any("_" in q for q in qs)
    only_status = df.drop(columns=["Region", "Tier"])
    qs2 = planner.heuristic_plan(only_status, profiler.profile_df(only_status), "", {}, explore=True)["questions"]
    assert not any(q.startswith("Which segments") for q in qs2) and any("by status" in q for q in qs2)


def test_a_model_plan_that_calls_a_status_column_a_segment_is_sent_back():
    df = frame()
    prof = profiler.profile_df(df)
    roles = {c["name"]: c["role"] for c in prof["columns"]}
    validate = planner.plan_validator(roles, {c["name"]: c["unique"] for c in prof["columns"]}, set(), 8)
    base = {"analyses": [{"tool": "group_compare", "params": {"target": "net_revenue", "group": "Region"}, "why": "x"}, {"tool": "distribution", "params": {"col": "net_revenue"}, "why": "y"}], "target": "net_revenue", "date_col": "order_date"}
    clean, errs = validate({**base, "questions": ["How does net revenue differ by Financial Status?"]})
    assert not errs, errs
    clean, errs = validate({**base, "questions": ["Which customer segments (Financial Status) earn most?"]})
    assert any("calls a status column a segment" in e for e in errs)
    assert "never call it" in planner.plan_system(6).lower() or "Never call it" in planner.plan_system(6)


def test_an_invented_grouping_phrase_is_found_only_when_no_column_gives_it():
    assert labels.invented_grouping("Customer segments differ by payment", ["Financial Status", "Region"]) == "Customer segments"
    assert labels.invented_grouping("Customer segments differ", ["Customer segment", "Region"]) == ""
    assert labels.invented_grouping("Paid orders lead", ["Financial Status"]) == ""


# ------------------------------------------------------------------ time questions
def items(with_trend: bool):
    base = [{"id": "r1", "text": "Top 3 City by total revenue: Pune", "column": "City", "measure": "revenue", "tool": "ranking", "kind": ""}]
    if with_trend:
        base.append({"id": "f2", "text": "revenue over time: a weak trend", "column": "order_date", "measure": "revenue", "tool": "trend", "kind": ""})
    return base


@pytest.mark.parametrize("with_trend", [True, False])
def test_a_time_question_is_answered_by_a_trend_or_says_there_is_none(monkeypatch, with_trend):
    q = "Is revenue trending up or down over time?"
    state = {"goal": q, "decisions": [], "plan": {"questions": [q]}, "findings": [], "profile": {}}
    monkeypatch.setattr(summary, "questions", lambda s: [q])
    monkeypatch.setattr(summary, "catalog", lambda s, r: items(with_trend))
    monkeypatch.setattr(summary, "goal_pick", lambda *a: [])                     # the rules found nothing by name
    monkeypatch.setattr(summary, "rules_pick", lambda *a: ["r1"])                # the old fallback: a ratio by city
    out = summary.pick(None, state, [])
    assert out["picks"][q] == ([] if not with_trend else [])                     # a split by city is never the answer to a question about time
    if not with_trend:
        assert "no usable trend" in summary.not_answered_reason(q, items(False))
    monkeypatch.setattr(summary, "goal_pick", lambda *a: ["f2"] if with_trend else [])
    assert summary.pick(None, state, [])["picks"][q] == (["f2"] if with_trend else [])


def test_a_model_that_picks_a_split_for_a_time_question_is_overruled(monkeypatch):
    q = "Is revenue growing over time?"
    state = {"goal": q, "decisions": [], "plan": {"questions": [q]}, "findings": [], "profile": {}}
    monkeypatch.setattr(summary, "questions", lambda s: [q])
    monkeypatch.setattr(summary, "catalog", lambda s, r: items(True))
    monkeypatch.setattr(summary, "goal_pick", lambda *a: [])
    monkeypatch.setattr(summary, "llm_pick", lambda *a, **k: {0: ["r1", "f2"]})
    assert summary.pick(object(), state, [])["picks"][q] == ["f2"]
