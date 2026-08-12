import asyncio
from pathlib import Path

import pytest
from PIL import Image

from src.agents.scene_image_agent import SceneImageAgent
from src.api_clients.image_base import ImageGenResult
from src.schemas.script import (
    LocationAsset,
    Scene,
    ScriptBreakdown,
    ShotVisualBinding,
    VisualAssetManifest,
)
from src.schemas.scene_state import LocationState
from src.schemas.project import CreativeProfile
from src.schemas.visual import VisualDesign


class _ImageClient:
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
        return ImageGenResult(True, local_path=str(path), width=1920, height=1080)


class _LLM:
    async def generate_text(self, **_kwargs):
        return "旧兼容描述"


def _script() -> ScriptBreakdown:
    return ScriptBreakdown(
        title="旧场景入口",
        genre="都市",
        logline="测试",
        duration_sec=3,
        narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=3,
                location="便利店",
                characters=[],
                action_description="有人推门进入。",
                camera_direction="全景",
                emotion_tone="平静",
            )
        ],
    )


def _script_with_manifest() -> ScriptBreakdown:
    script = _script()
    script.asset_manifest = VisualAssetManifest(
        locations=[
            LocationAsset(
                location_id="storefront",
                canonical_name="便利店门口",
                identity_description="狭窄的街角便利店外立面，玻璃门和白色灯牌固定不变。",
                fixed_dressing="红色遮雨棚、右侧冷柜和门边广告灯箱。",
                anchor_points=["玻璃门", "白色灯牌", "红色遮雨棚"],
                environment_description="雨夜积水路面反射便利店灯光，远处有模糊街道。",
                representative_scene_id=1,
                shot_ids=[1],
                states=[
                    LocationState(
                        state_id="rain_night",
                        label="雨夜营业状态",
                        time_condition="深夜",
                        weather="持续降雨",
                        light_direction="灯牌暖光从右上方照向门口",
                        visible_state_notes="地面积水和玻璃上的雨痕可见",
                        shot_ids=[1],
                        representative_scene_id=1,
                    )
                ],
                default_state_id="rain_night",
            )
        ],
        shot_bindings=[
            ShotVisualBinding(scene_id=1, location_id="storefront", state_id="rain_night")
        ],
    )
    return script


def test_legacy_scene_image_agent_respects_anime_and_live_action_media(tmp_path):
    client = _ImageClient(tmp_path)
    agent = SceneImageAgent(api_client=client)
    agent.output_dir = tmp_path
    agent.llm = _LLM()
    visual = VisualDesign(overall_style="动漫风", color_palette="冷蓝灰", scenes=[])

    anime = asyncio.run(agent.run(_script(), visual))
    assert "二维背景绘制" in anime.scenes[0].image_prompt
    assert "photorealistic" not in anime.scenes[0].image_prompt
    assert "照片级实拍" in client.calls[0]["negative_prompt"]

    client.calls.clear()
    live_visual = VisualDesign(overall_style="电影级真人写实", color_palette="冷蓝灰", scenes=[])
    live = asyncio.run(agent.run(_script(), live_visual))
    assert "电影级真人实拍环境" in live.scenes[0].image_prompt
    assert "二维背景绘制" not in live.scenes[0].image_prompt
    assert "动漫场景" in client.calls[0]["negative_prompt"]


def test_legacy_scene_image_agent_uses_manifest_identity_state_and_drops_shot_action(tmp_path):
    client = _ImageClient(tmp_path)
    agent = SceneImageAgent(api_client=client)
    agent.output_dir = tmp_path
    script = _script_with_manifest()
    visual = VisualDesign(overall_style="现代电影级二维赛璐璐", color_palette="冷蓝灰", scenes=[])

    result = asyncio.run(agent.run(script, visual))

    prompt = result.scenes[0].image_prompt
    assert "地点身份：便利店门口" in prompt
    assert "雨夜积水路面反射便利店灯光" in prompt
    assert "场景状态：雨夜营业状态" in prompt
    assert "横向16:9空镜参考图" in prompt
    assert "有人推门进入" not in prompt
    assert "二维背景绘制" in prompt
    assert result.scenes[0].location_id == "storefront"
    assert result.scenes[0].state_id == "rain_night"


@pytest.mark.parametrize(
    ("style", "positive", "negative"),
    [
        ("现代电影级二维赛璐璐", "二维背景绘制", "电影级真人实拍环境"),
        ("半写实动漫", "二维背景绘制", "电影级真人实拍环境"),
        ("日系商业动画", "二维背景绘制", "电影级真人实拍环境"),
        ("电影级真人写实", "专业影视摄影媒介", "二维背景绘制"),
    ],
)
def test_legacy_manifest_scene_prompt_keeps_selected_medium(tmp_path, style, positive, negative):
    client = _ImageClient(tmp_path)
    agent = SceneImageAgent(api_client=client)
    agent.output_dir = tmp_path

    result = asyncio.run(agent.run(_script_with_manifest(), VisualDesign(overall_style=style, color_palette="冷蓝灰", scenes=[])))

    assert positive in result.scenes[0].image_prompt
    assert negative not in result.scenes[0].image_prompt


def test_legacy_scene_image_agent_uses_manifest_even_when_binding_is_missing(tmp_path):
    client = _ImageClient(tmp_path)
    agent = SceneImageAgent(api_client=client)
    agent.output_dir = tmp_path
    script = _script_with_manifest()
    script.asset_manifest.shot_bindings = []

    result = asyncio.run(agent.run(script, VisualDesign(overall_style="电影级真人写实", color_palette="冷蓝灰", scenes=[])))

    prompt = result.scenes[0].image_prompt
    assert "地点身份：便利店门口" in prompt
    assert "有人推门进入" not in prompt
    assert result.scenes[0].location_id == "storefront"
    assert result.scenes[0].state_id == "rain_night"


def test_legacy_scene_image_agent_prefers_stable_project_style_over_stale_visual_text(tmp_path):
    client = _ImageClient(tmp_path)
    agent = SceneImageAgent(api_client=client)
    agent.output_dir = tmp_path

    result = asyncio.run(
        agent.run(
            _script(),
            VisualDesign(overall_style="电影级真人写实", color_palette="冷蓝灰", scenes=[]),
            creative_profile=CreativeProfile(
                visual_style_id="semi_realistic_anime",
                art_style="写实",
            ),
        )
    )

    assert "二维背景绘制" in result.scenes[0].image_prompt
    assert "电影级真人实拍环境" not in result.scenes[0].image_prompt
    assert "实拍照片" in client.calls[0]["negative_prompt"]
