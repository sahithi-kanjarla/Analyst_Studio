"""Plain rules that run before any AI call, so they cost nothing and cannot be talked round. The words here are the user's own question words (margin, profit),
never column names: whether the data can answer is decided from the understanding record."""
from __future__ import annotations

import re

from .context import Ctx

ASKS_MARGIN = re.compile(r"\bmargins?\b", re.I)
ASKS_PROFIT = re.compile(r"\b(profits?|profitab\w+|earnings?|net income)\b", re.I)
NAMED = re.compile(r"profit|margin", re.I)


def margin_gap(question: str, ctx: Ctx) -> str | None:
    """The reply when the question is about margin or profit and the data cannot give it, else None. A profit or margin column (named so, or a ratio the run derived) means the
    question goes on to be answered normally; a data set with no cost column and no such column cannot give a margin, and Lens says so instead of guessing."""
    margin, profit = ASKS_MARGIN.search(question or ""), ASKS_PROFIT.search(question or "")
    if not (margin or profit):
        return None
    if any(NAMED.search(f"{c.name} {c.display} {c.meaning}") for c in ctx.measures()):
        return None
    costs = ctx.cost_columns()
    if costs:
        return (f"The data has a cost column ({costs[0].display}), but I can't work out margin from it yet, because I can't tell whether it is a cost per unit or a total. "
                "I can show the cost and the sales separately.")
    return ("Margin can't be computed because the data has no cost column." if margin
            else "Profit can't be worked out because the data has no cost column, so margin can't be computed either.")


# ------------------------------------------------------------------ questions that are about the app or the report, not about a calculation. Plain patterns on the user's own words.
WHY_RE = re.compile(r"\bwhy\b|\bhow come\b|\bwhat (?:is |was )?(?:causing|behind|the reason|the cause)\b|\breason for\b|\bwhat caused\b", re.I)
SUMMARY_RE = re.compile(r"\b(?:summari[sz]e|summari[sz]ing|shorten|tl;?\s?dr|condense|in a nutshell|executive summary|short(?:er)? version|brief(?:er)? version|key takeaways?)\b"
                        r"|\bsummary of (?:the |this |my )?(?:report|analysis|findings|results|insights)\b|\bmake (?:it|this|the report) (?:shorter|brief|more concise)\b|\bgive me the (?:short|quick) (?:version|summary)\b", re.I)
SHORT_RE = re.compile(r"\b(?:shorten|tl;?\s?dr|condense|in a nutshell|short(?:er)? version|brief(?:er)? version|shorter|very short|one[- ]minute)\b", re.I)
VIEW_RE = re.compile(r"\b(?:dark|light) mode\b|\b(?:change|switch|toggle|swap|set|make|turn)\b[^.?]{0,30}\b(?:view|layout|theme|colou?rs?|font|fonts|sidebar|navigation|language|display)\b"
                     r"|\b(?:go to|switch to|open|jump to|navigate to|take me to)\b[^.?]{0,25}\b(?:tab|page|dashboard|insights|report|chat)\b", re.I)
