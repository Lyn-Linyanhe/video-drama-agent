"""Prompt Engineering Agent — converts visual designs into API-optimized video prompts."""

import random
from src.schemas.script import ScriptBreakdown
from src.schemas.visual import VisualDesign
from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.utils.llm import get_llm_client
from src.utils.visual_asset_manifest import props_for_scene
from src.config import DEFAULT_API
from src.services.timed_prompt_composer import apply_timed_prompt_structure

SYSTEM_PROMPT = """你是一个 AI 视频生成提示词工程师。你的任务是将分镜视觉设计转换为各视频 API 可直接使用的完整中文提示词。

## 核心原则
1. **中文 Prompt**: 所有视频提示词与负面提示词都使用中文，保留明确的分段结构
2. **具体而非抽象**: 用具体的视觉描述替代抽象形容词
3. **API适配**: 不同API有不同的prompt风格偏好
4. **负面提示词**: 针对每个场景定制negative prompt

## 视频Prompt最佳实践
```
风格: 电影感、写实 / 动漫 / 赛博朋克
镜头: 特写、中景、全景、推镜、跟拍
灯光: 电影灯光、柔和自然光、霓虹光、黄金时刻
运动: 细微动作、平稳运镜、静止镜头、慢摇镜
质量词: 高细节、清晰对焦、专业摄影、竖屏 9:16
人物: 详细描述外貌、服装、表情
环境: 详细描述场景、天气、时间
```

## 负面Prompt模板
```
低清晰度、模糊、畸形、水印、文字、标志、
畸形五官、多余手指、错误肢体、重复画面、压缩伪影、过曝、欠曝、道具变形
```

## 时长处理
- 如果场景超过10秒，需要拆分为sub_shots
- 每个sub_shot 5秒左右
- sub_shots之间用seed保持一致性

## Seed管理
- 同一角色在不同场景中使用相同seed保持面部一致性
- 不同场景使用不同seed增加画面多样性
- 在global_seed_range范围内分配

## 角色形象参考
- 如果提供了角色形象图(character_images)，在prompt中引用角色的详细外貌描述
- 同一角色在所有镜头中的外貌描述必须一致
- 用已生成的角色形象图中的外貌特征来精确描述角色

## 场景环境参考
- 如果提供了场景图(scene_images)，在prompt中引用场景的环境描述
- 保证场景的环境氛围与视觉设计一致"""


class PromptEngineeringAgent:
    """Agent that converts visual designs into optimized video generation prompts.

    This agent is domain-aware: it knows the quirks of each video API and
    crafts prompts that produce the best results on each platform.
    """

    def __init__(self, api_provider: str | None = None):
        self.llm = get_llm_client()
        self.api_provider = api_provider or DEFAULT_API

    async def run(
        self,
        script: ScriptBreakdown,
        visual: VisualDesign,
        character_images=None,
        scene_images=None,
        prompt_overrides: dict | None = None,
    ) -> GenerationPrompts:
        """Generate API-optimized prompts from the script and visual design.

        Args:
            script: The script breakdown.
            visual: The visual design.
            character_images: Optional CharacterImageAssets for reference.
            scene_images: Optional SceneImageAssets for reference.
            prompt_overrides: Optional dict {shot_index: overridden_prompt}.

        Returns:
            GenerationPrompts with optimized prompts per shot.
        """
        scenes_summary = self._format_scenes_for_prompt(script, visual)

        user_prompt = f"""请为以下短剧生成 {self.api_provider} API的视频生成提示词：

**全局视觉风格**: {visual.overall_style}
**色调方案**: {visual.color_palette}
**画幅比**: {visual.aspect_ratio}
**全局角色描述**:
{self._format_characters(visual)}

**分镜详情**:
{scenes_summary}

请为每个场景生成优化的完整中文视频 prompt。注意：
1. 目标API是 {self.api_provider}，请按照该API的最佳实践写prompt
2. 所有 prompt 和 negative_prompt 必须用中文
3. 同一角色在所有场景中用相同seed
4. 如果场景超过10秒，拆分为sub_shots
5. negative_prompt要根据场景内容定制"""

        prompts = await self.llm.generate_structured(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            output_schema=GenerationPrompts,
            temperature=0.6,  # Lower temperature for precise prompt engineering
        )

        # Override API provider (LLM might hallucinate it)
        prompts.api_provider = self.api_provider

        # Ensure all seeds are in valid range
        for shot in prompts.shots:
            if shot.seed is None:
                shot.seed = random.randint(1, 999999)

        # Apply user prompt overrides (direct text replacement)
        protected_scene_ids: set[int] = set()
        if prompt_overrides:
            for shot in prompts.shots:
                if str(shot.scene_id) in prompt_overrides:
                    shot.prompt = prompt_overrides[str(shot.scene_id)]
                    protected_scene_ids.add(shot.scene_id)

        apply_timed_prompt_structure(
            prompts.shots,
            script,
            protected_scene_ids=protected_scene_ids,
        )

        return prompts

    def _format_characters(self, visual: VisualDesign) -> str:
        """Format global character descriptions for the prompt."""
        descriptions = visual.global_character_descriptions_cn or visual.global_character_descriptions
        if not descriptions:
            return "(无)"
        lines = []
        for name, desc in descriptions.items():
            lines.append(f"  - {name}: {desc}")
        return "\n".join(lines)

    def _format_scenes_for_prompt(
        self, script: ScriptBreakdown, visual: VisualDesign
    ) -> str:
        """Format scenes with their visual designs for the prompt."""
        # Build scene_id → SceneVisual lookup
        visual_map = {v.scene_id: v for v in visual.scenes}

        lines = []
        for scene in script.scenes:
            vis = visual_map.get(scene.scene_id)
            lines.append(f"--- 镜头 {scene.scene_id} ({scene.duration_sec}s) ---")
            lines.append(f"地点: {scene.location}")
            lines.append(f"动作: {scene.action_description}")
            lines.append(f"情绪: {scene.emotion_tone}")
            lines.append(f"镜头: {scene.camera_direction}")
            props = props_for_scene(script, scene.scene_id)
            if props:
                lines.append("关键道具: " + "；".join(
                    f"{prop.name}（{prop.description or '外观以资产设定为准'}）" for prop in props
                ))
            if vis:
                lines.append(f"构图: {vis.composition}")
                lines.append(f"灯光: {vis.lighting}")
                lines.append(f"色调: {vis.color_tone}")
                lines.append(f"关键元素: {', '.join(vis.key_elements)}")
            if scene.dialogue:
                lines.append("台词:")
                for d in scene.dialogue:
                    lines.append(f"  {d.character}: {d.line}")
            lines.append("")
        return "\n".join(lines)
