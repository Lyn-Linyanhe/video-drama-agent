"""Regression coverage for persisted storyboard production state."""

import json
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi import HTTPException

import src.config as config
from src.api import context as api_context
from src.api.context import (
    _build_video_generation_snapshot,
    ProductionReferenceRequest,
    _reconcile_shot_references,
    _reference_catalog,
    _state_from_json,
    _state_to_json,
    _sync_active_shot_asset,
)
from src.api.routes.production import _schedule_shot_video_job
from src.graph.state import DramaState
from src.agents.video_gen_agent import VideoGenerationAgent
from src.schemas.character import CharacterImage, CharacterImageAssets
from src.schemas.assets import ShotProductionState, ShotReference, ShotVideoVersion
from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.scene_image import SceneImage, SceneImageAssets
from src.schemas.prop import PropImage, PropImageAssets
from src.schemas.scene_state import LocationState
from src.schemas.script import LocationAsset, PropAsset, Scene, ScriptBreakdown, ShotVisualBinding, VisualAssetManifest


def _state() -> DramaState:
    return DramaState(
        project_id="123456abcdef",
        script=ScriptBreakdown(
            title="test",
            genre="test",
            logline="test",
            duration_sec=5,
            narration_style="none",
            scenes=[
                Scene(
                    scene_id=1,
                    duration_sec=5,
                    location="room",
                    characters=["Hero"],
                    action_description="Hero waits.",
                    camera_direction="medium shot",
                    emotion_tone="tense",
                )
            ],
        ),
        generation_prompts=GenerationPrompts(
            api_provider="mock",
            shots=[ShotPrompt(scene_id=1, prompt="Hero waits.", duration_sec=5)],
        ),
    )


def test_shot_production_state_round_trips_with_references_and_versions():
    state = _state()
    state.shot_productions[1] = ShotProductionState(
        scene_id=1,
        references=[
            ShotReference(
                reference_id="character|Hero|front_full",
                asset_type="character",
                asset_id="Hero",
                view_type="front_full",
                image_path="hero.png",
            )
        ],
        video_versions=[
            ShotVideoVersion(
                scene_id=1,
                local_path="v1.mp4",
                success=True,
                version_id="version-0001",
                created_at="2026-07-15T10:00:00",
            )
        ],
        active_version_id="version-0001",
        generation_status="success",
    )

    restored = _state_from_json(_state_to_json(state))

    production = restored.shot_productions[1]
    assert production.references[0].reference_id == "character|Hero|front_full"
    assert production.video_versions[0].version_id == "version-0001"
    assert production.active_version_id == "version-0001"


def test_reference_source_and_snapshot_round_trip():
    state = _state()
    state.shot_productions[1] = ShotProductionState(
        scene_id=1,
        references=[
            ShotReference(
                reference_id="character|Hero|front_full",
                source="asset",
                asset_type="character",
                asset_id="Hero",
                view_type="front_full",
                asset_version_id="hero-v2",
                image_path="hero-v2.png",
            )
        ],
        last_generation_snapshot={"scene_id": 1, "provider_prompt": "scene one"},
    )

    restored = _state_from_json(_state_to_json(state))

    assert restored.shot_productions[1].references[0].asset_version_id == "hero-v2"
    assert restored.shot_productions[1].last_generation_snapshot["provider_prompt"] == "scene one"


def test_production_reference_request_accepts_manual_source():
    reference = ProductionReferenceRequest(
        reference_id="character|Hero|front_full",
        source="manual",
    )

    assert reference.source == "manual"


def test_active_video_version_remains_compatible_with_generated_assets():
    state = _state()
    state.shot_productions[1] = ShotProductionState(
        scene_id=1,
        video_versions=[
            ShotVideoVersion(scene_id=1, local_path="first.mp4", success=True, version_id="version-0001", created_at=""),
            ShotVideoVersion(scene_id=1, local_path="second.mp4", success=True, version_id="version-0002", created_at=""),
        ],
        active_version_id="version-0002",
        generation_status="success",
    )

    _sync_active_shot_asset(state, 1)

    assert state.generated_assets is not None
    assert state.generated_assets.shots[0].local_path == "second.mp4"


