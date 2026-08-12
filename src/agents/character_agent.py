"""Character Image Agent — analyzes characters and generates portrait images."""

import asyncio
import time

from src.schemas.script import ScriptBreakdown
from src.schemas.visual import VisualDesign
from src.schemas.character import CharacterProfile, CharacterImage, CharacterImageAssets
from src.services.asset_seed import derive_asset_seed
from src.api_clients.image_base import ImageAPIClient
from src.api_clients.image_mock import MockImageClient
from src.utils.llm import get_llm_client
from src.config import OUTPUT_DIR, CHARACTER_IMAGE_SIZE, MAX_CONCURRENT_IMAGE_GENERATIONS

ANALYSIS_SYSTEM_PROMPT = """你是一个专业的影视角色造型师。你的任务是对剧本中的每个角色进行详细的外貌分析。

## 分析要求
对于每个角色，请从以下维度进行精确描述，每个描述要**具体、可视觉化**：

1. **基本属性**: 性别、年龄段、身高体型
2. **面部特征**: 脸型、肤色、眼睛(大小/颜色/形状)、鼻子、嘴唇、眉形
3. **发型**: 发型、发色、长度、质感
4. **服装**: 风格、颜色、材质、层次
5. **配饰**: 眼镜/耳环/项链/手表等
6. **标志性特征**: 与众不同的地方(痣、疤痕、特殊妆容等)
7. **性格气质**: 通过外貌体现的性格特点

## 重要
- 同一个角色在所有场景中外观必须一致
- 描述要足够详细，以便图像生成AI能准确还原
- 用中文描述，具体而非抽象"""


