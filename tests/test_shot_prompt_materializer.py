import pytest

from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.script import DialogueLine, Scene, ScriptBreakdown
from src.schemas.shot_plan import ShotPlan, ShotUnit, SceneCapacityReport
from src.services.shot_prompt_materializer import materialize_shot_prompts


def _inputs():
    from src.services.beat_planner import build_narrative_beats
    scene = Scene(
        scene_id=1, duration_sec=8, location="室内", characters=["甲"],
        action_description="甲走到桌边拿起钥匙并打开盒子", camera_direction="固定中景", emotion_tone="紧张",
    )
    script = ScriptBreakdown(title="测试", genre="剧情", logline="测试", duration_sec=8, narration_style="无旁白", scenes=[scene])
    parent = ShotPrompt(
        scene_id=1,
        prompt="【主体状态】甲在室内。\n【动作时间轴】0-8s 甲走到桌边拿起钥匙并打开盒子。\n【镜头语言】固定中景。",
        duration_sec=8,
    )
    executable_ids = [
        beat.beat_id for beat in build_narrative_beats(scene)
        if beat.kind in {"action", "micro_action", "prop_change", "transition"}
    ]
    plan = ShotPlan(
        scene_id=1, parent_duration_sec=8,
        capacity=SceneCapacityReport(
            scene_id=1, required_total_duration_sec=8, available_duration_sec=8,
            recommended_total_duration_sec=8, recommended_durations_sec=[4, 4],
            recommended_shot_count=2, status="auto_split", warnings=["拆分"],
        ),
        units=[
            ShotUnit(shot_id="scene-1-shot-1", parent_scene_id=1, order=1, beat_ids=executable_ids[:1], duration_sec=4),
            ShotUnit(shot_id="scene-1-shot-2", parent_scene_id=1, order=2, beat_ids=executable_ids[1:], duration_sec=4, continuity_from_shot_id="scene-1-shot-1"),
        ],
    )
    return script, GenerationPrompts(api_provider="mock", shots=[parent], shot_plans=[plan]), plan


def test_materialize_shot_prompts_creates_one_prompt_per_shot_unit_with_scoped_timeline():
    script, prompts, plan = _inputs()
    shots = materialize_shot_prompts(prompts, script)

    assert [shot.shot_id for shot in shots] == ["scene-1-shot-1", "scene-1-shot-2"]
    assert [shot.parent_scene_id for shot in shots] == [1, 1]
    assert [shot.duration_sec for shot in shots] == [4, 4]
    assert "走到桌边" in shots[0].prompt
    assert "打开盒子" in shots[1].prompt
    assert "【本镜禁止执行】" in shots[0].prompt
    assert "不得执行本镜范围之外的动作或状态" in shots[0].prompt.split("【本镜禁止执行】", 1)[1]
    assert "打开盒子" not in shots[0].prompt.split("【本镜禁止执行】", 1)[1]
    assert "走到桌边" not in shots[1].prompt.split("【本镜禁止执行】", 1)[1]


def test_materialize_uses_saved_unit_prompt_instead_of_rebuilding():
    script, prompts, plan = _inputs()
    plan.units[0].prompt = "【动作时间轴】0-4s 人工保存的最新提示词，不要被重组覆盖。"
    shots = materialize_shot_prompts(prompts, script)
    assert "人工保存的最新提示词" in shots[0].prompt
    assert "走到桌边" not in shots[0].prompt


def test_materialize_persists_order_and_explicit_continuity_dependency():
    script, prompts, _ = _inputs()
    shots = materialize_shot_prompts(prompts, script)

    assert [shot.shot_order for shot in shots] == [1, 2]
    assert shots[0].continuity_from_shot_id is None
    assert shots[1].continuity_from_shot_id == "scene-1-shot-1"


def test_materialize_shot_prompts_keeps_unsplit_parent_backward_compatible():
    script, prompts, plan = _inputs()
    prompts.shot_plans[0].units = [
        ShotUnit(shot_id="scene-1-shot-1", parent_scene_id=1, order=1, beat_ids=plan.units[0].beat_ids + plan.units[1].beat_ids, duration_sec=8)
    ]
    shots = materialize_shot_prompts(prompts, script)

    assert len(shots) == 1
    assert shots[0].prompt.count("【动作时间轴】") == 1
    assert "走到桌边" in shots[0].prompt
    assert "打开盒子" in shots[0].prompt



def test_materialize_replaces_finalized_parent_timeline_with_child_duration():
    script, prompts, plan = _inputs()
    prompts.shots[0] = prompts.shots[0].model_copy(update={
        "duration_sec": 15,
        "prompt": (
            "【单镜头时间轴】\n"
            "核心分镜意图：父级镜头动作\n"
            "0s-15s，父级镜头完整动作。\n"
            "【连续锁定】\n"
            "总时长约15s，严格按上述时间轴执行。"
        ),
    })
    plan.units[0].duration_sec = 10

    shots = materialize_shot_prompts(prompts, script)

    assert "【动作时间轴】0-10s" in shots[0].prompt
    assert "0s-15s，父级镜头完整动作" not in shots[0].prompt



