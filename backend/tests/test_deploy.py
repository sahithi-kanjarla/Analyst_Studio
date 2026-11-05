"""Deploy readiness: one server on PORT, health with status and version only, the site's own origin, HTTPS behind a proxy, logs without data, SSE keep-alive, the public demo (retention, access code, caps, privacy note,
delete my run), the smoke script against a locally started server, and the Docker files read for what they must do."""
import io
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

os.environ["LLM_PROVIDER"] = "none"

import pytest
from fastapi.testclient import TestClient

from app import config, guard, logsafe, main, runner, web

ROOT = Path(__file__).resolve().parents[2]
client = TestClient(main.app)


# ------------------------------------------------------------------ health and config
def test_health_says_status_and_version_only():
    j = client.get("/api/health").json()
    assert set(j) == {"status", "version"} and j["status"] == "ok" and j["version"] == config.VERSION
    text = json.dumps(j)
    assert str(config.ROOT) not in text and "llm" not in text.lower() and "/" not in text


def test_the_settings_the_page_needs_are_on_the_config_route_without_secrets_or_paths(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_SHOULD_NEVER_APPEAR")
    j = client.get("/api/config").json()
    assert {"requires_code", "chatbot", "public_demo", "retention_days"} <= set(j)
    assert "gsk_SHOULD_NEVER_APPEAR" not in json.dumps(j) and str(config.ROOT) not in json.dumps(j)


# ------------------------------------------------------------------ cross-origin
def test_only_the_sites_own_origin_is_allowed_and_it_is_configurable(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("CORS_ORIGINS", raising=False)
    monkeypatch.delenv("SITE_ORIGIN", raising=False)
    assert web.allowed_origins() == []                                                     # production: nothing but the same origin, which needs no header
    r = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers
    pre = client.options("/api/runs", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
    assert pre.status_code == 403
    monkeypatch.setenv("SITE_ORIGIN", "https://studio.example.com/")
    monkeypatch.setenv("CORS_ORIGINS", "https://other.example.com")
    assert web.allowed_origins() == ["https://other.example.com", "https://studio.example.com"]
    ok = client.get("/api/health", headers={"Origin": "https://studio.example.com"})
    assert ok.headers["access-control-allow-origin"] == "https://studio.example.com"
    assert client.options("/api/runs", headers={"Origin": "https://studio.example.com", "Access-Control-Request-Method": "POST"}).status_code == 204
    assert "access-control-allow-origin" not in client.get("/api/health", headers={"Origin": "https://evil.example"}).headers
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.delenv("CORS_ORIGINS")
    monkeypatch.delenv("SITE_ORIGIN")
    assert web.allowed_origins() == web.DEV_ORIGINS                                       # development keeps the Vite dev server working


# ------------------------------------------------------------------ HTTPS behind a proxy
def test_https_is_recognised_behind_a_proxy_only_when_trusted(monkeypatch):
    monkeypatch.delenv("TRUST_PROXY_HEADERS", raising=False)
    r = client.get("/api/health", headers={"X-Forwarded-Proto": "https"})
    assert "strict-transport-security" not in r.headers                                    # a header anyone can send is not believed by default
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["referrer-policy"] == "no-referrer"
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "1")
    r = client.get("/api/health", headers={"X-Forwarded-Proto": "https"})
    assert "max-age" in r.headers["strict-transport-security"]
    assert "strict-transport-security" not in client.get("/api/health", headers={"X-Forwarded-Proto": "http"}).headers
    assert guard.visitor(type("R", (), {"headers": {"x-forwarded-for": "203.0.113.9, 10.0.0.1"}, "client": type("C", (), {"host": "10.0.0.1"})()})()) == "203.0.113.9"


# ------------------------------------------------------------------ logs never hold data
def test_log_filters_drop_query_strings_and_error_details():
    f = logsafe.StripQuery()
    rec = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d', ("1.2.3.4:5", "GET", "/api/runs/abc/data?q=SECRET_CELL&sort=x", "1.1", 200), None)
    f.filter(rec)
    assert "SECRET_CELL" not in rec.getMessage() and "/api/runs/abc/data" in rec.getMessage()
    try:
        raise KeyError("SECRET_CELL")
    except KeyError:
        import sys as _s
        err = logging.LogRecord("uvicorn.error", logging.ERROR, "", 0, "Exception in ASGI application", (), _s.exc_info())
    logsafe.NoMessagesNoTracebacks().filter(err)
    h = logging.StreamHandler(buf := io.StringIO())
    h.setFormatter(logging.Formatter(logsafe.SAFE_FORMAT))
    h.emit(err)
    assert "SECRET_CELL" not in buf.getvalue() and "KeyError" in buf.getvalue() and "Traceback" not in buf.getvalue()


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def start_server(tmp_path, port, **env):
    full = {**os.environ, "PORT": str(port), "DATA_DIR": str(tmp_path / "data"), "LLM_PROVIDER": "none", "CHART_DPI": "50", "APP_ENV": "production", **env}
    p = subprocess.Popen([sys.executable, "-m", "app"], cwd=ROOT / "backend", env=full, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for _ in range(80):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
            return p
        except Exception:
            time.sleep(0.5)
    p.kill()
    raise AssertionError("the server did not start: " + (p.stdout.read() if p.stdout else ""))


def stop(p) -> str:
    p.terminate()
    try:
        out, _ = p.communicate(timeout=15)
    except Exception:
        p.kill()
        out, _ = p.communicate()
    return out or ""


def test_the_server_listens_on_port_and_its_log_holds_no_question_search_term_or_file_content(tmp_path):
    port = free_port()
    p = start_server(tmp_path, port, UPLOADS_REQUIRE_CODE="letmein")
    try:
        base = f"http://127.0.0.1:{port}"
        assert json.loads(urllib.request.urlopen(base + "/api/health").read())["status"] == "ok"            # PORT was honoured
        demo = json.loads(urllib.request.urlopen(urllib.request.Request(base + "/api/demo", data=b"", method="POST")).read())["run_id"]
        urllib.request.urlopen(f"{base}/api/runs/{demo}/data?q=SEARCHTERM_ZZ9&sort=SORTCOL_ZZ9").read()
        body = json.dumps({"question": "QUESTION_TEXT_ZZ9 how many rows"}).encode()
        try:
            urllib.request.urlopen(urllib.request.Request(f"{base}/api/runs/{demo}/lens/ask", data=body, headers={"Content-Type": "application/json"}, method="POST")).read()
        except Exception:
            pass
        mp = b'--B\r\nContent-Disposition: form-data; name="file"; filename="x.csv"\r\n\r\ncol\nFILECONTENT_ZZ9\nFILECONTENT_ZZ9\r\n--B--\r\n'
        try:
            urllib.request.urlopen(urllib.request.Request(base + "/api/runs", data=mp, headers={"Content-Type": "multipart/form-data; boundary=B", "X-Access-Code": "letmein"}, method="POST")).read()
        except Exception:
            pass
    finally:
        log = stop(p)
    assert "uvicorn.access" in log and "GET /api/runs" in log
    for secret in ("SEARCHTERM_ZZ9", "SORTCOL_ZZ9", "QUESTION_TEXT_ZZ9", "FILECONTENT_ZZ9", "letmein"):
        assert secret not in log, secret


# ------------------------------------------------------------------ SSE behind a proxy
def test_the_event_stream_sends_keep_alive_comments_and_no_buffering_headers(monkeypatch):
    import asyncio
    monkeypatch.setattr(main, "KEEPALIVE_S", 0.5)
    rid = runner.create_run("g", "a.csv", b"a,b\n1,2\n")
    runner.write_status(rid, "running")
    resp = asyncio.run(main.stream_events(rid))
    assert resp.media_type == "text/event-stream" and "no-transform" in resp.headers["cache-control"]
    assert resp.headers["x-accel-buffering"] == "no" and resp.headers["connection"] == "keep-alive"

    async def first_comment():
        async for chunk in resp.body_iterator:
            if chunk.startswith(": keep-alive"):
                return chunk
    assert asyncio.run(asyncio.wait_for(first_comment(), 10)) == ": keep-alive\n\n"
    runner.write_status(rid, "done")


# ------------------------------------------------------------------ the public demo
def test_a_public_demo_keeps_runs_three_days_unless_told_otherwise(monkeypatch):
    monkeypatch.delenv("RETENTION_DAYS", raising=False)
    monkeypatch.delenv("PUBLIC_DEMO", raising=False)
    assert config.retention_days() == 30
    monkeypatch.setenv("PUBLIC_DEMO", "on")
    assert config.retention_days() == 3 and client.get("/api/config").json()["retention_days"] == 3
    monkeypatch.setenv("RETENTION_DAYS", "7")
    assert config.retention_days() == 7
    old = runner.create_run("g", "a.csv", b"a,b\n1,2\n")
    runner.write_status(old, "done")
    monkeypatch.delenv("RETENTION_DAYS")
    assert old not in runner.purge_expired(now=time.time() + 2 * 86400)                   # two days old: kept
    assert old in runner.purge_expired(now=time.time() + 4 * 86400)                       # four days old, default three: deleted


def upload(code=None, **kw):
    data = {"goal": "", **({"access_code": code} if code is not None else {})}
    return client.post("/api/runs", data=data, files={"file": ("t.csv", b"a,b\n" + b"\n".join(b"%d,%d" % (i, i * 2) for i in range(40)))}, **kw)


def test_uploads_and_typed_lens_questions_need_the_code_on_a_public_demo_but_the_sample_does_not(monkeypatch):
    monkeypatch.setenv("PUBLIC_DEMO", "on")
    monkeypatch.delenv("UPLOADS_REQUIRE_CODE", raising=False)
    r = upload()
    assert r.status_code == 403 and "switched off" in r.json()["detail"]                   # no code configured: closed, not open
    monkeypatch.setenv("UPLOADS_REQUIRE_CODE", "s3cret")
    assert upload().status_code == 403 and upload("wrong").status_code == 403
    assert client.get("/api/config").json()["requires_code"] is True
    guard.reset_rate()
    demo = client.post("/api/demo").json()["run_id"]                                       # the sample needs no code
    assert client.get(f"/api/runs/{demo}").json()["status"] == "done"
    ask = lambda rid, hdr=None: client.post(f"/api/runs/{rid}/lens/ask", json={"question": "how many rows are there"}, headers=hdr or {})
    sample = ask(demo)
    assert sample.status_code == 200 and not sample.json()["answer"].get("ai_calls")        # the sample run's chat needs no code and never calls the AI
    import shutil
    monkeypatch.setenv("CHATBOT", "on")
    mine = "b" * 12
    shutil.copytree(config.DEMO_DIR, runner.run_dir(mine))
    from app import owner
    client.cookies.set(owner.COOKIE, "M" * 43)                       # a public demo shows a run only to its owner: this visitor owns it
    runner.write_status(mine, "done", owner=owner.hash_token("M" * 43))
    try:
        assert ask(mine).status_code == 403 and ask(mine, {"X-Access-Code": "wrong"}).status_code == 403          # a typed question on your own run needs the code
        assert ask(mine, {"X-Access-Code": "s3cret"}).status_code == 200
    finally:
        shutil.rmtree(runner.run_dir(mine), ignore_errors=True)


def test_the_sample_report_makes_no_ai_call_and_needs_no_code(monkeypatch):
    monkeypatch.setenv("PUBLIC_DEMO", "on")
    monkeypatch.setenv("UPLOADS_REQUIRE_CODE", "s3cret")
    called = []

    class Boom:
        def invoke(self, *a, **k):
            called.append(1)
            raise AssertionError("the sample must not call the AI")
    monkeypatch.setattr(main, "get_llm", lambda: (Boom(), "boom"))
    import app.llm as llmmod
    monkeypatch.setattr(llmmod, "get_llm", lambda: (Boom(), "boom"))
    demo = client.post("/api/demo").json()["run_id"]
    assert client.get(f"/api/runs/{demo}").json()["status"] == "done"
    info = client.get(f"/api/runs/{demo}/lens").json()
    assert info["enabled"] and info["ai"] is False
    t = client.post(f"/api/runs/{demo}/lens/ask", json={"chip": (info["chips"][0]["id"] if info["chips"] else None)}, headers={"X-Access-Code": "s3cret"}) if info["chips"] else None
    assert not called


def test_the_daily_caps_default_low_on_a_public_demo_are_configurable_and_the_message_is_friendly(monkeypatch, tmp_path):
    for k in ("DAILY_AI_RUNS", "DAILY_LENS_QUESTIONS", "PUBLIC_DEMO"):
        monkeypatch.delenv(k, raising=False)
    assert (guard.cap_of("ai_runs"), guard.cap_of("lens_questions")) == (100, 500)
    monkeypatch.setenv("PUBLIC_DEMO", "on")
    assert (guard.cap_of("ai_runs"), guard.cap_of("lens_questions")) == (10, 60)
    monkeypatch.setenv("DAILY_AI_RUNS", "2")
    monkeypatch.setenv("DAILY_LENS_QUESTIONS", "1")
    assert (guard.cap_of("ai_runs"), guard.cap_of("lens_questions")) == (2, 1)
    monkeypatch.setattr(guard, "_usage_path", lambda: tmp_path / "usage.json")
    guard.reserve_ai_run()
    guard.reserve_ai_run()
    with pytest.raises(Exception) as e:
        guard.reserve_ai_run()
    assert e.value.status_code == 429 and "try again tomorrow" in e.value.detail and "sample report is open" in e.value.detail
    assert guard.under_cap("lens_questions") and (guard.count("lens_questions") or True) and not guard.under_cap("lens_questions")
    assert "suggested questions" in guard.LENS_MESSAGE


def test_the_upload_panel_says_what_is_stored_for_how_long_and_what_goes_to_the_ai():
    src = (ROOT / "frontend" / "src" / "components" / "UploadForm.tsx").read_text(encoding="utf-8")
    note = re.search(r'<p className="privacy-note[^>]*>(.*?)</p>', src, re.S).group(1)
    for must in ("stored on this server", "retention_days", "deleted automatically", "Delete my run", "summaries of your data", "never individual rows", "belong to this browser", "clear your cookies", "another device", "removes that run immediately"):
        assert must in note, must
    assert 'role="note"' in src


def test_delete_my_run_removes_the_run_and_its_uploaded_files_at_once():
    rid = runner.create_run("a goal", [("sales.csv", b"a,b\n1,2\n"), ("more.csv", b"c\n3\n")])
    runner.write_status(rid, "done")
    wd = runner.run_dir(rid)
    (wd / "result.json").write_text("{}")
    uploaded = sorted(p for p in (wd / "inputs").iterdir())
    assert len(uploaded) == 2 and all(p.exists() for p in uploaded)
    r = client.delete(f"/api/runs/{rid}")
    assert r.status_code == 200 and r.json() == {"deleted": rid, "files_removed": r.json()["files_removed"]} and r.json()["files_removed"] >= 4
    assert not wd.exists() and not any(p.exists() for p in uploaded)                         # the folder and every uploaded file are gone
    assert client.get(f"/api/runs/{rid}").status_code == 404 and client.delete(f"/api/runs/{rid}").status_code == 404
    demo = client.post("/api/demo").json()["run_id"]
    assert client.delete(f"/api/runs/{demo}").status_code == 403 and runner.run_dir(demo).exists()          # the sample is never deleted


def test_the_run_menu_has_delete_my_run_for_real_runs_only():
    bar = (ROOT / "frontend" / "src" / "components" / "nav" / "RunBar.tsx").read_text(encoding="utf-8")
    res = (ROOT / "frontend" / "src" / "components" / "Results.tsx").read_text(encoding="utf-8")
    assert "Delete my run" in bar and "isDemo(run.run_id) ? undefined" in res and "api.deleteRun" in res


# ------------------------------------------------------------------ the smoke script, against a server started here
def test_smoke_live_passes_against_a_local_server_and_fails_on_a_wrong_code(tmp_path):
    port = free_port()
    p = start_server(tmp_path, port, UPLOADS_REQUIRE_CODE="abc123")
    try:
        ok = subprocess.run([sys.executable, str(ROOT / "scripts" / "smoke_live.py"), f"http://127.0.0.1:{port}", "abc123"], capture_output=True, text=True, timeout=300)
        assert ok.returncode == 0, ok.stdout + ok.stderr
        lines = [x for x in ok.stdout.splitlines() if x.startswith(("PASS", "FAIL"))]
        assert len(lines) == 6 and all(x.startswith("PASS") for x in lines)       # the sixth: visitor B cannot list, read or delete visitor A's run
        bad = subprocess.run([sys.executable, str(ROOT / "scripts" / "smoke_live.py"), f"http://127.0.0.1:{port}", "nope"], capture_output=True, text=True, timeout=300)
        assert bad.returncode == 1 and "FAIL  an upload of a tiny CSV" in bad.stdout
        gone = subprocess.run([sys.executable, str(ROOT / "scripts" / "smoke_live.py"), "http://127.0.0.1:1"], capture_output=True, text=True, timeout=120)
        assert gone.returncode == 1 and "FAIL" in gone.stdout
    finally:
        stop(p)


# ------------------------------------------------------------------ the Docker files, read for what they must do
def test_the_dockerfile_builds_the_frontend_installs_chromium_drops_to_a_non_root_user_and_keeps_data_in_one_volume():
    d = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "npm run build" in d and "COPY --from=frontend /app/frontend/dist" in d
    assert "pip install --no-cache-dir -r backend/requirements.txt" in d and "chromium" in d and "BROWSER_PATH=/usr/bin/chromium" in d
    assert not re.search(r"^USER ", d, re.M), "no USER line: the entrypoint chowns /data as root, then drops to uid 10001 (see test_dockerfile.py)"
    assert "docker-entrypoint.sh" in d and "useradd" in d and "--uid 10001" in d
    assert "DATA_DIR=/data" in d and 'VOLUME ["/data"]' in d and "chown -R app:app /data" in d
    assert "HEALTHCHECK" in d and "/api/health" in d and "PORT" in d.split("HEALTHCHECK")[1]
    assert 'CMD ["python", "-m", "app"]' in d and "APP_ENV=production" in d
    ig = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert ".env" in ig.split() and "backend/data" in ig


def test_compose_mounts_the_one_data_volume_and_reads_the_port():
    c = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "analyst-data:/data" in c and "${PORT:-8000}" in c and "/api/health" in c and "env_file" in c and "restart: unless-stopped" in c


def test_everything_the_app_writes_goes_under_the_data_dir(monkeypatch, tmp_path):
    code = ("import os;os.environ['DATA_DIR']=%r;os.environ.pop('RUNS_DIR',None);os.environ.pop('GLOSSARY_PATH',None);os.environ.pop('AI_CACHE_DIR',None);"
            "from app import config, glossary, aicache, guard;print(config.RUNS_DIR, glossary.glossary_path(), aicache.cache_dir(), guard._usage_path())") % str(tmp_path)
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT / "backend", capture_output=True, text=True, env={**os.environ, "RUNS_DIR": ""}).stdout.split()
    assert len(out) == 4 and all(o.startswith(str(tmp_path)) for o in out), out


# ------------------------------------------------------------------ Dockerfile and requirements (hardening)
def test_no_dockerfile_instruction_line_has_a_trailing_comment():
    # Docker takes "FROM image  # note" as part of the instruction and fails; comments need their own line.
    bad = []
    for n, line in enumerate((ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines(), 1):
        if line.strip() and not line.lstrip().startswith("#") and " #" in line:
            bad.append((n, line))
    # HEALTHCHECK's python -c string has no " #"; anything listed here is a real trailing comment
    assert not bad, bad


def _pkg_names(path):
    names = set()
    for l in path.read_text().splitlines():
        l = l.split("#")[0].strip()
        if l and not l.startswith("-"):
            names.add(re.split(r"[<>=!~\[ ]", l)[0].lower().replace("_", "-"))
    return names


def test_runtime_requirements_have_the_sql_guard_packages_and_no_test_tools():
    runtime = _pkg_names(ROOT / "backend" / "requirements.txt")
    assert {"duckdb", "sqlglot"} <= runtime
    assert "pytest" not in runtime and "httpx" not in runtime
    pinned = _pkg_names(ROOT / "backend" / "constraints.txt")
    assert {"duckdb", "sqlglot"} <= pinned
    dev = (ROOT / "backend" / "requirements-dev.txt").read_text()
    assert "-r requirements.txt" in dev and "pytest" in dev


def test_every_third_party_import_in_the_app_is_a_runtime_requirement():
    import ast
    runtime = _pkg_names(ROOT / "backend" / "requirements.txt")
    alias = {"dotenv": "python-dotenv", "langchain-groq": "langchain-groq", "langchain-google-genai": "langchain-google-genai",
             "langchain-ollama": "langchain-ollama", "starlette": "fastapi", "pydantic": "fastapi", "multipart": "python-multipart"}
    optional = {"langchain-google-genai", "langchain-ollama"}
    stdlib = set(sys.stdlib_module_names)
    seen = set()
    for f in (ROOT / "backend" / "app").rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else ([node.module] if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module else [])
            for m in mods:
                top = m.split(".")[0]
                if top not in stdlib and top != "app":
                    seen.add(top.replace("_", "-"))
    missing = {alias.get(m, m) for m in seen} - runtime - optional
    assert not missing, missing
