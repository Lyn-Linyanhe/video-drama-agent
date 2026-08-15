from pathlib import Path

from PIL import Image


ROOT = Path(__file__).parents[1]


def test_reference_assets_are_16_by_9_webp():
    names = ("cinematic-cel", "semi-realistic-anime", "japanese-animation", "live-action-cinematic")
    for name in names:
        path = ROOT / "web" / "assets" / "style-references" / f"{name}.webp"
        assert path.is_file(), name
        with Image.open(path) as image:
            assert image.format == "WEBP"
            assert image.size == (1600, 900)


def test_creation_page_has_style_card_container_and_modifier_tags():
    html = (ROOT / "studio.html").read_text(encoding="utf-8")
    assert 'id="visual-style-options"' in html
    assert 'id="style-modifier-tags"' in html
    assert "现代电影级二维赛璐璐" in html
    assert "参考图只用于选择风格" in html
    assert 'id="style-tags"' not in html


def test_creation_dialog_stays_inside_the_viewport_and_can_scroll():
    html = (ROOT / "studio.html").read_text(encoding="utf-8")
    css = (ROOT / "web" / "css" / "base.css").read_text(encoding="utf-8")

    assert 'class="modal project-create-modal"' in html
    assert ".project-create-modal" in css
    assert "max-height: calc(100dvh - 32px)" in css
    assert "overflow-y: auto" in css


def test_script_module_submits_canonical_style_id_and_modifiers():
    source = (ROOT / "web/js/pages/script.js").read_text(encoding="utf-8")
    assert "loadVisualStyles" in source
    assert "selectVisualStyle" in source
    assert "visual_style_id" in source
    assert "style_modifiers" in source


def test_creation_style_catalog_has_anime_fallbacks_before_api_returns():
    source = (ROOT / "web/js/pages/script.js").read_text(encoding="utf-8")
    assert "FALLBACK_VISUAL_STYLES" in source
    assert "semi_realistic_anime" in source
    assert "japanese_animation" in source
    assert "renderVisualStyleOptions();" in source.split("void loadVisualStyles();", 1)[0]


def test_style_cards_use_delegated_clicks_and_retry_catalog_loading():
    source = (ROOT / "web/js/pages/script.js").read_text(encoding="utf-8")
    assert "addEventListener('click'" in source
    assert "visualStylesLoadPromise = null" in source


def test_creation_dialog_resets_the_complete_default_style_state():
    source = (ROOT / "web/js/pages/script.js").read_text(encoding="utf-8")
    dialog_source = source.split("function openNewProjectModal()", 1)[1].split(
        "function closeModal", 1
    )[0]

    assert "selectVisualStyle('cinematic_cel', false)" in dialog_source


def test_cached_style_catalog_rehydrates_a_reset_project_session():
    source = (ROOT / "web/js/pages/script.js").read_text(encoding="utf-8")
    loader_source = source.split("async function loadVisualStyles()", 1)[1].split(
        "function selectVisualStyle", 1
    )[0]

    assert "visualStylesLoadPromise.then" in loader_source
    assert "appState.visualStyles = mergeVisualStyleCatalog(styles)" in loader_source


def test_studio_uses_current_module_cache_version():
    html = (ROOT / "studio.html").read_text(encoding="utf-8")
    assert "20260815-image-preview-repair" in html


def test_project_restore_reads_saved_style_and_resets_session():
    source = (ROOT / "web/js/pages/projects.js").read_text(encoding="utf-8")
    assert "profile.visual_style_id" in source
    assert "profile.style_modifiers" in source
    assert "resetProjectSession" in source


def test_reference_assets_are_served_by_fastapi():
    from fastapi.testclient import TestClient
    from api_server import app

    response = TestClient(app).get("/static/assets/style-references/cinematic-cel.webp")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/webp")
