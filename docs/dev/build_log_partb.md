# Build log: studio-v3-partb (Tasks B to F)

Branch `studio-v3-partb`, created from `origin/studio-v3` at `303942f8c8076649c6e6d7a2fbb52de70168f4db` ("Groq default model: use openai/gpt-oss-120b (llama-3.3-70b-versatile returns model_not_found); add test"; the hash matches the one expected, the message is worded slightly differently). `main`, `backup-cleaned`, `studio-v2` and `studio-v3` were not touched. Commits are plain messages with no attribution lines. No real AI call was made anywhere: every test uses a fake model, and the interface was checked in a headless browser on the shipped sample run.

## Result

- **Full backend suite (once, at the end, final tree):** **828 passed, 0 failed, in 199.33 s (3 min 19 s)** (`cd backend && pytest tests -q`). The README test count (737) is older; this is the current number.
- **Frontend:** `npm run build` (tsc + vite) passes. It did **not** pass on `studio-v3` before this work: `LensAnswer.tsx` had an unused parameter (TS6133); fixed (one line). Two CSS-minify warnings about `color-mix` also appear on `studio-v3` and are unrelated.
- **New tests:** `test_partb_e.py` (4), `test_partb_pacing.py` (5), `test_partb_guard.py` (9), `test_partb_view.py` (14). Two older Lens tests were changed on purpose (see Decisions).
- **Browser check** (Playwright, desktop 1280 and phone 390, sample run): hide an action and restore it from Hidden items; short view on Insights and Dashboard; "Ask Lens about this" shows the chip "Focus: revenue by Segment" and removing it clears it; the chat sentence "make the insights tab shorter with only needed charts" applies the short view; the print panel lists hidden items; no console errors; no horizontal overflow on any of the four tabs at 390 px; a sample-run edit survives a reload. The script lived in the scratchpad and is not committed (the repository has no frontend test setup).

## Done

### Task E: strategy fixes
- **Tie-break:** `strategy.prioritise(cands, findings)` now sorts by data-backed first, impact/confidence/effort score, then the **largest checked effect among the findings a strategy cites**, then the order the candidates were found in. Never by name (the old key ended in the title). Effect sizes use the finding's own `stats.effect`; Cramer's V is squared so it sits on the same 0..1 scale as eta-squared. The value is stored as `effect_size` on each strategy.
- **Display names:** after ranking and the AI refinement, every strategy field (title, objective, rationale, how to test, actions, KPIs, risks) goes through `understanding.readable` (the existing name replacement), so `discount_pct` shows as the understanding record's display name. Lens strategy, priority, summary and general answers do the same with `Ctx.readable`. The real-AI output saved in `scripts/lens_fixpack_check_out_run1.txt` shows the raw `discount_pct` that this fixes.
- Tests: two tied strategies with different effect sizes (reverse alphabetical order wins by effect); exact tie keeps found order; impact still beats effect; no raw column name in any strategy field or in the Lens strategy answer when a display name exists.

### Task D: request pacing (`backend/app/pacing.py`)
- Before every AI call (each retry too) `llm._invoke` asks `pacing.wait_for_room(estimate)`; the estimate is about 4 characters a token (the same estimate the usage record uses) plus a reserve for the reply. If the last 60 s already hold too much, it waits until enough has aged out. A call larger than the whole window waits for an empty window and goes. Nothing about what a stage asks or how its answer is checked changed (a test compares the exact messages).
- Settings: `AI_TPM_LIMIT` (default 6000 when the provider is Groq; no pacing for other providers unless set; 0 = off), `AI_TPM_WINDOW_S` (60), `AI_TPM_MARGIN` (0.9), `AI_REPLY_TOKENS` (600).
- Tests with a fake clock and fake AI: a burst of 10 calls (about 2,600 estimated tokens each) completes, is spaced out, and no 60 s window ever holds more than 6000 estimated tokens.

