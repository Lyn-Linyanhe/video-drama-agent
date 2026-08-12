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
    """Best-effort measured durations for a project audio preview.

    The function reads the active shot version first, then legacy generated
    assets. Projects without local video files simply fall back to script
    durations in ``build_render_timeline``.
    """

    candidates: dict[int, str] = {}
    productions = getattr(state, "shot_productions", {}) or {}
    for scene_id, production in productions.items():
        active_id = str(getattr(production, "active_version_id", "") or "")
        versions = getattr(production, "video_versions", []) or []
        version = next((item for item in versions if str(getattr(item, "version_id", "")) == active_id), None)
        if version is None and versions:
            version = versions[-1]
        path = str(getattr(version, "local_path", "") or "") if version else ""
        if path:
            candidates[int(scene_id)] = path

    legacy = getattr(getattr(state, "generated_assets", None), "shots", []) or []
    for asset in legacy:
        scene_id = int(getattr(asset, "scene_id", 0) or 0)
        if scene_id and scene_id not in candidates:
            path = str(getattr(asset, "local_path", "") or "")
            if path:
                candidates[scene_id] = path

    durations: dict[int, float] = {}
    for scene_id, path in candidates.items():
        try:
            from moviepy import VideoFileClip

            clip = VideoFileClip(path)
            duration = float(clip.duration or 0.0)
            clip.close()
            if duration > 0:
                durations[scene_id] = duration
        except Exception:
            continue
    return durations
