import asyncio
import base64
from pathlib import Path

import pytest
from fastapi import HTTPException
from PIL import Image, ImageDraw

from src.api import context as api_context
from src.api.context import CharacterReferenceEditRequest, RegenerateImageRequest
from src.api_clients.image_base import ImageGenResult, ImageGroupResult
from src.api_clients.image_wan import WanImageClient
from src.schemas.script import VisualCharacterAsset
from src.services import asset_generation
from src.services.image_capabilities import normalize_policy
from src.services.media_quality import MediaQualityFailure
from tests.test_asset_generation_api import _client


class FakeWanClient:
    provider_name = "wan"

    def __init__(self, root: Path, count: int = 4, sequence_reference_counted: bool = False):
        self.root = root
        self.count = count
        self.sequence_reference_counted = sequence_reference_counted
        self.single_calls = []
        self.sequence_calls = []
        self.edit_calls = []
        self._number = 0

    def _image(self, label: str) -> ImageGenResult:
        self._number += 1
        path = self.root / f"{label}-{self._number}.png"
        image = Image.new("RGB", (1024, 1024), (60, 100, 140))
        ImageDraw.Draw(image).line((0, 0, 1023, 1023), fill=(240, 240, 240), width=8)
        image.save(path)
        return ImageGenResult(True, local_path=str(path), width=1024, height=1024, provider_name="wan")

    async def generate_image(self, **kwargs):
        self.single_calls.append(dict(kwargs))
        return self._image("single")

    async def generate_sequence(self, prompt, reference_paths, n, resolution):
        self.sequence_calls.append((prompt, list(reference_paths), n, resolution))
        metadata = {"sequence_reference_counted": True} if self.sequence_reference_counted else {}
        return ImageGroupResult(True, [self._image("group") for _ in range(self.count)], provider_name="wan", metadata=metadata)

    async def edit_image(self, prompt, reference_paths, resolution):
        self.edit_calls.append((prompt, list(reference_paths), resolution))
        return self._image("edit")


def _prepare_character_project(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)
    state = api_context._load_project_state(project_id)
    state.script.asset_manifest.characters.append(VisualCharacterAsset(
        asset_id="hero", name="hero", appearance_mode="on_screen", description="test character",
    ))
    state.image_generation_policy = {
        "provider": "wan", "model": "wan2.7-image-pro", "resolution": "2K",
    }
    api_context._save_project_state(state)
    return client, project_id


def _prepare_scene_project(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)
    state = api_context._load_project_state(project_id)
    state.image_generation_policy = {
        "provider": "wan", "model": "wan2.7-image-pro", "resolution": "2K",
    }
    api_context._save_project_state(state)
    return client, project_id, state.script.asset_manifest.locations[0].location_id


def _enable_wan(monkeypatch):
    import src.config as config

    monkeypatch.setattr(config, "HAPPYHORSE_API_KEY", "test-key")
    monkeypatch.setattr(config, "HAPPYHORSE_BASE_URL", "https://dashscope.aliyuncs.com")
    monkeypatch.setattr(config, "REVIEW_LLM_ENABLED", False)


def test_wan_capabilities_enforce_group_and_edit_resolution(monkeypatch):
    _enable_wan(monkeypatch)
    with pytest.raises(HTTPException):
        normalize_policy({"provider": "kling", "model": "kling-v1", "resolution": "1K"}, mode="group")
    with pytest.raises(HTTPException):
        normalize_policy({"provider": "wan", "model": "wan2.7-image-pro", "resolution": "1K"}, mode="group")
    with pytest.raises(HTTPException):
        normalize_policy({"provider": "wan", "model": "wan2.7-image-pro", "resolution": "4K"}, mode="reference_edit")
    assert normalize_policy({"provider": "wan", "model": "wan2.7-image-pro", "resolution": "4K"})["resolution"] == "4K"


def test_wan_group_request_uses_sequential_four_image_contract(tmp_path, monkeypatch):
    reference = tmp_path / "reference.png"
    Image.new("RGB", (240, 240), "white").save(reference)
    client = WanImageClient("test-key", "wan2.7-image-pro")
    captured = {}

    async def fake_create(content, parameters):
        captured["content"] = content
        captured["parameters"] = parameters
        return ImageGroupResult(True, [])

    monkeypatch.setattr(client, "_create", fake_create)
    asyncio.run(client.generate_sequence("same character", [str(reference)], 4, "2K"))
    assert captured["parameters"] == {"enable_sequential": True, "n": 4, "size": "2K"}
    assert captured["content"][-1] == {"text": "same character"}


def test_wan_sequence_adds_reference_to_provider_count_for_derived_views(tmp_path, monkeypatch):
    reference = tmp_path / "reference.png"
    Image.new("RGB", (240, 240), "white").save(reference)
    client = WanImageClient("test-key", "wan2.7-image-pro")
    captured = {}

    async def fake_create(content, parameters):
        captured["parameters"] = parameters
        return ImageGroupResult(True, [])

    monkeypatch.setattr(client, "_create", fake_create)
    result = asyncio.run(client.generate_sequence("scene views", [str(reference)], 3, "2K"))

    # n is the total sequential count at the provider: one locked master plus
    # three newly generated views.  The service still asks for three outputs.
    assert captured["parameters"] == {"enable_sequential": True, "n": 4, "size": "2K"}
    assert result.metadata["requested_output_count"] == 3
    assert result.metadata["provider_requested_n"] == 4


def test_wan_urls_accepts_supported_result_aliases_and_deduplicates():
    body = {
        "output": {
            "choices": [{"message": {"content": [{"type": "image", "image": "https://img/one"}]}}],
            "results": [{"image_url": "https://img/two"}, {"url": "https://img/one"}],
            "images": ["https://img/three"],
        }
    }

    assert WanImageClient._urls(body) == [
        "https://img/one",
        "https://img/two",
        "https://img/three",
    ]


def test_wan_group_client_uses_official_async_task_contract():
    client = WanImageClient("test-key", "wan2.7-image-pro")

    assert client._CREATE_PATH == "/api/v1/services/aigc/image-generation/generation"
    assert client._headers(asynchronous=True)["X-DashScope-Async"] == "enable"
    assert "X-DashScope-Async" not in client._headers()


def test_wan_reference_input_preserves_supported_formats_and_flattens_alpha(tmp_path):
    opaque = tmp_path / "master.webp"
    transparent = tmp_path / "alpha.png"
    Image.new("RGB", (240, 240), "white").save(opaque)
    Image.new("RGBA", (240, 240), (20, 30, 40, 100)).save(transparent)

    opaque_data = WanImageClient._data_url(opaque)
    alpha_data = WanImageClient._data_url(transparent)

    assert opaque_data.startswith("data:image/webp;base64,")
    assert alpha_data.startswith("data:image/jpeg;base64,")
    assert base64.b64decode(alpha_data.split(",", 1)[1]).startswith(b"\xff\xd8")


