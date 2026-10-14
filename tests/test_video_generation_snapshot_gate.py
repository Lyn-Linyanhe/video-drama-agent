from src.graph.state import DramaState
from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.script import Scene, ScriptBreakdown
from src.services.prompt_fidelity_gate import audit_generation_snapshot, generation_snapshot_digest


def _state(prompt="【动作时间轴】角色A观察门。"):
    return DramaState(
        project_id="snapshot-gate",
        script=ScriptBreakdown(
            title="快照门禁", genre="测试", logline="测试", duration_sec=5, narration_style="无旁白",
            scenes=[Scene(scene_id=1, duration_sec=5, location="室内", characters=["角色A"], action_description="角色A观察门", camera_direction="近景", emotion_tone="紧张")],
        ),
        generation_prompts=GenerationPrompts(
            api_provider="mock", shots=[ShotPrompt(scene_id=1, duration_sec=5, prompt=prompt)]
        ),
    )


def _snapshot(prompt="【动作时间轴】角色A观察门。", scene_id=1):
    snapshot = {
        "scene_id": scene_id,
        "raw_prompt": prompt,
        "source_prompt": prompt,
        "source_duration_sec": 5,
        "effective_duration_sec": 5,
        "prepared_shot": {"scene_id": 1, "duration_sec": 5, "prompt": prompt, "negative_prompt": ""},
        "provider_prompt": prompt,
    }
    snapshot["prompt_snapshot_digest"] = generation_snapshot_digest(snapshot)
    return snapshot


def test_snapshot_gate_rejects_stale_prompt_before_provider_call():
    report = audit_generation_snapshot(_state(), _snapshot("旧提示词"), 1)
    assert report.status == "blocked"
    assert any(issue.issue_code == "snapshot_prompt_mismatch" for issue in report.issues)


def test_snapshot_gate_rejects_scene_mismatch():
    report = audit_generation_snapshot(_state(), _snapshot(scene_id=2), 1)
    assert report.status == "blocked"
    assert any(issue.issue_code == "snapshot_scene_mismatch" for issue in report.issues)


def test_snapshot_gate_allows_matching_final_snapshot():
    report = audit_generation_snapshot(_state(), _snapshot(), 1)
    assert report.status == "clean"


def test_snapshot_gate_rejects_prepared_prompt_mismatch():
    snapshot = _snapshot()
    snapshot["prepared_shot"]["prompt"] = "另一个提示词"
    report = audit_generation_snapshot(_state(), snapshot, 1)
    assert any(issue.issue_code == "snapshot_digest_mismatch" for issue in report.issues)


def test_snapshot_gate_rejects_prepared_duration_mismatch():
    snapshot = _snapshot()
    snapshot["prepared_shot"]["duration_sec"] = 8
    report = audit_generation_snapshot(_state(), snapshot, 1)
    assert any(issue.issue_code == "snapshot_duration_mismatch" for issue in report.issues)


def test_snapshot_gate_rejects_provider_prompt_mismatch():
    snapshot = _snapshot()
    snapshot["provider_prompt"] = "不相关的供应商提示词"
    report = audit_generation_snapshot(_state(), snapshot, 1)
    assert any(issue.issue_code == "snapshot_provider_prompt_mismatch" for issue in report.issues)

def test_video_job_target_guard_rejects_payload_scene_mismatch():
    from src.api.routes.jobs import _validate_video_job_target

    class Store:
        def get_job_by_id(self, _job_id):
            return {"target_type": "shot", "target_id": "2"}

    class Manager:
        store = Store()

    class Context:
        job_id = "job-1"
        manager = Manager()

    try:
        _validate_video_job_target(Context(), 1)
    except ValueError as exc:
        assert "镜头" in str(exc)
    else:
        raise AssertionError("mismatched persisted target must be rejected")


def test_snapshot_gate_uses_materialized_child_prompt_when_shot_id_is_present():
    from src.schemas.shot_plan import ShotPlan, ShotUnit, SceneCapacityReport

    state = _state("【动作时间轴】角色A观察门。")
    state.generation_prompts.shot_plans = [ShotPlan(
        scene_id=1, parent_duration_sec=5,
        capacity=SceneCapacityReport(
            scene_id=1, required_total_duration_sec=3, available_duration_sec=5,
            recommended_total_duration_sec=5, recommended_durations_sec=[5],
            recommended_shot_count=1,
        ),
        units=[ShotUnit(
            shot_id="scene-1-shot-1", parent_scene_id=1, order=1,
            beat_ids=["scene-1-beat-1"], duration_sec=5,
        )],
    )]
    state.script.scenes[0].action_description = "角色A观察门"
    state.generation_prompts.shots[0].prompt = "【动作时间轴】角色A观察门。"
    from src.services.shot_prompt_materializer import materialize_shot_prompts
    child = materialize_shot_prompts(state.generation_prompts, state.script)[0]
    snapshot = {
        "scene_id": 1, "shot_id": child.shot_id,
        "source_prompt": child.prompt, "raw_prompt": child.prompt,
        "source_duration_sec": 5, "effective_duration_sec": 5,
        "prepared_shot": child.model_dump(), "provider_prompt": child.prompt,
    }
    snapshot["prompt_snapshot_digest"] = generation_snapshot_digest(snapshot)

    report = audit_generation_snapshot(state, snapshot, 1)

    assert not report.blocked
    assert not any(issue.issue_code == "snapshot_prompt_mismatch" for issue in report.issues)


