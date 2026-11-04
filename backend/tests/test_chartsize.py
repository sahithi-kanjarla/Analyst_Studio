"""The two display helpers the frontend shares, checked in node: how many of a chart's values to draw for its type, data and width
(chartsize.ts), and how a column name or a written sentence reads on screen (display.ts). Needs node 22 or newer, which runs the
TypeScript directly; skipped where node is missing or too old. The checks themselves live in ui/chartsize_check.mjs."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).parent / "ui" / "chartsize_check.mjs"


def node_major() -> int | None:
    if not shutil.which("node"):
        return None
    try:
        out = subprocess.run([shutil.which("node"), "-v"], capture_output=True, text=True, timeout=20).stdout.strip()
        return int(out.lstrip("v").split(".")[0])
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


@pytest.fixture(scope="module")
def results():
    major = node_major()
    if major is None or major < 22:
        pytest.skip("needs node 22+ (it runs the TypeScript helpers directly)")
    out = subprocess.run([shutil.which("node"), "--experimental-strip-types", str(SCRIPT)], cwd=ROOT, capture_output=True, text=True, timeout=180)
    line = [x for x in out.stdout.splitlines() if x.startswith("{")]
    assert line, out.stdout + out.stderr
    return json.loads(line[-1])


GROUPS = {
    "ranked categories": ["ranked_5_additive_shows_every_category", "ranked_5_additive_subtitle_says_all", "ranked_12_additive_shows_every_category", "ranked_12_additive_subtitle_says_all",
                          "ranked_5_rate_shows_every_category", "ranked_12_rate_shows_every_category", "ranked_12_rate_subtitle_says_all", "ranked_5_rate_subtitle_says_all",
                          "ranked_40_additive_shows_the_top_10_at_desktop_width", "ranked_40_additive_offers_show_all", "ranked_40_additive_pools_other_only_when_the_measure_adds_up",
                          "ranked_40_additive_subtitle_names_what_is_shown", "ranked_40_additive_other_bar_is_the_true_total_of_the_rest",
                          "ranked_40_rate_shows_the_top_10_at_desktop_width", "ranked_40_rate_offers_show_all", "ranked_40_rate_pools_other_only_when_the_measure_adds_up",
                          "ranked_40_rate_subtitle_names_what_is_shown", "ranked_at_the_boundary_of_few_shows_all"],
    "width": ["ranked_limit_is_fewer_on_a_phone_than_on_a_laptop_than_wide", "ranked_40_on_a_phone_shows_five"],
    "zero and negative": ["all_zero_categories_still_plan_without_error", "all_negative_categories_keep_the_true_pooled_total", "a_few_zero_categories_are_all_shown"],
    "share of a whole": ["share_30_slices_keeps_five_and_pools_the_rest", "share_30_slices_is_at_most_six_wedges", "share_subtitle_names_what_is_shown", "share_of_a_rate_is_refused",
                         "share_with_a_negative_part_is_refused", "share_of_all_zero_is_refused", "share_of_a_few_parts_shows_them_all"],
    "time": ["series_10_points_keeps_every_one", "series_10_subtitle_says_all", "series_2000_points_keeps_every_point",
             "series_2000_points_steps_the_period_up_instead_of_cutting", "time_step_moves_up_only_as_far_as_needed"],
    "points and cells": ["points_under_the_cap_are_all_drawn", "points_over_the_cap_are_sampled_and_said_to_be"],
    "a chart's table": ["table_shows_a_few_rows_then_show_all", "table_show_all_opens_every_row", "a_short_table_has_no_show_all"],
    "insight widgets": ["widget_ranking_uses_the_first_figure_column_not_the_row_count", "widget_picked_from_the_analysis_type_and_table", "widget_big_number_comes_from_the_sentence",
                        "widget_direction_only_where_the_sentence_supports_it", "widget_takeaway_is_two_sentences_and_keeps_decimals", "widget_share_bar_is_five_slices_plus_other",
                        "widget_themes_follow_the_analysis_type", "plain_label_drops_total_and_sum"],
    "latest month vs previous": ["latest_change_series_ending_on_day_1_shows_no_direction", "latest_change_series_ending_on_day_10_compares_per_day", "latest_change_series_ending_on_the_last_day_compares_the_totals",
                                 "latest_change_just_over_80_percent_of_the_days_is_complete", "latest_change_just_under_80_percent_is_per_day", "latest_change_of_an_average_is_compared_as_it_is",
                                 "latest_change_needs_two_periods_and_day_grain_is_unchanged"],
    "more charts count": ["more_charts_counts_finding_charts_and_drawn_live_charts_only"],
    "wording": ["plain_title_keeps_x_inside_words", "plain_title_still_removes_a_real_multiplier", "plain_title_has_no_symbols_or_p_values", "plain_title_is_at_most_six_words", "plural_handles_the_shapes_it_meets", "prettify_spells_out_an_internal_name", "prettify_keeps_acronyms_and_camel_case",
                "display_name_prefers_what_the_analysis_worked_out", "display_name_without_a_record_still_reads", "humanise_replaces_every_internal_name_in_a_sentence",
                "humanise_leaves_no_underscore_id_behind", "chart_title_drops_total_and_the_aggregation", "chart_title_of_an_average_reads_plainly", "looks_internal_spots_what_it_should",
                "plain_title_names_the_measure_and_the_split_from_display_names", "plain_title_drops_the_table_and_aggregation_words", "plain_title_turns_a_cross_into_and"],
}


@pytest.mark.parametrize("group", list(GROUPS))
def test_the_chart_size_and_wording_rules(results, group):
    missing = [k for k in GROUPS[group] if k not in results]
    assert not missing, f"checks never ran: {missing}"
    failed = {k: results[k] for k in GROUPS[group] if not results[k].get("ok")}
    assert not failed, failed


def test_every_check_in_the_script_is_covered_by_a_group(results):
    covered = {k for ks in GROUPS.values() for k in ks}
    assert not (set(results) - covered), f"checks not asserted by any group: {sorted(set(results) - covered)}"
