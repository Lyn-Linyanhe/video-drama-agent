"""FastAPI routes extracted from the original server entrypoint."""

from fastapi import APIRouter
from ..context import *
from .jobs import _job_public

router = APIRouter()


def _cancel_active_visual_jobs(project_id: str) -> None:
    """Discard superseded visual jobs after a replacement script is saved."""
    for job in job_store.list_jobs(project_id, states=ACTIVE_JOB_STATES, limit=100):
        if job["job_type"] == "script.visualize":
            job_manager.cancel(project_id, job["job_id"])


@router.get("/api/projects/{project_id}/script/draft")
async def get_script_draft(project_id: str):
    state = _load_project_state(project_id, require_visual=False, require_script=False)
    return {
        "project_id": project_id,
        "script_phase": state.script_phase,
        "draft_text": state.script_draft_text,
        "draft_instruction": state.script_draft_instruction,
        "draft_job_id": state.draft_job_id,
        "input_mode": state.input_mode,
        "source_filename": state.source_filename,
    }


@router.put("/api/projects/{project_id}/script/draft")
async def save_script_draft(project_id: str, req: ScriptDraftSaveRequest):
    state = _load_project_state(project_id, require_visual=False, require_script=False)
    if state.script_phase == "analyzing":
        raise HTTPException(409, "剧本正在解析，暂时不能覆盖草稿")
    state.script_draft_text = req.draft_text.strip()
    state.script_draft_instruction = ""
    state.script_phase = "draft_ready"
    state.input_mode = req.input_mode
    state.source_filename = req.source_filename
    state.user_input = state.script_draft_text
    _save_project_state(state)
    project_store.update_status(project_id, "draft_ready")
    return {"project_id": project_id, "script_phase": state.script_phase, "state_json": _state_to_json(state)}


@router.post("/api/projects/{project_id}/script/draft/confirm", status_code=202)
async def confirm_script_draft(project_id: str, req: ScriptDraftConfirmRequest | None = None):
    state = _load_project_state(project_id, require_visual=False, require_script=False)
    source_text = state.script_draft_text.strip()
    if state.script_phase not in {"draft_ready", "confirmed", "failed"} or not source_text:
        raise HTTPException(409, "请先生成或保存非空剧本草稿，再确认解析")
    _cancel_active_visual_jobs(project_id)
    state.script_phase = "analyzing"
    if req and req.source_filename:
        state.source_filename = req.source_filename
    _save_project_state(state)
    job = job_manager.create_job(
        project_id,
        "script.analyze",
        "script",
        "current",
        {
            "source_text": source_text,
            "original_input": source_text,
            "input_mode": state.input_mode,
            "source_filename": state.source_filename,
        },
        provider="llm",
    )
    return {"project_id": project_id, "job": _job_public(job), "script_phase": state.script_phase}


@router.post("/api/script/import")
async def import_script_source(file: UploadFile = File(...)):
    filename = Path(file.filename or "source.txt").name
    if Path(filename).suffix.lower() not in {".txt", ".docx", ".md"}:
        raise HTTPException(400, "Only TXT, DOCX, and MD files are supported")
    content = await file.read()
    if not content or len(content) > 2 * 1024 * 1024:
        raise HTTPException(400, "Script file must be between 1 byte and 2 MB")
    try:
        text = _extract_script_text(filename, content).strip()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not text:
        raise HTTPException(400, "No readable text found in the source file")
    if len(text) > LLM_MAX_INPUT_CHARS:
        raise HTTPException(400, f"Extracted screenplay exceeds {LLM_MAX_INPUT_CHARS:,} characters")
    return {"filename": filename, "text": text}


