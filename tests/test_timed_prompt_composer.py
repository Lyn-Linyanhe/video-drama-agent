"""Regression tests for current-shot entry/exit template fidelity."""

from src.schemas.prompt import ShotPrompt
from src.schemas.script import DialogueLine, Scene, ScriptBreakdown
from src.services.timed_prompt_composer import (
    _base_beats,
    _current_event_text,
    _has_entrance_cue,
    build_timed_shot_prompt,
    detect_entry_exit_direction,
)


def _fixture(event_text: str, *, characters: list[str] | None = None):
    scene = Scene(
        scene_id=1,
        duration_sec=8,
        location="头发店内部",
        characters=characters or ["德拉"],
        action_description=event_text,
        camera_direction="中景",
        emotion_tone="紧张",
    )
    script = ScriptBreakdown(
        title="fixture",
        genre="剧情",
        logline="fixture",
        duration_sec=8,
        narration_style="无旁白",
        scenes=[scene],
    )
    # 大特写 forces the production path that currently invokes _base_beats,
    # matching the path where the historical template pollution occurred.
    shot = ShotPrompt(
        scene_id=1,
        duration_sec=8,
        prompt=f"【镜头语言】大特写\n【主体状态】{event_text}",
    )
    return scene, script, shot


def _render_scene_prompt(event_text: str, *, characters: list[str] | None = None) -> str:
    scene, script, shot = _fixture(event_text, characters=characters)
    return build_timed_shot_prompt(shot, script)


def test_shop_entry_does_not_become_exit():
    prompt = _render_scene_prompt("德拉推门进入头发店")

    assert "走出" not in prompt
    assert "进入" in prompt or "进店" in prompt


def test_husband_enters_home_does_not_add_exit_for_another_character():
    prompt = _render_scene_prompt(
        "吉姆推门进屋，站在门口盯着德拉",
        characters=["吉姆", "德拉"],
    )

    assert "走出" not in prompt
    assert "吉姆" in prompt
    assert "德拉" in prompt


def test_surveillance_alone_does_not_trigger_door_entry_template():
    scene, _, shot = _fixture("岑鸢通过门口监控观察楼道动静", characters=["岑鸢"])
    event_text = _current_event_text(scene, shot.prompt)

    assert _has_entrance_cue(event_text) is False
    beats = _base_beats(scene, shot.prompt)
    rendered = "；".join(description for _, description in beats)
    assert "被推开" not in rendered
    assert "门铃轻响" not in rendered
    assert "走出" not in rendered


def test_entry_without_explicit_gaze_does_not_add_eye_contact():
    prompt = _render_scene_prompt("德拉推门进入头发店，停在门口查看室内")

    assert "两人短暂对视" not in prompt
    assert "对视" not in prompt


def test_explicit_entry_without_push_door_is_still_an_entry():
    prompt = _render_scene_prompt("德拉快步走进咖啡店")

    assert "德拉进入" in prompt
    assert "走出" not in prompt

def test_negated_entry_does_not_trigger_entry_template():
    scene, _, shot = _fixture("德拉没有进入房间，只站在门外观察")
    event_text = _current_event_text(scene, shot.prompt)

    assert _has_entrance_cue(event_text) is False
    beats = _base_beats(scene, shot.prompt)
    rendered = "；".join(description for _, description in beats)
    assert "进入头发店内部" not in rendered
    assert "被推开" not in rendered

def test_exit_actions_keep_both_primary_and_following_exit_subjects():
    prompt = _render_scene_prompt("林默走出店门，阿岚追出来把热拿铁塞进他手里", characters=["林默", "阿岚"])

    assert "林默走出店门" in prompt
    assert "阿岚追出来" in prompt
    assert "被推开" not in prompt

def test_current_entry_evidence_is_not_overridden_by_later_scene_exit():
    scene, script, shot = _fixture("德拉推门进入头发店，随后走出")
    shot.prompt = "【镜头语言】大特写\n【主体状态】德拉推门进入头发店"

    prompt = build_timed_shot_prompt(shot, script)

    assert "德拉进入" in prompt
    assert "走出" not in prompt


def test_direction_evidence_combines_structured_sections_for_current_entry():
    scene, script, shot = _fixture("德拉站在门口，随后走出")
    shot.prompt = "【镜头语言】大特写\n【主体状态】德拉站在门口\n【动作时间轴】德拉推门进入理发店"

    prompt = build_timed_shot_prompt(shot, script)

    assert "德拉进入" in prompt
    assert "走出" not in prompt


def test_camera_exit_phrase_does_not_become_scene_exit_event():
    scene, _, shot = _fixture("德拉走出画面，镜头停住")
    event_text = _current_event_text(scene, shot.prompt)

    assert _has_entrance_cue(event_text) is False
    assert detect_entry_exit_direction(event_text) == "none"


def test_negated_entry_phrases_do_not_trigger_entry_template():
    for phrase in ("无需进入房间", "禁止进入房间", "不必走进房间"):
        scene, _, shot = _fixture(f"德拉{phrase}")
        event_text = _current_event_text(scene, shot.prompt)

        assert _has_entrance_cue(event_text) is False


def test_exit_event_does_not_duplicate_generic_physical_action_beat():
    scene, _, shot = _fixture("德拉跑出店门")
    beats = _base_beats(scene, shot.prompt)

    physical_beats = [description for kind, description in beats if kind == "physical_action"]
    assert len(physical_beats) == 1


def test_exit_event_keeps_independent_phone_beat():
    scene, _, shot = _fixture("德拉跑出店门，拿起手机拨号")
    beats = _base_beats(scene, shot.prompt)

    assert any(kind == "phone" for kind, _ in beats)


def test_finalized_entry_line_is_repaired_against_scene_direction():
    scene, script, shot = _fixture("德拉在街边停下，随后快步走进店铺")
    shot.prompt = (
        "【时间轴分镜】\n"
        "核心分镜意图：德拉在街边停下，随后快步走进店铺\n"
        "0.8s，中近景/侧面固定，店内门被推开，德拉走出；两人短暂对视，德拉。"
    )

    prompt = build_timed_shot_prompt(shot, script)

    assert "德拉走出" not in prompt
    assert "德拉进入" in prompt or "走进店铺" in prompt
    assert "两人短暂对视" not in prompt


def test_finalized_timeline_restores_missing_script_dialogue():
    line = "假如吉姆看我一眼不把我宰掉的话，他定会说我像个科尼岛上合唱队的卖唱姑娘。但是我能怎么办呢——唉，只有一元八角七，我能干什么呢？"
    scene = Scene(
        scene_id=1,
        duration_sec=8,
        location="破旧公寓客厅内",
        characters=["德拉"],
        action_description="德拉对着镜子整理头发。",
        camera_direction="中景",
        emotion_tone="自嘲又无奈",
        dialogue=[DialogueLine(character="德拉", line=line, emotion="自嘲又无奈")],
    )
    script = ScriptBreakdown(
        title="fixture", genre="剧情", logline="fixture", duration_sec=8,
        narration_style="无旁白", scenes=[scene],
    )
    shot = ShotPrompt(
        scene_id=1,
        duration_sec=8,
        prompt="【时间轴分镜】\n核心分镜意图：德拉对着镜子整理头发。\n0-8秒，德拉整理头发。",
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert line in rendered
    assert "德拉：“" in rendered
