"""Selection engine: the cut picks what matters, rules take over when the AI is
down, charts are deduped, null results are grouped and the result is deterministic."""
import os

os.environ["LLM_PROVIDER"] = "none"

import json  # noqa: E402
import math  # noqa: E402

from app import curate  # noqa: E402

# ------------------------------------------------------------------ helpers

CONF_HIGH = {"level": "high", "label": "High", "reasons": ["OK"]}
CONF_LOW = {"level": "low", "label": "Low", "reasons": ["NS"]}


def _finding(i, tool="group_compare", sig=True, score=5.0, target="revenue",
             group="category", chart=None, headline=None, text="t",
             effect=0.2, effect_name="eta_squared", **kw):
    st = {"p": 0.001, "effect": effect, "effect_name": effect_name, "n": 500,
          "survives_correction": sig, **(kw.get("stats") or {})}
    kw.pop("stats", None)
    conf = CONF_HIGH if sig else CONF_LOW
    return {
        "id": f"f{i}", "tool": tool, "title": headline or f"{tool} {i}",
        "params": {"target": target, "group": group, **kw},
        "stats": st, "significant": sig, "text": text,
        "chart": chart or f"charts/f{i}.png",
        "chart_type": tool,
        "insight": {
            "headline": headline or f"{tool} {i}",
            "observation": text,
            "why_it_matters": "why",
            "score": score,
            "confidence": conf,
            "kpi_related": False,
        },
    }


def _strategy(i, evidence=None, impact="High"):
    return {
        "id": f"s{i}", "kind": "data", "theme": f"theme{i}",
        "title": f"Strategy {i}", "objective": f"Do thing {i}",
        "evidence": evidence or [f"f{i}"],
        "impact": impact, "effort": "Medium", "confidence": "high",
        "actions": ["a1"], "how_to_test": "t", "kpis": ["k"],
        "risks": ["r"], "horizon": "Quick win", "rank": i,
    }


def _state(findings=None, strategies=None, goal="Which category earns the most revenue?",
           target="revenue", metrics=None, dims=None):
    fs = findings if findings is not None else [_finding(1)]
    return {
        "goal": goal,
        "findings": fs,
        "strategies": strategies or [],
        "key_metrics": metrics or [],
        "plan": {"target": target, "date_col": "date",
                 "relevant_columns": ["revenue"], "analyses": []},
        "profile": {"rows": 1000, "cols": 6, "columns": []},
        "context": {},
        "dashboard": {"dims": dims or []},
    }


# ------------------------------------------------------------------ 1. dominant vs equal

def test_dominant_finding_keeps_fewer():
    """One dominant finding + many weak ones: the dominant is kept, most weak
    ones are cut.  Many equally strong findings: more survive."""
    # one dominant — the weak ones use a target NOT in the goal, so they are
    # not goal-relevant and can be cut.
    dominant = [_finding(1, score=20.0, effect=0.5)] + \
               [_finding(i, score=1.0, effect=0.01, target="clicks",
                         group=f"g{i}") for i in range(2, 22)]
    st = _state(findings=dominant,
                goal="Which category earns the most revenue?")
    res_dom = curate.curate(st)
    kept_dom = set()
    for v in res_dom["tabs"].values():
        kept_dom.update(v["kept"])

    # many equal — also non-goal so the comparison is fair
    equal = [_finding(i, score=10.0, effect=0.15, target="clicks",
                      group=f"g{i}") for i in range(1, 22)]
    st2 = _state(findings=equal,
                 goal="Which category earns the most revenue?")
    res_eq = curate.curate(st2)
    kept_eq = set()
    for v in res_eq["tabs"].values():
        kept_eq.update(v["kept"])

    # finding ids only (not chart ids)
    finding_dom = {i for i in kept_dom if i.startswith("f")}
    finding_eq = {i for i in kept_eq if i.startswith("f")}
    assert len(finding_dom) < len(finding_eq), \
        f"dominant kept {len(finding_dom)} findings, equal kept {len(finding_eq)}"
    assert "f1" in finding_dom, "the dominant finding must survive"


# ------------------------------------------------------------------ 2. 60-column ceiling

