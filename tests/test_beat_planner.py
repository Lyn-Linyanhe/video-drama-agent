from src.schemas.script import DialogueLine, Scene
from src.services.beat_planner import build_narrative_beats, estimate_scene_capacity


def _scene(*, duration=8, action="德拉走到门边，拿起钥匙，打开门，走出房间"):
    return Scene(
        scene_id=6,
        duration_sec=duration,
        location="破旧公寓",
        characters=["德拉"],
        dialogue=[DialogueLine(character="德拉", line="我只有一元八角七，我能干什么呢？", emotion="焦虑")],
        action_description=action,
        camera_direction="中景切特写",
        emotion_tone="紧张",
    )


def test_build_narrative_beats_preserves_order_and_semantic_kinds():
    beats = build_narrative_beats(_scene())

    assert [beat.order for beat in beats] == list(range(1, len(beats) + 1))
    assert [beat.kind for beat in beats] == [
        "action",
        "prop_change",
        "action",
        "transition",
        "dialogue",
        "camera",
        "emotion",
    ]
    assert all(beat.scene_id == 6 for beat in beats)
    assert all(beat.beat_id.startswith("scene-6-beat-") for beat in beats)
    assert any("一元八角七" in beat.source_excerpt for beat in beats)


def test_build_narrative_beats_keeps_inner_monologue_as_required_audio_beat():
    scene = _scene(action="德拉看着镜中的短发，叹了口气")
    scene.dialogue = [
        DialogueLine(
            character="德拉",
            line="假如吉姆看我一眼不把我宰掉的话，他定会说我像个卖唱姑娘。",
            emotion="内心独白",
        )
    ]

    beats = build_narrative_beats(scene)
    monologue = next(beat for beat in beats if beat.kind == "inner_monologue")

    assert monologue.required is True
    assert monologue.dialogue_id == "scene-6-dialogue-1"
    assert monologue.source_excerpt == scene.dialogue[0].line


def test_estimate_scene_capacity_does_not_clamp_required_duration_to_provider_cap():
    report = estimate_scene_capacity(_scene(duration=8), provider_max_duration_sec=8)

    assert report.required_total_duration_sec > report.available_duration_sec
    # Real dialogue/action overload may expand the execution budget, but only
    # to the compact evidence-based estimate rather than legacy inflated sums.
    assert report.recommended_total_duration_sec >= report.required_total_duration_sec
    assert report.recommended_total_duration_sec <= report.required_total_duration_sec + 1
    assert sum(report.recommended_durations_sec) == report.recommended_total_duration_sec
    assert report.status == "duration_insufficient"
    assert report.recommended_shot_count >= 2


def test_estimate_scene_capacity_keeps_simple_scene_clean():
    scene = _scene(duration=8, action="德拉抬眼看向窗外")
    scene.dialogue = []
    report = estimate_scene_capacity(scene)

    assert report.status == "clean"
    assert report.recommended_shot_count == 1
    assert report.recommended_durations_sec == [report.recommended_total_duration_sec]


def test_estimate_scene_capacity_splits_scene_duration_that_exceeds_provider_cap():
    scene = _scene(duration=12, action="德拉站在门边")
    scene.dialogue = []

    report = estimate_scene_capacity(scene, provider_max_duration_sec=10)

    assert report.status == "auto_split"
    assert report.recommended_shot_count == 2
    assert sum(report.recommended_durations_sec) == 12
    assert all(duration <= 10 for duration in report.recommended_durations_sec)


def test_build_narrative_beats_does_not_turn_descriptive_commas_into_serial_actions():
    scene = _scene(duration=8, action="昏暗的房间里，德拉站在窗边，神情疲惫")
    scene.dialogue = []
    scene.camera_direction = "固定中景"
    scene.emotion_tone = ""

    beats = build_narrative_beats(scene)

    action_beats = [beat for beat in beats if beat.kind == "action"]
    assert len(action_beats) == 1
    assert "德拉站在窗边" in action_beats[0].source_excerpt
    assert "神情疲惫" in action_beats[0].source_excerpt


