"""Quality Review Agent — reviews generated videos and decides pass/fail/retry."""

from src.schemas.script import ScriptBreakdown
from src.schemas.visual import VisualDesign
from src.schemas.assets import GeneratedAssets
from src.schemas.review import ReviewResult, Issue
from src.utils.llm import get_llm_client

SYSTEM_PROMPT = """你是一个专业的视频质量审核员。你的任务是审核AI生成的短视频是否符合剧本和视觉设计要求。

## 审核维度

### 1. 内容一致性 (content)
- 画面内容是否匹配剧本的动作描述？
- 人物、场景是否符合描述？
- 是否有不应该出现的元素？

### 2. 视觉质量 (quality)
- 画面是否模糊、变形、有伪影？
- 光照是否合理？
- 色彩是否符合视觉设计要求？

### 3. 角色一致性 (consistency)
- 同一角色在不同镜头中是否保持一致？
- 场景之间的视觉风格是否统一？

### 4. 时长 (timing)
- 实际时长是否在目标时长的±30%范围内？

## 评分标准
- 0.8-1.0: 优秀，可以直接使用
- 0.6-0.8: 良好，有小问题但不影响使用
- 0.4-0.6: 一般，有明显问题需要修改
- 0.0-0.4: 差，必须重新生成

## 通过条件
- 无 critical 级别问题
- overall_score >= 0.6

## 注意
- 由于我们无法真正"看到"视频，请基于剧本和视觉设计要求进行文本层面的审核
- 重点检查逻辑一致性和明显的描述偏差
- 给出具体、可操作的修改建议"""


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

请从内容一致性、视觉质量、角色一致性、时长四个维度审核，给出评分和问题列表。"""

        result = await self.llm.generate_structured(
            system_prompt=SYSTEM_PROMPT,
            user_prompt=user_prompt,
            output_schema=ReviewResult,
            temperature=0.3,  # Low temperature for objective review
        )

        # Add automated checks
        auto_issues = self._automated_checks(script, assets)
        result.issues.extend(auto_issues)

        # Recalculate score if automated checks found new issues
        if auto_issues and result.passed:
            has_critical = any(i.severity == "critical" for i in auto_issues)
            if has_critical:
                result.passed = False
                result.overall_score = min(result.overall_score, 0.5)

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
