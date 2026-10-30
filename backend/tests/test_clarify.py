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

from domains import DOMAINS, clean_table, hospital_stays, hr_attrition  # noqa: E402

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


def asked(qs):
    """The questions a person would see: those the app applies by itself (auto) are not asked."""
    return [q for q in qs if not q.get("auto")]


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
    assert {"near_sum", "rare_labels"} <= set(kinds(qs)) and "multi_measure" not in kinds(qs)        # exams has a strong placeholder, so the weak questions are included
    df = DOMAINS["exams"]().assign(attendance=lambda d: d["attendance"].where(d["attendance"] != 999, 70.0))
    qs, _, _ = build(df)
    assert asked(qs) == []                                                       # only weak signals left: the run is never paused for them


def test_unambiguous_data_has_no_questions():
    assert asked(build(clean_table())[0]) == []


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
    assert len(dropped) == 5 + 1 and sum(d.startswith("no answer changes") for d in dropped) == 2 and sum(d.startswith("duplicate") for d in dropped) == 1
    assert all(dv.is_harmless(clarify.option_for(q, q["default"])["action"]) for q in qs)
    assert all(not q["id"].startswith("extra") for q in qs)


def test_prompt_carries_profile_signals_goal_context_and_glossary():
    w = Writer(lambda s: [])
    typed, prof = prep(DOMAINS["hospital"]())
    sigs = signals.compute_signals(typed, prof)
    glossary.merge_answers("acme", {sigs[0]["id"]: "y", "stale:00000000": "z"}, {sigs[0]["id"]: "Old question", "stale:00000000": "Stale question"}, definitions={"readmission": "within 30 days"})
    clarify.llm_questions(w, prof, sigs, "Why are stays long?", {"company": "Acme", "industry": "Healthcare"}, glossary.get_workspace("acme"))
    for needle in ("Why are stays long?", "Acme", "PROFILE", "SIGNALS", "multi_date", "Old question", "within 30 days", "stay_days"):
        assert needle in w.prompt
    assert "Stale question" not in w.prompt                                           # a saved answer about an ambiguity this data does not have must not steer the writer


def test_llm_that_answers_with_garbage_falls_back_to_templates():
    df = DOMAINS["hospital"]()
    for raw in ("no json at all", json.dumps({"questions": "nope"}), json.dumps({"questions": junk("cost")})):
        qs, _, _ = build(df, llm=Writer(raw=raw))
        assert {"multi_date", "negatives", "sentinel"} <= set(kinds(qs))


def test_llm_saying_there_is_nothing_to_ask_is_respected():
    qs, _, _ = build(DOMAINS["hospital"](), llm=Writer(raw=json.dumps({"questions": []})))
    assert asked(qs) == []


def test_llm_questions_without_a_signal_are_dropped_duplicates_merge_and_the_cap_is_three():
    typed, prof = prep(DOMAINS["hospital"]())
    sigs = [{"id": f"x:{c}", "type": "near_sum", "columns": [c], "evidence": {}, "strong": True} for c in typed.columns]

    def q(text, col, sid):
        return {"text": text, "why": "x", "columns": [col], "signal": sid,
                "options": [{"label": "yes", "action": {"type": "note_only"}}, {"label": f"no {text}", "action": {"type": "drop_column", "column": col}}], "default": "yes"}
    no_signal = [q(f"Does reading {i} of cost count?", "cost", None) for i in range(3)] + [q("Is cost real?", "cost", "x:nothing")]
    qs, dropped = clarify.validate_all(no_signal, prof, sigs)
    assert qs == [] and len(dropped) == 4 and all(d.startswith("no signal in the data") for d in dropped)
    same = [q(f"Does reading {i} of cost count?", "cost", "x:cost") for i in range(9)]
    qs, _ = clarify.validate_all(same, prof, sigs, limit=clarify.MAX_QUESTIONS)
    assert len(qs) == 1 and qs[0]["id"] == "x:cost"                                 # all nine ask the same thing about the same signal: merged
    distinct = [q(f"Is {c} measured the same way throughout?", c, f"x:{c}") for c in typed.columns]
    qs, _ = clarify.validate_all(distinct, prof, sigs, limit=clarify.MAX_QUESTIONS)
    assert len(qs) == clarify.MAX_QUESTIONS == 3 and all(x["id"].startswith("x:") for x in qs)
    off_target = q("Is cost real?", "cost", "x:ward")                               # a real signal, but the option acts on a column it is not about
    assert clarify.validate_question(off_target, prof, {x["id"]: x for x in sigs})[0] is None


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


