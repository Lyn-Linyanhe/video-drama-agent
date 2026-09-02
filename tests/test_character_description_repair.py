import pytest
from src.schemas.script import Scene, ScriptBreakdown, VisualAssetManifest, VisualCharacterAsset
from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.visual import CharacterProfileRepairResult, SceneVisual, VisualDesign
from src.schemas.character import CharacterImage, CharacterImageAssets
from src.graph.state import DramaState
from src.api.context import (
    _repair_character_description_consumers,
    _state_from_json,
    _state_to_json,
)
from src.agents.visual_prompt_agent import VisualPromptAgent, _CharacterRegistryOutput, _stable_character_identity_description
from src.utils.visual_asset_manifest import (
    _character_facts_from_scenes,
    character_profile_missing_fields,
    enrich_character_description,
    enrich_manifest_descriptions,
)


def _scene(scene_id: int, action: str, characters: list[str]) -> Scene:
    return Scene(
        scene_id=scene_id,
        duration_sec=3,
        location="雨夜街道",
        characters=characters,
        action_description=action,
        camera_direction="中景",
        emotion_tone="紧张",
    )


def test_character_profile_requires_the_documented_eighty_character_minimum():
    description = (
        "成年女性，身形纤细，脸型圆润，肤色白皙，眉眼清秀，鼻梁挺直，"
        "唇形自然，黑色中长直发，穿米白色棉质风衣，佩戴银色腕表。"
    ).ljust(79, "稳")

    assert len(description) == 79
    assert "完整人物侧写（至少80个中文字符）" in character_profile_missing_fields(description)


def test_character_profile_accepts_compact_gender_and_approximate_age():
    description = (
        "女，约26岁，身形纤细修长；椭圆脸，肤色白皙，细眉杏眼，"
        "鼻梁挺直，唇色自然；黑色中长直发，穿米白色棉质长风衣与深色长裤，"
        "佩戴银色腕表，左眼下有一颗小痣，整体气质克制冷静。"
    ).ljust(80, "稳")

    assert "性别与年龄段" not in character_profile_missing_fields(description)


def test_enrichment_preserves_user_authored_structured_appearance_facts():
    original = "林澈：黑框眼镜；深色羊毛外套。"
    scenes = [
        _scene(
            1,
            "林澈是二十八岁青年男性，身形修长，脸型偏长，肤色自然，黑色短发。",
            ["林澈"],
        )
    ]

    enriched = enrich_character_description("林澈", original, scenes)

    assert "黑框眼镜" in enriched
    assert "深色羊毛外套" in enriched


def test_character_facts_accept_natural_color_and_adjacent_character_clauses():
    scenes = [
        _scene(1, "苏眠站在屋檐下，穿米白长风衣。", ["苏眠"]),
        _scene(2, "便利店玻璃上映出穿黑色雨衣、戴鸭舌帽和口罩的黑影。黑衣人抬手拨电话。", ["黑衣人"]),
        _scene(3, "陆临摘下口罩露出脸。他穿着黑色雨衣。", ["陆临"]),
        _scene(4, "顾衡穿深灰大衣。苏眠走进便利店。", ["顾衡", "苏眠"]),
        _scene(5, "玻璃上映出穿黑色雨衣、戴鸭舌帽和口罩的黑影。苏眠从反光中看见他，顾衡也看见。", ["苏眠", "顾衡", "黑衣人"]),
        _scene(6, "另一侧传来掌声，黑衣人摘下口罩露出陆临的脸——苏眠父亲最信任的学生。他穿着黑色雨衣，面带温和的笑。", ["陆临", "苏眠", "黑衣人"]),
    ]

    assert "米白长风衣" in _character_facts_from_scenes("苏眠", scenes)
    assert {"黑色雨衣", "鸭舌帽", "口罩"}.issubset(
        set(_character_facts_from_scenes("黑衣人", scenes))
    )
    assert "黑色雨衣" in _character_facts_from_scenes("陆临", scenes)
    assert "深灰大衣" not in _character_facts_from_scenes("苏眠", scenes)
    assert "黑色雨衣" not in _character_facts_from_scenes("苏眠", scenes)


