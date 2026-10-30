"""Saved answers: they apply only to the same question (same kind, column and action values), are listed before and after a run, can be changed (which re-runs and
misses the AI caches), and can be forgotten ("Ask me again"). Synthetic data, no AI."""
import os
import tempfile

os.environ["LLM_PROVIDER"] = "none"
os.environ["GLOSSARY_PATH"] = os.path.join(tempfile.mkdtemp(), "glossary.json")

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from app import aicache, clarify, glossary, llm as llm_mod  # noqa: E402
from app.config import RUNS_DIR  # noqa: E402
from domains import hospital_stays  # noqa: E402
from test_clarify import cl, qmap, result, start, wait  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_glossary():
    p = glossary.glossary_path()
    if p.exists():
        p.unlink()
    yield
    llm_mod.reset_cache()


def question(column, value, same_value="fix"):
    """A question about one signal whose option `same_value` treats `value` of `column` as missing."""
    acts = {"fix": {"type": "treat_value_as_missing", "column": column, "value": value}, "keep": {"type": "note_only"}}
    return {"id": "sentinel:abc", "kind": "sentinel", "text": "t", "default": "keep",
            "options": [{"value": "fix", "label": "Missing", "action": acts["fix"]}, {"value": "keep", "label": "Keep", "action": acts["keep"]}]}


def saved_for(q, value):
    return {q["id"]: {"value": value, "label": value, "question": q["text"], "updated": 1.7e9, "fp": clarify.fingerprint(q, value)}}


# ------------------------------------------------------------------ matching
def test_a_saved_answer_does_not_apply_to_a_different_column():
    saved = saved_for(question("stay_days", 9999), "fix")
    other = question("cost", 9999)                                   # same signal id and option value, another column
    assert clarify.resolve([other], None, saved) == ({other["id"]: "keep"}, {other["id"]: "default"})


def test_a_saved_answer_does_not_apply_to_different_values():
    saved = saved_for(question("stay_days", 9999), "fix")
    other = question("stay_days", -1)
    assert clarify.resolve([other], None, saved)[1] == {other["id"]: "default"}


def test_a_matching_saved_answer_applies():
    q = question("stay_days", 9999)
    saved = saved_for(q, "fix")
    assert clarify.resolve([question("stay_days", 9999)], None, saved) == ({q["id"]: "fix"}, {q["id"]: "glossary"})


def test_the_wording_of_a_question_does_not_matter_but_a_user_answer_still_wins():
    q = question("stay_days", 9999)
    saved = saved_for(q, "fix")
    reworded = {**question("stay_days", 9999), "text": "Another way of asking"}
    assert clarify.resolve([reworded], None, saved)[0] == {q["id"]: "fix"}
    assert clarify.resolve([reworded], {q["id"]: "keep"}, saved) == ({q["id"]: "keep"}, {q["id"]: "user"})


def test_an_unmatched_saved_answer_is_reported_as_ignored_with_a_reason():
    df = hospital_stays()
    typed, prof = clarify_inputs(df)
    qs = clarify.build_questions(typed, prof, "Explore this data", {}, True, None, None)
    neg = next(q for q in qs if q["kind"] == "negatives")
    stale = {"answers": {neg["id"]: {"value": neg["options"][0]["value"], "fp": "0" * 12, "label": "x", "question": "q"}}}
    _, ignored = clarify.build_questions_and_ignored(typed, prof, "Explore this data", {}, True, None, stale)
    assert any(i["id"] == neg["id"] and "other columns or values" in i["reason"] for i in ignored)


def clarify_inputs(df):
    from app.cleaner import coerce_types
    from app.profiler import profile_df
    prof = profile_df(df)
    typed, _ = coerce_types(df, prof)
    return typed, profile_df(typed)


# ------------------------------------------------------------------ runs
ANSWER_NEG = "exclude"


def first_run(company):
    df = hospital_stays()
    rid = start(df, company=company)
    assert wait(rid) == "needs_input"
    q = qmap(rid)
    body = {"answers": {q["multi_date"]["id"]: "discharged", q["negatives"]["id"]: ANSWER_NEG, q["placeholder"]["id"]: "missing"}}
    assert cl.post(f"/api/runs/{rid}/answers", json=body).status_code == 200
    assert wait(rid) == "done"
    return df, rid, q