SIGS = {"s": {"id": "s:1", "type": "unrestricted", "columns": list(ROLES), "evidence": {}, "strong": True},
        "m": {"id": "multi_measure:1", "type": "multi_measure", "columns": ["m1", "m2"], "evidence": {}, "strong": False}}


def question(options, default=None, text="Is m1 measured the same way throughout?", **kw):
    return {"text": text, "why": "x", "columns": ["m1"], "options": options, "default": default, "signal": "s", **kw}


def opt(label, **action):
    return {"label": label, "action": action}


def test_default_that_would_change_data_is_rewritten_or_the_question_dropped():
    q, _ = clarify.validate_question(question([opt("drop", type="drop_column", column="m1"), opt("keep", type="note_only")], default="drop"), PROFILE, SIGS)
    assert clarify.option_for(q, q["default"])["label"] == "keep"
    q, why = clarify.validate_question(question([opt("a", type="drop_column", column="m1"), opt("b", type="drop_column", column="m2")], default="a"), PROFILE, SIGS)
    assert q is None and "default" in why


NOTE = {"type": "note_only"}


@pytest.mark.parametrize("options", [
    [opt("bar", type="note_only"), opt("line", type="note_only")],                                                         # nothing is changed whatever is picked
    [opt("a", type="set_measures", columns=["m1"]), opt("b", type="set_measures", columns=["m2"])],                       # alternative columns that can all be analysed
    [opt("any", type="note_only"), opt("a", type="set_measures", columns=["m1"]), opt("b", type="set_measures", columns=["m2"])],
])
def test_questions_whose_answers_change_nothing_are_dropped_whatever_they_say(options):
    for text in ("Which chart do you prefer?", "Is m1 measured the same way throughout?"):          # the wording is irrelevant: the options decide
        q, why = clarify.validate_question(question(options, text=text), PROFILE, SIGS)
        assert q is None and why.startswith("no answer changes")


def test_questions_where_an_answer_changes_the_data_or_the_date_are_kept():
    q, _ = clarify.validate_question(question([opt("a", type="note_only"), opt("b", type="filter_rows", column="cat", operator="ne", value="x")], text="Which segment should we focus on?"), PROFILE, SIGS)
    assert q is not None
    q, _ = clarify.validate_question(question([opt("d1", type="set_date_column", column="d1"), opt("d2", type="set_date_column", column="d2")], text="Which date is the date?"), PROFILE, SIGS)
    assert q is not None


def test_a_question_with_a_made_up_or_measure_signal_is_dropped_even_if_an_answer_changes_the_data():
    drop = [opt("keep", type="note_only"), opt("drop", type="drop_column", column="m1")]
    for sid, start in ((None, "no signal"), ("ghost", "no signal"), ("m", "a multi_measure signal")):
        q, why = clarify.validate_question(question(drop, signal=sid), PROFILE, SIGS)
        assert q is None and why.startswith(start), why
    assert clarify.validate_question(question(drop), PROFILE, SIGS)[0]["id"] == "s:1"


def test_near_duplicates_are_merged_but_different_columns_are_not():
    sigs = [{"id": "s:m1", "type": "near_sum", "columns": ["m1"], "evidence": {}, "strong": True}, {"id": "s:m2", "type": "near_sum", "columns": ["m2"], "evidence": {}, "strong": True}]

    def q(text, col):
        return {"text": text, "why": "x", "columns": [col], "signal": f"s:{col}", "options": [opt("keep", type="note_only"), opt("drop", type="drop_column", column=col)], "default": "keep"}
    qs, dropped = clarify.validate_all([q("Is m1 measured the same way throughout?", "m1"), q("Is m1 measured the same way all through?", "m1"), q("Is m2 measured the same way throughout?", "m2")], PROFILE, sigs)
    assert [x["columns"] for x in qs] == [["m1"], ["m2"]] and len(dropped) == 1 and dropped[0].startswith("duplicate")


# ---- the four wrong questions seen on a real orders + order-details run (a fake LLM writes exactly these)