def test_previous_fallback_description_is_repaired_from_newly_recognized_facts():
    fallback = "苏眠：剧本未提供额外稳定外观细节；后续镜头保持同一脸型、发型、体态和服装，不根据单个动作临时改造身份。"
    script = ScriptBreakdown(
        title="外观修复",
        genre="悬疑",
        logline="测试角色外观描述修复",
        duration_sec=3,
        narration_style="无旁白",
        scenes=[_scene(1, "苏眠站在屋檐下，穿米白长风衣。", ["苏眠"])],
        asset_manifest=VisualAssetManifest(
            characters=[VisualCharacterAsset(
                asset_id="char_su_mian",
                name="苏眠",
                appearance_mode="on_screen",
                description=fallback,
            )],
        ),
    )

    enrich_manifest_descriptions(script)

    description = script.asset_manifest.characters[0].description
    assert "米白长风衣" in description
    assert "剧本未提供额外稳定外观细节" not in description


def test_character_fact_repair_uses_generic_appearance_language_without_cross_attribution():
    scenes = [
        _scene(
            1,
            "林澈出现在走廊尽头。他披着墨绿色斗篷，留着银灰色短发，戴圆框眼镜。",
            ["林澈"],
        ),
        _scene(
            2,
            "林澈看见周岚穿着猩红色制服，随后转身离开。",
            ["林澈", "周岚"],
        ),
    ]

    facts = _character_facts_from_scenes("林澈", scenes)

    assert {"墨绿色斗篷", "银灰色短发", "圆框眼镜"}.issubset(set(facts))
    assert "猩红色制服" not in facts


def test_legacy_generated_fact_list_is_repaired_but_human_description_is_preserved():
    scenes = [
        _scene(1, "林澈站在门边，身穿靛蓝色制服。", ["林澈"]),
        _scene(2, "林澈看见周岚穿着猩红色制服。", ["林澈", "周岚"]),
    ]
    script = ScriptBreakdown(
        title="旧描述修复",
        genre="都市",
        logline="测试历史自动生成描述的泛化修复",
        duration_sec=6,
        narration_style="无旁白",
        scenes=scenes,
        asset_manifest=VisualAssetManifest(
            characters=[
                VisualCharacterAsset(
                    asset_id="char_lin_che",
                    name="林澈",
                    appearance_mode="on_screen",
                    description="林澈：猩红色制服；圆框眼镜。",
                ),
                VisualCharacterAsset(
                    asset_id="char_zhou_lan",
                    name="周岚",
                    appearance_mode="on_screen",
                    description="一名独立、冷静的调查记者，服装由用户后续指定。",
                ),
            ],
        ),
    )

    enrich_manifest_descriptions(script)

    descriptions = {
        item.name: item.description
        for item in script.asset_manifest.characters
    }
    assert "靛蓝色制服" in descriptions["林澈"]
    assert "猩红色制服" not in descriptions["林澈"]
    assert descriptions["周岚"].startswith("一名独立、冷静的调查记者")


def test_generic_identity_reveal_and_anonymous_figure_continuations_keep_facts_bound():
    scenes = [
        _scene(
            1,
            "玻璃上倒映一个穿着靛蓝制服、戴圆框眼镜的身影。守门人抬手示意，旁观者保持沉默。",
            ["守门人", "旁观者"],
        ),
        _scene(
            2,
            "陌生人摘下口罩露出顾川的脸。他穿着暗红色外套，留着卷发。",
            ["陌生人", "顾川"],
        ),
        _scene(
            3,
            "橱窗上映出一个穿着墨绿色斗篷的身影。观察者看见他，守门人也看见。守门人抬手示意。",
            ["守门人", "观察者"],
        ),
    ]

    figure_facts = _character_facts_from_scenes("守门人", scenes)
    revealed_facts = _character_facts_from_scenes("顾川", scenes)

    assert {"靛蓝制服", "圆框眼镜", "墨绿色斗篷"}.issubset(set(figure_facts))
    assert {"暗红色外套", "卷发"}.issubset(set(revealed_facts))


def test_single_fact_legacy_description_is_repairable_after_extraction_rules_change():
    script = ScriptBreakdown(
        title="单事实修复",
        genre="都市",
        logline="测试只包含一个旧自动事实的角色描述",
        duration_sec=3,
        narration_style="无旁白",
        scenes=[_scene(1, "顾川身穿暗红色外套，留着卷发。", ["顾川"])],
        asset_manifest=VisualAssetManifest(
            characters=[VisualCharacterAsset(
                asset_id="char_gu_chuan",
                name="顾川",
                appearance_mode="on_screen",
                description="顾川：脸上的笑容。",
            )],
        ),
    )

    enrich_manifest_descriptions(script)

    description = script.asset_manifest.characters[0].description
    assert "暗红色外套" in description
    assert "脸上的笑容" not in description


