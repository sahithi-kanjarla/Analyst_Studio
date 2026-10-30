"""The report as a page, stage 1 (bugs and content): a Details toggle inside each answer card (no links), one line per goal-named column group, a trend question that says when the data is too
short, the breakdown with the largest spread for a 'where is it worst' question, 'Look into <group> first' instead of a raw fact line, the loss measure among the tiles, and one ordering for
the dashboard and the evidence list. Synthetic findings and fake AI output."""
import os
import re

os.environ["LLM_PROVIDER"] = "none"

from test_main_view import OUTSIDE, TAG, fake_ai, main_of, render, what_to_do  # noqa: E402
from test_report_tiers import compare, finding, make_state  # noqa: E402

from app import mainview, reporter, summary, tiering  # noqa: E402


def ranking(rid, group, measure="revenue", basis="total", lo=60.0, hi=100.0, **extra):
    spread = summary.spread_of(lo, hi)
    return {"id": rid, "tool": "ranking", "basis": basis, "group": group, "measure": measure, "top": "a", "bottom": "z", "focus": True, "range": f"{lo:.0f} to {hi:.0f}", **spread,
            "text": f"By total {measure}, {group} a is highest at {hi:.0f} and {group} z is lowest at {lo:.0f}.", "short": f"Top 3 {group} by total {measure}: {group} a {hi:.0f}", **extra}


def state_for(questions, rankings, picks, extra_findings=()):
    st = make_state()
    st["goal"] = " ".join(questions)
    st["plan"]["questions"] = list(questions)
    st["summary_facts"] = {"rankings": rankings, "picks": picks, "source": "rules"}
    st["findings"] += list(extra_findings)
    st["strategies"] = []
    st["key_metrics"] = [{"label": "Total revenue", "value": "10,000", "note": ""}, {"label": "Period", "value": "2023-01 to 2024-12", "note": ""}]
    return st


def card_of(md, n, q):
    """The visible lines of answer card n: from its question up to its Trust line."""
    return [l.strip() for l in main_of(md).split(f"**{n}. {q}**")[1].split("Trust:")[0].split("\n") if l.strip()]


# ------------------------------------------------------------------ 1. the Details toggle: no hash link anywhere
def test_the_report_has_no_hash_links_and_each_card_has_an_in_place_details_toggle():
    md = render(state_for(["Which segment earns the most revenue?", "Is revenue growing?"], [ranking("r1", "segment")], {"Which segment earns the most revenue?": ["r1"], "Is revenue growing?": ["f105"]}))
    assert not re.search(r"\]\(#|href=[\"']#|<a id=|<a name=", md)                          # nothing that navigates or changes the address
    head = md.split("<details>", 1)[0]
    assert head.count('<details class="card-details">') == 2 and head.count("<summary>Details</summary>") == 2
    first = head.split('<details class="card-details">')[1].split("</details>")[0]
    assert "Full answer:" in first and not TAG.findall(first) and not re.search(r"\bp\s*[=<]", first)        # plain words inside: no ids, no p-values


# ------------------------------------------------------------------ 2. card content
Q3 = "Which categories, channels and states earn the most revenue?"


def test_a_question_naming_three_columns_shows_one_line_for_each_even_when_one_fact_was_cited():
    rs = [ranking("r1", "category"), ranking("r2", "channel"), ranking("r3", "state"), ranking("r4", "colour")]            # colour is not named
    md = render(state_for([Q3], rs, {Q3: ["r1"]}))
    lines = [l for l in card_of(md, 1, Q3) if l.startswith("Top")]
    assert [l.split()[2] for l in lines] == ["category", "channel", "state"] and "colour" not in "\n".join(lines)


def test_a_question_naming_five_columns_shows_four_lines():
    q = "Which categories, channels, states, regions and cities earn the most revenue?"
    rs = [ranking(f"r{k}", g) for k, g in enumerate(["category", "channel", "state", "region", "city"], 1)]
    lines = [l for l in card_of(render(state_for([q], rs, {q: ["r1"]})), 1, q) if l.startswith("Top")]
    assert len(lines) == mainview.GROUP_LINES == 4


