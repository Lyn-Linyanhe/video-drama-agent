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
    _production_payload,
    ProductionReferenceRequest,
    ProductionShotUpdateRequest,
    _reconcile_shot_references,
    _reference_catalog,
    _state_from_json,
    _state_to_json,
    _sync_active_shot_asset,
)
from src.api.routes.production import _schedule_shot_video_job
from src.api.routes import production as production_routes
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
        generation_status_by_shot_id={"scene-1-shot-1": "failed"},
        generation_error_by_shot_id={"scene-1-shot-1": "persisted child failure"},
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


def test_production_reference_request_accepts_explicit_mention_token():
    reference = ProductionReferenceRequest(
        reference_id="character|Hero|front_full",
        source="explicit",
        token="@Hero-front-full",
        version_id="hero-v2",
    )

    assert reference.source == "explicit"
    assert reference.token == "@Hero-front-full"
    assert reference.version_id == "hero-v2"


def test_reference_label_parser_preserves_hyphenated_and_chinese_tokens():
    assert VideoGenerationAgent._reference_tag_from_label(
        "@Hero-front-full -> character: Hero (front_full)"
    ) == "@Hero-front-full"
    assert VideoGenerationAgent._reference_tag_from_label(
        "@角色:正面 -> character: 角色 (front)"
    ) == "@角色:正面"
    assert VideoGenerationAgent._reference_tag_from_label("character: Hero") == ""


def test_explicit_reference_token_must_exist_in_prompt():
    with pytest.raises(HTTPException, match="必须出现在分镜提示词"):
        production_routes._validate_explicit_reference_token(
            "镜头中没有资产标记。",
            "@Hero-front-full",
        )


def test_prompt_revision_protects_and_restores_explicit_mentions():
    references = [
        ShotReference(
            reference_id="character|Hero|front_full",
            source="explicit",
            reference_token="@Hero-front-full",
            asset_type="character",
            asset_id="Hero",
            view_type="front_full",
            image_path="hero.png",
        )
    ]

    protected, placeholders = production_routes._protect_explicit_mentions(
        "镜头保持 @Hero-front-full 的外貌。",
        references,
    )
    restored = production_routes._restore_explicit_mentions(
        protected.replace("MENTION_1", "MENTION_1"),
        placeholders,
    )

    assert "@Hero-front-full" not in protected
    assert "@Hero-front-full" in restored


def test_prompt_revision_rejects_output_that_drops_explicit_mention():
    references = [
        ShotReference(
            reference_id="character|Hero|front_full",
            source="explicit",
            reference_token="@Hero-front-full",
            asset_type="character",
            asset_id="Hero",
            view_type="front_full",
            image_path="hero.png",
        )
    ]

    protected, placeholders = production_routes._protect_explicit_mentions(
        "@Hero-front-full 等待。",
        references,
    )

    with pytest.raises(ValueError, match="显式引用"):
        production_routes._restore_explicit_mentions("只剩普通文本", placeholders)


def test_prompt_revision_accepts_llm_output_that_keeps_literal_token():
    _, placeholders = production_routes._protect_explicit_mentions(
        "@Hero-front-full 等待。",
        [ShotReference(reference_id="x", source="explicit", reference_token="@Hero-front-full", asset_type="character", asset_id="Hero", view_type="front_full", image_path="hero.png")],
    )

    restored = production_routes._restore_explicit_mentions("@Hero-front-full 继续等待。", placeholders)

    assert restored == "@Hero-front-full 继续等待。"


def test_explicit_reference_token_is_persisted_on_shot_reference():
    reference = ShotReference(
        reference_id="character|Hero|front_full",
        source="explicit",
        reference_token="@Hero-front-full",
        explicit_base_source="manual",
        asset_type="character",
        asset_id="Hero",
        view_type="front_full",
        image_path="hero.png",
    )

    restored = ShotReference.model_validate(reference.model_dump())

    assert restored.reference_token == "@Hero-front-full"
    assert restored.explicit_base_source == "manual"


def test_explicit_reference_survives_reconcile_and_state_round_trip(tmp_path, monkeypatch):
    image_path = tmp_path / "hero-v2.png"
    image_path.write_bytes(b"image")
    state = _state()
    explicit = ShotReference(
        reference_id="character|Hero|front_full",
        source="explicit",
        reference_token="@Hero-front-full",
        asset_type="character",
        asset_id="Hero",
        view_type="front_full",
        asset_version_id="hero-v2",
        image_path=str(image_path),
    )
    state.shot_productions[1] = ShotProductionState(scene_id=1, references=[explicit])

    monkeypatch.setattr(api_context, "_default_shot_references", lambda *_: [])

    reconciled = _reconcile_shot_references(state, 1)
    restored = _state_from_json(_state_to_json(state))

    assert reconciled[0].source == "explicit"
    assert reconciled[0].reference_token == "@Hero-front-full"
    assert reconciled[0].asset_version_id == "hero-v2"
    assert restored.shot_productions[1].references[0].source == "explicit"


def test_generation_snapshot_rejects_missing_explicit_reference(tmp_path, monkeypatch):
    state = _state()
    explicit = ShotReference(
        reference_id="character|Hero|front_full",
        source="explicit",
        reference_token="@Hero-front-full",
        asset_type="character",
        asset_id="Hero",
        view_type="front_full",
        asset_version_id="hero-deleted",
        image_path=str(tmp_path / "deleted.png"),
    )
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: [explicit])

    with pytest.raises(ValueError, match="显式引用.*不可用"):
        _build_video_generation_snapshot(
            state,
            1,
            {"provider": "mock", "model": "mock-video", "resolution": "720p"},
        )


def test_generation_snapshot_rejects_dropped_explicit_reference(tmp_path, monkeypatch):
    state = _state()
    first = tmp_path / "scene.png"
    second = tmp_path / "hero.png"
    first.write_bytes(b"image")
    second.write_bytes(b"image")
    references = [
        ShotReference(
            reference_id="scene|room|wide",
            source="asset",
            asset_type="scene",
            asset_id="room",
            view_type="wide",
            image_path=str(first),
        ),
        ShotReference(
            reference_id="character|Hero|front_full",
            source="explicit",
            reference_token="@Hero-front-full",
            asset_type="character",
            asset_id="Hero",
            view_type="front_full",
            image_path=str(second),
        ),
    ]
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: references)

    snapshot = _build_video_generation_snapshot(
        state,
        1,
        {
            "provider": "mock",
            "model": "mock-single-reference",
            "resolution": "720p",
            "capability": {"image_to_video": True, "multi_reference": False, "max_reference_images": 1},
        },
    )

    assert [item["image_path"] for item in snapshot["references"]] == [str(second)]
    assert snapshot["references"][0]["source"] == "explicit"