def test_sixty_column_ceiling():
    """A 60-column synthetic file produces many findings; the ceiling holds."""
    findings = [_finding(i, target=f"col{i}", group=f"dim{i % 5}",
                         score=3.0 + (60 - i) * 0.1, effect=0.08)
                for i in range(1, 61)]
    st = _state(findings=findings,
                goal="How does each dimension affect the columns?")
    res = curate.curate(st)
    for key, v in res["tabs"].items():
        tab, kind = key.split(":")
        n_candidates = len([c for c in res["candidates"] if c["kind"] == kind])
        ceiling = curate._ceiling(n_candidates)
        # null-result one-liners count as kept but do not break the ceiling
        non_null = [i for i in v["kept"]
                    if not any(c["id"] == i and c.get("null_result")
                               for c in res["candidates"])]
        assert len(non_null) <= ceiling, \
            f"{key}: {len(non_null)} kept > ceiling {ceiling} (of {n_candidates})"


# ------------------------------------------------------------------ 3. unknown / repeated ids

def test_ai_answer_cleaned():
    """Unknown ids and duplicates in the AI response are dropped silently."""
    candidates = [
        {"id": "f1", "kind": "finding", "title": "a"},
        {"id": "f2", "kind": "finding", "title": "b"},
        {"id": "f3", "kind": "finding", "title": "c"},
    ]
    valid = {c["id"] for c in candidates}

    # simulate an AI answer with repeats and unknowns
    class FakeLLM:
        pass

    import types
    from unittest.mock import patch

    def fake_ask(llm, system, user, **kw):
        return {"ranked": [
            {"id": "f2", "reason": "r"},
            {"id": "f2", "reason": "dup"},      # duplicate
            {"id": "UNKNOWN", "reason": "bad"},  # unknown
            {"id": "f1", "reason": "r"},
            {"id": "f3", "reason": "r"},
        ]}

    with patch("app.curate.ask_json", fake_ask):
        ids, reason = curate.ai_rank(FakeLLM(), candidates, "goal")

    assert reason == ""
    assert ids == ["f2", "f1", "f3"], f"got {ids}"
    assert len(ids) == len(set(ids)), "duplicates not removed"
    assert all(i in valid for i in ids), "unknown ids not removed"


# ------------------------------------------------------------------ 4. AI down → rules + pill

def test_ai_down_rules_and_pill():
    """When the AI is unavailable the engine falls back to rules-only ordering
    and records the reason."""
    findings = [_finding(1, score=10), _finding(2, score=5, group="g2")]
    st = _state(findings=findings)
    res = curate.curate(st, llm=None)

    assert res["ai_reason"], "should record why AI was not used"
    assert res["ai_ranking"] == [], "no AI ranking"
    # items are still scored and kept
    assert len(res["candidates"]) > 0
    for v in res["tabs"].values():
        assert isinstance(v["kept"], list)


# ------------------------------------------------------------------ 5. goal finding always survives

def test_goal_finding_survives():
    """A finding that answers the stated goal is never cut, even if its score
    is the lowest."""
    # f1 answers the goal (mentions 'revenue' and 'category'), very low score
    goal_f = _finding(1, score=0.1, effect=0.001, target="revenue",
                      group="category")
    # many high-score findings on other dimensions
    others = [_finding(i, score=20.0, effect=0.5, target="revenue",
                       group=f"other{i}")
              for i in range(2, 25)]
    st = _state(findings=[goal_f] + others,
                goal="Which category earns the most revenue?")
    res = curate.curate(st)
    all_kept = set()
    for v in res["tabs"].values():
        all_kept.update(v["kept"])
    assert "f1" in all_kept, "goal-answering finding must survive the cut"


# ------------------------------------------------------------------ 6. null results never get their own chart

def test_null_results_no_chart():
    """A 'no reliable difference' finding never gets its own chart kept."""
    null_f = _finding(1, sig=False, score=1.0,
                      headline="revenue does not differ reliably across category",
                      chart="charts/null.png")
    real_f = _finding(2, sig=True, score=10.0, group="region")
    st = _state(findings=[null_f, real_f])
    res = curate.curate(st)
    chart_kept = set()
    for key, v in res["tabs"].items():
        if key.endswith(":chart"):
            chart_kept.update(v["kept"])
    # the null finding's chart should not be kept
    assert "ch_f1" not in chart_kept, "null-result chart should not be kept"


