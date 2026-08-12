"""Derive and persist required scene reference views."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, Field


SCENE_VIEW_ORDER = ("scene_center", "scene_left_45", "scene_right_45", "scene_reverse")
COMPLEX_SPACE_CUES = (
    "空间结构复杂", "复杂空间", "关键地点", "四角度", "四个角度", "四视角", "四视图", "全方位",
)
REVERSE_CUES = (
    "深处向外", "向外拍摄", "向出口", "出口方向", "反向", "背后", "回头", "从里向外", "由内向外",
)
LEFT_CUES = (
    "左前", "左侧", "左边", "从左", "左45", "左 45", "左前45", "左前 45", "左前方",
)
RIGHT_CUES = (
    "右前", "右侧", "右边", "从右", "右45", "右 45", "右前45", "右前 45", "右前方",
)


class SceneViewRequirement(BaseModel):
    location_id: str
    state_id: str = ""
    views: tuple[str, ...] = ("scene_center",)
    reasons: list[str] = Field(default_factory=list)
    source: str = "auto"
    signature: str = ""


def scene_requirement_key(location_id: str, state_id: str = "") -> str:
    return f"{location_id}:{state_id or 'default'}"


def normalize_scene_views(views: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    invalid = [view for view in views if view not in SCENE_VIEW_ORDER]
    if invalid:
        raise ValueError("Invalid scene view")
    selected = {"scene_center", *views}
    return tuple(view for view in SCENE_VIEW_ORDER if view in selected)


def _signature(location_id: str, state_id: str, views: tuple[str, ...]) -> str:
    payload = "|".join([location_id, state_id or "default", *views])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _location_and_state(state: Any, location_id: str, state_id: str = ""):
    manifest = getattr(getattr(state, "script", None), "asset_manifest", None)
    location = next((item for item in (getattr(manifest, "locations", []) or []) if item.location_id == location_id), None)
    if not location:
        return None, None
    resolved_state_id = state_id or getattr(location, "default_state_id", "")
    location_state = next((item for item in getattr(location, "states", []) if item.state_id == resolved_state_id), None)
    return location, location_state


def _shot_ids_for_state(location, location_state) -> list[int]:
    if location_state and getattr(location_state, "shot_ids", None):
        return list(location_state.shot_ids)
    return list(getattr(location, "shot_ids", []) or [])


def _contains_any(text: str, cues: tuple[str, ...]) -> bool:
    return any(cue in text for cue in cues)


def derive_scene_view_requirements(state: Any, location_id: str, state_id: str = "") -> SceneViewRequirement:
    location, location_state = _location_and_state(state, location_id, state_id)
    resolved_state_id = (state_id or getattr(location, "default_state_id", "")) if location else state_id
    saved = (getattr(state, "scene_view_requirements", {}) or {}).get(scene_requirement_key(location_id, resolved_state_id))
    if isinstance(saved, SceneViewRequirement):
        return saved
    if isinstance(saved, dict) and saved.get("source") == "manual":
        views = normalize_scene_views(tuple(saved.get("views") or ()))
        return SceneViewRequirement(
            location_id=location_id,
            state_id=resolved_state_id,
            views=views,
            reasons=list(saved.get("reasons") or ["manual"]),
            source="manual",
            signature=_signature(location_id, resolved_state_id, views),
        )

    shot_ids = set(_shot_ids_for_state(location, location_state)) if location else set()
    scenes = [
        scene for scene in (getattr(getattr(state, "script", None), "scenes", []) or [])
        if scene.scene_id in shot_ids
    ]
    text = " ".join(
        " ".join([
            str(getattr(scene, "camera_direction", "") or ""),
            str(getattr(scene, "action_description", "") or ""),
            str(getattr(scene, "location", "") or ""),
        ])
        for scene in scenes
    )

    views = ["scene_center"]
    reasons = ["default_center"]
    if _contains_any(text, COMPLEX_SPACE_CUES):
        views = list(SCENE_VIEW_ORDER)
        reasons.append("complex_space")
    else:
        if _contains_any(text, REVERSE_CUES):
            views.append("scene_reverse")
            reasons.append("reverse_cue")
        if _contains_any(text, LEFT_CUES) and _contains_any(text, RIGHT_CUES):
            views.extend(["scene_left_45", "scene_right_45"])
            reasons.append("left_right_cues")

    normalized = normalize_scene_views(tuple(views))
    return SceneViewRequirement(
        location_id=location_id,
        state_id=resolved_state_id,
        views=normalized,
        reasons=reasons,
        source="auto",
        signature=_signature(location_id, resolved_state_id, normalized),
    )


def set_scene_view_requirements(state: Any, location_id: str, state_id: str, views: list[str]) -> SceneViewRequirement:
    location, location_state = _location_and_state(state, location_id, state_id)
    if not location:
        raise ValueError("Scene asset not found")
    resolved_state_id = state_id or getattr(location_state, "state_id", "") or getattr(location, "default_state_id", "")
    normalized = normalize_scene_views(tuple(views))
    requirement = SceneViewRequirement(
        location_id=location_id,
        state_id=resolved_state_id,
        views=normalized,
        reasons=["manual"],
        source="manual",
        signature=_signature(location_id, resolved_state_id, normalized),
    )
    state.scene_view_requirements[scene_requirement_key(location_id, resolved_state_id)] = requirement.model_dump()
    return requirement
