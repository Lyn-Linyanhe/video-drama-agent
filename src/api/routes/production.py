"""FastAPI routes extracted from the original server entrypoint."""

from fastapi import APIRouter
from ..context import *
from src.services.audio_timeline import project_audio_assets as _project_audio_assets
from src.services.video_capabilities import (
    normalize_video_policy,
    public_video_capabilities,
    requires_reference_image,
    validate_video_duration,
)

router = APIRouter()


def _protect_explicit_mentions(prompt: str, references) -> tuple[str, dict[str, str]]:
    """Replace explicit @ tokens before an LLM revision and remember exact text."""
    protected = str(prompt or "")
    placeholders: dict[str, str] = {}
    for index, reference in enumerate(references or [], start=1):
        token = str(getattr(reference, "reference_token", "") or "").strip()
        if not token or token in placeholders.values():
            continue
        placeholder = f"__STUDIO_ASSET_MENTION_{index}__"
        protected = protected.replace(token, placeholder)
        placeholders[placeholder] = token
    return protected, placeholders


def _restore_explicit_mentions(prompt: str, placeholders: dict[str, str]) -> str:
    """Restore every protected token; never accept an LLM result that drops one."""
    restored = str(prompt or "")
    missing: list[str] = []
    for placeholder, token in placeholders.items():
        if placeholder not in restored:
            if token in restored:
                continue
            missing.append(token)
            continue
        restored = restored.replace(placeholder, token)
    if missing:
        raise ValueError("显式引用在 AI 改写结果中丢失：" + "、".join(missing))
    return restored


def _validate_explicit_reference_token(prompt: str, token: str) -> None:
    if token and token not in str(prompt or ""):
        raise HTTPException(422, f"显式引用必须出现在分镜提示词中：{token}")

@router.get("/api/projects/{project_id}/production")
async def get_project_production(project_id: str):
    """Return the server-persisted single-shot production workspace."""
    state = _load_project_state(project_id)
    payload = _production_payload(state)
    _save_project_state(state)
    return payload


@router.get("/api/video-capabilities")
async def video_capabilities():
    return {"providers": public_video_capabilities()}


@router.get("/api/projects/{project_id}/video-generation-policy")
async def get_video_generation_policy(project_id: str):
    state = _load_project_state(project_id)
    policy = normalize_video_policy(state.video_generation_policy)
    compact = {key: value for key, value in policy.items() if key != "capability"}
    if state.video_generation_policy != compact:
        state.video_generation_policy = compact
        _save_project_state(state)
    return {"project_id": project_id, "policy": compact, "providers": public_video_capabilities()}


@router.put("/api/projects/{project_id}/video-generation-policy")
async def put_video_generation_policy(project_id: str, req: VideoGenerationPolicyRequest):
    state = _load_project_state(project_id)
    policy = normalize_video_policy(state.video_generation_policy, req.model_dump())
    state.video_generation_policy = {key: value for key, value in policy.items() if key != "capability"}
    _save_project_state(state)
    return {"project_id": project_id, "policy": state.video_generation_policy, "providers": public_video_capabilities()}


@router.post("/api/projects/{project_id}/characters/reextract")
async def reextract_project_characters(project_id: str):
    """Regenerate visible character descriptions without replacing shot prompts."""
    state = _load_project_state(project_id)
    if not state.script.asset_manifest:
        raise HTTPException(409, "旧项目请先重新解析剧本，以建立视觉资产清单")
    if state.visual_design is None:
        raise HTTPException(409, "项目缺少视觉设计，请先重新解析剧本")
    try:
        source_text = state.script_draft_text or (
            state.user_input if state.input_mode == "script" else ""
        )
        repair_result = await visual_prompt_agent.reextract_visible_characters(
            state.script,
            source_text=source_text,
        )
        if hasattr(repair_result, "english"):
            english = repair_result.english
            chinese = repair_result.chinese
            repair_status = repair_result.status
            repair_errors = list(repair_result.errors or [])
        else:
            english, chinese = repair_result
            repair_status = "legacy"
            repair_errors = []
    except Exception as exc:
        raise HTTPException(502, f"Character extraction failed: {exc}") from exc
    state.visual_design.global_character_descriptions = english
    state.visual_design.global_character_descriptions_cn = chinese
    state.visual_design.profile_repair_status = repair_status
    state.visual_design.profile_repair_errors = repair_errors
    project_store.set_visual_design(project_id, state.visual_design.model_dump())
    _save_project_state(state)
    return {
        "visual_design": state.visual_design.model_dump(),
        "state_json": _state_to_json(state),
    }


