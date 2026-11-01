"""Lens fix pack (from real testing): the split-by consistency bug, negative values and shares, "this graph is wrong", view-change requests, unusual changes, premise checks beyond
rank claims, plain-language summaries, "why did you choose it?" for strategies, and the sentence shown once. The AI is a fake one that chooses; every figure is compared with pandas.
The data is Kaveri-style: categories with sub-categories nested inside them (a sub-category belongs to one category), a profit column that is negative for one category, and a date column."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.lens import chat, engine, query as Q, records, render, reports, rules
from tests.test_lens import FakeAI, assert_figures_are_computed, make_run, numbers, q
from tests.test_lens_stage2a import REPORT, UND as SHOP_UND, shop, shop_df  # noqa: F401  (the shop fixture: a run with a report to summarise)

SUBS = {"Furniture": ["Chairs", "Tables", "Bookcases"], "Home Decor": ["Cushions", "Lamps", "Wall Art"], "Clothing": ["Shirts", "Trousers", "Sarees"],
        "Electronics": ["Phones", "Laptops", "Headphones"]}
MEAN = {"Furniture": 2700.0, "Home Decor": 300.0, "Clothing": 210.0, "Electronics": -870.0}

KUND = {"source": "llm", "goal_columns": {}, "descriptive": [], "unmapped": [], "retries": 0, "error": "", "columns": {
    "order_date": {"role": "date", "meaning": "when the order was placed", "display_name": "Order date"},
    "category": {"role": "category", "meaning": "product category", "display_name": "Category"},
    "sub_category": {"role": "category", "meaning": "product sub category", "display_name": "Sub category"},
    "channel": {"role": "category", "meaning": "sales channel", "display_name": "Channel"},
    "amount": {"role": "measure", "meaning": "order value", "display_name": "Amount", "measure_kind": "money"},
    "profit": {"role": "measure", "meaning": "profit on the order", "display_name": "Profit", "measure_kind": "money"},
    "customer_id": {"role": "id", "meaning": "customer", "display_name": "Customer"}}}


def kav_df(spike: bool = False) -> pd.DataFrame:
    rng = np.random.default_rng(11)
    n = 1200
    cat = rng.choice(list(SUBS), n)
    sub = [str(rng.choice(SUBS[c])) for c in cat]
    df = pd.DataFrame({
        "order_date": pd.to_datetime("2023-01-01") + pd.to_timedelta(rng.integers(0, 715, n), unit="D"),
        "category": cat, "sub_category": sub, "channel": rng.choice(list("ABCDEFGHI"), n),
        "amount": rng.gamma(2.0, 500.0, n).round(2),
        "profit": (np.array([MEAN[c] for c in cat]) * rng.uniform(0.7, 1.3, n)).round(2),
        "customer_id": ["C%03d" % i for i in rng.integers(0, 90, n)]})
    return df


@pytest.fixture
def kav(monkeypatch):
    monkeypatch.setenv("CHATBOT", "on")
    chat._DAILY["until"] = 0.0
    rid, wd, result = make_run(kav_df(), KUND, {"goal": "Which category earns most?"})
    return rid, wd, result, pd.read_pickle(wd / "clean.pkl")


def ask(run, ai=None, **kw):
    rid, wd, result, df = run
    return chat.ask(wd, result, llm=ai, allow_ai=ai is not None, **kw)


def run_query(run, **kw):
    rid, wd, result, df = run
    ctx = chat.load_data(wd, result)[1]
    ch = Q.check(kw, ctx, df)
    assert ch.query, ch.errors
    return engine.run(ch.query, df, ctx)


def table_matches_pandas_split(R, df, g, s, m, agg):
    """Every cell of a split-by table is the pandas value for that group and split value, and the chart is drawn from exactly those values."""
    pv = df.groupby([g, s])[m].agg(agg)
    cols = R.columns[1:-1] if agg == "sum" else R.columns[1:]
    for raw in R.rows:
        for name, v in zip(cols, raw[1:1 + len(cols)]):
            want = pv.get((raw[0], name), np.nan)
            assert (v is None and pd.isna(want)) or abs(v - want) < 1e-6, (raw[0], name, v, want)
    assert engine.drawn_vs_table(R) == []


# ------------------------------------------------------------------ 1. split-by: the largest combination can never be below its group's overall value
def test_split_by_largest_combination_is_not_below_its_groups_overall_average(kav):
    df = kav[3]
    R = run_query(kav, op="aggregate", measure="profit", agg="average", group_by=["category"], split_by=["sub_category"])
    overall = df.groupby("category")["profit"].mean()
    combos = df.groupby(["category", "sub_category"])["profit"].mean()
    largest = {f["label"]: f["value"] for f in R.figures}["largest combination"]
    assert largest == pytest.approx(combos.max())                   # the largest of ALL combinations, whether or not it was crowded off the chart
    for grp, v in overall.items():
        assert largest >= v - 1e-9, (grp, v, largest)               # a weighted mean of sub-category averages can't beat the largest one
    top_group = overall.idxmax()
    assert R.sentence.startswith(f"Average Profit by Category and Sub category: {top_group} is highest overall")
    assert f"{combos.idxmax()[0]} / {combos.idxmax()[1]}" in R.sentence
    for k in df["category"].unique():                               # every group's sub-categories are in the table (a nested split is a hierarchy, not a top-6)
        row = next(r for r in R.rows if r[0] == k)
        assert sum(x is not None for x in row[1:]) == df[df.category == k]["sub_category"].nunique()
    table_matches_pandas_split(R, df, "category", "sub_category", "profit", "mean")


@pytest.mark.parametrize("agg,split", [("average", "sub_category"), ("sum", "sub_category"), ("count", "sub_category"), ("average", "channel"), ("sum", "channel")])
def test_split_by_invariants_hold_for_every_calculation(kav, agg, split):
    df = kav[3]
    kw = dict(op="aggregate", measure="profit" if agg != "count" else None, agg=agg, group_by=["category"], split_by=[split])
    R = run_query(kav, **kw)
    assert engine.drawn_vs_table(R) == []                           # the drawn values equal the computed table
    shown_max = max(v for r in R.rows for v in r[1:1 + len(R.columns) - 1 - (1 if R.additive else 0)] if v is not None)
    largest = {f["label"]: f["value"] for f in R.figures}["largest combination"]
    assert largest == pytest.approx(shown_max)                      # the sentence's largest combination is one the table and chart show
    pv = (df.groupby(["category", split])["profit"].agg({"average": "mean", "sum": "sum"}[agg]) if agg != "count" else df.groupby(["category", split]).size())
    assert largest == pytest.approx(pv.max())
    if agg == "average":
        for v in df.groupby("category")["profit"].mean():
            assert largest >= v - 1e-9
    if R.additive:
        hidden = df[split].nunique() - (len(R.columns) - 2)
        tot = df.groupby("category")["profit"].sum() if agg == "sum" else df.groupby("category").size()
        for r in R.rows:
            assert r[-1] == pytest.approx(tot[r[0]])                 # the Total column is the group's real total whatever fits on the chart
            if not hidden:                                           # every split value shown: the cells add up to the total
                assert sum(x for x in r[1:-1] if x is not None) == pytest.approx(r[-1])
        assert (hidden > 0) == any("not shown" in n for n in R.how["notes"])      # columns left off the chart are said so


# ------------------------------------------------------------------ 2. negative values and shares
def test_negative_group_is_named_and_shares_say_they_can_exceed_100(kav):
    df = kav[3]
    t = ask(kav, FakeAI(q(op="share", measure="profit", agg="sum", group_by=["category"], n=6)), question="how is profit split by category?")
    a = assert_figures_are_computed(t)
    tot = df.groupby("category")["profit"].sum()
    assert tot["Electronics"] < 0 and tot.sum() > 0
    assert "Electronics has negative profit; shares can add up to more than 100%." in a["text"]
    assert "(negative)" in a["text"] and "more" not in a["text"].replace("more than 100%", "")       # 4 groups: all named, none summarised
    for k in tot.index:
        assert k in a["text"]
    assert a["chart"]["type"] != "share"                            # a pie cannot draw a negative slice
    top_share = tot.max() / tot.sum() * 100
    assert top_share > 100 and f"{top_share:.1f}%" in a["text"]


def test_aggregate_lists_all_groups_up_to_eight_with_the_negative_ones_marked(kav):
    df = kav[3]
    t = ask(kav, FakeAI(q(op="aggregate", measure="profit", agg="sum", group_by=["sub_category"], n=8)), question="total profit by sub category, top 8")
    a = assert_figures_are_computed(t)
    tot = df.groupby("sub_category")["profit"].sum().sort_values(ascending=False).head(8)
    for k in tot.index:
        assert k in a["text"]
    assert " more" not in a["text"].replace("more than 100%", "")
    for k, v in tot.items():
        if v < 0:
            assert f"{k} (" in a["text"] and a["text"].split(f"{k} (")[1].split(")")[0].endswith("negative")
    t = ask(kav, FakeAI(q(op="aggregate", measure="profit", agg="sum", group_by=["sub_category"], n=10)), question="total profit by sub category, top 10")
    assert "7 more" in t["answer"]["text"] or " more" in t["answer"]["text"]              # more than eight: the first three and how many more


def test_lose_money_chip_is_offered_and_shows_only_the_losing_groups(kav):
    df = kav[3]
    t = ask(kav, FakeAI(q(op="aggregate", measure="profit", agg="sum", group_by=["category"])), question="total profit by category")
    chip = next(c for c in t["answer"]["chips"] if c["text"] == "Show groups that lose money")
    b = ask(kav, None, chip=chip["id"])["answer"]
    tot = df.groupby("category")["profit"].sum()
    assert b["status"] == "answered" and [r[0] for r in b["table"]["rows"]] == list(tot[tot < 0].index)
    assert b["text"].startswith("Categories that lose money") and "Electronics" in b["text"]
    assert not any(c["text"] == "Show groups that lose money" for c in b["chips"])
    start = chat.chipmod.start(chat.load_data(kav[1], kav[2])[1], df, kav[2])
    assert any(c["text"] == "Show groups that lose money" for c in start)
    ok = ask(kav, FakeAI(q(op="aggregate", measure="amount", agg="sum", group_by=["category"])), question="total amount by category")["answer"]
    assert not any(c["text"] == "Show groups that lose money" for c in ok["chips"])          # nothing loses money: no chip, no note
    assert "negative" not in ok["text"]


# ------------------------------------------------------------------ 3. "this graph is wrong": re-check the latest chart, ask only if there is none
def test_graph_is_wrong_rechecks_the_latest_chart(kav):
    df = kav[3]
    ask(kav, FakeAI(q(op="aggregate", measure="profit", agg="sum", group_by=["category"])), question="total profit by category")
    t2 = ask(kav, FakeAI(q(op="trend", measure="profit", agg="sum", grain="month")), question="show total profit over time")
    for words in ("the graph is not plotted properly", "this graph is wrong"):
        ai = FakeAI({"action": "unsupported", "reason": "x"})
        a = ask(kav, ai, question=words)["answer"]
        assert ai.calls == 0                                         # plain rules: no AI needed
        assert a["status"] == "answered" and a["kind"] == "recheck" and a["query"]["op"] == "trend"       # the most recent chart, not the first
        assert "every one matches the computed table" in a["text"] and "Nothing is plotted wrongly" in a["text"]
        n = df["order_date"].dt.to_period("M").nunique()
        assert any(f["label"] == "values drawn" and f["value"] == n for f in a["figures"])
        assert a["chart"]["path"] and a["how"]["lines"]


def test_graph_is_wrong_with_no_chart_asks_which(kav):
    a = ask(kav, None, question="the graph is not plotted properly")["answer"]
    assert a["status"] == "no_fit" and a["text"].startswith("Which chart do you mean?")
    ask(kav, FakeAI(q(op="aggregate", measure="profit", agg="sum")), question="total profit")                # a single number has no chart
    a = ask(kav, None, question="this chart is wrong")["answer"]
    assert a["text"].startswith("Which chart do you mean?")


def test_graph_check_reports_a_mismatch_in_plain_words(kav, monkeypatch):
    R = run_query(kav, op="trend", measure="profit", agg="sum", grain="month")
    assert engine.drawn_vs_table(R) == []
    R.plot["series"][0]["values"][2] += 1000.0                        # a chart that does not show the table
    bad = engine.drawn_vs_table(R)
    assert len(bad) == 1 and R.rows[2][0] in bad[0]
    ask(kav, FakeAI(q(op="trend", measure="profit", agg="sum", grain="month")), question="show total profit over time")
    monkeypatch.setattr(engine, "drawn_vs_table", lambda R: ["Total Profit at 2023-03: the chart uses 5 but the table says 6"])
    a = ask(kav, None, question="this graph is wrong")["answer"]
    assert "found 1 mismatch" in a["text"] and "2023-03" in a["text"] and "redrawn" in a["text"]


# ------------------------------------------------------------------ 4. a request to change what a tab shows is not a summary
@pytest.mark.parametrize("text", ["make the ideas tab shorter", "simplify this tab", "remove this chart", "shorten the ideas tab", "hide this tab", "Make the dashboard simpler"])
def test_view_changes_are_not_summarised(kav, text):
    ai = FakeAI({"action": "summary", "length": "short"})
    a = ask(kav, ai, question=text)["answer"]
    assert ai.calls == 0 and a["kind"] == "view" and a["status"] == "answered"       # a view request is never turned into a summary
    assert "coming soon" not in a["text"] and a["label"] in ("View changes", "Not changed", "View changed", "No change")
    assert a["chips"] and not a.get("lines")


def test_summaries_still_work_and_data_requests_are_not_view_changes():
    assert rules.route("shorten the report")["kind"] == "summary" and rules.route("make it shorter")["kind"] == "summary"
    assert rules.route("remove Delhi from the chart") is None and rules.route("show revenue as a line chart") is None


# ------------------------------------------------------------------ 5. unusual changes
def spiky_df(flat: bool = False) -> pd.DataFrame:
    rng = np.random.default_rng(5)
    rows = []
    for p in pd.period_range("2023-01", "2024-06", freq="M"):
        last = p == pd.Period("2024-06")
        days = 5 if last else p.days_in_month                          # the last month is partial: the data ends on 5 June, with fewer rows
        n = 8 if last else 30
        scale = 100.0 * (0.4 if (p == pd.Period("2023-08") and not flat) else 1.0)
        for d, a in zip(rng.integers(1, days + 1, n), rng.uniform(0.95, 1.05, n) * scale):
            rows.append({"order_date": pd.Timestamp(p.year, p.month, int(d)), "amount": round(a, 2), "profit": round(a * 0.2, 2), "category": "A", "sub_category": "x", "channel": "A", "customer_id": "C1"})
    return pd.DataFrame(rows)


def unusual_expected(df: pd.DataFrame):
    g = df.groupby(df["order_date"].dt.to_period("M"))["amount"].agg(["sum", "size"])
    g = g.iloc[:-1]                                                   # the last, partial month is ignored
    pct = g["sum"].pct_change() * 100
    ok = (g["size"] >= 5) & (g["size"].shift() >= 5)
    return g, pct, [str(p) for p in g.index[(pct.abs() >= 40) & ok]]


def test_unusual_changes_are_flagged_marked_and_listed(monkeypatch):
    monkeypatch.setenv("CHATBOT", "on")
    chat._DAILY["until"] = 0.0
    df = spiky_df()
    run = make_run(df, KUND, {"goal": "x"})
    run = (*run, pd.read_pickle(run[1] / "clean.pkl"))
    t = ask(run, FakeAI(q(op="unusual_changes", measure="amount", agg="sum")), question="any sudden drop or rise?")
    a = assert_figures_are_computed(t)
    g, pct, flagged = unusual_expected(df)
    assert flagged == ["2023-08", "2023-09"]
    assert a["query"]["op"] == "unusual_changes" and a["query"]["grain"] == "month" and a["chart"]["type"] == "line" and a["chart"]["path"]
    for lab in flagged:
        assert lab in a["text"]
    assert "2024-06" not in a["text"] and "2024-06" not in [r[0] for r in a["table"]["rows"]]       # the partial month is ignored
    assert f"{abs(pct['2023-08']):.1f}%" in a["text"] and f"{abs(pct['2023-09']):.1f}%" in a["text"]
    assert "fell" in a["text"] and "rose" in a["text"]
    assert [r[0] for r in a["table"]["rows"] if r[4]] == flagged and {r[0]: r[4] for r in a["table"]["rows"]}["2023-08"] == "Drop"
    assert any("2024-06" in n and "incomplete" in n for n in a["how"]["notes"])
    R = engine.run(a["query"], df, chat.load_data(run[1], run[2])[1])
    assert R.plot["marks"] == [list(g.index.astype(str)).index(x) for x in flagged]               # the flagged points are marked on the line
    assert engine.drawn_vs_table(R) == []
    png = run[1] / "unusual.png"
    assert render.draw(R, png) and png.stat().st_size > 1000


def test_no_unusual_change_says_so_and_works_without_the_ai(monkeypatch):
    monkeypatch.setenv("CHATBOT", "on")
    chat._DAILY["until"] = 0.0
    run = make_run(spiky_df(flat=True), KUND, {"goal": "x"})
    run = (*run, pd.read_pickle(run[1] / "clean.pkl"))
    a = assert_figures_are_computed(ask(run, None, question="any sudden drop or rise?"))
    assert a["query"]["op"] == "unusual_changes" and a["text"].startswith("No unusual changes")
    assert not [r for r in a["table"]["rows"] if r[4]]


# ------------------------------------------------------------------ 6. premises beyond rank claims
def test_it_should_be_X_is_checked_against_the_computed_values(kav):
    df = kav[3]
    ai = FakeAI(q(op="top", measure="profit", agg="average", group_by=["category"], n=3, direction="top"))
    t = ask(kav, ai, question="why is Furniture the top, it should be Clothing")
    a = assert_figures_are_computed(t)
    m = df.groupby("category")["profit"].mean()
    assert a["text"].startswith("Correction first: Clothing is not the top category by average profit: Furniture is, at ")
    assert f"{m['Furniture']:,.0f}" in a["text"] and f"{m['Clothing']:,.0f}" in a["text"] and "Clothing is third" in a["text"]
    assert any("Clothing" in c.get("note", "") for c in a["premise"]["claims"])


def test_X_is_higher_than_Y_is_compared(kav):
    df = kav[3]
    m = df.groupby("category")["profit"].mean()
    ai = FakeAI(q(op="top", measure="profit", agg="average", group_by=["category"], n=3, direction="top"))
    a = assert_figures_are_computed(ask(kav, ai, question="Clothing is higher than Furniture on profit"))
    assert a["text"].startswith("Correction first: Clothing is not higher than Furniture by average profit: Clothing is ")
    assert f"{m['Clothing']:,.0f}" in a["text"] and f"{m['Furniture']:,.0f}" in a["text"] and "Furniture is higher." in a["text"]
    ok = ask(kav, FakeAI(q(op="top", measure="profit", agg="average", group_by=["category"], n=3, direction="top")), question="Furniture is higher than Clothing on profit")["answer"]
    assert "Correction first" not in ok["text"]                                                      # a true claim is not "corrected"


@pytest.mark.parametrize("ai", [True, False])
def test_direction_claims_are_checked_by_computing_the_change(kav, ai):
    df = kav[3]
    mon = df.groupby(df["order_date"].dt.to_period("M"))["amount"].sum()
    feb, mar = mon["2024-02"], mon["2024-03"]
    said = "down" if mar > feb else "up"                           # the opposite of what the data shows
    qn = f"sales went {said} in March 2024"
    fake = FakeAI(q(op="aggregate", measure="amount", agg="sum")) if ai else None
    a = assert_figures_are_computed(ask(kav, fake, question=qn))
    pc = abs(mar - feb) / abs(feb) * 100
    word, did = ("down", "went up") if said == "down" else ("up", "went down")
    assert a["text"].startswith(f"Correction first: Total amount did not go {word} in 2024-03: it {did}, from {feb:,.0f} in 2024-02 to {mar:,.0f} in 2024-03 ({'+' if mar > feb else '-'}{pc:.1f}%).")
    right = "up" if mar > feb else "down"
    b = ask(kav, FakeAI(q(op="aggregate", measure="amount", agg="sum")) if ai else None, question=f"sales went {right} in March 2024")["answer"]
    assert "Correction first" not in b["text"]


# ------------------------------------------------------------------ 7. plain-language summaries
PLAIN1 = "Clothing brings in the most: 40.0% of the total amount [r1]."
PLAIN2 = "The return rate trend is up 12% [f1]."
LONG = "By total amount the Clothing category is the highest of all the categories in the whole report at a share of 40.0% of the total amount overall [r1]."
REPEAT = "Clothing customers: Clothing is 40.0% of the total amount [r1]."


def test_summary_lines_are_short_plain_sentences_and_still_checked(shop):
    rid, wd, result, df = shop
    a = ask(shop, FakeAI({"lines": [PLAIN1, LONG, REPEAT, PLAIN2]}), question="Summarise this report")["answer"]
    assert a["lines"] == ["Clothing brings in the most: 40.0% of the total amount.", "The return rate trend is up 12%."]
    assert all(reports.words_in(x) <= 20 and not reports.repeats_a_label(x) for x in a["lines"])
    assert "2 lines the AI wrote did not pass" in " ".join(a["how"]["notes"])
    known = records.known_facts(result)
    for ln in a["lines"]:
        for n in numbers(ln):
            assert any(abs(float(n) - float(x)) < 0.051 for f in known.values() for x in f["numbers"]), (n, ln)
    s = ask(shop, FakeAI({"lines": [PLAIN1, PLAIN2, "Home is 10% of the total amount [r1]."]}), question="shorten the report")["answer"]
    assert len(s["lines"]) <= 3 and all(reports.words_in(x) <= 20 for x in s["lines"])


def test_summary_fallback_lines_are_plain_too(shop):
    long_ranking = ("By total amount, tier Gold is highest at 17,467,665 (38.5% of the total amount) and tier Silver is lowest at 12,347,779 in the first full period of the data, which covers many months")
    line = reports.plain_line(long_ranking, {"tier"})
    assert line and reports.words_in(line) <= 20 and "tier" not in line and any(ch.isdigit() for ch in line) and "Gold" in line
    assert reports.plain_line("Nothing here has a figure at all", set()) == ""
    a = ask(shop, FakeAI({"lines": ["nope", "nothing"]}), question="shorten the report")["answer"]
    assert a["lines"] and all(reports.words_in(x) <= 20 and not reports.repeats_a_label(x) and numbers(x) for x in a["lines"])


# ------------------------------------------------------------------ 8. why did you choose it?
def test_why_did_you_choose_it_explains_the_ranking_and_the_runner_up(shop):
    first = ask(shop, FakeAI({"action": "strategy", "focus": ""}), question="what should we do next?")["answer"]
    why_chip = next(c for c in first["chips"] if c["text"] == "Why did you choose it?")
    for a in (ask(shop, None, question="Why did you choose it?")["answer"], ask(shop, None, chip=why_chip["id"])["answer"]):
        assert a["status"] == "answered" and a["kind"] == "strategy" and a["ai_calls"] == 0
        text = " ".join(a["lines"])
        assert "“Reduce returns in Home” comes first" in text and "impact is High, the confidence medium and the effort Low" in text
        assert "It rests on this fact from the report: return rate rose 12% over the period (f1)" in text      # quoted from the report's own finding
        assert "The runner-up is “Tighten discounts in the North” (impact Medium, confidence high, effort Low)" in text
        assert "ranked lower because its impact is Medium against High." in text
        assert a["fact_ids"] == ["f1", "f2"] and a["how"]["lines"]


def test_best_strategy_and_why_answers_both(shop):
    a = ask(shop, FakeAI({"action": "strategy", "focus": ""}), question="what is the best strategy and why?")["answer"]
    assert a["lines"][0].startswith("Reduce returns in Home") and "Why this one:" in a["lines"]
    assert any("runner-up" in x for x in a["lines"])


# ------------------------------------------------------------------ 9. the sentence is shown once
def test_an_answer_carries_its_sentence_once_and_the_page_does_not_repeat_it(kav):
    for ai, qn in ((FakeAI(q(op="trend", measure="profit", agg="sum", grain="month")), "show total profit over time"),
                   (FakeAI(q(op="top", measure="profit", agg="average", group_by=["category"], n=3, direction="top")), "why is Furniture the top, it should be Clothing")):
        a = ask(kav, ai, question=qn)["answer"]
        assert json.dumps(a, default=str).count(a["text"]) == 1 and a["chart"]["note"] != a["text"]
        assert "caption" not in a["chart"]
    web = Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "lens"
    for f in ("LensAnswer.tsx", "LensPins.tsx"):
        src = (web / f).read_text(encoding="utf-8")
        assert "alt={a.text}" not in src and "alt={text}" not in src and "alt={p.text}" not in src      # the picture's alt text would repeat the sentence above it
