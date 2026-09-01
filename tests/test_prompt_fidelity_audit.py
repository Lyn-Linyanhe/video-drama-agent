from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.script import DialogueLine, Scene, ScriptBreakdown
from src.services.prompt_fidelity_audit import audit_prompt_fidelity


def _script(action="角色A站在关闭的门前", dialogue=None):
    return ScriptBreakdown(
        title="", genre="", logline="", duration_sec=5, narration_style="",
        scenes=[Scene(scene_id=1, duration_sec=5, location="室内", characters=["角色A"],
                      dialogue=dialogue or [], action_description=action,
                      camera_direction="近景", emotion_tone="紧张")],
    )


def test_fidelity_reports_missing_dialogue_with_both_sides_evidence():
    script = _script(dialogue=[DialogueLine(character="角色A", line="快走。")])
    prompts = GenerationPrompts(api_provider="mock", shots=[ShotPrompt(scene_id=1, duration_sec=5, prompt="【动作时间轴】角色A站立。")])
    report = audit_prompt_fidelity(script, prompts)
    issue = next(item for item in report.issues if item.issue_code == "dialogue_missing")
    assert report.dialogue_covered == 0
    assert issue.evidence.expected_excerpt == "快走。"
    assert issue.evidence.prompt_excerpt
    assert issue.evidence.prompt_section == "动作时间轴"


def test_fidelity_blocks_explicit_closed_door_conflict():
    script = _script(action="门始终关闭，角色A只能通过监控观察")
    prompts = GenerationPrompts(api_provider="mock", shots=[ShotPrompt(scene_id=1, duration_sec=5, prompt="【动作时间轴】角色A推门走出。")])
    report = audit_prompt_fidelity(script, prompts)
    assert report.status == "blocked"
    assert any(item.issue_code == "forbidden_exit_action" for item in report.issues)


def test_fidelity_clean_prompt_has_no_semantic_false_positive():
    script = _script(action="角色A拿起钥匙，打开门")
    prompts = GenerationPrompts(api_provider="mock", shots=[ShotPrompt(scene_id=1, duration_sec=5, prompt="【动作时间轴】角色A拿起钥匙，打开门")])
    report = audit_prompt_fidelity(script, prompts)
    assert report.status == "clean"
    assert report.issues == []
