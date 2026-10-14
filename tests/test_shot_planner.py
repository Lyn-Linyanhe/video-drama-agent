from src.schemas.script import Scene, DialogueLine
from src.services.beat_planner import build_narrative_beats, estimate_dialogue_duration_sec
from src.services.shot_planner import build_shot_plan


def _scene(duration=12, action="甲走到桌边拿起钥匙并打开盒子"):
    return Scene(
        scene_id=3,
        duration_sec=duration,
        location="室内",
        characters=["甲"],
        dialogue=[DialogueLine(character="甲", line="我回来了。")],
        action_description=action,
        camera_direction="固定中景",
        emotion_tone="紧张",
    )


def _maggi_scene_8():
    return Scene(
        scene_id=8,
        duration_sec=12,
        location="公寓",
        characters=["吉姆", "德拉"],
        action_description=(
            "七点整，吉姆推门进屋，站在门口纹丝不动，死死盯着德拉，眼神无法理解；"
            "德拉正坐在桌角、手里握着白金表链，紧张地站起来向他走去。"
            "见他沉默不语，她的表情从期待变为慌乱，急切地开口解释。"
        ),
        dialogue=[
            DialogueLine(character="吉姆", line="你已经把头发剪掉了？", order=1),
            DialogueLine(character="德拉", line="剪掉卖了。不管怎么说，你不也同样喜欢我吗？没了长发，我还是我嘛，对吗？", order=2),
            DialogueLine(character="吉姆", line="你说你的头发没有了吗？", order=3),
            DialogueLine(
                character="德拉",
                line="别找啦。告诉你，我已经卖了——卖掉了，没有啦。这是圣诞前夜，好人儿。好好待我，这是为了你呀。也许我的头发数得清，可谁也数不清我对你的恩爱啊。我做肉排了吗，吉姆？",
                order=4,
            ),
        ],
        camera_direction="全景纵向取门口立定的吉姆，再切双人中景",
        emotion_tone="紧张",
    )


def test_build_shot_plan_creates_executable_units_when_provider_cap_is_exceeded():
    plan = build_shot_plan(_scene(duration=12), provider_max_duration_sec=10)

    assert plan.capacity.status == "auto_split"
    assert [unit.shot_id for unit in plan.units] == ["scene-3-shot-1", "scene-3-shot-2"]
    assert all(3 <= unit.duration_sec <= 10 for unit in plan.units)
    assert all(unit.parent_scene_id == 3 for unit in plan.units)
    assert all(unit.beat_ids for unit in plan.units)
    assert plan.units[1].continuity_from_shot_id == plan.units[0].shot_id


def test_build_shot_plan_keeps_beats_when_scene_duration_is_insufficient():
    plan = build_shot_plan(_scene(duration=3, action="甲走到桌边拿起钥匙并打开盒子"), provider_max_duration_sec=10)

    assert plan.capacity.status == "duration_insufficient"
    assert plan.units
    assert plan.capacity.required_total_duration_sec > plan.capacity.available_duration_sec
    planned_beat_ids = {beat_id for unit in plan.units for beat_id in unit.beat_ids}
    assert planned_beat_ids


def test_build_shot_plan_splits_long_single_beat_by_duration_cap():
    scene = _scene(duration=12, action="甲站在门边")
    scene.dialogue = []
    scene.camera_direction = ""
    scene.emotion_tone = ""
    plan = build_shot_plan(scene, provider_max_duration_sec=10)

    assert plan.capacity.status == "auto_split"
    assert len(plan.units) == 2
    assert all(3 <= unit.duration_sec <= 10 for unit in plan.units)
    assert all(unit.beat_ids for unit in plan.units)
    assert plan.units[0].beat_ids == plan.units[1].beat_ids


def test_build_shot_plan_does_not_put_continuity_camera_emotion_into_last_unit_as_story():
    scene = _scene(duration=12, action="七点整，吉姆推门进屋；德拉站起来向他走去。")
    scene.dialogue = [
        DialogueLine(character="吉姆", line="你已经把头发剪掉了？"),
        DialogueLine(character="德拉", line="剪掉卖了。"),
        DialogueLine(character="德拉", line="别找啦。告诉你，我已经卖了。"),
    ]
    scene.camera_direction = "全景纵向取门口立定的吉姆，再切双人中景"
    scene.emotion_tone = "紧张"
    scene.continuity_from_prev = True
    scene.continuity_reason = "时间跳到晚七点、吉姆新登场，开启新节拍。"
    plan = build_shot_plan(scene, provider_max_duration_sec=15, provider_min_duration_sec=4)
    constraint_ids = {
        beat.beat_id for beat in build_narrative_beats(scene)
        if beat.kind in {"continuity", "camera", "emotion"}
    }
    last = plan.units[-1]
    assert not set(last.beat_ids) & constraint_ids or any(
        beat_id not in constraint_ids for beat_id in last.beat_ids
    )
    assert any(unit.dialogue_ids for unit in plan.units)


