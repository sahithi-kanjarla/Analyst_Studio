# Build log: studio-v3-curate

Branch from `studio-v3`.  Goal: stop showing everything — each tab should show
what matters, with counts from the data, never fixed numbers.

## Part B1 of 3: the selection engine (no UI changes yet)

### Step 1 — candidate list, AI ranking, importance scoring, the cut, tests

**New file `backend/app/curate.py`** — the selection engine.

1. **Candidate list** (`build_candidates`): after analysis, builds one entry per
   finding, chart, idea (strategy), key figure and dashboard filter.  Each entry
   carries id, plain title, kind, measure, dimension, effect size, share of
   total, reliability, whether it relates to the stated goal, whether it is a
   null result.  Robustness checks (`robust_compare`) and table tools
   (`group_table`, `summary_table`) are excluded as standalone candidates (they
   fold into the parent finding).  Null-result findings never produce a chart
   candidate.

2. **AI ranking** (`ai_rank`): one AI call sees the ids and titles and returns
   an ordered list with one-line reasons.  Code validates: unknown ids ignored,
   duplicates removed.  If the AI is unavailable or answers badly, the engine
   falls back to rules-only ordering from the same importance score, and the
   stage is recorded in the `ai_notes` / decisions list (the "AI partly
   unavailable" pill).

3. **Importance score** (`importance`): computed by code from goal relevance
   (+2), effect size (0–1), share of total (×2), reliability (0–1) and a small
   decaying bonus for AI-ranked position.  Null results get a −1.5 penalty.
   Data cautions get a +5 bonus (always kept).

4. **The cut** (`apply_cut`): keep items while they are at least
   `SCORE_FLOOR_FRAC` (0.35) of the top score **and** while the kept items
   carry under `MASS_TARGET` (0.85) of the total importance — whichever is
   stricter.  A loose safety ceiling that scales with the number of candidates
   (`max(12, ceil(n × 0.55))`) and rarely binds.  Applied separately per
   (tab, kind) group.  Always kept: goal-answering, data cautions, pinned items.

5. **Chart dedup**: never two charts with the same (chart_type, measure,
   dimension).

6. **Null-result grouping** (`group_nulls`): "no reliable difference" findings
   become short one-line entries only for the goal and the main measure; the
   rest are counted.

7. **Robustness attachment** (`attach_robust`): maps each robustness finding to
   its parent, so the parent card can show the check inline.

8. **Output** saved in `result.json` under `"curation"`:
   `{candidates, ai_ranking, ai_reason, tabs: {tab:kind: {kept, more,
   left_out_count, null_lines, null_counted}}, robust_map, kept_ids, more_ids,
   left_out_count}`.

**Graph change** (`backend/app/graph.py`): a new `curate` node runs between
`report` and `powerbi`.  Wrapped with `@isolated` so a failure falls back to
rules and never kills the run.

**Runner change** (`backend/app/runner.py`): `"curation"` added to the keys
saved in `result.json`.

**Tests** (`backend/tests/test_curate.py`, 11 tests):
- One dominant finding keeps fewer items than many equally strong ones.
- A 60-column synthetic file stays under the ceiling.
- An AI answer with unknown or repeated ids is cleaned.
- AI down gives the rules order and records the reason.
- A goal-answering finding always survives.
- Null results never get their own chart.
- The same (type, measure, dimension) is never two charts.
- The same state gives the same selection twice (deterministic).
- Robustness findings map to their parent.
- Goal-relevant items score higher than non-goal items.
- Data cautions score very high.

**All constants in one place** at the top of `curate.py`:
`SCORE_FLOOR_FRAC`, `MASS_TARGET`, `CEILING_BASE`, `CEILING_SCALE`,
`MIN_KEEP`, `AI_TOP_BONUS`, `GOAL_BONUS`, `CAUTION_BONUS`, `NULL_PENALTY`,
`EFFECT_PTS`, `RELIABILITY_PTS`.

Existing tests still pass: `test_pipeline` (6), `test_report_tiers` (8),
`test_token_budget` (5), `test_report_consistency` (10), `test_dashboard` (2).

## Part B2 of 3: tabs use the saved curation selection

### Step 2 — backend apply_curation, frontend tabs, tests

**Backend: `tiering.apply_curation(view, curation)`** — called in `main.py`
when serving the API response.  Mutates the view in place:

- **Insights tab**: re-partitions `evidence` (kept findings only) and adds
  `more_findings` (overflow with real information).  Null-result cards are
  removed from both lists; their one-line summaries go into `null_lines`.
  `null_counted` tracks how many null results were grouped away.
- **Report tab**: `report_findings` holds the most important findings for the
  shareable summary.  A floor of `MIN_REPORT_FINDINGS = 3` fills from the
  overall pool when curation keeps fewer.
- **Ideas tab**: `strategies.strategies` holds kept ideas,
  `strategies.more` holds overflow, `strategies.dropped` is the left-out count.
- **Data tab**: `left_out_count` (the aggregate from curate.py) tells the user
  how many minor findings were excluded and why.
- When no curation exists (old runs), the view is unchanged.

`main.py` change (lines ~162–174): applies curation after building chart_urls,
then patches chart_url on the new `report_findings` and `more_findings` lists.
Uses `.get()` for evidence/appendix to handle the empty-list case safely.

**Frontend changes**:

- `api.ts`: `NullLine` interface.  `ReportView` gains `report_findings`,
  `more_findings`, `null_lines`, `null_counted`, `left_out_count`, and
  `strategies.more`.
- `Tiers.tsx` (Insights tab): detects curated mode via `view.more_findings`.
  Evidence section renders pre-filtered cards.  A new `MoreFindingsList`
  component shows a collapsed `<details>` block — each item has title + one
  line of text, and the chart draws only when opened (`useState(opened)` on
  `onToggle`).  Null lines rendered as "Checked, no reliable difference"
  one-liners.  `LegacyAppendix` preserves the old rendering for runs without
  curation.
- `StrategyView.tsx`: accepts `more` prop; renders a collapsed "More (N ideas
  that ranked lower)" details block with title + one-line objective.
