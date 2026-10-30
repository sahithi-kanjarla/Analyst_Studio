"""scripts/report_check.py: one good and one bad sample for each of the seven rules (no AI, no run folder needed)."""
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("report_check", ROOT / "scripts" / "report_check.py")
rc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rc)

RANKING = [{"group": "Brand", "top": "Allen Solly", "basis": "total", "measure": "revenue"}]


def _actions_md(items: list[str], top_items: list[str] | None = None) -> str:
    what = "\n".join(f"{i}. {t}" for i, t in enumerate(items, 1))
    top = "\n".join(f"{i}. {t}" for i, t in enumerate(top_items if top_items is not None else items, 1))
    return f"## Executive summary\n\n### What to do\n\n{what}\n\n**Top recommendations**\n\n{top}\n\n## Key numbers\n"


# 1. recommendation lists are identical
def test_identical_lists_good():
    md = _actions_md(["Plan around the month pattern: Match effort to the strong months."])
    assert rc.rule_identical_lists(md, ["Plan around the month pattern: Match effort to the strong months."])[0]


def test_identical_lists_bad():
    md = _actions_md(["Put more behind Stride: Grow sales."])
    ok, detail = rc.rule_identical_lists(md, ["Test MRP as a lever: Find out whether it moves revenue.", "Fix returns: Check the return flag."])
    assert not ok and "saved actions has 2" in detail


# 2. no action is a bare fact line
def test_action_with_instruction_good():
    md = _actions_md(["Put more behind Stride: Grow overall revenue by investing where it is strongest."])
    assert rc.rule_no_bare_fact_actions(md, [])[0]


def test_action_that_is_a_fact_bad():
    md = _actions_md(["Revenue is highest in October and lowest in June."])
    ok, detail = rc.rule_no_bare_fact_actions(md, [])
    assert not ok and "read as a fact" in detail


# 3. 'biggest' claims match the computed top group
def test_biggest_claim_good():
    md = "Top 3 Brand by total revenue: brand Allen Solly 14,186,405 (17%), brand Raymond 9,572,674 (12%)."
    assert rc.rule_biggest_matches_top(md, RANKING)[0]


def test_biggest_claim_bad():
    md = "Top 3 Brand by total revenue: brand Levis 14,186,405 (17%), brand Raymond 9,572,674 (12%)."
    ok, detail = rc.rule_biggest_matches_top(md, RANKING)
    assert not ok and "Allen Solly" in detail


# 4. no investment advice from size alone
def test_investment_with_effect_good():
    md = _actions_md(["Invest in Stride: Grow sales where they are strongest.\n   *Supported by: Stride averages 3.3x the revenue (medium effect)*"])
    assert rc.rule_no_size_only_investment(md)[0]


def test_investment_from_size_only_bad():
    md = _actions_md(["Invest more in Stride: it has the largest total."])
    ok, detail = rc.rule_no_size_only_investment(md)
    assert not ok and "without an effect" in detail


# 5. coverage warning under 70% of calendar days
WARNING = "Only 3 of 10 days in this period have data, so trends and weekly or monthly comparisons may mislead."


def test_coverage_warning_shown_when_sparse_good():
    dates = pd.Series(pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-10"]))
    assert rc.rule_coverage_warning(f"{WARNING} Totals...", dates)[0]


def test_coverage_warning_missing_when_sparse_bad():
    dates = pd.Series(pd.to_datetime(["2024-01-01", "2024-01-02", "2024-01-10"]))
    ok, detail = rc.rule_coverage_warning("No warning here.", dates)
    assert not ok and "no warning" in detail


def test_no_warning_when_full_coverage_good():
    dates = pd.Series(pd.date_range("2024-01-01", periods=30))
    assert rc.rule_coverage_warning("Trend by month.", dates)[0]


def test_warning_on_full_coverage_bad():
    dates = pd.Series(pd.date_range("2024-01-01", periods=30))
    ok, _ = rc.rule_coverage_warning(WARNING, dates)
    assert not ok


# 6. no internal column names in the main view
def test_main_view_plain_names_good():
    md = "**Total revenue** 81,105,167 for Bill Date and Sale Price.\n\n<details>\n<summary>x</summary>\n\nraw_table_name here\n</details>\n"
    assert rc.rule_no_internal_names(md)[0]


def test_main_view_internal_name_bad():
    md = "Revenue by order_date shows the trend.\n\n<details>\n</details>"
    ok, detail = rc.rule_no_internal_names(md)
    assert not ok and "order_date" in detail


# 7. no hash links
def test_no_hash_links_good():
    assert rc.rule_no_hash_links("See [the chart](charts/chart_1.png) and [report](report.md).")[0]


def test_hash_link_bad():
    ok, detail = rc.rule_no_hash_links("Open [the run](#/run/abcdef012345).")
    assert not ok and "hash link" in detail


def test_check_runs_every_rule_and_reports_each():
    rows = rc.check("## Key numbers\n", {"report_view": {"actions": []}, "summary_facts": {"rankings": []}}, None)
    assert [r[0] for r in rows] == [
        "recommendation lists are identical", "no action is a bare fact line", "'biggest' claims match the top group",
        "no investment advice from size alone", "coverage warning under 70% of days", "no internal column names in the main view", "no hash links in the report",
    ]
    assert all(ok for _, ok, _ in rows)