def trend_fact(periods, coverage, freq="MS", values=None):
    vals = values or [100.0 + 10 * k for k in range(periods)]
    f = finding(105, "trend", True, "revenue is trending up (+30% over the period)", stats={"p": 0.01, "slope": 10.0, "periods": periods, "freq": freq, "agg": "sum", "n": 500}, target="revenue", date="when")
    f["series"] = [[f"2024-{k + 1:02d}-01", v] for k, v in enumerate(vals)]
    f["date_coverage"] = coverage
    return f


def trend_card(fact):
    q = "How is revenue changing over time?"
    st = state_for([q], [ranking("r1", "segment")], {q: ["f105"]})
    st["findings"] = [f for f in st["findings"] if f["id"] != "f105"] + [fact]
    return card_of(render(st), 1, q)


def test_a_trend_question_shows_the_monthly_totals_and_calls_a_short_series_too_short():
    short = trend_card(trend_fact(4, 0.95))
    assert any(l.startswith("Monthly totals of revenue: 2024-01 100") for l in short)
    assert any("too short to call a trend" in l and "only 4 months" in l for l in short)
    assert not any("trending up" in l for l in short)                                    # a trend that cannot be called is not stated


def test_a_trend_question_with_poor_date_coverage_says_so_in_plain_words():
    lines = trend_card(trend_fact(12, 0.5))
    assert any("too short to call a trend" in l and "50% of the rows" in l for l in lines)


def test_a_long_series_with_good_coverage_is_called_a_trend_and_shows_its_totals():
    lines = trend_card(trend_fact(12, 0.95))
    assert any("trending up" in l for l in lines) and any(l.startswith("Monthly totals of revenue: 2024-01") and "12 months" in l for l in lines)
    assert not any("too short" in l for l in lines)


# ------------------------------------------------------------------ 3. 'where is it worst': the breakdown with the largest spread
QL = "Where is revenue lost and how much do we lose?"


def outside(by):
    return {**OUTSIDE, "head": "revenue outside ok: 900 (4.5% of all revenue)", "by": by, "rate_amount": 0.045, "rate_rows": 0.2, "largest": "lost", "largest_share": 0.6}


def by_entry(group, lo, hi):
    return {"group": group, "range": f"{lo:.1%} to {hi:.1%}", "clause": f"highest rate by {group}: {group} a {hi:.0%}", **summary.spread_of(lo, hi, rate=True)}


def loss_card(by):
    return card_of(render(state_for([QL], [outside(by)], {QL: ["ro"]})), 1, QL)


def test_the_loss_breakdown_with_the_largest_spread_is_shown():
    lines = loss_card([by_entry("channel", 0.040, 0.050), by_entry("state", 0.010, 0.120), by_entry("category", 0.030, 0.045)])
    text = "\n".join(lines)
    assert lines[0].startswith("revenue outside ok: 900")
    assert "Biggest spread is across state (1.0% to 12.0%)" in text and "channel" not in text and "category" not in text and "no real difference" not in text


def test_when_every_breakdown_is_similar_the_line_says_there_is_no_real_difference():
    text = "\n".join(loss_card([by_entry("channel", 0.040, 0.044), by_entry("state", 0.041, 0.046)]))
    assert re.search(r"Similar across (channel|state) \(\d\.\d% to \d\.\d%\)", text) and "no real difference" in text and "Biggest spread" not in text


def test_a_total_ranking_question_about_the_worst_picks_the_widest_column():
    q = "Where is revenue worst?"
    rs = [ranking("r1", "segment", lo=90.0, hi=100.0), ranking("r2", "region", lo=20.0, hi=100.0)]
    lines = card_of(render(state_for([q], rs, {q: ["r1"]})), 1, q)
    assert any("Biggest spread is across region (20 to 100)" in l for l in lines) and not any("segment" in l for l in lines)


# ------------------------------------------------------------------ 4. a dropped or missing action is never a raw fact line
def test_a_dropped_ai_action_becomes_look_into_a_top_group_with_the_figure_and_measure(monkeypatch):
    q = "Which segment earns the most revenue?"
    st = state_for([q], [ranking("r1", "segment", hi=5000.0, lo=3000.0, top="a")], {q: ["r1"]})
    monkeypatch.setattr(reporter, "ask_json", fake_ai({"summary": "Fine [counts].", "recommendations": ["Do it."]}, {"actions": [{"question": 1, "text": "Shift budget to segment a."}]}, []))
    todo = what_to_do(main_of(render(st, object())))
    assert re.search(r"^1\. Look into segment a first: .*revenue.*\d", todo, re.M)
    assert "Shift budget" not in todo and not TAG.findall(todo)