def test_maggi_scene_8_sizes_each_child_from_its_own_content():
    scene = _maggi_scene_8()
    plan = build_shot_plan(scene, provider_max_duration_sec=15, provider_min_duration_sec=4)

    assert [unit.dialogue_ids for unit in plan.units] == [
        ["scene-8-dialogue-1"],
        ["scene-8-dialogue-2"],
        ["scene-8-dialogue-3"],
        ["scene-8-dialogue-4"],
    ]
    beats = {beat.beat_id: beat for beat in build_narrative_beats(scene)}
    assert any("慌乱" in (beats[beat_id].source_excerpt or "") for beat_id in plan.units[2].beat_ids)
    last_need = estimate_dialogue_duration_sec(scene.dialogue[-1].line)
    assert plan.units[-1].duration_sec >= 10
    assert plan.units[-1].duration_sec >= round(last_need)
    assert plan.units[-1].duration_sec <= 15
    assert all(4 <= unit.duration_sec <= 15 for unit in plan.units)
    assert sum(unit.duration_sec for unit in plan.units) > scene.duration_sec
    # Short children must not steal the long line's time by equal-splitting 12s.
    assert plan.units[0].duration_sec < plan.units[-1].duration_sec
    assert plan.units[2].duration_sec < plan.units[-1].duration_sec


def _excerpt_map(scene):
    from src.services.beat_planner import build_narrative_beats
    return {beat.beat_id: beat.source_excerpt or "" for beat in build_narrative_beats(scene)}


def _unit_text(unit, excerpts):
    return " ".join(excerpts.get(beat_id, "") for beat_id in unit.beat_ids)


def test_maggi_scene_4_does_not_put_shop_talk_on_the_street_sign():
    scene = Scene(
        scene_id=4, duration_sec=5, location="旧街头发店门口",
        characters=["德拉", "索弗罗妮夫人"],
        action_description="德拉走到头发店招牌前停下脚步，抬头看了一眼招牌，随后快步推开店门走进店铺。",
        dialogue=[
            DialogueLine(character="德拉", line="你要买我的头发吗？", order=1),
            DialogueLine(character="索弗罗妮夫人", line="我买头发。揭掉帽子，让我看看发样。", order=2),
            DialogueLine(character="索弗罗妮夫人", line="二十美元。", order=3),
            DialogueLine(character="德拉", line="快给我钱。", order=4),
        ],
        camera_direction="侧后方全景", emotion_tone="紧张",
    )
    plan = build_shot_plan(scene, 15, provider_min_duration_sec=4)
    excerpts = _excerpt_map(scene)
    first = _unit_text(plan.units[0], excerpts)
    assert "招牌" in first or "走" in first or "推开" in first
    assert "你要买我的头发吗" not in first
    assert "二十美元" not in first
    spoken = [unit.dialogue_ids for unit in plan.units if unit.dialogue_ids]
    assert spoken[-1] == ["scene-4-dialogue-4"]
    assert ["scene-4-dialogue-1"] in spoken


def test_maggi_scene_9_puts_love_speech_on_the_hug():
    scene = Scene(
        scene_id=9, duration_sec=11, location="公寓", characters=["吉姆", "德拉"],
        action_description="吉姆仍站在原地，机械地低声重复问句；德拉见他眼神空洞，言语更温柔急切。吉姆似乎从恍惚中醒来，大步上前将德拉紧紧搂在怀里，久久没有说话。",
        dialogue=[DialogueLine(character="吉姆", line="别对我产生误会，德尔。无论剪发、修面，还是洗头，我以为世上没有什么东西能减低一点点对我妻子的爱情。不过，你只消打开那包东西，就会明白刚才为什么使我楞头楞脑了。", order=1)],
        camera_direction="双人中景", emotion_tone="温柔",
    )
    plan = build_shot_plan(scene, 15, provider_min_duration_sec=4)
    excerpts = _excerpt_map(scene)
    speech_unit = next(unit for unit in plan.units if unit.dialogue_ids == ["scene-9-dialogue-1"])
    text = _unit_text(speech_unit, excerpts)
    assert "搂" in text or "醒来" in text
    assert "机械" not in text
    first = _unit_text(plan.units[0], excerpts)
    assert "机械" in first or "空洞" in first