def test_explicit_reference_labels_preserve_manual_reference_order_in_prompt():
    prompt = ShotPrompt(scene_id=1, prompt="Hero waits.", duration_sec=5)
    rendered = VideoGenerationAgent()._build_multi_reference_prompt(
        prompt,
        ["character.png", "scene.png"],
        reference_labels=["character: Hero front view", "scene: rainy street wide view"],
    )

    assert "reference image 1" not in rendered.lower()
    assert "Hero front view" in rendered
    assert "rainy street wide view" in rendered
    assert rendered.index("Hero front view") < rendered.index("rainy street wide view")


def test_reference_catalog_accepts_legacy_scene_reference_ids(tmp_path):
    image_path = tmp_path / "wide.png"
    image_path.write_bytes(b"png")
    state = _state()
    state.scene_images = SceneImageAssets(scenes=[
        SceneImage(
            scene_id=1,
            location_id="loc_room",
            location="room",
            view_type="wide",
            image_prompt="room",
            image_path=str(image_path),
            seed=1,
        )
    ])

    catalog = _reference_catalog(state)["by_id"]

    assert catalog["scene|loc_room|wide"].image_path == str(image_path)
    assert catalog["scene|1|wide"].image_path == str(image_path)
    assert catalog["scene|1|wide"].reference_id == "scene|loc_room|wide"


def test_reconcile_updates_asset_reference_and_keeps_manual_reference(monkeypatch):
    state = _state()
    state.shot_productions[1] = ShotProductionState(
        scene_id=1,
        references=[
            ShotReference(
                reference_id="character|Hero|front_full",
                source="asset",
                asset_type="character",
                asset_id="Hero",
                view_type="front_full",
                asset_version_id="hero-v1",
                image_path="hero-v1.png",
            ),
            ShotReference(
                reference_id="manual|board|1",
                source="manual",
                asset_type="manual",
                asset_id="board",
                view_type="image",
                image_path="moodboard.png",
            ),
        ],
    )
    latest = ShotReference(
        reference_id="character|Hero|front_full",
        source="asset",
        asset_type="character",
        asset_id="Hero",
        view_type="front_full",
        asset_version_id="hero-v2",
        image_path="hero-v2.png",
    )
    monkeypatch.setattr(api_context, "_default_shot_references", lambda *_: [latest])

    references = _reconcile_shot_references(state, 1)

    assert references[0].asset_version_id == "hero-v2"
    assert references[0].image_path == "hero-v2.png"
    assert references[1].source == "manual"
    assert references[1].image_path == "moodboard.png"


def test_production_payload_refreshes_asset_references_and_preserves_manual_entries(monkeypatch):
    state = _state()
    state.shot_productions[1] = ShotProductionState(
        scene_id=1,
        references=[
            ShotReference(
                reference_id="character|Hero|front_full",
                source="asset",
                asset_type="character",
                asset_id="Hero",
                view_type="front_full",
                asset_version_id="hero-v1",
                image_path="hero-v1.png",
            ),
            ShotReference(
                reference_id="manual|board|1",
                source="manual",
                asset_type="manual",
                asset_id="board",
                view_type="image",
                image_path="moodboard.png",
            ),
        ],
    )
    latest = ShotReference(
        reference_id="character|Hero|front_full",
        source="asset",
        asset_type="character",
        asset_id="Hero",
        view_type="front_full",
        asset_version_id="hero-v2",
        image_path="hero-v2.png",
    )
    monkeypatch.setattr(api_context, "_default_shot_references", lambda *_: [latest])

    payload = api_context._production_payload(state)

    references = payload["productions"][0]["production"]["references"]
    assert [(item["source"], item["image_path"]) for item in references] == [
        ("asset", "hero-v2.png"),
        ("manual", "moodboard.png"),
    ]