def test_repaired_manifest_description_syncs_visual_registry_and_prompt_anchor():
    old = "林澈：脸上的笑容。"
    new = "林澈：靛蓝色制服；圆框眼镜。"
    script = ScriptBreakdown(
        title="同步修复",
        genre="都市",
        logline="测试角色描述消费者同步",
        duration_sec=3,
        narration_style="无旁白",
        scenes=[_scene(1, "林澈身穿靛蓝色制服，戴圆框眼镜。", ["林澈"])],
        asset_manifest=VisualAssetManifest(
            characters=[VisualCharacterAsset(
                asset_id="char_lin_che",
                name="林澈",
                appearance_mode="on_screen",
                description=new,
            )],
        ),
    )
    state = DramaState(
        script=script,
        visual_design=VisualDesign(
            overall_style="写实",
            color_palette="冷色",
            global_character_descriptions={"林澈": old},
            global_character_descriptions_cn={"林澈": old},
            scenes=[SceneVisual(
                scene_id=1,
                composition="中景",
                lighting="自然光",
                color_tone="冷色",
                character_appearance={"林澈": old},
                key_elements=[],
            )],
        ),
        generation_prompts=GenerationPrompts(
            api_provider="mock",
            shots=[ShotPrompt(
                scene_id=1,
                prompt=f"【出镜角色与身份锚点】林澈：{old}。",
            )],
        ),
        character_images=CharacterImageAssets(characters=[CharacterImage(
            character_name="林澈",
            view_type="front_full",
            image_prompt=f"角色：林澈；设定：{old}",
            image_path="",
            seed=1,
        )]),
    )

    _repair_character_description_consumers(state, {"林澈": new})

    assert state.visual_design.global_character_descriptions_cn["林澈"] == new
    assert state.visual_design.global_character_descriptions["林澈"] == new
    assert state.visual_design.scenes[0].character_appearance["林澈"] == new
    assert new in state.generation_prompts.shots[0].prompt
    assert old not in state.generation_prompts.shots[0].prompt
    assert state.generation_prompts.shots[0].prompt.count("林澈：") == 1
    assert new not in state.character_images.characters[0].image_prompt
    assert "靛蓝色制服" in state.character_images.characters[0].image_prompt

    state.generation_prompts.shots[0].prompt = f"【出镜角色与身份锚点】林澈：{new}"
    _repair_character_description_consumers(state, {"林澈": new})
    assert state.generation_prompts.shots[0].prompt.count("林澈：") == 1


def test_load_repair_does_not_downgrade_a_complete_visual_profile():
    complete_profile = (
        "二十六岁成年女性，身形修长匀称；椭圆脸、自然白皙肤色，"
        "眉形清晰、杏眼沉静、鼻梁挺直、唇形自然；黑色中长直发，"
        "穿米白色棉质长风衣与深色长裤，佩戴细银色腕表，左眼下有一颗小痣。"
    )
    short_manifest = "苏眠：米白色长风衣；黑色中长发。"
    script = ScriptBreakdown(
        title="完整侧写优先",
        genre="都市悬疑",
        logline="加载项目时保留视觉阶段产出的完整人物档案。",
        duration_sec=3,
        narration_style="无旁白",
        scenes=[_scene(1, "苏眠站在门前。", ["苏眠"])],
        asset_manifest=VisualAssetManifest(
            characters=[VisualCharacterAsset(
                asset_id="char_su_mian",
                name="苏眠",
                appearance_mode="on_screen",
                description=short_manifest,
            )],
        ),
    )
    state = DramaState(
        script=script,
        visual_design=VisualDesign(
            overall_style="写实",
            color_palette="冷色",
            global_character_descriptions={"苏眠": complete_profile},
            global_character_descriptions_cn={"苏眠": complete_profile},
            scenes=[],
        ),
    )

    _repair_character_description_consumers(state, {"苏眠": short_manifest})

    assert state.visual_design.global_character_descriptions_cn["苏眠"] == complete_profile
    assert state.visual_design.global_character_descriptions["苏眠"] == complete_profile


def test_short_character_description_is_enriched_into_a_stable_side_profile():
    """A terse generated description must not hide stable facts in the script."""
    script = ScriptBreakdown(
        title="角色侧写完整性",
        genre="都市悬疑",
        logline="验证短角色描述的稳定外貌补全。",
        duration_sec=3,
        narration_style="无旁白",
        scenes=[
            _scene(
                1,
                "顾衡看上去二十九岁左右，身材高瘦，脸型偏长，肤色偏白，眉眼冷峻，鼻梁挺直，黑色短发，穿着深灰色羊毛大衣，戴银色腕表。",
                ["顾衡"],
            )
        ],
        asset_manifest=VisualAssetManifest(
            characters=[
                VisualCharacterAsset(
                    asset_id="char_gu_heng",
                    name="顾衡",
                    appearance_mode="on_screen",
                    description="顾衡：深灰大衣；冷峻。",
                )
            ]
        ),
    )

    enrich_manifest_descriptions(script)

    description = script.asset_manifest.characters[0].description
    assert description.startswith("顾衡：")
    for fact in ("二十九岁", "高瘦", "脸型偏长", "肤色偏白", "眉眼冷峻", "鼻梁挺直", "黑色短发", "深灰色羊毛大衣", "银色腕表"):
        assert fact in description


