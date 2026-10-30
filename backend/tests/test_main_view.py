"""The main view of the report: one card per goal question, at most four actions each tied to a question (or an explicit 'No action suggested'), 4-6 key numbers; growth tables,
rate-outside facts, ids and statistics only in the collapsed details. Synthetic findings and fake AI output."""
import os
import re

os.environ["LLM_PROVIDER"] = "none"

import pandas as pd  # noqa: E402
from test_report_tiers import compare, finding, make_state  # noqa: E402

from app import mainview, reporter, summary, tiering  # noqa: E402

MAIN_LINES_MAX = 60
CARD_DETAILS = re.compile(r'<details class="card-details">.*?</details>\s*', re.S)
TAG = re.compile(r"\b[rfs]\d+\b")
Q1, Q2 = "Which segment earns the most revenue?", "Is revenue growing?"


def growth(group="segment", measure="revenue", focus=True, n=12):
    rows = [{"group": f"g{i}", "first": 100.0, "last": 100.0 + i, "change": float(i), "pct": None, "line": f"g{i} +{i} (from 100 to {100 + i}), not statistically reliable"} for i in range(n)]
    return {"id": f"rg_{group}_{measure}", "tool": "ranking", "kind": "growth", "basis": "total", "group": group, "measure": measure, "periods": ["2023", "2024"], "groups": rows, "focus": focus,
            "text": "t", "short": f"Biggest change in {measure} by {group}, 2023 vs 2024: g{n - 1} +{n - 1} (100 to {100 + n - 1}), not statistically reliable"}


OUTSIDE = {"id": "ro", "tool": "ranking", "kind": "outside_rate", "basis": "ratio", "group": "status", "measure": "revenue", "top": "ok", "bottom": "ok", "values": ["lost"], "outside": 900.0, "by": [],
           "focus": True, "text": "revenue outside ok: 900 (4.5% of all revenue); 40 of 200 rows (20.0%) are outside ok, which holds 80.0% of the rows in status.", "short": "revenue outside ok: 900 (4.5% of all revenue)"}


def state_with(extra_rankings=(), strategies=True):
    st = make_state()
    st["summary_facts"]["rankings"][0]["group"] = "g0"                              # the answer to question 1 splits by g0, the column the strategies' evidence splits by
    st["summary_facts"]["rankings"] += list(extra_rankings)
    if not strategies:
        st["strategies"] = []
    st["key_metrics"] = [{"label": "Total revenue", "value": "10,000", "note": ""}, {"label": "Period", "value": "2023-01 to 2024-12", "note": ""}]
    return st


def render(st, llm=None):
    st["report_view"] = tiering.build_view(st)
    return reporter.write_report({**st, "goal_answer": None}, llm)[0]


def main_of(md):
    return CARD_DETAILS.sub("", md.split("<details>", 1)[0])          # what a reader sees: the Details toggle inside each card is collapsed


def what_to_do(main):
    return main.split("### What to do")[1].split("## Key numbers")[0]


# ------------------------------------------------------------------ B. one card per goal question
def test_every_goal_question_has_a_card_with_a_trust_label_and_a_details_toggle():
    md = render(state_with())
    main = main_of(md)
    for n, q in enumerate((Q1, Q2), 1):
        assert f"**{n}. {q}**" in main
    assert md.split("<details>", 1)[0].count('<details class="card-details">') == 2          # one toggle inside each card
    assert "Trust: **High**" in main


def test_no_fact_id_and_no_line_is_shown_twice_in_the_main_view():
    main = main_of(render(state_with([growth(), OUTSIDE])))
    assert not TAG.findall(main)                                                  # ids, "(r17)" and "(f2)" stay in the details
    lines = [l.strip() for l in main.split("\n") if l.strip() and not l.startswith(("|", "#", "---"))]
    assert len(lines) == len(set(lines))
    assert not re.search(r"\bp\s*[=<]\s*\d|\beta\b|effect size|kruskal|welch", main, re.I)


def test_growth_tables_and_rate_outside_are_only_in_the_details():
    md = render(state_with([growth(), growth(measure="margin"), OUTSIDE]))
    main, rest = md.split("<details>", 1)
    assert "Growth in revenue by" not in main and "Rate outside the main value" not in main and "g3 +3" not in main
    assert "<summary>Growth in revenue by segment, 2023 vs 2024" in rest and "<summary>Rate outside the main value" in rest
    assert main.count("Biggest change") <= 1                                      # at most one growth line, the biggest change
    secondary = rest.split("<summary>Appendix")[1]
    assert "Growth in margin by segment" in secondary and "Growth in margin by segment" not in rest.split("<summary>Appendix")[0]       # a secondary measure goes to the appendix
    table = rest.split("<summary>Growth in revenue by segment")[1].split("</details>")[0]
    assert table.count("not statistically reliable") == 1                         # said once for the table, not on every line


def test_main_view_stays_under_the_line_cap():
    for extra in ([], [growth(), OUTSIDE]):
        assert len(main_of(render(state_with(extra))).split("\n")) < MAIN_LINES_MAX


def test_top_groups_are_at_most_three_and_figures_are_rounded():
    rows = []
    for k, (cat, total) in enumerate((("a", 7_512_000), ("b", 5_100_000), ("c", 2_000_000), ("d", 900_000), ("e", 300_000))):
        rows += [{"seg": cat, "revenue": total / 10}] * 10
    df = pd.DataFrame(rows)
    fact = summary._rank_additive(df, {"profile": {"columns": []}, "plan": {}}, "seg", "revenue", 1)
    assert fact["short"].count(",") == 2 and "7.5M" in fact["short"] and "5.1M" in fact["short"] and "2M" in fact["short"] and "900" not in fact["short"]
    assert summary.short(12_345) == "12.3K" and summary.short(999_999) == "1M" and summary.short(-2_400_000) == "-2.4M"


