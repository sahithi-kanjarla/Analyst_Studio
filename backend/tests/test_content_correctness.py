"""Content correctness pass, with small fake data and facts: one group of tests per item (loss-style splits, minimum base for rate rankings, strategy rules, one recommendation list,
the dashboard period tile, date-coverage wording, share labels, shares in an average comparison's "so what", the default audience, listed data gaps, units in headlines)."""
import os
import re

os.environ["LLM_PROVIDER"] = "none"

import pandas as pd  # noqa: E402
from test_main_view import fake_ai, main_of, render, state_with, what_to_do  # noqa: E402
from test_report_tiers import compare, finding  # noqa: E402

from app import context as ctxmod, dashboard, insights, mainview, measures, reporter, understanding as und, strategy, summary, tiering, wording  # noqa: E402


def status_df():
    """100 orders: 80 'ok', 12 'late', 8 'lost'; amount 10 each, 'lost' ones 20 each; two regions."""
    status = ["ok"] * 80 + ["late"] * 12 + ["lost"] * 8
    amount = [10.0] * 92 + [20.0] * 8
    region = (["n", "s"] * 50)
    return pd.DataFrame({"status": status, "amount": amount, "region": region})


# ------------------------------------------------------------------ 1. loss-style: the split of the outside amount, named by code
def test_outside_amount_is_split_by_its_categories_as_a_share_of_the_outside_total():
    r = summary._outside_rate(status_df(), {}, "status", "ok", "amount", ["region"], 1)
    assert r["outside"] == 12 * 10 + 8 * 20 == 280.0
    assert [x["value"] for x in r["split"]] == ["lost", "late"]                        # 8 x 20 = 160 against 12 x 10 = 120
    assert abs(sum(x["share"] for x in r["split"]) - 1.0) < 1e-9
    assert r["largest"] == "lost" and abs(r["largest_share"] - 160 / 280) < 1e-9
    assert "of the outside total" in r["text"] and "of the outside total" in r["short"]


def test_the_so_what_of_a_loss_question_names_the_largest_part_by_computed_share_not_the_ai():
    r = summary._outside_rate(status_df(), {}, "status", "ok", "amount", [], 1)
    sentence, replaces = mainview.code_so_what([r["id"]], {}, {r["id"]: r})
    assert replaces and "lost" in sentence and "57%" in sentence and "late" not in sentence       # 160 of 280 = 57%


def test_the_ai_so_what_is_replaced_for_a_loss_question(monkeypatch):
    out = {"id": "ro", "tool": "ranking", "kind": "outside_rate", "basis": "ratio", "group": "status", "measure": "revenue", "top": "ok", "bottom": "ok", "values": ["lost"], "outside": 900.0, "by": [],
           "focus": True, "split": [{"value": "lost", "amount": 600.0, "share": 2 / 3}, {"value": "late", "amount": 300.0, "share": 1 / 3}], "largest": "lost", "largest_share": 2 / 3,
           "text": "revenue outside ok: 900.", "short": "revenue outside ok: 900 (4.5% of all revenue)"}
    st = state_with([out])
    st["summary_facts"]["picks"][st["plan"]["questions"][1]] = ["ro"]
    monkeypatch.setattr(reporter, "ask_json", fake_ai({"summary": "Fine [counts].", "recommendations": ["Do it."], "so_what": [{"question": 1, "text": "Late is the biggest part [ro]."}]}, {}, []))
    main = main_of(render(st, object()))
    assert "The largest part of the revenue outside ok is" in main and "67% of the outside total" in main and "Late is the biggest part" not in main


def test_a_loss_answer_does_not_list_the_main_value_in_a_top_list():
    r = summary._outside_rate(status_df(), {}, "status", "ok", "amount", [], 7)
    top = {"id": "r1", "tool": "ranking", "basis": "total", "group": "status", "measure": "amount", "top": "ok", "bottom": "lost", "text": "By total amount, status ok is highest at 800", "short": "s"}
    q = "How much amount is lost?"
    st = {"goal": q, "plan": {"questions": [q]}, "findings": [], "profile": {"columns": []}}
    got = summary.answers(st, {"rankings": [top, r], "picks": {q: ["r1", r["id"]]}})
    assert got[0]["ids"] == [r["id"]] and "status ok is highest" not in got[0]["answer"]