@router.post("/api/script", response_model=ScriptResponse)
async def generate_script(req: ScriptRequest):
    """第 1 步：生成剧本 + 视觉设计。"""
    if req.input_mode not in {"idea", "script"}:
        raise HTTPException(400, "input_mode must be idea or script")
    if req.project_id:
        project_id = req.project_id
        try:
            existing_project = project_store.get_project(project_id)
        except ValueError as exc:
            raise HTTPException(400, "Invalid project ID") from exc
        if not existing_project:
            raise HTTPException(404, "Project not found")
        state = _load_project_state(project_id, require_visual=False)
    else:
        project_id = project_store.create_project(
            title=req.user_input[:30] + ("…" if len(req.user_input) > 30 else ""),
            user_input=req.user_input,
        )
        state = DramaState(project_id=project_id)
    project_store.update_status(project_id, "生成中")

    state.user_input = req.user_input
    state.input_mode = req.input_mode
    state.source_filename = req.source_filename
    state.project_id = project_id
    requested_audio_mode = (
        state.creative_profile.audio_mode
        if req.project_id
        else "native_dialogue"
    )
    state.creative_profile = CreativeProfile.from_request(
        req.genre_tags,
        req.art_style,
        req.video_ratio,
        requested_audio_mode,
        visual_style_id=req.visual_style_id,
        style_modifiers=req.style_modifiers,
    )
    try:
        state.script = await script_agent.run(
            state.user_input, state.input_mode, state.creative_profile,
        )
    except Exception as e:
        import traceback
        err_msg = str(e)
        traceback.print_exc()
        return ScriptResponse(
            project_id=project_id,
            log=[f"❌ 脚本生成失败: {err_msg}",
                 "请检查：1) API 配置页面是否已保存 2) API Key 是否有效 3) 网络是否可达"],
        )

    _reconcile_state_after_script_change(state, state.script)
    state.visual_stage = "pending"
    state.visual_script_signature = _script_signature(state.script)
    project_store.set_script(project_id, state.script.model_dump())
    project_store.set_state_json(project_id, _state_to_json(state))
    project_store.update_status(project_id, "script_ready")
    _cancel_active_visual_jobs(project_id)
    visual_job = job_manager.create_job(
        project_id, "script.visualize", "script", "current",
        {"script_signature": state.visual_script_signature}, provider="llm",
    )
    state.visual_job_id = visual_job["job_id"]
    state.visual_stage = "generating"
    project_store.set_state_json(project_id, _state_to_json(state))
    return ScriptResponse(
        project_id=project_id,
        script=state.script.model_dump() if state.script else None,
        state_json=_state_to_json(state),
        log=state.execution_log,
        input_mode=state.input_mode,
        source_filename=state.source_filename,
    )


@router.post("/api/projects/{project_id}/script/analyze")
async def analyze_script_source(project_id: str, req: ScriptAnalyzeRequest):
    meta = project_store.get_project(project_id)
    if not meta:
        raise HTTPException(404, "Project not found")
    if req.input_mode not in {"idea", "script"}:
        raise HTTPException(400, "input_mode must be idea or script")
    state = _state_from_json((meta.get("assets") or {}).get("state_json") or "")
    state.project_id = project_id
    try:
        script = await script_agent.analyze_existing_script(req.source_text, state.creative_profile)
    except Exception as exc:
        raise HTTPException(502, f"AI script analysis failed: {exc}") from exc
    state.user_input = req.original_input or req.source_text
    state.input_mode = req.input_mode
    state.source_filename = req.source_filename
    _reconcile_state_after_script_change(state, script)
    state.visual_stage = "pending"
    state.visual_script_signature = _script_signature(script)
    project_store.set_script(project_id, script.model_dump())
    project_store.update_status(project_id, "script_ready")
    state_json = _state_to_json(state)
    project_store.set_state_json(project_id, state_json)
    _cancel_active_visual_jobs(project_id)
    visual_job = job_manager.create_job(
        project_id, "script.visualize", "script", "current",
        {"script_signature": state.visual_script_signature}, provider="llm",
    )
    state.visual_job_id = visual_job["job_id"]
    state.visual_stage = "generating"
    state_json = _state_to_json(state)
    project_store.set_state_json(project_id, state_json)
    return {
        "script": script.model_dump(),
        "state_json": state_json,
        "visual_job_id": state.visual_job_id,
    }