# ------------------------------------------------------------------ 7. same finding is never two charts

def test_no_duplicate_charts():
    """Two findings with the same (chart_type, measure, dimension) produce
    only one chart candidate."""
    f1 = _finding(1, tool="group_compare", target="revenue", group="category",
                  score=10.0)
    f2 = _finding(2, tool="group_compare", target="revenue", group="category",
                  score=8.0)
    st = _state(findings=[f1, f2])
    cands = curate.build_candidates(st)
    chart_cands = [c for c in cands if c["kind"] == "chart"]
    keys = [c["chart_key"] for c in chart_cands]
    assert len(keys) == len(set(keys)), f"duplicate chart keys: {keys}"


# ------------------------------------------------------------------ 8. deterministic

def test_deterministic():
    """The same state gives the same selection twice."""
    findings = [_finding(i, score=10.0 - i, group=f"g{i}") for i in range(1, 16)]
    st = _state(findings=findings)
    r1 = curate.curate(st)
    r2 = curate.curate(st)
    assert r1["kept_ids"] == r2["kept_ids"]
    assert r1["more_ids"] == r2["more_ids"]
    assert r1["left_out_count"] == r2["left_out_count"]
    for key in r1["tabs"]:
        assert r1["tabs"][key] == r2["tabs"][key], f"tab {key} differs"


# ------------------------------------------------------------------ extra: robustness attached

def test_robust_attached():
    """A robust_compare finding maps to the parent group_compare finding."""
    parent = _finding(1, tool="group_compare", target="revenue", group="cat")
    robust = _finding(2, tool="robust_compare", target="revenue", group="cat",
                      chart=None)
    robust["chart"] = None
    robust["chart_type"] = "robust_compare"
    st = _state(findings=[parent, robust])
    res = curate.curate(st)
    assert res["robust_map"].get("f2") == "f1"
    # robust finding is not a standalone candidate
    finding_ids = {c["id"] for c in res["candidates"] if c["kind"] == "finding"}
    assert "f2" not in finding_ids


# ------------------------------------------------------------------ extra: importance scoring

def test_importance_goal_beats_non_goal():
    """A goal-relevant item scores higher than an identical non-goal item."""
    base = {"base_score": 3.0, "effect_size": 0.1, "effect_label": "small",
            "share_of_total": 0.05, "reliability": "medium",
            "null_result": False, "caution": False}
    s_goal = curate.importance({**base, "goal_relevant": True})
    s_no = curate.importance({**base, "goal_relevant": False})
    assert s_goal > s_no


def test_importance_caution_always_high():
    """A data caution has a very high importance score."""
    c = {"base_score": 0.5, "effect_size": 0.0, "effect_label": None,
         "share_of_total": 0.0, "reliability": "descriptive",
         "null_result": False, "caution": True, "goal_relevant": False}
    assert curate.importance(c) > 4.0


# ============================================================ Part B2 tests
# apply_curation, report content, null results, left-out count, More list

from app import tiering  # noqa: E402


def _view_and_curation():
    """A small state with 6 findings, 2 strategies, and curation decisions."""
    fs = [
        _finding(1, score=20.0, effect=0.5),
        _finding(2, score=15.0, effect=0.3, group="region"),
        _finding(3, score=10.0, effect=0.2, group="channel"),
        _finding(4, sig=False, score=1.0,
                 headline="revenue does not differ reliably across segment",
                 group="segment"),
        _finding(5, score=3.0, effect=0.05, group="weekday"),
        _finding(6, score=2.0, effect=0.02, group="month"),
    ]
    strats = [_strategy(1, impact="High"), _strategy(2, impact="Medium"),
              _strategy(3, impact="Low")]
    st = _state(findings=fs, strategies=strats)
    view = tiering.build_view(st)

    curation = curate.curate(st)
    return view, curation, st