def wrong_questions():
    def q(text, *options):
        return {"text": text, "why": "x", "columns": [], "options": [{"label": lbl, "action": act} for lbl, act in options], "default": options[0][0]}
    return [
        q("Which is the main number you care about?", ("No preference", NOTE), ("order_total", {"type": "set_measures", "columns": ["order_total"]}),
          ("line_total", {"type": "set_measures", "columns": ["line_total"]})),
        q("What defines a customer segment?", ("By region", NOTE), ("By channel", NOTE), ("By tier", NOTE)),
        q("Which time interval should be used to evaluate growth?", ("Month over month", NOTE), ("Year over year", NOTE)),
        q("Should revenue be measured using total amount or average amount per order?", ("Total amount", NOTE), ("Average per order", NOTE)),
    ]


def orders_with_details(n=300, seed=11):
    """What the Joiner hands over for orders + order details: two measures, several segment columns, one date, nothing ambiguous."""
    rng = np.random.default_rng(seed)
    return pd.DataFrame({"order_id": np.arange(n), "order_date": pd.date_range("2025-01-01", periods=n).strftime("%Y-%m-%d"),
                         "region": rng.choice(["N", "S", "E", "W"], n), "channel": rng.choice(["web", "shop", "phone"], n), "tier": rng.choice(["a", "b", "c"], n),
                         "order_total": rng.gamma(3, 40, n).round(2), "line_total": rng.gamma(2, 25, n).round(2)})


def test_the_four_wrong_questions_are_dropped_with_a_reason():
    df = orders_with_details()
    typed, prof = prep(df)
    qs, dropped = clarify.validate_all(wrong_questions(), prof, signals.compute_signals(typed, prof))
    assert qs == [] and len(dropped) == 4 and all(d.startswith("no answer changes") for d in dropped)


def test_a_model_that_only_writes_the_wrong_questions_leaves_a_clean_orders_run_without_questions():
    typed, prof = prep(orders_with_details())
    sigs = signals.compute_signals(typed, prof)
    assert {s["type"] for s in sigs} == {"multi_measure"}                                                       # the only thing the data suggests is a weak signal
    qs, dropped = clarify.llm_questions(Writer(lambda s: wrong_questions()), prof, sigs, "Which region drives sales?", {}, None)
    assert qs is None and len(dropped) == 4                                                                     # unusable, so the templates take over...
    assert asked(clarify.build_questions(typed, prof, "Which region drives sales?", {}, False, Writer(lambda s: wrong_questions()))) == []   # ...and a weak signal alone never asks


def test_several_measures_never_make_a_question_with_or_without_a_strong_signal_next_to_them():
    df = orders_with_details()
    assert {s["type"] for s in signals.compute_signals(*prep(df))} == {"multi_measure"}
    assert build(df)[0] == []
    df["cost"] = np.where(np.arange(len(df)) % 9 == 0, -5.0, df["line_total"])                                  # strong signals appear: only those are asked
    qs, _, _ = build(df)
    assert "negatives" in kinds(qs) and "multi_measure" not in kinds(qs)
    assert not any(a["type"] == "set_measures" for q in qs for a in (o["action"] for o in q["options"]))


def test_saved_answers_whose_question_is_no_longer_valid_are_ignored_and_reported():
    typed, prof = prep(hospital_stays())
    sid = next(s["id"] for s in signals.compute_signals(typed, prof) if s["type"] == "sentinel")
    saved = {"answers": {sid: {"value": "missing", "label": "A placeholder", "question": "Old wording?", "updated": 1.7e9}}, "definitions": {}, "notes": ""}
    qs, ignored = clarify.build_questions_and_ignored(typed, prof, "g", {}, True, Writer(raw=json.dumps({"questions": []})), saved)   # the model now has nothing to ask
    assert asked(qs) == [] and [i["id"] for i in ignored] == [sid] and "no longer asked" in ignored[0]["reason"]
    qs, ignored = clarify.build_questions_and_ignored(typed, prof, "g", {}, True, None, saved)                                         # the templates still offer that option
    assert ignored == []
    saved["answers"][sid]["value"] = "an option that was reworded"
    qs, ignored = clarify.build_questions_and_ignored(typed, prof, "g", {}, True, None, saved)
    assert [i["id"] for i in ignored] == [sid] and ignored[0]["reason"].startswith("the saved option")
    final, sources = clarify.resolve(qs, None, saved["answers"])
    assert sources[sid] == "default"                                                                            # and it did not steer the answer


