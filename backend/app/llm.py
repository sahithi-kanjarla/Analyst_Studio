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
import sys
import threading
import time
import urllib.request
from contextlib import contextmanager

from . import aicache, pacing

_cache: dict = {}
SENT = {"calls": 0}      # how many requests were really sent to the model in this process (a saved result sends none)
_tls = threading.local()    # each run is its own worker thread, so concurrent runs never see each other's failures
TIMEOUT_S = 60
MAX_RETRIES = 0             # the provider SDKs must not retry on their own: _invoke is the only retry layer, so one failure is never multiplied
RATE_LIMIT_RETRIES = 2      # retries after a 429 (per-minute limits clear on their own); after these the stage falls back and says why
RATE_LIMIT_WAIT_CAP_S = 60  # the longest we wait for one retry, whatever retry-after asks for
RATE_LIMIT_BACKOFF_S = 5    # the wait when the 429 carries no retry-after
OTHER_ERROR_RETRIES = 1     # timeouts and provider errors: one gentle retry
RATE_LIMIT = "rate limit"
DAILY_LIMIT = "rate limit (daily limit reached)"

DEFAULTS = {
    "gemini": "gemini-flash-latest",
    "groq": "openai/gpt-oss-120b",
    "ollama": "qwen2.5:7b",
}


PACKAGES = {"gemini": "langchain-google-genai", "groq": "langchain-groq", "ollama": "langchain-ollama"}


def package_notice(label: str) -> str:
    """The header / run-log line when the AI package is missing, else ''."""
    return f"AI unavailable: {label.split('(', 1)[1].rstrip(')')}" if "AI package missing" in (label or "") else ""


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
    except ImportError as e:  # the provider's package is not installed: say so, never a silent rules-only run
        pkg = PACKAGES.get(provider, provider)
        llm, label = None, f"rules-only (AI package missing: {pkg} is not installed, run: pip install {pkg})"
    except Exception as e:  # bad key format
        llm, label = None, f"rules-only ({provider} unavailable: {type(e).__name__})"
    _cache["llm"] = (llm, label)
    return _cache["llm"]


def _text(e: BaseException) -> str:
    return f"{type(e).__name__} {e}".lower()


def is_rate_limit(e: BaseException) -> bool:
    low = _text(e)
    return any(w in low for w in ("ratelimit", "rate limit", "rate_limit", "429", "quota", "resource_exhausted", "resourceexhausted"))


def is_daily_limit(e: BaseException) -> bool:
    """A limit that resets per day (Groq 'tokens per day (TPD)', Gemini '...PerDay...'): waiting a minute will not help."""
    low = _text(e)
    return is_rate_limit(e) and any(w in low for w in ("per day", "perday", "(tpd)", "(rpd)", "daily"))


def retry_after(e: BaseException) -> float | None:
    """Seconds the provider asks us to wait (retry-after-ms or retry-after header on the error's response), or None."""
    headers = getattr(getattr(e, "response", None), "headers", None) or getattr(e, "headers", None)
    if not headers:
        return None
    try:
        ms = headers.get("retry-after-ms")
        if ms is not None:
            return max(0.0, float(ms) / 1000)
        v = headers.get("retry-after")
        return max(0.0, float(v)) if v is not None else None
    except (TypeError, ValueError, AttributeError):
        return None


def classify_error(e: BaseException) -> str:
    """The reason a call failed as a short phrase for a report header: rate limit, timeout, auth or provider error. Never the raw error text (it can name organisations or keys)."""
    low = _text(e)
    if is_daily_limit(e):
        return DAILY_LIMIT
    if is_rate_limit(e):
        return RATE_LIMIT
    if "timeout" in low or "timed out" in low or "deadline" in low:
        return "timeout"
    if "auth" in low or "api key" in low or "api_key" in low or "401" in low or "403" in low or "permission" in low or "unauthorized" in low:
        return "auth"
    return "provider error"


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


