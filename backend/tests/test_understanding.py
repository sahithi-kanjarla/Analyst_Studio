"""The data understanding record: the structure test and its retries, the fallback and its header line, and the three consumers (cleaning scope, summary answer linking,
dashboard chart choice) reading goal_columns. Fake AI output throughout; the data is synthetic."""
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app import dashboard, graph, planner, reporter, summary, understanding as und  # noqa: E402
from app.cleaner import scoped_clean  # noqa: E402
from app.profiler import profile_df  # noqa: E402

GOAL = "Which categories and states bring in the most sales?"
STATES = [f"S{i:02d}" for i in range(35)]


def as_codes(prof):
    """The profile the rules give a column of few whole numbers: a category (the profiler's rule for up to 8 integer values)."""
    return {**prof, "columns": [{**c, "role": "categorical"} if c["name"] == "seats" else c for c in prof["columns"]]}


def table(n=700, seats=True):
    rng = np.random.default_rng(5)
    d = {"category": rng.choice(["Set", "Kurta", "Top"], n), "ship-state": rng.choice(STATES, n), "amount": rng.integers(100, 900, n).astype(float)}
    if seats:
        d["seats"] = rng.choice([0.5, 1.0, 1.5, 2.0, 2.5], n)       # few distinct fractional values: the rules read it as a category
    return pd.DataFrame(d)


def entry(c, role, terms=(), **kw):
    return {"column": c, "meaning": f"what {c} holds", "role": role, "goal_terms": list(terms), **kw}


def reply(df, drop=(), **gc):
    roles = {"category": "category", "ship-state": "category", "amount": "measure", "seats": "measure"}
    return {"columns": [entry(c, roles[c]) for c in df.columns if c not in drop],
            "goal_columns": gc or {"categories": ["category"], "states": ["ship-state"], "sales": ["amount"]}}


def record(df, **gc):
    """A valid record built by validate(), as the AI would have produced it."""
    parts, errors = und.validate(reply(df, **gc), list(df.columns))
    assert not errors
    return und._record("llm", parts)


class FakeAI:
    def __init__(self, monkeypatch, *replies):
        self.replies, self.prompts = list(replies), []
        monkeypatch.setattr(und, "ask_json_ex", self.ask)

    def ask(self, llm, system, user):
        self.prompts.append(user)
        r = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return (r, "") if isinstance(r, dict) else (None, r)


def resolve(df):
    return und.resolve(object(), df, profile_df(df), GOAL, {"description": "A shop"})


# ------------------------------------------------------------------ the record and its structure test
def test_valid_record_is_used(monkeypatch):
    df = table()
    ai = FakeAI(monkeypatch, reply(df))
    rec = resolve(df)
    assert rec["source"] == "llm" and rec["retries"] == 0 and len(ai.prompts) == 1
    assert set(rec["columns"]) == set(df.columns) and rec["columns"]["amount"]["role"] == "measure"
    assert rec["goal_columns"] == {"categories": ["category"], "states": ["ship-state"], "sales": ["amount"]} and rec["unmapped"] == []
    assert "GOAL: " + GOAL in ai.prompts[0] and "A shop" in ai.prompts[0] and "FACT SHEET" in ai.prompts[0]      # the goal, the description and the fact sheet go in


def test_record_missing_a_column_is_retried_then_accepted(monkeypatch):
    df = table()
    ai = FakeAI(monkeypatch, reply(df, drop=("seats",)), reply(df))
    rec = resolve(df)
    assert rec["source"] == "llm" and rec["retries"] == 1 and len(ai.prompts) == 2
    assert "missing from 'columns': 'seats'" in ai.prompts[1]                  # the error is sent back to the AI
    assert und.notice(rec) == ""


def test_structure_test_rejects_bad_role_duplicate_and_unknown_column():
    df = table()
    bad = reply(df)
    bad["columns"][0]["role"] = "dimension"
    bad["columns"].append(entry("amount", "measure"))
    bad["goal_columns"]["regions"] = ["region"]
    _, errors = und.validate(bad, list(df.columns))
    text = " | ".join(errors)
    assert "role must be one of" in text and "appears 2 times" in text and "'region', which does not exist" in text


