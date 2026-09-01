from pathlib import Path


def test_production_model_selection_has_a_separate_change_path():
    source = Path("web/js/pages/production-actions.js").read_text(encoding="utf-8")

    assert 'id="production-video-provider" onchange="StudioActions.productionVideoProviderChanged()"' in source
    assert 'id="production-video-model" onchange="StudioActions.productionVideoModelChanged()"' in source
    assert "function productionVideoModelChanged()" in source
    assert "const selectedModel = model.value" in source


def test_video_selection_persists_as_project_default_for_following_shots():
    source = Path("web/js/pages/production-actions.js").read_text(encoding="utf-8")

    assert 'id="production-video-resolution" onchange="StudioActions.productionVideoResolutionChanged()"' in source
    assert "function persistProductionVideoPolicy" in source
    assert "/video-generation-policy" in source
    assert "productionData.video_generation_policy" in source

    provider_handler = source[source.index("function productionVideoProviderChanged") : source.index("async function productionGenerateCurrent")]
    assert "persistSelectedProductionVideoPolicy" in provider_handler
    assert "productionVideoResolutionChanged" in provider_handler


def test_video_generation_stops_when_shot_save_fails():
    source = Path("web/js/pages/production-actions.js").read_text(encoding="utf-8")

    assert "const saved = await productionSaveCurrent();" in source
    assert "if (!saved) return;" in source


def test_seedance_sensitive_reference_error_is_actionable():
    from src.api_clients.seedance import SeedanceVideoClient

    message = SeedanceVideoClient.format_provider_error(
        'Ark Seedance submit failed [400]: '
        '{"error":{"code":"InputImageSensitiveContentDetected.PrivacyInformation",'
        '"message":"the input image may contain real person"}}'
    )

    assert "InputImageSensitiveContentDetected.PrivacyInformation" in message
    assert "参考图" in message
    assert "快乐小马" in message


def test_seedance_sensitive_reference_errors_are_not_retried():
    from src.api_clients.seedance import SeedanceVideoClient

    assert SeedanceVideoClient.is_non_retriable_error(
        "InputImageSensitiveContentDetected.PrivacyInformation"
    )
    assert not SeedanceVideoClient.is_non_retriable_error("gateway timeout")


def test_seedance_can_run_text_to_video_when_all_references_are_removed():
    from src.services.video_capabilities import normalize_video_policy, requires_reference_image

    seedance = normalize_video_policy({
        "provider": "seedance",
        "model": "doubao-seedance-2-0-fast-260128",
        "resolution": "1080p",
    })
    happyhorse = normalize_video_policy({
        "provider": "happyhorse",
        "model": "happyhorse-1.1-i2v",
        "resolution": "1080p",
    })

    assert requires_reference_image(seedance) is False
    assert requires_reference_image(happyhorse) is True


def test_production_editor_clamps_existing_duration_to_selected_model_bounds():
    source = Path("web/js/pages/production-actions.js").read_text(encoding="utf-8")

    assert "if (Number.isFinite(currentDuration) && currentDuration < durationMin)" in source
    assert "durationInput.value = String(durationMin)" in source
