from pathlib import Path


def test_confirm_route_imports_the_shared_public_job_formatter():
    source = Path("src/api/routes/scripts.py").read_text(encoding="utf-8")

    assert "from .jobs import _job_public" in source
