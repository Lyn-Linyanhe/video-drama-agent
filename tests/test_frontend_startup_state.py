from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_frontend_does_not_present_offline_before_health_check():
    page = (ROOT / "studio.html").read_text(encoding="utf-8")
    assert 'id="api-status">连接中' in page
    assert 'id="api-status">离线' not in page


def test_project_list_has_explicit_loading_and_failure_states():
    source = (ROOT / "web/js/pages/projects.js").read_text(encoding="utf-8")
    assert "正在加载项目" in source
    assert "项目列表加载失败" in source


def test_api_health_check_runs_after_all_frontend_modules_are_loaded():
    bootstrap = (ROOT / "web/js/bootstrap.js").read_text(encoding="utf-8")
    shots = (ROOT / "web/js/pages/shots.js").read_text(encoding="utf-8")
    script = (ROOT / "web/js/pages/script.js").read_text(encoding="utf-8")
    assert "function checkApiHealth" in bootstrap
    assert "/api/health" in bootstrap
    assert "/api/health" not in shots
    assert "/api/health" not in script
