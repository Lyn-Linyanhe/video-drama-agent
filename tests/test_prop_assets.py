import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from src.agents.image_gen_agent import ImageGenerationAgent, build_prop_image_prompt
from src.api_clients.image_base import ImageGenResult
from src.api_clients.image_kling import KlingImageClient
from src.agents.visual_prompt_agent import VisualPromptAgent
from src.agents.video_gen_agent import VideoGenerationAgent
from src.schemas.prop import PropImage, PropImageAssets
from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.script import (
    LocationAsset,
    PropAsset,
    Scene,
    ScriptBreakdown,
    ShotVisualBinding,
    VisualAssetManifest,
)
from src.utils.visual_asset_manifest import (
    build_manifest_from_scenes,
    props_for_scene,
    prune_nonessential_props,
    reconcile_prop_bindings,
    validate_and_clean_props,
    validate_asset_manifest,
)


def _script_with_prop() -> ScriptBreakdown:
    return ScriptBreakdown(
        title="Key Prop",
        genre="Suspense",
        logline="A key exposes the truth.",
        duration_sec=5,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=5,
                location="Archive",
                characters=[],
                action_description="A hand turns an old key.",
                camera_direction="close-up",
                emotion_tone="tense",
            ),
            Scene(
                scene_id=2,
                duration_sec=5,
                location="Archive",
                characters=[],
                action_description="The same old key opens an archive drawer.",
                camera_direction="medium shot",
                emotion_tone="tense",
            ),
        ],
        asset_manifest=VisualAssetManifest(
            locations=[
                LocationAsset(
                    location_id="archive_night",
                    canonical_name="Archive",
                    representative_scene_id=1,
                    shot_ids=[1, 2],
                )
            ],
            props=[
                PropAsset(
                    prop_id="old_brass_key",
                    name="Old brass key",
                    description="A ten-centimeter brass key with green oxidation and complex teeth.",
                    shot_ids=[1, 2],
                    is_handheld=True,
                )
            ],
            shot_bindings=[
                ShotVisualBinding(
                    scene_id=1,
                    location_id="archive_night",
                    prop_ids=["old_brass_key"],
                ),
                ShotVisualBinding(
                    scene_id=2,
                    location_id="archive_night",
                    prop_ids=["old_brass_key"],
                ),
            ],
        ),
    )


def test_prop_prompt_uses_chinese_six_view_contract():
    prompt = build_prop_image_prompt("老式钥匙", "黄铜材质，暗绿色铜锈，复杂齿痕。", "现代悬疑，电影级写实")

    assert "2 行 3 列" in prompt
    assert "绝对正前方视图" in prompt
    assert "绝对正下方仰拍视图" in prompt
    assert "不得出现人物" in prompt
    assert "老式钥匙" in prompt
    assert "现代悬疑" not in prompt


def test_prune_nonessential_props_removes_one_shot_household_items():
    script = _script_with_prop()
    script.asset_manifest.props.extend([
        PropAsset(prop_id="towel", name="毛巾", description="普通家用毛巾，拿去擦水。", shot_ids=[1], is_handheld=True),
        PropAsset(prop_id="water_glass", name="水杯", description="桌上的普通水杯。", shot_ids=[1]),
    ])
    script.asset_manifest.shot_bindings[0].prop_ids.extend(["towel", "water_glass"])

    removed = prune_nonessential_props(script)

    assert removed == ["towel", "water_glass"]
    assert [item.prop_id for item in script.asset_manifest.props] == ["old_brass_key"]
    assert script.asset_manifest.shot_bindings[0].prop_ids == ["old_brass_key"]


def test_manifest_does_not_create_entity_props_for_sms_or_recording_content():
    script = ScriptBreakdown(
        title="虚拟内容",
        genre="悬疑",
        logline="手机收到短信，录音内容揭示线索。",
        duration_sec=6,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=3,
                location="办公室",
                characters=["苏眠"],
                action_description="苏眠查看手机上的短信，听见录音内容。",
                camera_direction="近景",
                emotion_tone="紧张",
            ),
            Scene(
                scene_id=2,
                duration_sec=3,
                location="办公室",
                characters=["苏眠"],
                action_description="苏眠再次查看短信，录音内容继续播放。",
                camera_direction="近景",
                emotion_tone="紧张",
            ),
        ],
    )

    manifest = build_manifest_from_scenes(script)

    assert {prop.name for prop in manifest.props}.isdisjoint({"短信", "录音"})


