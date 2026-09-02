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
