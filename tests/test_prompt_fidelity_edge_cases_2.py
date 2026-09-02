from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.script import Scene, ScriptBreakdown
from src.services.prompt_fidelity_audit import audit_prompt_fidelity


def test_action_anchor_does_not_count_when_only_in_non_action_lock():
    script = ScriptBreakdown(
        title="动作", genre="测试", logline="测试", duration_sec=5,
        narration_style="none",
        scenes=[Scene(scene_id=1, duration_sec=5, location="室内", characters=["甲"], action_description="甲拿起钥匙", camera_direction="近景", emotion_tone="紧张")],
    )
    prompts = GenerationPrompts(api_provider="mock", shots=[ShotPrompt(scene_id=1, duration_sec=5, prompt="【动作时间轴】甲站立。\n【连续锁定】必须保留甲拿起钥匙的动作。")])
    report = audit_prompt_fidelity(script, prompts)
    assert any(item.issue_code == "action_anchor_missing" for item in report.issues)
