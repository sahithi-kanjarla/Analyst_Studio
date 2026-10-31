# Build log: studio-v3

Branch `studio-v3`, created from `studio-v2` (`4e253e1`, "Task 5: polish, screenshots and README"). `main`, `backup-cleaned` and `studio-v2` were not touched. Commits are plain messages with no attribution lines.

## Result

- **Full test suite:** 701 passed, 0 failed, in 141.9 s (2 min 22 s), on the final working tree (`cd backend && pytest tests -q`).
- **Frontend:** `npm run build` (tsc + vite) passes. There is no frontend test framework in the repo, so the interface was checked with Playwright screenshots (see below), plus a script that measured the page for horizontal overflow on a phone.
- **Report check on the shipped sample run:** 7 of 7 rules pass.
- **Screenshots:** `docs/screenshots/` (1280x800 and 390x844, light and dark where listed). Taken by `scripts/landing_screenshots.js`. Two review rounds were used.

## Done

### Task A: start page, product first
1. **Hero:** "Know your sales. Trust every number." (6 words), one supporting line, **Try it yourself** (primary) and **Try with sample data** (secondary). No upload form in the hero.
2. **Try it yourself** opens a dialog (a full-screen sheet on a phone). It closes with Escape, the Close button, or a click outside; focus stays inside; focus returns to the button when it closes. It holds the whole existing flow: drag and drop, goal, company and the other fields, the "I'm not sure what to look for" option and the start button. **Try with sample data** opens the full app on the read-only sample run.
3. **Product tour** under the hero: a tab row named after the app's real tabs, a framed window with a small title bar, a short fade, and one line under the frame that explains the tab. The window renders the real components (the same `TabBody` the results page uses) on the sample run, scaled to fit, clickable, with no AI and no upload. At 1280x800 the tab row and the top of the window are visible without scrolling.
4. **How it works:** four numbered steps joined by a thin line, one sentence each, and a small inert preview of a real piece of the app for each. Under 800 px it is a vertical list with the line down the side.
5. **Scroll story:** two columns, a sticky window on the left and five text blocks on the right ("It asks before it assumes", "Every figure is calculated by code", "Only what matters", "Ask questions about your report", "Print or save as PDF"). The window and the tour tab follow the block in view, including the report with the chat panel open and with the print panel open. With reduced motion it is a normal list.
6. **Trust:** three statements, no icons, each a bold line and one sentence.
7. **What it can and can't do:** two columns taken from the README's Known limits.
8. **Closing block** with the same two buttons, then a quiet footer.
- Header: logo, New analysis (opens the dialog at `#/new`), History, Glossary. The "Brain: rules-only" pill is gone from the header and from the results toolbar. The mode is a line in the report footer.

### Task B: the report as a designed page
- Backend: `reporter._page` saves the main view as data (`report_view.page`): the same cards, actions, figures and chart captions as the markdown, in the same words. No calculation or wording logic changed. Old runs without it fall back to the plain report.
- Page: title, date, "How sure we are" pill, Print and Ask buttons top right; the short answer in large type; key figures as tiles of equal height; **What to do** as numbered cards (title, one line of why, the supporting figure); **The evidence** (at most six charts in a two-column grid, each with a title, direct value labels, "What this means" and a Details toggle); **Behind the numbers** (everything else, folded); a footer with the mode, the size of the data and the date.

### Task C: one look for the whole app
- Charts drawn by the server and by the interface share one style: the accent for the highlighted bar, warm grey for the rest, direct value labels, the same font. The dashboard was a dark canvas; it is now light and uses the same tokens, cards and tables. Tables share one style: light row lines, right-aligned numbers, sticky header (`layout.css`, `Md.tsx`).
- Removed: the grid backdrop, the glass header, the blur on modals, the hero rules of the earlier landing page, the old `.trust` rule, the dark chart text colours and the blue chart bars.
- Agent pills in the timeline are one neutral colour. Tab names are plain (**Ideas to try**, **Your data**, **What we cleaned**, **How we analysed**, **How files connect**, **Power BI export**) and shared by the results page and the tour (`tabs.ts`).
- Internal reference ids such as "(r1)" and "(f4)" are no longer printed in the Insights tab (they stay as hover text). The Plan tab now says "Number we focus on", "Date used" and "What we left out" instead of "agent", "Target" and raw tool names.