# ------------------------------------------------------------------ 2. rate rankings: a minimum base, top by amount as well as by rate
def test_minimum_base_is_one_percent_or_thirty_rows():
    assert summary.min_base(100) == 5 and summary.min_base(1000) == 10 and summary.min_base(100_000) == 30 and summary.min_base(3000) == 30


def test_a_group_under_the_base_is_listed_as_small_base_and_never_ranked_by_rate():
    n = 2000
    df = pd.DataFrame({"g": ["big"] * 1000 + ["mid"] * 990 + ["tiny"] * 10, "lost": [1.0] * 100 + [0.0] * 900 + [1.0] * 300 + [0.0] * 690 + [1.0] * 10, "total": [1.0] * n})
    df["lost"] = df["lost"].astype(float)
    spec = {"name": "loss_rate", "a": "lost", "b": "total", "kind": "ratio_of_sums"}
    r = summary._rank_ratio(df, spec, "g", "loss_rate", 1)
    assert r["top"] == "mid" and r["small_base"] == ["tiny"] and r["min_base"] == 20
    assert "Small base (under 20 rows, not ranked): " in r["text"] and "tiny" in r["text"].split("Small base")[1]
    assert "Top by amount (total lost, not by rate)" in r["text"] and "Ranked by rate" in r["text"]
    assert r["top_amount"][0] == "mid"                                                           # 300 lost against 100 for 'big'


def test_outside_rate_by_group_says_which_ranking_is_by_rate_and_which_by_amount():
    df = status_df()
    df["tiny"] = ["x"] * 97 + ["y"] * 3
    r = summary._outside_rate(df, {}, "status", "ok", "amount", ["region", "tiny"], 1)
    t = r["text"]
    assert "ranked by rate" in t and "ranked by amount, not by rate" in t
    assert any(b["group"] == "tiny" for b in r["by"]) is False or "y" in next(b for b in r["by"] if b["group"] == "tiny")["small_base"]


# ------------------------------------------------------------------ 3. strategy rule
def test_extra_investment_for_the_largest_share_is_blocked_but_protect_and_diversify_pass():
    st = {"findings": [finding(1, "pareto", True, stats={"top_k": 2, "top_share": 0.8}, target="revenue", group="seg")], "summary_facts": {"rankings": [
        {"id": "r1", "tool": "ranking", "basis": "total", "group": "seg", "measure": "revenue", "top": "a", "bottom": "b", "text": "t", "short": "s"}]}}
    assert strategy.blocked("Invest more in segment a, it holds the largest share [f1]", ["f1"], st)
    assert strategy.blocked("Put more behind segment a, which holds 62% of revenue", ["r1"], st)
    assert strategy.blocked("Protect segment a and diversify into the others", ["f1"], st) == ""
    st["findings"].append(compare(2, "seg"))
    assert strategy.blocked("Invest more in segment a because revenue is higher there", ["f2"], st) == ""          # a significant difference is a different basis, not a share


def test_no_action_ranks_groups_whose_differences_the_report_calls_not_significant():
    st = {"findings": [compare(1, "seg", sig=False)], "summary_facts": {"rankings": []}}
    st["findings"][0]["significant"] = False
    assert "not statistically significant" in strategy.blocked("Focus on the top seg values first", [], st)
    assert strategy.blocked("Focus on the top region values first", [], st) == ""
    assert strategy.blocked("Review the seg mix", [], st) == ""


def test_the_prompts_carry_the_concentration_rule():
    assert "never recommended for extra investment only because it holds the largest share" in strategy.CONCENTRATION_RULE
    assert "protect" in strategy.CONCENTRATION_RULE and "diversify" in strategy.CONCENTRATION_RULE
    assert strategy.CONCENTRATION_RULE in strategy.REFINE_SYSTEM


def test_an_ai_recommendation_that_invests_on_a_share_only_is_dropped(monkeypatch):
    st = state_with()
    st["findings"].append(finding(300, "pareto", True, stats={"top_k": 2, "top_share": 0.8}, target="revenue", group="seg"))
    st["findings"][-1]["text"] = "The top 2 seg values account for 80% of total revenue"
    monkeypatch.setattr(reporter, "ask_json", fake_ai({"summary": "Fine [counts].", "recommendations": ["Invest more in segment a, 62.5% of all revenue [f300].", "Keep the pricing review going."]}, {}, []))
    res = reporter.llm_summary(object(), st)
    assert res is not None and not any("Invest more" in r for r in res[1])


