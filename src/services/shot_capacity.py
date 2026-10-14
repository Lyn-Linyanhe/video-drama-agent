"""Deterministic, non-mutating shot content capacity diagnostics."""

from __future__ import annotations

import math
import re
from typing import Any, Iterable

from src.schemas.prompt import ShotCapacityDiagnostic, ShotPrompt
from src.schemas.script import Scene
from src.services.beat_planner import build_narrative_beats, estimate_scene_capacity

_ACTION_SPLIT_RE = re.compile(r"[。！？!?；;，,、\n]+")
_PROP_CHANGE_RE = re.compile(r"拿起|放下|打开|关闭|推开|锁上|解锁|递给|接过|交给|戴上|摘下|藏|取出|塞进|换成|摔碎|点燃|熄灭|拔出|插入")
_CAMERA_CUE_RE = re.compile(r"近景|特写|中景|远景|全景|俯拍|仰拍|推镜|拉远|摇镜|跟拍|切镜头|反打|移镜")
_TIMELINE_RE = re.compile(r"(?:\d+(?:\.\d+)?\s*(?:s|秒)\s*(?:-|到)\s*\d+(?:\.\d+)?\s*(?:s|秒))", re.I)


def _clauses(text: str) -> list[str]:
    values = [part.strip(" ，,、") for part in _ACTION_SPLIT_RE.split(str(text or ""))]
    return [part for part in values if part]


def _count_action_units(scene: Scene, prompt: ShotPrompt | None) -> int:
    clauses = _clauses(getattr(scene, "action_description", ""))
    timeline = _TIMELINE_RE.findall(str(getattr(prompt, "prompt", "") or "")) if prompt else []
    return max(len(clauses), len(timeline), 1 if clauses else 0)


def _count_prop_changes(text: str) -> int:
    return sum(1 for clause in _clauses(text) if _PROP_CHANGE_RE.search(clause))


def _count_camera_changes(scene: Scene, prompt: ShotPrompt | None) -> int:
    # Only inspect the source camera field and the dedicated camera section.
    # Project-wide style prose often contains words such as “推镜/拉远”;
    # counting those would inflate every shot and make the warning useless.
    camera_text = str(getattr(scene, "camera_direction", "") or "")
    if prompt:
        match = re.search(r"【镜头语言】(.*?)(?=\n【|$)", str(getattr(prompt, "prompt", "") or ""), re.S)
        if match:
            camera_text += " " + match.group(1)
    cues = list(dict.fromkeys(_CAMERA_CUE_RE.findall(camera_text)))
    return len(cues)



def analyze_shot_capacity(
    scene: Scene,
    prompt: ShotPrompt | None = None,
    provider_max_duration_sec: int | float | None = None,
    provider_durations: Iterable[int | float] | None = None,
    provider_min_duration_sec: int | float = 3,
) -> ShotCapacityDiagnostic:
    """Return advisory capacity facts without changing scene, prompt, or sub-shots."""
    scene_duration = max(0, int(getattr(scene, "duration_sec", 0) or 0))
    provider_cap = int(provider_max_duration_sec) if provider_max_duration_sec else None
    effective_duration = min(scene_duration, provider_cap) if provider_cap else scene_duration
    action_units = _count_action_units(scene, prompt)
    dialogue_chars = sum(len(str(getattr(line, "line", "") or "")) for line in (getattr(scene, "dialogue", []) or []))
    text = " ".join([str(getattr(scene, "action_description", "") or ""), *(str(getattr(line, "line", "") or "") for line in (getattr(scene, "dialogue", []) or []))])
    prop_changes = _count_prop_changes(text)
    camera_changes = _count_camera_changes(scene, prompt)

    # Keep the legacy diagnostic surface, but derive the new planning metrics from
    # the traceable beat planner. Required duration is never clamped to provider cap.
    report = estimate_scene_capacity(
        scene,
        provider_max_duration_sec=provider_cap,
        provider_durations=provider_durations,
        provider_min_duration_sec=provider_min_duration_sec,
    )
    estimated_sec = report.required_total_duration_sec
    warnings: list[str] = list(report.warnings)
    if provider_cap is not None and scene_duration > provider_cap:
        warnings.append(f"镜头时长 {scene_duration}s 超过当前模型上限 {provider_cap}s")
    if not warnings and effective_duration and estimated_sec > effective_duration:
        warnings.append(f"预计承载约 {math.ceil(estimated_sec)}s 内容，但可用时长仅 {effective_duration}s")
    warnings = list(dict.fromkeys(warnings))
    suggested_shot_count = report.recommended_shot_count
    provider_min = max(1, int(math.ceil(float(provider_min_duration_sec or 3))))
    suggested_duration = (
        min(provider_cap, max(provider_min, math.ceil(max(report.recommended_durations_sec))))
        if provider_cap is not None and report.recommended_durations_sec
        else (max(provider_min, math.ceil(max(report.recommended_durations_sec))) if report.recommended_durations_sec else 0)
    )
    # Evidence-based expansion remains advisory when the planner produced
    # legal provider durations. The UI should explain the added seconds rather
    # than present normal capacity expansion as a hard generation failure.
    hard_status = (
        report.status in {"dialogue_overflow", "beat_overflow", "timeline_invalid"}
        or (report.status == "duration_insufficient" and not report.recommended_durations_sec)
    )
    return ShotCapacityDiagnostic(
        scene_id=int(scene.scene_id),
        status="failed" if hard_status else ("warning" if warnings else "clean"),
        action_units=action_units,
        dialogue_chars=dialogue_chars,
        prop_state_changes=prop_changes,
        camera_changes=camera_changes,
        scene_duration_sec=scene_duration,
        effective_duration_sec=effective_duration,
        provider_max_duration_sec=provider_cap,
        suggested_shot_count=suggested_shot_count,
        suggested_duration_sec=suggested_duration,
        required_total_duration_sec=report.required_total_duration_sec,
        source_duration_sec=report.source_duration_sec or 0.0,
        minimum_duration_sec=report.minimum_duration_sec,
        expected_duration_sec=report.expected_duration_sec,
        maximum_duration_sec=report.maximum_duration_sec,
        serial_duration_sec=report.serial_duration_sec,
        overlap_duration_sec=report.overlap_duration_sec,
        metadata_duration_sec=report.metadata_duration_sec,
        timing_basis=report.timing_basis,
        recommended_total_duration_sec=report.recommended_total_duration_sec,
        recommended_durations_sec=report.recommended_durations_sec,
        estimated_dialogue_sec=sum(beat.duration.expected_sec for beat in build_narrative_beats(scene) if beat.kind in {"dialogue", "inner_monologue"}),
        action_required_sec=sum(beat.duration.expected_sec for beat in build_narrative_beats(scene) if beat.kind in {"action", "micro_action", "prop_change", "transition"}),
        emotional_turn_count=sum(1 for beat in build_narrative_beats(scene) if beat.kind == "emotion"),
        load_score=round(report.required_total_duration_sec / max(1.0, effective_duration or scene_duration or 1), 2),
        overloaded=report.status != "clean",
        planning_status=report.status,
        warnings=warnings,
    )