def test_materialize_rejects_child_duration_above_provider_cap():
    script, prompts, plan = _inputs()
    plan.units[0].duration_sec = 20

    with pytest.raises(ValueError, match=r"20.*15"):
        materialize_shot_prompts(prompts, script, duration_cap_sec=15)


def test_materialize_quantizes_integer_child_durations_up_to_provider_choices():
    script, prompts, plan = _inputs()
    plan.units[0].duration_sec = 4
    plan.units[1].duration_sec = 8

    shots = materialize_shot_prompts(
        prompts,
        script,
        duration_cap_sec=15,
        duration_options=[3, 5, 10, 15],
    )

    assert [shot.duration_sec for shot in shots] == [5, 10]
    assert "【动作时间轴】0-5s" in shots[0].prompt
    assert "【动作时间轴】0-10s" in shots[1].prompt


def test_materialize_does_not_quote_other_child_dialogue_in_forbidden_section():
    scene = Scene(
        scene_id=8,
        duration_sec=12,
        location="公寓",
        characters=["德拉", "吉姆"],
        dialogue=[
            DialogueLine(character="吉姆", line="你已经把头发剪掉了？", order=1),
            DialogueLine(character="德拉", line="剪掉卖了。你不也同样喜欢我吗？"),
        ],
        action_description="吉姆站在门口看着德拉。",
        camera_direction="双人中景",
        emotion_tone="紧张",
    )
    script = ScriptBreakdown(
        title="麦琪的礼物", genre="剧情", logline="测试", duration_sec=12,
        narration_style="无旁白", scenes=[scene],
    )
    parent = ShotPrompt(
        scene_id=8,
        prompt=(
            "【动作时间轴】0-12s 吉姆站在门口看着德拉。\n"
            "【对白/独白】吉姆：“你已经把头发剪掉了？” / "
            "德拉：“剪掉卖了。你不也同样喜欢我吗？”。对白原文必须完整保留。"
        ),
        duration_sec=12,
    )
    plan = ShotPlan(
        scene_id=8,
        parent_duration_sec=12,
        capacity=SceneCapacityReport(
            scene_id=8, required_total_duration_sec=20, available_duration_sec=12,
            recommended_total_duration_sec=20, recommended_durations_sec=[10, 10],
            recommended_shot_count=2, status="auto_split", warnings=["拆分"],
        ),
        units=[
            ShotUnit(
                shot_id="scene-8-shot-1", parent_scene_id=8, order=1,
                beat_ids=["scene-8-beat-2"],
                dialogue_ids=["scene-8-dialogue-1"], duration_sec=10,
            ),
            ShotUnit(
                shot_id="scene-8-shot-2", parent_scene_id=8, order=2,
                beat_ids=["scene-8-beat-3"],
                dialogue_ids=["scene-8-dialogue-2"], duration_sec=10,
            ),
        ],
    )

    shots = materialize_shot_prompts(
        GenerationPrompts(api_provider="happyhorse", shots=[parent], shot_plans=[plan]),
        script,
        duration_cap_sec=15,
    )

    assert "你已经把头发剪掉了？" in shots[0].prompt
    assert "剪掉卖了。你不也同样喜欢我吗？" not in shots[0].prompt
    assert "不得说出本镜未绑定对白" in shots[0].prompt
    assert "剪掉卖了。你不也同样喜欢我吗？" in shots[1].prompt
    assert "你已经把头发剪掉了？" not in shots[1].prompt


def test_materialized_child_formats_dialogue_with_speaker_and_removes_diagnostics():
    scene = Scene(
        scene_id=9, duration_sec=10, location="公寓", characters=["吉姆"],
        dialogue=[DialogueLine(character="吉姆", line="你已经把头发剪掉了？", emotion="震惊")],
        action_description="吉姆站在门口凝视德拉。", camera_direction="中景", emotion_tone="震惊",
    )
    script = ScriptBreakdown(
        title="测试", genre="剧情", logline="测试", duration_sec=10, narration_style="无旁白", scenes=[scene],
    )
    parent = ShotPrompt(
        scene_id=9, duration_sec=10,
        prompt=(
            "【项目视觉】写实。\n【时间容量诊断】预计完整执行需要约20s，但场景只有10s。\n"
            "【动作时间轴】0-10s 吉姆站在门口凝视德拉。\n"
            "【对白/独白】吉姆：“你已经把头发剪掉了？”。"
        ),
    )
    plan = ShotPlan(
        scene_id=9, parent_duration_sec=10,
        capacity=SceneCapacityReport(
            scene_id=9, required_total_duration_sec=10, available_duration_sec=10,
            recommended_total_duration_sec=10, recommended_durations_sec=[10],
            recommended_shot_count=1, status="clean", warnings=[],
        ),
        units=[ShotUnit(
            shot_id="scene-9-shot-1", parent_scene_id=9, order=1,
            beat_ids=["scene-9-beat-1", "scene-9-beat-2"],
            dialogue_ids=["scene-9-dialogue-1"], duration_sec=10,
        )],
    )

    child = materialize_shot_prompts(
        GenerationPrompts(api_provider="happyhorse", shots=[parent], shot_plans=[plan]), script,
    )[0]

    assert "【时间容量诊断】" not in child.prompt
    assert "【对白】" not in child.prompt
    action = child.prompt.split("【动作时间轴】", 1)[1].split("【", 1)[0]
    assert "你已经把头发剪掉了？" in action
    assert "吉姆" in action
    assert child.prompt.count("你已经把头发剪掉了？") == 1


