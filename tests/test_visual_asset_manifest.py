"""Regression coverage for normalized visual asset extraction."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from src.agents.image_gen_agent import ImageGenerationAgent
from src.agents.script_agent import ScriptAgent
from src.agents.visual_prompt_agent import VisualPromptAgent
from src.agents.video_gen_agent import VideoGenerationAgent
from src.api_clients.image_mock import MockImageClient
from src.schemas.character import CharacterImage, CharacterImageAssets
from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.scene_image import SceneImage, SceneImageAssets
from src.schemas.script import (
    LocationAsset,
    PropAsset,
    Scene,
    ScriptBreakdown,
    ShotVisualBinding,
    VisualAssetManifest,
    VisualCharacterAsset,
)
from src.schemas.visual import VisualDesign
from src.utils.visual_asset_manifest import (
    build_manifest_from_scenes,
    normalize_scene_assets,
    on_screen_character_names,
    representative_scene_id,
    repair_asset_manifest,
    validate_asset_manifest,
)


def test_compact_manifest_merges_a_revealed_disguise_into_the_real_character():
    script = ScriptBreakdown(
        title="身份揭晓",
        genre="悬疑",
        logline="蒙面追踪者露出真实身份",
        duration_sec=10,
        narration_style="无",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=5,
                location="地下通道",
                characters=["苏眠", "黑衣人"],
                action_description="黑衣人穿黑色雨衣和口罩，沿通道逼近苏眠。",
                camera_direction="中景",
                emotion_tone="紧张",
            ),
            Scene(
                scene_id=2,
                duration_sec=5,
                location="地下室",
                characters=["苏眠", "顾衡", "黑衣人", "陆临"],
                action_description=(
                    "黑衣人摘下口罩，露出一张熟悉的脸：苏远山生前最信任的学生陆临。"
                    "苏眠震惊后退，顾衡挡在她前面。"
                ),
                camera_direction="近景",
                emotion_tone="反转",
            ),
        ],
    )

    manifest = build_manifest_from_scenes(script)
    visible = [item for item in manifest.characters if item.appearance_mode == "on_screen"]
    names = {item.name for item in visible}

    assert "黑衣人" not in names
    assert "陆临" in names
    lu_lin = next(item for item in visible if item.name == "陆临")
    assert "黑衣人" in lu_lin.aliases
    assert lu_lin.asset_id in manifest.shot_bindings[0].on_screen_character_ids


def test_normalize_merges_revealed_disguise_in_an_existing_manifest():
    script = ScriptBreakdown(
        title="旧项目身份揭晓",
        genre="悬疑",
        logline="test",
        duration_sec=6,
        narration_style="无",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=3,
                location="地下通道",
                characters=["黑衣人"],
                action_description="黑衣人沿通道逼近。",
                camera_direction="中景",
                emotion_tone="紧张",
            ),
            Scene(
                scene_id=2,
                duration_sec=3,
                location="地下室",
                characters=["黑衣人", "陆临"],
                action_description="黑衣人摘下口罩，露出一张熟悉的脸：苏远山的学生陆临。",
                camera_direction="近景",
                emotion_tone="反转",
            ),
        ],
        asset_manifest=VisualAssetManifest(
            characters=[
                VisualCharacterAsset(asset_id="char_masked", name="黑衣人", appearance_mode="on_screen"),
                VisualCharacterAsset(asset_id="char_lu", name="陆临", appearance_mode="on_screen"),
            ],
            locations=[
                LocationAsset(location_id="passage", canonical_name="地下通道", representative_scene_id=1, shot_ids=[1]),
                LocationAsset(location_id="basement", canonical_name="地下室", representative_scene_id=2, shot_ids=[2]),
            ],
            shot_bindings=[
                ShotVisualBinding(scene_id=1, location_id="passage", on_screen_character_ids=["char_masked"]),
                ShotVisualBinding(scene_id=2, location_id="basement", on_screen_character_ids=["char_masked", "char_lu"]),
            ],
        ),
    )

    normalize_scene_assets(script)

    assert [item.name for item in script.asset_manifest.characters] == ["陆临"]
    assert script.asset_manifest.characters[0].aliases == ["黑衣人"]
    assert script.asset_manifest.shot_bindings[0].on_screen_character_ids == ["char_lu"]
    assert script.asset_manifest.shot_bindings[1].on_screen_character_ids == ["char_lu"]


@pytest.mark.parametrize(
    "action",
    [
        "黑衣人摘下口罩，原来是陆临。顾衡震惊后退。",
        "顾衡提醒：出口已经封死。黑衣人摘下口罩，露出真容，正是陆临。苏眠后退。",
    ],
)
def test_revealed_disguise_uses_the_reveal_sentence_without_requiring_a_colon(action):
    script = ScriptBreakdown(
        title="不同揭晓句式",
        genre="悬疑",
        logline="身份揭晓",
        duration_sec=6,
        narration_style="无",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=3,
                location="地下通道",
                characters=["黑衣人"],
                action_description="黑衣人沿通道逼近。",
                camera_direction="中景",
                emotion_tone="紧张",
            ),
            Scene(
                scene_id=2,
                duration_sec=3,
                location="地下室",
                characters=["黑衣人", "陆临", "苏眠", "顾衡"],
                action_description=action,
                camera_direction="近景",
                emotion_tone="反转",
            ),
        ],
    )

    manifest = build_manifest_from_scenes(script)

    assert "黑衣人" not in {item.name for item in manifest.characters}
    lu_lin = next(item for item in manifest.characters if item.name == "陆临")
    assert "黑衣人" in lu_lin.aliases


def test_normalize_scene_assets_merges_venue_subareas_but_keeps_inside_outside():
    script = _script_with_manifest()
    script.scenes = [
        Scene(scene_id=1, duration_sec=3, location="花店门口·街角", characters=[], action_description="外部", camera_direction="wide", emotion_tone="calm"),
        Scene(scene_id=2, duration_sec=3, location="花店门口·街景", characters=[], action_description="外部", camera_direction="wide", emotion_tone="calm"),
        Scene(scene_id=3, duration_sec=3, location="花店内部·柜台", characters=[], action_description="内部", camera_direction="wide", emotion_tone="calm"),
        Scene(scene_id=4, duration_sec=3, location="花店内部·门口", characters=[], action_description="内部", camera_direction="wide", emotion_tone="calm"),
    ]
    script.asset_manifest.locations = [
        LocationAsset(location_id=f"old_{scene.scene_id}", canonical_name=scene.location, representative_scene_id=scene.scene_id, shot_ids=[scene.scene_id])
        for scene in script.scenes
    ]
    script.asset_manifest.shot_bindings = [
        ShotVisualBinding(scene_id=scene.scene_id, location_id=f"old_{scene.scene_id}")
        for scene in script.scenes
    ]

    normalize_scene_assets(script)

    assert [item.canonical_name for item in script.asset_manifest.locations] == ["花店外部", "花店内部"]
    assert [item.shot_ids for item in script.asset_manifest.locations] == [[1, 2], [3, 4]]


def _script_with_manifest() -> ScriptBreakdown:
    return ScriptBreakdown(
        title="Manifest test",
        genre="suspense",
        logline="A location reuse test.",
        duration_sec=15,
        narration_style="none",
        scenes=[
            Scene(scene_id=1, duration_sec=5, location="Entrance", characters=["A", "Voice"], action_description="A enters.", camera_direction="wide", emotion_tone="tense"),
            Scene(scene_id=2, duration_sec=5, location="Entrance", characters=["A", "Voice"], action_description="A looks back.", camera_direction="close-up", emotion_tone="tense"),
            Scene(scene_id=3, duration_sec=5, location="Basement", characters=["A"], action_description="A stops.", camera_direction="medium", emotion_tone="dark"),
        ],
        asset_manifest=VisualAssetManifest(
            characters=[
                VisualCharacterAsset(asset_id="a", name="A", appearance_mode="on_screen"),
                VisualCharacterAsset(asset_id="voice", name="Voice", appearance_mode="voice_only"),
            ],
            locations=[
                LocationAsset(location_id="entrance_night", canonical_name="Entrance", time_condition="night", environment_description="wet entrance", representative_scene_id=1, shot_ids=[1, 2]),
                LocationAsset(location_id="basement_night", canonical_name="Basement", time_condition="night", environment_description="concrete basement", representative_scene_id=3, shot_ids=[3]),
            ],
            shot_bindings=[
                ShotVisualBinding(scene_id=1, location_id="entrance_night", on_screen_character_ids=["a"], voice_only_speakers=["Voice"]),
                ShotVisualBinding(scene_id=2, location_id="entrance_night", on_screen_character_ids=["a"], voice_only_speakers=["Voice"]),
                ShotVisualBinding(scene_id=3, location_id="basement_night", on_screen_character_ids=["a"]),
            ],
        ),
    )


def _visual() -> VisualDesign:
    return VisualDesign(
        overall_style="photorealistic suspense",
        color_palette="cool blue",
        global_character_descriptions={"A": "adult with short black hair", "Voice": "must not generate"},
        scenes=[],
    )


def test_manifest_generates_only_visible_characters_and_deduplicated_locations():
    script = _script_with_manifest()
    with TemporaryDirectory() as temp_dir:
        agent = ImageGenerationAgent(api_client=MockImageClient())
        agent.output_dir = Path(temp_dir)
        characters, scenes = asyncio.run(agent.run(script, _visual()))

    assert {item.character_name for item in characters.characters} == {"A"}
    assert len(characters.characters) == 4
    assert {item.scene_id for item in scenes.scenes} == {1, 3}
    assert len(scenes.scenes) == 4


def test_manifest_binds_visible_roles_and_reuses_scene_reference():
    script = _script_with_manifest()
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        entrance = root / "entrance.png"
        basement = root / "basement.png"
        character = root / "character.png"
        for path in (entrance, basement, character):
            path.write_bytes(b"reference")

        scene_images = SceneImageAssets(scenes=[
            SceneImage(scene_id=1, location="Entrance", view_type="wide", image_prompt="", image_path=str(entrance), seed=1),
            SceneImage(scene_id=3, location="Basement", view_type="wide", image_prompt="", image_path=str(basement), seed=3),
        ])
        character_images = CharacterImageAssets(characters=[
            CharacterImage(character_name="A", view_type="front_full", image_prompt="", image_path=str(character), seed=1),
        ])
        prompts = GenerationPrompts(api_provider="mock", shots=[
            ShotPrompt(scene_id=1, prompt="one", negative_prompt="", duration_sec=5),
            ShotPrompt(scene_id=2, prompt="two", negative_prompt="", duration_sec=5),
            ShotPrompt(scene_id=3, prompt="three", negative_prompt="", duration_sec=5),
        ])
        refs = VideoGenerationAgent()._build_reference_bundle(
            prompts, character_images, scene_images, script,
        )

    assert on_screen_character_names(script, 1) == ["A"]
    assert representative_scene_id(script, 2) == 1
    assert refs[1] == [str(entrance), str(character)]
    assert refs[2] == [str(entrance), str(character)]
    assert refs[3] == [str(basement), str(character)]
    assert validate_asset_manifest(script) == []


def test_scene_image_view_failure_is_reported_to_the_caller():
    script = _script_with_manifest()
    scene = script.scenes[0]
    agent = ImageGenerationAgent(api_client=MockImageClient())

    class FakeLLM:
        async def generate_text(self, **_kwargs):
            return "empty location"

    async def failed_scene_view(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    agent.llm = FakeLLM()
    agent._generate_scene_single = failed_scene_view
    with TemporaryDirectory() as temp_dir:
        with pytest.raises(RuntimeError, match="provider unavailable"):
            asyncio.run(agent._generate_scene_images(scene, None, _visual(), Path(temp_dir)))


def test_script_agent_retries_once_when_manifest_is_semantically_invalid():
    valid = _script_with_manifest()
    invalid = valid.model_copy(deep=True)
    invalid.asset_manifest.characters[0].appearance_mode = "voice_only"

    class FakeLLM:
        def __init__(self):
            self.prompts = []

        async def generate_structured(self, **kwargs):
            self.prompts.append(kwargs["user_prompt"])
            return invalid if len(self.prompts) == 1 else valid

    agent = ScriptAgent()
    fake_llm = FakeLLM()
    agent.llm = fake_llm
    script = asyncio.run(agent._generate_validated("test script", temperature=0.1))

    assert script.asset_manifest is not None
    assert len(fake_llm.prompts) == 2
    assert "未通过生产校验" in fake_llm.prompts[1]


def test_script_agent_retries_before_inferring_missing_manifest_locally():
    valid_without_manifest = _script_with_manifest().model_copy(update={"asset_manifest": None})

    class FakeLLM:
        def __init__(self):
            self.prompts = []

        async def generate_structured(self, **kwargs):
            self.prompts.append(kwargs["user_prompt"])
            return valid_without_manifest

    agent = ScriptAgent()
    fake_llm = FakeLLM()
    agent.llm = fake_llm
    script = asyncio.run(agent._generate_validated("test script", temperature=0.1))

    assert script.asset_manifest is not None
    assert len(fake_llm.prompts) == 2
    assert "只补全视觉资产清单" in fake_llm.prompts[1]
    assert validate_asset_manifest(script) == []


def test_visual_character_registry_maps_aliases_and_keeps_all_visible_roles():
    script = _script_with_manifest()
    script.asset_manifest.characters[0].aliases = ["A (Alias)"]

    registry = VisualPromptAgent()._canonical_character_registry(
        {"A (Alias)": "adult in a dark coat"}, script, english=True,
    )

    assert registry["A"] == "adult in a dark coat"
    assert "Voice" not in registry


def test_repair_enriches_generic_character_description_from_visible_script_facts():
    script = ScriptBreakdown(
        title="角色描述补全",
        genre="悬疑",
        logline="测试角色身份",
        duration_sec=6,
        narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=3,
                location="便利店外部",
                characters=["苏眠"],
                action_description="苏眠穿着米白色长风衣，黑色中长发贴在脸侧，左手握住信封。",
                camera_direction="中近景",
                emotion_tone="压迫",
            ),
            Scene(
                scene_id=2,
                duration_sec=3,
                location="便利店外部",
                characters=["苏眠"],
                action_description="苏眠站在便利店屋檐下，米白色长风衣被雨水打湿。",
                camera_direction="近景",
                emotion_tone="紧张",
            ),
        ],
        asset_manifest=VisualAssetManifest(
            characters=[
                VisualCharacterAsset(
                    asset_id="char_su_mian",
                    name="苏眠",
                    appearance_mode="on_screen",
                    description="画面角色，出现在分镜 1, 2",
                )
            ],
            locations=[
                LocationAsset(
                    location_id="loc_shop",
                    canonical_name="便利店外部",
                    representative_scene_id=1,
                    shot_ids=[1, 2],
                )
            ],
            shot_bindings=[
                ShotVisualBinding(
                    scene_id=1,
                    location_id="loc_shop",
                    on_screen_character_ids=["char_su_mian"],
                ),
                ShotVisualBinding(
                    scene_id=2,
                    location_id="loc_shop",
                    on_screen_character_ids=["char_su_mian"],
                ),
            ],
        ),
    )

    repair_asset_manifest(script)

    description = script.asset_manifest.characters[0].description
    assert "米白色长风衣" in description
    assert "黑色中长发" in description
    assert "出现在分镜" not in description


def test_repair_enriches_generic_prop_description_without_action_text():
    script = ScriptBreakdown(
        title="道具描述补全",
        genre="悬疑",
        logline="测试道具外观",
        duration_sec=6,
        narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=3,
                location="便利店外部",
                characters=["苏眠"],
                action_description="苏眠握着一只旧牛皮纸信封，封口有暗红色火漆印章。",
                camera_direction="手部特写",
                emotion_tone="紧张",
            ),
            Scene(
                scene_id=2,
                duration_sec=3,
                location="便利店外部",
                characters=["苏眠"],
                action_description="信封被她藏到身后，旧牛皮纸边角已经发软。",
                camera_direction="近景",
                emotion_tone="压迫",
            ),
        ],
        asset_manifest=VisualAssetManifest(
            locations=[
                LocationAsset(
                    location_id="loc_shop",
                    canonical_name="便利店外部",
                    representative_scene_id=1,
                    shot_ids=[1, 2],
                )
            ],
            props=[
                PropAsset(
                    prop_id="prop_letter",
                    name="信封",
                    description="信封作为跨镜头可见线索道具，外观、位置和持有状态需要保持一致。",
                    critical_reason="plot",
                    critical_evidence="两镜头反复出现并承载线索。",
                    shot_ids=[1, 2],
                )
            ],
            shot_bindings=[
                ShotVisualBinding(scene_id=1, location_id="loc_shop", prop_ids=["prop_letter"]),
                ShotVisualBinding(scene_id=2, location_id="loc_shop", prop_ids=["prop_letter"]),
            ],
        ),
    )

    repair_asset_manifest(script)

    description = script.asset_manifest.props[0].description
    assert "旧牛皮纸" in description
    assert "暗红色火漆印章" in description
    assert "作为跨镜头" not in description
    assert "藏到身后" not in description


def test_repair_does_not_cross_contaminate_prop_facts_from_other_props():
    script = ScriptBreakdown(
        title="道具事实隔离",
        genre="悬疑",
        logline="测试道具描述不能串镜头",
        duration_sec=6,
        narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=3,
                location="便利店外部",
                characters=["苏眠"],
                action_description="苏眠握着旧牛皮纸信封，封口有暗红色火漆印章，另一手查看手机，屏幕亮着一条短信提示。",
                camera_direction="手部特写",
                emotion_tone="紧张",
            ),
            Scene(
                scene_id=2,
                duration_sec=3,
                location="便利店外部",
                characters=["苏眠"],
                action_description="苏眠把信封和手机都放在桌面上。",
                camera_direction="手机特写",
                emotion_tone="紧张",
            ),
        ],
        asset_manifest=VisualAssetManifest(
            locations=[
                LocationAsset(
                    location_id="loc_shop",
                    canonical_name="便利店外部",
                    representative_scene_id=1,
                    shot_ids=[1, 2],
                )
            ],
            props=[
                PropAsset(
                    prop_id="prop_letter",
                    name="信封",
                    description="信封作为跨镜头可见线索道具，外观、位置和持有状态需要保持一致。",
                    critical_reason="plot",
                    critical_evidence="镜头1出现线索。",
                    shot_ids=[1, 2],
                ),
                PropAsset(
                    prop_id="prop_phone",
                    name="手机",
                    description="手机作为跨镜头可见线索道具，外观、位置和持有状态需要保持一致。",
                    critical_reason="continuity",
                    critical_evidence="镜头2出现设备。",
                    shot_ids=[1, 2],
                ),
            ],
            shot_bindings=[
                ShotVisualBinding(scene_id=1, location_id="loc_shop", prop_ids=["prop_letter", "prop_phone"]),
                ShotVisualBinding(scene_id=2, location_id="loc_shop", prop_ids=["prop_letter", "prop_phone"]),
            ],
        ),
    )

    repair_asset_manifest(script)
    # A second load/repair must remain idempotent and must not preserve a
    # previously contaminated generated description.
    repair_asset_manifest(script)

    descriptions = {item.name: item.description for item in script.asset_manifest.props}
    assert "旧牛皮纸" in descriptions["信封"]
    assert "火漆印章" in descriptions["信封"]
    assert "屏幕亮着" not in descriptions["信封"]
    assert "屏幕亮着" in descriptions["手机"]
    assert "火漆印章" not in descriptions["手机"]


def test_repair_uses_removed_physical_props_as_fact_boundaries():
    script = ScriptBreakdown(
        title="已删除道具仍隔离事实",
        genre="悬疑",
        logline="关键道具描述不能吸收同句中的非保留物品",
        duration_sec=6,
        narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=3,
                location="旧钟楼地下室",
                characters=["苏眠"],
                action_description="苏眠用生锈铜钥匙打开铁盒，里面是一叠档案、一支录音笔和一张新照片。",
                camera_direction="中景",
                emotion_tone="紧张",
            ),
            Scene(
                scene_id=2,
                duration_sec=3,
                location="旧钟楼地下室",
                characters=["苏眠"],
                action_description="苏眠握紧那枚生锈的铜钥匙，跑向暗道。",
                camera_direction="近景",
                emotion_tone="紧张",
            ),
        ],
        asset_manifest=VisualAssetManifest(
            locations=[
                LocationAsset(
                    location_id="loc_basement",
                    canonical_name="旧钟楼地下室",
                    representative_scene_id=1,
                    shot_ids=[1, 2],
                )
            ],
            props=[
                PropAsset(
                    prop_id="prop_key",
                    name="钥匙",
                    description="钥匙作为跨镜头可见线索道具，外观、位置和持有状态需要保持一致。",
                    critical_reason="plot",
                    critical_evidence="两个镜头反复出现并开启关键机关。",
                    shot_ids=[1, 2],
                )
            ],
            shot_bindings=[
                ShotVisualBinding(scene_id=1, location_id="loc_basement", prop_ids=["prop_key"]),
                ShotVisualBinding(scene_id=2, location_id="loc_basement", prop_ids=["prop_key"]),
            ],
        ),
    )

    repair_asset_manifest(script)

    description = script.asset_manifest.props[0].description
    assert "铜钥匙" in description
    assert "档案" not in description
    assert "录音笔" not in description
    assert "照片" not in description


def test_repair_rebuilds_previous_generated_prop_description_after_contamination():
    script = ScriptBreakdown(
        title="道具描述迁移",
        genre="悬疑",
        logline="修复历史生成结果",
        duration_sec=3,
        narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=3,
                location="便利店外部",
                characters=[],
                action_description="桌面上放着旧牛皮纸信封，封口有暗红色火漆印章。",
                camera_direction="特写",
                emotion_tone="紧张",
            )
        ],
        asset_manifest=VisualAssetManifest(
            locations=[
                LocationAsset(
                    location_id="loc_shop",
                    canonical_name="便利店外部",
                    representative_scene_id=1,
                    shot_ids=[1],
                )
            ],
            props=[
                PropAsset(
                    prop_id="prop_letter",
                    name="信封",
                    description="信封：暗红色火漆印章；旧牛皮纸；屏幕亮着；生锈的铜钥匙。",
                    critical_reason="plot",
                    critical_evidence="镜头1出现线索。",
                    shot_ids=[1],
                )
            ],
            shot_bindings=[
                ShotVisualBinding(scene_id=1, location_id="loc_shop", prop_ids=["prop_letter"]),
            ],
        ),
    )

    repair_asset_manifest(script)

    description = script.asset_manifest.props[0].description
    assert "旧牛皮纸" in description
    assert "暗红色火漆印章" in description
    assert "屏幕亮着" not in description
    assert "生锈的铜钥匙" not in description


def test_repair_replaces_dynamic_location_placeholder_with_static_set_facts():
    script = ScriptBreakdown(
        title="场景描述清洗",
        genre="悬疑",
        logline="测试场景身份",
        duration_sec=6,
        narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=3,
                location="地下通道",
                characters=["苏眠"],
                action_description="通道两侧灯管大多损坏，墙上有大片水痕，地面积着浅水。苏眠握紧信封，远处传来脚步声。",
                camera_direction="大全景",
                emotion_tone="阴森",
            ),
            Scene(
                scene_id=2,
                duration_sec=3,
                location="地下通道",
                characters=["苏眠"],
                action_description="苏眠走向墙边电箱准备切断线路，录音里的声音在通道中响起。",
                camera_direction="跟拍",
                emotion_tone="紧张",
            ),
        ],
        asset_manifest=VisualAssetManifest(
            locations=[
                LocationAsset(
                    location_id="loc_tunnel",
                    canonical_name="地下通道",
                    identity_description="物理地点身份：地下通道；可复用固定空间。",
                    fixed_dressing="通道两侧灯管大多损坏；墙上有大片水痕；地面积着浅水；生怕被雨水浸透；录音里的声音在通道中响起。",
                    environment_description="通道两侧灯管大多损坏；墙上有大片水痕；地面积着浅水；生怕被雨水浸透；录音里的声音在通道中响起。",
                    representative_scene_id=1,
                    shot_ids=[1, 2],
                )
            ],
            shot_bindings=[
                ShotVisualBinding(scene_id=1, location_id="loc_tunnel"),
                ShotVisualBinding(scene_id=2, location_id="loc_tunnel"),
            ],
        ),
    )

    repair_asset_manifest(script)

    location = script.asset_manifest.locations[0]
    assert "灯管大多损坏" in location.environment_description
    assert "大片水痕" in location.environment_description
    assert "浅水" in location.environment_description
    assert "生怕被雨水浸透" not in location.environment_description
    assert "录音里的声音" not in location.environment_description
    assert "走向墙边电箱" not in location.environment_description


def test_repair_preserves_a_detailed_physical_location_identity():
    script = ScriptBreakdown(
        title="展馆身份",
        genre="悬疑",
        logline="测试详细地点身份不被模板覆盖",
        duration_sec=5,
        narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=5,
                location="旧展馆地下室",
                characters=[],
                action_description="地下室中央是圆形展台，北墙嵌着拱形铁门。",
                camera_direction="大全景",
                emotion_tone="压迫",
            )
        ],
        asset_manifest=VisualAssetManifest(
            locations=[
                LocationAsset(
                    location_id="loc_gallery",
                    canonical_name="旧展馆地下室",
                    identity_description=(
                        "物理地点身份：旧展馆地下室；圆形展台位于空间中心，"
                        "北墙拱形铁门后露出向下石阶，保持房间比例和中心轴。"
                    ),
                    environment_description="中央圆形展台；北墙拱形铁门。",
                    fixed_dressing="中央圆形展台；北墙拱形铁门。",
                    anchor_points=["中央圆形展台", "北墙拱形铁门"],
                    representative_scene_id=1,
                    shot_ids=[1],
                )
            ],
            shot_bindings=[ShotVisualBinding(scene_id=1, location_id="loc_gallery")],
        ),
    )

    repair_asset_manifest(script)

    identity = script.asset_manifest.locations[0].identity_description
    assert "圆形展台位于空间中心" in identity
    assert "北墙拱形铁门后露出向下石阶" in identity


def test_manifest_recovers_static_fixtures_next_to_a_plot_prop():
    script = ScriptBreakdown(
        title="仓库陈设",
        genre="悬疑",
        logline="测试固定陈设与剧情道具分离",
        duration_sec=6,
        narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=3,
                location="废弃仓库内部",
                characters=[],
                action_description="中央长桌上摆着老式台灯和黑色录音机，四周堆着旧木箱。",
                camera_direction="大全景",
                emotion_tone="阴冷",
            ),
            Scene(
                scene_id=2,
                duration_sec=3,
                location="废弃仓库内部",
                characters=["调查员"],
                action_description="调查员看向仓库中央的巨大机械钟，随后沿石阶往下走，又看了看暗道并拿起录音机。",
                camera_direction="中景",
                emotion_tone="紧张",
            ),
        ],
    )

    location = build_manifest_from_scenes(script).locations[0]

    assert "中央长桌" in location.environment_description
    assert "老式台灯" in location.environment_description
    assert "巨大机械钟" in location.environment_description
    assert "录音机" not in location.environment_description
    assert "沿石阶往下走" not in location.environment_description
    assert "看了看暗道" not in location.environment_description


def test_repair_removes_historical_action_with_character_from_another_location():
    script = ScriptBreakdown(
        title="剧院调查",
        genre="都市悬疑",
        logline="测试跨地点历史动作污染清理",
        duration_sec=8,
        narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=4,
                location="旧剧院地下入口外部",
                characters=["摄影师"],
                action_description="摄影师停在旧剧院门前观察入口。",
                camera_direction="大全景",
                emotion_tone="警惕",
            ),
            Scene(
                scene_id=2,
                duration_sec=4,
                location="剧院后台",
                characters=["策展人"],
                action_description="策展人整理后台档案。",
                camera_direction="中景",
                emotion_tone="平静",
            ),
        ],
        asset_manifest=VisualAssetManifest(
            locations=[
                LocationAsset(
                    location_id="loc_theater_entrance",
                    canonical_name="旧剧院地下入口外部",
                    identity_description="室外旧砖墙中的拱形铁门入口。",
                    environment_description="旧砖墙；拱形铁门；策展人走过街巷",
                    fixed_dressing="旧砖墙；拱形铁门；策展人走过街巷",
                    anchor_points=["旧砖墙", "拱形铁门", "策展人走过街巷"],
                    representative_scene_id=1,
                    shot_ids=[1],
                ),
                LocationAsset(
                    location_id="loc_backstage",
                    canonical_name="剧院后台",
                    environment_description="旧木柜和灰色幕布。",
                    representative_scene_id=2,
                    shot_ids=[2],
                ),
            ],
            shot_bindings=[
                ShotVisualBinding(scene_id=1, location_id="loc_theater_entrance"),
                ShotVisualBinding(scene_id=2, location_id="loc_backstage"),
            ],
        ),
    )

    repair_asset_manifest(script)

    location = next(
        item for item in script.asset_manifest.locations
        if item.representative_scene_id == 1
    )
    assert "旧砖墙" in location.environment_description
    assert "拱形铁门" in location.environment_description
    assert "策展人" not in location.environment_description
    assert all("策展人" not in anchor for anchor in location.anchor_points)


def test_manifest_does_not_recover_multi_character_travel_as_set_dressing():
    script = ScriptBreakdown(
        title="雨夜带路",
        genre="都市悬疑",
        logline="测试多人移动动作不进入地点身份",
        duration_sec=4,
        narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=4,
                location="老电影院后巷入口",
                characters=["向导", "游客"],
                action_description=(
                    "向导带着游客拐过两条巷子，来到旧电影院的后门。"
                    "后门嵌在斑驳砖墙内，旁边固定着一盏老式壁灯。"
                ),
                camera_direction="大全景",
                emotion_tone="紧张",
            )
        ],
    )

    location = build_manifest_from_scenes(script).locations[0]

    assert "斑驳砖墙" in location.environment_description
    assert "游客" not in location.environment_description
    assert "拐过" not in location.environment_description


def test_manifest_does_not_turn_indoor_water_mentions_into_direct_rain():
    script = ScriptBreakdown(
        title="室内天气",
        genre="悬疑",
        logline="测试室内地点状态",
        duration_sec=6,
        narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=3,
                location="封闭地下通道内部",
                characters=[],
                action_description="墙面持续渗水，地面积水反光，旧信封边缘被雨水浸透。",
                camera_direction="大全景",
                emotion_tone="阴冷",
            ),
            Scene(
                scene_id=2,
                duration_sec=3,
                location="封闭地下通道内部",
                characters=[],
                action_description="门外暴雨声持续，室内天花板完整，没有直接落雨。",
                camera_direction="中景",
                emotion_tone="紧张",
            ),
        ],
    )

    location = build_manifest_from_scenes(script).locations[0]

    assert location.states[0].weather == "室内，无直接降雨；外部暴雨"
