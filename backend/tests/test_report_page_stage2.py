"""The report as a page, stage 2 (charts): each answer card embeds 1 to 2 charts drawn from the facts it uses, the AI picks them from a closed list and writes a short caption whose figures
pass the fact-id check, the page never has more than MAX_CHARTS charts, and each action shows the chart or figure that supports it. Synthetic facts and fake AI output."""
import os
import re

os.environ["LLM_PROVIDER"] = "none"

from test_main_view import main_of, what_to_do  # noqa: E402
from test_report_page_stage1 import QL, by_entry, ranking, state_for, outside  # noqa: E402

from app import mainview, reportcharts, reporter, tiering  # noqa: E402

Q1 = "Which segment earns the most revenue?"
BARS = [[f"s{k}", 100.0 - 7 * k] for k in range(10)]
IMG = re.compile(r"!\[[^\]]*\]\((charts/[^)]+)\)")


def fake(charts_json=None, actions_json=None, calls=None):
    def ask(llm, system, user):
        (calls if calls is not None else []).append(user[:40])
        if "GOAL QUESTIONS AND THEIR CHARTS" in user:
            return charts_json or {}
        if "GOAL QUESTIONS AND THEIR FACTS" in user:
            return actions_json or {}
        return {"summary": "Fine [counts].", "recommendations": ["Do it."]}
    return ask


def charted(tmp_path, questions=(Q1,), rankings=None, picks=None):
    rs = rankings if rankings is not None else [ranking("r1", "segment", bars=BARS)]
    st = state_for(list(questions), rs, picks or {Q1: ["r1"]})
    st["workdir"] = str(tmp_path)
    return st


def go(st, llm=None):
    """Like `render`, but on the state itself, so the chart plan the report made can be read back."""
    st["report_view"] = tiering.build_view(st)
    return reporter.write_report({**st, "goal_answer": None} if llm is None else st, llm)[0]


def pngs(md):
    return IMG.findall(md)


# ------------------------------------------------------------------ 7. charts in the cards, drawn from the card's own facts
def test_a_card_embeds_a_chart_of_its_ranking_fact_and_the_file_is_drawn(tmp_path, monkeypatch):
    monkeypatch.setattr(reporter, "ask_json", fake())
    st = charted(tmp_path)
    md = go(st, object())
    head = main_of(md)
    assert len(pngs(head)) == 1 and "How to read this:" in head
    assert (tmp_path / pngs(head)[0]).is_file() and (tmp_path / pngs(head)[0]).stat().st_size > 1000
    plan = st["chart_plan"]["cards"][Q1]
    assert [c["fact"] for c in plan] == ["r1"]                                           # the chart is a view of the fact the card cites


def test_the_ai_chooses_among_listed_specs_and_an_invented_chart_id_is_ignored(tmp_path, monkeypatch):
    rs = [outside([by_entry("channel", 0.04, 0.05), by_entry("state", 0.01, 0.12)])]
    rs[0]["bars"] = None
    for b, rate in zip(rs[0]["by"], (0.05, 0.12)):
        b["bars"] = [[f"g{k}", rate - 0.005 * k] for k in range(5)]
    rs[0]["split"] = [{"value": "lost", "amount": 600.0, "share": 0.6}, {"value": "late", "amount": 300.0, "share": 0.3}]
    st = charted(tmp_path, [QL], rs, {QL: ["ro"]})
    listed = reportcharts.specs_for(["ro"], {}, {"ro": rs[0]}, st)
    assert [s["id"] for s in listed][0] == "ro:by:state" and {s["fact"] for s in listed} == {"ro"}          # the widest spread first; every spec names a real fact
    monkeypatch.setattr(reporter, "ask_json", fake({"charts": [{"question": 1, "chart": "ro:nonsense", "caption": "x"}, {"question": 1, "chart": "ro:split", "caption": "Each bar is a part of the loss."}]}))
    go(st, object())
    assert [c["id"] for c in st["chart_plan"]["cards"][QL]] == ["ro:split"]


