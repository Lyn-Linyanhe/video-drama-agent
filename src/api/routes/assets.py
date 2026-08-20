"""FastAPI routes extracted from the original server entrypoint."""

from fastapi import APIRouter
from ..context import *
from src.services.asset_generation import (
    _asset_record,
    confirm_scene_master as _confirm_scene_master,
    confirm_prop_master as _confirm_prop_master,
    _confirmed_prop_master,
    edit_scene_reference_view as _edit_scene_reference_view,
    generate_character_reference_group as _generate_character_reference_group,
    generate_asset_views as _generate_asset_views_service,
    generate_pending_project_assets as _generate_pending_project_assets_service,
    generate_prop_master as _generate_prop_master,
    generate_prop_reference_views as _generate_prop_reference_views,
    generate_scene_master as _generate_scene_master,
    generate_scene_reference_group as _generate_scene_reference_group,
    regenerate_image_asset as _regenerate_image_asset,
    required_asset_views as _required_asset_views,
)
from src.services.asset_prompt_composer import compose_asset_prompt
from src.services.media_quality import MediaQualityFailure, classify_quality_report, review_decision, review_image_subjective
from src.services.image_capabilities import normalize_policy, public_capabilities
from src.services.prop_view_requirements import infer_prop_view_requirement, set_prop_view_requirement
from src.services.scene_view_requirements import derive_scene_view_requirements, set_scene_view_requirements
from src.utils.asset_versions import ImageAssetVersions, asset_commit_transaction

router = APIRouter()


class SceneViewRequirementRequest(BaseModel):
    state_id: str = Field(default="", max_length=80)
    views: list[str] = Field(default_factory=list, max_length=4)


class PropViewRequirementRequest(BaseModel):
    views: list[str] = Field(default_factory=list, max_length=6)


class PropReferenceViewsRequest(BaseModel):
    views: list[str] = Field(default_factory=list, min_length=1, max_length=5)
    custom_instructions: str = Field(default="", max_length=8000)
    provider: str = Field(default="", max_length=32)
    model: str = Field(default="", max_length=80)
    resolution: str = Field(default="", max_length=16)


class ImageHistoryReviewRequest(ImageAssetRequest):
    version_id: str = Field(default="", max_length=64)


def _allowed_asset_views(asset_type: str, required_views: tuple[str, ...]) -> tuple[str, ...]:
    if asset_type == "prop":
        return tuple(dict.fromkeys((*required_views, "standard_views")))
    return required_views


def _canonical_asset_identity(state, asset_type: str, asset_id: str) -> str:
    """Resolve a public character asset ID to the name used by legacy image records.

    Character images predate the visual manifest's ``asset_id`` field and are
    still keyed by ``character_name`` on disk.  The generic asset routes expose
    manifest IDs, so normalize them at the API boundary before checking views or
    loading version history.  Scene and prop IDs are already canonical.
    """
    if asset_type != "character":
        return asset_id
    manifest = getattr(getattr(getattr(state, "script", None), "asset_manifest", None), "characters", []) or []
    for item in manifest:
        if str(getattr(item, "asset_id", "") or "") == asset_id:
            return str(getattr(item, "name", "") or asset_id)
    for item in manifest:
        if asset_id == str(getattr(item, "name", "") or ""):
            return asset_id
        if asset_id in {str(alias) for alias in (getattr(item, "aliases", []) or [])}:
            return str(getattr(item, "name", "") or asset_id)
    return asset_id


@router.get("/api/image-capabilities")
async def image_capabilities():
    """Public provider capabilities used by all image-model controls."""
    return {"providers": public_capabilities()}


@router.get("/api/projects/{project_id}/image-generation-policy")
async def get_image_generation_policy(project_id: str):
    state = _load_project_state(project_id)
    policy = normalize_policy(state.image_generation_policy)
    if state.image_generation_policy != policy:
        state.image_generation_policy = policy
        _save_project_state(state)
    return {"project_id": project_id, "policy": policy, "providers": public_capabilities()}


