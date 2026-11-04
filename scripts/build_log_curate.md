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
