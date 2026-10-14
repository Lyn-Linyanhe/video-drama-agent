"""Regression tests for HappyHorse capacity closure.

These cover the deterministic execution chain:
- provider duration caps must create executable child shots;
- no plan may fall back to an over-capacity parent shot;
- materialization must reject child durations outside the provider cap.
"""

from src.api import context as api_context
from src.api.routes import production as production_routes
from src.graph.state import DramaState
from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.script import DialogueLine, Scene, ScriptBreakdown
from src.schemas.shot_plan import SceneCapacityReport, ShotPlan, ShotUnit
from src.services.beat_planner import build_narrative_beats
from src.services.shot_planner import build_shot_plan
from src.services.shot_prompt_materializer import materialize_shot_prompts
from src.agents.video_gen_agent import VideoGenerationAgent
from src.api_clients.happyhorse import HappyHorseVideoClient


def test_build_shot_plan_splits_duration_insufficient_scene_into_units():
    scene = Scene(
        scene_id=3,
        duration_sec=5,
        location="室内",
        characters=["甲"],
        dialogue=[DialogueLine(character="甲", line="我回来了。")],
        action_description="甲走到桌边拿起钥匙并打开盒子",
        camera_direction="固定中景",
        emotion_tone="紧张",
    )

    plan = build_shot_plan(scene, provider_max_duration_sec=10)

    assert plan.capacity.status == "duration_insufficient"
    assert plan.units, "duration_insufficient must still create executable units"
    assert all(3 <= unit.duration_sec <= 10 for unit in plan.units)
    # Child durations follow each unit's own content clock, so the total may
    # exceed a compact scene-level estimate because of provider minimums.
    assert sum(unit.duration_sec for unit in plan.units) >= plan.capacity.required_total_duration_sec

    planned_beat_ids = {beat_id for unit in plan.units for beat_id in unit.beat_ids}
    source_beat_ids = {
        beat.beat_id for beat in build_narrative_beats(scene)
        if beat.kind not in {"continuity", "camera", "emotion"}
    }
    assert source_beat_ids.issubset(planned_beat_ids), "child units must not drop story beats"


def test_build_shot_plan_never_creates_over_cap_units_for_happyhorse():
    scene = Scene(
        scene_id=9,
        duration_sec=30,
        location="室内",
        characters=["吉姆", "德拉"],
        dialogue=[DialogueLine(character="德拉", line="我把头发卖掉了，为了给你买礼物。" * 3)],
        action_description="吉姆走进房间，站在门口。德拉迎上前，低声解释。吉姆发现短发。",
        camera_direction="固定中景",
        emotion_tone="紧张",
    )

    plan = build_shot_plan(scene, provider_max_duration_sec=15)

    assert len(plan.units) >= 2
    assert all(3 <= unit.duration_sec <= 15 for unit in plan.units)
    for index, unit in enumerate(plan.units):
        assert unit.shot_id == f"scene-9-shot-{index + 1}"
        assert unit.order == index + 1
    assert plan.units[1].continuity_from_shot_id == plan.units[0].shot_id


def test_happyhorse_shot_plan_uses_declared_discrete_duration_values():
    scene = Scene(
        scene_id=12,
        duration_sec=8,
        location="室内",
        characters=["甲"],
        action_description="甲走到桌边拿起钥匙并打开盒子",
        camera_direction="固定中景",
        emotion_tone="紧张",
    )

    plan = build_shot_plan(
        scene,
        provider_max_duration_sec=15,
        provider_durations=[3, 5, 10, 15],
    )

    assert plan.units
    assert all(unit.duration_sec in {3, 5, 10, 15} for unit in plan.units)
    assert all(unit.duration_sec <= 15 for unit in plan.units)
    assert sum(unit.duration_sec for unit in plan.units) >= plan.capacity.required_total_duration_sec


