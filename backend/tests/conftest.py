"""Test isolation, applied before any test module imports the app: no LLM lookups, small charts, and runs and the glossary go to a throw-away folder
(not backend/data, which would otherwise collect hundreds of run folders and slow down every directory scan)."""
import os
import shutil
import tempfile

_TMP = tempfile.mkdtemp(prefix="analyst_studio_tests_")
os.environ.setdefault("LLM_PROVIDER", "none")
os.environ.setdefault("CHART_DPI", "50")     # full runs draw 15-25 charts each; pixels are never checked
os.environ["RUNS_DIR"] = os.path.join(_TMP, "runs")
os.environ.setdefault("GLOSSARY_PATH", os.path.join(_TMP, "glossary.json"))


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)
