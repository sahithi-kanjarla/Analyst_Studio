"""Report page polish: readable chart names, captions written by code from the chart's own numbers, value labels on bars, a one-line reason under the Trust label, the app's look on charts,
plain Mode wording, and one repair retry for a reply that is not JSON. Synthetic facts and fake AI output."""
import os
import re
from pathlib import Path

os.environ["LLM_PROVIDER"] = "none"

import pytest  # noqa: E402
from test_main_view import growth, main_of, render  # noqa: E402
from test_report_page_stage1 import QL, outside, ranking, state_for  # noqa: E402
from test_report_page_stage2 import BARS, Q1, charted, fake, go  # noqa: E402

from app import llm as llm_mod  # noqa: E402
from app import mainview, palette, reportcharts, reporter  # noqa: E402
from app.propose import AI_ASSISTED, MIXED, RULES_ONLY, derive_mode  # noqa: E402


# ------------------------------------------------------------------ 1. readable names on charts
def named_state():
    st = {"profile": {"columns": [{"name": "net_revenue"}, {"name": "ship_state"}]},
          "understanding": {"source": "llm", "columns": {"net_revenue": {"display_name": "Net revenue", "role": "measure"}, "ship_state": {"display_name": "Ship state", "role": "category"}},
                            "goal_columns": {}, "descriptive": []}}
    return st


def drawn(spec, tmp_path, monkeypatch):
    """The figure of a drawn chart, kept before it is closed."""
    kept = []
    real = reportcharts.plt.close
    monkeypatch.setattr(reportcharts.plt, "close", lambda fig=None: (kept.append(fig), real(fig))[1])
    reportcharts.draw(spec, tmp_path / "charts")
    return kept[0]


def test_chart_title_and_axis_labels_use_display_names_never_underscores(tmp_path, monkeypatch):
    r = ranking("r1", "ship_state", measure="net_revenue", bars=[["a", 17.5e6], ["b", 3.1e6]])
    spec = reportcharts.specs_for(["r1"], {}, {"r1": r}, named_state())[0]
    assert spec["title"] == "Total Net revenue by Ship state"
    fig = drawn(spec, tmp_path, monkeypatch)
    ax = fig.axes[0]
    assert (ax.get_title(loc="left"), ax.get_xlabel(), ax.get_ylabel()) == ("Total Net revenue by Ship state", "Total Net revenue", "Ship state")
    assert "_" not in ax.get_title(loc="left") + ax.get_xlabel() + ax.get_ylabel()


def test_loss_and_growth_charts_have_no_internal_names_either():
    g = growth(group="ship_state", measure="net_revenue")
    o = outside([])
    o.update(group="ship_state", measure="net_revenue", top="ok", split=[{"value": "lost", "amount": 600.0, "share": 0.6}, {"value": "late", "amount": 300.0, "share": 0.3}])
    o["by"] = [{"group": "ship_state", "bars": [["x", 0.05], ["y", 0.02]], "spread": 0.6}]
    specs = reportcharts.specs_for([g["id"], "ro"], {}, {g["id"]: g, "ro": o}, named_state())
    assert len(specs) == 3
    for sp in specs:
        assert "_" not in sp["title"] + sp["value_label"] + sp["group_label"], sp


# ------------------------------------------------------------------ 2. the fallback caption is built from the chart's own numbers
def spec_of(data, **extra):
    return {"id": "x", "fact": "r1", "kind": "bars", "title": "t", "data": data, **extra}


def test_fallback_caption_names_top_and_bottom_with_figures_and_a_large_gap():
    c = reportcharts.fallback_caption(spec_of([["Alpha", 17.5e6], ["Mid", 9e6], ["Beta", 3.1e6]]))
    assert c == "Highest: Alpha at 17.5M; lowest: Beta at 3.1M. The gap is large."


def test_fallback_caption_says_small_when_the_spread_is_small():
    c = reportcharts.fallback_caption(spec_of([["Alpha", 100.0], ["Beta", 95.0]]))
    assert "Alpha at 100" in c and "Beta at 95" in c and c.endswith("The gap is small.")


def test_fallback_caption_writes_rates_as_percentages_and_uses_the_rate_spread():
    big = reportcharts.fallback_caption(spec_of([["x", 0.061], ["y", 0.02]], pct=True))
    assert "x at 6.1%" in big and "y at 2.0%" in big and big.endswith("large.")
    assert reportcharts.fallback_caption(spec_of([["x", 0.061], ["y", 0.060]], pct=True)).endswith("small.")


