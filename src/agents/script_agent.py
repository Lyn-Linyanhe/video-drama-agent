"""Script Agent — breaks user story input into a structured shooting script."""

import json

from pydantic import BaseModel, Field

from src.schemas.script import Scene, ScriptBreakdown
from src.schemas.project import CreativeProfile
from src.utils.llm import get_llm_client
from src.utils.visual_asset_manifest import (
    build_manifest_from_scenes,
    normalize_scene_assets,
    prune_nonessential_props,
    reconcile_prop_bindings,
    repair_asset_manifest,
    validate_asset_manifest,
)

VISUAL_ASSET_RULES = """
## 视觉资产清单（必须输出 asset_manifest）
- scenes 是按时间顺序排列的分镜/镜头列表，必须保留所有剧情节拍，不能因为地点复用而删除镜头。
- asset_manifest.characters 只收录需要形象一致性的角色实体。appearance_mode 只能是 on_screen、voice_only、still_photo、mentioned。
- 只有 physically visible in at least one shot 的人物标为 on_screen；旁白、电话声、录音、画外音、被提及人物标为 voice_only 或 mentioned；只作为照片/遗像/屏幕照片出现的人物标为 still_photo。非 on_screen 人物绝不能出现在任何 shot_bindings[].on_screen_character_ids。
- 每个 on_screen 角色的 description 必须提供可用于后续形象设计的身份、年龄段或体态、常态服装和可见标志信息；不要把仅有声音或被提及的人物混入画面角色。
- 同一人物的便装、伪装或不同服饰是同一 asset_id；把差异写在 shot_bindings[].visible_state_notes 或 characters[].costume_states，不能另建“黑衣人”等重复人物。
- asset_manifest.locations 按“物理地点身份 + 室内/外分区”去重：同一场所的门口、街角、玻璃门、外立面和街景属于同一个外部 location；同一场所的柜台、入口、货架和室内纵深属于同一个内部 location。时间、天气、光源、机位、人物动作、道具特写和子区域名称不能新建 location。只有真正不同的建筑/房间，或明确跨越室内外边界时才拆分。
- canonical_name 写稳定的物理地点名，例如“花店外部”“花店内部”；原始子区域写入 aliases 或分镜 location，不能把“花店门口·街角”“花店门口·街景”拆成多个资产。
- 每个 location 的 environment_description 只写多个相关分镜共有的固定建筑、空间结构与陈设；冲突的时间、天气和光源不要写死。
- asset_manifest.props 只收录必须跨镜头保持外观连续，或直接承担线索、证据、凶器、密码、交接、反转等剧情作用的关键实体道具。手机、照片、钥匙、合同等只是可能的例子，不是自动候选；必须从当前剧本中找到同一实体至少在两个分镜可见或被使用，并有影响后续理解或镜头连续性的证据。仅仅被角色拿起、使用一次，不代表它是关键道具；普通毛巾、水杯、餐具、家具、背景陈设、一次性杂物、仅被提及但不出镜的物体绝不能创建 prop。
- 短信内容、录音内容、电话/通话、旁白、心声、对白和提示音都是信息或声音，不是实体道具；只有手机、录音机、录音笔等实际可触摸的设备在满足关键性条件时才可列为 prop。
- prop_id 使用稳定 ASCII 小写标识；description 只能描述肉眼可见的颜色、材质、尺寸比例、固定形态、结构、磨损和标志性细节，禁止写角色动作、用途或剧情句子。每个 prop 必须填写 critical_reason，且只能是 plot、continuity、action、handoff、identity 之一；还必须填写 critical_evidence，具体说明至少两个分镜中它如何作为线索/证据/交接物/身份标记而影响后续理解。没有明确理由、证据或跨镜头可见性时绝不能创建 prop。
- 普通物品即使跨镜头出现也不自动成为 prop；固定家具、桌椅、柜体、灯具、电脑、背景陈设归 location，血迹、水渍、烟雾、碎玻璃等非实体状态归 environment 或 visible_state_notes。不要设置固定数量上限；只要每个道具都满足关键性、证据和跨镜头绑定要求，就必须保留。
- prop.shot_ids 与 shot_bindings[].prop_ids 必须双向一致；只把该镜头实际可见且需要连续性的关键道具绑定进去。
- 每个分镜必须刚好有一个 shot_bindings 条目并绑定到一个 location_id；on_screen_character_ids 仅列该画面中实际看得见的人，voice_only_speakers 单独列声音来源。
- shot_bindings[].visible_state_notes 必须概述该镜头的人物可见状态、道具如何出现或被使用，以及镜头结束时需要延续的道具状态；没有关键道具时留空，不要编造。
- location.shot_ids 必须列出所有使用它的分镜，representative_scene_id 必须是其中一个分镜，用于生成一次可复用的场景图。
- asset_id 与 location_id 使用稳定的 ASCII 小写标识，例如 su_mian、clocktower_basement_night。
"""

