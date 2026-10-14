from src.schemas.script import DialogueLine, Scene
from src.services.beat_planner import build_narrative_beats, estimate_scene_capacity
from src.schemas.shot_plan import SceneCapacityReport


def _scene(*, duration: int = 12, action: str = "角色A站在门边", dialogue=None, camera: str = "固定中景", emotion: str = "紧张", continuity: bool = False, continuity_reason: str = "") -> Scene:
    return Scene(
        scene_id=1,
        duration_sec=duration,
        location="旧公寓",
        characters=["角色A"],
        dialogue=list(dialogue or []),
        action_description=action,
        camera_direction=camera,
        emotion_tone=emotion,
        continuity_from_prev=continuity,
        continuity_reason=continuity_reason,
    )


def test_camera_emotion_and_continuity_are_constraints_not_serial_story_time():
    report = estimate_scene_capacity(
        _scene(
            action="角色A站在门边",
            camera="近景推到特写",
            emotion="紧张",
            continuity=True,
            continuity_reason="保持门内站位和道具状态",
        )
    )

    assert report.metadata_duration_sec > 0
    assert report.expected_duration_sec == report.serial_duration_sec
    assert report.required_total_duration_sec == report.expected_duration_sec
    assert report.expected_duration_sec < report.maximum_duration_sec


def test_explicit_pause_adds_serial_time_but_ordinary_emotion_does_not():
    paused = estimate_scene_capacity(_scene(action="角色A沉默三秒后转身离开", emotion="冷静"))
    ordinary = estimate_scene_capacity(_scene(action="角色A转身离开", emotion="冷静"))

    assert paused.serial_duration_sec >= ordinary.serial_duration_sec + 2.0
    assert paused.expected_duration_sec >= paused.serial_duration_sec


def test_explicit_parallel_action_overlaps_dialogue_instead_of_double_counting():
    dialogue = [DialogueLine(character="角色A", line="我回来了，先把这些东西收好。")]
    report = estimate_scene_capacity(
        _scene(action="角色A一边整理桌面一边低声说话", dialogue=dialogue, emotion="专注")
    )
    dialogue_expected = sum(
        beat.duration.expected_sec
        for beat in build_narrative_beats(_scene(action="角色A站在门边", dialogue=dialogue, camera="", emotion=""))
        if beat.kind in {"dialogue", "inner_monologue"}
    )

    assert report.overlap_duration_sec > 0
    assert report.expected_duration_sec >= dialogue_expected
    assert report.expected_duration_sec == round(
        report.serial_duration_sec + report.overlap_duration_sec, 2
    )


def test_long_dialogue_is_never_shorter_than_its_dialogue_beat():
    dialogue = [
        DialogueLine(
            character="角色A",
            line="这是一段必须完整保留的长对白，用来说明人物的决定和接下来要做的事情。",
        )
    ]
    scene = _scene(action="角色A站在门边", dialogue=dialogue, camera="", emotion="")
    beats = build_narrative_beats(scene)
    dialogue_expected = max(
        beat.duration.expected_sec
        for beat in beats
        if beat.kind in {"dialogue", "inner_monologue"}
    )
    report = estimate_scene_capacity(scene)

    assert report.expected_duration_sec >= dialogue_expected
    assert report.minimum_duration_sec <= report.expected_duration_sec <= report.maximum_duration_sec


def test_complex_action_chain_keeps_physical_beats_serial_and_traceable():
    scene = _scene(
        action="角色A走到桌边，拿起钥匙，打开盒子，取出信封，转身离开",
        camera="",
        emotion="",
    )
    beats = build_narrative_beats(scene)
    physical = [beat for beat in beats if beat.kind not in {"camera", "emotion", "continuity"}]

    assert len(physical) >= 5
    assert all(beat.timing_role == "serial" for beat in physical)
    assert all(beat.source_excerpt for beat in physical)


def test_capacity_report_exposes_duration_range_without_mutating_source_duration():
    scene = _scene(duration=8, action="角色A走到桌边拿起钥匙并打开盒子", camera="", emotion="")
    report = estimate_scene_capacity(scene, provider_max_duration_sec=10)

    assert report.source_duration_sec == 8
    assert report.available_duration_sec == 8
    assert report.minimum_duration_sec <= report.expected_duration_sec <= report.maximum_duration_sec
    assert scene.duration_sec == 8


def test_capacity_report_reads_legacy_payload_with_new_timing_defaults():
    report = SceneCapacityReport.model_validate(
        {
            "scene_id": 1,
            "required_total_duration_sec": 6,
            "available_duration_sec": 8,
            "recommended_total_duration_sec": 8,
            "recommended_durations_sec": [8],
            "recommended_shot_count": 1,
            "status": "clean",
        }
    )

    assert report.required_total_duration_sec == 6
    assert report.source_duration_sec == 8
    assert report.expected_duration_sec == 6
    assert report.minimum_duration_sec <= report.expected_duration_sec <= report.maximum_duration_sec


