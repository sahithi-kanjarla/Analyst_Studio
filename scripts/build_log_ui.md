# UI clarity pass — build log

Branch `studio-v3-ui`, off `studio-v3-deploy` at `4f90163`. Frontend and display text only: no calculation, figure, ranking or
verified fact is changed. No real AI calls were made: every run below is rules-only (`LLM_PROVIDER=none`).

## How the audit was run

One server at a time, `python -m app` with `LLM_PROVIDER=none` on port 8010, against four datasets:

| name | file(s) | shape |
|---|---|---|
| `sample` | the shipped sample run `5a1e5d3e0001` | small, read-only |
| `shopify` | `backend/tests/fixtures/varied/a_shopify_orders.csv` | long-format orders, 640 rows, 51 columns, 2 lines per order |
| `mall` | `sample_data/mall/sales.csv` | 40,000 rows, many categories (128 SKUs, 13 brands) |
| `kaveri` | `sample_data/kaveri_test/kaveri_test/{customers,orders,order_lines}.csv` | three linked tables, 6,412 line rows |

All four fixtures the brief named were present, so nothing had to be substituted. The many-category file is the mall file
(128 distinct SKUs, the most of any fixture).

Screenshots and a DOM audit come from `scripts/ui_shots.js`, which drives the same headless Chromium the UI tests use
(global `playwright`, reached through `NODE_PATH`). 96 "before" screenshots: 4 runs x 8 views x 1440 / 1024 / 390 px, in
`docs/screens/ui_pass/before/`. Playwright was not installed on this machine; installing it is what made the screenshots
possible, and it is also what the existing `test_ui_nav.py` / `test_ui_simplify.py` need in order not to skip.

## What the audit found

Counts are at 1440 px. "closed" means a `<details>` the reader must click.

| tab | run | charts visible | charts hidden | folds | all closed? | visible text |
|---|---|---|---|---|---|---|
| Report | sample | 1 | 3 | 14 | yes | 1,157 chars |
| Report | **shopify** | **0** | **21** | 33 | yes | 911 chars |
| Report | mall | 3 | 20 | 25 | yes | 1,263 chars |
| Report | kaveri | 2 | 16 | 21 | yes | 1,503 chars |
| Insights | sample | 3 | 0 | 2 | yes | 1,416 chars |
| Insights | shopify | 5 | 13 | 2 | yes | 1,542 chars |
| Insights | mall | 5 | 12 | 2 | yes | 1,914 chars |
| Insights | kaveri | 5 | 9 | 2 | yes | 2,320 chars |
| Ideas to try | all four | **0** | 0 | 0 | — | 2,577 / 4,299 / 4,818 / **5,283** chars |
| Dashboard | sample / shopify / kaveri | 6 / 11 / 10 | 0 | 0 | — | fine |
| Dashboard | mall | renders, but slowly | — | — | — | see below |
| Your data | shopify | 0 | 0 | 1 | yes | **13,911 chars** |
| How files connect | single-table runs | 0 | 0 | 0 | — | **142 chars** |

### Report — the worst tab

1. **Every single fold on the page is closed, and every fold is inside "Behind the numbers".** 14 of them for the sample,
   33 for the Shopify file. The evidence findings and their charts are in there: `Evidence (8 findings) [8 charts]` is one
   arrow. For the Shopify file **not one chart is visible on the Report tab without a click**.
2. **Several folds have no name at all** — an empty `<summary>` wrapping a table. The reader sees a bare arrow.
3. "Behind the numbers" is a vague name that does not say what is inside.
4. `ReportPage.tsx` renders the "Behind the numbers" section **twice**: once as the `report:behind` node inside `Sections`
   (lines 127-133) and again unconditionally (lines 139-143), both with `id="rp-behind"`. Only one shows today because the
   short view folds the first, so this is a latent duplicate — two identical sections and a duplicate DOM id as soon as the
   tab is switched to the full view.
5. Chart titles carry raw column names: `Amount by sub_category`, `Discount_pct by sub_category`.
6. Only 3 or 4 stat tiles, and the headline is a plain paragraph: the page opens with prose rather than figures.

### Insights

1. The short view folds 9 to 24 items; `Appendix: N more findings` holds 9 to 13 charts behind one arrow.
2. A second, **nameless** closed fold sits on every run.
3. In the short view `ReportCard` is rendered `compact`, which drops the explanation entirely — the chart appears with no
   sentence, which is the opposite of what a first-time reader needs.
4. `ReportCard` folds any per-group table over 8 rows into `Per-group figures: …`, so the table is behind a click.
5. Headlines contain internal names: `sub_category = Headphones averages 8.6x the amount of Lamps`.

### Ideas to try — no widgets at all

1. **Zero charts and up to 5,283 characters of running text.** The tab is a wall of prose: `objective`, `Based on: …`,
   `Actions`, `Test it first`, `KPIs to track`, `Risks` all as plain lists.
2. The `Based on:` evidence line runs to 546 characters and repeats a whole insight headline inside a sentence.
3. The impact/effort matrix shows only rank *numbers* in cells, so it carries no meaning until you look elsewhere.
4. Nothing is collapsed here, which is right, but nothing is a widget either: no stat, no badge hierarchy, no first step
   singled out.

### Dashboard — the strongest tab, with two faults

