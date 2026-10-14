"""Regression tests for Chinese image prompts and isolated character generation."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from src.agents.image_gen_agent import (
    CHARACTER_PROMPT_TEMPLATE_VERSION,
    ImageGenerationAgent,
    build_character_image_prompt,
    build_character_negative_prompt,
    diagnose_character_prompt,
)
from src.agents.visual_prompt_agent import COMPACT_SYSTEM_PROMPT
from src.agents.video_gen_agent import VideoGenerationAgent
from src.api_clients.image_mock import MockImageClient
from src.schemas.script import Scene, ScriptBreakdown, VisualAssetManifest, VisualCharacterAsset
from src.schemas.visual import SceneVisual, VisualDesign
from src.schemas.project import CreativeProfile
from src.schemas.character import CharacterImage
from src.graph.state import DramaState
from src.services.asset_prompt_composer import compose_asset_prompt
from src.api.context import _character_description
from src.utils.visual_asset_manifest import build_manifest_from_scenes


class _CaptureSceneImageClient:
    provider_name = "mock"

    def __init__(self, root: Path):
        self.root = root
        self.calls = []
        self.index = 0

    async def generate_image(self, **kwargs):
        self.calls.append(kwargs)
        self.index += 1
        path = self.root / f"scene-{self.index}.png"
        Image.new("RGB", (1920, 1080), "white").save(path)
        from src.api_clients.image_base import ImageGenResult

        return ImageGenResult(
            True,
            local_path=str(path),
            width=1920,
            height=1080,
            provider_name=self.provider_name,
        )


def test_character_description_normalizes_scene_names_without_mutating_set_during_iteration():
    state = DramaState(
        script=ScriptBreakdown(
            title="角色提示词预览",
            genre="都市",
            logline="测试角色描述读取。",
            duration_sec=6,
            narration_style="无旁白",
            scenes=[
                Scene(
                    scene_id=1,
                    duration_sec=6,
                    location="便利店",
                    characters=["顾衡 (Gu Heng)"],
                    action_description="顾衡站在便利店门口，观察雨中的街道。",
                    camera_direction="中景",
                    emotion_tone="警觉",
                )
            ],
        )
    )

    description = _character_description(state, "顾衡 (Gu Heng)")

    assert "剧本可见外貌线索" in description
    assert "顾衡站在便利店门口" in description


class _StableScenePromptLLM:
    async def generate_text(self, **_kwargs):
        return "临时生成的场景描述"


def test_character_generation_uses_four_chinese_views_only():
    script = ScriptBreakdown(
        title="测试短剧",
        genre="都市悬疑",
        logline="测试角色形象生成。",
        duration_sec=6,
        narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=6,
                location="办公室",
                characters=["林夏"],
                action_description="林夏看向窗外。",
                camera_direction="中景",
                emotion_tone="紧张",
            )
        ],
    )
    visual = VisualDesign(
        overall_style="现代悬疑电影级写实",
        color_palette="冷蓝灰",
        global_character_descriptions={"林夏": "25岁女性，黑色中长发，身形纤瘦，穿米白色长风衣和黑色平底鞋，神情紧张但坚毅。"},
        scenes=[],
    )
    with TemporaryDirectory() as temp_dir:
        agent = ImageGenerationAgent(api_client=MockImageClient())
        agent.output_dir = Path(temp_dir)
        characters, scenes = asyncio.run(
            agent.run(script, visual, include_characters=True, include_scenes=False)
        )

        assert [image.view_type for image in characters.characters] == [
            "front_half", "front_full", "left_profile", "back_full",
        ]
        assert scenes.scenes == []
        assert all("高质量专业角色设定图" in image.image_prompt for image in characters.characters)
        assert all("纯白色干净背景" in image.image_prompt for image in characters.characters)
        assert all(image.image_path for image in characters.characters)


def test_legacy_scene_generation_uses_canonical_style_locked_prompts(tmp_path):
    script = ScriptBreakdown(
        title="场景风格兼容",
        genre="都市",
        logline="测试旧阶段入口。",
        duration_sec=6,
        narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=6,
                location="便利店",
                characters=[],
                action_description="有人推门进入并拿起饮料。",
                camera_direction="全景",
                emotion_tone="平静",
            )
        ],
    )
    script.asset_manifest = build_manifest_from_scenes(script)
    visual = VisualDesign(
        overall_style="现代电影级二维赛璐璐",
        color_palette="冷蓝灰",
        scenes=[],
    )
    client = _CaptureSceneImageClient(tmp_path)
    agent = ImageGenerationAgent(api_client=client)
    agent.output_dir = tmp_path
    agent.llm = _StableScenePromptLLM()

    _characters, scenes = asyncio.run(
        agent.run(script, visual, project_id="legacy-style", include_characters=False, include_scenes=True)
    )

    assert len(scenes.scenes) == 2
    assert all("二维背景绘制" in item.image_prompt for item in scenes.scenes)
    assert all("横向16:9" in item.image_prompt for item in scenes.scenes)
    assert all("动作描述" not in item.image_prompt for item in scenes.scenes)
    assert all("照片级实拍" in call["negative_prompt"] for call in client.calls)

    visual.overall_style = "电影级真人写实"
    client.calls.clear()
    _characters, live_scenes = asyncio.run(
        agent.run(script, visual, project_id="legacy-live", include_characters=False, include_scenes=True)
    )
    assert all("电影级真人实拍环境" in item.image_prompt for item in live_scenes.scenes)
    assert all("动漫场景" in call["negative_prompt"] for call in client.calls)


def test_image_generation_prefers_stable_project_style_for_character_prompts(tmp_path):
    script = ScriptBreakdown(
        title="角色风格兼容",
        genre="都市",
        logline="测试项目档案风格优先级。",
        duration_sec=6,
        narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=6,
                location="办公室",
                characters=["林夏"],
                action_description="林夏站在窗边。",
                camera_direction="中景",
                emotion_tone="平静",
            )
        ],
    )
    visual = VisualDesign(
        overall_style="电影级真人写实",
        color_palette="冷蓝灰",
        global_character_descriptions={"林夏": "25岁女性，黑色中长发，穿米白色风衣。"},
        scenes=[],
    )
    client = _CaptureSceneImageClient(tmp_path)
    agent = ImageGenerationAgent(api_client=client)
    agent.output_dir = tmp_path

    characters, _scenes = asyncio.run(
        agent.run(
            script,
            visual,
            project_id="stable-character-style",
            include_characters=True,
            include_scenes=False,
            creative_profile=CreativeProfile(
                visual_style_id="cinematic_cel",
                art_style="写实",
            ),
        )
    )

    assert characters.characters
    assert all("二维手绘动画媒介" in item.image_prompt for item in characters.characters)
    assert all("MANDATORY STYLE: photorealistic" not in item.image_prompt for item in characters.characters)


def test_scene_reference_generation_drops_dynamic_visual_key_elements(tmp_path):
    script = ScriptBreakdown(
        title="场景环境元素边界",
        genre="都市悬疑",
        logline="测试场景空镜不携带人物动作。",
        duration_sec=6,
        narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=6,
                location="便利店",
                characters=["苏眠"],
                action_description="苏眠站在便利店屋檐下，推开玻璃门。",
                camera_direction="全景",
                emotion_tone="压抑",
            )
        ],
    )
    script.asset_manifest = build_manifest_from_scenes(script)
    visual = VisualDesign(
        overall_style="现代电影级二维赛璐璐",
        color_palette="冷蓝灰",
        scenes=[
            SceneVisual(
                scene_id=1,
                composition="正面大全景",
                lighting="雨夜灯牌光",
                color_tone="冷蓝灰",
                key_elements=["苏眠站在便利店屋檐下", "白色灯牌", "积水路面"],
            )
        ],
    )
    client = _CaptureSceneImageClient(tmp_path)
    agent = ImageGenerationAgent(api_client=client)
    agent.output_dir = tmp_path

    asyncio.run(agent.run(script, visual, include_characters=False, include_scenes=True))

    assert client.calls
    assert all("苏眠站在便利店屋檐下" not in call["prompt"] for call in client.calls)
    assert all("白色灯牌" in call["prompt"] for call in client.calls)


def test_each_character_view_has_a_different_mandatory_framing_instruction():
    prompts = {
        view: build_character_image_prompt("林夏", "女性，25岁，黑色中长发，穿米白色风衣。", view)
        for view in ("front_half", "front_full", "left_profile", "back_full")
    }

    assert len(set(prompts.values())) == 4
    assert "胸部以上至头顶" in prompts["front_half"]
    assert "头顶到鞋底完整入画" in prompts["front_full"]
    assert "严格面向画面左侧" in prompts["left_profile"]
    assert "人物完全背向镜头" in prompts["back_full"]
    assert "脸部完全不可见" in prompts["back_full"]
    assert "面部清晰" not in prompts["back_full"]
    assert "正脸" in build_character_negative_prompt("back_full")
    assert "裁切脚部" in build_character_negative_prompt("front_full")


def test_video_prefers_new_front_full_reference_with_legacy_fallback():
    assert VideoGenerationAgent._choose_character_reference(
        {"front_full": "new.png", "full_body": "old.png"}
    ) == "new.png"
    assert VideoGenerationAgent._choose_character_reference(
        {"full_body": "old.png", "portrait": "portrait.png"}
    ) == "old.png"


def test_missing_character_asset_can_start_from_an_editable_draft():
    from src.api.context import ImageAssetRequest, _create_draft_image_asset

    state = DramaState(
        visual_design=VisualDesign(
            overall_style="现代悬疑",
            color_palette="冷蓝灰",
            global_character_descriptions={"林夏": "25岁女性，黑色中长发，穿米白色长风衣。"},
            scenes=[],
        )
    )
    asset = _create_draft_image_asset(
        state,
        ImageAssetRequest(asset_type="character", asset_id="林夏", view_type="front_half"),
    )

    assert asset.view_type == "front_half"
    assert "高质量专业角色设定图" in asset.image_prompt
    assert state.character_images is not None
    assert state.character_images.characters == [asset]

    full_asset = _create_draft_image_asset(
        state,
        ImageAssetRequest(asset_type="character", asset_id="林夏", view_type="front_full"),
    )
    assert (full_asset.width, full_asset.height) == (1080, 1920)
    assert full_asset.prompt_template_version == CHARACTER_PROMPT_TEMPLATE_VERSION


def test_legacy_english_key_uses_the_saved_chinese_character_description():
    from src.api.context import _character_description

    state = DramaState(
        visual_design=VisualDesign(
            overall_style="现代悬疑",
            color_palette="冷蓝灰",
            global_character_descriptions={"苏眠 (Su Mian)": "female, 26 years old"},
            global_character_descriptions_cn={"苏眠": "女性，26岁，黑色中长发，穿米白色风衣。"},
            scenes=[],
        )
    )

    assert _character_description(state, "苏眠 (Su Mian)") == "女性，26岁，黑色中长发，穿米白色风衣。"


def test_character_prompt_merges_manifest_details_when_visual_registry_is_short():
    """A terse visual-stage summary must not hide the richer asset description."""
    state = DramaState(
        visual_design=VisualDesign(
            overall_style="现代悬疑",
            color_palette="冷蓝灰",
            global_character_descriptions_cn={"苏眠": "女性，气质清冷。"},
            scenes=[],
        ),
        script=ScriptBreakdown(
            title="描述合并",
            genre="悬疑",
            logline="验证角色提示词来源合并。",
            duration_sec=3,
            narration_style="无旁白",
            scenes=[
                Scene(
                    scene_id=1,
                    duration_sec=3,
                    location="办公室",
                    characters=["苏眠"],
                    action_description="苏眠站在办公室门口。",
                    camera_direction="中景",
                    emotion_tone="紧张",
                )
            ],
            asset_manifest=VisualAssetManifest(
                characters=[
                    VisualCharacterAsset(
                        asset_id="char_su_mian",
                        name="苏眠",
                        appearance_mode="on_screen",
                        description="苏眠：约26岁女性；黑色中长发；米白色棉质长风衣；细眉、杏眼；左眼下方有小痣。",
                    )
                ]
            ),
        ),
    )

    description = _character_description(state, "苏眠")
    prompt = build_character_image_prompt("苏眠", description, "front_half")

    assert "黑色中长发" in description
    assert "左眼下方有小痣" in description
    assert "【角色稳定外貌锚点】" in prompt
    assert "左眼下方有小痣" in prompt


def test_compact_visual_prompt_requires_complete_character_anchors():
    for term in ("性别与年龄段", "脸型与肤色", "眉眼鼻唇", "服装款式/主色/材质", "配饰"):
        assert term in COMPACT_SYSTEM_PROMPT
    assert "外貌以剧本为准" in COMPACT_SYSTEM_PROMPT


def test_character_master_prompt_inherits_anime_project_style():
    prompt = build_character_image_prompt(
        "苏眠",
        "女性，黑色中长发，穿米白色风衣。",
        "front_half",
        art_style="动漫风",
    )
    negative = build_character_negative_prompt("front_half", art_style="动漫风")

    assert "动漫风" in prompt
    assert "二次元" in prompt or "动画角色" in prompt
    assert "photorealistic" not in prompt
    assert "真人写实照片" not in prompt
    assert "动漫" not in negative
    assert "真人摄影" in negative or "真人照片" in negative
    assert "胸部以下不得入画" in prompt
    assert "裁剪身体" not in negative
    assert "下身与鞋子信息仅作身份档案" in prompt
    assert "严格胸像特写" in prompt


def test_character_asset_preview_uses_project_art_style_in_provider_prompt():
    state = DramaState(
        project_id="anime-project",
        creative_profile=CreativeProfile(art_style="动漫风", video_ratio="9:16"),
        visual_design=VisualDesign(
            overall_style="动漫风",
            color_palette="明亮高饱和",
            global_character_descriptions_cn={"苏眠": "女性，黑色中长发，穿米白色风衣。"},
            scenes=[],
        ),
    )
    asset = CharacterImage(
        character_name="苏眠",
        view_type="front_half",
        image_prompt="",
        image_path="",
        seed=1,
    )

    composition = compose_asset_prompt(state, "character", "苏眠", "front_half", asset)

    assert "动漫风" in composition["provider_prompt"]
    assert "photorealistic" not in composition["provider_prompt"]
    assert "真人写实照片" not in composition["provider_prompt"]
    assert "real actor identity" not in composition["provider_prompt"]
    assert "锁定一个动漫角色身份" in composition["provider_prompt"]


def test_character_reference_group_keeps_anime_style_contract():
    from src.services.asset_generation import _character_reference_group_prompt

    prompt = _character_reference_group_prompt(
        "苏眠",
        "女性，黑色中长发，穿米白色风衣。",
        "",
        "动漫风",
    )

    assert "动漫/二次元角色设计" in prompt
    assert "禁止变成真人照片" in prompt
    assert "不得生成拼图" in prompt
    assert "动漫或数字人风格" not in prompt


def test_character_prompt_diagnostics_detect_style_conflicts():
    good = diagnose_character_prompt(
        "动漫风",
        "MANDATORY STYLE: 2D anime character design, 动漫风格角色设定图。",
        "photorealistic, 真人照片",
    )
    bad = diagnose_character_prompt(
        "动漫风",
        "photorealistic live-action human actor casting photo",
        "动漫，二次元",
    )

    assert good["status"] == "passed"
    assert good["missing_style_terms"] == []
    assert bad["status"] == "failed"
    assert bad["contradictory_prompt_terms"]


if __name__ == "__main__":
    test_character_generation_uses_four_chinese_views_only()
    test_each_character_view_has_a_different_mandatory_framing_instruction()
    test_video_prefers_new_front_full_reference_with_legacy_fallback()
    test_missing_character_asset_can_start_from_an_editable_draft()
    test_legacy_english_key_uses_the_saved_chinese_character_description()
    print("four-view-image-tests-ok")


def test_character_anchor_does_not_duplicate_name_or_period():
    # 外观描述本身以角色名开头且自带句号；锚点拼接不得造成
    # "杨氏子，杨氏子，……浅痣。。"式重复。
    from src.agents.image_gen_agent import build_character_image_prompt

    description = "杨氏子，男，约七八岁，身形瘦小挺直。黑发梳成双总角，左眉尾有一粒浅痣。"
    prompt = build_character_image_prompt("杨氏子", description, "front_half", art_style="电影级真人写实")

    assert "杨氏子，杨氏子" not in prompt
    assert "。。" not in prompt
    assert "杨氏子，男，约七八岁" in prompt


def test_character_style_layer_does_not_force_adult_proportions():
    # 项目含儿童角色（杨氏子约七八岁）；风格执行层不得硬性要求成人比例。
    from src.services.visual_styles import get_visual_style

    for style_id in ("live_action_cinematic", "semi_realistic_anime"):
        layer = get_visual_style(style_id)["prompt_layers"]["character"]
        assert "成年人体比例" not in layer
        assert "年龄" in layer


def test_character_negative_prompt_has_no_duplicate_terms():
    from src.agents.image_gen_agent import build_character_negative_prompt

    negative = build_character_negative_prompt("front_half", art_style="电影级真人写实")
    terms = [term.strip() for term in negative.replace("，", ",").split(",") if term.strip()]
    assert len(terms) == len(set(terms)), [t for t in terms if terms.count(t) > 1]
    assert negative.endswith(("换服装", "手办质感")) or not negative.endswith("，")


def test_prop_view_prompt_has_no_double_period():
    # 道具描述自带句号时，模板不得再补一个（"……叙事道具。。"）。
    from src.agents.image_gen_agent import build_prop_view_prompt

    prompt = build_prop_view_prompt(
        "一盘鲜红杨梅",
        "素色白瓷浅盘，盛放鲜红杨梅十余颗，是全片的关键叙事道具。",
        "front",
        style="电影级真人写实",
    )
    assert "。。" not in prompt
    assert "关键叙事道具。" in prompt
