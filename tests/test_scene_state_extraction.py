from src.agents.script_agent import ScriptAgent
from src.schemas.script import LocationAsset, Scene, ScriptBreakdown, ShotVisualBinding, VisualAssetManifest


def _script(locations, bindings) -> ScriptBreakdown:
    return ScriptBreakdown(
        title="状态提取",
        genre="悬疑",
        logline="测试状态提取",
        duration_sec=20,
        narration_style="无旁白",
        scenes=[
            Scene(scene_id=1, duration_sec=5, location="旧客厅", characters=[], action_description="角色坐下", camera_direction="中景", emotion_tone="平静"),
            Scene(scene_id=2, duration_sec=5, location="旧客厅", characters=[], action_description="角色站起", camera_direction="近景", emotion_tone="平静"),
            Scene(scene_id=3, duration_sec=5, location="旧客厅", characters=[], action_description="雨夜灯光闪烁", camera_direction="中景", emotion_tone="紧张"),
            Scene(scene_id=4, duration_sec=5, location="旧客厅", characters=[], action_description="雨夜地面积水反光", camera_direction="近景", emotion_tone="紧张"),
        ],
        asset_manifest=VisualAssetManifest(locations=locations, shot_bindings=bindings),
    )


def test_action_difference_does_not_create_extra_state():
    script = _script(
        [
            LocationAsset(location_id="room_a", canonical_name="旧客厅", time_condition="夜晚", representative_scene_id=1, shot_ids=[1]),
            LocationAsset(location_id="room_b", canonical_name="旧客厅", time_condition="夜晚", representative_scene_id=2, shot_ids=[2]),
        ],
        [
            ShotVisualBinding(scene_id=1, location_id="room_a"),
            ShotVisualBinding(scene_id=2, location_id="room_b"),
        ],
    )

    ScriptAgent._validate_visual_assets(script, require_manifest=True)

    location = script.asset_manifest.locations[0]
    assert len(script.asset_manifest.locations) == 1
    assert len(location.states) == 1
    assert location.states[0].shot_ids == [1, 2]
    assert {binding.state_id for binding in script.asset_manifest.shot_bindings} == {location.default_state_id}


def test_day_and_rain_have_exact_shot_bindings():
    script = _script(
        [
            LocationAsset(location_id="room_day", canonical_name="旧客厅", time_condition="白天", representative_scene_id=1, shot_ids=[1, 2]),
            LocationAsset(location_id="room_rain", canonical_name="旧客厅", time_condition="雨夜", representative_scene_id=3, shot_ids=[3, 4]),
        ],
        [
            ShotVisualBinding(scene_id=1, location_id="room_day"),
            ShotVisualBinding(scene_id=2, location_id="room_day"),
            ShotVisualBinding(scene_id=3, location_id="room_rain"),
            ShotVisualBinding(scene_id=4, location_id="room_rain"),
        ],
    )

    ScriptAgent._validate_visual_assets(script, require_manifest=True)

    location = script.asset_manifest.locations[0]
    assert len(script.asset_manifest.locations) == 1
    assert len(location.states) == 2
    assert sorted(sum((state.shot_ids for state in location.states), [])) == [1, 2, 3, 4]
    assert all(binding.state_id for binding in script.asset_manifest.shot_bindings)