DATA_WORDS = re.compile(r"\b(?:chart|graph|line|bar|pie|monthly|weekly|daily|quarterly|yearly|top \d+|bottom \d+|as a|stacked|histogram|scatter)\b", re.I)
APP_PATTERNS = (
    ("tab", re.compile(r"\bwhat (?:does|do|is|are)\b[^.?]{0,40}\b(?:tab|page|screen|section)\b[^.?]{0,25}\b(?:show|do|for|mean|contain|about|has)\b|\bwhat(?:'s| is) (?:on|in) (?:this|the) (?:tab|page|screen)\b|\bexplain (?:this|the) (?:tab|page|screen)\b", re.I)),
    ("trust", re.compile(r"\btrust\w*\b|\bhow (?:reliable|confident)\b|\bhow (?:much )?can i rely\b", re.I)),
    ("cleaning", re.compile(r"\bwhat (?:did|have) (?:you|we) (?:clean|change|fix|remove|drop|do to)\b|\bwhat (?:was|got|has been) (?:cleaned|changed|fixed|removed|dropped)\b|\bhow did you clean\b|\bcleaning (?:log|steps?)\b|\bwhat (?:cleaning|changes)\b[^.?]{0,25}\b(?:made|done|applied)\b", re.I)),
    ("definitions", re.compile(r"\bdefinitions?\b|\bwhat (?:assumptions?|rules?) (?:did|were|was)\b|\bwhich assumptions?\b|\bhow (?:did|do) you (?:define|treat)\b", re.I)),
    ("understanding", re.compile(r"\bhow did you (?:understand|read|interpret)\b[^.?]{0,20}\b(?:data|columns?)\b|\bwhat does (?:the )?(?:\w+ )?column\b[^.?]{0,30}\bmean\b|\bunderstanding record\b|\bcolumn meanings?\b", re.I)),
    ("data_gaps", re.compile(r"\bdata gaps?\b|\bwhat(?:'s| is) missing from (?:the |my )?data\b", re.I)),
)
PRIORITY_RE = re.compile(r"\bwhat matters most\b|\bwhat should i (?:focus|look|care)\b|\bi (?:care|worry) (?:most )?about\b|\b(?:my|our) (?:main |top )?(?:priority|priorities)\b|\bfocus(?:ed|ing)? on\b|\bmost important (?:for|to) (?:me|us)\b|\bwhat matters (?:for|to) (?:me|us)\b|\bi(?:'m| am) (?:interested|concerned) in\b", re.I)
PRIORITY_PHRASE = re.compile(r"(?:care about|worry about|focus(?:ed|ing)? on|priority is|priorities are|interested in|concerned (?:about|with|in)|matters? (?:most )?(?:is|are|to me is)|important (?:for|to) (?:me|us) (?:is|are))\s+(?P<p>[^.?!]{2,80})", re.I)
GENERIC_STOP = set("the a an and or of to in on for is are was be my our i we me it its with about most more matters matter care really that this what how much many".split())


# a request to change what a tab or chart shows ("make the ideas tab shorter", "simplify this tab", "remove this chart"): not a summary and not a data question
EDIT_VERB = r"(?:shorten|simplif(?:y|ies)|declutter|tidy(?: up)?|clean up|condense|trim|cut down|reduce|rearrange|reorder|resize|rename|hide|remove|delete|get rid of|drop|collapse|minimi[sz]e)"
EDIT_TARGET = (r"(?:tab|tabs|page|section|chart|graph|card|panel|widget|dashboard|ideas|insights|layout|screen|"
               r"(?:report|ideas to try|your data|what we cleaned|how we analysed|power bi export|how files connect)\s+(?:tab|page|section))")
VIEW_EDIT_RE = re.compile(rf"\b{EDIT_VERB}\s+(?:up\s+)?(?:this|the|that|my|our|these|those)\s+(?:\w+\s+){{0,3}}?{EDIT_TARGET}\b"
                          rf"|\bmake\s+(?:this|the|that|my|our)\s+(?:\w+\s+){{0,3}}?{EDIT_TARGET}\s+(?:shorter|simpler|smaller|cleaner|less busy|more compact|more concise|brief|briefer)\b"
                          rf"|\b(?:shorter|simpler|cleaner)\s+(?:ideas|insights|dashboard|report)\s+(?:tab|page)\b", re.I)
CHART_CHECK_RE = re.compile(r"\b(?:graph|chart|plot|visual|visuali[sz]ation|diagram)\b[^.?!]{0,50}\b(?:wrong|incorrect|not (?:right|correct|proper\w*|plotted|drawn|showing|accurate|working)|isn'?t (?:right|correct|proper\w*|plotted|drawn|accurate)|"
                            r"looks? (?:wrong|off|odd|weird|broken)|broken|inaccurate|mis-?plotted|plotted (?:wrong|incorrectly|badly)|drawn (?:wrong|incorrectly))"
                            r"|\b(?:wrong|incorrect|bad|broken|inaccurate)\s+(?:graph|chart|plot)\b|\b(?:not|isn'?t|wasn'?t)\s+(?:been\s+)?(?:plotted|drawn)\s+(?:properly|correctly|right)\b"
                            r"|\b(?:plotted|drawn)\s+(?:wrong|incorrectly|badly)\b", re.I)
