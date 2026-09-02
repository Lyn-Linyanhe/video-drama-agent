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


def test_fidelity_blocks_prompt_shot_id_not_present_in_script():
    script = _script()
    prompts = GenerationPrompts(api_provider="mock", shots=[
        ShotPrompt(scene_id=1, duration_sec=5, prompt="镜头一"),
        ShotPrompt(scene_id=99, duration_sec=5, prompt="额外镜头"),
    ])
    report = audit_prompt_fidelity(script, prompts)
    issue = next(item for item in report.issues if item.issue_code == "scene_shot_mismatch")
    assert report.status == "blocked"
    assert issue.scene_id == 99
    assert issue.evidence.expected_excerpt == "合法镜头编号：1"
    assert issue.evidence.prompt_excerpt == "scene_id=99"
from src.schemas.prompt import GenerationPrompts, PromptAssemblyDiagnostic, ShotPrompt
from src.schemas.script import Scene, ScriptBreakdown
from src.services.prompt_fidelity_audit import audit_prompt_fidelity


def test_assembly_failure_is_a_deterministic_generation_block():
    script = ScriptBreakdown(
        title="组装", genre="测试", logline="测试", duration_sec=5,
        narration_style="无旁白",
        scenes=[Scene(scene_id=1, duration_sec=5, location="室内", characters=["甲"], action_description="甲站立", camera_direction="近景", emotion_tone="紧张")],
    )
    prompts = GenerationPrompts(
        api_provider="mock",
        shots=[ShotPrompt(scene_id=1, duration_sec=5, prompt="【环境】甲望向窗外\n【动作时间轴】0-5s，甲站立")],
        assembly_diagnostics=[PromptAssemblyDiagnostic(
            scene_id=1, forbidden_conflicts=["甲望向窗外"], status="failed"
        )],
    )
    report = audit_prompt_fidelity(script, prompts)
    assert report.status == "blocked"
    issue = next(item for item in report.issues if item.issue_code == "prompt_assembly_failed")
    assert issue.evidence.prompt_excerpt == "甲望向窗外"

def test_fidelity_issue_exposes_repair_action_for_deterministic_diagnosis():
    report = audit_prompt_fidelity(
        _script(),
        GenerationPrompts(api_provider="mock", shots=[ShotPrompt(scene_id=1, duration_sec=5, prompt="【动作时间轴】角色A站立")]),
    )
    assert next(item for item in report.issues if item.issue_code == "action_anchor_missing").repair_action == "edit_prompt"