# ------------------------------------------------------------------ C. what to do
def test_every_goal_question_has_an_action_or_a_no_action_line():
    st = state_with()
    main = main_of(render(st))
    todo = what_to_do(main)
    n_actions = len(re.findall(r"^\d+\. ", todo, re.M))
    assert 1 <= n_actions <= mainview.ACTIONS_MAX
    for n in (1, 2):
        assert f"*(question {n})*" in todo or f"No action suggested for question {n}: " in todo
    assert "No action suggested for question 2: " in todo                          # the trend was not significant: nothing to act on


def test_a_small_effect_finding_is_not_a_headline_an_action_or_a_key_finding():
    st = state_with()
    small = compare(200, "g0", target="revenue", score=99.0)
    small["stats"].update(effect=0.02)                                            # eta squared 0.02: small
    st["findings"].append(small)
    st["strategies"].insert(0, {**st["strategies"][0], "id": "s_small", "theme": "Small", "title": "Act on a small effect", "evidence": ["f200"], "rank": 0})
    view = tiering.build_view(st)
    assert "f200" not in {h["id"] for h in view["headlines"]}
    assert "f200" not in view["gallery"]["key"] and "f200" in view["gallery"]["more"]
    md = render(st)
    assert "Act on a small effect" not in main_of(md)                             # never an action


def test_goal_named_findings_lead_the_dashboard_key_findings():
    st = state_with()
    other = compare(201, "unrelated", target="cost", score=500.0)
    st["findings"].append(other)
    key = tiering.build_view(st)["gallery"]["key"]
    assert key.index("f1") < key.index("f201") if "f201" in key else True       # g0 compares revenue, which the goal names
    assert mainview.gallery_order(st, top=1)["key"] != ["f201"]


# ------------------------------------------------------------------ AI sentences keep the fact-id check
def fake_ai(summary_json, actions_json, calls):
    def ask(llm, system, user):
        calls.append(user[:40])
        return actions_json if "GOAL QUESTIONS AND THEIR FACTS" in user else summary_json
    return ask


def test_so_what_sentences_keep_the_fact_id_check_and_are_one_per_card(monkeypatch):
    st = state_with()
    ok = "Segment a holds 62.5% of all revenue, so the mix depends on it [r1]. A second sentence."
    bad = "Revenue is 9,999 higher in segment b [r1]."
    calls = []
    monkeypatch.setattr(reporter, "ask_json", fake_ai({"summary": "Fine [counts].", "recommendations": ["Do it."],
                                                       "so_what": [{"question": 0, "text": ok}, {"question": 1, "text": bad}]}, {}, calls))
    md = render(st, object())
    main = main_of(md)
    assert "So what: Segment a holds 62.5% of all revenue, so the mix depends on it." in main and "A second sentence" not in main      # one sentence
    assert "9,999" not in main                                                    # a figure not in the fact it cites is dropped
    assert not TAG.findall(main)


def test_a_question_with_an_answer_but_no_action_gets_one_checked_ai_call(monkeypatch):
    st = state_with(strategies=False)
    calls = []
    good = "Review how segment a reaches 62.5% of all revenue before shifting budget [r1]."
    monkeypatch.setattr(reporter, "ask_json", fake_ai({"summary": "Fine [counts].", "recommendations": ["Do it."], "so_what": []},
                                                      {"actions": [{"question": 1, "text": good}, {"question": 2, "text": "Cut 12,345 from revenue [r1]."}]}, calls))
    todo = what_to_do(main_of(render(st, object())))
    assert "Review how segment a reaches 62.5% of all revenue before shifting budget." in todo and "*(question 1)*" in todo and "[r1]" not in todo
    assert sum("GOAL QUESTIONS" in c for c in calls) == 1                         # exactly one extra call
    assert "12,345" not in todo and "No action suggested for question 2: " in todo


def test_an_ai_action_without_a_fact_id_is_dropped(monkeypatch):
    st = state_with(strategies=False)
    monkeypatch.setattr(reporter, "ask_json", fake_ai({"summary": "Fine [counts].", "recommendations": ["Do it."]}, {"actions": [{"question": 1, "text": "Shift budget to segment a."}]}, []))
    todo = what_to_do(main_of(render(st, object())))
    assert "Shift budget" not in todo and "segment a is highest at 5,000" in todo      # Task A: a dropped action is replaced by the computed fact line, not by "No action suggested"
    assert any(d["decision"] == "report_actions" for d in st["decisions"])


def test_rules_only_mode_makes_no_extra_ai_call_and_still_says_why():
    todo = what_to_do(main_of(render(state_with(strategies=False))))
    assert "No action suggested for question 1: " in todo and "No action suggested for question 2: " in todo


def test_an_action_question_has_no_answer_card_only_the_what_to_do_section():
    st = state_with()
    st["goal"] = f"{Q1} What should we do?"
    st["summary_facts"].pop("picks")                                               # the picks are made from the new goal
    main = main_of(render(st))
    assert "What should we do?" not in main.split("### What to do")[0]             # no card, no 'Answered under...' stub, no details link for it
    assert "Answered under" not in main and "**1. " + Q1 + "**" in main
    todo = what_to_do(main)
    assert re.search(r"^1\. ", todo, re.M) and "*(question 2)*" not in todo        # its actions are listed without a question tag
