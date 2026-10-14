import pytest
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


def test_duration_policy_rejects_over_cap_happyhorse_too():
    import pytest
    from src.api.context import _duration_for_video_policy
    from src.services.video_capabilities import normalize_video_policy

    policy = normalize_video_policy({
        "provider": "happyhorse",
        "model": "happyhorse-1.1-i2v",
        "resolution": "1080p",
    })

    with pytest.raises(Exception, match=r"15"):
        _duration_for_video_policy(20, policy)


def test_happyhorse_client_rejects_over_cap_instead_of_clamping(monkeypatch):
    import asyncio
    import pytest
    from src.api_clients.happyhorse import HappyHorseVideoClient

    client = HappyHorseVideoClient(api_key="test")

    async def unexpected_submit(**_kwargs):
        raise AssertionError("invalid duration must fail before provider submission")

    monkeypatch.setattr(client, "_submit_task", unexpected_submit)
    result = asyncio.run(client.generate_video("test", duration_sec=20))
    assert result.success is False
    assert "4-15" in result.error_message


def test_happyhorse_accepts_the_full_integral_duration_range():
    from src.services.video_capabilities import normalize_video_policy, validate_video_duration

    policy = normalize_video_policy({
        "provider": "happyhorse",
        "model": "happyhorse-1.1-r2v",
        "resolution": "1080p",
    })

    for duration in (4, 5, 6, 8, 9, 10, 11, 14, 15):
        assert validate_video_duration(policy, duration) == duration


def test_happyhorse_duration_policy_rejects_fractional_duration_without_rounding():
    from src.services.video_capabilities import normalize_video_policy, validate_video_duration

    policy = normalize_video_policy({
        "provider": "happyhorse",
        "model": "happyhorse-1.1-i2v",
        "resolution": "1080p",
    })

    with pytest.raises(Exception, match="integer|整数"):
        validate_video_duration(policy, 15.4)


def test_happyhorse_client_rejects_fractional_duration_without_rounding():
    from src.api_clients.happyhorse import HappyHorseVideoClient

    client = HappyHorseVideoClient(api_key="test")

    with pytest.raises(ValueError, match="integer|整数"):
        client._validate_duration(3.4)


def test_fractional_declared_duration_option_is_ignored_instead_of_rounded():
    from src.services.video_capabilities import duration_options_from_capability

    assert duration_options_from_capability({
        "discrete_durations": True,
        "durations": [3.4, 5, "not-a-duration"],
    }) == [5]


def test_happyhorse_accepts_non_discrete_durations_in_range():
    from src.services.video_capabilities import normalize_video_policy, validate_video_duration

    policy = normalize_video_policy({
        "provider": "happyhorse",
        "model": "happyhorse-1.1-r2v",
        "resolution": "1080p",
    })

    for duration in (4, 6, 8, 9, 11, 14, 15):
        assert validate_video_duration(policy, duration) == duration