def test_manifest_keeps_a_physical_recorder_device():
    script = ScriptBreakdown(
        title="实体录音机",
        genre="悬疑",
        logline="录音机反复播放线索。",
        duration_sec=6,
        narration_style="none",
        scenes=[
            Scene(scene_id=1, duration_sec=3, location="办公室", characters=[], action_description="录音机播放录音。", camera_direction="近景", emotion_tone="紧张"),
            Scene(scene_id=2, duration_sec=3, location="办公室", characters=[], action_description="录音机继续播放录音。", camera_direction="近景", emotion_tone="紧张"),
        ],
    )

    manifest = build_manifest_from_scenes(script)

    assert "录音机" in {prop.name for prop in manifest.props}


def test_manifest_does_not_count_dialogue_mentions_as_visible_prop_shots():
    script = ScriptBreakdown(
        title="照片只在结尾出现",
        genre="悬疑",
        logline="角色先谈论照片，最终才看到实体照片。",
        duration_sec=9,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=3,
                location="便利店",
                characters=["苏眠"],
                dialogue=[{"character": "苏眠", "line": "照片背面写着不要相信他。", "emotion": "警惕"}],
                action_description="苏眠把信封藏到身后。",
                camera_direction="中近景",
                emotion_tone="紧张",
            ),
            Scene(
                scene_id=2,
                duration_sec=3,
                location="地下室",
                characters=["苏眠"],
                dialogue=[{"character": "苏眠", "line": "那张照片到底在哪？", "emotion": "追问"}],
                action_description="苏眠走向长桌。",
                camera_direction="中景",
                emotion_tone="压迫",
            ),
            Scene(
                scene_id=3,
                duration_sec=3,
                location="地下室",
                characters=["苏眠"],
                action_description="铁盒里放着一张新照片，苏眠拿起照片。",
                camera_direction="照片特写",
                emotion_tone="震惊",
            ),
        ],
    )

    manifest = build_manifest_from_scenes(script)

    assert "照片" not in {prop.name for prop in manifest.props}


def test_virtual_prop_names_are_removed_even_with_llm_critical_fields():
    script = _script_with_prop()
    script.asset_manifest.props.append(
        PropAsset(
            prop_id="sms_content",
            name="短信",
            description="屏幕上显示的短信内容，不是实体物品。",
            shot_ids=[1, 2],
            critical_reason="plot",
            critical_evidence="短信揭示线索。",
        )
    )
    for binding in script.asset_manifest.shot_bindings:
        binding.prop_ids.append("sms_content")

    removed = prune_nonessential_props(script)

    assert removed == ["sms_content"]
    assert all(prop.name != "短信" for prop in script.asset_manifest.props)


def test_reconcile_prop_bindings_uses_shot_bindings_as_source_of_truth():
    script = _script_with_prop()
    key = script.asset_manifest.props[0]
    key.shot_ids = [1, 2, 3]
    script.asset_manifest.shot_bindings[1].prop_ids = []

    reconciled = reconcile_prop_bindings(script)

    assert reconciled == ["old_brass_key"]
    assert key.shot_ids == [1]
    assert validate_asset_manifest(script) == []


def test_reconcile_prop_bindings_updates_generated_evidence_shot_list():
    script = _script_with_prop()
    key = script.asset_manifest.props[0]
    key.shot_ids = [1, 2, 3]
    key.critical_evidence = "该道具在分镜 1, 2, 3 中反复出现并承载剧情信息。"
    script.asset_manifest.shot_bindings[1].prop_ids = []

    reconcile_prop_bindings(script)

    assert key.shot_ids == [1]
    assert key.critical_evidence == "该道具在分镜 1 中反复出现并承载剧情信息。"


def test_prune_nonessential_props_removes_even_justified_single_shot_prop():
    script = _script_with_prop()
    key = script.asset_manifest.props[0]
    key.critical_reason = "plot"
    key.critical_evidence = "The key opens a hidden archive drawer and advances the plot."
    script.asset_manifest.shot_bindings[1].prop_ids = []
    reconcile_prop_bindings(script)

    removed = prune_nonessential_props(script)

    assert removed == ["old_brass_key"]
    assert script.asset_manifest.props == []
    assert all("old_brass_key" not in binding.prop_ids for binding in script.asset_manifest.shot_bindings)