def test_retries_exhausted_falls_back_and_shows_the_header_line(monkeypatch):
    df = table()
    ai = FakeAI(monkeypatch, reply(df, drop=("seats",)))
    rec = resolve(df)
    assert rec["source"] == "fallback" and len(ai.prompts) == 1 + und.MAX_RETRIES and rec["retries"] == und.MAX_RETRIES
    assert rec["columns"] == {} and rec["goal_columns"] == {}
    line = und.notice(rec)
    assert line.startswith("AI unavailable for data understanding: ") and len(line) < 170
    md = reporter.with_notices("# Report\n\n**Mode:** mixed\n\nbody", [line])
    assert md.split("\n")[4] == f"- ⚠ {line}"                                 # right under the Mode line
    assert und.goal_columns(rec) == [] and und.unmapped_lines(rec) == [] and und.measure_columns(rec) == set()      # consumers see nothing: their own rules decide


def test_a_failed_call_falls_back_at_once_with_its_reason(monkeypatch):
    ai = FakeAI(monkeypatch, "rate limit (per-minute)")
    rec = resolve(table())
    assert rec["source"] == "fallback" and len(ai.prompts) == 1 and rec["error"] == "rate limit (per-minute)"


def test_node_writes_the_record_to_the_run_folder_and_the_header_note(monkeypatch, tmp_path):
    df = table()
    ai = FakeAI(monkeypatch, reply(df, drop=("seats",)))
    monkeypatch.setattr(graph, "get_llm", lambda: (object(), "fake"))
    monkeypatch.setattr(graph, "emit", lambda *a, **k: None)
    df.to_pickle(tmp_path / "raw.pkl")
    out = graph.understand_node({"run_id": "t", "workdir": str(tmp_path), "goal": GOAL, "profile": profile_df(df), "context": {}, "decisions": [], "ai_notes": []})
    saved = __import__("json").loads((tmp_path / "understanding.json").read_text(encoding="utf-8"))
    assert out["understanding"]["source"] == "fallback" and saved["record"]["source"] == "fallback"
    assert out["ai_notes"][0].startswith("AI unavailable for data understanding: ") and out["stage_sources"]["understanding"] == "fallback"
    ok = FakeAI(monkeypatch, reply(df))
    (tmp_path / "understanding.json").unlink()
    out = graph.understand_node({"run_id": "t", "workdir": str(tmp_path), "goal": GOAL, "profile": profile_df(df), "context": {}, "decisions": [], "ai_notes": []})
    assert out["understanding"]["source"] == "llm" and ok.prompts and not out["ai_notes"]
    n = len(ok.prompts)
    again = graph.understand_node({"run_id": "t", "workdir": str(tmp_path), "goal": GOAL, "profile": profile_df(df), "context": {}, "decisions": [], "ai_notes": []})
    assert again["understanding"] == out["understanding"] and len(ok.prompts) == n      # a restarted run reads the saved record, no second call


# ------------------------------------------------------------------ an unmapped goal term
def test_unmapped_term_gives_the_report_line(monkeypatch):
    df = table()
    rec = record(df, **{"states": ["ship-state"], "warehouses": "none"})
    assert rec["unmapped"] == ["warehouses"]
    assert und.unmapped_lines(rec) == ["Your goal mentions warehouses, but no column matches."]
    state = {"understanding": rec, "goal": GOAL}
    monkeypatch.setattr(reporter, "llm_summary", lambda *a, **k: None)
    monkeypatch.setattr(reporter, "rule_summary", lambda s: ("Summary.", ["Do a thing."]))
    monkeypatch.setattr(reporter, "build_report", lambda s, summary, *a, **k: summary)
    monkeypatch.setattr(reporter.tiering, "build_view", lambda *a, **k: {"answers": [], "rankings": []})
    md, _ = reporter.write_report({**state, "report_view": {"answers": [], "rankings": []}}, None)
    assert "- Your goal mentions warehouses, but no column matches." in md