1. **Titles are built from raw column names**: `amount by sub_category`, `sub_category breakdown`,
   `discount_pct by Segment: per-group figures`. `BIDashboard.tsx` interpolates `d.col` directly.
2. **An "Other" bar is pooled even for non-additive measures.** `dimItems` builds `Other (k groups)` from a weighted mean
   when `agg !== "sum"`, which invites reading an average of averages as a real group.
3. No "Show all (n)" on the bar charts — only the breakdown table has one. The bars silently stop at the top 8.
4. The mall file (40,000 rows) sends a **2.4 MB** dashboard payload and does every aggregation in the browser. It renders,
   but for about a second the tab shows only the bare words `Building dashboard…` with no skeleton and then the layout
   jumps. `MAX_ROWS` is 50,000, so a 40,000-row file is never sampled.
5. The breakdown table shows 10 rows before "Show all", where 5 to 8 reads better.

### Data and its sub-views

1. **Your data** for the Shopify file is 13,911 characters: a 51-row column table with every row visible at once, and no
   way to see less. The one fold on the page (`What cleaning changed: 30 of 51 columns`) is closed.
2. **How files connect** is 142 characters for any single-table run: one bare sentence, no heading, no visual — an
   unfinished empty state on three of the four fixtures.
3. The `kpi-strip` repeats the same key figures on Insights, Ideas to try and all four Data sub-views, where they are not
   the subject of the page.

### Across every tab

1. No horizontal scrolling at 390 px anywhere — that part is already sound.
2. No page errors in the console on any tab of any run.
3. Chart titles and subtitles are the single biggest wording problem: raw ids, underscores, and `Total X by Y` phrasing.
4. `Expand all details` is present on every tab, which is correct and is kept.

## What changed

See the sections below, written as each tab was done.

---

## Progress (kept up to date after every step)

### Step 1 — build and tests: DONE
- Merged `origin/studio-v3-deploy` into this branch (Windows import fix and NaN fix) and pushed.
- `npm run build` (tsc -b + vite) is clean; the only note is a pre-existing minify warning for `color-mix()` in `landing.css`.
- `test_chartsize.py` (9) and `chartsize_check.mjs` pass.
- Tests that assumed `DEFAULT_SHORT = ("report","insights","dashboard")` now assume every tab opens in the full view: `test_simplify.py`,
  `test_partb_view.py`, `test_lens_misses.py`, `tests/ui/simplify_check.js` (one "Show less" link per tab instead of "Show all (n)"),
  `tests/ui/nav_check.js` (the column table heading is now "What each column holds").
  Short view, hide/show/undo, "Show all (n)" and Lens view edits still work and are still covered.

### Left to do
- Step 2 review of Report and Insights, display.ts, chartsize.ts, widgets.css against the rules.
- Step 3: Ideas to try, Dashboard, Data sub-views, Insights as widgets.
- Step 4: designer review of every tab, print/PDF parity, light and dark.
- New browser tests (per tab; no underscores or raw ids in titles) and the final full run.

### Step 2 — review of Report and Insights: DONE
- Report chart headings lose "Total", "Sum of" and "(sum)"; finding tables sort by header click (display order only) and keep the first 6 rows with "Show all (n)".
- Findings' charts on Report and Insights are server-drawn images: their inner titles and the top-N inside them cannot be changed from the frontend (noted for later).

### Step 3a — Ideas to try: DONE
- Each idea card shows title, why, the first evidence line, ALL first steps, "Test it first" and the KPIs as chips. Only the full evidence list and risks are in one fold "How we got this".

### Step 3b — Dashboard, Data: DONE (checked, small fixes)
- Dashboard already had stat tiles first, tidy grid, skeleton while loading, ranked bars with "Show all (n)", subtitles (done in the earlier partial pass). No collapsed charts.
- Data > Your data: first 10 of the column table with "Show all (n)"; the cleaning-changes fold is now named "How we got this". Backticks no longer leak into sentences.

### Step 3c — Insights as widgets: DONE
- New `frontend/src/insightwidget.ts` (pure helpers, unit-tested in `chartsize_check.mjs`) and `components/InsightWidget.tsx`. The widget is chosen from the finding's analysis type, its table and its sentence — never from a column name:
  ranking -> compact list with mini bars (top 6, "Show all (n)", same order as the table) plus highest/lowest side-by-side cards when the table lists every group;
  composition -> share bar (top 5 + Other); everything else (trend, link, spread) -> the finding's own chart image.
  Headline stat tile (a multiple, percentage or correlation taken from the sentence as written; up/down arrow and colour only where the sentence or its sign supports a direction),
  badges (type, confidence, your KPI), one-to-two-sentence takeaway, a "What to do" chip when a report action rests on the finding, and one fold "How we got this" (observation, chart image for lists, full sortable table, checks).
- `Tiers.tsx`: findings grouped under theme headings (ordered by the most important finding in each theme), 2-column grid on desktop, one column on a phone. Lower-ranked findings are visible too (first 4, "Show all (n)"); the column profile is the one fold.
- FindingGallery tiles on the Dashboard and `FindingTable` use plain names (no raw ids), 6 rows then "Show all (n)". Stat labels lose a leading "Total".
- New browser test `tests/test_ui_widgets.py` + `tests/ui/widgets_check.js`: per tab, chart + explanation + action visible without a click; Show all reveals the rest; Expand all details opens folds; no underscore / raw id / "Total" / "(sum)" in titles; print = screen.
