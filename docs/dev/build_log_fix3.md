# Build log: studio-v3-fix3

Branch from `studio-v3`. Fake AI only, no real AI calls. Diagnosis used the saved run `74564241f2d1411a9895f843a3c29fb2` (`backend/data/runs/`), read only, not rerun.
`scripts/review_run.py` exists only on `studio-v3-review`, so its changes (steps 1 and 3) are on the separate branch **`studio-v3-review-fix3`** (made from `studio-v3-review`). Nothing from the review pack was merged here.

Tests run per file (never the whole suite in one process). **The full suite was not run**; leave it to a cloud session.

## 1. "AI mode: partly" with "AI partly unavailable in 0 stages"

**Cause (two separate things).**
- *Runner (the review file's label):* `ai_usage.json` has one failed call, `strategy.refine_with_llm`, reason `provider error`. The very next call for the same stage succeeded, so the app's retry worked and the stage used the AI. `ai_mode()` counted every failed call as "partly", while the "unavailable stages" list only counts decisions named "AI unavailable…" or "stage failed…". A recovered call is in neither, so: partly, 0 stages.
- *App (the run's own label, `AI-assisted (partly)`):* `stage_sources` had `insights = rules` (the AI call answered, but none of its rewording was used) and `report = mixed` (the AI summary lost some sentences to the figure check). Neither is a "fallback" decision, so the Report-tab pill "AI partly unavailable" could not name them. The only fallback it could name was `report_chart_caption`.

**Fix.**
- App (this branch): `reporter.ai_partly()` now lists the fallback stages as before, then every stage the run label counts as not-AI (`rules` or `mixed`) with its reason, e.g. `insights (the AI's wording was not used, the rule-based text was kept)`. Nothing is listed for a run with no AI at all ("Rules only"). The pill is unchanged in the front end, it joins the strings.
- Runner (`studio-v3-review-fix3`): a failed call answered on the retry is no longer "partly" (`recovered_failures`), stages with an unanswered failure are named in the "unavailable" list (`unrecovered_stages`), and the header says how many calls were answered on a retry.

Tests: `test_ai_json.py::test_a_partly_run_names_every_stage_that_did_not_use_the_ai_and_why`; `test_review_run.py::test_a_failed_call_answered_on_the_retry_does_not_make_the_run_partly`.

## 2. Revenue by item and Net Received by item not merged

**What the saved curation block shows.** The item findings are `f12` (net_received by item: leader Bridal Lehenga, last Printed Dupatta), `f17` (discount_amount by item: last Palazzo Pants) and `f19` (**revenue by item *within category = Ethnic***: 4 of the 12 items, leader Bridal Lehenga, last **Cotton Kurta**). There is no whole-table "revenue by item" finding at all, so there was nothing to merge `f12` with. `f12` got overlap 0.5 (the "same leader, different last place" partial rule) and no merge.
The whole-table revenue ranking does have the same ends as `f12` (checked in `cleaned.csv`: leader Bridal Lehenga, last Printed Dupatta, by mean and by sum), so the shape you saw on screen is real, but the finding that stands for it is the Ethnic-only slice, whose last place differs.

**Not changed:** the merge rule itself. It was right not to merge `f12` with `f19`.
**Real fault found while reading it:** `_relation` ignored the slice (`within`). A slice and the whole table are different populations; if a slice had happened to end on the same item it would have been folded into the whole-table card with no mention of the slice. Now findings with a different `within` never merge or count as the same; the "same leader" partial penalty still applies.

Test: `test_curate.py::test_a_slice_and_the_whole_table_are_never_merged` (same shape, and it fails without the fix).
**Open question for you:** if you want a whole-table "revenue by item" finding, the planner would have to ask for it, it is not a merge problem.

## 3. Dashboard chart count (27)

`review_run.py` counted every chart in `viewedit.catalogue` for the Dashboard tab (22 in the data) plus 5 it drew itself (trend, share, 3 ranked bars) = 27. The page draws the planned tiles from `dashplan.build_plan`: **5 tiles and 5 under "More charts"**. The plan contains **no median-based-test and no null-result tiles** (tiles: ranked bars for item, category, item_code, and findings f12 and f17, all group comparisons), so the Dashboard was **not changed**.
Runner fix (`studio-v3-review-fix3`): the Dashboard count is the planned tiles; the review file says "N planned tiles, M under More charts, K in the data" so both numbers are visible. On the saved run: 5 / 5 / 22.
Test: `test_review_run.py::test_the_dashboard_counts_its_planned_tiles_not_every_chart_in_the_data`.
(The plan has 5 under "More" here, you saw 6 in the browser; the browser also counts pinned or Lens-added items and the live trend. Not investigated further.)

## 4. Squashed names in chart titles

Found in `viewedit._plain`, which removed **every** underscore (meant for markdown emphasis), so `net_received` became `netreceived` in the item titles (Lens "Focus", the hidden-items list, view edits). It also never applied display names. Now only emphasis marks at the edge of a word go, and catalogue titles use the display names (`Net Received`).
Not confirmed in a browser: I found no other place that squashes names (the Dashboard tile title itself already uses `humanise`). If you still see it on the tile, tell me where.
Tests: `test_main_view_polish.py::test_item_titles_keep_column_names_whole_and_use_display_names`.

## 5. Second "swap day and month" question

The AI's "date order" concern becomes a swap question; the code's own date-order question asks "day first or month first" for the same column. Now `clarify.drop_swap_when_order_asked` removes a swap question for any column that already has a `set_date_order` question (and records the column in `concerns_out["settled_by_date_order"]`). A swap question for a column with no order question stays.
Test: `test_clarify.py::test_swap_question_is_not_asked_for_a_column_that_already_has_a_date_order_question`.

## 6. "Latest month vs previous" on an incomplete month

New pure helper `frontend/src/periodchange.ts` with the same rules as `backend/app/partial.py`: last month reached on under 80% of its days is incomplete; then it is compared **per day** with the previous month (that month's own length); under 7 days: no direction, the tile says "Too early" and "N of M days". A complete month is compared total against total as before. An average or ratio measure is compared as it is (an unfinished month does not shrink an average) but still shows the coverage. Day-level data is unchanged. The tile label says ", per day" when it is a per-day comparison.
Tests (run in node, as for the other front-end helpers): `ui/chartsize_check.mjs`, group "latest month vs previous" in `test_chartsize.py`: series ending on day 1 (no direction), day 10 (per day: +20% where total against total would read -60%), the last day (totals), plus 25 of 31 and 24 of 31 days, an average, and day-level data.
**Not tested in a browser.** Where the filters change the last date, the coverage follows the rows shown.

## 7. Raw column names on merged cards

Merged-card lines are written by `curate._item` with column names. Now `curate.curate` writes display names into them (Report, Insights and Dashboard all read them) and `dashplan.build_plan` does the same on read, so runs saved before this fix also show display names. The `measure` ids stay.
Tests: `test_dashplan.py` (two tests) including a result saved with column names.

## 8. Item and Item Code both as filters and charts

`dashboard.drop_twin_dims`: of two columns that group the rows identically (every value of one goes with exactly one value of the other, both ways), keep one: the one the goal names, else the one whose **values** read as words (no digits) and are longer. Decided from values, not column names. Only columns with the same number of values are compared. On the saved run `item_code` is dropped (dims now `category`, `item`; filters `item`, `category`).
Tests: `test_dashboard.py` (two tests: a name/code pair with the code first, and the goal's column kept, plus an unrelated pair untouched).
Finding charts built on the dropped column (none in this run) are not removed by this step.

## Tests run (per file, fake AI)

`test_ai_json` 11, `test_curate` 35, `test_dashplan` 22, `test_dashboard` 4, `test_chartsize` 11 (node 22), `test_clarify` + `test_ai_concerns` 122, `test_main_view_polish` + `test_partb_view` 27, `test_nav` + `test_old_tab_names` + `test_simplify` + `test_agent_briefs` + `test_lens_misses` 53, `test_review_run` 11 (on the review branch). All passed. `npx tsc -b` clean.
Not run: the whole suite, `test_ui_simplify.py` (real browser), anything with a real model.