def test_materialized_child_places_bound_dialogue_immediately_after_action_before_environment():
    scene = Scene(
        scene_id=8, duration_sec=6, location="公寓", characters=["吉姆", "德拉"],
        dialogue=[DialogueLine(character="吉姆", line="你已经把头发剪掉了？")],
        action_description="吉姆推门进屋，德拉握着白金表链起身走近。",
        camera_direction="固定侧面中景", emotion_tone="紧张",
    )
    script = ScriptBreakdown(
        title="麦琪的礼物", genre="剧情", logline="测试", duration_sec=6,
        narration_style="无旁白", scenes=[scene],
    )
    parent = ShotPrompt(
        scene_id=8, duration_sec=6,
        prompt=(
            "【项目视觉】电影级真人写实。\n"
            "【出镜角色与身份锚点】吉姆：长篇身份。德拉：长篇身份。\n"
            "【动作时间轴】0-6s 吉姆推门进屋，德拉握着白金表链起身走近。\n"
            "【环境】破旧公寓。\n"
            "【镜头语言】固定侧面中景。\n"
            "【对白】吉姆：\"你已经把头发剪掉了？\""
        ),
    )
    plan = ShotPlan(
        scene_id=8, parent_duration_sec=6,
        capacity=SceneCapacityReport(
            scene_id=8, required_total_duration_sec=6, available_duration_sec=6,
            recommended_total_duration_sec=6, recommended_durations_sec=[6],
            recommended_shot_count=1, status="clean", warnings=[],
        ),
        units=[ShotUnit(
            shot_id="scene-8-shot-1", parent_scene_id=8, order=1,
            beat_ids=["scene-8-beat-1"], dialogue_ids=["scene-8-dialogue-1"], duration_sec=6,
        )],
    )

    child = materialize_shot_prompts(
        GenerationPrompts(api_provider="happyhorse", shots=[parent], shot_plans=[plan]), script,
        duration_cap_sec=15, provider_min_duration_sec=4,
    )[0]

    assert "【对白】" not in child.prompt
    action = child.prompt.split("【动作时间轴】", 1)[1].split("【", 1)[0]
    assert "你已经把头发剪掉了？" in action
    assert "吉姆" in action
    assert child.prompt.index("【动作时间轴】") < child.prompt.index("【环境】")
    assert child.prompt.count("你已经把头发剪掉了？") == 1


def test_materialize_keeps_only_executable_action_and_does_not_leak_planning_or_other_shot_camera():
    scene = Scene(
        scene_id=8, duration_sec=12, location="公寓", characters=["吉姆", "德拉"],
        dialogue=[
            DialogueLine(character="吉姆", line="你已经把头发剪掉了？", order=1),
            DialogueLine(character="德拉", line="剪掉卖了。你不也同样喜欢我吗？"),
            DialogueLine(character="吉姆", line="你说你的头发没有了吗？", order=3),
            DialogueLine(character="德拉", line="别找啦。告诉你，我已经卖了。"),
        ],
        action_description="七点整，吉姆推门进屋，站在门口死死盯着德拉；德拉握着白金表链起身走近。",
        camera_direction="全景纵向取门口立定的吉姆，再切双人中景，slow dolly in至德拉仰望吉姆的近景",
        emotion_tone="僵持、紧张、慌乱想弥补",
        continuity_from_prev=True,
        continuity_reason="时间跳到晚七点、吉姆新登场，开启新节拍。",
    )
    script = ScriptBreakdown(
        title="麦琪的礼物", genre="剧情", logline="测试", duration_sec=12,
        narration_style="无旁白", scenes=[scene],
    )
    parent = ShotPrompt(
        scene_id=8, duration_sec=12,
        prompt=(
            "【项目视觉】电影级真人写实。\n"
            "【动作时间轴】0-12s 吉姆推门进屋，德拉起身走近。\n"
            "【镜头语言】全景纵向取门口立定的吉姆，再切双人中景。\n"
            "【环境】破旧公寓。"
        ),
    )
    from src.services.shot_planner import build_shot_plan
    plan = build_shot_plan(scene, 15, provider_min_duration_sec=4)

    shots = materialize_shot_prompts(
        GenerationPrompts(api_provider="happyhorse", shots=[parent], shot_plans=[plan]),
        script, duration_cap_sec=15, provider_min_duration_sec=4,
    )

    first = shots[0]
    last_line = next(shot for shot in shots if "别找啦" in shot.prompt)
    first_action = first.prompt.split("【动作时间轴】", 1)[1].split("【", 1)[0]
    last_action = last_line.prompt.split("【动作时间轴】", 1)[1].split("【", 1)[0]
    assert "你已经把头发剪掉了？" in first.prompt
    assert "别找啦" not in first.prompt
    assert "时间跳到晚七点" not in first_action
    assert "新登场" not in last_action
    assert "开启新节拍" not in last_action
    assert "切双人中景" not in last_action
    assert "别找啦" in last_line.prompt
    assert "你已经把头发剪掉了？" not in last_line.prompt
    forbidden = first.prompt.split("【本镜禁止执行】", 1)[1] if "【本镜禁止执行】" in first.prompt else ""
    assert "切双人中景" not in forbidden
    assert "时间跳到晚七点" not in forbidden


