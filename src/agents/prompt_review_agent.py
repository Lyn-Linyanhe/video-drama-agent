"""Evidence-first, versioned Kimi review for storyboard prompts.

The public ``run`` method keeps a legacy path for old callers that omit the
source-text argument. New jobs pass the argument explicitly and use the v2
three-stage audit: source alignment, single-shot semantics, and adjacent-shot
continuity, followed by an independent candidate review.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from collections import defaultdict
from copy import deepcopy
from typing import Any, Iterable

from src.schemas.prompt import GenerationPrompts, PromptPolicy
from src.schemas.prompt_review import (
    PromptReviewAuditSnapshot,
    PromptReviewEvidence,
    PromptReviewIssue,
    PromptReviewResult,
    PromptReviewShot,
    PromptReviewStageRecord,
    SceneFactSheet,
)
from src.schemas.script import ScriptBreakdown
from src.schemas.visual import VisualDesign
from src.services.prompt_review_facts import (
    audit_snapshot_signature,
    build_audit_snapshot as build_fact_snapshot,
    referenced_asset_tokens,
    snapshot_search_text,
)


SYSTEM_PROMPT = """你是短剧分镜审核总监。你只诊断，不改写剧本或提示词。

你必须严格区分三个责任层：source_parse（原始剧本到结构化剧本）和 prompt（结构化剧本到分镜提示词）。
只报告有证据的问题：每条问题必须同时引用期望事实（source_script 或 structured_script）和实际提示词片段（prompt）；
缺失类问题还必须列出 checked_prompt_sections。不要因为两个相邻镜头的 location_id 不同就判定连续性错误。
审核输入中的 context/context_scenes 只用于判断关系，不得把上下文镜头的问题归到目标镜头。

