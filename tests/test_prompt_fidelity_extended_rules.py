from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.script import LocationAsset, Scene, ScriptBreakdown, VisualAssetManifest, VisualCharacterAsset
from src.services.prompt_fidelity_audit import audit_prompt_fidelity


def _script(scenes, manifest=None):
    return ScriptBreakdown(
        title="保真规则测试", genre="测试", logline="测试", duration_sec=sum(s.duration_sec for s in scenes),
        narration_style="无旁白", scenes=scenes, asset_manifest=manifest,
    )


def _prompts(*values):
    return GenerationPrompts(api_provider="mock", shots=[ShotPrompt(scene_id=i + 1, duration_sec=5, prompt=value) for i, value in enumerate(values)])


def _codes(report):
    return {issue.issue_code for issue in report.issues}


def test_entry_exit_conflict_requires_opposite_direction_evidence():
    script = _script([Scene(scene_id=1, duration_sec=5, location="室内", characters=["A"], action_description="角色A进入房间", camera_direction="中景", emotion_tone="紧张")])
    report = audit_prompt_fidelity(script, _prompts("【动作时间轴】角色A走出房间。"))
    assert "entry_exit_conflict" in _codes(report)
    issue = next(item for item in report.issues if item.issue_code == "entry_exit_conflict")
    assert issue.blocks_generation is True
    assert issue.evidence.expected_excerpt
    assert issue.evidence.prompt_excerpt


def test_cross_shot_dynamic_leak_only_reports_previous_action_in_environment():
    script = _script([
        Scene(scene_id=1, duration_sec=5, location="窗边", characters=["A"], action_description="雨水持续冲刷窗户", camera_direction="近景", emotion_tone="压抑"),
        Scene(scene_id=2, duration_sec=5, location="室内", characters=["A"], action_description="角色A抬头观察", camera_direction="近景", emotion_tone="紧张", continuity_from_prev=False),
    ])
    report = audit_prompt_fidelity(script, _prompts(
        "【动作时间轴】雨水持续冲刷窗户。",
        "【动作时间轴】角色A抬头观察。\n【环境】上一镜动作：雨水持续冲刷窗户。",
    ))
    assert "cross_shot_dynamic_leak" in _codes(report)
    issue = next(item for item in report.issues if item.issue_code == "cross_shot_dynamic_leak")
    assert issue.evidence.prompt_section == "环境"


def test_mutable_character_state_conflict_only_checks_global_identity_anchor():
    manifest = VisualAssetManifest(
        characters=[VisualCharacterAsset(asset_id="hero", name="角色A", appearance_mode="on_screen", description="")],
        locations=[LocationAsset(location_id="room", canonical_name="室内", representative_scene_id=1)],
    )
    script = _script([Scene(scene_id=1, duration_sec=5, location="室内", characters=["角色A"], action_description="角色A站立", camera_direction="近景", emotion_tone="平静")], manifest)
    report = audit_prompt_fidelity(script, _prompts("【出镜角色与身份锚点】角色A：长发、卷发；同时固定为短发、直发。\n【动作时间轴】角色A站立。"))
    assert "mutable_character_state_conflict" in _codes(report)
    assert report.status == "blocked"


def test_future_event_leak_requires_exact_future_action_and_allows_explicit_flashback():
    scenes = [
        Scene(scene_id=1, duration_sec=5, location="室内", characters=["A"], action_description="角色A观察桌面", camera_direction="近景", emotion_tone="紧张"),
        Scene(scene_id=2, duration_sec=5, location="室内", characters=["A"], action_description="角色A打开保险箱拿出地图", camera_direction="特写", emotion_tone="揭示"),
    ]
    script = _script(scenes)
    report = audit_prompt_fidelity(script, _prompts(
        "【动作时间轴】角色A观察桌面。\n【画面内容】未来镜头预先出现：角色A打开保险箱拿出地图。",
        "【动作时间轴】角色A打开保险箱拿出地图。",
    ))
    assert "future_event_leak" in _codes(report)

    flashback = _script([
        Scene(scene_id=1, duration_sec=5, location="室内", characters=["A"], action_description="闪回：角色A打开保险箱拿出地图", camera_direction="特写", emotion_tone="回忆"),
        scenes[1],
    ])
    clean = audit_prompt_fidelity(flashback, _prompts("【动作时间轴】闪回：角色A打开保险箱拿出地图。", "【动作时间轴】角色A打开保险箱拿出地图。"))
    assert "future_event_leak" not in _codes(clean)


def test_timeline_overflow_uses_explicit_end_time_only():
    script = _script([Scene(scene_id=1, duration_sec=5, location="室内", characters=["A"], action_description="角色A观察桌面", camera_direction="近景", emotion_tone="紧张")])
    report = audit_prompt_fidelity(script, _prompts("【动作时间轴】0-8s，角色A观察桌面。"))
    assert "timeline_overflow" in _codes(report)
    issue = next(item for item in report.issues if item.issue_code == "timeline_overflow")
    assert issue.blocks_generation is True
    clean = audit_prompt_fidelity(script, _prompts("【动作时间轴】0-5s，角色A观察桌面。"))
    assert "timeline_overflow" not in _codes(clean)


