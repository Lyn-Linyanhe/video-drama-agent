from fastapi.testclient import TestClient
from pathlib import Path
from urllib.parse import quote

import api_server


EXPECTED_PATHS = {
    "/api/health",
    "/api/script",
    "/api/projects",
    "/api/projects/{project_id}",
    "/api/projects/{project_id}/assets/generate-pending",
    "/api/projects/{project_id}/scenes/{asset_id}/master/generate",
    "/api/projects/{project_id}/scenes/{asset_id}/master/confirm",
    "/api/projects/{project_id}/scenes/{asset_id}/states/{state_id}/master/generate",
    "/api/projects/{project_id}/scenes/{asset_id}/states/{state_id}/master/confirm",
    "/api/projects/{project_id}/scenes/{asset_id}/states/{state_id}/reference-group/generate",
    "/api/projects/{project_id}/scenes/{asset_id}/states/{state_id}/views/{view_type}/edit",
    "/api/projects/{project_id}/scenes/{asset_id}/states/{state_id}/view-requirements",
    "/api/projects/{project_id}/scenes/{asset_id}/view-requirements",
    "/api/projects/{project_id}/props/{prop_id}/master/generate",
    "/api/projects/{project_id}/props/{prop_id}/master/confirm",
    "/api/projects/{project_id}/props/{prop_id}/views/{view_type}/generate",
    "/api/projects/{project_id}/props/{prop_id}/views/generate",
    "/api/projects/{project_id}/props/{prop_id}/view-requirements",
    "/api/projects/{project_id}/image-generation-policy",
    "/api/projects/{project_id}/video-generation-policy",
    "/api/projects/{project_id}/production",
    "/api/projects/{project_id}/audio-timeline",
    "/api/projects/{project_id}/audio-preflight",
    "/api/projects/{project_id}/audio-timeline/subtitles",
    "/api/projects/{project_id}/jobs",
    "/api/projects/{project_id}/job-events",
    "/api/projects/{project_id}/script/draft",
    "/api/projects/{project_id}/script/draft/confirm",
    "/api/projects/{project_id}/image-history/review",
    "/api/projects/{project_id}/image-history/candidate/accept",
    "/api/projects/{project_id}/image-history/candidate/reject",
    "/api/settings",
    "/api/settings/visual-review-capabilities",
    "/api/settings/test-visual-review",
    "/api/image-capabilities",
    "/api/video-capabilities",
    "/api/test/tts",
    "/api/visual-styles",
}


def test_domain_routers_preserve_public_contract():
    paths = set(api_server.app.openapi()["paths"])
    assert EXPECTED_PATHS <= paths
    # Five visual-review endpoints were added to the public contract.
    # A standalone subtitle burn endpoint was added on top of those.
    assert len(paths) == 94
    # The entrypoint only composes the FastAPI application.  Route internals
    # remain owned by their domain modules rather than becoming implicit API
    # server globals again.
    assert not hasattr(api_server, "generate_pending_project_assets")
    assert not hasattr(api_server, "stream_project_job_events")


def test_studio_shell_and_native_modules_are_served():
    client = TestClient(api_server.app)
    page = client.get("/")
    assert page.status_code == 200
    assert 'type="module" src="/static/js/bootstrap.js?v=' in page.text
    assert "function defaultAppState" not in page.text

    bootstrap = client.get("/static/js/bootstrap.js")
    assert bootstrap.status_code == 200
    assert "await import(`./core/state-api.js${moduleVersion}`)" in bootstrap.text

    audio = client.get("/static/js/components/audio-timeline.js")
    assert audio.status_code == 200
    assert "productionAudioTimelineHtml" in audio.text


def test_studio_frontend_resources_are_not_served_from_a_stale_browser_cache():
    client = TestClient(api_server.app)

    page = client.get("/")
    assert page.headers["cache-control"] == "no-store, max-age=0, must-revalidate"
    assert "bootstrap.js?v=20260906-reasonable-expansion-1" in page.text

    module = client.get("/static/js/pages/production-actions.js")
    assert module.headers["cache-control"] == "no-store, max-age=0, must-revalidate"


def test_production_reference_images_are_versioned_for_media_cache_invalidation():
    source = Path("web/js/pages/production.js").read_text(encoding="utf-8")
    assert "const versionId = reference.asset_version_id || reference.version_id || '';" in source
    assert "return mediaUrl(reference.image_path, versionId);" in source


def test_generated_media_is_not_served_from_a_stale_browser_cache():
    files = [path for path in Path("output").rglob("*") if path.is_file()]
    assert files, "the project must retain its generated output assets"
    relative = files[0].relative_to(Path("output")).as_posix()

    response = TestClient(api_server.app).get(f"/output/{quote(relative, safe='/')}")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store, max-age=0, must-revalidate"


def test_seedance_ui_contract_exposes_current_models_and_reference_decisions():
    from pathlib import Path

    settings = Path("web/js/pages/settings.js").read_text(encoding="utf-8")
    studio = Path("studio.html").read_text(encoding="utf-8")
    jobs = Path("web/js/core/job-events.js").read_text(encoding="utf-8")
    production = Path("web/js/pages/production-actions.js").read_text(encoding="utf-8")

    assert "doubao-seedance-2-0-fast-260128" in settings
    assert "doubao-seedance-2-5-260628" in studio
    assert "doubao-seedance-2-0-mini-260615" in studio
    assert "accepted_references" in jobs
    assert "dropped_references" in jobs
    assert "doubao-seedance-2-0-fast-260128" in production
    assert "duration_min" in production
    assert "max_reference_images" in production
    assert "reference_modes" in production


def test_production_page_does_not_render_price_or_transition_editors():
    from pathlib import Path

    source = Path("web/js/pages/production-actions.js").read_text(encoding="utf-8")
    assert "production-video-price" not in source
    assert "production-transition-duration" not in source
    assert "id=\"production-transition\"" not in source


def test_audio_workspace_has_single_mount_and_subtitle_selector():
    source = Path("web/js/components/audio-timeline.js").read_text(encoding="utf-8")
    assert "production-audio-workspace" in source
    assert "subtitle-mode" in source
    assert "句失败待重试" in source
    assert "句待生成" in source