def test_summary_says_where_a_saved_answer_came_from():
    q = [mkq("g", ("keep", {"type": "note_only"}), ("drop", {"type": "drop_column", "column": "c"}), default="keep")]
    rows = clarify.summarise(q, {"g": "drop"}, {"g": "glossary"}, {}, {"g": {"value": "drop", "updated": 1.7e9}}, "acme")
    assert rows[0]["source"] == "glossary" and rows[0]["set_by"].startswith("workspace 'acme', saved 20")
    assert clarify.summarise(q, {"g": "keep"}, {"g": "user"}, {})[0]["set_by"] == ""


# ---- the same pattern in other domains and wordings: what the options can do to the data decides, never the words

def retail_sales(n=400, seed=21, placeholder=False):
    """Amount and Profit as measures, three categorical columns, a date range. Nothing ambiguous unless `placeholder`."""
    rng = np.random.default_rng(seed)
    amount = rng.gamma(3, 50, n).round(2)
    if placeholder:
        amount[rng.random(n) < 0.1] = 9999
    return pd.DataFrame({"order_id": np.arange(n), "order_date": pd.date_range("2024-01-01", periods=n).strftime("%Y-%m-%d"),
                         "region": rng.choice(["N", "S", "E", "W"], n), "channel": rng.choice(["web", "shop", "phone"], n), "tier": rng.choice(["a", "b", "c"], n),
                         "amount": amount, "profit": (amount * rng.uniform(0.05, 0.3, n)).round(2)})


def pick_one_column(text, cols):
    return {"text": text, "why": "x", "columns": [], "options": [{"label": c, "action": NOTE} for c in cols], "default": cols[0]}


def pick_a_window(text, col, labels_and_cutoffs, as_filters):
    """'Last month / Last quarter / Last year / All time' as a model might encode it: notes, or a range filter on the date (or on a number) per option."""
    opts = [{"label": lbl, "action": {"type": "filter_rows", "column": col, "operator": "ge", "value": cut} if as_filters else NOTE} for lbl, cut in labels_and_cutoffs]
    return {"text": text, "why": "x", "columns": [col] if as_filters else [], "options": opts + [{"label": "All time", "action": NOTE}], "default": "All time"}


def pick_measure(text, cols):
    return {"text": text, "why": "x", "columns": [], "options": [{"label": c.title(), "action": {"type": "set_measures", "columns": [c]}} for c in cols], "default": cols[0].title()}


WINDOWS = [("Last month", "2024-12-01"), ("Last quarter", "2024-10-01"), ("Last year", "2024-02-01")]
HORIZON = "What time horizon should be used to assess growth of the segment's revenue?"
CASES = {
    # name: (data, [questions that must be dropped], column of the placeholder question that must be KEPT, the placeholder)
    "retail": (retail_sales(placeholder=True), [
        pick_measure("Which is the main number you care about?", ["amount", "profit"]),
        pick_one_column("Which column defines the customer segment for the analysis?", ["region", "channel", "tier"]),
        pick_a_window(HORIZON, "order_date", WINDOWS, False),
        pick_a_window(HORIZON, "order_date", WINDOWS, True)], "amount", 9999),
    "hr": (hr_attrition(), [
        pick_one_column("Which column defines the employee group for this analysis?", ["team", "left_company"]),
        pick_a_window("What period should be used to judge attrition trends?", "hired_on", [("Last quarter", "2022-01-01"), ("Last year", "2021-06-01")], False),
        pick_a_window("What period should be used to judge attrition trends?", "hired_on", [("Last quarter", "2022-01-01"), ("Last year", "2021-06-01")], True)], "perf_rating", -1),
    "hospital": (hospital_stays(), [
        pick_one_column("Which column defines the patient cohort?", ["ward", "readmitted"]),
        pick_a_window("Over what window should readmission be measured?", "stay_days", [("7 days", 7), ("30 days", 30), ("90 days", 90)], False),
        pick_a_window("Over what window should readmission be measured?", "stay_days", [("7 days", 7), ("30 days", 30), ("90 days", 90)], True)], "stay_days", 9999),
}


def kept_definition(col, value, sig=None):
    return {"text": f"How should the placeholder value {value} in {col} be treated?", "why": "A repeated code distorts averages.", "columns": [col], "signal": sig,
            "options": [{"label": "A real value", "action": NOTE}, {"label": "Missing", "action": {"type": "treat_value_as_missing", "column": col, "values": [value]}}], "default": "A real value"}


