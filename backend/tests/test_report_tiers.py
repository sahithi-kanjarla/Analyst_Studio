"""Report size: three tiers over a synthetic findings list. Tier caps, merges, the checked list, the data profile, the % rule and the outlier fence."""
import os

os.environ["LLM_PROVIDER"] = "none"

from app import reporter, tiering  # noqa: E402

CONF = {"level": "high", "label": "High", "reasons": ["Still significant after correction."]}


def finding(i, tool, sig=None, headline=None, score=1.0, text="t", stats=None, **params):
    return {"id": f"f{i}", "tool": tool, "title": f"{tool} {i}", "params": params, "stats": stats or {}, "significant": sig, "text": text, "action": None,
            "insight": {"headline": headline or f"{tool} {i} headline", "observation": text, "why_it_matters": "because", "score": score, "confidence": CONF,
                        "hypotheses": ["h1", "h2", "h3", "h4"], "check_next": ["c1", "c2", "c3"], "kpi_related": False}}


def compare(i, group, target="revenue", sig=True, score=5.0, **params):
    st = {"p": 0.0001, "p_adjusted": 0.001, "survives_correction": sig, "effect": 0.2, "effect_name": "eta_squared", "n": 500}
    return finding(i, "group_compare", sig, f"{group} drives {target}", score, stats=st, target=target, group=group, **params)


def make_state():
    fs, n = [], 0
    for g in range(12):                                           # twelve reliable, large-effect comparisons: only 8 fit in the evidence tier
        n += 1
        fs.append(compare(n, f"g{g}", score=20 - g))
    table = finding(102, "group_table", None, "revenue by g0: per-group figures", target="revenue", group="g0")
    table["table"] = {"columns": ["Group", "Total"], "rows": [["a", 1], ["b", 2]], "total": 2}
    fs += [finding(100, "robust_compare", True, "median holds", stats={"p": 0.0001}, target="revenue", group="g0"),         # folded into the g0 card
           finding(101, "robust_compare", False, "median does not hold", stats={"p": 0.4}, target="revenue", group="g1"),
           table,
           compare(103, "g0", target="margin"),                                                                              # ratio of revenue / cost on the same column
           compare(104, "g0", target="cost", sig=False),
           finding(105, "trend", False, "No reliable trend in revenue over time", stats={"p": 0.6}, target="revenue", date="when"),
           finding(106, "composition", False, "The region mix is the same across g0", stats={"p": 0.7}, group="g0", by="region"),
           finding(107, "composition", True, "The region mix differs across g1", stats={"p": 0.001}, group="g1", by="region"),
           finding(108, "distribution", None, "revenue: typical value 5", col="revenue"),
           finding(109, "category_share", None, "g0: 4 distinct values", col="g0"),
           finding(110, "outliers", None, "10 unusual values in revenue", text="**revenue**: 10 of 200 values (5.0%) fall outside the usual range (-370 to 744).",
                   stats={"n": 200, "outliers": 10, "outlier_pct": 0.05}, col="revenue"),
           finding(111, "outliers", None, "3 unusual values in profit", text="**profit**: 3 of 200 values (1.5%) fall outside the usual range (-50 to 80).",
                   stats={"n": 200, "outliers": 3, "outlier_pct": 0.015}, col="profit")]
    strategies = [{"id": f"s{k}", "kind": "data", "theme": "Growth", "title": f"Put more behind what works: g{k} = a", "objective": f"o{k}", "evidence": [f"f{k + 1}"], "impact": "High", "effort": "Medium",
                   "confidence": "high", "actions": ["a1", "a2", "a3"], "how_to_test": "t", "kpis": ["k"], "risks": ["r1", "r2", "r3"], "horizon": "Quick win (0-30 days)", "rank": k + 1} for k in range(3)]
    strategies += [{"id": f"s{k + 3}", "kind": "data", "theme": f"Theme{k}", "title": f"Strategy {k}", "objective": f"o{k}", "evidence": [f"f{k + 4}"], "impact": "Medium", "effort": "Low",
                    "confidence": "medium", "actions": ["a1"], "how_to_test": "t", "kpis": ["k"], "risks": ["r"], "horizon": "Quick win (0-30 days)", "rank": k + 4} for k in range(6)]
    strategies += [{"id": "s9", "kind": "foundation", "theme": "Data foundation", "title": "Close data gaps", "objective": "More data.", "evidence": [], "impact": "Medium", "effort": "Low",
                    "confidence": "", "actions": ["x"], "how_to_test": "t", "kpis": ["k"], "risks": ["r"], "horizon": "Quick win (0-30 days)", "rank": 10}]
    cols = [{"name": "revenue", "stats": {"min": 4.0, "max": 5000.0}}, {"name": "profit", "stats": {"min": -900.0, "max": 800.0}}, {"name": "margin", "stats": {"min": 0.1, "max": 0.5}}]
    questions = ["Which segment earns the most revenue?", "Is revenue growing?"]
    return {"goal": "Which segment earns the most revenue, and is it growing?", "findings": fs, "strategies": strategies, "strategy_summary": "", "critique": {"issues": []}, "cleaning_log": [],
            "data_gaps": [], "key_metrics": [], "context": {}, "profile": {"rows": 200, "cols": 6, "columns": cols}, "decisions": [],
            "plan": {"target": "revenue", "date_col": "when", "questions": questions, "objective": "o", "relevant_columns": ["revenue", "profit"], "explore": False, "analyses": [],
                     "derived": [{"name": "margin", "a": "revenue", "b": "cost", "kind": "ratio_of_sums"}]}}


