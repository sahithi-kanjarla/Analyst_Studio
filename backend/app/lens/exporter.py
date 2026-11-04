"""Export: a brief, the full report or the short report as a PDF made on the server with a headless browser, a zip of all briefs (one PDF each), or plain text to copy.
The page has a title, the company, the date and the trust label, and no buttons or menus. Raw column names are replaced by their display names. The browser is found like the screenshot tools
find theirs: BROWSER_PATH, then the usual Chrome, Chromium and Edge locations."""
from __future__ import annotations

import base64
import copy
import html
import io
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

from .. import palette
from .. import understanding as und
from . import briefs as bf
from . import records

BROWSER_CANDIDATES = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe", r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable", "/usr/bin/microsoft-edge",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]
PW_GLOBS = ("/opt/pw-browsers/chromium-*/chrome-linux*/chrome", "/opt/pw-browsers/chromium_headless_shell-*/chrome-headless-shell-linux*/chrome-headless-shell")
TIMEOUT_S = 60


class ExportError(Exception):
    pass


def browsers() -> list[str]:
    """Every browser that could make the PDF, most likely first: BROWSER_PATH, a bundled Chromium, then the usual installed ones."""
    import glob
    out: list[str] = []
    env = os.environ.get("BROWSER_PATH", "").strip()
    if env and Path(env).exists():
        out.append(env)
    for g in PW_GLOBS:
        out += sorted(glob.glob(g), reverse=True)
    out += [c for c in BROWSER_CANDIDATES if Path(c).exists()]
    out += [p for p in (shutil.which(n) for n in ("chromium", "chromium-browser", "google-chrome", "chrome", "msedge")) if p]
    return list(dict.fromkeys(out))


def find_browser() -> str | None:
    b = browsers()
    return b[0] if b else None


_WORKING: dict = {}


def _try(exe: str, page_html: str, timeout: float) -> bytes | None:
    with tempfile.TemporaryDirectory(prefix="lens_pdf_", ignore_cleanup_errors=True) as d:
        src, out = Path(d) / "page.html", Path(d) / "out.pdf"
        src.write_text(page_html, encoding="utf-8")
        cmd = [exe, "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage", "--no-pdf-header-footer", f"--user-data-dir={Path(d) / 'profile'}", f"--print-to-pdf={out}", src.as_uri()]
        try:
            subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout, check=False)
        except (subprocess.TimeoutExpired, OSError):
            return None
        data = out.read_bytes() if out.exists() else b""
    return data if data.startswith(b"%PDF") and len(data) > 200 else None


def html_to_pdf(page_html: str) -> bytes:
    cands = browsers()
    if not cands:
        raise ExportError("A PDF can't be made on this server right now. You can copy the text instead.")
    if _WORKING.get("exe") in cands:
        cands = [_WORKING["exe"]] + [c for c in cands if c != _WORKING["exe"]]
    for exe in cands:
        data = _try(exe, page_html, TIMEOUT_S if len(cands) == 1 else 25)
        if data:
            _WORKING["exe"] = exe
            return data
    raise ExportError("The PDF could not be made.")


# The exported page uses the app's own colours, from the one shared palette (app/palette.py).
CSS = f"""body{{font:14px/1.5 'IBM Plex Sans',-apple-system,'Segoe UI',Roboto,Arial,sans-serif;color:{palette.INK};margin:0;padding:0;font-variant-numeric:tabular-nums}}
main{{max-width:760px;margin:0 auto;padding:8px 4px}}
h1{{font-size:24px;margin:0 0 4px;letter-spacing:-.02em}}h2{{font-size:16px;margin:22px 0 6px;border-bottom:1px solid {palette.LINE};padding-bottom:3px}}
.meta{{color:{palette.MUTED};font-size:12px;margin-bottom:14px}}.pill{{display:inline-block;border:1px solid {palette.LINE};border-radius:999px;padding:1px 10px;font-size:12px;margin-left:6px}}
.headline{{font-size:18px;font-weight:600;margin:10px 0}}.fig{{margin:4px 0}}.rec{{margin:6px 0}}.edited{{color:{palette.MUTED};font-size:11px;margin-left:6px}}
.quality{{background:{palette.GRID};border:1px solid {palette.LINE};border-radius:8px;padding:8px 12px;margin-top:18px}}.quality li{{margin:2px 0}}
figure{{margin:12px 0;page-break-inside:avoid}}img{{max-width:100%}}figcaption{{font-size:12px;color:{palette.MUTED}}}
.tiles{{display:flex;gap:10px;flex-wrap:wrap}}.tile{{border:1px solid {palette.LINE};border-radius:8px;padding:6px 12px}}.tile b{{display:block;font-size:18px}}
@page{{margin:16mm}}"""