def test_generation_snapshot_rejects_explicit_reference_for_text_only_model(tmp_path, monkeypatch):
    state = _state()
    image_path = tmp_path / "hero.png"
    image_path.write_bytes(b"image")
    reference = ShotReference(
        reference_id="character|Hero|front_full",
        source="explicit",
        reference_token="@Hero-front-full",
        asset_type="character",
        asset_id="Hero",
        view_type="front_full",
        image_path=str(image_path),
    )
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: [reference])

    with pytest.raises(ValueError, match="显式引用.*不支持参考图"):
        _build_video_generation_snapshot(
            state,
            1,
            {
                "provider": "mock",
                "model": "mock-text-only",
                "resolution": "720p",
                "capability": {"image_to_video": False, "multi_reference": False, "max_reference_images": 0},
            },
        )


def test_generation_snapshot_rejects_explicit_reference_missing_from_seedance_plan(tmp_path, monkeypatch):
    state = _state()
    image_path = tmp_path / "hero.png"
    image_path.write_bytes(b"image")
    reference = ShotReference(
        reference_id="character|Hero|front_full",
        source="explicit",
        reference_token="@Hero-front-full",
        asset_type="character",
        asset_id="Hero",
        view_type="front_full",
        image_path=str(image_path),
    )
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: [reference])
    policy = {
        "provider": "seedance",
        "model": "doubao-seedance-2-0-fast-260128",
        "resolution": "720p",
        "capability": {"image_to_video": True, "multi_reference": True, "max_reference_images": 9},
    }
    fake_agent = SimpleNamespace(
        api_client=SimpleNamespace(plan_reference_inputs=lambda *_args, **_kwargs: {"items": [], "dropped_references": []}),
    )
    import src.services.video_capabilities as video_capabilities
    monkeypatch.setattr(video_capabilities, "video_agent_for_policy", lambda _policy: fake_agent)

    with pytest.raises(ValueError, match="显式引用.*未被供应商接受"):
        _build_video_generation_snapshot(state, 1, policy)


def test_generation_snapshot_rejects_explicit_reference_when_limit_is_zero(tmp_path, monkeypatch):
    state = _state()
    image_path = tmp_path / "hero.png"
    image_path.write_bytes(b"image")
    reference = ShotReference(
        reference_id="character|Hero|front_full",
        source="explicit",
        reference_token="@Hero-front-full",
        asset_type="character",
        asset_id="Hero",
        view_type="front_full",
        image_path=str(image_path),
    )
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: [reference])

    with pytest.raises(ValueError, match="显式引用.*模型限制"):
        _build_video_generation_snapshot(
            state,
            1,
            {
                "provider": "mock",
                "model": "mock-zero-reference",
                "resolution": "720p",
                "capability": {"image_to_video": True, "multi_reference": False, "max_reference_images": 0},
            },
        )


def test_generation_snapshot_prioritizes_explicit_reference_over_automatic_reference(tmp_path, monkeypatch):
    state = _state()
    automatic_path = tmp_path / "scene.png"
    explicit_path = tmp_path / "hero.png"
    automatic_path.write_bytes(b"image")
    explicit_path.write_bytes(b"image")
    references = [
        ShotReference(
            reference_id="scene|room|wide",
            source="asset",
            asset_type="scene",
            asset_id="room",
            view_type="wide",
            image_path=str(automatic_path),
        ),
        ShotReference(
            reference_id="character|Hero|front_full",
            source="explicit",
            reference_token="@Hero-front-full",
            asset_type="character",
            asset_id="Hero",
            view_type="front_full",
            image_path=str(explicit_path),
        ),
    ]
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: references)

    snapshot = _build_video_generation_snapshot(
        state,
        1,
        {
            "provider": "mock",
            "model": "mock-single-reference",
            "resolution": "720p",
            "capability": {"image_to_video": True, "multi_reference": False, "max_reference_images": 1},
        },
    )

    assert [item["image_path"] for item in snapshot["references"]] == [str(explicit_path)]
    assert snapshot["references"][0]["source"] == "explicit"


def test_explicit_reference_rebinds_to_locked_history_path(tmp_path, monkeypatch):
    state = _state()
    state.shot_productions[1] = ShotProductionState(scene_id=1, references=[
        ShotReference(
            reference_id="character|Hero|front_full",
            source="explicit",
            reference_token="@Hero-front-full",
            asset_type="character",
            asset_id="Hero",
            view_type="front_full",
            asset_version_id="hero-v1",
            image_path=str(tmp_path / "active.png"),
        )
    ])
    history_path = tmp_path / "assets" / "characters" / "Hero" / "history" / "hero-v1.png"
    history_path.parent.mkdir(parents=True)
    history_path.write_bytes(b"image")
    manifest = tmp_path / "assets" / "asset_manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"versions": {
        "character:Hero:front_full": [{"id": "hero-v1", "path": str(history_path), "active": False}]
    }}), encoding="utf-8")
    monkeypatch.setattr(api_context.project_store, "get_project_dir", lambda _project_id: tmp_path)
    monkeypatch.setattr(api_context, "_default_shot_references", lambda *_: [])

    references = _reconcile_shot_references(state, 1)

    assert references[0].image_path == str(history_path)
    assert references[0].stale is False


@pytest.mark.asyncio
async def test_single_shot_generation_converts_explicit_reference_failure_to_conflict(monkeypatch):
    state = _state()
    production = ShotProductionState(scene_id=1)
    monkeypatch.setattr(production_routes, "_load_project_state", lambda _project_id: state)
    monkeypatch.setattr(production_routes, "_shot_prompt", lambda _state, _scene_id: state.generation_prompts.shots[0])
    async def prepare(_state):
        return None
    monkeypatch.setattr(production_routes, "_prepare_auto_references", prepare)
    monkeypatch.setattr(production_routes, "_ensure_shot_production", lambda _state, _scene_id: production)
    monkeypatch.setattr(production_routes, "normalize_video_policy", lambda *_args, **_kwargs: {
        "provider": "mock", "model": "mock", "resolution": "720p", "capability": {"continuity": False},
    })
    monkeypatch.setattr(production_routes.job_store, "list_jobs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        production_routes,
        "_build_video_generation_snapshot",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("显式引用不可用：@Hero-front-full")),
    )

    with pytest.raises(HTTPException) as error:
        await production_routes.generate_project_production_shot(state.project_id, 1)

    assert error.value.status_code == 409
    assert "显式引用不可用" in str(error.value.detail)