def ids(cards):
    return [c["id"] for c in cards]


# ------------------------------------------------------------------ tier caps
def test_tier_caps_and_hypotheses_only_on_tiers_one_and_two():
    view = tiering.build_view(make_state())
    assert len(view["evidence"]) == tiering.EVIDENCE_MAX == 8
    assert len(view["headlines"]) == tiering.HEADLINES_MAX == 5 and view["headlines"][0]["id"] == view["evidence"][0]["id"]
    assert len(view["actions"]) <= tiering.ACTIONS_MAX == 3
    assert all(len(c["hypotheses"]) <= 2 and len(c["check_next"]) <= 2 and c["hypotheses"] for c in view["evidence"])
    assert view["appendix"] and all(not c["hypotheses"] and not c["check_next"] for c in view["appendix"])
    assert len(view["answers"]) == 2                                                                      # one answer per goal question
    eligible_but_over_cap = {f"f{k}" for k in range(9, 13)}
    assert eligible_but_over_cap <= set(ids(view["appendix"]))                                            # the overflow goes to the appendix, not away


def test_strategies_are_capped_merged_and_cite_findings():
    view = tiering.build_view(make_state())
    strat = view["strategies"]
    assert len(strat["strategies"]) == tiering.STRATEGIES_MAX == 4 and strat["dropped"] == 7 - 4
    growth = [s for s in strat["strategies"] if s["theme"] == "Growth"]
    assert len(growth) == 1 and growth[0]["evidence"] == ["f1", "f2", "f3"] and growth[0]["merged_from"] == ["s0", "s1", "s2"]      # three of one template became one
    assert all(s["evidence"] for s in strat["strategies"])                                                # each cites at least one finding id
    assert [f["title"] for f in strat["foundation"]] == ["Close data gaps"] and "\n" not in strat["foundation"][0]["line"]


# ------------------------------------------------------------------ merges
def test_robust_table_and_ratio_are_folded_into_the_parent_card():
    view = tiering.build_view(make_state())
    cards = view["evidence"] + view["appendix"]
    assert not [c for c in cards if c["tool"] == "robust_compare" or c["id"] in ("f100", "f102", "f103")]            # none of them is a card of its own
    g0 = next(c for c in cards if c["id"] == "f1")
    assert any("holds" in x for x in g0["checks"]) and len(g0["tables"]) == 1                              # one line for the median check; the table sits inside, collapsed
    assert [a["id"] for a in g0["also"]] == ["f103"] and {"f100", "f102", "f103"} <= set(g0["members"])    # revenue and its ratio margin: one card
    md = reporter._card_md(g0)
    assert md.count("<details>") == 1 and "Per-group figures" in "\n".join(md)


