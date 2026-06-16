"""Image Generation Agent — generates multi-view reference images per character and scene.

Characters: 3 images (portrait / profile / full_body)
Scenes:     2 images (wide / medium)

Multiple views give video APIs richer visual reference for character consistency.
"""

import asyncio
import shutil
import time
from pathlib import Path

from src.schemas.script import ScriptBreakdown
from src.schemas.visual import VisualDesign
from src.schemas.character import CharacterImage, CharacterImageAssets
from src.schemas.scene_image import SceneImage, SceneImageAssets
from src.api_clients.image_base import ImageAPIClient
from src.api_clients.image_mock import MockImageClient
from src.utils.llm import get_llm_client
from src.config import (
    OUTPUT_DIR, CHARACTER_IMAGE_SIZE, IMAGE_WIDTH, IMAGE_HEIGHT,
    MAX_CONCURRENT_IMAGE_GENERATIONS,
)

# ── Multi-view config ──────────────────────────────────────────────

CHARACTER_VIEWS = [
    ("portrait", "正面肖像", 1024, 1024,
     "Front-facing headshot portrait, looking directly at camera, clean studio lighting, "
     "neutral background, centered composition, photorealistic, 8K, sharp focus, high detail"),
    ("profile", "3/4侧面", 1024, 1024,
     "3/4 profile view, medium shot from chest up, looking slightly to the side, "
     "showing facial contour and depth, studio lighting, neutral background, "
     "photorealistic, 8K, sharp focus"),
    ("full_body", "全身照", IMAGE_WIDTH, IMAGE_HEIGHT,
     "Full body shot, standing pose against neutral background, showing complete outfit "
     "and physique, studio lighting, vertical 9:16 composition, photorealistic, 8K"),
]

SCENE_VIEWS = [
    ("wide", "全景",
     "Wide establishing shot of {location}, showing the full environment. "
     "{extra}cinematic, photorealistic, 9:16 vertical composition, empty scene without people"),
    ("medium", "中景",
     "Medium shot focusing on key details of {location}. "
     "{extra}cinematic lighting, photorealistic, 9:16 vertical composition, detailed environment, no characters"),
]

# ── LLM prompt for scene descriptions ──────────────────────────────

SCENE_PROMPT_SYSTEM = """你是一个影视场景设计师。为短剧场景生成英文环境视觉描述提示词。

要求：
1. 所有提示词必须用英文
2. 描述场景的空间布局、建筑风格、道具、光线氛围
3. 不包含人物（纯场景空镜）
4. 风格必须与整体视觉设计一致
5. 适配竖屏构图 (9:16)
6. 风格：cinematic, photorealistic, 9:16 vertical composition"""


