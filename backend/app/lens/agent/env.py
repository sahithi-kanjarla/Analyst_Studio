"""Everything one request may use, built once: the run's data and understanding record, the report's records, the view state, the AI model and the call budget. Never raw rows in anything sent to the AI."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

MAX_CALLS = 2            # normal budget per request: the plan, and one repair or one wording call
EXTRA_CUSTOM = 2         # custom analysis may use up to 2 extra calls to repair its query


@dataclass
class Env:
    wd: Path
    result: dict
    df: object
    ctx: object
    question: str = ""
    tab: str = ""
    llm: object = None
    ai_ok: bool = False
    focus: dict | None = None               # item_context of the focused item
    view_state: dict | None = None          # the sample run's state held by the browser; None = the run's saved state
    cat: dict = field(default_factory=dict)
    facts: dict[str, dict] = field(default_factory=dict)
    calls: int = 0
    extra_calls: int = 0
    notes: list[str] = field(default_factory=list)
    view_base: dict | None = None           # the view state when the request began: one Undo goes back to it for the whole request

    def can_call(self, extra: bool = False) -> bool:
        if not (self.ai_ok and self.llm is not None):
            return False
        return self.calls < MAX_CALLS + (EXTRA_CUSTOM if extra else 0)

    def spend(self, n: int = 1) -> None:
        self.calls += n
