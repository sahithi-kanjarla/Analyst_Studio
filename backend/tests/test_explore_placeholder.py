"""The sentence create_run stores when "explore" is ticked is a placeholder, not the person's goal:
it is never matched against columns and never shown as their goal.  Fake model only, no network."""
import os
import tempfile

os.environ["LLM_PROVIDER"] = "none"
os.environ["GLOSSARY_PATH"] = os.path.join(tempfile.mkdtemp(), "glossary.json")

import pandas as pd  # noqa: E402

from app.context import DEFAULT_EXPLORE_GOAL  # noqa: E402
from test_clarify import _visitor_glossary, cl, fresh_glossary, result, start, wait  # noqa: E402,F401


def _df():
    # column names built from the placeholder's own words: if the placeholder were matched as a goal, these would match
    n = 60
    return pd.DataFrame({"insights": [f"g{i % 4}" for i in range(n)], "business": [f"b{i % 3}" for i in range(n)],
                         "units": [(i * 7) % 11 + 1 for i in range(n)], "amount": [float((i * 13) % 50 + 5) for i in range(n)]})


def test_explore_run_does_not_store_or_print_the_placeholder_as_the_goal():
    rid = start(_df(), goal="")                       # the empty goal is how the form's explore tick reaches create_run
    assert wait(rid) in ("done", "needs_input")
    if wait(rid) == "needs_input":
        assert cl.post(f"/api/runs/{rid}/answers", json={}).status_code == 200
        assert wait(rid) == "done"
    res = result(rid)
    assert res["goal"] == ""                          # the graph never saw the placeholder as the goal
    assert res["context"]["explore"] is True
    assert res["curation"]["mode"] == "overview"
    assert res["curation"]["mode_note"]
    assert DEFAULT_EXPLORE_GOAL not in (res.get("report_md") or "")
    assert DEFAULT_EXPLORE_GOAL not in str(res.get("report_view"))
    assert not res["report_view"].get("goal")
    api = cl.get(f"/api/runs/{rid}").json()
    assert api["goal"] == ""
    assert all(r["goal"] != DEFAULT_EXPLORE_GOAL for r in cl.get("/api/runs").json())


def test_a_short_typed_goal_is_kept_as_the_goal_and_is_not_an_overview():
    rid = start(_df(), goal="best seller?")
    assert wait(rid) in ("done", "needs_input")
    if wait(rid) == "needs_input":
        assert cl.post(f"/api/runs/{rid}/answers", json={}).status_code == 200
        assert wait(rid) == "done"
    res = result(rid)
    assert res["goal"] == "best seller?"
    assert res["curation"]["mode"] == "goal"
