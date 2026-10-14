from types import SimpleNamespace

from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.script import DialogueLine, Scene, ScriptBreakdown
from src.schemas.shot_plan import ShotPlan, ShotUnit
from src.services.native_audio import append_native_dialogue_prompt, build_native_dialogue_block, build_dialogue_plan
from src.services.shot_prompt_materializer import materialize_shot_prompts
from src.services.beat_planner import estimate_scene_capacity


def _scene():
    return SimpleNamespace(
        scene_id=2,
        dialogue=[
            SimpleNamespace(character="顾衡", line="你终于来了。", emotion="压低声音"),
            SimpleNamespace(character="苏眠", line="我一直在这里。", emotion=None),
        ],
    )


def test_seedance_dialogue_block_keeps_scene_specific_source_ids():
    block, lines = build_native_dialogue_block(_scene())
    assert "【对白】" in block
    assert "顾衡" in block
    assert lines[0]["line_id"] == "2:1"
    assert lines[1]["line_id"] == "2:2"


def test_append_native_dialogue_prompt_does_not_replace_original_prompt():
    prompt, lines = append_native_dialogue_prompt("一个雨夜的地下通道", _scene())
    assert prompt.startswith("一个雨夜的地下通道")
    assert "不新增台词" in prompt
    assert lines[0]["text"] == "你终于来了。"


def test_native_dialogue_block_can_limit_output_to_child_shot_dialogue_ids():
    block, lines = build_native_dialogue_block(
        _scene(),
        dialogue_ids=["scene-2-dialogue-2"],
    )

    assert [line["line_id"] for line in lines] == ["2:2"]
    assert "我一直在这里。" in block
    assert "你终于来了。" not in block


def test_append_native_dialogue_prompt_replaces_existing_dialogue_section():
    existing = (
        "【动作时间轴】0-4s 顾衡看向门口。\n"
        "【对白】\n顾衡｜压低声音：\n“你终于来了。”\n"
        "【镜头语言】固定近景"
    )

    prompt, lines = append_native_dialogue_prompt(
        existing,
        _scene(),
        dialogue_ids=["scene-2-dialogue-1"],
    )

    assert prompt.count("【对白】") == 1
    assert "2:1 顾衡" in prompt
    assert "你终于来了。" in prompt
    assert "我一直在这里。" not in prompt
    assert "【镜头语言】固定近景" in prompt
    assert [line["line_id"] for line in lines] == ["2:1"]


def test_append_native_dialogue_prompt_replaces_inline_provider_dialogue_section():
    existing = (
        "[Reference binding] 参考图1：顾衡。 "
        "【原始时间轴提示词】 【动作时间轴】0-4s 顾衡看向门口。 "
        "【对白】 顾衡｜压低声音：“你终于来了。” "
        "【镜头语言】固定近景 [Reference hierarchy] 保持身份。"
    )

    prompt, _ = append_native_dialogue_prompt(
        existing, _scene(), dialogue_ids=["scene-2-dialogue-1"],
    )

    assert prompt.count("【对白】") == 1
    assert prompt.count("你终于来了。") == 1
    assert "我一直在这里。" not in prompt
    assert "【镜头语言】固定近景" in prompt


def test_dialogue_speaker_falls_back_to_only_scene_character():
    scene = Scene(
        scene_id=7, duration_sec=6, location="公寓", characters=["德拉"],
        dialogue=[DialogueLine(character="", line="我必须给吉姆买一件礼物。")],
        action_description="德拉数零钱", camera_direction="近景", emotion_tone="焦虑",
    )
    script = ScriptBreakdown(title="测试", genre="测试", logline="", duration_sec=6, narration_style="无旁白", characters=[], scenes=[scene])
    parent = ShotPrompt(scene_id=7, prompt="【动作时间轴】0-6s：德拉数零钱", duration_sec=6)
    plan = ShotPlan(
        scene_id=7, parent_duration_sec=6,
        capacity=estimate_scene_capacity(scene, provider_min_duration_sec=4, provider_max_duration_sec=15),
        units=[ShotUnit(
            shot_id="scene-7-shot-1", parent_scene_id=7, order=1,
            beat_ids=["scene-7-beat-1"], dialogue_ids=["scene-7-dialogue-1"], duration_sec=6,
        )],
    )

    shots = materialize_shot_prompts(
        GenerationPrompts(api_provider="happyhorse", shots=[parent], shot_plans=[plan]),
        script, provider_min_duration_sec=4, duration_cap_sec=15,
    )
    prompt, payload = build_native_dialogue_block(scene, ["scene-7-dialogue-1"])

    assert "德拉：" in shots[0].prompt
    assert "德拉：" in prompt
    assert payload[0]["character"] == "德拉"
    assert "未知角色" not in shots[0].prompt


def test_native_dialogue_replaces_key_dialogue_in_place_instead_of_moving_it_to_prompt_tail():
    existing = (
        "【本镜执行核心】吉姆进门后发问。\n"
        "【动作时间轴】0-6s 吉姆推门进屋，德拉起身走近。\n"
        "【关键对白（必须出现）】吉姆：\"你已经把头发剪掉了？\"\n"
        "【环境与视觉风格】破旧公寓。\n"
        "【本镜禁止】不得加入其他对白。"
    )
    scene = SimpleNamespace(
        scene_id=8,
        dialogue=[SimpleNamespace(character="吉姆", line="你已经把头发剪掉了？", emotion=None)],
    )

    prompt, _ = append_native_dialogue_prompt(existing, scene, ["scene-8-dialogue-1"])

    assert prompt.count("你已经把头发剪掉了？") == 1
    assert prompt.index("【动作时间轴】") < prompt.index("【关键对白（必须出现）】")
    assert prompt.index("【关键对白（必须出现）】") < prompt.index("【环境与视觉风格】")


