"""Kimi-powered text review for generated storyboard prompts."""

from __future__ import annotations

import json
import os
import re
from typing import Any

from src.schemas.prompt import GenerationPrompts, PromptPolicy
from src.schemas.prompt_review import (
    PromptReviewIssue,
    PromptReviewResult,
    PromptReviewShot,
)
from src.schemas.script import ScriptBreakdown
from src.schemas.visual import VisualDesign
from src.utils.visual_asset_manifest import binding_for_scene


SYSTEM_PROMPT = """你是短剧分镜提示词审核总监。你只审核文本，不生成或改写提示词。

目标：逐镜核对“结构化剧本”和“视频生成提示词”，判断提示词是否完整、准确、可执行地体现该镜头剧情，并检查全片连续性。

审核标准：
1. 覆盖度：地点、可见角色、关键道具、动作、台词对应的表演或口型意图、情绪、镜头语言、时长是否被提示词体现。
2. 忠实度：不得混入其他镜头事件、提前泄露后续剧情、遗漏反转或关键行为；角色和道具必须符合资产绑定。
3. 可执行性：提示词应明确主体、动作先后、空间、光线/氛围、镜头构图或运动；避免“按剧情推进”等空泛词。
4. 连续性：角色外观、场景身份、道具状态和承接镜头不能互相冲突。
5. 质量：中文表达明确，适合视频模型执行；不要因提示词没有不必要细节而扣分。

评分：90-100 可直接用于生成；75-89 有轻微优化项；60-74 有明显遗漏；0-59 不适合生成。
通过条件：总分 >= 75 且不存在 critical 问题。

每条问题必须给出具体证据和可直接执行的修正建议。没有问题时返回空数组。"""


