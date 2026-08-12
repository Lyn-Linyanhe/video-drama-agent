import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from src.api import context as api_context
from src.api.context import CharacterReferenceEditRequest, RegenerateImageRequest
from src.api.routes import jobs as job_routes
from src.api_clients.image_base import ImageGenResult
from src.schemas.scene_state import LocationState
from src.schemas.script import LocationAsset, Scene, ScriptBreakdown, ShotVisualBinding, VisualAssetManifest
from src.services import asset_generation, media_quality
from src.utils.asset_versions import ImageAssetVersions
from src.utils.visual_asset_manifest import _revealed_character_aliases, normalize_scene_assets
from tests.test_asset_generation_api import _client
from tests.test_wan_character_references import FakeWanClient, _enable_wan, _prepare_character_project


def _detailed_png(path: Path, size: tuple[int, int] = (256, 256)) -> Path:
    image = Image.new("RGB", size, (40, 90, 140))
    draw = ImageDraw.Draw(image)
    for offset in range(0, min(size), 8):
        draw.line((0, offset, size[0] - 1, size[1] - 1 - offset), fill=(230, 220, 80), width=3)
    image.save(path)
    return path


@pytest.mark.asyncio
async def test_asset_validation_passes_every_real_view_type_to_subjective_review(tmp_path, monkeypatch):
    image_path = _detailed_png(tmp_path / "asset.png")
    seen = []

    monkeypatch.setattr(asset_generation, "inspect_image", lambda *_args, **_kwargs: {
        "status": "passed", "technical_checks": [], "warnings": [],
    })

    async def review(_path, _prompt, view_type, asset_type="image"):
        seen.append((asset_type, view_type))
        return {"status": "passed", "passed": True, "issues": []}

    monkeypatch.setattr(asset_generation, "review_image_subjective", review)
    result = SimpleNamespace(local_path=str(image_path))

    await asset_generation._validate_generated_image(result, "front", "character", "front_half", "wan")
    await asset_generation._validate_generated_image(result, "reverse", "scene", "scene_reverse", "wan")
    await asset_generation._validate_generated_image(result, "left", "prop", "left", "wan")

    assert seen == [
        ("character", "front_half"),
        ("scene", "scene_reverse"),
        ("prop", "left"),
    ]


@pytest.mark.asyncio
async def test_primary_openai_vision_fallback_uses_asset_review_contract(tmp_path, monkeypatch):
    image_path = _detailed_png(tmp_path / "scene.png")
    captured = {}

    class FakeLLM:
        async def analyze_image(self, _path, prompt, system):
            captured["prompt"] = prompt
            return '{"passed":true,"issues":[]}'

    import src.utils.llm as llm_module

    monkeypatch.setattr(media_quality, "_vision_capability", lambda: (True, "openai/gpt-5"))
    monkeypatch.setattr(media_quality, "resolve_review_config", lambda: {
        "provider": "", "model": "", "api_key": "", "base_url": "",
    })
    monkeypatch.setattr(llm_module, "get_llm_client", lambda: FakeLLM())

    await media_quality.review_image_subjective(image_path, "雨夜地下室", "scene_reverse", "scene")

    assert captured["prompt"] == media_quality.build_image_review_prompt(
        "scene", "scene_reverse", "雨夜地下室",
    )