def planner_model(llm):
    """The model that plans a Lens request. LENS_PLANNER_MODEL (a model name for the same provider, e.g. a smaller one) when set and the model can be re-bound to it; otherwise the model given, unchanged.
    Only the planning call uses it: figures and wording still come from code and from the model the run was given."""
    name = (os.environ.get("LENS_PLANNER_MODEL") or "").strip()
    if not name or llm is None:
        return llm
    for field in ("model_name", "model"):
        try:
            if hasattr(llm, field) and hasattr(llm, "model_copy"):
                return llm.model_copy(update={field: name})
        except Exception:      # noqa: BLE001  a model that cannot be re-bound keeps its own
            return llm
    return llm


def deterministic(llm):
    """The same model at temperature 0, for decisions that must repeat from run to run. A model that cannot be re-bound is returned unchanged."""
    try:
        return llm.bind(temperature=0) if llm is not None and hasattr(llm, "bind") else llm
    except Exception:
        return llm


def begin_run(cache: bool = False) -> None:
    """Start of a run on this thread: forget the daily-limit stop, the cached answers and the usage record of the previous run. cache=True (the runner) answers an identical call
    again from memory for the rest of this run; direct callers get no cache."""
    _tls.daily = False
    _tls.answers = {} if cache else None
    _tls.usage = []
    aicache.reset_reused()


def note_parts(parts: dict | None) -> None:
    """Name the parts of the NEXT call's text (system text, data card, fact sheet, item list, history, other) with their sizes in characters; _record turns them into tokens."""
    _tls.parts = dict(parts) if parts else None


def _record(stage: str, *, cache_hit: bool, ok: bool = True, reason: str = "", system: str = "", user: str = "", out=None, seconds: float = 0.0,
            reply: str = "") -> None:
    """One line of the run's AI usage: which stage asked, the tokens (the provider's own count when it gives one, otherwise an estimate of about 4 characters a token),
    whether a saved or repeated answer was used, and how long the call took. Failed calls count too, with their reason."""
    usage = getattr(out, "usage_metadata", None) or {}
    given_in, given_out = usage.get("input_tokens"), usage.get("output_tokens")
    estimated = not (isinstance(given_in, int) and isinstance(given_out, int))
    if cache_hit:
        tokens_in, tokens_out = 0, 0
    elif estimated:
        tokens_in, tokens_out = (len(system) + len(user)) // 4, len(reply) // 4
    else:
        tokens_in, tokens_out = int(given_in), int(given_out)
    log = getattr(_tls, "usage", None)
    named, _tls.parts = getattr(_tls, "parts", None), None
    if log is not None:
        row = {"stage": stage, "ok": ok, "reason": reason, "cache_hit": cache_hit, "input_tokens": tokens_in, "output_tokens": tokens_out,
               "tokens_estimated": estimated and not cache_hit, "seconds": round(seconds, 3)}
        if named and not cache_hit:           # where the input tokens went, by part (estimated at about 4 characters a token, scaled to the call's own count)
            total = sum(named.values()) or 1
            row["input_by_part"] = {k: round(tokens_in * v / total) for k, v in named.items()}
        log.append(row)


def usage_calls() -> list[dict]:
    """The AI calls of this run so far, in order (empty outside a run)."""
    return list(getattr(_tls, "usage", None) or [])


def totals(calls: list[dict]) -> dict:
    """Calls, calls sent, calls answered from saved or repeated results, tokens (sent calls only, estimated when any of them is) and seconds."""
    sent = [c for c in calls if not c["cache_hit"]]
    return {"calls": len(calls), "sent": len(sent), "cached": len(calls) - len(sent), "input_tokens": sum(c["input_tokens"] for c in sent),
            "output_tokens": sum(c["output_tokens"] for c in sent), "estimated": any(c["tokens_estimated"] for c in sent),
            "seconds": round(sum(c["seconds"] for c in calls), 1)}


