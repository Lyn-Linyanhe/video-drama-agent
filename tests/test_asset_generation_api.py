import json
import asyncio
import inspect
from pathlib import Path
from urllib.parse import quote

from fastapi.testclient import TestClient

import api_server
from src.api import context as api_context
import src.api_clients.image_mock as image_mock_module
import src.utils.project_store as project_store_module
from src.api_clients.image_mock import MockImageClient
from src.graph.state import DramaState
from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.script import (
    LocationAsset,
    PropAsset,
    Scene,
    ScriptBreakdown,
    ShotVisualBinding,
    VisualCharacterAsset,
    VisualAssetManifest,
)
from src.schemas.visual import VisualDesign
from src.schemas.project import CreativeProfile
from src.utils.project_store import ProjectStore
from src.utils.visual_asset_manifest import normalize_scene_assets, stable_location_id


def test_single_image_generation_enables_quality_checks_by_default():
    from src.services.asset_generation import regenerate_image_asset

    assert inspect.signature(regenerate_image_asset).parameters["quality_check"].default is True


def _script() -> ScriptBreakdown:
    return ScriptBreakdown(
        title="资产 API",
        genre="悬疑",
        logline="钥匙打开了地下室。",
        duration_sec=10,
        narration_style="无旁白",
        scenes=[
            Scene(scene_id=1, duration_sec=5, location="地下室", characters=[], action_description="铁门和旧柜子。", camera_direction="全景", emotion_tone="压抑"),
            Scene(scene_id=2, duration_sec=5, location=" 地下室 ", characters=[], action_description="墙面水痕和积水。", camera_direction="中景", emotion_tone="紧张"),
        ],
        asset_manifest=VisualAssetManifest(
            locations=[
                LocationAsset(location_id="basement_a", canonical_name="地下室", environment_description="铁门和旧柜子。", representative_scene_id=1, shot_ids=[1]),
                LocationAsset(location_id="basement_b", canonical_name=" 地下室 ", environment_description="墙面水痕和积水。", representative_scene_id=2, shot_ids=[2]),
            ],
            props=[
                PropAsset(prop_id="old_key", name="老式钥匙", description="黄铜材质，带绿色铜锈。", shot_ids=[1], is_handheld=True),
            ],
            shot_bindings=[
                ShotVisualBinding(scene_id=1, location_id="basement_a", prop_ids=["old_key"]),
                ShotVisualBinding(scene_id=2, location_id="basement_b"),
            ],
        ),
    )


def _state(project_id: str) -> DramaState:
    script = normalize_scene_assets(_script())
    return DramaState(
        project_id=project_id,
        user_input="测试",
        image_generation_policy={"provider": "mock", "model": "mock", "resolution": "1K"},
        script=script,
        visual_design=VisualDesign(overall_style="现代悬疑写实", color_palette="冷蓝灰", scenes=[]),
        generation_prompts=GenerationPrompts(
            api_provider="mock",
            shots=[
                ShotPrompt(scene_id=1, prompt="镜头一", duration_sec=5),
                ShotPrompt(scene_id=2, prompt="镜头二", duration_sec=5),
            ],
        ),
    )


