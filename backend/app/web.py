"""What sits between the browser and the routes: the allowed cross-origin sites, the security headers, and the forwarded scheme behind a proxy. Each is read from the environment on every request.

CORS_ORIGINS     comma-separated origins allowed to call the API from another site. The site's own origin needs nothing (the browser does not ask). SITE_ORIGIN adds one origin. Nothing is allowed by default in production
                 (APP_ENV=production); in development the Vite dev server's origin is.
TRUST_PROXY_HEADERS  1 = believe X-Forwarded-Proto (and the visitor address in X-Forwarded-For): only behind a proxy you control."""
from __future__ import annotations

import os

from starlette.requests import Request
from starlette.responses import Response

DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]


def allowed_origins() -> list[str]:
    out = [o.strip().rstrip("/") for o in os.environ.get("CORS_ORIGINS", "").split(",") if o.strip()]
    if os.environ.get("SITE_ORIGIN", "").strip():
        out.append(os.environ["SITE_ORIGIN"].strip().rstrip("/"))
    if not out and os.environ.get("APP_ENV", "").strip().lower() != "production":
        out = list(DEV_ORIGINS)
    return list(dict.fromkeys(out))


def trust_proxy() -> bool:
    return os.environ.get("TRUST_PROXY_HEADERS", "0").strip().lower() in ("1", "true", "yes", "on")


def scheme_of(request: Request) -> str:
    if trust_proxy():
        fwd = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
        if fwd in ("http", "https"):
            return fwd
    return request.url.scheme


def install(app) -> None:
    @app.middleware("http")
    async def edge(request: Request, call_next):
        origin = request.headers.get("origin", "").rstrip("/")
        ok = bool(origin) and origin in allowed_origins()
        cors = {"Access-Control-Allow-Origin": origin, "Vary": "Origin"} if ok else {}
        if request.method == "OPTIONS" and request.headers.get("access-control-request-method"):          # a preflight
            if not ok:
                return Response("This site may not call the API from that origin.", status_code=403)
            return Response(status_code=204, headers={**cors, "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS", "Access-Control-Allow-Headers": request.headers.get("access-control-request-headers", "*") or "*",
                                                      "Access-Control-Max-Age": "600"})
        response = await call_next(request)
        for k, v in cors.items():
            response.headers[k] = v
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        if scheme_of(request) == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return response