def test_upload_validates_before_replace_and_records_unreviewed(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)
    generated = client.post(f"/api/projects/{project_id}/assets/prop/old_key/generate", json={})
    assert generated.status_code == 200
    before = api_context._load_project_state(project_id).prop_images.props[0]
    active_path = Path(before.image_path)
    original_bytes = active_path.read_bytes()
    original_version = before.version_id

    invalid = client.post(
        f"/api/projects/{project_id}/image-upload",
        data={"asset_type": "prop", "asset_id": "old_key", "view_type": "front"},
        files={"file": ("broken.png", b"not an image", "image/png")},
    )

    assert invalid.status_code == 400
    assert active_path.read_bytes() == original_bytes
    assert api_context._load_project_state(project_id).prop_images.props[0].version_id == original_version

    upload_path = _detailed_png(tmp_path / "uploaded.png", (300, 300))
    uploaded = client.post(
        f"/api/projects/{project_id}/image-upload",
        data={"asset_type": "prop", "asset_id": "old_key", "view_type": "front"},
        files={"file": ("uploaded.png", upload_path.read_bytes(), "image/png")},
    )

    assert uploaded.status_code == 200
    active = next(item for item in uploaded.json()["history"] if item["active"])
    assert active["quality_report"]["status"] == "unreviewed"
    saved = api_context._load_project_state(project_id).prop_images.props[0]
    assert saved.quality_report["status"] == "unreviewed"
    assert saved.quality_report.get("automatic_reference_allowed") is not True


def test_history_activation_restores_selected_version_quality_report(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)
    first_path = _detailed_png(tmp_path / "first.png")
    second_path = _detailed_png(tmp_path / "second.png")
    versions = ImageAssetVersions(api_context.project_store.get_project_dir(project_id))
    first_report = {"status": "passed", "automatic_reference_allowed": True}
    second_report = {"status": "warning", "automatic_reference_allowed": False}
    _, first_history = versions.replace_with_file(
        "prop", "old_key", "front", first_path, "first", "generated",
        metadata={"quality_report": first_report},
    )
    first_id = next(item["id"] for item in first_history if item["active"])
    _, second_history = versions.replace_with_file(
        "prop", "old_key", "front", second_path, "second", "generated",
        metadata={"quality_report": second_report},
    )
    second_id = next(item["id"] for item in second_history if item["active"])
    state, asset, _, _ = api_context._resolve_image_asset(
        project_id, RegenerateImageRequest(asset_type="prop", asset_id="old_key", view_type="front"),
        create_missing=True,
    )
    api_context._save_image_asset_state(
        project_id, state, asset, versions.active_path("prop", "old_key", "front"), "second",
        version_id=second_id, quality_report=second_report,
    )

    response = client.post(
        f"/api/projects/{project_id}/image-history/activate",
        json={"asset_type": "prop", "asset_id": "old_key", "view_type": "front", "version_id": first_id},
    )

    assert response.status_code == 200
    saved = api_context._load_project_state(project_id).prop_images.props[0]
    assert saved.version_id == first_id
    assert saved.quality_report == first_report


def test_reveal_alias_parser_uses_the_sentence_containing_the_cue():
    script = ScriptBreakdown(
        title="揭晓", genre="悬疑", logline="身份揭晓", duration_sec=6, narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=1, duration_sec=3, location="地下室", characters=["黑衣人", "陆临", "顾衡"],
                action_description="顾衡低声说：别动。黑衣人摘下口罩，原来是陆临。顾衡后退。",
                camera_direction="中景", emotion_tone="紧张",
            ),
            Scene(
                scene_id=2, duration_sec=3, location="地下室", characters=["黑衣人", "陆临"],
                action_description="黑衣人露出真容，站在灯下的正是陆临。",
                camera_direction="近景", emotion_tone="震惊",
            ),
        ],
    )

    assert _revealed_character_aliases(script) == {"黑衣人": "陆临"}