def usage_summary() -> dict:
    """Totals of this run so far."""
    return totals(usage_calls())


def by_stage(calls: list[dict]) -> list[dict]:
    """One row per stage (the module and function that asked), in the order the stages first asked."""
    rows: dict[str, dict] = {}
    for c in calls:
        r = rows.setdefault(c["stage"], {"stage": c["stage"], "calls": 0, "cached": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0,
                                         "estimated": False, "seconds": 0.0})
        r["calls"] += 1
        r["cached"] += c["cache_hit"]
        r["failed"] += not c["ok"]
        r["input_tokens"] += c["input_tokens"]
        r["output_tokens"] += c["output_tokens"]
        r["estimated"] |= c["tokens_estimated"]
        r["seconds"] = round(r["seconds"] + c["seconds"], 3)
    return list(rows.values())


def save_usage(workdir) -> None:
    """Append this run's AI calls to ai_usage.json in the run folder (a resumed run adds its calls after the first part's)."""
    import json as _json
    from pathlib import Path as _Path
    p = _Path(workdir) / "ai_usage.json"
    prior = _json.loads(p.read_text(encoding="utf-8")).get("calls", []) if p.exists() else []
    calls = prior + usage_calls()
    p.write_text(_json.dumps({"calls": calls, "totals": totals(calls), "by_stage": by_stage(calls)}, indent=1), encoding="utf-8")


def reset_cache():
    _cache.clear()
    begin_run()


def daily_limit_hit() -> bool:
    return bool(getattr(_tls, "daily", False))


def _sleep(seconds: float) -> None:     # one place to patch in tests
    time.sleep(seconds)


def _invoke(llm, system: str, user: str, json_mode: bool = False) -> tuple[str | None, str]:
    """(text, reason). reason is "" on success; otherwise a short phrase for why the call failed. This is the only retry layer.
    A 429 waits for retry-after (at most RATE_LIMIT_WAIT_CAP_S) and retries up to RATE_LIMIT_RETRIES times; a per-day limit is not retried and stops every
    later call in the run; an identical call already answered in this run is not sent again."""
    for cap in getattr(_tls, "stack", []):
        cap.calls += 1
    if daily_limit_hit():
        note_failure(DAILY_LIMIT)
        return None, DAILY_LIMIT
    answers = getattr(_tls, "answers", None)
    key = (system, user, str(getattr(llm, "kwargs", "")))
    if answers is not None and key in answers:
        _record(_stage(), cache_hit=True, system=system, user=user)
        return answers[key], ""
    reason, rate_tries, other_tries = "", 0, 0
    use = json_bound(llm) if json_mode else llm
    while True:
        pacing.wait_for_room(pacing.estimate(system, user))        # spaces the call to fit the per-minute token window; what is asked is unchanged
        started = time.monotonic()
        try:
            SENT["calls"] += 1
            out = use.invoke([("system", system), ("human", user)])
            content = out.content
            if isinstance(content, list):  # some providers return content blocks
                content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
            if answers is not None:
                answers[key] = str(content)
            _record(_stage(), cache_hit=False, system=system, user=user, out=out, seconds=time.monotonic() - started, reply=str(content))
            return str(content), ""
        except Exception as e:
            reason = classify_error(e)
            _record(_stage(), cache_hit=False, ok=False, reason=reason, system=system, user=user, seconds=time.monotonic() - started)
            if use is not llm and _json_mode_refused(e):          # the provider does not do JSON mode for this model: ask without it, once, and remember
                JSON_MODE_OFF.add(_provider_class(llm))
                use = llm
                continue
            if is_daily_limit(e):
                _tls.daily = True
                break
            if is_rate_limit(e):
                if rate_tries >= RATE_LIMIT_RETRIES:
                    break
                wait = retry_after(e)
                _sleep(min(RATE_LIMIT_WAIT_CAP_S, wait if wait is not None else RATE_LIMIT_BACKOFF_S))
                rate_tries += 1
            else:
                if other_tries >= OTHER_ERROR_RETRIES:
                    break
                other_tries += 1
                _sleep(2)
    note_failure(reason)
    return None, reason


