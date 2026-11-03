"""Visitor separation: every run belongs to the browser that made it. Fake AI only (no model, no network).
The route-table test below reads the app's own routes, so a route added later that forgets the check makes it fail."""
import json
import logging
import os
import time

os.environ["LLM_PROVIDER"] = "none"
os.environ["CHATBOT"] = "on"            # Lens routes answer (without an AI) so that they can be told apart from "not found"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import glossary, owner, runner  # noqa: E402
from app.config import RUNS_DIR  # noqa: E402
from app.main import app  # noqa: E402

GONE = {"detail": "Run not found"}


def is_gone(r):
    """The answer for a missing run, or for a run that is someone else's (they are the same, so existence is not revealed)."""
    return r.status_code == 404 and r.headers.get("content-type", "").startswith("application/json") and r.json() == GONE


def make_csv():
    rng = np.random.default_rng(1)
    n = 200
    seg = rng.choice(["Enterprise", "SMB", "Consumer"], n)
    base = {"Enterprise": 400, "SMB": 220, "Consumer": 120}
    df = pd.DataFrame({"Segment": seg, "region": rng.choice(list("NSEW"), n), "revenue": np.array([base[s] for s in seg]) * rng.lognormal(0, 0.4, n)})
    return df.to_csv(index=False).encode()


def wait_done(c, rid, timeout=240):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = (runner.read_status(rid) or {}).get("status")
        if st == "needs_input":
            c.post(f"/api/runs/{rid}/answers", json={"skip": True})
        elif st in ("done", "error"):
            assert st == "done", (runner.read_status(rid) or {}).get("error")
            return
        time.sleep(0.3)
    raise AssertionError("run did not finish")


def upload(c):
    r = c.post("/api/runs", data={"goal": "Which segment earns the most revenue?"}, files=[("files", ("d.csv", make_csv()))])
    assert r.status_code == 200, r.text
    return r.json()["run_id"]


@pytest.fixture(scope="module")
def world():
    a, b = TestClient(app), TestClient(app)
    rid = upload(a)
    wait_done(a, rid)
    demo = a.post("/api/demo").json()["run_id"]
    return a, b, rid, demo


def test_new_ids_are_32_characters_and_the_owner_is_only_a_hash(world):
    a, b, rid, _ = world
    assert len(rid) == 32 and int(rid, 16) >= 0
    st = runner.read_status(rid)
    token = a.cookies.get(owner.COOKIE)
    assert token and len(token) == 43
    assert st["owner"] == owner.hash_token(token) and token not in json.dumps(st)
    for p in (RUNS_DIR / rid).rglob("*"):                   # the raw token is nowhere in the run's folder (metadata, result, saved chat, events)
        if p.is_file() and p.suffix in (".json", ".txt", ".md", ".jsonl"):
            assert token not in p.read_text(errors="ignore"), p.name


def test_cookie_flags():
    c = TestClient(app)
    r = c.post("/api/runs", data={"goal": "Explore"}, files=[("files", ("d.csv", make_csv()))])
    h = r.headers["set-cookie"].lower()
    assert "httponly" in h and "samesite=lax" in h and "path=/" in h and "max-age=" in h and "secure" not in h
    assert int(h.split("max-age=")[1].split(";")[0]) > 180 * 24 * 3600
    c2 = TestClient(app, base_url="https://testserver")
    assert "secure" in c2.post("/api/runs", data={"goal": "Explore"}, files=[("files", ("d.csv", make_csv()))]).headers["set-cookie"].lower()
    c3 = TestClient(app)       # behind a proxy that says https
    os.environ["TRUST_PROXY_HEADERS"] = "1"
    try:
        assert "secure" in c3.post("/api/runs", data={"goal": "Explore"}, headers={"X-Forwarded-Proto": "https"}, files=[("files", ("d.csv", make_csv()))]).headers["set-cookie"].lower()
    finally:
        os.environ.pop("TRUST_PROXY_HEADERS")


