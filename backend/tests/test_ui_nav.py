"""The navigation and layout of the analysis view, driven in a real headless browser against the shipped sample run (no AI): five tabs, the Data sub-views, the Export menu, Expand all details, Review answers,
the logo, old links and widths. Skipped when node, the playwright package or a built frontend is not available."""
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

SCRIPT = ROOT / "backend" / "tests" / "ui" / "nav_check.js"


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    npm = node_path()
    if not npm or not (ROOT / "frontend" / "dist" / "index.html").exists():
        pytest.skip("needs node with playwright and a built frontend (npm run build)")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    tmp = tmp_path_factory.mktemp("uinav")
    env = {**os.environ, "RUNS_DIR": str(tmp / "runs"), "GLOSSARY_PATH": str(tmp / "g.json"), "LLM_PROVIDER": "none", "CHATBOT": "auto", "CHART_DPI": "50", "AI_CACHE_DIR": str(tmp / "c"), "NODE_PATH": npm}
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(port)], cwd=ROOT / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(60):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1)
                break
            except Exception:
                time.sleep(0.5)
        out = subprocess.run([shutil.which("node"), str(SCRIPT), f"http://127.0.0.1:{port}"], env=env, capture_output=True, text=True, timeout=420)
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
    "five tabs in one thin bar": ["five_tabs_in_order", "bar_is_thin", "tabs_fit_at_1280_without_scrolling", "old_tabs_are_gone", "no_print_or_power_bi_buttons_on_the_page", "landing_header_is_not_shown_in_the_analysis_view",
                                  "bar_has_export_review_and_a_menu", "menu_has_glossary_and_history"],
    "Data sub-views": ["data_has_four_visible_subviews", "subviews_are_a_segmented_control_not_a_dropdown", "your_data_includes_how_the_data_was_set_up", "subview_How_files_connect_is_a_full_page",
                       "subview_What_we_cleaned_is_a_full_page", "subview_How_we_analysed_is_a_full_page", "chosen_subview_is_remembered"],
    "every item id reachable": ["every_section_id_is_on_its_page", "the_catalogue_still_covers_all_nine_tabs"],
    "Export menu": ["export_menu_has_every_export", "download_pdf_is_the_report_pdf", "power_bi_pack_and_the_other_downloads_are_direct_links", "export_briefs_opens_the_briefs", "export_print_opens_the_print_options",
                    "export_dialog_closes_on_click_outside", "export_menu_closes_on_escape_and_returns_focus"],
    "Expand all details": [f"one_details_control_on_{n}" for n in ("Report", "Insights", "Ideas_to_try", "Dashboard", "What_we_cleaned", "How_we_analysed")] + [
        "report_has_folded_supporting_detail", "the_headline_actions_and_chart_captions_are_open_without_expanding", "expand_all_opens_every_fold_and_the_button_changes", "expanded_state_is_remembered_for_this_report",
        "expanding_applies_to_every_page", "collapse_folds_them_again", "lens_can_show_all_details_by_chat", "lens_can_collapse_details_by_chat"],
    "Review answers": ["review_opens_as_a_dialog", "review_closes_with_the_x_button", "review_returns_focus_to_its_button", "review_closes_with_escape", "review_closes_with_a_click_outside", "review_traps_focus_while_open",
                       "review_with_no_changes_closes_without_asking", "review_asks_before_closing_with_unsaved_changes_escape", "review_keep_editing_stays_open", "review_asks_before_closing_with_unsaved_changes_outside_click",
                       "review_asks_before_closing_with_unsaved_changes_x"],
    "the logo": ["logo_asks_first_when_answers_are_unsaved", "stay_keeps_the_report_open", "discard_and_close_closes_the_review", "logo_returns_to_the_home_page"],
    "old links": [f"old_link_{n}_opens_its_new_place" for n in ("powerbi", "print", "chat", "cleaning", "model", "plan", "data")],
    "widths": [f"no_horizontal_scroll_at_{w}" for w in (1440, 1024, 768, 390)] + ["phone_reaches_the_last_tab"],
    "no errors": ["no_page_errors"],
}


@pytest.mark.parametrize("group", list(GROUPS))
def test_the_navigation(results, group):
    assert "crashed" not in results, results.get("crashed")
    failed = {k: results.get(k) for k in GROUPS[group] if not (results.get(k) or {}).get("ok")}
    assert not failed, failed
