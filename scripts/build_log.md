# Build log: studio-v2

Branch `studio-v2`, created from `claude/affectionate-mayer-5594af` at commit `5b3de3a` ("Polish report page"). `main` and `backup-cleaned` were not touched.
Commits are plain messages, without attribution lines, as requested.

## Result

- **Full test suite:** 699 passed, 0 failed, in 184.9 s (3 min 4 s), run on the final working tree (`cd backend && pytest tests -q`).
- **Frontend:** `npm run build` (tsc + vite) passes.
- **Report check on the shipped sample run:** 7 of 7 rules pass (`python scripts/report_check.py sample_data/demo/sales_run`).
- **Baseline note:** the repo's `backend/full_suite*.txt` files record an earlier run of 1 failed, 654 passed, in 55 min. The failing test, `test_ai_call_cache.py::test_failed_and_unusable_calls_are_not_cached`, passed on every run in this branch. I did not change that test.

## What was done

### Task 1: packaging
- README: test count corrected, a Privacy section (what is stored, where, and what is sent to a model), and Known limits rewritten for retention and the row limit.
- `RETENTION_DAYS` (default 30): finished or failed runs and their uploads older than this are deleted at server start and before each new run. Runs queued or running are never deleted. `0` keeps everything.
- `MAX_ROWS` (default 500 000): CSV, TSV and TXT are counted exactly at upload and refused with the row count and the limit. Every loaded table is checked again as a backstop (Excel and JSON are checked only there).
- Tests: `backend/tests/test_limits_and_retention.py` (5).

### Task 2: report check
- `scripts/report_check.py <run id or folder>` prints PASS or FAIL per rule and exits 1 on any failure. Seven rules, as specified.
- Tests: `backend/tests/test_report_check.py` (17), one good and one bad sample per rule, plus a test that runs every rule.
- **A real defect found and fixed:** the "What to do" section listed one action while the saved report view listed three different ones, and the UI's "Answer" tier showed the saved three. The saved view now carries the same list as "What to do" (`reporter.build_report`). Its evidence is kept where the action text is unchanged.

### Task 3: AI usage accounting
- Every AI call is recorded in `llm.py`: stage (the module and function that asked), input and output tokens, whether the count is an estimate, whether a saved or repeated answer was used, failure reason and time. Failed calls are recorded too.
- Written to `ai_usage.json` in each run folder (also for paused and failed runs; a resumed run appends).
- The report's data section has one line: calls sent, calls answered from saved results, tokens (marked "estimated" when any count is estimated), and time in AI calls. For a rules-only run it says "no AI calls in this run".
- `scripts/scorecard.py` prints a per-stage table after each run.
- Tests: `backend/tests/test_ai_usage.py` (6).

### Task 4: demo data
- The sample run is `sample_data/demo/sales_run/` (1.8 MB, no `inputs/`, no `.env`). It is built from `sample_data/sales.csv`.
- "Try with sample data" (start page) calls `POST /api/demo`, which copies the run into the runs folder with a fixed id. The copy is refreshed whenever the shipped copy changes.
- Read-only: answering, re-running, and removing derived metrics return 403 for the sample run, and the UI hides those controls on it. Retention never removes it. It makes no AI calls, and a test proves the page loads with every AI entry point patched to fail.
- Tests: `backend/tests/test_demo_run.py` (6).
- `.gitignore` had `*.pkl`; a negation keeps the sample's two pickles in git.
- **A defect fixed on the way:** `GET /api/runs/{id}` returned the run id stored inside `result.json`, which for a copied run is the original id. The URL's id is now authoritative.

### Task 5: frontend redesign (partial, see "Skipped")
- **Research:** the session reached the Power BI product page. julius.ai, hex.tech and tableau.com did not resolve, and claude.ai returned 403. `scripts/design_notes.md` says which lines are from a page and which are from general knowledge.
- **Tokens:** `frontend/src/tokens.css` holds the colours (warm off-white, near-black ink, one accent, light and dark), six type sizes and an 8-point-friendly spacing scale. It loads last, so it restyles the app's existing variables. Contrast checked: every text pair is at least 5.5:1 (AA needs 4.5:1).
- **Start page:** one headline and one line for small online sellers, the upload form with "Try with sample data" beside it, then the product section, the trust line, a second upload box, and the recent runs.
- **Product tour:** tabs Dashboard, Insights, Report, Data, Cleaning log and Plan, each rendering the same component the results page uses, on the sample run. Arrow keys move between tabs. The tab row scrolls sideways on a phone.
- **Scroll story:** six steps read with IntersectionObserver. The screen follows the step in view. A click on a tab holds until the next step changes. Reduced motion removes the fades and the animation.
- **Print / Save as PDF** (report tab): include or leave out summary, key numbers, charts, tables, data-quality notes and setup, and evidence; title, company, date, note; A4 or Letter; portrait or landscape; colour or black and white. Charts and tables are not split across pages. Choices are stored in `localStorage` behind a try/catch. The print header shows title, company, date and note.
- **Charts:** the accent is the highlight colour, other bars are a neutral warm grey (`tools.py`). Previously every chart was blue.
- **Results page:** the report, cleaning-log and plan bodies are now exported components (`ReportBody`, `CleaningLog`, `PlanView`) so the tour and the results page share them.
- **Screenshots:** `docs/screenshots/` has the start page, each tour tab, a mid-scroll state, the report and the print view at desktop (1280 px) and phone (390 px) widths. Captured by `scripts/landing_screenshots.js` (Playwright, Chromium). README embeds the start page.
- **Self-review:** one pass. Fixed in it: the intro and form were misaligned, the sample button was far from the upload box, the step layout left a large gap under each title, the print header was hidden with its panel, the print background was not white, and the print button sat alone between the KPI tiles and the title.

