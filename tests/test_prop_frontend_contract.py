from pathlib import Path


def test_prop_asset_buttons_submit_jobs_and_track_reference_group():
    source = Path("web/js/pages/shots.js").read_text(encoding="utf-8")
    master_start = source.index("async function generatePropMaster")
    master_end = source.index("async function confirmPropMaster", master_start)
    master_block = source[master_start:master_end]
    asset_start = source.index("async function generateAssetCard")
    asset_end = source.index("async function confirmSceneMaster", asset_start)
    asset_block = source[asset_start:asset_end]

    assert "submitProjectJob" in master_block
    assert "/master/generate" not in master_block
    assert "view_type: 'front'" in master_block
    assert "prop.reference.group" in asset_block


def test_prop_reference_group_refreshes_asset_ui_and_task_drawer():
    events = Path("web/js/core/job-events.js").read_text(encoding="utf-8")
    drawer = Path("web/js/components/task-drawer.js").read_text(encoding="utf-8")
    editor = Path("web/js/components/image-editor.js").read_text(encoding="utf-8")

    assert "prop.reference.group" in events
    assert "prop.reference.group" in drawer
    assert "isPropGroup" in drawer
    assert "job.job_type === 'prop.reference.group'" in editor


def test_image_editor_job_controls_match_batch_and_single_asset_targets():
    drawer = Path("web/js/components/task-drawer.js").read_text(encoding="utf-8")

    assert "String(job.target_id) === activeImageEditor.assetId" in drawer
    assert "String(job.payload?.state_id || '')" in drawer
    assert "related.find(job => job.job_type === 'asset.generate'" in drawer
    assert "scene.reference.group" in drawer
    assert "prop.reference.group" in drawer


def test_scene_frontend_cards_and_editor_are_state_aware():
    script = Path("web/js/pages/script.js").read_text(encoding="utf-8")
    cards = Path("web/js/components/asset-cards.js").read_text(encoding="utf-8")
    shots = Path("web/js/pages/shots.js").read_text(encoding="utf-8")
    editor = Path("web/js/components/image-editor.js").read_text(encoding="utf-8")
    drawer = Path("web/js/components/task-drawer.js").read_text(encoding="utf-8")

    assert "flatMap((location" in script
    assert "stateId: state.state_id" in script
    assert "scene:${sceneId}:${stateId}" in cards
    assert "generateAssetCard('scene','${encodedId}',${Boolean(mainImg?.image_path)},'${escapeHtml(stateId)}')" in cards
    assert "state_id: stateId || ''" in shots
    assert "state_id: activeImageEditor.assetType === 'scene'" in editor
    assert "function hasSceneReferenceMaster" in editor
    assert "scene.reference.edit" in editor
    assert "job.payload?.state_id" in editor
    assert "stateSuffix" in drawer
    assert "scene.reference.edit" in drawer
