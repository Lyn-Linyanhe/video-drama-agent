import asyncio

import pytest

from fastapi import HTTPException

from src.api import context as api_context
from src.api.context import RegenerateImageRequest
from src.services import asset_generation
from src.services.asset_prompt_composer import _scene_scale_lock
from src.services.asset_prompt_composer import scene_environment_key_elements
from src.services.asset_prompt_composer import build_scene_prompt_layers
from src.services.asset_prompt_composer import compose_asset_prompt
from src.graph.state import DramaState
from src.schemas.script import Scene, ScriptBreakdown
from src.schemas.script import DialogueLine
from src.schemas.visual import VisualDesign, SceneVisual
from src.schemas.scene_image import SceneImage
from src.utils.visual_asset_manifest import stable_location_id
from src.agents.image_gen_agent import ImageGenerationAgent
from src.services.timed_prompt_composer import build_timed_shot_prompt
from src.services.timed_prompt_composer import _prop_label, _prop_names, _prop_phrase
from src.agents.visual_prompt_agent import _estimated_content_duration
from src.schemas.prompt import ShotPrompt
from src.utils.visual_asset_manifest import build_manifest_from_scenes
from src.utils.visual_asset_manifest import validate_and_clean_props
from tests.test_wan_character_references import FakeWanClient, _enable_wan, _prepare_character_project, _prepare_scene_project


def test_ambiguous_venue_name_uses_neutral_space_scope():
    scope = _scene_scale_lock("便利店", "", "", "")

    assert "临街建筑外立面" not in scope
    assert "可复用空间边界" in scope


def test_legacy_image_seed_isolated_by_project(tmp_path):
    """The compatibility generator must not reuse a seed across projects."""
    from src.api_clients.image_base import ImageGenResult
    from PIL import Image

    class CaptureImageClient:
        provider_name = "mock"

        def __init__(self):
            self.calls = []
            self.index = 0

        async def generate_image(self, **kwargs):
            self.calls.append(kwargs)
            self.index += 1
            path = tmp_path / f"seed-{self.index}.png"
            Image.new("RGB", (kwargs["width"], kwargs["height"]), "white").save(path)
            return ImageGenResult(True, local_path=str(path), width=kwargs["width"], height=kwargs["height"], provider_name=self.provider_name)

    script = ScriptBreakdown(
        title="seed isolation",
        genre="drama",
        logline="one character",
        duration_sec=3,
        narration_style="none",
        scenes=[Scene(
            scene_id=1,
            duration_sec=3,
            location="neutral room",
            characters=["A"],
            action_description="A stands still",
            camera_direction="center",
            emotion_tone="calm",
        )],
    )
    visual = VisualDesign(
        overall_style="cinematic live action",
        color_palette="neutral",
        global_character_descriptions={"A": "adult person"},
        scenes=[],
    )
    client = CaptureImageClient()
    agent = ImageGenerationAgent(api_client=client)
    agent.output_dir = tmp_path
    asyncio.run(agent.run(script, visual, project_id="project-a", include_scenes=False))
    first_seeds = [call["seed"] for call in client.calls]
    client.calls.clear()
    asyncio.run(agent.run(script, visual, project_id="project-b", include_scenes=False))
    second_seeds = [call["seed"] for call in client.calls]

    assert first_seeds
    assert first_seeds != second_seeds


def test_legacy_scene_quality_validation_receives_expected_dimensions(tmp_path, monkeypatch):
    """The old scene entrypoint must pass its requested 16:9 dimensions to review."""
    from tests.test_image_gen_agent_four_views import _CaptureSceneImageClient

    script = ScriptBreakdown(
        title="dimension contract",
        genre="drama",
        logline="scene",
        duration_sec=3,
        narration_style="none",
        scenes=[Scene(
            scene_id=1,
            duration_sec=3,
            location="room",
            characters=[],
            action_description="empty room",
            camera_direction="center",
            emotion_tone="calm",
        )],
    )
    visual = VisualDesign(overall_style="cinematic live action", color_palette="neutral", scenes=[])
    client = _CaptureSceneImageClient(tmp_path)
    agent = ImageGenerationAgent(api_client=client)
    agent.output_dir = tmp_path
    seen = []

    async def capture(_image, _prompt, _asset_type, _view_type, _provider, expected_width=0, expected_height=0):
        seen.append((expected_width, expected_height))
        return {"status": "unreviewed", "automatic_reference_allowed": True}

    monkeypatch.setattr("src.services.asset_generation._validate_generated_image", capture)
    asyncio.run(agent.run(script, visual, project_id="dimensions", include_characters=False, include_scenes=True))

    assert seen and all(item == (1920, 1080) for item in seen)