def test_apply_curation_partitions_evidence_and_more():
    """After apply_curation, evidence contains only kept findings; more_findings
    holds the overflow items."""
    view, curation, _ = _view_and_curation()
    all_before = set(c["id"] for c in view["evidence"] + view["appendix"])
    tiering.apply_curation(view, curation)

    kept_ids = set(c["id"] for c in view["evidence"])
    more_ids = set(c["id"] for c in view.get("more_findings", []))
    # every id was either kept, more, or left out — none lost
    assert kept_ids | more_ids <= all_before
    # kept and more are disjoint
    assert not (kept_ids & more_ids)


def test_report_findings_has_a_floor():
    """The report tab always has at least MIN_REPORT_FINDINGS cards."""
    view, curation, _ = _view_and_curation()
    tiering.apply_curation(view, curation)
    rf = view.get("report_findings", [])
    assert len(rf) >= tiering.MIN_REPORT_FINDINGS


def test_report_never_empty_on_small_file():
    """Even one finding is enough — the report has real content."""
    fs = [_finding(1, score=5.0)]
    st = _state(findings=fs)
    view = tiering.build_view(st)
    curation = curate.curate(st)
    tiering.apply_curation(view, curation)
    assert len(view["evidence"]) >= 1
    assert len(view.get("report_findings", view["evidence"])) >= 1


def test_null_result_has_no_chart_in_display():
    """No tab shows a chart for a null result finding."""
    null_f = _finding(1, sig=False, score=1.0,
                      headline="revenue does not differ reliably across category",
                      chart="charts/null.png")
    real_f = _finding(2, sig=True, score=10.0, group="region")
    st = _state(findings=[null_f, real_f])
    view = tiering.build_view(st)
    curation = curate.curate(st)
    tiering.apply_curation(view, curation)
    # null results must not appear as full cards in evidence or report_findings
    for c in view["evidence"] + view.get("report_findings", []):
        assert c["id"] != "f1", "null result must not be a card in evidence"
    # null results as one-line entries only
    null_lines = view.get("null_lines", [])
    null_ids = {nl["id"] for nl in null_lines}
    assert "f1" in null_ids or not any(
        c["id"] == "f1" for c in view.get("more_findings", []))


def test_left_out_count_in_view():
    """The view carries a left_out_count for the Data tab."""
    view, curation, _ = _view_and_curation()
    tiering.apply_curation(view, curation)
    assert "left_out_count" in view
    assert isinstance(view["left_out_count"], int)
    assert view["left_out_count"] >= 0


def test_more_findings_reachable():
    """Overflow findings are in the more_findings list, accessible to the user
    and the Lens agent."""
    fs = [_finding(i, score=20.0 - i, group=f"g{i}") for i in range(1, 20)]
    st = _state(findings=fs)
    view = tiering.build_view(st)
    curation = curate.curate(st)
    tiering.apply_curation(view, curation)
    more = view.get("more_findings", [])
    kept = {c["id"] for c in view["evidence"]}
    more_set = {c["id"] for c in more}
    # all more items are real findings, none are kept
    assert not (kept & more_set)
    # every non-left-out finding is either in evidence or more or appendix
    all_finding_ids = {f"f{i}" for i in range(1, 20)}
    accounted = kept | more_set | {c["id"] for c in view.get("appendix", [])}
    assert all_finding_ids <= accounted


def test_strategies_more_list():
    """Overflow strategies end up in strategies.more, not lost."""
    view, curation, _ = _view_and_curation()
    tiering.apply_curation(view, curation)
    strats = view.get("strategies", {})
    kept = {s["id"] for s in strats.get("strategies", [])}
    more = {s["id"] for s in strats.get("more", [])}
    assert not (kept & more)


def test_apply_curation_empty_is_noop():
    """An empty curation dict leaves the view unchanged."""
    view, _, _ = _view_and_curation()
    ev_before = list(view["evidence"])
    tiering.apply_curation(view, {})
    assert view["evidence"] == ev_before
    tiering.apply_curation(view, {"tabs": {}})
    assert view["evidence"] == ev_before