### Task C: deploy prep
- `Dockerfile` (two stages: node builds the frontend, python serves both on port 8000, non-root user, health check), `docker-compose.yml` (one service, `.env`, a named volume for `backend/data`), `.dockerignore`, `.env.example` rewritten to list every setting with a short comment (no keys), README **Deploy** section.
- `backend/app/guard.py`: `UPLOADS_REQUIRE_CODE` (the code itself; checked in constant time; needed for uploads, re-runs, answering questions and typed Lens questions on a real run; the sample report, the demo, suggested questions and chart controls stay open; the upload panel shows a code field only when `/api/health` says `requires_code`); `DAILY_AI_RUNS` (default 100) and `DAILY_LENS_QUESTIONS` (default 500), counted per UTC day across all visitors, saved in `usage_daily.json` so a restart does not reset them, with friendly messages (429 for uploads; for Lens the answer says suggestions still work); `UPLOAD_RATE_LIMIT` per `UPLOAD_RATE_WINDOW_S` (default 10 per hour) per visitor, where a visitor is the connecting address, or the first `X-Forwarded-For` only when `TRUST_PROXY_HEADERS=1`.
- Tests for each protection with the fake AI (the analysis itself is stubbed out in these tests).

### Task B: view edits, by hand and by chat, one mechanism
- **One mechanism** (`backend/app/viewedit.py`): a closed list of commands (hide, show, set_mode, top_n, only_supporting, chart, move, rename, pin, unpin, reset, undo), validated against the run's own items and sections, applied by one pure function, saved with the run (`lens.json`, key `view_edits`), resolved into what each tab shows (hidden, folded under "Show more", order, titles). The Hide/Change buttons call `POST /api/runs/{id}/view`; chat calls the same `apply_many`. Several commands from one request are one undoable step; an invalid id refuses the whole request with a plain sentence.
- **By hand:** Hide and Change (rename, pin, move section, chart type, time grain, top N) on every card, chart, insight, action, strategy and section; "Hidden items (n)" with Restore, Full/Short view, Undo and Reset view on the Report, Insights, Ideas to try and Dashboard tabs; "Ask Lens about this" on cards, charts, insights and strategies.
- **By chat:** `lens/viewchat.py`. Plain phrases ("make this tab shorter", "only needed charts", "show only the top 3", "undo", "hide this" with an item in focus) are read by rules with no AI call. Otherwise the AI returns `{"action":"view","commands":[...]}` (the SYSTEM prompt now documents the closed list instead of calling these requests unsupported); code validates and applies. Styling requests ("dark mode", "switch the layout") and unclear ones get the available view changes as chips. "Shorten / simplify this tab / remove this chart" still route to view edits, not to a summary. The "view changes are coming soon" reply is gone.
- **Short view:** Insights: one line plus its chart, top 5 by the report's own order (`report_view.evidence`), the rest and the appendix under Show more. Report and Dashboard: at most 6 charts, only those an action or the summary cites. The score is 2 when an action cites a chart (through the findings behind it, or the action's own chart), plus 1 when the summary does; the report's own ranking chart counts as summary-supported.
- **Nothing deleted, no figure changes:** a test hashes `result.json` and `report.md` before and after a series of edits; hidden items stay in the catalogue and appear in the print panel as optional checkboxes (CSS shows a hidden item in print only when ticked).
- **Ask Lens about this:** the focus chip "Focus: <title>" stays until removed; each question carries the item id; code builds the item's context from the saved report (`item_context`) and puts it in the AI prompt; without an AI the item's own saved sentences are quoted.
- **Sample run:** edits are shared across visitors on a server, so the sample's state is held in the browser (`localStorage`), sent with each request, and never stored on the server.

### Task F: documentation
- README top rewritten (pitch, the problem, two screenshots, Try it); `docs/architecture.md` (pipeline diagram, Lens diagram, "Why the AI proposes and code verifies"); `docs/evaluation.md` (only numbers that exist in saved files, each with its file); Known limits: daily AI token limit, data not stored securely beyond retention, unusual-change thresholds untuned, premise check covers rank, comparison and direction claims only. A "View edits" section was added to the README.

