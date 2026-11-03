"""Checks that backend/constraints.txt installs together with backend/requirements.txt: pip's own resolver, as a dry run (nothing is installed).
This is the exact resolution the Dockerfile does. Needs network access to the package index. Exit code 0 = resolves cleanly.
Run:  python scripts/check_constraints.py"""
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"


def main() -> int:
    cmd = [sys.executable, "-m", "pip", "install", "--dry-run", "--ignore-installed", "--disable-pip-version-check", "-r", str(BACKEND / "requirements.txt"), "-c", str(BACKEND / "constraints.txt")]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print((r.stdout + r.stderr)[-3000:])
        print("FAIL: constraints.txt does not resolve with requirements.txt (see DEPLOY.md, 'Pinned dependency versions').")
        return 1
    print("OK: constraints.txt resolves cleanly with requirements.txt.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
