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
