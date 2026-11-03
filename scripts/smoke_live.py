"""Smoke test of a running site.

    python scripts/smoke_live.py BASE_URL [ACCESS_CODE]

Checks, in order, and prints PASS or FAIL for each: /api/health answers; the sample report loads (no code, no AI); a wrong access code is refused (when the site asks for a code); an upload of a tiny CSV with the right
code is accepted and finishes; a PDF export returns bytes that start with %PDF. Exit code 0 only when every check passed. Uses nothing but the standard library."""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
import uuid

TINY = "order_id,region,amount\n" + "\n".join(f"{1000 + i},{'NSEW'[i % 4]},{100 + i * 7}" for i in range(40)) + "\n"


def call(method: str, url: str, data: bytes | None = None, headers: dict | None = None, timeout: float = 60) -> tuple[int, bytes, dict]:
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read(), dict(e.headers)


def multipart(fields: dict[str, str], filename: str, content: str) -> tuple[bytes, str]:
    b = uuid.uuid4().hex
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n' for k, v in fields.items()]
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\nContent-Type: text/csv\r\n\r\n{content}\r\n--{b}--\r\n')
    return "".join(parts).encode(), f"multipart/form-data; boundary={b}"


def run(base: str, code: str = "") -> list[tuple[str, bool, str]]:
    base = base.rstrip("/")
    out: list[tuple[str, bool, str]] = []

    def check(name: str, fn) -> object:
        try:
            ok, detail, val = fn()
        except Exception as e:           # noqa: BLE001  a check that cannot run is a failed check
            ok, detail, val = False, f"{type(e).__name__}: {str(e)[:150]}", None
        out.append((name, bool(ok), detail))
        return val

    def health():
        s, b, _ = call("GET", base + "/api/health")
        j = json.loads(b or b"{}")
        return s == 200 and j.get("status") == "ok" and set(j) <= {"status", "version"}, f"HTTP {s}, {j}", None
    check("health answers with status and version only", health)

    def sample():
        s, b, _ = call("POST", base + "/api/demo", b"")
        rid = json.loads(b).get("run_id") if s == 200 else None
        if not rid:
            return False, f"HTTP {s}", None
        s2, b2, _ = call("GET", f"{base}/api/runs/{rid}")
        j = json.loads(b2 or b"{}")
        return s2 == 200 and j.get("status") == "done" and bool(j.get("report_md")), f"run {rid}: HTTP {s2}, status {j.get('status')}", rid
    demo = check("the sample report loads", sample)

    try:
        cfg = json.loads(call("GET", base + "/api/config")[1] or b"{}")
    except Exception:           # noqa: BLE001  an unreachable site fails the checks below; it must not stop the report
        cfg = {}

    def wrong_code():
        if not cfg.get("requires_code"):
            return True, "this site does not ask for a code (nothing to refuse)", None
        body, ctype = multipart({"goal": "", "access_code": "definitely-not-the-code"}, "tiny.csv", TINY)
        s, b, _ = call("POST", base + "/api/runs", body, {"Content-Type": ctype})
        return s == 403, f"HTTP {s}", None
    check("a wrong access code is refused", wrong_code)

    def upload():
        body, ctype = multipart({"goal": "", "access_code": code}, "tiny.csv", TINY)
        s, b, _ = call("POST", base + "/api/runs", body, {"Content-Type": ctype})
        if s != 200:
            return False, f"HTTP {s}: {b[:120].decode('utf-8', 'replace')}", None
        rid = json.loads(b)["run_id"]
        for _ in range(120):
            st = json.loads(call("GET", f"{base}/api/runs/{rid}")[1] or b"{}").get("status")
            if st in ("done", "error", "needs_input"):
                return st in ("done", "needs_input"), f"run {rid} finished: {st}", rid
            time.sleep(1)
        return False, f"run {rid} did not finish in 120 s", rid
    mine = check("an upload of a tiny CSV with the right code works", upload)

    def pdf():
        rid = demo or mine
        if not rid:
            return False, "no run to export", None
        s, b, h = call("GET", f"{base}/api/runs/{rid}/export/report", timeout=120)
        return s == 200 and b[:4] == b"%PDF", f"HTTP {s}, first bytes {b[:8]!r}", None
    check("a PDF export returns bytes starting with %PDF", pdf)

    if isinstance(mine, str):          # leave nothing behind on the site that was tested
        call("DELETE", f"{base}/api/runs/{mine}")
    return out


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    results = run(argv[1], argv[2] if len(argv) > 2 else "")
    for name, ok, detail in results:
        print(f"{'PASS' if ok else 'FAIL'}  {name}  ({detail})")
    bad = [n for n, ok, _ in results if not ok]
    print(f"{len(results) - len(bad)} of {len(results)} checks passed" + (f"; failed: {', '.join(bad)}" if bad else ""))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