def test_materialize_sizes_dialogue_only_child_from_speech_clock():
    from src.services.shot_planner import build_shot_plan
    scene = Scene(
        scene_id=8, duration_sec=12, location="公寓", characters=["吉姆", "德拉"],
        action_description=(
            "七点整，吉姆推门进屋，站在门口纹丝不动，死死盯着德拉，眼神无法理解；"
            "德拉正坐在桌角、手里握着白金表链，紧张地站起来向他走去。"
            "见他沉默不语，她的表情从期待变为慌乱，急切地开口解释。"
        ),
        dialogue=[
            DialogueLine(character="吉姆", line="你已经把头发剪掉了？", order=1),
            DialogueLine(character="德拉", line="剪掉卖了。不管怎么说，你不也同样喜欢我吗？没了长发，我还是我嘛，对吗？", order=2),
            DialogueLine(character="吉姆", line="你说你的头发没有了吗？", order=3),
            DialogueLine(character="德拉", line="别找啦。告诉你，我已经卖了——卖掉了，没有啦。这是圣诞前夜，好人儿。好好待我，这是为了你呀。也许我的头发数得清，可谁也数不清我对你的恩爱啊。我做肉排了吗，吉姆？", order=4),
        ],
        camera_direction="全景纵向取门口立定的吉姆，再切双人中景",
        emotion_tone="紧张",
    )
    script = ScriptBreakdown(title="麦琪的礼物", genre="剧情", logline="测试", duration_sec=12, narration_style="无旁白", scenes=[scene])
    parent = ShotPrompt(scene_id=8, prompt="【动作时间轴】0-12s 吉姆进门。\n【镜头语言】双人中景。", duration_sec=12)
    plan = build_shot_plan(scene, 15, provider_min_duration_sec=4)
    shots = materialize_shot_prompts(
        GenerationPrompts(api_provider="happyhorse", shots=[parent], shot_plans=[plan]),
        script, duration_cap_sec=15, provider_min_duration_sec=4,
    )
    assert [shot.dialogue_ids for shot in shots] == [
        ["scene-8-dialogue-1"],
        ["scene-8-dialogue-2"],
        ["scene-8-dialogue-3"],
        ["scene-8-dialogue-4"],
    ]
    last = shots[-1]
    assert last.duration_sec >= 10
    assert "德拉保持当前站位" in last.prompt
    assert "别找啦" in last.prompt
    assert "你已经把头发剪掉了？" not in last.prompt
    assert "慌乱" in shots[2].prompt


def test_child_camera_does_not_reuse_apartment_doorway_for_shop():
    from types import SimpleNamespace
    from src.services.shot_prompt_materializer import _child_camera_block
    scene = Scene(
        scene_id=6, duration_sec=9, location="旧城区珠宝店柜台前(室内,暖黄柜灯)",
        characters=["德拉"], action_description="德拉挑选白金表链。",
        camera_direction="全景切中景再切特写", emotion_tone="期待",
    )
    unit = SimpleNamespace(order=1, dialogue_ids=[])
    camera = _child_camera_block(unit, scene, ["德拉挑选白金表链"])
    assert "门口" not in camera
    assert "桌角" not in camera
    assert "柜台" in camera or "中景" in camera

def test_establishing_camera_uses_doorway_when_shot_enters():
    from types import SimpleNamespace
    from src.schemas.script import Scene
    from src.services.shot_prompt_materializer import _establishing_camera

    scene = Scene(
        scene_id=8, duration_sec=8, location="破旧廉租公寓起居室", characters=["吉姆", "德拉"],
        dialogue=[], action_description="吉姆推门进屋", camera_direction="全景切中景", emotion_tone="紧张",
    )
    camera = _establishing_camera(scene, ["七点整，吉姆推门进屋，站在门口纹丝不动"])
    assert "门口" in camera
    assert "切镜头" in camera or "不要切" in camera