def test_unreviewed_image_stays_in_catalog_but_is_not_auto_bound(monkeypatch):
    monkeypatch.setattr(Path, "is_file", lambda _path: True)
    state = _state()
    state.character_images = CharacterImageAssets(characters=[
        CharacterImage(
            character_name="Hero",
            view_type="front_full",
            image_prompt="Hero full body",
            image_path="hero.png",
            seed=1,
            quality_report={"status": "unreviewed"},
        )
    ])
    monkeypatch.setattr(
        api_context.video_gen_agent,
        "_build_reference_identity_bundle",
        lambda *_args: {1: [("character", "Hero", "front_full")]},
    )

    catalog = api_context._reference_catalog(state)

    assert "character|Hero|front_full" in catalog["by_id"]
    assert api_context._default_shot_references(state, 1) == []


def test_passed_image_can_be_auto_bound(monkeypatch):
    monkeypatch.setattr(Path, "is_file", lambda _path: True)
    state = _state()
    state.character_images = CharacterImageAssets(characters=[
        CharacterImage(
            character_name="Hero",
            view_type="front_full",
            image_prompt="Hero full body",
            image_path="hero.png",
            seed=1,
            quality_report={"status": "passed"},
        )
    ])
    monkeypatch.setattr(
        api_context.video_gen_agent,
        "_build_reference_identity_bundle",
        lambda *_args: {1: [("character", "Hero", "front_full")]},
    )

    references = api_context._default_shot_references(state, 1)

    assert [item.reference_id for item in references] == ["character|Hero|front_full"]


def test_mock_unreviewed_image_can_opt_in_to_auto_binding(monkeypatch):
    monkeypatch.setattr(Path, "is_file", lambda _path: True)
    state = _state()
    state.character_images = CharacterImageAssets(characters=[
        CharacterImage(
            character_name="Hero",
            view_type="front_full",
            image_prompt="Hero full body",
            image_path="hero.png",
            seed=1,
            api_provider="mock",
            quality_report={
                "status": "unreviewed",
                "automatic_reference_allowed": True,
            },
        )
    ])
    monkeypatch.setattr(
        api_context.video_gen_agent,
        "_build_reference_identity_bundle",
        lambda *_args: {1: [("character", "Hero", "front_full")]},
    )

    references = api_context._default_shot_references(state, 1)

    assert [item.reference_id for item in references] == ["character|Hero|front_full"]


def test_production_payload_uses_selected_shot_provider_for_prompt_preview(monkeypatch):
    state = _state()
    state.video_generation_policy = {
        "provider": "seedance",
        "model": "doubao-seedance-2-0-fast-260128",
        "resolution": "1080p",
    }
    monkeypatch.setattr(
        api_context,
        "video_gen_agent",
        SimpleNamespace(
            api_client=SimpleNamespace(provider_name="mock"),
            _build_reference_identity_bundle=lambda *_args: {},
        ),
    )
    captured = {}

    def capture_prompt(*args, **kwargs):
        captured.update(kwargs)
        return {"provider": args[0]}

    monkeypatch.setattr(api_context, "compose_video_prompt", capture_prompt)

    payload = api_context._production_payload(state)

    assert payload["productions"][0]["effective_prompt"]["provider"] == "seedance"
    assert captured["creative_profile"] is state.creative_profile


def test_production_payload_exposes_normalized_default_video_policy_for_legacy_state(monkeypatch):
    state = _state()
    state.video_generation_policy = {}
    monkeypatch.setattr(
        config,
        "DEFAULT_API",
        "seedance",
        raising=False,
    )
    monkeypatch.setattr(
        config,
        "ARK_MODEL",
        "doubao-seedance-2-0-fast-260128",
        raising=False,
    )

    payload = api_context._production_payload(state)

    policy = payload["video_generation_policy"]
    assert policy["provider"] == "seedance"
    assert policy["model"] == "doubao-seedance-2-0-fast-260128"
    assert policy["resolution"] == "1080p"
    assert "capability" not in policy


