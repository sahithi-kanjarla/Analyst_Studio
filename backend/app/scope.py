"""What the question writer must NOT ask about: presentation and scope.

The app analyses every reasonable view and ranks the results, so asking which chart, grouping, column, time frame or view to
show is never useful. This is the app's own analytics vocabulary, not a vocabulary about any business domain, which is why it
lives apart from the question logic (see test_clarify.py::test_question_logic_has_no_domain_words)."""
from __future__ import annotations

import re

SCOPE_RX = re.compile(
    r"\b(chart|graph|plot|visuali[sz]\w*|dashboard|diagram|time ?frames?|breakdowns?|segment\w*|group(?:ed)? by|split by|slice|drill|"
    r"which view|what view|show me|display|which (?:column|field|variable|metric|measure)s? (?:to|should we|do you want to|would you like to) (?:use|show|plot|analy[sz]e|look|focus|compare))\b",
    re.I)


def is_scope_question(text: str) -> bool:
    return bool(SCOPE_RX.search(text or ""))
