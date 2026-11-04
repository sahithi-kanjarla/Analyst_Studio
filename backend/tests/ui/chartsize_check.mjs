// Unit checks for the two display helpers the frontend shares: how many of a chart's values to draw (chartsize.ts) and
// how a name or a sentence reads on screen (display.ts). Prints one JSON object of named checks; test_chartsize.py asserts them.
//   node --experimental-strip-types backend/tests/ui/chartsize_check.mjs
import {
  FEW, POINTS_MAX, SHARE_SLICES, TABLE_ROWS, planPoints, planRanked, planSeries, planShare, planTable, plural, rankedLimit, timeStep,
} from '../../../frontend/src/chartsize.ts';
import { chartTitle, displayName, humanise, looksInternal, plainLabel, prettify } from '../../../frontend/src/display.ts';
import { moreChartCount } from '../../../frontend/src/morecharts.ts';
import { latestChange } from '../../../frontend/src/periodchange.ts';
import { bigNumber, direction, shareSlices, tableSeries, takeaway, themeOf, widgetKind } from '../../../frontend/src/insightwidget.ts';

const R = {};
const ok = (name, cond, detail) => { R[name] = { ok: !!cond, detail: detail === undefined ? '' : String(detail) }; };

const vals = (n, f = (i) => 100 - i) => Array.from({ length: n }, (_, i) => ({ key: `k${i}`, label: `Group ${i}`, value: f(i), n: 10 }));
const opts = (extra = {}) => ({ width: 1440, measure: 'additive', group: 'region', measureLabel: 'Revenue', ...extra });
const other = (p) => p.items.find((i) => i.other);

// ---------------------------------------------------------------- ranked categories: 5, 12 and 40, additive and not
for (const measure of ['additive', 'rate']) {
  for (const n of [5, 12]) {
    const p = planRanked(vals(n), opts({ measure }));
    ok(`ranked_${n}_${measure}_shows_every_category`, p.shown === n && p.items.length === n && !p.pooled && !p.showAll && p.hidden === 0, JSON.stringify({ shown: p.shown, sub: p.subtitle }));
    ok(`ranked_${n}_${measure}_subtitle_says_all`, p.subtitle === `All ${n} regions`, p.subtitle);
  }
  const p = planRanked(vals(40), opts({ measure }));
  const pooled = measure === 'additive';
  ok(`ranked_40_${measure}_shows_the_top_10_at_desktop_width`, p.shown === 10 && p.total === 40 && p.hidden === 30, JSON.stringify({ shown: p.shown, hidden: p.hidden }));
  ok(`ranked_40_${measure}_offers_show_all`, p.showAll === true && p.hidden === 30, p.hidden);
  ok(`ranked_40_${measure}_pools_other_only_when_the_measure_adds_up`, p.pooled === pooled && !!other(p) === pooled, JSON.stringify({ pooled: p.pooled, hasOther: !!other(p) }));
  ok(`ranked_40_${measure}_subtitle_names_what_is_shown`, p.subtitle.startsWith('Top 10 of 40 regions by revenue') && p.subtitle.includes('pooled as Other') === pooled, p.subtitle);
  if (pooled) {
    const rest = vals(40).slice(10).reduce((s, v) => s + v.value, 0);
    ok('ranked_40_additive_other_bar_is_the_true_total_of_the_rest', other(p).value === rest && other(p).groups === 30 && other(p).label === 'Other (30 items)', JSON.stringify({ got: other(p).value, want: rest }));
  }
}
ok('ranked_at_the_boundary_of_few_shows_all', planRanked(vals(FEW), opts()).shown === FEW && planRanked(vals(FEW + 1), opts()).shown === 10, FEW);

// ---------------------------------------------------------------- width
ok('ranked_limit_is_fewer_on_a_phone_than_on_a_laptop_than_wide', rankedLimit(390) === 5 && rankedLimit(1024) === 8 && rankedLimit(1440) === 10, [rankedLimit(390), rankedLimit(1024), rankedLimit(1440)].join('/'));
ok('ranked_40_on_a_phone_shows_five', planRanked(vals(40), opts({ width: 390 })).shown === 5);