def test_unresolved_legacy_reference_is_preserved_as_manual():
    state = _state()
    state.shot_productions[1] = ShotProductionState(
        scene_id=1,
        references=[
            ShotReference(
                reference_id="scene|old|wide",
                asset_type="scene",
                asset_id="old",
                view_type="wide",
                image_path="old-image.png",
            )
        ],
    )
    payload = json.loads(_state_to_json(state))

    restored = _state_from_json(json.dumps(payload))

    assert restored.shot_productions[1].references[0].source == "manual"
    assert restored.shot_productions[1].references[0].image_path == "old-image.png"


def test_generation_snapshot_freezes_current_scene_prompt_and_reference(tmp_path, monkeypatch):
    state = _state()
    image_path = tmp_path / "hero.png"
    image_path.write_bytes(b"image")
    reference = ShotReference(
        reference_id="character|Hero|front_full",
        source="asset",
        asset_type="character",
        asset_id="Hero",
        view_type="front_full",
        asset_version_id="hero-v2",
        image_path=str(image_path),
        label="Hero front_full",
    )
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: [reference])

    snapshot = _build_video_generation_snapshot(
        state,
        1,
        {"provider": "mock", "model": "mock-video", "resolution": "720p"},
    )
    state.generation_prompts.shots[0].prompt = "later draft"

    assert snapshot["scene_id"] == 1
    assert snapshot["raw_prompt"] == "Hero waits."
    assert "Hero waits." in snapshot["provider_prompt"]
    assert "参考图1" in snapshot["provider_prompt"]
    expected_reference = reference.model_dump()
    expected_reference["reference_tag"] = "@ref01_character_front_full"
    assert snapshot["references"] == [expected_reference]


def test_generation_snapshot_places_continuity_tail_frame_before_asset_references(tmp_path, monkeypatch):
    state = _state()
    state.shot_productions[1] = ShotProductionState(scene_id=1, continuity_enabled=True)
    asset_path = tmp_path / "hero.png"
    tail_path = tmp_path / "tail.png"
    asset_path.write_bytes(b"image")
    tail_path.write_bytes(b"image")
    asset_reference = ShotReference(
        reference_id="character|Hero|front_full",
        source="asset",
        asset_type="character",
        asset_id="Hero",
        view_type="front_full",
        image_path=str(asset_path),
    )
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: [asset_reference])
    monkeypatch.setattr(
        "src.services.video_continuity.continuity_reference",
        lambda *_args: (str(tail_path), "前一镜尾帧"),
    )

    snapshot = _build_video_generation_snapshot(
        state,
        1,
        {"provider": "mock", "model": "mock", "resolution": "720p", "capability": {"continuity": True}},
    )

    assert [item["image_path"] for item in snapshot["references"]] == [
        str(tail_path),
        str(asset_path),
    ]
    assert snapshot["references"][0]["source"] == "continuity"


def test_generation_snapshot_records_only_the_first_reference_for_single_reference_models(tmp_path, monkeypatch):
    state = _state()
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"image")
    second.write_bytes(b"image")
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: [
        ShotReference(reference_id="scene|room|wide", asset_type="scene", asset_id="room", view_type="wide", image_path=str(first)),
        ShotReference(reference_id="character|Hero|front_full", asset_type="character", asset_id="Hero", view_type="front_full", image_path=str(second)),
    ])

    snapshot = _build_video_generation_snapshot(
        state,
        1,
        {
            "provider": "mock",
            "model": "mock",
            "resolution": "720p",
            "capability": {"image_to_video": True, "multi_reference": False},
        },
    )

    assert [item["image_path"] for item in snapshot["references"]] == [str(first)]


def test_multi_reference_prompt_emphasizes_continuity_and_view_consistency():
    prompt = ShotPrompt(scene_id=1, prompt="Hero turns toward the door.", duration_sec=5)
    rendered = VideoGenerationAgent()._build_multi_reference_prompt(
        prompt,
        ["tail.jpg", "hero-front.png", "hero-back.png"],
        reference_labels=[
            "continuity: previous frame",
            "character: Hero front_half",
            "character: Hero back_full",
        ],
    )

    assert "[Reference hierarchy]" in rendered
    assert "Use character references only to lock identity" in rendered
    assert "[Single-take rule]" in rendered
    assert "no shot-reverse-shot" in rendered
    assert "上一镜尾帧" in rendered
    assert "正面、侧面和背面" in rendered
    assert "人物转身" in rendered


