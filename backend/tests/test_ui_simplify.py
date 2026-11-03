"""The calm interface, driven in a real headless browser against the shipped sample run (no AI): no controls on cards at rest, click-to-focus, the default short view, the slim bar, chat edits visible on every tab,
the answer menu, and brief rename and edit. Skipped when node, the playwright package or a built frontend is not available."""
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
SCRIPT = Path(__file__).parent / "ui" / "simplify_check.js"


def node_path() -> str | None:
    if not shutil.which("node"):
        return None
    try:
        g = subprocess.run(["npm", "root", "-g"], capture_output=True, text=True, timeout=20).stdout.strip()
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
        out = subprocess.run(["node", str(SCRIPT), f"http://127.0.0.1:{port}", shots], env=env, capture_output=True, text=True, timeout=300)
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
    "no controls on cards at rest": ["no_per_card_buttons", "no_menu_dots_on_cards", "at_most_two_actions_per_report_card", "no_slim_bar_at_rest"],
    "full view by default with one Show less link": ["default_short_report", "default_short_insights", "default_short_dashboard"],
    "click to focus": ["click_selects_and_opens_lens_with_chip", "selected_card_is_outlined", "click_elsewhere_clears_focus", "enter_selects_a_focused_card", "cards_are_keyboard_focusable", "removing_the_chip_clears_focus"],
    "few suggestions": ["first_open_chips_at_most_four_without_an_ai", "first_open_has_more_ideas_link", "more_ideas_reveals_more", "starters_removed_after_the_first_message", "at_most_three_next_chips"],
    "slim bar": ["undo_removes_the_bar", "bar_text_counts_hidden", "bar_gone_after_undo"],
    "chat edits visible on every tab": [f"{k}_{t}" for t in ("data", "cleaning", "plan", "powerbi", "chat") for k in ("chat_hide_visible_on", "slim_bar_on", "reset_removes_bar", "reset_restores")] + ["model_tab_items_exist_in_the_catalogue", "short_view_on_cleaning", "show_all_brings_the_rest_back"],
    "brief": ["brief_shows_trust_and_quality_note_first", "brief_one_primary_one_secondary_at_rest", "brief_rename", "brief_edit_is_labelled", "brief_quality_note_survives_edit", "brief_saved_with_the_run"],
    "answer menu": ["answer_menu_closed_at_rest", "answer_menu_has_the_actions", "answer_keeps_thumbs_and_next_chips"],
    "no errors": ["no_page_errors"],
}


@pytest.mark.parametrize("group", list(GROUPS))
def test_the_calm_interface(results, group):
    assert "crashed" not in results, results.get("crashed")
    failed = {k: results.get(k) for k in GROUPS[group] if not (results.get(k) or {}).get("ok")}
    assert not failed, failed