def test_scene_scale_lock_uses_english_location_and_state_cues():
    outdoor = _scene_scale_lock("street corner", "", "", "outdoor street at night")
    indoor = _scene_scale_lock("hospital", "", "", "indoor emergency room")
    underground = _scene_scale_lock("underground entrance", "", "", "outdoor entrance")

    assert "临街建筑外立面" in outdoor
    assert "完整房间轮廓" in indoor
    assert "街巷入口" in underground


def test_scene_scale_lock_ignores_negated_spatial_cues():
    """A negative example must not change the physical interior/exterior scope."""
    outdoor = _scene_scale_lock(
        "城市街角",
        "室外街道转角，旧砖墙和路灯",
        "潮湿路面",
        "场景状态：夜晚；不得生成室内房间或办公室",
    )
    indoor = _scene_scale_lock(
        "医院急诊室",
        "室内急诊室，候诊区和观察床；不要出现街道外立面",
        "白色墙体和玻璃隔断",
        "场景状态：深夜",
    )

    assert "临街建筑外立面" in outdoor
    assert "完整房间轮廓" in indoor
    assert "临街建筑外立面" not in indoor


def test_scene_environment_keys_do_not_drop_short_name_inside_static_word():
    """A one-character role named 林 must not erase the static 林荫道 anchor."""
    script = ScriptBreakdown(
        title="short-name environment",
        genre="drama",
        logline="A character waits on a tree-lined road.",
        duration_sec=3,
        narration_style="none",
        scenes=[Scene(
            scene_id=1,
            duration_sec=3,
            location="城市街角",
            characters=["林"],
            action_description="林站在林荫道边等待。",
            camera_direction="wide",
            emotion_tone="calm",
        )],
    )
    script.asset_manifest = build_manifest_from_scenes(script)
    visual = SceneVisual(
        scene_id=1,
        composition="wide",
        lighting="soft",
        color_tone="neutral",
        key_elements=["林荫道两侧的路灯", "林站在路边"],
    )

    assert scene_environment_key_elements(visual, script, 1) == ["林荫道两侧的路灯"]


def test_scene_environment_keys_use_word_boundaries_for_ascii_short_names():
    script = ScriptBreakdown(
        title="ascii short-name environment",
        genre="drama",
        logline="A waits in an alley.",
        duration_sec=3,
        narration_style="none",
        scenes=[Scene(
            scene_id=1,
            duration_sec=3,
            location="Alley",
            characters=["A"],
            action_description="A waits beside the alley lights.",
            camera_direction="wide",
            emotion_tone="calm",
        )],
    )
    script.asset_manifest = build_manifest_from_scenes(script)
    visual = SceneVisual(
        scene_id=1,
        composition="wide",
        lighting="soft",
        color_tone="neutral",
        key_elements=["Alley lights", "A waits beside the wall"],
    )

    assert scene_environment_key_elements(visual, script, 1) == ["Alley lights"]


def test_legacy_medium_view_does_not_mix_wide_and_medium_contracts():
    script = ScriptBreakdown(
        title="view contract",
        genre="drama",
        logline="scene",
        duration_sec=3,
        narration_style="none",
        scenes=[Scene(
            scene_id=1,
            duration_sec=3,
            location="generic room",
            characters=[],
            action_description="empty room",
            camera_direction="center",
            emotion_tone="calm",
        )],
    )
    state = DramaState(project_id="view-contract", script=script)
    asset = SceneImage(scene_id=1, location_id=stable_location_id("generic room"), location="generic room", view_type="medium", image_prompt="", image_path="", seed=0)

    wide = compose_asset_prompt(state, "scene", asset.location_id, "wide", asset)["effective_prompt"]
    medium = compose_asset_prompt(state, "scene", asset.location_id, "medium", asset)["effective_prompt"]

    assert "大全景建立镜头" in wide
    assert "中景细节镜头" in medium
    assert not ("大全景建立镜头" in medium and "中景细节镜头" in medium)