def test_build_narrative_beats_splits_compound_action_transitions_and_props():
    scene = _scene(duration=8, action="德拉走到桌边拿起钥匙并打开盒子")
    scene.dialogue = []
    scene.camera_direction = "固定中景"
    scene.emotion_tone = ""

    beats = build_narrative_beats(scene)

    assert [beat.source_excerpt for beat in beats] == [
        "德拉走到桌边",
        "拿起钥匙",
        "打开盒子",
        "固定中景",
    ]
    assert [beat.kind for beat in beats] == ["action", "prop_change", "prop_change", "camera"]


def test_build_narrative_beats_can_use_explicit_dialogue_order():
    scene = _scene(duration=8, action="德拉抬头看向吉姆，然后低头")
    scene.dialogue = [
        DialogueLine(character="德拉", line="你回来了。", order=2),
    ]
    scene.camera_direction = "固定中景"
    scene.emotion_tone = ""

    beats = build_narrative_beats(scene)

    assert [beat.source_excerpt for beat in beats[:3]] == [
        "德拉抬头看向吉姆",
        "你回来了。",
        "低头",
    ]


def test_capacity_diagnostic_preserves_planning_status_without_breaking_legacy_status():
    diagnostic = __import__("src.services.shot_capacity", fromlist=["analyze_shot_capacity"]).analyze_shot_capacity(
        _scene(duration=12, action="德拉站在门边"),
        provider_max_duration_sec=10,
    )

    assert diagnostic.status == "warning"
    assert diagnostic.planning_status == "auto_split"
    assert diagnostic.recommended_durations_sec == [6.0, 6.0]


def test_dialogue_clock_uses_compact_drama_pacing_without_large_per_line_tax():
    from src.services.beat_planner import estimate_dialogue_duration_sec

    assert estimate_dialogue_duration_sec("这是一句十个字左右的对白") < 3.0
    assert estimate_dialogue_duration_sec("我把头发卖掉了，因为我要给你买一件圣诞礼物。") < 5.0


def test_capacity_expands_only_to_compact_dialogue_need():
    scene = _scene(duration=12, action="德拉站在桌边")
    scene.dialogue = [DialogueLine(character="德拉", line="这是一段较长的对白。" * 8)]

    report = estimate_scene_capacity(
        scene, provider_min_duration_sec=4, provider_max_duration_sec=15,
    )

    assert report.required_total_duration_sec > report.available_duration_sec
    assert report.recommended_total_duration_sec >= report.required_total_duration_sec
    assert report.recommended_total_duration_sec <= report.required_total_duration_sec + 1
    assert sum(report.recommended_durations_sec) == report.recommended_total_duration_sec
    assert all(4 <= value <= 15 for value in report.recommended_durations_sec)


def test_multiple_parallel_action_clauses_use_distinct_timing_groups():
    scene = _scene(
        duration=4,
        action="德拉一边收起零钱一边走向镜子；吉姆同时拿起外套",
    )
    scene.dialogue = []
    scene.camera_direction = ""
    scene.emotion_tone = ""

    beats = build_narrative_beats(scene)
    overlap_beats = [beat for beat in beats if beat.timing_role == "overlap"]

    assert len(overlap_beats) == 2
    assert len({beat.overlap_group for beat in overlap_beats}) == 2
    report = estimate_scene_capacity(scene, provider_min_duration_sec=4, provider_max_duration_sec=15)
    assert report.overlap_duration_sec == sum(beat.duration.expected_sec for beat in overlap_beats)