def test_multi_reference_prompt_respects_explicit_timed_edit_plan():
    prompt = ShotPrompt(
        scene_id=1,
        prompt="2s close-up on Gu Heng, 1.5s fast pan as Su Mian turns back, 3s fixed two-shot confrontation.",
        duration_sec=7,
    )
    rendered = VideoGenerationAgent()._build_multi_reference_prompt(
        prompt,
        ["tail.jpg", "hero-front.png"],
        reference_labels=[
            "continuity: previous frame",
            "character: Hero front_half",
        ],
    )

    assert "[Planned edit rule]" in rendered
    assert "Cuts are allowed only where the prompt explicitly specifies" in rendered
    assert "[Single-take rule]" not in rendered


def test_reference_identity_bundle_preserves_assets_that_share_a_path(tmp_path):
    shared_path = tmp_path / "shared.png"
    shared_path.write_bytes(b"png")
    state = _state()
    state.scene_images = SceneImageAssets(scenes=[
        SceneImage(
            scene_id=1,
            location_id="loc_room",
            location="room",
            view_type="wide",
            image_prompt="room",
            image_path=str(shared_path),
            seed=1,
            version_id="scene-v2",
        )
    ])
    state.character_images = CharacterImageAssets(characters=[
        CharacterImage(
            character_name="Hero",
            view_type="front_full",
            image_prompt="hero",
            image_path=str(shared_path),
            seed=1,
            version_id="hero-v2",
        )
    ])

    identities = VideoGenerationAgent()._build_reference_identity_bundle(
        state.generation_prompts,
        state.character_images,
        state.scene_images,
        state.script,
        state.prop_images,
    )

    assert identities[1] == [
        ("scene", "loc_room", "wide"),
        ("character", "Hero", "front_full"),
    ]


def test_reference_bundle_includes_multiple_character_views_in_priority_order(tmp_path):
    state = _state()
    state.character_images = CharacterImageAssets(characters=[
        CharacterImage(
            character_name="Hero",
            view_type="front_half",
            image_prompt="hero",
            image_path=str(tmp_path / "front_half.png"),
            seed=1,
            version_id="v1",
        ),
        CharacterImage(
            character_name="Hero",
            view_type="front_full",
            image_prompt="hero",
            image_path=str(tmp_path / "front_full.png"),
            seed=2,
            version_id="v2",
        ),
        CharacterImage(
            character_name="Hero",
            view_type="left_profile",
            image_prompt="hero",
            image_path=str(tmp_path / "left_profile.png"),
            seed=3,
            version_id="v3",
        ),
        CharacterImage(
            character_name="Hero",
            view_type="back_full",
            image_prompt="hero",
            image_path=str(tmp_path / "back_full.png"),
            seed=4,
            version_id="v4",
        ),
    ])
    for image in state.character_images.characters:
        Path(image.image_path).write_bytes(b"png")

    refs = VideoGenerationAgent()._build_reference_bundle(
        state.generation_prompts,
        state.character_images,
        state.scene_images,
        state.script,
        state.prop_images,
    )
    identities = VideoGenerationAgent()._build_reference_identity_bundle(
        state.generation_prompts,
        state.character_images,
        state.scene_images,
        state.script,
        state.prop_images,
    )

    assert refs[1] == [
        str(tmp_path / "front_half.png"),
        str(tmp_path / "front_full.png"),
        str(tmp_path / "left_profile.png"),
        str(tmp_path / "back_full.png"),
    ]
    assert identities[1] == [
        ("character", "Hero", "front_half"),
        ("character", "Hero", "front_full"),
        ("character", "Hero", "left_profile"),
        ("character", "Hero", "back_full"),
    ]


