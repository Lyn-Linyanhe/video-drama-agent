from src.schemas.prompt import ShotPrompt
from src.schemas.script import DialogueLine, Scene
from src.services.shot_capacity import analyze_shot_capacity


def _scene(duration=5, action="角色A拿起钥匙，打开门，冲出房间，回头观察"):
    return Scene(
        scene_id=1,
        duration_sec=duration,
        location="室内",
        characters=["角色A"],
        dialogue=[DialogueLine(character="角色A", line="快走，外面有人。")],
        action_description=action,
        camera_direction="近景切中景再推到特写",
        emotion_tone="紧张",
    )


def test_capacity_diagnostic_warns_without_mutating_source():
    scene = _scene()
    prompt = ShotPrompt(scene_id=1, prompt="动作时间轴：0-5秒", duration_sec=5)
    diagnostic = analyze_shot_capacity(scene, prompt, provider_max_duration_sec=5)

    assert diagnostic.status == "warning"
    assert diagnostic.action_units >= 4
    assert diagnostic.dialogue_chars == len("快走，外面有人。")
    assert diagnostic.suggested_shot_count >= 2
    assert diagnostic.provider_max_duration_sec == 5
    assert scene.duration_sec == 5
    assert prompt.duration_sec == 5


def test_capacity_diagnostic_is_clean_for_short_single_action():
    scene = _scene(duration=8, action="角色A抬眼看向窗外")
    scene.dialogue = []
    prompt = ShotPrompt(scene_id=1, prompt="", duration_sec=8)
    diagnostic = analyze_shot_capacity(scene, prompt)

    assert diagnostic.status == "clean"
    assert diagnostic.suggested_shot_count == 1
    assert diagnostic.warnings == []


def test_capacity_diagnostic_reports_provider_cap_as_warning():
    scene = _scene(duration=12, action="角色A缓慢走向门边")
    scene.dialogue = []
    prompt = ShotPrompt(scene_id=1, prompt="", duration_sec=12)
    diagnostic = analyze_shot_capacity(scene, prompt, provider_max_duration_sec=10)

    assert diagnostic.provider_max_duration_sec == 10
    assert diagnostic.effective_duration_sec == 10
    assert diagnostic.status == "warning"
    assert any("上限" in item for item in diagnostic.warnings)