def test_character_registry_cannot_return_a_short_anchor_when_manifest_has_facts():
    script = ScriptBreakdown(
        title="角色注册表侧写",
        genre="都市悬疑",
        logline="验证视觉阶段角色注册表补全。",
        duration_sec=3,
        narration_style="无旁白",
        scenes=[
            _scene(
                1,
                "苏眠看上去二十六岁左右，身材纤细，脸型偏圆，肤色白皙，黑色中长发，穿米白色棉质长风衣，左眼下有一颗小痣。",
                ["苏眠"],
            )
        ],
        asset_manifest=VisualAssetManifest(
            characters=[
                VisualCharacterAsset(
                    asset_id="char_su_mian",
                    name="苏眠",
                    appearance_mode="on_screen",
                    description="苏眠：米白长风衣。",
                )
            ]
        ),
    )

    registry = VisualPromptAgent()._canonical_character_registry(
        {"苏眠": "米白长风衣。"}, script, english=False,
    )

    for fact in ("二十六岁", "纤细", "脸型偏圆", "肤色白皙", "黑色中长发", "米白色棉质长风衣", "左眼下"):
        assert fact in registry["苏眠"]


def test_character_registry_accepts_asset_id_keys_from_llm_output():
    complete_profile = (
        "二十六岁成年女性，身形修长匀称；椭圆脸、自然白皙肤色，"
        "眉形清晰、杏眼沉静、鼻梁挺直、唇形自然；黑色中长直发，"
        "穿米白色棉质长风衣与深色长裤，佩戴细银色腕表，左眼下有一颗小痣。"
    )
    script = ScriptBreakdown(
        title="资产编号角色表",
        genre="都市悬疑",
        logline="模型按资产编号返回人物侧写。",
        duration_sec=3,
        narration_style="无旁白",
        scenes=[_scene(1, "苏眠站在门前。", ["苏眠"])],
        asset_manifest=VisualAssetManifest(
            characters=[VisualCharacterAsset(
                asset_id="char_su_mian",
                name="苏眠",
                appearance_mode="on_screen",
                description="苏眠：米白色长风衣。",
            )],
        ),
    )

    registry = VisualPromptAgent()._canonical_character_registry(
        {"char_su_mian": complete_profile},
        script,
        english=False,
    )

    assert registry["苏眠"] == complete_profile


def test_project_load_recovers_character_profile_from_saved_script_draft():
    script = ScriptBreakdown(
        title="草稿侧写恢复",
        genre="都市悬疑",
        logline="验证结构化分镜丢失的外貌仍能从草稿恢复。",
        duration_sec=3,
        narration_style="无旁白",
        scenes=[_scene(1, "苏眠站在便利店屋檐下。", ["苏眠"])],
        asset_manifest=VisualAssetManifest(
            characters=[VisualCharacterAsset(
                asset_id="char_su_mian",
                name="苏眠",
                appearance_mode="on_screen",
                description="苏眠：米白长风衣。",
            )],
        ),
    )
    state = DramaState(
        project_id="profile-recovery",
        input_mode="script",
        script_phase="confirmed",
        script_draft_text=(
            "苏眠看上去二十六岁左右，身材纤细，脸型偏圆，肤色白皙，"
            "黑色中长发，穿米白色棉质长风衣，左眼下有一颗小痣。"
        ),
        script=script,
    )

    restored = _state_from_json(_state_to_json(state))

    description = restored.script.asset_manifest.characters[0].description
    for fact in ("二十六岁", "纤细", "脸型偏圆", "肤色白皙", "黑色中长发", "米白色棉质长风衣", "左眼下"):
        assert fact in description
    assert "剧本未提供额外稳定外观细节" not in description



def test_character_identity_separates_mutually_exclusive_hair_states():
    profile = "成年女性；身形修长；椭圆脸肤色白皙；黑色长发；剧情后剪成短发；米白色风衣"

    stable = _stable_character_identity_description(profile)

    assert "长发" not in stable
    assert "短发" not in stable
    assert "身形修长" in stable
    assert "米白色风衣" in stable
    assert "本镜角色状态" in stable


