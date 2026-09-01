"""Deterministic, non-mutating shot content capacity diagnostics."""

from __future__ import annotations

import math
import re
from typing import Any

from src.schemas.prompt import ShotCapacityDiagnostic, ShotPrompt
from src.schemas.script import Scene

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
    text = " ".join([
        str(getattr(scene, "camera_direction", "") or ""),
        str(getattr(prompt, "prompt", "") or "") if prompt else "",
    ])
    return len(_CAMERA_CUE_RE.findall(text))


def analyze_shot_capacity(
    scene: Scene,
    prompt: ShotPrompt | None = None,
    provider_max_duration_sec: int | float | None = None,
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

    # This is deliberately conservative: it is a warning heuristic, not a semantic
    # verdict and never silently edits the source schedule.
    estimated_sec = max(3.0, action_units * 1.8 + dialogue_chars / 10.0 + prop_changes * 0.8 + max(0, camera_changes - 1) * 0.6)
    warnings: list[str] = []
    if provider_cap is not None and scene_duration > provider_cap:
        warnings.append(f"镜头时长 {scene_duration}s 超过当前模型上限 {provider_cap}s")
    if effective_duration and estimated_sec > effective_duration:
        warnings.append(f"预计承载约 {math.ceil(estimated_sec)}s 内容，但可用时长仅 {effective_duration}s")
    suggested_shot_count = max(1, math.ceil(estimated_sec / max(1, effective_duration or scene_duration or 1)))
    suggested_duration = max(3, math.ceil(estimated_sec))
    if provider_cap is not None:
        suggested_duration = min(suggested_duration, provider_cap)
    return ShotCapacityDiagnostic(
        scene_id=int(scene.scene_id),
        status="warning" if warnings else "clean",
        action_units=action_units,
        dialogue_chars=dialogue_chars,
        prop_state_changes=prop_changes,
        camera_changes=camera_changes,
        scene_duration_sec=scene_duration,
        effective_duration_sec=effective_duration,
        provider_max_duration_sec=provider_cap,
        suggested_shot_count=suggested_shot_count,
        suggested_duration_sec=suggested_duration,
        warnings=warnings,
    )