def test_materialized_dialogue_does_not_embed_voice_instruction():
    from src.schemas.prompt import GenerationPrompts, ShotPrompt
    from src.schemas.script import DialogueLine, Scene, ScriptBreakdown
    from src.schemas.shot_plan import SceneCapacityReport, ShotPlan, ShotUnit
    from src.services.shot_prompt_materializer import materialize_shot_prompts

    scene = Scene(
        scene_id=9, duration_sec=10, location="公寓", characters=["吉姆"],
        dialogue=[DialogueLine(character="吉姆", line="你已经把头发剪掉了？")],
        action_description="吉姆站在门口凝视德拉。", camera_direction="中景", emotion_tone="震惊",
    )
    script = ScriptBreakdown(title="测试", genre="剧情", logline="测试", duration_sec=10, narration_style="无旁白", scenes=[scene])
    parent = ShotPrompt(scene_id=9, duration_sec=10, prompt="【动作时间轴】0-10s 吉姆站在门口凝视德拉。")
    plan = ShotPlan(
        scene_id=9, parent_duration_sec=10,
        capacity=SceneCapacityReport(
            scene_id=9, required_total_duration_sec=10, available_duration_sec=10,
            recommended_total_duration_sec=10, recommended_durations_sec=[10],
            recommended_shot_count=1, status="clean", warnings=[],
        ),
        units=[ShotUnit(shot_id="scene-9-shot-1", parent_scene_id=9, order=1, beat_ids=["scene-9-beat-1", "scene-9-beat-2"], dialogue_ids=["scene-9-dialogue-1"], duration_sec=10)],
    )
    child = materialize_shot_prompts(GenerationPrompts(api_provider="happyhorse", shots=[parent], shot_plans=[plan]), script)[0]
    assert "【对白】" not in child.prompt
    action = child.prompt.split("【动作时间轴】", 1)[1].split("【", 1)[0]
    assert "你已经把头发剪掉了？" in action
    assert "对白必须生成真实人声" not in child.prompt


def test_followup_child_camera_has_no_cross_shot_reference():
    # A single shot is sent to the model with no memory of the previous one,
    # so "保持当前机位/不要重新起镜/切到新地点" are meaningless cross-shot
    # references.  Follow-up units must state their own framing instead.
    from types import SimpleNamespace
    from src.schemas.script import DialogueLine, Scene
    from src.services.shot_prompt_materializer import _child_camera_block

    scene = Scene(
        scene_id=8, duration_sec=7, location="破旧廉租公寓", characters=["吉姆", "德拉"],
        dialogue=[DialogueLine(character="德拉", line="剪掉卖了。")],
        action_description="德拉正坐在桌角，紧张地站起来向他走去。",
        camera_direction="固定侧面中景", emotion_tone="慌乱",
    )
    camera = _child_camera_block(
        SimpleNamespace(order=2, dialogue_ids=["scene-8-dialogue-1"]),
        scene,
        ["德拉正坐在桌角，紧张地站起来向他走去"],
    )
    for banned in ("保持当前机位", "不要重新起镜", "切到新地点", "完成这一镜对白"):
        assert banned not in camera
    assert any(word in camera for word in ("中景", "近景", "全景", "特写"))
    assert "口型" in camera


def test_followup_action_child_camera_has_no_cross_shot_reference():
    from types import SimpleNamespace
    from src.schemas.script import Scene
    from src.services.shot_prompt_materializer import _child_camera_block

    scene = Scene(
        scene_id=11, duration_sec=6, location="旧街头发店店内",
        characters=["德拉"], dialogue=[],
        action_description="德拉打开纸包，露出玳瑁发梳。",
        camera_direction="近景", emotion_tone="惊喜",
    )
    camera = _child_camera_block(
        SimpleNamespace(order=2, dialogue_ids=[]), scene,
        ["德拉打开纸包，露出玳瑁发梳"],
    )
    for banned in ("保持当前机位", "不要重新起镜", "切到新地点"):
        assert banned not in camera
    assert "口型" not in camera  # no dialogue in this unit
    assert any(word in camera for word in ("中景", "近景", "全景", "特写"))


_LONG_LINE = "吉姆，亲爱的，别那样盯着我。我把头发剪掉卖了，因为不送你一件礼物，我无法过圣诞节。头发会再长起来——你不会介意，是吗？我非这么做不可。我的头发长得快极了。说恭贺圣诞吧！吉姆，让我们快快乐乐的。你肯定猜不着我给你买了一件多么好的——多么美丽精致的礼物啊！"


def _over_cap_inputs():
    from src.services.shot_planner import build_shot_plan
    scene = Scene(
        scene_id=7, duration_sec=12, location="公寓", characters=["德拉"],
        action_description="德拉握着表链紧张地站起身，急切地开口解释。",
        dialogue=[DialogueLine(character="德拉", line=_LONG_LINE, order=1)],
        camera_direction="近景", emotion_tone="急切",
    )
    script = ScriptBreakdown(title="麦琪的礼物", genre="剧情", logline="测试", duration_sec=12, narration_style="无旁白", scenes=[scene])
    parent = ShotPrompt(scene_id=7, prompt="【动作时间轴】0-12s 德拉握着表链。\n【镜头语言】近景。", duration_sec=12)
    plan = build_shot_plan(scene, 15, provider_min_duration_sec=4)
    return script, GenerationPrompts(api_provider="happyhorse", shots=[parent], shot_plans=[plan]), plan