@router.put("/api/projects/{project_id}/script")
async def save_structured_script(project_id: str, req: ScriptUpdateRequest):
    from src.schemas.script import ScriptBreakdown

    meta = project_store.get_project(project_id)
    if not meta:
        raise HTTPException(404, "Project not found")
    try:
        script = ScriptBreakdown.model_validate(req.script)
    except Exception as exc:
        raise HTTPException(400, f"Invalid structured script: {exc}") from exc
    state = _state_from_json((meta.get("assets") or {}).get("state_json") or "")
    active_visual = [
        item for item in job_store.list_jobs(project_id, states=ACTIVE_JOB_STATES, limit=100)
        if item["job_type"] == "script.visualize"
    ]
    if active_visual and req.visual_conflict_action is None:
        raise HTTPException(409, {
            "code": "visual_task_running",
            "message": "视觉与分镜提示词仍在生成，请选择保留旧任务或按新剧本重启",
            "job_id": active_visual[0]["job_id"],
        })
    if active_visual and req.visual_conflict_action == "restart":
        for job in active_visual:
            job_manager.cancel(project_id, job["job_id"])
    _reconcile_state_after_script_change(state, script)
    state.visual_script_signature = _script_signature(script)
    state.visual_stage_error = ""
    state.visual_stage = "stale" if req.visual_conflict_action == "continue" else "pending"
    project_store.set_script(project_id, script.model_dump())
    state_json = _state_to_json(state)
    project_store.set_state_json(project_id, state_json)
    if req.visual_conflict_action == "restart" or not active_visual:
        visual_job = job_manager.create_job(
            project_id, "script.visualize", "script", "current",
            {"script_signature": state.visual_script_signature}, provider="llm",
        )
        state.visual_job_id = visual_job["job_id"]
        state.visual_stage = "generating"
        state_json = _state_to_json(state)
        project_store.set_state_json(project_id, state_json)
    return {"script": script.model_dump(), "state_json": state_json, "visual_stage": state.visual_stage, "visual_job_id": state.visual_job_id}


@router.post("/api/projects/{project_id}/script/revise")
async def revise_structured_script(project_id: str, req: ScriptRevisionRequest):
    meta = project_store.get_project(project_id)
    if not meta:
        raise HTTPException(404, "Project not found")
    state = _state_from_json((meta.get("assets") or {}).get("state_json") or "")
    if not state.script:
        raise HTTPException(400, "Generate or understand a script before revising it")
    try:
        script = await script_agent.revise_structure(state.script, req.instruction)
    except Exception as exc:
        raise HTTPException(502, f"AI script revision failed: {exc}") from exc
    active_visual = [
        item for item in job_store.list_jobs(project_id, states=ACTIVE_JOB_STATES, limit=100)
        if item["job_type"] == "script.visualize"
    ]
    for job in active_visual:
        job_manager.cancel(project_id, job["job_id"])
    _reconcile_state_after_script_change(state, script)
    state.visual_script_signature = _script_signature(script)
    state.visual_stage = "pending"
    state.visual_stage_error = ""
    project_store.set_script(project_id, script.model_dump())
    visual_job = job_manager.create_job(
        project_id, "script.visualize", "script", "current",
        {"script_signature": state.visual_script_signature}, provider="llm",
    )
    state.visual_job_id = visual_job["job_id"]
    state.visual_stage = "generating"
    state_json = _state_to_json(state)
    project_store.set_state_json(project_id, state_json)
    return {
        "script": script.model_dump(),
        "state_json": state_json,
        "visual_stage": state.visual_stage,
        "visual_job_id": state.visual_job_id,
    }

__all__ = [name for name in globals() if not name.startswith("__")]
