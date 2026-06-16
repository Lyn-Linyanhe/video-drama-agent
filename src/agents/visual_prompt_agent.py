"""Visual Prompt Agent — merged VisualDesign + PromptEngineering in one LLM call.

Eliminates the serial translation step: previously VisualDesignAgent produced
Chinese visual specs, then PromptEngineeringAgent translated them to English
API prompts in a second call. Now both are generated together.
"""

import random
from src.schemas.script import ScriptBreakdown
from src.schemas.visual import VisualDesign, SceneVisual
from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.utils.llm import get_llm_client
from src.config import DEFAULT_API

SYSTEM_PROMPT = """你是一个专业的影视视觉设计师兼AI视频提示词工程师。你的任务是一次性完成两项工作：

1. **视觉设计**: 为短剧的每个场景设计视觉方案（构图、灯光、色调）
2. **API提示词**: 将视觉设计转化为目标视频API最优的英文提示词

## 核心原则

### 角色一致性 (极其重要!)
- 为每个角色创建详细的外貌特征字典，**必须用英文**（下游图片/视频 API 对英文响应更好）
- 维度包括: gender, age range, height/build, face shape, skin tone, eyes (size/color/shape), nose, lips, hair (style/color/length), eyebrows, body type, clothing (style+colors, be specific), accessories, distinguishing features
- 同一角色在所有场景的 character_appearance 中描述必须完全一致
- 先在 global_character_descriptions 中定义所有角色，示例:
  "林屿": "Male, 17 years old, 178cm slim build, oval face, fair skin, deep black eyes, high nose bridge, thin lips, short black hair with side part, sword eyebrows, lean body, wearing a crisp white shirt and dark blue school blazer, clean and gentle aura"
- 同时提供 global_character_descriptions_cn 字段保存对应的中文版本（供前端展示）

### 视觉风格指南
- 都市甜宠: 明亮暖色调、柔和灯光、浅景深
- 逆袭打脸: 对比色调、戏剧化灯光、强调质感
- 悬疑反转: 冷色调、高对比、阴影重
- 古装/穿越: 古风暖黄或冷峻水墨
- composition 和 lighting 用英文影视术语描述

### 视频Prompt最佳实践
- 所有 prompt 必须用**英文**
- 具体而非抽象：用视觉描述替代形容词
- 质量词: 8K, high detail, sharp focus, professional cinematography
- 镜头: close-up shot, medium shot, wide establishing shot
- 灯光: cinematic lighting, soft natural light, neon lights, golden hour
- 运动: subtle motion, gentle camera movement, static shot, slow pan

### 时长处理
- 每个场景 duration_sec 2-30秒
- 如果场景超过10秒考虑拆分为 sub_shots

### 构图指南 (竖屏9:16)
- 人物居中或上三分之二，下方留白用于字幕
- 特写占画面70%以上，中景50%，远景30%

### Seed管理
- 同一角色在不同场景中用相同seed
- 不同场景用不同seed增加画面多样性"""