### Task D: a proper chatbot
- **Entry and panel:** an **Ask about this report** button opens a chat panel docked on the right on a desktop (the report stays readable beside it) and a bottom sheet on a phone. It has a title, a message list, an input with Send, and 4 to 6 suggested-question chips written from this report's own content.
- **Chips run without AI.** Each chip carries its operation and runs directly. A typed question that clearly matches a chip (close text) runs that chip. Anything else, with no model, gets the chips offered. The sample run never calls a model, even when one is configured.
- **With a model:** the AI picks one operation from the closed list; code validates it against the run's real columns and values, runs it and draws the chart. A question that does not fit says so and offers two it can answer.
- **Answers:** a plain sentence, a small table or chart, a **How this was calculated** toggle (operation and columns), Pin and Copy. Typing indicator, friendly error, per-run history kept in `chat.json` (the 50 latest).
- **Flag:** `CHATBOT=auto` (default: on when a model is available and always on for the sample), `on` or `off`.
- **Tests** (`backend/tests/test_ask_report.py`, fake AI only): every chip runs with the model call patched to fail; a chip's figure equals an independent pandas sum; a typed question matching a chip runs it with zero model calls; an unsupported question, an invented column and an invented value are refused politely; flag modes; the sample chat works for real through the API; the sample never calls a model.

### Task E: self-review loop (two rounds)
Round 1 found and fixed: the page opened already scrolled (the timeline preview auto-scrolled); the preview boxes were different heights; the sticky window did not stick (`overflow-x: hidden` on `html, body`, now `clip`); the chat panel started below the fold and its two scroll calls fought each other; the tile numbers split mid-number; the results toolbar wrapped into two rows; the toolbar and the chart cards were inconsistent; internal ids and agent wording were visible; dark chart text was unreadable on the light dashboard.
Round 2 found and fixed: **the start page was 736 px wide on a 390 px phone** (the grid let the tab row set the column width; the overflow was hidden by the clip, so it was not visible in a screenshot until a tab could not be tapped), the story window captured the scroll wheel, the embedded chat rendered cramped and its title huge (start-page heading rules leaked into it), the trust section inherited a small grey style from an old rule, the question preview had no bullets, and the phone tab row wrapped onto three lines.

## Skipped, partial, or different from the brief

- **No frontend unit tests.** The repo has no frontend test setup, and adding one was outside "no new libraries unless really needed". Behaviour was checked in a real browser instead.
- **"How it works" previews:** step 1 is a static copy of the drop zone's markup and classes, not the live upload component. Step 2 shows the plan's question list from the sample, because the real question form needs a run that paused to ask something, and the sample did not.
- **Plain language is only partly achievable without changing wording logic.** The report's own sentences (the headline, the card lines, "Behind the numbers", the Insights tab) keep their analyst wording; I only removed the bracketed p-value from the headline in the display and left the sentence as it is. Everything the interface itself writes was rewritten in plain words.
- **Charts already saved with older runs keep their old style.** Only new runs, and the regenerated sample, use the new chart style.
- **Old runs without `report_view.page`** show the plain report with the print panel; they have no chat button.
- **The chat history of the sample report is stored in the runs folder copy**, so visitors on one machine share it.
- **Dark theme:** screenshots were taken of the start page and the report. The other views use the same tokens but were not each checked in dark.
- **Removed screenshots:** `question-form.png`, `report-definitions.png`, `glossary-page.png`, `dash-17-top.png` and `dash-17-gallery.png` showed the earlier blue UI. The glossary and dashboard ones are replaced with new captures; the question form and definitions ones cannot be retaken without a run that asks questions, so their README lines were removed.
- **Layout shift risk:** the tour window uses CSS `zoom`, which Firefox supports only from version 126.

## Decisions

- Branch, plain commit messages: as instructed.
- **`report_view.page` is saved data**, not parsed from markdown, so the designed page cannot drift from the report; the markdown report is unchanged and still passes the seven report rules.
- **Chips are generated by code** from the cleaned data (the first category column, the main number, the first date column) and map to the existing closed operations; the model is never involved in them.
- **Typed text matches a chip only at 0.8 similarity or above**, so a vague question is never silently answered by a different one.
- **`CHATBOT=auto`** treats the sample as always on and real runs as on only with a model, so the demo works without a key and a keyless real run does not show a chat that cannot type.
- **The key figures drop "Columns"** and "Rows" is shown as "Records in your file", because they mean little to a seller.
- **The tour's tabs** are the real tabs that exist on the sample (Report, Dashboard, Insights, Ideas to try, Your data, What we cleaned, How we analysed); "How files connect" and "Power BI export" are left out because the sample has one file and the Power BI tab needs no picture.
- **Story window:** non-scrolling so it never takes the wheel from the page; the hero tour window scrolls.
- **`html, body { overflow-x: clip }`** replaces `hidden`, which silently broke `position: sticky`.
- **Chart colours:** accent for the highlight, `#CFC7B9` for everything else; the same two values in the server charts, the SVG charts and the chat charts.

## Commits on `studio-v3`

