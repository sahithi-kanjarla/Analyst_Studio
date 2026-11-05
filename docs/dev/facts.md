# Analyst Studio: facts sheet

Measured or read from code on 2026-10-26, at commit `59475fd`. Nothing in the code was changed to produce this sheet.
Paths are relative to the repo root; `file:line` points to the code that backs the statement.
"Measured" means read from the run folders in `backend/data/runs/` (timestamps in `events.jsonl`, shapes from `raw.pkl` / `clean.pkl`).

## 1. Upload limits

| Limit | Value | Source |
|---|---|---|
| Files per upload | 8 (env `MAX_FILES`); more gives HTTP 400 | `backend/app/config.py:15`, `backend/app/main.py:51-52` |
| Size | 50 MB **total across all files** (env `MAX_UPLOAD_MB`); over gives HTTP 413. There is no per-file limit. | `config.py:14`, `main.py:58-61` |
| Rows and columns | **No limit in code.** Upload checks only count, extension and total bytes. | `main.py:48-63` |
| Accepted formats | `.csv .tsv .txt .xlsx .xls .json`, checked by file extension only | `config.py:19`, `main.py:55-57` |
| CSV parsing | Delimiter sniffed from the first 20 000 bytes among `, ; \t \|`; undecodable bytes replaced | `profiler.py:45-52, 63` |
| Multi-sheet Excel | A workbook with more than one non-empty sheet becomes one table per sheet, named `<file>_<sheet>`; empty sheets are dropped; a single non-empty sheet is read as a normal table. The tables are then joined by the Joiner stage. | `profiler.py:67-76` |
| Multi-file | Several tables are connected automatically; single-column keys only, never many-to-many, links with under 50% match are skipped | `relations.py`, `README.md:161, 238` |

Caveats:
- `.xls` is accepted by the extension check but needs `xlrd`, which is not in `backend/requirements.txt` and is not installed in the project venv. An `.xls` upload would fail at the profile stage. Not tested.
- The whole upload is read into memory (`await up.read()`, `main.py:58`), and pandas loads each table fully.
- Internal sampling exists only for speed, not as a cap: key detection samples above 300 000 rows (`relations.py:22, 79`), the signals stage samples 5 000 rows (`signals.py:23, 141, 184`), the dashboard slice is capped at 50 000 rows (`dashboard.py:18, 107-108`), and a table finding carries at most 500 rows (`tools.py:189`).

## 2. Storage

- **Location:** `backend/data/runs/<run_id>/`, where `run_id` is 12 hex characters (`uuid4().hex[:12]`). Override the root with env `RUNS_DIR` (`config.py:13`, `runner.py:16, 73`). `backend/data/` is git-ignored (`.gitignore`).
- **What a run stores** (`runner.py:75-83, 110`, `events.py:25`, `graph.py`; listing of finished runs):
  `inputs/` (the uploaded files as received), `input.json` (goal, file names, context), `status.json`, `events.jsonl` (timeline), `brief.json`, `questions.json` and `answers.json` (when questions were asked), `raw.pkl` and `clean.pkl` (and `tbl_<name>.pkl` per source table for multi-file runs), `cleaned.csv`, `charts/*.png`, `report.md`, `result.json`, `powerbi/` plus `powerbi_pack.zip`, and `error.txt` on failure.
- **Size of a typical run** (measured on disk, all files in the folder):

| Run | Dataset | Input | Run folder |
|---|---|---|---|
| `d9fcf75d08fa` (25 Oct) | Kaveri, 3 CSV files | 0.5 MB | 10.7 MB |
| `ecfc18aab97c` (25 Oct) | Mall, 1 CSV, 40 000 rows | 3.4 MB | 30.3 MB |
| `b7b898e431c0` (25 Oct) | Fashion, 1 XLSX, 31 047 rows | 4.2 MB | 31.1 MB |

  Runs are roughly 8 to 20 times the input size: the pickles, the CSV copy, the charts and the Power BI pack each hold a full or large copy of the data. In `b7b898e431c0` the largest parts are `clean.pkl` 6.8 MB, `raw.pkl` 6.6 MB, `powerbi/` 5.5 MB, `cleaned.csv` 4.3 MB and `powerbi_pack.zip` 2.9 MB.
