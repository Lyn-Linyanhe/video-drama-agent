"""Quality Review Agent — reviews generated videos and decides pass/fail/retry."""

from src.schemas.script import ScriptBreakdown
from src.schemas.visual import VisualDesign
from src.schemas.assets import GeneratedAssets
from src.schemas.review import ReviewResult, Issue
from src.services.media_quality import review_video_subjective
from src.utils.llm import get_llm_client

SYSTEM_PROMPT = """你是一个专业的视频质量审核员。你的任务是审核 AI 生成的短视频是否符合剧本、视觉设计、角色一致性和短剧成片效果。

## 审核维度

### 1. 内容一致性 (content)
- 画面内容是否匹配剧本的动作描述？
- 人物、场景是否符合描述？
- 是否遗漏关键剧情动作、关键道具或关键物证？
- 是否有不应该出现的元素？

### 2. 视觉质量 (quality)
- 画面是否模糊、变形、有伪影？
- 人脸、手部、肢体是否自然？
- 光照是否合理？
- 色彩是否符合视觉设计要求？

### 3. 角色一致性 (consistency)
- 同一角色在不同镜头中是否保持一致？
- 是否保持脸型、发型、服装主色、标志物和年龄感一致？
- 是否出现换脸、换衣、发型漂移、体型变化？
- 场景之间的视觉风格是否统一？

### 4. 镜头与动作 (timing)
- 实际时长是否在目标时长的±30%范围内？
- 动作是否有连续推进，而不是突然跳变？
- 运镜是否符合 prompt 中的景别、角度和运动要求？

### 5. 短剧效果 (drama)
- 开头是否有钩子？
- 情绪是否递进？
- 结尾是否有反转、爽点、悬念或强记忆画面？

## 评分标准
- 0.8-1.0: 优秀，可以直接使用
- 0.6-0.8: 良好，有小问题但不影响使用
- 0.4-0.6: 一般，有明显问题需要修改
- 0.0-0.4: 差，必须重新生成

## 通过条件
- 无 critical 级别问题
- overall_score >= 0.6

## 注意
- 如果用户提供了视频帧、联系表或视觉审核结果，必须把它们作为主要证据；不要只根据文字设想画面
- 未提供视觉证据时，才基于剧本和视觉设计要求进行文本层面的审核
- 重点检查逻辑一致性、角色一致性、时长偏差和明显的描述偏差
- 给出具体、可操作的修改建议，例如“补充角色服装锚点”而不是“优化质量”"""


