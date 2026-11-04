import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Keys such as GROQ_API_KEY live in .env (project root or backend/). Real environment variables win over the file.
try:
    from dotenv import load_dotenv
    for _env in (ROOT / ".env", ROOT.parent / ".env"):
        load_dotenv(_env, override=False)
except ImportError:  # python-dotenv is optional: without it only real environment variables are read
    pass
VERSION = os.environ.get("APP_VERSION", "1.0.0")
# Everything the app writes (runs, uploads, the glossary, the AI cache, the daily counts) lives under one folder: mount it as a volume. Each part can still be moved by its own variable.
DATA_DIR = Path(os.environ.get("DATA_DIR") or ROOT / "data")
RUNS_DIR = Path(os.environ.get("RUNS_DIR") or DATA_DIR / "runs")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))  # total across all files of one run
MAX_FILES = int(os.environ.get("MAX_FILES", "8"))


def max_concurrent_runs() -> int:
    """How many runs may work at the same time (default 2, at least 1). Extra runs wait with the status "queued"."""
    try:
        return max(1, int(os.environ.get("MAX_CONCURRENT_RUNS", "2")))
    except ValueError:
        return 2


MAX_ROWS = int(os.environ.get("MAX_ROWS", "500000"))  # rows in one table; a larger file is rejected at upload with a clear message
# Runs (and their uploads) older than this many days are deleted at start-up and when a new run is created. 0 keeps everything.
def on(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "on", "yes")


def public_demo() -> bool:
    """PUBLIC_DEMO=on: a public sample site (short retention, uploads and AI questions behind an access code, low daily caps). Read when asked, so a change takes effect without a restart in tests."""
    return on("PUBLIC_DEMO")


def retention_days() -> float:
    """Days a finished run is kept: RETENTION_DAYS, else 3 on a public demo, else 30. 0 keeps everything."""
    raw = os.environ.get("RETENTION_DAYS", "").strip()
    try:
        return float(raw) if raw else (3.0 if public_demo() else 30.0)
    except ValueError:
        return 3.0 if public_demo() else 30.0


RETENTION_DAYS = retention_days()          # the value at start-up (kept for callers that import it)
# How many critic -> analyst follow-up rounds are allowed after the first pass.
MAX_ITERATIONS = int(os.environ.get("MAX_ITERATIONS", "1"))
CHART_DPI = int(os.environ.get("CHART_DPI", "192"))  # lower it (the tests use 50) to render charts faster
ALLOWED_EXT = {".csv", ".tsv", ".txt", ".xlsx", ".xls", ".json"}
FRONTEND_DIST = ROOT.parent / "frontend" / "dist"
DEMO_DIR = ROOT.parent / "sample_data" / "demo" / "sales_run"   # a finished run shipped with the repo: read-only, no AI calls, no uploads

RUNS_DIR.mkdir(parents=True, exist_ok=True)
