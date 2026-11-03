# Build log: studio-v3-review

Branch from `studio-v3`. Purpose: a real-AI review of the app on a pack of synthetic datasets, with pandas as the answer key. No change to the app.

## Done
- `scripts/make_review_datasets.py`: 26 datasets (R01-R17 single files, M01-M09 connected). New: **M07** six monthly files with identical columns (March has an extra column, May has the columns reversed), **M08** CSV orders + Excel customers with a 4-row header offset and a footer line, **M09** eight files (the upload limit): four connected, four unrelated. The 23 earlier datasets are byte-identical after the rebuild (git shows only the README, the index and the new folders).
- `scripts/review_run.py`: runs one dataset at a time with the real AI (or `--fake-ai`), compares each figure with `expected.json`, asks five Lens questions, times every stage, writes `review_out/<folder>.md`, `summary.md`, and appends `scoreboard.md` / `scoreboard.csv`. Options: `--list --only --max-tokens --force --screenshots --fake-ai --out --goal`.
- `scripts/review_shots.js`: one browser, one screenshot per tab at 1440 px, and the headings and chart cards as the browser shows them.
- Timing needs no change to the app: the script wraps the stage functions, `pacing.wait_for_room` and `llm._sleep` while it runs (each wrapper calls the original unchanged). AI wait = the `seconds` the app already logs per call.

## Tested
- Dry run with the fake AI on R01, M01, M07 (with `--max-tokens 3000`, saved as PARTIAL), M09, and `--screenshots` on M01. No real AI call, no full test suite. The app was not touched, so no app tests were re-run.

## Not tested
- Anything with the real AI: the Lens checks (a fake model cannot plan, so Q1-Q4 FAIL in a dry run by design), the pacing and rate-limit wait totals, and `--max-tokens` against real token counts.
- `--screenshots` only on the sample of one dataset.

## Found by the dry run (rules path, to confirm with the real AI)
- M07: the app does not stack same-shaped files. It analyses one table and lists the other five as ignored, so every M07 figure fails.
- R01: the "counted order amounts once" step blanks 888 of 900 `unit_cost` values although the cost differs by item.

## Follow-up: the "auth" failures (3 Nov 2026)

- The first real run (R01) had every AI call refused with `auth` and ran 13 minutes on rules. The key in the repo-root `.env` (the file the app itself loads; no environment variable overrides it) is refused by Groq: HTTP 401, "Invalid API Key". The runner and the app load the same key, so this is not a loading difference; the key itself is invalid or revoked and needs replacing.
- `review_run.py` now stops at once after the first `auth` answer ("AI key refused: nothing was tested with the real AI"), saves the dataset as status `ai-refused`, starts no later dataset and exits with code 2. Tested against the real (invalid) key: 4 seconds instead of 13 minutes.
- New `ai_mode` column in the scoreboard and review files: `real`, `partly`, `rules-only`, or `dry-run`. Only `real` complete runs count in the overall percentage. The earlier R01 row is marked `rules-only` and the fake-AI rows `dry-run` (a one-off edit of the existing files).
- Not tested: a key that works (no valid key was available), and a mid-run auth failure after earlier successful calls.
