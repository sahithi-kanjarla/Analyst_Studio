import os
import tempfile

os.environ["LLM_PROVIDER"] = "none"
os.environ["GLOSSARY_PATH"] = os.path.join(tempfile.mkdtemp(), "glossary.json")

import ast  # noqa: E402
import json  # noqa: E402
import re  # noqa: E402
import time  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import clarify, directives as dv, glossary, llm as llm_mod, runner, signals  # noqa: E402
from app.cleaner import apply_directives, coerce_types  # noqa: E402
from app.config import RUNS_DIR  # noqa: E402
from app.main import app  # noqa: E402
from app.profiler import profile_df  # noqa: E402

from domains import DOMAINS, clean_table, hospital_stays  # noqa: E402

cl = TestClient(app)


@pytest.fixture(autouse=True)
def fresh_glossary():
    p = glossary.glossary_path()
    if p.exists():
        p.unlink()
    yield
    llm_mod.reset_cache()


def prep(df):
    prof = profile_df(df)
    typed, _ = coerce_types(df, prof)
    return typed, prof


def build(df, goal="Explore this data", llm=None, ctx=None, explore=True):
    typed, prof = prep(df)
    return clarify.build_questions(typed, prof, goal, ctx or {}, explore, llm, None), typed, prof


def kinds(qs):
    return [q["kind"] for q in qs if not q.get("auto")]


class Msg:
    def __init__(self, content):
        self.content = content


class Writer:
    """A fake question-writing model. `make(signals) -> list of questions` is its answer; `raw` replaces the whole reply. Other agents get non-JSON."""

    def __init__(self, make=None, raw=None):
        self.make, self.raw, self.prompt = make, raw, ""

    def invoke(self, messages):
        if "Clarifying-question writer" not in messages[0][1]:
            return Msg("not json")
        self.prompt = messages[1][1]
        if self.raw is not None:
            return Msg(self.raw)
        sigs = json.loads(self.prompt.split("SIGNALS:\n", 1)[1])
        return Msg(json.dumps({"questions": self.make(sigs)}))


def ideal(sigs):
    """What a good model writes: one meaning-changing question per strong signal."""
    out = []
    for s in sigs:
        c, ev = s["columns"], s["evidence"]
        if s["type"] == "multi_date":
            out.append({"text": "Which of these dates marks the event you report on?", "why": "Two dates, one timeline.", "signal": s["id"], "columns": c,
                        "options": [{"label": d, "action": {"type": "set_date_column", "column": d}} for d in c], "default": c[0]})
        elif s["type"] == "negatives":
            out.append({"text": f"Are the negative values in {c[0]} real?", "why": "They lower totals.", "signal": s["id"], "columns": c,
                        "options": [{"label": "Yes, keep", "action": {"type": "handle_negatives", "column": c[0], "mode": "keep"}},
                                    {"label": "No, drop those rows", "action": {"type": "handle_negatives", "column": c[0], "mode": "exclude"}}], "default": "Yes, keep"})
        elif s["type"] == "sentinel":
            out.append({"text": f"Does {ev['values'][0]['value']} in {c[0]} mean 'no value'?", "why": "A repeated code distorts averages.", "signal": s["id"], "columns": c,
                        "options": [{"label": "It is real", "action": {"type": "note_only"}},
                                    {"label": "It is a placeholder", "action": {"type": "treat_value_as_missing", "column": c[0], "values": [h["value"] for h in ev["values"]]}}], "default": "It is real"})
        elif s["type"] == "near_sum":
            out.append({"text": f"Is {c[0]} just the others added up?", "why": "Counting both double counts.", "signal": s["id"], "columns": c,
                        "options": [{"label": "No", "action": {"type": "note_only"}}, {"label": "Yes, leave it out", "action": {"type": "drop_column", "column": c[0]}}], "default": "No"})
    return out


def junk(first_col):
    """Everything a careless model might add on top."""
    note = {"type": "note_only"}
    return [
        {"text": "Is ghost_col in dollars?", "why": "x", "columns": ["ghost_col"], "options": [{"label": "a", "action": note}, {"label": "b", "action": note}], "default": "a"},
        {"text": f"Should {first_col} be run through custom code?", "why": "x", "columns": [first_col],
         "options": [{"label": "a", "action": {"type": "run_python", "code": "import os"}}, {"label": "b", "action": note}], "default": "b"},
        {"text": f"Drop {first_col} using a bad parameter?", "why": "x", "columns": [first_col],
         "options": [{"label": "a", "action": {"type": "drop_column", "column": "phantom"}}, {"label": "b", "action": note}], "default": "b"},
        {"text": "Which chart should we show first?", "why": "x", "columns": [first_col], "options": [{"label": "bar", "action": note}, {"label": "line", "action": note}], "default": "bar"},
        {"text": "Which time frame do you want to look at?", "why": "x", "columns": [first_col], "options": [{"label": "month", "action": note}, {"label": "year", "action": note}], "default": "year"},
    ]


