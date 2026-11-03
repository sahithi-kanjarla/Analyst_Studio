"""No user-visible text names a tab the interface no longer has: the Power BI export tab, the Chat tab, a Print tab, or a Data view called a tab. The five tabs are Report, Insights, Ideas to try, Dashboard and Data;
Power BI files and Print are in the Export menu; the full-width conversation is the expanded Lens."""
import os
import re
from pathlib import Path

os.environ["LLM_PROVIDER"] = "none"

from app import viewedit as ve
from app.lens import chat, records, starters, viewchat
from tests.test_lens import FakeAI
from tests.test_partb_view import run  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
OLD = re.compile(r"Power BI export|\bChat tab\b|\bthe Chat\b|\bPower BI tab\b|\bPrint tab\b|\b(?:Your data|What we cleaned|How we analysed|How files connect) tab\b|full-width tab", re.I)


def clean(text: str) -> str:
    return OLD.sub(lambda m: "<<" + m.group(0) + ">>", text)


def test_the_text_the_app_declares_has_no_old_tab_name():
    texts = list(records.TAB_HELP.values()) + list(ve.TAB_NAMES.values()) + [ve.place(t) for t in ve.TABS] + [viewchat.COMMAND_HELP]
    for t in texts:
        assert not OLD.search(t), clean(t)
    assert "menu" in records.TAB_HELP["export"] and "Power BI files" in records.TAB_HELP["export"]


def test_lens_answers_about_tabs_use_the_new_names(run):
    rid, wd, res = run
    for q, tab in (("what does each tab show", "Report"), ("what is the Dashboard tab", "Report"), ("what does What we cleaned show", "Report"), ("where is the Power BI export", "Report"), ("what does this tab show", "What we cleaned"),
                   ("what does this tab show", "Power BI files"), ("what does this tab show", "Lens"), ("what does the data tab show", "Report")):
        a = chat.ask(wd, res, question=q, llm=FakeAI({"action": "app", "topic": "tab"}), allow_ai=True, tab=tab)["answer"]          # the app-explanation record, quoted
        shown = " ".join([str(a.get("text") or "")] + [str(x) for x in a.get("lines") or []] + [c["text"] for c in a.get("chips") or []])
        assert not OLD.search(shown), (q, tab, clean(shown))
    a = chat.ask(wd, res, question="what do the tabs show", llm=FakeAI({"action": "app", "topic": "tab"}), allow_ai=True, tab="")["answer"]
    assert "five tabs" in a["text"] and "Export menu" in a["text"] and "Data" in a["text"], a["text"]
    b = chat.ask(wd, res, question="what does this tab show", llm=None, allow_ai=False, tab="What we cleaned")["answer"]
    assert b["text"].startswith("The Data tab, What we cleaned view")


def test_view_messages_use_the_new_names(run):
    rid, wd, res = run
    cat = ve.catalogue(res)
    for t in ve.TABS:
        for bad in (lambda: ve._tab({"cmd": "set_mode", "tab": t}, {"items": {}, "tabs": {t: {"sections": []}}}) if False else None,):
            bad()
    for q in ("hide the power bi export", "make the cleaning tab shorter", "make this tab shorter", "hide all the charts", "show the top 3 on the chat tab"):
        for tab in ("Report", "What we cleaned", "Power BI files", "Lens", "Data"):
            a = chat.ask(wd, res, question=q, llm=None, allow_ai=False, tab=tab)["answer"]
            shown = " ".join([str(a.get("text") or "")] + [str(x) for x in a.get("lines") or []] + [c["text"] for c in a.get("chips") or []])
            assert not OLD.search(shown), (q, tab, clean(shown))
            chat.ask(wd, res, question="undo", llm=None, allow_ai=False, tab=tab)


def strip_comments(src: str) -> str:
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?<![:\"'])//[^\n]*", "", src)


def test_the_interface_source_has_no_old_tab_name_in_visible_text():
    bad = {}
    for p in list((ROOT / "frontend" / "src").rglob("*.ts*")):
        found = OLD.findall(strip_comments(p.read_text(encoding="utf-8")))
        if found:
            bad[str(p.relative_to(ROOT))] = found
    assert not bad, bad


def test_the_landing_tour_lists_the_five_real_tabs():
    src = (ROOT / "frontend" / "src" / "components" / "StartPage.tsx").read_text(encoding="utf-8")
    assert "const TOUR_TABS: NavTab[] = NAV_TABS" in src and "NAV_LABEL[t]" in src
    tabs = (ROOT / "frontend" / "src" / "tabs.ts").read_text(encoding="utf-8")
    assert re.search(r'NAV_TABS: NavTab\[\] = \["report", "insights", "strategy", "dashboard", "data"\]', tabs)
