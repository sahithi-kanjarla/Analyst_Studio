"""Markdown in the report may carry raw HTML from the uploaded file (a product name like <img src=x onerror=...>).
frontend/src/components/Md.tsx must strip anything that can run code and keep the report's own <details>/<summary>.
Renders the real component through node (esbuild + react-dom/server); skipped when node or the frontend packages are not there."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

FRONT = Path(__file__).resolve().parents[2] / "frontend"
pytestmark = pytest.mark.skipif(not shutil.which("node") or not (FRONT / "node_modules" / "rehype-sanitize").exists(), reason="needs node and npm install in frontend/")


def render(cases):
    r = subprocess.run(["node", "scripts/md_check.mjs"], cwd=FRONT, input=json.dumps(cases), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_scripts_event_handlers_and_javascript_links_are_removed_but_details_stay():
    out = render({
        "attack": 'Top seller: <img src=x onerror="alert(1)"> and <script>alert(2)</script> and [x](javascript:alert(3)) and <iframe src="https://e.x"></iframe> <a href="https://ok.example" onclick="bad()">fine</a>',
        "details": '<details class="card-details" open><summary>Details</summary>\n\nHidden **body** here\n\n</details>',
        "table": "| a | b |\n|---|---|\n| 1 | 2 |",
    })
    a = out["attack"].lower()
    for bad in ("onerror", "<script", "alert(2)", "javascript:", "<iframe", "onclick"):
        assert bad not in a, (bad, out["attack"])
    assert "https://ok.example" in a and "fine" in a            # a normal link survives
    d = out["details"]
    assert "<details" in d and "<summary>Details</summary>" in d and 'class="card-details"' in d and "<strong>body</strong>" in d
    assert '<td class="num">1</td>' in out["table"]               # the table styling components still apply
