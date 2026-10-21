import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = Path(os.environ.get("RUNS_DIR", ROOT / "data" / "runs"))
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))  # total across all files of one run
MAX_FILES = int(os.environ.get("MAX_FILES", "8"))
# How many critic -> analyst follow-up rounds are allowed after the first pass.
MAX_ITERATIONS = int(os.environ.get("MAX_ITERATIONS", "1"))
ALLOWED_EXT = {".csv", ".tsv", ".txt", ".xlsx", ".xls", ".json"}
FRONTEND_DIST = ROOT.parent / "frontend" / "dist"

RUNS_DIR.mkdir(parents=True, exist_ok=True)
