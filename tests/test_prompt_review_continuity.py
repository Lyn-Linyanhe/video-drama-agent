# -*- coding: utf-8 -*-
from types import SimpleNamespace

from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.prompt_review import PromptReviewResult, PromptReviewShot
from src.schemas.script import (
    LocationAsset,
    PropAsset,
    Scene,
    ScriptBreakdown,
    ShotVisualBinding,
    VisualAssetManifest,
)


def _script(loc_b: str | None = None) -> ScriptBreakdown:
    scenes = [
        Scene(
            scene_id=1,
            duration_sec=5,
            location="雨夜街道",
            characters=["苏眠"],
            action_description="苏眠在雨中打开信封。",
            camera_direction="中景",
            emotion_tone="紧张",
        ),
        Scene(
            scene_id=2,
            duration_sec=5,
            location="雨夜街道",
            characters=["苏眠"],
            action_description="苏眠攥紧信纸抬头。",
            camera_direction="近景",
            emotion_tone="紧张",
            continuity_from_prev=True,
            continuity_reason="同一时空连续动作",
        ),
    ]
    loc_id_2 = loc_b if loc_b is not None else "street_night"
    bindings = [
        ShotVisualBinding(scene_id=1, location_id="street_night", prop_ids=["letter"]),
        ShotVisualBinding(scene_id=2, location_id=loc_id_2, prop_ids=["letter"]),
    ]
    manifest = VisualAssetManifest(
        locations=[
            LocationAsset(location_id="street_night", canonical_name="雨夜街道", representative_scene_id=1, shot_ids=[1, 2]),
        ],
        props=[PropAsset(prop_id="letter", name="第七封信", description="湿透的纸质信封", shot_ids=[1, 2], is_handheld=True)],
        shot_bindings=bindings,
    )
    return ScriptBreakdown(
        title="t", genre="g", logline="l", duration_sec=10, narration_style="n",
        scenes=scenes, asset_manifest=manifest,
    )


def _prompts(script: ScriptBreakdown) -> GenerationPrompts:
    return GenerationPrompts(
        api_provider="mock",
        shots=[
            ShotPrompt(scene_id=scene.scene_id, prompt=f"prompt {scene.scene_id}")
            for scene in script.scenes
        ],
    )


def _result() -> PromptReviewResult:
    return PromptReviewResult(
        overall_score=85,
        passed=True,
        summary="ok",
        shots=[
            PromptReviewShot(scene_id=1, score=85, passed=True, summary=""),
            PromptReviewShot(scene_id=2, score=85, passed=True, summary=""),
        ],
        issues=[],
    )


def test_continuity_same_location_no_issue():
    from src.agents.prompt_review_agent import PromptReviewAgent

    script = _script()
    result = PromptReviewAgent._apply_deterministic_checks(script, _prompts(script), _result())
    continuity = [i for i in result.issues if i.category == "continuity"]
    assert continuity == []


def test_continuity_cross_location_is_critical():
    from src.agents.prompt_review_agent import PromptReviewAgent

    script = _script(loc_b="alley_night")
    result = PromptReviewAgent._apply_deterministic_checks(script, _prompts(script), _result())
    continuity = [i for i in result.issues if i.category == "continuity"]
    assert len(continuity) == 1
    assert continuity[0].severity == "critical"
    assert "地点不同" in continuity[0].description
    assert result.passed is False
    assert result.overall_score <= 59


def test_first_shot_marked_continuity_is_critical():
    from src.agents.prompt_review_agent import PromptReviewAgent

    script = _script()
    script.scenes[0].continuity_from_prev = True
    result = PromptReviewAgent._apply_deterministic_checks(script, _prompts(script), _result())
    continuity = [i for i in result.issues if i.category == "continuity"]
    assert len(continuity) == 1
    assert continuity[0].severity == "critical"
    assert "首镜" in continuity[0].description


def test_continuity_missing_prev_prop_is_major():
    from src.agents.prompt_review_agent import PromptReviewAgent

    script = _script()
    script.asset_manifest.shot_bindings[1].prop_ids = []
    result = PromptReviewAgent._apply_deterministic_checks(script, _prompts(script), _result())
    continuity = [i for i in result.issues if i.category == "continuity"]
    assert len(continuity) == 1
    assert continuity[0].severity == "major"
    assert "关键道具" in continuity[0].description