@pytest.mark.parametrize("name", list(CASES))
def test_pick_a_column_or_a_window_questions_are_dropped_in_every_domain_and_a_real_definition_is_kept(name):
    df, wrong, col, value = CASES[name]
    typed, prof = prep(df)
    sigs = signals.compute_signals(typed, prof)
    sid = next(sg["id"] for sg in sigs if sg["type"] == "sentinel" and sg["columns"] == [col])
    qs, dropped = clarify.validate_all(wrong + [kept_definition(col, value, sid)], prof, sigs)
    assert [q["text"] for q in qs] == [f"How should the placeholder value {value} in {col} be treated?"]
    assert len(dropped) == len(wrong)
    assert all(d.startswith(("no answer changes", "alternative windows")) for d in dropped)


@pytest.mark.parametrize("name", list(CASES))
def test_a_model_that_writes_only_those_questions_cannot_make_the_run_ask_them(name):
    df, wrong, col, value = CASES[name]
    typed, prof = prep(df)
    qs = clarify.build_questions(typed, prof, "Explore", {}, True, Writer(lambda s: wrong), None)
    assert not {w["text"] for w in wrong} & {q["text"] for q in qs}
    assert all(q["kind"] != "extra" for q in qs)                                                           # replaced by the generic templates, never by the model's wording


