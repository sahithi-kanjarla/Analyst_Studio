"""Who a run belongs to: one browser, not an account.

`current_owner(request)` is the ONE place that says who the caller is. Today it is the browser: on the first request that needs one, a random 32-byte token is set in a cookie, and only a hash of it
(HMAC-SHA256 with a server secret) is kept, in the run's status.json, as the run's owner. The raw token is never stored and never logged. A later sign-in replaces `current_owner` (and `cookie_for`)
and nothing else: the endpoints and the check below only ever compare the owner string it returns.

The check is applied by `guard_run_routes` to EVERY path of the form /api/runs/<run id>/..., so a route added later is protected without anyone remembering to. A caller that does not own the run
gets 404, the same as for a run that does not exist. The sample run is public: readable by anyone (what it may change is decided by the routes, as before).
Runs made before owners existed have no owner: they are open to anyone while PUBLIC_DEMO is off (local single-user use) and to nobody while it is on."""
from __future__ import annotations

import hmac
import hashlib
import os
import re
import secrets
from contextvars import ContextVar

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .config import DATA_DIR, public_demo

COOKIE = "analyst_owner"
COOKIE_MAX_AGE = 400 * 24 * 3600            # about the longest a browser keeps a cookie
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-]{43}$")          # secrets.token_urlsafe(32)
_RUN_PATH = re.compile(r"^/api/runs/([a-f0-9]{12}|[a-f0-9]{32})(?:/|$)")
_secret_cache: dict = {}


def server_secret() -> bytes:
    """OWNER_SECRET from the environment; if it is not set, a random one generated once and kept in the data directory (owner_secret, readable by the server user only)."""
    env = os.environ.get("OWNER_SECRET", "").strip()
    if env:
        return env.encode()
    path = DATA_DIR / "owner_secret"
    if path in _secret_cache:
        return _secret_cache[path]
    try:
        val = path.read_text().strip()
    except OSError:
        val = ""
    if len(val) < 32:
        val = secrets.token_hex(32)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600) if not path.exists() else None
        if fd is not None:
            with os.fdopen(fd, "w") as f:
                f.write(val)
        else:                              # lost a race with another process, or an unusable file: use what is there, else keep ours for this process
            try:
                other = path.read_text().strip()
                val = other if len(other) >= 32 else val
            except OSError:
                pass
    _secret_cache[path] = val.encode()
    return val.encode()


def hash_token(token: str) -> str:
    return hmac.new(server_secret(), token.encode(), hashlib.sha256).hexdigest()


def _scheme_is_https(request: Request) -> bool:
    from .web import scheme_of
    return scheme_of(request) == "https"


def cookie_for(response: Response, request: Request, token: str) -> None:
    response.set_cookie(COOKIE, token, max_age=COOKIE_MAX_AGE, path="/", httponly=True, samesite="lax", secure=_scheme_is_https(request))


def current_owner(request: Request, response: Response | None = None) -> str | None:
    """The caller's owner id (the hash), or None for a caller with no cookie yet. Pass `response` where the request needs an owner (starting a run, saving to the glossary):
    a caller with none then gets a new cookie set on that response and the new owner id."""
    token = request.cookies.get(COOKIE, "")
    if _TOKEN_RE.match(token):
        return hash_token(token)
    if response is None:
        return None
    token = secrets.token_urlsafe(32)
    cookie_for(response, request, token)
    return hash_token(token)


def owner_of(run_id: str) -> str | None:
    from .runner import read_status
    return (read_status(run_id) or {}).get("owner") or None


def can_access(run_id: str, caller: str | None) -> bool:
    """May this caller see and manage this run? Never reveals why not."""
    from .runner import is_demo, run_dir
    if is_demo(run_id):
        return True
    if not run_dir(run_id).is_dir():
        return False
    stored = owner_of(run_id)
    if stored:
        return caller is not None and hmac.compare_digest(stored, caller)
    return not public_demo()                 # a run from before owners existed


def scope_of(run_id: str) -> str:
    """The glossary scope a run's own work uses: its owner's, 'demo' for the sample, '' for a run from before owners existed (the old shared glossary)."""
    from .runner import is_demo
    if is_demo(run_id):
        return "demo"
    return (owner_of(run_id) or "")[:32]


def guard_run_routes(request: Request) -> Response | None:
    """For a request to /api/runs/<id>/...: None when the caller may proceed, else the same 404 a missing run gets."""
    m = _RUN_PATH.match(request.url.path)
    if not m:
        return None
    if not can_access(m.group(1), current_owner(request)):
        return JSONResponse({"detail": "Run not found"}, status_code=404)
    return None