def test_continuous_environment_state_is_not_dynamic_leak():
    script = _script([
        Scene(scene_id=1, duration_sec=5, location="窗边", characters=["A"], action_description="雨水持续冲刷窗户", camera_direction="近景", emotion_tone="压抑"),
        Scene(scene_id=2, duration_sec=5, location="室内", characters=["A"], action_description="角色A抬头观察", camera_direction="近景", emotion_tone="紧张", continuity_from_prev=True),
    ])
    report = audit_prompt_fidelity(script, _prompts(
        "【动作时间轴】雨水持续冲刷窗户。",
        "【动作时间轴】角色A抬头观察。\n【环境】雨水持续冲刷窗户。",
    ))
    assert "cross_shot_dynamic_leak" not in _codes(report)


def test_cross_shot_and_future_evidence_are_actual_prompt_text():
    cross_script = _script([
        Scene(scene_id=1, duration_sec=5, location="窗边", characters=["A"], action_description="雨水持续冲刷窗户", camera_direction="近景", emotion_tone="压抑"),
        Scene(scene_id=2, duration_sec=5, location="室内", characters=["A"], action_description="角色A观察桌面", camera_direction="近景", emotion_tone="紧张"),
    ])
    cross_prompt = "【动作时间轴】角色A观察桌面。\n【环境】雨水持续冲刷 窗户。"
    cross_report = audit_prompt_fidelity(cross_script, _prompts("【动作时间轴】雨水持续冲刷窗户。", cross_prompt))
    cross = next(item for item in cross_report.issues if item.issue_code == "cross_shot_dynamic_leak")
    assert cross.evidence.prompt_excerpt == "雨水持续冲刷 窗户"

    future_script = _script([
        Scene(scene_id=1, duration_sec=5, location="室内", characters=["A"], action_description="角色A观察桌面", camera_direction="近景", emotion_tone="紧张"),
        Scene(scene_id=2, duration_sec=5, location="室内", characters=["A"], action_description="角色A打开保险箱拿出地图", camera_direction="近景", emotion_tone="紧张"),
    ])
    future_prompt = "【动作时间轴】角色A观察桌面。\n【画面内容】角色A打开保险箱拿出地图。"
    future_report = audit_prompt_fidelity(future_script, _prompts(future_prompt, "【动作时间轴】角色A打开保险箱拿出地图。"))
    future = next(item for item in future_report.issues if item.issue_code == "future_event_leak")
    assert future.evidence.prompt_excerpt == "角色A打开保险箱拿出地图"
    assert future.evidence.prompt_section == "画面内容"


def test_entry_exit_conflict_does_not_reject_scripted_multi_stage_direction():
    script = _script([Scene(scene_id=1, duration_sec=5, location="室内", characters=["A"], action_description="角色A走进房间，然后离开房间", camera_direction="中景", emotion_tone="紧张")])
    report = audit_prompt_fidelity(script, _prompts("【动作时间轴】角色A走进房间，随后走出房间。"))
    assert "entry_exit_conflict" not in _codes(report)


def test_non_contiguous_scene_ids_are_rejected():
    scenes = [
        Scene(scene_id=2, duration_sec=5, location="室内", characters=["A"], action_description="角色A观察", camera_direction="近景", emotion_tone="紧张"),
        Scene(scene_id=3, duration_sec=5, location="室内", characters=["A"], action_description="角色A观察", camera_direction="近景", emotion_tone="紧张"),
    ]
    script = _script(scenes)
    prompts = GenerationPrompts(api_provider="mock", shots=[ShotPrompt(scene_id=2, duration_sec=5, prompt="【动作时间轴】角色A观察。"), ShotPrompt(scene_id=3, duration_sec=5, prompt="【动作时间轴】角色A观察。")])
    report = audit_prompt_fidelity(script, prompts)
    assert "non_contiguous_scene_ids" in _codes(report)


def test_character_alias_matching_does_not_use_unsafe_substrings():
    manifest = VisualAssetManifest(
        characters=[
            VisualCharacterAsset(asset_id="a", name="A", appearance_mode="on_screen", description=""),
            VisualCharacterAsset(asset_id="alice", name="Alice", appearance_mode="on_screen", description=""),
        ],
        locations=[LocationAsset(location_id="room", canonical_name="室内", representative_scene_id=1)],
    )
    script = _script([Scene(scene_id=1, duration_sec=5, location="室内", characters=["Alice"], action_description="Alice站立", camera_direction="近景", emotion_tone="平静")], manifest)
    report = audit_prompt_fidelity(script, _prompts("【出镜角色与身份锚点】Alice：长发、短发。\n【动作时间轴】Alice站立。"))
    issues = [item for item in report.issues if item.issue_code == "mutable_character_state_conflict"]
    assert len(issues) == 1
    assert issues[0].evidence.fact_id.endswith(":alice:state")
