# Build log: visitor separation and the dependency pin (branch studio-v3-owner, from studio-v3)

Another session can continue from here. Order: Fix 2 first, then Fix 1 in four commits.

## Done
- Commit 0 (Fix 2): certifi pinned 2026.10.18 -> 2026.10.19 (langchain-core 1.7.4 needs >= 2026.10.19). Fresh venv: `pip install -r requirements.txt -c constraints.txt` clean, `pip check` clean.
  Added scripts/check_constraints.py (pip dry run), backend/tests/test_constraints.py, DEPLOY.md note.

- Commit 1 (Fix 1): backend/app/owner.py (current_owner, cookie, HMAC hash, server secret in DATA_DIR/owner_secret or OWNER_SECRET, can_access, guard_run_routes);
  web.py middleware applies the check to EVERY /api/runs/<id>/... path (404 for non-owner; sample run public), so a new route cannot forget it;
  runs store `owner` (hash) in status.json; re-runs inherit it; new ids are uuid4 hex (32); old 12-char ids still accepted (RUN_ID_RE);
  GET /api/runs lists own runs + sample; glossary scoped per owner (glossary.scoped contextvar, key "<scope>::<company>"; run worker threads and routes set it from the run's owner);
  existing tests test_clarify/test_saved_answers pin a visitor cookie (conftest.pinned_scope).

- Commit 2 (Fix 1): the sample run is read-only on the routes that persist (lens pins/views/feedback, brief rename/edit/delete now 403 there); ownerless legacy runs follow PUBLIC_DEMO (can_access);
  export/PDF is authorised by the same route guard (the PDF is made from a local HTML file, no internal HTTP request, so no signed token was needed).

- Commit 3 (Fix 1): backend/tests/test_owner.py (route-table test over every route containing {run_id} plus /api/glossary: B gets the identical "Run not found" 404, owner reaches the handler, sample readable;
  delete/view/lens-chip/list/glossary/legacy/PUBLIC_DEMO/cookie flags/SSE/PDF/log+metadata token checks); mutation-checked (guard disabled -> test fails);
  scripts/smoke_live.py now uses cookie jars and has the visitor-B check (6 checks); test_deploy.py adjusted (owned run under PUBLIC_DEMO, 6 smoke lines).

## Left to do
- Fix 1, commit 4: DEPLOY.md, README Known limits, upload-panel privacy note.
- Full suite once at the end (the venv /tmp/claude-0/v1 holds the pinned versions; the single final run also covers Fix 2's "suite passes on those versions").

## Notes
- The local studio-v3 was stale (no deploy work); reset to origin/studio-v3 before branching.
- Headless-browser PDFs are made from a local HTML file built on the server (lens/exporter.py), not by loading the app's own URL, so no internal signed token is needed; the export endpoint itself gets the owner check.

## NOT tested
(filled in at the end)
