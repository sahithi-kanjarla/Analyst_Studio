"""backend/constraints.txt must be installable: every line an exact pin, and (with RUN_PIP_RESOLVE=1, which needs the network) pip's resolver accepts it with requirements.txt."""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _pins():
    lines = [l.split("#")[0].strip() for l in (ROOT / "backend" / "constraints.txt").read_text().splitlines()]
    return [l for l in lines if l]


def test_every_line_is_an_exact_pin_and_names_are_unique():
    pins = _pins()
    assert pins
    for l in pins:
        assert re.fullmatch(r"[A-Za-z0-9_.\-]+==[A-Za-z0-9_.!+\-]+", l), l
    names = [l.split("==")[0].lower().replace("_", "-") for l in pins]
    assert len(names) == len(set(names))


def test_langchain_core_and_certifi_agree():
    """The pair that once made the Dockerfile install fail: langchain-core 1.7.4 needs certifi >= 2026.10.19."""
    d = dict(l.split("==") for l in _pins())
    key = lambda v: tuple(int(x) for x in v.split(".") if x.isdigit())
    if d.get("langchain-core") == "1.7.4":
        assert key(d["certifi"]) >= key("2026.10.19")


@pytest.mark.skipif(os.environ.get("RUN_PIP_RESOLVE") != "1", reason="needs the network: set RUN_PIP_RESOLVE=1")
def test_constraints_resolve_with_pip_dry_run():
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "check_constraints.py")], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
