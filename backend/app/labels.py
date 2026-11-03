"""What to call a column in a sentence: by its own name, and for a column of states (paid, pending, refunded; open, closed) by what it is, a status; never by a grouping word the data did not give.
The test is the column's name and the vocabulary of its values, not any one column."""
from __future__ import annotations

import re

STATUS_NAME = re.compile(r"(?i)\b(?:status|stage|condition|sthiti|outcome|disposition)\b")
STATUS_VALUES = {"paid", "unpaid", "pending", "refunded", "partially refunded", "partially paid", "voided", "authorized", "cancelled", "canceled", "shipped", "delivered", "returned", "fulfilled", "unfulfilled", "partial",
                 "processing", "open", "closed", "active", "inactive", "completed", "complete", "failed", "approved", "rejected", "draft", "on hold", "expired", "settled", "dispatched", "in transit", "new", "won", "lost",
                 "pura hua", "baaki", "aadha"}
MAX_STATUS_VALUES = 12


def _norm(v) -> str:
    return re.sub(r"[\s_\-]+", " ", str(v).strip().lower())


def is_status_like(name: str, values=()) -> bool:
    """A column named like a status, or a short list of values that are nearly all states of something (paid, pending, refunded ...)."""
    if STATUS_NAME.search(str(name).replace("_", " ")):
        return True
    vals = {_norm(v) for v in list(values)[:MAX_STATUS_VALUES + 1] if str(v).strip()}
    return 2 <= len(vals) <= MAX_STATUS_VALUES and sum(v in STATUS_VALUES for v in vals) >= 0.6 * len(vals)


def shown(name: str) -> str:
    """The column's own name for a sentence: underscores become spaces, nothing else changes."""
    return str(name).replace("_", " ") if "_" in str(name) else str(name)


def split_groups(groups: list[str], values_of) -> tuple[list[str], list[str]]:
    """(status-like columns, the others), each in order. `values_of(name)` returns a few of the column's values."""
    status = [g for g in groups if is_status_like(g, values_of(g))]
    return status, [g for g in groups if g not in status]


INVENTED = re.compile(r"(?i)\b(?:customer|client|buyer|user|shopper)\s+(?:segments?|groups?|types?|tiers?)\b")


def invented_grouping(text: str, columns) -> str:
    """"" or the grouping phrase ("customer segments") a sentence uses that no column of the table gives."""
    m = INVENTED.search(text or "")
    if not m:
        return ""
    words = set(re.findall(r"[a-z]+", " ".join(str(c) for c in columns).lower().replace("_", " ")))
    return "" if m.group(0).lower().split()[-1].rstrip("s") in {w.rstrip("s") for w in words} else m.group(0)