// ---------------------------------------------------------------- all zero, and all negative
const zero = planRanked(vals(40, () => 0), opts());
ok('all_zero_categories_still_plan_without_error', zero.shown === 10 && other(zero).value === 0, JSON.stringify({ shown: zero.shown, other: other(zero).value }));
const neg = planRanked(vals(40, (i) => -1 - i), opts());
ok('all_negative_categories_keep_the_true_pooled_total', other(neg).value === vals(40, (i) => -1 - i).slice(10).reduce((s, v) => s + v.value, 0), other(neg).value);
const zeroFew = planRanked(vals(4, () => 0), opts());
ok('a_few_zero_categories_are_all_shown', zeroFew.shown === 4 && !zeroFew.pooled);

// ---------------------------------------------------------------- share of a whole: 30 slices
const share30 = planShare(vals(30), { measure: 'additive', group: 'region', measureLabel: 'Revenue' });
ok('share_30_slices_keeps_five_and_pools_the_rest', share30.suitable && share30.shown === SHARE_SLICES - 1 && other(share30).groups === 30 - (SHARE_SLICES - 1), JSON.stringify({ shown: share30.shown, slices: share30.items.length }));
ok('share_30_slices_is_at_most_six_wedges', share30.items.length <= SHARE_SLICES, share30.items.length);
ok('share_subtitle_names_what_is_shown', share30.subtitle.startsWith('Top 5 of 30 regions by share of revenue'), share30.subtitle);
ok('share_of_a_rate_is_refused', planShare(vals(8), { measure: 'rate', group: 'region', measureLabel: 'Average discount' }).suitable === false);
ok('share_with_a_negative_part_is_refused', planShare(vals(8, (i) => (i === 3 ? -5 : 10)), { measure: 'additive', group: 'region', measureLabel: 'Revenue' }).suitable === false);
ok('share_of_all_zero_is_refused', planShare(vals(8, () => 0), { measure: 'additive', group: 'region', measureLabel: 'Revenue' }).suitable === false);
ok('share_of_a_few_parts_shows_them_all', planShare(vals(4), { measure: 'additive', group: 'region', measureLabel: 'Revenue' }).shown === 4);

// ---------------------------------------------------------------- time: 10 and 2,000 points
const s10 = planSeries(vals(10), { step: 'month' });
ok('series_10_points_keeps_every_one', s10.shown === 10 && s10.hidden === 0 && s10.step === 'month' && !s10.showAll, JSON.stringify({ shown: s10.shown, step: s10.step }));
ok('series_10_subtitle_says_all', s10.subtitle === 'All 10 months', s10.subtitle);
const s2000 = planSeries(vals(2000), { step: 'day' });
ok('series_2000_points_keeps_every_point', s2000.shown === 2000 && s2000.hidden === 0, JSON.stringify({ shown: s2000.shown, hidden: s2000.hidden }));
ok('series_2000_points_steps_the_period_up_instead_of_cutting', s2000.step === 'quarter' && s2000.subtitle.includes('grouped by quarter'), JSON.stringify({ step: s2000.step, sub: s2000.subtitle }));
ok('time_step_moves_up_only_as_far_as_needed', timeStep(10, 'day') === 'day' && timeStep(200, 'day') === 'week' && timeStep(1000, 'day') === 'month' && timeStep(100000, 'day') === 'quarter', [timeStep(10, 'day'), timeStep(200, 'day'), timeStep(1000, 'day'), timeStep(100000, 'day')].join('/'));

// ---------------------------------------------------------------- points and cells
const few = planPoints(vals(500), { what: 'orders' });
ok('points_under_the_cap_are_all_drawn', few.shown === 500 && !few.sampled && few.subtitle === 'All 500 orders', few.subtitle);
const many = planPoints(vals(POINTS_MAX * 3));
ok('points_over_the_cap_are_sampled_and_said_to_be', many.sampled && many.shown <= POINTS_MAX && many.subtitle.includes('evenly sampled'), JSON.stringify({ shown: many.shown, sub: many.subtitle }));

// ---------------------------------------------------------------- a chart's table
const t = planTable(vals(40));
ok('table_shows_a_few_rows_then_show_all', t.shown === TABLE_ROWS && t.showAll && t.subtitle === `Showing ${TABLE_ROWS} of 40`, t.subtitle);
ok('table_show_all_opens_every_row', planTable(vals(40), { all: true }).shown === 40);
ok('a_short_table_has_no_show_all', planTable(vals(3)).showAll === false);

