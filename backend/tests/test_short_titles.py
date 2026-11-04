"""Fix G: an idea's title from the AI is a short label; the rest moves to the body with the same facts. Fake AI only."""
import json
import os

os.environ["LLM_PROVIDER"] = "none"

from app import strategy  # noqa: E402


class Msg:
    def __init__(self, c):
        self.content = c


def run(title, objective="Keep gold accounts happy over the next quarter."):
    finding = {"id": "f1", "text": "Gold accounts average ₹8,216.", "title": "Average amount by tier", "params": {"group": "tier"}, "stats": {}}
    strat = {"id": "s1", "kind": "data", "title": "Protect gold accounts", "objective": "Keep gold accounts happy.", "actions": ["Call them.", "Offer a plan."], "risks": ["Cost.", "Delay."], "kpis": ["Churn"],
             "how_to_test": "Pilot it.", "evidence": ["f1"]}

    class L:
        def invoke(self, m):
            return Msg(json.dumps({"strategies": [{"id": "s1", "title": title, "objective": objective}]}, ensure_ascii=False))
    state = {"goal": "g", "context": {}, "findings": [finding], "decisions": []}
    assert strategy.refine_with_llm(L(), state, [strat]) == 1
    return strat


def test_a_long_title_is_cut_at_a_clause_and_the_rest_leads_the_body_with_its_figures():
    s = run("Protect gold accounts, which average ₹8,216 [f1] each and so carry most of the risk if they leave this quarter")
    assert s["title"] == "Protect gold accounts" and len(s["title"].split()) <= strategy.MAX_TITLE_WORDS
    assert s["objective"].startswith("Which average ₹8,216 each and so carry most of the risk if they leave this quarter.") and s["objective"].endswith("Keep gold accounts happy over the next quarter.")


def test_a_long_title_without_a_clause_break_is_cut_at_twelve_words_and_loses_nothing():
    s = run("Protect gold accounts that average ₹8,216 [f1] each against churn with a retention call every month before renewal")
    assert len(s["title"].split()) <= strategy.MAX_TITLE_WORDS and s["title"].split()[-1].lower() not in strategy.TRAILING
    whole = " ".join((s["title"] + " " + s["objective"].split(". ")[0]).replace(".", "").split())
    assert "₹8,216" in (s["title"] + s["objective"]) and "before renewal" in s["objective"]
    assert whole.count("renewal") == 1


def test_a_title_within_twelve_words_is_left_alone():
    s = run("Protect gold accounts that average ₹8,216 [f1] each")
    assert s["title"] == "Protect gold accounts that average ₹8,216 each" and s["objective"] == "Keep gold accounts happy over the next quarter."


def test_short_title_helper_never_returns_more_than_the_limit():
    for t in ("one two three four five six seven eight nine ten eleven twelve thirteen", "a: " + "w " * 30, "Increase retention among customers, and also review pricing for every segment this year"):
        head, rest = strategy.short_title(t)
        assert len(head.split()) <= strategy.MAX_TITLE_WORDS and head
        assert len((head + " " + rest).split()) >= len(t.replace(":", "").replace(",", "").split()) - 1