def test_identical_location_states_merge_by_visual_signature():
    states = [
        LocationState(
            state_id="old_a", label="夜晚", time_condition="夜晚", weather="雨",
            light_direction="左侧冷光", visible_state_notes="地面积水", shot_ids=[1], representative_scene_id=1,
        ),
        LocationState(
            state_id="old_b", label="另一个名称", time_condition="夜晚", weather="雨",
            light_direction="左侧冷光", visible_state_notes="地面积水", shot_ids=[2], representative_scene_id=2,
        ),
        LocationState(
            state_id="old_c", label="雨停", time_condition="夜晚", weather="晴",
            light_direction="左侧冷光", visible_state_notes="地面积水", shot_ids=[3], representative_scene_id=3,
        ),
    ]
    script = ScriptBreakdown(
        title="状态去重", genre="悬疑", logline="同场景状态", duration_sec=9, narration_style="无旁白",
        scenes=[
            Scene(scene_id=i, duration_sec=3, location="旧客厅", characters=[], action_description="静态空镜", camera_direction="中景", emotion_tone="平静")
            for i in (1, 2, 3)
        ],
        asset_manifest=VisualAssetManifest(
            locations=[
                LocationAsset(
                    location_id="room_a", canonical_name="旧客厅", representative_scene_id=1,
                    shot_ids=[1], states=[states[0]], default_state_id="old_a",
                ),
                LocationAsset(
                    location_id="room_b", canonical_name="旧客厅", representative_scene_id=2,
                    shot_ids=[2], states=[states[1]], default_state_id="old_b",
                ),
                LocationAsset(
                    location_id="room_c", canonical_name="旧客厅", representative_scene_id=3,
                    shot_ids=[3], states=[states[2]], default_state_id="old_c",
                ),
            ],
            shot_bindings=[
                ShotVisualBinding(scene_id=1, location_id="room_a", state_id="old_a"),
                ShotVisualBinding(scene_id=2, location_id="room_b", state_id="old_b"),
                ShotVisualBinding(scene_id=3, location_id="room_c", state_id="old_c"),
            ],
        ),
    )

    normalize_scene_assets(script)

    location = script.asset_manifest.locations[0]
    assert len(location.states) == 2
    merged = next(state for state in location.states if state.weather == "雨")
    distinct = next(state for state in location.states if state.weather == "晴")
    assert merged.shot_ids == [1, 2]
    assert {binding.state_id for binding in script.asset_manifest.shot_bindings[:2]} == {merged.state_id}
    assert script.asset_manifest.shot_bindings[2].state_id == distinct.state_id


@pytest.mark.asyncio
async def test_generic_multiview_failure_rolls_back_all_activated_views(tmp_path, monkeypatch):
    _, project_id = _client(tmp_path, monkeypatch)

    class FailingSecondClient:
        provider_name = "mock"

        def __init__(self):
            self.calls = 0

        async def generate_image(self, **_kwargs):
            self.calls += 1
            if self.calls == 2:
                return ImageGenResult(False, error_message="second view failed", provider_name="mock")
            path = _detailed_png(tmp_path / f"generic-{self.calls}.png", (1920, 1080))
            return ImageGenResult(
                True, local_path=str(path), width=1920, height=1080, provider_name="mock",
            )

    failing_client = FailingSecondClient()
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: failing_client)
    result = await asset_generation.generate_asset_views(
        project_id, "scene", api_context._load_project_state(project_id).script.asset_manifest.locations[0].location_id,
        ("wide", "medium"), regenerate=True,
    )

    assert result["generated"] == []
    assert len(result["errors"]) == 1
    state = api_context._load_project_state(project_id)
    assert not any(image.image_path for image in (state.scene_images.scenes if state.scene_images else []))