def test_wan_provider_error_keeps_code_message_and_request_id():
    message = WanImageClient._provider_error(
        {"code": "InvalidParameter", "message": "n must be 1", "request_id": "req-123"}, "Wan task failed",
    )

    assert "InvalidParameter" in message
    assert "n must be 1" in message
    assert "request_id=req-123" in message


def test_wan_single_image_preserves_requested_aspect_ratio():
    assert WanImageClient._size_for_dimensions(1024, 576) == "1024*576"
    assert WanImageClient._size_for_dimensions(2048, 1152) == "2048*1152"


def test_reference_group_is_atomic_and_reference_edit_keeps_other_views(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id = _prepare_character_project(tmp_path, monkeypatch)
    state = api_context._load_project_state(project_id)
    state.script.asset_manifest.characters[0].name = "沈月"
    state.script.asset_manifest.characters[0].description = "黑色短发，深灰风衣"
    api_context._save_project_state(state)
    fake = FakeWanClient(tmp_path)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    asyncio.run(asset_generation.regenerate_image_asset(
        project_id,
        RegenerateImageRequest(
            asset_type="character", asset_id="hero", view_type="front_half",
            provider="wan", model="wan2.7-image-pro", resolution="2K",
        ),
    ))

    result = asyncio.run(asset_generation.generate_character_reference_group(project_id, "hero"))
    assert fake.sequence_calls[0][2:] == (4, "2K")
    assert "沈月" in fake.sequence_calls[0][0]
    assert "黑色短发，深灰风衣" in fake.sequence_calls[0][0]
    assert "角色：hero" not in fake.sequence_calls[0][0]
    state = api_context._load_project_state(project_id)
    images = [image for image in state.character_images.characters if image.character_name == "hero"]
    assert {image.view_type for image in images} == {"front_half", "front_full", "left_profile", "back_full"}
    assert {image.group_id for image in images} == {result["group_id"]}
    assert all(image.master_version_id == result["master_version_id"] for image in images)
    untouched = {image.view_type: image.version_id for image in images if image.view_type != "back_full"}

    edited = asyncio.run(asset_generation.edit_character_reference(
        project_id,
        CharacterReferenceEditRequest(
            asset_type="character", asset_id="hero", view_type="back_full",
            prompt="make the coat longer", provider="wan", model="wan2.7-image-pro", resolution="2K",
            reference_version_ids=[next(image.version_id for image in images if image.view_type == "left_profile")],
        ),
    ))
    assert edited["master_version_id"] == result["master_version_id"]
    assert len(fake.edit_calls[0][1]) >= 2
    after = api_context._load_project_state(project_id)
    assert {image.view_type: image.version_id for image in after.character_images.characters if image.view_type != "back_full"} == untouched


def test_character_group_never_replaces_locked_master_when_gateway_returns_all_views(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id = _prepare_character_project(tmp_path, monkeypatch)
    fake = FakeWanClient(tmp_path, count=4)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)

    asyncio.run(asset_generation.regenerate_image_asset(
        project_id,
        RegenerateImageRequest(
            asset_type="character", asset_id="hero", view_type="front_half",
            provider="wan", model="wan2.7-image-pro", resolution="2K",
        ),
    ))
    before = api_context._load_project_state(project_id)
    locked_before = next(item for item in before.character_images.characters if item.view_type == "front_half")

    result = asyncio.run(asset_generation.generate_character_reference_group(project_id, "hero"))

    after = api_context._load_project_state(project_id)
    locked_after = next(item for item in after.character_images.characters if item.view_type == "front_half")
    assert locked_after.version_id == locked_before.version_id
    assert locked_after.image_path == locked_before.image_path
    assert after.character_images.reference_masters["hero"] == locked_before.version_id
    assert locked_after.group_id == result["group_id"]


def test_group_repair_references_locked_master_and_nearest_valid_neighbor(tmp_path):
    master = tmp_path / "master.png"
    front = tmp_path / "front.png"
    left = tmp_path / "left.png"
    back = tmp_path / "back.png"
    for path in (master, front, left, back):
        Image.new("RGB", (32, 32), "white").save(path)

    references = asset_generation._group_repair_reference_paths(
        str(master),
        "back_full",
        ("front_half", "front_full", "left_profile", "back_full"),
        {
            "front_half": str(master),
            "front_full": str(front),
            "left_profile": str(left),
            "back_full": str(back),
        },
    )
    assert references == [str(master), str(left)]


def test_scene_master_hard_visual_failure_retries_once_and_keeps_first_candidate(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id, location_id = _prepare_scene_project(tmp_path, monkeypatch)
    fake = FakeWanClient(tmp_path, count=1)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    calls = []

    async def validate(image, prompt, asset_type, view_type, provider, *args):
        calls.append((asset_type, view_type, prompt))
        if len(calls) == 1:
            raise MediaQualityFailure(
                "hard visual failure",
                {"visual_review": {
                    "status": "warning", "passed": False, "hard_fail": True,
                    "evidence_confidence": "high", "issues": ["场景出现人物"],
                }},
            )
        return {"status": "passed", "review_decision": "passed", "visual_review": {"status": "passed"}}

    monkeypatch.setattr(asset_generation, "_validate_generated_image", validate)
    result = asyncio.run(asset_generation.generate_scene_master(project_id, location_id))

    assert len(fake.single_calls) == 2
    assert len(calls) == 2
    assert result["quality"]["status"] == "passed"
    state = api_context._load_project_state(project_id)
    master = next(image for image in state.scene_images.scenes if image.location_id == location_id and image.view_type == "scene_center")
    assert master.version_id == result["version_id"]
    records = api_context._resolve_image_asset(
        project_id,
        api_context.RegenerateImageRequest(asset_type="scene", asset_id=location_id, view_type="scene_center", state_id=master.state_id),
        create_missing=True,
    )[2].list_versions("scene", location_id, "scene_center", master.image_prompt)
    assert any(record.get("candidate") and record.get("attempt_no") == 1 for record in records)
    assert sum(1 for record in records if record.get("active")) == 1


def test_scene_master_second_hard_failure_auto_selects_retry_and_keeps_both_candidates(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id, location_id = _prepare_scene_project(tmp_path, monkeypatch)
    fake = FakeWanClient(tmp_path, count=1)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    calls = []

    async def validate(image, prompt, asset_type, view_type, provider, *args):
        calls.append(view_type)
        score = 0.18 if len(calls) == 1 else 0.55
        raise MediaQualityFailure(
            "hard visual failure",
            {"status": "failed", "visual_review": {
                "status": "warning", "passed": False, "hard_fail": True,
                "score": score, "evidence_confidence": "high", "issues": ["场景身份偏差"],
            }},
        )

    monkeypatch.setattr(asset_generation, "_validate_generated_image", validate)
    result = asyncio.run(asset_generation.generate_scene_master(project_id, location_id))

    assert len(fake.single_calls) == 2
    assert result["quality"]["review_decision"] == "failed_after_retry"
    assert result.get("candidate_version_id")
    assert result["needs_confirmation"] is True
    assert not any(record.get("active") for record in result["history"])
    assert {record.get("attempt_no") for record in result["history"] if record.get("candidate")} == {1, 2}


def test_prop_master_hard_visual_failure_retries_once_and_keeps_first_candidate(tmp_path, monkeypatch):
    _, project_id = _client(tmp_path, monkeypatch)
    _enable_wan(monkeypatch)
    state = api_context._load_project_state(project_id)
    state.image_generation_policy = {"provider": "wan", "model": "wan2.7-image-pro", "resolution": "2K"}
    api_context._save_project_state(state)
    state = api_context._load_project_state(project_id)
    state.image_generation_policy = {"provider": "wan", "model": "wan2.7-image-pro", "resolution": "2K"}
    api_context._save_project_state(state)
    fake = FakeWanClient(tmp_path, count=1)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    calls = []

    async def validate(image, prompt, asset_type, view_type, provider, *args):
        calls.append(view_type)
        if len(calls) == 1:
            raise MediaQualityFailure(
                "hard visual failure",
                {"visual_review": {
                    "status": "warning", "passed": False, "hard_fail": True,
                    "evidence_confidence": "high", "issues": ["道具身份不一致"],
                }},
            )
        return {"status": "passed", "review_decision": "passed", "visual_review": {"status": "passed"}}

    monkeypatch.setattr(asset_generation, "_validate_generated_image", validate)
    result = asyncio.run(asset_generation.generate_prop_master(project_id, "old_key"))

    assert len(fake.single_calls) == 2
    assert calls == ["front", "front"]
    assert result["quality"]["status"] == "passed"
    state = api_context._load_project_state(project_id)
    master = next(image for image in state.prop_images.props if image.prop_id == "old_key" and image.view_type == "front")
    records = api_context._resolve_image_asset(
        project_id,
        api_context.RegenerateImageRequest(asset_type="prop", asset_id="old_key", view_type="front"),
        create_missing=True,
    )[2].list_versions("prop", "old_key", "front", master.image_prompt)
    assert any(record.get("candidate") and record.get("attempt_no") == 1 for record in records)
    assert sum(1 for record in records if record.get("active")) == 1


def test_character_reference_edit_hard_failure_retries_with_master_and_neighbor(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id = _prepare_character_project(tmp_path, monkeypatch)
    fake = FakeWanClient(tmp_path)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    asyncio.run(asset_generation.regenerate_image_asset(
        project_id,
        RegenerateImageRequest(
            asset_type="character", asset_id="hero", view_type="front_half",
            provider="wan", model="wan2.7-image-pro", resolution="2K",
        ),
    ))
    asyncio.run(asset_generation.generate_character_reference_group(project_id, "hero"))
    before = api_context._load_project_state(project_id)
    images = [item for item in before.character_images.characters if item.character_name == "hero"]
    old_back = next(item.version_id for item in images if item.view_type == "back_full")
    neighbor = next(item.version_id for item in images if item.view_type == "left_profile")
    master_path = next(item.image_path for item in images if item.view_type == "front_half")
    calls = []

    async def validate(image, prompt, asset_type, view_type, provider, *args):
        calls.append(view_type)
        if len(calls) == 1:
            raise MediaQualityFailure(
                "hard visual failure",
                {"visual_review": {
                    "status": "warning", "passed": False, "hard_fail": True,
                    "evidence_confidence": "high", "issues": ["背面仍然露脸"],
                }},
            )
        return {"status": "passed", "review_decision": "passed", "visual_review": {"status": "passed"}}

    monkeypatch.setattr(asset_generation, "_validate_generated_image", validate)
    result = asyncio.run(asset_generation.edit_character_reference(
        project_id,
        CharacterReferenceEditRequest(
            asset_type="character", asset_id="hero", view_type="back_full",
            prompt="保持背面全身，但修正衣服长度", provider="wan",
            model="wan2.7-image-pro", resolution="2K", reference_version_ids=[neighbor],
        ),
    ))

    assert len(fake.edit_calls) == 2
    assert fake.edit_calls[0][1][0] == master_path
    assert len(calls) == 2
    assert result["quality_report"]["status"] == "passed"
    after = api_context._load_project_state(project_id)
    after_images = [item for item in after.character_images.characters if item.character_name == "hero"]
    assert next(item.version_id for item in after_images if item.view_type == "back_full") != old_back
    assert {item.view_type: item.version_id for item in after_images if item.view_type != "back_full"} == {
        item.view_type: item.version_id for item in images if item.view_type != "back_full"
    }
    records = api_context._resolve_image_asset(
        project_id,
        CharacterReferenceEditRequest(
            asset_type="character", asset_id="hero", view_type="back_full",
            prompt="保持背面全身", provider="wan", model="wan2.7-image-pro", resolution="2K",
        ),
        create_missing=True,
    )[2].list_versions("character", "hero", "back_full", "")
    first_candidate = next(record for record in records if record.get("candidate") and record.get("attempt_no") == 1)
    assert first_candidate["master_version_id"] == result["master_version_id"]
    assert neighbor in first_candidate["reference_version_ids"]


def test_character_group_repairs_only_one_hard_failed_view_and_keeps_first_group_history(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id = _prepare_character_project(tmp_path, monkeypatch)
    fake = FakeWanClient(tmp_path, count=4)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    asyncio.run(asset_generation.regenerate_image_asset(
        project_id,
        RegenerateImageRequest(
            asset_type="character", asset_id="hero", view_type="front_half",
            provider="wan", model="wan2.7-image-pro", resolution="2K",
        ),
    ))
    before = api_context._load_project_state(project_id)
    master_path = next(item.image_path for item in before.character_images.characters if item.view_type == "front_half")
    calls = []

    async def validate(image, prompt, asset_type, view_type, provider, *args):
        calls.append(view_type)
        if view_type == "left_profile" and calls.count(view_type) == 1:
            raise MediaQualityFailure(
                "hard visual failure",
                {"visual_review": {
                    "status": "warning", "passed": False, "hard_fail": True,
                    "evidence_confidence": "high", "issues": ["左侧面仍然正对镜头"],
                }},
            )
        return {"status": "passed", "review_decision": "passed", "visual_review": {"status": "passed"}}

    monkeypatch.setattr(asset_generation, "_validate_generated_image", validate)
    result = asyncio.run(asset_generation.generate_character_reference_group(project_id, "hero"))

    assert len(fake.edit_calls) == 1
    assert fake.edit_calls[0][1][0] == master_path
    # left_profile is between front_full and back_full; only the nearest
    # already-valid neighbor is supplied in addition to the locked master.
    assert len(fake.edit_calls[0][1]) == 2
    assert calls.count("left_profile") == 2
    state = api_context._load_project_state(project_id)
    images = [item for item in state.character_images.characters if item.character_name == "hero"]
    assert {item.view_type for item in images} == {"front_half", "front_full", "left_profile", "back_full"}
    assert {item.group_id for item in images} == {result["group_id"]}
    left_history = api_context._resolve_image_asset(
        project_id,
        RegenerateImageRequest(asset_type="character", asset_id="hero", view_type="left_profile"),
        create_missing=True,
    )[2].list_versions("character", "hero", "left_profile", "")
    assert any(record.get("candidate") and record.get("attempt_no") == 1 for record in left_history)


def test_character_group_retry_candidate_keeps_the_original_group_id(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id = _prepare_character_project(tmp_path, monkeypatch)
    fake = FakeWanClient(tmp_path, count=4)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    asyncio.run(asset_generation.regenerate_image_asset(
        project_id,
        RegenerateImageRequest(
            asset_type="character", asset_id="hero", view_type="front_half",
            provider="wan", model="wan2.7-image-pro", resolution="2K",
        ),
    ))
    calls = []

    async def validate(image, prompt, asset_type, view_type, provider, *args):
        calls.append(view_type)
        if view_type == "left_profile":
            raise MediaQualityFailure(
                "hard visual failure",
                {"visual_review": {
                    "status": "warning", "passed": False, "hard_fail": True,
                    "evidence_confidence": "high", "issues": ["左侧面视角错误"],
                }},
            )
        return {"status": "passed", "review_decision": "passed", "visual_review": {"status": "passed"}}

    monkeypatch.setattr(asset_generation, "_validate_generated_image", validate)
    result = asyncio.run(asset_generation.generate_character_reference_group(project_id, "hero"))

    assert result["generated"] == []
    assert calls.count("left_profile") == 2
    histories = result["candidate_histories"]["left_profile"]
    group_ids = {record.get("group_id") for record in histories if record.get("candidate")}
    assert group_ids == {result["group_id"]}
    state = api_context._load_project_state(project_id)
    left = next((item for item in state.character_images.characters if item.character_name == "hero" and item.view_type == "left_profile"), None)
    assert left is None or not left.image_path


def test_scene_reference_group_second_hard_failure_auto_selects_retry(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id, location_id = _prepare_scene_project(tmp_path, monkeypatch)
    from src.services.scene_view_requirements import set_scene_view_requirements

    state = api_context._load_project_state(project_id)
    set_scene_view_requirements(state, location_id, "", ["scene_left_45", "scene_right_45", "scene_reverse"])
    api_context._save_project_state(state)
    fake = FakeWanClient(tmp_path, count=3)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    master = asyncio.run(asset_generation.generate_scene_master(project_id, location_id))
    asyncio.run(asset_generation.confirm_scene_master(project_id, location_id, master["version_id"]))
    calls = []

    async def validate(image, prompt, asset_type, view_type, provider, *args):
        if asset_type == "scene" and view_type == "scene_left_45":
            calls.append(view_type)
            raise MediaQualityFailure(
                "hard visual failure",
                {"visual_review": {
                    "status": "warning", "passed": False, "hard_fail": True,
                    "score": 0.2 if len(calls) == 1 else 0.6,
                    "evidence_confidence": "high", "issues": ["场景视角错误"],
                }},
            )
        return {"status": "passed", "review_decision": "passed", "visual_review": {"status": "passed"}}

    monkeypatch.setattr(asset_generation, "_validate_generated_image", validate)
    result = asyncio.run(asset_generation.generate_scene_reference_group(project_id, location_id))

    assert result["generated"] == []
    assert calls == ["scene_left_45", "scene_left_45"]
    assert result["retry_performed"] is True
    state = api_context._load_project_state(project_id)
    left = next(
        (item for item in state.scene_images.scenes
         if item.location_id == location_id and item.view_type == "scene_left_45"),
        None,
    )
    assert left is None or not left.image_path
    assert {record.get("attempt_no") for record in result["candidate_histories"]["scene_left_45"] if record.get("candidate")} == {1, 2}


def test_prop_reference_group_second_hard_failure_auto_selects_retry(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id = _prepare_character_project(tmp_path, monkeypatch)
    state = api_context._load_project_state(project_id)
    from src.schemas.script import PropAsset

    state.script.asset_manifest.props.append(PropAsset(prop_id="hero_prop", name="道具", description="测试道具"))
    api_context._save_project_state(state)
    fake = FakeWanClient(tmp_path, count=2)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    master = asyncio.run(asset_generation.generate_prop_master(project_id, "hero_prop"))
    asyncio.run(asset_generation.confirm_prop_master(project_id, "hero_prop", master["version_id"]))
    calls = []

    async def validate(image, prompt, asset_type, view_type, provider, *args):
        if asset_type == "prop" and view_type == "back":
            calls.append(view_type)
            raise MediaQualityFailure(
                "hard visual failure",
                {"visual_review": {
                    "status": "warning", "passed": False, "hard_fail": True,
                    "score": 0.2 if len(calls) == 1 else 0.6,
                    "evidence_confidence": "high", "issues": ["道具视角错误"],
                }},
            )
        return {"status": "passed", "review_decision": "passed", "visual_review": {"status": "passed"}}

    monkeypatch.setattr(asset_generation, "_validate_generated_image", validate)
    result = asyncio.run(asset_generation.generate_prop_reference_views(project_id, "hero_prop", ("back", "left")))

    assert result["generated"] == []
    assert calls == ["back", "back"]
    assert result["retry_performed"] is True
    state = api_context._load_project_state(project_id)
    back = next(
        (item for item in state.prop_images.props
         if item.prop_id == "hero_prop" and item.view_type == "back"),
        None,
    )
    assert back is None or not back.image_path
    assert {record.get("attempt_no") for record in result["candidate_histories"]["back"] if record.get("candidate")} == {1, 2}


def test_character_group_multiple_hard_failures_activate_usable_group_without_extra_retries(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id = _prepare_character_project(tmp_path, monkeypatch)
    fake = FakeWanClient(tmp_path, count=4)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    asyncio.run(asset_generation.regenerate_image_asset(
        project_id,
        RegenerateImageRequest(
            asset_type="character", asset_id="hero", view_type="front_half",
            provider="wan", model="wan2.7-image-pro", resolution="2K",
        ),
    ))

    async def validate(image, prompt, asset_type, view_type, provider, *args):
        if view_type in {"left_profile", "back_full"}:
            raise MediaQualityFailure(
                "hard visual failure",
                {"visual_review": {
                    "status": "warning", "passed": False, "hard_fail": True,
                    "evidence_confidence": "high", "issues": ["视角错误"],
                }},
            )
        return {"status": "passed", "review_decision": "passed", "visual_review": {"status": "passed"}}

    monkeypatch.setattr(asset_generation, "_validate_generated_image", validate)
    result = asyncio.run(asset_generation.generate_character_reference_group(project_id, "hero"))

    assert result["generated"] == []
    assert result["retry_performed"] is False
    assert fake.edit_calls == []
    state = api_context._load_project_state(project_id)
    assert {
        item.view_type for item in state.character_images.characters
        if item.character_name == "hero" and item.view_type in {"left_profile", "back_full"} and item.image_path
    } == set()
    assert {record.get("attempt_no") for record in result["candidate_histories"]["left_profile"] if record.get("candidate")} == {1}


def test_character_reference_edit_quality_failure_keeps_the_active_version(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id = _prepare_character_project(tmp_path, monkeypatch)
    fake = FakeWanClient(tmp_path)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    asyncio.run(asset_generation.regenerate_image_asset(
        project_id,
        RegenerateImageRequest(
            asset_type="character", asset_id="hero", view_type="front_half",
            provider="wan", model="wan2.7-image-pro", resolution="2K",
        ),
    ))
    group = asyncio.run(asset_generation.generate_character_reference_group(project_id, "hero"))
    before = api_context._load_project_state(project_id)
    active_before = next(item.version_id for item in before.character_images.characters if item.view_type == "back_full")

    async def reject(*_args, **_kwargs):
        raise MediaQualityFailure("bad edit", {"status": "failed"})

    monkeypatch.setattr(asset_generation, "_validate_generated_image", reject)

    with pytest.raises(MediaQualityFailure):
        asyncio.run(asset_generation.edit_character_reference(
            project_id,
            CharacterReferenceEditRequest(
                asset_type="character", asset_id="hero", view_type="back_full",
                prompt="make the coat longer", provider="wan", model="wan2.7-image-pro", resolution="2K",
                reference_version_ids=[group["master_version_id"]],
            ),
        ))

    after = api_context._load_project_state(project_id)
    assert next(item.version_id for item in after.character_images.characters if item.view_type == "back_full") == active_before


def test_incomplete_wan_group_does_not_activate_partial_views(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id = _prepare_character_project(tmp_path, monkeypatch)
    fake = FakeWanClient(tmp_path, count=3)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    asyncio.run(asset_generation.regenerate_image_asset(
        project_id,
        RegenerateImageRequest(
            asset_type="character", asset_id="hero", view_type="front_half",
            provider="wan", model="wan2.7-image-pro", resolution="2K",
        ),
    ))

    with pytest.raises(HTTPException):
        asyncio.run(asset_generation.generate_character_reference_group(project_id, "hero"))

    state = api_context._load_project_state(project_id)
    images = state.character_images.characters if state.character_images else []
    assert not any(image.generation_mode == "reference_group" for image in images)


def test_wan_sequence_group_uses_locked_master_when_provider_excludes_it(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id = _prepare_character_project(tmp_path, monkeypatch)
    fake = FakeWanClient(tmp_path, count=3, sequence_reference_counted=True)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)

    asyncio.run(asset_generation.regenerate_image_asset(
        project_id,
        RegenerateImageRequest(
            asset_type="character", asset_id="hero", view_type="front_half",
            provider="wan", model="wan2.7-image-pro", resolution="2K",
        ),
    ))
    result = asyncio.run(asset_generation.generate_character_reference_group(project_id, "hero"))

    state = api_context._load_project_state(project_id)
    images = [image for image in state.character_images.characters if image.character_name == "hero"]
    assert {image.view_type for image in images} == {"front_half", "front_full", "left_profile", "back_full"}
    assert {image.group_id for image in images} == {result["group_id"]}


def test_wan_character_batch_creation_uses_self_healing_reference_group(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    client, project_id = _prepare_character_project(tmp_path, monkeypatch)

    response = client.post(
        f"/api/projects/{project_id}/jobs",
        json={
            "job_type": "asset.generate",
            "target_type": "character",
            "target_id": "hero",
            "payload": {
                "asset_type": "character",
                "asset_id": "hero",
                "provider": "wan",
                "model": "wan2.7-image-pro",
                "resolution": "2K",
            },
        },
    )

    assert response.status_code == 202
    jobs = response.json()["jobs"]
    assert [(job["job_type"], job["target_id"]) for job in jobs] == [("character.reference.group", "hero")]


def test_wan_character_batch_promotes_single_resolution_to_group_resolution(tmp_path, monkeypatch):
    """A 1K project default must not submit an invalid 1K group request."""
    _enable_wan(monkeypatch)
    client, project_id = _prepare_character_project(tmp_path, monkeypatch)

    response = client.post(
        f"/api/projects/{project_id}/jobs",
        json={
            "job_type": "asset.generate",
            "target_type": "character",
            "target_id": "hero",
            "payload": {
                "asset_type": "character",
                "asset_id": "hero",
                "provider": "wan",
                "model": "wan2.7-image-pro",
                "resolution": "1K",
            },
        },
    )

    assert response.status_code == 202
    job = response.json()["jobs"][0]
    assert job["job_type"] == "character.reference.group"
    assert job["payload"]["resolution"] == "2K"


def test_wan_character_batch_creation_uses_reference_group_after_master(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    client, project_id = _prepare_character_project(tmp_path, monkeypatch)
    fake = FakeWanClient(tmp_path, count=3)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    asyncio.run(asset_generation.regenerate_image_asset(
        project_id,
        RegenerateImageRequest(
            asset_type="character", asset_id="hero", view_type="front_half",
            provider="wan", model="wan2.7-image-pro", resolution="2K",
        ),
    ))

    response = client.post(
        f"/api/projects/{project_id}/jobs",
        json={
            "job_type": "asset.generate",
            "target_type": "character",
            "target_id": "hero",
            "payload": {
                "asset_type": "character",
                "asset_id": "hero",
                "provider": "wan",
                "model": "wan2.7-image-pro",
                "resolution": "2K",
            },
        },
    )

    assert response.status_code == 202
    jobs = response.json()["jobs"]
    assert [job["job_type"] for job in jobs] == ["character.reference.group"]


def test_direct_wan_reference_group_request_generates_master_when_missing(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    client, project_id = _prepare_character_project(tmp_path, monkeypatch)

    response = client.post(
        f"/api/projects/{project_id}/jobs",
        json={
            "job_type": "character.reference.group",
            "target_type": "character",
            "target_id": "hero",
            "payload": {
                "asset_id": "hero",
                "provider": "wan",
                "model": "wan2.7-image-pro",
                "resolution": "2K",
            },
        },
    )

    assert response.status_code == 202
    jobs = response.json()["jobs"]
    assert [(job["job_type"], job["target_id"]) for job in jobs] == [("character.reference.group", "hero")]


def test_wan_character_front_half_master_uses_single_asset_job(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    client, project_id = _prepare_character_project(tmp_path, monkeypatch)

    response = client.post(
        f"/api/projects/{project_id}/jobs",
        json={
            "job_type": "asset.generate",
            "target_type": "character",
            "target_id": "hero",
            "payload": {
                "asset_type": "character",
                "asset_id": "hero",
                "view_type": "front_half",
                "provider": "wan",
                "model": "wan2.7-image-pro",
                "resolution": "2K",
            },
        },
    )

    assert response.status_code == 202
    jobs = response.json()["jobs"]
    assert [(item["job_type"], item["target_id"]) for item in jobs] == [("asset.generate", "hero:front_half")]


def test_reference_group_generates_missing_front_half_master(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id = _prepare_character_project(tmp_path, monkeypatch)
    fake = FakeWanClient(tmp_path)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)

    result = asyncio.run(asset_generation.generate_character_reference_group(project_id, "hero"))

    assert result["master_version_id"]
    assert len(fake.sequence_calls) == 1
    state = api_context._load_project_state(project_id)
    images = [image for image in state.character_images.characters if image.character_name == "hero"]
    assert {image.view_type for image in images} == {"front_half", "front_full", "left_profile", "back_full"}


def test_scene_reference_group_requires_confirmed_master(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id, location_id = _prepare_scene_project(tmp_path, monkeypatch)
    fake = FakeWanClient(tmp_path)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)

    with pytest.raises(HTTPException, match="确认场景中心母版"):
        asyncio.run(asset_generation.generate_scene_reference_group(project_id, location_id))

    assert fake.sequence_calls == []


def test_scene_master_confirm_then_group_uses_master_reference(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id, location_id = _prepare_scene_project(tmp_path, monkeypatch)
    from src.services.scene_view_requirements import set_scene_view_requirements

    state = api_context._load_project_state(project_id)
    set_scene_view_requirements(state, location_id, "", ["scene_reverse"])
    api_context._save_project_state(state)
    fake = FakeWanClient(tmp_path, count=1)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)

    master = asyncio.run(asset_generation.generate_scene_master(project_id, location_id))
    state = api_context._load_project_state(project_id)
    master_image = next(image for image in state.scene_images.scenes if image.location_id == location_id and image.view_type == "scene_center")
    assert master_image.is_master is True
    assert master_image.needs_confirmation is True

    confirmed = asyncio.run(asset_generation.confirm_scene_master(project_id, location_id, master["version_id"]))
    assert confirmed["confirmed"] is True
    group = asyncio.run(asset_generation.generate_scene_reference_group(project_id, location_id))

    assert fake.sequence_calls[0][1] == [master["image_path"]]
    assert group["master_version_id"] == master["version_id"]
    state = api_context._load_project_state(project_id)
    group_images = [image for image in state.scene_images.scenes if image.location_id == location_id and image.group_id == group["group_id"]]
    assert group_images
    assert all(image.master_version_id == master["version_id"] for image in group_images)


def test_scene_reference_group_persists_view_specific_display_prompts(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id, location_id = _prepare_scene_project(tmp_path, monkeypatch)
    from src.services.scene_view_requirements import set_scene_view_requirements

    state = api_context._load_project_state(project_id)
    set_scene_view_requirements(state, location_id, "", ["scene_left_45", "scene_right_45"])
    api_context._save_project_state(state)
    fake = FakeWanClient(tmp_path, count=2)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)

    master = asyncio.run(asset_generation.generate_scene_master(project_id, location_id))
    asyncio.run(asset_generation.confirm_scene_master(project_id, location_id, master["version_id"]))
    group = asyncio.run(asset_generation.generate_scene_reference_group(project_id, location_id))

    state = api_context._load_project_state(project_id)
    images = [
        image for image in state.scene_images.scenes
        if image.location_id == location_id and image.group_id == group["group_id"]
    ]
    by_view = {image.view_type: image for image in images}
    assert "左前方45度" in by_view["scene_left_45"].image_prompt
    assert "右前方45度" in by_view["scene_right_45"].image_prompt
    assert by_view["scene_left_45"].image_prompt != by_view["scene_right_45"].image_prompt


def test_wan_scene_master_merges_negative_constraints_into_provider_prompt(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id, location_id = _prepare_scene_project(tmp_path, monkeypatch)
    fake = FakeWanClient(tmp_path, count=1)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)

    asyncio.run(asset_generation.generate_scene_master(project_id, location_id))

    request = fake.single_calls[0]
    assert request["negative_prompt"] == ""
    assert "避免出现：" in request["prompt"]
    assert "人物" in request["prompt"]


def test_wan_scene_master_uses_official_landscape_dimensions(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id, location_id = _prepare_scene_project(tmp_path, monkeypatch)
    fake = FakeWanClient(tmp_path, count=1)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)

    asyncio.run(asset_generation.generate_scene_master(project_id, location_id))

    request = fake.single_calls[0]
    assert (request["width"], request["height"]) == (2688, 1536)


def test_scene_master_regeneration_marks_same_state_derivatives_stale(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id, location_id = _prepare_scene_project(tmp_path, monkeypatch)
    from src.services.scene_view_requirements import set_scene_view_requirements

    state = api_context._load_project_state(project_id)
    set_scene_view_requirements(state, location_id, "", ["scene_reverse"])
    api_context._save_project_state(state)
    fake = FakeWanClient(tmp_path, count=1)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)

    first_master = asyncio.run(asset_generation.generate_scene_master(project_id, location_id))
    asyncio.run(asset_generation.confirm_scene_master(project_id, location_id, first_master["version_id"]))
    group = asyncio.run(asset_generation.generate_scene_reference_group(project_id, location_id))
    state = api_context._load_project_state(project_id)
    reverse = next(image for image in state.scene_images.scenes if image.location_id == location_id and image.view_type == "scene_reverse")
    assert reverse.is_stale is False
    assert reverse.master_version_id == first_master["version_id"]

    second_master = asyncio.run(asset_generation.generate_scene_master(project_id, location_id))
    state = api_context._load_project_state(project_id)
    reverse_after = next(image for image in state.scene_images.scenes if image.location_id == location_id and image.view_type == "scene_reverse")
    master_after = next(image for image in state.scene_images.scenes if image.location_id == location_id and image.view_type == "scene_center")

    assert second_master["version_id"] != first_master["version_id"]
    assert group["master_version_id"] == first_master["version_id"]
    assert reverse_after.is_stale is True
    assert master_after.is_stale is False


def test_scene_reference_edit_prepends_confirmed_master(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    client, project_id, location_id = _prepare_scene_project(tmp_path, monkeypatch)
    from src.services.scene_view_requirements import set_scene_view_requirements

    state = api_context._load_project_state(project_id)
    set_scene_view_requirements(state, location_id, "", ["scene_reverse"])
    api_context._save_project_state(state)
    fake = FakeWanClient(tmp_path, count=1)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)

    master = asyncio.run(asset_generation.generate_scene_master(project_id, location_id))
    asyncio.run(asset_generation.confirm_scene_master(project_id, location_id, master["version_id"]))
    group = asyncio.run(asset_generation.generate_scene_reference_group(project_id, location_id))
    state = api_context._load_project_state(project_id)
    before_center = next(image.version_id for image in state.scene_images.scenes if image.location_id == location_id and image.view_type == "scene_center")
    before_reverse = next(image.version_id for image in state.scene_images.scenes if image.location_id == location_id and image.view_type == "scene_reverse")

    response = client.post(
        f"/api/projects/{project_id}/scenes/{location_id}/states/{group['state_id']}/views/scene_reverse/edit",
        json={
            "prompt": "增加墙面水痕",
            "provider": "wan",
            "model": "wan2.7-image-pro",
            "resolution": "2K",
        },
    )

    assert response.status_code == 200
    assert fake.edit_calls[0][1][0] == master["image_path"]
    after = api_context._load_project_state(project_id)
    center = next(image for image in after.scene_images.scenes if image.location_id == location_id and image.view_type == "scene_center")
    reverse = next(image for image in after.scene_images.scenes if image.location_id == location_id and image.view_type == "scene_reverse")
    assert center.version_id == before_center
    assert reverse.version_id != before_reverse
    assert reverse.generation_mode == "reference_edit"
    assert reverse.master_version_id == master["version_id"]
    assert reverse.is_stale is False


def test_scene_reference_edit_quality_failure_keeps_the_active_version(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _client_obj, project_id, location_id = _prepare_scene_project(tmp_path, monkeypatch)
    from src.services.scene_view_requirements import set_scene_view_requirements

    state = api_context._load_project_state(project_id)
    set_scene_view_requirements(state, location_id, "", ["scene_reverse"])
    api_context._save_project_state(state)
    fake = FakeWanClient(tmp_path, count=1)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    master = asyncio.run(asset_generation.generate_scene_master(project_id, location_id))
    asyncio.run(asset_generation.confirm_scene_master(project_id, location_id, master["version_id"]))
    group = asyncio.run(asset_generation.generate_scene_reference_group(project_id, location_id))
    before = api_context._load_project_state(project_id)
    active_before = next(
        item.version_id for item in before.scene_images.scenes
        if item.location_id == location_id and item.view_type == "scene_reverse"
    )

    async def reject(*_args, **_kwargs):
        raise MediaQualityFailure("bad edit", {"status": "failed"})

    monkeypatch.setattr(asset_generation, "_validate_generated_image", reject)

    with pytest.raises(MediaQualityFailure):
        asyncio.run(asset_generation.edit_scene_reference_view(
            project_id,
            location_id,
            group["state_id"],
            "scene_reverse",
            "增加墙面水痕",
            [],
            {"provider": "wan", "model": "wan2.7-image-pro", "resolution": "2K"},
        ))

    after = api_context._load_project_state(project_id)
    assert next(
        item.version_id for item in after.scene_images.scenes
        if item.location_id == location_id and item.view_type == "scene_reverse"
    ) == active_before


def test_scene_reference_edit_merges_anime_style_negative_constraints(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _client_obj, project_id, location_id = _prepare_scene_project(tmp_path, monkeypatch)
    from src.services.scene_view_requirements import set_scene_view_requirements

    state = api_context._load_project_state(project_id)
    state.creative_profile.art_style = "动漫风"
    state.creative_profile.visual_style_id = "cinematic_cel"
    set_scene_view_requirements(state, location_id, "", ["scene_reverse"])
    api_context._save_project_state(state)
    fake = FakeWanClient(tmp_path, count=1)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)

    master = asyncio.run(asset_generation.generate_scene_master(project_id, location_id))
    asyncio.run(asset_generation.confirm_scene_master(project_id, location_id, master["version_id"]))
    state = api_context._load_project_state(project_id)
    state_id = state.script.asset_manifest.locations[0].default_state_id
    asyncio.run(
        asset_generation.edit_scene_reference_view(
            project_id,
            location_id,
            state_id,
            "scene_reverse",
            "增加墙面水痕",
            [],
            {"provider": "wan", "model": "wan2.7-image-pro", "resolution": "2K"},
        )
    )

    edit_prompt = fake.edit_calls[-1][0]
    assert "二维背景绘制" in edit_prompt
    assert "照片级实拍" in edit_prompt


def test_wan_scene_batch_creation_uses_master_before_reference_group(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    client, project_id, location_id = _prepare_scene_project(tmp_path, monkeypatch)
    requirement = client.put(
        f"/api/projects/{project_id}/scenes/{location_id}/view-requirements",
        json={"views": ["scene_reverse"]},
    )
    assert requirement.status_code == 200
    assert requirement.json()["views"] == ["scene_center", "scene_reverse"]

    first = client.post(
        f"/api/projects/{project_id}/jobs",
        json={
            "job_type": "asset.generate",
            "target_type": "scene",
            "target_id": location_id,
            "payload": {
                "asset_type": "scene",
                "asset_id": location_id,
                "provider": "wan",
                "model": "wan2.7-image-pro",
                "resolution": "2K",
            },
        },
    )

    assert first.status_code == 202
    assert [(job["job_type"], job["target_id"]) for job in first.json()["jobs"]] == [
        ("asset.generate", f"{location_id}:scene_center")
    ]

    fake = FakeWanClient(tmp_path, count=2)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    master = asyncio.run(asset_generation.generate_scene_master(project_id, location_id))
    asyncio.run(asset_generation.confirm_scene_master(project_id, location_id, master["version_id"]))

    second = client.post(
        f"/api/projects/{project_id}/jobs",
        json={
            "job_type": "asset.generate",
            "target_type": "scene",
            "target_id": location_id,
            "payload": {
                "asset_type": "scene",
                "asset_id": location_id,
                "provider": "wan",
                "model": "wan2.7-image-pro",
                "resolution": "2K",
            },
        },
    )

    assert second.status_code == 202
    assert [(job["job_type"], job["target_id"]) for job in second.json()["jobs"]] == [
        ("scene.reference.group", location_id)
    ]


def test_character_reference_edit_warning_keeps_active_version(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id = _prepare_character_project(tmp_path, monkeypatch)
    fake = FakeWanClient(tmp_path)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    asyncio.run(asset_generation.regenerate_image_asset(
        project_id,
        RegenerateImageRequest(
            asset_type="character", asset_id="hero", view_type="front_half",
            provider="wan", model="wan2.7-image-pro", resolution="2K",
        ),
    ))
    group = asyncio.run(asset_generation.generate_character_reference_group(project_id, "hero"))
    before = api_context._load_project_state(project_id)
    active_before = next(item.version_id for item in before.character_images.characters if item.view_type == "back_full")

    async def warn(*_args, **_kwargs):
        return {
            "status": "warning", "passed": False, "review_verdict": "warning",
            "review_decision": "uncertain", "automatic_reference_allowed": False,
            "visual_review": {"status": "warning", "passed": False, "issues": ["证据不足"]},
        }

    monkeypatch.setattr(asset_generation, "_validate_generated_image", warn)
    result = asyncio.run(asset_generation.edit_character_reference(
        project_id,
        CharacterReferenceEditRequest(
            asset_type="character", asset_id="hero", view_type="back_full",
            prompt="make the coat longer", provider="wan", model="wan2.7-image-pro", resolution="2K",
            reference_version_ids=[group["master_version_id"]],
        ),
    ))

    after = api_context._load_project_state(project_id)
    active_after = next(item.version_id for item in after.character_images.characters if item.view_type == "back_full")
    assert active_after == active_before
    assert result.get("candidate_version_id")
    history = result.get("history") or []
    assert any(item.get("candidate") and item.get("attempt_no") == 1 for item in history)


def test_scene_reference_edit_warning_keeps_active_version(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _client_obj, project_id, location_id = _prepare_scene_project(tmp_path, monkeypatch)
    from src.services.scene_view_requirements import set_scene_view_requirements

    state = api_context._load_project_state(project_id)
    set_scene_view_requirements(state, location_id, "", ["scene_reverse"])
    api_context._save_project_state(state)
    fake = FakeWanClient(tmp_path, count=1)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    master = asyncio.run(asset_generation.generate_scene_master(project_id, location_id))
    asyncio.run(asset_generation.confirm_scene_master(project_id, location_id, master["version_id"]))
    group = asyncio.run(asset_generation.generate_scene_reference_group(project_id, location_id))
    before = api_context._load_project_state(project_id)
    active_before = next(
        item.version_id for item in before.scene_images.scenes
        if item.location_id == location_id and item.view_type == "scene_reverse"
    )

    async def warn(*_args, **_kwargs):
        return {
            "status": "warning", "passed": False, "review_verdict": "warning",
            "review_decision": "uncertain", "automatic_reference_allowed": False,
            "visual_review": {"status": "warning", "passed": False, "issues": ["证据不足"]},
        }

    monkeypatch.setattr(asset_generation, "_validate_generated_image", warn)
    result = asyncio.run(asset_generation.edit_scene_reference_view(
        project_id,
        location_id,
        group["state_id"],
        "scene_reverse",
        "增加墙面水痕",
        [],
        {"provider": "wan", "model": "wan2.7-image-pro", "resolution": "2K"},
    ))

    after = api_context._load_project_state(project_id)
    active_after = next(
        item.version_id for item in after.scene_images.scenes
        if item.location_id == location_id and item.view_type == "scene_reverse"
    )
    assert active_after == active_before
    assert result.get("candidate_version_id")
    history = result.get("history") or []
    assert any(item.get("candidate") and item.get("attempt_no") == 1 for item in history)


def test_scene_reference_group_warning_keeps_whole_group_as_candidates(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id, location_id = _prepare_scene_project(tmp_path, monkeypatch)
    from src.services.scene_view_requirements import set_scene_view_requirements

    state = api_context._load_project_state(project_id)
    set_scene_view_requirements(state, location_id, "", ["scene_left_45", "scene_right_45", "scene_reverse"])
    api_context._save_project_state(state)
    fake = FakeWanClient(tmp_path, count=3)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    master = asyncio.run(asset_generation.generate_scene_master(project_id, location_id))
    asyncio.run(asset_generation.confirm_scene_master(project_id, location_id, master["version_id"]))

    async def warn(image, prompt, asset_type, view_type, provider, *args):
        if view_type == "scene_left_45":
            return {
                "status": "warning", "passed": False, "review_verdict": "warning",
                "review_decision": "uncertain", "automatic_reference_allowed": False,
                "visual_review": {"status": "warning", "passed": False, "issues": ["证据不足"]},
            }
        return {
            "status": "passed", "passed": True, "review_decision": "passed",
            "review_verdict": "passed", "visual_review": {"status": "passed"},
        }

    monkeypatch.setattr(asset_generation, "_validate_generated_image", warn)
    result = asyncio.run(asset_generation.generate_scene_reference_group(project_id, location_id))

    assert result["generated"] == []
    assert set(result["candidate_histories"].keys()) == {"scene_left_45", "scene_right_45", "scene_reverse"}
    for view in result["candidate_histories"]:
        assert any(item.get("candidate") for item in result["candidate_histories"][view])
    state_after = api_context._load_project_state(project_id)
    left = next(
        (item for item in state_after.scene_images.scenes
         if item.location_id == location_id and item.view_type == "scene_left_45"),
        None,
    )
    assert left is None or not left.image_path


def test_prop_reference_single_failed_view_does_not_raise_stop_iteration(tmp_path, monkeypatch):
    """A single derived view has no neighbor candidate; failed retry must still return a report."""
    _enable_wan(monkeypatch)
    _, project_id = _prepare_character_project(tmp_path, monkeypatch)
    state = api_context._load_project_state(project_id)
    from src.schemas.script import PropAsset

    state.script.asset_manifest.props.append(PropAsset(prop_id="single_prop", name="道具", description="测试道具"))
    api_context._save_project_state(state)
    fake = FakeWanClient(tmp_path, count=1)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    master = asyncio.run(asset_generation.generate_prop_master(project_id, "single_prop"))
    asyncio.run(asset_generation.confirm_prop_master(project_id, "single_prop", master["version_id"]))

    async def reject(*_args, **_kwargs):
        raise MediaQualityFailure(
            "hard visual failure",
            {"visual_review": {
                "status": "warning", "passed": False, "hard_fail": True,
                "evidence_confidence": "high", "issues": ["底视图错误"],
            }},
        )

    monkeypatch.setattr(asset_generation, "_validate_generated_image", reject)
    result = asyncio.run(asset_generation.generate_prop_reference_views(project_id, "single_prop", ("bottom",)))

    assert result["generated"] == []
    assert result["retry_performed"] is True
    assert {record.get("attempt_no") for record in result["candidate_histories"]["bottom"] if record.get("candidate")} == {1, 2}
    assert fake.edit_calls
