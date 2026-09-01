from src.agents.visual_prompt_agent import (
    VisualPromptAgent,
    _CompactShotVisualPrompt,
    _CompactVisualPromptOutput,
    find_mutually_exclusive_character_states,
    split_character_identity_and_state,
)
from src.schemas.script import Scene, ScriptBreakdown


def test_split_character_identity_and_state_keeps_only_stable_identity_globally():
    stable, current = split_character_identity_and_state(
        "角色A",
        "成年女性；褐色长发；短发；棕色外套",
        "当前为短发",
        "她短发站在门边",
    )

    assert "长发" not in stable
    assert "短发" not in stable
    assert "棕色外套" in stable
    assert "短发" in current


def test_find_mutually_exclusive_character_states_is_name_agnostic():
    script = ScriptBreakdown(
        title="状态", genre="剧情", logline="", duration_sec=6, narration_style="无旁白",
        scenes=[
            Scene(scene_id=1, duration_sec=3, location="室内", characters=["角色A"], action_description="角色A留着长发。", camera_direction="中景", emotion_tone="平静"),
            Scene(scene_id=2, duration_sec=3, location="室内", characters=["角色A"], action_description="角色A剪成短发。", camera_direction="近景", emotion_tone="平静"),
        ],
    )

    conflicts = find_mutually_exclusive_character_states(script, "角色A")

    assert conflicts
    assert conflicts[0]["category"] == "hair_length"
    assert set(conflicts[0]["scene_ids_by_state"]) == {"0", "1"}



def test_conflicting_states_across_scenes_are_removed_from_global_registry():
    script = ScriptBreakdown(
        title="跨镜状态", genre="剧情", logline="", duration_sec=6, narration_style="无旁白",
        scenes=[
            Scene(scene_id=1, duration_sec=3, location="室内", characters=["角色A"], action_description="角色A留着长发。", camera_direction="中景", emotion_tone="平静"),
            Scene(scene_id=2, duration_sec=3, location="室内", characters=["角色A"], action_description="角色A剪成短发。", camera_direction="近景", emotion_tone="平静"),
        ],
    )
    output = _CompactVisualPromptOutput(
        overall_style="写实", color_palette="冷色", aspect_ratio="9:16",
        global_character_descriptions_cn={"角色A": "成年女性；身形修长；黑色长发；棕色外套"},
        shots=[
            _CompactShotVisualPrompt(scene_id=1, action_timeline="0-3秒站立", duration_sec=3),
            _CompactShotVisualPrompt(scene_id=2, action_timeline="0-3秒剪成短发", duration_sec=3),
        ],
    )

    _visual, prompts = VisualPromptAgent(api_provider="mock")._split_output(output, script, None)

    identity = prompts.shots[0].prompt
    assert "长发" not in identity.split("角色锚点：", 1)[-1].split("主体状态：", 1)[0]
    assert "短发" not in identity.split("角色锚点：", 1)[-1].split("主体状态：", 1)[0]
    assert "长发" in identity