@router.put("/api/projects/{project_id}/production/shots/{scene_id}")
async def update_project_production_shot(
    project_id: str,
    scene_id: int,
    req: ProductionShotUpdateRequest,
):
    state = _load_project_state(project_id)
    production = _ensure_shot_production(state, scene_id)
    prompt = _shot_prompt(state, scene_id)
    scene = _script_scene(state, scene_id)

    if req.prompt.strip():
        prompt.prompt = req.prompt.strip()
        state.prompt_overrides[str(scene_id)] = prompt.prompt
    if req.negative_prompt is not None:
        prompt.negative_prompt = req.negative_prompt.strip()
    if req.duration_sec is not None:
        candidate_policy = normalize_video_policy(
            state.video_generation_policy,
            req.video_override.model_dump() if req.video_override is not None else None,
        )
        if candidate_policy.get("provider") == "seedance":
            validate_video_duration(candidate_policy, req.duration_sec)
        prompt.duration_sec = req.duration_sec
        scene.duration_sec = req.duration_sec
    if req.references is not None:
        from src.schemas.assets import ShotReference

        catalog = _reference_catalog(state)["by_id"]
        # Keep persisted stale references addressable.  Their files may have
        # disappeared, but the user must still be able to edit the prompt or
        # remove the reference instead of receiving a misleading "unknown"
        # asset error on every save.
        persisted = {item.reference_id: item for item in production.references}
        references: list[ShotReference] = []
        seen: set[str] = set()
        for item in req.references:
            reference = catalog.get(item.reference_id) or persisted.get(item.reference_id)
            if reference is None:
                raise HTTPException(422, f"Unknown reference: {item.reference_id}")
            if reference.reference_id not in seen:
                if item.source == "explicit" and not item.token.strip():
                    raise HTTPException(422, "Explicit asset references require a token")
                _validate_explicit_reference_token(req.prompt.strip() or prompt.prompt, item.token.strip())
                selected = reference.model_copy(update={
                    "source": item.source,
                    "reference_token": item.token.strip() if item.source == "explicit" else "",
                    "explicit_base_source": item.explicit_base_source if item.source == "explicit" else "",
                })
                requested_version = item.version_id.strip()
                if requested_version:
                    try:
                        from src.utils.asset_versions import ImageAssetVersions
                        record = ImageAssetVersions(project_store.get_project_dir(project_id)).find_version(
                            selected.asset_type,
                            selected.asset_id,
                            selected.view_type,
                            requested_version,
                        )
                    except (OSError, TypeError, ValueError):
                        record = None
                    safe_path = None
                    if record:
                        try:
                            versions = ImageAssetVersions(project_store.get_project_dir(project_id))
                            candidate = Path(str(record.get("path") or "")).resolve()
                            candidate.relative_to(versions.asset_root.resolve())
                            safe_path = candidate
                        except (OSError, RuntimeError, ValueError):
                            safe_path = None
                    if not record or safe_path is None or not safe_path.is_file():
                        raise HTTPException(409, f"显式引用版本不可用：{requested_version}")
                    selected = selected.model_copy(update={
                        "asset_version_id": requested_version,
                        "image_path": str(safe_path),
                        "stale": False,
                    })
                references.append(selected)
                seen.add(reference.reference_id)
        production.references = references
    if req.video_override is not None:
        policy = normalize_video_policy(state.video_generation_policy, req.video_override.model_dump())
        production.video_override = {key: value for key, value in policy.items() if key != "capability"}
    if req.continuity_enabled is not None:
        production.continuity_enabled = req.continuity_enabled
        production.continuity_manual = True
        production.continuity_source_scene_id = scene_id - 1 if req.continuity_enabled and scene_id > 1 else 0
        if not req.continuity_enabled:
            production.continuity_source_version_id = ""
            production.continuity_tail_frame_path = ""
            production.continuity_stale = False
    if req.transition_type is not None:
        transition = req.transition_type.lower()
        if transition not in {"cut", "fade", "dissolve"}:
            raise HTTPException(422, "transition_type must be cut, fade, or dissolve")
        production.transition_type = transition
        production.transition_manual = True
        scene.transition_from_prev = transition
    if req.transition_duration_sec is not None:
        production.transition_duration_sec = req.transition_duration_sec
        production.transition_manual = True
        scene.transition_duration_sec = req.transition_duration_sec
    if req.prompt.strip() or req.negative_prompt is not None or req.duration_sec is not None:
        from .jobs import _mark_prompt_review_stale
        _mark_prompt_review_stale(state)
    _save_project_state(state)
    return _production_payload(state)


