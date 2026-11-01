"""Request pacing: spaces AI calls so they fit a per-minute token window.
Before each call the caller says how many tokens it expects to use (input plus a reserve for the reply). If the last WINDOW_S seconds already hold too many, the call waits
until enough of them have aged out. It changes only WHEN a call is sent, never what is asked or how the answer is checked, and it means fewer rate-limit retries.

Settings (environment): AI_TPM_LIMIT tokens per window (default 6000 when the provider is Groq, the free tier; no pacing for other providers unless set; 0 turns pacing off),
AI_TPM_WINDOW_S window length in seconds (default 60), AI_TPM_MARGIN share of the window to fill (default 0.9), AI_REPLY_TOKENS reserve for the reply (default 600)."""
from __future__ import annotations

import os
import threading
import time
from collections import deque

DEFAULT_LIMIT = 6000
DEFAULT_WINDOW_S = 60.0
DEFAULT_MARGIN = 0.9
DEFAULT_REPLY_TOKENS = 600
_lock = threading.Lock()
_events: deque = deque()          # (time, tokens) of calls already sent in the window
STATS = {"waits": 0, "waited_s": 0.0}


def _now() -> float:           # patched with a fake clock in tests
    return time.monotonic()


def _sleep(s: float) -> None:     # patched with a fake clock in tests
    time.sleep(s)


def _num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _on_groq() -> bool:
    p = os.environ.get("LLM_PROVIDER", "").strip().lower()
    return p == "groq" or (not p and bool(os.environ.get("GROQ_API_KEY")))


def limit() -> int:
    """The per-window token budget. Unset: the free Groq tier's figure when Groq is the provider, otherwise no pacing (a local model has no such limit)."""
    if "AI_TPM_LIMIT" in os.environ:
        return int(_num("AI_TPM_LIMIT", DEFAULT_LIMIT))
    return DEFAULT_LIMIT if _on_groq() else 0


def window_s() -> float:
    return max(1.0, _num("AI_TPM_WINDOW_S", DEFAULT_WINDOW_S))


def estimate(system: str, user: str) -> int:
    """About 4 characters a token (the same estimate the usage record uses when the provider gives no count), plus the reserve for the reply."""
    return (len(system) + len(user)) // 4 + int(_num("AI_REPLY_TOKENS", DEFAULT_REPLY_TOKENS))


def reset() -> None:
    with _lock:
        _events.clear()
    STATS.update(waits=0, waited_s=0.0)


def wait_for_room(tokens: int) -> float:
    """Blocks until `tokens` fit in the window, then records them. Returns the seconds waited. A call bigger than the whole window waits for an empty window and then goes."""
    cap, win = limit(), window_s()
    if cap <= 0:
        return 0.0
    room = max(1.0, cap * max(0.1, min(1.0, _num("AI_TPM_MARGIN", DEFAULT_MARGIN))))
    waited = 0.0
    while True:
        with _lock:
            now = _now()
            while _events and now - _events[0][0] >= win:
                _events.popleft()
            used = sum(t for _, t in _events)
            if not _events or used + tokens <= room:
                _events.append((now, tokens))
                return waited
            wait = _events[0][0] + win - now + 0.01
        STATS["waits"] += 1
        STATS["waited_s"] += wait
        waited += wait
        _sleep(max(0.01, wait))
