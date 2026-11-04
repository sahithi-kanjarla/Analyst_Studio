"""Run lifecycle: create run folder, execute the graph in a worker thread, persist status/result."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import traceback
import uuid
from pathlib import Path

from .config import DEMO_DIR, RUNS_DIR, max_concurrent_runs, retention_days
from . import reporter
from .events import emit, set_quiet

RUN_ID_RE = r"^(?:[a-f0-9]{12}|[a-f0-9]{32})$"      # new ids are 32 characters (a full uuid4); runs made earlier keep their 12-character ids
DEMO_RUN_ID = "5a1e5d3e0001"  # the sample run's id: a copy of sample_data/demo/sales_run, read-only and kept by the retention sweep


def is_demo(run_id: str) -> bool:
    return run_id == DEMO_RUN_ID


def ensure_demo_run() -> str:
    """Copy the shipped sample run into the runs folder, and again whenever the shipped copy changes (or the copy was removed). Returns its id.
    Nothing in it is ever re-run."""
    import shutil
    wd = run_dir(DEMO_RUN_ID)
    shipped = DEMO_DIR / "result.json"
    if not (wd / "result.json").exists() or (wd / "result.json").read_bytes() != shipped.read_bytes():
        shutil.rmtree(wd, ignore_errors=True)
        shutil.copytree(DEMO_DIR, wd)
    return DEMO_RUN_ID


def run_dir(run_id: str) -> Path:
    return RUNS_DIR / run_id


def write_status(run_id: str, status: str, **extra) -> None:
    p = run_dir(run_id) / "status.json"
    cur = json.loads(p.read_text()) if p.exists() else {}
    cur.update(status=status, updated=time.time(), **extra)
    tmp = p.with_name(f"status.{threading.get_ident()}.tmp")      # a reader polling the status never sees a half-written file
    tmp.write_text(json.dumps(cur))
    for attempt in range(10):     # on Windows os.replace raises PermissionError while a poller (or antivirus) has the target open
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            time.sleep(0.02 * (attempt + 1))
    try:
        p.write_text(json.dumps(cur))     # last resort: still blocked, write in place rather than killing the run
    finally:
        tmp.unlink(missing_ok=True)


def read_status(run_id: str) -> dict | None:
    p = run_dir(run_id) / "status.json"
    for attempt in range(5):      # on Windows a replace can briefly block a read
        try:
            return json.loads(p.read_text())
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            time.sleep(0.02 * (attempt + 1))
    return None


def _safe_filename(name: str, used: set[str]) -> str:
    safe = "".join(ch for ch in Path(name).name if ch.isalnum() or ch in "._-") or "data.csv"
    stem, suffix, n = Path(safe).stem, Path(safe).suffix, 2
    while safe in used:
        safe, n = f"{stem}_{n}{suffix}", n + 1
    used.add(safe)
    return safe


from .context import DEFAULT_EXPLORE_GOAL, typed_goal      # noqa: E402  (re-exported: main.py imports it from here)


def create_run(goal: str, files_or_name, content: bytes | None = None, context: dict | None = None, review_saved: bool = False, owner: str | None = None) -> str:
    """create_run(goal, [(filename, bytes), ...], context=...)  or, for one file, create_run(goal, filename, bytes).
    An empty goal means exploratory mode. `review_saved` pauses the run to show the saved answers it will reuse, even when they cover every question."""
    from .context import normalise_context
    items = [(files_or_name, content)] if content is not None else list(files_or_name)
    context = normalise_context(context)
    if not goal.strip():
        goal, context["explore"] = DEFAULT_EXPLORE_GOAL, True
    run_id = uuid.uuid4().hex
    wd = run_dir(run_id)
    (wd / "inputs").mkdir(parents=True)  # uploads live in their own folder so they can never overwrite status/result files
    used: set[str] = set()
    names = []
    for filename, data in items:
        safe = _safe_filename(filename, used)
        (wd / "inputs" / safe).write_bytes(data)
        names.append(safe)
    (wd / "input.json").write_text(json.dumps({"goal": goal, "files": names, "context": context, "review_saved": review_saved}))
    write_status(run_id, "queued", goal=typed_goal(goal), filename=", ".join(names), created=time.time(), **({"owner": owner} if owner else {}))     # owner: the hash of the browser's token (owner.py), never the token
    return run_id


def _owner_kw(src_id: str) -> dict:
    """A re-run belongs to whoever owned the run it was made from."""
    o = (read_status(src_id) or {}).get("owner")
    return {"owner": o} if o else {}


QUEUED_MESSAGE = "Waiting for other runs to finish"
_slots: threading.BoundedSemaphore | None = None
_slots_lock = threading.Lock()


def _run_slots() -> threading.BoundedSemaphore:
    """The semaphore that limits simultaneous runs, made on first use from MAX_CONCURRENT_RUNS."""
    global _slots
    with _slots_lock:
        if _slots is None:
            _slots = threading.BoundedSemaphore(max_concurrent_runs())
        return _slots


def _reset_slots() -> None:
    """Forget the semaphore so the next run reads MAX_CONCURRENT_RUNS again (tests)."""
    global _slots
    with _slots_lock:
        _slots = None


def _execute(run_id: str) -> None:
    slots = _run_slots()
    if not slots.acquire(blocking=False):      # every slot is busy: say so, then wait for one
        write_status(run_id, "queued", message=QUEUED_MESSAGE)
        emit(run_id, "System", QUEUED_MESSAGE, queued=True)
        slots.acquire()
    try:
        _execute_scoped(run_id)
    finally:
        slots.release()


def _execute_scoped(run_id: str) -> None:
    from . import glossary, owner
    tok = glossary.set_scope(owner.scope_of(run_id))        # the run reads and saves the glossary of its own owner only
    try:
        _execute_in_scope(run_id)
    finally:
        glossary.reset_scope(tok)


def _execute_in_scope(run_id: str) -> None:
    from .graph import GRAPH  # imported lazily so the API starts fast
    wd = run_dir(run_id)
    meta = json.loads((wd / "input.json").read_text())
    write_status(run_id, "running", message=None)
    from . import llm as llm_mod
    llm_mod.begin_run(cache=True)
    if (wd / "answers.json").exists():
        set_quiet(run_id, True)  # resumed after a pause: profile/relate run again but must not repeat their timeline events
    try:
        context = meta.get("context") or {}
        final = GRAPH.invoke({"run_id": run_id, "goal": typed_goal(meta["goal"]), "data_paths": [str(wd / "inputs" / f) for f in meta["files"]],
                              "workdir": str(wd), "iteration": 0, "remove_derived": meta.get("remove_derived") or [], "review_saved": bool(meta.get("review_saved")), "findings": [], "context": context, "explore": bool(context.get("explore"))})
        set_quiet(run_id, False)
        if final.get("awaiting_input"):
            write_status(run_id, "needs_input")
            return
        keys = ("goal", "profile", "plan", "cleaning_log", "findings", "critique", "report_md", "report_view", "powerbi", "llm_mode", "data_model",
                "context", "strategies", "strategy_summary", "data_gaps", "key_metrics", "applied_definitions", "ignored_definitions", "definitions", "derived_metrics", "goal_answer", "summary_facts", "decisions", "run_mode", "stage_sources", "brief", "warnings", "ai_notes", "concerns", "understanding", "curation")
        result = {k: final.get(k) for k in keys}
        result["run_id"] = run_id
        notices = [*(result.get("ai_notes") or []), *(result.get("warnings") or [])]
        if result.get("report_md") and notices:       # a stage after the report (Power BI) may have failed, or the report may have been written before the notes were known
            result["report_md"] = reporter.with_notices(result["report_md"], notices)
            (wd / "report.md").write_text(result["report_md"], encoding="utf-8")
        (wd / "result.json").write_text(json.dumps(result, default=str))
        emit(run_id, "System", "Done" + (f", with {len(result.get('warnings') or [])} stage warning(s)." if result.get("warnings") else "."))
        write_status(run_id, "done", warnings=result.get("warnings") or [], ai_notes=result.get("ai_notes") or [])
    except Exception as e:
        set_quiet(run_id, False)
        emit(run_id, "System", f"The run failed: {type(e).__name__}: {e}")
        (wd / "error.txt").write_text(traceback.format_exc())
        write_status(run_id, "error", error=f"{type(e).__name__}: {e}")
    finally:
        try:
            llm_mod.save_usage(wd)      # every run, finished, paused or failed, keeps its AI calls
            t = llm_mod.usage_summary()
            if t["calls"]:
                emit(run_id, "System", f"AI usage: {t['sent']} call(s) sent, {t['cached']} from saved results; {t['input_tokens']:,} input and {t['output_tokens']:,} output tokens" + (" (estimated)" if t["estimated"] else "") + " - per stage in ai_usage.json")
        except OSError:
            pass


def purge_expired(days: float | None = None, now: float | None = None) -> list[str]:
    """Delete finished or failed runs (their status, result and uploads) older than RETENTION_DAYS. Runs still queued or running are never touched,
    and days=0 keeps everything. Returns the ids that were deleted."""
    import shutil
    days = retention_days() if days is None else days
    if days <= 0 or not RUNS_DIR.exists():
        return []
    cutoff = (time.time() if now is None else now) - days * 86400
    removed = []
    for d in RUNS_DIR.iterdir():
        if not re.match(RUN_ID_RE, d.name) or is_demo(d.name):
            continue
        st = read_status(d.name) or {}
        if st.get("status") in ("queued", "running", None):
            continue
        if float(st.get("updated") or st.get("created") or 0) < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            removed.append(d.name)
    return removed


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


def rerun_with_answers(run_id: str, answers: dict, ask_again: list[str]) -> str:
    """Start a new run on the same files, goal and context with the given answers (question id -> option value), from the cleaning step on.
    A question in `ask_again` has no answer: the new run pauses and asks it. The old run is untouched."""
    import shutil
    src, src_id = run_dir(run_id), run_id
    meta = json.loads((src / "input.json").read_text())
    new_id = uuid.uuid4().hex
    dst = run_dir(new_id)
    shutil.copytree(src / "inputs", dst / "inputs")
    stored = json.loads((src / "questions.json").read_text(encoding="utf-8"))
    for q in stored["questions"]:
        if q["id"] in ask_again:
            q["prefill"], q["prefill_source"] = q["default"], "default"
        elif q["id"] in answers:
            q["prefill"], q["prefill_source"] = answers[q["id"]], "user"
    (dst / "questions.json").write_text(json.dumps(stored), encoding="utf-8")
    if not ask_again:
        old = json.loads((src / "answers.json").read_text(encoding="utf-8")) if (src / "answers.json").exists() else {}
        (dst / "answers.json").write_text(json.dumps({"answers": answers, "skip": False, "notes": old.get("notes", "")}), encoding="utf-8")
    (dst / "input.json").write_text(json.dumps({**meta, "review_saved": False}))
    write_status(new_id, "queued", goal=typed_goal(meta["goal"]), filename=", ".join(meta["files"]), created=time.time(), **_owner_kw(src_id))
    start_run(new_id)
    return new_id


def rerun_without_derived(run_id: str, names: list[str]) -> str:
    """Start a new run on the same files, goal, context and answers, leaving out the derived metrics the user removed. The old run is untouched."""
    import shutil
    src, src_id = run_dir(run_id), run_id
    meta = json.loads((src / "input.json").read_text())
    new_id = uuid.uuid4().hex
    dst = run_dir(new_id)
    shutil.copytree(src / "inputs", dst / "inputs")
    for f in ("questions.json", "answers.json"):
        if (src / f).exists():
            shutil.copy(src / f, dst / f)
    removed = list(dict.fromkeys(list(meta.get("remove_derived") or []) + names))
    (dst / "input.json").write_text(json.dumps({**meta, "remove_derived": removed}))
    write_status(new_id, "queued", goal=typed_goal(meta["goal"]), filename=", ".join(meta["files"]), created=time.time(), **_owner_kw(src_id))
    start_run(new_id)
    return new_id