def test_a_second_visitor_gets_a_different_owner_and_a_junk_cookie_is_ignored(world):
    a, _, rid, _ = world
    c = TestClient(app)
    c.cookies.set(owner.COOKIE, "not-a-token")
    assert c.get(f"/api/runs/{rid}").status_code == 404
    assert owner.current_owner(type("R", (), {"cookies": {owner.COOKIE: "x" * 43}})()) != owner.current_owner(type("R", (), {"cookies": {owner.COOKIE: "y" * 43}})())


# ------------------------------------------------------------------ every route, from the app's own route table
def run_routes():
    out = []
    for r in app.routes:
        if isinstance(r, APIRoute) and ("{run_id}" in r.path or r.path == "/api/glossary"):
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                out.append((m, r.path))
    return out


def fill(path, rid):
    return path.replace("{run_id}", rid).replace("{brief_id}", "b1").replace("{pin_id}", "p1").replace("{path:path}", "charts/x.png").replace("{what}", "report")


def send(c, method, path, rid):
    url = fill(path, rid)
    if path == "/api/glossary":
        return c.request(method, url, params={"workspace": "acme"}, json={"notes": "n"} if method == "PUT" else None)
    return c.request(method, url, json={} if method in ("POST", "PUT") else None)


def test_the_route_table_has_the_routes_this_test_covers():
    paths = {p for _, p in run_routes()}
    assert len(paths) >= 25 and "/api/runs/{run_id}/events" in paths and "/api/runs/{run_id}/export/{what}" in paths and "/api/runs/{run_id}/lens/ask" in paths and "/api/runs/{run_id}" in paths
    assert "/api/runs/{run_id}/files/{path:path}" in paths and "/api/runs/{run_id}/powerbi" in paths and "/api/runs/{run_id}/lens/pins/{pin_id}" in paths


def test_every_run_route_is_404_for_another_visitor_and_reaches_the_handler_for_the_owner(world):
    a, b, rid, demo = world
    assert is_gone(send(b, "GET", "/api/runs/{run_id}", "0" * 32))
    for method, path in run_routes():
        if path == "/api/glossary" or (method == "DELETE" and path == "/api/runs/{run_id}"):
            continue                           # the glossary has its own test below; the delete of the run is checked last, on its own
        if path.endswith("/events"):
            continue                           # a stream: tested on its own
        theirs = send(b, method, path, rid)
        assert is_gone(theirs), (method, path, theirs.status_code)          # the same answer as for a run that does not exist
        mine = send(a, method, path, rid)
        assert not is_gone(mine), (method, path)                            # the owner gets past the check (a handler's own answer, even a 4xx about the request)
        assert mine.status_code != 500, (method, path, mine.text[:200])
        if method == "GET" and "/files/" not in path:
            assert not is_gone(send(b, method, path, demo)), (method, path)      # the sample is readable by anyone
        if method != "GET":
            assert send(b, method, path, demo).status_code != 500, (method, path)


def test_reads_that_must_succeed_for_the_owner(world):
    a, b, rid, _ = world
    for p in ("", "/data", "/data/download", "/data/compare", "/sources", "/dashboard", "/view", "/briefs", "/lens", "/questions", "/answers-review", "/powerbi", "/files/report.md", "/files/cleaned.csv"):
        r = a.get(f"/api/runs/{rid}{p}")
        assert not is_gone(r), p
        if p in ("", "/data", "/data/download", "/data/compare", "/sources", "/dashboard", "/view", "/briefs", "/lens"):
            assert r.status_code == 200, (p, r.status_code)
        assert is_gone(b.get(f"/api/runs/{rid}{p}")), p


def test_the_event_stream_of_another_visitors_run_is_refused(world):
    a, b, rid, _ = world
    r = b.get(f"/api/runs/{rid}/events")
    assert is_gone(r)
    with a.stream("GET", f"/api/runs/{rid}/events") as ok:
        assert ok.status_code == 200 and "text/event-stream" in ok.headers["content-type"]
        assert "data:" in next(ok.iter_text())


