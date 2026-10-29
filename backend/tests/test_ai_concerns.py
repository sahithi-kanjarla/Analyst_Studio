import os
import tempfile

os.environ["LLM_PROVIDER"] = "none"
os.environ["GLOSSARY_PATH"] = os.path.join(tempfile.mkdtemp(), "glossary.json")

import json  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from app import clarify, concerns  # noqa: E402
from app.cleaner import coerce_types  # noqa: E402
from app.profiler import profile_df  # noqa: E402

GOAL = "How do sales differ by group?"


def table(n=967):
    g = ["Male"] * 480 + ["Female"] * 480 + ["M"] * 4 + ["F"] * 3
    return pd.DataFrame({"group": g[:n], "amount": np.arange(n) * 1.5 + 1.0})


def prep(df):
    prof = profile_df(df)
    typed, _ = coerce_types(df, prof)
    return typed, profile_df(typed)


def merge(col="group", mapping=None, **kw):
    mapping = mapping or {"M": "Male", "F": "Female"}
    return {"column": col, "concern_type": "label_variants", "values": list(mapping), "confidence": "high", "reason": "same label, different spelling",
            "proposed_action": {"type": "merge_labels", "column": col, "mapping": mapping}, **kw}


@pytest.fixture
def ai(monkeypatch):
    """Replace the model: whatever the test sets in box['reply'] is what the AI concern call returns; the question writer gets nothing usable."""
    box = {"reply": None}

    def fake(llm, system, user):
        return box["reply"] if system.startswith("You are the data-quality reviewer") else None
    monkeypatch.setattr(concerns, "ask_json", fake)
    monkeypatch.setattr(clarify, "ask_json", fake)
    return box


def run(df, goal=GOAL):
    typed, prof = prep(df)
    out, dropped = {}, []
    qs, _ = clarify.build_questions_and_ignored(typed, prof, goal, {}, False, object(), None, None, dropped, concerns_out=out)
    return qs, dropped, out


def test_a_label_variants_become_one_question_with_suggestion_and_keep(ai):
    ai["reply"] = {"concerns": [merge()]}
    qs, _, out = run(table())
    q = [x for x in qs if x.get("ai")]
    assert len(q) == 1 and out["path"] == "ai"
    assert [o["value"] for o in q[0]["options"]] == ["apply", "keep"]
    assert q[0]["options"][0]["action"] == {"type": "merge_labels", "column": "group", "mapping": {"M": "Male", "F": "Female"}}
    assert q[0]["options"][1]["action"] == {"type": "note_only"}
    assert q[0]["default"] == "apply" and q[0]["options"][0]["rows"] == 7


def test_b_unlisted_label_is_rejected_with_reason(ai):
    ai["reply"] = {"concerns": [merge(mapping={"Mal": "Male"})]}
    qs, _, out = run(table())
    assert not [x for x in qs if x.get("ai")]
    assert len(out["rejected"]) == 1 and out["rejected"][0].startswith("AI suggestion not applied:") and "'Mal'" in out["rejected"][0]


@pytest.mark.parametrize("bad", [
    {"column": "nope", "concern_type": "label_variants", "values": [], "proposed_action": {"type": "merge_labels", "column": "nope", "mapping": {"a": "b"}}},
    merge(proposed_action={"type": "drop_column", "column": "group"}),
    merge(proposed_action={"type": "rm_rf", "column": "group"}),
    {"column": "group", "concern_type": "invented", "values": [], "proposed_action": None},
    {"column": "amount", "concern_type": "date_order", "values": [], "proposed_action": {"type": "swap_day_month", "column": "amount"}},
    {"column": "group", "concern_type": "missing_code", "values": ["Unknown"], "proposed_action": {"type": "treat_value_as_missing", "column": "group", "values": ["Unknown"]}},
])
def test_b_other_rejections(ai, bad):
    ai["reply"] = {"concerns": [bad]}
    qs, _, out = run(table())
    assert not [x for x in qs if x.get("ai")] and len(out["rejected"]) == 1


def test_c_ai_unavailable_rule_questions_still_appear(ai):
    ai["reply"] = None
    qs, _, out = run(table())
    assert out["path"] == "rules" and out["rejected"] == []
    assert "label_merge" in [q["kind"] for q in qs] and not any(q.get("ai") for q in qs)


def test_c_no_llm_uses_rules():
    typed, prof = prep(table())
    out = {}
    qs, _ = clarify.build_questions_and_ignored(typed, prof, GOAL, {}, False, None, concerns_out=out)
    assert out["path"] == "rules" and out["why_not"] == "no AI configured" and "label_merge" in [q["kind"] for q in qs]