def test_materialize_splits_over_cap_line_with_part_scoped_dialogue_ids():
    import re as _re
    from src.services.beat_planner import split_speech_chunks

    script, prompts, _ = _over_cap_inputs()
    shots = materialize_shot_prompts(prompts, script, duration_cap_sec=15, provider_min_duration_sec=4)

    part_ids = [did for shot in shots for did in shot.dialogue_ids]
    assert part_ids == ["scene-7-dialogue-1-part-1", "scene-7-dialogue-1-part-2"]

    chunks = split_speech_chunks(_LONG_LINE, 14.0)
    assert len(chunks) >= 2
    for index, chunk in enumerate(chunks, start=1):
        host = next(shot for shot in shots if f"scene-7-dialogue-1-part-{index}" in shot.dialogue_ids)
        others = [shot for shot in shots if shot.shot_id != host.shot_id]
        assert chunk in host.prompt
        for other in others:
            assert chunk not in other.prompt
    # 整句不得出现在任何一个子镜提示词里：原生对白块按段取文，时间轴也只嵌本段。
    assert _LONG_LINE not in [shot.prompt for shot in shots if shot.dialogue_ids][0]
    for shot in shots:
        assert "上一镜" not in shot.prompt


def test_materialize_rebuilds_plan_when_stored_dialogue_ids_lack_part_suffix():
    from src.services.beat_planner import split_speech_chunks

    script, prompts, plan = _over_cap_inputs()
    # 模拟旧版计划：拆段 beats 已存在，但 unit 仍存整句 dialogue_id。
    for unit in plan.units:
        unit.dialogue_ids = [
            _re_sub_part_suffix(did) for did in unit.dialogue_ids
        ]
    shots = materialize_shot_prompts(prompts, script, duration_cap_sec=15, provider_min_duration_sec=4)

    part_ids = [did for shot in shots for did in shot.dialogue_ids]
    assert "scene-7-dialogue-1-part-1" in part_ids
    assert "scene-7-dialogue-1" not in part_ids
    chunks = split_speech_chunks(_LONG_LINE, 14.0)
    host = next(shot for shot in shots if "scene-7-dialogue-1-part-2" in shot.dialogue_ids)
    assert chunks[-1] in host.prompt
    assert chunks[0] not in host.prompt


def _re_sub_part_suffix(dialogue_id):
    import re
    return re.sub(r"-part-\d+$", "", dialogue_id)


def test_dialogue_hold_excerpt_has_no_cross_shot_reference_and_resolves_part_speaker():
    from types import SimpleNamespace
    from src.services.shot_prompt_materializer import _dialogue_hold_excerpt

    scene = SimpleNamespace(
        scene_id=7,
        characters=["德拉"],
        dialogue=[SimpleNamespace(character="德拉", line=_LONG_LINE, emotion="急切")],
    )
    unit = SimpleNamespace(dialogue_ids=["scene-7-dialogue-1-part-2"])

    text = _dialogue_hold_excerpt(scene, unit)

    assert text.startswith("德拉")
    assert "上一镜" not in text
    assert "保持当前站位" in text

def test_child_camera_appends_speaker_without_double_punctuation():
    # 口型指引是拼接到机位句尾的；机位句自带句号时不得出现“。；”连标点。
    from types import SimpleNamespace
    from src.services.shot_prompt_materializer import _child_camera_block

    scene = Scene(
        scene_id=7, duration_sec=6, location="公寓", characters=["德拉"],
        dialogue=[DialogueLine(character="德拉", line="求求上帝，让他觉得我还是漂亮的吧。")],
        camera_direction="镜前中景与镜面反射特写交替；竖幅9:16突出小房间的局促感。",
        action_description="德拉对着镜子烫发", emotion_tone="急切",
    )
    camera = _child_camera_block(
        SimpleNamespace(order=1, dialogue_ids=["scene-7-dialogue-1"]),
        scene,
        ["德拉对着镜子烫发"],
    )
    assert "。；" not in camera
    assert camera.endswith("口型与声音同步。")
    assert camera.count("镜面反射特写交替") == 1