def test_a_matching_saved_answer_is_reused_and_listed():
    df, rid, q = first_run("Saver")
    saved = glossary.get_workspace("saver")["answers"]
    assert all(a.get("fp") for a in saved.values())                  # every saved answer knows what it was about
    rid2 = start(df, company="Saver")
    assert wait(rid2) == "done"                                      # nothing to ask
    rows = {r["id"]: r for r in cl.get(f"/api/runs/{rid2}/answers-review").json()["answers"]}
    assert rows[q["negatives"]["id"]]["reused"] and rows[q["negatives"]["id"]]["source"] == "glossary" and rows[q["negatives"]["id"]]["saved"]
    by = {d["id"]: d for d in result(rid2)["applied_definitions"]}
    assert by[q["negatives"]["id"]]["source"] == "glossary" and by[q["negatives"]["id"]]["set_by"].startswith("workspace 'saver'")


def test_a_run_started_with_review_pauses_to_show_the_saved_answers():
    df, rid, q = first_run("Reviewer")
    rid2 = cl.post("/api/runs", data={"goal": "Explore this data", "company": "Reviewer", "review_saved": "on"},
                   files=[("files", ("data.csv", df.to_csv(index=False).encode()))]).json()["run_id"]
    assert wait(rid2) == "needs_input"
    rows = cl.get(f"/api/runs/{rid2}/answers-review").json()["answers"]
    assert rows and all(r["reused"] for r in rows)
    assert cl.post(f"/api/runs/{rid2}/answers", json={}).status_code == 200      # Continue keeps them
    assert wait(rid2) == "done"


def test_changing_an_answer_changes_the_run_replaces_the_saved_answer_and_misses_the_cache():
    df, rid, q = first_run("Changer")
    neg = q["negatives"]
    keep = next(o["value"] for o in neg["options"] if o["value"] != ANSWER_NEG and o["value"] == neg["default"])
    n_before = len(pd.read_pickle(RUNS_DIR / rid / "clean.pkl"))
    key_before = aicache.key("t", {}, "g", "", {"answers": glossary.get_workspace("changer")["answers"]}, "m", "p")
    r = cl.post(f"/api/runs/{rid}/rerun-answers", json={"answers": {neg["id"]: keep}})
    assert r.status_code == 200
    rid2 = r.json()["run_id"]
    assert wait(rid2) == "done"
    assert len(pd.read_pickle(RUNS_DIR / rid2 / "clean.pkl")) == len(df) and n_before < len(df)       # the rows the old answer removed are back
    assert glossary.get_workspace("changer")["answers"][neg["id"]]["value"] == keep                    # the changed answer replaced the saved one
    key_after = aicache.key("t", {}, "g", "", {"answers": glossary.get_workspace("changer")["answers"]}, "m", "p")
    assert key_before != key_after                                                                     # every AI cache keyed on the saved answers misses
    by = {d["id"]: d for d in result(rid2)["applied_definitions"]}
    assert by[neg["id"]]["answer"] == keep and by[neg["id"]]["source"] == "user"
    assert cl.post(f"/api/runs/{rid}/rerun-answers", json={"answers": {neg["id"]: "bogus"}}).status_code == 400


def test_ask_me_again_forgets_the_saved_answer_and_asks_the_question():
    df, rid, q = first_run("Asker")
    neg = q["negatives"]
    r = cl.post(f"/api/runs/{rid}/rerun-answers", json={"ask_again": [neg["id"]]})
    assert r.status_code == 200
    rid2 = r.json()["run_id"]
    assert wait(rid2) == "needs_input"
    assert neg["id"] not in glossary.get_workspace("asker")["answers"]
    asked = {x["id"]: x for x in cl.get(f"/api/runs/{rid2}/questions").json()["questions"]}
    assert neg["id"] in asked and asked[neg["id"]]["prefill_source"] == "default"
    assert cl.post(f"/api/runs/{rid2}/answers", json={"answers": {neg["id"]: ANSWER_NEG}}).status_code == 200
    assert wait(rid2) == "done"


def test_only_a_finished_run_can_be_re_run_with_other_answers():
    df = hospital_stays()
    rid = start(df, company="Waiter")
    assert wait(rid) == "needs_input"
    assert cl.post(f"/api/runs/{rid}/rerun-answers", json={}).status_code == 409