def test_fallback_caption_for_a_line_or_month_series_and_for_edge_cases():
    s = {"id": "s", "fact": "f", "kind": "series", "title": "t", "freq": "MS", "data": [["2024-01-01", 10.0], ["2024-02-01", 12.0], ["2024-05-01", 4.0]]}
    c = reportcharts.fallback_caption(s)
    assert "2024-02 at 12" in c and "2024-05 at 4" in c and c.endswith("large.")
    assert "Only a is shown, at 5" in reportcharts.fallback_caption(spec_of([["a", 5.0]]))
    assert reportcharts.fallback_caption(spec_of([])) == "There is no data to show."


def test_fallback_caption_has_no_generic_bars_text_and_stays_within_the_word_limit():
    c = reportcharts.fallback_caption(spec_of([["A very long group name that goes on and on", 17.5e6], ["Another quite long group name here", 3.1e6]]))
    assert "Bars show each group" not in c and len(c.split()) <= reportcharts.CAPTION_WORDS


def test_a_card_whose_ai_caption_is_unavailable_gets_the_numbers_caption(tmp_path, monkeypatch):
    monkeypatch.setattr(reporter, "ask_json", fake({}))                                          # the AI picked nothing and wrote nothing
    st = charted(tmp_path)
    head = main_of(go(st, object()))
    assert "How to read this: Highest: s0 at 100; lowest: s9 at 37." in head                      # BARS runs from 100 down to 37
    assert "The gap is large." in head


# ------------------------------------------------------------------ 3. bars show their value
def test_bars_carry_their_value_at_the_end(tmp_path, monkeypatch):
    spec = reportcharts.specs_for(["r1"], {}, {"r1": ranking("r1", "g", bars=[["a", 17.5e6], ["b", 3.1e6]])}, {})[0]
    assert [t.get_text() for t in drawn(spec, tmp_path, monkeypatch).axes[0].texts] == ["3.1M", "17.5M"]      # bottom bar first: the chart lists the largest on top


def test_rate_bars_are_labelled_as_percentages(tmp_path, monkeypatch):
    spec = spec_of([["x", 0.061], ["y", 0.0204]], pct=True, value_label="Rate", group_label="G")
    assert [t.get_text() for t in drawn(spec, tmp_path, monkeypatch).axes[0].texts] == ["2.0%", "6.1%"]


def test_monthly_bars_are_labelled_and_an_empty_month_has_no_label(tmp_path, monkeypatch):
    s = {"id": "s2", "fact": "f", "kind": "series", "title": "t", "freq": "MS", "data": [["2024-01-01", 10.0], ["2024-02-01", 12.0], ["2024-04-01", 9.0]], "value_label": "v", "group_label": ""}
    assert [t.get_text() for t in drawn(s, tmp_path, monkeypatch).axes[0].texts] == ["10", "12", "", "9"]


# ------------------------------------------------------------------ 4. the Trust label has a plain reason
def card_with(state_rankings, picks):
    st = state_for([Q1], state_rankings, {Q1: picks})
    st["strategies"] = []
    return render(st)


def test_trust_gets_a_reason_from_the_weakest_figure():
    g = growth()
    md = card_with([g], [g["id"]])
    assert re.search(r"Trust: \*\*Medium\*\*: the growth figure is not statistically reliable", md)


def test_trust_reason_for_computed_figures_and_for_weak_findings():
    md = card_with([ranking("r1", "segment")], ["r1"])
    assert "Trust: **High**: the figures are computed directly from the data" in md
    by_id = {"f1": {"id": "f1", "tool": "trend", "insight": {"confidence": {"level": "low"}}}, "f2": {"id": "f2", "tool": "compare", "insight": {"confidence": {"level": "high"}}}}
    assert mainview.trust_reason("Low", ["f1", "f2"], by_id, {}) == "the trend result could be random variation"
    assert mainview.trust_reason("High", ["f2"], by_id, {}) == "every figure passed its statistical checks"
    assert mainview.trust_reason("", ["f1"], by_id, {}) == ""


def test_the_reason_is_one_short_line():
    g = growth()
    md = card_with([g], [g["id"]])
    line = next(l for l in md.split("\n") if l.startswith("Trust:"))
    assert len(line) < 120 and "\n" not in line


# ------------------------------------------------------------------ 5. the app's look
def test_charts_use_the_apps_font_and_readable_colours(tmp_path, monkeypatch):
    # the colours and the font come from the one shared palette, generated from tokens.css
    css = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "tokens.css").read_text(encoding="utf8")
    for name, value in (("chart-ink", reportcharts.INK), ("chart-line", reportcharts.LINE), ("chart-accent", palette.ACCENT)):
        assert f"--{name}: {value};" in css, name
    assert palette.FONTS[0] in css
    assert reportcharts.plt.rcParams["font.sans-serif"][0] == palette.FONTS[0]
    spec = reportcharts.specs_for(["r1"], {}, {"r1": ranking("r1", "g", bars=BARS)}, {})[0]
    fig = drawn(spec, tmp_path, monkeypatch)
    ax = fig.axes[0]
    assert ax._left_title.get_color() == reportcharts.INK and fig.get_facecolor()[:3] == (1.0, 1.0, 1.0)
    assert all(t.get_color() == reportcharts.INK for t in ax.texts)