// ---------------------------------------------------------------- wording
ok('plural_handles_the_shapes_it_meets', plural('region') === 'regions' && plural('city') === 'cities' && plural('class') === 'classes' && plural('status') === 'statuses' && plural('months') === 'months' && plural('orders') === 'orders',
  [plural('region'), plural('city'), plural('class'), plural('status'), plural('months')].join('/'));
ok('prettify_spells_out_an_internal_name', prettify('sub_category') === 'Sub category' && prettify('discount_pct') === 'Discount %' && prettify('order_qty') === 'Order quantity', [prettify('sub_category'), prettify('discount_pct'), prettify('order_qty')].join(' | '));
ok('prettify_keeps_acronyms_and_camel_case', prettify('customerId') === 'Customer ID' && prettify('gst_amt') === 'GST amount', [prettify('customerId'), prettify('gst_amt')].join(' | '));
const und = { columns: { sub_category: { display_name: 'Product range' }, discount_pct: {} } };
ok('display_name_prefers_what_the_analysis_worked_out', displayName('sub_category', und) === 'Product range' && displayName('discount_pct', und) === 'Discount %', [displayName('sub_category', und), displayName('discount_pct', und)].join(' | '));
ok('display_name_without_a_record_still_reads', displayName('net_revenue', null) === 'Net revenue');
const sentence = humanise('sub_category = Headphones averages 8.6x the discount_pct of Tables', und);
ok('humanise_replaces_every_internal_name_in_a_sentence', sentence === 'Product range = Headphones averages 8.6x the Discount % of Tables', sentence);
ok('humanise_leaves_no_underscore_id_behind', !looksInternal(humanise('growth of unknown_col_here by region', und)), humanise('growth of unknown_col_here by region', und));
ok('chart_title_drops_total_and_the_aggregation', chartTitle('Total revenue', 'region', und) === 'Revenue by region' && chartTitle('revenue (sum)', 'sub_category', und) === 'Revenue by product range', [chartTitle('Total revenue', 'region', und), chartTitle('revenue (sum)', 'sub_category', und)].join(' | '));
ok('chart_title_of_an_average_reads_plainly', chartTitle('Average Sale Price', 'Dept', und) === 'Sale Price by dept', chartTitle('Average Sale Price', 'Dept', und));
ok('looks_internal_spots_what_it_should', looksInternal('discount_pct by region') && looksInternal('revenue (sum)') && !looksInternal('Revenue by region'));

// ---------------------------------------------------------------- insight widgets: chosen from metadata and data, never from a name
const tbl = { id: 't', title: 'Revenue by region (table)', columns: ['Region', 'Rows', 'Average', 'Share of total'], total: 3, rows: [['A', 50, '4,618', '16%'], ['B', 57, '4,033', '15%'], ['C', 65, '3,331', '9%']] };
const ser = tableSeries(tbl, 'group_compare');
ok('widget_ranking_uses_the_first_figure_column_not_the_row_count', ser && ser.valueHeader === 'Average' && ser.items[0].value === 4618 && ser.items.length === 3 && ser.title === 'Revenue by region', JSON.stringify(ser && ser.valueHeader));
ok('widget_picked_from_the_analysis_type_and_table', widgetKind('group_compare', [tbl], 'x.png') === 'ranking' && widgetKind('trend', [tbl], 'x.png') === 'image' && widgetKind('composition', [tbl], 'x.png') === 'share' && widgetKind('trend', [], 'x.png') === 'image');
ok('widget_big_number_comes_from_the_sentence', bigNumber('A averages 7.4× the revenue of B', 'group_compare').value === '7.4×' && bigNumber('revenue is trending down (-44% over the period)', 'trend').value === '-44%' && bigNumber('x rises as y rises (rho = +0.83)', 'correlation').value === '+0.83' && bigNumber('nothing numeric here', 'trend') === null);
ok('widget_direction_only_where_the_sentence_supports_it', direction('revenue is trending down', 'trend') === 'down' && direction('revenue rises as price rises', 'correlation') === 'up' && direction('A averages 3x B', 'group_compare') === null && direction('up and down', 'trend') === null && direction('x', 'trend', -1) === 'down');
ok('widget_takeaway_is_two_sentences_and_keeps_decimals', takeaway('First one is 60.0 here. Second. Third.', '') === 'First one is 60.0 here. Second.', takeaway('First one is 60.0 here. Second. Third.', ''));
const sl = Array.from({ length: 30 }, (_, i) => ({ label: `S${i}`, value: 30 - i, text: `${30 - i}%` }));
ok('widget_share_bar_is_five_slices_plus_other', shareSlices(sl).length === 6 && shareSlices(sl)[5].label.startsWith('Other (25') && shareSlices(sl.slice(0, 6)).length === 6);
ok('widget_themes_follow_the_analysis_type', themeOf('group_compare') === 'ranking' && themeOf('trend') === 'time' && themeOf('correlation') === 'link' && themeOf('outliers') === 'shape');
ok('plain_label_drops_total_and_sum', plainLabel('Total revenue') === 'Revenue' && plainLabel('revenue (sum)') === 'Revenue' && plainLabel('Total Orders') === 'Orders');

