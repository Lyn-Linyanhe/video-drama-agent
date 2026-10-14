from src.schemas.prompt import ShotPrompt
from src.schemas.script import DialogueLine, Scene
from src.services.prompt_execution_context import build_execution_context, render_execution_prompt


def _scene():
    return Scene(
        scene_id=8,
        duration_sec=6,
        location="破旧廉租公寓起居室",
        characters=["吉姆", "德拉"],
        dialogue=[DialogueLine(character="吉姆", line="你已经把头发剪掉了？")],
        action_description=(
            "七点整，吉姆推门进屋，站在门口纹丝不动，死死盯着德拉；"
            "德拉手里握着白金表链，紧张地站起来向他走去，表情从期待变为慌乱。"
        ),
        camera_direction="固定侧面中景",
        emotion_tone="震惊、慌乱",
    )


def _shot():
    return ShotPrompt(
        scene_id=8,
        shot_id="scene-8-shot-1",
        parent_scene_id=8,
        shot_order=1,
        dialogue_ids=["scene-8-dialogue-1"],
        prompt=(
            "【项目视觉】电影级真人写实。\n"
            "【出镜角色与身份锚点】吉姆：完整长篇外貌档案。德拉：完整长篇外貌档案。\n"
            "【主体状态】德拉手里握着白金表链。\n"
            "【动作时间轴】0-6s 七点整，吉姆推门进屋，站在门口；德拉起身走近并慌乱解释。\n"
            "【镜头语言】固定侧面中景。\n"
            "【环境】破旧廉租公寓起居室，晚间暖黄灯光。\n"
            "【对白】吉姆：\"你已经把头发剪掉了？\""
        ),
        duration_sec=6,
    )


def test_execution_prompt_puts_action_and_dialogue_before_environment_and_omits_identity_when_refs_cover_characters():
    refs = [
        {"asset_type": "scene", "asset_id": "loc-8", "view_type": "scene_center", "image_path": "scene.png"},
        {"asset_type": "character", "asset_id": "吉姆", "view_type": "left_profile", "image_path": "jim.png"},
        {"asset_type": "character", "asset_id": "德拉", "view_type": "left_profile", "image_path": "della.png"},
        {"asset_type": "prop", "asset_id": "prop_watch_chain", "view_type": "front", "image_path": "chain.png"},
    ]
    context = build_execution_context(shot=_shot(), scene=_scene(), valid_references=refs)
    rendered = render_execution_prompt(context)

    assert "【出镜角色与身份锚点】" not in rendered
    assert "吉姆推门进屋" in rendered
    assert "德拉手里握着白金表链" in rendered
    assert "你已经把头发剪掉了？" in rendered
    assert rendered.index("【动作时间轴】") < rendered.index("【环境与视觉风格】")
    timeline = rendered.split("【动作时间轴】", 1)[1].split("【", 1)[0]
    assert "你已经把头发剪掉了？" in timeline
    assert "吉姆" in timeline
    assert rendered.count("你已经把头发剪掉了？") == 1
    assert "identity_reference_fallback" not in rendered


def test_execution_prompt_falls_back_to_short_identity_block_when_character_reference_missing():
    context = build_execution_context(
        shot=_shot(),
        scene=_scene(),
        valid_references=[
            {"asset_type": "scene", "asset_id": "loc-8", "view_type": "scene_center", "image_path": "scene.png"},
        ],
    )
    rendered = render_execution_prompt(context)

    assert context.identity_reference_fallback is True
    assert "【身份回退约束】" in rendered
    assert "稳定身份信息由参考图补充" not in rendered


def test_scoped_child_does_not_fallback_to_parent_action_description():
    shot = _shot()
    shot.shot_id = "scene-8-shot-1"
    shot.dialogue_ids = []
    shot.prompt = "【镜头语言】固定中景。\n【环境】破旧廉租公寓。"
    rendered = render_execution_prompt(build_execution_context(shot=shot, scene=_scene(), valid_references=[]))
    assert "吉姆推门进屋" not in rendered
    assert "德拉手里握着白金表链" not in rendered


def test_scoped_child_without_dialogue_ids_does_not_inherit_scene_lines():
    shot = _shot()
    shot.shot_id = "scene-8-shot-2"
    shot.dialogue_ids = []
    shot.prompt = (
        "【动作时间轴】0-4s 德拉握着表链站在原地。\n"
        "【对白】吉姆：\"你已经把头发剪掉了？\"\n"
        "【本镜禁止执行】不得说出本镜未绑定对白。"
    )
    rendered = render_execution_prompt(build_execution_context(shot=shot, scene=_scene(), valid_references=[]))
    assert "你已经把头发剪掉了" not in rendered
    assert "【关键对白" not in rendered