@pytest.mark.asyncio
async def test_saving_with_a_stale_explicit_reference_does_not_report_unknown_asset(tmp_path, monkeypatch):
    """A missing locked file must remain editable so the user can remove it."""
    state = _state()
    reference = ShotReference(
        reference_id="character|Hero|front_full",
        source="explicit",
        reference_token="@Hero-front-full",
        asset_type="character",
        asset_id="Hero",
        view_type="front_full",
        image_path=str(tmp_path / "deleted.png"),
        stale=True,
    )
    production = ShotProductionState(scene_id=1, references=[reference])
    state.shot_productions[1] = production
    monkeypatch.setattr(production_routes, "_load_project_state", lambda _project_id: state)
    monkeypatch.setattr(production_routes, "_ensure_shot_production", lambda *_args: production)
    monkeypatch.setattr(
        production_routes,
        "_reference_catalog",
        lambda _state: {"by_id": {}, "characters": [], "scenes": [], "props": [], "auto_reference_ids": set()},
    )
    monkeypatch.setattr(production_routes, "_save_project_state", lambda _state: None)
    monkeypatch.setattr(production_routes, "_production_payload", lambda _state: {"ok": True})

    result = await production_routes.update_project_production_shot(
        state.project_id,
        1,
        ProductionShotUpdateRequest(
            prompt="Hero waits. @Hero-front-full",
            references=[ProductionReferenceRequest(
                reference_id=reference.reference_id,
                source="explicit",
                token=reference.reference_token,
            )],
        ),
    )

    assert result == {"ok": True}
    assert production.references[0].stale is True


@pytest.mark.asyncio
async def test_explicit_history_reference_must_stay_inside_project_asset_root(tmp_path, monkeypatch):
    state = _state()
    reference = ShotReference(
        reference_id="character|Hero|front_full",
        source="asset",
        asset_type="character",
        asset_id="Hero",
        view_type="front_full",
        image_path=str(tmp_path / "active.png"),
    )
    production = ShotProductionState(scene_id=1, references=[reference])
    state.shot_productions[1] = production
    outside_path = tmp_path / "outside.png"
    outside_path.write_bytes(b"image")
    project_dir = tmp_path / "project"
    manifest_path = project_dir / "assets" / "asset_manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text(json.dumps({"versions": {
        "character:Hero:front_full": [{"id": "hero-v1", "path": str(outside_path)}],
    }}), encoding="utf-8")
    monkeypatch.setattr(production_routes, "_load_project_state", lambda _project_id: state)
    monkeypatch.setattr(production_routes, "_ensure_shot_production", lambda *_args: production)
    monkeypatch.setattr(
        production_routes,
        "_reference_catalog",
        lambda _state: {
            "by_id": {reference.reference_id: reference},
            "characters": [], "scenes": [], "props": [], "auto_reference_ids": set(),
        },
    )
    monkeypatch.setattr(production_routes.project_store, "get_project_dir", lambda _project_id: project_dir)

    with pytest.raises(HTTPException, match="显式引用版本不可用"):
        await production_routes.update_project_production_shot(
            state.project_id,
            1,
            ProductionShotUpdateRequest(
                prompt="Hero waits. @Hero-front-full",
                references=[ProductionReferenceRequest(
                    reference_id=reference.reference_id,
                    source="explicit",
                    token="@Hero-front-full",
                    version_id="hero-v1",
                )],
            ),
        )


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


def test_multi_reference_provider_prompt_is_not_rebound_before_submission():
    prompt = ShotPrompt(scene_id=1, prompt="Hero waits.", duration_sec=5)
    agent = VideoGenerationAgent()
    labels = ["@character:Hero:front_full -> @ref01_character_front_full = character: Hero"]

    rendered = agent._build_multi_reference_prompt(
        prompt,
        ["character.png"],
        reference_labels=labels,
    )

    assert "[Reference binding]" in rendered
    assert agent._finalize_provider_prompt(
        rendered,
        prompt,
        ["character.png"],
        labels,
    ) == rendered


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
                "review_verdict": "unreviewed",
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


def test_production_payload_preserves_parent_duration_and_exposes_child_plan(monkeypatch):
    from src.schemas.shot_plan import ShotPlan, ShotUnit, SceneCapacityReport

    state = _state()
    state.script.scenes[0].duration_sec = 30
    state.script.duration_sec = 30
    state.generation_prompts.shots[0].duration_sec = 30
    state.generation_prompts.shot_plans = [ShotPlan(
        scene_id=1,
        parent_duration_sec=30,
        capacity=SceneCapacityReport(
            scene_id=1, required_total_duration_sec=30, available_duration_sec=30,
            recommended_total_duration_sec=30, recommended_durations_sec=[15, 15],
            recommended_shot_count=2, status="auto_split", warnings=["split"],
        ),
        units=[
            ShotUnit(shot_id="scene-1-shot-1", parent_scene_id=1, order=1, duration_sec=15),
            ShotUnit(shot_id="scene-1-shot-2", parent_scene_id=1, order=2, duration_sec=15),
        ],
    )]
    state.video_generation_policy = {
        "provider": "happyhorse",
        "model": "happyhorse-1.1-i2v",
        "resolution": "1080p",
    }
    monkeypatch.setattr(
        api_context,
        "video_gen_agent",
        SimpleNamespace(
            api_client=SimpleNamespace(provider_name="happyhorse"),
            _build_reference_identity_bundle=lambda *_args: {},
        ),
    )

    payload = api_context._production_payload(state)
    item = payload["productions"][0]

    assert item["prompt"]["duration_sec"] == 30
    assert item["source_duration_sec"] == 30
    assert [shot["shot_id"] for shot in item["executable_shots"]] == [
        "scene-1-shot-1", "scene-1-shot-2"
    ]
    assert item["estimated_duration_sec"] == 30
    assert item["estimated_cost_rmb"] == 0.0


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
    assert snapshot["raw_prompt"] == "@character:Hero:front_full waits."
    assert "@character:Hero:front_full waits." in snapshot["provider_prompt"]
    assert "参考图1" in snapshot["provider_prompt"]
    expected_reference = reference.model_dump()
    expected_reference["reference_tag"] = "@ref01_character_front_full"
    expected_reference["inline_token"] = "@character:Hero:front_full"
    assert snapshot["references"] == [expected_reference]


