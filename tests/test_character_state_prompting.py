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


def test_registry_without_manifest_requires_visible_scene_role_profile():
    script = ScriptBreakdown(
        title="\u65e0\u8d44\u4ea7\u6e05\u5355", genre="\u5267\u60c5", logline="", duration_sec=3, narration_style="\u65e0\u65c1\u767d",
        scenes=[Scene(scene_id=1, duration_sec=3, location="\u5ba4\u5185", characters=["\u89d2\u8272A"], action_description="\u89d2\u8272A\u7ad9\u7acb\u3002", camera_direction="\u4e2d\u666f", emotion_tone="\u5e73\u9759")],
    )
    agent = VisualPromptAgent(api_provider="mock")
    assert agent._registry_needs_profile_repair({}, script) is True
    assert agent._registry_needs_profile_repair({"\u89d2\u8272A": "\u5b8c\u6574\u4fa7\u5199"}, script) is False


def test_registry_without_manifest_drops_non_scene_roles():
    script = ScriptBreakdown(
        title="\u65e0\u8d44\u4ea7\u6e05\u5355", genre="\u5267\u60c5", logline="", duration_sec=3, narration_style="\u65e0\u65c1\u767d",
        scenes=[Scene(scene_id=1, duration_sec=3, location="\u5ba4\u5185", characters=["\u89d2\u8272A"], action_description="\u89d2\u8272A\u7ad9\u7acb\u3002", camera_direction="\u4e2d\u666f", emotion_tone="\u5e73\u9759")],
    )
    registry = VisualPromptAgent(api_provider="mock")._canonical_character_registry(
        {"\u89d2\u8272A": "\u7a33\u5b9a\u8eab\u4efd", "\u65c1\u767d": "\u4e0d\u5e94\u51fa\u955c"}, script, english=False,
    )
    assert set(registry) == {"\u89d2\u8272A"}


def test_same_shot_character_states_are_scoped_to_named_character():
    scene_text = "\u89d2\u8272A\u7a7f\u7ea2\u8272\u5916\u5957\uff0c\u89d2\u8272B\u7a7f\u84dd\u8272\u5916\u5957"
    _stable_a, state_a = split_character_identity_and_state(
        "\u89d2\u8272A", "\u6210\u5e74\u5973\u6027\uff1b\u8eab\u5f62\u4fee\u957f", "", scene_text, character_names=("\u89d2\u8272A", "\u89d2\u8272B")
    )
    _stable_b, state_b = split_character_identity_and_state(
        "\u89d2\u8272B", "\u6210\u5e74\u7537\u6027\uff1b\u8eab\u5f62\u9ad8\u5927", "", scene_text, character_names=("\u89d2\u8272A", "\u89d2\u8272B")
    )
    assert "\u7ea2\u8272\u5916\u5957" in state_a and "\u84dd\u8272\u5916\u5957" not in state_a
    assert "\u84dd\u8272\u5916\u5957" in state_b and "\u7ea2\u8272\u5916\u5957" not in state_b


def test_english_registry_removes_cross_scene_hair_conflicts():
    script = ScriptBreakdown(
        title="\u82f1\u6587\u72b6\u6001", genre="\u5267\u60c5", logline="", duration_sec=6, narration_style="\u65e0\u65c1\u767d",
        scenes=[
            Scene(scene_id=1, duration_sec=3, location="\u5ba4\u5185", characters=["\u89d2\u8272A"], action_description="\u89d2\u8272A\u7559\u7740\u957f\u53d1\u3002", camera_direction="\u4e2d\u666f", emotion_tone="\u5e73\u9759"),
            Scene(scene_id=2, duration_sec=3, location="\u5ba4\u5185", characters=["\u89d2\u8272A"], action_description="\u89d2\u8272A\u526a\u6210\u77ed\u53d1\u3002", camera_direction="\u8fd1\u666f", emotion_tone="\u5e73\u9759"),
        ],
    )
    registry = VisualPromptAgent(api_provider="mock")._canonical_character_registry(
        {"\u89d2\u8272A": "adult woman with long hair and short hair"}, script, english=True,
    )
    assert "long hair" not in registry["\u89d2\u8272A"].lower()
    assert "short hair" not in registry["\u89d2\u8272A"].lower()
