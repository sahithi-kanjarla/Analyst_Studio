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
RUNS_DIR = Path(os.environ.get("RUNS_DIR", ROOT / "data" / "runs"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))  # total across all files of one run
MAX_FILES = int(os.environ.get("MAX_FILES", "8"))
# How many critic -> analyst follow-up rounds are allowed after the first pass.
MAX_ITERATIONS = int(os.environ.get("MAX_ITERATIONS", "1"))
CHART_DPI = int(os.environ.get("CHART_DPI", "140"))  # lower it (the tests use 50) to render charts faster
ALLOWED_EXT = {".csv", ".tsv", ".txt", ".xlsx", ".xls", ".json"}
FRONTEND_DIST = ROOT.parent / "frontend" / "dist"

RUNS_DIR.mkdir(parents=True, exist_ok=True)