- **Total on this machine:** 714 run folders, 19 411 files, about 1 564 MB (about 2.2 MB per run on average, because most are small test runs).
- **Cleanup / retention:** **None.** No code deletes a run folder (a search for `rmtree` or any expiry logic in `backend/app` finds nothing; the only deletes are temp files and the glossary lock). `GET /api/runs` lists only the 20 newest (`main.py:68-75`), but the folders stay on disk.
- **Stuck runs:** runs are never marked failed on restart. This machine has 22 runs left in `queued` and 23 in `needs_input` (abandoned or killed), alongside 666 `done` and 3 `error`.
- **User separation / auth:** **None.**
  - There is no authentication or user concept. The API says so itself (`main.py:191`, `README.md:237`).
  - Any caller who knows or guesses a run id can read that run. Ids are random (48 bits), but `GET /api/runs` lists the 20 newest runs, with goal and filename, to everyone.
  - CORS allows only `localhost:5173` and `127.0.0.1:5173` (`main.py:20`). That is a browser rule, not access control.
  - The glossary is shared and keyed by company name (lower-cased, 80 characters, `default` when empty), so anyone can read, overwrite or delete any company's saved answers (`glossary.py:34-41`, `main.py:189-214`).
  - File downloads are limited to charts, Power BI files, `report.md`, `cleaned.csv` and the pack zip, with a path-traversal check (`main.py:314-322`).

## 3. Concurrency

- **Model:** each run is a daemon `threading.Thread` started inside the API process (`runner.py:120-123`). There is **no queue, no worker pool, no semaphore and no cap**. Every accepted upload starts immediately, so N uploads mean N simultaneous runs.
- **What happens to the rest:** nothing is queued; all of them run in parallel in the same process and compete for CPU, memory and the LLM provider's rate limit. The status shows `queued` only for the instant between folder creation and thread start.
- **What keeps parallel runs safe:**
  - Per-run folders.
  - Atomic `status.json` writes with retry (`runner.py:23-38`).
  - Thread-local LLM failure capture (`llm.py:23`).
  - A lock around the event buffer (`events.py:9`).
  - A resume lock so two answer submits cannot start two threads (`runner.py:126-139`).
  - A lock plus cross-process lock file around the glossary (`glossary.py:27, 56`).
- **Not tested:** running several runs at once. All measurements below are one run at a time (the scorecard script waits for the previous run and skips if another process is busy, `scripts/scorecard.py`). The runs are CPU-bound pandas and matplotlib work, so the Python GIL will make parallel runs slower rather than faster; this is an expectation, not a measurement.
- **Restart:** a restart loses in-flight runs (daemon threads die). Their status stays `running` or `queued` forever.

## 4. Performance (measured)

Wall-clock is `events.jsonl` last timestamp minus first timestamp. All runs used the Groq free tier, model `openai/gpt-oss-120b` (the `llm_mode` field in each `result.json`), one run at a time on a Windows laptop. Time includes network waits for the LLM and the 2 s retry sleeps.
Runs that paused for clarifying questions were auto-answered by the scorecard script with defaults.