def test_setup_lines_show_the_record_short():
    text = "\n".join(und.setup_lines(record(table())))
    assert "**measure**: `amount`, `seats`" in text and "“states” → `ship-state`" in text


# ------------------------------------------------------------------ consumer 1: the cleaning scope
def test_plural_goal_word_keeps_the_differently_named_column_in_the_cleaning_scope():
    df = table(seats=False)
    prof = profile_df(df)
    plain = planner.heuristic_plan(df, prof, GOAL)
    assert "ship-state" not in plain["relevant_columns"]                      # word matching alone loses it: "states" is not "ship state"
    mapped = planner.heuristic_plan(df, prof, GOAL, None, False, {"goal_columns": ["category", "ship-state"]})
    assert "ship-state" in mapped["relevant_columns"]
    _, log = scoped_clean(df, prof, mapped)
    assert not [x for x in log if x["step"] == "Scope to the goal" and "ship-state" in x["detail"]]
    _, log = scoped_clean(df, prof, plain)
    assert [x for x in log if x["step"] == "Scope to the goal" and "ship-state" in x["detail"]]


def test_a_measure_is_never_a_grouping_in_the_plan():
    df = table()
    prof = as_codes(profile_df(df))
    groups = lambda plan: {a["params"].get("group") for a in plan["analyses"]}
    assert "seats" in groups(planner.heuristic_plan(df, prof, GOAL))
    assert "seats" not in groups(planner.heuristic_plan(df, prof, GOAL, None, False, {"record_measures": ["seats"]}))


# ------------------------------------------------------------------ consumer 2: the summary answer linking
def summary_state(df, rec):
    cols = [{"name": c, "role": "numeric" if c == "amount" else "categorical", "unique": int(df[c].nunique())} for c in df.columns]
    return {"goal": GOAL, "findings": [], "decisions": [], "context": {}, "brief": None, "understanding": rec, "profile": {"rows": len(df), "cols": len(cols), "columns": cols},
            "plan": {"target": "amount", "date_col": None, "questions": [GOAL], "objective": "o", "relevant_columns": list(df.columns), "analyses": [], "explore": False, "derived": []}}


def test_plural_goal_word_links_the_differently_named_column_in_the_summary_answer(monkeypatch):
    df = table(seats=False)
    state = summary_state(df, record(df))
    rankings = summary.ranking_facts(state, df)
    by_group = {r["group"]: r["id"] for r in rankings if r.get("basis") == "total" and r.get("kind") not in ("growth", "outside_rate")}
    assert {"category", "ship-state"} <= set(by_group)
    # the AI links the question to one ranking only; the record's goal terms add the other column the question names
    monkeypatch.setattr(summary, "ask_json", lambda *a, **k: {"answers": [{"question": 0, "ids": [by_group["category"]]}]})
    with monkeypatch.context() as m:          # the rules match this question confidently now and skip the AI call; here the rules find nothing, so the model decides
        m.setattr(summary, "goal_pick", lambda *a: [])
        facts = summary.pick(object(), state, rankings)
    assert facts["source"] == "llm" and set(facts["picks"][GOAL]) == {by_group["category"], by_group["ship-state"]}
    answer = summary.answers(state, facts)[0]["answer"]
    assert "ship-state" in answer and "category" in answer
    # the rules path (no AI) reads the record too
    rules = summary.pick(None, state, rankings)
    assert by_group["ship-state"] in rules["picks"][GOAL]
    # without the record, the AI's single id stands (that is the defect the record fixes)
    plain = summary_state(df, None)
    monkeypatch.setattr(summary, "ask_json", lambda *a, **k: {"answers": [{"question": 0, "ids": [by_group["category"]]}]})
    with monkeypatch.context() as m:
        m.setattr(summary, "goal_pick", lambda *a: [])
        assert summary.pick(object(), plain, summary.ranking_facts(plain, df))["picks"][GOAL] == [by_group["category"]]