def _client(tmp_path: Path, monkeypatch) -> tuple[TestClient, str]:
    monkeypatch.setattr(project_store_module, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(image_mock_module, "OUTPUT_DIR", tmp_path)
    store = ProjectStore()
    monkeypatch.setattr(api_context.project_store, "_target", store)
    monkeypatch.setattr(api_context.image_gen_agent, "api_client", MockImageClient())
    project_id = store.create_project("资产测试")
    state = _state(project_id)
    store.set_script(project_id, state.script.model_dump())
    store.set_visual_design(project_id, state.visual_design.model_dump())
    store.set_state_json(project_id, api_context._state_to_json(state))
    return TestClient(api_server.app), project_id


def test_same_name_scenes_are_normalized_to_one_stable_asset():
    script = normalize_scene_assets(_script())

    assert len(script.asset_manifest.locations) == 1
    location = script.asset_manifest.locations[0]
    assert location.location_id == stable_location_id("地下室")
    assert location.shot_ids == [1, 2]
    assert {binding.location_id for binding in script.asset_manifest.shot_bindings} == {location.location_id}


def test_project_detail_returns_normalized_snapshot_on_first_read(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)
    meta = api_context.project_store.get_project(project_id)
    meta["assets"]["script"]["asset_manifest"]["locations"][0]["environment_description"] = "旧镜头动作副本"
    api_context.project_store._target._save_meta(project_id, meta)

    response = client.get(f"/api/projects/{project_id}")

    assert response.status_code == 200
    environment = response.json()["meta"]["assets"]["script"]["asset_manifest"]["locations"][0]["environment_description"]
    assert environment != "旧镜头动作副本"


def test_prop_prompt_pending_generation_and_random_regeneration(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)

    prompt = client.get(
        f"/api/projects/{project_id}/assets/prop/old_key/prompt",
        params={"view_type": "standard_views"},
    )
    assert prompt.status_code == 200
    assert "2 行 3 列" in prompt.json()["prompt"]
    assert "分别生成六张独立单视图" in prompt.json()["prompt"]
    assert "货架" in prompt.json()["prompt"]

    first = client.post(
        f"/api/projects/{project_id}/assets/generate-pending",
        json={"asset_type": "prop"},
    )
    assert first.status_code == 200
    assert len(first.json()["generated"]) == 1
    first_item = first.json()["generated"][0]
    assert first_item["view_type"] == "front"
    assert first_item["version_id"]
    assert Path(first_item["image_path"]).is_file()
    from PIL import Image
    with Image.open(first_item["image_path"]) as generated_view:
        assert generated_view.size == (1024, 1024)

    pending_again = client.post(
        f"/api/projects/{project_id}/assets/generate-pending",
        json={"asset_type": "prop"},
    )
    assert pending_again.status_code == 200
    assert pending_again.json()["generated"] == []
    assert len(pending_again.json()["skipped"]) == 1

    regenerated = client.post(
        f"/api/projects/{project_id}/assets/prop/old_key/generate",
        json={"regenerate": True},
    )
    assert regenerated.status_code == 200
    second_item = regenerated.json()["generated"][0]
    assert second_item["seed"] != first_item["seed"]
    assert second_item["version_id"] != first_item["version_id"]

    history = client.get(
        f"/api/projects/{project_id}/image-history",
        params={"asset_type": "prop", "asset_id": "old_key", "view_type": "front"},
    )
    assert history.status_code == 200
    assert len(history.json()["versions"]) == 2


def test_scene_pending_generation_uses_location_id(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)
    location_id = stable_location_id("地下室")

    response = client.post(
        f"/api/projects/{project_id}/assets/generate-pending",
        json={"asset_type": "scene"},
    )
    assert response.status_code == 200
    assert {item["view_type"] for item in response.json()["generated"]} == {"wide", "medium"}
    state = json.loads(response.json()["state_json"])
    assert {item["location_id"] for item in state["scene_images"]["scenes"]} == {location_id}
    assert all(item["version_id"] for item in state["scene_images"]["scenes"])


def test_unconfigured_visual_review_is_persisted_as_unreviewed(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)
    import src.config as config

    monkeypatch.setattr(config, "REVIEW_LLM_ENABLED", True)
    monkeypatch.setattr(config, "REVIEW_LLM_PROVIDER", "")
    monkeypatch.setattr(config, "REVIEW_LLM_API_KEY", "")
    monkeypatch.setattr(config, "HAPPYHORSE_API_KEY", "")

    response = client.post(f"/api/projects/{project_id}/assets/prop/old_key/generate", json={})

    assert response.status_code == 200
    payload = response.json()
    assert payload["generated"][0]["quality_report"]["status"] == "unreviewed"
    state = json.loads(payload["state_json"])
    saved = state["prop_images"]["props"][0]
    assert saved["quality_report"]["status"] == "unreviewed"


def test_asset_prompt_preview_uses_the_same_saved_supplements_as_generation(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)
    location_id = stable_location_id("地下室")

    generated = client.post(
        f"/api/projects/{project_id}/assets/scene/{location_id}/generate",
        json={"view_type": "wide", "custom_instructions": "雨夜，地面有少量积水", "regenerate": True},
    )
    assert generated.status_code == 200

    preview = client.get(
        f"/api/projects/{project_id}/assets/scene/{location_id}/prompt",
        params={"view_type": "wide"},
    )
    assert preview.status_code == 200
    payload = preview.json()
    assert "高质量影视场景空镜" in payload["base_prompt"]
    assert "雨夜，地面有少量积水" in payload["effective_prompt"]
    assert "禁止人物" in payload["effective_prompt"]
    assert payload["provider_prompt"] == payload["effective_prompt"]
    assert "人物" in payload["negative_prompt"]


def test_asset_prompt_includes_project_style_and_scene_ratio(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)
    state = api_context._load_project_state(project_id)
    state.creative_profile = CreativeProfile(art_style="国风水墨", video_ratio="16:9")
    api_context._save_project_state(state)
    location_id = stable_location_id("地下室")

    scene = client.get(f"/api/projects/{project_id}/assets/scene/{location_id}/prompt", params={"view_type": "wide"})
    prop = client.get(f"/api/projects/{project_id}/assets/prop/old_key/prompt", params={"view_type": "standard_views"})

    assert scene.status_code == prop.status_code == 200
    assert "国风水墨" in scene.json()["effective_prompt"]
    assert "16:9" in scene.json()["project_constraints"]
    assert (scene.json()["width"], scene.json()["height"]) == (1920, 1080)
    assert "国风水墨" in prop.json()["effective_prompt"]
    assert "单张独立道具参考图" in prop.json()["project_constraints"]
    assert "不得改成多视图拼图" in prop.json()["project_constraints"]


def test_asset_prompt_compatibility_endpoints_delegate_to_canonical_composer(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)

    canonical = client.get(
        f"/api/projects/{project_id}/assets/prop/old_key/prompt",
        params={"view_type": "standard_views"},
    )
    legacy = client.get(
        f"/api/projects/{project_id}/prop-image-prompt",
        params={"asset_id": "old_key"},
    )
    assert canonical.status_code == legacy.status_code == 200
    assert legacy.json()["effective_prompt"] == canonical.json()["effective_prompt"]
    assert legacy.json()["negative_prompt"] == canonical.json()["negative_prompt"]
    assert len(canonical.json()["provider_prompts"]) == 6


def test_character_asset_id_resolves_to_the_canonical_prompt_identity(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)
    state = api_context._load_project_state(project_id)
    state.script.asset_manifest.characters = [
        VisualCharacterAsset(
            asset_id="char_su_mian",
            name="苏眠",
            appearance_mode="on_screen",
            description="苏眠：米白色长风衣，黑色中长发。",
        )
    ]
    state.visual_design.global_character_descriptions_cn = {
        "苏眠": "米白色长风衣，黑色中长发。",
    }
    api_context._save_project_state(state)

    response = client.get(
        f"/api/projects/{project_id}/assets/character/char_su_mian/prompt",
        params={"view_type": "front_half"},
    )

    assert response.status_code == 200
    assert response.json()["asset_id"] == "苏眠"
    assert "苏眠" in response.json()["effective_prompt"]


def test_prop_prompt_snapshot_exposes_negative_mode_and_requirement_signature(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)

    mock_prompt = client.get(
        f"/api/projects/{project_id}/assets/prop/old_key/prompt",
        params={"view_type": "front"},
    )
    assert mock_prompt.status_code == 200
    mock_payload = mock_prompt.json()
    assert mock_payload["negative_prompt_mode"] == "separate"
    assert mock_payload["effective_negative_prompt"] == mock_payload["provider_negative_prompt"]
    assert mock_payload["requirement_signature"]

    state = api_context._load_project_state(project_id)
    state.image_generation_policy = {
        "provider": "wan",
        "model": "wan2.7-image-pro",
        "resolution": "2K",
    }
    api_context._save_project_state(state)
    wan_prompt = client.get(
        f"/api/projects/{project_id}/assets/prop/old_key/prompt",
        params={"view_type": "front"},
    )
    assert wan_prompt.status_code == 200
    wan_payload = wan_prompt.json()
    assert wan_payload["negative_prompt_mode"] == "merged"
    assert wan_payload["provider_negative_prompt"] == ""
    assert "避免出现" in wan_payload["provider_prompt"]


def test_unknown_project_id_is_rejected_before_script_generation():
    client = TestClient(api_server.app)
    response = client.post(
        "/api/script",
        json={"project_id": "deadbeefcafe", "user_input": "不会调用模型"},
    )
    assert response.status_code == 404
