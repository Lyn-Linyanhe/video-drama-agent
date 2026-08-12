"""Image-generation operations shared by asset endpoints and persistent jobs."""

import asyncio
import hashlib
from pathlib import Path
import secrets
from typing import Any
import uuid

from fastapi import HTTPException

from src.api.context import (
    RegenerateImageRequest,
    _character_description,
    _resolve_image_asset,
    _save_image_asset_state,
    image_gen_agent,
)
from src.services.media_quality import MediaQualityFailure, inspect_image, review_image_subjective
from src.services.asset_prompt_composer import (
    PROP_PROMPT_TEMPLATE_VERSION,
    SCENE_PROMPT_TEMPLATE_VERSION,
    build_scene_prompt_layers,
    compose_asset_prompt,
)
from src.services.asset_seed import derive_asset_seed, resolve_generation_seed
from src.services.image_capabilities import (
    client_for_policy,
    dimensions_for_provider,
    normalize_policy,
)
from src.utils.asset_versions import asset_commit_transaction


def _asset_transaction(project_id: str):
    from src.api.context import project_store

    return asset_commit_transaction(project_store.get_project_dir(project_id))


def _style_snapshot_metadata(state: Any) -> dict[str, Any]:
    """Attach an immutable visual-style snapshot to every image version."""
    from src.api.context import _visual_style_snapshot

    snapshot = _visual_style_snapshot(getattr(state, "creative_profile", None))
    return {
        "visual_style_id": snapshot["id"],
        "style_modifiers": list(snapshot["modifiers"]),
        "style_snapshot": snapshot,
    }


async def _validate_generated_image(
    image: Any,
    prompt: str,
    asset_type: str,
    view_type: str,
    provider: str,
    expected_width: int = 0,
    expected_height: int = 0,
) -> dict[str, Any]:
    """Return the complete activation report or raise before any version is replaced."""
    report = inspect_image(
        image.local_path,
        expected_width=expected_width,
        expected_height=expected_height,
        allow_placeholder=provider == "mock",
    )
    if report["status"] == "failed":
        messages = [item["message"] for item in report["technical_checks"] if not item["passed"]]
        raise MediaQualityFailure("; ".join(messages) or "Image quality check failed", report)
    if provider == "mock":
        report["visual_review"] = {"status": "skipped", "reason": "mock placeholder image"}
        report["status"] = "unreviewed"
        report["automatic_reference_allowed"] = True
        return report
    visual_review = await review_image_subjective(
        image.local_path,
        prompt,
        view_type,
        asset_type=asset_type,
    )
    report["visual_review"] = visual_review
    if visual_review.get("status") == "skipped":
        report["status"] = "unreviewed"
    elif visual_review.get("status") == "warning":
        report["status"] = "warning"
        report["warnings"] = visual_review.get("issues", [])
        if visual_review.get("should_regenerate"):
            raise MediaQualityFailure("Image visual review requires regeneration", report)
    return report


def required_asset_views(state: Any, asset_type: str) -> dict[str, tuple[str, ...]]:
    """Return current visual-manifest identities and the views they require."""
    from src.agents.image_gen_agent import CHARACTER_VIEWS, SCENE_VIEWS
    from src.utils.visual_asset_manifest import repair_asset_manifest

    if not state.script or not state.script.asset_manifest:
        raise HTTPException(409, "Visual asset manifest is not available")
    repair_asset_manifest(state.script)
    manifest = state.script.asset_manifest
    if asset_type == "character":
        views = tuple(item[0] for item in CHARACTER_VIEWS)
        visible = {item.name: views for item in manifest.characters if item.appearance_mode == "on_screen"}
        if visible:
            return visible
        visual = getattr(state, "visual_design", None)
        names = (getattr(visual, "global_character_descriptions_cn", {}) or {}).keys()
        if not names:
            names = (getattr(visual, "global_character_descriptions", {}) or {}).keys()
        return {name: views for name in names}
    if asset_type == "scene":
        policy = normalize_policy(getattr(state, "image_generation_policy", None))
        if policy["provider"] == "wan":
            from src.services.scene_view_requirements import derive_scene_view_requirements
            return {
                item.location_id: derive_scene_view_requirements(state, item.location_id).views
                for item in manifest.locations
            }
        views = tuple(item[0] for item in SCENE_VIEWS)
        return {item.location_id: views for item in manifest.locations}
    if asset_type == "prop":
        from src.services.prop_view_requirements import infer_prop_view_requirement

        return {item.prop_id: infer_prop_view_requirement(state, item.prop_id).views for item in manifest.props}
    raise HTTPException(422, "asset_type must be character, scene, or prop")


def _asset_record(state: Any, asset_type: str, asset_id: str, view_type: str):
    if asset_type == "character":
        return next(
            (
                item for item in (state.character_images.characters if state.character_images else [])
                if item.character_name == asset_id and item.view_type == view_type
            ),
            None,
        )
    if asset_type == "scene":
        return next(
            (
                item for item in (state.scene_images.scenes if state.scene_images else [])
                if (item.location_id or str(item.scene_id)) == asset_id and item.view_type == view_type
            ),
            None,
        )
    return next(
        (
            item for item in (state.prop_images.props if state.prop_images else [])
            if item.prop_id == asset_id and item.view_type == view_type
        ),
        None,
    )


def stable_asset_seed(asset_type: str, asset_id: str, view_type: str) -> int:
    return derive_asset_seed("", asset_type, asset_id, view_type, 1)


def _scene_location(state: Any, asset_id: str):
    manifest = getattr(getattr(state, "script", None), "asset_manifest", None)
    return next((item for item in (getattr(manifest, "locations", []) or []) if item.location_id == asset_id), None)


def _scene_state_id(state: Any, asset_id: str, state_id: str = "") -> str:
    requested = str(state_id or "").strip()
    if requested:
        return requested
    location = _scene_location(state, asset_id)
    if not location:
        return ""
    return getattr(location, "default_state_id", "") or (
        location.states[0].state_id if getattr(location, "states", None) else ""
    )


def _find_scene_image(state: Any, asset_id: str, view_type: str, state_id: str = ""):
    expected_state = _scene_state_id(state, asset_id, state_id)
    for image in (state.scene_images.scenes if state.scene_images else []):
        if (image.location_id or str(image.scene_id)) != asset_id:
            continue
        if image.view_type != view_type:
            continue
        if expected_state and (getattr(image, "state_id", "") or "") != expected_state:
            continue
        return image
    return None


def _scene_master_prompt(state: Any, asset_id: str, custom_instructions: str = "", state_id: str = "") -> str:
    from src.services.asset_prompt_composer import build_scene_prompt_layers

    layers = build_scene_prompt_layers(state, asset_id, state_id, "scene_center", custom_instructions)
    return (
        "高质量专业场景中心母版。只生成一张16:9横向无人物空场景图，"
        "机位为正面中心线大全景，镜头正对空间中心轴，严格居中对称。"
        "这张图将作为后续多视角参考组图的母版，必须清晰锁定空间结构、固定陈设、材质、时间天气和主光方向。\n"
        + layers["effective_prompt"]
    )


def _confirmed_scene_master(state: Any, asset_id: str, state_id: str = ""):
    expected_state = _scene_state_id(state, asset_id, state_id)
    master = _find_scene_image(state, asset_id, "scene_center", expected_state)
    if (
        master
        and getattr(master, "image_path", "")
        and Path(master.image_path).is_file()
        and getattr(master, "version_id", "")
        and getattr(master, "is_master", False)
        and not getattr(master, "needs_confirmation", False)
    ):
        return master
    return None