def luminance(hex_):
    ch = [int(hex_[k:k + 2], 16) / 255 for k in (1, 3, 5)]
    ch = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in ch]
    return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_chart_text_is_readable_on_the_panel_that_both_themes_show():
    assert contrast(reportcharts.INK, reportcharts.PANEL) >= 7 and contrast(reportcharts.MUTED, reportcharts.PANEL) >= 4.5
    css = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "styles.css").read_text(encoding="utf8")
    assert re.search(r"\.report img[^{]*\{[^}]*background: #fff", css)          # the dark page puts the picture on a white panel, so the dark text stays readable


# ------------------------------------------------------------------ 6. Mode wording
def test_mode_wording_is_plain():
    ai = {"planner": "llm", "polarity": "llm", "report": "llm"}
    assert derive_mode(ai) == AI_ASSISTED == "AI-assisted"
    assert derive_mode({**ai, "polarity": "fallback"}) == MIXED == "AI-assisted (partly)"
    assert derive_mode({"planner": "rules", "report": "rules"}) == RULES_ONLY == "Rules only"
    assert derive_mode({}) == "Rules only"


def test_the_report_header_uses_the_plain_mode_wording():
    st = state_for([Q1], [ranking("r1", "segment")], {Q1: ["r1"]})
    st["stage_sources"] = {"planner": "llm", "polarity": "fallback"}
    assert "**Mode:** AI-assisted (partly)" in render(st) and "mixed" not in render(st).split("\n", 3)[2]


# ------------------------------------------------------------------ 7. one repair retry for a reply that is not JSON
class Msg:
    def __init__(self, content):
        self.content = content


class Replies:
    def __init__(self, *replies):
        self.replies, self.prompts = list(replies), []

    def invoke(self, messages):
        self.prompts.append(messages[1][1])
        r = self.replies[min(len(self.prompts), len(self.replies)) - 1]
        if isinstance(r, Exception):
            raise r
        return Msg(r)


def test_an_empty_reply_gets_one_repair_retry_that_quotes_it():
    m = Replies("", '{"ok": 1}')
    obj, reason = llm_mod.ask_json_ex(m, "s", "u", repair=True)
    assert obj == {"ok": 1} and reason == "" and len(m.prompts) == 2
    assert m.prompts[1].startswith("u") and "not valid JSON" in m.prompts[1] and "(empty)" in m.prompts[1]


def test_prose_is_quoted_back_and_a_second_bad_reply_gives_up_after_one_retry():
    m = Replies("sorry, here you go: nothing")
    obj, reason = llm_mod.ask_json_ex(m, "s", "u", repair=True)
    assert obj is None and reason.startswith("bad JSON") and len(m.prompts) == 2 and "sorry, here you go" in m.prompts[1]


def test_a_rate_limit_is_not_repaired_as_bad_json():
    m = Replies(RuntimeError("Error code: 429 - rate limit reached"))
    obj, reason = llm_mod.ask_json_ex(m, "s", "u", repair=True)
    assert obj is None and reason.startswith("rate limit") and len(m.prompts) == llm_mod.RATE_LIMIT_RETRIES + 1        # only the rate-limit retries, no repair prompt
    assert not any("not valid JSON" in p for p in m.prompts)


def test_a_good_first_reply_is_not_asked_twice():
    m = Replies('```json\n{"ok": 2}\n```')
    assert llm_mod.ask_json_ex(m, "s", "u", repair=True) == ({"ok": 2}, "") and len(m.prompts) == 1


def test_the_repair_retry_is_not_swallowed_by_the_in_run_answer_cache():
    llm_mod.begin_run(cache=True)
    m = Replies("", '{"ok": 3}')
    assert llm_mod.ask_json_ex(m, "s", "u", repair=True)[0] == {"ok": 3} and len(m.prompts) == 2


def test_ask_json_repairs_but_callers_with_their_own_repair_loop_are_not_doubled():
    m = Replies("", '{"ok": 4}')
    assert llm_mod.ask_json(m, "s", "u") == {"ok": 4} and len(m.prompts) == 2
    m2 = Replies("")
    assert llm_mod.ask_json_ex(m2, "s", "u")[0] is None and len(m2.prompts) == 1          # propose and understanding repair on their own, with their own error text