def test_validate_and_clean_props_returns_removal_reasons():
    script = _script_with_prop()
    script.asset_manifest.props[0].critical_reason = "plot"
    script.asset_manifest.props[0].critical_evidence = ""

    result = validate_and_clean_props(script)

    assert result.cleaned_script is script
    assert result.removed_props == [{
        "prop_id": "old_brass_key",
        "name": "Old brass key",
        "reason": "missing_critical_evidence",
        "shot_ids": [1, 2],
    }]


def test_prop_whitelist_requires_a_reason_and_rejects_background_fixtures():
    script = _script_with_prop()
    key = script.asset_manifest.props[0]
    key.critical_reason = "plot"
    key.critical_evidence = "钥匙打开档案室，决定后续剧情"
    script.asset_manifest.props.extend([
        PropAsset(
            prop_id="laptop", name="笔记本电脑", description="银灰色普通笔记本电脑",
            shot_ids=[1, 2], critical_reason="", critical_evidence="",
        ),
        PropAsset(
            prop_id="cabinet", name="铁柜", description="墙边固定的灰色铁柜",
            shot_ids=[1], critical_reason="continuity", critical_evidence="与上个镜头保持一致",
        ),
        PropAsset(
            prop_id="recorder", name="裂纹录音笔", description="黑色金属录音笔，侧面有裂纹与红色指示灯",
            shot_ids=[1, 2], critical_reason="plot", critical_evidence="播放其中的录音揭露真相",
        ),
    ])
    script.asset_manifest.shot_bindings[0].prop_ids.extend(["laptop", "cabinet", "recorder"])
    script.asset_manifest.shot_bindings[1].prop_ids.append("recorder")

    removed = prune_nonessential_props(script)

    assert set(removed) == {"laptop", "cabinet"}
    assert [item.prop_id for item in script.asset_manifest.props] == ["old_brass_key", "recorder"]
    assert script.asset_manifest.shot_bindings[0].prop_ids == ["old_brass_key", "recorder"]
    assert script.asset_manifest.props[1].critical_reason == "plot"


def test_valid_many_props_are_not_truncated_and_emit_density_warning():
    script = _script_with_prop()
    anchor = script.asset_manifest.props[0]
    anchor.critical_reason = "plot"
    anchor.critical_evidence = "打开档案室"
    extra_props = []
    for index in range(1, 9):
        extra_props.append(PropAsset(
            prop_id=f"evidence_{index}", name=f"编号证物{index}",
            description=f"刻有编号{index}的黄铜证物，边缘有独特划痕",
            shot_ids=[1, 2], critical_reason="plot" if index < 6 else "action",
            critical_evidence="揭露案件真相" if index < 6 else "角色拿起后离开",
        ))
    script.asset_manifest.props.extend(extra_props)
    script.asset_manifest.shot_bindings[0].prop_ids.extend(item.prop_id for item in extra_props)
    script.asset_manifest.shot_bindings[1].prop_ids.extend(item.prop_id for item in extra_props)

    result = validate_and_clean_props(script)

    assert result.removed_props == []
    assert len(script.asset_manifest.props) == 9
    assert result.warnings == ["prop_density_high:9>8"]


def test_prop_sheet_generates_six_single_views_and_composes_locally():
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)

        class FakeImageClient:
            provider_name = "fake"

            def __init__(self):
                self.calls = []

            async def generate_image(self, prompt, negative_prompt="", width=1024, height=1024, seed=None):
                index = len(self.calls)
                self.calls.append((prompt, negative_prompt, width, height, seed))
                image_path = root / f"view_{index}.png"
                Image.new("RGB", (800 + index * 10, 700), (30 * index, 80, 160)).save(image_path)
                return ImageGenResult(success=True, local_path=str(image_path), width=800 + index * 10, height=700, seed=seed or 0, api_cost=0.01)

        client = FakeImageClient()
        agent = ImageGenerationAgent(api_client=client)
        agent.output_dir = root
        result = asyncio.run(agent.generate_prop_sheet("老式钥匙", "黄铜材质，10厘米长，绿色铜锈。", seed=1234))

        assert result.success is True
        assert len(client.calls) == 6
        assert all("不是拼图" in call[0] for call in client.calls)
        assert all("货架" in call[1] for call in client.calls)
        assert all(call[2:4] == (1024, 1024) for call in client.calls)
        assert {call[4] for call in client.calls} == {1234}
        with Image.open(result.local_path) as sheet:
            assert sheet.size == (1536, 1024)