def test_generic_parallel_action_does_not_make_unrelated_dialogue_free():
    scene = _scene(duration=4, action="德拉同时拿起发梳")
    scene.dialogue = [DialogueLine(character="德拉", line="吉姆就要回来了。") ]
    scene.camera_direction = ""
    scene.emotion_tone = ""

    beats = build_narrative_beats(scene)
    dialogue = next(beat for beat in beats if beat.kind == "dialogue")

    assert dialogue.timing_role == "serial"
    report = estimate_scene_capacity(scene, provider_min_duration_sec=4, provider_max_duration_sec=15)
    action = next(beat for beat in beats if beat.kind in {"action", "prop_change"})
    assert report.required_total_duration_sec == round(
        action.duration.expected_sec + dialogue.duration.expected_sec, 2
    )


def test_explicit_speaking_action_overlaps_one_dialogue_line():
    scene = _scene(duration=4, action="德拉说着把零钱倒在桌上")
    scene.dialogue = [
        DialogueLine(character="德拉", line="我一定要给吉姆买一件礼物。"),
        DialogueLine(character="德拉", line="可是这些钱远远不够。"),
    ]
    scene.camera_direction = ""
    scene.emotion_tone = ""

    beats = build_narrative_beats(scene)
    dialogue_beats = [beat for beat in beats if beat.kind == "dialogue"]
    action = next(beat for beat in beats if beat.kind in {"action", "prop_change"})

    assert dialogue_beats[0].timing_role == "overlap"
    assert dialogue_beats[0].overlap_group == action.overlap_group
    assert dialogue_beats[1].timing_role == "serial"


def test_build_narrative_beats_splits_semicolon_and_compound_action_chains():
    scene = _scene(
        duration=12,
        action="七点整，吉姆推门进屋，站在门口死死盯着德拉；德拉握着白金表链，紧张地站起来向他走去。见他沉默不语，她急切地开口解释。",
    )
    scene.dialogue = []
    scene.camera_direction = ""
    scene.emotion_tone = ""

    excerpts = [beat.source_excerpt for beat in build_narrative_beats(scene)]
    assert any("推门进屋" in item or "进门" in item or "进屋" in item for item in excerpts)
    assert any("站起来" in item or "走去" in item for item in excerpts)
    assert any("开口解释" in item or "解释" in item for item in excerpts)
    assert len([item for item in excerpts if item]) >= 3


def test_expand_overlong_speech_beats_splits_line_that_exceeds_provider_cap():
    from src.services.beat_planner import expand_overlong_speech_beats, estimate_dialogue_duration_sec
    scene = _scene(duration=12, action="德拉望着镜子")
    scene.dialogue = [DialogueLine(
        character="德拉",
        line="吉姆，亲爱的，别那样盯着我。我把头发剪掉卖了，因为不送你一件礼物，我无法过圣诞节。头发会再长起来——你不会介意，是吗？我非这么做不可。我的头发长得快极了。说恭贺圣诞吧！吉姆，让我们快快乐乐的。你肯定猜不着我给你买了一件多么好的——多么美丽精致的礼物啊！",
    )]
    scene.camera_direction = ""
    scene.emotion_tone = ""
    beats = build_narrative_beats(scene)
    speech = [beat for beat in beats if beat.kind == "dialogue"]
    assert len(speech) == 1
    assert estimate_dialogue_duration_sec(speech[0].source_excerpt) > 14
    expanded = expand_overlong_speech_beats(beats, 15)
    parts = [beat for beat in expanded if beat.kind == "dialogue"]
    assert len(parts) >= 2
    assert all(estimate_dialogue_duration_sec(beat.source_excerpt) <= 14 for beat in parts)
    assert "".join(beat.source_excerpt for beat in parts).replace(" ", "") in speech[0].source_excerpt.replace(" ", "") or speech[0].source_excerpt[:8] in parts[0].source_excerpt
    # 拆段后的每个 part 必须携带可解析的段落 id，供原生对白块按段取文本。
    assert [beat.dialogue_id for beat in parts] == [
        f"{speech[0].dialogue_id}-part-{index}" for index in range(1, len(parts) + 1)
    ]