def _readable(text, result: dict) -> str:
    return html.escape(und.readable(str(text or ""), result))


def _img(wd: Path, path: str) -> str:
    p = (Path(wd) / path)
    try:
        p.resolve().relative_to(Path(wd).resolve())
        data = base64.b64encode(p.read_bytes()).decode()
    except (OSError, ValueError):
        return ""
    return f"data:image/png;base64,{data}"


def _head(title: str, result: dict, trust: str) -> str:
    company = (result.get("context") or {}).get("company") or ""
    return (f"<h1>{_readable(title, result)}</h1><div class='meta'>{html.escape(company + ' · ' if company else '')}{time.strftime('%d %B %Y')}"
            f"<span class='pill'>How sure we are: {html.escape(trust)}</span></div>")


def _page(body: str) -> str:
    return f"<!doctype html><html><head><meta charset='utf-8'><title>Report</title><style>{CSS}</style></head><body><main>{body}</main></body></html>"


def brief_html(b: dict, result: dict, wd: Path) -> str:
    e = lambda x: "<span class='edited'>Edited by you</span>" if x.get("edited") else ""
    body = _head(b["title"], result, b["quality"]["trust"])
    if b.get("no_info"):
        body += f"<p class='headline'>{_readable(b['no_info'], result)}</p>"
    elif b.get("headline"):
        body += f"<p class='headline'>{_readable(b['headline']['text'], result)}{e(b['headline'])}</p>"
    if b.get("key_figures"):
        body += "<h2>Key figures</h2>" + "".join(f"<p class='fig'>{_readable(x['text'], result)}{e(x)}</p>" for x in b["key_figures"])
    if b.get("recommendations"):
        body += "<h2>What to do</h2><ol>" + "".join(f"<li class='rec'>{_readable(x['text'], result)}{e(x)}</li>" for x in b["recommendations"]) + "</ol>"
    for c in b.get("charts") or []:
        src = _img(wd, c["path"])
        if src:
            body += f"<figure><img src='{src}' alt=''><figcaption>{_readable(c.get('title'), result)}</figcaption></figure>"
    body += "<div class='quality'><b>Data quality and how sure we are</b><ul>" + f"<li>How sure we are: {html.escape(b['quality']['trust'])}</li>" + "".join(f"<li>{_readable(n, result)}</li>" for n in b["quality"]["notes"]) + "</ul></div>"
    return _page(body)


def curated_cards(result: dict) -> list[dict] | None:
    """The findings the Report tab shows (the saved selection, merged findings once), or None for a run made before the selection existed.
    Built the same way the page is: the stored view with the selection applied, so the print/PDF report and the screen agree."""
    cur = result.get("curation") or {}
    rv = result.get("report_view") or {}
    if not cur.get("tabs") or not rv:
        return None
    from .. import tiering
    view = copy.deepcopy(rv)
    tiering.apply_curation(view, cur)
    return view.get("report_findings")