async def generate_scene_master(
    project_id: str,
    asset_id: str,
    custom_instructions: str = "",
    override: dict | None = None,
    project_lock: asyncio.Lock | None = None,
    state_id: str = "",
) -> dict:
    """Generate one unconfirmed center master for a scene state."""
    from src.api.context import RegenerateImageRequest, _load_project_state, _resolve_image_asset, _save_image_asset_state

    state = _load_project_state(project_id)
    policy = normalize_policy(state.image_generation_policy, override)
    if policy["provider"] != "wan":
        raise HTTPException(409, "场景中心母版仅支持万相 2.7")
    location = _scene_location(state, asset_id)
    if not location:
        raise HTTPException(404, "Scene asset not found")
    resolved_state_id = _scene_state_id(state, asset_id, state_id)
    req = RegenerateImageRequest(
        asset_type="scene",
        asset_id=asset_id,
        view_type="scene_center",
        state_id=resolved_state_id,
    )
    state, asset, versions, _ = _resolve_image_asset(project_id, req, create_missing=True)
    asset.state_id = resolved_state_id
    prompt_layers = build_scene_prompt_layers(state, asset_id, resolved_state_id, "scene_center", custom_instructions)
    prompt = _scene_master_prompt(state, asset_id, custom_instructions, resolved_state_id)
    asset.width, asset.height = dimensions_for_provider(
        1920,
        1080,
        policy["resolution"],
        policy["provider"],
    )
    seed = derive_asset_seed(project_id, "scene", f"{asset_id}:{resolved_state_id}", "scene_center", 4)
    # Wan's image endpoint does not provide a reliable independent negative
    # prompt field. Keep the editor-facing prompt readable, but send the
    # canonical exclusions in the actual provider prompt so the center master
    # cannot drift into people, panels, or the wrong medium.
    provider_prompt = prompt
    provider_negative_prompt = prompt_layers["negative_prompt"]
    negative_prompt_mode = "separate"
    if policy["provider"] == "wan":
        provider_prompt = f"{prompt}\n避免出现：{prompt_layers['negative_prompt']}"
        provider_negative_prompt = ""
        negative_prompt_mode = "merged"
    result = await client_for_policy(policy).generate_image(
        prompt=provider_prompt,
        negative_prompt=provider_negative_prompt,
        width=asset.width,
        height=asset.height,
        seed=seed,
    )
    if not result.success or not result.local_path:
        raise HTTPException(502, result.error_message or "万相场景中心母版生成失败")

    quality = await _validate_generated_image(result, prompt, "scene", "scene_center", policy["provider"])

    def commit() -> dict:
        latest, updated, updated_versions, _ = _resolve_image_asset(project_id, req, create_missing=True)
        active_path, history = updated_versions.replace_with_file(
            "scene",
            asset_id,
            "scene_center",
            Path(result.local_path),
            prompt,
            "scene_master",
            metadata={
                **_style_snapshot_metadata(state),
                "provider": policy["provider"],
                "model": policy["model"],
                "resolution": policy["resolution"],
                "generation_mode": "master",
                "state_id": resolved_state_id,
                "is_master": True,
                "needs_confirmation": True,
                "provider_prompt": provider_prompt,
                "provider_negative_prompt": provider_negative_prompt,
                "negative_prompt_mode": negative_prompt_mode,
                "quality_report": quality,
            },
        )
        version_id = next((entry["id"] for entry in history if entry.get("active")), "")
        updated.state_id = resolved_state_id
        updated.is_master = True
        updated.needs_confirmation = True
        updated.master_version_id = version_id
        updated.reference_version_ids = []
        updated.derived_from_version_id = ""
        updated.generation_mode = "master"
        updated.group_id = ""
        updated.group_view_count = 1
        updated.model = policy["model"]
        updated.resolution = policy["resolution"]
        updated.is_stale = False
        if latest.scene_images:
            for candidate in latest.scene_images.scenes:
                same_asset = (candidate.location_id or str(candidate.scene_id)) == asset_id
                same_state = (getattr(candidate, "state_id", "") or "") == resolved_state_id
                if same_asset and same_state and candidate.view_type != "scene_center" and candidate.image_path:
                    candidate.is_stale = True
        state_json = _save_image_asset_state(
            project_id, latest, updated, active_path, prompt, result,
            custom_instructions=custom_instructions,
            prompt_template_version=SCENE_PROMPT_TEMPLATE_VERSION,
            version_id=version_id,
            quality_report=quality,
        )
        return {
            "state_json": state_json,
            "history": history,
            "version_id": version_id,
            "image_path": str(active_path),
            "state_id": resolved_state_id,
            "needs_confirmation": True,
            "policy": policy,
            "quality": quality,
        }

    if project_lock is not None:
        async with project_lock:
            return commit()
    return commit()


async def confirm_scene_master(
    project_id: str,
    asset_id: str,
    version_id: str,
    state_id: str = "",
) -> dict:
    """Mark the active center master as confirmed for scene reference groups."""
    from src.api.context import _load_project_state, _save_project_state, _state_to_json

    state = _load_project_state(project_id)
    resolved_state_id = _scene_state_id(state, asset_id, state_id)
    master = _find_scene_image(state, asset_id, "scene_center", resolved_state_id)
    if not master:
        raise HTTPException(404, "场景中心母版不存在")
    if master.version_id != version_id:
        raise HTTPException(409, "只能确认当前活动的场景中心母版版本")
    if not master.image_path or not Path(master.image_path).is_file():
        raise HTTPException(409, "场景中心母版文件不存在，请重新生成")
    master.is_master = True
    master.needs_confirmation = False
    master.master_version_id = version_id
    master.state_id = resolved_state_id
    _save_project_state(state)
    return {
        "state_json": _state_to_json(state),
        "asset_id": asset_id,
        "state_id": resolved_state_id,
        "version_id": version_id,
        "confirmed": True,
    }


def _prop_manifest_asset(state: Any, prop_id: str):
    manifest = getattr(getattr(state, "script", None), "asset_manifest", None)
    return next((item for item in (getattr(manifest, "props", []) or []) if item.prop_id == prop_id), None)


def _find_prop_image(state: Any, prop_id: str, view_type: str):
    return next(
        (
            item for item in (state.prop_images.props if state.prop_images else [])
            if item.prop_id == prop_id and item.view_type == view_type
        ),
        None,
    )


def _confirmed_prop_master(state: Any, prop_id: str):
    master = _find_prop_image(state, prop_id, "front")
    if (
        master
        and getattr(master, "image_path", "")
        and Path(master.image_path).is_file()
        and getattr(master, "version_id", "")
        and getattr(master, "is_master", False)
        and not getattr(master, "needs_confirmation", False)
    ):
        return master
    return None


async def generate_prop_master(
    project_id: str,
    prop_id: str,
    custom_instructions: str = "",
    override: dict | None = None,
    project_lock: asyncio.Lock | None = None,
    user_regenerate: bool = False,
) -> dict:
    """Generate one unconfirmed front master for a prop."""
    from src.api.context import RegenerateImageRequest, _load_project_state, _resolve_image_asset, _save_image_asset_state

    state = _load_project_state(project_id)
    if not _prop_manifest_asset(state, prop_id):
        raise HTTPException(404, "Prop asset not found")
    policy = normalize_policy(state.image_generation_policy, override)
    req = RegenerateImageRequest(asset_type="prop", asset_id=prop_id, view_type="front")
    state, asset, versions, _ = _resolve_image_asset(project_id, req, create_missing=True)
    composition = compose_asset_prompt(
        state,
        "prop",
        prop_id,
        "front",
        asset,
        custom_instructions,
        provider=policy["provider"],
    )
    prompt = composition["effective_prompt"]
    negative_prompt = composition.get("provider_negative_prompt", composition["negative_prompt"])
    asset.width, asset.height = dimensions_for_provider(
        composition["width"],
        composition["height"],
        policy["resolution"],
        policy["provider"],
    )
    seed = resolve_generation_seed(
        project_id,
        "prop",
        prop_id,
        "front",
        composition["prompt_template_version"],
        user_regenerate=user_regenerate,
    )
    result = await client_for_policy(policy).generate_image(
        prompt=composition["provider_prompt"],
        negative_prompt=negative_prompt,
        width=asset.width,
        height=asset.height,
        seed=seed,
    )
    if not result.success or not result.local_path:
        raise HTTPException(502, result.error_message or "Prop master generation failed")

    quality = await _validate_generated_image(result, prompt, "prop", "front", policy["provider"])

    def commit() -> dict:
        latest, updated, updated_versions, _ = _resolve_image_asset(project_id, req, create_missing=True)
        updated.width, updated.height = asset.width, asset.height
        active_path, history = updated_versions.replace_with_file(
            "prop",
            prop_id,
            "front",
            Path(result.local_path),
            prompt,
            "prop_master",
            metadata={
                **_style_snapshot_metadata(state),
                "provider": policy["provider"],
                "model": policy["model"],
                "resolution": policy["resolution"],
                "generation_mode": "master",
                "is_master": True,
                "needs_confirmation": True,
                "effective_negative_prompt": composition.get("effective_negative_prompt", ""),
                "provider_prompt": composition.get("provider_prompt", ""),
                "provider_negative_prompt": composition.get("provider_negative_prompt", ""),
                "negative_prompt_mode": composition.get("negative_prompt_mode", ""),
                "requirement_signature": composition.get("requirement_signature", ""),
                "quality_report": quality,
                "style_mode": composition.get("style_mode", ""),
                "style_diagnostics": composition.get("style_diagnostics", {}),
            },
        )
        version_id = next((entry["id"] for entry in history if entry.get("active")), "")
        updated.is_master = True
        updated.needs_confirmation = True
        updated.master_version_id = version_id
        updated.reference_version_ids = []
        updated.derived_from_version_id = ""
        updated.generation_mode = "master"
        updated.model = policy["model"]
        updated.resolution = policy["resolution"]
        updated.is_stale = False
        updated.requirement_signature = composition.get("requirement_signature", "")
        if latest.prop_images:
            for candidate in latest.prop_images.props:
                if candidate.prop_id == prop_id and candidate.view_type != "front" and candidate.image_path:
                    candidate.is_stale = True
        state_json = _save_image_asset_state(
            project_id,
            latest,
            updated,
            active_path,
            prompt,
            result,
            custom_instructions=custom_instructions,
            prompt_template_version=composition["prompt_template_version"],
            version_id=version_id,
            quality_report=quality,
        )
        return {
            "state_json": state_json,
            "history": history,
            "version_id": version_id,
            "image_path": str(active_path),
            "seed": result.seed,
            "needs_confirmation": True,
            "policy": policy,
            "quality": quality,
        }

    if project_lock is not None:
        async with project_lock:
            return commit()
    return commit()


