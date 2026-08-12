import json
from pathlib import Path

import asyncio
import pytest
from PIL import Image, ImageDraw

from tests.test_asset_generation_api import _client
from src.api import context as api_context
from src.api_clients.image_base import ImageGenResult, ImageGroupResult
from src.services import asset_generation


class FakeWanClient:
    provider_name = "wan"

    def __init__(self, root: Path, count: int = 2):
        self.root = root
        self.count = count
        self.sequence_calls = []
        self.edit_calls = []
        self._number = 0

    def _image(self, label: str) -> ImageGenResult:
        self._number += 1
        path = self.root / f"{label}-{self._number}.png"
        image = Image.new("RGB", (1024, 1024), (80, 120, 160))
        ImageDraw.Draw(image).line((0, 0, 1023, 1023), fill=(240, 240, 240), width=8)
        image.save(path)
        return ImageGenResult(True, local_path=str(path), width=1024, height=1024, provider_name="wan")

    async def generate_image(self, **_kwargs):
        return self._image("master")

    async def generate_sequence(self, prompt, reference_paths, n, resolution):
        self.sequence_calls.append((prompt, list(reference_paths), n, resolution))
        return ImageGroupResult(True, [self._image("group") for _ in range(self.count)], provider_name="wan")

    async def edit_image(self, prompt, reference_paths, resolution):
        self.edit_calls.append((prompt, list(reference_paths), resolution))
        return self._image("edit")


def _enable_wan_project(project_id: str, monkeypatch):
    import src.config as config

    monkeypatch.setattr(config, "HAPPYHORSE_API_KEY", "test-key")
    monkeypatch.setattr(config, "HAPPYHORSE_BASE_URL", "https://dashscope.aliyuncs.com")
    monkeypatch.setattr(config, "REVIEW_LLM_ENABLED", False)
    state = api_context._load_project_state(project_id)
    state.image_generation_policy = {
        "provider": "wan",
        "model": "wan2.7-image-pro",
        "resolution": "2K",
    }
    api_context._save_project_state(state)


def _prop_images(state_json: str):
    return json.loads(state_json)["prop_images"]["props"]


def test_prop_master_generation_requires_confirmation(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)

    response = client.post(
        f"/api/projects/{project_id}/props/old_key/master/generate",
        json={},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["version_id"]
    assert payload["needs_confirmation"] is True
    assert Path(payload["image_path"]).is_file()
    images = _prop_images(payload["state_json"])
    master = next(item for item in images if item["prop_id"] == "old_key" and item["view_type"] == "front")
    assert master["is_master"] is True
    assert master["needs_confirmation"] is True
    assert master["generation_mode"] == "master"
    assert master["master_version_id"] == payload["version_id"]


def test_prop_master_confirmation_accepts_only_active_version(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)
    generated = client.post(
        f"/api/projects/{project_id}/props/old_key/master/generate",
        json={},
    ).json()

    stale = client.post(
        f"/api/projects/{project_id}/props/old_key/master/confirm",
        json={"version_id": "not-the-active-version"},
    )
    confirmed = client.post(
        f"/api/projects/{project_id}/props/old_key/master/confirm",
        json={"version_id": generated["version_id"]},
    )

    assert stale.status_code == 409
    assert confirmed.status_code == 200
    master = next(
        item for item in _prop_images(confirmed.json()["state_json"])
        if item["prop_id"] == "old_key" and item["view_type"] == "front"
    )
    assert master["needs_confirmation"] is False
    assert master["is_master"] is True


def test_prop_derived_view_requires_confirmed_master(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)

    blocked = client.post(
        f"/api/projects/{project_id}/props/old_key/views/back/generate",
        json={},
    )
    generated_master = client.post(
        f"/api/projects/{project_id}/props/old_key/master/generate",
        json={},
    ).json()
    client.post(
        f"/api/projects/{project_id}/props/old_key/master/confirm",
        json={"version_id": generated_master["version_id"]},
    )
    generated_back = client.post(
        f"/api/projects/{project_id}/props/old_key/views/back/generate",
        json={},
    )

    assert blocked.status_code == 409
    assert generated_back.status_code == 200
    image = next(
        item for item in _prop_images(generated_back.json()["state_json"])
        if item["prop_id"] == "old_key" and item["view_type"] == "back"
    )
    assert image["generation_mode"] == "single"
    assert Path(image["image_path"]).is_file()


def test_wan_prop_group_uses_confirmed_master_reference(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)
    _enable_wan_project(project_id, monkeypatch)
    fake = FakeWanClient(tmp_path, count=2)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)

    master = client.post(f"/api/projects/{project_id}/props/old_key/master/generate", json={}).json()
    client.post(
        f"/api/projects/{project_id}/props/old_key/master/confirm",
        json={"version_id": master["version_id"]},
    )
    response = client.post(
        f"/api/projects/{project_id}/props/old_key/views/generate",
        json={"views": ["back", "left"], "provider": "wan", "model": "wan2.7-image-pro", "resolution": "2K"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["generation_mode"] == "reference_group"
    assert fake.sequence_calls[0][1] == [master["image_path"]]
    assert fake.sequence_calls[0][2:] == (2, "2K")
    images = _prop_images(payload["state_json"])
    derived = [item for item in images if item["prop_id"] == "old_key" and item["view_type"] in {"back", "left"}]
    assert {item["generation_mode"] for item in derived} == {"reference_group"}
    assert all(item["master_version_id"] == master["version_id"] for item in derived)
    assert all(item["reference_version_ids"] == [master["version_id"]] for item in derived)


def test_wan_prop_reference_prompt_keeps_project_anime_style(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)
    state = api_context._load_project_state(project_id)
    state.creative_profile.art_style = "动漫风"
    state.creative_profile.visual_style_id = "cinematic_cel"

    prompt = asset_generation._prop_reference_prompt(state, "old_key", ("back", "left"))

    assert "二维动画设定稿媒介" in prompt
    assert "产品实拍" in prompt
    assert "项目风格执行层" in prompt
    assert "产品摄影光线" not in prompt

    state.creative_profile.art_style = "写实"
    state.creative_profile.visual_style_id = "live_action_cinematic"
    live_prompt = asset_generation._prop_reference_prompt(state, "old_key", ("back", "left"))
    assert "电影级实物道具" in live_prompt
    assert "插画道具" in live_prompt
    assert "产品摄影光线" in live_prompt


def test_wan_prop_single_view_uses_reference_edit(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)
    _enable_wan_project(project_id, monkeypatch)
    fake = FakeWanClient(tmp_path, count=1)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)

    master = client.post(f"/api/projects/{project_id}/props/old_key/master/generate", json={}).json()
    client.post(
        f"/api/projects/{project_id}/props/old_key/master/confirm",
        json={"version_id": master["version_id"]},
    )
    response = client.post(
        f"/api/projects/{project_id}/props/old_key/views/back/generate",
        json={"provider": "wan", "model": "wan2.7-image-pro", "resolution": "2K"},
    )

    assert response.status_code == 200
    assert fake.edit_calls[0][1] == [master["image_path"]]
    image = next(
        item for item in _prop_images(response.json()["state_json"])
        if item["prop_id"] == "old_key" and item["view_type"] == "back"
    )
    assert image["generation_mode"] == "reference_edit"
    assert image["derived_from_version_id"] == master["version_id"]


def test_short_wan_prop_group_does_not_activate_partial_views(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)
    _enable_wan_project(project_id, monkeypatch)
    fake = FakeWanClient(tmp_path, count=1)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)

    master = client.post(f"/api/projects/{project_id}/props/old_key/master/generate", json={}).json()
    client.post(
        f"/api/projects/{project_id}/props/old_key/master/confirm",
        json={"version_id": master["version_id"]},
    )
    response = client.post(
        f"/api/projects/{project_id}/props/old_key/views/generate",
        json={"views": ["back", "left"], "provider": "wan", "model": "wan2.7-image-pro", "resolution": "2K"},
    )

    assert response.status_code == 502
    state = api_context._load_project_state(project_id)
    images = state.prop_images.props if state.prop_images else []
    assert not any(image.view_type in {"back", "left"} and image.image_path for image in images)