# --------------------------------------------------------------------------------------- signals

EXPECTED = {   # strong questions the no-LLM templates must raise for each domain
    "hr": {"multi_date", "negatives", "sentinel"},
    "hospital": {"multi_date", "negatives", "sentinel"},
    "shipments": {"multi_date", "negatives", "sentinel"},
    "exams": {"sentinel"},
    "sensors": {"sentinel"},
    "survey": {"sentinel"},
}


@pytest.mark.parametrize("name", list(DOMAINS))
def test_templates_ask_sensible_questions_on_any_domain(name):
    qs, typed, prof = build(DOMAINS[name]())
    asked = {q["kind"] for q in qs if q["strong"] and not q.get("auto")}
    assert asked == EXPECTED[name], (name, kinds(qs))
    assert len(qs) <= clarify.MAX_QUESTIONS
    for q in qs:
        assert q["why"] and q["text"].endswith("?") and q["id"].startswith(q["kind"] + ":") and 2 <= len(q["options"]) <= 6
        assert dv.is_harmless(clarify.option_for(q, q["default"])["action"])


def test_signed_readings_do_not_trigger_a_negatives_question():
    typed, prof = prep(DOMAINS["sensors"]())
    assert "negatives" not in {s["type"] for s in signals.compute_signals(typed, prof)}


def test_weak_signals_only_ride_along_when_something_stronger_is_asked():
    qs, _, _ = build(DOMAINS["exams"]())
    assert {"near_sum", "rare_labels", "multi_measure"} <= set(kinds(qs))        # exams has a strong placeholder, so the weak questions are included
    df = DOMAINS["exams"]().assign(attendance=lambda d: d["attendance"].where(d["attendance"] != 999, 70.0))
    qs, _, _ = build(df)
    assert qs == []                                                              # only weak signals left: the run is never paused for them


def test_unambiguous_data_has_no_questions():
    assert build(clean_table())[0] == []


def test_signals_come_from_values_alone_not_from_names():
    typed, prof = prep(DOMAINS["hr"]())
    renamed = typed.rename(columns={c: f"c{i}" for i, c in enumerate(typed.columns)})
    a = {s["type"] for s in signals.compute_signals(typed, prof)}
    b = {s["type"] for s in signals.compute_signals(renamed, profile_df(renamed))}
    assert a == b >= {"multi_date", "negatives", "sentinel", "near_sum", "multi_measure"}
    assert {"duplicate_columns", "sentinel"} <= {s["type"] for s in signals.compute_signals(*prep(DOMAINS["shipments"]()))}
    assert "rare_labels" in {s["type"] for s in signals.compute_signals(*prep(DOMAINS["exams"]()))}
    assert "high_missing" in {s["type"] for s in signals.compute_signals(*prep(DOMAINS["sensors"]()))}


def test_ids_are_stable_across_runs_and_row_order():
    a, _, _ = build(DOMAINS["hospital"]())
    b, _, _ = build(DOMAINS["hospital"]().sample(frac=1, random_state=3).reset_index(drop=True))
    assert [q["id"] for q in a] == [q["id"] for q in b]
    assert len({signals.signal_id("negatives", ["x"]), signals.signal_id("negatives", ["y"]), signals.signal_id("sentinel", ["y"])}) == 3


def test_goal_naming_a_date_settles_the_date_question():
    qs, _, _ = build(hospital_stays(), goal="How does cost change by discharged date?", explore=False)
    auto = next(q for q in qs if q["kind"] == "multi_date")
    assert auto["auto"] == "goal" and auto["default"] == "discharged" and "multi_date" not in kinds(qs)


# --------------------------------------------------------------------------------------- the LLM writer

@pytest.mark.parametrize("name", list(DOMAINS))
def test_fake_llm_questions_are_validated_on_every_domain(name):
    df = DOMAINS[name]()
    typed, prof = prep(df)
    sigs = signals.compute_signals(typed, prof)
    good = ideal(sigs)
    qs, dropped = clarify.llm_questions(Writer(lambda s: junk(df.columns[0]) + good + good[:1]), prof, sigs, "Explore", {}, None)
    assert [q["id"] for q in qs] == [g["signal"] for g in good]                   # valid ones kept, in order, ids from the signals
    assert len(dropped) == 5 + 1 and sum("scope" in d for d in dropped) == 2 and sum(d.startswith("duplicate") for d in dropped) == 1
    assert all(dv.is_harmless(clarify.option_for(q, q["default"])["action"]) for q in qs)
    assert all(not q["id"].startswith("extra") for q in qs)