- `ReportPage.tsx`: the `report:evidence` section shows curated
  `report_findings` (each with chart image, headline, observation text) when
  available, falls back to `page.charts` for old runs.  New `report:ideas`
  section shows the top 4 strategies with a "and N more on the Ideas tab" link.
  New `ReportFinding` component for individual finding cards.
- `Results.tsx`: Data tab shows a muted note when `left_out_count > 0`:
  "N minor findings were left out … they stay in the full results."
  Passes `strategies.more` to `StrategyView`.

**Tests** (8 new in `test_curate.py`, 19 total):
- `test_apply_curation_partitions_evidence_and_more`: kept and more are
  disjoint; every id accounted for.
- `test_report_findings_has_a_floor`: at least `MIN_REPORT_FINDINGS` cards.
- `test_report_never_empty_on_small_file`: one finding is enough.
- `test_null_result_has_no_chart_in_display`: null results are never full cards
  in evidence or report_findings.
- `test_left_out_count_in_view`: view carries an integer `left_out_count`.
- `test_more_findings_reachable`: 19 findings — all non-left-out are in
  evidence, more_findings or appendix.
- `test_strategies_more_list`: kept and more strategies are disjoint.
- `test_apply_curation_empty_is_noop`: empty curation leaves view unchanged.

**No old tests changed.** The existing tests call `tiering.build_view()`
directly, which is unchanged.  `apply_curation` is a separate post-processing
step applied in `main.py`, so existing assertions on the raw view still hold.

**Test results**: 19 passed (test_curate), 25 passed (test_report_tiers +
test_report_page_stage1), 11 passed (test_report_consistency), 13 passed
(test_main_view).  TypeScript compiles clean.

## Part B4: stricter goal match, merging overlap, overview mode

### Step 1 — `backend/app/curate.py`, `tiering.py`, front end, tests

**What the old code did with an empty goal.** `main.py` / `runner.create_run` replace an empty goal
with a placeholder sentence and set `explore=True`. `curate` ignored that flag, so the placeholder
words were matched against column names like a real goal (a column called, say, "data" would have been
"goal-relevant"), and nothing told the reader it was an overview.

**1. Goal match is a boost only.** `goal_relevant` now only adds `GOAL_BONUS`. The always-kept items are
`direct` (the finding whose measure *and* dimension the goal names; if the goal names a measure but
no usable dimension, the single best finding on that measure; if it names no measure, the planner's
target is the measure), data cautions and pinned items. A chart carries its finding's flags.

**2. Overlap** (`apply_overlap`, runs after scoring, before the cut), walking the ranked findings:
- same measure + dimension, other tool (e.g. comparison and share chart) → merged into one;
- same dimension, same leader **and same last place**, other measure → merged; the card lists each
  measure's figure (`merged.items`, text built by code from the original stats, original finding ids kept in `members`);
