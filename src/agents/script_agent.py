"""Script Agent — breaks user story input into a structured shooting script."""

from src.schemas.script import ScriptBreakdown
from src.utils.llm import get_llm_client

SYSTEM_PROMPT = """你是一个专业的短剧编剧。你的任务是将用户输入的故事情节，拆分为一个结构化的分镜脚本。

## 核心原则
1. **短剧节奏感**: 开头3秒抓人(冲突/悬念)，中段发展，结尾反转或爽点
2. **竖屏拍摄**: 所有画面设计要适配9:16竖屏比例
3. **场景紧凑**: 每个场景控制在5-10秒，总共5-12个场景
4. **对话精炼**: 每句台词不超过30字，用动作和画面讲故事优先于对话
5. **视觉化思维**: 关注画面感——每个场景必须对后续的视频生成有明确的视觉指导

## 镜头指导
- 特写(close-up): 表现情绪、关键物品
- 中景(medium shot): 对话、人物互动
- 远景(wide shot): 环境建立、大场面

## 短剧常见类型
- 都市甜宠/霸总、逆袭打脸、悬疑反转、穿越/重生、家庭伦理
- 根据用户输入自动判断类型

## 重要约束
- scene_id 从1开始递增
- duration_sec 每个场景2-30秒，总和不超过180秒
- 每句台词控制在30字以内
- location 要具体（如"现代办公室"、"古风庭院"）
- action_description 要视觉化、可拍摄
- transition_from_prev 从第二个场景开始填写"""


class ScriptAgent:
    """Agent that breaks a story into structured shooting script.

    This is the first agent in the pipeline. It takes raw user text and
    produces a ScriptBreakdown with detailed scenes, dialogue, and camera directions.
    """

    def __init__(self):
        self.llm = get_llm_client()

    async def run(self, user_input: str) -> ScriptBreakdown:
        """Convert user story input into a structured script breakdown.

        Args:
            user_input: Raw text — can be a full story, a scene idea,
                        a premise, or even just a genre request.

        Returns:
            A structured ScriptBreakdown ready for the Visual Design Agent.
        """
        user_prompt = f"""请将以下内容改编为短剧分镜脚本：

---
{user_input}
---

请输出完整的分镜脚本，包含：
- 吸引人的短剧标题(title)
- 准确的类型标签(genre)
- 一句话梗概(logline)
- 预估总时长(duration_sec)
- 旁白风格(narration_style)
- 5-12个详细场景(scenes)，每个包含场景地点、角色、对话、动作描述、镜头指导、情绪基调"""

        script = await self.llm.generate_structured(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            output_schema=ScriptBreakdown,
            temperature=0.8,  # Higher creativity for script writing
        )
        return script