def _stage() -> str:
    """The module and function that asked, for the report's 'Reused saved AI result' line."""
    f = sys._getframe(1)
    while f is not None and f.f_globals.get("__name__") == __name__:
        f = f.f_back
    return f"{f.f_globals.get('__name__', '?').rsplit('.', 1)[-1]}.{f.f_code.co_name}" if f is not None else "?"


def _disk_key(llm, system: str, user: str) -> str:
    return aicache.call_key(system, user, aicache.model_name(llm), aicache.temperature_of(llm))


def ask_text(llm, system: str, user: str) -> str | None:
    if llm is None:
        return None
    k = _disk_key(llm, system, user)
    if (hit := aicache.get(k)) is not None:
        aicache.note_reused(_stage())
        _record(_stage(), cache_hit=True, system=system, user=user)
        return hit
    out = _invoke(llm, system, user)[0]
    if out is not None:
        aicache.put(k, out)
    return out


_THINK = re.compile(r"<(think|thinking|reasoning|reflection)>.*?</\1>", re.S | re.I)
_FENCE = re.compile(r"```[a-zA-Z0-9_-]*\s*\n?(.*?)```", re.S)


def extract_json(raw: str) -> tuple[dict | None, str]:
    """(the first valid JSON object in a reply, "") or (None, why). Reasoning text and code fences around it are ignored, prose before or after it is ignored, and a reply that was cut off says so.
    Only an object counts: a list or a bare number is not the shape any caller expects."""
    text = _THINK.sub(" ", raw or "")
    text = re.sub(r"<(?:think|thinking|reasoning)>.*\Z", " ", text, flags=re.S | re.I)           # reasoning that never closed: nothing after it is the answer
    chunks = [m.group(1) for m in _FENCE.finditer(text)] + [text]
    dec, last_err = json.JSONDecoder(), ""
    for chunk in chunks:
        i, tries = chunk.find("{"), 0
        while i != -1 and tries < 40:
            try:
                obj, _ = dec.raw_decode(chunk[i:])
                if isinstance(obj, dict):
                    return obj, ""
            except json.JSONDecodeError as e:
                last_err = f"{e.msg} at line {e.lineno} column {e.colno}"
            i, tries = chunk.find("{", i + 1), tries + 1
    if "{" not in text:
        return None, "no JSON object in the reply"
    if text.count("{") > text.count("}") or (last_err and re.search(r"Unterminated|Expecting.*delimiter|Expecting value", last_err) and text.rstrip()[-1:] not in "}]"):
        return None, "the reply was cut off before the JSON object ended"
    return None, f"invalid JSON ({last_err or 'could not be read'})"


def _extract(raw: str) -> dict | None:
    return extract_json(raw)[0]


JSON_MODE_OFF: set[str] = set()          # provider classes whose JSON mode the provider refused: asked without it from then on


def _provider_class(llm) -> str:
    return type(getattr(llm, "bound", llm)).__name__.lower()


def json_bound(llm):
    """The model asked to answer in JSON, where its provider has such a mode (Groq and OpenAI-style: response_format; Gemini: a JSON mime type; Ollama: format json); the model itself otherwise."""
    name = _provider_class(llm)
    if llm is None or not hasattr(llm, "bind") or name in JSON_MODE_OFF:
        return llm
    try:
        if "groq" in name or "openai" in name:
            return llm.bind(response_format={"type": "json_object"})
        if "google" in name or "gemini" in name:
            return llm.bind(response_mime_type="application/json")
        if "ollama" in name:
            return llm.bind(format="json")
    except Exception:           # noqa: BLE001  a model that cannot be bound is asked without the mode
        pass
    return llm


