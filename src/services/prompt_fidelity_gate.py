"""Shared deterministic gate for storyboard prompt fidelity before video generation."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from src.services.prompt_fidelity_audit import audit_prompt_fidelity
from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.prompt_fidelity import PromptFidelityEvidence, PromptFidelityIssue, PromptFidelityReport


def audit_current_prompt_fidelity(state: Any) -> PromptFidelityReport:
    """Audit the exact prompt set currently held by a project state.

    A missing script or prompt set is a hard failure: video generation must not
    silently proceed with an incomplete storyboard.
    """
    script = getattr(state, "script", None)
    prompts = getattr(state, "generation_prompts", None)
    if script is None or prompts is None:
        issue = PromptFidelityIssue(
            scene_id=0,
            issue_code="missing_storyboard_input",
            severity="critical",
            description="结构化剧本或最终分镜提示词不存在，无法安全生成视频",
            deterministic=True,
            blocks_generation=True,
            evidence=PromptFidelityEvidence(
                scene_id=0,
                fact_id="project:storyboard-input",
                expected_excerpt="结构化剧本与最终分镜提示词均已准备完成",
                prompt_excerpt="",
                prompt_section="",
            ),
        )
        return PromptFidelityReport(
            status="blocked",
            scene_count=len(getattr(script, "scenes", []) or []) if script else 0,
            shot_count=len(getattr(prompts, "shots", []) or []) if prompts else 0,
            issues=[issue],
        )
    return audit_prompt_fidelity(script, prompts)


def generation_snapshot_digest(snapshot: dict[str, Any]) -> str:
    """Return a stable digest for immutable prompt-bearing snapshot fields."""
    fields = {
        key: snapshot.get(key)
        for key in (
            "scene_id", "source_prompt", "source_duration_sec", "effective_duration_sec",
            "raw_prompt", "raw_negative_prompt", "prepared_shot", "provider",
            "effective_prompt", "effective_negative_prompt", "provider_prompt",
            "provider_negative_prompt", "negative_prompt_mode", "policy",
        )
    }
    return hashlib.sha256(json.dumps(fields, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _snapshot_issue(scene_id: int, code: str, description: str, expected: str, actual: str, section: str = "generation_snapshot") -> PromptFidelityIssue:
    return PromptFidelityIssue(
        scene_id=scene_id,
        issue_code=code,
        severity="critical",
        description=description,
        deterministic=True,
        blocks_generation=True,
        evidence=PromptFidelityEvidence(
            scene_id=scene_id,
            fact_id=f"snapshot:{scene_id}:{code}",
            expected_excerpt=expected or "快照应与当前任务镜头一致",
            prompt_excerpt=actual or "",
            prompt_section=section,
        ),
    )


def audit_generation_snapshot(state: Any, snapshot: dict[str, Any] | None, scene_id: int) -> PromptFidelityReport:
    """Audit a queued video's frozen shot against the current storyboard state.

    The project-level audit protects the current state; this second audit protects
    the exact payload that is about to reach a video provider.  It prevents an old
    queued task or a hand-crafted payload from using a prompt after the user has
    edited the storyboard.
    """
    base = audit_current_prompt_fidelity(state)
    issues = list(base.issues)
    snapshot = snapshot or {}
    requested_scene_id = int(snapshot.get("scene_id") or 0)
    if requested_scene_id != int(scene_id):
        issues.append(_snapshot_issue(
            scene_id,
            "snapshot_scene_mismatch",
            "视频任务快照的镜头编号与任务目标不一致",
            f"scene_id={scene_id}",
            f"scene_id={requested_scene_id}",
        ))

    current_shot = next(
        (shot for shot in (getattr(getattr(state, "generation_prompts", None), "shots", []) or [])
         if int(getattr(shot, "scene_id", 0) or 0) == int(scene_id)),
        None,
    )
    source_prompt = str(snapshot.get("source_prompt") or "").strip()
    raw_prompt = str(snapshot.get("raw_prompt") or "").strip()
    current_prompt = str(getattr(current_shot, "prompt", "") or "").strip()
    if not source_prompt:
        issues.append(_snapshot_issue(
            scene_id,
            "snapshot_source_prompt_missing",
            "视频任务快照没有记录创建时的源提示词",
            "source_prompt 非空",
            raw_prompt,
            "source_prompt",
        ))
    elif not current_prompt or " ".join(current_prompt.split()) != " ".join(source_prompt.split()):
        issues.append(_snapshot_issue(
            scene_id,
            "snapshot_prompt_mismatch",
            "视频任务快照对应的源提示词不是当前结构化剧本的最新提示词",
            current_prompt,
            source_prompt,
            "source_prompt",
        ))
    if not raw_prompt:
        issues.append(_snapshot_issue(
            scene_id,
            "snapshot_prompt_missing",
            "视频任务快照缺少最终提示词",
            source_prompt or current_prompt or "当前镜头存在最终提示词",
            "",
            "raw_prompt",
        ))

    prepared_data = snapshot.get("prepared_shot")
    try:
        prepared = ShotPrompt.model_validate(prepared_data or {})
    except Exception:
        prepared = None
    if prepared is None:
        issues.append(_snapshot_issue(
            scene_id,
            "snapshot_prepared_shot_invalid",
            "视频任务快照缺少可验证的 prepared_shot",
            "prepared_shot.scene_id、duration_sec、prompt 可被解析",
            str(prepared_data or ""),
            "prepared_shot",
        ))
    else:
        if int(prepared.scene_id or 0) != int(scene_id):
            issues.append(_snapshot_issue(
                scene_id,
                "snapshot_prepared_scene_mismatch",
                "prepared_shot 的镜头编号与任务目标不一致",
                f"scene_id={scene_id}",
                f"scene_id={prepared.scene_id}",
                "prepared_shot",
            ))
        prepared_prompt = str(prepared.prompt or "").strip()
        if not prepared_prompt:
            issues.append(_snapshot_issue(
                scene_id,
                "snapshot_prepared_prompt_missing",
                "prepared_shot 没有最终提示词",
                "prepared_shot.prompt 非空",
                "",
                "prepared_shot",
            ))
        expected_duration = snapshot.get("effective_duration_sec")
        if expected_duration is None:
            expected_duration = snapshot.get("source_duration_sec") or getattr(current_shot, "duration_sec", 0)
        try:
            expected_duration = int(expected_duration or 0)
        except (TypeError, ValueError):
            expected_duration = 0
        if expected_duration and int(prepared.duration_sec or 0) != expected_duration:
            issues.append(_snapshot_issue(
                scene_id,
                "snapshot_duration_mismatch",
                "prepared_shot 时长与任务快照记录的有效时长不一致",
                f"duration_sec={expected_duration}",
                f"duration_sec={prepared.duration_sec}",
                "prepared_shot",
            ))

    provider_prompt = str(snapshot.get("provider_prompt") or "").strip()
    if not provider_prompt:
        issues.append(_snapshot_issue(
            scene_id,
            "snapshot_provider_prompt_missing",
            "视频任务快照没有即将提交给供应商的最终提示词",
            "provider_prompt 非空",
            "",
            "provider_prompt",
        ))

    if prepared is not None and provider_prompt:
        prepared_text = " ".join(str(prepared.prompt or "").split())
        provider_text = " ".join(provider_prompt.split())
        if prepared_text and prepared_text not in provider_text and provider_text not in prepared_text:
            issues.append(_snapshot_issue(
                scene_id,
                "snapshot_provider_prompt_mismatch",
                "provider_prompt 与 prepared_shot 不是同一份最终提示词链路",
                str(prepared.prompt or ""),
                provider_prompt,
                "provider_prompt",
            ))

    expected_digest = str(snapshot.get("prompt_snapshot_digest") or "").strip()
    if not expected_digest:
        issues.append(_snapshot_issue(
            scene_id,
            "snapshot_digest_missing",
            "视频任务快照缺少提示词完整性摘要，无法确认其内容未被替换",
            "prompt_snapshot_digest 非空",
            "",
            "prompt_snapshot_digest",
        ))
    elif expected_digest != generation_snapshot_digest(snapshot):
        issues.append(_snapshot_issue(
            scene_id,
            "snapshot_digest_mismatch",
            "视频任务快照的提示词完整性摘要与内容不一致",
            expected_digest,
            generation_snapshot_digest(snapshot),
            "prompt_snapshot_digest",
        ))

    status = "blocked" if any(issue.blocks_generation for issue in issues) else ("warning" if issues else "clean")
    return base.model_copy(update={"status": status, "issues": issues})


def prompt_fidelity_gate_detail(report: PromptFidelityReport | None) -> dict[str, Any] | None:
    """Return a structured HTTP error detail when a report blocks generation."""
    if report is None or not report.blocked:
        return None
    return {
        "code": "prompt_fidelity_blocked",
        "message": "提示词与结构化剧本存在确定性保真问题，禁止生成视频",
        "status": report.status,
        "issues": [issue.model_dump() for issue in report.issues if issue.blocks_generation],
        "report": report.model_dump(),
    }
