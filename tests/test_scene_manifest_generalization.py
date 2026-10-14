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



def test_binding_includes_declared_character_even_when_text_uses_generic_noun():
    # 场景 5-14 的教训：动作文本用"孩子"泛称主角，按名字面匹配会让
    # 杨氏子在 12 个子镜里完全没有参考图。声明出镜的角色必须绑定，
    # 漏绑（身份无锚）的代价远大于多绑（多占一个参考位）。
    from src.schemas.script import DialogueLine, Scene, ScriptBreakdown
    from src.utils.visual_asset_manifest import build_manifest_from_scenes

    scene = Scene(
        scene_id=5, duration_sec=10, location="杨家院子", characters=["杨氏子", "孔君平"],
        action_description="孔君平看着孩子忙前忙后的身影，眼里露出赞许。",  # 只点名孔君平
        dialogue=[DialogueLine(character="孔君平", line="你还真是个懂礼貌的孩子。")],
        camera_direction="中景", emotion_tone="和蔼",
    )
    script = ScriptBreakdown(title="测试", genre="剧情", logline="", duration_sec=10, narration_style="无旁白", scenes=[scene])
    manifest = build_manifest_from_scenes(script)
    binding = next(b for b in manifest.shot_bindings if b.scene_id == 5)
    names = {c.name for c in manifest.characters}
    bound_names = {next(c.name for c in manifest.characters if c.asset_id == cid) for cid in binding.on_screen_character_ids}
    assert bound_names == names == {"杨氏子", "孔君平"}