def test_a_goal_column_named_by_the_record_is_listed_as_uncovered_when_no_answer_uses_it():
    df = table(seats=False)
    state = summary_state(df, record(df))
    rankings = summary.ranking_facts(state, df)
    view = {"rankings": rankings, "answers": []}
    assert "ship-state" in {r["group"] for r in summary.uncovered_groups(state, view)}
    assert "ship-state" not in {r["group"] for r in summary.uncovered_groups(summary_state(df, None), view)}


def test_a_measure_is_never_a_summary_grouping():
    df = table()
    rec = record(df)
    assert "seats" in summary.group_columns(summary_state(df, None), df, every=True)
    assert "seats" not in summary.group_columns(summary_state(df, rec), df, every=True)


# ------------------------------------------------------------------ consumer 3: the dashboard
def dash(tmp_path, df, rec, relevant):
    df.to_pickle(tmp_path / "clean.pkl")
    prof = as_codes(profile_df(df))
    plan = {"target": "amount", "date_col": None, "relevant_columns": relevant, "derived": [], "objective": "o"}
    return dashboard.build_dashboard(tmp_path, {"plan": plan, "profile": prof, "understanding": rec, "context": {}, "brief": None})


def test_plural_goal_word_gets_a_dashboard_chart_for_the_differently_named_column(tmp_path):
    df = table(seats=False)
    d = dash(tmp_path, df, record(df), ["amount", "category"])
    assert "ship-state" in {x["col"] for x in d["dims"]} and "ship-state" in d["columns"]
    assert "ship-state" not in {x["col"] for x in dash(tmp_path, df, None, ["amount", "category"])["dims"]}


def test_a_35_value_goal_column_gives_a_10_bar_chart(tmp_path):
    df = table(seats=False)
    d = dash(tmp_path, df, record(df), ["amount", "category", "ship-state"])
    dim = next(x for x in d["dims"] if x["col"] == "ship-state")
    assert dim["unique"] == 35 and dim["top"] == 10


def test_a_goal_column_with_over_40_values_is_not_dropped(tmp_path):
    df = table(seats=False)
    df["ship-state"] = [f"S{i % 60:02d}" for i in range(len(df))]
    d = dash(tmp_path, df, record(df), ["amount", "category"])
    dim = next(x for x in d["dims"] if x["col"] == "ship-state")
    assert dim["unique"] == 60 and dim["top"] == 10
    assert "ship-state" not in {x["col"] for x in dash(tmp_path, df, None, ["amount", "category", "ship-state"])["dims"]}      # the old cut-off drops it


def test_a_measure_is_not_used_as_a_dashboard_grouping(tmp_path):
    df = table()
    relevant = ["amount", "category", "seats"]
    assert "seats" in {x["col"] for x in dash(tmp_path, df, None, relevant)["dims"]}
    assert "seats" not in {x["col"] for x in dash(tmp_path, df, record(df), relevant)["dims"]}


def test_a_count_the_model_calls_a_flag_becomes_a_measure():
    """The model said 'indicator, 1 or 0' for a column whose values are counts: code reads the values and drops that wording."""
    import pandas as pd
    from app.understanding import check_flags
    df = pd.DataFrame({"n_things": [0, 1, 2, 3, 5] * 4, "yes_no": [0, 1] * 10})
    cols = {c: {"role": "flag", "meaning": "Indicator if it happened (1) or not (0)", "display_name": "Thing Flag"} for c in df.columns}
    out = check_flags(cols, df)
    assert out["n_things"]["role"] == "measure" and out["n_things"]["measure_kind"] == "count"
    assert "flag" not in out["n_things"]["display_name"].lower() and "indicator" not in out["n_things"]["meaning"].lower()
    assert out["yes_no"]["role"] == "flag"          # a real two-valued column stays a flag