def test_maggi_scene_10_puts_hair_line_on_untying_the_parcel():
    scene = Scene(
        scene_id=10, duration_sec=8, location="公寓", characters=["吉姆", "德拉"],
        action_description="吉姆松开德拉，从大衣口袋里掏出一个小包，轻轻扔在桌上并示意她打开；德拉头发蓬乱、眼睛含泪，看看吉姆又看看小包，手指微颤地解开绳结。",
        dialogue=[DialogueLine(character="德拉", line="我的头发长得飞快，吉姆！", order=1)],
        camera_direction="中景", emotion_tone="含泪",
    )
    plan = build_shot_plan(scene, 15, provider_min_duration_sec=4)
    excerpts = _excerpt_map(scene)
    speech_unit = next(unit for unit in plan.units if "scene-10-dialogue-1" in unit.dialogue_ids)
    text = _unit_text(speech_unit, excerpts)
    assert "解开" in text or "绳结" in text
    assert "掏出" not in text


def test_maggi_scene_11_puts_line_after_seeing_the_combs():
    scene = Scene(
        scene_id=11, duration_sec=9, location="公寓", characters=["德拉", "吉姆"],
        action_description="德拉打开纸包，露出全套玳瑁发梳；她先发出一声欣喜的尖叫，随即转为抽泣，将发梳紧紧搂在胸前，泪水不断滑落；吉姆低声安慰她。过了一会儿，她抬起泪眼，露出一个微笑，说出这句话。",
        dialogue=[DialogueLine(character="德拉", line="漂亮吗，吉姆？我搜遍了全城才找到了它。现在，你每天可以看一百次时间了。把表给我，我要看看它配在表上的样子。", order=1)],
        camera_direction="近景", emotion_tone="悲喜",
    )
    plan = build_shot_plan(scene, 15, provider_min_duration_sec=4)
    excerpts = _excerpt_map(scene)
    speech_unit = next(unit for unit in plan.units if "scene-11-dialogue-1" in unit.dialogue_ids)
    text = _unit_text(speech_unit, excerpts)
    assert "说出这句话" in text or "微笑" in text
    assert "打开纸包" not in text
    first = _unit_text(plan.units[0], excerpts)
    assert "发梳" in first or "打开" in first


def test_maggi_scene_12_puts_reveal_on_the_couch_collapse():
    scene = Scene(
        scene_id=12, duration_sec=8, location="公寓", characters=["德拉", "吉姆"],
        action_description="德拉像被烫到的小猫般跳起来，兴奋地摊开手掌，露出掌心那条白金表链，递向吉姆；吉姆没有接表链，而是向后倒在破旧睡椅上，两手枕在头下，脸上露出苦笑。",
        dialogue=[DialogueLine(character="吉姆", line="德尔，让我们把圣诞礼物放在一边，保存一会儿吧。它们实在太好了，目前尚不宜用。我卖掉金表，换钱为你买了发梳。现在，你作肉排吧。", order=1)],
        camera_direction="中景", emotion_tone="苦笑",
    )
    plan = build_shot_plan(scene, 15, provider_min_duration_sec=4)
    excerpts = _excerpt_map(scene)
    speech_unit = next(unit for unit in plan.units if "scene-12-dialogue-1" in unit.dialogue_ids)
    text = _unit_text(speech_unit, excerpts)
    assert "倒" in text or "睡椅" in text
    assert "跳起来" not in text
    first = _unit_text(plan.units[0], excerpts)
    assert "表链" in first or "跳" in first


def test_long_christmas_speech_is_split_under_happyhorse_cap():
    scene = Scene(
        scene_id=7, duration_sec=12, location="公寓", characters=["德拉"],
        action_description="德拉放下铁钳，望着镜中的自己叹了口气，一边低声自语，一边转身到炉灶点燃火，煮上咖啡。",
        dialogue=[DialogueLine(
            character="德拉", order=1,
            line="吉姆，亲爱的，别那样盯着我。我把头发剪掉卖了，因为不送你一件礼物，我无法过圣诞节。头发会再长起来——你不会介意，是吗？我非这么做不可。我的头发长得快极了。说恭贺圣诞吧！吉姆，让我们快快乐乐的。你肯定猜不着我给你买了一件多么好的——多么美丽精致的礼物啊！",
        )],
        camera_direction="中景", emotion_tone="不安",
    )
    plan = build_shot_plan(scene, 15, provider_min_duration_sec=4)
    assert all(4 <= unit.duration_sec <= 15 for unit in plan.units)
    speech_units = [unit for unit in plan.units if unit.dialogue_ids]
    assert len(speech_units) >= 2