def test_the_pdf_export_works_for_the_owner_and_not_for_others(world):
    a, b, rid, _ = world
    from app.lens import exporter
    if not exporter.browsers():
        pytest.skip("no headless browser on this machine")
    r = a.get(f"/api/runs/{rid}/export/report")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    assert b.get(f"/api/runs/{rid}/export/report").status_code == 404
    assert a.get(f"/api/runs/{rid}/export/text").status_code == 200


def test_the_list_shows_only_your_own_runs_and_the_sample(world):
    a, b, rid, demo = world
    mine = {r["run_id"] for r in a.get("/api/runs").json()}
    assert rid in mine
    other = TestClient(app)
    rid_b = upload(other)
    assert rid_b not in {r["run_id"] for r in a.get("/api/runs").json()}
    theirs = {r["run_id"] for r in other.get("/api/runs").json()}
    assert rid_b in theirs and rid not in theirs
    nobody = {r["run_id"] for r in TestClient(app).get("/api/runs").json()}
    assert rid not in nobody and nobody <= {demo}                                # no cookie: nothing but the sample


def test_delete_by_another_visitor_fails_and_the_run_remains(world):
    a, b, rid, _ = world
    assert b.delete(f"/api/runs/{rid}").status_code == 404
    assert (RUNS_DIR / rid).is_dir() and a.get(f"/api/runs/{rid}").status_code == 200
    mine = TestClient(app)
    own = upload(mine)
    wait_done(mine, own)
    assert mine.delete(f"/api/runs/{own}").status_code == 200 and not (RUNS_DIR / own).exists()


def test_a_view_change_by_another_visitor_fails_and_the_saved_view_is_unchanged(world):
    a, b, rid, _ = world
    before = a.get(f"/api/runs/{rid}/view").json()
    cat = before["items"]
    iid = next(iter(cat))
    r = b.post(f"/api/runs/{rid}/view", json={"command": {"op": "hide", "id": iid}})
    assert r.status_code == 404
    assert a.get(f"/api/runs/{rid}/view").json()["state"] == before["state"]


def test_a_suggested_lens_question_by_another_visitor_fails(world):
    a, b, rid, _ = world
    info = a.get(f"/api/runs/{rid}/lens").json()
    chip = info["chips"][0]["id"]
    assert b.post(f"/api/runs/{rid}/lens/ask", json={"chip": chip}).status_code == 404
    assert a.get(f"/api/runs/{rid}/lens").json()["history"] == info["history"]            # nothing was added by B
    assert a.post(f"/api/runs/{rid}/lens/ask", json={"chip": chip}).status_code == 200


def test_the_sample_is_readable_by_anyone_and_cannot_be_changed(world):
    _, b, _, demo = world
    for p in ("", "/view", "/lens", "/briefs", "/dashboard"):
        assert b.get(f"/api/runs/{demo}{p}").status_code == 200, p
    assert b.delete(f"/api/runs/{demo}").status_code == 403
    assert b.post(f"/api/runs/{demo}/lens/pins", json={"turn": "x", "target": "report"}).status_code == 403
    assert b.delete(f"/api/runs/{demo}/lens/views/short").status_code == 403
    assert b.post(f"/api/runs/{demo}/briefs/x/rename", json={"title": "t"}).status_code == 403
    assert b.post(f"/api/runs/{demo}/answers", json={}).status_code == 403
    assert b.post(f"/api/runs/{demo}/remove-derived", json={"names": ["x"]}).status_code == 403
    assert runner.is_demo(demo) and (RUNS_DIR / demo).is_dir()