// ---------------------------------------------------------------- the Dashboard's "latest month vs previous": never a total over part of a month against a whole one
const series = (last) => [{ key: '2026-10', value: 3100 }, { key: '2026-11', value: 3000 }, { key: '2026-12', value: last }];
const d1 = latestChange(series(100), 'month', '2026-12-01', true);
ok('latest_change_series_ending_on_day_1_shows_no_direction', d1 && d1.kind === 'too_few' && d1.change === null && d1.days === 1 && d1.of === 31, JSON.stringify(d1));
const d10 = latestChange(series(1200), 'month', '2026-12-10', true);     // 1,200 over 10 days is 120 a day; November was 100 a day. Total against total would read -60%.
ok('latest_change_series_ending_on_day_10_compares_per_day', d10 && d10.kind === 'per_day' && Math.abs(d10.change - 0.2) < 1e-9 && d10.days === 10 && d10.of === 31, JSON.stringify(d10));
const d31 = latestChange(series(3100), 'month', '2026-12-31', true);
ok('latest_change_series_ending_on_the_last_day_compares_the_totals', d31 && d31.kind === 'total' && Math.abs(d31.change - (100 / 3000)) < 1e-9, JSON.stringify(d31));
const d25 = latestChange(series(2500), 'month', '2026-12-25', true);       // 25 of 31 days is 81%: complete enough, as in the report's partial-period rule
ok('latest_change_just_over_80_percent_of_the_days_is_complete', d25 && d25.kind === 'total', JSON.stringify(d25));
const d24 = latestChange(series(2400), 'month', '2026-12-24', true);       // 24 of 31 is 77%: incomplete
ok('latest_change_just_under_80_percent_is_per_day', d24 && d24.kind === 'per_day' && Math.abs(d24.change - ((2400 / 24) / (3000 / 30) - 1)) < 1e-9, JSON.stringify(d24));
const rate = latestChange(series(55), 'month', '2026-12-10', false);       // an average is not made smaller by an unfinished month: compared as it is, coverage still shown
ok('latest_change_of_an_average_is_compared_as_it_is', rate && rate.kind === 'per_day' && Math.abs(rate.change - (55 / 3000 - 1)) < 1e-9 && rate.days === 10, JSON.stringify(rate));
ok('latest_change_needs_two_periods_and_day_grain_is_unchanged', latestChange([{ key: '2026-12', value: 5 }], 'month', '2026-12-10', true) === null && latestChange([{ key: '2026-12-01', value: 5 }, { key: '2026-12-02', value: 10 }], 'day', '2026-12-02', true).change === 1);

ok('more_charts_counts_finding_charts_and_drawn_live_charts_only', moreChartCount(5, 0) === 5 && moreChartCount(3, 2) === 5 && moreChartCount(0, 0) === 0, 'the table card adds nothing');
const failed = Object.entries(R).filter(([, v]) => !v.ok);
process.stdout.write(JSON.stringify(R) + '\n');
if (failed.length) process.exitCode = 1;
