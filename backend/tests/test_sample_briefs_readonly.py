"""The shared sample run's briefs are read-only for every visitor (rename, edit, delete and create all 403), but can still be read and exported.
A visitor's own run keeps full control of its briefs. Fake AI only (no model, no network)."""
import os

os.environ["LLM_PROVIDER"] = "none"
os.environ["CHATBOT"] = "on"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.lens import briefs as lensbriefs, store  # noqa: E402
from app.main import app  # noqa: E402
from app.runner import run_dir  # noqa: E402
from tests.test_owner import upload, wait_done  # noqa: E402


@pytest.fixture(scope="module")
def world():
    a, b = TestClient(app), TestClient(app)
    demo = a.post("/api/demo").json()["run_id"]
    wd = run_dir(demo)
    seeded = lensbriefs.new_brief("Shipped brief", "the board", "", {}, {"text": "Revenue is steady.", "ids": [], "edited": False}, [], [], [], {"trust": "High", "notes": []}, "code")
    d = store.load(wd)
    d["briefs"] = [seeded]                       # written directly: the shipped sample would carry its briefs in its own folder
    store.save(wd, d)
    return a, b, demo, seeded["id"]


def test_nobody_can_change_the_sample_briefs(world):
    a, b, demo, bid = world
    before = store.get_briefs(run_dir(demo))
    for c in (a, b):                             # any visitor, the one who opened it and a stranger
        for r in (c.post(f"/api/runs/{demo}/briefs/{bid}/rename", json={"title": "Hacked"}),
                  c.post(f"/api/runs/{demo}/briefs/{bid}/edit", json={"section": "headline", "text": "Hacked"}),
                  c.delete(f"/api/runs/{demo}/briefs/{bid}"),
                  c.post(f"/api/runs/{demo}/briefs/nope/rename", json={"title": "x"}),
                  c.post(f"/api/runs/{demo}/lens/ask", json={"question": "write a brief for the board about revenue"}),
                  ):
            assert r.status_code == 403, (r.request.url, r.text)
        assert c.delete(f"/api/runs/{demo}/briefs/{bid}").json()["detail"] == c.post(f"/api/runs/{demo}/lens/feedback", json={"turn": "t", "vote": "up"}).json()["detail"]
    for q in ("delete all my briefs", "rename the brief to Hacked"):        # without a model the chat cannot route these, but if it ever does they must not change anything
        a.post(f"/api/runs/{demo}/lens/ask", json={"question": q})
    assert store.get_briefs(run_dir(demo)) == before


def test_creating_a_brief_on_the_sample_is_refused_with_403(world):
    a, _, demo, _ = world
    r = a.post(f"/api/runs/{demo}/lens/ask", json={"question": "write a brief for the new hire about growth"})
    assert r.status_code == 403 and "read-only" in r.json()["detail"]
    assert len(store.get_briefs(run_dir(demo))) == 1


def test_the_sample_briefs_can_still_be_read_and_exported(world):
    a, b, demo, bid = world
    for c in (a, b):
        assert [x["id"] for x in c.get(f"/api/runs/{demo}/briefs").json()["briefs"]] == [bid]
        t = c.get(f"/api/runs/{demo}/export/text", params={"brief": bid})           # plain text needs no browser
        assert t.status_code == 200 and "Revenue is steady." in t.text
        assert c.get(f"/api/runs/{demo}/export/all_briefs").status_code in (200, 503)      # 503 only when no browser is installed for PDFs
        assert c.get(f"/api/runs/{demo}/export/brief", params={"brief": bid}).status_code in (200, 503)


def test_a_visitor_keeps_full_control_of_briefs_on_their_own_run(world):
    a, b, demo, _ = world
    rid = upload(a)
    wait_done(a, rid)
    r = a.post(f"/api/runs/{rid}/lens/ask", json={"question": "write a brief for the new hire about revenue"})
    assert r.status_code == 200, r.text
    bs = a.get(f"/api/runs/{rid}/briefs").json()["briefs"]
    assert bs, "a brief should have been made on the visitor's own run"
    bid = bs[0]["id"]
    assert a.post(f"/api/runs/{rid}/briefs/{bid}/rename", json={"title": "Mine"}).status_code == 200
    assert a.post(f"/api/runs/{rid}/briefs/{bid}/edit", json={"section": "headline", "text": "My line."}).status_code == 200
    assert a.delete(f"/api/runs/{rid}/briefs/{bid}").status_code == 200
    assert b.post(f"/api/runs/{rid}/briefs/{bid}/rename", json={"title": "x"}).status_code == 404     # still someone else's run to a stranger
