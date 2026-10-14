from src.agents.visual_prompt_agent import VisualPromptAgent, _VisualPromptOutput, _ShotVisualPrompt
from src.schemas.script import Scene, ScriptBreakdown


def test_split_output_does_not_rewrite_source_scene_schedule():
    scene = Scene(
        scene_id=1,
        duration_sec=9,
        location="室内",
        characters=["甲"],
        action_description="甲站在门边",
        camera_direction="固定中景",
        emotion_tone="",
    )
    script = ScriptBreakdown(
        title="源时长",
        genre="剧情",
        logline="测试",
        duration_sec=12,
        narration_style="无旁白",
        scenes=[scene],
    )
    output = _VisualPromptOutput(
        overall_style="写实",
        color_palette="冷色",
        aspect_ratio="9:16",
        shots=[_ShotVisualPrompt(
            scene_id=1,
            subject_state="甲站在门边",
            action_timeline="0-15s 固定中景，甲站在门边",
            duration_sec=15,
        )],
    )

    _visual, prompts = VisualPromptAgent(api_provider="mock")._split_output(
        output, script, prompt_overrides=None
    )

    assert prompts.shots[0].duration_sec == 9
    assert scene.duration_sec == 9
    assert script.duration_sec == 12

def test_split_output_uses_model_capability_for_capacity_diagnostics():
    script = ScriptBreakdown(
        title="容量",
        genre="剧情",
        logline="测试",
        duration_sec=12,
        narration_style="无旁白",
        scenes=[Scene(
            scene_id=1,
            duration_sec=12,
            location="室内",
            characters=["甲"],
            action_description="甲站在门边",
            camera_direction="固定中景",
            emotion_tone="",
        )],
    )
    output = _VisualPromptOutput(
        overall_style="写实",
        color_palette="冷色",
        aspect_ratio="9:16",
        global_negative_prompt="",
        global_character_descriptions={},
        global_character_descriptions_cn={},
        shots=[_ShotVisualPrompt(
            scene_id=1,
            subject_state="甲站在门边",
            action_timeline="0-12s 固定中景，甲站在门边",
            environment_delta="",
            camera_motion="固定中景",
            lighting_delta="",
            prop_state="",
            negative_prompt="",
            motion_strength=0.2,
        )],
    )
    _visual, prompts = VisualPromptAgent(api_provider="mock")._split_output(
        output,
        script,
        prompt_overrides=None,
        video_generation_policy={
            "provider": "seedance",
            "model": "doubao-seedance-2-0-fast-260128",
            "resolution": "1080p",
        },
    )

    diagnostic = prompts.capacity_diagnostics[0]
    assert diagnostic.provider_max_duration_sec == 15
    assert diagnostic.recommended_durations_sec == [12]
    assert prompts.shot_plans[0].capacity.status == "clean"
    assert prompts.shot_plans[0].units[0].duration_sec == 12


def test_split_output_keeps_parent_duration_and_builds_happyhorse_child_plan():
    script = ScriptBreakdown(
        title="快乐小马容量",
        genre="剧情",
        logline="测试",
        duration_sec=30,
        narration_style="无旁白",
        scenes=[Scene(
            scene_id=1,
            duration_sec=30,
            location="室内",
            characters=["甲"],
            action_description="甲走到桌边拿起钥匙并打开盒子。甲停在门边等待。",
            camera_direction="固定中景",
            emotion_tone="紧张",
        )],
    )
    output = _VisualPromptOutput(
        overall_style="写实",
        color_palette="冷色",
        aspect_ratio="9:16",
        shots=[_ShotVisualPrompt(
            scene_id=1,
            subject_state="甲在室内",
            action_timeline="0-30s 甲完成场景动作",
            camera_motion="固定中景",
        )],
    )

    _visual, prompts = VisualPromptAgent(api_provider="mock")._split_output(
        output,
        script,
        prompt_overrides=None,
        video_generation_policy={
            "provider": "happyhorse",
            "model": "happyhorse-1.1-r2v",
            "resolution": "1080p",
        },
    )

    # The authored parent scene remains 30s in metadata. Child shots follow
    # their own content clock inside HappyHorse's 4-15s range instead of
    # padding back up to the parent total.
    assert prompts.shots[0].duration_sec == 30
    assert prompts.capacity_diagnostics[0].provider_max_duration_sec == 15
    assert len(prompts.shot_plans[0].units) >= 2
    assert all(4 <= unit.duration_sec <= 15 for unit in prompts.shot_plans[0].units)


def test_split_output_uses_happyhorse_four_second_minimum_for_short_children():
    script = ScriptBreakdown(
        title="快乐小马最小时长", genre="剧情", logline="测试", duration_sec=8, narration_style="无旁白",
        scenes=[Scene(
            scene_id=1, duration_sec=8, location="室内", characters=["甲"],
            dialogue=[{"character": "甲", "line": "收到。"}],
            action_description="甲点头，然后走到桌边。", camera_direction="固定中景", emotion_tone="平静",
        )],
    )
    output = _VisualPromptOutput(
        overall_style="写实", color_palette="冷色", aspect_ratio="9:16",
        shots=[_ShotVisualPrompt(scene_id=1, subject_state="甲在室内", action_timeline="0-8s 甲完成动作", camera_motion="固定中景")],
    )

    _visual, prompts = VisualPromptAgent(api_provider="mock")._split_output(
        output, script, prompt_overrides=None,
        video_generation_policy={"provider": "happyhorse", "model": "happyhorse-1.1-r2v", "resolution": "1080p"},
    )

    assert prompts.shot_plans[0].units
    assert all(unit.duration_sec >= 4 for unit in prompts.shot_plans[0].units)


def test_continuity_segment_has_no_cross_shot_instruction():
    # 每个子镜独立提交给模型；【顺承上一镜】只能携带状态事实，
    # 不能再附带"不要重复上一镜…"这类请求里没有指代物的指令句。
    from types import SimpleNamespace

    from src.agents.visual_prompt_agent import _build_continuity_segment

    prev = SimpleNamespace(
        subject_state="孩子立于门内侧石凳旁",
        environment_delta="青砖地面",
        lighting_delta="上午天光",
        prop_state="木门半掩",
    )
    text = _build_continuity_segment(prev)
    assert "顺承上一镜" in text
    assert "不要重复上一镜" not in text


def test_project_visual_style_block_avoids_double_period():
    from src.services.prompt_composer import _project_visual_style_block

    class _Profile:
        visual_style_id = "cinematic_cel"
        art_style = "写实"
        style_modifiers: list[str] = []

    line, _ = _project_visual_style_block(_Profile())
    assert "。。" not in line
    assert line.startswith("【项目视觉媒介】")
