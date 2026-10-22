"""Business glossary: answers to clarifying questions and free-text definitions, saved per company so the next run pre-fills them.

Stored as one JSON file, keyed by workspace (the company name, or "default"):
  {"acme": {"answers": {"date_col": {"value": "ship_date", "question": "...", "updated": 1.7e9}},
            "definitions": {"active customer": "bought in the last 90 days"}, "notes": "..."}}

Everything the user types is unverified context: it is passed to the LLM as such and never treated as a fact about the data.
Writes are serialised (thread lock + lock file) and atomic (temp file + replace). The API in front of this has no authentication."""
from __future__ import annotations

import json
import os
import re
import threading
import time
from pathlib import Path

from .config import ROOT

NOTES_MAX = 500          # characters of free-text notes per workspace
DEF_TEXT_MAX = 300       # characters per definition
DEF_TERM_MAX = 60
DEF_COUNT_MAX = 20
ANSWER_VALUE_MAX = 120
ANSWER_COUNT_MAX = 100

_lock = threading.Lock()


def glossary_path() -> Path:
    return Path(os.environ.get("GLOSSARY_PATH") or ROOT / "data" / "glossary.json")


def workspace_key(ctx: dict | None) -> str:
    name = re.sub(r"\s+", " ", str((ctx or {}).get("company") or "")).strip().lower()[:80]
    return name or "default"


def clean_key(name: str) -> str:
    return re.sub(r"\s+", " ", str(name or "")).strip().lower()[:80] or "default"


def _empty() -> dict:
    return {"answers": {}, "definitions": {}, "notes": ""}


def _read() -> dict:
    p = glossary_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


class _FileLock:
    """Cross-process guard: an exclusively-created lock file next to the glossary. A lock older than 30s is treated as stale."""

    def __init__(self, path: Path):
        self.path = path.with_suffix(path.suffix + ".lock")

    def __enter__(self):
        deadline = time.time() + 5
        while True:
            try:
                os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                return self
            except FileExistsError:
                try:
                    if time.time() - self.path.stat().st_mtime > 30:
                        self.path.unlink()
                        continue
                except OSError:
                    pass
                if time.time() > deadline:
                    raise TimeoutError("The glossary file is locked by another process.")
                time.sleep(0.05)

    def __exit__(self, *exc):
        try:
            self.path.unlink()
        except OSError:
            pass


def _modify(fn) -> dict:
    p = glossary_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _lock, _FileLock(p):
        data = _read()
        fn(data)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, p)
        return data


def get_workspace(key: str) -> dict:
    ws = _read().get(clean_key(key))
    if not isinstance(ws, dict):
        return _empty()
    return {"answers": dict(ws.get("answers") or {}), "definitions": dict(ws.get("definitions") or {}), "notes": str(ws.get("notes") or "")}


def all_workspaces() -> dict:
    return {k: get_workspace(k) for k in _read()}


def validate_entry(entry: dict) -> str | None:
    """Returns an error message, or None when the entry is within the size limits."""
    answers, defs, notes = entry.get("answers") or {}, entry.get("definitions") or {}, entry.get("notes") or ""
    if not isinstance(answers, dict) or not isinstance(defs, dict) or not isinstance(notes, str):
        return "answers and definitions must be objects and notes a string."
    if len(notes) > NOTES_MAX:
        return f"Notes are limited to {NOTES_MAX} characters."
    if len(defs) > DEF_COUNT_MAX:
        return f"At most {DEF_COUNT_MAX} definitions can be saved."
    for term, text in defs.items():
        if not isinstance(text, str) or not term.strip() or len(term) > DEF_TERM_MAX:
            return f"A term must be 1-{DEF_TERM_MAX} characters and its definition text."
        if len(text) > DEF_TEXT_MAX:
            return f"Definition of '{term[:30]}' is longer than {DEF_TEXT_MAX} characters."
    if len(answers) > ANSWER_COUNT_MAX:
        return f"At most {ANSWER_COUNT_MAX} saved answers per workspace."
    for qid, a in answers.items():
        v = a.get("value") if isinstance(a, dict) else a
        if not isinstance(v, str) or not v or len(v) > ANSWER_VALUE_MAX or len(qid) > 200:
            return f"Answer '{qid[:40]}' is not a short text value."
    return None


def put_workspace(key: str, entry: dict) -> dict:
    """Replace a workspace's saved entry. Editing or deleting an item in the UI is a PUT of the entry without it."""
    now = time.time()
    answers = {}
    for qid, a in (entry.get("answers") or {}).items():
        a = a if isinstance(a, dict) else {"value": a}
        answers[qid] = {"value": a["value"], "question": str(a.get("question") or "")[:300], "updated": a.get("updated") or now}
    new = {"answers": answers, "definitions": {t.strip(): x.strip() for t, x in (entry.get("definitions") or {}).items()}, "notes": (entry.get("notes") or "").strip()}
    _modify(lambda d: d.__setitem__(clean_key(key), new))
    return new


def merge_answers(key: str, answers: dict[str, str], questions: dict[str, str], notes: str = "", definitions: dict[str, str] | None = None) -> dict:
    """Add the answers given for a run to the workspace (existing items are overwritten, others kept)."""
    now = time.time()
    ck = clean_key(key)

    def fn(d):
        ws = d.setdefault(ck, _empty())
        ws.setdefault("answers", {}).update({q: {"value": v, "question": questions.get(q, "")[:300], "updated": now} for q, v in answers.items()})
        ws.setdefault("definitions", {}).update({t.strip(): x.strip() for t, x in (definitions or {}).items() if t.strip() and x.strip()})
        if notes.strip():
            ws["notes"] = notes.strip()
    _modify(fn)
    return get_workspace(ck)


def delete_workspace(key: str) -> bool:
    found = []
    _modify(lambda d: found.append(d.pop(clean_key(key), None) is not None))
    return bool(found and found[0])


def definitions_text(ws: dict | None, run_notes: str = "") -> str:
    """Free-text part of a workspace, formatted for LLM prompts. Always labelled unverified by the caller."""
    ws = ws or {}
    lines = [f"{t}: {x}" for t, x in (ws.get("definitions") or {}).items()]
    note = (run_notes or ws.get("notes") or "").strip()
    if note:
        lines.append(f"Notes: {note}")
    return "\n".join(lines)