def test_wan_prop_group_does_not_activate_when_any_image_fails_technical_quality(tmp_path, monkeypatch):
    class InvalidSecondImageWan(FakeWanClient):
        def _image(self, label: str) -> ImageGenResult:
            result = super()._image(label)
            if label == "group" and self._number == 3:
                Image.new("RGB", (1024, 1024), "white").save(result.local_path)
            return result

    client, project_id = _client(tmp_path, monkeypatch)
    _enable_wan_project(project_id, monkeypatch)
    fake = InvalidSecondImageWan(tmp_path, count=2)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)

    master = client.post(f"/api/projects/{project_id}/props/old_key/master/generate", json={}).json()
    client.post(f"/api/projects/{project_id}/props/old_key/master/confirm", json={"version_id": master["version_id"]})
    response = client.post(
        f"/api/projects/{project_id}/props/old_key/views/generate",
        json={"views": ["back", "left"], "provider": "wan", "model": "wan2.7-image-pro", "resolution": "2K"},
    )

    assert response.status_code == 502
    state = api_context._load_project_state(project_id)
    assert not any(image.view_type in {"back", "left"} and image.image_path for image in (state.prop_images.props or []))


def test_wan_prop_batch_jobs_use_master_then_reference_group(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)
    _enable_wan_project(project_id, monkeypatch)
    requirement = client.put(
        f"/api/projects/{project_id}/props/old_key/view-requirements",
        json={"views": ["back", "left"]},
    )
    assert requirement.status_code == 200

    first = client.post(
        f"/api/projects/{project_id}/jobs",
        json={
            "job_type": "asset.generate",
            "target_type": "prop",
            "target_id": "old_key",
            "payload": {
                "asset_type": "prop",
                "asset_id": "old_key",
                "provider": "wan",
                "model": "wan2.7-image-pro",
                "resolution": "2K",
            },
        },
    )

    assert first.status_code == 202
    assert [(job["job_type"], job["target_id"]) for job in first.json()["jobs"]] == [
        ("asset.generate", "old_key:front")
    ]

    fake = FakeWanClient(tmp_path, count=2)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    master = asyncio.run(asset_generation.generate_prop_master(project_id, "old_key"))
    asyncio.run(asset_generation.confirm_prop_master(project_id, "old_key", master["version_id"]))

    second = client.post(
        f"/api/projects/{project_id}/jobs",
        json={
            "job_type": "asset.generate",
            "target_type": "prop",
            "target_id": "old_key",
            "payload": {
                "asset_type": "prop",
                "asset_id": "old_key",
                "provider": "wan",
                "model": "wan2.7-image-pro",
                "resolution": "2K",
            },
        },
    )

    assert second.status_code == 202
    assert [(job["job_type"], job["target_id"]) for job in second.json()["jobs"]] == [
        ("prop.reference.group", "old_key")
    ]
    assert second.json()["jobs"][0]["provider"] == "wan"
