"""The Report's title, answer sentence and tag, built by code from the run's own saved facts and display names.

Nothing here asks a model and nothing is computed anew: every figure is read from a saved ranking fact (its bars and its top share), so the
sentence says what the fact says. No column name is hard-coded; a value is shown as it is in the data, a measure by its display name."""
from __future__ import annotations

import re

from . import summary, understanding as und

NO_GOAL_TAG = "Overview, no goal given"


def _lower(name: str) -> str:
    """A name inside a sentence: first letter lower-case unless the word is an acronym or already mixed-case ("VAT", "eBay")."""
    first = name.split(" ", 1)[0]
    return name if len(first) > 1 and first[1:] != first[1:].lower() else name[:1].lower() + name[1:]


def _shown(state: dict, col: str) -> str:
    return (und.display_names(state).get(col) or str(col)).strip()


def _fact(state: dict) -> dict | None:
    """The first total-by-group ranking whose bars are all positive, so each share is a share of a real total."""
    for r in (state.get("summary_facts") or {}).get("rankings") or []:
        bars = r.get("bars") or []
        if r.get("basis") == "total" and r.get("kind") is None and len(bars) > 1 and r.get("top_share") and all(v > 0 for _, v in bars):
            return r
    return None


def _join(parts: list[str]) -> str:
    return parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]


def ranking_sentence(state: dict, r: dict) -> str:
    """One plain sentence for a ranking: the leaders (tied when their rounded shares are equal) with their share and amount, then the next ones.
    "Clothing and Electronics each account for about 36% of profit (13.3K and 13.2K); Furniture adds 28% (10.5K)."
    The measure is named once and the group's own name is not used at all."""
    bars = sorted(((str(k), float(v)) for k, v in r["bars"]), key=lambda kv: -kv[1])[:3]
    total = bars[0][1] / float(r["top_share"])
    pct = [round(100 * v / total) for _, v in bars]
    measure = _lower(_shown(state, r["measure"]))
    amt = [summary.short(v) for _, v in bars]
    lead = [i for i in range(len(bars)) if pct[i] == pct[0]] if len(bars) > 1 else [0]
    lead = lead if len(lead) > 1 else [0]
    names = _join([bars[i][0] for i in lead])
    first = (f"{names} each account for about {pct[0]}% of {measure} ({_join([amt[i] for i in lead])})" if len(lead) > 1
             else f"{names} accounts for about {pct[0]}% of {measure} ({amt[0]})")
    rest = [i for i in range(len(bars)) if i not in lead]
    if not rest:
        return first + "."
    tail = [f"{bars[i][0]} {pct[i]}% ({amt[i]})" for i in rest]
    return f"{first}; {bars[rest[0]][0]} adds {pct[rest[0]]}% ({amt[rest[0]]})" + (f" and {tail[1]}" if len(tail) > 1 else "") + "."


def title(state: dict) -> str:
    """"<Measure> overview": the measure the answer is about (else the plan's target, else the main measure of the overview), never a generic name."""
    r = _fact(state)
    col = (r or {}).get("measure") or (state.get("plan") or {}).get("target") or (state.get("curation") or {}).get("main_measure") or ""
    return f"{_shown(state, col)} overview" if col else "Data overview"


def build(state: dict) -> dict:
    """{"title", "answer", "tag"}; "answer" is "" when no ranking fact can carry the sentence (the page then shows the saved headline)."""
    r = _fact(state)
    ans = state.get("goal_answer") or {}
    sentence = "" if ans.get("sentence") else ranking_sentence(state, r) if r else ""
    overview = (state.get("curation") or {}).get("mode") == "overview"
    return {"title": title(state), "answer": sentence, "tag": NO_GOAL_TAG if overview else ""}


def repeated_word(text: str) -> str | None:
    """A word written twice in a row ("category category"), for the tests."""
    m = re.search(r"\b(\w+)\s+\1\b", text, re.I)
    return m.group(1) if m else None
