"""Shared render-time timeline calculations for video, audio and subtitles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence


@dataclass(frozen=True)
class RenderTimeline:
    """Actual project-time positions for the ordered scenes being rendered."""

    scene_starts_ms: dict[int, int]
    scene_durations_ms: dict[int, int]
    overlap_ms: dict[int, int]
    total_duration_ms: int


def _duration_seconds(value: object, fallback: float = 0.1) -> float:
    try:
        duration = float(value)
    except (TypeError, ValueError):
        duration = fallback
    return max(0.1, duration)


def build_render_timeline(
    scenes: Sequence[object],
    clip_durations: Mapping[int, float] | None = None,
) -> RenderTimeline:
    """Build one timeline used by visual and audio renderers.

    ``clip_durations`` contains measured provider output durations. Missing
    entries fall back to the script duration. Legacy fade/dissolve values are
    still honored for old projects; new scripts are normalized to ``cut``.
    """

    clip_durations = clip_durations or {}
    starts: dict[int, int] = {}
    durations: dict[int, int] = {}
    overlaps: dict[int, int] = {}
    elapsed = 0.0
    previous_duration = 0.0

    for index, scene in enumerate(scenes):
        scene_id = int(getattr(scene, "scene_id"))
        measured = clip_durations.get(scene_id)
        duration = _duration_seconds(
            measured if measured is not None else getattr(scene, "duration_sec", 3),
            fallback=3.0,
        )
        transition = str(getattr(scene, "transition_from_prev", "cut") or "cut").lower()
        requested = _duration_seconds(getattr(scene, "transition_duration_sec", 0.25), fallback=0.25)
        overlap = 0.0
        if index and transition in {"fade", "dissolve"}:
            overlap = min(requested, previous_duration / 2.0, duration / 2.0)

        start = max(0.0, elapsed - overlap)
        starts[scene_id] = int(round(start * 1000))
        durations[scene_id] = int(round(duration * 1000))
        overlaps[scene_id] = int(round(overlap * 1000))
        elapsed = start + duration
        previous_duration = duration

    return RenderTimeline(
        scene_starts_ms=starts,
        scene_durations_ms=durations,
        overlap_ms=overlaps,
        total_duration_ms=max(0, int(round(elapsed * 1000))),
    )


def active_video_durations(state: object) -> dict[int, float]:
    """Return measured duration per parent scene, summing executable children."""
    candidates: dict[int, list[object]] = {}
    productions = getattr(state, "shot_productions", {}) or {}
    for scene_id, production in productions.items():
        versions = getattr(production, "video_versions", []) or []
        child_versions = [
            item for item in versions
            if getattr(item, "shot_id", None) and item.success and item.local_path
        ]
        if child_versions:
            active_by_shot = getattr(production, "active_version_by_shot_id", {}) or {}
            selected_by_shot: dict[str, object] = {}
            for item in child_versions:
                shot_id = str(getattr(item, "shot_id", "") or "")
                active_id = str(active_by_shot.get(shot_id, "") or "")
                if active_id:
                    if str(getattr(item, "version_id", "")) == active_id:
                        selected_by_shot[shot_id] = item
                else:
                    # Legacy child records have no per-shot active map; the
                    # last retained successful version is the readable fallback.
                    selected_by_shot[shot_id] = item
            child_versions = list(selected_by_shot.values())
            child_versions.sort(key=lambda item: _shot_order(item))
            candidates[int(scene_id)] = [item.local_path for item in child_versions]
            continue
        active_id = str(getattr(production, "active_version_id", "") or "")
        version = next((item for item in versions if str(getattr(item, "version_id", "")) == active_id), None)
        if version is None and versions:
            version = versions[-1]
        path = str(getattr(version, "local_path", "") or "") if version else ""
        if path:
            candidates[int(scene_id)] = [path]

    legacy = getattr(getattr(state, "generated_assets", None), "shots", []) or []
    for asset in legacy:
        scene_id = int(getattr(asset, "scene_id", 0) or 0)
        path = str(getattr(asset, "local_path", "") or "")
        if scene_id and path and scene_id not in candidates:
            candidates[scene_id] = [path]

    durations: dict[int, float] = {}
    for scene_id, paths in candidates.items():
        total = 0.0
        for path in paths:
            try:
                from moviepy import VideoFileClip
                clip = VideoFileClip(path)
                duration = float(clip.duration or 0.0)
                clip.close()
                total += max(0.0, duration)
            except Exception:
                continue
        if total > 0:
            durations[scene_id] = total
    return durations


def _shot_order(asset: object) -> tuple[int, str]:
    explicit_order = getattr(asset, "shot_order", None)
    if explicit_order is not None:
        try:
            return (int(explicit_order), str(getattr(asset, "shot_id", "") or ""))
        except (TypeError, ValueError):
            pass
    import re
    shot_id = str(getattr(asset, "shot_id", "") or "")
    match = re.search(r"(?:shot|unit)-(\d+)$", shot_id)
    return (int(match.group(1)) if match else 10**9, shot_id)