def test_expand_overlong_speech_beats_keeps_line_that_fits_provider_cap():
    # 估算落在 (cap-1, cap] 区间的整句过去会因为拆段器预留的 1 秒被拆开，
    # 产生大量不必要的段落子镜；只有估算超过模型上限时才允许拆段。
    from src.services.beat_planner import expand_overlong_speech_beats, estimate_dialogue_duration_sec
    scene = _scene(duration=12, action="德拉望着镜子")
    scene.dialogue = [DialogueLine(
        character="德拉",
        line="德拉数完了全部的铜板，一共只有一元八角七，其中的六角还是请求杂货店老板赊账时零凑出来的，她把铜板攥在手心反复掂量，觉得自己实在太穷了。窗外传来晚祷的钟声，她望着空空的钱罐发呆，最后把头埋进手臂里肩膀微微耸动地哭了起来。",
    )]
    scene.camera_direction = ""
    scene.emotion_tone = ""
    beats = build_narrative_beats(scene)
    speech = [beat for beat in beats if beat.kind == "dialogue"]
    assert len(speech) == 1
    estimate = estimate_dialogue_duration_sec(speech[0].source_excerpt)
    assert 14 < estimate <= 15

    expanded = expand_overlong_speech_beats(beats, 15)
    parts = [beat for beat in expanded if beat.kind == "dialogue"]
    assert len(parts) == 1
    assert parts[0].dialogue_id == speech[0].dialogue_id


def test_command_dialogue_anchors_to_keyword_action_not_speaker_last_action():
    # Maggi scene 5: Sofroni's single line commands "揭掉帽子…看发样"; it must
    # anchor to the hat/show-hair request beat, not to her later weighing beat.
    scene = Scene(
        scene_id=5,
        duration_sec=12,
        location="旧街头发店店内",
        characters=["德拉", "索弗罗妮夫人"],
        dialogue=[DialogueLine(character="索弗罗妮夫人", line="我买头发。揭掉帽子，让我看看发样。")],
        action_description=(
            "店内，索弗罗妮夫人冷淡地看着德拉；索弗罗妮夫人要她摘下帽子看发样；"
            "德拉脱下帽子，褐色长发如瀑布般垂下；索弗罗妮夫人内行地抓起头发掂量。"
        ),
        camera_direction="双人中景",
        emotion_tone="冷淡",
    )

    beats = build_narrative_beats(scene)
    dlg_beat = next(b for b in beats if b.dialogue_id)
    # The dialogue must sort with the hat-request beat, before the 脱下帽子 beat.
    hat_request = next(i for i, b in enumerate(beats) if "摘下帽子看发样" in (b.source_excerpt or ""))
    hat_off = next(i for i, b in enumerate(beats) if "德拉脱下帽子" in (b.source_excerpt or ""))
    assert hat_request < beats.index(dlg_beat) < hat_off


def test_command_dialogue_without_keyword_still_uses_speaker_last_action():
    # No object keyword overlap → fall back to the previous last-action rule.
    scene = Scene(
        scene_id=9,
        duration_sec=8,
        location="破旧廉租公寓",
        characters=["德拉", "吉姆"],
        dialogue=[DialogueLine(character="吉姆", line="别那样盯着我。")],
        action_description=(
            "吉姆推门进屋，站在门口纹丝不动；吉姆死死盯着德拉，眼神无法理解。"
        ),
        camera_direction="全景",
        emotion_tone="僵持",
    )
    beats = build_narrative_beats(scene)
    dlg_beat = next(b for b in beats if b.dialogue_id)
    # "盯着" is shared with the staring beat, so the keyword anchor places the
    # line right after it (still the last real action in this fixture).
    last_real_action = max(
        i for i, b in enumerate(beats) if b.kind in {"action", "prop_change", "transition"}
    )
    assert beats.index(dlg_beat) > last_real_action