def test_later_children_do_not_inherit_scene_start_state():
    # 场次级【主体状态/角色状态】描述的是整场开场（如"吉姆掏出小包"）。
    # 后续子镜入镜时小包已在桌上，继承开场状态只会制造矛盾——
    # 其入镜状态应由尾帧参考图与本镜时间轴承载。
    from src.services.shot_planner import build_shot_plan
    from src.services.prompt_execution_context import build_execution_context, render_execution_prompt

    scene = Scene(
        scene_id=10, duration_sec=8, location="公寓", characters=["吉姆", "德拉"],
        action_description=(
            "吉姆松开德拉，从大衣口袋里掏出一个小包，轻轻扔在桌上并示意她打开；"
            "德拉头发蓬乱、眼睛含泪，看看吉姆又看看小包，手指微颤地解开绳结。"
        ),
        dialogue=[DialogueLine(character="德拉", line="我的头发长得飞快，吉姆！", order=1)],
        camera_direction="中景", emotion_tone="含泪",
    )
    script = ScriptBreakdown(title="麦琪的礼物", genre="剧情", logline="测试", duration_sec=8, narration_style="无旁白", scenes=[scene])
    parent = ShotPrompt(
        scene_id=10,
        prompt=(
            "【主体状态】吉姆抱着德拉站在原地。\n"
            "【角色状态】@character:吉姆:back_full：从大衣口袋里掏出一个小包。\n"
            "【动作时间轴】0-8s 吉姆松开德拉，掏出小包扔在桌上并示意她打开；德拉含泪解开绳结。\n"
            "【环境】破旧廉租公寓。"
        ),
        duration_sec=8,
    )
    plan = build_shot_plan(scene, 15, provider_min_duration_sec=4)
    assert len(plan.units) >= 2
    shots = materialize_shot_prompts(
        GenerationPrompts(api_provider="happyhorse", shots=[parent], shot_plans=[plan]),
        script, duration_cap_sec=15, provider_min_duration_sec=4,
    )
    first, last = shots[0], shots[-1]
    assert int(getattr(first, "order", 1) or getattr(first, "shot_order", 1)) == 1

    # 首子镜保留场次开场状态；后续子镜不再继承。
    assert "【角色状态】" in first.prompt
    assert "【角色状态】" not in last.prompt
    assert "【主体状态】" not in last.prompt
    rendered = render_execution_prompt(build_execution_context(shot=last, scene=scene, valid_references=[]))
    assert "当前状态：" not in rendered
    assert "解开绳结" in rendered

def test_children_strip_stale_continuity_segment_and_empty_timeline_shell():
    # 父级【顺承上一镜】描述的是上一场景的结尾状态：对本场首子镜是合法
    # 入镜上下文；对场内后续子镜则是过期状态，且含跨镜指令句，必须剥离。
    # 空的【时间轴分镜】壳没有任何可执行信息，还要困住原生对白追加定位。
    from src.services.shot_planner import build_shot_plan

    scene = Scene(
        scene_id=4, duration_sec=9, location="杨家院门口", characters=["孔君平", "杨氏子"],
        action_description="孔君平站在门口含笑发问；孩子摇头作答；孔君平点头应下。",
        dialogue=[DialogueLine(character="孔君平", line="你的父亲在家吗？", order=1)],
        camera_direction="中景", emotion_tone="和蔼",
    )
    script = ScriptBreakdown(title="杨氏之子", genre="剧情", logline="测试", duration_sec=9, narration_style="无旁白", scenes=[scene])
    parent = ShotPrompt(
        scene_id=4,
        prompt=(
            "【顺承上一镜】本镜承接上一镜结尾画面状态，开头保持与上一镜一致——主体状态：孩子立于门内侧。不要重复上一镜已经完成的动作过程。\n"
            "【时间轴分镜】\n"
            "【动作时间轴】0-9s 孔君平在门口与孩子问答。\n"
            "【环境】杨家院门口(上午,室外)。"
        ),
        duration_sec=9,
    )
    plan = build_shot_plan(scene, 15, provider_min_duration_sec=4)
    assert len(plan.units) >= 2
    shots = materialize_shot_prompts(
        GenerationPrompts(api_provider="happyhorse", shots=[parent], shot_plans=[plan]),
        script, duration_cap_sec=15, provider_min_duration_sec=4,
    )
    first, last = shots[0], shots[-1]
    assert "顺承上一镜" in first.prompt
    assert "顺承上一镜" not in last.prompt
    assert "【时间轴分镜】\n【" not in first.prompt
    assert "【时间轴分镜】\n【" not in last.prompt

def test_establishing_camera_does_not_misread_courtyard_as_street():
    # 场地串带"(上午,室外)"括注时，院子戏不得套用"街道/店门/招牌"机位。
    from src.services.shot_prompt_materializer import _establishing_camera

    scene = Scene(
        scene_id=9, duration_sec=8, location="梁国杨家院子石桌旁(上午,室外庭院)",
        characters=["杨氏子", "孔君平"], dialogue=[],
        action_description="孩子抬手指向孔君平", camera_direction="", emotion_tone="机智",
    )
    camera = _establishing_camera(scene, ["孩子抬手指向孔君平"])
    for banned in ("街道", "店门", "招牌"):
        assert banned not in camera