def test_unscoped_parent_without_dialogue_ids_still_keeps_scene_lines():
    shot = _shot()
    shot.shot_id = None
    shot.dialogue_ids = []
    rendered = render_execution_prompt(build_execution_context(shot=shot, scene=_scene(), valid_references=[]))
    assert "你已经把头发剪掉了" in rendered
    timeline = rendered.split("【动作时间轴】", 1)[1].split("【", 1)[0]
    assert "你已经把头发剪掉了" in timeline

def test_humanize_reference_tags_restores_broken_chinese_and_character_names():
    from src.services.prompt_execution_context import humanize_reference_tags

    text = "白@prop:prop_watch_chain:front被@character:德拉:left_profile握着"
    assert humanize_reference_tags(text, prop_labels={"prop_watch_chain": "白金表链"}) == "白金表链被德拉握着"


def test_execution_prompt_humanizes_tags_and_does_not_duplicate_voice_instruction():
    shot = _shot()
    shot.prompt = (
        "【动作时间轴】0-4s @character:吉姆:left_profile推门进屋。\n"
        "【光线与色调】@character:德拉:left_profile面部偏暖。\n"
        "【关键道具】白@prop:prop_watch_chain:front被德拉握着。\n"
        "【对白】吉姆：\"你已经把头发剪掉了？\"\n对白必须生成真实人声并与嘴型同步，不得用字幕代替人声。"
    )
    refs = [
        {"asset_type": "scene", "asset_id": "loc-8", "label": "公寓", "image_path": "scene.png"},
        {"asset_type": "character", "asset_id": "吉姆", "label": "吉姆", "image_path": "jim.png"},
        {"asset_type": "character", "asset_id": "德拉", "label": "德拉", "image_path": "della.png"},
        {"asset_type": "prop", "asset_id": "prop_watch_chain", "label": "白金表链", "image_path": "chain.png"},
    ]
    rendered = render_execution_prompt(build_execution_context(shot=shot, scene=_scene(), valid_references=refs))
    assert "@character:" not in rendered
    assert "@prop:" not in rendered
    assert "白金表链被德拉握着" in rendered
    assert "吉姆推门进屋" in rendered
    assert rendered.count("对白必须生成真实人声") == 1

def test_prop_labels_from_display_reference_cover_watch_chain_tag():
    from src.services.prompt_execution_context import humanize_reference_tags, _prop_labels_from_references

    labels = _prop_labels_from_references([
        {
            "asset_type": "prop",
            "asset_id": "prop: 道具 白金表链 (front)",
            "label": "道具 白金表链",
            "image_path": "chain.png",
        }
    ])
    text = "白@prop:prop_watch_chain:front被德拉握着"
    assert humanize_reference_tags(text, prop_labels=labels) == "白金表链被德拉握着"

def test_execution_prompt_does_not_repeat_same_action_as_core_and_timeline():
    shot = _shot()
    shot.prompt = "【动作时间轴】0-4s 吉姆推门进屋，站在门口纹丝不动。"
    rendered = render_execution_prompt(build_execution_context(shot=shot, scene=_scene(), valid_references=[]))
    assert rendered.count("吉姆推门进屋") == 1
    assert "【本镜执行核心】" not in rendered
    timeline = rendered.split("【动作时间轴】", 1)[1].split("【", 1)[0]
    assert "吉姆推门进屋" in timeline

def test_execution_prompt_embeds_spoken_line_inside_action_timeline():
    rendered = render_execution_prompt(build_execution_context(shot=_shot(), scene=_scene(), valid_references=[]))
    timeline = rendered.split("【动作时间轴】", 1)[1].split("【", 1)[0]
    assert "你已经把头发剪掉了？" in timeline
    assert "吉姆" in timeline
    assert "【关键对白" not in rendered
    assert rendered.count("你已经把头发剪掉了？") == 1