async def confirm_prop_master(project_id: str, prop_id: str, version_id: str) -> dict:
    """Mark the active front prop master as confirmed for on-demand views."""
    from src.api.context import _load_project_state, _save_project_state, _state_to_json

    state = _load_project_state(project_id, require_visual=False)
    if not _prop_manifest_asset(state, prop_id):
        raise HTTPException(404, "Prop asset not found")
    master = _find_prop_image(state, prop_id, "front")
    if not master:
        raise HTTPException(404, "Prop front master does not exist")
    if master.version_id != version_id:
        raise HTTPException(409, "Only the active prop master version can be confirmed")
    if not master.image_path or not Path(master.image_path).is_file():
        raise HTTPException(409, "Prop master file is missing; regenerate it")
    master.is_master = True
    master.needs_confirmation = False
    master.master_version_id = version_id
    master.generation_mode = "master"
    master.is_stale = False
    _save_project_state(state)
    return {
        "state_json": _state_to_json(state),
        "asset_id": prop_id,
        "version_id": version_id,
        "confirmed": True,
    }


PROP_DERIVED_VIEWS = ("back", "left", "right", "top", "bottom")


def _prop_view_label(view_type: str) -> str:
    labels = {
        "front": "正面",
        "back": "背面",
        "left": "左侧面",
        "right": "右侧面",
        "top": "俯视顶部",
        "bottom": "仰视底部",
    }
    return labels.get(view_type, view_type)


def _prop_reference_signature(prop_id: str, views: tuple[str, ...]) -> str:
    return hashlib.sha256("|".join([prop_id, *views]).encode("utf-8")).hexdigest()[:16]


def _prop_reference_prompt(state: Any, prop_id: str, views: tuple[str, ...], custom_instructions: str = "") -> str:
    prop = _prop_manifest_asset(state, prop_id)
    if not prop:
        raise HTTPException(404, "Prop asset not found")
    ordered = "；".join(
        f"第{index}张：{_prop_view_label(view)}单图"
        for index, view in enumerate(views, start=1)
    )
    extra = (
        f"\n用户补充要求：{custom_instructions.strip()}。补充要求不得改变道具身份、材质、颜色、尺寸比例、磨损痕迹或视图顺序。"
        if custom_instructions.strip()
        else ""
    )
    from src.agents.image_gen_agent import PROP_NEGATIVE_PROMPT, is_anime_art_style
    from src.services.asset_prompt_composer import _locked_project_constraint, project_style_layers
    from src.services.visual_styles import resolve_profile_style

    style_positive, style_negative = project_style_layers(state, "prop")
    style_lock = _locked_project_constraint(state, "prop")
    style_name = resolve_profile_style(getattr(state, "creative_profile", None))["name"]
    lighting = (
        "白色或浅灰背景，柔和均匀的二维设定稿光线，轮廓清晰、色块稳定"
        if is_anime_art_style(style_name)
        else "白色或浅灰摄影棚背景，柔和产品摄影光线"
    )
    negative = "，".join(item for item in (PROP_NEGATIVE_PROMPT, style_negative) if item)
    return (
        "基于第一张参考图中的同一个道具母版，生成道具一致性参考图。"
        f"道具：{prop.name}。身份与外观：{prop.description}。"
        f"固定输出顺序：{ordered}。"
        "每张都必须是独立单图，不得拼图、不得分屏、不得出现网格、编号、文字、水印或UI。"
        "必须保持同一个物体的形状、材质、颜色、尺寸比例、边缘轮廓、标志性瑕疵和使用痕迹一致；"
        "只改变相机观察方向，不改变道具状态，不额外加入手、人、背景场景、包装、阴影主体或其他物品。"
        f"{lighting}，主体居中，完整清晰。"
        f"项目风格执行层：{style_positive}。{style_lock}"
        f"供应商反向约束：避免出现{negative}。"
        + extra
    )


async def generate_prop_reference_views(
    project_id: str,
    prop_id: str,
    views: tuple[str, ...],
    custom_instructions: str = "",
    override: dict | None = None,
    project_lock: asyncio.Lock | None = None,
) -> dict:
    """Generate derived prop views from the confirmed front master.

    Wan uses reference edit for one view and reference group for multiple views.
    The active prop views are not changed until the provider returns exactly the
    requested set and every returned local file is present.
    """
    from src.api.context import RegenerateImageRequest, _load_project_state, _resolve_image_asset, _save_image_asset_state, _save_project_state, _state_to_json

    requested = tuple(dict.fromkeys(view for view in views if view in PROP_DERIVED_VIEWS))
    if not requested:
        raise HTTPException(400, "No derived prop views requested")
    state = _load_project_state(project_id, require_visual=False)
    if not _prop_manifest_asset(state, prop_id):
        raise HTTPException(404, "Prop asset not found")
    master = _confirmed_prop_master(state, prop_id)
    if not master:
        raise HTTPException(409, "Please confirm the prop front master before generating derived prop views")
    mode = "group" if len(requested) >= 2 else "reference_edit"
    policy = normalize_policy(state.image_generation_policy, override, mode=mode)
    if policy["provider"] != "wan":
        raise HTTPException(409, "Prop reference views from a confirmed master require Wan 2.7")

    prompt = _prop_reference_prompt(state, prop_id, requested, custom_instructions)
    client = client_for_policy(policy)
    if len(requested) >= 2:
        result = await client.generate_sequence(prompt, [master.image_path], len(requested), policy["resolution"])
        if not result.success:
            raise HTTPException(502, result.error_message or "Wan prop reference group generation failed")
        if len(result.images) != len(requested):
            raise HTTPException(
                502,
                f"Wan prop reference group returned {len(result.images)} images, expected {len(requested)}; no partial views were activated",
            )
        produced = list(result.images)
        generation_mode = "reference_group"
        group_id = uuid.uuid4().hex
    else:
        single = await client.edit_image(prompt, [master.image_path], policy["resolution"])
        if not single.success or not single.local_path:
            raise HTTPException(502, single.error_message or "Wan prop reference edit failed")
        produced = [single]
        generation_mode = "reference_edit"
        group_id = ""

    missing = [
        image.local_path or ""
        for image in produced
        if not image.local_path or not Path(image.local_path).is_file()
    ]
    if missing:
        raise HTTPException(502, "Wan prop reference result file is missing; no partial views were activated")
    try:
        quality_reports = []
        for view_type, image in zip(requested, produced):
            review_prompt = (
                f"{prompt}\nVIEW-SPECIFIC CONTRACT: {view_type}\n"
                f"{_prop_view_label(view_type)}: inspect only this returned view, not the other views."
            )
            quality_reports.append(
                await _validate_generated_image(image, review_prompt, "prop", view_type, policy["provider"])
            )
    except MediaQualityFailure as exc:
        raise HTTPException(502, str(exc)) from exc
    signature = _prop_reference_signature(prop_id, requested)

    async def commit() -> dict:
        with _asset_transaction(project_id):
            return await commit_group()

    async def commit_group() -> dict:
        latest = _load_project_state(project_id, require_visual=False)
        current_master = _confirmed_prop_master(latest, prop_id)
        if not current_master or current_master.version_id != master.version_id:
            raise HTTPException(409, "Prop master changed while the reference views were generating")
        histories = {}
        generated = []
        for view_type, image in zip(requested, produced):
            req = RegenerateImageRequest(asset_type="prop", asset_id=prop_id, view_type=view_type)
            latest, asset, versions, _ = _resolve_image_asset(project_id, req, create_missing=True)
            active_path, history = versions.replace_with_file(
                "prop",
                prop_id,
                view_type,
                Path(image.local_path),
                prompt,
                generation_mode,
                metadata={
                    **_style_snapshot_metadata(latest),
                    "provider": policy["provider"],
                    "model": policy["model"],
                    "resolution": policy["resolution"],
                    "generation_mode": generation_mode,
                    "group_id": group_id,
                    "master_version_id": master.version_id,
                    "reference_version_ids": [master.version_id],
                    "derived_from_version_id": master.version_id,
                    "requirement_signature": signature,
                    "provider_prompt": prompt,
                    "provider_negative_prompt": "",
                    "negative_prompt_mode": "merged",
                    "prompt_template_version": PROP_PROMPT_TEMPLATE_VERSION,
                    "quality_report": quality_reports[requested.index(view_type)],
                },
            )
            version_id = next((entry["id"] for entry in history if entry.get("active")), "")
            asset.is_master = False
            asset.needs_confirmation = False
            asset.master_version_id = master.version_id
            asset.reference_version_ids = [master.version_id]
            asset.derived_from_version_id = master.version_id
            asset.generation_mode = generation_mode
            asset.model = policy["model"]
            asset.resolution = policy["resolution"]
            asset.is_stale = False
            asset.requirement_signature = signature
            state_json = _save_image_asset_state(
                project_id,
                latest,
                asset,
                active_path,
                prompt,
                image,
                custom_instructions=custom_instructions,
                prompt_template_version=PROP_PROMPT_TEMPLATE_VERSION,
                version_id=version_id,
                quality_report=quality_reports[requested.index(view_type)],
            )
            histories[view_type] = history
            generated.append({
                "asset_type": "prop",
                "asset_id": prop_id,
                "view_type": view_type,
                "version_id": version_id,
                "image_path": str(active_path),
                "generation_mode": generation_mode,
            })
        _save_project_state(latest)
        return {
            "state_json": _state_to_json(latest),
            "generated": generated,
            "histories": histories,
            "generation_mode": generation_mode,
            "group_id": group_id,
            "view_count": len(requested),
            "master_version_id": master.version_id,
            "reference_version_ids": [master.version_id],
            "requirement_signature": signature,
            "policy": policy,
            "quality_reports": dict(zip(requested, quality_reports)),
        }

    if project_lock is not None:
        async with project_lock:
            return await commit()
    return await commit()


