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

    assert "version_id:item.asset_version_id || ''" in source
    assert "productionAddReference(referenceId)" in source
    assert "source:'manual'" in source


def test_explicit_asset_mentions_are_inserted_and_saved_with_version_token():
    source = Path("web/js/pages/production-actions.js").read_text(encoding="utf-8")

    assert "reference_token:item.reference_token || ''" in source
    assert "productionInsertMention" in source
    assert "productionMentionInput" in source
    assert "source: 'explicit'" in source
    assert "const sourceLabel = reference.source === 'explicit'" not in source
    assert "syncProductionMentionBindings" in source
    assert "explicit_base_source" in source


def test_production_editor_supports_keyboard_at_completion_and_reference_metadata():
    source = Path("web/js/pages/production-actions.js").read_text(encoding="utf-8")

    assert "productionMentionKeydown" in source
    assert "productionMentionSuggestions" in source
    assert "image-history" in source
    assert "sourceLabel" in source
    assert "asset_version_id" in source


def test_manual_reference_history_version_is_submitted_to_backend():
    source = Path("web/js/pages/production-actions.js").read_text(encoding="utf-8")

    # A manually selected historical image must remain pinned after reload;
    # version_id is therefore independent of whether the reference also has
    # an explicit @ token.
    assert "version_id:item.asset_version_id || ''" in source


def test_production_editor_warns_before_continuity_reference_conflict():
    source = Path("web/js/pages/production-actions.js").read_text(encoding="utf-8")

    assert "continuityConsumesReferences" in source
    assert "显式 @ 引用" in source
    assert "production-continuity-conflict" in source


def test_production_editor_has_no_corrupted_review_or_placeholder_labels():
    source = Path("web/js/pages/production-actions.js").read_text(encoding="utf-8")

    assert "AI 淇敼澶辫触" not in source
    assert "????" not in source


def test_prompt_policy_ui_uses_compact_status_and_lazy_editor():
    """An empty project policy must not render a large blank editor by default."""
    source = Path("web/js/pages/production-actions.js").read_text(encoding="utf-8")

    assert "function promptPolicyEditorHtml" in source
    assert "function promptPolicySummaryHtml" in source
    assert "production-prompt-policy-slot" in source
    assert "productionOpenPromptPolicy" in source
    assert "promptPolicySummaryHtml(promptPolicy)" in source


def test_production_workspace_has_semantic_editor_sections():
    """The shot editor keeps a predictable visual hierarchy without dropping controls."""
    source = Path("web/js/pages/production-actions.js").read_text(encoding="utf-8")

    for section in (
        "production-editor-header",
        "production-reference-section",
        "production-prompt-section",
        "production-generation-section",
        "production-ai-section",
        "production-editor-footer-actions",
    ):
        assert section in source

    for control in (
        'id="production-duration"',
        'id="production-video-provider"',
        'id="production-video-model"',
        'id="production-video-resolution"',
        'id="production-continuity"',
        'id="productionMentionInput"',
        'id="production-prompt"',
        'id="production-negative"',
        'id="production-feedback"',
        'id="production-generate-current"',
    ):
        assert control in source


def test_production_workspace_uses_labeled_primary_actions():
    source = Path("web/js/pages/production-actions.js").read_text(encoding="utf-8")

    assert 'class="production-section-heading"' in source
    assert 'class="production-field-label"' in source
    assert "production-editor-footer-actions" in source
    assert 'class="production-reference-toolbar"' in source


def test_reference_manager_is_compact_but_keeps_advanced_controls():
    """Automatic references should not dominate the editor while remaining manageable."""
    source = Path("web/js/pages/production-actions.js").read_text(encoding="utf-8")

    assert 'class="production-reference-summary"' in source
    assert 'class="production-reference-manager"' in source
    assert "管理参考图" in source
    assert "submittedReferenceCount" in source
    assert "production.references.map(productionReferenceHtml)" in source
    assert 'class="production-reference-toolbar"' in source


def test_asset_mentions_use_visual_chips_with_plain_text_serialization():
    source = Path("web/js/pages/production-actions.js").read_text(encoding="utf-8")

    assert 'class="production-mention-chip"' in source
    assert "data-asset-token" in source
    assert "productionPromptSerializeNode" in source
    assert 'id="production-prompt-value"' in source
    assert "productionSyncPromptValue()" in source


def test_mention_picker_is_grouped_and_restores_automatic_reference_source():
    source = Path("web/js/pages/production-actions.js").read_text(encoding="utf-8")

    for group in ("characters", "scenes", "props"):
        assert f"['{group}'" in source
    assert "productionOpenMentionPicker" in source
    assert "productionMentionSearch" in source
    assert "explicit_base_source === 'asset'" in source


def test_mentions_expose_structured_entity_metadata_and_reopen_picker_on_click():
    source = Path("web/js/pages/production-actions.js").read_text(encoding="utf-8")

    assert "productionMentionEntityType" in source
    assert "data-entity-mention" in source
    assert "open-production-mention-picker" in source
    assert "productionOpenMentionPickerForChip" in source
    assert "production-prompt-editor" in source


def test_mention_picker_merges_catalog_and_current_manual_references():
    source = Path("web/js/pages/production-actions.js").read_text(encoding="utf-8")

    assert "const references = productionPromptReferenceCatalog()" in source
    assert "productionMentionEntityType(reference) === 'role'" in source
    assert "productionMentionEntityType(reference) === 'location'" in source
    assert "productionMentionEntityType(reference) === 'prop'" in source
    assert "const merged = new Map()" in source
    assert "current.forEach(item" in source
    assert "explicit @ mentions survive reloads" in source