def test_a_trend_fact_is_drawn_with_empty_months_left_blank(tmp_path):
    f = {"id": "f9", "tool": "trend", "stats": {"freq": "MS", "agg": "sum", "periods": 3}, "params": {"target": "revenue"}, "series": [["2024-01-01", 10.0], ["2024-02-01", 12.0], ["2024-05-01", 9.0]]}
    spec = reportcharts.specs_for(["f9"], {"f9": f}, {}, {})[0]
    assert spec["kind"] == "series" and spec["fact"] == "f9"
    rel = reportcharts.draw(spec, tmp_path / "charts")
    assert (tmp_path / rel).is_file() and (tmp_path / rel).stat().st_size > 1000


def test_ranking_facts_carry_at_most_ten_bars():
    import pandas as pd
    from app import summary
    df = pd.DataFrame({"g": [f"g{k % 15}" for k in range(300)], "y": [float(k) for k in range(300)]})
    r = summary._rank_additive(df, {}, "g", "y", 1)
    assert len(r["bars"]) == summary.BARS_MAX == 10 and r["bars"][0][1] >= r["bars"][-1][1]


# ------------------------------------------------------------------ 8. captions: at most 20 words, every figure passes the fact-id check
def plain_caption():
    """What code writes for the r1 chart when the AI caption is not usable: built from that chart's own numbers."""
    return reportcharts.fallback_caption(reportcharts.specs_for(["r1"], {}, {"r1": ranking("r1", "segment", bars=BARS)}, {})[0])


def caption_for(tmp_path, monkeypatch, text):
    monkeypatch.setattr(reporter, "ask_json", fake({"charts": [{"question": 1, "chart": "r1:bars", "caption": text}]}))
    st = charted(tmp_path)
    go(st, object())
    return st["chart_plan"]["cards"][Q1][0]["caption"]


def test_a_caption_with_a_figure_tied_to_its_fact_is_kept(tmp_path, monkeypatch):
    assert caption_for(tmp_path, monkeypatch, "Segment a is highest at 100 [r1]; compare the other bars with it.") == "Segment a is highest at 100; compare the other bars with it."


def test_a_caption_with_a_wrong_or_untagged_figure_is_replaced_by_a_plain_one(tmp_path, monkeypatch):
    plain = plain_caption()
    assert "s0" in plain and "s9" in plain
    assert caption_for(tmp_path, monkeypatch, "Segment a is highest at 9,999 [r1].") == plain
    assert caption_for(tmp_path, monkeypatch, "Segment a is highest at 100.") == plain                    # a figure with no fact id
    assert caption_for(tmp_path, monkeypatch, "Segment a is highest at 100 [r7].") == plain               # an id that does not exist


def test_a_caption_over_twenty_words_is_replaced(tmp_path, monkeypatch):
    long = " ".join(["Look"] * 21)
    assert caption_for(tmp_path, monkeypatch, long) == plain_caption()
    assert len(plain_caption().split()) <= reportcharts.CAPTION_WORDS


# ------------------------------------------------------------------ chart cap
def test_the_page_never_has_more_charts_than_the_cap(tmp_path, monkeypatch):
    qs = [f"Which {g} earns the most revenue?" for g in ("segment", "region", "channel", "state")]
    rs = [ranking(f"r{k}", g, bars=BARS) for k, g in enumerate(("segment", "region", "channel", "state"), 1)]
    picks = {q: [f"r{k}"] for k, q in enumerate(qs, 1)}
    monkeypatch.setattr(reportcharts, "MAX_CHARTS", 3)
    monkeypatch.setattr(reporter, "ask_json", fake({"charts": [{"question": k, "chart": f"r{k}:bars", "caption": "Bars show each group."} for k in range(1, 5)]}))
    st = charted(tmp_path, qs, rs, picks)
    md = go(st, object())
    assert len(set(pngs(md))) <= 3 and len(set(pngs(md))) == 3