## Skipped, partial, or different from the brief

- **Docker was not built or run.** The Docker CLI is present but I only checked `docker compose config` (passes). The image, the volume and the health check are untested.
- **Chart changes are partial:** only group comparisons (type, top N) and trends (type, grain) can be redrawn, through the same Lens query and chart code; the redrawn picture is a separate file labelled as redrawn, and the aggregation (sum or average) is taken from the finding's own text, so for a finding whose text does not say, the redraw can differ from the original chart. Other charts show no chart options.
- **Dashboard:** the interactive Power BI-style visuals are one hide-able, movable section, not hide-able chart by chart; KPI tiles on the dashboard have no controls. The finding charts under them do.
- **Insights without `report_view`** (runs made before the designed report page) show the flat card list with no view controls.
- **Print:** hidden-item checkboxes work for the Report tab, which is the only printable tab.
- **Ask Lens about this** is not on key-figure tiles or sections.
- **The report screenshot in the README was not retaken**, so it shows the page before the view bar existed.
- **Real-model checks and scorecards were not re-run** (no key here); see the list below.

## Decisions

- **Rules first, then the AI, for view commands.** A plain phrase costs no AI call and gives the same state every time (a test shows the same request with and without an AI gives the same view); the AI is used only when no rule reads the request. Rules never read data questions as view edits: "show only the top 3 insights" is a view edit, "top 5 cities by amount" is not; with an item in focus, "hide this" and "rename this to ..." are.
- **Old tests changed on purpose:** `test_lens_fixpack.py::test_view_changes_are_not_summarised` and `test_lens_stage2a.py::test_a_view_change_is_not_refused_as_a_data_question` asserted the "coming soon" text; they now assert that view requests are never summarised, that no "coming soon" appears, that styling is declined with chips, and that no AI call is made for them.
- **The Lens strategy explanation** ("why this one") used to say ties were broken alphabetically; it now says the larger checked effect, then the report's own order.
- **Quoted records stay verbatim:** display-name replacement in Lens runs on strategy, priority, summary and general answers only, not on the cleaning log or definitions, which are quoted word for word (an existing test pins that).
- **Pacing default only for Groq,** because a local model has no token window and the conftest must not make tests wait. The conftest switches the three public-use protections off and gives pacing a no-op sleep; tests that need them switch them on.
- **The access code is the environment value itself** (one shared code), sent as a form field on upload and as an `X-Access-Code` header elsewhere, kept in `sessionStorage` for the tab.
- **Daily counts include only runs and questions that use an AI** (a keyless run does not count).
- **A hidden section hides everything in it;** the Hidden items list shows only what the user hid, not the items inside.
- **Short view and "only needed charts" are separate on Insights** (short alone does not drop charts), as the brief words it; on Report and Dashboard short includes the chart rules.

## NOT tested with the real AI

- The real model returning `{"action":"view","commands":[...]}`: the prompt wording, whether it picks valid ids, and how often a reply is refused by validation.
- The focus context in a real prompt: whether the model answers about the focused item and stays inside the text it is given.
- Pacing against a real provider: how close the 4-characters-a-token estimate and the 600-token reply reserve are to Groq's own count, and whether 6000 per minute (and a 0.9 fill) matches the account's real limit (the default Groq model here is `openai/gpt-oss-120b`).
- Strategy wording after the AI's refinement: display-name replacement is applied after the model's text, and was only tested with rules-only strategies and a fake refinement path.
- The Kaveri and mall scorecards and the three real-AI Lens checks were not re-run after the strategy tie-break and display-name changes. The ranking of Kaveri strategies could change where ties existed; the saved results in `docs/evaluation.md` are from before these changes.
- The access code, daily caps and upload rate limit end to end with a real run (the tests stub the analysis start) and behind a real reverse proxy.
- Docker build, `docker compose up`, and the health check.