class ReviewAgent:
    """Agent that reviews generated video assets for quality.

    This agent acts as the quality gate in the pipeline. If review fails,
    the orchestrator routes back to the Prompt Engineering Agent for retry.
    """

    def __init__(self):
        self.llm = get_llm_client()

    async def run(
        self,
        script: ScriptBreakdown,
        visual: VisualDesign,
        assets: GeneratedAssets,
    ) -> ReviewResult:
        """Review generated video assets against the script and visual design.

        Args:
            script: The original script breakdown.
            visual: The visual design specification.
            assets: The generated video assets to review.

        Returns:
            A ReviewResult indicating pass/fail and listing issues.
        """
        # Build a detailed review request
        scene_summaries = self._build_scene_summaries(script, visual, assets)

        user_prompt = f"""请审核以下AI生成视频短剧的质量：

**剧本**: {script.title}
**类型**: {script.genre}
**视觉风格**: {visual.overall_style}

**各场景详情**:
{scene_summaries}

**生成统计**:
- 总场景数: {len(assets.shots)}
- 成功生成: {sum(1 for s in assets.shots if s.success)}
- 失败: {sum(1 for s in assets.shots if not s.success)}

请从内容一致性、视觉质量、角色一致性、镜头动作、短剧效果五个维度审核，给出评分和问题列表。"""

        result = await self.llm.generate_structured(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            output_schema=ReviewResult,
            temperature=0.3,  # Low temperature for objective review
        )

        # Add automated checks
        auto_issues = self._automated_checks(script, assets)
        result.issues.extend(auto_issues)

        # Add vision-backed checks from sampled video frames when local files exist.
        visual_issues = await self._visual_video_checks(script, visual, assets)
        result.issues.extend(visual_issues)

        # Recalculate score if automated checks found new issues
        added_issues = auto_issues + visual_issues
        if added_issues:
            has_critical = any(i.severity == "critical" for i in added_issues)
            has_major = any(i.severity == "major" for i in added_issues)
            if has_critical:
                result.passed = False
                result.overall_score = min(result.overall_score, 0.5)
            elif has_major:
                result.overall_score = min(result.overall_score, 0.68)
                result.passed = result.overall_score >= 0.6

        if visual_issues:
            result.summary = (result.summary + "\n" if result.summary else "") + (
                f"视频视觉审核发现 {len(visual_issues)} 个问题。"
            )

        return result

    def _build_scene_summaries(
        self,
        script: ScriptBreakdown,
        visual: VisualDesign,
        assets: GeneratedAssets,
    ) -> str:
        """Build scene summaries for the review prompt."""
        visual_map = {v.scene_id: v for v in visual.scenes}
        asset_map = {a.scene_id: a for a in assets.shots}

        lines = []
        for scene in script.scenes:
            asset = asset_map.get(scene.scene_id)
            vis = visual_map.get(scene.scene_id)

            status = "✅ 已生成" if (asset and asset.success) else "❌ 生成失败"
            lines.append(f"--- 场景 {scene.scene_id} {status} ---")
            lines.append(f"剧本要求: {scene.action_description}")
            lines.append(f"目标时长: {scene.duration_sec}s")
            if asset and asset.success:
                lines.append(f"实际时长: {asset.duration_sec:.1f}s")
                lines.append(f"使用的API: {asset.api_used}")
            if vis:
                lines.append(f"设计要求: 构图-{vis.composition}, 色调-{vis.color_tone}")
            lines.append("")
        return "\n".join(lines)

    async def _visual_video_checks(
        self,
        script: ScriptBreakdown,
        visual: VisualDesign,
        assets: GeneratedAssets,
    ) -> list[Issue]:
        """Review actual generated clips by sampling video frames with a vision model."""
        issues: list[Issue] = []
        visual_map = {v.scene_id: v for v in visual.scenes}
        scene_map = {s.scene_id: s for s in script.scenes}

        for asset in assets.shots:
            if not asset.success:
                continue

            scene = scene_map.get(asset.scene_id)
            if scene is None:
                continue

            if not asset.local_path:
                issues.append(Issue(
                    scene_id=asset.scene_id,
                    severity="major",
                    type="quality",
                    description=f"场景 {asset.scene_id} 缺少本地视频文件，无法执行真实画面审核",
                    suggestion="请确保视频生成客户端下载并保存 local_path 后再进入审核",
                ))
                continue

            expected_prompt = self._build_visual_review_prompt(scene, visual_map.get(asset.scene_id))
            review = await review_video_subjective(asset.local_path, expected_prompt)
            status = review.get("status")

            if status == "skipped":
                issues.append(Issue(
                    scene_id=asset.scene_id,
                    severity="major",
                    type="quality",
                    description=f"场景 {asset.scene_id} 未执行真实画面审核: {review.get('reason', '视觉模型未启用')}",
                    suggestion="请切换到支持视觉输入的审核模型，例如 OpenAI gpt-4o、gpt-4.1 或 gpt-5 系列",
                ))
                continue

            for index, problem in enumerate(review.get("issues", []), start=1):
                issues.append(Issue(
                    scene_id=asset.scene_id,
                    severity="major",
                    type="quality",
                    description=f"场景 {asset.scene_id} 视频画面问题 {index}: {problem}",
                    suggestion="请根据该问题调整镜头 prompt、参考图或重新生成该镜头",
                ))

        return issues

    @staticmethod
    def _build_visual_review_prompt(scene, visual_scene) -> str:
        """Build the expected visual content prompt used by frame-based review."""
        parts = [
            f"场景ID: {scene.scene_id}",
            f"地点: {scene.location}",
            f"角色: {', '.join(scene.characters)}",
            f"剧情动作: {scene.action_description}",
            f"镜头要求: {scene.camera_direction}",
            f"情绪基调: {scene.emotion_tone}",
            f"目标时长: {scene.duration_sec}s",
        ]
        if getattr(scene, "dialogue", None):
            dialogue = " / ".join(f"{line.character}: {line.line}" for line in scene.dialogue)
            parts.append(f"台词: {dialogue}")
        if visual_scene:
            parts.extend([
                f"构图: {visual_scene.composition}",
                f"光照: {visual_scene.lighting}",
                f"色调: {visual_scene.color_tone}",
                f"关键元素: {', '.join(visual_scene.key_elements)}",
            ])
            if visual_scene.character_appearance:
                appearance = "; ".join(
                    f"{name}: {desc}" for name, desc in visual_scene.character_appearance.items()
                )
                parts.append(f"角色外观: {appearance}")
        return "\n".join(parts)

    def _automated_checks(
        self, script: ScriptBreakdown, assets: GeneratedAssets
    ) -> list[Issue]:
        """Perform automated rule-based checks (no LLM needed)."""
        issues = []
        asset_map = {a.scene_id: a for a in assets.shots}

        for scene in script.scenes:
            asset = asset_map.get(scene.scene_id)

            # Check if scene was generated at all
            if asset is None:
                issues.append(Issue(
                    scene_id=scene.scene_id,
                    severity="critical",
                    type="content",
                    description=f"场景 {scene.scene_id} 未生成（不在资源列表中）",
                    suggestion="请重新生成该场景",
                ))
                continue

            # Check if generation failed
            if not asset.success:
                issues.append(Issue(
                    scene_id=scene.scene_id,
                    severity="critical",
                    type="quality",
                    description=f"场景 {scene.scene_id} 生成失败: {asset.error_message}",
                    suggestion="请检查API配置或prompt后重新生成",
                ))
                continue

            # Check duration deviation (>50% off)
            if asset.duration_sec > 0 and scene.duration_sec > 0:
                deviation = abs(asset.duration_sec - scene.duration_sec) / scene.duration_sec
                if deviation > 0.5:
                    issues.append(Issue(
                        scene_id=scene.scene_id,
                        severity="minor",
                        type="timing",
                        description=f"场景 {scene.scene_id} 时长偏差 {deviation:.0%}",
                        suggestion=f"目标 {scene.duration_sec}s，实际 {asset.duration_sec:.1f}s，可在合成时调整",
                    ))

        # Check for overall success rate
        success_count = sum(1 for a in assets.shots if a.success)
        if success_count == 0:
            issues.append(Issue(
                scene_id=0,
                severity="critical",
                type="quality",
                description="所有场景生成失败",
                suggestion="请检查API密钥、网络连接和prompt质量",
            ))

        return issues