def test_capacity_planner_ignores_malformed_provider_duration_values():
    scene = Scene(
        scene_id=13,
        duration_sec=8,
        location="室内",
        characters=["甲"],
        action_description="甲走到桌边拿起钥匙并打开盒子",
        camera_direction="固定中景",
        emotion_tone="紧张",
    )

    from src.services.beat_planner import estimate_scene_capacity

    report = estimate_scene_capacity(
        scene,
        provider_max_duration_sec=15,
        provider_durations=[3, "not-a-duration", 15],
    )

    assert report.recommended_durations_sec
    assert all(duration in {3, 15} for duration in report.recommended_durations_sec)


def _script_and_prompts_for_materializer() -> tuple[ScriptBreakdown, GenerationPrompts, ShotPlan]:
    scene = Scene(
        scene_id=1,
        duration_sec=8,
        location="室内",
        characters=["甲"],
        action_description="甲走到桌边拿起钥匙并打开盒子",
        camera_direction="固定中景",
        emotion_tone="紧张",
    )
    script = ScriptBreakdown(
        title="测试",
        genre="剧情",
        logline="测试",
        duration_sec=8,
        narration_style="无旁白",
        scenes=[scene],
    )
    parent = ShotPrompt(
        scene_id=1,
        prompt="【主体状态】甲在室内。\n【动作时间轴】0-8s 甲走到桌边拿起钥匙并打开盒子。\n【镜头语言】固定中景。",
        duration_sec=8,
    )
    plan = ShotPlan(
        scene_id=1,
        parent_duration_sec=8,
        capacity=SceneCapacityReport(
            scene_id=1,
            required_total_duration_sec=8,
            available_duration_sec=8,
            recommended_total_duration_sec=8,
            recommended_durations_sec=[4, 4],
            recommended_shot_count=2,
            status="auto_split",
            warnings=["拆分"],
        ),
        units=[
            ShotUnit(shot_id="scene-1-shot-1", parent_scene_id=1, order=1, beat_ids=["scene-1-beat-1"], duration_sec=4),
            ShotUnit(shot_id="scene-1-shot-2", parent_scene_id=1, order=2, beat_ids=["scene-1-beat-2"], duration_sec=4, continuity_from_shot_id="scene-1-shot-1"),
        ],
    )
    return script, GenerationPrompts(api_provider="mock", shots=[parent], shot_plans=[plan]), plan


def test_materialize_rejects_plan_with_empty_units_and_failed_capacity():
    script, prompts, plan = _script_and_prompts_for_materializer()
    plan.units = []
    plan.capacity = plan.capacity.model_copy(update={"status": "duration_insufficient", "warnings": ["容量不足"]})

    try:
        materialize_shot_prompts(prompts, script, duration_cap_sec=15)
        raise AssertionError("materializer must reject over-capacity fallback parent shots")
    except ValueError as exc:
        assert "容量" in str(exc)
        assert "子镜头" in str(exc)


def test_materialize_keeps_legacy_shot_without_plan_for_backward_compatibility():
    script, prompts, _ = _script_and_prompts_for_materializer()
    prompts.shot_plans = []

    shots = materialize_shot_prompts(prompts, script, duration_cap_sec=15)

    assert len(shots) == 1
    assert shots[0].shot_id is None


def test_materialize_rejects_child_unit_duration_above_provider_cap():
    script, prompts, plan = _script_and_prompts_for_materializer()
    plan.units[0].duration_sec = 20

    try:
        materialize_shot_prompts(prompts, script, duration_cap_sec=15)
        raise AssertionError("child duration above provider cap must fail hard")
    except ValueError as exc:
        assert "20" in str(exc)
        assert "15" in str(exc)


def test_materialize_rejects_legacy_parent_above_provider_cap_instead_of_falling_back():
    script, prompts, _ = _script_and_prompts_for_materializer()
    prompts.shot_plans = []
    prompts.shots[0].duration_sec = 20

    try:
        materialize_shot_prompts(prompts, script, duration_cap_sec=15)
        raise AssertionError("legacy parent above provider cap must fail hard")
    except ValueError as exc:
        assert "20" in str(exc)
        assert "15" in str(exc)
        assert "回退" in str(exc) or "超" in str(exc)