| Dataset | Shape (raw → clean) | Runs measured | Wall-clock (s) | Median | Rows/s at median |
|---|---|---|---|---|---|
| **Kaveri** (3 CSVs: 700 customers, 3 175 orders, 6 412 order lines; joined) | 6 400 × 17 → 6 400 × 19 | 5 (25 Oct 17:01 to 18:27) | 80, 211, 215, 248, 296 | 215 | about 30 |
| **Mall** (`sample_data/mall/sales.csv`) | 40 000 × 13 → 39 970 × 15 | 4 (25 Oct 17:52 to 22:55) | 87, 91, 128, 147 | 110 | about 360 |
| **Fashion** (user's `FashionStore_srawData.xlsx`, not in repo) | 31 047 × 19 → 31 047 × 19/20 | 7 (25 Oct 19:11 to 23:01) | 108, 109, 127, 152, 381, 386, 2 666 | 152 | about 200 |

Notes:
- Rows/s is rows of the joined analysis table divided by wall-clock. It is only a rough guide, because most of the time is spent in about 10 LLM calls, chart rendering and the Power BI pack, not in row processing. Time depends far more on the number of findings (28 to 41) and on LLM latency than on row count: the 40 000-row mall run is faster than the 6 400-row Kaveri run.
- **Largest dataset tested: 40 000 rows × 13 columns (mall, 3.4 MB CSV).** The largest by file size was the 4.2 MB fashion workbook (31 047 rows × 19 columns). Nothing near the 50 MB upload cap has been run, and nothing above 40 000 rows. Behaviour at 500 000 rows or more is unknown.
- Long outliers: Kaveri's slower runs and the 381 to 386 s fashion runs contain single gaps of 50 to 280 s between events (for example a 280 s gap at the Clarifier in `71f87f458319`), consistent with LLM retries or provider throttling. I did not verify the cause. The 2 666 s fashion run (`aa32a56279b2`) has one 2 254 s gap at the Joiner stage; the cause is unknown (a stalled LLM call or the machine sleeping are both plausible) and it is not representative.
- Rules-only mode (no LLM) was not timed in this audit.

## 5. Feature list (one line per stage)

Pipeline order is defined in `backend/app/graph.py:650-664`. Files are in `backend/app/`.

| Stage | What it does | Main file |
|---|---|---|
| Profile | Loads each file or sheet; infers a role per column (id, binary, categorical, numeric, text, date) from values; detects currency, percent and code-like columns | `profiler.py` |
| Relate (Joiner) | Finds keys between tables, measures match rates and orphans, builds one analysis table, never joins many-to-many; LLM can veto a join | `relations.py` |
| Brief | One LLM call: what each column means and which column each named KPI is; validated | `brief.py` |
| Clarify | At most 3 ranked questions on ambiguous data (placeholder values, scope, flag rows); answers saved to a per-company glossary and pre-filled next time; the run pauses until answered or skipped | `clarify.py`, `signals.py`, `placeholders.py`, `glossary.py`, `directives.py` |
| Plan | Chooses up to 14 analyses (18 in explore mode) from the 16-tool toolbox; the LLM plan is validated with a rules fallback; also derived ratio metrics and metric polarity | `planner.py`, `derived.py`, `derived_check.py`, `polarity.py`, `retail_roles.py`, `measures.py` |
| Clean | Scoped, fully logged cleaning: whitespace, NA tokens, numbers, dates, duplicates, placeholders | `cleaner.py` |
| Analyze | Deterministic tools produce every number and chart: distribution, outliers, category share, group compare, outcome compare, robust compare, pareto, trend, correlation, heatmap, segment trend, scatter, composition, seasonality, summary table, group table | `tools.py` |
| Critic | Tries to break conclusions (small samples, weak effects, multiple testing); up to `MAX_ITERATIONS` (1) follow-up rounds | `critic.py` |
| Insights | Turns findings into described insights with why it matters and possible explanations | `insights.py` |
| Strategy | Ranked, testable strategies from significant, correction-surviving findings, with an impact/effort matrix | `strategy.py` |
| Report | Markdown report in three tiers, a goal answer, executive summary, number check, wording rules | `reporter.py`, `summary.py`, `tiering.py`, `goal.py`, `wording.py` |
| Power BI pack | Cleaned data, data dictionary, DAX measures, visual specs, theme and how-to (a `.pbix` is not generated) | `powerbi.py` |
| API / UI | FastAPI with SSE live timeline; React app with dashboard, finding gallery, data viewer, ER diagram, glossary, history | `main.py`, `dashboard.py`, `frontend/src/` |

Safety behaviours: each stage runs inside `isolated(...)`, so a failing stage falls back to rules or is skipped with a warning, and the run still finishes (`graph.py:105-127`). AI failures (rate limit, timeout, auth) are classified and reported in the run's notes (`llm.py:79-94`). Only aggregated metadata is sent to the LLM, never raw rows (`llm.py:1-12`).

## 6. Tests and benchmarks

- **Tests:** 419 tests collected, in 36 test files under `backend/tests/`. The last recorded full run is `backend/full_suite.txt` (26 Oct 01:23, UTF-16 file): **419 passed, 15 warnings in 361.6 s**. Tests use fake models and no network (`README.md:220`). The README still says "185 tests" at line 220, which is stale.
- **Scorecards:** 12 checks per dataset against a known answer (`truth.json`), run by `scripts/scorecard.py`. History is in `scripts/scorecard_history.csv`, which is git-ignored, 46 rows from 24 Oct 11:37 to 25 Oct 22:56 (local time).

| Dataset | Result | Dates |
|---|---|---|
| Kaveri | 5/12 on first run → 12/12 by 24 Oct 23:47 (3 times in a row), then 10 to 12/12 after later changes; **12/12 in 10 of 11 runs from 25 Oct 13:57 onward** (the one miss was 11/12 on check 7 at 17:55) | 24 Oct to 25 Oct 18:32 |
| Mall | 10/12 on the first two runs (25 Oct 17:52, 17:54) → **12/12 on all three later runs** (18:15, 18:18, 22:56) | 25 Oct |
| Fashion | **No scorecard.** Fashion has no truth file, so it is a timing and manual check only. | n/a |

  The latest recorded scores are Kaveri 12/12 (25 Oct 18:32, `d9fcf75d08fa`) and Mall 12/12 (25 Oct 22:56, `ecfc18aab97c`). No scorecard was run after the 26 Oct commit `59475fd` (history ends 25 Oct 22:56), so the current head is covered by the test suite but not by a fresh scorecard.
- **Caveat on the benchmarks:** Kaveri and mall datasets were built for this project (`sample_data/kaveri_test`, `scripts/make_mall.py`), and fixes were made while looking at their scores. The 12/12 shows the known answers are found on these two datasets. It does not show the same accuracy on unseen data.

## 7. Known limitations

1. **Free-tier LLM dependence.** All measured runs used Groq's free tier. It is rate-limited, calls retry once after 2 s with a 60 s timeout (`llm.py:24-25, 159`), and some runs show gaps of 50 to 280 s. When the LLM fails, stages fall back to rules, so a report is still produced but it is less tailored. The report says when this happened (`ai_notes`). Only the Groq SDK is installed in this venv; Gemini is documented but its package is not installed here.
2. **Retail-centred helpers.** The quantity × price → amount logic (`retail_roles.py`), the sales/profit/discount/returns wording and the scorecard datasets are all retail. It is isolated in one swappable module and detection is value-based, not name-based, but other domains (churn, HR, SaaS) get the generic path only. `context.py` has industry lenses for wording, not tested at benchmark depth.
3. **No auth, no isolation, no retention** (section 2). Local use only. Do not expose the API publicly.
4. **No concurrency control** (section 3): no cap, no queue, runs lost on restart, stuck runs never cleaned up.
5. **Scale is unproven.** Largest tested is 40 000 rows. No row or column cap exists, the full upload is held in memory, and run folders are 8 to 20 times the input size. A large upload is likely to be slow or exhaust memory; this was not tested.
6. **`.xls` is probably broken** (no `xlrd`). `.json` is read with `pd.read_json`, so only table-shaped JSON works. `.txt` is parsed as delimited text.
7. **Joins:** single-column keys, never many-to-many, heuristic key detection; a table reachable only through a summarised table is reported as not used (`README.md:238-239`).
8. **Associations, not causes.** Critic flags weak or small effects, but findings are correlational.
9. **Charts are static PNGs.** Interactive slicing is via the dashboard tab (sampled to 50 000 rows) or Power BI. Non-English labels need a font with those letters; the test log shows missing-glyph warnings for Devanagari (`backend/full_suite.txt`).
10. **Dates:** ambiguous `dd/mm` vs `mm/dd` is inferred; day-first is assumed when fully ambiguous (logged).
11. **Benchmarks are on author-built data** (section 6); the Fashion dataset has no ground truth, so its correctness has not been scored.
12. **Docs drift:** `README.md:220` says 185 tests (actual 419).