def test_prompt_carries_profile_signals_goal_context_and_glossary():
    w = Writer(lambda s: [])
    typed, prof = prep(DOMAINS["hospital"]())
    sigs = signals.compute_signals(typed, prof)
    glossary.merge_answers("acme", {"x": "y"}, {"x": "Old question"}, definitions={"readmission": "within 30 days"})
    clarify.llm_questions(w, prof, sigs, "Why are stays long?", {"company": "Acme", "industry": "Healthcare"}, glossary.get_workspace("acme"))
    for needle in ("Why are stays long?", "Acme", "PROFILE", "SIGNALS", "multi_date", "Old question", "within 30 days", "stay_days"):
        assert needle in w.prompt


def test_llm_that_answers_with_garbage_falls_back_to_templates():
    df = DOMAINS["hospital"]()
    for raw in ("no json at all", json.dumps({"questions": "nope"}), json.dumps({"questions": junk("cost")})):
        qs, _, _ = build(df, llm=Writer(raw=raw))
        assert {"multi_date", "negatives", "sentinel"} <= set(kinds(qs))


def test_llm_saying_there_is_nothing_to_ask_is_respected():
    qs, _, _ = build(DOMAINS["hospital"](), llm=Writer(raw=json.dumps({"questions": []})))
    assert qs == []


def test_llm_questions_without_a_signal_get_hash_ids_duplicates_merge_and_the_cap_is_six():
    typed, prof = prep(DOMAINS["hospital"]())
    same = [{"text": f"Does reading {i} of cost count?", "why": "x", "columns": ["cost"],
             "options": [{"label": "yes", "action": {"type": "note_only"}}, {"label": f"no {i}", "action": {"type": "drop_column", "column": "cost"}}], "default": "yes"} for i in range(9)]
    qs, _ = clarify.validate_all(same, prof, [], limit=clarify.MAX_QUESTIONS)
    assert len(qs) == 1                                                            # all nine ask the same thing about the same column: merged
    distinct = [{"text": f"Is {c} measured the same way throughout?", "why": "x", "columns": [c],
                 "options": [{"label": "yes", "action": {"type": "note_only"}}, {"label": "no", "action": {"type": "drop_column", "column": c}}]} for c in typed.columns]
    qs, _ = clarify.validate_all(distinct, prof, [], limit=clarify.MAX_QUESTIONS)
    assert len(qs) == 6 and all(q["id"].startswith("extra:") for q in qs)


# --------------------------------------------------------------------------------------- the validator

ROLES = {"d1": "date", "d2": "date", "m1": "numeric", "m2": "numeric", "cat": "categorical"}
PROFILE = {"rows": 10, "cols": 5, "columns": [{"name": n, "role": r, "missing_pct": 0, "unique": 5, "stats": {}} for n, r in ROLES.items()]}


@pytest.mark.parametrize("action", [
    {"type": "run_python"}, {"type": "set_date_column", "column": "m1"}, {"type": "set_date_column", "column": "ghost"}, {"type": "set_measures", "columns": ["cat"]},
    {"type": "set_measures", "columns": []}, {"type": "define_derived_metric", "kind": "ratio", "a": "m1", "b": "m2"}, {"type": "define_derived_metric", "kind": "difference", "a": "m1", "b": "m1"},
    {"type": "filter_rows", "column": "cat", "operator": "like", "value": "x"}, {"type": "filter_rows", "column": "cat", "operator": "in", "value": "x"},
    {"type": "filter_rows", "column": "cat", "operator": "eq", "value": {"a": 1}}, {"type": "treat_value_as_missing", "column": "m1", "values": []},
    {"type": "handle_negatives", "column": "m1", "mode": "net_against_column"}, {"type": "handle_negatives", "column": "m1", "mode": "delete"},
    {"type": "handle_negatives", "column": "cat", "mode": "keep"}, {"type": "drop_column", "column": "nope"}, {"type": "merge_columns", "columns": ["m1"]},
    {"type": "merge_columns", "columns": ["cat", "m1"], "how": "sum"}, {"type": "merge_columns", "columns": ["m1", "m2"], "how": "eval"}, "drop_column", None,
])
def test_invalid_actions_are_rejected(action):
    assert dv.validate_action(action, ROLES)[0] is None


