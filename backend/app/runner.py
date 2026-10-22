"""Run lifecycle: create run folder, execute the graph in a worker thread, persist status/result."""
from __future__ import annotations

import json
import threading
import time
import traceback
import uuid
from pathlib import Path

from .config import RUNS_DIR
from .events import emit, set_quiet

RUN_ID_RE = r"^[a-f0-9]{12}$"


def run_dir(run_id: str) -> Path:
    return RUNS_DIR / run_id


def write_status(run_id: str, status: str, **extra) -> None:
    p = run_dir(run_id) / "status.json"
    cur = json.loads(p.read_text()) if p.exists() else {}
    cur.update(status=status, updated=time.time(), **extra)
    p.write_text(json.dumps(cur))


def read_status(run_id: str) -> dict | None:
    p = run_dir(run_id) / "status.json"
    return json.loads(p.read_text()) if p.exists() else None


def _safe_filename(name: str, used: set[str]) -> str:
    safe = "".join(ch for ch in Path(name).name if ch.isalnum() or ch in "._-") or "data.csv"
    stem, suffix, n = Path(safe).stem, Path(safe).suffix, 2
    while safe in used:
        safe, n = f"{stem}_{n}{suffix}", n + 1
    used.add(safe)
    return safe


DEFAULT_EXPLORE_GOAL = "Explore this data and surface the most important insights and opportunities for the business."


def create_run(goal: str, files_or_name, content: bytes | None = None, context: dict | None = None) -> str:
    """create_run(goal, [(filename, bytes), ...], context=...)  or, for one file, create_run(goal, filename, bytes).
    An empty goal means exploratory mode."""
    from .context import normalise_context
    items = [(files_or_name, content)] if content is not None else list(files_or_name)
    context = normalise_context(context)
    if not goal.strip():
        goal, context["explore"] = DEFAULT_EXPLORE_GOAL, True
    run_id = uuid.uuid4().hex[:12]
    wd = run_dir(run_id)
    (wd / "inputs").mkdir(parents=True)  # uploads live in their own folder so they can never overwrite status/result files
    used: set[str] = set()
    names = []
    for filename, data in items:
        safe = _safe_filename(filename, used)
        (wd / "inputs" / safe).write_bytes(data)
        names.append(safe)
    (wd / "input.json").write_text(json.dumps({"goal": goal, "files": names, "context": context}))
    write_status(run_id, "queued", goal=goal, filename=", ".join(names), created=time.time())
    return run_id


def _execute(run_id: str) -> None:
    from .graph import GRAPH  # imported lazily so the API starts fast
    wd = run_dir(run_id)
    meta = json.loads((wd / "input.json").read_text())
    write_status(run_id, "running")
    if (wd / "answers.json").exists():
        set_quiet(run_id, True)  # resumed after a pause: profile/relate run again but must not repeat their timeline events
    try:
        context = meta.get("context") or {}
        final = GRAPH.invoke({"run_id": run_id, "goal": meta["goal"], "data_paths": [str(wd / "inputs" / f) for f in meta["files"]],
                              "workdir": str(wd), "iteration": 0, "findings": [], "context": context, "explore": bool(context.get("explore"))})
        set_quiet(run_id, False)
        if final.get("awaiting_input"):
            write_status(run_id, "needs_input")
            return
        keys = ("goal", "profile", "plan", "cleaning_log", "findings", "critique", "report_md", "powerbi", "llm_mode", "data_model",
                "context", "strategies", "strategy_summary", "data_gaps", "key_metrics", "applied_definitions", "definitions")
        result = {k: final.get(k) for k in keys}
        result["run_id"] = run_id
        (wd / "result.json").write_text(json.dumps(result, default=str))
        emit(run_id, "System", "Done.")
        write_status(run_id, "done")
    except Exception as e:
        set_quiet(run_id, False)
        emit(run_id, "System", f"The run failed: {type(e).__name__}: {e}")
        (wd / "error.txt").write_text(traceback.format_exc())
        write_status(run_id, "error", error=f"{type(e).__name__}: {e}")


def start_run(run_id: str) -> threading.Thread:
    t = threading.Thread(target=_execute, args=(run_id,), daemon=True)
    t.start()
    return t


_resume_lock = threading.Lock()


def resume_with_answers(run_id: str, payload: dict) -> bool:
    """Save the answers and restart a paused run. The status flips to "running" before the thread starts, under a lock,
    so two submits can never start two runs. Returns False if the run is not waiting for input."""
    with _resume_lock:
        st = read_status(run_id)
        if not st or st.get("status") != "needs_input":
            return False
        (run_dir(run_id) / "answers.json").write_text(json.dumps(payload), encoding="utf-8")
        write_status(run_id, "running")
        start_run(run_id)
        return True
