"""Tiny in-process event bus: agents emit events, the API streams them to the UI via SSE.
Events are also appended to events.jsonl so a finished run can be replayed after a restart."""
import json
import threading
import time

from .config import RUNS_DIR

_lock = threading.Lock()
_events: dict[str, list[dict]] = {}


def emit(run_id: str, agent: str, message: str, **data) -> dict:
    ev = {"t": round(time.time(), 3), "agent": agent, "message": message, "data": data}
    with _lock:
        _events.setdefault(run_id, []).append(ev)
    path = RUNS_DIR / run_id / "events.jsonl"
    try:
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(ev, default=str) + "\n")
    except OSError:
        pass
    return ev


def get_events(run_id: str, start: int = 0) -> list[dict]:
    with _lock:
        if run_id not in _events:
            path = RUNS_DIR / run_id / "events.jsonl"
            if path.exists():
                _events[run_id] = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        return list(_events.get(run_id, []))[start:]