def test_valid_actions_are_cleaned_to_whitelisted_parameters():
    a, _ = dv.validate_action({"type": "filter_rows", "column": "cat", "operator": "not_in", "value": ["x", "y"], "code": "boom"}, ROLES)
    assert a == {"type": "filter_rows", "column": "cat", "operator": "not_in", "value": ["x", "y"]}
    a, _ = dv.validate_action({"type": "define_derived_metric", "kind": "ratio_of_sums", "a": "m1", "b": "m2", "name": "m1"}, ROLES)
    assert a["name"] not in ROLES and a["use_as_main"] is True                      # a new column never overwrites a real one


def question(options, default=None, text="Is m1 measured the same way throughout?", **kw):
    return {"text": text, "why": "x", "columns": ["m1"], "options": options, "default": default, **kw}


def opt(label, **action):
    return {"label": label, "action": action}


def test_default_that_would_change_data_is_rewritten_or_the_question_dropped():
    q, _ = clarify.validate_question(question([opt("drop", type="drop_column", column="m1"), opt("keep", type="note_only")], default="drop"), PROFILE, {})
    assert clarify.option_for(q, q["default"])["label"] == "keep"
    q, why = clarify.validate_question(question([opt("a", type="drop_column", column="m1"), opt("b", type="drop_column", column="m2")], default="a"), PROFILE, {})
    assert q is None and "default" in why


@pytest.mark.parametrize("text", ["Which chart do you prefer?", "What time frame matters?", "Which segment should we focus on?", "Do you want a dashboard view?", "Which column should we analyse?"])
def test_scope_questions_are_dropped(text):
    q, why = clarify.validate_question(question([opt("a", type="note_only"), opt("b", type="note_only")], text=text), PROFILE, {})
    assert q is None and "scope" in why


def test_definition_questions_are_not_mistaken_for_scope_questions():
    for text in ("Which date should be used for time analysis?", "Does 999 in m1 mean no value?", "Which is the main number you care about?"):
        q, _ = clarify.validate_question(question([opt("a", type="note_only"), opt("b", type="note_only")], text=text), PROFILE, {})
        assert q is not None, text


# --------------------------------------------------------------------------------------- the executor: every directive changes the data and is logged

def mkq(qid, *options, default=None):
    return {"id": qid, "kind": "extra", "text": f"Question {qid}?", "why": "x", "default": default or options[0][0], "columns": [], "signal": None, "strong": True,
            "options": [{"value": v, "label": v, "action": a} for v, a in options]}


def run_directive(df, action):
    roles = {c: ("numeric" if pd.api.types.is_numeric_dtype(df[c]) else "date" if pd.api.types.is_datetime64_any_dtype(df[c]) else "categorical") for c in df.columns}
    a, why = dv.validate_action(action, roles)
    assert a is not None, why
    q = mkq("q", ("note", {"type": "note_only"}), ("go", a), default="note")
    out, log, eff, added = apply_directives(df, [q], {"q": "go"})
    return out, log, eff.get("q"), added


def small():
    return pd.DataFrame({"when": pd.to_datetime(["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-04", "2025-01-05", "2025-01-06"]),
                         "a": [10.0, 20.0, -5.0, 40.0, 999.0, 60.0], "b": [1.0, 2.0, 0.0, 4.0, 5.0, 0.0], "c": [None, 7.0, 8.0, None, 9.0, 1.0],
                         "k": ["x", "y", "x", "z", "y", "x"]})


def test_directive_treat_value_as_missing():
    out, log, eff, _ = run_directive(small(), {"type": "treat_value_as_missing", "column": "a", "values": [999]})
    assert out["a"].isna().sum() == 1 and pd.isna(out.loc[4, "a"]) and eff["rows"] == 1 and log[0]["count"] == 1 and "set to missing" in log[0]["detail"]
    out, log, eff, _ = run_directive(small(), {"type": "treat_value_as_missing", "column": "k", "values": ["X", "z"]})
    assert out["k"].isna().sum() == 4 and eff["rows"] == 4                          # text matches ignore case and spaces