def test_default_references_auto_select_wan_scene_group_view(tmp_path):
    scene_center = tmp_path / "scene_center.png"
    legacy_wide = tmp_path / "wide.png"
    scene_center.write_bytes(b"png")
    legacy_wide.write_bytes(b"png")
    state = _state()
    state.scene_images = SceneImageAssets(scenes=[
        SceneImage(
            scene_id=1,
            location_id="loc_room",
            location="room",
            view_type="wide",
            image_prompt="room",
            image_path=str(legacy_wide),
            seed=1,
        ),
        SceneImage(
            scene_id=1,
            location_id="loc_room",
            location="room",
            view_type="scene_center",
            image_prompt="room group",
            image_path=str(scene_center),
            seed=2,
            generation_mode="reference_group",
            group_id="group-1",
            group_view_count=4,
        ),
    ])

    references = api_context._default_shot_references(state, 1)
    refs = VideoGenerationAgent()._build_reference_bundle(
        state.generation_prompts,
        state.character_images,
        state.scene_images,
        state.script,
        state.prop_images,
    )

    assert references[0].reference_id == "scene|loc_room|scene_center"
    assert references[0].image_path == str(scene_center)
    assert refs[1] == [str(scene_center)]


def test_state_aware_scene_selection_prefers_matching_location_state(tmp_path):
    day = tmp_path / "day.png"
    rain = tmp_path / "rain.png"
    day.write_bytes(b"png")
    rain.write_bytes(b"png")
    state = _state()
    state.script.asset_manifest = VisualAssetManifest(
        locations=[
            LocationAsset(
                location_id="loc_room",
                canonical_name="room",
                representative_scene_id=1,
                shot_ids=[1, 2],
                default_state_id="state_day",
                states=[
                    LocationState(state_id="state_day", label="day", shot_ids=[1], representative_scene_id=1),
                    LocationState(state_id="state_rain", label="rain", shot_ids=[2], representative_scene_id=2),
                ],
            )
        ],
        shot_bindings=[
            ShotVisualBinding(scene_id=1, location_id="loc_room", state_id="state_day"),
            ShotVisualBinding(scene_id=2, location_id="loc_room", state_id="state_rain"),
        ],
    )
    state.generation_prompts = GenerationPrompts(
        api_provider="mock",
        shots=[
            ShotPrompt(scene_id=1, prompt="day shot", duration_sec=5),
            ShotPrompt(scene_id=2, prompt="rain shot", duration_sec=5),
        ],
    )
    state.scene_images = SceneImageAssets(scenes=[
        SceneImage(
            scene_id=1,
            location_id="loc_room",
            state_id="state_day",
            location="room",
            view_type="scene_center",
            image_prompt="day",
            image_path=str(day),
            seed=1,
        ),
        SceneImage(
            scene_id=2,
            location_id="loc_room",
            state_id="state_rain",
            location="room",
            view_type="scene_left_45",
            image_prompt="rain",
            image_path=str(rain),
            seed=2,
        ),
    ])

    refs = VideoGenerationAgent()._build_reference_bundle(
        state.generation_prompts,
        state.character_images,
        state.scene_images,
        state.script,
        state.prop_images,
    )
    identities = VideoGenerationAgent()._build_reference_identity_bundle(
        state.generation_prompts,
        state.character_images,
        state.scene_images,
        state.script,
        state.prop_images,
    )

    assert refs[1] == [str(day)]
    assert refs[2] == [str(rain)]
    assert identities[1][0] == ("scene", "loc_room", "state_day", "scene_center")
    assert identities[2][0] == ("scene", "loc_room", "state_rain", "scene_left_45")