class ImageGenerationAgent:
    """Generates multi-view character portraits and scene environment images.

    Each character gets 3 reference images (portrait, profile, full_body).
    Each scene gets 2 reference images (wide, medium).

    All images are generated concurrently for maximum throughput.
    """

    def __init__(self, api_client: ImageAPIClient | None = None):
        self.llm = get_llm_client()
        self.api_client = api_client or MockImageClient()
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_IMAGE_GENERATIONS)
        self.output_dir = OUTPUT_DIR

    async def run(
        self,
        script: ScriptBreakdown,
        visual: VisualDesign,
        project_id: str = "",
    ) -> tuple[CharacterImageAssets, SceneImageAssets]:
        """Generate all character and scene reference images.

        Returns:
            Tuple of (CharacterImageAssets, SceneImageAssets).
        """
        start_time = time.time()

        # Output directories
        char_base = self.output_dir / "characters"
        scene_base = self.output_dir / "scenes"
        if project_id:
            char_base = char_base / project_id
            scene_base = scene_base / project_id

        char_descriptions = visual.global_character_descriptions or {}

        # ── Build all tasks ──────────────────────────────────────

        # Each character × 3 views
        char_tasks = []
        for name, desc in char_descriptions.items():
            char_dir = char_base / name
            char_dir.mkdir(parents=True, exist_ok=True)
            base_seed = sum(ord(c) for c in name) % 999999 + 1
            for view_type, label, w, h, suffix in CHARACTER_VIEWS:
                char_tasks.append(
                    self._generate_character_image(
                        name, desc, view_type, label, w, h, suffix, base_seed, char_dir,
                    )
                )

        # Each scene × 2 views (LLM prompt per scene shared across views)
        visual_map = {v.scene_id: v for v in visual.scenes}
        scene_tasks = []
        for scene in script.scenes:
            scene_dir = scene_base / f"scene_{scene.scene_id:02d}"
            scene_dir.mkdir(parents=True, exist_ok=True)
            scene_visual = visual_map.get(scene.scene_id)
            scene_tasks.append(
                self._generate_scene_images(
                    scene, scene_visual, visual, scene_dir,
                )
            )

        # ── Run all concurrently ─────────────────────────────────
        all_tasks = char_tasks + scene_tasks
        results = await asyncio.gather(*all_tasks, return_exceptions=True)

        # ── Collect results ──────────────────────────────────────
        characters: list[CharacterImage] = []
        scene_images: list[SceneImage] = []
        total_cost = 0.0

        for r in results:
            if isinstance(r, list):
                # Scene tasks return lists of SceneImage
                for item in r:
                    if isinstance(item, SceneImage):
                        scene_images.append(item)
                        total_cost += item.cost
            elif isinstance(r, CharacterImage):
                characters.append(r)
                total_cost += r.cost
            elif isinstance(r, SceneImage):
                scene_images.append(r)
                total_cost += r.cost
            elif isinstance(r, Exception):
                print(f"  ⚠️ Image generation failed: {r}")

        elapsed = time.time() - start_time

        return (
            CharacterImageAssets(
                characters=characters,
                total_cost=total_cost,
                generation_time_sec=elapsed,
            ),
            SceneImageAssets(
                scenes=scene_images,
                total_cost=total_cost,
                generation_time_sec=elapsed,
            ),
        )

    # ── Character image generation ────────────────────────────────

    async def _generate_character_image(
        self,
        name: str,
        description: str,
        view_type: str,
        label: str,
        width: int,
        height: int,
        prompt_suffix: str,
        base_seed: int,
        out_dir: Path,
    ) -> CharacterImage:
        """Generate a single character reference image for one view."""
        image_prompt = (
            f"Character portrait of {name}: {description}. {prompt_suffix}"
        )
        # Offset seed per view so same character's different views are deterministic
        view_offsets = {"portrait": 0, "profile": 1, "full_body": 2}
        seed = base_seed + view_offsets.get(view_type, 0)

        async with self._semaphore:
            result = await self.api_client.generate_image(
                prompt=image_prompt,
                width=width,
                height=height,
                seed=seed,
            )

        local_path = result.local_path or ""
        if result.success and local_path:
            dest = out_dir / f"{view_type}.png"
            try:
                shutil.move(local_path, str(dest))
                local_path = str(dest)
            except Exception:
                pass

        return CharacterImage(
            character_name=name,
            view_type=view_type,
            image_prompt=image_prompt,
            image_path=local_path,
            seed=seed,
            width=width,
            height=height,
            api_provider=self.api_client.provider_name,
            cost=result.api_cost,
        )

    # ── Scene image generation ────────────────────────────────────

    async def _generate_scene_images(
        self,
        scene,
        scene_visual,
        visual: VisualDesign,
        out_dir: Path,
    ) -> list[SceneImage]:
        """Generate all view images for one scene. Returns list of SceneImage."""
        # Build context for LLM prompt
        vis_context = ""
        if scene_visual:
            vis_context = (
                f"Composition: {scene_visual.composition}. "
                f"Lighting: {scene_visual.lighting}. "
                f"Color tone: {scene_visual.color_tone}. "
                f"Key elements: {', '.join(scene_visual.key_elements)}. "
            )

        # Generate base scene description via LLM (one call, shared across views)
        user_prompt = f"""请为以下场景生成一个英文场景环境描述（不包含人物）：

**场景**: {scene.scene_id}
**地点**: {scene.location}
**动作描述**: {scene.action_description}
**情绪**: {scene.emotion_tone}
**整体风格**: {visual.overall_style}
**色调方案**: {visual.color_palette}
{vis_context}

请输出一个纯场景空镜的英文视觉描述（不含人物）。用英文输出，直接给出文本。"""

        try:
            base_description = await self.llm.generate_text(
                system_prompt=SCENE_PROMPT_SYSTEM,
                user_prompt=user_prompt,
                temperature=0.6,
            )
            base_description = base_description.strip().strip('"').strip("'")
        except Exception as e:
            print(f"  ⚠️ Scene prompt generation failed for scene {scene.scene_id}: {e}")
            base_description = f"{scene.location}, {scene.emotion_tone} atmosphere, {visual.overall_style}"

        # Generate each view
        base_seed = scene.scene_id * 1000 + hash(scene.location) % 1000
        view_offsets = {"wide": 0, "medium": 1}

        tasks = []
        for view_type, view_label, template in SCENE_VIEWS:
            prompt = template.format(
                location=scene.location,
                extra=vis_context,
            )
            seed = base_seed + view_offsets.get(view_type, 0)
            tasks.append(
                self._generate_scene_single(
                    scene.scene_id, scene.location, view_type, view_label,
                    prompt, seed, out_dir,
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, SceneImage)]

    async def _generate_scene_single(
        self,
        scene_id: int,
        location: str,
        view_type: str,
        label: str,
        prompt: str,
        seed: int,
        out_dir: Path,
    ) -> SceneImage:
        """Generate a single scene view image."""
        async with self._semaphore:
            result = await self.api_client.generate_image(
                prompt=prompt,
                width=IMAGE_WIDTH,
                height=IMAGE_HEIGHT,
                seed=seed,
            )

        local_path = result.local_path or ""
        if result.success and local_path:
            dest = out_dir / f"{view_type}.png"
            try:
                shutil.move(local_path, str(dest))
                local_path = str(dest)
            except Exception:
                pass

        return SceneImage(
            scene_id=scene_id,
            location=location,
            view_type=view_type,
            image_prompt=prompt,
            image_path=local_path,
            seed=seed,
            width=IMAGE_WIDTH,
            height=IMAGE_HEIGHT,
            api_provider=self.api_client.provider_name,
            cost=result.api_cost,
        )
