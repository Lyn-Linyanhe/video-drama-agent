from src.agents.visual_prompt_agent import (
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