def test_a_dropped_action_whose_fact_has_no_top_group_says_no_action_suggested(monkeypatch):
    q = "How much revenue is lost?"
    st = state_for([q], [outside([by_entry("channel", 0.04, 0.05)])], {q: ["ro"]})
    st["strategies"] = []
    monkeypatch.setattr(reporter, "ask_json", fake_ai({"summary": "Fine [counts].", "recommendations": ["Do it."]}, {"actions": [{"question": 1, "text": "Cut 12,345 from revenue [ro]."}]}, []))
    rs = st["summary_facts"]["rankings"]
    rs[0] = {**rs[0], "largest": None}                                                  # no part of the loss stands out: nothing to look into
    todo = what_to_do(main_of(render(st, object())))
    assert "No action suggested" in todo and "12,345" not in todo and "Look into" not in todo and "revenue outside ok" not in todo


def test_look_into_is_none_without_a_figure_that_names_its_measure():
    r = ranking("r1", "segment", short="Top 3 segment by total sales", text="segment a leads")
    assert mainview.look_into("r1", {}, {"r1": r}, {}) is None                           # the line has no figure naming revenue


# ------------------------------------------------------------------ 5. tiles carry the loss measure when the goal asks about loss
def test_the_tiles_include_the_loss_amount_and_share_when_the_goal_asks_about_loss():
    st = state_for([QL], [outside([by_entry("channel", 0.04, 0.05)])], {QL: ["ro"]})
    st["report_view"] = tiering.build_view(st)
    tiles = dict(mainview.tiles(st, st["report_view"]))
    label = next(k for k in tiles if "outside ok" in k)
    assert tiles[label] == "900 (4.5%)" and 4 <= len(tiles) <= mainview.TILES_MAX
    plain = state_for(["Which segment earns the most revenue?"], [ranking("r1", "segment"), outside([])], {"Which segment earns the most revenue?": ["r1"]})
    assert not any("outside" in k for k, _ in mainview.tiles(plain, tiering.build_view(plain)))


# ------------------------------------------------------------------ 6. one ordering for key findings, what stands out and the evidence list
def test_goal_named_findings_lead_and_small_or_unnamed_ones_go_to_more_charts():
    st = make_state()
    small = compare(200, "g0", target="revenue", score=99.0)
    small["stats"].update(effect=0.02)                                                   # a named column, but a small effect
    other = compare(201, "unrelated", target="cost", score=500.0)                        # a strong effect on a column the goal does not name
    st["findings"] += [small, other]
    key = mainview.gallery_order(st, top=20)
    assert "f200" not in key["key"] and "f201" not in key["key"] and "f200" in key["more"] and "f201" in key["more"]
    assert key["key"] and all(tiering.goal_named(next(f for f in st["findings"] if f["id"] == i), st["goal"]) for i in key["key"])


def test_the_evidence_list_uses_the_same_order_as_the_key_findings():
    st = make_state()
    small = compare(200, "g0", target="revenue", score=99.0)
    small["stats"].update(effect=0.02)
    unnamed = compare(201, "unrelated", target="cost", score=500.0)
    st["findings"] += [small, unnamed]
    view = tiering.build_view(st)
    ev = [c["id"] for c in view["evidence"]]
    assert ev.index("f1") < ev.index("f201") if "f201" in ev else True
    keys = [mainview.order_key(c["goal_named"], c["effect"], c["score"]) for c in view["evidence"]]
    assert keys == sorted(keys)                                                          # the list is in the one shared order
    assert view["evidence"][0]["id"] not in ("f200", "f201")


def test_with_no_goal_named_column_the_key_findings_fall_back_to_the_strong_ones():
    st = make_state()
    st["goal"] = "Give me a general understanding of this data."
    key = mainview.gallery_order(st)["key"]
    assert key                                                                           # nothing is named, so nothing is demoted for being unnamed
