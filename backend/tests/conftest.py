"""Test isolation, applied before any test module imports the app: no LLM lookups, small charts, and runs and the glossary go to a throw-away folder
(not backend/data, which would otherwise collect hundreds of run folders and slow down every directory scan)."""
import os
import shutil
import tempfile

_TMP = tempfile.mkdtemp(prefix="analyst_studio_tests_")
os.environ.setdefault("LLM_PROVIDER", "none")
os.environ.setdefault("OWNER_SECRET", "test-only-owner-secret-0123456789abcdef")       # tests never write a secret file into backend/data
os.environ["UPLOAD_RATE_LIMIT"] = "0"         # the public-use protections are off unless a test switches one on
os.environ["DAILY_AI_RUNS"] = "0"
os.environ["DAILY_LENS_QUESTIONS"] = "0"
os.environ.pop("UPLOADS_REQUIRE_CODE", None)
os.environ.setdefault("CHART_DPI", "50")     # full runs draw 15-25 charts each; pixels are never checked
os.environ["RUNS_DIR"] = os.path.join(_TMP, "runs")
os.environ.setdefault("GLOSSARY_PATH", os.path.join(_TMP, "glossary.json"))


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_TMP, ignore_errors=True)


import pytest

PINNED_TOKEN = "T" * 43       # a browser id a test client can hold from its first request, so a test can also read or seed that visitor's glossary


def pinned_scope(token: str = PINNED_TOKEN) -> str:
    from app import owner
    return owner.hash_token(token)[:32]


@pytest.fixture(autouse=True)
def fresh_ai_state(monkeypatch, tmp_path):
    """Every test starts without a daily-limit stop or cached AI answers, and never really waits for a rate-limit retry (tests that check the wait patch it themselves)."""
    from app import llm
    llm.begin_run()
    monkeypatch.setenv("AI_CACHE_DIR", str(tmp_path / "ai_cache"))      # the saved AI results never leak from one test to another
    monkeypatch.setenv("AI_CACHE", "off")      # fake models answer the same prompt differently from one call to the next; the tests of the cache itself switch it on
    monkeypatch.setattr(llm, "_sleep", lambda s: None)
    from app import pacing
    pacing.reset()
    monkeypatch.setattr(pacing, "_sleep", lambda s: None)      # a test never really waits for the token window (the pacing tests use a fake clock)
    yield
    llm.begin_run()


ORDER_KEY_QUESTION_TESTS = ("test_order_key", "test_varied_data")       # the modules that exercise the order-key question itself


@pytest.fixture(autouse=True, scope="module")
def no_order_key_question(request):
    """Rules-only runs pause with "Which column identifies one order?" whenever a column is named like an order key. The older tests are about other things and use order_id columns freely, so for them the
    question is not generated; the tests of the question itself (ORDER_KEY_QUESTION_TESTS) see the real behaviour. Module scope, because module-scoped run fixtures are built before any function fixture."""
    if request.module.__name__.split(".")[-1] in ORDER_KEY_QUESTION_TESTS:
        yield
        return
    from app import signals
    mp = pytest.MonkeyPatch()
    mp.setattr(signals, "order_key_signal", lambda profile: [])
    yield
    mp.undo()