def test_assembled_prompt_injects_prev_state_only_for_continuity_shot():
    import src.agents.visual_prompt_agent as vpa
    from src.agents.visual_prompt_agent import VisualPromptAgent

    script = _script()
    prev_shot = vpa._CompactShotVisualPrompt(
        scene_id=1,
        subject_state="苏眠撑着伞站立",
        action_timeline="0-2s 拆开信封，2-5s 拿出信纸",
        environment_delta="街道积水，雨势持续",
        camera_motion="中景",
        lighting_delta="冷蓝路灯",
        negative_prompt="低清晰度",
        motion_strength=0.5,
    )
    cur_shot = vpa._CompactShotVisualPrompt(
        scene_id=2,
        subject_state="苏眠攥紧信纸",
        action_timeline="0-5s 抬头看向镜头",
        environment_delta="",
        camera_motion="近景",
        lighting_delta="冷蓝路灯",
        negative_prompt="低清晰度",
        motion_strength=0.5,
    )
    output = SimpleNamespace(
        overall_style="写实",
        color_palette="冷灰蓝",
        aspect_ratio="9:16",
        global_character_descriptions_cn={},
    )

    cur_prompt = VisualPromptAgent._assemble_compact_shot_prompt(
        script, script.scenes[1], cur_shot, output, None, prev_shot
    )
    assert "【顺承上一镜】" in cur_prompt
    assert "撑着伞站立" in cur_prompt
    assert "雨势持续" in cur_prompt
    assert "冷蓝路灯" in cur_prompt
    # 上一镜完整动作时间轴不得泄入本镜，避免剧情串用。
    assert "拆开信封" not in cur_prompt
    assert "0-2s" not in cur_prompt

    # 未标记承接的镜头不注入顺承段。
    script.scenes[1].continuity_from_prev = False
    normal_prompt = VisualPromptAgent._assemble_compact_shot_prompt(
        script, script.scenes[1], cur_shot, output, None, prev_shot
    )
    assert "【顺承上一镜】" not in normal_prompt


def test_assembled_prompt_injects_generic_continuity_for_legacy_shot():
    from types import SimpleNamespace
    import src.agents.visual_prompt_agent as vpa
    from src.agents.visual_prompt_agent import VisualPromptAgent

    script = _script()
    # 旧契约镜头没有 subject_state/environment_delta 等字段（single-shot 回落路径）。
    prev_shot = SimpleNamespace(
        scene_id=1,
        subject_state="",
        action_timeline="0-5s 拆开信封",
        environment_delta="",
        lighting_delta="",
        prop_state="",
    )
    cur_shot = SimpleNamespace(
        scene_id=2,
        subject_state="苏眠攥紧信纸",
        action_timeline="0-5s 抬头看向镜头",
        environment_delta="",
        camera_motion="近景",
        lighting_delta="",
        negative_prompt="低清晰度",
        motion_strength=0.5,
    )
    output = SimpleNamespace(overall_style="写实", color_palette="冷灰蓝", aspect_ratio="9:16",
                             global_character_descriptions_cn={})
    prompt = VisualPromptAgent._assemble_compact_shot_prompt(
        script, script.scenes[1], cur_shot, output, None, prev_shot
    )
    assert "【顺承上一镜】" in prompt
    # 兜底句保留承接语义，但不泄露上一镜动作细节。
    assert "姿态、站位、空间与光线" in prompt
    assert "拆开信封" not in prompt


def test_split_output_appends_continuity_segment_after_timed_structure():
    import src.agents.visual_prompt_agent as vpa
    from src.agents.visual_prompt_agent import VisualPromptAgent

    script = _script()  # scene 2 continuity_from_prev=True
    prev_shot = vpa._CompactShotVisualPrompt(
        scene_id=1, subject_state="苏眠撑着伞站立", action_timeline="0-2s 拆开信封，2-5s 拿出信纸",
        environment_delta="街道积水，雨势持续", camera_motion="中景", lighting_delta="冷蓝路灯",
        negative_prompt="低清晰度", motion_strength=0.5,
    )
    cur_shot = vpa._CompactShotVisualPrompt(
        scene_id=2, subject_state="苏眠攥紧信纸", action_timeline="0-5s 抬头看向镜头",
        environment_delta="", camera_motion="近景", lighting_delta="冷蓝路灯",
        negative_prompt="低清晰度", motion_strength=0.5,
    )
    output = vpa._CompactVisualPromptOutput(
        overall_style="写实", color_palette="冷灰蓝", aspect_ratio="9:16",
        global_negative_prompt="neg",
        global_character_descriptions={}, global_character_descriptions_cn={},
        shots=[prev_shot, cur_shot],
    )
    agent = VisualPromptAgent()
    _visual, prompts = agent._split_output(output, script, None, None)
    shot2 = next(sp for sp in prompts.shots if sp.scene_id == 2)
    assert "【顺承上一镜】" in shot2.prompt
    assert "撑着伞站立" in shot2.prompt
    assert "雨势持续" in shot2.prompt
    assert "拆开信封" not in shot2.prompt
    shot1 = next(sp for sp in prompts.shots if sp.scene_id == 1)
    assert "【顺承上一镜】" not in shot1.prompt