async def generate_asset_views(
    project_id: str,
    asset_type: str,
    asset_id: str,
    views: tuple[str, ...],
    custom_instructions: str = "",
    regenerate: bool = False,
    override: dict | None = None,
    project_lock: asyncio.Lock | None = None,
) -> dict[str, list[dict]]:
    """Generate a target asset through the one versioned generation path."""
    from src.api.context import RegenerateImageRequest, _load_project_state

    state = _load_project_state(project_id)
    policy = normalize_policy(state.image_generation_policy, override)
    character_group_views = ("front_half", "front_full", "left_profile", "back_full")
    if (
        asset_type == "character"
        and policy["provider"] == "wan"
        and len(set(views).intersection(character_group_views)) >= 2
    ):
        from src.agents.image_gen_agent import CHARACTER_PROMPT_TEMPLATE_VERSION

        latest = _load_project_state(project_id)
        existing = {
            view: _asset_record(latest, "character", asset_id, view)
            for view in character_group_views
        }
        has_stale_character_prompt = any(
            item
            and item.image_path
            and Path(item.image_path).is_file()
            and getattr(item, "prompt_template_version", 1) < CHARACTER_PROMPT_TEMPLATE_VERSION
            for item in existing.values()
        )
        if not regenerate and all(
            item and item.image_path and Path(item.image_path).is_file()
            for item in existing.values()
        ) and not has_stale_character_prompt:
            return {
                "generated": [],
                "skipped": [
                    {"asset_type": "character", "asset_id": asset_id, "view_type": view}
                    for view in views
                ],
                "errors": [],
            }

        master = existing.get("front_half")
        master_is_stale = (
            master
            and master.image_path
            and Path(master.image_path).is_file()
            and getattr(master, "prompt_template_version", 1) < CHARACTER_PROMPT_TEMPLATE_VERSION
        )
        if regenerate or master_is_stale or not master or not master.image_path or not Path(master.image_path).is_file() or not getattr(master, "version_id", ""):
            await regenerate_image_asset(
                project_id,
                RegenerateImageRequest(
                    asset_type="character",
                    asset_id=asset_id,
                    view_type="front_half",
                    prompt=custom_instructions,
                    seed=None if regenerate or master_is_stale else derive_asset_seed(project_id, "character", asset_id, "front_half", CHARACTER_PROMPT_TEMPLATE_VERSION),
                    use_standard_view_prompt=not bool(custom_instructions),
                    provider=str((override or {}).get("provider") or ""),
                    model=str((override or {}).get("model") or ""),
                    resolution=str((override or {}).get("resolution") or ""),
                ),
                project_lock=project_lock,
            )

        try:
            await generate_character_reference_group(
                project_id,
                asset_id,
                custom_instructions,
                override,
                project_lock,
            )
            latest = _load_project_state(project_id)
            return {
                "generated": [
                    {
                        "asset_type": "character",
                        "asset_id": asset_id,
                        "view_type": view,
                        "version_id": getattr(_asset_record(latest, "character", asset_id, view), "version_id", ""),
                        "image_path": getattr(_asset_record(latest, "character", asset_id, view), "image_path", ""),
                    }
                    for view in character_group_views
                    if view in views
                ],
                "skipped": [],
                "errors": [],
            }
        except Exception as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            return {
                "generated": [],
                "skipped": [],
                "errors": [{
                    "asset_type": "character",
                    "asset_id": asset_id,
                    "view_type": "reference_group",
                    "message": str(detail)[:500],
                }],
            }

    if asset_type == "scene" and policy["provider"] == "wan" and any(view.startswith("scene_") for view in views):
        state = _load_project_state(project_id)
        confirmed_master = _confirmed_scene_master(state, asset_id)
        pending_master = _find_scene_image(state, asset_id, "scene_center")
        if not confirmed_master:
            if not regenerate and pending_master and pending_master.image_path and Path(pending_master.image_path).is_file() and getattr(pending_master, "needs_confirmation", False):
                return {
                    "generated": [],
                    "skipped": [{
                        "asset_type": "scene",
                        "asset_id": asset_id,
                        "view_type": "scene_center",
                        "version_id": getattr(pending_master, "version_id", ""),
                        "needs_confirmation": True,
                    }],
                    "errors": [],
                }
            result = await generate_scene_master(project_id, asset_id, custom_instructions, override, project_lock)
            return {"generated": [{"asset_type": "scene", "asset_id": asset_id, "view_type": "scene_center", "version_id": result.get("version_id", ""), "image_path": result.get("image_path", ""), "needs_confirmation": True, "quality_report": result.get("quality", {})}], "skipped": [], "errors": []}
        result = await generate_scene_reference_group(project_id, asset_id, custom_instructions, override, project_lock)
        latest = _load_project_state(project_id)
        reports = result.get("quality_reports", {})
        return {"generated": [{"asset_type": "scene", "asset_id": asset_id, "view_type": view, "version_id": getattr(_asset_record(latest, "scene", asset_id, view), "version_id", ""), "image_path": getattr(_asset_record(latest, "scene", asset_id, view), "image_path", ""), "quality_report": reports.get(view, {})} for view in views], "skipped": [], "errors": [], "quality_reports": reports}

    if asset_type == "prop":
        state = _load_project_state(project_id)
        confirmed_master = _confirmed_prop_master(state, asset_id)
        pending_master = _find_prop_image(state, asset_id, "front")
        if "front" in views and not confirmed_master:
            if (
                not regenerate
                and pending_master
                and pending_master.image_path
                and Path(pending_master.image_path).is_file()
                and getattr(pending_master, "needs_confirmation", False)
            ):
                return {
                    "generated": [],
                    "skipped": [{
                        "asset_type": "prop",
                        "asset_id": asset_id,
                        "view_type": "front",
                        "version_id": getattr(pending_master, "version_id", ""),
                        "needs_confirmation": True,
                    }],
                    "errors": [],
                }
            result = await generate_prop_master(project_id, asset_id, custom_instructions, override, project_lock, regenerate)
            return {
                "generated": [{
                    "asset_type": "prop",
                    "asset_id": asset_id,
                    "view_type": "front",
                    "version_id": result.get("version_id", ""),
                    "image_path": result.get("image_path", ""),
                    "seed": result.get("seed", 0),
                    "needs_confirmation": True,
                    "quality_report": result.get("quality", {}),
                }],
                "skipped": [],
                "errors": [],
            }
        if confirmed_master:
            views = tuple(view for view in views if view != "front")
            if not views:
                return {
                    "generated": [],
                    "skipped": [{"asset_type": "prop", "asset_id": asset_id, "view_type": "front"}],
                    "errors": [],
                }
            if policy["provider"] == "wan" and any(view in PROP_DERIVED_VIEWS for view in views):
                derived_views = tuple(view for view in views if view in PROP_DERIVED_VIEWS)
                try:
                    result = await generate_prop_reference_views(
                        project_id,
                        asset_id,
                        derived_views,
                        custom_instructions,
                        override,
                        project_lock,
                    )
                except Exception as exc:
                    detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
                    return {
                        "generated": [],
                        "skipped": [],
                        "errors": [{
                            "asset_type": "prop",
                            "asset_id": asset_id,
                            "view_type": "reference_group" if len(derived_views) >= 2 else derived_views[0],
                            "message": str(detail)[:500],
                        }],
                    }
                skipped_standard = [
                    {"asset_type": "prop", "asset_id": asset_id, "view_type": view}
                    for view in views
                    if view not in PROP_DERIVED_VIEWS
                ]
                return {
                    "generated": result["generated"],
                    "skipped": skipped_standard,
                    "errors": [],
                }

    generated: list[dict] = []
    skipped: list[dict] = []
    errors: list[dict] = []
    class _ViewGenerationFailed(RuntimeError):
        pass

    try:
        with _asset_transaction(project_id):
            for view_type in views:
                state = _load_project_state(project_id)
                existing = _asset_record(state, asset_type, asset_id, view_type)
                if not regenerate and existing and existing.image_path and Path(existing.image_path).is_file():
                    skipped.append({"asset_type": asset_type, "asset_id": asset_id, "view_type": view_type})
                    continue
                try:
                    response = await regenerate_image_asset(
                        project_id,
                        RegenerateImageRequest(
                            asset_type=asset_type,
                            asset_id=asset_id,
                            view_type=view_type,
                            prompt=custom_instructions,
                            seed=None if regenerate else derive_asset_seed(project_id, asset_type, asset_id, view_type, 1),
                            use_standard_view_prompt=not bool(custom_instructions),
                            provider=str((override or {}).get("provider") or ""),
                            model=str((override or {}).get("model") or ""),
                            resolution=str((override or {}).get("resolution") or ""),
                        ),
                        project_lock=project_lock,
                    )
                    updated_state = _load_project_state(project_id)
                    record = _asset_record(updated_state, asset_type, asset_id, view_type)
                    generated.append({
                        "asset_type": asset_type,
                        "asset_id": asset_id,
                        "view_type": view_type,
                        "version_id": getattr(record, "version_id", "") if record else "",
                        "seed": getattr(record, "seed", 0) if record else 0,
                        "image_path": getattr(record, "image_path", "") if record else "",
                        "quality_report": getattr(record, "quality_report", {}) if record else {},
                        "history": response.get("history", []),
                    })
                except Exception as exc:
                    detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
                    errors.append({
                        "asset_type": asset_type,
                        "asset_id": asset_id,
                        "view_type": view_type,
                        "message": str(detail)[:500],
                    })
                    raise _ViewGenerationFailed from exc
    except _ViewGenerationFailed:
        generated = []
    return {"generated": generated, "skipped": skipped, "errors": errors}