- same dimension and leader but a different last place → half overlap (score x 0.6);
- same measure, another dimension whose group values are identical (a one-to-one mapping) → full overlap (score x 0.2), no merge;
- charts: one per measure + dimension. Merged originals are shown only inside the merged card (not in More or the left-out count);
  `curation.merges` and `curation.absorbed` record them.
- A direct/overview item is never folded into another *measure's* finding (it gets the partial reduction instead).
  The "top share within tolerance" idea was dropped for comparisons: matching the same last place is stricter and needs no tolerance.

**3. Views.** `tiering.apply_curation` sets `card["merged"]` and extends `card["members"]` on the surviving card, so Report,
Insights and More read it without change. Front end: `ReportFinding` (Report) and `ReportCard` (Insights) list the merged figures;
types in `api.ts`. No colours or layout changed. **The Dashboard does not read the curation result in the current code**
(only `tiering.apply_curation` does), so there was nothing to change there; part B3 will wire it.
Also fixed: the Report floor now also applies when curation kept no Report finding at all.

**4. Overview mode** (`goal_mode`): empty goal, explore flag, or a goal that names no column and is a vague phrase or <= 3 words.
Curation gets `mode`, `mode_note`, `main_measure`; the Report shows the note under the title.
Main measure (`main_measure`, always checked to be a numeric profile column that was analysed): with a usable understanding
record, its measures by kind money, count, quantity, rate, cost, then best filled; without one, additive kind (brief kinds, then
the shared `measures.measure_kind`), then the planner's target, then best filled, then name (a deterministic tiebreak only).
Always kept: top contributors (comparison) on the main dimension, the trend, concentration (share). Others compete by score plus a
coverage term (`COVERAGE_PTS`) and the overlap rules. Goal bonus is off in this mode.
Note: the understanding record has no explicit "main measure" field, so I use its measure kinds; a prompt change is not made.

**Tests**: 12 new in `test_curate.py` (31 total): merge of three measures with same leader (figures equal originals), different leader not
merged, one-to-one dimension, weak goal-related finding not kept, direct answer survives, goal with no dimension, determinism,
merged card + Report floor, empty/vague goal -> overview, main measure from record and by rules, overview keeps core findings and Report not empty.
**No old tests changed** (the old "goal finding survives" test still passes because that finding is the direct answer).
Run: test_curate, test_report_tiers, test_report_page_stage1, test_main_view, test_report_consistency (80 passed), test_pipeline + test_token_budget (12 passed). `tsc --noEmit` clean.
Not tested: a real-AI run, the browser rendering of the merged lists, the full suite (leave to a cloud session).

## B4 follow-up: the explore flag is the only trigger for overview mode

The goal cannot be empty in the app: the person types a goal or ticks "I'm not sure what to look for…", which sets the explore flag.

- `curate.goal_mode` now returns overview **only** when the explore flag is set (state, plan or context). Removed: the "goal names no column and is
  vague / three words or fewer" rule, the `VAGUE_WORDS` / `VAGUE_MAX_WORDS` constants and `NOTE_VAGUE_GOAL`. Anything typed ("best seller?",
  "sales", "I do not know") is a real goal and goes through the normal goal matching. The rest of overview mode is unchanged (note on the Report,
  main measure chosen by code, always-kept overview findings).
- **Placeholder check.** `create_run` still stores the placeholder sentence in `input.json` (so old runs and reruns read the same), but it is now
  `DEFAULT_EXPLORE_GOAL` in `context.py` with `typed_goal()`, which turns it back into `""`. The graph is started with `typed_goal(goal)`, so no stage
  can match the placeholder's words ("insights", "business", "data"…) against column names; `status.json` and the run list/status API return `""`
  too, so the History list and the Dashboard fall back to "Exploratory analysis" / the objective instead of printing it. The Report already printed
  "Mode: exploratory. No specific goal was given" from the plan's explore flag. A typed goal with the explore box also ticked is kept as typed.
- Small guard in `_candidates_from_metrics`: a goal with no word of 3+ letters ("a b") used to build an empty regex that matched every label.
- Prompts to the AI now get an empty GOAL line for an explore run instead of the placeholder (the explore flag is still passed).