def test_visual_prompt_dialogue_clock_uses_the_same_per_line_estimate():
    from src.agents.visual_prompt_agent import _dialogue_duration
    from src.services.beat_planner import estimate_dialogue_duration_sec

    dialogue = [
        DialogueLine(character="角色A", line="第一句完整台词。"),
        DialogueLine(character="角色B", line="第二句完整台词。"),
    ]
    scene = _scene(action="角色A站在门边", dialogue=dialogue, camera="", emotion="")

    assert _dialogue_duration(scene) == sum(
        estimate_dialogue_duration_sec(line.line) for line in dialogue
    )



def test_parallel_action_only_overlaps_the_explicitly_associated_dialogue():
    dialogue = [
        DialogueLine(character="角色A", line="第一句与动作同时说。"),
        DialogueLine(character="角色A", line="第二句默认按顺序承载。"),
    ]
    beats = build_narrative_beats(
        _scene(action="角色A一边整理桌面一边低声说话", dialogue=dialogue, camera="", emotion="")
    )

    overlapped_dialogue = [
        beat for beat in beats
        if beat.kind in {"dialogue", "inner_monologue"} and beat.timing_role == "overlap"
    ]
    serial_dialogue = [
        beat for beat in beats
        if beat.kind in {"dialogue", "inner_monologue"} and beat.timing_role == "serial"
    ]

    assert len(overlapped_dialogue) == 1
    assert len(serial_dialogue) == 1


def test_provider_cap_cannot_produce_sub_three_second_recommendations():
    scene = _scene(duration=5, action="角色A站在门边", camera="", emotion="")
    report = estimate_scene_capacity(scene, provider_max_duration_sec=3)

    assert report.status == "duration_insufficient"
    assert report.recommended_shot_count == 0
    assert report.recommended_durations_sec == []


def test_empty_dialogue_line_does_not_break_capacity_planning():
    scene = _scene(
        action="角色A站在门边",
        dialogue=[DialogueLine(character="角色A", line="")],
        camera="",
        emotion="",
    )

    beats = build_narrative_beats(scene)
    report = estimate_scene_capacity(scene)

    assert not [beat for beat in beats if beat.kind in {"dialogue", "inner_monologue"}]
    assert report.expected_duration_sec >= 0


def test_capacity_diagnostic_exposes_timing_overload_as_advisory_warning():
    from src.services.shot_capacity import analyze_shot_capacity

    scene = _scene(
        duration=5,
        action="角色A走到桌边拿起钥匙并打开盒子",
        dialogue=[DialogueLine(character="角色A", line="这是一段需要完整承载的较长对白。")],
        camera="",
        emotion="",
    )
    diagnostic = analyze_shot_capacity(scene, provider_max_duration_sec=5)

    assert diagnostic.status == "warning"
    assert diagnostic.planning_status == "duration_insufficient"
    assert diagnostic.recommended_durations_sec


def test_visual_duration_budget_does_not_write_back_to_source_script():
    from src.agents.visual_prompt_agent import _fit_duration_budget
    from src.schemas.prompt import ShotPrompt
    from src.schemas.script import ScriptBreakdown

    script = ScriptBreakdown(
        title="源时长不可变",
        genre="剧情",
        logline="测试",
        duration_sec=12,
        narration_style="无旁白",
        scenes=[_scene(duration=9, action="角色A站在门边", camera="", emotion="")],
    )
    prompt = ShotPrompt(scene_id=1, prompt="测试提示词", duration_sec=5)

    _fit_duration_budget([prompt], script, {1: 15}, 12)

    assert prompt.duration_sec == 15
    assert script.scenes[0].duration_sec == 9
    assert script.duration_sec == 12


def test_visual_duration_budget_respects_provider_cap_without_mutating_script():
    from src.agents.visual_prompt_agent import _fit_duration_budget
    from src.schemas.prompt import ShotPrompt
    from src.schemas.script import ScriptBreakdown

    script = ScriptBreakdown(
        title="提供方上限",
        genre="剧情",
        logline="测试",
        duration_sec=30,
        narration_style="无旁白",
        scenes=[_scene(duration=30, action="角色A站在门边", camera="", emotion="")],
    )
    prompt = ShotPrompt(scene_id=1, prompt="测试提示词", duration_sec=30)

    _fit_duration_budget([prompt], script, {1: 30}, 30, provider_max_duration_sec=15)

    assert prompt.duration_sec == 30
    assert script.scenes[0].duration_sec == 30
    assert script.duration_sec == 30
