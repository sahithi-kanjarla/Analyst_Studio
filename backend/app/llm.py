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
import time
import urllib.request

_cache: dict = {}

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
            llm = ChatGoogleGenerativeAI(model=model, temperature=0.2)
        elif provider == "groq":
            from langchain_groq import ChatGroq
            llm = ChatGroq(model=model, temperature=0.2)
        elif provider == "ollama":
            from langchain_ollama import ChatOllama
            llm = ChatOllama(model=model, temperature=0.2,
                             base_url=os.environ.get("OLLAMA_HOST", "http://localhost:11434"))
        if llm is not None:
            label = f"{provider}:{model}"
    except Exception as e:  # missing package / bad key format
        llm, label = None, f"rules-only ({provider} unavailable: {type(e).__name__})"
    _cache["llm"] = (llm, label)
    return _cache["llm"]


def reset_cache():
    _cache.clear()


def _invoke(llm, system: str, user: str, retries: int = 1) -> str | None:
    for attempt in range(retries + 1):
        try:
            out = llm.invoke([("system", system), ("human", user)])
            content = out.content
            if isinstance(content, list):  # some providers return content blocks
                content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
            return str(content)
        except Exception:
            if attempt < retries:
                time.sleep(2)  # free tiers rate-limit; one gentle retry
    return None


def ask_text(llm, system: str, user: str) -> str | None:
    if llm is None:
        return None
    return _invoke(llm, system, user)


def ask_json(llm, system: str, user: str) -> dict | None:
    """Ask for JSON and defensively extract it. Returns None if the model output is unusable."""
    if llm is None:
        return None
    raw = _invoke(llm, system + "\n\nReturn ONLY a single valid JSON object. No prose, no markdown fences.", user)
    if not raw:
        return None
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(raw[start:end + 1])
    except json.JSONDecodeError:
        return None