@router.put("/api/projects/{project_id}/image-generation-policy")
async def put_image_generation_policy(project_id: str, req: ImageGenerationPolicyRequest):
    state = _load_project_state(project_id)
    policy = normalize_policy(state.image_generation_policy, req.model_dump())
    state.image_generation_policy = policy
    _save_project_state(state)
    return {"project_id": project_id, "policy": policy, "providers": public_capabilities()}

@router.post("/api/characters")
async def generate_characters(req: StageRequest):
    """Compatibility wrapper: generate missing character references."""
    supplied = _state_from_json(req.state_json)
    if not supplied.project_id or not project_store.get_project(supplied.project_id):
        return StageResponse(state_json=req.state_json, log=[], error="请先完成剧本解析")
    try:
        result = await generate_pending_project_assets(
            supplied.project_id, GeneratePendingAssetsRequest(asset_type="character"),
        )
    except Exception as e:
        return StageResponse(state_json=req.state_json, log=[], error=f"角色生成失败: {str(e)[:300]}")
    errors = "；".join(item["message"] for item in result["errors"][:3])
    return StageResponse(state_json=result["state_json"], log=[], error=errors)


@router.post("/api/scenes")
async def generate_scenes(req: StageRequest):
    """Compatibility wrapper: generate missing scene references."""
    supplied = _state_from_json(req.state_json)
    if not supplied.project_id or not project_store.get_project(supplied.project_id):
        return StageResponse(state_json=req.state_json, log=[], error="请先完成剧本解析")
    try:
        result = await generate_pending_project_assets(
            supplied.project_id, GeneratePendingAssetsRequest(asset_type="scene"),
        )
    except Exception as e:
        return StageResponse(state_json=req.state_json, log=[], error=f"场景生成失败: {str(e)[:300]}")
    errors = "；".join(item["message"] for item in result["errors"][:3])
    return StageResponse(state_json=result["state_json"], log=[], error=errors)


@router.post("/api/props")
async def generate_props(req: StageRequest):
    """Compatibility wrapper: generate missing prop references."""
    supplied = _state_from_json(req.state_json)
    if not supplied.project_id or not project_store.get_project(supplied.project_id):
        return StageResponse(state_json=req.state_json, log=[], error="请先完成剧本解析")
    try:
        result = await generate_pending_project_assets(
            supplied.project_id, GeneratePendingAssetsRequest(asset_type="prop"),
        )
    except Exception as exc:
        return StageResponse(state_json=req.state_json, log=[], error=f"道具生成失败: {str(exc)[:300]}")
    errors = "；".join(item["message"] for item in result["errors"][:3])
    return StageResponse(state_json=result["state_json"], log=[], error=errors)