def test_video_job_target_guard_accepts_child_shot_target():
    from src.api.routes.jobs import _validate_video_job_target

    class Store:
        def get_job_by_id(self, _job_id):
            return {
                "target_type": "shot_unit",
                "target_id": "scene-1-shot-2",
                "payload": {"scene_id": 1, "shot_id": "scene-1-shot-2"},
            }

    class Manager:
        store = Store()

    class Context:
        job_id = "job-child"
        manager = Manager()

    _validate_video_job_target(Context(), 1, "scene-1-shot-2")


def test_snapshot_gate_rejects_unknown_child_shot_instead_of_falling_back_to_parent():
    snapshot = _snapshot()
    snapshot["shot_id"] = "scene-1-shot-404"
    snapshot["prepared_shot"]["shot_id"] = "scene-1-shot-404"
    snapshot["prompt_snapshot_digest"] = generation_snapshot_digest(snapshot)

    report = audit_generation_snapshot(_state(), snapshot, 1)

    assert report.status == "blocked"
    assert any(issue.issue_code == "snapshot_shot_not_found" for issue in report.issues)



def test_snapshot_gate_uses_provider_duration_policy_from_snapshot():
    """Do not reject a HappyHorse snapshot just because default materialization is 3s."""
    from src.schemas.script import DialogueLine
    from src.schemas.shot_plan import SceneCapacityReport, ShotPlan, ShotUnit
    from src.services.shot_prompt_materializer import materialize_shot_prompts

    default_prompt = (
        "【单镜头时间轴】\n"
        "【动作时间轴】0-3s 德拉回到公寓，将烫发铁钳放在煤气炉上加热，对着镜子把短发烫成紧贴头皮的小卷。\n"
        "【镜头语言】镜前中景。\n"
        "【本镜禁止执行】不得执行本镜范围之外的动作或状态；不得说出本镜未绑定对白。"
    )
    provider_prompt = (
        "【单镜头时间轴】\n"
        "【动作时间轴】0-5s 德拉回到公寓，将烫发铁钳放在煤气炉上加热，对着镜子把短发烫成紧贴头皮的小卷。\n"
        "【对白】\n德拉：\n“求求上帝，让他觉得我还是漂亮的吧。”\n"
        "【镜头语言】镜前中景。\n"
        "【本镜禁止执行】不得执行本镜范围之外的动作或状态；不得说出本镜未绑定对白。"
    )

    state = _state(default_prompt)
    state.script.scenes[0].duration_sec = 12
    state.script.scenes[0].action_description = (
        "德拉回到公寓，将烫发铁钳放在煤气炉上加热，对着镜子把短发烫成紧贴头皮的小卷。"
    )
    state.script.scenes[0].dialogue = [
        DialogueLine(character="德拉", line="求求上帝，让他觉得我还是漂亮的吧。", order=1)
    ]
    state.generation_prompts.shots[0].duration_sec = 12
    state.generation_prompts.shot_plans = [
        ShotPlan(
            scene_id=1,
            parent_duration_sec=12,
            capacity=SceneCapacityReport(
                scene_id=1,
                required_total_duration_sec=12,
                available_duration_sec=12,
                recommended_total_duration_sec=5,
                recommended_durations_sec=[5],
                recommended_shot_count=1,
            ),
            units=[
                ShotUnit(
                    shot_id="scene-1-shot-1",
                    parent_scene_id=1,
                    order=1,
                    beat_ids=["scene-1-beat-1"],
                    source_fact_ids=["scene:1:action:1"],
                    dialogue_ids=["scene-1-dialogue-1"],
                    duration_sec=5,
                )
            ],
        )
    ]

    class _DefaultChild:
        shot_id = "scene-1-shot-1"
        scene_id = 1
        prompt = default_prompt
        duration_sec = 3

    class _ProviderChild:
        shot_id = "scene-1-shot-1"
        scene_id = 1
        prompt = provider_prompt
        duration_sec = 5

        def model_dump(self):
            return {
                "scene_id": 1,
                "shot_id": "scene-1-shot-1",
                "duration_sec": 5,
                "prompt": provider_prompt,
                "negative_prompt": "",
            }

    original = materialize_shot_prompts

    def fake_materialize(prompts, script, **kwargs):
        if kwargs.get("duration_cap_sec") == 15 and kwargs.get("provider_min_duration_sec") == 4:
            return [_ProviderChild()]
        return [_DefaultChild()]

    import src.services.prompt_fidelity_gate as gate
    import src.services.shot_prompt_materializer as materializer
    materializer.materialize_shot_prompts = fake_materialize
    try:
        snapshot = {
            "scene_id": 1,
            "shot_id": "scene-1-shot-1",
            "source_prompt": provider_prompt,
            "raw_prompt": provider_prompt,
            "source_duration_sec": 5,
            "effective_duration_sec": 5,
            "prepared_shot": _ProviderChild().model_dump(),
            "provider_prompt": provider_prompt,
            "policy": {
                "provider": "happyhorse",
                "model": "happyhorse-1.1-r2v",
                "resolution": "1080p",
            },
        }
        snapshot["prompt_snapshot_digest"] = generation_snapshot_digest(snapshot)
        report = audit_generation_snapshot(state, snapshot, 1)
    finally:
        materializer.materialize_shot_prompts = original

    assert not report.blocked
    assert not any(issue.issue_code == "snapshot_prompt_mismatch" for issue in report.issues)