def test_production_payload_inlines_automatic_asset_mentions_into_prompt(monkeypatch):
    """The editable prompt must show where generated image assets constrain the text."""
    state = _state()
    state.generation_prompts.shots[0].prompt = "Hero opens the letter in the room."
    state.shot_productions[1] = ShotProductionState(
        scene_id=1,
        references=[
            ShotReference(
                reference_id="scene|room|wide",
                source="asset",
                asset_type="scene",
                asset_id="room",
                view_type="wide",
                image_path="room.png",
                label="room wide",
            ),
            ShotReference(
                reference_id="character|Hero|front_full",
                source="asset",
                asset_type="character",
                asset_id="Hero",
                view_type="front_full",
                image_path="hero.png",
                label="Hero front_full",
            ),
            ShotReference(
                reference_id="prop|letter|front",
                source="asset",
                asset_type="prop",
                asset_id="letter",
                view_type="front",
                image_path="letter.png",
                label="Prop letter",
            ),
        ],
    )
    monkeypatch.setattr(
        api_context,
        "_reconcile_shot_references",
        lambda state, scene_id: state.shot_productions[scene_id].references,
    )

    payload = _production_payload(state)
    prompt = payload["productions"][0]["prompt"]["prompt"]

    assert "本镜场景设定在：@scene:room:wide" in prompt
    assert "@character:Hero:front_full opens" in prompt
    assert "@prop:letter:front" in prompt


def test_generation_snapshot_deduplicates_one_asset_mentioned_multiple_times(tmp_path, monkeypatch):
    """Repeated inline mentions of one image occupy one provider reference slot."""
    state = _state()
    image_path = tmp_path / "hero.png"
    image_path.write_bytes(b"image")
    state.generation_prompts.shots[0].prompt = "Hero turns. Hero speaks."
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
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: [reference, reference.model_copy(deep=True)])

    snapshot = _build_video_generation_snapshot(
        state,
        1,
        {
            "provider": "mock",
            "model": "mock-multi-reference",
            "resolution": "720p",
            "capability": {"image_to_video": True, "multi_reference": True, "max_reference_images": 9},
        },
    )

    assert snapshot["raw_prompt"].count("@character:Hero:front_full") == 2
    assert [item["image_path"] for item in snapshot["references"]] == [str(image_path)]
    assert len(snapshot["accepted_references"]) == 1


def test_generation_snapshot_uses_one_primary_character_view_for_inline_mentions(tmp_path, monkeypatch):
    """Multiple identity views stay available to the provider without nesting @ tokens."""
    state = _state()
    state.generation_prompts.shots[0].prompt = "苏眠站在屋檐下。苏眠抬头看向远方。"
    references = []
    for view_type in ("back_full", "left_profile", "front_full", "front_half"):
        image_path = tmp_path / f"sumian-{view_type}.png"
        image_path.write_bytes(b"image")
        references.append(ShotReference(
            reference_id=f"character|苏眠|{view_type}",
            source="asset",
            asset_type="character",
            asset_id="苏眠",
            view_type=view_type,
            asset_version_id=f"sumian-{view_type}-v1",
            image_path=str(image_path),
            label=f"苏眠 {view_type}",
        ))
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: references)

    snapshot = _build_video_generation_snapshot(
        state,
        1,
        {
            "provider": "mock",
            "model": "mock-multi-reference",
            "resolution": "720p",
            "capability": {"image_to_video": True, "multi_reference": True, "max_reference_images": 9},
        },
    )

    assert snapshot["raw_prompt"].count("@character:苏眠:front_half") == 2
    assert "@character:@character" not in snapshot["raw_prompt"]
    assert "@character:苏眠:back_full" not in snapshot["raw_prompt"]
    assert len(snapshot["references"]) == 1
    assert snapshot["references"][0]["view_type"] == "front_half"


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
    assert "【单镜头连续拍摄规则】" in rendered
    assert "正反打" in rendered
    assert "上一镜尾帧" in rendered
    assert "正面、侧面和背面" in rendered
    # The old free-text 「人物转身…要和参考图一致」 sentence was replaced by an
    # explicit first-frame lock: the tail frame must be the first frame, and
    # scene references are not allowed to override its camera.
    assert "必须作为本镜第一帧" in rendered
    assert "场景参考图只提供空间陈设，不得覆盖尾帧机位" in rendered


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

    assert "【计划剪辑规则】" in rendered
    assert "只允许在提示词明确写出时长" in rendered
    assert "【单镜头连续拍摄规则】" not in rendered


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

    assert refs[1] == [str(tmp_path / "front_half.png")]
    assert identities[1] == [("character", "Hero", "front_half")]


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


@pytest.mark.asyncio
async def test_refresh_native_asr_uses_active_child_version(monkeypatch):
    from src.schemas.assets import GeneratedAssets, ShotAsset
    from src.schemas.audio import AudioAssets

    state = _state()
    state.generated_assets = GeneratedAssets(shots=[ShotAsset(
        scene_id=1,
        shot_id="scene-1-shot-1",
        local_path="new.mp4",
        native_audio_enabled=True,
        success=True,
    )])
    state.audio_assets = AudioAssets(scenes=[])
    state.shot_productions[1] = ShotProductionState(
        scene_id=1,
        active_version_id="old",
        active_version_by_shot_id={"scene-1-shot-1": "new"},
        video_versions=[
            ShotVideoVersion(scene_id=1, shot_id="scene-1-shot-1", local_path="old.mp4", native_audio_enabled=True, success=True, version_id="old", created_at="", generation_snapshot={"dialogue_plan": ["old"]}),
            ShotVideoVersion(scene_id=1, shot_id="scene-1-shot-1", local_path="new.mp4", native_audio_enabled=True, success=True, version_id="new", created_at="", generation_snapshot={"dialogue_plan": ["new"]}),
        ],
    )
    called = []
    from src.api.routes import jobs as jobs_routes
    monkeypatch.setattr(jobs_routes, "align_native_subtitles", lambda path, plan, enabled, model_name: (called.append((path, plan)) or {"status": "disabled", "provider": "x", "model": "y", "segments": [], "changed_lines": [], "error": ""}))

    await jobs_routes._refresh_native_asr(state)

    assert called == [("new.mp4", ["new"])]


def test_legacy_scene_generation_entry_rejects_planned_child_shots(monkeypatch):
    state = _state()
    from src.schemas.shot_plan import ShotPlan, ShotUnit, SceneCapacityReport
    state.generation_prompts.shot_plans = [ShotPlan(
        scene_id=1,
        parent_duration_sec=5,
        capacity=SceneCapacityReport(
            scene_id=1, required_total_duration_sec=6, available_duration_sec=6,
            recommended_total_duration_sec=6, recommended_durations_sec=[3, 3],
            recommended_shot_count=2, status="auto_split", warnings=[],
        ),
        units=[
            ShotUnit(shot_id="scene-1-shot-1", parent_scene_id=1, order=1, duration_sec=3),
            ShotUnit(shot_id="scene-1-shot-2", parent_scene_id=1, order=2, duration_sec=3),
        ],
    )]

    with pytest.raises(HTTPException, match="子镜头"):
        _schedule_shot_video_job(state, 1)


