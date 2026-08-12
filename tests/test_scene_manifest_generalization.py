from src.schemas.script import Scene
from src.utils.visual_asset_manifest import (
    _clean_environment_description,
    _common_environment_description,
    _location_time_condition_from_scenes,
    _location_weather_from_scenes,
)


def test_scene_state_recovery_scans_the_full_action_text():
    long_prefix = "镜头沿着空旷空间缓慢推进，保持稳定透视和深景深。" * 20
    scenes = [
        Scene(
            scene_id=1,
            duration_sec=5,
            location="医院",
            characters=[],
            action_description=long_prefix + "室内病房，窗外暴雨，深夜的冷蓝色灯光从窗边照入。",
            camera_direction="大全景",
            emotion_tone="压迫",
        )
    ]

    assert _location_time_condition_from_scenes(scenes) == "深夜"
    assert _location_weather_from_scenes(scenes) == "室内，无直接降雨；外部暴雨"


def test_common_environment_description_keeps_all_cleaned_fixtures_when_no_clause_matches():
    result = _common_environment_description([
        "旧砖墙；拱形铁门",
        "中央长桌；老式台灯",
    ])

    assert "旧砖墙" in result
    assert "拱形铁门" in result
    assert "中央长桌" in result
    assert "老式台灯" in result


def test_clean_environment_description_preserves_named_fixed_portrait_fixture():
    scenes = [
        Scene(
            scene_id=1,
            duration_sec=5,
            location="旧展馆地下室",
            characters=["顾衡"],
            action_description="顾衡站在中央长桌旁。",
            camera_direction="大全景",
            emotion_tone="阴冷",
        )
    ]

    result = _clean_environment_description(
        "墙面挂着顾衡的肖像画；中央长桌和铜钟固定不变",
        scenes,
    )

    assert "顾衡的肖像画" in result
    assert "中央长桌" in result
    assert "铜钟" in result