def test_legacy_image_generation_records_quality_report_for_generated_scene(tmp_path):
    """The compatibility stage must not bypass the shared quality contract."""
    from tests.test_image_gen_agent_four_views import _CaptureSceneImageClient

    script = ScriptBreakdown(
        title="兼容入口质量审核",
        genre="都市",
        logline="测试旧图片入口的质量报告。",
        duration_sec=3,
        narration_style="无旁白",
        scenes=[Scene(
            scene_id=1,
            duration_sec=3,
            location="未命名室内空间",
            characters=[],
            action_description="空场景",
            camera_direction="正面",
            emotion_tone="平静",
        )],
    )
    client = _CaptureSceneImageClient(tmp_path)
    agent = ImageGenerationAgent(api_client=client)
    agent.output_dir = tmp_path

    _characters, scenes = __import__("asyncio").run(
        agent.run(
            script,
            VisualDesign(overall_style="现代电影级二维赛璐璐", color_palette="冷蓝灰", scenes=[]),
            include_characters=False,
            include_scenes=True,
        )
    )

    assert scenes.scenes
    assert all(item.quality_report.get("status") == "unreviewed" for item in scenes.scenes)
    assert all(item.quality_report.get("automatic_reference_allowed") is True for item in scenes.scenes)


def test_timed_prompt_never_invents_entities_from_another_story():
    script = ScriptBreakdown(
        title="图书馆密室",
        genre="悬疑",
        logline="林岚与周谨在旧图书馆追查失踪线索。",
        duration_sec=8,
        narration_style="无旁白",
        scenes=[Scene(
            scene_id=1,
            duration_sec=8,
            location="旧图书馆地下阅览室",
            characters=["林岚", "周谨", "黑衣人"],
            dialogue=[
                DialogueLine(character="林岚", line="你到底是谁？", emotion="震惊"),
                DialogueLine(character="周谨", line="别靠近她。", emotion="警惕"),
            ],
            action_description="黑衣人摘下口罩露出真实身份，周谨冲上前与黑衣人扭打，林岚拿起录音笔后退。",
            camera_direction="中近景手持跟拍",
            emotion_tone="紧张",
        )],
    )
    rendered = build_timed_shot_prompt(
        ShotPrompt(scene_id=1, prompt="黑衣人摘下口罩露出真实身份，周谨与黑衣人扭打。", duration_sec=8),
        script,
    )

    for forbidden in ("苏眠", "顾衡", "陆临", "第七封信", "便利店", "铜钟", "暗道"):
        assert forbidden not in rendered
    assert "林岚" in rendered and "周谨" in rendered and "黑衣人" in rendered
    assert "扭打" in rendered


def test_timed_prompt_repairs_use_current_story_entities_and_props():
    script = ScriptBreakdown(
        title="港口档案室",
        genre="悬疑",
        logline="Mira与Noah在档案室短暂对峙。",
        duration_sec=10,
        narration_style="none",
        scenes=[Scene(
            scene_id=1,
            duration_sec=10,
            location="港口档案室",
            characters=["Mira", "Noah"],
            action_description="Noah推开档案室玻璃门走出，Mira把蓝色档案盒藏到身后，两人对视。",
            camera_direction="中景双人/侧面固定",
            emotion_tone="紧张",
        )],
    )
    rendered = build_timed_shot_prompt(
        ShotPrompt(scene_id=1, prompt="Noah推门而出，Mira藏起蓝色档案盒，两人对视。", duration_sec=10),
        script,
    )

    assert "Mira" in rendered
    assert "Noah" in rendered
    assert "档案室" in rendered
    assert "蓝色档案盒" in rendered
    for forbidden in ("苏眠", "顾衡", "陆临", "便利店", "铜钟", "暗道", "第七封信"):
        assert forbidden not in rendered


