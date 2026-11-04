"""G2 Report polish: the lead figure, the plain line, the variety of findings and the action lines are all built by code from the saved findings.
Fake AI only (the pipeline runs with no model); nothing here changes a figure, a trust label or a check."""
import json
import os
import re
from pathlib import Path

os.environ["LLM_PROVIDER"] = "none"

import pytest  # noqa: E402

from app import report_lead as rl  # noqa: E402
from app import runner, tiering  # noqa: E402
from tests.fixtures_bakery import bakery, boutique  # noqa: E402

FRONT = Path(__file__).resolve().parents[2] / "frontend" / "src"
FORBIDDEN = re.compile(r"=|\beffect\b|\bp\s*[<>]|\bp-value|\brho\b|\beta\b|compared with the lowest|\d\s?[×x]\b", re.I)


def group_finding(**over):
    f = {"id": "f1", "tool": "group_compare", "title": "Amount by Sub-Category", "text": "Sub-Category = Tables averages 43.7x the Amount of Skirt (large effect)",
         "params": {"target": "Amount", "group": "Sub-Category"},
         "stats": {"kind": "numeric", "best": "Tables", "worst": "Skirt", "values": {"Tables": 1330.0, "Printers": 801.0, "Skirt": 30.4}, "total_groups": 17, "effect": 0.4, "effect_name": "eta_squared"}}
    f.update(over)
    return f


# ---- 1. the lead figure
def test_lead_is_the_top_groups_own_value_not_the_ratio():
    lead = rl.lead_for(group_finding(), "large")
    assert lead["value"] == "1,330"
    assert "Tables" in lead["label"] and "average" in lead["label"]
    assert "×" not in lead["value"] and "lowest" not in lead["label"]


def test_the_ratio_stays_inside_the_details_only():
    lead = rl.lead_for(group_finding(), "large")
    assert lead["ratio"] == "The top group is 43.8 times the lowest."
    assert lead["ratio"] not in (lead["value"], lead["label"], lead["line"])


def test_trend_lead_is_the_change_and_carries_the_monthly_series():
    f = {"id": "f2", "tool": "trend", "params": {"target": "Sales", "date": "Date"}, "stats": {"first": 100.0, "last": 150.0},
         "series": [["2018-01-01", 100.0], ["2018-02-01", 120.0], ["2018-03-01", 150.0]]}
    lead = rl.lead_for(f)
    assert lead["value"] == "+50%" and lead["sign"] == 1
    assert [p["label"] for p in lead["series"]] == ["Jan", "Feb", "Mar"]


def test_rate_lead_is_a_percentage():
    f = group_finding(tool="rate_compare", stats={"kind": "binary", "best": "A", "worst": "B", "values": {"A": 0.25, "B": 0.1}})
    assert rl.lead_for(f)["value"] == "25%"


# ---- 2. the plain line
def test_line_names_both_groups_with_no_statistics():
    line = rl.lead_for(group_finding(), "large")["line"]
    assert line == "Tables averages 1,330; Skirt averages 30.4."
    assert not FORBIDDEN.search(line)


@pytest.mark.parametrize("text", [
    "Category = Electronics averages 3.5x the Amount of Clothing (average 540 per record vs 152 for Clothing) (large effect)",
    "Amount rises as Quantity rises (rho = +0.38)",
    "The difference is statistically significant (ANOVA p=0.006, eta²=0.007, negligible effect).",
])
def test_fallback_line_is_clean_or_empty(text):
    assert not rl._BAD.search(rl.plain_line(text))


def test_effect_labels_become_plain_words_in_the_details():
    assert rl.lead_for(group_finding(), "large")["difference"] == "a big difference"
    assert rl.lead_for(group_finding(), "negligible")["difference"] == "a very small difference"


def test_clean_sentences_drop_the_ones_with_statistics():
    out = rl.clean_sentences("Tables is where Amount is strongest. The gap is real (p = 0.01). Skirt lags.")
    assert out == "Tables is where Amount is strongest. Skirt lags."


# ---- 3. variety
def card(i, tool, measure):
    return {"id": f"f{i}", "tool": tool, "title": f"t{i}"}, {"id": f"f{i}", "tool": tool, "params": {"target": measure}, "stats": {}}


def pick(spec, n):
    cards, found = zip(*[card(i, t, m) for i, (t, m) in enumerate(spec)])
    return rl.diversify(list(cards), {f["id"]: f for f in found}, n)