def test_legacy_prop_sheet_can_apply_anime_style_contract():
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)

        class FakeImageClient:
            provider_name = "fake"

            def __init__(self):
                self.calls = []

            async def generate_image(self, prompt, negative_prompt="", width=1024, height=1024, seed=None):
                index = len(self.calls)
                self.calls.append((prompt, negative_prompt))
                image_path = root / f"view_{index}.png"
                Image.new("RGB", (1024, 1024), "white").save(image_path)
                return ImageGenResult(success=True, local_path=str(image_path), width=1024, height=1024, seed=seed or 0)

        client = FakeImageClient()
        agent = ImageGenerationAgent(api_client=client)
        agent.output_dir = root
        result = asyncio.run(
            agent.generate_prop_sheet(
                "旧钥匙",
                "黄铜材质，绿色铜锈。",
                seed=1234,
                art_style="动漫风",
            )
        )

        assert result.success is True
        assert all("二维动画道具设定参考图" in call[0] for call in client.calls)
        assert all("高质量写实道具" not in call[0] for call in client.calls)


def test_kling_client_reports_downloaded_image_dimensions():
    with TemporaryDirectory() as temp_dir:
        image_path = Path(temp_dir) / "downloaded.png"
        Image.new("RGB", (1344, 768), "white").save(image_path)
        client = KlingImageClient(api_key="test-key")

        async def submit(**_kwargs):
            return "task-id"

        async def poll(_task_id):
            return "https://example.invalid/image.png"

        async def download(_url, _task_id):
            return str(image_path)

        client._submit_task = submit
        client._poll_task = poll
        client._download_image = download
        result = asyncio.run(client.generate_image("test", width=1536, height=1024, seed=1))

        assert result.success is True
        assert (result.width, result.height) == (1344, 768)


def test_manifest_validates_prop_binding_and_returns_props_for_shot():
    script = _script_with_prop()

    assert validate_asset_manifest(script) == []
    assert [prop.prop_id for prop in props_for_scene(script, 1)] == ["old_brass_key"]

    script.asset_manifest.shot_bindings[0].prop_ids = ["missing_prop"]
    assert any("missing prop" in error for error in validate_asset_manifest(script))


def test_video_reference_bundle_ignores_standard_views_prop_sheet():
    script = _script_with_prop()
    prompts = GenerationPrompts(api_provider="mock", shots=[ShotPrompt(scene_id=1, prompt="key close-up", negative_prompt="", duration_sec=5)])
    with TemporaryDirectory() as temp_dir:
        image_path = Path(temp_dir) / "standard_views.png"
        image_path.write_bytes(b"reference")
        props = PropImageAssets(props=[
            PropImage(
                prop_id="old_brass_key",
                prop_name="Old brass key",
                image_prompt="prompt",
                image_path=str(image_path),
                seed=1,
            )
        ])

        refs = VideoGenerationAgent()._build_reference_bundle(
            prompts, None, None, script, props,
        )

    assert refs[1] == []


def test_bound_prop_is_injected_into_chinese_video_prompt():
    script = _script_with_prop()
    prompts = GenerationPrompts(
        api_provider="mock",
        shots=[ShotPrompt(scene_id=1, prompt="【0-3秒】手指靠近钥匙。", negative_prompt="", duration_sec=5)],
    )

    VideoGenerationAgent()._inject_prop_descriptions(prompts, script)

    assert "【道具锚点】" in prompts.shots[0].prompt
    assert "Old brass key" in prompts.shots[0].prompt
    assert "green oxidation" in prompts.shots[0].prompt
    assert "材质、颜色、尺寸和状态一致" in prompts.shots[0].prompt


def test_visual_prompt_context_lists_bound_props_for_each_scene():
    context = VisualPromptAgent()._format_scenes(_script_with_prop())

    assert "关键道具" in context
    assert "Old brass key" in context
