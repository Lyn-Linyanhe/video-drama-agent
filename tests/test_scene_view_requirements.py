from pathlib import Path

from fastapi.testclient import TestClient

import api_server
from src.api import context as api_context
import src.utils.project_store as project_store_module
from src.graph.state import DramaState
from src.schemas.scene_state import LocationState
from src.schemas.script import LocationAsset, Scene, ScriptBreakdown, ShotVisualBinding, VisualAssetManifest
from src.services.asset_generation import required_asset_views
from src.services.scene_view_requirements import derive_scene_view_requirements
from src.utils.project_store import ProjectStore
from src.utils.visual_asset_manifest import stable_location_id


LOCATION_ID = stable_location_id("地下通道")


def _state() -> DramaState:
    location = LocationAsset(
        location_id=LOCATION_ID,
        canonical_name="地下通道",
        identity_description="狭长地下通道，左侧管线，右侧铁门，尽头有出口。",
        environment_description="潮湿的地下通道",
        representative_scene_id=1,
        shot_ids=[1, 2, 3, 4],
        states=[
            LocationState(
                state_id="day",
                label="白天",
                time_condition="白天",
                weather="晴",
                light_direction="出口自然光",
                shot_ids=[1],
                representative_scene_id=1,
            ),
            LocationState(
                state_id="rain",
                label="雨夜",
                time_condition="夜晚",
                weather="暴雨",
                light_direction="冷蓝逆光",
                visible_state_notes="地面积水",
                shot_ids=[2, 3, 4],
                representative_scene_id=2,
            ),
        ],
        default_state_id="day",
    )
    script = ScriptBreakdown(
        title="场景视图需求",
        genre="悬疑",
        logline="测试场景视图需求",
        duration_sec=20,
        narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=5,
                location="地下通道",
                characters=[],
                action_description="建立地下通道空间",
                camera_direction="正面中心线大全景",
                emotion_tone="紧张",
            ),
            Scene(
                scene_id=2,
                duration_sec=5,
                location="地下通道",
                characters=[],
                action_description="从通道最深处向外拍摄，看到出口方向",
                camera_direction="反向大全景",
                emotion_tone="紧张",
            ),
            Scene(
                scene_id=3,
                duration_sec=5,
                location="地下通道",
                characters=[],
                action_description="左前45度展示墙面管线",
                camera_direction="左前45度",
                emotion_tone="紧张",
            ),
            Scene(
                scene_id=4,
                duration_sec=5,
                location="地下通道",
                characters=[],
                action_description="右前45度展示铁门和出口",
                camera_direction="右前45度",
                emotion_tone="紧张",
            ),
        ],
        asset_manifest=VisualAssetManifest(
            locations=[location],
            shot_bindings=[
                ShotVisualBinding(scene_id=1, location_id=LOCATION_ID, state_id="day"),
                ShotVisualBinding(scene_id=2, location_id=LOCATION_ID, state_id="rain"),
                ShotVisualBinding(scene_id=3, location_id=LOCATION_ID, state_id="rain"),
                ShotVisualBinding(scene_id=4, location_id=LOCATION_ID, state_id="rain"),
            ],
        ),
    )
    return DramaState(
        project_id="p-scene-views",
        script=script,
        image_generation_policy={"provider": "wan", "model": "wan2.7-image-pro", "resolution": "2K"},
    )


def _client(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(project_store_module, "OUTPUT_DIR", tmp_path)
    store = ProjectStore()
    monkeypatch.setattr(api_context.project_store, "_target", store)
    project_id = store.create_project("场景视图需求")
    state = _state()
    state.project_id = project_id
    store.set_script(project_id, state.script.model_dump())
    store.set_state_json(project_id, api_context._state_to_json(state))
    return TestClient(api_server.app), project_id


def _two_shot_center_only_state() -> DramaState:
    location_id = stable_location_id("客厅")
    location = LocationAsset(
        location_id=location_id,
        canonical_name="客厅",
        environment_description="普通客厅",
        representative_scene_id=1,
        shot_ids=[1, 2],
        states=[
            LocationState(
                state_id="default",
                label="默认状态",
                shot_ids=[1, 2],
                representative_scene_id=1,
            )
        ],
        default_state_id="default",
    )
    script = ScriptBreakdown(
        title="中心视图",
        genre="剧情",
        logline="测试中心视图",
        duration_sec=10,
        narration_style="无旁白",
        scenes=[
            Scene(scene_id=1, duration_sec=5, location="客厅", characters=[], action_description="环境建立", camera_direction="中景", emotion_tone="平静"),
            Scene(scene_id=2, duration_sec=5, location="客厅", characters=[], action_description="人物经过", camera_direction="近景", emotion_tone="平静"),
        ],
        asset_manifest=VisualAssetManifest(
            locations=[location],
            shot_bindings=[
                ShotVisualBinding(scene_id=1, location_id=location_id, state_id="default"),
                ShotVisualBinding(scene_id=2, location_id=location_id, state_id="default"),
            ],
        ),
    )
    return DramaState(
        project_id="p-center-only",
        script=script,
        image_generation_policy={"provider": "wan", "model": "wan2.7-image-pro", "resolution": "2K"},
    )


def test_one_shot_scene_only_needs_center():
    result = derive_scene_view_requirements(_state(), LOCATION_ID, "day")

    assert result.views == ("scene_center",)
    assert result.state_id == "day"


def test_linear_space_needs_reverse_and_side_cues_are_state_scoped():
    result = derive_scene_view_requirements(_state(), LOCATION_ID, "rain")

    assert result.views == ("scene_center", "scene_left_45", "scene_right_45", "scene_reverse")
    assert "reverse_cue" in result.reasons
    assert "left_right_cues" in result.reasons


def test_required_asset_views_uses_scene_requirements_for_wan_policy():
    state = _two_shot_center_only_state()
    required = required_asset_views(state, "scene")

    location_id = state.script.asset_manifest.locations[0].location_id
    assert required[location_id] == ("scene_center",)


def test_manual_override_always_keeps_center_on_state_route(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)

    response = client.put(
        f"/api/projects/{project_id}/scenes/{LOCATION_ID}/states/rain/view-requirements",
        json={"views": ["scene_right_45"]},
    )

    assert response.status_code == 200
    assert response.json()["views"] == ["scene_center", "scene_right_45"]
    assert response.json()["source"] == "manual"
    followup = client.get(f"/api/projects/{project_id}/scenes/{LOCATION_ID}/states/rain/view-requirements")
    assert followup.json()["views"] == ["scene_center", "scene_right_45"]


def test_manual_override_rejects_unknown_scene_view(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)

    response = client.put(
        f"/api/projects/{project_id}/scenes/{LOCATION_ID}/states/rain/view-requirements",
        json={"views": ["scene_diagonal"]},
    )

    assert response.status_code == 400