@pytest.mark.parametrize("action, expect_rows", [
    ({"type": "filter_rows", "column": "k", "operator": "ne", "value": "x"}, 3), ({"type": "filter_rows", "column": "k", "operator": "eq", "value": "x"}, 3),
    ({"type": "filter_rows", "column": "k", "operator": "not_in", "value": ["x", "y"]}, 1), ({"type": "filter_rows", "column": "k", "operator": "in", "value": ["x", "y"]}, 5),
    ({"type": "filter_rows", "column": "a", "operator": "gt", "value": 0}, 5), ({"type": "filter_rows", "column": "a", "operator": "le", "value": 20}, 3),
    ({"type": "filter_rows", "column": "c", "operator": "not_null"}, 4), ({"type": "filter_rows", "column": "c", "operator": "is_null"}, 2),
    ({"type": "filter_rows", "column": "when", "operator": "ge", "value": "2025-01-04"}, 3),
])
def test_directive_filter_rows_keeps_matching_rows(action, expect_rows):
    df = small()
    out, log, eff, _ = run_directive(df, action)
    assert len(out) == expect_rows and eff["rows"] == len(df) - expect_rows and log[0]["count"] == len(df) - expect_rows and log[0]["step"] == "Definition applied"


def test_filter_that_would_remove_everything_is_skipped_and_logged():
    out, log, eff, _ = run_directive(small(), {"type": "filter_rows", "column": "k", "operator": "eq", "value": "nothing"})
    assert len(out) == 6 and "every row" in log[0]["detail"] and eff["rows"] == 0


def test_directive_handle_negatives():
    df = small()
    out, log, eff, _ = run_directive(df, {"type": "handle_negatives", "column": "a", "mode": "exclude"})
    assert len(out) == 5 and (out["a"] >= 0).all() and eff["rows"] == 1 and log[0]["count"] == 1
    out, log, eff, _ = run_directive(df, {"type": "handle_negatives", "column": "a", "mode": "keep"})
    assert out.equals(df) and log == [] and eff["rows"] == 1
    out, log, eff, added = run_directive(df, {"type": "handle_negatives", "column": "a", "mode": "net_against_column", "against": "b", "name": "a_net"})
    assert added == ["a_net"] and out["a_net"].tolist() == [9.0, 18.0, -5.0, 36.0, 994.0, 60.0] and out["a"].equals(df["a"]) and eff["rows"] == 4 and log[0]["count"] == 4


def test_directive_derived_metric():
    out, log, eff, added = run_directive(small(), {"type": "define_derived_metric", "kind": "difference", "a": "a", "b": "b", "name": "gap"})
    assert added == ["gap"] and out["gap"].tolist() == [9.0, 18.0, -5.0, 36.0, 994.0, 60.0] and log[0]["count"] == 6
    out, log, eff, added = run_directive(small(), {"type": "define_derived_metric", "kind": "ratio_of_sums", "a": "a", "b": "b", "name": "ratio"})
    assert out["ratio"].isna().sum() == 2 and "Overall ratio of sums" in eff["effect"] and f"{1124 / 12:.4g}" in eff["effect"]    # rows with b == 0 stay missing; the overall figure is sum/sum


def test_directive_merge_columns():
    out, log, eff, added = run_directive(small(), {"type": "merge_columns", "columns": ["c", "b"], "name": "cb", "how": "first_non_null"})
    assert added == ["cb"] and out["cb"].tolist() == [1.0, 7.0, 8.0, 4.0, 9.0, 1.0] and "left out of the analysis" in eff["effect"]
    out, log, eff, added = run_directive(small(), {"type": "merge_columns", "columns": ["c", "b"], "name": "cb", "how": "sum"})
    assert out["cb"].tolist() == [1.0, 9.0, 8.0, 4.0, 14.0, 1.0]


def test_directive_drop_column_and_plan_level_directives_report_effects_and_overrides():
    df = small()
    q = [mkq("d", ("keep", {"type": "note_only"}), ("drop", {"type": "drop_column", "column": "c"}), default="keep"),
         mkq("t", ("auto", {"type": "note_only"}), ("when", {"type": "set_date_column", "column": "when"}), default="auto"),
         mkq("m", ("auto", {"type": "note_only"}), ("ab", {"type": "set_measures", "columns": ["a", "b"]}), default="auto")]
    out, log, eff, added = apply_directives(df, q, {"d": "drop", "t": "when", "m": "ab"})
    assert eff["d"]["rows"] == 4 and log[0]["count"] == 4 and eff["t"]["rows"] == 6 and "'a' is the main number, analysed alongside b" in eff["m"]["effect"]
    ov = clarify.plan_overrides(q, {"d": "drop", "t": "when", "m": "ab"})
    assert ov == {"target": "a", "date_col": "when", "exclude": ["c"], "include": ["b"]}