@router.get("/api/projects/{project_id}/image-history")
async def get_image_history(project_id: str, asset_type: str, asset_id: str, view_type: str, state_id: str = ""):
    state = _load_project_state(project_id)
    canonical_id = _canonical_asset_identity(state, asset_type, asset_id)
    required = _required_asset_views(state, asset_type)
    if canonical_id not in required:
        raise HTTPException(404, "Asset not found in the current project manifest")
    if view_type not in _allowed_asset_views(asset_type, required[canonical_id]):
        raise HTTPException(400, "Invalid asset view")
    try:
        request = ImageAssetRequest(
            asset_type=asset_type,
            asset_id=canonical_id,
            view_type=view_type,
            state_id=state_id,
        )
        _state, asset, versions, _active_path = _resolve_image_asset(
            project_id, request, create_missing=True,
        )
        prompt = str(getattr(asset, "image_prompt", "") or "")
        return {"versions": versions.list_versions(asset_type, canonical_id, view_type, prompt)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("/api/projects/{project_id}/character-image-prompt")
async def get_character_image_prompt(project_id: str, asset_id: str, view_type: str):
    """Return the current canonical prompt even before a character asset exists."""
    state = _load_project_state(project_id)
    return _prompt_payload(project_id, "character", _canonical_asset_identity(state, "character", asset_id), view_type)


@router.get("/api/projects/{project_id}/prop-image-prompt")
async def get_prop_image_prompt(project_id: str, asset_id: str):
    """Return the canonical six-view prompt for a prop before an image exists."""
    return _prompt_payload(project_id, "prop", asset_id, "standard_views")


def _prompt_payload(project_id: str, asset_type: str, asset_id: str, view_type: str, state_id: str = "") -> dict:
    request = ImageAssetRequest(asset_type=asset_type, asset_id=asset_id, view_type=view_type, state_id=state_id)
    state, asset, _, _ = _resolve_image_asset(project_id, request, create_missing=True)
    try:
        return compose_asset_prompt(state, asset_type, asset_id, view_type, asset)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


async def _generate_asset_views(
    project_id: str,
    asset_type: str,
    asset_id: str,
    views: tuple[str, ...],
    custom_instructions: str = "",
    regenerate: bool = False,
    override: dict | None = None,
) -> dict:
    result = await _generate_asset_views_service(
        project_id, asset_type, asset_id, views, custom_instructions, regenerate, override,
    )
    return {"state_json": _state_to_json(_load_project_state(project_id)), **result}


@router.get("/api/projects/{project_id}/assets/{asset_type}/{asset_id}/prompt")
async def get_asset_prompt(project_id: str, asset_type: str, asset_id: str, view_type: str = "", state_id: str = ""):
    state = _load_project_state(project_id)
    canonical_id = _canonical_asset_identity(state, asset_type, asset_id)
    required = _required_asset_views(state, asset_type)
    if canonical_id not in required:
        raise HTTPException(404, "Asset not found in the current project manifest")
    selected_view = view_type or required[canonical_id][0]
    if selected_view not in _allowed_asset_views(asset_type, required[canonical_id]):
        raise HTTPException(400, "Invalid asset view")
    return _prompt_payload(project_id, asset_type, canonical_id, selected_view, state_id)


@router.post("/api/projects/{project_id}/assets/{asset_type}/{asset_id}/generate")
async def generate_project_asset(
    project_id: str,
    asset_type: str,
    asset_id: str,
    req: GenerateAssetRequest,
):
    state = _load_project_state(project_id)
    canonical_id = _canonical_asset_identity(state, asset_type, asset_id)
    required = _required_asset_views(state, asset_type)
    if canonical_id not in required:
        raise HTTPException(404, "Asset not found in the current project manifest")
    views = (req.view_type,) if req.view_type else required[canonical_id]
    allowed = _allowed_asset_views(asset_type, required[canonical_id])
    if any(view not in allowed for view in views):
        raise HTTPException(400, "Invalid asset view")
    if asset_type == "prop" and "standard_views" in views:
        raise HTTPException(
            409,
            "Legacy prop standard_views sheets are preview-only; generate and confirm the front master, then generate requested independent views.",
        )
    return await _generate_asset_views(
        project_id, asset_type, canonical_id, views, req.custom_instructions, req.regenerate,
        {"provider": req.provider, "model": req.model, "resolution": req.resolution},
    )


@router.post("/api/projects/{project_id}/assets/generate-pending")
async def generate_pending_project_assets(project_id: str, req: GeneratePendingAssetsRequest):
    return await _generate_pending_project_assets_service(project_id, req.asset_type)


@router.post("/api/projects/{project_id}/scenes/{asset_id}/master/generate")
async def generate_scene_master(project_id: str, asset_id: str, req: GenerateAssetRequest):
    return await _generate_scene_master(
        project_id,
        asset_id,
        req.custom_instructions,
        {"provider": req.provider, "model": req.model, "resolution": req.resolution},
        state_id=getattr(req, "state_id", ""),
    )


@router.post("/api/projects/{project_id}/scenes/{asset_id}/states/{state_id}/master/generate")
async def generate_scene_state_master(project_id: str, asset_id: str, state_id: str, req: GenerateAssetRequest):
    return await _generate_scene_master(
        project_id,
        asset_id,
        req.custom_instructions,
        {"provider": req.provider, "model": req.model, "resolution": req.resolution},
        state_id=state_id,
    )


@router.post("/api/projects/{project_id}/scenes/{asset_id}/master/confirm")
async def confirm_scene_master(project_id: str, asset_id: str, req: SceneMasterConfirmRequest):
    return await _confirm_scene_master(project_id, asset_id, req.version_id, req.state_id)


@router.post("/api/projects/{project_id}/scenes/{asset_id}/states/{state_id}/master/confirm")
async def confirm_scene_state_master(project_id: str, asset_id: str, state_id: str, req: SceneMasterConfirmRequest):
    return await _confirm_scene_master(project_id, asset_id, req.version_id, state_id)


@router.post("/api/projects/{project_id}/scenes/{asset_id}/states/{state_id}/reference-group/generate")
async def generate_scene_state_reference_group(project_id: str, asset_id: str, state_id: str, req: GenerateAssetRequest):
    return await _generate_scene_reference_group(
        project_id,
        asset_id,
        req.custom_instructions,
        {"provider": req.provider, "model": req.model, "resolution": req.resolution},
        state_id=state_id,
    )


@router.post("/api/projects/{project_id}/scenes/{asset_id}/states/{state_id}/views/{view_type}/edit")
async def edit_scene_view(project_id: str, asset_id: str, state_id: str, view_type: str, req: SceneReferenceEditRequest):
    return await _edit_scene_reference_view(
        project_id,
        asset_id,
        state_id,
        view_type,
        req.prompt,
        req.reference_version_ids,
        {"provider": req.provider, "model": req.model, "resolution": req.resolution},
    )


@router.post("/api/projects/{project_id}/props/{prop_id}/master/generate")
async def generate_prop_master(project_id: str, prop_id: str, req: GenerateAssetRequest):
    return await _generate_prop_master(
        project_id,
        prop_id,
        req.custom_instructions,
        {"provider": req.provider, "model": req.model, "resolution": req.resolution},
        user_regenerate=req.regenerate,
    )


@router.post("/api/projects/{project_id}/props/{prop_id}/master/confirm")
async def confirm_prop_master(project_id: str, prop_id: str, req: PropMasterConfirmRequest):
    return await _confirm_prop_master(project_id, prop_id, req.version_id)


@router.post("/api/projects/{project_id}/props/{prop_id}/views/{view_type}/generate")
async def generate_prop_view(project_id: str, prop_id: str, view_type: str, req: GenerateAssetRequest):
    if view_type not in {"back", "left", "right", "top", "bottom"}:
        raise HTTPException(400, "Invalid prop derived view")
    state = _load_project_state(project_id, require_visual=False)
    if not _confirmed_prop_master(state, prop_id):
        raise HTTPException(409, "Please confirm the prop front master before generating derived prop views")
    return await _generate_asset_views(
        project_id,
        "prop",
        prop_id,
        (view_type,),
        req.custom_instructions,
        req.regenerate,
        {"provider": req.provider, "model": req.model, "resolution": req.resolution},
    )


@router.post("/api/projects/{project_id}/props/{prop_id}/views/generate")
async def generate_prop_views(project_id: str, prop_id: str, req: PropReferenceViewsRequest):
    views = tuple(dict.fromkeys(req.views))
    if any(view not in {"back", "left", "right", "top", "bottom"} for view in views):
        raise HTTPException(400, "Invalid prop derived view")
    state = _load_project_state(project_id, require_visual=False)
    if not _confirmed_prop_master(state, prop_id):
        raise HTTPException(409, "Please confirm the prop front master before generating derived prop views")
    try:
        return await _generate_prop_reference_views(
            project_id,
            prop_id,
            views,
            req.custom_instructions,
            {"provider": req.provider, "model": req.model, "resolution": req.resolution},
        )
    except MediaQualityFailure as exc:
        # Technical failures still use the legacy HTTP error contract, while
        # the failed provider outputs have already been archived as candidates.
        raise HTTPException(502, str(exc)) from exc


@router.get("/api/projects/{project_id}/scenes/{asset_id}/view-requirements")
@router.get("/api/projects/{project_id}/scenes/{asset_id}/states/{state_id}/view-requirements")
async def get_scene_view_requirements(project_id: str, asset_id: str, state_id: str = ""):
    state = _load_project_state(project_id, require_visual=False)
    return derive_scene_view_requirements(state, asset_id, state_id).model_dump()


@router.put("/api/projects/{project_id}/scenes/{asset_id}/view-requirements")
async def put_scene_view_requirements(project_id: str, asset_id: str, req: SceneViewRequirementRequest):
    state = _load_project_state(project_id, require_visual=False)
    try:
        requirement = set_scene_view_requirements(state, asset_id, req.state_id, req.views)
    except ValueError as exc:
        message = str(exc)
        raise HTTPException(400 if "Invalid" in message else 404, message) from exc
    _save_project_state(state)
    return requirement.model_dump()


@router.put("/api/projects/{project_id}/scenes/{asset_id}/states/{state_id}/view-requirements")
async def put_scene_state_view_requirements(project_id: str, asset_id: str, state_id: str, req: SceneViewRequirementRequest):
    state = _load_project_state(project_id, require_visual=False)
    try:
        requirement = set_scene_view_requirements(state, asset_id, state_id, req.views)
    except ValueError as exc:
        message = str(exc)
        raise HTTPException(400 if "Invalid" in message else 404, message) from exc
    _save_project_state(state)
    return requirement.model_dump()


@router.get("/api/projects/{project_id}/props/{prop_id}/view-requirements")
async def get_prop_view_requirements(project_id: str, prop_id: str):
    state = _load_project_state(project_id, require_visual=False)
    try:
        return infer_prop_view_requirement(state, prop_id).model_dump()
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.put("/api/projects/{project_id}/props/{prop_id}/view-requirements")
async def put_prop_view_requirements(project_id: str, prop_id: str, req: PropViewRequirementRequest):
    state = _load_project_state(project_id, require_visual=False)
    try:
        requirement = set_prop_view_requirement(state, prop_id, req.views)
    except ValueError as exc:
        message = str(exc)
        raise HTTPException(400 if "Invalid" in message else 404, message) from exc
    _save_project_state(state)
    return requirement.model_dump()


async def _legacy_regenerate_image_asset(
    project_id: str,
    req: RegenerateImageRequest,
    quality_check: bool = True,
    project_lock: asyncio.Lock | None = None,
) -> dict:
    """Compatibility shim retained for old imports; delegates to the unified service."""
    return await _regenerate_image_asset(project_id, req, quality_check, project_lock)


@router.post("/api/projects/{project_id}/image-regenerate")
async def regenerate_image(project_id: str, req: RegenerateImageRequest):
    try:
        return await _regenerate_image_asset(project_id, req)
    except HTTPException:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/projects/{project_id}/character-four-views")
async def generate_character_four_views(project_id: str, req: CharacterFourViewsRequest):
    """Generate all four character references, using provider group mode when available."""
    views = ("front_half", "front_full", "left_profile", "back_full")
    response = None
    generated_views: list[str] = []
    try:
        state = _load_project_state(project_id)
        policy = normalize_policy(state.image_generation_policy)
        if policy["provider"] == "wan":
            from src.agents.image_gen_agent import CHARACTER_PROMPT_TEMPLATE_VERSION

            master = _asset_record(state, "character", req.asset_id, "front_half")
            needs_master = (
                not master
                or not master.image_path
                or not Path(master.image_path).is_file()
                or not getattr(master, "version_id", "")
                or getattr(master, "prompt_template_version", 1) < CHARACTER_PROMPT_TEMPLATE_VERSION
            )
            if needs_master:
                await _regenerate_image_asset(
                    project_id,
                    RegenerateImageRequest(
                        asset_type="character",
                        asset_id=req.asset_id,
                        view_type="front_half",
                        prompt=req.custom_instructions,
                        use_standard_view_prompt=not bool(req.custom_instructions),
                    ),
                )
            group = await _generate_character_reference_group(
                project_id,
                req.asset_id,
                req.custom_instructions,
            )
            return {**group, "generated_views": list(views), "generation_mode": "reference_group"}

        result = await _generate_asset_views_service(
            project_id,
            "character",
            req.asset_id,
            views,
            req.custom_instructions,
            regenerate=True,
        )
        if result["errors"]:
            raise HTTPException(502, result["errors"][0]["message"])
        return {
            "state_json": _state_to_json(_load_project_state(project_id)),
            "generated": result["generated"],
            "generated_views": [item["view_type"] for item in result["generated"]],
        }
    except HTTPException:
        raise
    except (OSError, ValueError, RuntimeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/projects/{project_id}/image-upload")
async def upload_image(
    project_id: str,
    asset_type: str = Form(...),
    asset_id: str = Form(...),
    view_type: str = Form(...),
    state_id: str = Form(""),
    file: UploadFile = File(...),
):
    request = ImageAssetRequest(asset_type=asset_type, asset_id=asset_id, view_type=view_type, state_id=state_id)
    try:
        state, asset, versions, active_path = _resolve_image_asset(project_id, request, create_missing=True)
        extension = Path(file.filename or "upload.png").suffix.lower()
        if extension not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise HTTPException(400, "Only PNG, JPG, JPEG, and WEBP images are supported")
        content = await file.read()
        if not content or len(content) > 20 * 1024 * 1024:
            raise HTTPException(400, "Image file must be between 1 byte and 20 MB")
        temporary_path = active_path.parent / f".upload_{uuid.uuid4().hex}{extension}"
        temporary_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path.write_bytes(content)
        quality_report = inspect_image(temporary_path)
        if quality_report["status"] == "failed":
            messages = [
                item["message"] for item in quality_report["technical_checks"]
                if not item["passed"]
            ]
            temporary_path.unlink(missing_ok=True)
            raise HTTPException(400, "; ".join(messages) or "Image quality check failed")
        quality_report["status"] = "unreviewed"
        quality_report["visual_review"] = {
            "status": "skipped",
            "reason": "uploaded images require explicit visual review",
        }
        quality_report["review_verdict"] = "unreviewed"
        quality_report["review_reason"] = "上传图片默认不调用视觉模型，请手动执行视觉检查"
        quality_report["review_evidence"] = []
        try:
            active_path, history = versions.replace_with_file(
                asset_type,
                asset_id,
                view_type,
                temporary_path,
                asset.image_prompt,
                "uploaded",
                metadata={"quality_report": quality_report},
            )
        finally:
            temporary_path.unlink(missing_ok=True)
        version_id = next((item["id"] for item in history if item.get("active")), "")
        state_json = _save_image_asset_state(
            project_id, state, asset, active_path, asset.image_prompt, version_id=version_id,
            quality_report=quality_report,
        )
        return {"state_json": state_json, "history": history}
    except HTTPException:
        raise
    except (OSError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/projects/{project_id}/image-history/activate")
async def activate_image_history(project_id: str, req: ActivateImageVersionRequest):
    try:
        state, asset, versions, _ = _resolve_image_asset(project_id, req)
        active_path, prompt, history = versions.activate(
            req.asset_type,
            req.asset_id,
            req.view_type,
            req.version_id,
            asset.image_prompt,
        )
        active_version = next((item for item in history if item.get("active")), {})
        version_id = active_version.get("id", "")
        quality_report = active_version.get("quality_report", {})
        state_json = _save_image_asset_state(
            project_id, state, asset, active_path, prompt, version_id=version_id,
            quality_report=quality_report,
        )
        return {"state_json": state_json, "history": history}
    except HTTPException:
        raise
    except (OSError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/projects/{project_id}/image-history/review")
async def review_image_history(project_id: str, req: ImageHistoryReviewRequest):
    """Run an explicit visual review on an active or historical image only."""
    try:
        state, asset, versions, _ = _resolve_image_asset(project_id, req)
        history = versions.list_versions(req.asset_type, req.asset_id, req.view_type, asset.image_prompt)
        selected = next(
            (item for item in history if item.get("id") == req.version_id),
            None,
        ) if req.version_id else next((item for item in history if item.get("active")), None)
        if not selected:
            raise ValueError("Image history version not found")
        image_path = Path(str(selected.get("path") or ""))
        if not image_path.is_file():
            raise ValueError("Image history file is missing")
        prompt = str(selected.get("prompt") or asset.image_prompt or "")
        visual = await review_image_subjective(image_path, prompt, req.view_type, req.asset_type)
        current_report = selected.get("quality_report") if isinstance(selected.get("quality_report"), dict) else {}
        report = {**current_report, "visual_review": visual}
        visual_status = str(visual.get("status") or "").strip().casefold()
        verdict = classify_quality_report(report)
        report["review_verdict"] = verdict
        report["status"] = (
            "passed" if verdict == "passed"
            else "failed" if verdict == "hard_fail"
            else "unreviewed" if visual_status in {"skipped", "unreviewed"}
            else "warning"
        )
        history = versions.update_quality_report(
            req.asset_type, req.asset_id, req.view_type, str(selected.get("id")), report,
        )
        if selected.get("active"):
            state_json = _save_image_asset_state(
                project_id, state, asset, image_path, prompt,
                version_id=str(selected.get("id") or ""), quality_report=report,
            )
        else:
            state_json = _state_to_json(state)
        return {"state_json": state_json, "history": history, "review": visual, "version_id": selected.get("id")}
    except HTTPException:
        raise
    except (OSError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/projects/{project_id}/image-history/candidate/accept")
async def accept_image_candidate(project_id: str, req: ActivateImageVersionRequest):
    """Accept a warning/unreviewed candidate as the active image version."""
    try:
        state, asset, versions, _ = _resolve_image_asset(project_id, req)
        selected = versions.find_version(req.asset_type, req.asset_id, req.view_type, req.version_id)
        group_id = str((selected or {}).get("group_id") or "")
        if group_id:
            with asset_commit_transaction(project_store.get_project_dir(project_id)):
                group_histories = versions.accept_candidate_group(
                    req.asset_type,
                    req.asset_id,
                    group_id,
                )
                current_history = group_histories.get(req.view_type, [])
                for view_type, history in group_histories.items():
                    active_version = next((item for item in history if item.get("active")), {})
                    view_request = req.model_copy(update={"view_type": view_type})
                    view_state, view_asset, _view_versions, _ = _resolve_image_asset(project_id, view_request)
                    _save_image_asset_state(
                        project_id,
                        view_state,
                        view_asset,
                        versions.active_path(req.asset_type, req.asset_id, view_type),
                        str(active_version.get("prompt") or view_asset.image_prompt or ""),
                        version_id=str(active_version.get("id") or ""),
                        quality_report=active_version.get("quality_report", {}),
                    )
                active_version = next((item for item in current_history if item.get("active")), {})
                state_json = _state_to_json(_load_project_state(project_id))
            return {
                "state_json": state_json,
                "history": current_history,
                "histories": group_histories,
                "accepted_version_id": active_version.get("id", req.version_id),
                "group_id": group_id,
            }
        with asset_commit_transaction(project_store.get_project_dir(project_id)):
            active_path, prompt, history = versions.accept_candidate(
                req.asset_type,
                req.asset_id,
                req.view_type,
                req.version_id,
                asset.image_prompt,
            )
            active_version = next((item for item in history if item.get("active")), {})
            quality_report = active_version.get("quality_report", {})
            state_json = _save_image_asset_state(
                project_id,
                state,
                asset,
                active_path,
                prompt,
                version_id=str(active_version.get("id") or req.version_id),
                quality_report=quality_report,
            )
        return {"state_json": state_json, "history": history, "accepted_version_id": req.version_id}
    except HTTPException:
        raise
    except (OSError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/projects/{project_id}/image-history/candidate/reject")
async def reject_image_candidate(project_id: str, req: ActivateImageVersionRequest):
    """Reject a candidate while retaining its file and history record."""
    try:
        state, _asset, versions, _ = _resolve_image_asset(project_id, req)
        history = versions.reject_candidate(
            req.asset_type,
            req.asset_id,
            req.view_type,
            req.version_id,
        )
        return {"state_json": _state_to_json(state), "history": history, "rejected_version_id": req.version_id}
    except HTTPException:
        raise
    except (OSError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/api/prompts")
async def generate_prompts(req: StageRequest):
    """第 3 步：生成视频提示词。"""
    state = _state_from_json(req.state_json)
    try:
        result = await orchestrator.run_stage(3, state)
    except Exception as e:
        return StageResponse(state_json=req.state_json, log=[], error="操作失败，请重试")

    project_store.add_stage(result.project_id, "stage3")
    project_store.set_state_json(result.project_id, _state_to_json(result))
    return StageResponse(
        state_json=_state_to_json(result),
        log=result.execution_log,
    )


@router.post("/api/revise-prompt")
async def revise_prompt(req: PromptEditRequest):
    """AI 根据用户反馈修订指定镜头的提示词。"""
    state = _state_from_json(req.state_json)
    if not state.generation_prompts or req.shot_index >= len(state.generation_prompts.shots):
        raise HTTPException(400, "无效的镜头编号")

    original = state.generation_prompts.shots[req.shot_index].prompt
    revision_prompt = f"""用户对以下中文视频提示词有修改意见：

**原始提示词**：
{original}

**用户反馈**：
{req.feedback}

请根据用户反馈修改提示词，输出修改后的完整中文视频提示词（只输出 prompt 文本，不要其他解释）。

修改要求：
- 保留原 prompt 的角色锚点、绑定道具的材质与状态、动作时间轴、镜头语言、光线、技术参数和连续性约束
- 如果用户只要求改动作，只调整动作和时间轴，不要随意改变角色外貌
- 如果用户只要求改风格，保留角色锚点和剧情动作
- 输出必须是中文
- 保留或补充竖屏 9:16、电影感、高细节、自然运动模糊等基础技术标签"""

    try:
        new_prompt = await script_agent.llm.generate_text(
            system_prompt="你是一个专业的 AI 视频生成提示词工程师，擅长根据用户反馈优化中文视频描述。你必须保留角色锚点、道具状态、动作时间轴、镜头语言、光线、技术参数和连续性约束。只输出修改后的完整中文提示词。",
            user_prompt=revision_prompt,
            temperature=0.5,
        )
        new_prompt = new_prompt.strip().strip('"').strip("'")
        # 更新到 state
        state.generation_prompts.shots[req.shot_index].prompt = new_prompt
        state.prompt_overrides[str(req.shot_index)] = new_prompt
    except Exception:
        raise HTTPException(500, "AI 修订失败，请重试")

    return {
        "new_prompt": new_prompt,
        "shot_index": req.shot_index,
        "state_json": _state_to_json(state),
    }

__all__ = [name for name in globals() if not name.startswith("__")]
