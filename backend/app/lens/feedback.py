"""A thumbs up or down on an answer, recorded in lens_feedback.jsonl in the run folder with the plan and the tools used (and the request text and tab; never data values), and kept on the turn so it
shows after a reload. `scripts/lens_feedback_report.py` summarises the file together with lens_unmet.jsonl."""
from __future__ import annotations

import json
import time
from pathlib import Path

from . import store

FILE = "lens_feedback.jsonl"
VOTES = ("up", "down")


def record(wd: Path, turn_id: str, vote: str) -> dict | None:
    """The saved feedback line, or None when the turn does not exist or the vote is not up or down. A second vote on the same answer replaces the first on the turn (both lines stay in the file)."""
    if vote not in VOTES:
        return None
    t = store.get_turn(wd, turn_id)
    if t is None:
        return None
    a = t["answer"]
    ag = a.get("agent") or {}
    line = {"turn": turn_id, "vote": vote, "request": " ".join(str(t.get("question") or "").split())[:300], "tab": t.get("tab") or "", "at": time.time(), "status": a.get("status"), "kind": a.get("kind") or "data",
            "plan": ag.get("plan") or [], "tools": ag.get("tools") or [], "unmet": bool(a.get("unmet"))}
    try:
        with open(Path(wd) / FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(line, default=str) + "\n")
    except OSError:
        return None
    a["feedback"] = vote
    store.replace_turn(wd, t)
    return line