async def generate_scene_reference_group(project_id: str, asset_id: str, custom_instructions: str = "", override: dict | None = None, project_lock: asyncio.Lock | None = None, state_id: str = "") -> dict:
    """Atomically save a coherent Wan scene-reference group; never activate partial results."""
    from src.api.context import RegenerateImageRequest, _load_project_state, _resolve_image_asset, _save_image_asset_state, _save_project_state, _state_to_json
    from src.services.asset_prompt_composer import build_scene_group_prompt, build_scene_prompt_layers
    from src.services.scene_view_requirements import derive_scene_view_requirements

    state = _load_project_state(project_id)
    policy = normalize_policy(state.image_generation_policy, override, mode="group")
    if policy["provider"] != "wan":
        raise HTTPException(409, "场景参考组图仅支持万相 2.7")
    resolved_state_id = _scene_state_id(state, asset_id, state_id)
    master = _confirmed_scene_master(state, asset_id, resolved_state_id)
    if not master:
        raise HTTPException(409, "请先生成并确认场景中心母版，再生成场景参考组图")
    requirement = derive_scene_view_requirements(state, asset_id, resolved_state_id)
    views = tuple(view for view in requirement.views if view != "scene_center")
    if not views:
        return {
            "state_json": _state_to_json(state),
            "group_id": "",
            "view_count": 0,
            "histories": {},
            "policy": policy,
            "master_version_id": master.version_id,
            "state_id": resolved_state_id,
            "generated": [],
        }
    prompt, count = build_scene_group_prompt(
        state,
        asset_id,
        custom_instructions,
        state_id=resolved_state_id,
        view_types=views,
    )
    result = await client_for_policy(policy).generate_sequence(prompt, [master.image_path], count, policy["resolution"])
    if not result.success:
        raise HTTPException(502, result.error_message or "万相场景组图生成失败")
    if len(result.images) != count:
        raise HTTPException(502, f"万相场景组图仅返回 {len(result.images)} 张，未激活部分结果，请重新生成整组")
    try:
        quality_reports = {}
        for view_type, image in zip(views, result.images):
            view_layer = build_scene_prompt_layers(
                state, asset_id, resolved_state_id, view_type, custom_instructions,
            )
            review_prompt = (
                f"{prompt}\nVIEW-SPECIFIC CONTRACT: {view_type}\n"
                f"{view_layer['view']}\nReview this returned image only against this view contract."
            )
            quality_reports[view_type] = await _validate_generated_image(
                image, review_prompt, "scene", view_type, policy["provider"],
            )
    except MediaQualityFailure as exc:
        raise HTTPException(502, str(exc)) from exc
    group_id = uuid.uuid4().hex

    async def commit():
        with _asset_transaction(project_id):
            return await commit_group()

    async def commit_group():
        latest = _load_project_state(project_id)
        current_master = _confirmed_scene_master(latest, asset_id, resolved_state_id)
        if (
            not current_master
            or current_master.version_id != master.version_id
            or not current_master.image_path
            or not Path(current_master.image_path).is_file()
        ):
            raise HTTPException(409, "Scene master changed while the group was generating")
        histories = {}
        for view_type, image in zip(views, result.images):
            request = RegenerateImageRequest(asset_type="scene", asset_id=asset_id, view_type=view_type, state_id=resolved_state_id)
            latest, asset, versions, _ = _resolve_image_asset(project_id, request, create_missing=True)
            # Wan receives one shared group prompt for a coherent sequence,
            # while the asset editor/history must retain the selected view's
            # own camera constraint instead of displaying the same text for
            # every group result.
            view_prompt = build_scene_prompt_layers(
                latest,
                asset_id,
                resolved_state_id,
                view_type,
                custom_instructions,
            )["effective_prompt"]
            active_path, history = versions.replace_with_file(
                "scene", asset_id, view_type, Path(image.local_path), view_prompt, "reference_group",
                metadata={
                    **_style_snapshot_metadata(latest),
                    "provider": policy["provider"],
                    "model": policy["model"],
                    "resolution": policy["resolution"],
                    "generation_mode": "reference_group",
                    "group_id": group_id,
                    "group_view_count": count,
                    "state_id": resolved_state_id,
                    "master_version_id": master.version_id,
                    "reference_version_ids": [master.version_id],
                    "provider_prompt": prompt,
                    "display_prompt": view_prompt,
                    "negative_prompt_mode": "merged",
                    "quality_report": quality_reports[view_type],
                },
            )
            version_id = next((entry["id"] for entry in history if entry.get("active")), "")
            asset.generation_mode, asset.group_id, asset.group_view_count = "reference_group", group_id, count
            asset.state_id = resolved_state_id
            asset.is_master = False
            asset.needs_confirmation = False
            asset.master_version_id = master.version_id
            asset.reference_version_ids = [master.version_id]
            asset.derived_from_version_id = master.version_id
            asset.model, asset.resolution = policy["model"], policy["resolution"]
            asset.is_stale = False
            _save_image_asset_state(
                project_id,
                latest,
                asset,
                active_path,
                view_prompt,
                image,
                custom_instructions,
                SCENE_PROMPT_TEMPLATE_VERSION,
                version_id,
                quality_reports[view_type],
            )
            histories[view_type] = history
        _save_project_state(latest)
        return {
            "state_json": _state_to_json(latest),
            "group_id": group_id,
            "view_count": count,
            "histories": histories,
            "policy": policy,
            "quality_reports": quality_reports,
            "master_version_id": master.version_id,
            "state_id": resolved_state_id,
            "generated": [
                {
                    "asset_type": "scene",
                    "asset_id": asset_id,
                    "view_type": view_type,
                    "version_id": next((entry["id"] for entry in history if entry.get("active")), ""),
                    "image_path": str(history[-1].get("path", "")) if history else "",
                    "generation_mode": "reference_group",
                    "quality_report": quality_reports[view_type],
                }
                for view_type, history in histories.items()
            ],
        }
    if project_lock is not None:
        async with project_lock:
            return await commit()
    return await commit()


