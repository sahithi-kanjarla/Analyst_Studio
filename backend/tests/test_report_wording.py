"""Report wording: segment-trend directions, counts that agree everywhere, one answer per question, strategies in the entity's own words, LLM limitations checked."""
import os

os.environ["LLM_PROVIDER"] = "none"

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from app import critic, insights, reporter, strategy, tools, wording  # noqa: E402
from app.clarify import named_in_goal  # noqa: E402


def seg_df(slopes, level=100.0, seed=0, months=24, per=40):
    """Monthly rows per group; each group's level moves by `slope` per month."""
    rng = np.random.default_rng(seed)
    rows = []
    for g, (start, slope) in slopes.items():
        for m in range(months):
            for _ in range(per // 4):
                rows.append((g, pd.Timestamp("2023-01-15") + pd.DateOffset(months=m), start + slope * m + rng.normal(0, 1)))
    return pd.DataFrame(rows, columns=["seg", "when", "score"])


def run_seg(df, tmp_path):
    return tools.segment_trend(df, {"charts_dir": tmp_path}, "score", "when", "seg")


# ------------------------------------------------------------------ 1. segment trends
def test_all_groups_moving_the_same_way_is_not_called_different_directions(tmp_path):
    f = run_seg(seg_df({"A": (100, 2.0), "B": (100, 1.0), "C": (100, 0.5)}), tmp_path)
    text = f["text"] + " " + f["title"] + " " + (f["action"] or "")
    assert f["stats"]["shape"] == "up" and wording.trend_shape(f["stats"]) == "up"
    assert "different directions" not in text and "other way" not in text and f["action"] is None
    assert "All groups move up" in f["text"]
    st = f["stats"]
    assert wording.lead_group(st) == "A" and "A" in wording.trend_headline(st, "seg", "score") and "rising fastest" in wording.trend_headline(st, "seg", "score")


def test_all_falling_names_the_fastest_faller(tmp_path):
    f = run_seg(seg_df({"A": (100, -2.0), "B": (100, -1.0), "C": (100, -0.5)}), tmp_path)
    h = wording.trend_headline(f["stats"], "seg", "score")
    assert f["stats"]["shape"] == "down" and "moving down" in h and h.split(";")[1].strip().startswith("A") and "falling fastest" in h


def test_opposite_signs_are_called_different_directions(tmp_path):
    f = run_seg(seg_df({"A": (100, 2.0), "B": (100, 0.0), "C": (100, -2.0)}), tmp_path)
    assert f["stats"]["shape"] == "mixed" and f["action"] and "different directions" in f["action"]
    assert "other way" in wording.trend_headline(f["stats"], "seg", "score")


def test_percentage_is_not_shown_when_the_start_is_near_zero_or_the_sign_flips(tmp_path):
    f = run_seg(seg_df({"Near": (0.3, 1.5), "Flip": (-20, 1.5), "Steady": (100, 0.5)}, seed=1), tmp_path)
    g = f["stats"]["groups"]
    assert g["Near"]["pct_ok"] is False and g["Flip"]["pct_ok"] is False and g["Steady"]["pct_ok"] is True
    assert "%" not in wording.change_phrase(g["Near"]) and "up" in wording.change_phrase(g["Near"]) and "from" in wording.change_phrase(g["Flip"])
    assert "%" in wording.change_phrase(g["Steady"])
    listed = f["text"].split("**Near**")[1].split(";")[0]
    assert "%" not in listed.split("(")[0]


# ------------------------------------------------------------------ 3. one source of truth for counts
def fake(tool, sig, surv=None, p=0.01, **params):
    st = {"p": p} if tool in wording.COMPARISON_TOOLS else {}
    if surv is not None:
        st["survives_correction"] = surv
    return {"id": f"f{abs(hash((tool, str(params), sig))) % 9999}", "tool": tool, "title": f"{tool} {params}", "params": params, "stats": st, "significant": sig, "text": "t", "action": None}


def state_with(findings, **kw):
    prof = {"rows": 5000, "cols": 6, "columns": [{"name": c, "role": "numeric", "missing_pct": 0.0, "unique": 50} for c in ("value", "tier", "when")]}
    plan = {"target": "value", "date_col": "when", "relevant_columns": ["value", "tier", "when"], "questions": [], "objective": "o", "analyses": [], "explore": False}
    return {"findings": findings, "profile": prof, "plan": plan, "goal": "g", "context": {}, "cleaning_log": [], "polarity": None, **kw}


def test_counts_agree_in_summary_key_numbers_and_critic():
    fs = [fake("group_compare", True, group="tier", target="value"), fake("group_compare", True, group="region", target="value", p=0.04),
          fake("group_compare", False, group="plan", target="value", p=0.5), fake("trend", True, target="value"), fake("robust_compare", True, group="tier", target="value"),
          fake("summary_table", None)]
    state = state_with(fs)
    crit = critic.review(state, None)
    state["critique"] = crit
    c = wording.counts(fs)
    assert c == {"analyses": 6, "tested": 4, "significant": 3, "reliable": 2, "comparisons": 3} or c["comparisons"] == 3
    insights.build_insights(state)
    summary, _ = reporter.rule_summary(state)
    km = next(m for m in insights.key_metrics(state, pd.DataFrame({"value": [1.0, 2.0], "when": pd.to_datetime(["2024-01-01", "2024-02-01"])})) if m["label"] == "Reliable findings")
    assert f"{c['analyses']} analyses were run" in summary and f"{c['tested']} of them were tested" in summary and f"{c['significant']} showed" in summary
    assert f"correcting for {c['comparisons']} comparisons" in summary
    assert km["value"] == f"{c['reliable']} of {c['tested']}" and f"{c['comparisons']} comparisons" in km["note"]
    assert any(f"{c['comparisons']} comparisons were run" in i["text"] for i in crit["issues"])


# ------------------------------------------------------------------ 2. every question answered, goal's segment first
def with_insights(state):
    state["critique"] = {"issues": []}
    insights.build_insights(state)
    return state


def test_every_planned_question_gets_one_line_and_the_goal_segment_comes_first():
    gc = lambda grp, sig, p: {**fake("group_compare", sig, group=grp, target="value", p=p), "stats": {"p": p, "kind": "numeric", "best": "x", "worst": "y", "values": {"x": 5.0, "y": 4.0}, "effect": 0.2, "effect_name": "eta_squared"}}
    fs = [gc("region", True, 0.001), gc("tier", False, 0.6), fake("trend", False, target="value", date="when")]
    fs[2]["stats"] = {"slope": 0.1, "relative_change": 0.02, "p": 0.6, "periods": 12, "freq": "MS", "n": 300}
    state = with_insights(state_with(fs, goal="Does value differ by tier and is it growing?"))
    state["plan"]["questions"] = ["What explains differences or changes in value?", "Which segments (region, tier) perform best and worst on value?", "Is value trending up or down over time?",
                                  "How is value, one of the KPIs you named, behaving?", "Something nobody ran an analysis for?"]
    qa = wording.question_answers(state, named_in_goal)
    assert [q for q, _ in qa] == state["plan"]["questions"] and all(a.endswith(".") and "\n" not in a for _, a in qa)
    seg = dict(qa)["Which segments (region, tier) perform best and worst on value?"]
    assert seg.index("tier") < seg.index("region")                                           # the segment named in the goal first
    assert "not differ reliably" in seg or "does not differ" in seg                           # a "no reliable difference" answer is still an answer
    assert "No reliable trend" in dict(qa)["Is value trending up or down over time?"]
    assert dict(qa)["Something nobody ran an analysis for?"].startswith(("Not answered", "No reliable driver"))
    md = reporter.write_report({**state, "strategies": [], "strategy_summary": "", "key_metrics": [], "data_gaps": [], "goal_answer": None}, None)[0]
    summary = md.split("## Key numbers")[0].split("## What we set out to answer")[0]
    assert "in one line each" in summary and summary.index("in one line each") < summary.index("analyses were run")


# ------------------------------------------------------------------ 4. strategies
def group_finding(target="margin_pct", group="Sub-Category"):
    f = fake("group_compare", True, group=group, target=target)
    f["stats"] = {"p": 0.0001, "kind": "numeric", "best": "Laptops", "worst": "Pens", "values": {"Laptops": 9.0, "Pens": 3.0}, "effect": 0.3, "effect_name": "eta_squared", "survives_correction": True}
    f["action"] = "act"
    return f


def strat_state(f, ctx=None, pol=None, **kw):
    state = state_with([f], context=ctx or {"industry_key": "ecommerce"}, polarity=pol, data_gaps=[], **kw)
    state["plan"]["relevant_columns"] = ["margin_pct", "Sub-Category", "Region"]
    state["plan"]["target"] = "margin_pct"
    return with_insights(state)


def text_of(cands):
    return " ".join(str(v) for c in cands for v in c.values() if isinstance(v, (str, list)))


def test_strategy_uses_the_entitys_own_noun_and_no_industry_words():
    cands = [c for c in strategy.candidates(strat_state(group_finding())) if c["kind"] == "data"]
    txt = text_of(cands)
    assert "sub-category Laptops" in txt and "Laptops customers" not in txt and "Pens customers" not in txt
    for word in ("contract", "usage", "channel", "pricing", "onboarding", "staffing"):
        assert word not in txt.lower()


def test_no_share_kpi_for_a_ratio_and_no_promotions_in_a_reduction_programme():
    f = group_finding(target="margin_pct")
    cands = [c for c in strategy.candidates(strat_state(f, pol={"margin_pct": "lower"})) if c["kind"] == "data"]
    assert cands and not any("Share of" in k for c in cands for k in c["kpis"])
    assert "reduction programme" in cands[0]["title"] and not strategy.UP_WORDS.search(" ".join(cands[0]["actions"]))
    additive = [c for c in strategy.candidates(strat_state(group_finding(target="revenue"), pol={"revenue": "higher"})) if c["kind"] == "data"]
    assert any("Share of total revenue" in k for c in additive for k in c["kpis"])
    assert strategy.pushes_both_ways("Reduce churn for tier Gold", "Design promotions for tier Gold") and not strategy.pushes_both_ways("Reduce churn", "Profile tier Gold")


def seg_state(tmp_path, slopes, seed=0):
    df = seg_df(slopes, seed=seed)
    f = run_seg(df, tmp_path)
    f["params"] = {"target": "score", "date": "when", "group": "seg"}
    f["id"] = "f1"
    state = state_with([f])
    state["plan"].update({"target": "score", "relevant_columns": ["score", "seg", "when"]})
    state["context"] = {}
    return with_insights(state), f


def test_strategy_never_calls_an_unreliable_segment_trend_declining(tmp_path):
    state, f = seg_state(tmp_path, {"A": (100, 2.0), "B": (100, 0.0), "C": (100, -0.01)}, seed=3)
    st = f["stats"]
    assert st["shape"] == "mixed" and wording.significant_direction(st, "C") is None and wording.significant_direction(st, "A") == "rising"
    cands = [c for c in strategy.candidates(state) if c["kind"] == "data"]
    txt = text_of(cands)
    assert cands and "A rising" in cands[0]["title"] and "falling" not in txt and "slipping" not in txt
    same, _ = seg_state(tmp_path, {"A": (100, 2.0), "B": (100, 1.0), "C": (100, 0.5)})
    assert not [c for c in strategy.candidates(same) if c["kind"] == "data"]               # everyone moves the same way: nothing to rebalance


# ------------------------------------------------------------------ 5. LLM limitation statements
def test_llm_limitations_that_contradict_the_findings_are_dropped():
    fs = [fake("group_compare", True, group="tier", target="value"), fake("group_compare", False, group="plan", target="value", p=0.5)]
    state = state_with(fs)
    critic.review(state, None)
    assert wording.contradicts("No statistically significant effects were found.", state)
    assert wording.contradicts("The dataset is small, so results are unstable.", state)                 # 5,000 rows
    assert wording.contradicts("There is no date column, so trends cannot be tested.", state)
    assert wording.contradicts("No multiple-comparison correction was applied.", state)
    assert wording.contradicts("A large amount of missing data limits the analysis.", state)
    assert wording.contradicts("All findings are significant.", state)
    assert wording.contradicts("Results are associations, not proof of cause.", state) is None
    assert wording.contradicts("Prices may have changed during the period.", state) is None

    class Fake:
        pass
    import app.critic as c
    orig = c.ask_json
    c.ask_json = lambda *a, **k: {"cautions": ["No statistically significant effects were found.", "Groups were not randomised, so differences may be confounded."], "followups": []}
    try:
        crit = c.review(state, Fake())
    finally:
        c.ask_json = orig
    texts = [i["text"] for i in crit["issues"] if i.get("source") == "llm"]
    assert texts == ["Groups were not randomised, so differences may be confounded."] and crit["dropped_notes"][0]["reason"].startswith("says nothing is significant")