def test_d_more_than_three_concerns_ask_three_and_list_the_rest(ai):
    n = 400
    df = pd.DataFrame({f"c{i}": np.random.default_rng(i).permutation(["Aa"] * 190 + ["Bb"] * 190 + ["A"] * (5 + i) + ["B"] * (5 + i) + ["Aa"] * (10 - 2 * i))[:n] for i in range(5)})
    df["v"] = np.arange(n) * 1.5 + 1.0
    ai["reply"] = {"concerns": [merge(f"c{i}", {"A": "Aa", "B": "Bb"}) for i in range(5)]}
    qs, dropped, out = run(df)
    asked = [q for q in qs if q.get("ai") and not q.get("auto")]
    assert len(out["accepted"]) == 5 and len(asked) == 3
    assert len(dropped) == 2 and all(d["default"].startswith("Apply the AI") for d in dropped)
    assert len([q for q in qs if q.get("auto") == "capped"]) == 2        # a merge is the default, so the capped ones still run


def test_d_ranked_by_goal_relevance_then_rows(ai):
    n = 400
    df = pd.DataFrame({"zone": (["Aa"] * 200 + ["A"] * 10 + ["Bb"] * 190)[:n], "region": (["Aa"] * 200 + ["A"] * 40 + ["Bb"] * 160)[:n], "v": np.arange(n) * 1.5 + 1.0})
    ai["reply"] = {"concerns": [merge("region", {"A": "Aa"}), merge("zone", {"A": "Aa"})]}
    qs, _, _ = run(df, goal="Compare sales by zone")
    assert [q["columns"][0] for q in qs if q.get("ai")][0] == "zone"


def test_e_no_duplicate_question_for_a_column_the_ai_covered(ai):
    ai["reply"] = {"concerns": [merge()]}
    qs, _, _ = run(table())
    on_group = [q for q in qs if q["columns"] == ["group"]]
    assert len(on_group) == 1 and on_group[0].get("ai") and "label_merge" not in [q["kind"] for q in qs if not q.get("ai")]


def test_date_swap_and_missing_code_and_gap_notes(ai):
    n = 300
    df = pd.DataFrame({"when": pd.to_datetime("2020-01-01") + pd.to_timedelta(np.arange(n) * 3, unit="D"), "code": ["x"] * 100 + ["y"] * 100 + ["N/A"] * 100,
                       "qty": np.arange(n) % 5 + 1, "v": np.arange(n) * 1.5 + 1.0})
    ai["reply"] = {"concerns": [
        {"column": "when", "concern_type": "date_order", "values": [], "confidence": "medium", "reason": "few distinct days", "proposed_action": {"type": "swap_day_month", "column": "when"}},
        {"column": "code", "concern_type": "missing_code", "values": ["N/A"], "confidence": "high", "reason": "filler", "proposed_action": {"type": "treat_value_as_missing", "column": "code", "values": ["N/A"]}},
        {"column": "qty", "concern_type": "number_is_category", "values": [], "confidence": "low", "reason": "only five values", "proposed_action": None}]}
    qs, _, out = run(df)
    ai_q = {q["columns"][0]: q for q in qs if q.get("ai")}
    assert ai_q["when"]["default"] == "keep" and ai_q["when"]["options"][1]["action"]["type"] == "swap_day_month"
    assert ai_q["code"]["default"] == "keep" and ai_q["code"]["options"][1]["action"]["values"] == ["N/A"]
    assert len(out["gaps"]) == 1 and out["gaps"][0]["column"] == "qty" and "qty" not in ai_q


def test_f_fact_sheet_is_bounded_on_a_100_column_table():
    rng = np.random.default_rng(1)
    n = 500
    cols = {}
    for i in range(100):
        k = i % 4
        cols[f"col_{i}"] = ([f"label number {j} " + "x" * 30 for j in rng.integers(0, 400, n)] if k == 0 else rng.normal(size=n) if k == 1
                            else rng.integers(0, 12, n) if k == 2 else list(pd.date_range("2020-01-01", periods=n)))
    df = pd.DataFrame(cols)
    typed, prof = prep(df)
    sheet = concerns.fact_sheet(typed, prof, "goal")
    assert len(json.dumps(sheet, ensure_ascii=False, default=str)) <= concerns.FACT_MAX_CHARS
    assert len(sheet["columns"]) + len(sheet["omitted"]) == 100
    for rec in sheet["columns"].values():
        assert len(rec.get("top", [])) <= concerns.TOP_N and len(rec.get("rarest", [])) <= concerns.RARE_N
    assert all("top" not in r for r in sheet["columns"].values() if r["distinct"] > concerns.HIGH_CARD)


def test_fact_sheet_has_no_raw_rows_and_dates_have_counts():
    typed, prof = prep(pd.DataFrame({"d": pd.date_range("2020-01-01", periods=60), "g": ["a", "b"] * 30}))
    s = concerns.fact_sheet(typed, prof)["columns"]
    assert s["d"]["range"] == ["2020-01-01", "2020-02-29"] and s["d"]["distinct_months"] == 2 and s["d"]["distinct_years"] == 1
    assert s["g"]["top"] == [["a", 30], ["b", 30]] and "rarest" not in s["g"]