### Task 6: ask about this report (off by default)
- `CHATBOT=on` shows **Ask about this report** on the report tab. Off, the endpoint returns 404 and the health endpoint says `chatbot: false`.
- The AI picks one operation from a closed list (top N, compare, trend, share, filter then total) and its parameters. Code checks the operation, the column names and the values against the run, and refuses anything that does not match. Every sentence and figure is computed in code, and a chart is drawn for each answer.
- A question that fits nothing says so, with two questions the report can answer.
- The sample run refuses questions (403), since it makes no AI calls.
- Tests: `backend/tests/test_ask_report.py` (10), using a fake model. They check that the figures equal independent pandas sums, that an invented column or value is not run, that an unknown operation is refused, that no model is reported plainly, and the on/off behaviour.

## Skipped, and why

- **Task 5, step 5 (download chart as image, table as CSV):** not started. Time went to the tour, print and the report check, which the brief placed first.
- **Task 5, step 3 (report main view trimmed to at most 5 summary lines and 6 charts):** the report already has this structure (answers, actions, key numbers, everything else under "View more"). I did not measure the count of charts on every run or change the summary length, because that touches the report's wording and selection logic, which the brief says not to change.
- **Task 5, step 1 research:** only one of five pages could be read (see above).
- **Print panel:** "actions" is not a separate toggle, because it sits inside the summary section of the report; the cleaning log is not in the printed report, because it is a separate tab. Both are listed under Known limits in the README.
- **Dark-mode screenshots:** not taken. Dark colours are defined and the contrast was checked numerically, but I did not look at the rendered dark pages.
- **Task 5 requirement "no purple gradients, no emoji icons":** the old `styles.css` and `polish.css` still contain gradient and glassmorphism rules in parts of the app I did not restyle (for example the agent flow and history pages). The tokens override the colours; the rules themselves were not removed.
- **Kaveri sample:** not in the repository (the `sample_data/kaveri*` folders are git-ignored or missing). The sample run uses `sample_data/sales.csv` instead.

## Decisions

- **Branch:** the working instructions named `claude/affectionate-mayer-5594af` for development, and the brief asked for `studio-v2`. I followed the brief, created `studio-v2` from `5b3de3a`, and pushed only to it. `main` and `backup-cleaned` were not touched.
- **Attribution:** the brief's rule (no attribution lines) was followed for commits, not the session reminder that asked for them.
- **Row limit:** 500 000 rows per table, counted exactly for CSV/TSV/TXT at upload; Excel and JSON are checked after loading.
- **Retention:** whole run folders are removed (uploads included); runs still queued or running are kept; `0` disables it.
- **Demo dataset:** Mall was considered first but its run is 30 MB, too heavy to commit; `sales.csv` gives a 1.8 MB run with the same report structure.
- **Demo id:** fixed to `5a1e5d3e0001`, so the page and the tests share one address.
- **Report check rules:** claims are matched by their wording ("is highest", "Top N … by total"). A check of the claim's measure uses the ranking whose measure the sentence names.
- **Chart colour:** accent for the highlight, neutral warm grey for the rest.
- **Print:** default is the main view (summary, key numbers, charts); tables, setup and details are opt-in.
- **Chatbot:** questions are answered with the operation the model picks; if the model is not configured the answer says so rather than guessing.

## Commits on `studio-v2`

- `Task 1: packaging`
- `Task 2: report check`
- `Task 3: AI usage accounting`
- `Task 4: demo data`
- `Task 5: frontend redesign`
- `Task 6: ask about this report`
- `Task 5: polish, screenshots and README` (chart palette, print button, sample refresh, README, screenshots, this log)

## How to try it

```bash
cd backend && pip install -r requirements.txt
cd ../frontend && npm ci && npm run build
cd ../backend && python -m uvicorn app.main:app --port 8000
```

Open http://127.0.0.1:8000 and press **Try with sample data**.

Check the sample report against the seven rules:

```bash
python scripts/report_check.py sample_data/demo/sales_run
```

Run the tests:

```bash
cd backend && pytest tests -q
```

Turn on "Ask about this report" (needs an AI model; for example Groq or Gemini key in `backend/.env`):

```bash
CHATBOT=on python -m uvicorn app.main:app --port 8000
```

Take the screenshots again (needs the server above running):

```bash
node scripts/landing_screenshots.js docs/screenshots both
```