def test_retail_run_with_those_three_questions_asks_nothing_and_analyses_every_column_and_period():
    df = retail_sales()
    llm_mod._cache["llm"] = (Writer(lambda s: CASES["retail"][1]), "fake:model")
    rid = start(df, goal="Which segment drives revenue and how is it growing?", company="Retail Co")
    assert wait(rid, ("done",)) == "done"                                                                  # 0 questions asked: it was never blocked
    assert cl.get(f"/api/runs/{rid}/questions").status_code == 404
    res = result(rid)
    analyses = res["plan"]["analyses"]
    assert {a["params"]["group"] for a in analyses if a["tool"] == "group_compare"} == {"region", "channel", "tier"}      # every categorical column
    assert any(a["tool"] == "trend" for a in analyses)
    assert not [s for s in res["cleaning_log"] if s["step"] == "Definition applied" and "Filter" in s["detail"]]          # no period was cut away
    assert len(pd.read_pickle(RUNS_DIR / rid / "clean.pkl")) == len(df)
    measured = {v for a in analyses for k, v in a["params"].items() if k in ("target", "col")}
    assert {"amount", "profit"} <= measured                                                                # both measures analysed, not one picked
    assert not [x for x in res.get("applied_definitions") or [] if x["id"].startswith("multi_measure:")]    # it was never a question, so there is no definition to show
    assert res["plan"]["derived"] == []                                                                     # no LLM, so no invented ratio


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
    assert out["a"].isna().sum() == 1 and pd.isna(out.loc[4, "a"]) and eff["rows"] == 1 and log[0]["count"] == 1 and "as missing" in log[0]["detail"]
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
                "revenue", "segment", "segments", "price", "prices", "sales", "profit", "customer", "customers", "churn", "salary", "invoice", "shipment", "delay",
                "patient", "student", "employee", "score", "cost", "quantity", "region", "ticket", "transaction", "payment"}   # not listed: order (date order) and product (arithmetic) are false positives


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
    """Poll the run's status until it is one of `want`. A run that ends in another final state fails at once, with its error, instead of timing out."""
    t0, st = time.time(), None
    while time.time() - t0 < timeout:
        st = (runner.read_status(rid) or {}).get("status")
        if st in want:
            return st
        if st in ("done", "error") or (st == "needs_input" and "needs_input" not in want):
            raise AssertionError(f"run {rid} ended as {st!r} while waiting for {want}: {(runner.read_status(rid) or {}).get('error')}")
        time.sleep(0.1)
    raise AssertionError(f"run {rid} still {st!r} after {timeout}s (waiting for {want})")


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
    assert {"multi_date", "negatives", "placeholder"} <= set(q) and "sentinel" not in q       # the 9999 is asked about once, as a placeholder on the source table

    assert cl.post(f"/api/runs/{rid}/answers", json={"answers": {"nope": "x"}}).status_code == 400
    assert cl.post(f"/api/runs/{rid}/answers", json={"answers": {q["negatives"]["id"]: "bogus"}}).status_code == 400
    assert cl.post(f"/api/runs/{rid}/answers", json={"notes": "x" * 600}).status_code == 422

    body = {"answers": {q["multi_date"]["id"]: "discharged", q["negatives"]["id"]: "exclude", q["placeholder"]["id"]: "missing"},
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
    assert "<summary>Definitions used" in md and "Rows affected" in md and "not applied to the data" in md and "back within 30 days" in md
    by = {d["id"]: d for d in res["applied_definitions"]}
    assert by[q["negatives"]["id"]]["source"] == "user" and by[q["negatives"]["id"]]["rows"] == n_neg
    evs = [json.loads(line) for line in (RUNS_DIR / rid / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sum(e["agent"] == "Profiler" for e in evs) == 2 and sum(e["agent"] == "Clarifier" for e in evs) == 2      # one pass of profiler events (two lines), nothing repeated after the resume

    rid2 = start(df)                                                                    # same company: pre-filled from the glossary, nothing to ask
    assert wait(rid2) == "done"
    by2 = {d["id"]: d for d in result(rid2)["applied_definitions"]}
    assert by2[q["multi_date"]["id"]]["answer"] == "discharged" and by2[q["multi_date"]["id"]]["source"] == "glossary"
    assert result(rid2)["plan"]["date_col"] == "discharged"
    assert by2[q["multi_date"]["id"]]["set_by"].startswith("workspace 'acme', saved ")
    assert "Saved glossary (workspace 'acme', saved " in result(rid2)["report_md"]                       # the report says whose saved answer steered the analysis
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


def test_skip_uses_defaults_and_only_the_placeholder_default_changes_data():
    df = hospital_stays()
    rid = start(df, company="Gamma")
    assert wait(rid) == "needs_input"
    q = qmap(rid)
    assert cl.post(f"/api/runs/{rid}/answers", json={"skip": True, "answers": {q["negatives"]["id"]: "exclude"}}).status_code == 200
    assert wait(rid) == "done"
    res = result(rid)
    clean = pd.read_pickle(RUNS_DIR / rid / "clean.pkl")
    assert len(clean) == len(df) and (clean["cost"] < 0).sum() == (df["cost"] < 0).sum()      # no row removed, negatives kept
    assert (clean["stay_days"] == 9999).sum() == 0                                           # the placeholder's default is "as missing"
    applied = [s for s in res["cleaning_log"] if s["step"] == "Definition applied" and s["count"]]
    assert len(applied) == 1 and "stay_days" in applied[0]["detail"]
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
    assert wait(rid, ("needs_input",)) == "needs_input"
    qs = cl.get(f"/api/runs/{rid}/questions").json()["questions"]
    assert {q["kind"] for q in qs} == {"multi_date", "negatives", "placeholder"} and all(q["id"] == q["signal"] for q in qs)
    dumped = json.dumps(qs)
    assert "ghost_col" not in dumped and "chart" not in dumped and "run_python" not in dumped
    neg = next(q for q in qs if q["kind"] == "negatives")
    assert cl.post(f"/api/runs/{rid}/answers", json={"answers": {neg["id"]: val(neg, "No")}}).status_code == 200
    assert wait(rid, ("done",)) == "done"
    assert len(pd.read_pickle(RUNS_DIR / rid / "clean.pkl")) == len(df) - int((df["cost"] < 0).sum())


def test_orders_and_details_files_with_a_model_writing_the_four_wrong_questions_run_without_asking():
    rng = np.random.default_rng(5)
    orders = orders_with_details(200).drop(columns=["order_total", "line_total"])
    lines = pd.DataFrame({"order_id": rng.integers(0, 200, 600), "item_group": rng.choice(["p", "q", "r", "s"], 600), "units": rng.integers(1, 9, 600),
                          "line_total": rng.gamma(2, 25, 600).round(2)})
    llm_mod._cache["llm"] = (Writer(lambda s: wrong_questions()), "fake:model")
    r = cl.post("/api/runs", data={"goal": "Which region drives sales?", "company": "Orders Co"},
                files=[("files", ("orders.csv", orders.to_csv(index=False).encode())), ("files", ("order_details.csv", lines.to_csv(index=False).encode()))])
    assert r.status_code == 200
    rid = r.json()["run_id"]
    assert wait(rid, ("done",)) == "done"                                                                  # nothing worth asking, so nothing blocks the run
    assert cl.get(f"/api/runs/{rid}/questions").status_code == 404
    events = (RUNS_DIR / rid / "events.jsonl").read_text(encoding="utf-8")
    assert not any(w["text"] in events for w in wrong_questions())


def test_rules_still_work_with_no_llm():
    assert llm_mod.get_llm()[0] is None
    assert clarify.llm_questions(None, PROFILE, [], "g", {}, None) == (None, [])
    assert build(hospital_stays())[0]
