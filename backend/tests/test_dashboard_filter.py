"""The Dashboard filters, driven in a real headless browser against the shipped sample run (no AI): click a bar, slice or month and the other charts and figures follow, a "Filtered by" chip
with a clear button appears, a second click clears it; each category has its own colour and the unselected marks drop to about half strength. Skipped when node, playwright or a built frontend is missing."""
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).parent / "ui" / "dashboard_filter_check.js"


def node_path() -> str | None:
    npm = shutil.which("npm")
    if not shutil.which("node") or not npm:
        return None
    try:
        g = subprocess.run([npm, "root", "-g"], capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception:
        return None
    return g if g and (Path(g) / "playwright").exists() else None


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    npm = node_path()
    if not npm or not (ROOT / "frontend" / "dist" / "index.html").exists():
        pytest.skip("needs node with playwright and a built frontend (npm run build)")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    tmp = tmp_path_factory.mktemp("ui")
    env = {**os.environ, "RUNS_DIR": str(tmp / "runs"), "GLOSSARY_PATH": str(tmp / "g.json"), "LLM_PROVIDER": "none", "CHATBOT": "on", "CHART_DPI": "50", "AI_CACHE_DIR": str(tmp / "c"), "NODE_PATH": npm}
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)], cwd=ROOT / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        shots = os.environ.get("UI_SHOTS", "")
        out = subprocess.run([shutil.which("node"), str(SCRIPT), f"http://127.0.0.1:{port}", shots], env=env, capture_output=True, text=True, timeout=300)
        line = [x for x in out.stdout.splitlines() if x.startswith("{")]
        assert line, out.stdout + out.stderr
        yield json.loads(line[-1])
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except Exception:
            srv.kill()



def test_every_chart_kind_filters_and_clears(results):
    bad = {k: v for k, v in results.items() if not v["ok"]}
    assert not bad, bad
    assert "at_least_one_chart_kind_found" in results