返回严格 JSON，不要 Markdown。issues 为空表示没有候选问题。"""

_CATEGORY_VALUES = {"coverage", "fidelity", "clarity", "continuity", "executability", "quality"}
_CATEGORY_ALIASES = {
    "coverage": "coverage", "completeness": "coverage", "omission": "coverage", "missing": "coverage",
    "完整性": "coverage", "遗漏": "coverage",
    "fidelity": "fidelity", "accuracy": "fidelity", "faithfulness": "fidelity",
    "attribution": "fidelity", "misattribution": "fidelity", "wrong_attribution": "fidelity",
    "future_leak": "fidelity", "future_leakage": "fidelity", "剧本忠实度": "fidelity",
    "忠实度": "fidelity", "准确性": "fidelity",
    "clarity": "clarity", "readability": "clarity", "清晰度": "clarity",
    "continuity": "continuity", "consistency": "continuity", "连贯性": "continuity", "连续性": "continuity",
    "executability": "executability", "feasibility": "executability", "implementability": "executability",
    "practicality": "executability", "execution": "executability", "可执行性": "executability", "可行性": "executability",
    "quality": "quality", "technical_quality": "quality", "field_pollution": "quality",
    "template_residue": "quality", "pollution": "quality", "整体质量": "quality", "质量": "quality",
}
_SEVERITY_VALUES = {"critical", "major", "minor"}
_LAYER_VALUES = {"source_parse", "prompt"}
_SCOPE_VALUES = {"source_alignment", "single_shot", "cross_shot", "rule"}
_CONFIDENCE_VALUES = {"confirmed", "suspected", "rejected", "unreviewed"}
_CONFIDENCE_ALIASES = {
    "confirmed": "confirmed", "certain": "confirmed", "definite": "confirmed",
    "verified": "confirmed", "high_confidence": "confirmed", "高置信": "confirmed",
    "suspected": "suspected", "uncertain": "suspected", "likely": "suspected",
    "probable": "suspected", "medium_confidence": "suspected", "待确认": "suspected",
    "rejected": "rejected", "否定": "rejected",
    "unreviewed": "unreviewed", "not_reviewed": "unreviewed", "未审核": "unreviewed",
}
_MISSING_WORDS = ("缺少", "遗漏", "未体现", "没有体现", "missing", "omitted", "absent")


class PromptReviewAgent:
    """Run the dedicated Kimi audit without depending on the main script LLM."""

    provider = "kimi"
    DEFAULT_BATCH_SIZE = 4
    _last_request_at = 0.0

    @staticmethod
    def _settings() -> tuple[str, str, str]:
        key = os.getenv("KIMI_PROMPT_REVIEW_API_KEY", "").strip()
        if not key and os.getenv("LLM_PROVIDER", "").strip().lower() == "kimi":
            key = os.getenv("OPENAI_API_KEY", "").strip()
        model = (
            os.getenv("KIMI_PROMPT_REVIEW_MODEL", "").strip()
            or (os.getenv("LLM_MODEL", "").strip() if os.getenv("LLM_PROVIDER", "").strip().lower() == "kimi" else "")
            or "kimi-k2.6"
        )
        base_url = os.getenv("KIMI_PROMPT_REVIEW_BASE_URL", "").strip() or "https://api.moonshot.cn/v1"
        return key, model, base_url

    @classmethod
    def enabled(cls) -> bool:
        return os.getenv("KIMI_PROMPT_REVIEW_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}

    @classmethod
    def is_configured(cls) -> bool:
        return bool(cls._settings()[0])

    @classmethod
    def configuration_error(cls) -> str:
        return "请在 API 设置的“分镜提示词审核（Kimi）”中配置 Kimi API Key"

    @classmethod
    def build_audit_snapshot(
        cls, script: ScriptBreakdown, prompts: GenerationPrompts,
        visual_design: VisualDesign | None, prompt_policy: PromptPolicy,
        source_text: str = "", scene_vision_descriptions: dict[str, Any] | None = None,
    ) -> PromptReviewAuditSnapshot:
        return build_fact_snapshot(
            script, prompts, visual_design, prompt_policy, source_text=source_text,
            scene_vision_descriptions=scene_vision_descriptions or {},
        )

    @staticmethod
    def audit_signature(snapshot: PromptReviewAuditSnapshot) -> str:
        return audit_snapshot_signature(snapshot)

    async def run(
        self, script: ScriptBreakdown, prompts: GenerationPrompts,
        visual_design: VisualDesign | None, prompt_policy: PromptPolicy,
        source_text: str | None = None, scene_vision_descriptions: dict[str, Any] | None = None,
    ) -> PromptReviewResult:
        key, model, base_url = self._settings()
        if not key:
            raise ValueError(self.configuration_error())
        # None is the pre-v2 API. New jobs pass a concrete value, including "".
        if source_text is None:
            return await self._run_legacy(script, prompts, visual_design, prompt_policy, key, model, base_url)

        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=key, base_url=base_url, timeout=300, max_retries=0)
        snapshot = self.build_audit_snapshot(
            script, prompts, visual_design, prompt_policy, source_text, scene_vision_descriptions,
        )
        signature = self.audit_signature(snapshot)
        stage_records: list[PromptReviewStageRecord] = []
        candidates: list[PromptReviewIssue] = []
        scene_ids = {sheet.scene_id for sheet in snapshot.scene_facts}

        if snapshot.source_script.status == "available":
            issues, status = await self._stage_call(
                client, model, "source_alignment", self._source_alignment_payload(snapshot), snapshot,
                scene_ids, "source_parse", "source_alignment",
            )
            candidates.extend(issues)
            stage_records.append(PromptReviewStageRecord(
                stage="source_alignment", status=status, scene_ids=sorted(scene_ids), candidate_count=len(issues),
                error=self._stage_error(issues),
            ))
        else:
            stage_records.append(PromptReviewStageRecord(
                stage="source_alignment", status="skipped", error="source_parse=not_applicable",
            ))

        for index, sheet in enumerate(snapshot.scene_facts):
            issues, status = await self._stage_call(
                client, model, "single_shot", self._single_shot_payload(snapshot, index), snapshot,
                {sheet.scene_id}, "prompt", "single_shot",
            )
            candidates.extend(issues)
            stage_records.append(PromptReviewStageRecord(
                stage="single_shot", status=status, scene_ids=[sheet.scene_id], candidate_count=len(issues),
                error=self._stage_error(issues),
            ))

        for index in range(1, len(snapshot.scene_facts)):
            previous, current = snapshot.scene_facts[index - 1:index + 1]
            issues, status = await self._stage_call(
                client, model, "cross_shot", self._continuity_payload(snapshot, previous, current), snapshot,
                {previous.scene_id, current.scene_id}, "prompt", "cross_shot",
            )
            candidates.extend(issues)
            stage_records.append(PromptReviewStageRecord(
                stage="cross_shot", status=status, scene_ids=[previous.scene_id, current.scene_id], candidate_count=len(issues),
                error=self._stage_error(issues),
            ))

        candidates = self._deduplicate_issues(candidates)
        reviewed, review_status = await self._rereview_candidates(client, model, snapshot, candidates)
        stage_records.append(PromptReviewStageRecord(
            stage="candidate_rereview", status=review_status,
            scene_ids=sorted({item.scene_id for item in candidates if item.scene_id}),
            candidate_count=len(candidates),
            confirmed_count=sum(item.confidence == "confirmed" for item in reviewed),
            error=self._stage_error(reviewed),
        ))
        deterministic = self._deterministic_issues(script, prompts, snapshot)
        all_issues = self._deduplicate_issues([*reviewed, *deterministic])
        snapshot.stage_records = stage_records
        report = self._finalize_report(script, snapshot, all_issues, signature)
        report.stage_records = stage_records
        report.audit_snapshot = snapshot
        return report

    async def _run_legacy(self, script, prompts, visual_design, prompt_policy, key, model, base_url):
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=key, base_url=base_url, timeout=300, max_retries=0)
        batch_results = []
        prompt_by_scene = {item.scene_id: item for item in prompts.shots}
        scenes = list(script.scenes)
        batch_size = self._batch_size()
        for start in range(0, len(scenes), batch_size):
            batch_scenes = scenes[start:start + batch_size]
            batch_ids = [scene.scene_id for scene in batch_scenes]
            payload = self._batch_payload(script, prompt_by_scene, visual_design, prompt_policy, start, batch_size)
            raw = await self._request_json(client, model, self._batch_user_prompt(batch_ids, payload))
            batch_results.append((batch_ids, PromptReviewResult.model_validate(self._normalize_review_payload(raw))))
        return self._apply_deterministic_checks(script, prompts, self._merge_batch_results(batch_results))

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
            "scene_id": scene.scene_id, "location": scene.location, "characters": scene.characters,
            "action_description": scene.action_description, "camera_direction": scene.camera_direction,
            "emotion_tone": scene.emotion_tone, "dialogue": [line.model_dump() for line in scene.dialogue],
            "continuity_from_prev": scene.continuity_from_prev, "continuity_reason": scene.continuity_reason,
            "duration_sec": scene.duration_sec,
        }

    @classmethod
    def _batch_payload(cls, script, prompt_by_scene, visual_design, prompt_policy, start, batch_size):
        target_scenes = script.scenes[start:start + batch_size]
        target_ids = {scene.scene_id for scene in target_scenes}
        relevant_characters = {character for scene in target_scenes for character in scene.characters}
        visual_by_scene = {item.scene_id: item for item in (visual_design.scenes if visual_design else [])}
        context_scenes = []
        if start:
            context_scenes.append(cls._scene_summary(script.scenes[start - 1]))
        if start + batch_size < len(script.scenes):
            context_scenes.append(cls._scene_summary(script.scenes[start + batch_size]))
        return {
            "review_scope": {"target_scene_ids": sorted(target_ids), "instruction": "只审核 target_scene_ids，context_scenes 仅用于判断承接关系。"},
            "project_style": {
                "title": script.title, "genre": script.genre, "logline": script.logline,
                "overall_style": visual_design.overall_style if visual_design else "",
                "color_palette": visual_design.color_palette if visual_design else "",
                "aspect_ratio": visual_design.aspect_ratio if visual_design else "",
                "character_anchors": {name: desc for name, desc in (visual_design.global_character_descriptions_cn.items() if visual_design else []) if name in relevant_characters},
            },
            "prompt_policy": prompt_policy.model_dump(),
            "target_scenes": [{
                "script": cls._scene_summary(scene),
                "visual_design": visual_by_scene[scene.scene_id].model_dump() if scene.scene_id in visual_by_scene else None,
                "generation_prompt": prompt_by_scene[scene.scene_id].model_dump() if scene.scene_id in prompt_by_scene else None,
            } for scene in target_scenes],
            "context_scenes": context_scenes,
        }

    @staticmethod
    def _batch_user_prompt(batch_ids, payload):
        return (
            "请审核以下分镜批次，返回严格 JSON，不要 Markdown。"
            f"本批只审核镜头：{','.join(str(item) for item in batch_ids)}。"
            "shots 只能包含这些 scene_id；context_scenes 仅用于连续性判断。\n"
            "JSON schema: {\"overall_score\":0,\"passed\":false,\"summary\":\"\",\"shots\":[{\"scene_id\":1,\"score\":0,\"passed\":false,\"summary\":\"\",\"issues\":[{\"scene_id\":1,\"severity\":\"major\",\"category\":\"coverage\",\"description\":\"\",\"suggestion\":\",\"evidence\":\"\"}]}],\"issues\":[]}\n"
            f"项目数据:\n{json.dumps(payload, ensure_ascii=False)}"
        )

    @staticmethod
    def _merge_batch_results(batch_results):
        shots, issues, summaries = [], [], []
        for expected_ids, batch in batch_results:
            by_scene = {shot.scene_id: shot for shot in batch.shots}
            for scene_id in expected_ids:
                shot = by_scene.get(scene_id)
                if shot:
                    shots.append(shot)
                else:
                    missing = PromptReviewIssue(scene_id=scene_id, severity="major", category="quality", description="该镜头未包含在本批 Kimi 审核结果中。", suggestion="重新检验该项目；若持续发生，请减小审核批次。")
                    shots.append(PromptReviewShot(scene_id=scene_id, score=0, passed=False, summary="Kimi 未返回该镜头的审核结果。", issues=[missing]))
            issues.extend(batch.issues)
            if batch.summary:
                summaries.append(batch.summary)
        overall = round(sum(item.score for item in shots) / len(shots)) if shots else 0
        all_issues = issues + [issue for shot in shots for issue in shot.issues]
        has_critical = any(item.severity == "critical" for item in all_issues)
        return PromptReviewResult(overall_score=overall, passed=overall >= 75 and not has_critical and all(item.passed for item in shots), summary="；".join(summaries)[:2000], shots=shots, issues=issues)

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        text = (content or "").strip()
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
        if fenced:
            text = fenced.group(1).strip()
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", text)
            if not match:
                raise ValueError("Kimi 未返回可解析的分镜审核 JSON")
            value = json.loads(match.group())
        if not isinstance(value, dict):
            raise ValueError("Kimi 审核 JSON 顶层必须是对象")
        return value

    @staticmethod
    def _request_options() -> dict[str, Any]:
        """Use Kimi's reasoning mode without allowing the answer to be truncated."""
        thinking = os.getenv("KIMI_PROMPT_REVIEW_THINKING", "enabled").strip().lower()
        thinking_disabled = thinking in {"0", "false", "no", "off", "none", "disabled"}
        try:
            requested_max_tokens = int(os.getenv("KIMI_PROMPT_REVIEW_MAX_TOKENS", "16384"))
        except ValueError:
            requested_max_tokens = 16384
        max_tokens = max(4096, min(requested_max_tokens, 32768))
        return {
            "temperature": 0.6 if thinking_disabled else 1,
            "max_tokens": max_tokens,
            "extra_body": {"thinking": {"type": "disabled" if thinking_disabled else "enabled"}},
        }

    @staticmethod
    def _request_interval() -> float:
        try:
            return max(0.0, float(os.getenv("KIMI_PROMPT_REVIEW_REQUEST_INTERVAL_SEC", "0")))
        except ValueError:
            return 0.0

    @staticmethod
    def _rate_limit_retry_delay(error_text: str, interval: float) -> float:
        match = re.search(r"(?:after|retry[-_ ]after)\s*(\d+(?:\.\d+)?)\s*seconds?", error_text, re.IGNORECASE)
        provider_delay = float(match.group(1)) if match else 0.0
        try:
            fallback = max(0.0, float(os.getenv("KIMI_PROMPT_REVIEW_RATE_LIMIT_RETRY_SEC", "20")))
        except ValueError:
            fallback = 20.0
        return max(interval, provider_delay, fallback)

    @classmethod
    async def _create_completion(cls, client, kwargs):
        """Throttle and retry provider rate limits without changing review semantics."""
        interval = cls._request_interval()
        max_attempts = 2
        for attempt in range(max_attempts):
            if interval:
                elapsed = time.monotonic() - cls._last_request_at
                if elapsed < interval:
                    await asyncio.sleep(interval - elapsed)
            cls._last_request_at = time.monotonic()
            try:
                return await client.chat.completions.create(**kwargs)
            except Exception as exc:
                message = str(exc)
                lowered = message.lower()
                rate_limited = "429" in lowered or "rate limit" in lowered or "rate_limit" in lowered
                if not rate_limited or attempt + 1 >= max_attempts:
                    raise
                await asyncio.sleep(cls._rate_limit_retry_delay(message, interval))
        raise RuntimeError("unreachable")

    @staticmethod
    async def _request_json(client, model, user_prompt, repair_prompt=""):
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_prompt}]
        if repair_prompt:
            messages.append({"role": "user", "content": repair_prompt})
        kwargs = {
            "model": model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            **PromptReviewAgent._request_options(),
        }
        try:
            response = await PromptReviewAgent._create_completion(client, kwargs)
        except Exception as exc:
            message = str(exc).lower()
            temperature_one_only = (
                "temperature" in message
                and "only 1 is allowed" in message
            )
            unsupported_json_mode = (
                "response_format" in message
                and any(token in message for token in ("unsupported", "not support", "invalid", "unknown", "not allowed"))
            )
            if temperature_one_only:
                kwargs["temperature"] = 1
                kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
                try:
                    response = await PromptReviewAgent._create_completion(client, kwargs)
                except Exception as retry_exc:
                    retry_message = str(retry_exc).lower()
                    retry_json_mode_unsupported = (
                        "response_format" in retry_message
                        and any(token in retry_message for token in ("unsupported", "not support", "invalid", "unknown", "not allowed"))
                    )
                    if not retry_json_mode_unsupported:
                        raise
                    kwargs.pop("response_format", None)
                    response = await PromptReviewAgent._create_completion(client, kwargs)
            elif not isinstance(exc, TypeError) and not unsupported_json_mode:
                raise
            else:
                kwargs.pop("response_format", None)
                response = await PromptReviewAgent._create_completion(client, kwargs)
        try:
            return PromptReviewAgent._parse_json(response.choices[0].message.content or "")
        except ValueError as exc:
            raise _ReviewFormatError(str(exc)) from exc

    @classmethod
    def _source_alignment_payload(cls, snapshot):
        return {"stage": "source_alignment", "instruction": "逐项核对原始剧本与结构化剧本；只报告遗漏、虚构、错归属、错顺序、错误拆镜或字段污染。若原文片段无法由 source_references 可靠绑定到单一镜头，使用 scene_id=0，并把相关结构化镜头写入 related_scene_ids；该原文 evidence 的 scene_id 也必须为 0。", "source_script": snapshot.source_script.model_dump(), "structured_script": snapshot.structured_script, "scene_facts": [item.model_dump() for item in snapshot.scene_facts], "generation_prompts": snapshot.generation_prompts, "output": cls._stage_schema("source_parse", "source_alignment")}

    @classmethod
    def _single_shot_payload(cls, snapshot, index):
        target = snapshot.scene_facts[index]
        neighbours = ([snapshot.scene_facts[index - 1].model_dump()] if index else []) + ([snapshot.scene_facts[index + 1].model_dump()] if index + 1 < len(snapshot.scene_facts) else [])
        return {"stage": "single_shot", "target_scene_id": target.scene_id, "instruction": "核对覆盖、动作/对白顺序、镜头语言、时长承载、剧本外内容和未来泄露。", "target_fact_sheet": target.model_dump(), "adjacent_complete_fact_sheets": neighbours, "full_fact_index": [item.model_dump() for item in snapshot.scene_facts], "output": cls._stage_schema("prompt", "single_shot")}

    @classmethod
    def _continuity_payload(cls, snapshot, previous, current):
        return {"stage": "cross_shot", "target_scene_ids": [previous.scene_id, current.scene_id], "instruction": "只检查前镜结束状态与后镜起始状态的角色、道具、空间、光线承接；不同 location_id 本身不是错误。", "previous_fact_sheet": previous.model_dump(), "current_fact_sheet": current.model_dump(), "all_fact_index": [item.model_dump() for item in snapshot.scene_facts], "output": cls._stage_schema("prompt", "cross_shot")}

    @staticmethod
    def _stage_schema(layer, scope):
        return {"issues": [{"scene_id": 0, "related_scene_ids": [], "severity": "minor", "category": "coverage", "layer": layer, "scope": scope, "issue_code": "stable.code", "description": "具体问题", "suggestion": "可执行修复建议", "confidence": "suspected", "fact_ids": ["scene:1:action"], "evidence_items": [{"source": "structured_script", "scene_id": 1, "fact_id": "scene:1:action", "excerpt": "期望事实"}, {"source": "prompt", "scene_id": 1, "section": "核心分镜意图", "excerpt": "实际提示词片段"}], "checked_prompt_sections": ["核心分镜意图"], "repair_action": "edit_prompt"}], "category_rule": "category 必须使用 coverage、fidelity、clarity、continuity、executability 或 quality；不要自造分类。单镜 related_scene_ids 只能引用相邻上下文镜头，scene_id 必须是当前目标镜头。"}

    async def _stage_call(self, client, model, stage, payload, snapshot, target_scene_ids, default_layer, default_scope):
        prompt = "按系统规则审核以下单一阶段输入。只审核目标镜头，返回 {\"issues\": [...]}；没有问题返回空数组。\n" + json.dumps(payload, ensure_ascii=False)
        try:
            raw = await self._request_json(client, model, prompt)
            return self._parse_stage_issues(raw, snapshot, target_scene_ids, default_layer, default_scope, stage), "completed"
        except _ReviewFormatError as exc:
            try:
                raw = await self._request_json(client, model, prompt, "上一份 JSON 未满足审核契约。只修复格式和证据引用，不新增判断；错误：" + str(exc)[:500])
                return self._parse_stage_issues(raw, snapshot, target_scene_ids, default_layer, default_scope, stage), "completed"
            except Exception as repair_exc:
                return [self._unreviewed_issue(
                    snapshot, min(target_scene_ids) if target_scene_ids else 0,
                    default_scope, f"{stage} 审核输出无法通过格式或证据校验。",
                    str(repair_exc), related_scene_ids=sorted(target_scene_ids) if stage == "cross_shot" else [],
                )], "unreviewed"
        except Exception as exc:
            return [self._unreviewed_issue(
                snapshot, min(target_scene_ids) if target_scene_ids else 0,
                default_scope, f"{stage} 审核请求未完成。", str(exc),
                related_scene_ids=sorted(target_scene_ids) if stage == "cross_shot" else [],
            )], "unreviewed"

    @classmethod
    def _parse_stage_issues(cls, payload, snapshot, target_scene_ids, default_layer, default_scope, stage):
        if "issues" not in payload or not isinstance(payload.get("issues"), list):
            raise _ReviewFormatError("issues 必须是数组")
        if "shots" in payload and not isinstance(payload.get("shots"), list):
            raise _ReviewFormatError("shots 必须是数组")
        raw_items = list(payload.get("issues") or [])
        for shot in payload.get("shots") or []:
            if isinstance(shot, dict):
                raw_items.extend(shot.get("issues") or [])
        parsed = []
        errors = []
        allowed_scene_ids = cls._allowed_stage_scene_ids(snapshot, target_scene_ids, stage)
        for index, raw in enumerate(raw_items, start=1):
            try:
                if not isinstance(raw, dict):
                    raise _ReviewFormatError("问题项必须是对象")
                item = cls._normalize_v2_issue(raw, default_layer, default_scope, stage)
                if stage == "single_shot" and item.scene_id not in target_scene_ids:
                    raise _ReviewFormatError("单镜问题必须绑定当前目标镜头")
                if stage != "single_shot" and item.scene_id and item.scene_id not in target_scene_ids:
                    raise _ReviewFormatError("问题镜头超出当前阶段目标范围")
                if any(scene_id not in allowed_scene_ids for scene_id in item.related_scene_ids):
                    raise _ReviewFormatError("关联镜头超出当前阶段允许范围")
                cls._validate_issue_evidence(item, snapshot, stage, target_scene_ids)
                parsed.append(item)
            except _ReviewFormatError as exc:
                errors.append(f"候选 {index}: {exc}")
        if raw_items and not parsed:
            raise _ReviewFormatError("所有问题候选均未通过校验：" + "；".join(errors)[:800])
        return parsed

    @staticmethod
    def _allowed_stage_scene_ids(snapshot, target_scene_ids, stage):
        allowed = set(target_scene_ids)
        if stage != "single_shot":
            return allowed
        scene_ids = [item.scene_id for item in snapshot.scene_facts]
        positions = {scene_id: index for index, scene_id in enumerate(scene_ids)}
        for scene_id in target_scene_ids:
            index = positions.get(scene_id)
            if index is None:
                continue
            for neighbour_index in (index - 1, index + 1):
                if 0 <= neighbour_index < len(scene_ids):
                    allowed.add(scene_ids[neighbour_index])
        return allowed

    @staticmethod
    def _normalize_v2_issue(raw, layer, scope, stage):
        value = deepcopy(raw)
        if "layer" in value and value.get("layer") != layer:
            raise _ReviewFormatError("责任层与当前阶段不匹配")
        if value.get("layer") not in _LAYER_VALUES:
            if "layer" in value:
                raise _ReviewFormatError("未知责任层")
            value["layer"] = layer
        if "scope" in value and value.get("scope") != scope:
            raise _ReviewFormatError("审核范围与当前阶段不匹配")
        if value.get("scope") not in _SCOPE_VALUES:
            if "scope" in value:
                raise _ReviewFormatError("未知审核范围")
            value["scope"] = scope
        raw_category = str(value.get("category") or "").strip().lower().replace("-", "_").replace(" ", "_")
        value["category"] = _CATEGORY_ALIASES.get(raw_category, raw_category)
        if value.get("category") not in _CATEGORY_VALUES:
            raise _ReviewFormatError("未知问题分类")
        if value.get("severity") not in _SEVERITY_VALUES:
            raise _ReviewFormatError("未知严重级别")
        if not str(value.get("issue_code") or "").strip():
            raise _ReviewFormatError("缺少稳定 issue_code")
        if not str(value.get("description") or "").strip() or not str(value.get("suggestion") or "").strip():
            raise _ReviewFormatError("问题描述或修复建议为空")
        if "confidence" in value:
            raw_confidence = str(value.get("confidence") or "").strip().lower().replace("-", "_").replace(" ", "_")
            value["confidence"] = _CONFIDENCE_ALIASES.get(raw_confidence, raw_confidence)
            if value["confidence"] not in _CONFIDENCE_VALUES:
                raise _ReviewFormatError("未知置信状态")
        if "confidence" not in value:
            value["confidence"] = "suspected"
        value.setdefault("scene_id", 0)
        value.setdefault("related_scene_ids", [])
        if isinstance(value.get("evidence"), list):
            if not value.get("evidence_items"):
                value["evidence_items"] = value["evidence"]
            value["evidence"] = ""
        value.setdefault("evidence_items", [])
        value.setdefault("fact_ids", [])
        value.setdefault("checked_prompt_sections", [])
        value.setdefault("repair_action", "inspect")
        # Only backend rules can be deterministic gates. Provider output is
        # always a semantic candidate, regardless of fields it attempts to set.
        value["deterministic"] = False
        value["blocks_generation"] = False
        if not value.get("candidate_id"):
            identity = json.dumps(
                value.get("evidence_items") or value.get("evidence") or [],
                ensure_ascii=False, sort_keys=True,
            )
            digest = hashlib.sha1(identity.encode("utf-8")).hexdigest()[:10]
            value["candidate_id"] = f"{stage}:{value['scene_id']}:{value['issue_code']}:{digest}"
        if not value.get("fact_ids"):
            value["fact_ids"] = [
                str(item.get("fact_id") or "")
                for item in value.get("evidence_items") or []
                if isinstance(item, dict) and item.get("fact_id")
            ]
        if not value.get("fact_ids"):
            raise _ReviewFormatError("缺少事实 ID")
        try:
            return PromptReviewIssue.model_validate(value)
        except Exception as exc:
            raise _ReviewFormatError(f"问题字段不符合 schema：{str(exc)[:300]}") from exc

    @staticmethod
    def _flatten_evidence_values(value):
        if isinstance(value, dict):
            return [item for nested in value.values() for item in PromptReviewAgent._flatten_evidence_values(nested)]
        if isinstance(value, (list, tuple)):
            return [item for nested in value for item in PromptReviewAgent._flatten_evidence_values(nested)]
        if value is None or value is False:
            return []
        return [str(value)]

    @staticmethod
    def _evidence_contains(haystack, excerpt):
        """Accept provider line wrapping while keeping substring membership strict."""
        haystack_text = str(haystack or "")
        excerpt_text = str(excerpt or "")
        if not excerpt_text.strip():
            return False
        if excerpt_text in haystack_text:
            return True
        compact_haystack = re.sub(r"\s+", "", haystack_text)
        compact_excerpt = re.sub(r"\s+", "", excerpt_text)
        return bool(compact_excerpt) and compact_excerpt in compact_haystack

    @classmethod
    def _structured_fact_values(cls, snapshot, scene_id, fact_id):
        sheet = next((item for item in snapshot.scene_facts if item.scene_id == scene_id), None)
        if not sheet:
            return []
        if fact_id == f"scene:{scene_id}":
            return [
                json.dumps(sheet.model_dump(), ensure_ascii=False, sort_keys=True),
                f"source_references: {json.dumps([item.model_dump() for item in sheet.source_references], ensure_ascii=False)}",
            ]
        facts = [
            sheet.location, *sheet.characters, *sheet.dialogue, sheet.action,
            sheet.camera, sheet.emotion, *sheet.visual_facts,
        ]
        for fact in facts:
            if fact.fact_id == fact_id:
                return cls._flatten_evidence_values(fact.value)
        values = {
            f"scene:{scene_id}:duration": [sheet.duration_sec],
            f"scene:{scene_id}:continuity": [sheet.continuity_reason, sheet.continuity_from_prev],
            f"scene:{scene_id}:transition": [sheet.transition_from_prev, sheet.transition_duration_sec],
            f"scene:{scene_id}:asset_binding": [sheet.asset_binding.model_dump()],
        }
        return cls._flatten_evidence_values(values.get(fact_id, []))

    @staticmethod
    def _rule_fact_values(snapshot, scene_id, fact_id):
        fact = next((item for item in snapshot.rule_facts if item.fact_id == fact_id), None)
        if not fact or fact.scene_id != scene_id:
            return []
        return PromptReviewAgent._flatten_evidence_values(fact.value)

    @staticmethod
    def _prompt_evidence_matches_snapshot(snapshot, evidence):
        sheet = next((item for item in snapshot.scene_facts if item.scene_id == evidence.scene_id), None)
        prompt_text = ""
        duration_text = ""
        if sheet:
            prompt_text = sheet.prompt.prompt_text + "\n" + sheet.prompt.negative_prompt
            duration_text = str(sheet.prompt.duration_sec) if sheet.prompt.duration_sec is not None else ""
        else:
            for prompt in snapshot.generation_prompts.get("shots") or []:
                if isinstance(prompt, dict) and int(prompt.get("scene_id") or 0) == evidence.scene_id:
                    prompt_text = str(prompt.get("prompt") or "") + "\n" + str(prompt.get("negative_prompt") or "")
                    duration = prompt.get("duration_sec")
                    duration_text = str(duration) if duration is not None else ""
                    break
        duration_values = {duration_text}
        if sheet and sheet.prompt.duration_sec is not None:
            duration_values.add(f"{float(sheet.prompt.duration_sec):g}")
        return bool(prompt_text or duration_text) and (
            PromptReviewAgent._evidence_contains(prompt_text, evidence.excerpt)
            or any(PromptReviewAgent._evidence_contains(value, evidence.excerpt) for value in duration_values)
        )

    @staticmethod
    def _line_range_matches(evidence, paragraph):
        if not evidence.line_start and not evidence.line_end:
            return True
        if evidence.line_start < 1 or evidence.line_end < evidence.line_start:
            return False
        return (
            evidence.line_start == paragraph.line_start
            and evidence.line_end == paragraph.line_end
        )

    @classmethod
    def _source_script_evidence_matches(cls, snapshot, issue, evidence):
        if snapshot.source_script.status != "available":
            return False
        if issue.scene_id and evidence.scene_id != issue.scene_id:
            return False
        if evidence.scene_id:
            sheet = next(
                (item for item in snapshot.scene_facts if item.scene_id == evidence.scene_id),
                None,
            )
            return bool(sheet) and any(
                cls._evidence_contains(reference.excerpt, evidence.excerpt)
                and cls._line_range_matches(evidence, reference)
                for reference in sheet.source_references
            )
        return any(
            cls._evidence_contains(paragraph.text, evidence.excerpt)
            and cls._line_range_matches(evidence, paragraph)
            for paragraph in snapshot.source_script.paragraphs
        )

    @classmethod
    def _validate_issue_evidence(cls, issue, snapshot, stage, target_scene_ids=None, require_semantic_pair=True):
        if not issue.evidence_items:
            raise _ReviewFormatError("问题缺少 evidence_items")
        searchable = snapshot_search_text(snapshot)
        sources = {item.source for item in issue.evidence_items}
        for item in issue.evidence_items:
            if item.source == "source_script" and issue.scene_id and item.scene_id != issue.scene_id:
                raise _ReviewFormatError("原文证据镜头必须绑定到问题主镜头")
            if item.source == "source_script" and not cls._source_script_evidence_matches(snapshot, issue, item):
                raise _ReviewFormatError("原文证据不在原始剧本或有效镜头引用中")
            if not cls._evidence_contains(searchable, item.excerpt):
                raise _ReviewFormatError("证据片段不在审核快照中")
            if (
                item.scene_id
                and stage != "source_alignment"
                and item.scene_id != issue.scene_id
                and item.scene_id not in issue.related_scene_ids
            ):
                raise _ReviewFormatError("证据镜头不属于问题目标镜头")
            if stage == "single_shot" and item.scene_id and item.scene_id != issue.scene_id:
                raise _ReviewFormatError("单镜证据必须绑定当前目标镜头")
            if stage in {"single_shot", "cross_shot"} and item.scene_id not in {
                issue.scene_id, *issue.related_scene_ids,
            }:
                raise _ReviewFormatError("证据镜头超出阶段目标范围")
            if (
                stage == "source_alignment"
                and item.source != "source_script"
                and item.scene_id
                and item.scene_id not in {issue.scene_id, *issue.related_scene_ids}
            ):
                raise _ReviewFormatError("原文对齐证据镜头未关联到问题")
            if item.source == "structured_script" and not item.fact_id:
                raise _ReviewFormatError("结构化事实证据缺少事实 ID")
            if item.source == "structured_script":
                if item.fact_id == f"scene:{item.scene_id}":
                    raise _ReviewFormatError("结构化证据必须使用具体结构化事实 ID")
                if item.fact_id != f"scene:{item.scene_id}" and not item.fact_id.startswith(f"scene:{item.scene_id}:"):
                    raise _ReviewFormatError("结构化事实 ID 与证据镜头不匹配")
                values = cls._structured_fact_values(snapshot, item.scene_id, item.fact_id)
                if not values or not any(cls._evidence_contains(value, item.excerpt) for value in values):
                    raise _ReviewFormatError("结构化事实片段与事实 ID 不匹配")
            if item.source == "rule":
                values = cls._rule_fact_values(snapshot, item.scene_id, item.fact_id)
                if not values or not any(item.excerpt in value for value in values):
                    raise _ReviewFormatError("规则事实证据不在审核快照中")
            if item.source == "prompt":
                if not cls._prompt_evidence_matches_snapshot(snapshot, item):
                    raise _ReviewFormatError("提示词证据归属不匹配")
            if item.source == "source_script" and snapshot.source_script.status != "available":
                raise _ReviewFormatError("原文不可用却引用了 source_script")
        evidence_fact_ids = {item.fact_id for item in issue.evidence_items if item.fact_id}
        if issue.fact_ids and not set(issue.fact_ids).issubset(evidence_fact_ids):
            raise _ReviewFormatError("问题事实 ID 未被证据引用")
        if require_semantic_pair:
            if not (sources & {"structured_script", "source_script"}):
                raise _ReviewFormatError("缺少期望事实证据")
            if "prompt" not in sources:
                raise _ReviewFormatError("缺少实际提示词证据")
            if stage == "source_alignment" and not {"source_script", "structured_script"}.issubset(sources):
                raise _ReviewFormatError("原文对齐问题必须同时引用原文和结构化事实")
        if stage == "cross_shot":
            expected_scene_ids = set(target_scene_ids or ())
            if len(expected_scene_ids) < 2:
                raise _ReviewFormatError("跨镜阶段必须包含前后两个镜头")
            if issue.scene_id:
                required_scene_ids = expected_scene_ids - {issue.scene_id}
                if not required_scene_ids.issubset(set(issue.related_scene_ids)):
                    raise _ReviewFormatError("跨镜问题缺少另一侧关联镜头")
            elif not expected_scene_ids.issubset(set(issue.related_scene_ids)):
                raise _ReviewFormatError("跨镜问题缺少另一侧关联镜头")
            evidence_scene_ids = {item.scene_id for item in issue.evidence_items}
            expected_evidence_ids = expected_scene_ids
            if not expected_evidence_ids.issubset(evidence_scene_ids):
                raise _ReviewFormatError("跨镜问题必须分别引用前后两镜证据")
        if require_semantic_pair and any(word.lower() in issue.description.lower() for word in _MISSING_WORDS) and not issue.checked_prompt_sections:
            raise _ReviewFormatError("缺失类问题必须列出 checked_prompt_sections")

    async def _rereview_candidates(self, client, model, snapshot, candidates):
        if not candidates:
            return [], "completed"
        payload = {
            "stage": "candidate_rereview",
            "instruction": "只复核候选问题及其证据；逐项返回 confirmed、suspected 或 rejected。不得新增候选问题。必须用提供的事实索引和提示词快照重新核对候选里的期望事实与实际提示词片段。",
            "candidates": [item.model_dump() for item in candidates],
            "fact_index": [item.model_dump() for item in snapshot.scene_facts],
            "source_script": snapshot.source_script.model_dump(),
            "rule_facts": [item.model_dump() for item in snapshot.rule_facts],
            "output": {"decisions": [{"candidate_id": "...", "status": "confirmed"}]},
        }
        prompt = "请复核以下候选问题，返回严格 JSON：\n" + json.dumps(payload, ensure_ascii=False)
        try:
            raw = await self._request_json(client, model, prompt)
            return self._apply_rereview_decisions(candidates, raw), "completed"
        except _ReviewFormatError as exc:
            try:
                raw = await self._request_json(client, model, prompt, "上一份复核 JSON 不符合契约。只修复 decisions 的 candidate_id 和 status，不新增问题；错误：" + str(exc)[:500])
                return self._apply_rereview_decisions(candidates, raw), "completed"
            except Exception as repair_exc:
                return self._unreviewed_rereview_result(snapshot, candidates, repair_exc)
        except Exception as exc:
            return self._unreviewed_rereview_result(snapshot, candidates, exc)

    @classmethod
    def _unreviewed_rereview_result(cls, snapshot, candidates, error):
        visible = [item.model_copy(update={"confidence": "suspected"}) for item in candidates]
        affected_scene_ids = sorted({
            scene_id
            for item in candidates
            for scene_id in [item.scene_id, *item.related_scene_ids]
            if scene_id
        })
        visible.append(cls._unreviewed_issue(
            snapshot, 0, "rule", "候选问题的独立复核未完成。", str(error),
            related_scene_ids=affected_scene_ids,
        ))
        return visible, "unreviewed"

    @staticmethod
    def _stage_error(issues):
        return next(
            (item.description[:1000] for item in issues if item.confidence == "unreviewed"),
            "",
        )

    @classmethod
    def _apply_rereview_decisions(cls, candidates, payload):
        if isinstance(payload.get("output"), dict) and (
            "decisions" in payload["output"] or "issues" in payload["output"]
        ):
            # Some Kimi-compatible gateways wrap the declared JSON body in a
            # stage envelope.  The inner object is the rereview contract.
            payload = payload["output"]
        if "decisions" not in payload and "issues" not in payload:
            raise _ReviewFormatError("复核结果缺少 decisions")
        decisions = list(payload.get("decisions") or [])
        if not decisions and payload.get("issues"):
            decisions = list(payload["issues"])
        candidate_ids = [item.candidate_id for item in candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise _ReviewFormatError("复核候选 ID 重复")
        by_id = dict(zip(candidate_ids, candidates))
        result, seen = [], set()
        for decision in decisions:
            if not isinstance(decision, dict):
                raise _ReviewFormatError("复核 decision 必须是对象")
            candidate_id = str(decision.get("candidate_id") or "")
            if candidate_id not in by_id:
                raise _ReviewFormatError("复核候选 ID 未知")
            raw_status = str(decision.get("status") or decision.get("confidence") or "").strip().lower().replace("-", "_").replace(" ", "_")
            status = _CONFIDENCE_ALIASES.get(raw_status, raw_status)
            if status not in {"confirmed", "suspected", "rejected"}:
                raise _ReviewFormatError("未知复核状态")
            if candidate_id in seen:
                raise _ReviewFormatError("复核候选 ID 重复")
            if status != "rejected":
                result.append(by_id[candidate_id].model_copy(update={"confidence": status}))
            seen.add(candidate_id)
        if seen != set(by_id):
            raise _ReviewFormatError("复核结果未覆盖全部候选问题")
        return result

    @staticmethod
    def _unreviewed_issue(snapshot, scene_id, scope, description, error="", related_scene_ids=None):
        sheet = next((item for item in snapshot.scene_facts if item.scene_id == scene_id), None)
        if sheet is None and snapshot.scene_facts:
            sheet = snapshot.scene_facts[0]
        evidence_items = []
        if sheet:
            excerpt = sheet.prompt.prompt_text or str(sheet.action.value or "") or "未提供快照片段"
            source = "prompt" if sheet.prompt.prompt_text else "structured_script"
            evidence_items.append(PromptReviewEvidence(
                source=source, scene_id=sheet.scene_id,
                fact_id=sheet.action.fact_id if source == "structured_script" else "",
                excerpt=excerpt[:4000],
            ))
        detail = str(error or "").strip()
        if detail:
            detail = re.sub(r"sk-[A-Za-z0-9_-]+", "sk-[REDACTED]", detail)
            description = f"{description} 原因：{detail}"
        return PromptReviewIssue(
            scene_id=scene_id, related_scene_ids=sorted(set(related_scene_ids or [])), severity="minor", category="audit",
            description=description[:1000], suggestion="重新运行审核或人工核对该阶段。",
            evidence="", evidence_items=evidence_items,
            layer="prompt", scope=scope, issue_code="audit.output_validation",
            confidence="unreviewed", repair_action="inspect", rule_id="audit.output_validation",
        )

    @staticmethod
    def _fact_excerpt(snapshot, scene_id, suffix):
        sheet = next((item for item in snapshot.scene_facts if item.scene_id == scene_id), None)
        if not sheet:
            return ""
        return {"location": sheet.location.value, "action": sheet.action.value, "camera": sheet.camera.value, "emotion": sheet.emotion.value}.get(suffix, "") or ""

    @classmethod
    def _rule_evidence(cls, snapshot, scene_id, text, source="prompt", fact_id=""):
        excerpt = str(text or "").strip() or cls._fact_excerpt(snapshot, scene_id, "action") or "未提供"
        fact_id = fact_id or (f"scene:{scene_id}:action" if source == "structured_script" and scene_id else "")
        return [PromptReviewEvidence(source=source, scene_id=scene_id, fact_id=fact_id, excerpt=excerpt[:4000])]

    @staticmethod
    def _rule_fact_evidence(snapshot, fact_id):
        fact = next((item for item in snapshot.rule_facts if item.fact_id == fact_id), None)
        if not fact:
            raise ValueError(f"缺少确定性审核事实 {fact_id}")
        return PromptReviewEvidence(
            source="rule", scene_id=fact.scene_id, fact_id=fact.fact_id,
            excerpt=str(fact.value),
        )

    @classmethod
    def _deterministic_issues(cls, script, prompts, snapshot):
        issues = []
        scene_ids = [scene.scene_id for scene in script.scenes]
        prompt_ids = [item.scene_id for item in prompts.shots]
        for scene_id in sorted(set(scene_ids) - set(prompt_ids)):
            scene = next(scene for scene in script.scenes if scene.scene_id == scene_id)
            issues.append(cls._rule_issue(scene_id, "rule.prompt_missing", "coverage", "critical", f"镜头 {scene_id} 缺少视频生成提示词。", "为该镜头补充提示词后重新审核。", snapshot, scene.action_description, "structured_script", extra_evidence=[cls._rule_fact_evidence(snapshot, f"scene:{scene_id}:prompt:presence")], checked_prompt_sections=["核心分镜意图", "环境", "单镜头时间轴", "镜头"]))
        for scene_id in sorted(set(prompt_ids) - set(scene_ids)):
            prompt = next(item for item in prompts.shots if item.scene_id == scene_id)
            issues.append(cls._rule_issue(scene_id, "rule.prompt_extra_scene", "fidelity", "critical", f"提示词包含剧本中不存在的镜头 {scene_id}。", "删除该提示词或将其对应到有效镜头。", snapshot, prompt.prompt, "prompt", extra_evidence=[cls._rule_fact_evidence(snapshot, "project:script:scene_ids")]))
        for scene_id in sorted({item for item in prompt_ids if prompt_ids.count(item) > 1}):
            issues.append(cls._rule_issue(scene_id, "rule.prompt_duplicate_id", "coverage", "critical", f"提示词列表包含重复的镜头 ID {scene_id}。", "保留一个镜头提示词，并将其他内容归并到正确镜头。", snapshot, next(item.prompt for item in prompts.shots if item.scene_id == scene_id), "prompt", extra_evidence=[cls._rule_fact_evidence(snapshot, f"scene:{scene_id}:prompt:count")]))
        valid_assets = cls._valid_asset_ids(getattr(script, "asset_manifest", None))
        for prompt in prompts.shots:
            sheet = next((item for item in snapshot.scene_facts if item.scene_id == prompt.scene_id), None)
            if not str(prompt.prompt or "").strip():
                issues.append(cls._rule_issue(prompt.scene_id, "rule.prompt_empty", "quality", "critical", f"镜头 {prompt.scene_id} 的视频生成提示词为空。", "补充可执行的主体、动作、空间和镜头信息。", snapshot, cls._fact_excerpt(snapshot, prompt.scene_id, "action") or "未提供动作事实", "structured_script", extra_evidence=[cls._rule_fact_evidence(snapshot, f"scene:{prompt.scene_id}:prompt:content")], checked_prompt_sections=["核心分镜意图", "环境", "单镜头时间轴", "镜头"]))
            scene = next((item for item in script.scenes if item.scene_id == prompt.scene_id), None)
            if scene and int(prompt.duration_sec) != int(scene.duration_sec):
                issues.append(cls._rule_issue(
                    prompt.scene_id, "rule.duration_conflict", "executability", "major",
                    f"镜头时长字段冲突：剧本 {scene.duration_sec}s，提示词 {prompt.duration_sec}s。",
                    "统一剧本与提示词的镜头时长后重新审核。", snapshot, str(scene.duration_sec),
                    "structured_script",
                    extra_evidence=[PromptReviewEvidence(
                        source="prompt", scene_id=prompt.scene_id, fact_id=f"scene:{prompt.scene_id}:prompt:duration",
                        section="duration_sec", excerpt=str(prompt.duration_sec),
                    )],
                    fact_ids=[f"scene:{prompt.scene_id}:duration", f"scene:{prompt.scene_id}:prompt:duration"],
                    checked_prompt_sections=["duration_sec"],
                ))
            timeline = sheet.prompt.timeline_segments if sheet else []
            for timeline_error in (sheet.prompt.timeline_errors if sheet else []):
                issues.append(cls._rule_issue(
                    prompt.scene_id, "rule.timeline_invalid_range", "executability", "major",
                    f"镜头 {prompt.scene_id} 的时间轴区间无效。", "将每个时间轴区间改为结束时间晚于开始时间。",
                    snapshot, timeline_error, "prompt",
                    extra_evidence=[cls._rule_fact_evidence(snapshot, f"scene:{prompt.scene_id}:prompt:timeline")],
                ))
            if timeline:
                previous_end = None
                for segment in timeline:
                    if previous_end is not None and segment.start_sec < previous_end:
                        issues.append(cls._rule_issue(prompt.scene_id, "rule.timeline_overlap", "executability", "major", f"镜头 {prompt.scene_id} 的时间轴存在重叠。", "调整时间轴区间，使相邻动作不重叠。", snapshot, segment.text or str(segment.start_sec), "prompt"))
                        break
                    previous_end = segment.end_sec
                duration = float(prompt.duration_sec)
                over_budget = max(timeline, key=lambda item: item.end_sec)
                if over_budget.end_sec > duration:
                    issues.append(cls._rule_issue(prompt.scene_id, "rule.timeline_budget", "executability", "major", f"镜头 {prompt.scene_id} 的时间轴超出提示词时长预算。", "缩短动作区间或提高镜头时长，并保持剧本字段一致。", snapshot, f"{over_budget.start_sec:g}-{over_budget.end_sec:g}s {over_budget.text}".strip(), "prompt", extra_evidence=[cls._rule_fact_evidence(snapshot, f"scene:{prompt.scene_id}:prompt:timeline")]))
            for kind, asset, _view in referenced_asset_tokens(prompt.prompt):
                if not cls._asset_token_valid(kind, asset, valid_assets):
                    issues.append(cls._rule_issue(prompt.scene_id, "rule.invalid_reference", "fidelity", "critical", f"镜头 {prompt.scene_id} 引用了不存在的资产 {kind}:{asset}。", "改用资产清单中的有效引用，或先补充对应资产。", snapshot, f"@{kind}:{asset}", "prompt", extra_evidence=[cls._rule_fact_evidence(snapshot, "project:asset_manifest:ids")]))
        for scene in script.scenes:
            if scene.scene_id == scene_ids[0] and scene.continuity_from_prev:
                issues.append(cls._rule_issue(scene.scene_id, "rule.first_shot_continuity", "continuity", "critical", f"镜头 {scene.scene_id} 是首镜，不能标记为承接上一镜。", "取消首镜的承接标记，或调整镜头顺序。", snapshot, scene.continuity_reason or str(scene.continuity_from_prev), "structured_script"))
        return issues

    @staticmethod
    def _valid_asset_ids(manifest):
        if not manifest:
            return None
        return {"character": {item.asset_id for item in manifest.characters}, "location": {item.location_id for item in manifest.locations}, "scene": {item.location_id for item in manifest.locations}, "prop": {item.prop_id for item in manifest.props}}

    @staticmethod
    def _asset_token_valid(kind, asset, valid_assets):
        if valid_assets is None:
            return True
        aliases = {"char": "character", "characters": "character", "locations": "location", "props": "prop"}
        return asset in valid_assets.get(aliases.get(kind.lower(), kind.lower()), set())

    @classmethod
    def _rule_issue(cls, scene_id, issue_code, category, severity, description, suggestion, snapshot, excerpt, source="rule", extra_evidence=None, fact_ids=None, checked_prompt_sections=None):
        fact_suffix = {
            "rule.prompt_missing": "action",
            "rule.prompt_empty": "action",
            "rule.duration_conflict": "duration",
            "rule.first_shot_continuity": "continuity",
        }.get(issue_code, "")
        primary_fact_id = f"scene:{scene_id}:{fact_suffix}" if source == "structured_script" and scene_id and fact_suffix else ""
        evidence_items = cls._rule_evidence(snapshot, scene_id, excerpt, source, primary_fact_id)
        evidence_items.extend(extra_evidence or [])
        if fact_ids is None:
            fact_ids = sorted({item.fact_id for item in evidence_items if item.fact_id})
        issue = PromptReviewIssue(scene_id=scene_id, severity=severity, category=category, description=description, suggestion=suggestion, evidence_items=evidence_items, layer="prompt", scope="rule", issue_code=issue_code, confidence="confirmed", fact_ids=fact_ids, checked_prompt_sections=checked_prompt_sections or [], repair_action="edit_prompt", rule_id=issue_code, deterministic=True, blocks_generation=True, candidate_id=issue_code + ":" + str(scene_id))
        cls._validate_issue_evidence(issue, snapshot, "rule", require_semantic_pair=False)
        return issue

    @classmethod
    def _apply_deterministic_checks(cls, script, prompts, result):
        snapshot = cls.build_audit_snapshot(script, prompts, None, PromptPolicy())
        deterministic = cls._deterministic_issues(script, prompts, snapshot)
        if result.report_version == 1 and not result.audit_signature:
            # Preserve v1 provider scores for old direct callers. The new job
            # path always uses _finalize_report and therefore deterministic
            # duration-weighted scoring.
            result.issues = cls._deduplicate_issues([
                *result.issues,
                *[issue for shot in result.shots for issue in shot.issues],
                *deterministic,
            ])
            if any(issue.severity == "critical" for issue in result.issues):
                result.passed = False
                result.overall_score = min(result.overall_score, 59)
            return result
        issues = cls._deduplicate_issues([
            *result.issues,
            *[issue for shot in result.shots for issue in shot.issues],
            *deterministic,
        ])
        return cls._finalize_report(script, snapshot, issues, result.audit_signature or "")

    @classmethod
    def _finalize_report(cls, script, snapshot, issues, audit_signature=""):
        issues = cls._deduplicate_issues(issues)
        primary_by_scene = defaultdict(list)
        scoring_by_scene = defaultdict(list)
        index_by_scene = defaultdict(list)
        for issue in issues:
            if issue.scene_id:
                primary_by_scene[issue.scene_id].append(issue)
                index_by_scene[issue.scene_id].append(issue)
                scoring_by_scene[issue.scene_id].append(issue)
            for related_scene_id in set(issue.related_scene_ids):
                if related_scene_id and related_scene_id != issue.scene_id:
                    if issue.scope != "single_shot":
                        index_by_scene[related_scene_id].append(issue)
                    if issue.scope == "cross_shot":
                        scoring_by_scene[related_scene_id].append(issue)
        shots, weighted_total, total_duration = [], 0.0, 0
        has_unreviewed = any(issue.confidence == "unreviewed" for issue in issues)
        has_blocking = any(
            issue.deterministic and issue.blocks_generation and issue.confidence == "confirmed"
            for issue in issues
        )
        has_major_or_critical = any(
            issue.confidence == "confirmed" and issue.severity in {"major", "critical"}
            for issue in issues
        )
        for scene in script.scenes:
            scene_issues = index_by_scene.get(scene.scene_id, [])
            scoring_issues = scoring_by_scene.get(scene.scene_id, [])
            confirmed = [item for item in scoring_issues if item.confidence == "confirmed"]
            penalty = sum({"critical": 40, "major": 15, "minor": 5}[item.severity] for item in confirmed)
            score = max(0, 100 - penalty)
            has_major_or_critical |= any(item.severity in {"major", "critical"} for item in confirmed)
            duration = int(scene.duration_sec)
            weighted_total += score * duration
            total_duration += duration
            shots.append(PromptReviewShot(scene_id=scene.scene_id, score=score, passed=score >= 85 and not any(item.severity in {"major", "critical"} for item in confirmed), summary="" if not scene_issues else "；".join(item.description for item in scene_issues)[:1000], fact_sheet_id=f"scene:{scene.scene_id}", duration_sec=duration, issues=scene_issues))
        overall = round(weighted_total / total_duration) if total_duration else 0
        if has_blocking:
            verdict = "blocked"
        elif has_unreviewed:
            verdict = "unreviewed"
        elif has_major_or_critical or any(shot.score < 85 for shot in shots):
            verdict = "needs_review"
        else:
            verdict = "pass"
        return PromptReviewResult(report_version=2, audit_signature=audit_signature, verdict=verdict, overall_score=overall, passed=verdict == "pass", summary=cls._summary_for_verdict(verdict, issues), shots=shots, issues=issues, shots_issue_index={str(key): value for key, value in index_by_scene.items()}, audit_snapshot=snapshot)

    @staticmethod
    def _summary_for_verdict(verdict, issues):
        if verdict == "pass":
            return "所有镜头通过结构与语义审核。"
        if verdict == "blocked":
            return "存在确定性结构错误，已阻断继续生成。"
        if verdict == "unreviewed":
            return "部分审核输出未能通过格式或证据校验，不能据此判定通过。"
        return f"发现 {sum(item.confidence == 'confirmed' for item in issues)} 项已确认问题，建议人工处理后继续。"

    @staticmethod
    def _deduplicate_issues(issues: Iterable[PromptReviewIssue]):
        result, positions = [], {}
        confidence_rank = {"unreviewed": 0, "suspected": 1, "confirmed": 2, "rejected": -1}
        for issue in issues:
            key = (
                issue.layer, issue.scope, issue.scene_id,
                tuple(sorted(set(issue.related_scene_ids))), issue.issue_code,
                tuple(sorted(set(issue.fact_ids))),
            )
            if key not in positions:
                positions[key] = len(result)
                result.append(issue)
                continue
            index = positions[key]
            current = result[index]
            merged_evidence = list(current.evidence_items)
            evidence_keys = {
                (item.source, item.scene_id, item.fact_id, item.excerpt)
                for item in merged_evidence
            }
            for item in issue.evidence_items:
                evidence_key = (item.source, item.scene_id, item.fact_id, item.excerpt)
                if evidence_key not in evidence_keys:
                    evidence_keys.add(evidence_key)
                    merged_evidence.append(item)
            result[index] = current.model_copy(update={
                "severity": issue.severity
                if {"minor": 1, "major": 2, "critical": 3}[issue.severity]
                > {"minor": 1, "major": 2, "critical": 3}[current.severity]
                else current.severity,
                "confidence": issue.confidence
                if confidence_rank.get(issue.confidence, 0) > confidence_rank.get(current.confidence, 0)
                else current.confidence,
                "fact_ids": sorted(set(current.fact_ids) | set(issue.fact_ids)),
                "related_scene_ids": sorted(set(current.related_scene_ids) | set(issue.related_scene_ids)),
                "evidence_items": merged_evidence,
                "checked_prompt_sections": sorted(set(current.checked_prompt_sections) | set(issue.checked_prompt_sections)),
                "deterministic": current.deterministic or issue.deterministic,
                "blocks_generation": current.blocks_generation or issue.blocks_generation,
            })
        return result

    @staticmethod
    def _normalize_review_payload(payload):
        aliases = {"coverage": "coverage", "completeness": "coverage", "完整性": "coverage", "fidelity": "fidelity", "accuracy": "fidelity", "faithfulness": "fidelity", "忠实度": "fidelity", "clarity": "clarity", "readability": "clarity", "清晰度": "clarity", "continuity": "continuity", "consistency": "continuity", "连贯性": "continuity", "连续性": "continuity", "executability": "executability", "feasibility": "executability", "implementability": "executability", "practicality": "executability", "execution": "executability", "可执行性": "executability", "可行性": "executability", "quality": "quality", "technical_quality": "quality", "整体质量": "quality", "质量": "quality"}
        def normalize(issue):
            if isinstance(issue, dict):
                raw = str(issue.get("category") or "").strip().lower().replace("-", "_").replace(" ", "_")
                issue["category"] = aliases.get(raw, "quality")
        normalized = dict(payload)
        for issue in normalized.get("issues") or []:
            normalize(issue)
        for shot in normalized.get("shots") or []:
            if isinstance(shot, dict):
                for issue in shot.get("issues") or []:
                    normalize(issue)
        return normalized


class _ReviewFormatError(ValueError):
    """Provider output violates the v2 contract and needs a format retry."""


__all__ = ["PromptReviewAgent"]
