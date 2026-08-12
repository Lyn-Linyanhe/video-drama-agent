"""Derive and persist required prop reference views."""

from __future__ import annotations

import hashlib
from typing import Any

from pydantic import BaseModel, Field


PROP_VIEW_ORDER = ("front", "back", "left", "right", "top", "bottom")


class PropViewRequirement(BaseModel):
    prop_id: str
    views: tuple[str, ...] = ("front",)
    reasons: list[str] = Field(default_factory=list)
    source: str = "auto"
    signature: str = ""


def normalize_prop_views(views: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    selected = {"front", *[view for view in views if view in PROP_VIEW_ORDER]}
    return tuple(view for view in PROP_VIEW_ORDER if view in selected)


def _signature(prop_id: str, views: tuple[str, ...]) -> str:
    return hashlib.sha256("|".join([prop_id, *views]).encode("utf-8")).hexdigest()[:16]


def merge_prop_view_requirement(
    saved: PropViewRequirement | dict | None,
    auto_views: list[str] | tuple[str, ...],
    prop_id: str = "",
    auto_reasons: list[str] | None = None,
) -> PropViewRequirement:
    saved_model: PropViewRequirement | None = None
    if isinstance(saved, PropViewRequirement):
        saved_model = saved
    elif isinstance(saved, dict):
        saved_model = PropViewRequirement.model_validate(saved)

    saved_views = tuple(saved_model.views) if saved_model else ()
    views = normalize_prop_views(tuple(auto_views) + saved_views)
    source = "manual" if saved_model and saved_model.source == "manual" else "auto"
    reasons = list(auto_reasons or [])
    if source == "manual":
        reasons.extend(reason for reason in (saved_model.reasons if saved_model else []) if reason not in reasons)
        if "manual" not in reasons:
            reasons.append("manual")
    return PropViewRequirement(
        prop_id=prop_id or (saved_model.prop_id if saved_model else ""),
        views=views,
        reasons=reasons or ["default_front"],
        source=source,
        signature=_signature(prop_id or (saved_model.prop_id if saved_model else ""), views),
    )


def infer_prop_view_requirement(state: Any, prop_id: str) -> PropViewRequirement:
    saved = (getattr(state, "prop_view_requirements", {}) or {}).get(prop_id)
    script = getattr(state, "script", None)
    manifest = getattr(script, "asset_manifest", None)
    prop = next((item for item in (getattr(manifest, "props", []) or []) if item.prop_id == prop_id), None)
    if not prop:
        raise ValueError("Prop asset not found")
    shot_ids = set(getattr(prop, "shot_ids", []) or [])
    scenes = [scene for scene in (getattr(script, "scenes", []) or []) if scene.scene_id in shot_ids]
    text = " ".join(
        " ".join([
            str(getattr(scene, "camera_direction", "") or ""),
            str(getattr(scene, "action_description", "") or ""),
        ])
        for scene in scenes
    ).lower()
    views = ["front"]
    reasons = ["default_front"]
    if any(token in text for token in ("back", "rear", "behind", "背面", "背后", "翻到背面", "反面")):
        views.append("back")
        reasons.append("back_cue")
    if any(token in text for token in ("left side", "left-side", "from left", "左侧", "左边", "从左")):
        views.append("left")
        reasons.append("left_cue")
    if any(token in text for token in ("right side", "right-side", "from right", "右侧", "右边", "从右")):
        views.append("right")
        reasons.append("right_cue")
    if any(token in text for token in ("top", "overhead", "from above", "俯拍", "顶部", "上方", "顶面")):
        views.append("top")
        reasons.append("top_cue")
    if any(token in text for token in ("bottom", "underside", "from below", "底部", "底面", "下方")):
        views.append("bottom")
        reasons.append("bottom_cue")
    normalized = normalize_prop_views(tuple(views))
    return merge_prop_view_requirement(saved, normalized, prop_id, reasons)


def set_prop_view_requirement(state: Any, prop_id: str, views: list[str]) -> PropViewRequirement:
    if any(view not in PROP_VIEW_ORDER for view in views):
        raise ValueError("Invalid prop view")
    manifest = getattr(getattr(state, "script", None), "asset_manifest", None)
    if not any(item.prop_id == prop_id for item in (getattr(manifest, "props", []) or [])):
        raise ValueError("Prop asset not found")
    normalized = normalize_prop_views(tuple(views))
    requirement = PropViewRequirement(
        prop_id=prop_id,
        views=normalized,
        reasons=["manual"],
        source="manual",
        signature=_signature(prop_id, normalized),
    )
    state.prop_view_requirements[prop_id] = requirement.model_dump()
    return requirement