async def edit_scene_reference_view(
    project_id: str,
    asset_id: str,
    state_id: str,
    view_type: str,
    prompt: str,
    reference_version_ids: list[str] | None = None,
    override: dict | None = None,
    project_lock: asyncio.Lock | None = None,
) -> dict:
    """Edit one scene view from the confirmed state master plus optional references."""
    from src.api.context import RegenerateImageRequest, _load_project_state, _resolve_image_asset, _save_image_asset_state
    from src.services.asset_prompt_composer import build_scene_prompt_layers

    if view_type not in {"scene_left_45", "scene_right_45", "scene_reverse"}:
        raise HTTPException(400, "Invalid editable scene view")
    if not prompt.strip():
        raise HTTPException(400, "Scene edit prompt cannot be empty")
    state = _load_project_state(project_id)
    resolved_state_id = _scene_state_id(state, asset_id, state_id)
    master = _confirmed_scene_master(state, asset_id, resolved_state_id)
    if not master:
        raise HTTPException(409, "Please confirm the scene center master before editing scene views")
    policy = normalize_policy(state.image_generation_policy, override, mode="reference_edit")
    if policy["provider"] != "wan":
        raise HTTPException(409, "Scene reference edit requires Wan 2.7")

    req = RegenerateImageRequest(asset_type="scene", asset_id=asset_id, view_type=view_type, state_id=resolved_state_id)
    state, asset, versions, _ = _resolve_image_asset(project_id, req, create_missing=True)
    layers = build_scene_prompt_layers(state, asset_id, resolved_state_id, view_type, prompt)
    # Wan's edit endpoint accepts one text message and no separate negative
    # field. Merge the canonical negative/style constraints so an anime scene
    # cannot drift back to live-action or 3D during a reference edit.
    edit_prompt = (
        layers["effective_prompt"]
        + f"\n避免出现：{layers['negative_prompt']}"
        + "\n只修正用户提出的问题，保持参考图中的地点身份、场景状态、空间结构和当前视角一致。"
    )
    references = [master.image_path]
    for version_id in reference_version_ids or []:
        if version_id == master.version_id:
            continue
        active = next(
            (
                image for image in (state.scene_images.scenes if state.scene_images else [])
                if (image.location_id or str(image.scene_id)) == asset_id
                and (getattr(image, "state_id", "") or "") == resolved_state_id
                and image.version_id == version_id
                and image.image_path
                and Path(image.image_path).is_file()
            ),
            None,
        )
        candidate_path = active.image_path if active else ""
        if not candidate_path:
            for reference_view in ("scene_center", "scene_left_45", "scene_right_45", "scene_reverse"):
                record = versions.find_version("scene", asset_id, reference_view, version_id)
                candidate_path = str((record or {}).get("path") or "")
                if candidate_path and Path(candidate_path).is_file():
                    break
        if candidate_path and candidate_path not in references:
            references.append(candidate_path)
    if asset.image_path and Path(asset.image_path).is_file() and asset.image_path not in references:
        references.append(asset.image_path)

    result = await client_for_policy(policy).edit_image(edit_prompt, references, policy["resolution"])
    if not result.success or not result.local_path:
        raise HTTPException(502, result.error_message or "Wan scene reference edit failed")
    quality_report = await _validate_generated_image(
        result, edit_prompt, "scene", view_type, policy["provider"],
    )

    async def commit() -> dict:
        latest, updated, updated_versions, _ = _resolve_image_asset(project_id, req, create_missing=True)
        current_master = _confirmed_scene_master(latest, asset_id, resolved_state_id)
        if not current_master or current_master.version_id != master.version_id:
            raise HTTPException(409, "Scene master changed while the edit was generating")
        active_path, history = updated_versions.replace_with_file(
            "scene",
            asset_id,
            view_type,
            Path(result.local_path),
            edit_prompt,
            "reference_edit",
            metadata={
                **_style_snapshot_metadata(latest),
                "provider": policy["provider"],
                "model": policy["model"],
                "resolution": policy["resolution"],
                "generation_mode": "reference_edit",
                "state_id": resolved_state_id,
                "master_version_id": master.version_id,
                "reference_version_ids": [master.version_id, *(reference_version_ids or [])],
                "derived_from_version_id": master.version_id,
                "quality_report": quality_report,
            },
        )
        version_id = next((entry["id"] for entry in history if entry.get("active")), "")
        updated.state_id = resolved_state_id
        updated.is_master = False
        updated.needs_confirmation = False
        updated.master_version_id = master.version_id
        updated.reference_version_ids = [master.version_id, *(reference_version_ids or [])]
        updated.derived_from_version_id = master.version_id
        updated.generation_mode = "reference_edit"
        updated.model = policy["model"]
        updated.resolution = policy["resolution"]
        updated.is_stale = False
        state_json = _save_image_asset_state(
            project_id,
            latest,
            updated,
            active_path,
            edit_prompt,
            result,
            custom_instructions=prompt,
            prompt_template_version=SCENE_PROMPT_TEMPLATE_VERSION,
            version_id=version_id,
            quality_report=quality_report,
        )
        return {
            "state_json": state_json,
            "history": history,
            "policy": policy,
            "master_version_id": master.version_id,
            "reference_version_ids": [master.version_id, *(reference_version_ids or [])],
            "quality_report": quality_report,
        }

    if project_lock is not None:
        async with project_lock:
            return await commit()
    return await commit()


async def generate_pending_project_assets(project_id: str, asset_type: str) -> dict[str, Any]:
    """Generate only missing manifest views and retain partial failures."""
    from src.api.context import _load_project_state, _state_to_json

    required = required_asset_views(_load_project_state(project_id), asset_type)
    aggregate: dict[str, list[dict]] = {"generated": [], "skipped": [], "errors": []}
    for asset_id, views in required.items():
        result = await generate_asset_views(project_id, asset_type, asset_id, views)
        for key in aggregate:
            aggregate[key].extend(result[key])
    return {"state_json": _state_to_json(_load_project_state(project_id)), **aggregate}


async def regenerate_image_asset(
    project_id: str,
    req: RegenerateImageRequest,
    quality_check: bool = True,
    project_lock: asyncio.Lock | None = None,
) -> dict:
    """Generate and version one asset while retaining server-owned constraints."""
    state, asset, versions, _ = _resolve_image_asset(project_id, req, create_missing=True)
    base_policy = state.image_generation_policy or {
        "provider": image_gen_agent.api_client.provider_name,
        "model": "mock" if image_gen_agent.api_client.provider_name == "mock" else getattr(image_gen_agent.api_client, "model", "kling-v1"),
        "resolution": "1K",
    }
    # Resolve the one-shot override before composing prompts.  Prompt payload
    # semantics (especially Wan's merged negative constraints) must follow the
    # provider that will actually receive this task.
    policy = normalize_policy(base_policy, {
        "provider": req.provider, "model": req.model, "resolution": req.resolution,
    })
    # A request with no explicit supplement inherits the persisted supplement.
    # This makes pending generation and editor preview use the same constraints.
    custom_override = None if req.use_standard_view_prompt else req.prompt.strip()
    try:
        composition = compose_asset_prompt(
            state,
            req.asset_type,
            req.asset_id,
            req.view_type,
            asset,
            custom_override,
            provider=policy["provider"],
        )
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    prompt = composition["effective_prompt"]
    negative_prompt = composition["negative_prompt"]
    provider_negative_prompt = composition.get("provider_negative_prompt", negative_prompt)
    custom_instructions = composition["custom_instructions"]
    template_version = composition["prompt_template_version"]
    if req.asset_type == "scene" and policy["provider"] == "wan" and req.view_type.startswith("scene_"):
        if req.view_type == "scene_center":
            master = await generate_scene_master(
                project_id,
                req.asset_id,
                "" if req.use_standard_view_prompt else req.prompt.strip(),
                {"provider": req.provider, "model": req.model, "resolution": req.resolution},
                project_lock,
                req.state_id,
            )
            latest, updated, updated_versions, _ = _resolve_image_asset(project_id, req, create_missing=True)
            history = updated_versions.list_versions("scene", req.asset_id, req.view_type, updated.image_prompt)
            return {"state_json": master["state_json"], "history": history, "quality": master["quality"], "version_id": master["version_id"], "needs_confirmation": True}
        group = await generate_scene_reference_group(
            project_id,
            req.asset_id,
            "" if req.use_standard_view_prompt else req.prompt.strip(),
            {"provider": req.provider, "model": req.model, "resolution": req.resolution},
            project_lock,
            req.state_id,
        )
        latest, updated, updated_versions, _ = _resolve_image_asset(project_id, req, create_missing=True)
        history = updated_versions.list_versions("scene", req.asset_id, req.view_type, updated.image_prompt)
        return {"state_json": group["state_json"], "history": history, "quality_reports": group.get("quality_reports", {}), "group_id": group["group_id"], "view_count": group["view_count"]}
    if req.asset_type == "prop" and req.view_type not in {"front", "standard_views"}:
        if not _confirmed_prop_master(state, req.asset_id):
            raise HTTPException(409, "Please confirm the prop front master before generating derived prop views")
    asset.width, asset.height = dimensions_for_provider(
        composition["width"],
        composition["height"],
        policy["resolution"],
        policy["provider"],
    )

    user_regenerate_seed = bool(
        req.seed is None
        and getattr(asset, "image_path", "")
        and Path(asset.image_path).is_file()
    )
    generation_seed = req.seed if req.seed is not None else resolve_generation_seed(
        project_id,
        req.asset_type,
        req.asset_id,
        req.view_type,
        template_version,
        user_regenerate=user_regenerate_seed,
    )
    client = client_for_policy(policy)
    if req.asset_type == "prop":
        if req.view_type == "standard_views":
            raise HTTPException(
                409,
                "Legacy prop standard_views sheets are preview-only; generate and confirm the front master, then generate requested independent views.",
            )
        prop = next(
            (item for item in state.script.asset_manifest.props if item.prop_id == req.asset_id),
            None,
        )
        if prop is None:
            raise HTTPException(404, "Prop asset not found in the visual manifest")
        result = await client.generate_image(
            prompt=composition["provider_prompt"],
            negative_prompt=provider_negative_prompt,
            width=asset.width,
            height=asset.height,
            seed=generation_seed,
        )
    else:
        result = await client.generate_image(
            prompt=composition["provider_prompt"],
            negative_prompt=provider_negative_prompt,
            width=asset.width,
            height=asset.height,
            seed=generation_seed,
        )
    if not result.success or not result.local_path:
        raise HTTPException(502, result.error_message or "Image generation failed")

    quality = inspect_image(result.local_path, asset.width, asset.height, policy["provider"] == "mock")
    if quality_check:
        quality = await _validate_generated_image(
            result, prompt, req.asset_type, req.view_type, policy["provider"], asset.width, asset.height,
        )

    from src.services.generation_hooks import get_generation_hooks

    get_generation_hooks().check_cancelled()
    def commit_result() -> dict:
        commit_state, commit_asset, commit_versions = state, asset, versions
        if project_lock is not None:
            commit_state, commit_asset, commit_versions, _ = _resolve_image_asset(project_id, req, create_missing=True)
            commit_asset.width, commit_asset.height = asset.width, asset.height
        active_path, history = commit_versions.replace_with_file(
            req.asset_type, req.asset_id, req.view_type, Path(result.local_path), prompt, "generated",
            metadata={
                **_style_snapshot_metadata(commit_state),
                "provider": policy["provider"],
                "model": policy["model"],
                "resolution": policy["resolution"],
                "generation_mode": "single",
                "effective_negative_prompt": composition.get("effective_negative_prompt", ""),
                "quality_report": quality,
                "provider_prompt": composition.get("provider_prompt", ""),
                "provider_negative_prompt": composition.get("provider_negative_prompt", ""),
                "negative_prompt_mode": composition.get("negative_prompt_mode", ""),
                "requirement_signature": composition.get("requirement_signature", ""),
            },
        )
        version_id = next((item["id"] for item in history if item.get("active")), "")
        if req.asset_type == "character":
            commit_asset.model = policy["model"]
            commit_asset.resolution = policy["resolution"]
            commit_asset.generation_mode = "single"
        if req.asset_type == "prop":
            commit_asset.model = policy["model"]
            commit_asset.resolution = policy["resolution"]
            commit_asset.generation_mode = "legacy_sheet" if req.view_type == "standard_views" else "single"
            commit_asset.requirement_signature = composition.get("requirement_signature", "")
        state_json = _save_image_asset_state(
            project_id, commit_state, commit_asset, active_path, prompt, result,
            custom_instructions=custom_instructions,
            prompt_template_version=template_version,
            version_id=version_id,
            quality_report=quality,
        )
        return {"state_json": state_json, "history": history, "quality": quality}

    if project_lock is not None:
        async with project_lock:
            get_generation_hooks().check_cancelled()
            return commit_result()
    return commit_result()