@pytest.mark.asyncio
async def test_wan_character_group_commit_failure_rolls_back_files_manifest_and_state(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id = _prepare_character_project(tmp_path, monkeypatch)
    fake = FakeWanClient(tmp_path)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    await asset_generation.regenerate_image_asset(
        project_id,
        RegenerateImageRequest(
            asset_type="character", asset_id="hero", view_type="front_half",
            provider="wan", model="wan2.7-image-pro", resolution="2K",
        ),
    )
    project_dir = api_context.project_store.get_project_dir(project_id)
    manifest_path = project_dir / "assets" / "asset_manifest.json"
    meta_path = project_dir / "meta.json"
    before_manifest = manifest_path.read_bytes()
    before_state_json = json.loads(meta_path.read_text(encoding="utf-8"))["assets"]["state_json"]
    before_active = (project_dir / "assets" / "characters" / "hero" / "front_half.png").read_bytes()
    original = ImageAssetVersions.replace_with_file
    calls = 0

    def fail_second(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("manifest write failed")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ImageAssetVersions, "replace_with_file", fail_second)

    with pytest.raises(OSError, match="manifest write failed"):
        await asset_generation.generate_character_reference_group(project_id, "hero")

    assert manifest_path.read_bytes() == before_manifest
    restored_state_json = json.loads(meta_path.read_text(encoding="utf-8"))["assets"]["state_json"]
    assert restored_state_json == before_state_json
    assert (project_dir / "assets" / "characters" / "hero" / "front_half.png").read_bytes() == before_active


@pytest.mark.asyncio
async def test_character_reference_edit_validates_and_persists_quality(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id = _prepare_character_project(tmp_path, monkeypatch)
    fake = FakeWanClient(tmp_path)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    await asset_generation.regenerate_image_asset(
        project_id,
        RegenerateImageRequest(
            asset_type="character", asset_id="hero", view_type="front_half",
            provider="wan", model="wan2.7-image-pro", resolution="2K",
        ),
    )
    group = await asset_generation.generate_character_reference_group(project_id, "hero")
    expected = {"status": "warning", "automatic_reference_allowed": False, "technical_checks": []}
    seen = []

    async def validate(_image, _prompt, asset_type, view_type, _provider, *_dimensions):
        seen.append((asset_type, view_type))
        return expected

    monkeypatch.setattr(asset_generation, "_validate_generated_image", validate)
    result = await asset_generation.edit_character_reference(
        project_id,
        CharacterReferenceEditRequest(
            asset_type="character", asset_id="hero", view_type="back_full", prompt="延长外套",
            provider="wan", model="wan2.7-image-pro", resolution="2K", reference_version_ids=[],
        ),
    )

    assert seen == [("character", "back_full")]
    active = next(item for item in result["history"] if item["active"])
    assert active["quality_report"] == expected
    state = api_context._load_project_state(project_id)
    edited = next(image for image in state.character_images.characters if image.view_type == "back_full")
    assert edited.quality_report == expected
    assert result["quality_report"] == expected


@pytest.mark.asyncio
async def test_scene_master_and_job_result_return_quality_reports(tmp_path, monkeypatch):
    _, project_id = _client(tmp_path, monkeypatch)
    state_json = api_context._state_to_json(api_context._load_project_state(project_id))
    report = {"status": "unreviewed", "technical_checks": []}

    async def fake_master(*_args, **_kwargs):
        return {
            "state_json": state_json, "version_id": "v1", "image_path": "scene.png",
            "quality": report,
        }

    monkeypatch.setattr(asset_generation, "generate_scene_master", fake_master)
    state = api_context._load_project_state(project_id)
    state.image_generation_policy = {"provider": "wan", "model": "wan2.7-image-pro", "resolution": "2K"}
    api_context._save_project_state(state)
    location_id = state.script.asset_manifest.locations[0].location_id

    service_result = await asset_generation.generate_asset_views(
        project_id, "scene", location_id, ("scene_center",), regenerate=True,
    )
    assert service_result["generated"][0]["quality_report"] == report

    async def fake_regenerate(*_args, **_kwargs):
        return {
            "state_json": state_json,
            "history": [{"id": "v1", "active": True}],
            "quality": report,
        }

    monkeypatch.setattr(job_routes, "_regenerate_image_asset", fake_regenerate)
    monkeypatch.setattr(job_routes, "_asset_record", lambda *_args: SimpleNamespace(image_path="scene.png"))

    class Context:
        project_lock = None
        job_id = "job"

        def __init__(self, current_project_id):
            self.project_id = current_project_id

        def progress(self, *_args):
            pass

        def quality(self, *_args):
            pass

        def provider_task(self, *_args):
            pass

        def check_cancelled(self):
            pass

    job_result = await job_routes._job_asset_generate(
        Context(project_id), {"asset_type": "scene", "asset_id": location_id, "view_type": "wide"},
    )
    assert job_result["quality_report"] == report
