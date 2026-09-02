from src.api import context as api_context
from src.graph.state import DramaState
from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.script import Scene, ScriptBreakdown
from src.services.prompt_fidelity_gate import (
    audit_current_prompt_fidelity,
    prompt_fidelity_gate_detail,
)


def _state(prompt: str = "【动作时间轴】角色A推门走出。") -> DramaState:
    script = ScriptBreakdown(
        title="门禁测试",
        genre="悬疑",
        logline="封闭空间",
        duration_sec=5,
        narration_style="无旁白",
        scenes=[Scene(
            scene_id=1,
            duration_sec=5,
            location="室内",
            characters=["角色A"],
            action_description="门始终关闭，角色A只能通过监控观察",
            camera_direction="近景",
            emotion_tone="紧张",
        )],
    )
    return DramaState(
        project_id="gate-test",
        script=script,
        generation_prompts=GenerationPrompts(
            api_provider="mock",
            shots=[ShotPrompt(scene_id=1, duration_sec=5, prompt=prompt)],
        ),
    )


def test_prompt_fidelity_gate_returns_structured_evidence_for_blocking_issue():
    report = audit_current_prompt_fidelity(_state())
    assert report.status == "blocked"
    detail = prompt_fidelity_gate_detail(report)
    assert detail["code"] == "prompt_fidelity_blocked"
    assert detail["issues"][0]["issue_code"] == "forbidden_exit_action"
    assert detail["issues"][0]["evidence"]["expected_excerpt"]
    assert detail["issues"][0]["evidence"]["prompt_excerpt"]


def test_prompt_fidelity_gate_allows_clean_current_prompt():
    report = audit_current_prompt_fidelity(_state("【动作时间轴】角色A通过监控观察，门保持关闭。"))
    assert report.status == "clean"
    assert prompt_fidelity_gate_detail(report) is None


def test_production_payload_exposes_fidelity_and_capacity_diagnostics(monkeypatch):
    state = _state("【动作时间轴】角色A通过监控观察，门保持关闭。")
    payload = api_context._production_payload(state)
    assert payload["prompt_fidelity"]["status"] == "clean"
    assert payload["prompt_fidelity"]["scene_count"] == 1
    assert "items" in payload["shot_capacity"]
    assert "character_profile_repair" in payload
    assert state.prompt_fidelity["status"] == "clean"