@pytest.mark.asyncio
async def test_character_reextract_keeps_asset_anchor_when_repair_remains_incomplete():
    script = ScriptBreakdown(
        title="侧写降级", genre="悬疑", logline="测试", duration_sec=3,
        narration_style="无旁白",
        scenes=[_scene(1, "苏眠站在屋檐下。", ["苏眠"])],
        asset_manifest=VisualAssetManifest(
            characters=[VisualCharacterAsset(
                asset_id="char_su_mian", name="苏眠", appearance_mode="on_screen",
                description="苏眠：米白色长风衣。",
            )],
        ),
    )

    class _AlwaysIncompleteLLM:
        async def generate_structured(self, **kwargs):
            return _CharacterRegistryOutput()

    agent = VisualPromptAgent(api_provider="mock")
    agent.llm = _AlwaysIncompleteLLM()

    _english, chinese = await agent.reextract_visible_characters(script)

    assert chinese["苏眠"] == "苏眠：米白色长风衣。"
    assert agent.character_profile_repair_status == "partial"


@pytest.mark.asyncio
async def test_character_reextract_api_failure_uses_existing_asset_anchor():
    script = ScriptBreakdown(
        title="侧写异常降级", genre="悬疑", logline="测试", duration_sec=3,
        narration_style="无旁白",
        scenes=[_scene(1, "苏眠站在屋檐下。", ["苏眠"])],
        asset_manifest=VisualAssetManifest(
            characters=[VisualCharacterAsset(
                asset_id="char_su_mian", name="苏眠", appearance_mode="on_screen",
                description="苏眠：米白色长风衣。",
            )],
        ),
    )

    class _FailingLLM:
        async def generate_structured(self, **kwargs):
            raise RuntimeError("temporary profile provider failure")

    agent = VisualPromptAgent(api_provider="mock")
    agent.llm = _FailingLLM()

    _english, chinese = await agent.reextract_visible_characters(script)

    assert chinese["苏眠"] == "苏眠：米白色长风衣。"
    assert agent.character_profile_repair_status == "partial"



def test_character_profile_repair_result_exposes_status_and_legacy_unpacking():
    result = CharacterProfileRepairResult(
        english={"角色A": "stable"}, chinese={"角色A": "稳定"},
        status="partial", errors=["缺少发型"],
    )

    english, chinese = result
    assert result.status == "partial"
    assert english["角色A"] == "stable"
    assert chinese["角色A"] == "稳定"


@pytest.mark.asyncio
async def test_character_reextract_preserves_existing_profile_on_repair_failure():
    script = ScriptBreakdown(
        title="\u4fdd\u7559\u5b8c\u6574\u4fa7\u5199", genre="\u60ac\u7591", logline="\u6d4b\u8bd5", duration_sec=3, narration_style="\u65e0\u65c1\u767d",
        scenes=[_scene(1, "\u82cf\u7720\u7ad9\u5728\u5c4b\u6a90\u4e0b\u3002", ["\u82cf\u7720"])],
        asset_manifest=VisualAssetManifest(characters=[VisualCharacterAsset(
            asset_id="char_su_mian", name="\u82cf\u7720", appearance_mode="on_screen", description="\u82cf\u7720\uff1a\u77ed\u8d44\u4ea7\u951a\u70b9\u3002",
        )]),
    )
    complete = "\u82cf\u7720\uff1a\u6210\u5e74\u5973\u6027\uff0c\u4e8c\u5341\u516d\u5c81\uff0c\u8eab\u6750\u7ea4\u7ec6\uff0c\u5706\u8138\u767d\u7684\uff0c\u9ed1\u8272\u76f4\u53d1\uff0c\u7a7f\u7c73\u767d\u8272\u68c9\u8d28\u98ce\u8863\uff0c\u5de6\u773c\u4e0b\u6709\u5c0f\u75e3\u3002"

    class _RepairFailsLLM:
        def __init__(self): self.calls = 0
        async def generate_structured(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return _CharacterRegistryOutput(global_character_descriptions_cn={"\u82cf\u7720": complete})
            raise RuntimeError("repair failed")

    agent = VisualPromptAgent(api_provider="mock")
    agent.llm = _RepairFailsLLM()
    result = await agent.reextract_visible_characters(script)
    assert result.status == "failed"
    assert result.chinese["\u82cf\u7720"] == complete
    assert any("repair failed" in error for error in result.errors)
