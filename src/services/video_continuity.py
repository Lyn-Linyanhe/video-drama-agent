"""Tail-frame continuity helpers for versioned production shots."""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from src.config import OUTPUT_DIR
from src.utils.video import run_ffmpeg


def active_video_version(state, scene_id: int, shot_id: str | None = None):
    production = state.shot_productions.get(scene_id)
    if not production:
        return None
    if shot_id:
        matching = [
            item for item in production.video_versions
            if str(getattr(item, "shot_id", "") or "") == str(shot_id)
            and item.success and item.local_path
        ]
        active_id = str(getattr(production, "active_version_by_shot_id", {}).get(str(shot_id), "") or "")
        if active_id:
            active = next((item for item in matching if str(getattr(item, "version_id", "")) == active_id), None)
            if active is not None:
                return active
        return matching[-1] if matching else None
    return next((item for item in production.video_versions if item.version_id == production.active_version_id), None)


def extract_tail_frame(project_id: str, scene_id: int, version) -> str:
    if not version or not version.local_path or not Path(version.local_path).is_file():
        raise HTTPException(409, "连续镜头缺少上一镜的有效视频版本")
    existing = str(getattr(version, "tail_frame_path", "") or "")
    if existing and Path(existing).is_file():
        return existing
    duration = max(0.0, float(version.duration_sec or 0.0))
    seek = max(0.0, duration - 0.12)
    output = OUTPUT_DIR / project_id / "assets" / "videos" / "continuity" / f"shot_{scene_id:03d}_{version.version_id}_tail.jpg"
    output.parent.mkdir(parents=True, exist_ok=True)
    result = run_ffmpeg([
        "-y", "-ss", f"{seek:.3f}", "-i", str(version.local_path), "-frames:v", "1",
        "-q:v", "2", str(output),
    ], timeout=90)
    if result.returncode != 0 or not output.is_file():
        raise HTTPException(502, f"上一镜尾帧提取失败: {result.stderr[-300:]}")
    version.tail_frame_path = str(output)
    return str(output)


def continuity_reference(
    state,
    scene_id: int,
    policy: dict,
    shot_id: str | None = None,
    continuity_from_shot_id: str | None = None,
):
    production = state.shot_productions.get(scene_id)
    if not production:
        return "", ""
    explicit_child_dependency = continuity_from_shot_id is not None
    if not production.continuity_enabled and not explicit_child_dependency:
        return "", ""
    if explicit_child_dependency and not str(continuity_from_shot_id or "").strip():
        return "", ""
    if not policy["capability"].get("continuity"):
        raise HTTPException(409, f"{policy['model']} 不支持首帧参考，无法生成连续镜头")
    source_scene_id = scene_id
    source_shot_id = ""
    label = ""
    if explicit_child_dependency:
        source_shot_id = str(continuity_from_shot_id).strip()
        match = __import__("re").search(r"^scene-(\d+)-", source_shot_id)
        if match:
            source_scene_id = int(match.group(1))
        label = f"上一子镜头 {source_shot_id} 的末帧（最高优先级连续性首图）"
    else:
        source_scene_id = production.continuity_source_scene_id or scene_id - 1
        if source_scene_id < 1:
            return "", ""
        label = f"上一镜 {source_scene_id} 的末帧（最高优先级连续性首图）"
    source = active_video_version(state, source_scene_id, source_shot_id or None)
    if not source:
        raise HTTPException(409, "请先生成上一镜，再生成需要连续性的本镜")
    path = extract_tail_frame(state.project_id, source_scene_id, source)
    production.continuity_source_scene_id = source_scene_id
    production.continuity_source_version_id = source.version_id
    production.continuity_tail_frame_path = path
    production.continuity_stale = False
    return path, label


def mark_dependents_stale(
    state,
    source_scene_id: int,
    source_version_id: str,
    source_shot_id: str | None = None,
) -> None:
    for scene_id, production in state.shot_productions.items():
        if source_shot_id:
            child_dependents = [
                item for item in (getattr(production, "video_versions", []) or [])
                if str(getattr(item, "continuity_from_shot_id", "") or "") == str(source_shot_id)
                and str(getattr(item, "continuity_source_version_id", "") or "") != str(source_version_id)
            ]
            if child_dependents:
                production.continuity_stale = True
        if not production.continuity_enabled:
            continue
        expected_source = production.continuity_source_scene_id or scene_id - 1
        if expected_source == source_scene_id and production.continuity_source_version_id and production.continuity_source_version_id != source_version_id:
            production.continuity_stale = True
