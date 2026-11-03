# Build log: visitor separation and the dependency pin (branch studio-v3-owner, from studio-v3)

Another session can continue from here. Order: Fix 2 first, then Fix 1 in four commits.

## Done
- Commit 0 (Fix 2): certifi pinned 2026.10.18 -> 2026.10.19 (langchain-core 1.7.4 needs >= 2026.10.19). Fresh venv: `pip install -r requirements.txt -c constraints.txt` clean, `pip check` clean.
  Added scripts/check_constraints.py (pip dry run), backend/tests/test_constraints.py, DEPLOY.md note.

## Left to do
- Fix 1, commit 1: current_owner(), cookie, per-run owner check on every run endpoint.
- Fix 1, commit 2: run list, glossary scoping, 32-char ids, PDF/export authorisation.
- Fix 1, commit 3: route-by-route tests, smoke script visitor-B check.
- Fix 1, commit 4: DEPLOY.md, README Known limits, upload-panel privacy note.
- Full suite once at the end (the venv /tmp/claude-0/v1 holds the pinned versions; the single final run also covers Fix 2's "suite passes on those versions").

## Notes
- The local studio-v3 was stale (no deploy work); reset to origin/studio-v3 before branching.
- Headless-browser PDFs are made from a local HTML file built on the server (lens/exporter.py), not by loading the app's own URL, so no internal signed token is needed; the export endpoint itself gets the owner check.

## NOT tested
(filled in at the end)