# ------------------------------------------------------------------ 4. one recommendation list
def test_what_to_do_and_top_recommendations_are_the_same_list():
    md = render(state_with())
    numbered = re.compile(r"^\d+\. ")
    todo = [l for l in what_to_do(main_of(md)).split("\n") if numbered.match(l)]
    top = [l for l in md.split("**Top recommendations**")[1].split("data-backed strateg")[0].split("\n") if numbered.match(l)]
    assert todo and todo == top


def test_a_monitor_only_strategy_is_not_a_recommendation():
    st = state_with()
    st["strategies"][0].update({"title": "Understand the rise in revenue", "monitor": True})
    st["strategies"][1].update({"title": "Monitor revenue each month"})
    st["strategies"][2].update({"title": "Keep watching the cost"})
    todo = what_to_do(main_of(render(st)))
    assert "Understand the rise" not in todo and "Monitor revenue" not in todo and "Keep watching" not in todo


# ------------------------------------------------------------------ 5. dashboard period tile
def test_the_dashboard_reports_date_coverage(tmp_path):
    days = pd.date_range("2024-01-01", periods=100, freq="D")[::4]
    df = pd.DataFrame({"d": days, "seg": ["a", "b"] * (len(days) // 2) + ["a"] * (len(days) % 2), "v": range(len(days))})
    df.to_pickle(tmp_path / "clean.pkl")
    out = dashboard.build_dashboard(tmp_path, {"plan": {"target": "v", "date_col": "d", "relevant_columns": ["v", "seg", "d"]}, "profile": {"columns": [
        {"name": "v", "role": "numeric"}, {"name": "seg", "role": "categorical"}, {"name": "d", "role": "date"}]}})
    assert 0.2 < out["date_coverage"] < 0.3


def test_the_period_tile_is_labelled_partial_in_the_page():
    tsx = open(os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "src", "components", "BIDashboard.tsx"), encoding="utf-8").read()
    assert "(partial data)" in tsx and "date_coverage < 0.7" in tsx and "< 6" in tsx


# ------------------------------------------------------------------ 6. coverage warning
def test_coverage_warning_says_totals_and_shares_cover_only_the_days_present():
    d = pd.Series(pd.date_range("2024-01-01", periods=100, freq="D")[::2])
    w = insights.coverage_warning(d)
    assert w.endswith("Totals and shares cover only the days present.") and w.startswith("Only 50 of 99 days")
    assert insights.coverage_warning(pd.Series(pd.date_range("2024-01-01", periods=100, freq="D"))) == ""


# ------------------------------------------------------------------ 7. share labels
def test_a_share_of_rows_and_of_a_quantity_are_labelled_from_what_was_computed():
    rec = {"source": "llm", "columns": {"q": {"role": "measure", "measure_kind": "quantity"}, "m": {"role": "measure", "measure_kind": "money"}, "x": {"role": "measure"}}}
    assert und.share_label(rec, None) == "all records" and und.share_label(rec, "q") == "all units" and und.share_label(rec, "m") == "all m" and und.share_label(rec, "x") == "all records"
    assert und.share_label(None, "q") == "all records"                                   # no record: unknown, never guessed from the column name
    df = pd.DataFrame({"g": ["a"] * 10 + ["b"] * 10, "q": [3.0] * 10 + [1.0] * 10})
    assert "75.0% of all units" in summary._rank_additive(df, {"understanding": rec}, "g", "q", 1)["text"]
    assert "75.0% of all records" in summary._rank_additive(df, {}, "g", "q", 1)["text"]
    out = summary._outside_rate(status_df(), {}, "status", "ok", None, [], 1)           # no measure: rows are counted
    assert "of all records" in out["short"] and "of records" in out["text"] and "of all rows" not in out["text"]


# ------------------------------------------------------------------ 8. so what of an average comparison mentions the shares
def test_so_what_of_an_average_comparison_mentions_each_groups_share_of_the_total():
    f = compare(1, "seg")
    f["stats"].update({"kind": "numeric", "ratio_of_sums": False, "best": "a", "worst": "b", "shares": {"a": 0.2, "b": 0.5}})
    sentence, replaces = mainview.code_so_what(["f1"], {"f1": f}, {})
    assert not replaces and "20%" in sentence and "50%" in sentence and "seg" in sentence
    assert mainview.with_shares("The lower-average group matters.", sentence) == "The lower-average group matters. " + sentence
    assert mainview.with_shares("a holds 20% and b holds 50%.", sentence) == "a holds 20% and b holds 50%."


def test_group_compare_stores_the_shares_of_the_best_and_worst_group(tmp_path):
    from app import tools
    rev = [30.0, 31.0, 29.0, 32.0] * 5 + [10.0, 11.0, 9.0, 12.0] * 20
    df = pd.DataFrame({"seg": ["a"] * 20 + ["b"] * 80, "revenue": rev})
    f = tools.group_compare(df, {"charts_dir": tmp_path}, "revenue", "seg")
    sa, sb = sum(rev[:20]), sum(rev[20:])
    assert f["stats"]["best"] == "a" and abs(f["stats"]["shares"]["a"] - sa / (sa + sb)) < 1e-3 and abs(f["stats"]["shares"]["b"] - sb / (sa + sb)) < 1e-3


# ------------------------------------------------------------------ 9. audience
def test_the_default_audience_is_the_business_owner_and_leadership_needs_to_be_named(monkeypatch):
    assert ctxmod.audience({}) == "you (the business owner)" and ctxmod.audience({"audience": "the board"}) == "the board"
    assert wording.audience_clash("This matters for leadership.", {"context": {}}) == "leadership"
    assert wording.audience_clash("This matters for leadership.", {"context": {"audience": "the leadership team"}}) == ""
    st = state_with()
    systems = []
    inner = fake_ai({"summary": "Leadership should act [counts]. Fine [counts].", "recommendations": ["Do it."]}, {}, [])
    monkeypatch.setattr(reporter, "ask_json", lambda llm, system, user: (systems.append(system), inner(llm, system, user))[1])
    res = reporter.llm_summary(object(), st)
    assert "eadership" not in res[0] and any("you (the business owner)" in x for x in systems)


# ------------------------------------------------------------------ 10. data gaps are listed or the item is omitted
def test_close_the_gaps_lists_the_actual_gaps_or_is_left_out():
    base = state_with()
    base["findings"] = [compare(1, "g0")]
    base["data_gaps"] = [{"severity": "warn", "title": "Margin: not computable, no cost column", "detail": "d", "suggestion": "Add a cost column."},
                         {"severity": "warn", "title": "Dates cover only 40% of days", "detail": "d", "suggestion": "Fill the missing days."}]
    cands = strategy.candidates({**base, "plan": {**base["plan"]}, "polarity": None})
    gap = next(c for c in cands if c["title"] == "Close the most important data gaps")
    assert "no cost column" in gap["objective"] and "Dates cover only 40% of days" in gap["objective"] and gap["actions"] == ["Add a cost column.", "Fill the missing days."]
    cands = strategy.candidates({**base, "data_gaps": []})
    assert not any(c["title"] == "Close the most important data gaps" for c in cands)


# ------------------------------------------------------------------ 11. every figure in a headline has its unit and says what it is
def test_headline_figures_carry_their_unit_and_what_they_are():
    st = {"p": 0.001, "effect": 0.2, "effect_name": "eta_squared", "best": "a", "worst": "b", "kind": "numeric", "values": {"a": 862.0, "b": 400.0}, "ratio_of_sums": False}
    f = finding(1, "group_compare", True, stats=st, target="revenue", group="seg")
    h = insights._narrative(f, {"currency": "₹"}, None, [])["headline"]
    assert "average ₹862 per record vs ₹400 for b" in h
    binary = finding(2, "group_compare", True, stats={**st, "kind": "binary", "values": {"a": 0.75, "b": 0.5}}, target="returned", group="seg")
    assert "75% of records" in insights._narrative(binary, {}, None, [])["headline"]


# ------------------------------------------------------------------ 0. the kind of a measure comes from the understanding record, validated by code
def test_measure_kind_is_validated_against_the_list_and_only_kept_for_measures():
    obj = {"columns": [{"column": "a", "meaning": "m", "role": "measure", "measure_kind": "Quantity"}, {"column": "b", "meaning": "m", "role": "measure", "measure_kind": "weight"},
                       {"column": "c", "meaning": "m", "role": "category", "measure_kind": "money"}], "goal_columns": {}}
    parts, errors = und.validate(obj, ["a", "b", "c"])
    assert not errors and parts["columns"]["a"]["measure_kind"] == "quantity"
    assert "measure_kind" not in parts["columns"]["b"] and "measure_kind" not in parts["columns"]["c"]           # not in the list, or not a measure: no label
    assert "measure_kind" in und.SYSTEM
