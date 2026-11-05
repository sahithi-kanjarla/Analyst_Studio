"""The widgets of each tab, driven in a real headless browser against the shipped sample run (no AI): the main chart, its explanation and its action are on screen without a click, "Show all (n)" reveals the rest,
"Expand all details" opens the folds, no chart title shows an underscore, a raw column id, "Total" or "(sum)", and print shows what the screen shows. Skipped when node, playwright or a built frontend is missing."""
import json
import os
import socket
import shutil
import subprocess
import sys
import time
import urllib.request

import pytest

from tests.test_ui_simplify import ROOT, node_path

SCRIPT = ROOT / "backend" / "tests" / "ui" / "widgets_check.js"


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    npm = node_path()
    if not npm or not (ROOT / "frontend" / "dist" / "index.html").exists():
        pytest.skip("needs node with playwright and a built frontend (npm run build)")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    tmp = tmp_path_factory.mktemp("uiw")
    env = {**os.environ, "RUNS_DIR": str(tmp / "runs"), "GLOSSARY_PATH": str(tmp / "g.json"), "LLM_PROVIDER": "none", "CHATBOT": "auto", "CHART_DPI": "50", "AI_CACHE_DIR": str(tmp / "c"), "NODE_PATH": npm}
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)], cwd=ROOT / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        out = subprocess.run([shutil.which("node"), str(SCRIPT), f"http://127.0.0.1:{port}"], env=env, capture_output=True, text=True, timeout=300)
        line = [x for x in out.stdout.splitlines() if x.startswith("{")]
        assert line, out.stdout + out.stderr
        yield json.loads(line[-1])
    finally:
        srv.terminate()
        try:
            srv.wait(10)
        except Exception:
            srv.kill()


GROUPS = {
    "report": ["report_main_chart_explanation_and_action_visible", "report_titles_are_plain"],
    "insights": ["insights_widget_chart_explanation_and_action_visible", "insights_have_a_headline_stat_and_badges", "insights_are_grouped_under_theme_headings", "insights_titles_are_plain",
                 "insights_show_all_reveals_the_rest", "insights_expand_all_details_opens_the_folds"],
    "ideas": ["ideas_title_why_steps_and_kpis_visible", "ideas_expand_all_details", "ideas_titles_are_plain"],
    "dashboard": ["dashboard_stat_tiles_and_charts_visible", "dashboard_no_collapsed_chart", "dashboard_show_all_reveals_the_rest", "dashboard_titles_are_plain", "dashboard_card_opens_a_dialog_and_esc_closes_it"],
    "data": ["data_first_rows_visible_with_show_all", "data_show_all_reveals_every_column"],
    "print and errors": ["print_shows_the_same_insights_as_the_screen", "no_page_errors"],
}


@pytest.mark.parametrize("group", list(GROUPS))
def test_the_tab_widgets(results, group):
    assert "crashed" not in results, results.get("crashed")
    failed = {k: results.get(k) for k in GROUPS[group] if not (results.get(k) or {}).get("ok")}
    assert not failed, failed