UNUSUAL_RE = re.compile(r"\b(?:sudden|suddenly|spikes?|spiked|abrupt|unusual|anomal\w+|outliers?|sharp (?:drop|rise|fall|increase|decrease|jump)|any (?:big|large|major|huge) (?:drop|rise|jump|fall|change)s?|(?:drop|rise|jump|fall|dip|surge)s?\s+or\s+(?:drop|rise|jump|fall|dip|surge)s?)\b", re.I)
CHOICE_RE = re.compile(r"\bwhy\b[^.?!]{0,25}\b(?:choose|chose|pick(?:ed)?|select(?:ed)?|recommend(?:ed)?|rank(?:ed)?|suggest(?:ed)?|prefer(?:red)?|put)\b[^.?!]{0,30}\b(?:it|that|this|first|top|one|idea|strategy)\b"
                       r"|\bwhy (?:is|was) (?:it|that|this)(?: one| idea| strategy)? (?:the )?(?:best|first|top|number one|#1)\b|\bhow did you (?:choose|pick|rank|decide)\b", re.I)


_NUM = r"(?:\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten)"
VIEW_EDIT2_RE = re.compile(rf"\b(?:show|keep|display|give me)\s+(?:only|just)\s+(?:the\s+)?(?:top\s+|best\s+|first\s+)?{_NUM}\s+(?:insights?|ideas?|strateg\w+|findings?|charts?|graphs?|actions?|cards?|items?)\b"
                           r"|\b(?:only|just)\s+(?:the\s+)?(?:needed|necessary|useful|supporting)\s+charts?\b|\bonly\s+(?:the\s+)?charts?\s+(?:that|which)\s+support\b|^\s*(?:please\s+)?undo(?:\s+that|\s+the last(?: change)?)?\s*[.!]?\s*$"
                           r"|\bundo\s+(?:that|the last|my last)\s+(?:view\s+)?change\b|\b(?:reset|restore)\s+(?:the |my )?view\b|\bshow\s+(?:me\s+)?(?:the\s+)?(?:full|short)\s+(?:view|version)\s+of\s+(?:the\s+)?(?:insights|ideas|report|dashboard)\b", re.I)


def wants_view_edit(question: str) -> bool:
    return bool(VIEW_EDIT_RE.search(question or "") or VIEW_EDIT2_RE.search(question or ""))


def wants_chart_check(question: str) -> bool:
    return bool(CHART_CHECK_RE.search(question or ""))


def asks_unusual(question: str) -> bool:
    return bool(UNUSUAL_RE.search(question or ""))


def asks_choice(question: str) -> bool:
    return bool(CHOICE_RE.search(question or ""))


def is_why(question: str) -> bool:
    return bool(WHY_RE.search(question or ""))


def wants_view_change(question: str) -> bool:
    return bool(VIEW_RE.search(question or "")) and not DATA_WORDS.search(question or "")


def route(question: str) -> dict | None:
    """The question's destination when plain words settle it (no AI needed), else None: {"kind": "view"|"summary"|"app"|"priority", ...}."""
    q = question or ""
    if wants_view_edit(q) or wants_view_change(q):
        return {"kind": "view"}
    if wants_chart_check(q):
        return {"kind": "recheck"}
    if SUMMARY_RE.search(q):
        return {"kind": "summary", "length": "short" if SHORT_RE.search(q) else "standard"}
    for topic, rx in APP_PATTERNS:
        if rx.search(q):
            return {"kind": "app", "topic": topic}
    if PRIORITY_RE.search(q):
        return {"kind": "priority", "terms": priority_terms(q)}
    return None


def priority_terms(question: str) -> list[str]:
    """The words after "I care about ..." and the like. Used when no AI picked the framing."""
    m = PRIORITY_PHRASE.search(question or "")
    if not m:
        return []
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z\-]+", m.group("p")) if w.lower() not in GENERIC_STOP]
    return words[:5]