class CharacterAgent:
    """Agent that analyzes characters and generates their portrait images.

    This agent runs AFTER VisualDesignAgent and BEFORE PromptEngineeringAgent.
    It takes the script and visual design, analyzes each character in detail,
    then generates consistent character portrait images.

    The generated images serve as reference for subsequent video generation,
    improving character consistency across scenes.
    """

    def __init__(self, api_client: ImageAPIClient | None = None):
        self.llm = get_llm_client()
        self.api_client = api_client or MockImageClient()
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_IMAGE_GENERATIONS)
        self.output_dir = OUTPUT_DIR / "characters"
        self.output_dir.mkdir(exist_ok=True)

    async def run(
        self,
        script: ScriptBreakdown,
        visual: VisualDesign,
        project_id: str = "",
    ) -> CharacterImageAssets:
        """Analyze all characters and generate their portrait images.

        Args:
            script: The script breakdown with character lists.
            visual: The visual design with global_character_descriptions.
            project_id: Optional project ID for output subdirectory.

        Returns:
            CharacterImageAssets with all generated character images.
        """
        start_time = time.time()

        # Use project subdirectory if provided
        out_dir = self.output_dir
        if project_id:
            out_dir = self.output_dir / project_id
            out_dir.mkdir(parents=True, exist_ok=True)

        # Step 1: Analyze each character in detail
        profiles = await self._analyze_characters(script, visual)

        # Step 2: Generate portrait image for each character concurrently
        async def gen_one(profile: CharacterProfile) -> CharacterImage:
            async with self._semaphore:
                return await self._generate_character_image(profile, out_dir)

        tasks = [gen_one(p) for p in profiles]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect results
        characters: list[CharacterImage] = []
        total_cost = 0.0
        for r in results:
            if isinstance(r, CharacterImage):
                characters.append(r)
                total_cost += r.cost
            elif isinstance(r, Exception):
                print(f"  ⚠️ Character image generation failed: {r}")

        elapsed = time.time() - start_time

        return CharacterImageAssets(
            characters=characters,
            total_cost=total_cost,
            generation_time_sec=elapsed,
        )

    async def _analyze_characters(
        self, script: ScriptBreakdown, visual: VisualDesign
    ) -> list[CharacterProfile]:
        """Analyze all unique characters from the script and visual design."""
        # Collect all unique character names from all scenes
        all_characters: set[str] = set()
        for scene in script.scenes:
            all_characters.update(scene.characters)

        if not all_characters:
            return []

        # Build context from visual design
        char_descriptions = visual.global_character_descriptions or {}
        char_context = "\n".join(
            f"- {name}: {char_descriptions.get(name, '(无描述)')}"
            for name in all_characters
        )

        user_prompt = f"""请对以下剧本中的每个角色进行详细的外貌分析：

**剧本标题**: {script.title}
**类型**: {script.genre}
**视觉风格**: {visual.overall_style}
**色调方案**: {visual.color_palette}

**视觉设计中已有的角色描述**:
{char_context}

请为以下每个角色输出详细的 CharacterProfile（角色形象分析）：
{', '.join(all_characters)}

每个角色至少包含：性别、年龄、脸型、五官、发型发色、身材、服装风格、标志性特征。"""

        # Use LLM to get structured profiles
        profiles: list[CharacterProfile] = []
        for name in all_characters:
            char_prompt = f"{user_prompt}\n\n现在请专门分析角色「{name}」，输出一个完整的 CharacterProfile。"
            try:
                profile = await self.llm.generate_structured(
                    system_prompt=ANALYSIS_SYSTEM_PROMPT,
                    user_prompt=char_prompt,
                    output_schema=CharacterProfile,
                    temperature=0.7,
                )
                # Ensure the name field is correct
                if not profile.name or profile.name != name:
                    profile.name = name
                profiles.append(profile)
            except Exception as e:
                print(f"  ⚠️ Character analysis failed for '{name}': {e}")
                # Create a basic fallback profile
                existing_desc = char_descriptions.get(name, f"{name}，角色")
                profiles.append(CharacterProfile(
                    name=name,
                    gender="未知",
                    age_range="成年",
                    height="标准",
                    face_shape="标准脸型",
                    skin_tone="自然肤色",
                    eyes="标准",
                    nose="标准",
                    lips="标准",
                    hair_style="黑色短发",
                    eyebrows="自然眉形",
                    body_type="标准",
                    clothing_style="根据场景确定",
                    personality="待定",
                    role_type="配角",
                ))

        return profiles

    async def _generate_character_image(
        self, profile: CharacterProfile, out_dir
    ) -> CharacterImage:
        """Generate a portrait image for a single character."""
        image_prompt = f"""高质量专业角色设定图，单张独立正面半身图，纯白色干净背景，中性摄影棚灯光，平视镜头，写实影视质感。
角色信息：{profile.name}，{profile.age_range}，{profile.gender}，{profile.height}，{profile.body_type}；{profile.face_shape}，{profile.skin_tone}，{profile.eyes}，{profile.nose}，{profile.lips}；{profile.hair_style}，{profile.eyebrows}；穿{profile.clothing_style}。配饰：{profile.accessories or '无'}。标志性特征：{profile.distinguishing_features or '无'}。
要求：面部、发型、体型和服装清晰一致，人物空手、表情自然，不出现其他角色、环境背景、文字、水印、标签或 UI 元素。"""

        seed = derive_asset_seed("", "character", profile.name, "front_half", 1)

        result = await self.api_client.generate_image(
            prompt=image_prompt,
            negative_prompt="道具，武器，手持物，其他角色，环境背景，文字，水印，UI元素，模糊，畸变，额外肢体",
            width=CHARACTER_IMAGE_SIZE[0],
            height=CHARACTER_IMAGE_SIZE[1],
            seed=seed,
        )

        # Move to character-specific path if successful
        local_path = result.local_path or ""
        if result.success and local_path:
            import shutil
            dest = out_dir / f"{profile.name}.png"
            try:
                shutil.move(local_path, str(dest))
                local_path = str(dest)
            except Exception:
                pass  # Keep original path

        return CharacterImage(
            character_name=profile.name,
            image_prompt=image_prompt,
            image_path=local_path,
            seed=seed,
            width=CHARACTER_IMAGE_SIZE[0],
            height=CHARACTER_IMAGE_SIZE[1],
            api_provider=self.api_client.provider_name,
            cost=result.api_cost,
        )
