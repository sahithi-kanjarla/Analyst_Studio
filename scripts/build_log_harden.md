# Build log: studio-v3-harden

Branch `studio-v3-harden`, from `studio-v3`. Fake AI only, no real AI calls. One commit per numbered step.
Only the tests for the files touched were run (the laptop runs out of memory on the whole suite).

## What was done

1. **Dockerfile and requirements.**
   - The comment after `FROM python:3.13-slim` is now on its own line (Docker rejects it after the instruction).
   - `duckdb` and `sqlglot` (needed at runtime by `lens/agent/sqlguard.py`) moved from "Dev" to the runtime requirements.
     `pytest` and `httpx` moved to a new `backend/requirements-dev.txt` (`-r requirements.txt` plus the test tools), so the image no longer installs them.
     `httpx` still reaches the image as a dependency of `groq`; it is only no longer listed by us.
   - `constraints.txt` already pinned `duckdb==2.0.0` and `sqlglot==30.27.0`; those were left as they are.
     **Check:** the venv on this laptop has `sqlglot 30.23.0` installed, so the tests here did not run on exactly the pinned sqlglot. I did not verify where 30.27.0 was tested; please confirm the pin against the cloud session that wrote it.
   - `python scripts/check_constraints.py` worked (network was available): "constraints.txt resolves cleanly with requirements.txt".
   - New tests in `test_deploy.py`: no instruction line in the Dockerfile has a trailing ` #` comment; `duckdb`/`sqlglot` are runtime requirements and `pytest`/`httpx` are not; every third-party import in `backend/app` is a runtime requirement.
2. **Chart rendering lock.** `tools.py` has one re-entrant `RENDER_LOCK` and a `@rendering` decorator. It is on the 14 drawing tools (`distribution` … `seasonality`) and on `reportcharts.draw`. The outermost call always closes every open figure, also after an error. New `test_render_lock.py`: four threads draw at once and give the same text and the same picture bytes as one after another; no figure is left open; an error still closes figures; nested calls do not close the outer figure.
3. **Run limit.** `MAX_CONCURRENT_RUNS` (default 2, minimum 1) in `config.py`; `runner.py` takes a slot before the pipeline runs. A run that has to wait is written as `queued` with `message` = "Waiting for other runs to finish" (shown by `GET /api/runs/{id}` and as an event in the progress stream). The slot is always released, also when the run fails. A run paused for questions gives its slot back. Documented in `DEPLOY.md`. Tests: `test_run_limit.py`.
4. **Chunked uploads.** `main._read_limited` reads 1 MB at a time and raises the same 413 message ("The files together are larger than N MB.") as soon as the total passes the limit. `DEPLOY.md` says the host should also limit the request body. Tests with a fake 500 MB stream (only about one chunk beyond the limit is read).
5. **Sanitized markdown.** `Md.tsx` now runs `rehype-sanitize` (pinned `6.0.0` in `package.json`) after `rehype-raw`, with a schema in `mdSchema.ts` that adds `details` and `summary` (and keeps `class` and `open` on them, which the report cards use). Test `test_md_sanitize.py` renders the real component through node (`frontend/scripts/md_check.mjs`, esbuild + `react-dom/server`): `onerror`, `<script>`, `javascript:` links, `<iframe>` and `onclick` are gone; normal links, `details`/`summary` and the numeric table cells still work. The test skips itself when node or `frontend/node_modules` is missing.
6. **Interrupted runs.** At server start `runner.mark_interrupted()` marks every run left `queued` or `running` as `error` with "interrupted by a restart", so the page stops waiting and retention can delete it. The progress stream gives up on a run that has shown no event for `STREAM_STALE_S` seconds (default 900) **and** whose status has not changed for that long **and** that has no live worker thread in this process; it marks the run an error ("The run stopped responding") and ends the stream, so the browser loads the result and stops spinning. A run that is really working (or waiting for a slot) is never given up on. Tests: `test_interrupted_runs.py`.
7. **.xls.** Refused at upload, before anything is read, with 400 "Please save it as .xlsx or .csv. The old .xls format cannot be read." No run folder is created. README "Known limits" line updated.
8. **Daily AI cap.** `guard.reserve_ai_run` still counts first, but the three endpoints that call it (`POST /api/runs`, `rerun-answers`, `remove-derived`) are wrapped in `guard.gives_back_slot_on_failure`: if the request then fails for any reason, `release_ai_run()` gives the slot back (never below zero). A request that succeeds keeps it. Tests in `test_upload_limits.py`.
9. **Windows test.** In `test_owner.py` the `0o600` assertion only runs when `sys.platform != "win32"`. I used an `if` on that one line rather than a `skipif` on the whole test, because the same test also checks that the secret is generated and read back, and that still runs on Windows.
10. **DEPLOY.md.** A "Deploy same-origin" note: the visitor cookie would need credentials headers the app does not send, so `CORS_ORIGINS` / `SITE_ORIGIN` do not make a split deployment work. The env-table row for those two settings now says to leave them unset.
11. **Frontend.** `npx tsc -b` has no errors; `npm run build` succeeds (`frontend/dist` is git-ignored, so nothing to commit).

## Tests run (only the files touched, plus the neighbours of the tool and report code)

`test_render_lock`, `test_run_limit`, `test_interrupted_runs`, `test_md_sanitize`, `test_upload_limits`, `test_deploy`, `test_owner`, `test_constraints`, `test_derived_and_display`, `test_planner_coverage`, `test_content_correctness`. Result: 118 passed, 1 skipped (the one failure in that run was a missing `import sys` in `test_owner.py`, fixed; `test_owner.py` re-run alone: 18 passed).

## Not tested

- **The Docker image was not built** (no Docker daemon). The comment fix and the install list are checked by reading the Dockerfile in tests and by the pip dry run, not by `docker build`. The first build is still open.
- **The full suite was not run** here; leave it to a cloud session.
- **`test_ui_simplify.py` (real browser) was not run.** It is the test that would show whether the sanitizer changed how the report looks in the browser; only the node render test covers that. Please look at one report page and one `<details>` card by eye.
- **Concurrency under real load:** the render lock and the run limit are tested with fake runs and four threads, not with four real pipeline runs at once. The lock makes drawing one-at-a-time, so several runs' charts now queue behind each other; on a slow host that may add time per run (not measured).
- **A restart with a real run in flight** was tested by writing the status files, not by killing a live server.
- **The stale-stream timeout** is tested with `STREAM_STALE_S=1`; the browser side was not exercised (it relies on the existing end-event handling in `RunView.tsx`).
- **The `rerun-answers` and `remove-derived` slot give-back** share the decorator tested on `POST /api/runs`; they have no endpoint test of their own.
- Installed `sqlglot` here is 30.23.0, not the pinned 30.27.0 (see step 1).
- `.xls`: the old reader branch in `tableio.py` is still there; it is no longer reachable from upload.
