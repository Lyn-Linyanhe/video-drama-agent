"""Regression tests for current-shot entry/exit template fidelity."""

from src.schemas.prompt import ShotPrompt
from src.schemas.script import DialogueLine, Scene, ScriptBreakdown
from src.services.timed_prompt_composer import (
    _base_beats,
    _camera_label,
    _frame_label_for_characters,
    _current_event_text,
    _has_entrance_cue,
    _sync_duration_targets,
    apply_timed_prompt_structure,
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


def test_duration_sync_updates_only_shot_not_source_schedule():
    scene, script, shot = _fixture("event")
    scene.duration_sec = 5
    script.duration_sec = 5
    shot.duration_sec = 5

    _sync_duration_targets(shot, script, scene, 5, 8)

    assert shot.duration_sec == 8
    assert scene.duration_sec == 5
    assert script.duration_sec == 5


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



def test_overloaded_timed_prompt_exposes_capacity_without_rewriting_source_schedule():
    scene = Scene(
        scene_id=1,
        duration_sec=5,
        location="室内",
        characters=["角色A"],
        action_description="角色A走到桌边拿起钥匙并打开盒子",
        camera_direction="大特写",
        emotion_tone="紧张",
        dialogue=[DialogueLine(character="角色A", line="这是一段需要完整承载的较长对白。")],
    )
    script = ScriptBreakdown(
        title="fixture",
        genre="剧情",
        logline="fixture",
        duration_sec=5,
        narration_style="无旁白",
        scenes=[scene],
    )
    shot = ShotPrompt(
        scene_id=1,
        duration_sec=5,
        prompt="【镜头语言】大特写\n【主体状态】角色A走到桌边拿起钥匙并打开盒子",
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "【时间容量诊断】" in rendered
    assert "预计完整执行需要" in rendered
    assert "不得通过加速、跳过或压缩关键内容解决" in rendered
    assert shot.duration_sec == 5
    assert scene.duration_sec == 5
    assert script.duration_sec == 5



def test_apply_timed_prompt_structure_uses_provider_duration_cap():
    scene, script, shot = _fixture("德拉连续完成两个动作")
    shot.duration_sec = 8
    script.scenes[0].duration_sec = 8

    try:
        apply_timed_prompt_structure([shot], script, duration_cap_sec=5)
    except ValueError as exc:
        assert "8" in str(exc)
        assert "5" in str(exc)
    else:
        raise AssertionError("finalized over-cap timeline must fail instead of being shortened")

    assert "总时长约5s" not in shot.prompt
    assert shot.duration_sec == 8
    assert scene.duration_sec == 8
    assert script.duration_sec == 8


def test_timed_prompt_rejects_provider_duration_cap_below_executable_floor():
    scene, script, shot = _fixture("德拉完成动作")

    try:
        build_timed_shot_prompt(shot, script, duration_cap_sec=2)
    except ValueError as exc:
        assert "3" in str(exc)
    else:
        raise AssertionError("provider cap below 3 seconds must not produce an executable prompt")


def test_single_take_with_explicit_camera_cuts_is_recomposed_as_multi_cut():
    scene = Scene(
        scene_id=1,
        duration_sec=9,
        location="破旧公寓内部",
        characters=["德拉"],
        action_description=(
            "德拉停止哭泣后走到窗前，随后转身走到壁镜前，最后解开长发。"
        ),
        camera_direction="中景跟随，再切换中景，最后切至特写",
        emotion_tone="由哀伤转为坚定",
    )
    script = ScriptBreakdown(
        title="fixture", genre="剧情", logline="fixture", duration_sec=9,
        narration_style="无旁白", scenes=[scene],
    )
    shot = ShotPrompt(
        scene_id=1,
        duration_sec=9,
        prompt=(
            "【单镜头时间轴】\n"
            "【镜头语言】中景跟随，再切换中景，最后切至特写\n"
            "【动作时间轴】0-3秒，德拉停止哭泣后走到窗前；"
            "3-6秒，切换中景，德拉走到壁镜前；6-9秒，切至特写，德拉解开长发。\n"
            "【连续锁定】\n总时长约9s，单镜头连续完成，不切镜头。"
        ),
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "【时间轴分镜】" in rendered
    assert "【单镜头时间轴】" not in rendered
    assert "单镜头连续完成，不切镜头" not in rendered
    assert "切镜头" in rendered


def test_multi_cut_timeline_repairs_contradictory_single_take_camera_language():
    scene = Scene(
        scene_id=1,
        duration_sec=6,
        location="室内",
        characters=["甲"],
        action_description="甲打开纸包，随后抬头。",
        camera_direction="中景",
        emotion_tone="紧张",
    )
    script = ScriptBreakdown(
        title="fixture", genre="剧情", logline="fixture", duration_sec=6,
        narration_style="无旁白", scenes=[scene],
    )
    shot = ShotPrompt(
        scene_id=1,
        duration_sec=6,
        prompt=(
            "【时间轴分镜】\n"
            "【镜头语言】单镜头贯穿：先拍纸包特写，再推至人物面部\n"
            "0-3秒，特写纸包；切镜头，3-6秒，中景拍甲抬头。\n"
            "【连续锁定】\n总时长约6s，严格按上述时间轴执行，只在写明“切镜头”的位置切换。"
        ),
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "单镜头贯穿" not in rendered
    assert "按时间轴分段剪辑" in rendered
    assert "切镜头" in rendered


def test_special_entry_template_keeps_canonical_action_beats():
    scene = Scene(
        scene_id=1,
        duration_sec=15,
        location="破旧公寓内部",
        characters=["吉姆", "德拉"],
        action_description=(
            "七点整，吉姆推门进屋，站在门口纹丝不动，死死盯着德拉。"
            "德拉坐在桌角，手里握着表链，紧张地站起向他走去。"
            "见他沉默，她的表情从期待变为慌乱，开始急切地解释并用双手比划。"
        ),
        camera_direction="先中景，再反打近景，随后缓慢推进",
        emotion_tone="压迫、慌乱",
        dialogue=[
            DialogueLine(character="德拉", line="吉姆，亲爱的，别那样盯着我。", emotion="慌乱"),
        ],
    )
    script = ScriptBreakdown(
        title="fixture", genre="剧情", logline="fixture", duration_sec=15,
        narration_style="无旁白", scenes=[scene],
    )
    shot = ShotPrompt(
        scene_id=1,
        duration_sec=15,
        prompt=(
            "【镜头语言】中景固定\n"
            "【主体状态】吉姆推门进屋后站在门口，德拉保持紧张\n"
            "【动作时间轴】0-15秒，按顺序完成当前场景动作。"
        ),
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "德拉坐在桌角" in rendered
    assert "手里握着表链" in rendered
    assert "紧张地站起" in rendered
    assert "期待变为慌乱" in rendered
    assert "双手比划" in rendered


def test_insert_style_push_in_does_not_turn_a_single_take_into_cuts():
    scene = Scene(
        scene_id=1,
        duration_sec=8,
        location="室内",
        characters=["甲"],
        action_description="甲缓慢抬起照片，视线停在照片上。",
        camera_direction="单镜头贯穿，插入镜头式缓慢推近",
        emotion_tone="克制",
    )
    script = ScriptBreakdown(
        title="fixture", genre="剧情", logline="fixture", duration_sec=8,
        narration_style="无旁白", scenes=[scene],
    )
    shot = ShotPrompt(
        scene_id=1,
        duration_sec=8,
        prompt=(
            "【单镜头时间轴】\n"
            "【镜头语言】单镜头贯穿，插入镜头式缓慢推近\n"
            "【动作时间轴】0-8秒，甲缓慢抬起照片，视线停在照片上。\n"
            "【连续锁定】\n总时长约8s，单镜头连续完成，不切镜头。"
        ),
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "【单镜头时间轴】" in rendered
    assert "【时间轴分镜】" not in rendered
    assert "不切镜头" in rendered


def test_structured_current_scope_does_not_reactivate_later_canonical_action():
    scene, script, shot = _fixture("甲站在门口，随后拿起手机拨号")
    shot.prompt = "【镜头语言】大特写\n【主体状态】甲站在门口"

    event_text = _current_event_text(scene, shot.prompt)
    rendered = build_timed_shot_prompt(shot, script)

    assert "拿起手机" not in event_text
    assert "手机拨号" not in rendered
    assert "甲站在门口" in rendered


def test_explicit_current_timeline_does_not_restore_unmentioned_later_action():
    scene, script, shot = _fixture("甲站在门口，随后查看摄像头并切断线路")
    shot.prompt = (
        "【镜头语言】大特写\n"
        "【主体状态】甲站在门口\n"
        "【动作时间轴】0-8秒，甲站在门口保持警戒。"
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "查看摄像头" not in rendered
    assert "切断线路" not in rendered
    assert "甲站在门口" in rendered


def test_identity_reveal_keeps_named_revealed_character():
    scene = Scene(
        scene_id=1,
        duration_sec=15,
        location="旧钟楼地下室",
        characters=["苏眠", "顾衡", "陆临"],
        action_description=(
            "黑衣人摘下口罩露出陆临的脸，陆临逼近索要信封；"
            "顾衡冲上前和陆临扭打。"
        ),
        camera_direction="中景三人/侧面固定",
        emotion_tone="身份反转与动作冲突",
        dialogue=[
            DialogueLine(character="陆临", line="把信给我。", emotion="阴冷"),
        ],
    )
    script = ScriptBreakdown(
        title="fixture", genre="悬疑", logline="fixture", duration_sec=15,
        narration_style="无旁白", scenes=[scene],
    )
    shot = ShotPrompt(
        scene_id=1,
        duration_sec=15,
        prompt="陆临露出真面目，顾衡与陆临扭打。",
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "露出陆临的身份" in rendered
    assert "露出顾衡的身份" not in rendered


def test_approach_to_character_is_not_rewritten_as_entry_or_exit():
    scene = Scene(
        scene_id=1,
        duration_sec=15,
        location="贫困公寓内部",
        characters=["德拉", "吉姆"],
        action_description=(
            "吉姆推门进屋，站在门口不动；德拉起身向吉姆走出两三步，"
            "随后急切解释。"
        ),
        camera_direction="先中景，再近景反打",
        emotion_tone="紧张",
    )
    script = ScriptBreakdown(
        title="fixture", genre="剧情", logline="fixture", duration_sec=15,
        narration_style="无旁白", scenes=[scene],
    )
    shot = ShotPrompt(
        scene_id=1, duration_sec=15,
        prompt=(
            "【时间轴分镜】\n"
            "【镜头语言】从门内拍摄吉姆进屋，随后切到德拉反应\n"
            "核心分镜意图：吉姆进屋后德拉紧张地向他靠近\n"
            "0s-4s，吉姆推门进屋并停在门口。\n"
            "切镜头，4s-8s，德拉起身向吉姆走出两三步，急切解释。"
        ),
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "向吉姆走近两三步" in rendered
    assert "向吉姆进入两三步" not in rendered
    assert "向吉姆走出两三步" not in rendered


def test_approach_beat_does_not_keep_door_entry_sensory_fragment():
    scene = Scene(
        scene_id=1, duration_sec=10, location="公寓内部",
        characters=["德拉", "吉姆"],
        action_description="吉姆推门进屋，德拉向吉姆走出两三步。",
        camera_direction="中景", emotion_tone="紧张",
    )
    script = ScriptBreakdown(
        title="fixture", genre="剧情", logline="fixture", duration_sec=10,
        narration_style="无旁白", scenes=[scene],
    )
    shot = ShotPrompt(
        scene_id=1, duration_sec=10,
        prompt=(
            "【时间轴分镜】\n"
            "核心分镜意图：吉姆进屋后德拉向他靠近\n"
            "0s-3s，吉姆推门进屋。\n"
            "切镜头，3s-6s，德拉向吉姆走出两三步，重点表现手推门、门轴声、脚步和人物进入空间的方向。"
        ),
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "向吉姆走近两三步" in rendered
    assert "人物进入空间的方向" not in rendered
    assert "手推门、门轴声" not in rendered


def test_dynamic_shot_state_overrides_static_role_costume_anchor():
    scene = Scene(
        scene_id=1, duration_sec=8, location="贫困公寓内部",
        characters=["德拉"],
        action_description="德拉已剪去长发，留着贴头皮短卷发，身穿旧枣红衣裙。",
        camera_direction="中景", emotion_tone="克制",
    )
    script = ScriptBreakdown(
        title="fixture", genre="剧情", logline="fixture", duration_sec=8,
        narration_style="无旁白", scenes=[scene],
    )
    shot = ShotPrompt(
        scene_id=1, duration_sec=8,
        prompt=(
            "【时间轴分镜】\n"
            "【角色锚点】德拉：女，鹅蛋脸；穿旧褐色毛呢外套、素色长裙，戴褐色圆帽，深褐长发。\n"
            "核心分镜意图：德拉已剪去长发，留着贴头皮短卷发，身穿旧枣红衣裙。\n"
            "0s-8s，德拉保持短卷发和旧枣红衣裙，克制地站在室内。"
        ),
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "穿旧褐色毛呢外套" not in rendered
    assert "戴褐色圆帽" not in rendered
    assert "深褐长发" not in rendered
    assert "本镜发型、帽子和服装以" in rendered
    assert "鹅蛋脸" in rendered


def test_clean_role_anchor_is_preserved_without_dynamic_state_conflict():
    scene = Scene(
        scene_id=1, duration_sec=8, location="室内", characters=["甲"],
        action_description="甲坐在桌边保持安静。",
        camera_direction="中景", emotion_tone="平静",
    )
    script = ScriptBreakdown(
        title="fixture", genre="剧情", logline="fixture", duration_sec=8,
        narration_style="无旁白", scenes=[scene],
    )
    shot = ShotPrompt(
        scene_id=1, duration_sec=8,
        prompt=(
            "【时间轴分镜】\n"
            "【角色锚点】甲：女，短发，穿蓝色外套。\n"
            "核心分镜意图：甲坐在桌边。\n"
            "0s-8s，甲坐在桌边保持安静。"
        ),
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "穿蓝色外套" in rendered
    assert "本镜发型、帽子和服装以" not in rendered


def test_dynamic_state_sanitization_preserves_multi_character_anchor_boundaries():
    scene = Scene(
        scene_id=1, duration_sec=8, location="贫困公寓内部",
        characters=["德拉", "吉姆"],
        action_description="德拉已剪去长发，留着贴头皮短卷发；吉姆站在门口。",
        camera_direction="双人中景", emotion_tone="紧张",
    )
    script = ScriptBreakdown(
        title="fixture", genre="剧情", logline="fixture", duration_sec=8,
        narration_style="无旁白", scenes=[scene],
    )
    shot = ShotPrompt(
        scene_id=1, duration_sec=8,
        prompt=(
            "【时间轴分镜】\n"
            "【出镜角色与身份锚点】德拉：女，鹅蛋脸；穿旧褐色毛呢外套，戴褐色圆帽；左眉尾有小痣。；"
            "吉姆：男，方脸；深棕短发修剪利落，向右后梳；穿旧深灰色大衣，内穿格纹衬衫，深色长裤；"
            "右手背有旧疤。\n"
            "核心分镜意图：德拉留着贴头皮短卷发，吉姆站在门口。\n"
            "0s-8s，德拉和吉姆保持当前站位。"
        ),
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "；吉姆：" in rendered
    assert "吉姆：男，方脸" in rendered
    assert "右手背有旧疤" in rendered
    assert "，内，" not in rendered


def test_over_shoulder_view_is_not_overwritten_by_closeup():
    scene = Scene(
        scene_id=1, duration_sec=6, location="公寓", characters=["德拉"],
        action_description="德拉看向吉姆", camera_direction="", emotion_tone="紧张",
    )

    label = _camera_label(scene, "过肩特写，缓慢推近")

    assert "过肩" in label
    assert "正面" not in label


def test_single_character_without_angle_uses_neutral_three_quarter_view():
    scene = Scene(
        scene_id=1, duration_sec=6, location="公寓", characters=["德拉"],
        action_description="德拉整理桌上的纸包", camera_direction="", emotion_tone="克制",
    )

    label = _frame_label_for_characters(scene, ["德拉"])

    assert "三分之二侧前方" in label
    assert "正面" not in label


def test_explicit_front_view_remains_available():
    scene = Scene(
        scene_id=1, duration_sec=6, location="公寓", characters=["德拉"],
        action_description="德拉正面看向镜头", camera_direction="正面固定", emotion_tone="坚定",
    )

    label = _frame_label_for_characters(scene, ["德拉"])

    assert "正面" in label


def test_single_dialogue_fallback_uses_scene_aware_neutral_view():
    scene = Scene(
        scene_id=1, duration_sec=6, location="公寓", characters=["德拉"],
        dialogue=[DialogueLine(character="德拉", line="你回来了。")],
        action_description="德拉看向门口", camera_direction="", emotion_tone="克制",
    )
    script = ScriptBreakdown(
        title="视角测试", genre="剧情", logline="", duration_sec=6,
        narration_style="无旁白", scenes=[scene],
    )

    rendered = "；".join(description for _, description in _base_beats(scene, "", script))

    assert "三分之二侧前方" in rendered
    assert "中近景/正面固定" not in rendered


def test_angle_matched_character_reference_order_prioritizes_profile_or_back():
    from src.agents.video_gen_agent import VideoGenerationAgent

    paths = {
        "front_half": "front_half.png",
        "front_full": "front_full.png",
        "left_profile": "left_profile.png",
        "back_full": "back_full.png",
    }

    side = VideoGenerationAgent._character_reference_paths(paths, "侧面看向门口")
    back = VideoGenerationAgent._character_reference_paths(paths, "背面跟拍人物离开")
    neutral = VideoGenerationAgent._character_reference_paths(paths, "人物说话")

    assert side == ["left_profile.png"]
    assert back == ["back_full.png"]
    assert neutral == ["front_half.png"]


def test_happyhorse_accepts_integral_durations_across_official_range():
    from src.services.video_capabilities import normalize_video_policy, validate_video_duration

    policy = normalize_video_policy({
        "provider": "happyhorse",
        "model": "happyhorse-1.1-r2v",
        "resolution": "1080p",
    })

    for duration in (4, 6, 8, 9, 11, 14, 15):
        assert validate_video_duration(policy, duration) == duration


def test_finalized_timeline_is_normalized_to_shot_duration():
    scene, script, shot = _fixture("德拉观察表链并抬头看向吉姆")
    shot.duration_sec = 9
    scene.duration_sec = 9
    script.duration_sec = 9
    shot.prompt = (
        "【时间轴分镜】\n"
        "核心分镜意图：德拉观察表链并抬头看向吉姆\n"
        "总时长约12s\n"
        "0-3s，德拉观察表链；\n"
        "3-9s，德拉抬头看向吉姆；\n"
        "9-12s，德拉保持反应。"
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "总时长约9s" in rendered
    assert "9-12s" not in rendered
    import re
    ends = [float(match.group("end")) for match in re.finditer(r"(?P<start>\d+(?:\.\d+)?)s-(?P<end>\d+(?:\.\d+)?)s", rendered)]
    ends += [float(match.group("end")) for match in re.finditer(r"(?P<start>\d+(?:\.\d+)?)(?:s|秒)?-(?P<end>\d+(?:\.\d+)?)(?:s|秒)", rendered)]
    assert ends and max(ends) <= 9.0


def test_child_shot_keeps_scoped_action_timeline():
    scene, script, shot = _fixture(
        "杨氏子快步跑向院门，双手拉开木门；门外站着孔君平。杨氏子认出他来，连忙拱手行礼。"
    )
    shot.shot_id = "scene-3-shot-1"
    shot.duration_sec = 4
    shot.prompt = (
        "【镜头语言】固定中景跟拍，不要切镜头。\n"
        "【动作时间轴】0-4s 杨氏子快步跑向院门，双手拉开木门；门外站着孔君平。\n"
        "【本镜禁止执行】不得拱手行礼，不得说出对白。"
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "0-4s 杨氏子快步跑向院门" in rendered
    assert "连忙拱手行礼" not in rendered
