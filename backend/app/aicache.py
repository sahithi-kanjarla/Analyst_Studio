"""A small on-disk cache for AI results. Two layers use it: the data understanding record and the proposed data concerns (keyed on the fact sheet, goal, description,
glossary answers, model and prompt), and every single AI call (llm.py: keyed on the full prompt text, model name and temperature). AI_CACHE=off turns it off.

The key is a hash of everything that can change the answer: the fact sheet, the goal, the business description, the user's saved glossary answers, the model name and the
prompt text. A different data table, goal, description, glossary, model or prompt is therefore a miss. Only successful results are stored. Entries older than CACHE_DAYS
(env var, default 7) are ignored. A hit is never silent: the caller reports it in the run log (REUSED).
Nothing here knows a column name or a domain word."""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time

from .config import DATA_DIR
from pathlib import Path

from .config import DATA_DIR, ROOT

REUSED = "Reused saved AI result"
DEFAULT_DAYS = 7


_tls = threading.local()


def enabled() -> bool:
    return os.environ.get("AI_CACHE", "on").strip().lower() not in ("off", "0", "false", "no")


def note_reused(stage: str) -> None:
    """Remember, for this run, that a saved AI result was used for `stage` (the report says so)."""
    if stage not in reused_stages():
        _tls.reused = [*reused_stages(), stage]


def reused_stages() -> list[str]:
    return list(getattr(_tls, "reused", []))


def reset_reused() -> None:
    _tls.reused = []


def call_key(system: str, user: str, model: str, temperature) -> str:
    """The key of one AI call: the full prompt text, the model and the temperature."""
    blob = json.dumps({"system": system, "user": user, "model": model, "temperature": temperature}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def temperature_of(llm):
    """The temperature a chat object will use: set on a binding, else on the model itself (None when it says nothing)."""
    for obj in (llm, getattr(llm, "bound", None)):
        if obj is None:
            continue
        for attr in ("kwargs", "settings"):
            d = getattr(obj, attr, None)
            if isinstance(d, dict) and "temperature" in d:
                return d["temperature"]
        t = getattr(obj, "temperature", None)
        if isinstance(t, (int, float)):
            return t
    return None


def cache_dir() -> Path:
    return Path(os.environ.get("AI_CACHE_DIR") or DATA_DIR / "ai_cache")


def cache_days() -> float:
    try:
        return float(os.environ.get("CACHE_DAYS", DEFAULT_DAYS))
    except ValueError:
        return float(DEFAULT_DAYS)


def model_name(llm) -> str:
    """The model behind a chat object, as best its attributes say (a bound model keeps the model on `.bound`)."""
    inner = getattr(llm, "bound", None) or llm
    for attr in ("model_name", "model", "model_id"):
        v = getattr(inner, attr, None)
        if isinstance(v, str) and v:
            return v
    return type(inner).__name__


def key(kind: str, sheet: dict, goal: str, description: str, glossary: dict | None, model: str, prompt: str) -> str:
    blob = json.dumps({"kind": kind, "sheet": sheet, "goal": goal or "", "description": description or "", "glossary": glossary or {}, "model": model, "prompt": prompt},
                      sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def glossary_answers(saved: dict | None) -> dict:
    """{question id: saved value} - the only part of the glossary that can steer the AI."""
    return {k: (v.get("value") if isinstance(v, dict) else v) for k, v in sorted(((saved or {}).get("answers") or {}).items())}


def get(k: str):
    """The stored result, or None when there is none, it expired, the cache is off or the file is unreadable."""
    if not enabled():
        return None
    try:
        entry = json.loads((cache_dir() / f"{k}.json").read_text(encoding="utf-8"))
        if time.time() - float(entry["saved"]) > cache_days() * 86400:
            return None
        return entry["value"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def put(k: str, value) -> None:
    """Store a successful result. A failure to write only costs the saving."""
    if not enabled():
        return
    try:
        d = cache_dir()
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{k}.json").write_text(json.dumps({"saved": time.time(), "value": value}, default=str), encoding="utf-8")
    except OSError:
        pass