# ------------------------------------------------------------------ the glossary
def test_the_glossary_of_another_visitor_is_invisible_and_uneditable(world, tmp_path, monkeypatch):
    monkeypatch.setenv("GLOSSARY_PATH", str(tmp_path / "g.json"))
    a, b = TestClient(app), TestClient(app)
    assert a.put("/api/glossary", params={"workspace": "acme"}, json={"definitions": {"active": "bought in 90 days"}, "notes": "mine"}).status_code == 200
    assert a.get("/api/glossary", params={"workspace": "acme"}).json()["notes"] == "mine"
    assert "acme" in a.get("/api/glossary").json()["workspaces"]
    assert b.get("/api/glossary", params={"workspace": "acme"}).json()["notes"] == ""
    assert b.get("/api/glossary").json() == {"workspaces": {}}
    assert b.delete("/api/glossary", params={"workspace": "acme"}).status_code == 404
    assert b.put("/api/glossary", params={"workspace": "acme"}, json={"notes": "theirs"}).status_code == 200      # B writes only to B's own "acme"
    assert a.get("/api/glossary", params={"workspace": "acme"}).json()["notes"] == "mine"
    assert a.delete("/api/glossary", params={"workspace": "acme"}).status_code == 200
    assert b.get("/api/glossary", params={"workspace": "acme"}).json()["notes"] == "theirs"
    assert TestClient(app).get("/api/glossary").json() == {"workspaces": {}}           # no cookie: nothing


# ------------------------------------------------------------------ runs from before owners existed
def legacy_run():
    rid = "ab" * 6
    d = RUNS_DIR / rid
    d.mkdir(parents=True, exist_ok=True)
    (d / "status.json").write_text(json.dumps({"status": "done", "goal": "old", "created": time.time(), "updated": time.time(), "filename": "old.csv"}))
    return rid


def test_a_run_without_an_owner_is_open_when_not_a_public_demo_and_hidden_when_it_is(monkeypatch):
    rid = legacy_run()
    c = TestClient(app)
    monkeypatch.delenv("PUBLIC_DEMO", raising=False)
    assert c.get(f"/api/runs/{rid}").status_code == 200 and rid in {r["run_id"] for r in c.get("/api/runs").json()}
    assert c.delete(f"/api/runs/{rid}").status_code == 200
    rid = legacy_run()
    monkeypatch.setenv("PUBLIC_DEMO", "on")
    assert c.get(f"/api/runs/{rid}").status_code == 404 and rid not in {r["run_id"] for r in c.get("/api/runs").json()}
    assert c.delete(f"/api/runs/{rid}").status_code == 404
    assert runner.purge_expired(days=1, now=time.time() + 5 * 86400) and not (RUNS_DIR / rid).exists()          # the normal retention cleanup removes it


def test_old_12_character_ids_still_work_for_their_owner(world):
    a, b, _, _ = world
    token = a.cookies.get(owner.COOKIE)
    rid = "cd" * 6
    d = RUNS_DIR / rid
    d.mkdir(parents=True, exist_ok=True)
    (d / "status.json").write_text(json.dumps({"status": "done", "goal": "old", "created": time.time(), "updated": time.time(), "owner": owner.hash_token(token)}))
    assert a.get(f"/api/runs/{rid}").status_code == 200 and b.get(f"/api/runs/{rid}").status_code == 404


# ------------------------------------------------------------------ the secret and the logs
def test_the_raw_token_is_never_logged(world, caplog):
    a, b, rid, _ = world
    token = a.cookies.get(owner.COOKIE)
    with caplog.at_level(logging.DEBUG):
        a.get(f"/api/runs/{rid}")
        b.get(f"/api/runs/{rid}")
        a.get("/api/runs")
    assert token not in caplog.text and owner.hash_token(token) not in caplog.text


def test_the_server_secret_comes_from_the_environment_or_is_generated_and_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("OWNER_SECRET", "from-the-environment" * 2)
    assert owner.server_secret() == b"from-the-environment" * 2
    monkeypatch.delenv("OWNER_SECRET")
    monkeypatch.setattr(owner, "DATA_DIR", tmp_path)
    s1 = owner.server_secret()
    assert len(s1) >= 32 and (tmp_path / "owner_secret").read_text().strip().encode() == s1
    owner._secret_cache.clear()
    assert owner.server_secret() == s1                      # read back, not regenerated
    assert oct((tmp_path / "owner_secret").stat().st_mode & 0o777) == oct(0o600)
    owner._secret_cache.clear()
