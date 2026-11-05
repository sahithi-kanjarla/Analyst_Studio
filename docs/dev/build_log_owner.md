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

- Commit 2 (Fix 1): the sample run is read-only on the routes that persist (lens pins/views/feedback now 403 there; briefs deliberately left editable, see Decisions); ownerless legacy runs follow PUBLIC_DEMO (can_access);
  export/PDF is authorised by the same route guard (the PDF is made from a local HTML file, no internal HTTP request, so no signed token was needed).

- Commit 3 (Fix 1): backend/tests/test_owner.py (route-table test over every route containing {run_id} plus /api/glossary: B gets the identical "Run not found" 404, owner reaches the handler, sample readable;
  delete/view/lens-chip/list/glossary/legacy/PUBLIC_DEMO/cookie flags/SSE/PDF/log+metadata token checks); mutation-checked (guard disabled -> test fails);
  scripts/smoke_live.py now uses cookie jars and has the visitor-B check (6 checks); test_deploy.py adjusted (owned run under PUBLIC_DEMO, 6 smoke lines).

- Commit 4 (Fix 1): upload-panel privacy note (runs belong to this browser; clearing cookies / another device loses them; "Delete my run" removes immediately), DEPLOY.md "Visitor separation" + OWNER_SECRET, README Known limits, .env.example. Frontend build passes.

- Full suite, run once on the final code in the pinned venv: 1296 passed, 1 skipped, 10 failed. The 10 were (a) my own list test, which did not account for ownerless runs left by other tests (fixed) and
  (b) test_ui_simplify (real browser; previously skipped because no frontend build existed): it renames/edits a brief on the shared sample, which my read-only rule on brief routes blocked, so I removed the rule on briefs.
  Afterwards I re-ran only test_ui_simplify.py and test_owner.py: 27 passed. The rest of the suite was not re-run after that small change.

## Left to do
(nothing planned)

## Decisions
- Ownership is enforced by one middleware over every /api/runs/<id>/... path (default-deny), not per endpoint, so a new route cannot forget it. The route-table test was mutation-checked (guard disabled -> it fails).
- Sample run: its Lens conversation and briefs are shared server state and stay editable through Lens/briefs (the product and the UI test rely on it); chat there is canned-chip only (no AI, no typed text), so no visitor data enters it.
  Pins, saved short views, feedback, answers, re-runs and delete are 403 on the sample. A fully ephemeral sample Lens would be a larger change.
- PDF export builds a local HTML file for the headless browser; no internal HTTP request, so no signed token exists or is needed.
- Glossary entries from before this change stay in the old shared area (used only by ownerless runs); not migrated, not shown by the API.
- Server secret: OWNER_SECRET or DATA_DIR/owner_secret. Losing or changing it orphans every run.

## NOT tested
- A real browser over HTTPS behind a proxy (the Secure flag is tested with an https TestClient and X-Forwarded-Proto only).
- Real sign-in (not built); a multi-process race creating the secret file; migration of existing production data.
- The Vite dev server (cross-origin) cookie behaviour: cookies are same-origin only.

## Sample briefs made read-only (supersedes the earlier "briefs stay editable on the sample" decision)
- Rename, edit, delete and create of briefs on the shared sample run now return the same 403 as the other sample changes; reading and exporting its briefs still works. Visitors keep full control of briefs on their own runs.
- Enforced at the route (`_writable`) and again where briefs are saved (`store.put_briefs` refuses the sample's folder), so a brief made through chat is refused too.
- `test_ui_simplify` now makes a brief on the visitor's own run; new `test_sample_briefs_readonly.py` covers the sample.
- Found while doing this: the frontend only opened 12-character run ids from a link, so reloading a visitor's own (32-character) run went to the start page. Fixed in `App.tsx`.