def _character_reference_group_prompt(name: str, description: str, extra: str, art_style: str = "写实") -> str:
    """Request four coherent character views from one locked front-half master."""
    from src.agents.image_gen_agent import character_gender_lock, character_style_prefix, is_anime_art_style

    anime_style = is_anime_art_style(art_style)
    identity = "同一位动画角色" if anime_style else "同一位真实人物"
    style_rule = (
        f"项目艺术风格为“{art_style}”，四张图必须保持统一的动漫/二次元角色设计、线稿、上色和动画脸型比例，禁止变成真人照片。"
        if anime_style else
        "四张图必须保持真人摄影、真实皮肤和真实服装材质，禁止变成动漫或插画。"
    )
    forbidden_style = "真人照片、摄影写实画面" if anime_style else "动漫、二次元、漫画、插画、卡通或数字人风格"

    suffix = (
        f"\n用户补充要求：{extra.strip()}。补充要求不得改变性别、身份、服装、发型、体型或四张视图的固定顺序。"
        if extra.strip() else ""
    )
    return f"""{character_style_prefix(art_style)}
基于提供的正面半身角色母版，生成{identity}的四张连续角色参考图。必须保留同一张脸、相同性别、年龄感、体型、发型、服装颜色与材质、鞋子、标志特征和统一纯色背景。角色：{name}；设定：{description}。身份锁定：{character_gender_lock(name, description)}。{style_rule}
严格按以下顺序返回四张独立图片：
1. 正面半身：正对镜头，胸部以上，完整清晰脸部、发型和上身服装。
2. 正面全身：正对镜头，9:16 竖构图，头顶到鞋底完整入画，双脚清晰可见。
3. 左侧面半身：身体与脸部严格朝画面左侧，只见左侧脸轮廓，不得出现正脸或三分之二正脸。
4. 背面全身：相机严格位于背脊中线正后方，人物完全背向镜头，头顶到鞋底完整入画，脸部、侧脸、镜中倒影均不可见。
每张只能包含一人、一个机位、纯白干净背景；不得生成拼图、分屏、重复人物、文字、水印、标签、UI、其他人物、道具、环境布景、{forbidden_style}。{suffix}
"""


def _character_reference_edit_prompt(name: str, description: str, view_type: str, extra: str, art_style: str = "写实") -> str:
    from src.agents.image_gen_agent import CHARACTER_SINGLE_IMAGE_RULE, character_gender_lock, character_style_prefix, is_anime_art_style, character_view_spec

    label, _, _, view_instruction, _ = character_view_spec(view_type)
    face_rule = (
        "必须保留参考图中的同一张脸和性别，正面五官比例、发际线、眉眼鼻唇、下颌轮廓和年龄感不得改变。"
        if view_type != "back_full"
        else "必须保留参考图中的同一位真人身份、性别、年龄感、体型、发型轮廓和服装；本后背视图中不得露出脸、五官、眼睛、鼻子或嘴。"
    )
    suffix = f"\n用户补充要求：{extra.strip()}。补充要求不得改变性别、身份、服装、发型、体型和当前视图方向。" if extra.strip() else ""
    anime_style = is_anime_art_style(art_style)
    identity = "同一位动画角色" if anime_style else "同一位真人角色"
    single_image_rule = (
        "输出格式：严格只输出一张单独的二维角色设定图，一个角色、一个镜头角度、一个干净纯色背景，不得拼图或分屏。"
        if anime_style else CHARACTER_SINGLE_IMAGE_RULE
    )
    style_rule = (
        f"项目艺术风格为“{art_style}”，必须保持动漫/二次元角色设计、线稿、上色和动画脸型比例，禁止改成真人照片。"
        if anime_style else
        "风格要求：真实摄影、真人演员定妆照、写实影视质感、自然皮肤纹理、真实五官比例、真实服装面料。"
    )
    return f"""{character_style_prefix(art_style)}
{single_image_rule}
Use the provided reference image only as an identity anchor. Preserve the same {identity}, same gender, same age impression, same body type, same hairstyle, same outfit and same background treatment.
请基于参考图中的{identity}生成一张新的{label}角色设定图。
角色：{name}，{description}。
硬性身份约束：{character_gender_lock(name, description)}；{face_rule}
当前视图要求：{view_instruction}
保持完全一致：同一角色身份、同一性别、同一年龄感、同一体型、同一发型、同一服装颜色和材质、同一鞋子、同一标志性特征、同一干净背景和同一光线逻辑。
{style_rule}
禁止：性别改变、男变女、女变男、换脸、换身份、换年龄、换发型、换衣服、{'' if anime_style else '动漫、二次元、漫画、插画、卡通、'}CG、3D建模、游戏角色、虚拟偶像、数字人、娃娃脸、塑料皮肤、夸张大眼、尖下巴、文字、水印、其他人物、道具、复杂背景、身体裁切。{suffix}
最终只输出一张{label}单图。"""


