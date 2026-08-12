"""Visual Design Agent — designs visual style, composition, and color for each scene."""

from src.schemas.script import ScriptBreakdown
from src.schemas.visual import VisualDesign
from src.utils.llm import get_llm_client

SYSTEM_PROMPT = """你是一个专业的影视视觉设计师。你的任务是为短剧的每个场景设计视觉方案。

## 核心职责
1. 确定整体视觉风格和色调
2. 为每个场景设计构图、灯光、色彩方案
3. **最关键**: 保持角色外观一致性——同一个角色在所有场景中的外貌描述必须完全一致

## 角色一致性规则 (极其重要!)
- 为每个角色创建一个"角色特征字典"，必须包含以下全部维度:
  - 性别、年龄段、身高体型
  - 脸型（瓜子脸/方脸/圆脸/长脸等）
  - 肤色（白皙/小麦色/偏黑等）
  - 眼睛特征（大小/颜色/单双眼皮/形状）
  - 鼻子特征（高挺/小巧/圆润等）
  - 嘴唇特征（薄/厚/唇色等）
  - 发型发色（长度/颜色/直卷/刘海等）
  - 眉形（柳叶眉/剑眉/平眉等）
  - 身材类型（苗条/魁梧/丰满/瘦削）
  - 服装风格与颜色（要具体，如"白色衬衫+深蓝牛仔裤"而非"现代服装"）
  - 标志性特征（痣/疤痕/胡子/眼镜/特殊妆容等）
- 在所有场景的 character_appearance 中，同一角色的描述必须**逐字完全相同**
- 先在 global_character_descriptions 中定义所有角色，然后在每个场景中引用
- 角色描述要足够详细，以便后续的图像生成AI能准确还原角色外貌

## 风格选择指南
- 都市甜宠: 明亮暖色调、柔和灯光、浅景深
- 逆袭打脸: 对比色调、戏剧化灯光、强调质感
- 悬疑反转: 冷色调、高对比、阴影重
- 古装/穿越: 根据时代选择，古风暖黄或冷峻水墨
- 赛博朋克: 霓虹色调、蓝紫调、高饱和

## 构图指南(竖屏9:16)
- 人物居中或上三分之二位置
- 留白在下部用于字幕
- 特写占画面70%以上，中景占50%，远景占30%

## 输出格式
- composition: 用英文影视术语描述，便于后续生成视频prompt
- lighting: 描述主光源方向、色温、强度
- color_tone: 简洁描述色彩方案
- key_elements: 列出画面中必须包含的关键元素"""


class VisualDesignAgent:
    """Agent that designs visual style for each scene.

    This agent ensures visual consistency across the entire drama —
    same characters look the same in every scene.
    """

    def __init__(self):
        self.llm = get_llm_client()

    async def run(self, script: ScriptBreakdown) -> VisualDesign:
        """Design visual style for the entire script.

        Args:
            script: The script breakdown from ScriptAgent.

        Returns:
            A VisualDesign with consistent character appearances and per-scene visuals.
        """
        # Format script scenes for the prompt
        scenes_text = self._format_script_for_prompt(script)

        user_prompt = f"""请为以下短剧设计视觉方案：

**剧本标题**: {script.title}
**类型**: {script.genre}
**总时长**: {script.duration_sec}秒
**旁白风格**: {script.narration_style}

**场景列表**:
{scenes_text}

请输出完整的视觉设计方案，特别注意角色外观一致性。先在 global_character_descriptions 中列出所有角色及其外貌特征，然后在每个场景的 character_appearance 中使用完全相同的描述。"""

        visual = await self.llm.generate_structured(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            output_schema=VisualDesign,
            temperature=0.7,
        )
        return visual

    def _format_script_for_prompt(self, script: ScriptBreakdown) -> str:
        """Format script scenes as readable text for the LLM prompt."""
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