def report_html(result: dict, wd: Path, short: bool = False) -> str:
    page = (result.get("report_view") or {}).get("page") or {}
    trust = records.trust_of(result)
    body = _head(page.get("title") or ((result.get("context") or {}).get("company") + " report" if (result.get("context") or {}).get("company") else "Report"), result, trust)
    body += f"<p class='headline'>{_readable(page.get('headline') or result.get('goal'), result)}</p>"
    if page.get("headline_more") and not short:
        body += f"<p>{_readable(page['headline_more'], result)}</p>"
    tiles = [t for t in page.get("tiles") or [] if t.get("label") != "Columns"][: (3 if short else 8)]
    if tiles:
        body += "<div class='tiles'>" + "".join(f"<div class='tile'><b>{_readable(t.get('value'), result)}</b>{_readable(t.get('label'), result)}</div>" for t in tiles) + "</div>"
    acts = (page.get("actions") or [])[: (3 if short else 12)]
    if acts:
        body += "<h2>What to do</h2><ol>" + "".join(f"<li class='rec'>{_readable(a.get('text'), result)}</li>" for a in acts) + "</ol>"
    cards = curated_cards(result)
    if cards is not None:                                  # the same findings the Report tab shows, one card for a merged group
        for c in cards[: (2 if short else None)]:
            lines = "".join(f"<li>{_readable(i.get('text'), result)}</li>" for i in ((c.get("merged") or {}).get("items") or [])) if len(((c.get("merged") or {}).get("items") or [])) > 1 else ""
            src = _img(wd, c.get("chart") or "")
            img = f"<img src='{src}' alt=''>" if src else ""
            cap = f"<b>{_readable(c.get('headline'), result)}</b>. {_readable(c.get('why_it_matters') or c.get('observation'), result)}" + (f"<ul>{lines}</ul>" if lines else "")
            body += f"<figure>{img}<figcaption>{cap}</figcaption></figure>"
    else:
        for c in (page.get("charts") or [])[: (2 if short else 6)]:
            src = _img(wd, c.get("path", ""))
            if src:
                body += f"<figure><img src='{src}' alt=''><figcaption><b>{_readable(c.get('title'), result)}</b>. {_readable(c.get('caption'), result)}</figcaption></figure>"
    notes = bf.quality_note(result, [])["notes"]
    body += "<div class='quality'><b>Data quality and how sure we are</b><ul>" + f"<li>How sure we are: {html.escape(trust)}</li>" + "".join(f"<li>{_readable(n, result)}</li>" for n in notes) + "</ul></div>"
    return _page(body)


def report_text(result: dict, short: bool = False) -> str:
    page = (result.get("report_view") or {}).get("page") or {}
    out = [page.get("title") or "Report", "", und.readable(str(page.get("headline") or ""), result), ""]
    for a in (page.get("actions") or [])[: (3 if short else 12)]:
        out.append("- " + und.readable(str(a.get("text")), result))
    out += ["", f"How sure we are: {records.trust_of(result)}"] + [f"- {und.readable(n, result)}" for n in bf.quality_note(result, [])["notes"]]
    return "\n".join(out)


def brief_text(b: dict, result: dict) -> str:
    return und.readable("\n".join(bf.lines_of(b)), result)


def make(what: str, result: dict, wd: Path, brief: dict | None = None, briefs: list[dict] | None = None) -> tuple[bytes, str, str]:
    """(bytes, content type, file name)."""
    safe = lambda t: re.sub(r"[^A-Za-z0-9._-]+", "-", t).strip("-")[:60] or "file"
    if what == "brief" and brief:
        return html_to_pdf(brief_html(brief, result, wd)), "application/pdf", safe(brief["title"]) + ".pdf"
    if what == "all_briefs":
        if not briefs:
            raise ExportError("There are no saved briefs to download.")
        buf, used = io.BytesIO(), set()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for b in briefs:
                name = safe(b["title"])
                n, k = name, 2
                while n in used:
                    n, k = f"{name}-{k}", k + 1
                used.add(n)
                z.writestr(n + ".pdf", html_to_pdf(brief_html(b, result, wd)))
        return buf.getvalue(), "application/zip", "briefs.zip"
    if what in ("report", "short_report"):
        return html_to_pdf(report_html(result, wd, short=what == "short_report")), "application/pdf", ("short-report.pdf" if what == "short_report" else "report.pdf")
    if what == "text":
        t = brief_text(brief, result) if brief else report_text(result)
        return t.encode("utf-8"), "text/plain; charset=utf-8", "text.txt"
    raise ExportError("I can export a brief, all briefs, the report or the short report.")