def test_single_shot_generation_requires_at_least_one_saved_reference_image():
    state = _state()
    with pytest.raises(HTTPException, match="参考图"):
        _schedule_shot_video_job(state, 1)


def test_shot_prompt_resolves_planned_child_unit_by_stable_shot_id():
    from src.schemas.shot_plan import ShotPlan, ShotUnit, SceneCapacityReport

    state = _state()
    state.script.scenes[0].duration_sec = 8
    state.script.duration_sec = 8
    state.script.scenes[0].action_description = "Hero walks to the table and picks up the key"
    state.generation_prompts.shots[0].duration_sec = 8
    state.generation_prompts.shots[0].prompt = "【动作时间轴】0-8s Hero walks to the table and picks up the key"
    state.generation_prompts.shot_plans = [ShotPlan(
        scene_id=1, parent_duration_sec=8,
        capacity=SceneCapacityReport(scene_id=1, required_total_duration_sec=8, available_duration_sec=8, recommended_total_duration_sec=8, recommended_durations_sec=[4, 4], recommended_shot_count=2, status="auto_split", warnings=["split"]),
        units=[
            ShotUnit(shot_id="scene-1-shot-1", parent_scene_id=1, order=1, beat_ids=["scene-1-beat-1"], duration_sec=3),
            ShotUnit(shot_id="scene-1-shot-2", parent_scene_id=1, order=2, beat_ids=["scene-1-beat-2"], duration_sec=4, continuity_from_shot_id="scene-1-shot-1"),
        ],
    )]

    child = api_context._shot_prompt(state, 1, "scene-1-shot-2")

    assert child.shot_id == "scene-1-shot-2"
    assert child.duration_sec == 4
    assert "【本镜禁止执行】" in child.prompt


def test_video_snapshot_freezes_child_shot_prompt_and_identity(monkeypatch):
    from src.schemas.shot_plan import ShotPlan, ShotUnit, SceneCapacityReport

    state = _state()
    state.script.scenes[0].duration_sec = 8
    state.script.duration_sec = 8
    state.script.scenes[0].action_description = "Hero walks to the table and picks up the key"
    state.generation_prompts.shots[0].duration_sec = 8
    state.generation_prompts.shots[0].prompt = "【动作时间轴】0-8s Hero walks to the table and picks up the key"
    state.generation_prompts.shot_plans = [ShotPlan(
        scene_id=1, parent_duration_sec=8,
        capacity=SceneCapacityReport(scene_id=1, required_total_duration_sec=8, available_duration_sec=8, recommended_total_duration_sec=8, recommended_durations_sec=[4, 4], recommended_shot_count=2, status="auto_split", warnings=["split"]),
        units=[
            ShotUnit(shot_id="scene-1-shot-1", parent_scene_id=1, order=1, beat_ids=["scene-1-beat-1"], duration_sec=4),
            ShotUnit(shot_id="scene-1-shot-2", parent_scene_id=1, order=2, beat_ids=["scene-1-beat-2"], duration_sec=4, continuity_from_shot_id="scene-1-shot-1"),
        ],
    )]
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_args: [])
    from src.services.video_capabilities import normalize_video_policy
    policy = normalize_video_policy({"provider": "mock", "model": "mock", "resolution": "720p"})

    snapshot = _build_video_generation_snapshot(state, 1, policy, shot_id="scene-1-shot-2")

    assert snapshot["shot_id"] == "scene-1-shot-2"
    assert snapshot["scene_id"] == 1
    assert snapshot["effective_duration_sec"] == 4
    assert "【本镜禁止执行】" in snapshot["raw_prompt"]


def test_sync_active_shot_asset_keeps_all_generated_child_shots():
    state = _state()
    state.shot_productions[1] = ShotProductionState(
        scene_id=1,
        video_versions=[
            ShotVideoVersion(scene_id=1, shot_id="scene-1-shot-1", parent_scene_id=1, local_path="child-1.mp4", success=True, version_id="v-child-1", created_at=""),
            ShotVideoVersion(scene_id=1, shot_id="scene-1-shot-2", parent_scene_id=1, local_path="child-2.mp4", success=True, version_id="v-child-2", created_at=""),
        ],
        active_version_id="v-child-2",
        generation_status="success",
    )

    _sync_active_shot_asset(state, 1)

    assert [item.shot_id for item in state.generated_assets.shots] == ["scene-1-shot-1", "scene-1-shot-2"]


