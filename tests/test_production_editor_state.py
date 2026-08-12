from pathlib import Path


def test_production_draft_snapshot_uses_the_rendered_scene_id():
    source = Path("web/js/pages/production-actions.js").read_text(encoding="utf-8")

    capture_start = source.index("function captureProductionEditorState")
    capture_end = source.index("function restoreProductionEditorState", capture_start)
    capture = source[capture_start:capture_end]

    assert "dataset.productionEditorSceneId" in capture
    assert "sceneId: renderedSceneId" in capture


def test_manually_added_reference_is_saved_with_manual_source():
    source = Path("web/js/pages/production-actions.js").read_text(encoding="utf-8")

    assert "references: current.production.references.map(item => ({reference_id:item.reference_id,source:item.source || 'asset'}))" in source
    assert "productionAddReference(referenceId)" in source
    assert "source:'manual'" in source
