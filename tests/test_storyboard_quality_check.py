from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.script import Scene, ScriptBreakdown
from scripts.run_storyboard_quality_check import audit_storyboard_quality


def _case():
    script = ScriptBreakdown(
        title="audit", genre="剧情", logline="", duration_sec=6, narration_style="无旁白",
        scenes=[
            Scene(scene_id=1, duration_sec=3, location="室内", characters=["角色A"], action_description="角色A长发站在窗边。", camera_direction="中景", emotion_tone="紧张"),
            Scene(scene_id=2, duration_sec=3, location="室内", characters=["角色A"], action_description="角色A剪成短发。", camera_direction="近景", emotion_tone="紧张"),
        ],
    )
    prompts = GenerationPrompts(
        api_provider="mock",
        shots=[
            ShotPrompt(scene_id=1, prompt="【环境】室内；走到窗前；墙面斑驳\n【动作时间轴】0-3秒站立", duration_sec=3),
            ShotPrompt(scene_id=2, prompt="【出镜角色与身份锚点】角色A：黑色长发；短发\n【动作时间轴】0-3秒剪成短发", duration_sec=3),
        ],
    )
    return script, prompts


def test_quality_audit_reports_dynamic_environment_leak():
    result = audit_storyboard_quality(*_case())
    assert result["dynamic_environment_leaks"] >= 1


def test_quality_audit_reports_mutually_exclusive_character_states():
    result = audit_storyboard_quality(*_case())
    assert result["character_state_conflicts"] >= 1


def test_quality_audit_reports_dialogue_coverage_and_evidence():
    script, prompts = _case()
    script.scenes[0].dialogue = []
    result = audit_storyboard_quality(script, prompts)
    assert result["dialogue_total"] == 0
    assert isinstance(result["evidence"], list)