- `Task D1: chatbot chips, no-AI answers and history`
- `Tasks A-D: product start page, designed report page, one look, chat panel`
- `Task E: review fixes, screenshots, README and build log` (this log, README, screenshots and the fixes from the two review rounds)

## How to try it

```bash
cd backend && pip install -r requirements.txt
cd ../frontend && npm ci && npm run build
cd ../backend && python -m uvicorn app.main:app --port 8000
```

Open http://127.0.0.1:8000. Press **Try with sample data**, then the round **Lens** button and pick a suggested question (no API key needed).

```bash
python scripts/report_check.py sample_data/demo/sales_run     # the seven report rules
cd backend && pytest tests -q                                  # the full suite
node scripts/landing_screenshots.js docs/screenshots both     # retake the screenshots (server running)
```


## Lens, Stage 1 (the chatbot over the run's whole cleaned data)

Replaces the earlier five-operation "Ask about this report" (`ask.py`, `ChatPanel`). Principle kept: the AI chooses, code computes, every figure comes from code.

- **Where it lives:** `backend/app/lens/` (context, query, engine, render, chips, chat, store, rules) and `frontend/src/components/lens/` (context, chat, answer, dock, pins). Saved state is `lens.json` in the run folder, versioned, with room for later stages (summaries, ranking, view edits, memory). A new operation is one function in `engine.RUNNERS` plus one entry in `query.OP_HELP`.
- **Operations (closed list):** aggregate (sum, average, count, count distinct; filters, group by, split by), top or bottom N, share of total, compare two groups, change between two periods, trend over time (day to year), distribution, relationship between two measures. A derived ratio (for example a profit margin) is always a ratio of totals, never a sum or average of row ratios.
- **What the AI sees:** the understanding record (meanings, roles, display names, units), date spans, the tab name, the last 6 turns. No rows and no values; code finds the real value for what the user typed, and asks which column when a value is in two.
- **Cost:** at most 2 AI calls per question (the second only to repair), the existing rate-limit handling and AI cache, a per-run cap (`LENS_MAX_QUESTIONS`, 50), a ten-minute stop after a per-day provider limit. Suggested questions and chart-control buttons never call the AI.
- **Decisions worth knowing:**
  - Typed text runs a suggested question only when it is that question (same words in any order, or a typo away). A looser match ran "amount and quantity" for "amount and discount" in a test, so it was tightened.
  - The reply's `action` label is read by what it carries: the real model once wrote `"action":"top"`; the query inside was valid, so it is run (the checks decide, not the label).
  - The margin rule runs before any AI call: a margin or profit question with no profit or margin column and no cost column gets "Margin can't be computed because the data has no cost column." A cost column without a profit column is said plainly too (margin from a cost column is not built yet, because it is unclear whether the column is per unit or a total).
  - Charts that do not fit a result fall back to the sensible chart with a one-line note instead of an error.
  - Partial periods (the data ends mid-month, or a compared period is only partly covered) are said in the answer and in the calculation notes.
  - Pins are copies in `lens.json`; `result.json` and `report.md` are never written (a test hashes both).
- **Checks:** `backend/tests/test_lens.py` (fake AI: each operation against pandas, invalid queries, follow-up, clarifying question, chip without AI, margin rule, pins leave the report unchanged, history after reload, every shown figure equals the computed value, call limits, cap, rate limits, cache); `scripts/lens_kaveri_check.py` (real model, 8 Kaveri questions against pandas, output in `scripts/lens_kaveri_check_out.txt`); `scripts/lens_screenshots.js` (browser flow, desktop and phone).

**Results at the end of Stage 1**

- Full suite: 737 passed (`scripts/lens_full_suite_out.txt`; 44 of them are the new `test_lens.py`).
- Real-AI check, 8 Kaveri questions (2 follow-ups, 2 chart controls), every answer against pandas: 8 of 8 match (`scripts/lens_kaveri_check_out.txt`, one real AI call per question). The first run on `openai/gpt-oss-120b` scored 7 of 8: the model labelled its reply `"action":"top"` and Lens refused a valid query; that is fixed and tested. The second run on 120b scored 8 of 8, and then the key's 200,000-token daily limit was used up, so the saved file and the scorecard below were produced on `openai/gpt-oss-20b` (set with `LLM_MODEL`). Lens itself reported that limit plainly ("The AI has reached its daily limit...") and made no further calls.
- Kaveri scorecard on the same model: 12 of 12 (`scripts/scorecard_kaveri_out.txt`).
- Note: `llm.DEFAULTS["groq"]` (`llama-3.3-70b-versatile`) is not available to this Groq organisation (404); the `.env` `LLM_MODEL` is what makes Groq work here.
