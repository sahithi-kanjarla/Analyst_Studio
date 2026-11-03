# Fix round 2 (branch studio-v3-fix2, from studio-v3)

Findings of review runs on a clean bakery file (900 rows, one row per order). Rules: fake AI only, nothing hard-coded to a column name or example value, a figure changes only where a fix says so.

## Status

| Fix | State |
|---|---|
| A. Order key detection | done |
| B. Key figures (no sum of a rate; gross profit and margin by code) | done |
| C. Partial periods | done |
| D. Derived columns, one-to-one categories | done |
| E. Display names | done |
| F. AI-proposed derived measures checked in code | left to do |
| G. Short titles | left to do |

## Fix A: order key (done)

- `understanding.py`: the record now carries `order_key` (the column the AI names as the order, invoice or transaction identifier, or "none"). A name that is not a column is no answer.
- `grain.py` rewritten around one decision (`decide`): the user's answer, then the AI's record, then (only with no AI answer) a column named like an order key. Values are never used to find a key (`structural_key` and the repeat-based `key_columns` are gone). The data only verifies (`verify`): unique per row = already order grain (nothing blanked, 900 orders on the bakery); repeated = it must behave like an order (a date or amount stays the same within it while other columns change, and when the file has dates one of them stays the same within an order).
- An amount that is constant within a product or category column (a product's price or cost) is an item attribute and is never blanked (`is_item_attribute`).
- Unsure (no AI, no answer) or disagreement (the AI's pick fails the data check): every value is kept and the question "Which column identifies one order?" is asked, candidates as options, default "None of these: keep every value" (`signals.order_key_signal`, `clarify` template, new directive `set_order_key`). The answer is read when the run restarts (`graph._order_key`).
- The profile no longer guesses a grain at profile time; `profile["grain"]` (the Orders tile) and `profile["grain_decision"]` are set in the understanding stage.
- Tests: `tests/test_order_key.py` (7). Older tests that used the rules-only repeat detection were rewritten to name the key (a fake record, an answer, or an explicit key). Older test modules do not see the new question (`conftest.no_order_key_question`), because a rules-only run on any file with an order_id column now pauses on it.

## Fix A follow-up: no question when the key is unique per row (done)

- `grain.decide`: with no AI answer, a name-matched candidate that `verify` finds unique per row now gives verdict `order_grain` (source "name"), so no question is asked and nothing is blanked. The question is still asked when the candidates repeat, or when the AI and the data disagree.
- Tests (`tests/test_order_key.py`): unique key asks nothing (bakery), repeated key still asks (handicrafts), pipeline on the bakery does not pause on the question. 8 passed.

## Fix B: key figures (done)

- New `derived_cols.py`: finds columns that are exact products or sums of two others from the values (tolerance = a rounding error). Also reused by D and F.
- A rate is a column the brief or record labels a rate, or one the data shows as the per-unit factor of a product whose other factor is a count (`per_unit_factors`). `summary.rate_columns` collects them; `kpi_measures` and `insights.key_metrics` no longer sum them (an average is still shown).
- `summary.profit_figures`: when a revenue-like and a cost-like additive column exist (record labels first, name words only as a fallback, `measures.COST_WORDS` / `REVENUE_WORDS`), shows Gross profit = revenue - cost and Gross margin = profit / revenue, on the rows that have both. Added to `key_metrics` and `tiering.key_numbers`.
- A ratio of revenue over cost is dropped from the key figures when gross margin is shown.
- Tests: `tests/test_key_figures.py` (5). Related files re-run: ratio_measures, summary_answers, report_tiers, main_view, context_insights, report_consistency, scorecard_gaps: 77 passed.

## Fix C: partial periods (done)

- New `partial.py`. For a trend that adds up a measure by month or week, the last period is incomplete when the data reaches under 80% of its days. Coverage = days from the period's start to the last date in the data, out of the days in the period ("1 of 31 days"). This counts the days the data reaches, not days that had a row, so a seller who skips days is not called incomplete (a reading of "days with data" I chose; say if you want distinct days with rows).
- `tools.trend`: the trend is fitted on the complete periods; the stats also carry `with_partial` (the fit with the incomplete period) and `partial` (coverage, per-day rate, the earlier per-day range, verdict). The chart shows both trend lines and marks the incomplete period; the text states both. The incomplete period is not a point of the report's `series`, so the report chart and its caption do not call it the weakest month. Fewer than 4 complete periods: no trend finding.
- Verdict, per day against the complete periods: `normal` (inside their range, or within 2 standard errors of the usual day: "the lower total comes from the period being incomplete"), `fell` / `rose` (outside, with at least 7 days), `too_short` (outside, under 7 days: no direction claimed). Only sums are judged; averages and ratios have no total to be cut short.
- Ideas: `partial.idea_ok` refuses a trend idea when the incomplete period's per-day rate points the other way. The idea basis was already the complete-period fit.
- Question: signal `partial_period` (weak: asked only when the run pauses anyway) when the verdict is not `normal`. "Your data ends on <date>. Is that when the data was exported, or did <measure> really stop?" Default "The data was exported then" (note only). The other answer is the new action `set_data_end` (directives, cleaner); the trend text then says the end is real.
- Not changed: Lens `period_change` / `unusual_changes` (they already note an incomplete period but compare totals); the first period of a series is not treated the same way. The "latest month vs previous -89.5%" wording is not produced by the report trend code, so I believe it came from Lens; not confirmed.
- Tests: `tests/test_partial_periods.py` (12): ends on day 1 (normal), day 10 (fell), day 3 (too short), last day of a month (unchanged), the weekly versions, mean measure, idea guard, question and answer. Related files re-run: 190 + 23 passed.

## Fix D: derived columns and one-to-one categories (done)

- `derived_cols.derived_columns(df)`: a column that equals the product or sum of two others on 98% of rows (within a rounding error), found from the values only. A small fraction that is the quotient of two others is marked as the derived one ("ratio"). Examples on the bakery: `amount = quantity x unit_price`, `cost_amount = quantity x unit_cost`.
- Known limit: for a sum or difference the three columns fit one equation, and the code marks the sum as the derived one (`net = gross - tax` marks `gross`). Both are exact functions of the other two; which one the business "built" cannot be told from values.
- `tools.correlation` leaves out every pair inside one relation (a derived column with its inputs, and two derived columns) and says so; `tools.scatter` returns nothing for such a pair (the run log says why). Two independent inputs (quantity and unit_price) are still compared.
- `graph.analyze_node` stores `derived_columns` in the state; `strategy.candidates` never makes a derived column the lever (it swaps to the other column, or skips when both are derived).
- Categorical columns that map one to one (item_code, item) are one dimension: analyses asked of both run once, on the column with the longer values (the label). Checked against findings from earlier rounds too. Run log says "Skipped ... analysed once as item".
- A real fake-AI pipeline run on the bakery: no scatter or correlation of amount with quantity or unit_price, one finding per item/item_code, gross profit and margin as key figures. 
- Tests: `tests/test_derived_columns.py` (6). Re-run with it: new_tools_and_views, ratio_of_sums_display, store_growth_and_ratio_correlation, varied_data, partial_periods: 84 passed (8.6 min).

## Fix E: display names (done)

- Finding about the finding: the squashed names in `review_out/` ("costamount", "unitprice", "amounttocostamountratio") are made by the review runner, not the app: `scripts/review_run.py` (on `studio-v3-bench`, line ~229) deletes underscores when it cleans markdown (`re.sub(r"[*_`#>]", ...)`), which turns `cost_amount` into `costamount`. The stored real-AI run (`255a08ff...`) has `cost_amount` everywhere. That script is not on this branch and I did not change it.
- The app's real problem was the opposite: raw internal names (`cost_amount`, `unit_price`) stayed in the report's evidence cards, "checked" lines, appendix, ranking sentences, the "behind the numbers" details and chart titles. In the real-AI run's report text there were about 200 of them.
- `understanding.display_names` now covers every column and derived measure whose readable name differs from the name apart from capitals (so a joined name like `costamount` gets the record's "Cost amount"; `amount` stays `amount` mid-sentence). `apply_names` / `readable_md` do the replacement; `readable_md` leaves link and image targets alone.
- `tiering.pretty_view` renames the text fields of the whole view (answers, headlines, rankings' sentences, actions, evidence and appendix cards with their tables, checked lines, profile lines, number notes) on copies; ids, `group` / `measure` / `target` parameters, stats and key-number labels keep the internal names because other code matches on them. `reporter` applies `readable_md` to the whole report markdown and to the page's chart titles and captions. `tools._save` renames every text on a chart figure (title, axis labels, legend, notes); file names keep the internal names.
- Joined names with no AI display name (rules-only run on a file whose headers are already joined) cannot be split by code; they stay as they are. Not tested with a real AI.
- Test `test_main_view_polish` changed on purpose: it said the details keep the internal names; they now use display names. New: `tests/test_display_names.py` (5), including a full fake-AI bakery run (no internal name in the report, image links still point at real files). Related files re-run: 81 + 158 + 132 + 54 passed.