def test_the_default_cap_is_eight_and_a_card_has_at_most_two():
    assert reportcharts.MAX_CHARTS == 8 and reportcharts.PER_CARD == 2


def test_a_card_gets_at_most_two_charts_even_if_the_ai_picks_more(tmp_path, monkeypatch):
    r = ranking("r1", "segment", bars=BARS)
    r2 = ranking("r2", "region", bars=BARS)
    monkeypatch.setattr(reporter, "ask_json", fake({"charts": [{"question": 1, "chart": "r1:bars", "caption": "a"}, {"question": 1, "chart": "r2:bars", "caption": "b"}]}))
    q = "Which segment and which region earn the most revenue?"
    st = charted(tmp_path, [q], [r, r2], {q: ["r1", "r2"]})
    md = go(st, object())
    assert len(pngs(main_of(md))) <= reportcharts.PER_CARD


# ------------------------------------------------------------------ 9. an action shows the chart or figure that supports it
def test_an_action_under_what_to_do_shows_the_chart_of_the_fact_it_rests_on(tmp_path, monkeypatch):
    good = "Review how segment a reaches 100 revenue before shifting budget [r1]."
    monkeypatch.setattr(reporter, "ask_json", fake({"charts": [{"question": 1, "chart": "r1:bars", "caption": "Bars show each group."}]}, {"actions": [{"question": 1, "text": good}]}))
    st = charted(tmp_path)
    st["strategies"] = []
    todo = what_to_do(main_of(go(st, object())))
    assert re.search(r"^1\. Review how segment a", todo, re.M) and len(pngs(todo)) == 1 and "Supported by:" in todo
    assert pngs(todo)[0] == st["chart_plan"]["cards"][Q1][0]["path"]                    # the same chart as in the card: not drawn twice, not counted twice


def test_an_action_resting_on_a_finding_without_a_chart_shows_its_figure(tmp_path, monkeypatch):
    monkeypatch.setattr(reporter, "ask_json", fake())
    st = charted(tmp_path)
    st["strategies"] = [{"id": "s0", "kind": "data", "theme": "Growth", "title": "Put more behind what works: g0 = a", "objective": "o0", "evidence": ["f1"], "impact": "High", "effort": "Medium",
                         "confidence": "high", "actions": ["a"], "how_to_test": "t", "kpis": ["k"], "risks": ["r"], "horizon": "Quick win (0-30 days)", "rank": 1}]
    st["summary_facts"]["rankings"][0]["group"] = "g0"
    todo = what_to_do(main_of(go(st, object())))
    assert "Put more behind what works" in todo and "Supported by:" in todo and not pngs(todo)


def test_an_action_resting_on_a_finding_with_its_own_chart_shows_that_chart(tmp_path, monkeypatch):
    monkeypatch.setattr(reporter, "ask_json", fake())
    st = charted(tmp_path)
    f1 = next(f for f in st["findings"] if f["id"] == "f1")
    f1["chart"] = "charts/group_compare_f1.png"
    st["strategies"] = [{"id": "s0", "kind": "data", "theme": "Growth", "title": "Put more behind what works: g0 = a", "objective": "o0", "evidence": ["f1"], "impact": "High", "effort": "Medium",
                         "confidence": "high", "actions": ["a"], "how_to_test": "t", "kpis": ["k"], "risks": ["r"], "horizon": "Quick win (0-30 days)", "rank": 1}]
    st["summary_facts"]["rankings"][0]["group"] = "g0"
    todo = what_to_do(main_of(go(st, object())))
    assert pngs(todo) == ["charts/group_compare_f1.png"]


def test_without_a_work_folder_the_report_has_no_charts_and_nothing_changes():
    st = state_for([Q1], [ranking("r1", "segment", bars=BARS)], {Q1: ["r1"]})
    md = go(st)
    assert not pngs(md) and "How to read this" not in md and "Supported by" not in md