def test_generic_repeated_item_with_llm_critical_label_is_removed_but_story_clue_survives():
    """Critical props need narrative evidence, not only a model-supplied label."""
    from src.schemas.script import PropAsset, ShotVisualBinding, VisualAssetManifest

    script = ScriptBreakdown(
        title="generic prop filtering",
        genre="drama",
        logline="A clue is recovered.",
        duration_sec=9,
        narration_style="none",
        scenes=[
            Scene(scene_id=1, duration_sec=3, location="office", characters=["Mira"], action_description="Mira places a coffee mug on the desk.", camera_direction="wide", emotion_tone="calm"),
            Scene(scene_id=2, duration_sec=3, location="office", characters=["Mira"], action_description="Mira picks up the coffee mug and notices an old key beside it.", camera_direction="medium", emotion_tone="alert"),
            Scene(scene_id=3, duration_sec=3, location="basement", characters=["Mira"], action_description="Mira uses the old key to open the basement door.", camera_direction="close", emotion_tone="tense"),
        ],
    )
    script.asset_manifest = VisualAssetManifest(
        characters=[], locations=[],
        props=[
            PropAsset(
                prop_id="prop_coffee_mug", name="咖啡杯", description="关键道具：白色陶瓷杯，普通圆柱形。",
                critical_reason="plot", critical_evidence="分镜 1、2 均出现，保持外观一致。", shot_ids=[1, 2],
            ),
            PropAsset(
                prop_id="prop_old_key", name="旧钥匙", description="生锈黄铜钥匙，齿痕独特。",
                critical_reason="plot", critical_evidence="分镜 2 发现并在后续用于开启地下室暗门，是揭示线索。", shot_ids=[2, 3],
            ),
        ],
        shot_bindings=[
            ShotVisualBinding(scene_id=1, location_id="loc_office", prop_ids=["prop_coffee_mug"]),
            ShotVisualBinding(scene_id=2, location_id="loc_office", prop_ids=["prop_coffee_mug", "prop_old_key"]),
            ShotVisualBinding(scene_id=3, location_id="loc_basement", prop_ids=["prop_old_key"]),
        ],
    )

    result = validate_and_clean_props(script)

    assert [item["prop_id"] for item in result.removed_props] == ["prop_coffee_mug"]
    assert [prop.prop_id for prop in script.asset_manifest.props] == ["prop_old_key"]


def test_compact_manifest_accepts_arbitrary_llm_prop_candidate():
    """Compact providers can keep a novel prop without a hard-coded noun list."""
    script = ScriptBreakdown(
        title="novel prop",
        genre="mystery",
        logline="A marked watch identifies the missing witness.",
        duration_sec=6,
        narration_style="none",
        scenes=[
            Scene(scene_id=1, duration_sec=3, location="station", characters=["Mira"], action_description="Mira finds a red enamel pocket watch beside the bench.", camera_direction="close", emotion_tone="alert"),
            Scene(scene_id=2, duration_sec=3, location="station", characters=["Mira"], action_description="Mira compares the same red enamel pocket watch with the witness photo.", camera_direction="medium", emotion_tone="tense"),
        ],
    )

    manifest = build_manifest_from_scenes(script, critical_props=[{
        "name": "红珐琅怀表",
        "aliases": ["red enamel pocket watch"],
        "description": "红色珐琅外壳，银色指针，表盖有独特划痕。",
        "critical_reason": "identity",
        "critical_evidence": "两个镜头持续出现，是确认失踪证人身份的标志物。",
        "shot_ids": [1, 2],
    }])

    assert [prop.name for prop in manifest.props] == ["红珐琅怀表"]
    assert manifest.props[0].shot_ids == [1, 2]


def test_legacy_scene_prompt_resolves_stable_location_id_to_representative_scene():
    script = ScriptBreakdown(
        title="Fallback",
        genre="drama",
        logline="fallback",
        duration_sec=3,
        narration_style="none",
        scenes=[Scene(
            scene_id=1,
            duration_sec=3,
            location="地下室",
            characters=[],
            action_description="空场景",
            camera_direction="正面",
            emotion_tone="紧张",
        )],
    )
    state = DramaState(project_id="p", script=script)

    layers = build_scene_prompt_layers(state, stable_location_id("地下室"), view_type="scene_center")

    assert "地下室" in layers["identity"]
    assert "missing" not in layers["identity"].casefold()


def test_character_group_rejects_results_when_master_changes_during_generation(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id = _prepare_character_project(tmp_path, monkeypatch)
    fake = FakeWanClient(tmp_path)

    class MutatingClient(FakeWanClient):
        async def generate_sequence(self, prompt, reference_paths, n, resolution):
            result = await super().generate_sequence(prompt, reference_paths, n, resolution)
            state = api_context._load_project_state(project_id)
            master = next(item for item in state.character_images.characters if item.character_name == "hero" and item.view_type == "front_half")
            master.version_id = "new-master-version"
            api_context._save_project_state(state)
            return result

    mutating = MutatingClient(tmp_path)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: mutating)
    asyncio.run(asset_generation.regenerate_image_asset(
        project_id,
        RegenerateImageRequest(asset_type="character", asset_id="hero", view_type="front_half", provider="wan", model="wan2.7-image-pro", resolution="2K"),
    ))

    with pytest.raises(HTTPException, match="master changed"):
        asyncio.run(asset_generation.generate_character_reference_group(project_id, "hero"))


