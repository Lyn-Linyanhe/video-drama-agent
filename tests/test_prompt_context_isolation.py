from src.agents.visual_prompt_agent import (
    _sanitize_environment_with_drops,
    build_shot_environment_context,
)
from src.schemas.prompt import PromptAssemblyDiagnostic
from src.schemas.script import (
    LocationAsset,
    ScriptBreakdown,
    Scene,
    ShotVisualBinding,
    VisualAssetManifest,
)


def _script_with_location() -> ScriptBreakdown:
    scene = Scene(
        scene_id=1,
        duration_sec=8,
        location="破旧公寓起居室内部",
        characters=["角色A"],
        action_description="角色A走到窗前，灰猫从窗台窜过；她转身离开；墙面斑驳。",
        camera_direction="中景固定",
        emotion_tone="压抑",
    )
    location = LocationAsset(
        location_id="loc_home",
        canonical_name="破旧公寓起居室内部",
        identity_description="破旧公寓起居室内部",
        fixed_dressing="墙面斑驳；窗帘半掩",
        environment_description="破旧公寓起居室；走到窗前；窗台上一只灰猫窜过；红地毯上溅出泪痕",
        representative_scene_id=1,
        shot_ids=[1],
    )
    manifest = VisualAssetManifest(
        characters=[],
        locations=[location],
        props=[],
        shot_bindings=[ShotVisualBinding(scene_id=1, location_id="loc_home")],
    )
    return ScriptBreakdown(
        title="测试",
        genre="剧情",
        logline="测试",
        duration_sec=8,
        narration_style="无",
        scenes=[scene],
        asset_manifest=manifest,
    )


def test_environment_sanitizer_reports_and_removes_dynamic_clauses():
    clean, dropped = _sanitize_environment_with_drops(
        "破旧公寓起居室；走到窗前；窗台上一只灰猫窜过；红地毯上溅出泪痕；墙面斑驳；窗帘半掩"
    )

    assert clean == "破旧公寓起居室；墙面斑驳；窗帘半掩"
    assert "走到窗前" in dropped
    assert "窗台上一只灰猫窜过" in dropped
    assert "红地毯上溅出泪痕" in dropped


def test_shot_environment_context_uses_only_stable_location_facts_and_current_static_delta():
    script = _script_with_location()
    context = build_shot_environment_context(
        script,
        1,
        "说话时视线偏向窗边；墙面斑驳；室内冷光持续",
    )

    assert "墙面斑驳" in context
    assert "室内冷光持续" in context
    assert "走到窗前" not in context
    assert "灰猫从窗台窜过" not in context
    assert "溅出泪痕" not in context
    assert context.count("破旧公寓起居室内部") <= 1


def test_environment_context_does_not_repeat_canonical_name_inside_long_location_fact():
    script = _script_with_location()
    script.asset_manifest.locations[0].environment_description = "破旧公寓起居室内部墙面斑驳；窗帘半掩"

    context = build_shot_environment_context(script, 1, "")

    assert context.count("破旧公寓起居室内部") == 1
    assert "破旧公寓起居室内部墙面斑驳" in context


def test_generation_prompts_persist_assembly_diagnostics_with_legacy_default():
    diagnostic = PromptAssemblyDiagnostic(
        scene_id=1,
        dropped_facts=["走到窗前"],
        status="warning",
    )
    assert diagnostic.status == "warning"
    assert diagnostic.dropped_facts == ["走到窗前"]