def test_api_materializer_uses_project_happyhorse_cap():
    script, prompts, plan = _script_and_prompts_for_materializer()
    plan.units[0].duration_sec = 20
    state = DramaState(
        project_id="happyhorse-context",
        script=script,
        generation_prompts=prompts,
        video_generation_policy={
            "provider": "happyhorse",
            "model": "happyhorse-1.1-i2v",
            "resolution": "1080p",
        },
    )

    try:
        api_context._materialized_shot_prompts(state, 1)
        raise AssertionError("API materializer must enforce selected provider cap")
    except ValueError as exc:
        assert "20" in str(exc)
        assert "15" in str(exc)


def test_production_prompt_resolution_passes_selected_duration_cap(monkeypatch):
    script, prompts, _ = _script_and_prompts_for_materializer()
    state = DramaState(
        project_id="happyhorse-production",
        script=script,
        generation_prompts=prompts,
        video_generation_policy={
            "provider": "happyhorse",
            "model": "happyhorse-1.1-i2v",
            "resolution": "1080p",
        },
    )
    captured = {}

    def fake_materialized(*_args, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(production_routes, "_materialized_shot_prompts", fake_materialized)
    monkeypatch.setattr(production_routes, "_shot_prompt", lambda *_args, **_kwargs: prompts.shots[0])

    production_routes._production_shot_prompts(state, 1, duration_cap_sec=15)

    assert captured["duration_cap_sec"] == 15


def test_materializer_rejects_fractional_child_duration_instead_of_rounding():
    script, prompts, plan = _script_and_prompts_for_materializer()
    for unit in plan.units:
        object.__setattr__(unit, "duration_sec", 4.6)

    try:
        materialize_shot_prompts(
            prompts,
            script,
            duration_cap_sec=15,
            duration_options=[3, 5, 10, 15],
        )
        raise AssertionError("fractional child duration must fail instead of rounding to 5s")
    except ValueError as exc:
        assert "整数" in str(exc) or "integer" in str(exc)


def test_happyhorse_continuous_range_never_plans_below_declared_minimum():
    scene = Scene(
        scene_id=21, duration_sec=8, location="室内", characters=["甲"],
        dialogue=[DialogueLine(character="甲", line="收到。")],
        action_description="甲点头，然后走到桌边。", camera_direction="固定中景", emotion_tone="平静",
    )

    plan = build_shot_plan(
        scene,
        provider_min_duration_sec=4,
        provider_max_duration_sec=15,
    )

    assert plan.units
    assert all(4 <= unit.duration_sec <= 15 for unit in plan.units)


def test_materializer_repairs_legacy_three_second_child_for_happyhorse_range():
    scene = Scene(
        scene_id=22, duration_sec=8, location="室内", characters=["甲"],
        dialogue=[DialogueLine(character="甲", line="收到。")],
        action_description="甲点头。", camera_direction="固定中景", emotion_tone="平静",
    )
    script = ScriptBreakdown(
        title="测试", genre="剧情", logline="测试", duration_sec=8,
        narration_style="无旁白", scenes=[scene],
    )
    parent = ShotPrompt(scene_id=22, prompt="【动作时间轴】0-8s 甲点头。", duration_sec=8)
    plan = ShotPlan(
        scene_id=22, parent_duration_sec=8,
        capacity=SceneCapacityReport(
            scene_id=22, required_total_duration_sec=8, available_duration_sec=8,
            recommended_total_duration_sec=8, recommended_durations_sec=[3, 5],
            recommended_shot_count=2, status="auto_split", warnings=[],
        ),
        units=[
            ShotUnit(shot_id="scene-22-shot-1", parent_scene_id=22, order=1, beat_ids=["scene-22-beat-1"], dialogue_ids=["scene-22-dialogue-1"], duration_sec=3),
            ShotUnit(shot_id="scene-22-shot-2", parent_scene_id=22, order=2, beat_ids=["scene-22-beat-2"], duration_sec=5),
        ],
    )

    shots = materialize_shot_prompts(
        GenerationPrompts(api_provider="happyhorse", shots=[parent], shot_plans=[plan]),
        script, provider_min_duration_sec=4, duration_cap_sec=15,
    )

    assert shots[0].duration_sec == 4
    assert "【动作时间轴】0-4s" in shots[0].prompt


def test_api_materializer_repairs_legacy_three_second_child_from_happyhorse_policy():
    script, prompts, plan = _script_and_prompts_for_materializer()
    plan.units[0].duration_sec = 3
    plan.units[1].duration_sec = 5
    state = DramaState(
        project_id="happyhorse-min-context", script=script, generation_prompts=prompts,
        video_generation_policy={"provider": "happyhorse", "model": "happyhorse-1.1-r2v", "resolution": "1080p"},
    )

    shots = api_context._materialized_shot_prompts(state, 1)

    assert shots[0].duration_sec == 4
    assert all(4 <= shot.duration_sec <= 15 for shot in shots)


def test_production_prompt_resolution_passes_selected_duration_minimum(monkeypatch):
    script, prompts, _ = _script_and_prompts_for_materializer()
    state = DramaState(project_id="happyhorse-production-min", script=script, generation_prompts=prompts)
    captured = {}

    def fake_materialized(*_args, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(production_routes, "_materialized_shot_prompts", fake_materialized)
    monkeypatch.setattr(production_routes, "_shot_prompt", lambda *_args, **_kwargs: prompts.shots[0])

    production_routes._production_shot_prompts(
        state, 1, duration_cap_sec=15, provider_min_duration_sec=4,
    )

    assert captured["provider_min_duration_sec"] == 4


def test_happyhorse_prepare_request_rejects_three_second_shot():
    agent = VideoGenerationAgent(
        api_client=HappyHorseVideoClient(api_key="test", model="happyhorse-1.1-r2v")
    )
    shot = ShotPrompt(scene_id=1, prompt="甲点头。", duration_sec=3)

    try:
        agent.prepare_shot_request(
            shot, generation_prompts=GenerationPrompts(api_provider="happyhorse", shots=[shot]),
            prompt_policy=None,
        )
        raise AssertionError("HappyHorse must reject a three-second provider request")
    except ValueError as exc:
        assert "4-15" in str(exc)


def test_overloaded_authored_budget_expands_only_to_compact_semantic_need():
    scene = Scene(
        scene_id=23, duration_sec=12, location="公寓", characters=["德拉", "吉姆"],
        dialogue=[
            DialogueLine(character="吉姆", line="你真的把那么长的头发全都剪掉卖掉了吗？"),
            DialogueLine(character="德拉", line="我剪掉卖了，但不管头发怎样，我仍然还是原来的我。"),
            DialogueLine(character="吉姆", line="所以你的头发真的一点也没有留下来吗？"),
            DialogueLine(character="德拉", line="别再找了，这是为了给你准备一件真正合适的圣诞礼物。"),
        ],
        action_description="吉姆推门进屋，德拉握着表链起身走向他。",
        camera_direction="双人中景转近景", emotion_tone="紧张",
    )

    plan = build_shot_plan(
        scene, provider_min_duration_sec=4, provider_max_duration_sec=15,
    )

    assert len(plan.units) >= 2
    assert all(4 <= unit.duration_sec <= 15 for unit in plan.units)
    assert sum(unit.duration_sec for unit in plan.units) > scene.duration_sec
    assert [dialogue_id for unit in plan.units for dialogue_id in unit.dialogue_ids] == [
        "scene-23-dialogue-1", "scene-23-dialogue-2",
        "scene-23-dialogue-3", "scene-23-dialogue-4",
    ]
