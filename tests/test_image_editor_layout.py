from pathlib import Path


def test_image_editor_has_compact_two_column_structure_and_explicit_empty_state():
    html = Path("studio.html").read_text(encoding="utf-8")
    css = Path("web/css/base.css").read_text(encoding="utf-8")
    editor = Path("web/js/components/image-editor.js").read_text(encoding="utf-8")

    assert 'id="image-editor-empty-state"' in html
    assert 'class="image-editor-prompt-details"' in html
    assert 'class="image-editor-advanced"' in html
    assert 'id="image-editor-history-section"' in html
    assert '.image-editor-layout' in css
    assert 'grid-template-columns: minmax(300px, .9fr) minmax(420px, 1.1fr)' in css
    assert 'image-editor-empty-state' in editor
    assert 'image-editor-history-section' in editor


def test_image_editor_hides_empty_history_and_does_not_render_empty_image_src():
    editor = Path("web/js/components/image-editor.js").read_text(encoding="utf-8")

    assert 'historySection.hidden = !history.length' in editor
    assert "previewImage.removeAttribute('src')" in editor
    assert "previewEmpty.hidden" in editor


def test_asset_cards_use_explicit_empty_and_load_failure_states():
    cards = Path("web/js/components/asset-cards.js").read_text(encoding="utf-8")
    shots = Path("web/js/pages/shots.js").read_text(encoding="utf-8")
    css = Path("web/css/base.css").read_text(encoding="utf-8")

    assert "function assetPreviewMarkup" in cards
    assert "handleAssetCardImageError" in cards
    assert "data-asset-status" in cards
    assert "assetPreviewMarkup(image, prop.name, missing)" in shots
    assert ".asset-preview-empty.is-error" in css


def test_history_records_with_empty_paths_are_not_rendered_as_images():
    editor = Path("web/js/components/image-editor.js").read_text(encoding="utf-8")

    assert "isUsableHistoryRecord(record)" in editor
    assert "文件不可用" in editor
    assert "historyError" in editor


def test_image_editor_inspector_has_clear_typography_and_control_groups():
    html = Path("studio.html").read_text(encoding="utf-8")
    css = Path("web/css/base.css").read_text(encoding="utf-8")
    editor = Path("web/js/components/image-editor.js").read_text(encoding="utf-8")

    assert 'image-editor-view-tabs' in html
    assert 'image-editor-view-tab' in editor
    assert 'class="image-editor-section image-editor-policy-section"' in html
    assert 'class="image-editor-section image-editor-prompt-section"' in html
    assert '.image-editor-section-title' in css
    assert '.image-generation-policy select' in css
    assert '.image-editor-help' in css
    assert 'aria-label="当前视图"' in html


def test_image_editor_primary_actions_remain_explicit_and_easy_to_target():
    html = Path("studio.html").read_text(encoding="utf-8")
    css = Path("web/css/base.css").read_text(encoding="utf-8")

    assert 'class="image-editor-footer-actions"' in html
    assert 'class="btn-primary image-editor-primary-action"' in html
    assert '.image-editor-primary-action' in css
    assert '.image-editor-footer-actions' in css