def test_themes_come_before_repeats_and_no_same_measure_and_kind_twice():
    out = pick([("group_compare", "Amount"), ("group_compare", "Amount"), ("group_compare", "Profit"), ("trend", "Amount"), ("scatter", "Amount"), ("pareto", "Amount")], 4)
    assert len({rl.theme_of(c["tool"]) for c in out}) == 4          # a leader, a time finding, a link and a concentration, once each
    assert "f1" not in [c["id"] for c in out]                       # the second Amount comparison is the duplicate


def test_a_duplicate_is_shown_only_when_nothing_else_exists():
    assert [c["id"] for c in pick([("group_compare", "Amount")] * 3, 3)] == ["f0", "f1", "f2"]


def test_a_trend_is_drawn_with_the_dashboards_column_chart():
    page = (FRONT / "components" / "ReportPage.tsx").read_text(encoding="utf-8")
    assert 'import { Columns } from "./charts"' in page and "L.series.length > 1" in page
    assert "<Columns" in (FRONT / "components" / "BIDashboard.tsx").read_text(encoding="utf-8")      # the same component draws the Dashboard's per-month chart


# ---- 5. the action lines
def test_action_has_a_short_bold_lead_one_line_and_the_rest_for_based_on():
    a = rl.split_action("Put more behind what works: Segment = Enterprise: Grow overall revenue by investing where it is strongest while diagnosing why Consumer lags. Check the margin first.")
    assert len(a["lead"].split()) <= 12
    assert a["line"] == "Grow overall revenue by investing where it is strongest while diagnosing why Consumer lags."
    assert "Segment = Enterprise" in a["rest"] and "Check the margin first." in a["rest"]


def test_a_long_lead_is_cut_to_twelve_words_and_the_spill_moves_to_the_rest():
    a = rl.split_action("one two three four five six seven eight nine ten eleven twelve thirteen fourteen: Do the thing.")
    assert len(a["lead"].split()) == 12 and "thirteen fourteen" in a["rest"]


def test_split_loses_no_word():
    text = "Lead words here: middle bit: First sentence is here, with a long tail that keeps going and going and going well past twenty four words in total length. Second one."
    a = rl.split_action(text)
    got = " ".join([a["lead"], a["rest"], a["line"]])
    for w in re.findall(r"[A-Za-z]+", text):
        assert w in got


# ---- the whole pipeline, as the page receives it (fake AI)
@pytest.mark.parametrize("make", [bakery, boutique])
def test_report_body_has_no_equals_effect_words_or_ratio_lead(tmp_path, monkeypatch, make):
    monkeypatch.setenv("RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("GLOSSARY_PATH", str(tmp_path / "g.json"))
    rid = runner.create_run("", [("sales.csv", make().to_csv(index=False).encode())])
    runner._execute(rid)
    wd = runner.run_dir(rid)
    if (runner.read_status(rid) or {}).get("status") == "needs_input":
        (wd / "answers.json").write_text(json.dumps({"answers": {}, "skip": True, "notes": ""}))
        runner._execute(rid)
    res = json.loads((wd / "result.json").read_text(encoding="utf-8"))
    view = res.get("report_view") or tiering.build_view(res)
    if res.get("curation"):
        tiering.apply_curation(view, res["curation"])
    rl.apply(view, res["findings"])
    for a in view["page"]["actions"]:
        assert len(a["split_lead"].split()) <= 12
    cards = view.get("report_findings") or []
    assert cards, "the curated Report has findings"
    by_id = {f["id"]: f for f in res["findings"]}
    for c in cards:
        lead = c["lead"]
        for field in ("value", "label", "line", "why"):
            assert not FORBIDDEN.search(lead[field]), (c["id"], field, lead[field])
        for t in lead["merged"]:
            assert not FORBIDDEN.search(t), t
    keys = [(rl._measure(by_id[c["id"]]), rl._kind(by_id[c["id"]])) for c in cards]
    themes = {rl.theme_of(c["tool"]) for c in cards}
    assert len(set(keys)) == len(keys) or len(themes) >= min(3, len(cards))     # a repeat only appears next to real variety, or when nothing else existed


def test_a_weekday_or_month_finding_draws_every_period_and_the_line_starts_with_a_capital():
    vals = {d: float(i + 1) for i, d in enumerate(["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])}
    f = {"id": "f3", "tool": "seasonality", "params": {"target": "Sales"}, "stats": {"best": "Sun", "worst": "Mon", "values": vals, "cycle": "weekday"}}
    lead = rl.lead_for(f)
    assert [p["label"] for p in lead["series"]] == list(vals) and lead["bars"] == []
    assert rl.lead_for({"tool": "scatter", "params": {}, "stats": {"rho": 0.5, "x": "a", "y": "revenue"}})["line"].startswith("R")