def _json_mode_refused(e: BaseException) -> bool:
    low = _text(e)
    return any(w in low for w in ("response_format", "json_object", "json mode", "mime type", "response_mime_type", "json_validate_failed", "failed_generation", "unsupported parameter", "format"))\
        and not is_rate_limit(e)


REPAIR_ASK = "\n\nYour previous reply could not be used: {problem}.\nIt was: {seen}\nAnswer again with only the corrected JSON object, nothing else."


def ask_json_ex(llm, system: str, user: str, repair: bool = False, validate=None, cache_ok=None, cache_tag: str = "") -> tuple[dict | None, str]:
    """Ask for JSON and defensively extract it. (object, reason): the object is None when the call failed or the output is unusable, and reason says which.
    The model is asked in its provider's JSON mode where there is one. The first valid JSON object in the reply is taken (fences, reasoning text and prose around it are ignored).
    `validate(obj) -> list of problems` checks the shape the caller expects; with problems, or a reply that is not JSON, repair=True asks exactly once more and shows the model what was wrong.
    A reply that parsed (and passed `validate`) is saved on disk (aicache) by the full prompt, model and temperature and answers the same call later; failed calls are never saved.
    `cache_ok(obj) -> bool` is the caller's own acceptance of the reply (checked after parsing): a reply it refuses is returned but never saved, and a saved reply it now refuses is ignored, so a rerun asks again.
    `cache_tag` is mixed into the saved-reply key: change it when the caller's checks change, and older saved replies are no longer used.
    The callers that already run their own repair loop (propose, understanding, the Lens planner) leave repair off."""
    if llm is None:
        return None, "no LLM configured"
    system = system + "\n\nReturn ONLY a single valid JSON object. No prose, no markdown fences."
    k = _disk_key(llm, system + (f"\n[cache:{cache_tag}]" if cache_tag else ""), user)
    if (hit := aicache.get(k)) is not None and (obj := _extract(hit)) is not None and not (validate and validate(obj)) and not (cache_ok and not cache_ok(obj)):
        aicache.note_reused(_stage())
        _record(_stage(), cache_hit=True, system=system, user=user)
        return obj, ""
    raw, reason = _invoke(llm, system, user, json_mode=True)
    if raw is None:
        return None, reason
    obj, why = extract_json(raw)
    problems = [] if obj is None else (list(validate(obj)) if validate else [])
    if (obj is None or problems) and repair:          # the call worked but the reply was not usable: ask once more, quoting what came back and what was wrong. Never for a failed call (rate limit, timeout): _invoke already handled those.
        seen = " ".join(raw.split())[:300] or "(empty)"
        problem = f"it was not valid JSON ({why})" if obj is None else "it did not have the expected shape: " + "; ".join(problems)[:300]
        raw2, reason2 = _invoke(llm, system, user + REPAIR_ASK.format(seen=seen, problem=problem), json_mode=True)
        obj2, why2 = extract_json(raw2) if raw2 is not None else (None, "")
        problems2 = [] if obj2 is None else (list(validate(obj2)) if validate else [])
        if obj2 is None or problems2:
            bad = reason2 or (f"bad JSON ({why2})" if obj2 is None else "unexpected shape: " + "; ".join(problems2)[:200])
            if not reason2:
                note_failure(bad)
            return None, bad
        obj, raw = obj2, raw2
    elif obj is None or problems:
        bad = f"bad JSON ({why})" if obj is None else "unexpected shape: " + "; ".join(problems)[:200]
        note_failure(bad)
        return None, bad
    if cache_ok is None or cache_ok(obj):
        aicache.put(k, raw)
    return obj, ""


def ask_json(llm, system: str, user: str, validate=None, cache_ok=None, cache_tag: str = "") -> dict | None:
    return ask_json_ex(llm, system, user, repair=True, validate=validate, cache_ok=cache_ok, cache_tag=cache_tag)[0]
