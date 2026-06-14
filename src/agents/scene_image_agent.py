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

SCENE_PROMPT_SYSTEM = """你是一个影视场景设计师。你的任务是为短剧每场戏生成场景环境的英文视觉描述提示词。

## 要求
1. 所有提示词必须用英文
2. 描述场景的空间布局、建筑风格、道具、光线氛围
3. 不包含人物（这是纯场景空镜）
4. 风格必须与整体视觉设计一致
5. 适配竖屏构图 (9:16)"""


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
                return await self._generate_scene_image(scene, vis, visual, out_dir)

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
    ) -> SceneImage:
        """Generate an environment image for a single scene."""
        # Build scene context for prompt generation
        vis_context = ""
        if scene_visual:
            vis_context = (
                f"Composition: {scene_visual.composition}\n"
                f"Lighting: {scene_visual.lighting}\n"
                f"Color tone: {scene_visual.color_tone}\n"
                f"Key elements: {', '.join(scene_visual.key_elements)}"
            )

        user_prompt = f"""请为以下场景生成一个英文场景环境提示词（不包含人物）：

**场景**: {scene.scene_id}
**地点**: {scene.location}
**动作描述**: {scene.action_description}
**情绪**: {scene.emotion_tone}
**整体风格**: {visual.overall_style}
**色调方案**: {visual.color_palette}
{vis_context}

请输出一个纯场景空镜的英文视觉提示词（不含人物），适合用于AI图像生成。
风格：cinematic, photorealistic, 9:16 vertical composition.
用英文输出，直接给出prompt文本。"""

        # Generate the scene prompt via LLM (text, not structured)
        try:
            scene_prompt = await self.llm.generate_text(
                system_prompt=SCENE_PROMPT_SYSTEM,
                user_prompt=user_prompt,
                temperature=0.6,
            )
            scene_prompt = scene_prompt.strip().strip('"').strip("'")
        except Exception as e:
            print(f"  ⚠️ Scene prompt generation failed for scene {scene.scene_id}: {e}")
            scene_prompt = (
                f"Cinematic wide shot of {scene.location}, "
                f"{scene.emotion_tone} atmosphere, {visual.overall_style}, "
                f"vertical 9:16 composition, photorealistic, empty scene without people"
            )

        # Generate image
        seed = scene.scene_id * 1000 + hash(scene.location) % 1000

        result = await self.api_client.generate_image(
            prompt=scene_prompt,
            width=IMAGE_WIDTH,
            height=IMAGE_HEIGHT,
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
            width=IMAGE_WIDTH,
            height=IMAGE_HEIGHT,
            api_provider=self.api_client.provider_name,
            cost=result.api_cost,
        )