async def generate_character_reference_group(project_id: str, asset_id: str, custom_instructions: str = "", override: dict | None = None, project_lock: asyncio.Lock | None = None) -> dict:
    """Build a Wan reference group, creating the front-half master if needed."""
    from src.api.context import _load_project_state, _save_image_asset_state, _save_project_state, _state_to_json
    from src.agents.image_gen_agent import CHARACTER_PROMPT_TEMPLATE_VERSION, character_style_mode

    state = _load_project_state(project_id)
    policy = normalize_policy(state.image_generation_policy, override, mode="group")
    if policy["provider"] != "wan":
        raise HTTPException(409, "角色参考组图仅支持万相 2.7")
    master = _asset_record(state, "character", asset_id, "front_half")
    master_needs_generation = (
        not master
        or not getattr(master, "image_path", "")
        or not Path(master.image_path).is_file()
        or not getattr(master, "version_id", "")
        or not str(getattr(master, "model", "") or "").startswith("wan2.7-image")
        or getattr(master, "prompt_template_version", 1) < CHARACTER_PROMPT_TEMPLATE_VERSION
    )
    if master_needs_generation:
        await regenerate_image_asset(
            project_id,
            RegenerateImageRequest(
                asset_type="character",
                asset_id=asset_id,
                view_type="front_half",
                prompt=custom_instructions,
                seed=None,
                use_standard_view_prompt=not bool(custom_instructions),
                provider=str((override or {}).get("provider") or ""),
                model=str((override or {}).get("model") or ""),
                resolution=str((override or {}).get("resolution") or ""),
            ),
            project_lock=project_lock,
        )
        state = _load_project_state(project_id)
        master = _asset_record(state, "character", asset_id, "front_half")
    if not master or not master.image_path or not Path(master.image_path).is_file() or not master.version_id:
        raise HTTPException(409, "角色正面半身母版自动生成失败，请检查图片模型配置后重试")
    master_id = master.version_id
    if state.character_images:
        state.character_images.reference_masters[asset_id] = master_id
    description = _character_description(state, asset_id)
    manifest = getattr(getattr(state, "script", None), "asset_manifest", None)
    character = next(
        (item for item in (getattr(manifest, "characters", []) or []) if item.asset_id == asset_id),
        None,
    )
    display_name = getattr(character, "name", "") or asset_id
    from src.services.visual_styles import resolve_profile_style

    profile = getattr(state, "creative_profile", None)
    art_style = resolve_profile_style(profile)["name"] if profile is not None else "电影级真人写实"
    group_prompt = _character_reference_group_prompt(display_name, description, custom_instructions, art_style)
    group_result = await client_for_policy(policy).generate_sequence(
        group_prompt, [master.image_path], 4, policy["resolution"],
    )
    if not group_result.success:
        raise HTTPException(502, group_result.error_message or "万相角色组图生成失败")
    if len(group_result.images) != 4:
        raise HTTPException(
            502,
            f"万相角色组图仅返回 {len(group_result.images)} 张，未激活部分结果，请重新生成整组",
        )
    group_views = ("front_half", "front_full", "left_profile", "back_full")
    group_images = dict(zip(group_views, group_result.images))
    try:
        from src.agents.image_gen_agent import character_view_spec

        quality_reports = {}
        for view_type, image in group_images.items():
            view_instruction = character_view_spec(view_type)[3]
            review_prompt = (
                f"{group_prompt}\nVIEW-SPECIFIC CONTRACT: {view_type}\n"
                f"{view_instruction}\nReview this returned image only against this view contract."
            )
            quality_reports[view_type] = await _validate_generated_image(
                image, review_prompt, "character", view_type, policy["provider"],
            )
    except MediaQualityFailure as exc:
        raise HTTPException(502, str(exc)) from exc
    group_id = uuid.uuid4().hex
    async def commit():
        with _asset_transaction(project_id):
            return await commit_group()

    async def commit_group():
        latest = _load_project_state(project_id)
        current_master = _asset_record(latest, "character", asset_id, "front_half")
        if (
            not current_master
            or current_master.version_id != master_id
            or not current_master.image_path
            or not Path(current_master.image_path).is_file()
        ):
            raise HTTPException(409, "Character master changed while the group was generating")
        histories = {}
        for view_type in group_views:
            image = group_images[view_type]
            req = RegenerateImageRequest(asset_type="character", asset_id=asset_id, view_type=view_type)
            latest, asset, item_versions, _ = _resolve_image_asset(project_id, req, create_missing=True)
            prompt = group_prompt
            active_path, history = item_versions.replace_with_file(
                "character", asset_id, view_type, Path(image.local_path), prompt, "reference_group",
            metadata={**_style_snapshot_metadata(latest), "provider": policy["provider"], "model": policy["model"], "resolution": policy["resolution"], "generation_mode": "reference_group", "group_id": group_id, "master_version_id": master_id, "reference_version_ids": [master_id], "style_mode": character_style_mode(art_style), "provider_prompt": group_prompt, "negative_prompt_mode": "merged", "quality_report": quality_reports[view_type]},
            )
            version_id = next((entry["id"] for entry in history if entry.get("active")), "")
            asset.model, asset.resolution, asset.generation_mode = policy["model"], policy["resolution"], "reference_group"
            asset.group_id, asset.master_version_id, asset.reference_version_ids = group_id, master_id, [master_id]
            _save_image_asset_state(project_id, latest, asset, active_path, prompt, image, custom_instructions, CHARACTER_PROMPT_TEMPLATE_VERSION, version_id, quality_reports[view_type])
            histories[view_type] = history
        if latest.character_images:
            latest.character_images.reference_masters[asset_id] = master_id
        _save_project_state(latest)
        return {"state_json": _state_to_json(latest), "group_id": group_id, "master_version_id": master_id, "histories": histories, "policy": policy, "quality_reports": quality_reports}
    if project_lock is not None:
        async with project_lock:
            return await commit()
    return await commit()


async def edit_character_reference(project_id: str, req, project_lock: asyncio.Lock | None = None) -> dict:
    from src.api.context import _load_project_state, _save_image_asset_state
    from src.agents.image_gen_agent import CHARACTER_PROMPT_TEMPLATE_VERSION

    state, asset, versions, _ = _resolve_image_asset(project_id, req, create_missing=True)
    policy = normalize_policy(state.image_generation_policy, {"provider": req.provider, "model": req.model, "resolution": req.resolution}, mode="reference_edit")
    if policy["provider"] != "wan":
        raise HTTPException(409, "角色参考修订仅支持万相 2.7")
    master_id = (state.character_images.reference_masters.get(req.asset_id, "") if state.character_images else "") or asset.master_version_id
    master = next((item for item in (state.character_images.characters if state.character_images else []) if item.character_name == req.asset_id and item.version_id == master_id), None)
    master_path = master.image_path if master and Path(master.image_path).is_file() else ""
    if not master_path:
        for master_view in ("front_half", "front_full", "left_profile", "back_full"):
            record = versions.find_version("character", req.asset_id, master_view, master_id)
            candidate_path = str((record or {}).get("path") or "")
            if candidate_path and Path(candidate_path).is_file():
                master_path = candidate_path
                break
    if not master_path:
        raise HTTPException(409, "当前角色没有可用母版，请先生成角色参考组图")
    references = [master_path]
    for version_id in req.reference_version_ids:
        candidate = next((item for item in state.character_images.characters if item.character_name == req.asset_id and item.version_id == version_id), None)
        if candidate and Path(candidate.image_path).is_file() and candidate.image_path not in references:
            references.append(candidate.image_path)
            continue
        for reference_view in ("front_half", "front_full", "left_profile", "back_full"):
            record = versions.find_version("character", req.asset_id, reference_view, version_id)
            candidate_path = str((record or {}).get("path") or "")
            if candidate_path and candidate_path not in references and Path(candidate_path).is_file():
                references.append(candidate_path)
                break
    if asset.image_path and Path(asset.image_path).is_file() and asset.image_path not in references:
        references.append(asset.image_path)
    composition = compose_asset_prompt(
        state,
        "character",
        req.asset_id,
        req.view_type,
        asset,
        req.prompt,
        provider=policy["provider"],
    )
    prompt = composition["effective_prompt"] + "\n只修正用户提出的问题，保持参考图人物身份、服装、发型和视图方向一致。"
    client = client_for_policy(policy)
    result = await client.edit_image(prompt, references, policy["resolution"])
    if not result.success or not result.local_path:
        raise HTTPException(502, result.error_message or "万相参考修订失败")
    quality_report = await _validate_generated_image(
        result, prompt, "character", req.view_type, policy["provider"],
    )
    async def commit():
        latest, updated, updated_versions, _ = _resolve_image_asset(project_id, req, create_missing=True)
        active_path, history = updated_versions.replace_with_file(
            "character", req.asset_id, req.view_type, Path(result.local_path), prompt, "reference_edit",
            metadata={**_style_snapshot_metadata(latest), "provider": policy["provider"], "model": policy["model"], "resolution": policy["resolution"], "generation_mode": "reference_edit", "master_version_id": master_id, "reference_version_ids": [master_id, *req.reference_version_ids], "quality_report": quality_report},
        )
        version_id = next((entry["id"] for entry in history if entry.get("active")), "")
        updated.model, updated.resolution, updated.generation_mode = policy["model"], policy["resolution"], "reference_edit"
        updated.master_version_id, updated.reference_version_ids = master_id, [master_id, *req.reference_version_ids]
        state_json = _save_image_asset_state(project_id, latest, updated, active_path, prompt, result, req.prompt, CHARACTER_PROMPT_TEMPLATE_VERSION, version_id, quality_report)
        return {"state_json": state_json, "history": history, "policy": policy, "master_version_id": master_id, "quality_report": quality_report}
    if project_lock is not None:
        async with project_lock:
            return await commit()
    return await commit()
