"""Scene Image Agent — generates environment/location images for each scene."""

import asyncio
import time

from src.schemas.script import ScriptBreakdown
from src.schemas.visual import VisualDesign
from src.schemas.scene_image import SceneImage, SceneImageAssets
from src.api_clients.image_base import ImageAPIClient
from src.api_clients.image_mock import MockImageClient
from src.utils.llm import get_llm_client
from src.config import OUTPUT_DIR, IMAGE_WIDTH, IMAGE_HEIGHT, MAX_CONCURRENT_IMAGE_GENERATIONS

SCENE_PROMPT_SYSTEM = """你是一个影视场景设计师。你的任务是为短剧每场戏生成中文场景环境视觉描述提示词。

## 要求
1. 所有提示词必须用中文
2. 描述场景的空间布局、建筑风格、道具、光线氛围
3. 不包含人物（这是纯场景空镜）
4. 风格必须与整体视觉设计一致
5. 固定为横向16:9场景参考构图"""


class SceneImageAgent:
    """Agent that generates environment/location images for each scene.

    These scene images help establish the visual world of the drama and
    can serve as reference or input for image-to-video generation.
    """

    def __init__(self, api_client: ImageAPIClient | None = None):
        self.llm = get_llm_client()
        self.api_client = api_client or MockImageClient()
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_IMAGE_GENERATIONS)
        self.output_dir = OUTPUT_DIR / "scenes"
        self.output_dir.mkdir(exist_ok=True)

    async def run(
        self,
        script: ScriptBreakdown,
        visual: VisualDesign,
        project_id: str = "",
        creative_profile=None,
    ) -> SceneImageAssets:
        """Generate environment images for all scenes.

        Args:
            script: The script breakdown.
            visual: The visual design with per-scene details.
            project_id: Optional project ID for output subdirectory.

        Returns:
            SceneImageAssets with all generated scene images.
        """
        start_time = time.time()

        out_dir = self.output_dir
        if project_id:
            out_dir = self.output_dir / project_id
            out_dir.mkdir(parents=True, exist_ok=True)

        # Build scene_id → SceneVisual lookup
        visual_map = {v.scene_id: v for v in visual.scenes}

        async def gen_one(scene) -> SceneImage:
            async with self._semaphore:
                vis = visual_map.get(scene.scene_id)
                return await self._generate_scene_image(
                    scene,
                    vis,
                    visual,
                    out_dir,
                    script=script,
                    creative_profile=creative_profile,
                )

        tasks = [gen_one(s) for s in script.scenes]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        scenes: list[SceneImage] = []
        total_cost = 0.0
        for r in results:
            if isinstance(r, SceneImage):
                scenes.append(r)
                total_cost += r.cost
            elif isinstance(r, Exception):
                print(f"  ⚠️ Scene image generation failed: {r}")

        elapsed = time.time() - start_time

        return SceneImageAssets(
            scenes=scenes,
            total_cost=total_cost,
            generation_time_sec=elapsed,
        )

    async def _generate_scene_image(
        self,
        scene,
        scene_visual,
        visual: VisualDesign,
        out_dir,
        script: ScriptBreakdown | None = None,
        creative_profile=None,
    ) -> SceneImage:
        """Generate an environment image for a single scene."""
        # Projects with a normalized visual manifest must use the same
        # identity/state/view contract as the current asset API. This keeps
        # the legacy orchestrator from reintroducing shot actions into a
        # reusable location prompt while preserving the fallback below for
        # projects that predate the manifest.
        manifest = getattr(script, "asset_manifest", None) if script is not None else None
        binding = next(
            (item for item in (getattr(manifest, "shot_bindings", []) or []) if item.scene_id == scene.scene_id),
            None,
        ) if manifest is not None else None
        location_asset = next(
            (
                item
                for item in (getattr(manifest, "locations", []) or [])
                if (
                    (binding is not None and item.location_id == binding.location_id)
                    or item.canonical_name == scene.location
                    or scene.location in item.canonical_name
                    or item.canonical_name in scene.location
                    or scene.location in (getattr(item, "aliases", []) or [])
                )
            ),
            None,
        ) if manifest is not None else None
        if manifest is not None:
            from src.graph.state import DramaState
            from src.schemas.project import CreativeProfile
            from src.services.asset_prompt_composer import compose_asset_prompt, scene_environment_key_elements
            from src.utils.visual_asset_manifest import scene_source_signature, stable_location_id

            location_id = (
                (binding.location_id if binding is not None else "")
                or getattr(location_asset, "location_id", "")
                or stable_location_id(scene.location)
            )
            state_id = (
                (binding.state_id if binding is not None else "")
                or getattr(location_asset, "default_state_id", "")
            )
            profile = creative_profile or CreativeProfile.from_request(
                art_style=getattr(visual, "overall_style", "") or "写实",
                video_ratio=getattr(visual, "aspect_ratio", "9:16"),
            )
            prompt_state = DramaState(
                script=script,
                visual_design=visual,
                creative_profile=profile,
            )
            visual_custom = ""
            if scene_visual:
                visual_custom = "；".join(
                    part for part in (
                        f"构图：{scene_visual.composition}",
                        f"光线：{scene_visual.lighting}",
                        f"色调：{scene_visual.color_tone}",
                        f"关键环境元素：{'、'.join(scene_environment_key_elements(scene_visual, script, scene.scene_id))}",
                    )
                    if part.split("：", 1)[-1].strip()
                )
            asset = SceneImage(
                scene_id=scene.scene_id,
                location_id=location_id,
                state_id=state_id,
                location=scene.location,
                view_type="wide",
                image_prompt="",
                image_path="",
                seed=0,
            )
            composition = compose_asset_prompt(
                prompt_state,
                "scene",
                location_id,
                "wide",
                asset,
                visual_custom,
                provider=getattr(self.api_client, "provider_name", ""),
            )
            prompt = composition["provider_prompt"]
            negative_prompt = composition.get(
                "provider_negative_prompt",
                composition["negative_prompt"],
            )
            seed = scene.scene_id * 1000 + hash(location_id) % 1000
            result = await self.api_client.generate_image(
                prompt=prompt,
                negative_prompt=negative_prompt,
                width=composition["width"],
                height=composition["height"],
                seed=seed,
            )
            local_path = result.local_path or ""
            if result.success and local_path:
                import shutil

                dest = out_dir / f"scene_{scene.scene_id:02d}.png"
                try:
                    shutil.move(local_path, str(dest))
                    local_path = str(dest)
                except Exception:
                    pass
            return SceneImage(
                scene_id=scene.scene_id,
                location_id=location_id,
                state_id=state_id,
                location=scene.location,
                view_type="wide",
                image_prompt=composition["effective_prompt"],
                base_prompt=composition.get("base_prompt", ""),
                image_path=local_path,
                seed=seed,
                width=composition["width"],
                height=composition["height"],
                api_provider=self.api_client.provider_name,
                cost=result.api_cost,
                source_signature=scene_source_signature(location_id, composition["effective_prompt"]),
                prompt_template_version=composition.get("prompt_template_version", 1),
            )

        # Build scene context for the compatibility prompt path.  This agent
        # predates the project asset composer, so keep its public API but use
        # the same canonical style registry and medium-specific constraints.
        vis_context = ""
        if scene_visual:
            vis_context = (
                f"构图：{scene_visual.composition}\n"
                f"光线：{scene_visual.lighting}\n"
                f"色调：{scene_visual.color_tone}\n"
                f"关键元素：{'、'.join(scene_visual.key_elements)}"
            )

        from src.agents.image_gen_agent import is_anime_art_style
        from src.schemas.project import CreativeProfile
        from src.services.visual_styles import resolve_profile_style

        profile = creative_profile or CreativeProfile.from_request(
            art_style=getattr(visual, "overall_style", "") or "写实",
            video_ratio=getattr(visual, "aspect_ratio", "9:16"),
        )
        style_id = resolve_profile_style(profile)["id"]
        from src.services.visual_styles import get_visual_style

        style_entry = get_visual_style(style_id)
        style_name = style_entry["name"]
        style_positive = style_entry["prompt_layers"]["scene"]
        style_negative = style_entry["negative_prompt_layers"]["scene"]
        anime_style = is_anime_art_style(style_name)
        medium = (
            "二维背景绘制媒介，稳定透视，清晰线稿或受控材质绘制，电影级构图。"
            if anime_style
            else "电影级真人实拍环境，物理可信材质、光源和自然景深。"
        )
        scene_prompt = (
            f"高质量专业场景参考图，横向16:9空镜构图。地点：{scene.location}。"
            f"情绪氛围：{scene.emotion_tone}。色调方案：{visual.color_palette}。"
            f"{vis_context}项目艺术风格：{style_name}。风格执行层：{style_positive}"
            f"{medium}空间结构清晰，前景、中景、后景层次明确，固定陈设和材质稳定，"
            "画面只包含环境与陈设，不包含人物、行人、人体局部、文字、水印、标签或UI元素。"
        )
        scene_negative = (
            "人物，角色，行人，人体局部，手脚，人脸，文字，水印，字幕，标签，UI元素，模糊，畸变，"
            + style_negative
        )

        # Generate image
        seed = scene.scene_id * 1000 + hash(scene.location) % 1000

        result = await self.api_client.generate_image(
            prompt=scene_prompt,
            negative_prompt=scene_negative,
                        width=1920,
                        height=1080,
            seed=seed,
        )

        # Move to scene-specific path
        local_path = result.local_path or ""
        if result.success and local_path:
            import shutil
            dest = out_dir / f"scene_{scene.scene_id:02d}.png"
            try:
                shutil.move(local_path, str(dest))
                local_path = str(dest)
            except Exception:
                pass

        return SceneImage(
            scene_id=scene.scene_id,
            location=scene.location,
            image_prompt=scene_prompt,
            image_path=local_path,
            seed=seed,
            width=1920,
            height=1080,
            api_provider=self.api_client.provider_name,
            cost=result.api_cost,
        )