def test_non_significant_findings_go_to_one_checked_list():
    view = tiering.build_view(make_state())
    checked = {c["id"] for c in view["checked"]}
    assert checked == {"f104", "f105", "f106"}                                                             # every non-significant finding, and only those
    assert not checked & set(ids(view["evidence"] + view["appendix"]))
    assert all("\n" not in c["line"] for c in view["checked"])
    md = "\n".join(reporter._checked_section(view))
    assert md.count("\n- ") == 3 and "Checked, nothing found" in md


def test_composition_distribution_and_outliers_go_to_the_data_profile():
    view = tiering.build_view(make_state())
    cards = {c["id"] for c in view["evidence"] + view["appendix"]}
    assert not cards & {"f107", "f108", "f109", "f110", "f111"}
    prof = view["profile"]
    assert [x["id"] for x in prof["distributions"]] == ["f108"] and {x["id"] for x in prof["composition"]} == {"f107", "f109"}
    assert [x["id"] for x in prof["outliers"]] == ["f110", "f111"]                                         # one line per column


# ------------------------------------------------------------------ outlier fence
def test_lower_fence_below_the_column_minimum_is_not_shown():
    state = make_state()
    lines = {x["id"]: x["line"] for x in tiering.build_view(state)["profile"]["outliers"]}
    assert "-370" not in lines["f110"] and "744" in lines["f110"] and "upper fence" in lines["f110"]        # revenue never goes below 4
    assert "-50 to 80" in lines["f111"]                                                                    # profit goes down to -900: both fences are real


# ------------------------------------------------------------------ % rule
def trend(target, rel, agg="sum", periods=12, slope=-100.0):
    st = {"p": 0.3, "slope": slope, "relative_change": rel, "periods": periods, "freq": "MS", "agg": agg, "n": 500}
    text = f"**{target}** ({agg} per month) shows a weak trend (about {rel:+.0%} across the period, p=0.30)."
    return finding(1, "trend", True, "trend", stats=st, text=text, target=target, date="when")


def test_percent_change_is_replaced_by_the_absolute_change_for_a_ratio_or_a_series_crossing_zero():
    state = make_state()
    plain = trend("revenue", -0.44)
    assert "-44%" in tiering.scrub_change(plain, plain["text"], state)                                      # additive, always positive: the % stays
    ratio = trend("margin", -0.44, agg="ratio of sums")
    out = tiering.scrub_change(ratio, ratio["text"], state)
    assert "%" not in out.split("p=")[0] and "fitted change of -1,100 across the period" in out
    mixed = trend("profit", -0.44)                                                                          # profit is negative on some rows and positive on others
    assert "%" not in tiering.scrub_change(mixed, mixed["text"], state).split("p=")[0]
    crossing = trend("revenue", 3.5)                                                                        # the fitted line starts and ends on opposite sides of zero
    assert "%" not in tiering.scrub_change(crossing, crossing["text"], state).split("p=")[0]


def test_report_shows_tiers_and_collapsed_appendix():
    state = make_state()
    md = reporter.build_report(state, "Summary.", ["rec one", "rec two", "rec three", "rec four"], "rules")
    top, rest = md.split("## Key numbers" if "## Key numbers" in md else "## Evidence", 1)
    assert "**Headline findings**" in top and top.count("\n- ") <= 5 + 1 + 1 + 3                                 # headlines, plus the answers and actions, all short
    assert "rec four" not in md
    assert md.index("## Evidence") < md.index("## Checked, nothing found") < md.index("## Appendix")
    assert "<details>" in md.split("## Appendix")[1] and md.count("<details>") == md.count("</details>")
    assert "Possible explanations" not in md.split("## Appendix")[1]