class PromptReviewAgent:
    """Runs the dedicated Kimi audit without depending on the main script LLM."""

    provider = "kimi"
    DEFAULT_BATCH_SIZE = 4

    @staticmethod
    def _settings() -> tuple[str, str, str]:
        key = os.getenv("KIMI_PROMPT_REVIEW_API_KEY", "").strip()
        if not key and os.getenv("LLM_PROVIDER", "").strip().lower() == "kimi":
            key = os.getenv("OPENAI_API_KEY", "").strip()
        model = (
            os.getenv("KIMI_PROMPT_REVIEW_MODEL", "").strip()
            or (
                os.getenv("LLM_MODEL", "").strip()
                if os.getenv("LLM_PROVIDER", "").strip().lower() == "kimi"
                else ""
            )
            or "kimi-k2.6"
        )
        base_url = (
            os.getenv("KIMI_PROMPT_REVIEW_BASE_URL", "").strip()
            or "https://api.moonshot.cn/v1"
        )
        return key, model, base_url

    @classmethod
    def enabled(cls) -> bool:
        return os.getenv("KIMI_PROMPT_REVIEW_ENABLED", "true").strip().lower() not in {
            "0", "false", "no", "off",
        }

    @classmethod
    def is_configured(cls) -> bool:
        return bool(cls._settings()[0])

    @classmethod
    def configuration_error(cls) -> str:
        return "请在 API 设置的“分镜提示词审核（Kimi）”中配置 Kimi API Key"

    async def run(
        self,
        script: ScriptBreakdown,
        prompts: GenerationPrompts,
        visual_design: VisualDesign | None,
        prompt_policy: PromptPolicy,
    ) -> PromptReviewResult:
        key, model, base_url = self._settings()
        if not key:
            raise ValueError(self.configuration_error())

        from openai import AsyncOpenAI

        client = AsyncOpenAI(api_key=key, base_url=base_url, timeout=300, max_retries=0)
        batch_results: list[tuple[list[int], PromptReviewResult]] = []
        prompt_by_scene = {item.scene_id: item for item in prompts.shots}
        scenes = list(script.scenes)
        batch_size = self._batch_size()

        # Review batches serially so long projects do not create a single oversized
        # request or multiple concurrent provider requests.
        for start in range(0, len(scenes), batch_size):
            batch_scenes = scenes[start:start + batch_size]
            batch_ids = [scene.scene_id for scene in batch_scenes]
            payload = self._batch_payload(
                script=script,
                prompt_by_scene=prompt_by_scene,
                visual_design=visual_design,
                prompt_policy=prompt_policy,
                start=start,
                batch_size=batch_size,
            )
            user_prompt = self._batch_user_prompt(batch_ids, payload)
            response = await client.chat.completions.create(
                model=model,
                temperature=1,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
            )
            content = response.choices[0].message.content or ""
            batch_results.append((
                batch_ids,
                PromptReviewResult.model_validate(
                    self._normalize_review_payload(self._parse_json(content))
                ),
            ))

        result = self._merge_batch_results(batch_results)
        return self._apply_deterministic_checks(script, prompts, result)

    @classmethod
    def _batch_size(cls) -> int:
        try:
            requested = int(os.getenv("KIMI_PROMPT_REVIEW_BATCH_SIZE", str(cls.DEFAULT_BATCH_SIZE)))
        except ValueError:
            requested = cls.DEFAULT_BATCH_SIZE
        return max(1, min(requested, 8))

    @staticmethod
    def _scene_summary(scene) -> dict[str, Any]:
        return {
            "scene_id": scene.scene_id,
            "location": scene.location,
            "characters": scene.characters,
            "action_description": scene.action_description,
            "camera_direction": scene.camera_direction,
            "emotion_tone": scene.emotion_tone,
            "dialogue": [line.model_dump() for line in scene.dialogue],
            "continuity_from_prev": scene.continuity_from_prev,
            "continuity_reason": scene.continuity_reason,
        }

    @classmethod
    def _batch_payload(
        cls,
        script: ScriptBreakdown,
        prompt_by_scene: dict[int, Any],
        visual_design: VisualDesign | None,
        prompt_policy: PromptPolicy,
        start: int,
        batch_size: int,
    ) -> dict[str, Any]:
        target_scenes = script.scenes[start:start + batch_size]
        target_ids = {scene.scene_id for scene in target_scenes}
        relevant_characters = {
            character
            for scene in target_scenes
            for character in scene.characters
        }
        visual_by_scene = {
            item.scene_id: item
            for item in (visual_design.scenes if visual_design else [])
        }
        context_scenes = []
        if start:
            context_scenes.append(cls._scene_summary(script.scenes[start - 1]))
        if start + batch_size < len(script.scenes):
            context_scenes.append(cls._scene_summary(script.scenes[start + batch_size]))

        return {
            "review_scope": {
                "target_scene_ids": sorted(target_ids),
                "instruction": "只审核 target_scene_ids，context_scenes 仅用于判断承接关系。",
            },
            "project_style": {
                "title": script.title,
                "genre": script.genre,
                "logline": script.logline,
                "overall_style": visual_design.overall_style if visual_design else "",
                "color_palette": visual_design.color_palette if visual_design else "",
                "aspect_ratio": visual_design.aspect_ratio if visual_design else "",
                "character_anchors": {
                    name: description
                    for name, description in (
                        visual_design.global_character_descriptions_cn.items()
                        if visual_design
                        else []
                    )
                    if name in relevant_characters
                },
            },
            "prompt_policy": prompt_policy.model_dump(),
            "target_scenes": [
                {
                    "script": cls._scene_summary(scene),
                    "visual_design": visual_by_scene[scene.scene_id].model_dump()
                    if scene.scene_id in visual_by_scene
                    else None,
                    "generation_prompt": prompt_by_scene[scene.scene_id].model_dump()
                    if scene.scene_id in prompt_by_scene
                    else None,
                }
                for scene in target_scenes
            ],
            "context_scenes": context_scenes,
        }

    @staticmethod
    def _batch_user_prompt(batch_ids: list[int], payload: dict[str, Any]) -> str:
        return (
            "请审核以下分镜批次。返回严格 JSON，不要使用 Markdown 或额外说明。\n"
            f"本批只审核镜头：{','.join(str(scene_id) for scene_id in batch_ids)}。"
            "shots 只能包含这些 scene_id；context_scenes 仅用于连续性判断。\n"
            "JSON schema:\n"
            "{\"overall_score\":0,\"passed\":false,\"summary\":\"\","
            "\"shots\":[{\"scene_id\":1,\"score\":0,\"passed\":false,\"summary\":\"\","
            "\"issues\":[{\"scene_id\":1,\"severity\":\"major\",\"category\":\"coverage\","
            "\"description\":\"\",\"suggestion\":\"\",\"evidence\":\"\"}]}],"
            "\"issues\":[]}\n"
            "category 只能是 coverage、fidelity、clarity、continuity、executability、quality 之一。\n\n"
            f"项目数据:\n{json.dumps(payload, ensure_ascii=False)}"
        )

    @staticmethod
    def _merge_batch_results(
        batch_results: list[tuple[list[int], PromptReviewResult]],
    ) -> PromptReviewResult:
        shots: list[PromptReviewShot] = []
        issues: list[PromptReviewIssue] = []
        summaries: list[str] = []

        for expected_ids, batch in batch_results:
            by_scene = {shot.scene_id: shot for shot in batch.shots}
            for scene_id in expected_ids:
                shot = by_scene.get(scene_id)
                if shot:
                    shots.append(shot)
                    continue
                shots.append(PromptReviewShot(
                    scene_id=scene_id,
                    score=0,
                    passed=False,
                    summary="Kimi 未返回该镜头的审核结果。",
                    issues=[PromptReviewIssue(
                        scene_id=scene_id,
                        severity="major",
                        category="quality",
                        description="该镜头未包含在本批 Kimi 审核结果中。",
                        suggestion="重新检验该项目；若持续发生，请减小审核批次。",
                    )],
                ))
            issues.extend(batch.issues)
            if batch.summary:
                summaries.append(batch.summary)

        overall_score = round(sum(shot.score for shot in shots) / len(shots)) if shots else 0
        has_critical = any(
            issue.severity == "critical"
            for issue in issues + [issue for shot in shots for issue in shot.issues]
        )
        return PromptReviewResult(
            overall_score=overall_score,
            passed=overall_score >= 75 and not has_critical and all(shot.passed for shot in shots),
            summary="；".join(summaries)[:2000],
            shots=shots,
            issues=issues,
        )

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        text = content.strip()
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
        if fenced:
            text = fenced.group(1).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", text)
            if match:
                return json.loads(match.group())
        raise ValueError("Kimi 未返回可解析的分镜审核 JSON")

    @staticmethod
    def _normalize_review_payload(payload: dict[str, Any]) -> dict[str, Any]:
        """Keep provider wording differences from failing the whole review job."""
        category_aliases = {
            "coverage": "coverage",
            "completeness": "coverage",
            "完整性": "coverage",
            "fidelity": "fidelity",
            "accuracy": "fidelity",
            "faithfulness": "fidelity",
            "忠实度": "fidelity",
            "clarity": "clarity",
            "readability": "clarity",
            "清晰度": "clarity",
            "continuity": "continuity",
            "consistency": "continuity",
            "连贯性": "continuity",
            "连续性": "continuity",
            "executability": "executability",
            "feasibility": "executability",
            "implementability": "executability",
            "practicality": "executability",
            "execution": "executability",
            "可执行性": "executability",
            "可行性": "executability",
            "quality": "quality",
            "technical_quality": "quality",
            "整体质量": "quality",
            "质量": "quality",
        }

        def normalize_issue(issue: Any) -> None:
            if not isinstance(issue, dict):
                return
            raw_category = str(issue.get("category") or "").strip().lower()
            raw_category = raw_category.replace("-", "_").replace(" ", "_")
            issue["category"] = category_aliases.get(raw_category, "quality")

        normalized = dict(payload)
        for issue in normalized.get("issues") or []:
            normalize_issue(issue)
        for shot in normalized.get("shots") or []:
            if not isinstance(shot, dict):
                continue
            for issue in shot.get("issues") or []:
                normalize_issue(issue)
        return normalized

    @staticmethod
    def _continuity_issues(script: ScriptBreakdown, prompts: GenerationPrompts) -> list[PromptReviewIssue]:
        """Deterministic structural gate for shots explicitly marked as continuing the previous one.

        Only scenes with ``continuity_from_prev=True`` are checked so adjacent shots that
        simply happen to share a location are never flagged. Rules stay at the schema level
        (location binding, prop binding) to avoid unreliable free-text semantic guessing.
        """
        issues: list[PromptReviewIssue] = []
        manifest = getattr(script, "asset_manifest", None)
        scenes = list(script.scenes)
        index_by_id = {scene.scene_id: idx for idx, scene in enumerate(scenes)}
        for scene in scenes:
            if not getattr(scene, "continuity_from_prev", False):
                continue
            idx = index_by_id.get(scene.scene_id)
            if idx is None:
                continue
            reason = str(getattr(scene, "continuity_reason", "") or "")
            if idx == 0:
                issues.append(PromptReviewIssue(
                    scene_id=scene.scene_id,
                    severity="critical",
                    category="continuity",
                    description=f"镜头 {scene.scene_id} 是首镜，不能标记为承接上一镜",
                    suggestion="取消该镜头的承接标记，或调整镜头顺序",
                    evidence=reason,
                ))
                continue
            prev = scenes[idx - 1]
            if manifest is None:
                continue
            cur_binding = binding_for_scene(script, scene.scene_id)
            prev_binding = binding_for_scene(script, prev.scene_id)
            if cur_binding and prev_binding and cur_binding.location_id != prev_binding.location_id:
                issues.append(PromptReviewIssue(
                    scene_id=scene.scene_id,
                    severity="critical",
                    category="continuity",
                    description=(
                        f"镜头 {scene.scene_id} 标记承接上一镜，但地点不同"
                        f"（{prev_binding.location_id} → {cur_binding.location_id}）"
                    ),
                    suggestion="承接镜头必须与上一镜处于同一地点，或取消承接标记",
                    evidence=reason,
                ))
            prev_props = set(prev_binding.prop_ids or []) if prev_binding else set()
            cur_props = set(cur_binding.prop_ids or []) if cur_binding else set()
            if prev_props and not cur_props:
                issues.append(PromptReviewIssue(
                    scene_id=scene.scene_id,
                    severity="major",
                    category="continuity",
                    description=(
                        f"镜头 {scene.scene_id} 承接上一镜，但未绑定上一镜关键道具"
                        f"（{', '.join(sorted(prev_props))}）"
                    ),
                    suggestion="为承接镜头保留上一镜关键道具绑定，或说明道具已移出画面",
                    evidence=", ".join(sorted(prev_props)),
                ))
        return issues

    @staticmethod
    def _apply_deterministic_checks(
        script: ScriptBreakdown,
        prompts: GenerationPrompts,
        result: PromptReviewResult,
    ) -> PromptReviewResult:
        prompt_ids = {item.scene_id for item in prompts.shots}
        scene_ids = {item.scene_id for item in script.scenes}
        issues = list(result.issues)
        issues.extend(PromptReviewAgent._continuity_issues(script, prompts))
        for scene in script.scenes:
            if scene.scene_id not in prompt_ids:
                issues.append(PromptReviewIssue(
                    scene_id=scene.scene_id,
                    severity="critical",
                    category="coverage",
                    description=f"镜头 {scene.scene_id} 缺少视频生成提示词",
                    suggestion="为该镜头补充完整中文提示词后重新审核",
                    evidence=scene.action_description,
                ))
        for extra_id in sorted(prompt_ids - scene_ids):
            issues.append(PromptReviewIssue(
                scene_id=extra_id,
                severity="major",
                category="fidelity",
                description=f"提示词包含剧本中不存在的镜头 {extra_id}",
                suggestion="删除该提示词或将其对应到正确镜头",
            ))
        result.issues = issues
        if issues:
            result.passed = False if any(item.severity == "critical" for item in issues) else result.passed
            if any(item.severity == "critical" for item in issues):
                result.overall_score = min(result.overall_score, 59)
        return result
