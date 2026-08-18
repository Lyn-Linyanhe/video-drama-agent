from src.schemas.script import Scene, ScriptBreakdown
from src.utils.visual_asset_manifest import build_manifest_from_scenes


def test_generic_gender_term_resolves_to_canonical_character_alias():
    script = ScriptBreakdown(
        title="泛称归一化",
        genre="悬疑",
        logline="测试动作文本中的人物泛称",
        duration_sec=6,
        narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=3,
                location="旧公寓客厅",
                characters=["王涛"],
                action_description="男生靠墙站立，客厅的木柜和窗户保持固定。",
                camera_direction="中景",
                emotion_tone="紧张",
            ),
            Scene(
                scene_id=2,
                duration_sec=3,
                location="旧公寓客厅",
                characters=["王涛", "林雨"],
                action_description="男生抬手指向窗户，女生站在木柜旁。",
                camera_direction="中景",
                emotion_tone="紧张",
            ),
        ],
    )

    manifest = build_manifest_from_scenes(script)

    wang = next(character for character in manifest.characters if character.name == "王涛")
    assert "男生" in wang.aliases
    assert "男生" not in manifest.locations[0].fixed_dressing
    assert "抬手" not in manifest.locations[0].fixed_dressing
