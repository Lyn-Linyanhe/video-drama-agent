from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.script import LocationAsset, PropAsset, Scene, ScriptBreakdown, VisualAssetManifest
from src.services.prompt_fidelity_audit import audit_prompt_fidelity
from src.services.timed_prompt_composer import _timeline_from_text


def _script_with_manifest():
    return ScriptBreakdown(
        title="资产", genre="测试", logline="测试", duration_sec=5,
        narration_style="none",
        scenes=[Scene(scene_id=1, duration_sec=5, location="地下室", characters=[], action_description="观察", camera_direction="近景", emotion_tone="紧张")],
        asset_manifest=VisualAssetManifest(
            locations=[LocationAsset(location_id="loc-basement", canonical_name="地下室", environment_description="铁门", representative_scene_id=1)],
            props=[PropAsset(prop_id="old-key", name="钥匙", description="黄铜", shot_ids=[1])],
        ),
    )


def test_timeline_parser_splits_comma_delimited_ranges():
    parsed = _timeline_from_text("0-2秒，动作一，2-4秒，动作二", 4)
    assert [description for _, description in parsed] == ["动作一", "动作二"]
    assert sum(duration for duration, _ in parsed) == 4


def test_closed_door_negative_constraint_is_not_a_real_exit_action():
    script = ScriptBreakdown(
        title="门", genre="测试", logline="测试", duration_sec=5,
        narration_style="none",
        scenes=[Scene(scene_id=1, duration_sec=5, location="室内", characters=["甲"], action_description="门始终关闭，甲只能通过监控观察", camera_direction="近景", emotion_tone="紧张")],
    )
    prompts = GenerationPrompts(api_provider="mock", shots=[ShotPrompt(scene_id=1, duration_sec=5, prompt="【动作时间轴】甲通过监控观察。\n【连续锁定】不得推门、不得走出。")])
    report = audit_prompt_fidelity(script, prompts)
    assert report.status == "clean"
    assert not any(item.issue_code == "forbidden_exit_action" for item in report.issues)


def test_asset_audit_uses_location_and_prop_ids():
    script = _script_with_manifest()
    prompts = GenerationPrompts(api_provider="mock", shots=[ShotPrompt(scene_id=1, duration_sec=5, prompt="【动作时间轴】观察。\nlocation_id:wrong-location\n@asset:wrong-prop")])
    report = audit_prompt_fidelity(script, prompts)
    assert {item.issue_code for item in report.issues} >= {"invalid_asset_reference"}
    assert report.status == "blocked"