SYSTEM_PROMPT = """你是一个专业的短剧编剧。你的任务是将用户输入的故事情节，拆分为结构化、可拍摄、适合 AI 视频生成的分镜脚本。

## 核心原则
1. **开头3秒抓人**: 第一场必须出现强冲突、悬念、反常识结果、危险动作、关键物证或强情绪。
2. **短剧节奏感**: 中段快速推进冲突，结尾必须有反转、爽点、悬念、情绪释放或强记忆画面。
3. **竖屏拍摄**: 所有画面设计适配9:16竖屏，人物优先居中或上三分之二，下方留字幕空间。
4. **场景紧凑**: 每个场景控制在5-10秒优先；总场景数按原剧本节拍决定，5-20 个场景是系统容量范围，不得为满足数量合并或删减镜头；每个场景只承载一个核心动作。
5. **对话精炼**: 每句台词尽量不超过20-30字，用动作和画面讲故事优先于对白解释。
6. **视觉化思维**: 把心理活动转化为表情、停顿、视线、手部动作、道具互动、环境变化。
7. **可生成性**: 避免过度复杂特效、超大群戏、难以稳定生成的抽象画面。

## 场景必须包含的生产信息
- 具体地点：如“雨夜小区门口”“深夜医院走廊”“黄昏教学楼天台”
- 时间和光线：清晨/黄昏/深夜/雨夜/冷白灯/暖色夕阳/霓虹反光
- 核心动作：谁做了什么，动作如何开始、推进、结束
- 关键道具示例（仅供理解，不得照抄）：手机、照片、钥匙、合同、伞、血迹、耳钉等；只有当前剧本明确满足实体、跨镜头可见或使用、以及剧情影响条件时才可列入，不能因为示例词出现就创建道具
- 画面结束状态：角色停在什么姿态、道具暴露了什么、下一场如何承接

## 地点与动作必须分离
- `location` 只写稳定的物理地点身份和必要的室内/室外边界，例如“旧钟楼地下室”“便利店外部”；不能写“某人走向电箱”“拧开铁门”“停在门前”“众人消失后”等镜头动作或结果。
- `action_description` 才写人物移动、操作、姿态、进出、打斗、物体掉落和镜头结束瞬时状态；这些内容不得复制到地点、固定陈设或可复用场景描述。
- 固定陈设只写跨相关镜头持续存在的建筑结构、空间比例、家具和不可移动锚点；时间、天气、光线变化、积水反光和人物动作属于场景状态或镜头动作。

## 镜头指导
- 特写(close-up): 表现情绪、眼神、手部、关键物品
- 中景(medium shot): 对话、人物互动、动作变化
- 远景/全景(wide shot): 环境建立、空间关系、大场面
- 运镜可使用: slow dolly in, slow dolly out, slow pan, tracking shot, handheld follow

## 题材策略
- 悬疑反转: 冷色调、高对比阴影、线索特写、慢推镜、安静压迫感
- 都市甜宠: 柔光、暖色、浅景深、眼神交流、手部细节
- 逆袭打脸: 低角度、强对比、关键物证揭示、决断动作、旁人反应
- 古装/穿越: 服饰层次、发饰、庭院纵深、灯笼光、雾气、袖摆动作
- 家庭伦理: 餐桌距离、室内暖光、克制表情、旧物特写、沉默停顿

## 重要约束
- scene_id 从1开始递增
- duration_sec 每个场景3-30秒，总和不超过180秒
- location 要具体，不能只写“室内”“街上”
- action_description 要视觉化、可拍摄、可生成
- camera_direction 要包含景别或运镜
- emotion_tone 要能指导后续视觉风格
- transition_from_prev 从第二个场景开始统一填写 cut；镜头连续性通过尾帧/首帧承接和参考素材处理，不使用装饰性转场
- continuity_from_prev 只在同一时空的连续动作、同一段镜头拆分时为 true；时间跳转、地点变化、叙事切换必须为 false
- continuity_reason 简短说明是否需要把上一镜末帧作为本镜首帧参考"""