def test_scene_group_rejects_results_when_master_changes_during_generation(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id, location_id = _prepare_scene_project(tmp_path, monkeypatch)
    from src.services.scene_view_requirements import set_scene_view_requirements

    state = api_context._load_project_state(project_id)
    set_scene_view_requirements(state, location_id, "", ["scene_reverse"])
    api_context._save_project_state(state)
    fake = FakeWanClient(tmp_path, count=1)

    class MutatingClient(FakeWanClient):
        async def generate_sequence(self, prompt, reference_paths, n, resolution):
            result = await super().generate_sequence(prompt, reference_paths, n, resolution)
            state = api_context._load_project_state(project_id)
            master = next(item for item in state.scene_images.scenes if item.location_id == location_id and item.view_type == "scene_center")
            master.version_id = "new-scene-master-version"
            api_context._save_project_state(state)
            return result

    mutating = MutatingClient(tmp_path, count=1)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: mutating)
    master = asyncio.run(asset_generation.generate_scene_master(project_id, location_id))
    asyncio.run(asset_generation.confirm_scene_master(project_id, location_id, master["version_id"]))

    with pytest.raises(HTTPException, match="master changed"):
        asyncio.run(asset_generation.generate_scene_reference_group(project_id, location_id))


def test_character_group_validation_receives_view_specific_contracts(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id = _prepare_character_project(tmp_path, monkeypatch)
    fake = FakeWanClient(tmp_path)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    asyncio.run(asset_generation.regenerate_image_asset(
        project_id,
        RegenerateImageRequest(asset_type="character", asset_id="hero", view_type="front_half", provider="wan", model="wan2.7-image-pro", resolution="2K"),
    ))

    seen = []

    async def validate(_image, prompt, _asset_type, view_type, *_args):
        seen.append((view_type, prompt))
        return {"status": "passed", "passed": True}

    monkeypatch.setattr(asset_generation, "_validate_generated_image", validate)
    asyncio.run(asset_generation.generate_character_reference_group(project_id, "hero"))

    assert len(seen) == 4
    assert len({prompt for _view, prompt in seen}) == 4
    assert all(f"VIEW-SPECIFIC CONTRACT: {view}" in prompt for view, prompt in seen)


def test_scene_group_validation_receives_view_specific_contracts(tmp_path, monkeypatch):
    _enable_wan(monkeypatch)
    _, project_id, location_id = _prepare_scene_project(tmp_path, monkeypatch)
    from src.services.scene_view_requirements import set_scene_view_requirements

    state = api_context._load_project_state(project_id)
    set_scene_view_requirements(state, location_id, "", ["scene_left_45", "scene_right_45"])
    api_context._save_project_state(state)
    fake = FakeWanClient(tmp_path, count=2)
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    master = asyncio.run(asset_generation.generate_scene_master(project_id, location_id))
    asyncio.run(asset_generation.confirm_scene_master(project_id, location_id, master["version_id"]))

    seen = []

    async def validate(_image, prompt, _asset_type, view_type, *_args):
        seen.append((view_type, prompt))
        return {"status": "passed", "passed": True}

    monkeypatch.setattr(asset_generation, "_validate_generated_image", validate)
    asyncio.run(asset_generation.generate_scene_reference_group(project_id, location_id))

    assert len(seen) == 2
    assert len({prompt for _view, prompt in seen}) == 2
    assert all(f"VIEW-SPECIFIC CONTRACT: {view}" in prompt for view, prompt in seen)


def test_generic_letter_keeps_its_declared_material_only():
    """A bare letter must not inherit the previous drama's kraft-paper detail."""
    assert _prop_label("信封") == "信封"
    assert _prop_phrase("角色拿起信封") == "信封"
    assert _prop_phrase("角色拿起旧牛皮纸信封") == "旧牛皮纸信封"
    assert "三封旧信" in _prop_names(None, None, "角色翻看三封旧信")


def test_flashback_and_duration_rules_accept_arbitrary_story_terms():
    """Generic numbered letters and non-specific revelations remain supported."""
    script = ScriptBreakdown(
        title="Archive corridor",
        genre="mystery",
        logline="Mira examines a sealed letter while a recorder plays.",
        duration_sec=8,
        narration_style="none",
        scenes=[Scene(
            scene_id=1,
            duration_sec=8,
            location="archive corridor",
            characters=["Mira"],
        action_description="Mira想起三封旧信并听见录音，信封保持关闭。",
            camera_direction="close-up",
            emotion_tone="tense",
        )],
    )
    rendered = build_timed_shot_prompt(
        ShotPrompt(scene_id=1, prompt="Mira想起三封旧信。", duration_sec=8),
        script,
    )
    assert "三封旧信摊开在桌面" in rendered
    assert "牛皮纸" not in rendered
    assert "第七封信" not in rendered

    duration = _estimated_content_duration(
        script.scenes[0],
        ShotPrompt(scene_id=1, prompt="", duration_sec=8),
    )
    assert duration >= 3


def test_generic_subject_state_does_not_hide_canonical_action_for_new_topics():
    """A compact visual response must not erase the scene's concrete action."""
    script = ScriptBreakdown(
        title="Laboratory handoff",
        genre="procedural",
        logline="A researcher checks a sample.",
        duration_sec=8,
        narration_style="none",
        scenes=[Scene(
            scene_id=1,
            duration_sec=8,
            location="laboratory",
            characters=["Chen"],
            action_description="Chen places the sample container in the centrifuge, watches the display, and records the result.",
            camera_direction="medium",
            emotion_tone="focused",
        )],
    )
    prompt = "【主体状态】Chen remains focused in the laboratory。【动作时间轴】保持动作起点清楚。"

    rendered = build_timed_shot_prompt(ShotPrompt(scene_id=1, prompt=prompt, duration_sec=8), script)

    assert "places the sample container in the centrifuge" in rendered
    assert "records the result" in rendered


def test_generic_dialogue_shot_keeps_canonical_action_instead_of_only_dialogue():
    """Dialogue staging must still carry an arbitrary topic's physical beat."""
    script = ScriptBreakdown(
        title="Greenhouse handoff",
        genre="drama",
        logline="Two students exchange a badge.",
        duration_sec=8,
        narration_style="none",
        scenes=[Scene(
            scene_id=1,
            duration_sec=8,
            location="school greenhouse",
            characters=["Evan", "Lina"],
            action_description="Evan hands Lina the school badge, and Lina locks the greenhouse door.",
            camera_direction="medium two shot",
            emotion_tone="cautious",
            dialogue=[
                DialogueLine(character="Evan", line="Keep it safe.", emotion="quiet"),
                DialogueLine(character="Lina", line="I will.", emotion="serious"),
            ],
        )],
    )
    prompt = "【主体状态】两人保持谨慎。【动作时间轴】保持动作起点清楚。"

    rendered = build_timed_shot_prompt(ShotPrompt(scene_id=1, prompt=prompt, duration_sec=8), script)

    assert "hands Lina the school badge" in rendered
    assert "locks the greenhouse door" in rendered


def test_courtyard_gate_scale_lock_does_not_use_street_shop_or_iron_door():
    from src.services.asset_prompt_composer import _scene_scale_lock

    inner = _scene_scale_lock("杨家院门内侧(上午,室外)", "从院内望向院门", "", "上午")
    doorway = _scene_scale_lock("杨家院门口(上午,室外)", "半开的院门口，深褐木门槛与石阶", "", "上午")
    assert "临街建筑外立面" not in inner
    assert "铁门" not in doorway
    assert "从院内望向院门" in inner or "庭院" in inner or "门体" in inner
    assert "门槛" in doorway or "院门" in doorway


def test_stone_table_and_path_scale_lock_beat_generic_courtyard_tokens():
    from src.services.asset_prompt_composer import _scene_scale_lock

    table = _scene_scale_lock("梁国杨家院子石桌旁(上午,室外庭院)", "庭院一角的石桌区域", "", "上午")
    path = _scene_scale_lock("杨家院门外小路(上午,室外)", "院门外的青石小路", "", "上午")
    courtyard = _scene_scale_lock("梁国杨家院子(上午,室外庭院)", "古代民居庭院，一侧双扇木门", "", "上午")

    assert "石桌石凳围合区域" in table
    assert "双侧门楼" in table
    assert "完整庭院边界" not in table
    assert "青石小路纵深" in path
    assert "禁止拍成院内天井" in path
    assert "单侧一座双扇门体" in courtyard
    assert "左右两侧各设一座门楼" in courtyard
