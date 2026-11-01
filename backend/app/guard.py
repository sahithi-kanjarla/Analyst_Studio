"""Public-use protections. Every one is configured by an environment variable and has a safe default; none changes what a run computes.

UPLOADS_REQUIRE_CODE   when set to a code, uploads and AI runs (and typed Lens questions) need that access code. The sample report and its demo stay open to everyone. Unset: open.
DAILY_AI_RUNS          most analyses that use the AI per day, across all visitors (default 100; 0 = no cap). A run with no AI available does not count.
DAILY_LENS_QUESTIONS   most Lens questions that use the AI per day, across all visitors (default 500; 0 = no cap). Suggested questions and chart controls never count.
UPLOAD_RATE_LIMIT      uploads one visitor may start per window (default 10; 0 = no limit). UPLOAD_RATE_WINDOW_S is the window (default 3600 s).
TRUST_PROXY_HEADERS    1 = identify a visitor by the first X-Forwarded-For address (only behind a proxy you control); default 0 = the connecting address.
The day is the UTC day. Counts are saved in usage_daily.json next to the runs folder so a restart does not reset the cap."""
from __future__ import annotations

import hmac
import json
import os
import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from .config import RUNS_DIR

_lock = threading.Lock()
_uploads: dict[str, deque] = defaultdict(deque)


def _int(name: str, default: int) -> int:
    try:
        return max(0, int(float(os.environ.get(name, default))))
    except ValueError:
        return default


def _now() -> float:        # patched in tests
    return time.time()


# ------------------------------------------------------------------ access code
def required_code() -> str:
    return os.environ.get("UPLOADS_REQUIRE_CODE", "").strip()


def code_required() -> bool:
    return bool(required_code())


def check_code(given: str | None) -> None:
    """403 with a friendly message when a code is required and the one given is missing or wrong. Compared in constant time."""
    need = required_code()
    if not need:
        return
    if not given or not hmac.compare_digest(str(given).strip().encode(), need.encode()):
        raise HTTPException(403, "An access code is needed to upload data or run an analysis. Enter the code you were given on the upload panel. The sample report is open to everyone." if not given
                            else "That access code was not accepted. Please check it and try again. The sample report is open to everyone.")


def code_from(request: Request, form_value: str | None = None) -> str | None:
    return form_value or request.headers.get("x-access-code")


# ------------------------------------------------------------------ per-visitor upload rate limit
def visitor(request: Request) -> str:
    if os.environ.get("TRUST_PROXY_HEADERS", "0").strip().lower() in ("1", "true", "yes", "on"):
        fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        if fwd:
            return fwd
    return request.client.host if request.client else "unknown"


def check_upload_rate(request: Request) -> None:
    limit, window = _int("UPLOAD_RATE_LIMIT", 10), _int("UPLOAD_RATE_WINDOW_S", 3600) or 3600
    if limit <= 0:
        return
    who, now = visitor(request), _now()
    with _lock:
        q = _uploads[who]
        while q and now - q[0] >= window:
            q.popleft()
        if len(q) >= limit:
            wait = int(q[0] + window - now) + 1
            mins = max(1, -(-wait // 60))
            raise HTTPException(429, f"You have started {limit} uploads in a short time, which is the limit for one visitor. Please try again in about {mins} minute{'s' if mins != 1 else ''}. The sample report is always open.")
        q.append(now)


def reset_rate() -> None:
    with _lock:
        _uploads.clear()


# ------------------------------------------------------------------ daily caps across all visitors
def _usage_path():
    return RUNS_DIR.parent / "usage_daily.json"


def _today() -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(_now()))


def _load() -> dict:
    try:
        d = json.loads(_usage_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    return d if d.get("day") == _today() else {"day": _today(), "ai_runs": 0, "lens_questions": 0}


def used(kind: str) -> int:
    with _lock:
        return int(_load().get(kind, 0))


def under_cap(kind: str) -> bool:
    cap = _int("DAILY_AI_RUNS", 100) if kind == "ai_runs" else _int("DAILY_LENS_QUESTIONS", 500)
    return cap <= 0 or used(kind) < cap


def count(kind: str, n: int = 1) -> None:
    with _lock:
        d = _load()
        d[kind] = int(d.get(kind, 0)) + n
        try:
            _usage_path().write_text(json.dumps(d), encoding="utf-8")
        except OSError:
            pass


AI_RUNS_MESSAGE = "Today's limit of AI analyses has been reached for all visitors, to keep this free service available. Please try again tomorrow. The sample report is open now."
LENS_MESSAGE = "Today's limit of AI questions has been reached for all visitors. The suggested questions and chart controls still work, and the limit resets tomorrow."


def reserve_ai_run() -> None:
    """429 with a friendly message when today's cap on AI analyses is used up; otherwise counts this one."""
    with _lock:
        d = _load()
        cap = _int("DAILY_AI_RUNS", 100)
        if cap > 0 and int(d.get("ai_runs", 0)) >= cap:
            raise HTTPException(429, AI_RUNS_MESSAGE)
        d["ai_runs"] = int(d.get("ai_runs", 0)) + 1
        try:
            _usage_path().write_text(json.dumps(d), encoding="utf-8")
        except OSError:
            pass