class _CompactPropCandidate(BaseModel):
    """Small optional contract for novel props in compact script responses."""

    name: str = Field(description="Canonical physical prop name")
    description: str = Field(default="", description="Visible material, form, and distinctive marks")
    critical_reason: str = Field(default="", description="plot, continuity, action, handoff, or identity")
    critical_evidence: str = Field(default="", description="Why it affects plot or visual continuity")
    shot_ids: list[int] = Field(default_factory=list, description="At least two shots where it is visible or used")
    aliases: list[str] = Field(default_factory=list, description="Alternate names appearing in the script")


class _ScriptOnlyBreakdown(BaseModel):
    """Kimi-oriented compact script schema without the large asset manifest tree."""

    title: str = Field(description="Drama title")
    genre: str = Field(description="Genre: 都市, 古装, 悬疑, 甜宠, 逆袭, etc.")
    logline: str = Field(description="One-sentence summary of the story")
    duration_sec: int = Field(description="Estimated total duration in seconds")
    narration_style: str = Field(description="Narration style")
    target_audience: str = Field(default="通用", description="Target audience")
    scenes: list[Scene] = Field(description="Ordered list of scenes", min_length=1, max_length=20)
    critical_props: list[_CompactPropCandidate] = Field(
        default_factory=list,
        description="Optional novel physical props that pass the two-shot and narrative-evidence rule",
    )