def test_append_native_dialogue_skips_line_already_embedded_in_timeline():
    from types import SimpleNamespace
    scene = SimpleNamespace(
        scene_id=8,
        dialogue=[SimpleNamespace(character="吉姆", line="你已经把头发剪掉了？", emotion=None)],
    )
    embedded = (
        "【动作时间轴】\n"
        "0-4s 七点整，吉姆推门进屋，死死盯着德拉，这时候吉姆：\"你已经把头发剪掉了？\"。\n"
        "【环境与视觉风格】破旧公寓。"
    )

    prompt, lines = append_native_dialogue_prompt(embedded, scene, ["scene-8-dialogue-1"])

    assert prompt.count("你已经把头发剪掉了？") == 1
    assert "【关键对白（必须出现）】" not in prompt
    assert "【对白】" not in prompt
    assert lines[0]["character"] == "吉姆"


def _over_cap_scene():
    # 真实场景7的整句：紧凑估算约15.7s，超过15s模型上限，规划必然拆段。
    return Scene(
        scene_id=7, duration_sec=12, location="公寓", characters=["德拉"],
        dialogue=[DialogueLine(
            character="德拉",
            line="吉姆，亲爱的，别那样盯着我。我把头发剪掉卖了，因为不送你一件礼物，我无法过圣诞节。头发会再长起来——你不会介意，是吗？我非这么做不可。我的头发长得快极了。说'恭贺圣诞'吧！吉姆，让我们快快乐乐的。你肯定猜不着我给你买了一件多么好的——多么美丽精致的礼物啊！",
        )],
        action_description="德拉握着表链紧张地站起身。",
        camera_direction="近景", emotion_tone="急切",
    )


def test_build_dialogue_plan_resolves_part_ids_to_their_own_segment():
    from src.services.beat_planner import split_speech_chunks

    scene = _over_cap_scene()
    chunks = split_speech_chunks(scene.dialogue[0].line, 14.0)
    assert len(chunks) >= 2

    first = build_dialogue_plan(scene, ["scene-7-dialogue-1-part-1"], speech_budget_sec=15)
    second = build_dialogue_plan(scene, ["scene-7-dialogue-1-part-2"], speech_budget_sec=15)

    assert first[0]["text"] == chunks[0]
    assert second[0]["text"] == chunks[-1]
    assert first[0]["text"] != scene.dialogue[0].line
    assert first[0]["character"] == "德拉"


def test_append_native_skips_block_when_only_own_part_is_inline():
    from src.services.beat_planner import split_speech_chunks

    scene = _over_cap_scene()
    chunk1 = split_speech_chunks(scene.dialogue[0].line, 14.0)[0]
    embedded = (
        "【动作时间轴】\n"
        f"0-15s 德拉握着表链紧张地站起身，这时候德拉：\"{chunk1}\"。\n"
        "【环境与视觉风格】破旧公寓。"
    )

    prompt, lines = append_native_dialogue_prompt(
        embedded, scene, ["scene-7-dialogue-1-part-1"], speech_budget_sec=15,
    )

    assert lines[0]["text"] == chunk1
    assert "【关键对白（必须出现）】" not in prompt
    assert prompt.count(chunk1) == 1


def test_embed_dialogue_after_question_mark_has_no_double_period():
    from src.services.prompt_execution_context import _embed_dialogue_in_timeline
    timeline = "0-4s 吉姆推门进屋，盯着德拉。"
    dialogue = "吉姆：\"你已经把头发剪掉了？\""

    rendered = _embed_dialogue_in_timeline(timeline, dialogue)

    assert rendered.endswith('？"')
    assert '？"。' not in rendered


def test_embed_dialogue_after_plain_action_still_ends_period():
    from src.services.prompt_execution_context import _embed_dialogue_in_timeline
    timeline = "0-6s 德拉紧张地站起来向他走去。"
    dialogue = "德拉：\"剪掉卖了。\""

    rendered = _embed_dialogue_in_timeline(timeline, dialogue)

    assert "这时候德拉" in rendered
    assert rendered.endswith('"')


def _yang_scene():
    return SimpleNamespace(
        scene_id=4,
        dialogue=[SimpleNamespace(character="孔君平", line="你的父亲在家吗？", emotion=None)],
    )


def test_append_native_skips_when_line_lives_in_later_timeline_section():
    # 父级模板残留的空【时间轴分镜】壳排最前；真正内嵌台词的是后面的
    # 【动作时间轴】。追加器必须扫完所有时间轴段再判断幂等，否则台词插两遍。
    embedded = (
        "【时间轴分镜】\n"
        "【项目视觉】写实；9:16画幅。\n"
        "【动作时间轴】0-4s 孔君平站在门口含笑发问，这时候孔君平：\"你的父亲在家吗？\"\n"
        "【本镜禁止】不得新增对白。"
    )

    final, _ = append_native_dialogue_prompt(
        embedded, _yang_scene(), ["scene-4-dialogue-1"], speech_budget_sec=15,
    )

    assert final.count("你的父亲在家吗？") == 1
    assert "【关键对白（必须出现）】" not in final


def test_append_native_inserts_after_real_timeline_not_empty_shell():
    embedded = (
        "【时间轴分镜】\n"
        "【动作时间轴】0-4s 孔君平站在门口含笑发问。\n"
        "【本镜禁止】不得新增对白。"
    )

    final, _ = append_native_dialogue_prompt(
        embedded, _yang_scene(), ["scene-4-dialogue-1"], speech_budget_sec=15,
    )

    assert final.count("你的父亲在家吗？") == 1
    assert final.index("【关键对白（必须出现）】") > final.index("【动作时间轴】")