def test_note_only_changes_nothing_and_goes_to_the_llm_as_context():
    q = [mkq("n", ("yes", {"type": "note_only"}), ("no", {"type": "note_only"}), default="yes")]
    out, log, eff, _ = apply_directives(small(), q, {"n": "no"})
    assert out.equals(small()) and log == [] and clarify.note_lines(q, {"n": "no"}) == ["Question n? -> no"] and clarify.note_lines(q, {}) == []


def test_missing_column_is_reported_not_crashed():
    q = [mkq("g", ("keep", {"type": "note_only"}), ("drop", {"type": "drop_column", "column": "gone"}), default="keep")]
    out, log, eff, _ = apply_directives(small(), q, {"g": "drop"})
    assert out.equals(small()) and "not found" in log[0]["detail"]


# --------------------------------------------------------------------------------------- skipping changes nothing

@pytest.mark.parametrize("name", list(DOMAINS))
@pytest.mark.parametrize("writer", ["templates", "fake_llm"])
def test_default_answers_leave_the_data_untouched(name, writer):
    df = DOMAINS[name]()
    typed, prof = prep(df)
    llm = Writer(lambda s: junk(df.columns[0]) + ideal(s)) if writer == "fake_llm" else None
    qs = clarify.build_questions(typed, prof, "Explore", {}, True, llm, None)
    assert qs
    answers, sources = clarify.resolve(qs, {q["id"]: q["options"][-1]["value"] for q in qs}, {}, skip=True)       # a skip ignores whatever was picked
    out, log, eff, added = apply_directives(typed, qs, answers)
    pd.testing.assert_frame_equal(out, typed)
    assert added == [] and not log
    ov = clarify.plan_overrides(qs, answers)
    assert ov["exclude"] == [] and set(sources.values()) <= {"default", "goal"}


# --------------------------------------------------------------------------------------- the code has no domain vocabulary

DOMAIN_WORDS = {"refund", "refunds", "return", "returns", "returned", "cancel", "cancelled", "canceled", "cancellation", "discount", "discounts", "status", "state", "stage", "test", "amount",
                "revenue", "segment", "segments", "price", "prices", "sales", "profit", "customer", "customers", "order", "orders", "churn", "salary", "invoice", "shipment", "delay",
                "patient", "student", "employee", "score", "cost", "quantity", "region", "product", "ticket", "transaction", "payment"}