class ScriptAgent:
    """Agent that breaks a story into structured shooting script.

    This is the first agent in the pipeline. It takes raw user text and
    produces a ScriptBreakdown with detailed scenes, dialogue, and camera directions.
    """

    def __init__(self):
        self.llm = get_llm_client()

    async def run(
        self,
        user_input: str,
        input_mode: str = "idea",
        creative_profile: CreativeProfile | None = None,
        telemetry: dict | None = None,
    ) -> ScriptBreakdown:
        """Convert user story input into a structured script breakdown.

        Args:
            user_input: Raw text — can be a full story, a scene idea,
                        a premise, or even just a genre request.

        Returns:
            A structured ScriptBreakdown ready for the Visual Design Agent.
        """
        profile = creative_profile or CreativeProfile()
        if input_mode == "script":
            return await self.analyze_existing_script(user_input, profile, telemetry=telemetry)

        user_prompt = f"""请将以下内容扩写并改编为短剧分镜脚本：

---
{user_input}
---

请输出完整的分镜脚本，包含：
- 吸引人的短剧标题(title)
- 准确的类型标签(genre)
- 一句话梗概(logline)
- 预估总时长(duration_sec)
- 旁白风格(narration_style)
- 目标受众(target_audience)
- 按原剧本节拍输出详细场景(scenes)，通常在5-20个容量范围内；每个包含具体地点、角色、短句台词、可视化动作、镜头指导、情绪基调、转场承接，不得为满足数量合并或删减镜头

额外要求：
- 第一场必须有强钩子
- 每个场景只写一个核心动作
- 动作描述要包含开始、推进、结束状态
- 每个场景都要包含可生成的视觉信息：地点、时间、光线、关键道具、人物表情或肢体动作"""
        user_prompt += (
            f"\n\n项目创作约束：类型：{'、'.join(profile.genre_tags) or '未指定'}；"
            f"艺术风格：{profile.art_style}；最终视频画幅：{profile.video_ratio}。"
            "分镜视觉设计必须服从这些约束。"
        )

        return await self._generate_validated(
            user_prompt, temperature=0.8, telemetry=telemetry, phase="script.generate",
        )

    async def analyze_existing_script(
        self,
        source_text: str,
        creative_profile: CreativeProfile | None = None,
        telemetry: dict | None = None,
    ) -> ScriptBreakdown:
        """Faithfully convert an existing screenplay into production structure."""
        profile = creative_profile or CreativeProfile()
        user_prompt = f"""请忠实理解并结构化以下已有剧本，不要扩写、删改核心剧情、改写结局或加入原稿没有的角色与事件。

--- 原始剧本开始 ---
{source_text}
--- 原始剧本结束 ---

请输出适用于后续短剧生产的结构化脚本：标题、类型、梗概、时长、旁白风格、受众，以及按原稿顺序拆分的场景。
每个场景必须保留原有角色、对话、关键动作、地点、情绪和时间线；镜头描述只用于将原稿已有内容可视化，不能改变剧情。"""
        user_prompt += (
            f"\n项目视觉约束：艺术风格为{profile.art_style}，最终视频画幅为{profile.video_ratio}。"
            "不改动剧情，但所有镜头语言和构图必须符合该约束。"
        )
        return await self._generate_validated(
            user_prompt, temperature=0.2, telemetry=telemetry, phase="script.analyze",
        )

    async def generate_draft(
        self,
        idea: str,
        creative_profile: CreativeProfile | None = None,
        details: dict | None = None,
    ) -> str:
        """Expand an idea into an editable readable screenplay, not production data."""
        profile = creative_profile or CreativeProfile()
        extra = details or {}
        constraints = "\n".join(
            f"{label}：{str(extra.get(key) or '').strip()}"
            for key, label in (("characters", "人物"), ("conflict", "核心冲突"), ("duration", "目标时长"), ("ending", "结局方向"))
            if str(extra.get(key) or "").strip()
        )
        prompt = f"""根据以下创意写出完整、可阅读、可直接编辑的中文短剧正文。

创意：{idea}
项目类型：{'、'.join(profile.genre_tags) or '未指定'}
视觉风格：{profile.art_style}
画幅：{profile.video_ratio}
{constraints}

请只输出剧本正文，不输出 JSON、资产清单、镜头编号说明、创作分析或 Markdown 解释。正文应包含场景、人物动作和对白，首段需要明确的剧情钩子。"""
        return (await self.llm.generate_text(
            SYSTEM_PROMPT, prompt, temperature=0.8, phase="script.draft.generate",
        )).strip()

    async def revise_draft(self, draft_text: str, instruction: str) -> str:
        """Rewrite an editable readable screenplay while preserving unspecified content."""
        prompt = f"""请根据用户要求修改以下中文短剧正文。

用户要求：{instruction}

当前正文：
---
{draft_text}
---

只输出修改后的完整剧本正文，不输出 JSON、分析、解释或 Markdown 包装。保留用户未要求修改的剧情、角色和对白。"""
        return (await self.llm.generate_text(
            SYSTEM_PROMPT, prompt, temperature=0.45, phase="script.draft.revise",
        )).strip()

    async def revise_structure(self, script: ScriptBreakdown, instruction: str) -> ScriptBreakdown:
        """Apply a user instruction to the structured result while keeping a valid schema."""
        user_prompt = f"""用户希望修改已结构化的剧本结果。

用户要求：{instruction}

当前结构化剧本：
{json.dumps(script.model_dump(), ensure_ascii=False, indent=2)}

请执行用户要求，输出完整的结构化剧本。保留用户没有要求修改的剧情、角色、场景顺序和生产字段；scene_id 必须从 1 连续递增。"""
        return await self._generate_validated(user_prompt, temperature=0.4)

    async def _generate_validated(
        self,
        user_prompt: str,
        temperature: float,
        telemetry: dict | None = None,
        phase: str = "script.generate",
    ) -> ScriptBreakdown:
        """Generate the script and repair one semantically invalid manifest response."""
        if getattr(self.llm, "provider", "") in {"kimi", "deepseek"}:
            return await self._generate_compact(user_prompt, temperature, telemetry, phase)

        prompt = user_prompt + VISUAL_ASSET_RULES
        last_error: ValueError | None = None
        total_attempts = 0
        total_repairs = 0
        semantic_retries = 0
        for attempt in range(2):
            request_telemetry: dict = {}
            script = await self.llm.generate_structured(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
                output_schema=ScriptBreakdown,
                temperature=temperature,
                telemetry=request_telemetry,
                phase=phase,
            )
            total_attempts += int(request_telemetry.get("attempts", 0))
            total_repairs += int(request_telemetry.get("repair_attempts", 0))
            if script.asset_manifest is None and attempt == 0:
                last_error = ValueError("剧本解析未返回视觉资产清单，请重试")
                semantic_retries += 1
                prompt += (
                    "\n\n上一次输出漏掉了必须的 asset_manifest。请不要改写标题、剧情、"
                    "分镜顺序、台词或时长，只补全视觉资产清单：characters、locations、props "
                    "和 shot_bindings。每个分镜必须刚好有一个地点绑定。"
                )
                continue
            try:
                validated = self._validate_visual_assets(script, require_manifest=True)
                if telemetry is not None:
                    telemetry.update(request_telemetry)
                    telemetry["attempts"] = total_attempts
                    telemetry["repair_attempts"] = total_repairs
                    telemetry["semantic_retries"] = semantic_retries
                return validated
            except ValueError as exc:
                last_error = exc
                if attempt == 0:
                    semantic_retries += 1
                    prompt += (
                        "\n\n上一次输出的视觉资产清单未通过生产校验："
                        f"{exc}。请保留剧本剧情与所有分镜，仅修正 asset_manifest，"
                        "确保每个分镜恰有一个地点绑定，且只有 on_screen 角色出现在画面角色列表。"
                    )
        if telemetry is not None:
            telemetry.update({
                "attempts": total_attempts,
                "repair_attempts": total_repairs,
                "semantic_retries": semantic_retries,
            })
        raise last_error or ValueError("视觉资产清单生成失败")

    async def _generate_compact(
        self,
        user_prompt: str,
        temperature: float,
        telemetry: dict | None = None,
        phase: str = "script.generate",
    ) -> ScriptBreakdown:
        """Keep plain-JSON providers on a small first-stage contract."""
        prompt = user_prompt + """

请只输出短剧结构化分镜，不要输出 asset_manifest。
后端会根据 scenes 本地生成视觉资产清单；你只需要保证每个场景的地点、角色、动作、镜头、对白和连续性字段完整。
scene_id 必须从 1 连续递增，scenes 控制在 5-20 个容量范围内；必须保留输入剧本的全部有效节拍，不得为满足数量合并或删减镜头。
location 只写稳定的物理地点身份和室内/室外边界，不得包含人物动作、操作结果、镜头机位或瞬时状态；人物走向、停下、开门、拿取、站起、离开和消失等全部写入 action_description。"""
        prompt += """
可选输出 critical_props：仅列出剧本中实际可见、至少在两个分镜出现或被使用、并直接承担线索/证据/身份/交接/动作连续性的实体道具。普通物品、家具、背景陈设、短信/录音内容和只出现一次的物品不要列出。每项必须给出规范名称、可见外观、critical_reason、critical_evidence、shot_ids；不要为了凑数量添加道具。"""
        last_error: ValueError | None = None
        total_attempts = 0
        total_repairs = 0
        semantic_retries = 0
        for attempt in range(2):
            request_telemetry: dict = {}
            compact = await self.llm.generate_structured(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=prompt,
                output_schema=_ScriptOnlyBreakdown,
                temperature=temperature,
                telemetry=request_telemetry,
                phase=phase,
            )
            total_attempts += int(request_telemetry.get("attempts", 0))
            total_repairs += int(request_telemetry.get("repair_attempts", 0))
            script = ScriptBreakdown(
                title=compact.title,
                genre=compact.genre,
                logline=compact.logline,
                duration_sec=compact.duration_sec,
                narration_style=compact.narration_style,
                target_audience=compact.target_audience,
                scenes=compact.scenes,
                asset_manifest=None,
            )
            # Preserve the compact candidate list only long enough for the
            # deterministic manifest builder; it is never stored as a second
            # source of truth in the project state.
            critical_props = list(compact.critical_props or [])
            script.asset_manifest = build_manifest_from_scenes(script, critical_props=critical_props)
            try:
                validated = self._validate_visual_assets(script, require_manifest=True)
                if telemetry is not None:
                    telemetry.update(request_telemetry)
                    telemetry["attempts"] = total_attempts
                    telemetry["repair_attempts"] = total_repairs
                    telemetry["semantic_retries"] = semantic_retries
                    telemetry["compact_script_generation"] = True
                return validated
            except ValueError as exc:
                last_error = exc
                if attempt == 0:
                    semantic_retries += 1
                    prompt += (
                        "\n\n上一次分镜结构未通过本地资产清单校验："
                        f"{exc}。请只修正 scenes 的地点、角色、动作和连续性字段，"
                        "不要输出 asset_manifest，不要改变核心剧情。"
                    )
        if telemetry is not None:
            telemetry.update({
                "attempts": total_attempts,
                "repair_attempts": total_repairs,
                "semantic_retries": semantic_retries,
                "compact_script_generation": True,
            })
        raise last_error or ValueError("Kimi 紧凑剧本解析失败")

    @staticmethod
    def _validate_visual_assets(script: ScriptBreakdown, require_manifest: bool) -> ScriptBreakdown:
        # Preserve every LLM-produced beat while enforcing the video pipeline's
        # practical minimum duration. This is a deterministic repair, not a
        # narrative rewrite.
        for scene in script.scenes:
            scene.duration_sec = max(3, min(30, int(scene.duration_sec)))
            # New script parses use hard cuts. Keep the duration field within
            # the legacy schema range so old serialized projects remain valid.
            scene.transition_from_prev = "cut"
            scene.transition_duration_sec = max(0.15, min(0.8, float(scene.transition_duration_sec or 0.25)))
        if require_manifest and script.asset_manifest is None:
            script.asset_manifest = build_manifest_from_scenes(script)
        repair_asset_manifest(script)
        prune_nonessential_props(script)
        reconcile_prop_bindings(script)
        errors = validate_asset_manifest(script)
        if errors:
            raise ValueError("视觉资产清单不完整：" + "；".join(errors[:5]))
        return script