def test_execution_prompt_splits_inline_lighting_out_of_camera_block():
    shot = ShotPrompt(
        scene_id=8,
        shot_id="scene-8-shot-1",
        parent_scene_id=8,
        shot_order=1,
        dialogue_ids=["scene-8-dialogue-1"],
        prompt=(
            "【动作时间轴】0-4s 吉姆推门进屋。\n"
            "【镜头语言】固定全景到中景，不要切镜头。【光线与色调】走廊冷光泄入，德拉面部偏暖。\n"
            "【环境】破旧廉租公寓。"
        ),
        duration_sec=4,
    )

    rendered = render_execution_prompt(
        build_execution_context(shot=shot, scene=_scene(), valid_references=[])
    )

    camera_line = next(
        (seg for seg in rendered.split("\n\n") if seg.startswith("【镜头与表演】")), ""
    )
    env_line = next(
        (seg for seg in rendered.split("\n\n") if seg.startswith("【环境与视觉风格】")), ""
    )
    assert "【光线与色调】" not in camera_line
    assert "走廊冷光泄入" in env_line
    assert "固定全景到中景" in camera_line

def test_execution_prompt_drops_prefix_duplicated_core_for_dialogue_only_child():
    # 纯对白子镜的兜底动作会同时充当"核心"与"时间轴"首句；核心只是动作
    # 第一个分句的逐字前缀时，不应再单列【本镜执行核心】制造重复。
    shot = ShotPrompt(
        scene_id=7,
        shot_id="scene-7-shot-3",
        prompt=(
            "【动作时间轴】\n"
            "0-6s 德拉保持当前站位、表情和关键道具，把本镜对白完整清楚地说完；"
            "对方只听、不打断、不开始新的进门或走动，这时候德拉：\"你肯定猜不着我给你买了一件多么好的礼物啊！\"。\n"
            "【当前镜头角色与道具】\n出镜角色：德拉"
        ),
        duration_sec=6,
    )
    scene = Scene(
        scene_id=7, duration_sec=6, location="公寓", characters=["德拉"],
        action_description="德拉握着表链",
        dialogue=[DialogueLine(character="德拉", line="你肯定猜不着我给你买了一件多么好的礼物啊。")],
        camera_direction="近景", emotion_tone="急切",
    )

    rendered = render_execution_prompt(build_execution_context(shot=shot, scene=scene, valid_references=[]))

    assert "【本镜执行核心】" not in rendered
    assert rendered.count("把本镜对白完整清楚地说完") == 1
    assert "你肯定猜不着我给你买了一件多么好的礼物啊！" in rendered

def test_embed_dialogue_uses_inner_monologue_connector():
    # 心理描写按用户要求保留为"内心独白"：时间轴用"心中暗道"引出，
    # 不再使用"这时候"（那是张嘴说话的指示）。
    from src.services.prompt_execution_context import _embed_dialogue_in_timeline

    timeline = "0-9s 孔君平愣住，端茶的手停在半空，眼睛微微睁大。"
    dialogue = "孔君平｜内心独白（画外音）：\n“如果因为孩子姓杨，就说杨梅是杨家的水果，那么孔雀岂不也该是孔家的鸟？”"

    rendered = _embed_dialogue_in_timeline(timeline, dialogue)

    assert "孔君平心中暗道：" in rendered
    assert "这时候" not in rendered
    assert "孔雀岂不也该是孔家的鸟" in rendered


def test_current_props_reads_dao_ju_anchored_section():
    # 部分父级模板把道具状态写在【道具锚点】段；执行层必须读到，
    # 否则杨梅特写镜的道具状态约束整个丢失。
    from src.services.prompt_execution_context import _sections, _current_props

    prompt = (
        "【单镜头时间轴】0-7s 杨梅特写。\n"
        "【道具锚点】白瓷浅盘居中于石桌，盘中杨梅鲜红饱满，为画面最显眼处。"
    )
    props = _current_props(_sections(prompt))
    assert "杨梅鲜红饱满" in props


def test_character_view_rank_ignores_camera_unrelated_prose():
    # 环境叙述里的"背影/侧脸"不得影响参考视图选择；
    # 全景镜头应优先正面全身参考。
    from src.api.context import _character_inline_view_rank

    frontal_prompt = (
        "【环境】暖光落在孩子侧脸上，背景虚化。\n"
        "【镜头与表演】固定中景，两人正面交流。\n"
        "【动作时间轴】0-6s 孔君平端起茶杯。\n"
    )
    wide_prompt = "【镜头与表演】wide shot，slow dolly out 缓缓拉远。\n【动作时间轴】0-8s 孩子站在院门口。\n"

    class Ref:
        def __init__(self, view_type):
            self.view_type = view_type

    assert _character_inline_view_rank(frontal_prompt, Ref("front_half")) < _character_inline_view_rank(frontal_prompt, Ref("back_full"))
    assert _character_inline_view_rank(wide_prompt, Ref("front_full")) < _character_inline_view_rank(wide_prompt, Ref("front_half"))