def test_child_camera_inner_monologue_is_voiceover_without_lip_sync():
    # 独白子镜的口型指引必须反过来：口型不动、画外音呈现。
    from types import SimpleNamespace

    from src.services.shot_prompt_materializer import _child_camera_block

    scene = Scene(
        scene_id=10, duration_sec=9, location="梁国杨家院子石桌旁",
        characters=["孔君平", "杨氏子"],
        action_description="孔君平愣住，端茶的手停在半空，一时说不出话。",
        dialogue=[DialogueLine(
            character="孔君平",
            line="如果因为孩子姓杨，就说杨梅是杨家的水果，那么孔雀岂不也该是孔家的鸟？",
            emotion="内心独白（画外音）",
        )],
        camera_direction="close-up 孔君平面部", emotion_tone="震惊",
    )
    camera = _child_camera_block(
        SimpleNamespace(order=1, dialogue_ids=["scene-10-dialogue-1"]),
        scene,
        ["孔君平愣住，端茶的手停在半空"],
    )
    assert "口型不动" in camera
    assert "画外音" in camera
    assert "口型与声音同步" not in camera


def test_establishing_camera_keeps_single_move_dolly_direction():
    # 单段 dolly 是一个连续运镜，可直接执行；只有"切"才需要退回默认模板。
    from src.services.shot_prompt_materializer import _establishing_camera

    scene = Scene(
        scene_id=16, duration_sec=8, location="杨家院门口(上午,室外)",
        characters=["杨氏子"], dialogue=[],
        action_description="孩子安静站在院门口",
        camera_direction="wide shot，slow dolly out 缓缓拉远，孩子居画面上三分之二",
        emotion_tone="平静",
    )
    assert _establishing_camera(scene, ["孩子安静站在院门口"]) == scene.camera_direction

def test_establishing_camera_replaces_reverse_shot_plan_with_locked_two_shot():
    # 正反打会把一场戏的对白摊到多个机位，生成出来的子镜会反复倾倒同一段
    # 剧情。执行层统一改写成固定双人中近景，是否切换交给尾帧参考图。
    from src.services.shot_prompt_materializer import _establishing_camera

    cam_text = "竖屏近距对话正反打：0-2s 说话人近景开口，2s 切听者近景给出反应；两侧人物各居画面中心偏上"
    scene = Scene(
        scene_id=4, duration_sec=9, location="杨家院门口", characters=["孔君平", "杨氏子"],
        action_description="孔君平站在门口含笑发问",
        camera_direction=cam_text, emotion_tone="和蔼",
    )
    resolved = _establishing_camera(scene, ["孔君平站在门口含笑发问"])
    assert resolved != cam_text
    assert "正反打" not in resolved.replace("不要正反打", "")
    assert "固定双人中近景" in resolved


def test_establishing_camera_still_drops_untimed_cut_plan():
    # 只有"切"没有时间锚的模糊多段调度仍然退回默认模板。
    from src.services.shot_prompt_materializer import _establishing_camera

    scene = Scene(
        scene_id=15, duration_sec=8, location="杨家院门外小路", characters=["孔君平"],
        action_description="孔君平沿小路走远",
        camera_direction="全景转侧跟，随后切到孔君平若有所思的特写",
        emotion_tone="平静",
    )
    assert _establishing_camera(scene, ["孔君平沿小路走远"]) != scene.camera_direction
    assert "固定" in _establishing_camera(scene, ["孔君平沿小路走远"])


def test_compact_template_child_keeps_prop_anchor_as_key_props():
    # 紧凑模板父级的"道具锚点：…"行与核心分镜意图同块，会被剥离；
    # 组装子镜时必须抢救成【关键道具】段，道具状态才能进执行层。
    from src.services.shot_planner import build_shot_plan

    scene = Scene(
        scene_id=1, duration_sec=8, location="杨家院子", characters=["杨氏子"],
        action_description="孩子独坐院中，闻敲门声起身。",
        dialogue=[],
        camera_direction="特写，slow dolly in 推近孩子面部",
        emotion_tone="安静",
    )
    script = ScriptBreakdown(title="杨氏之子", genre="剧情", logline="测试", duration_sec=8, narration_style="无旁白", scenes=[scene])
    parent = ShotPrompt(
        scene_id=1,
        prompt=(
            "【单镜头时间轴】\n"
            "核心分镜意图：杨氏子独坐石凳，闻声起身。\n"
            "视觉风格：写实；古雅低饱和。\n"
            "道具锚点：石凳位于孩子身侧，青灰色石面，表面干净无杂物。\n"
            "0s-3s，镜头缓慢推近孩子面部。\n"
            "0s-8s，他撑膝站起身。"
        ),
        duration_sec=8,
    )
    plan = build_shot_plan(scene, 15, provider_min_duration_sec=4)
    shots = materialize_shot_prompts(
        GenerationPrompts(api_provider="happyhorse", shots=[parent], shot_plans=[plan]),
        script, duration_cap_sec=15, provider_min_duration_sec=4,
    )
    assert "【关键道具】石凳位于孩子身侧" in shots[0].prompt
    assert "核心分镜意图" not in shots[0].prompt
