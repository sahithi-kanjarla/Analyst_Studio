"""Free-LLM abstraction.

Provider order when LLM_PROVIDER=auto (default):
    GOOGLE_API_KEY  -> Gemini free tier
    GROQ_API_KEY    -> Groq free tier
    Ollama running  -> local model, fully offline and free
    otherwise       -> no LLM: the agents fall back to deterministic rules (still produces a full report)

Only aggregated metadata (column names, types, summary stats, findings) is ever sent to an LLM -
never raw rows.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
from contextlib import contextmanager

_cache: dict = {}
_tls = threading.local()    # each run is its own worker thread, so concurrent runs never see each other's failures
TIMEOUT_S = 60
MAX_RETRIES = 2

DEFAULTS = {
    "gemini": "gemini-flash-latest",
    "groq": "llama-3.3-70b-versatile",
    "ollama": "qwen2.5:7b",
}


def _ollama_up() -> bool:
    host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    try:
        urllib.request.urlopen(host.rstrip("/") + "/api/tags", timeout=1)
        return True
    except Exception:
        return False


def resolve_provider() -> str:
    p = os.environ.get("LLM_PROVIDER", "auto").lower()
    if p != "auto":
        return p
    if os.environ.get("GOOGLE_API_KEY"):
        return "gemini"
    if os.environ.get("GROQ_API_KEY"):
        return "groq"
    if _ollama_up():
        return "ollama"
    return "none"


def get_llm():
    """Returns (chat_model | None, label)."""
    if "llm" in _cache:
        return _cache["llm"]
    provider = resolve_provider()
    model = os.environ.get("LLM_MODEL") or DEFAULTS.get(provider, "")
    llm, label = None, "rules-only (no LLM)"
    try:
        if provider == "gemini":
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(model=model, temperature=0.2, timeout=TIMEOUT_S, max_retries=MAX_RETRIES)
        elif provider == "groq":
            from langchain_groq import ChatGroq
            llm = ChatGroq(model=model, temperature=0.2, timeout=TIMEOUT_S, max_retries=MAX_RETRIES)
        elif provider == "ollama":
            from langchain_ollama import ChatOllama
            llm = ChatOllama(model=model, temperature=0.2, client_kwargs={"timeout": TIMEOUT_S},
                             base_url=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
        if llm is not None:
            label = f"{provider}:{model}"
    except Exception as e:  # missing package / bad key format
        llm, label = None, f"rules-only ({provider} unavailable: {type(e).__name__})"
    _cache["llm"] = (llm, label)
    return _cache["llm"]


def classify_error(e: BaseException) -> str:
    """The reason a call failed, in words a reader understands: rate limit, auth, timeout, or the provider's own error."""
    name, msg = type(e).__name__, str(e)
    low = f"{name} {msg}".lower()
    if "ratelimit" in low or "rate limit" in low or "rate_limit" in low or "429" in low or "quota" in low or "resource_exhausted" in low or "resourceexhausted" in low:
        kind = "rate limit"
    elif "timeout" in low or "timed out" in low or "deadline" in low:
        kind = "timeout"
    elif "auth" in low or "api key" in low or "api_key" in low or "401" in low or "403" in low or "permission" in low or "unauthorized" in low:
        kind = "auth"
    else:
        kind = "provider error"
    return f"{kind} ({name}: {' '.join(msg.split())[:120]})" if msg else f"{kind} ({name})"


class Capture:
    """The reasons AI calls failed inside a `with capture()` block; `reason` is the last one."""
    def __init__(self):
        self.reasons: list[str] = []
        self.calls = 0

    @property
    def reason(self) -> str:
        return self.reasons[-1] if self.reasons else ""


@contextmanager
def capture():
    stack = getattr(_tls, "stack", None)
    if stack is None:
        stack = _tls.stack = []
    cap = Capture()
    stack.append(cap)
    try:
        yield cap
    finally:
        stack.remove(cap)


def note_failure(reason: str) -> None:
    _tls.last = reason
    for cap in getattr(_tls, "stack", []):
        cap.reasons.append(reason)


def last_error() -> str:
    """The most recent failure on this thread (one run). Prefer the reason that ask_json_ex returns for the call itself."""
    return getattr(_tls, "last", "")


def deterministic(llm):
    """The same model at temperature 0, for decisions that must repeat from run to run. A model that cannot be re-bound is returned unchanged."""
    try:
        return llm.bind(temperature=0) if llm is not None and hasattr(llm, "bind") else llm
    except Exception:
        return llm


def reset_cache():
    _cache.clear()


def _invoke(llm, system: str, user: str, retries: int = 1) -> tuple[str | None, str]:
    """(text, reason). reason is "" on success; otherwise why the last attempt failed."""
    reason = ""
    for cap in getattr(_tls, "stack", []):
        cap.calls += 1
    for attempt in range(retries + 1):
        try:
            out = llm.invoke([("system", system), ("human", user)])
            content = out.content
            if isinstance(content, list):  # some providers return content blocks
                content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
            return str(content), ""
        except Exception as e:
            reason = classify_error(e)
            if attempt < retries:
                time.sleep(2)  # free tiers rate-limit; one gentle retry
    note_failure(reason)
    return None, reason


def ask_text(llm, system: str, user: str) -> str | None:
    if llm is None:
        return None
    return _invoke(llm, system, user)[0]


def ask_json_ex(llm, system: str, user: str) -> tuple[dict | None, str]:
    """Ask for JSON and defensively extract it. (object, reason): the object is None when the call failed or the output is unusable, and reason says which."""
    if llm is None:
        return None, "no LLM configured"
    raw, reason = _invoke(llm, system + "\n\nReturn ONLY a single valid JSON object. No prose, no markdown fences.", user)
    if raw is None:
        return None, reason
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    start, end = raw.find("{"), raw.rfind("}")
    bad = "bad JSON (the reply had no parseable JSON object)"
    if start == -1 or end == -1:
        note_failure(bad)
        return None, bad
    try:
        return json.loads(raw[start:end + 1]), ""
    except json.JSONDecodeError:
        note_failure(bad)
        return None, bad


def ask_json(llm, system: str, user: str) -> dict | None:
    return ask_json_ex(llm, system, user)[0]