def test_question_logic_has_no_domain_words():
    """Identifiers and string literals (docstrings aside) in the question logic must not mention any business domain."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    found = {}
    for fname in ("clarify.py", "signals.py", "directives.py"):
        tree = ast.parse((app_dir / fname).read_text(encoding="utf-8"))
        docs = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body and isinstance(n.body[0], ast.Expr)
                and isinstance(n.body[0].value, ast.Constant) and isinstance(n.body[0].value.value, str)}
        texts = []
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
                texts.append(n.value)
            elif isinstance(n, ast.Name):
                texts.append(n.id)
            elif isinstance(n, ast.Attribute):
                texts.append(n.attr)
            elif isinstance(n, (ast.FunctionDef, ast.ClassDef)):
                texts.append(n.name)
            elif isinstance(n, ast.arg):
                texts.append(n.arg)
            elif isinstance(n, ast.keyword) and n.arg and n.arg != "random_state":      # a pandas parameter name, not a domain word
                texts.append(n.arg)
        hit = DOMAIN_WORDS & set(re.findall(r"[a-z]+", " ".join(texts).lower().replace("_", " ")))
        if hit:
            found[fname] = sorted(hit)
    assert not found, f"domain words in question logic: {found}"


# --------------------------------------------------------------------------------------- glossary store

def test_glossary_roundtrip_limits_and_lock_cleanup():
    glossary.merge_answers("Acme Ltd", {"date_col": "ship_date"}, {"date_col": "Which date?"}, notes="fiscal year starts in April", definitions={"active customer": "bought in 90 days"})
    ws = glossary.get_workspace("  ACME   ltd ")
    assert ws["answers"]["date_col"]["value"] == "ship_date" and ws["definitions"] == {"active customer": "bought in 90 days"} and ws["notes"].startswith("fiscal")
    assert not glossary.glossary_path().with_suffix(".json.lock").exists()
    assert glossary.validate_entry({"notes": "x" * 501}) and glossary.validate_entry({"definitions": {"t": "y" * 301}})
    assert glossary.validate_entry({"definitions": {f"t{i}": "d" for i in range(21)}})
    assert glossary.validate_entry({"notes": "ok", "definitions": {"a": "b"}, "answers": {"q": "v"}}) is None
    assert glossary.delete_workspace("acme ltd") and glossary.get_workspace("acme ltd") == {"answers": {}, "definitions": {}, "notes": ""}


# --------------------------------------------------------------------------------------- full runs through the API

def wait(rid, want=("done", "error", "needs_input"), timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = runner.read_status(rid)["status"]
        if st in want:
            return st
        time.sleep(0.3)
    raise AssertionError("timeout")


def start(df, goal="Explore this data", company="Acme", name="data.csv"):
    r = cl.post("/api/runs", data={"goal": goal, "company": company}, files=[("files", (name, df.to_csv(index=False).encode()))])
    assert r.status_code == 200
    return r.json()["run_id"]


def result(rid):
    return json.loads((RUNS_DIR / rid / "result.json").read_text())


def qmap(rid):
    return {q["kind"]: q for q in cl.get(f"/api/runs/{rid}/questions").json()["questions"]}


def val(q, label_start):
    return next(o["value"] for o in q["options"] if o["label"].startswith(label_start))


def test_ambiguous_run_pauses_then_answers_change_the_data_and_the_report():
    df = hospital_stays()
    rid = start(df)
    assert wait(rid) == "needs_input"
    assert not (RUNS_DIR / rid / "result.json").exists()
    q = qmap(rid)
    assert {"multi_date", "negatives", "sentinel"} <= set(q)

    assert cl.post(f"/api/runs/{rid}/answers", json={"answers": {"nope": "x"}}).status_code == 400
    assert cl.post(f"/api/runs/{rid}/answers", json={"answers": {q["negatives"]["id"]: "bogus"}}).status_code == 400
    assert cl.post(f"/api/runs/{rid}/answers", json={"notes": "x" * 600}).status_code == 422

    body = {"answers": {q["multi_date"]["id"]: "discharged", q["negatives"]["id"]: "exclude", q["sentinel"]["id"]: "missing"},
            "notes": "Costs are in thousands.", "definitions": {"readmission": "back within 30 days"}}
    assert cl.post(f"/api/runs/{rid}/answers", json=body).status_code == 200
    assert cl.post(f"/api/runs/{rid}/answers", json=body).status_code == 409           # a double submit cannot start a second run
    assert wait(rid) == "done"
    res = result(rid)

    assert res["plan"]["date_col"] == "discharged"
    clean = pd.read_pickle(RUNS_DIR / rid / "clean.pkl")
    n_neg, n_sent = int((df["cost"] < 0).sum()), int((df["stay_days"] == 9999).sum())
    assert len(clean) == len(df) - n_neg and (clean["cost"] >= 0).all()
    assert (clean["stay_days"] == 9999).sum() == 0
    steps = [s for s in res["cleaning_log"] if s["step"] == "Definition applied"]
    assert {s["count"] for s in steps} >= {n_neg, n_sent}
    md = res["report_md"]
    assert "## Definitions used" in md and "Rows affected" in md and "not applied to the data" in md and "back within 30 days" in md
    by = {d["id"]: d for d in res["applied_definitions"]}
    assert by[q["negatives"]["id"]]["source"] == "user" and by[q["negatives"]["id"]]["rows"] == n_neg
    evs = [json.loads(line) for line in (RUNS_DIR / rid / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(e["agent"] == "Profiler" for e in evs) == 2 and sum(e["agent"] == "Clarifier" for e in evs) == 2      # one pass of profiler events (two lines), nothing repeated after the resume

    rid2 = start(df)                                                                    # same company: pre-filled from the glossary, nothing to ask
    assert wait(rid2) == "done"
    by2 = {d["id"]: d for d in result(rid2)["applied_definitions"]}
    assert by2[q["multi_date"]["id"]]["answer"] == "discharged" and by2[q["multi_date"]["id"]]["source"] == "glossary"
    assert result(rid2)["plan"]["date_col"] == "discharged"
    assert len(pd.read_pickle(RUNS_DIR / rid2 / "clean.pkl")) == len(df) - n_neg
    saved = cl.get("/api/glossary", params={"workspace": "acme"}).json()
    assert saved["definitions"]["readmission"].startswith("back") and saved["notes"].startswith("Costs")


def test_glossary_prefills_partially_answered_questions():
    df = hospital_stays()
    rid = start(df, company="Beta")
    assert wait(rid) == "needs_input"
    first = qmap(rid)
    glossary.merge_answers("beta", {first["multi_date"]["id"]: "discharged"}, {})
    rid2 = start(df, company="Beta")
    assert wait(rid2) == "needs_input"                                                  # the other questions are still open
    q = qmap(rid2)
    assert q["multi_date"]["prefill"] == "discharged" and q["multi_date"]["prefill_source"] == "glossary" and q["negatives"]["prefill_source"] == "default"


def test_skip_uses_defaults_and_changes_nothing():
    df = hospital_stays()
    rid = start(df, company="Gamma")
    assert wait(rid) == "needs_input"
    q = qmap(rid)
    assert cl.post(f"/api/runs/{rid}/answers", json={"skip": True, "answers": {q["negatives"]["id"]: "exclude"}}).status_code == 200
    assert wait(rid) == "done"
    res = result(rid)
    clean = pd.read_pickle(RUNS_DIR / rid / "clean.pkl")
    assert len(clean) == len(df) and (clean["stay_days"] == 9999).sum() == (df["stay_days"] == 9999).sum() and (clean["cost"] < 0).sum() == (df["cost"] < 0).sum()
    assert not [s for s in res["cleaning_log"] if s["step"] == "Definition applied" and s["count"]]
    assert all(d["source"] in ("default", "goal") for d in res["applied_definitions"])
    assert glossary.get_workspace("gamma")["answers"] == {}                             # skipping saves nothing


def test_goal_that_names_the_date_does_not_pause_for_it():
    df = hospital_stays().drop(columns=["cost", "stay_days"])
    rid = start(df, goal="How do readmissions change by discharged date?", company="Zeta")
    assert wait(rid) == "done"                                                          # the only strong ambiguity was settled by the goal
    d = next(x for x in result(rid)["applied_definitions"] if x["source"] == "goal")
    assert d["answer"] == "discharged" and result(rid)["plan"]["date_col"] == "discharged"


def test_unambiguous_run_never_blocks_and_has_no_definitions_section():
    rid = start(clean_table(), goal="Why do visits change over time?", company="")
    assert wait(rid) == "done"
    assert not (RUNS_DIR / rid / "questions.json").exists() and "Definitions used" not in result(rid)["report_md"]
    assert cl.get(f"/api/runs/{rid}/questions").status_code == 404
    assert cl.post(f"/api/runs/{rid}/answers", json={}).status_code == 404


def test_glossary_endpoints():
    assert cl.get("/api/glossary").json() == {"workspaces": {}}
    r = cl.put("/api/glossary", params={"workspace": "Delta"}, json={"answers": {"date_col": "ship_date"}, "definitions": {"churn": "no order in 6 months"}, "notes": "n"})
    assert r.status_code == 200 and r.json()["workspace"] == "delta" and r.json()["answers"]["date_col"]["value"] == "ship_date"
    assert cl.get("/api/glossary", params={"workspace": "delta"}).json()["definitions"] == {"churn": "no order in 6 months"}
    assert cl.put("/api/glossary", params={"workspace": "delta"}, json={"notes": "x" * 501}).status_code == 422
    edited = cl.get("/api/glossary", params={"workspace": "delta"}).json()
    edited["definitions"].pop("churn")
    assert cl.put("/api/glossary", params={"workspace": "delta"}, json=edited).json()["definitions"] == {}
    assert cl.delete("/api/glossary", params={"workspace": "delta"}).status_code == 200
    assert cl.delete("/api/glossary", params={"workspace": "delta"}).status_code == 404


def test_run_with_fake_llm_asks_only_valid_questions_and_applies_the_answer():
    df = hospital_stays()
    fake = Writer(lambda s: junk("cost") + ideal(s))
    llm_mod._cache["llm"] = (fake, "fake:model")
    rid = start(df, company="Epsilon")
    assert wait(rid) == "needs_input"
    qs = cl.get(f"/api/runs/{rid}/questions").json()["questions"]
    assert {q["kind"] for q in qs} == {"multi_date", "negatives", "sentinel"} and all(q["id"] == q["signal"] for q in qs)
    dumped = json.dumps(qs)
    assert "ghost_col" not in dumped and "chart" not in dumped and "run_python" not in dumped
    neg = next(q for q in qs if q["kind"] == "negatives")
    cl.post(f"/api/runs/{rid}/answers", json={"answers": {neg["id"]: val(neg, "No")}})
    assert wait(rid) == "done"
    assert len(pd.read_pickle(RUNS_DIR / rid / "clean.pkl")) == len(df) - int((df["cost"] < 0).sum())


def test_rules_still_work_with_no_llm():
    assert llm_mod.get_llm()[0] is None
    assert clarify.llm_questions(None, PROFILE, [], "g", {}, None) == (None, [])
    assert build(hospital_stays())[0]