**Tests changed**: `test_curate.py::test_empty_and_vague_goal_give_overview_mode` was **rewritten** as `test_explore_flag_is_the_only_trigger_for_overview_mode`
(its `("", False)`, `"I do not know"` and `"anything interesting"` cases expected overview from the text; they now expect goal mode). **Added** in
`test_curate.py`: `test_typed_goals_of_any_length_are_real_goals`, `test_short_typed_goal_goes_through_goal_matching`,
`test_placeholder_goal_is_not_a_typed_goal`. **New file** `test_explore_placeholder.py`: an end-to-end fake-AI run with the explore tick (goal stored
empty, mode overview, placeholder absent from report and API) and one with the typed goal "best seller?" (kept, goal mode). Other tests that pass
`_plain_state("", True, …)` are unchanged (explore flag on).
Run: test_curate + test_explore_placeholder (36 passed); test_main_view, test_main_view_polish, test_report_page_stage1, test_report_consistency,
test_run_safety, test_partb_view (79 passed). Not tested: a real-AI run, the browser, the full suite (leave to a cloud session).


## Part B3 of 3: the Dashboard reads the selection

### Step 1 - dashboard plan, window-filling layout, frontend
**New `backend/app/dashplan.py`** (no column name or example value in it). `build_plan(dash, result)` is added to `/dashboard` as `plan`:
- **Tiles**: the time view, one interactive chart per category column, and the chart of each kept finding (`curation.tabs["dashboard:chart"]`). They go through the
  same `curate.apply_cut` as the other tabs. What the cut drops, and what one screen cannot hold (`MAX_TILES` = grid area / `MIN_TILE_AREA` = 12), is the **More** list.
  A merged finding is one tile and carries its measure lines (`items`). A finding chart that repeats a live chart (same measure + dimension ranked bars, or the same time view)
  is never a second tile; its merged measure lines move onto the live tile. The one-column table "in full" moved into More.
- **Importance** of a live tile: the importance of the finding it repeats, else top importance x the column's share of variance explained; goal columns are always kept; the time view is always kept in overview mode.
- **Filters**: the category columns tied to the kept tiles first, at most `MAX_FILTERS` (3; the page already capped at 3). Date controls (year, quarter) are the existing ones. **Filters did exist, so nothing new was built.**
- **Layout** (`layout`, `arrange`): a slicing of a 24 x 24 grid. Weight = 0.5 + importance / top importance, so the top tile is bigger; every cell belongs to exactly one tile, so there is no gap, overlap or filler,
  and with few tiles each tile is bigger. Cuts leave each half room for its tiles (`MIN_TILE_AREA`).
- **Objective line** (`objective_line`): explore run -> "Overview of your data"; otherwise the plan's objective or the typed goal; never blank, never the placeholder.
- `GET /api/runs/{id}/dashboard/layout?importance=9,5,2` returns the rectangles for any list of tiles (used when a view edit hides or folds some, so the grid still fills the window).

**Front end**: `BIDashboard.tsx` draws the plan: key figures row, filters in the header row, then the tiles in a CSS grid whose height is the room left in the window
(`.bi-fit`, widgets.css; layout only, no colours). Each live tile measures its body and draws to fit (bars that fit, donut ring size, column height). Below: "Answer to your goal" and a collapsed
"More charts" (drawn only when opened). On a phone width (< 760px) the tiles stack. `FindingGallery.tsx` no longer lists every finding. No colours or click/highlight behaviour changed.
Old runs without curation: the plan falls back to the gallery's key order (the sample run was checked in a browser at 1440x900: 3 tiles filled the grid exactly, no gap).

### Step 2 - merged cards, objective line
Checked with a three-measure run (same leader on revenue, units, profit): Report and Insights show one card with one line per measure, and the chart URL answers 200. On the Dashboard the merged chart was the
live region chart, so its measure lines were being lost; they now show on that tile. `curate._item`: a merged line with no single figure to quote now reads as the finding's headline, not a bare column name.