def test_state_aware_scene_selection_never_uses_other_state(tmp_path):
    rain = tmp_path / "rain.png"
    rain.write_bytes(b"png")
    state = _state()
    state.script.asset_manifest = VisualAssetManifest(
        locations=[
            LocationAsset(
                location_id="loc_room",
                canonical_name="room",
                representative_scene_id=1,
                shot_ids=[1, 2],
                default_state_id="state_day",
                states=[
                    LocationState(state_id="state_day", label="day", shot_ids=[1], representative_scene_id=1),
                    LocationState(state_id="state_rain", label="rain", shot_ids=[2], representative_scene_id=2),
                ],
            )
        ],
        shot_bindings=[
            ShotVisualBinding(scene_id=1, location_id="loc_room", state_id="state_day"),
            ShotVisualBinding(scene_id=2, location_id="loc_room", state_id="state_rain"),
        ],
    )
    state.generation_prompts = GenerationPrompts(
        api_provider="mock",
        shots=[ShotPrompt(scene_id=1, prompt="day shot", duration_sec=5)],
    )
    state.scene_images = SceneImageAssets(scenes=[
        SceneImage(
            scene_id=2,
            location_id="loc_room",
            state_id="state_rain",
            location="room",
            view_type="scene_center",
            image_prompt="rain",
            image_path=str(rain),
            seed=2,
        ),
    ])

    refs = VideoGenerationAgent()._build_reference_bundle(
        state.generation_prompts,
        None,
        state.scene_images,
        state.script,
        None,
    )
    identities = VideoGenerationAgent()._build_reference_identity_bundle(
        state.generation_prompts,
        None,
        state.scene_images,
        state.script,
        None,
    )

    assert refs[1] == []
    assert identities[1] == []


def test_scene_center_reference_must_be_confirmed(tmp_path):
    pending_center = tmp_path / "pending_center.png"
    pending_center.write_bytes(b"png")
    state = _state()
    state.script.asset_manifest = VisualAssetManifest(
        locations=[
            LocationAsset(
                location_id="loc_room",
                canonical_name="room",
                representative_scene_id=1,
                shot_ids=[1],
                default_state_id="state_day",
                states=[LocationState(state_id="state_day", label="day", shot_ids=[1], representative_scene_id=1)],
            )
        ],
        shot_bindings=[ShotVisualBinding(scene_id=1, location_id="loc_room", state_id="state_day")],
    )
    state.scene_images = SceneImageAssets(scenes=[
        SceneImage(
            scene_id=1,
            location_id="loc_room",
            state_id="state_day",
            location="room",
            view_type="scene_center",
            image_prompt="pending",
            image_path=str(pending_center),
            seed=1,
            is_master=True,
            needs_confirmation=True,
        ),
    ])

    refs = VideoGenerationAgent()._build_reference_bundle(
        state.generation_prompts,
        None,
        state.scene_images,
        state.script,
        None,
    )

    assert refs[1] == []


def test_video_reference_bundle_ignores_prop_contact_sheet(tmp_path):
    sheet = tmp_path / "standard_views.png"
    front = tmp_path / "front.png"
    sheet.write_bytes(b"png")
    front.write_bytes(b"png")
    state = _state()
    state.script.asset_manifest = VisualAssetManifest(
        props=[PropAsset(prop_id="key", name="key", description="test prop", critical_reason="plot", critical_evidence="appears twice", shot_ids=[1, 2])],
        shot_bindings=[ShotVisualBinding(scene_id=1, location_id="loc_room", prop_ids=["key"])],
    )
    state.prop_images = PropImageAssets(props=[
        PropImage(
            prop_id="key",
            prop_name="key",
            view_type="standard_views",
            image_prompt="sheet",
            image_path=str(sheet),
            seed=1,
        ),
        PropImage(
            prop_id="key",
            prop_name="key",
            view_type="front",
            image_prompt="front",
            image_path=str(front),
            seed=2,
            is_master=True,
            needs_confirmation=False,
        ),
    ])
    refs = VideoGenerationAgent()._build_reference_bundle(
        state.generation_prompts,
        state.character_images,
        state.scene_images,
        state.script,
        state.prop_images,
    )
    identities = VideoGenerationAgent()._build_reference_identity_bundle(
        state.generation_prompts,
        state.character_images,
        state.scene_images,
        state.script,
        state.prop_images,
    )

    assert refs[1] == [str(front)]
    assert identities[1] == [("prop", "key", "front")]