@pytest.mark.asyncio
async def test_production_route_expands_shot_plan_into_child_jobs(monkeypatch):
    from src.schemas.shot_plan import ShotPlan, ShotUnit, SceneCapacityReport

    state = _state()
    state.generation_prompts.shots[0].prompt = "Hero waits."
    state.generation_prompts.shot_plans = [ShotPlan(
        scene_id=1, parent_duration_sec=5,
        capacity=SceneCapacityReport(
            scene_id=1, required_total_duration_sec=4, available_duration_sec=5,
            recommended_total_duration_sec=6, recommended_durations_sec=[3, 3],
            recommended_shot_count=2, status="auto_split", warnings=["split"],
        ),
        units=[
            ShotUnit(shot_id="scene-1-shot-1", parent_scene_id=1, order=1, beat_ids=["scene-1-beat-1"], duration_sec=3),
            ShotUnit(shot_id="scene-1-shot-2", parent_scene_id=1, order=2, beat_ids=["scene-1-beat-2"], duration_sec=3),
        ],
    )]
    production = ShotProductionState(scene_id=1)
    captured = []

    monkeypatch.setattr(production_routes, "_load_project_state", lambda _project_id: state)
    monkeypatch.setattr(production_routes, "_save_project_state", lambda _state: None)
    monkeypatch.setattr(production_routes, "_production_payload", lambda _state: {"ok": True})
    async def prepare(_state):
        return None
    monkeypatch.setattr(production_routes, "_prepare_auto_references", prepare)
    monkeypatch.setattr(production_routes, "_ensure_shot_production", lambda *_args: production)
    monkeypatch.setattr(production_routes, "audit_current_prompt_fidelity", lambda _state: SimpleNamespace(model_dump=lambda: {}, blocked=False))
    monkeypatch.setattr(production_routes, "prompt_fidelity_gate_detail", lambda _report: None)
    monkeypatch.setattr(production_routes.job_store, "list_jobs", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(production_routes, "normalize_video_policy", lambda *_args, **_kwargs: {
        "provider": "mock", "model": "mock", "resolution": "720p",
        "unit_price_rmb_per_sec": 0.0, "capability": {"continuity": False},
    })
    monkeypatch.setattr(production_routes, "_build_video_generation_snapshot", lambda _state, scene_id, policy, **kwargs: (
        captured.append(kwargs.get("shot_id")) or {"scene_id": scene_id, "shot_id": kwargs.get("shot_id"), "references": []}
    ))
    monkeypatch.setattr(production_routes.job_manager, "create_job", lambda project_id, **spec: {"job_id": spec["target_id"]})

    result = await production_routes.generate_project_production_shot(state.project_id, 1)

    assert result["job_ids"] == ["scene-1-shot-1", "scene-1-shot-2"]
    assert captured == ["scene-1-shot-1", "scene-1-shot-2"]


@pytest.mark.asyncio
async def test_legacy_worker_uses_materialized_child_and_project_policy(tmp_path, monkeypatch):
    from src.schemas.assets import ShotAsset

    state = _state()
    state.video_generation_policy = {
        "provider": "happyhorse",
        "model": "happyhorse-1.1-i2v",
        "resolution": "1080p",
    }
    reference_path = tmp_path / "reference.png"
    reference_path.write_bytes(b"png")
    video_path = tmp_path / "child.mp4"
    video_path.write_bytes(b"mp4")
    production = api_context._ensure_shot_production(state, 1)
    production.references = []
    child = state.generation_prompts.shots[0].model_copy(update={
        "shot_id": "scene-1-shot-1",
        "parent_scene_id": 1,
        "shot_order": 1,
        "duration_sec": 5,
        "prompt": "child prompt",
    })
    calls = []
    selected_policies = []

    class _SelectedAgent:
        async def generate_shot(self, shot, references, **kwargs):
            calls.append((shot, references, kwargs))
            return ShotAsset(
                scene_id=shot.scene_id,
                shot_id=shot.shot_id,
                parent_scene_id=shot.parent_scene_id,
                shot_order=shot.shot_order,
                duration_sec=shot.duration_sec,
                local_path=str(video_path),
                success=True,
            )

    selected_agent = _SelectedAgent()
    monkeypatch.setattr(api_context, "_load_project_state", lambda _project_id: state)
    monkeypatch.setattr(api_context, "_save_project_state", lambda _state: None)
    monkeypatch.setattr(api_context, "_materialized_shot_prompts", lambda *_args, **_kwargs: [child])
    monkeypatch.setattr(
        api_context,
        "_build_video_generation_snapshot",
        lambda _state, _scene_id, _policy, shot_id=None: {
            "scene_id": 1,
            "shot_id": shot_id,
            "prepared_shot": child.model_dump(),
            "provider_prompt": "child provider prompt",
            "references": [{"image_path": str(reference_path), "asset_type": "scene", "asset_id": "room", "view_type": "wide"}],
            "reference_labels": ["scene:room (wide)"],
            "reference_mode": "first_frame",
            "native_audio_requested": False,
        },
    )
    monkeypatch.setattr(
        api_context,
        "video_agent_for_policy",
        lambda policy: (selected_policies.append(policy) or selected_agent),
        raising=False,
    )
    monkeypatch.setattr(api_context, "video_gen_agent", selected_agent)
    monkeypatch.setattr(api_context, "inspect_video", lambda *_args, **_kwargs: {"status": "passed"})
    async def _review(*_args, **_kwargs):
        return {"status": "pass", "issues": []}
    monkeypatch.setattr(api_context, "review_video_subjective", _review)
    api_context.video_jobs.clear()

    await api_context._run_shot_video_job(state.project_id, 1, "legacy-version")

    assert selected_policies and selected_policies[0]["provider"] == "happyhorse"
    assert len(calls) == 1
    assert calls[0][0].shot_id == "scene-1-shot-1"
    assert calls[0][0].duration_sec == 5
    assert calls[0][1] == [str(reference_path)]
    assert calls[0][2]["request_is_prepared"] is True
    assert state.shot_productions[1].video_versions[0].shot_id == "scene-1-shot-1"


def test_sync_active_shot_asset_persists_expected_child_shot_ids():
    from src.schemas.shot_plan import ShotPlan, ShotUnit, SceneCapacityReport

    state = _state()
    state.generation_prompts.shot_plans = [ShotPlan(
        scene_id=1, parent_duration_sec=5,
        capacity=SceneCapacityReport(
            scene_id=1, required_total_duration_sec=4, available_duration_sec=5,
            recommended_total_duration_sec=6, recommended_durations_sec=[3, 3],
            recommended_shot_count=2, status="auto_split", warnings=["split"],
        ),
        units=[
            ShotUnit(shot_id="scene-1-shot-1", parent_scene_id=1, order=1, duration_sec=3),
            ShotUnit(shot_id="scene-1-shot-2", parent_scene_id=1, order=2, duration_sec=3),
        ],
    )]
    state.shot_productions[1] = ShotProductionState(
        scene_id=1,
        video_versions=[ShotVideoVersion(scene_id=1, shot_id="scene-1-shot-1", local_path="one.mp4", success=True, version_id="v1", created_at="")],
        active_version_id="v1", generation_status="success",
    )

    _sync_active_shot_asset(state, 1)

    assert state.generated_assets.expected_shot_ids == ["scene-1-shot-1", "scene-1-shot-2"]


def test_sync_active_shot_asset_uses_only_latest_active_version_per_child():
    state = _state()
    state.shot_productions[1] = ShotProductionState(
        scene_id=1,
        video_versions=[
            ShotVideoVersion(scene_id=1, shot_id="scene-1-shot-1", local_path="old.mp4", success=True, version_id="old", created_at=""),
            ShotVideoVersion(scene_id=1, shot_id="scene-1-shot-1", local_path="new.mp4", success=True, version_id="new", created_at=""),
            ShotVideoVersion(scene_id=1, shot_id="scene-1-shot-2", local_path="two.mp4", success=True, version_id="two", created_at=""),
        ],
        active_version_id="two",
        active_version_by_shot_id={"scene-1-shot-1": "new", "scene-1-shot-2": "two"},
        generation_status="success",
    )

    _sync_active_shot_asset(state, 1)

    assert [(item.shot_id, item.local_path) for item in state.generated_assets.shots] == [
        ("scene-1-shot-1", "new.mp4"), ("scene-1-shot-2", "two.mp4")
    ]


def test_sync_active_shot_asset_uses_latest_child_version_without_active_map():
    state = _state()
    state.shot_productions[1] = ShotProductionState(
        scene_id=1,
        video_versions=[
            ShotVideoVersion(scene_id=1, shot_id="scene-1-shot-1", local_path="old.mp4", success=True, version_id="old", created_at=""),
            ShotVideoVersion(scene_id=1, shot_id="scene-1-shot-1", local_path="new.mp4", success=True, version_id="new", created_at=""),
        ],
        active_version_id="new",
        generation_status="success",
    )

    _sync_active_shot_asset(state, 1)

    assert [(item.shot_id, item.local_path) for item in state.generated_assets.shots] == [
        ("scene-1-shot-1", "new.mp4")
    ]


@pytest.mark.asyncio
async def test_activate_child_version_updates_per_shot_active_map(monkeypatch):
    from src.api.context import ActivateVideoVersionRequest

    state = _state()
    state.shot_productions[1] = ShotProductionState(
        scene_id=1,
        video_versions=[
            ShotVideoVersion(scene_id=1, local_path="parent.mp4", success=True, version_id="version-parent", created_at=""),
            ShotVideoVersion(scene_id=1, shot_id="scene-1-shot-1", local_path="one-old.mp4", success=True, version_id="version-old", created_at=""),
            ShotVideoVersion(scene_id=1, shot_id="scene-1-shot-1", local_path="one-new.mp4", success=True, version_id="version-new", created_at=""),
            ShotVideoVersion(scene_id=1, shot_id="scene-1-shot-2", local_path="two.mp4", success=True, version_id="version-two", created_at=""),
        ],
        active_version_id="version-parent",
        active_version_by_shot_id={"scene-1-shot-2": "version-two"},
        generation_status="success",
    )
    monkeypatch.setattr(production_routes, "_load_project_state", lambda _project_id: state)
    monkeypatch.setattr(production_routes, "_save_project_state", lambda _state: None)
    monkeypatch.setattr(production_routes, "_production_payload", lambda _state: {"ok": True})

    await production_routes.activate_project_video_version(
        state.project_id, 1, ActivateVideoVersionRequest(version_id="version-new", shot_id="scene-1-shot-1")
    )

    assert state.shot_productions[1].active_version_by_shot_id == {
        "scene-1-shot-1": "version-new", "scene-1-shot-2": "version-two",
    }
    assert state.shot_productions[1].active_version_id == "version-parent"
    assert next(item for item in state.generated_assets.shots if item.shot_id == "scene-1-shot-1").local_path == "one-new.mp4"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("version_id", "shot_id"),
    [
        ("version-two", "scene-1-shot-1"),
        ("version-parent", "scene-1-shot-1"),
        ("version-new", None),
    ],
)
async def test_activate_version_rejects_cross_child_and_parent_child_scope_mismatch(
    monkeypatch, version_id, shot_id,
):
    from src.api.context import ActivateVideoVersionRequest

    state = _state()
    state.shot_productions[1] = ShotProductionState(
        scene_id=1,
        video_versions=[
            ShotVideoVersion(scene_id=1, local_path="parent.mp4", success=True, version_id="version-parent", created_at=""),
            ShotVideoVersion(scene_id=1, shot_id="scene-1-shot-1", local_path="one.mp4", success=True, version_id="version-new", created_at=""),
            ShotVideoVersion(scene_id=1, shot_id="scene-1-shot-2", local_path="two.mp4", success=True, version_id="version-two", created_at=""),
        ],
        active_version_id="version-parent",
        active_version_by_shot_id={"scene-1-shot-2": "version-two"},
        generation_status="success",
    )
    monkeypatch.setattr(production_routes, "_load_project_state", lambda _project_id: state)
    monkeypatch.setattr(production_routes, "_save_project_state", lambda _state: None)
    request = ActivateVideoVersionRequest(version_id=version_id, **({"shot_id": shot_id} if shot_id is not None else {}))

    with pytest.raises(HTTPException) as exc_info:
        await production_routes.activate_project_video_version(state.project_id, 1, request)

    assert exc_info.value.status_code == 409
    assert state.shot_productions[1].active_version_id == "version-parent"
    assert state.shot_productions[1].active_version_by_shot_id == {"scene-1-shot-2": "version-two"}


