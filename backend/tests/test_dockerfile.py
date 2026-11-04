"""The image must cope with a volume mounted as root (Railway does this): it starts as root, fixes /data, then drops to the app user."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_dockerfile_uses_an_entrypoint_that_drops_privileges():
    d = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    lines = [l.strip() for l in d.splitlines() if not l.strip().startswith("#")]
    assert not any(l.startswith("USER ") for l in lines), "a USER line would stop the entrypoint from fixing a root-owned volume"
    assert any(l.startswith("ENTRYPOINT") and "docker-entrypoint.sh" in l for l in lines)
    assert any(l.startswith("COPY docker-entrypoint.sh") for l in lines)


def test_entrypoint_fixes_the_volume_then_runs_as_app():
    e = (ROOT / "docker-entrypoint.sh").read_text(encoding="utf-8")
    assert "\r" not in e, "must use LF line endings to run in Linux"
    assert "chown" in e and "10001" in e
    assert 'exec setpriv --reuid=10001' in e and '"$@"' in e


def test_dockerignore_keeps_what_the_image_needs():
    ig = {l.strip() for l in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()}
    for needed in ("docker-entrypoint.sh", "sample_data", "backend/app", "backend/requirements.txt", "backend/constraints.txt"):
        assert needed not in ig
    assert (ROOT / "sample_data" / "demo" / "sales_run").is_dir()