### Step 3 - every other place that shows findings or charts
| place | follows the selection? | what was done |
|---|---|---|
| Report, Insights, Ideas tabs | yes (B2/B4) | unchanged |
| Dashboard | **now yes** | step 1 |
| Print / PDF report (`lens/exporter.py` `report_html`) | **was no** (used the report page's fixed first 6 charts) | now uses the same cards the Report tab shows (`curated_cards`: stored view + `tiering.apply_curation`), a merged group once with its measure lines; older runs keep the old charts |
| Power BI starter pack (`powerbi.build_visuals`) | **was no** (one visual per finding, nulls included) | now the findings the Dashboard keeps, merged once; `POWERBI_GUIDE.md` says how many lower-ranked findings are not listed; older runs list everything as before |
| Plain-text report export | has no findings list (headline, actions, trust notes) | nothing to change |
| `report.md` (the written full report, "Report" download) | **no** | It is written in the report stage, which runs *before* the curate stage, and it is the full written record (every finding, checks, cleaning). Not changed: following the selection would mean moving the curate stage before the report stage and cutting the full record. **Your call.** |
| Lens agent facts (`report_facts`, `rank`, `data_query`...) | not limited by it, on purpose | they search the whole fact sheet / data, so a More or left-out finding can still be asked about |
| Lens briefs | built from the report's headline, key figures and chosen facts, not from the findings list | nothing to change |
| Older runs without `curation` | yes, fallback | Dashboard uses the gallery's key order; PDF and Power BI use the old lists; tabs unchanged (B2) |

**Lens can show a More or left-out item.** The view command `show` on a Dashboard chart that is not hidden now **pins** it (the pin means "kept, never folded"), and the Dashboard plan (`dashplan.build_plan(..., pinned)`)
always draws pinned charts, even one the selection left out entirely; the grid is re-cut for them. `undo` removes it again. The Dashboard re-reads its plan when the pins change.
Test: `test_dashplan.py::test_lens_can_show_a_more_item_on_request_and_it_joins_the_dashboard` (through the same `/view` route Lens commands use) and `test_a_chart_the_selection_left_out_entirely_can_still_be_asked_for`.
Not tested: Lens choosing that command from a typed sentence with a real model (fake AI only).

### Step 4 - tests (all in `backend/tests/test_dashplan.py`, plus one in `test_explore_placeholder.py`)
Tile sizes leave no gap or overlap for 1, 2, 3, 6, 10 and 12 tiles; the top tile is the largest; 3 tiles are each bigger than 10; a repeated chart is never a second tile; with one important item the plan has
1-3 tiles and no filler; merged cards are one tile with their measures (also on the live tile); filters are few; explore objective is "Overview of your data" (unit and end-to-end); More item shown on request;
PDF and Power BI follow the selection. Old tests changed in B3: **none**.

### Step 5 - the whole suite, in chunks (one pytest process at a time, this laptop, fake AI)
| group | files | passed | skipped | failed | time |
|---|---|---|---|---|---|
| 1 | 10 | 174 | 0 | 0 | 1:33 |
| 2 | 10 | 225 | 1 | 0 | 2:28 |
| 3 | 10 | 95 | 0 | 0 | 1:47 |
| 4 | 10 | 69 | 0 | 0 | 0:39 |
| 5 | 10 | 165 | 0 | 0 | 1:33 |
| 6 | 10 | 88 | 0 | **1** | 2:37 |
| 7 | 10 | 109 | 0 | 0 | 0:50 |
| 8 | 10 | 135 | 0 | 0 | 0:17 |
| 9 | 10 | 208 | 0 | 0 | 1:20 |
| 10 | 7 | 110 | 0 | 0 | 8:11 |
| 11 (browser: test_ui_nav, test_ui_simplify, test_ui_widgets) | 3 | 25 | 0 | 0 | 3:02 |
| **total** | 100 | **1403** | **1** | **1** | about 25:20 |

- The one failure is `test_owner.py::test_the_server_secret_comes_from_the_environment_or_is_generated_and_kept`: it asserts the `owner_secret` file mode is 0o600, and Windows reports 0o666 (no POSIX modes). Not caused by this branch (confirmed by re-running the file alone: same failure); it should pass on Linux/Docker. Not changed.
- The one skip is `test_constraints.py:35`: the pip dry-run needs the network (`RUN_PIP_RESOLVE=1`).
- The browser tests did **not** skip: `frontend/dist` was built and Chromium was found. They run against the built page, which does include the B3 Dashboard.
- Not run: any real-AI call, a browser check of a *curated* Dashboard (the browser check I did was on the sample run, which has no curation, so it used the older-run fallback), the page at phone width, hiding a tile with a view edit in the browser (the layout endpoint is tested, the browser path is not), `python scripts/check_constraints.py`, the Docker image.
- Old tests changed: **B2: none. B3: none. B4: `test_curate.py::test_empty_and_vague_goal_give_overview_mode` was rewritten as `test_explore_flag_is_the_only_trigger_for_overview_mode`** (its text-based overview cases now expect goal mode); 3 tests were added in `test_curate.py` and a new file `test_explore_placeholder.py`.