@pytest.mark.asyncio
async def test_activate_parent_version_keeps_legacy_request_compatible(monkeypatch):
    from src.api.context import ActivateVideoVersionRequest

    state = _state()
    state.shot_productions[1] = ShotProductionState(
        scene_id=1,
        video_versions=[ShotVideoVersion(scene_id=1, local_path="parent.mp4", success=True, version_id="version-parent", created_at="")],
        active_version_id="old-parent",
        generation_status="failed",
    )
    monkeypatch.setattr(production_routes, "_load_project_state", lambda _project_id: state)
    monkeypatch.setattr(production_routes, "_save_project_state", lambda _state: None)
    monkeypatch.setattr(production_routes, "_production_payload", lambda _state: {"ok": True})

    await production_routes.activate_project_video_version(
        state.project_id, 1, ActivateVideoVersionRequest(version_id="version-parent")
    )

    assert state.shot_productions[1].active_version_id == "version-parent"


@pytest.mark.asyncio
async def test_child_failure_persists_exact_error_without_borrowing_sibling(monkeypatch):
    from src.api.routes import jobs

    state = _state()
    state.shot_productions[1] = ShotProductionState(
        scene_id=1,
        generation_status="generating",
        generation_status_by_shot_id={"scene-1-shot-1": "generating", "scene-1-shot-2": "generating"},
        generation_error_by_shot_id={"scene-1-shot-2": "sibling failure"},
    )
    monkeypatch.setattr(jobs, "_load_project_state", lambda _project_id: state)
    monkeypatch.setattr(jobs, "_save_project_state", lambda _state: None)

    await jobs._mark_video_job_stopped(state.project_id, 1, "selected child failure", "scene-1-shot-1")

    production = state.shot_productions[1]
    assert production.generation_error_by_shot_id == {
        "scene-1-shot-1": "selected child failure",
        "scene-1-shot-2": "sibling failure",
    }
    assert production.error_message == "scene-1-shot-1: selected child failure"


def test_restarting_service_keeps_child_generation_running_when_child_job_is_active(monkeypatch):
    state = _state()
    state.shot_productions[1] = ShotProductionState(scene_id=1, generation_status="generating")
    monkeypatch.setattr(api_context.job_store, "list_jobs", lambda *_args, **_kwargs: [{
        "job_type": "video.generate", "target_type": "shot_unit",
        "target_id": "scene-1-shot-2", "payload": {"scene_id": 1, "shot_id": "scene-1-shot-2"},
    }])

    production = api_context._ensure_shot_production(state, 1)

    assert production.generation_status == "generating"