@router.post("/api/projects/{project_id}/production/shots/{scene_id}/revise-prompt")
async def revise_project_production_prompt(
    project_id: str,
    scene_id: int,
    req: ProductionPromptRevisionRequest,
):
    state = _load_project_state(project_id)
    prompt = _shot_prompt(state, scene_id)
    production = _ensure_shot_production(state, scene_id)
    protected_prompt, mention_placeholders = _protect_explicit_mentions(
        prompt.prompt,
        production.references,
    )
    revision = f"""请根据用户意见修改以下中文视频生成提示词，仅输出修改后的完整中文 prompt。
原始提示词：{protected_prompt}
用户意见：{req.feedback}
必须保留角色外貌一致性、绑定道具的材质与状态、动作时间轴、镜头语言、环境、灯光和技术约束；只修改用户明确要求的部分。"""
    try:
        updated = await script_agent.llm.generate_text(
            system_prompt="你是严谨的视频提示词编辑器。只输出完整中文视频提示词。",
            user_prompt=revision,
            temperature=0.4,
        )
    except Exception as exc:
        raise HTTPException(502, f"AI prompt revision failed: {exc}") from exc
    try:
        updated = _restore_explicit_mentions(updated.strip().strip('"').strip("'"), mention_placeholders)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    prompt.prompt = updated
    state.prompt_overrides[str(scene_id)] = prompt.prompt
    from .jobs import _mark_prompt_review_stale
    _mark_prompt_review_stale(state)
    _save_project_state(state)
    return _production_payload(state)


def _schedule_shot_video_job(state: DramaState, scene_id: int) -> str:
    key = (state.project_id, scene_id)
    if key in video_jobs:
        raise HTTPException(409, "该镜头正在生成")
    production = _ensure_shot_production(state, scene_id)
    if not production.references or not any(Path(item.image_path).is_file() for item in production.references):
        raise HTTPException(
            409,
            "请先生成角色形象图或场景图，并为该分镜添加至少一张有效参考图",
        )
    version_id = uuid.uuid4().hex[:12]
    production.generation_status = "generating"
    production.error_message = ""
    production.started_at = datetime.now().isoformat(timespec="seconds")
    production.completed_at = ""
    video_jobs.add(key)
    _save_project_state(state)
    asyncio.create_task(_run_shot_video_job(state.project_id, scene_id, version_id))
    return version_id


@router.post("/api/projects/{project_id}/production/shots/{scene_id}/generate", status_code=202)
async def generate_project_production_shot(project_id: str, scene_id: int):
    state = _load_project_state(project_id)
    fidelity_report = audit_current_prompt_fidelity(state)
    state.prompt_fidelity = fidelity_report.model_dump()
    fidelity_detail = prompt_fidelity_gate_detail(fidelity_report)
    if fidelity_detail:
        _save_project_state(state)
        raise HTTPException(409, detail=fidelity_detail)
    _shot_prompt(state, scene_id)
    await _prepare_auto_references(state)
    production = _ensure_shot_production(state, scene_id)
    policy = normalize_video_policy(state.video_generation_policy, production.video_override)
    if policy.get("provider") == "seedance":
        validate_video_duration(policy, _shot_prompt(state, scene_id).duration_sec)
    if production.continuity_enabled and not policy["capability"].get("continuity"):
        raise HTTPException(409, f"{policy['model']} 不支持首帧参考，无法生成连续镜头")
    active = job_store.list_jobs(project_id, states=ACTIVE_JOB_STATES, limit=100)
    duplicate = next((item for item in active if item["job_type"] == "video.generate" and item["target_id"] == str(scene_id)), None)
    if duplicate:
        return {"job_id": duplicate["job_id"], "production": _production_payload(state)}
    try:
        snapshot = _build_video_generation_snapshot(state, scene_id, policy)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if requires_reference_image(policy) and not snapshot["references"]:
        raise HTTPException(409, f"镜头 {scene_id} 缺少可用参考图，请先生成或添加图片")
    production.last_generation_snapshot = snapshot
    job = job_manager.create_job(
        project_id, "video.generate", "shot", str(scene_id),
        {
            "scene_id": scene_id,
            "video_policy": {key: value for key, value in policy.items() if key != "capability"},
            "generation_snapshot": snapshot,
        },
        provider=policy["provider"],
    )
    _save_project_state(state)
    return {"job_id": job["job_id"], "production": _production_payload(state)}