class VisualPromptAgent:
    """Agent that generates visual design AND API-optimized prompts in one pass.

    Replaces the two-step VisualDesignAgent → PromptEngineeringAgent pipeline
    with a single LLM call that outputs both the visual specification and the
    corresponding English video generation prompts.
    """

    def __init__(self, api_provider: str | None = None):
        self.llm = get_llm_client()
        self.api_provider = api_provider or DEFAULT_API

    async def run(
        self,
        script: ScriptBreakdown,
        character_images=None,
        scene_images=None,
        prompt_overrides: dict | None = None,
    ) -> tuple[VisualDesign, GenerationPrompts]:
        """Generate visual design and API prompts from the script.

        Args:
            script: The script breakdown.
            character_images: Optional CharacterImageAssets for reference.
            scene_images: Optional SceneImageAssets for reference.
            prompt_overrides: Optional dict {shot_index: overridden_prompt}.

        Returns:
            Tuple of (VisualDesign, GenerationPrompts).
        """
        scenes_text = self._format_scenes(script)

        user_prompt = f"""请为以下短剧设计视觉方案并生成 {self.api_provider} API 的英文视频提示词：

**剧本标题**: {script.title}
**类型**: {script.genre}
**总时长**: {script.duration_sec}秒
**旁白风格**: {script.narration_style}

**场景列表**:
{scenes_text}

请一次性输出：
1. 整体视觉风格和色调方案
2. 所有角色的详细外貌描述 (global_character_descriptions)
3. 每个场景的视觉设计（构图、灯光、色调、关键元素、角色外貌）
4. 每个场景的英文API prompt、negative_prompt、seed、motion_strength

注意：
- 所有 prompt 必须用英文
- 目标API是 {self.api_provider}，按该API最佳实践写prompt
- 同一角色在所有场景中用相同seed，外貌描述完全一致
- negative_prompt 根据场景内容定制，默认包含 blurry, distorted, low quality, watermark, text, logo, deformed face, extra fingers, ugly, bad anatomy"""

        output = await self.llm.generate_structured(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            output_schema=_VisualPromptOutput,
            temperature=0.7,
        )

        return self._split_output(output, script, prompt_overrides)

    def _format_scenes(self, script: ScriptBreakdown) -> str:
        lines = []
        for scene in script.scenes:
            lines.append(f"--- 场景 {scene.scene_id} ({scene.duration_sec}s) ---")
            lines.append(f"地点: {scene.location}")
            lines.append(f"角色: {', '.join(scene.characters)}")
            lines.append(f"动作: {scene.action_description}")
            lines.append(f"镜头: {scene.camera_direction}")
            lines.append(f"情绪: {scene.emotion_tone}")
            if scene.dialogue:
                for d in scene.dialogue:
                    lines.append(f"  {d.character}: {d.line}")
            lines.append("")
        return "\n".join(lines)

    def _split_output(
        self,
        output: "_VisualPromptOutput",
        script: ScriptBreakdown,
        prompt_overrides: dict | None,
    ) -> tuple[VisualDesign, GenerationPrompts]:
        """Split the combined LLM output into VisualDesign and GenerationPrompts."""

        # Build VisualDesign
        scene_visuals = []
        prompt_shots = []

        # Index shots by scene_id for quick lookup
        shot_by_scene = {s.scene_id: s for s in output.shots}

        for scene in script.scenes:
            shot = shot_by_scene.get(scene.scene_id)

            scene_visuals.append(SceneVisual(
                scene_id=scene.scene_id,
                composition=getattr(shot, 'composition', 'medium shot') if shot else 'medium shot',
                lighting=getattr(shot, 'lighting', 'natural light') if shot else 'natural light',
                color_tone=getattr(shot, 'color_tone', 'neutral') if shot else 'neutral',
                character_appearance=getattr(shot, 'character_appearance', {}) if shot else {},
                key_elements=getattr(shot, 'key_elements', []) if shot else [],
                style_reference=getattr(shot, 'style_reference', '') if shot else '',
            ))

            duration = scene.duration_sec if shot is None else (shot.duration_sec or scene.duration_sec)
            seed = None if shot is None else shot.seed
            if seed is None:
                seed = random.randint(1, 999999)

            prompt_shots.append(ShotPrompt(
                scene_id=scene.scene_id,
                prompt=getattr(shot, 'prompt', f'{scene.action_description}') if shot else scene.action_description,
                negative_prompt=getattr(
                    shot, 'negative_prompt',
                    'blurry, distorted, low quality, watermark, text, logo, deformed face, extra fingers, ugly, bad anatomy'
                ) if shot else 'blurry, distorted, low quality, watermark, text, logo, deformed face, extra fingers, ugly, bad anatomy',
                seed=seed,
                motion_strength=getattr(shot, 'motion_strength', 0.7) if shot else 0.7,
                duration_sec=duration,
                batch_size=getattr(shot, 'batch_size', 2) if shot else 2,
            ))

        visual = VisualDesign(
            overall_style=output.overall_style,
            color_palette=output.color_palette,
            aspect_ratio=output.aspect_ratio or "9:16",
            global_character_descriptions=output.global_character_descriptions or {},
            global_character_descriptions_cn=output.global_character_descriptions_cn or {},
            scenes=scene_visuals,
        )

        prompts = GenerationPrompts(
            api_provider=self.api_provider,
            global_negative_prompt=output.global_negative_prompt or (
                "blurry, distorted, low quality, watermark, text, logo, "
                "deformed face, extra fingers, ugly, bad anatomy"
            ),
            shots=prompt_shots,
        )

        # Apply user prompt overrides
        if prompt_overrides:
            for i, shot in enumerate(prompts.shots):
                if str(i) in prompt_overrides:
                    shot.prompt = prompt_overrides[str(i)]
                elif i in prompt_overrides:
                    shot.prompt = prompt_overrides[i]

        return visual, prompts


# ── Combined output schema (internal to this module) ──────────────

from pydantic import BaseModel, Field


class _ShotVisualPrompt(BaseModel):
    """Per-shot: visual design details + English API prompt."""
    scene_id: int = Field(description="Matches ScriptBreakdown.scenes[].scene_id")
    # Visual design
    composition: str = Field(default="medium shot", description="Composition / framing")
    lighting: str = Field(default="natural light", description="Lighting setup")
    color_tone: str = Field(default="neutral", description="Color grading")
    character_appearance: dict[str, str] = Field(
        default_factory=dict,
        description="Character name → appearance description (consistent across scenes)"
    )
    key_elements: list[str] = Field(default_factory=list, description="Must-appear elements")
    style_reference: str = Field(default="", description="Style reference")
    # API prompt
    prompt: str = Field(description="English API-optimized video prompt")
    negative_prompt: str = Field(
        default="blurry, distorted, low quality, watermark, text, logo, deformed face, extra fingers, ugly, bad anatomy"
    )
    seed: int | None = Field(default=None, description="Random seed")
    motion_strength: float = Field(default=0.7, ge=0.0, le=1.0)
    duration_sec: int = Field(default=5, ge=1, le=30)
    batch_size: int = Field(default=2, ge=1, le=4)


class _VisualPromptOutput(BaseModel):
    """Combined output: overall visual design + per-shot prompts."""
    overall_style: str = Field(description="Overall visual style: 写实, 动漫, 古风, etc.")
    color_palette: str = Field(description="Overall color palette description")
    aspect_ratio: str = Field(default="9:16")
    global_character_descriptions: dict[str, str] = Field(
        default_factory=dict,
        description="Master character appearance registry (English, for API use)"
    )
    global_character_descriptions_cn: dict[str, str] = Field(
        default_factory=dict,
        description="中文版本的角色外貌描述（供前端展示）"
    )
    global_negative_prompt: str = Field(
        default="blurry, distorted, low quality, watermark, text, logo, deformed face, extra fingers, ugly, bad anatomy"
    )
    shots: list[_ShotVisualPrompt] = Field(description="Per-scene visual + prompt")