def test_video_prop_reference_prefers_requested_view_and_falls_back_to_confirmed_master(tmp_path):
    front = tmp_path / "front.png"
    back = tmp_path / "back.png"
    stale_back = tmp_path / "stale_back.png"
    front.write_bytes(b"png")
    back.write_bytes(b"png")
    stale_back.write_bytes(b"png")
    state = _state()
    state.script.scenes[0].action_description = "The hand flips the key to show the back engraving."
    state.script.asset_manifest = VisualAssetManifest(
        props=[PropAsset(prop_id="key", name="key", description="test prop", critical_reason="plot", critical_evidence="appears twice", shot_ids=[1, 2])],
        shot_bindings=[ShotVisualBinding(scene_id=1, location_id="loc_room", prop_ids=["key"])],
    )
    state.prop_images = PropImageAssets(props=[
        PropImage(
            prop_id="key", prop_name="key", view_type="front", image_prompt="front",
            image_path=str(front), seed=1, is_master=True, needs_confirmation=False,
        ),
        PropImage(
            prop_id="key", prop_name="key", view_type="back", image_prompt="back",
            image_path=str(back), seed=2,
        ),
    ])

    refs = VideoGenerationAgent()._build_reference_bundle(
        state.generation_prompts, None, None, state.script, state.prop_images,
    )
    identities = VideoGenerationAgent()._build_reference_identity_bundle(
        state.generation_prompts, None, None, state.script, state.prop_images,
    )
    state.prop_images.props[1].image_path = str(stale_back)
    state.prop_images.props[1].is_stale = True
    fallback_refs = VideoGenerationAgent()._build_reference_bundle(
        state.generation_prompts, None, None, state.script, state.prop_images,
    )

    assert refs[1] == [str(back)]
    assert identities[1] == [("prop", "key", "back")]
    assert fallback_refs[1] == [str(front)]


def test_video_prop_reference_skips_unconfirmed_front_master(tmp_path):
    front = tmp_path / "front.png"
    front.write_bytes(b"png")
    state = _state()
    state.script.asset_manifest = VisualAssetManifest(
        props=[PropAsset(prop_id="key", name="key", description="test prop", critical_reason="plot", critical_evidence="appears twice", shot_ids=[1, 2])],
        shot_bindings=[ShotVisualBinding(scene_id=1, location_id="loc_room", prop_ids=["key"])],
    )
    state.prop_images = PropImageAssets(props=[
        PropImage(
            prop_id="key", prop_name="key", view_type="front", image_prompt="front",
            image_path=str(front), seed=1, is_master=True, needs_confirmation=True,
        ),
    ])

    refs = VideoGenerationAgent()._build_reference_bundle(
        state.generation_prompts, None, None, state.script, state.prop_images,
    )

    assert refs[1] == []


def test_reference_catalog_lists_only_video_ready_prop_references(tmp_path):
    sheet = tmp_path / "standard_views.png"
    pending = tmp_path / "pending_front.png"
    confirmed = tmp_path / "confirmed_front.png"
    sheet.write_bytes(b"png")
    pending.write_bytes(b"png")
    confirmed.write_bytes(b"png")
    state = _state()
    state.prop_images = PropImageAssets(props=[
        PropImage(prop_id="key", prop_name="key", view_type="standard_views", image_prompt="sheet", image_path=str(sheet), seed=1),
        PropImage(prop_id="key", prop_name="key", view_type="front", image_prompt="pending", image_path=str(pending), seed=2, is_master=True, needs_confirmation=True),
        PropImage(prop_id="key", prop_name="key", view_type="front", image_prompt="confirmed", image_path=str(confirmed), seed=3, is_master=True, needs_confirmation=False),
    ])

    catalog = _reference_catalog(state)

    assert set(catalog["by_id"]) == {"prop|key|front"}
    assert catalog["by_id"]["prop|key|front"].image_path == str(confirmed)
    assert [item["image_path"] for item in catalog["props"]] == [str(confirmed)]


def test_single_shot_generation_requires_at_least_one_saved_reference_image():
    state = _state()
    with pytest.raises(HTTPException, match="参考图"):
        _schedule_shot_video_job(state, 1)