def test_user_removed_automatic_reference_is_not_readded_on_reconcile(monkeypatch):
    state = _state()
    kept = ShotReference(
        reference_id="character|Hero|front_full",
        source="asset",
        asset_type="character",
        asset_id="Hero",
        view_type="front_full",
        asset_version_id="hero-v1",
        image_path="hero-v1.png",
    )
    removed = ShotReference(
        reference_id="prop|watch|front",
        source="asset",
        asset_type="prop",
        asset_id="watch",
        view_type="front",
        asset_version_id="watch-v1",
        image_path="watch-v1.png",
    )
    state.shot_productions[1] = ShotProductionState(
        scene_id=1,
        references=[kept],
        excluded_automatic_reference_ids=[removed.reference_id],
    )
    monkeypatch.setattr(api_context, "_default_shot_references", lambda *_: [kept, removed])

    references = _reconcile_shot_references(state, 1)

    assert [item.reference_id for item in references] == [kept.reference_id]
    assert state.shot_productions[1].excluded_automatic_reference_ids == [removed.reference_id]


@pytest.mark.asyncio
async def test_readding_reference_clears_automatic_exclusion(monkeypatch):
    state = _state()
    reference = ShotReference(
        reference_id="character|Hero|front_full",
        source="asset",
        asset_type="character",
        asset_id="Hero",
        view_type="front_full",
        asset_version_id="hero-v1",
        image_path="hero-v1.png",
    )
    production = ShotProductionState(
        scene_id=1,
        references=[],
        excluded_automatic_reference_ids=[reference.reference_id],
    )
    state.shot_productions[1] = production
    monkeypatch.setattr(production_routes, "_load_project_state", lambda _project_id: state)
    monkeypatch.setattr(production_routes, "_ensure_shot_production", lambda *_args: production)
    monkeypatch.setattr(
        production_routes,
        "_reference_catalog",
        lambda _state: {"by_id": {reference.reference_id: reference}, "characters": [], "scenes": [], "props": [], "auto_reference_ids": {reference.reference_id}},
    )
    monkeypatch.setattr(production_routes, "_save_project_state", lambda _state: None)
    monkeypatch.setattr(production_routes, "_production_payload", lambda _state: {"ok": True})

    result = await production_routes.update_project_production_shot(
        state.project_id,
        1,
        ProductionShotUpdateRequest(
            prompt="Hero waits.",
            references=[ProductionReferenceRequest(reference_id=reference.reference_id)],
        ),
    )

    assert result == {"ok": True}
    assert production.excluded_automatic_reference_ids == []
    assert production.references[0].reference_id == reference.reference_id


def test_manual_reference_removal_does_not_suppress_asset_binding():
    state = _state()
    manual = ShotReference(
        reference_id="manual|board|1",
        source="manual",
        asset_type="manual",
        asset_id="board",
        view_type="image",
        image_path="moodboard.png",
    )
    state.shot_productions[1] = ShotProductionState(scene_id=1, references=[manual])

    references = _reconcile_shot_references(state, 1)

    assert references[0].reference_id == manual.reference_id
    assert state.shot_productions[1].excluded_automatic_reference_ids == []

@pytest.mark.asyncio
async def test_deleting_automatic_duplicate_keeps_exclusion_even_if_manual_same_asset_remains(monkeypatch):
    state = _state()
    reference_id = "prop|prop_watch_chain|front"
    manual = ShotReference(
        reference_id=reference_id,
        source="manual",
        asset_type="prop",
        asset_id="prop_watch_chain",
        view_type="front",
        asset_version_id="watch-v1",
        image_path="watch-v1.png",
    )
    automatic = manual.model_copy(update={"source": "asset"})
    production = ShotProductionState(
        scene_id=1,
        references=[manual, automatic],
    )
    state.shot_productions[1] = production
    monkeypatch.setattr(production_routes, "_load_project_state", lambda _project_id: state)
    monkeypatch.setattr(production_routes, "_ensure_shot_production", lambda *_args: production)
    monkeypatch.setattr(
        production_routes,
        "_reference_catalog",
        lambda _state: {"by_id": {reference_id: automatic}, "characters": [], "scenes": [], "props": [], "auto_reference_ids": {reference_id}},
    )
    monkeypatch.setattr(production_routes, "_save_project_state", lambda _state: None)
    monkeypatch.setattr(production_routes, "_production_payload", lambda _state: {"ok": True})

    result = await production_routes.update_project_production_shot(
        state.project_id,
        1,
        ProductionShotUpdateRequest(
            prompt="Hero waits.",
            references=[ProductionReferenceRequest(reference_id=reference_id, source="manual")],
            excluded_automatic_reference_ids=[reference_id],
        ),
    )

    assert result == {"ok": True}
    assert production.excluded_automatic_reference_ids == [reference_id]


@pytest.mark.asyncio
async def test_settings_only_save_preserves_over_cap_parent_prompt_and_duration(monkeypatch):
    from src.api.context import VideoGenerationPolicyRequest

    state = _state()
    state.script.scenes[0].duration_sec = 30
    state.generation_prompts.shots[0].duration_sec = 30
    state.generation_prompts.shots[0].negative_prompt = "parent negative"
    state.prompt_review = {"status": "ready"}
    production = ShotProductionState(scene_id=1, continuity_enabled=True)
    state.shot_productions[1] = production
    monkeypatch.setattr(production_routes, "_load_project_state", lambda _project_id: state)
    monkeypatch.setattr(production_routes, "_ensure_shot_production", lambda *_args: production)
    monkeypatch.setattr(production_routes, "_save_project_state", lambda _state: None)
    monkeypatch.setattr(production_routes, "_production_payload", lambda _state: {"ok": True})
    monkeypatch.setattr(
        production_routes, "validate_video_duration",
        lambda *_args: (_ for _ in ()).throw(AssertionError("settings must not validate parent duration")),
    )

    result = await production_routes.update_project_production_shot(
        state.project_id, 1, ProductionShotUpdateRequest(
            video_override=VideoGenerationPolicyRequest(provider="mock", model="mock", resolution="720p"),
            continuity_enabled=False,
        ),
    )

    assert result == {"ok": True}
    assert state.generation_prompts.shots[0].prompt == "Hero waits."
    assert state.generation_prompts.shots[0].negative_prompt == "parent negative"
    assert state.generation_prompts.shots[0].duration_sec == 30
    assert state.script.scenes[0].duration_sec == 30
    assert state.prompt_review == {"status": "ready"}
    assert production.video_override["resolution"] == "720p"
    assert production.continuity_enabled is False