@router.post("/api/projects/{project_id}/production/generate-pending", status_code=202)
async def generate_project_pending_shots(project_id: str):
    state = _load_project_state(project_id)
    fidelity_report = audit_current_prompt_fidelity(state)
    state.prompt_fidelity = fidelity_report.model_dump()
    fidelity_detail = prompt_fidelity_gate_detail(fidelity_report)
    if fidelity_detail:
        _save_project_state(state)
        raise HTTPException(409, detail=fidelity_detail)
    await _prepare_auto_references(state)
    scheduled: list[int] = []
    specs: list[dict] = []
    skipped: list[dict[str, object]] = []
    active = job_store.list_jobs(project_id, states=ACTIVE_JOB_STATES, limit=500)
    active_targets = {
        item["target_id"]
        for item in active
        if item["job_type"] == "video.generate" and item["target_type"] == "shot"
    }
    for scene in state.script.scenes:
        production = _ensure_shot_production(state, scene.scene_id)
        if str(scene.scene_id) in active_targets:
            continue
        if production.generation_status not in {"idle", "failed", "canceled"} and production.video_versions:
            continue
        policy = normalize_video_policy(state.video_generation_policy, production.video_override)
        if policy.get("provider") == "seedance":
            validate_video_duration(policy, _shot_prompt(state, scene.scene_id).duration_sec)
        if production.continuity_enabled and not policy["capability"].get("continuity"):
            continue
        try:
            snapshot = _build_video_generation_snapshot(state, scene.scene_id, policy)
        except ValueError as exc:
            skipped.append({"scene_id": scene.scene_id, "reason": str(exc)})
            continue
        if requires_reference_image(policy) and not snapshot["references"]:
            continue
        production.last_generation_snapshot = snapshot
        specs.append({
            "job_type": "video.generate",
            "target_type": "shot",
            "target_id": str(scene.scene_id),
            "provider": policy["provider"],
            "payload": {
                "scene_id": scene.scene_id,
                "video_policy": {key: value for key, value in policy.items() if key != "capability"},
                "generation_snapshot": snapshot,
            },
        })
        scheduled.append(scene.scene_id)
    jobs = []
    if len(specs) == 1:
        jobs = [job_manager.create_job(project_id=project_id, **specs[0])]
    elif specs:
        _, jobs = job_manager.create_batch(project_id, specs)
    _save_project_state(state)
    return {
        "scheduled_scene_ids": scheduled,
        "job_ids": [item["job_id"] for item in jobs],
        "skipped": skipped,
        "production": _production_payload(state),
    }


@router.post("/api/projects/{project_id}/production/shots/{scene_id}/activate-version")
async def activate_project_video_version(
    project_id: str,
    scene_id: int,
    req: ActivateVideoVersionRequest,
):
    state = _load_project_state(project_id)
    production = _ensure_shot_production(state, scene_id)
    if not any(item.version_id == req.version_id for item in production.video_versions):
        raise HTTPException(404, "Video version not found")
    production.active_version_id = req.version_id
    production.generation_status = "success"
    production.error_message = ""
    _sync_active_shot_asset(state, scene_id)
    _save_project_state(state)
    return _production_payload(state)


@router.put("/api/projects/{project_id}/production/settings")
async def update_project_production_settings(
    project_id: str,
    req: ProductionSettingsRequest,
):
    """Persist project-level output settings used by every final assembly path."""
    state = _load_project_state(project_id)
    mode = req.subtitle_mode or ("dialogue" if req.subtitle_enabled else "off")
    state.subtitle_mode = mode
    state.subtitle_enabled = mode != "off"
    audio = _project_audio_assets(state)
    if req.audio_mode is not None:
        state.creative_profile.audio_mode = req.audio_mode
    if req.asr_enabled is not None:
        audio.asr_enabled = bool(req.asr_enabled)
    if req.asr_model is not None and req.asr_model.strip():
        audio.asr_model = req.asr_model.strip()
    _save_project_state(state)
    return _production_payload(state)


@router.get("/api/projects/{project_id}/prompt-policy")
async def get_project_prompt_policy(project_id: str):
    state = _load_project_state(project_id)
    provider = video_gen_agent.api_client.provider_name
    return {
        "project_id": project_id,
        "prompt_policy": state.prompt_policy.model_dump(),
        "provider": provider,
        "capabilities": provider_capabilities(provider),
    }


@router.put("/api/projects/{project_id}/prompt-policy")
async def update_project_prompt_policy(project_id: str, req: PromptPolicyRequest):
    from src.schemas.prompt import PromptPolicy

    state = _load_project_state(project_id)
    state.prompt_policy = PromptPolicy.model_validate(req.model_dump())
    from .jobs import _mark_prompt_review_stale
    _mark_prompt_review_stale(state)
    _save_project_state(state)
    return _production_payload(state)


@router.post("/api/projects/{project_id}/production/assemble")
async def assemble_project_production(project_id: str):
    """Assemble the project using server-persisted active video versions."""
    state = _load_project_state(project_id)
    try:
        result = await orchestrator.run_stage(6, state)
    except Exception as exc:
        raise HTTPException(502, f"Video assembly failed: {exc}") from exc
    project_store.set_final_video(project_id, result.output_video_path)
    _save_project_state(result)
    return _production_payload(result)

__all__ = [name for name in globals() if not name.startswith("__")]
