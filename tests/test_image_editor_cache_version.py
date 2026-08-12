from pathlib import Path


def test_frontend_module_url_uses_the_current_cache_version():
    source = Path("studio.html").read_text(encoding="utf-8")

    assert "bootstrap.js?v=20260811-asset-quality-references" in source
