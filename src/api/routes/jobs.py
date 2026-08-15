"""FastAPI routes extracted from the original server entrypoint."""

from fastapi import APIRouter
import time
import shutil
import os
import asyncio
from pathlib import Path
from ..context import *
from src.services.audio_timeline import (
    auto_select_project_audio as _auto_select_project_audio,
    audio_summary as _audio_summary,
    layout_project_audio as _layout_project_audio,
    project_audio_assets as _project_audio_assets,
    render_project_audio_preview as _render_project_audio_preview,
    validate_project_voice_profiles as _validate_project_voice_profiles,
)
from src.services.asset_generation import (
    _asset_record,
    edit_character_reference as _edit_character_reference,
    edit_scene_reference_view as _edit_scene_reference_view,
    generate_character_reference_group as _generate_character_reference_group,
    generate_prop_master as _generate_prop_master,
    generate_prop_reference_views as _generate_prop_reference_views,
    generate_scene_master as _generate_scene_master,
    generate_scene_reference_group as _generate_scene_reference_group,
    regenerate_image_asset as _regenerate_image_asset,
    required_asset_views as _required_asset_views,
    stable_asset_seed as _stable_asset_seed,
)
from src.services.image_capabilities import normalize_policy
from src.services.video_capabilities import (
    normalize_video_policy,
    requires_reference_image,
    validate_video_duration,
    video_agent_for_policy,
)
from src.services.video_continuity import continuity_reference, extract_tail_frame, mark_dependents_stale
from src.services.asr import align_native_subtitles
from src.schemas.audio import SceneAudio
from src.schemas.prompt import ShotPrompt
from src.agents.prompt_review_agent import PromptReviewAgent

router = APIRouter()

# ── 项目级持久化任务 ─────────────────────────────────────────

def _job_hooks(context) -> GenerationHooks:
    return GenerationHooks(
        progress_callback=context.progress,
        provider_task_callback=context.provider_task,
        cancel_callback=context.check_cancelled,
    )


def _organize_provider_tail_frame(project_id: str, scene_id: int, version_id: str, path: str | None) -> str:
    """Move a provider-returned tail into the same project-owned video directory."""
    if not path or not Path(path).is_file():
        return ""
    destination = OUTPUT_DIR / project_id / "assets" / "videos" / f"shot_{scene_id:03d}" / version_id / "provider_tail.jpg"
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        if Path(path).resolve() != destination.resolve():
            shutil.move(path, destination)
        return str(destination)
    except OSError:
        return str(path)


def _job_public(job: dict) -> dict:
    total = max(1, int(job.get("progress_total") or 1))
    current = max(0, int(job.get("progress_current") or 0))
    percent = 100 if job.get("state") == "succeeded" else min(99, round(current / total * 100))
    return JobPublic(
        job_id=job["job_id"],
        project_id=job["project_id"],
        batch_id=job.get("batch_id", ""),
        job_type=job["job_type"],
        target_type=job.get("target_type", ""),
        target_id=job.get("target_id", ""),
        state=job["state"],
        stage=job.get("stage", ""),
        progress={"current": current, "total": total, "percent": percent},
        attempt=int(job.get("attempt") or 1),
        retry_of_job_id=job.get("retry_of_job_id", ""),
        provider=job.get("provider", ""),
        provider_task_id=job.get("provider_task_id", ""),
        payload=job.get("payload") or job.get("request") or {},
        result=job.get("result") or {},
        error=(job.get("error") or None),
        quality=(job.get("quality") or None),
        cancel_requested=bool(job.get("cancel_requested")),
        can_cancel=job["state"] in ACTIVE_JOB_STATES,
        can_retry=job["state"] in {"failed", "canceled", "interrupted"},
        created_at=job.get("created_at", ""),
        started_at=job.get("started_at", ""),
        completed_at=job.get("completed_at", ""),
        updated_at=job.get("updated_at", ""),
    ).model_dump()


def _batch_public(project_id: str, batch_id: str, jobs: list[dict] | None = None) -> dict:
    jobs = jobs if jobs is not None else job_store.list_jobs(project_id, batch_id=batch_id, limit=500)
    public_jobs = [_job_public(item) for item in jobs]
    states = [item["state"] for item in public_jobs]
    return JobBatchPublic(
        batch_id=batch_id,
        project_id=project_id,
        jobs=public_jobs,
        total=len(states),
        completed=sum(state in TERMINAL_JOB_STATES for state in states),
        succeeded=states.count("succeeded"),
        failed=states.count("failed") + states.count("interrupted"),
        canceled=states.count("canceled"),
        active=sum(state in ACTIVE_JOB_STATES for state in states),
    ).model_dump()


async def _job_script_generate(context, payload: dict) -> dict:
    context.progress("preparing", 0, 1, "正在准备剧本生成")
    state = DramaState(
        user_input=str(payload.get("user_input") or ""),
        input_mode=str(payload.get("input_mode") or "idea"),
        source_filename=str(payload.get("source_filename") or ""),
        project_id=context.project_id,
    )
    project_store.update_status(context.project_id, "生成中")
    try:
        context.progress("waiting_provider", 0, 1, "正在生成剧本结构与资产清单")
        result = state
        started = time.perf_counter()
        llm_metrics: dict = {}
        result.script = await script_agent.run(
            state.user_input, state.input_mode, state.creative_profile, telemetry=llm_metrics,
        )
        result.visual_performance = {
            "requests": [{
                "stage": "script", "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "provider": getattr(script_agent.llm, "provider", ""),
                "model": getattr(script_agent.llm, "model", ""),
                "input_chars": len(state.user_input),
                "output_chars": len(json.dumps(result.script.model_dump(), ensure_ascii=False)),
                "attempts": llm_metrics.get("attempts", 1),
                "repair_attempts": llm_metrics.get("repair_attempts", 0),
                "semantic_retries": llm_metrics.get("semantic_retries", 0),
                "fallback_used": llm_metrics.get("fallback_used", False),
                "transport_attempts": llm_metrics.get("transport_attempts", 0),
                "transport_retries": llm_metrics.get("transport_retries", 0),
                "timeout_sec": llm_metrics.get("timeout_sec", 0),
            }]
        }
        context.check_cancelled()
        if result.script is None:
            raise ValueError(result.error_message or "剧本生成未返回结构化结果")
        async with context.project_lock:
            context.progress("saving", 0, 1, "正在保存剧本")
            if result.script:
                project_store.set_script(context.project_id, result.script.model_dump())
            _reconcile_state_after_script_change(result, result.script)
            result.visual_stage = "pending"
            result.visual_script_signature = _script_signature(result.script)
            result.visual_stage_error = ""
            project_store.set_state_json(context.project_id, _state_to_json(result))
            project_store.update_status(context.project_id, "script_ready")
            _cancel_active_visual_jobs(context.project_id)
            visual_job = job_manager.create_job(
                context.project_id, "script.visualize", "script", "current",
                {"script_signature": result.visual_script_signature}, provider="llm",
            )
            result.visual_job_id = visual_job["job_id"]
            result.visual_stage = "generating"
            project_store.set_state_json(context.project_id, _state_to_json(result))
        return {
            "project_id": context.project_id,
            "script": result.script.model_dump() if result.script else None,
            "visual_job_id": result.visual_job_id,
            "state_json": _state_to_json(result),
            "input_mode": result.input_mode,
            "source_filename": result.source_filename,
        }
    except BaseException:
        project_store.update_status(context.project_id, "failed")
        raise


async def _job_script_draft_generate(context, payload: dict) -> dict:
    idea = str(payload.get("idea") or payload.get("user_input") or "").strip()
    if not idea:
        raise ValueError("故事创意不能为空")
    context.progress("waiting_provider", 0, 1, "正在生成可编辑剧本草稿")
    async with context.project_lock:
        state = _load_project_state(context.project_id, require_visual=False, require_script=False)
        state.script_phase = "draft_generating"
        state.input_mode = "idea"
        state.user_input = idea
        state.script_draft_instruction = ""
        _save_project_state(state)
        project_store.update_status(context.project_id, "draft_generating")
    try:
        draft_text = await script_agent.generate_draft(
            idea, state.creative_profile, payload.get("details") or {},
        )
    except BaseException:
        async with context.project_lock:
            failed = _load_project_state(context.project_id, require_visual=False, require_script=False)
            failed.script_phase = "failed"
            _save_project_state(failed)
        raise
    if not draft_text:
        raise ValueError("AI 未返回可编辑剧本草稿")
    context.check_cancelled()
    async with context.project_lock:
        latest = _load_project_state(context.project_id, require_visual=False, require_script=False)
        latest.script_draft_text = draft_text
        latest.script_draft_instruction = ""
        latest.draft_job_id = context.job_id
        latest.script_phase = "draft_ready"
        latest.error_message = ""
        _save_project_state(latest)
        project_store.update_status(context.project_id, "draft_ready")
    return {"draft_text": draft_text, "script_phase": "draft_ready", "state_json": _state_to_json(latest)}


async def _job_script_draft_revise(context, payload: dict) -> dict:
    instruction = str(payload.get("instruction") or "").strip()
    if not instruction:
        raise ValueError("草稿改写要求不能为空")
    async with context.project_lock:
        state = _load_project_state(context.project_id, require_visual=False, require_script=False)
        if not state.script_draft_text.strip():
            raise ValueError("请先生成或保存剧本草稿")
        draft_text = state.script_draft_text
        state.script_phase = "draft_generating"
        state.script_draft_instruction = instruction
        _save_project_state(state)
    context.progress("waiting_provider", 0, 1, "正在改写剧本草稿")
    try:
        revised = await script_agent.revise_draft(draft_text, instruction)
    except BaseException:
        async with context.project_lock:
            failed = _load_project_state(context.project_id, require_visual=False, require_script=False)
            failed.script_phase = "draft_ready"
            _save_project_state(failed)
        raise
    if not revised:
        raise ValueError("AI 未返回改写后的剧本草稿")
    context.check_cancelled()
    async with context.project_lock:
        latest = _load_project_state(context.project_id, require_visual=False, require_script=False)
        latest.script_draft_text = revised
        latest.script_phase = "draft_ready"
        latest.draft_job_id = context.job_id
        _save_project_state(latest)
    return {"draft_text": revised, "script_phase": "draft_ready", "state_json": _state_to_json(latest)}


async def _job_script_analyze(context, payload: dict) -> dict:
    source_text = str(payload.get("source_text") or "")
    if not source_text.strip():
        raise ValueError("剧本文本不能为空")
    context.progress("waiting_provider", 0, 1, "正在解析剧本结构与资产清单")
    started = time.perf_counter()
    llm_metrics: dict = {}
    script = await script_agent.analyze_existing_script(source_text, telemetry=llm_metrics)
    context.check_cancelled()
    async with context.project_lock:
        meta = project_store.get_project(context.project_id)
        if not meta:
            raise ValueError("项目不存在")
        state = _state_from_json((meta.get("assets") or {}).get("state_json") or "")
        state.project_id = context.project_id
        state.user_input = str(payload.get("original_input") or source_text)
        state.input_mode = str(payload.get("input_mode") or "script")
        state.source_filename = str(payload.get("source_filename") or "")
        _reconcile_state_after_script_change(state, script)
        state.visual_stage = "pending"
        state.script_draft_text = source_text
        state.script_phase = "analyzing"
        state.visual_script_signature = _script_signature(script)
        state.visual_stage_error = ""
        state.visual_performance = {
            "requests": [{
                "stage": "script", "elapsed_ms": round((time.perf_counter() - started) * 1000),
                "provider": getattr(script_agent.llm, "provider", ""),
                "model": getattr(script_agent.llm, "model", ""),
                "input_chars": len(source_text),
                "output_chars": len(json.dumps(script.model_dump(), ensure_ascii=False)),
                "attempts": llm_metrics.get("attempts", 1),
                "repair_attempts": llm_metrics.get("repair_attempts", 0),
                "semantic_retries": llm_metrics.get("semantic_retries", 0),
                "fallback_used": llm_metrics.get("fallback_used", False),
                "transport_attempts": llm_metrics.get("transport_attempts", 0),
                "transport_retries": llm_metrics.get("transport_retries", 0),
                "timeout_sec": llm_metrics.get("timeout_sec", 0),
            }]
        }
        project_store.set_script(context.project_id, script.model_dump())
        project_store.update_status(context.project_id, "script_ready")
        state_json = _state_to_json(state)
        project_store.set_state_json(context.project_id, state_json)
        _cancel_active_visual_jobs(context.project_id)
        visual_job = job_manager.create_job(
            context.project_id, "script.visualize", "script", "current",
            {"script_signature": state.visual_script_signature}, provider="llm",
        )
        state.visual_job_id = visual_job["job_id"]
        state.visual_stage = "generating"
        state.script_phase = "confirmed"
        state_json = _state_to_json(state)
        project_store.set_state_json(context.project_id, state_json)
    return {"script": script.model_dump(), "visual_job_id": state.visual_job_id, "state_json": state_json}


def _cancel_active_visual_jobs(project_id: str) -> None:
    """Stop superseded visual work only after a replacement script was saved."""
    for job in job_store.list_jobs(project_id, states=ACTIVE_JOB_STATES, limit=100):
        if job["job_type"] == "script.visualize":
            job_manager.cancel(project_id, job["job_id"])


def _prompt_review_signature(state: DramaState) -> str:
    """Version the audit against the exact script, prompts, and project policy."""
    payload = {
        "script": state.script.model_dump() if state.script else None,
        "generation_prompts": state.generation_prompts.model_dump() if state.generation_prompts else None,
        "prompt_policy": state.prompt_policy.model_dump(),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def _mark_prompt_review_stale(state: DramaState) -> None:
    """Keep reports from being presented as current after a prompt edit."""
    previous = dict(state.prompt_review or {})
    if not previous:
        return
    previous["status"] = "stale"
    previous["error"] = ""
    state.prompt_review = previous


async def _job_prompt_review(context, payload: dict) -> dict:
    """Audit generated storyboard prompts with the configured Kimi account."""
    expected_signature = str(payload.get("prompt_signature") or "")
    agent = PromptReviewAgent()
    if not agent.enabled():
        return {"skipped": True, "reason": "Kimi 分镜审核已关闭"}
    if not agent.is_configured():
        raise ValueError(agent.configuration_error())

    async with context.project_lock:
        state = _load_project_state(context.project_id)
        current_signature = _prompt_review_signature(state)
        if expected_signature and expected_signature != current_signature:
            _mark_prompt_review_stale(state)
            _save_project_state(state)
            return {"stale": True, "prompt_signature": expected_signature}
        state.prompt_review = {
            **dict(state.prompt_review or {}),
            "status": "generating",
            "prompt_signature": current_signature,
            "job_id": context.job_id,
            "provider": "kimi",
            "model": agent._settings()[1],
            "error": "",
        }
        _save_project_state(state)
        script = state.script.model_copy(deep=True)
        prompts = state.generation_prompts.model_copy(deep=True)
        visual = state.visual_design.model_copy(deep=True) if state.visual_design else None
        policy = state.prompt_policy.model_copy(deep=True)

    context.progress("waiting_provider", 0, 1, "Kimi 正在核对剧本与分镜提示词")
    try:
        result = await agent.run(script, prompts, visual, policy)
    except BaseException as exc:
        async with context.project_lock:
            latest = _load_project_state(context.project_id, require_visual=False)
            if _prompt_review_signature(latest) == expected_signature:
                latest.prompt_review = {
                    **dict(latest.prompt_review or {}),
                    "status": "failed",
                    "prompt_signature": expected_signature,
                    "job_id": context.job_id,
                    "provider": "kimi",
                    "model": agent._settings()[1],
                    "error": str(exc)[:500],
                }
                _save_project_state(latest)
        raise
    context.check_cancelled()
    async with context.project_lock:
        latest = _load_project_state(context.project_id)
        current_signature = _prompt_review_signature(latest)
        if expected_signature and expected_signature != current_signature:
            _mark_prompt_review_stale(latest)
            _save_project_state(latest)
            return {"stale": True, "prompt_signature": expected_signature}
        latest.prompt_review = {
            "status": "ready",
            "prompt_signature": current_signature,
            "job_id": context.job_id,
            "provider": "kimi",
            "model": agent._settings()[1],
            "error": "",
            "report": result.model_dump(),
        }
        _save_project_state(latest)
    context.progress("saving", 1, 1, "已保存 Kimi 分镜审核报告")
    return {
        "stale": False,
        "prompt_signature": current_signature,
        "report": result.model_dump(),
    }


async def _job_script_visualize(context, payload: dict) -> dict:
    """Generate visual design after the user can already review the script."""
    expected_signature = str(payload.get("script_signature") or "")
    async with context.project_lock:
        state = _load_project_state(context.project_id, require_visual=False)
        if not state.script:
            raise ValueError("剧本不存在，无法生成视觉与分镜提示词")
        # Loading a legacy project applies deterministic manifest repairs. Those
        # repairs can change the in-memory JSON (and therefore its derived
        # signature) without changing the authored screenplay. The persisted
        # signature is the revision key captured when this job was queued; it
        # remains valid unless a save operation has replaced it.
        current_signature = _script_signature(state.script)
        persisted_signature = str(getattr(state, "visual_script_signature", "") or "")
        if expected_signature not in {current_signature, persisted_signature}:
            state.visual_stage = "stale"
            _save_project_state(state)
            return {"stale": True, "script_signature": expected_signature}
        state.visual_stage = "generating"
        state.visual_job_id = context.job_id
        _save_project_state(state)
        script = state.script.model_copy(deep=True)
        profile = state.creative_profile
        source_text = state.script_draft_text or (
            state.user_input if state.input_mode == "script" else ""
        )
    context.progress("waiting_provider", 0, 1, "正在生成视觉设计与分镜提示词")
    started = time.perf_counter()
    llm_metrics: dict = {}
    try:
        visual, generation_prompts = await visual_prompt_agent.run(
            script,
            creative_profile=profile,
            telemetry=llm_metrics,
            progress_callback=context.progress,
            source_text=source_text,
        )
    except asyncio.CancelledError:
        async with context.project_lock:
            latest = _load_project_state(context.project_id, require_visual=False)
            latest_signature = _script_signature(latest.script)
            if (
                latest.visual_job_id == context.job_id
                and expected_signature in {latest_signature, str(getattr(latest, "visual_script_signature", "") or "")}
            ):
                # A refresh must leave the last complete prompt set usable.
                # First-time visualization remains pending so the user can retry.
                latest.visual_stage = "ready" if latest.generation_prompts else "pending"
                latest.visual_stage_error = ""
                _save_project_state(latest)
        raise
    except BaseException as exc:
        async with context.project_lock:
            latest = _load_project_state(context.project_id, require_visual=False)
            if expected_signature == _script_signature(latest.script):
                latest.visual_stage = "failed"
                latest.visual_stage_error = str(exc)[:500]
                _save_project_state(latest)
        raise
    context.check_cancelled()
    async with context.project_lock:
        latest = _load_project_state(context.project_id, require_visual=False)
        latest_signature = _script_signature(latest.script)
        latest_persisted_signature = str(getattr(latest, "visual_script_signature", "") or "")
        if expected_signature not in {latest_signature, latest_persisted_signature}:
            latest.visual_stage = "stale"
            _save_project_state(latest)
            return {"stale": True, "script_signature": expected_signature}
        # The script may have been normalized while loading a legacy project
        # (for example, after removing virtual props). Persist the signature
        # that was actually validated so the next refresh uses the same
        # revision key instead of comparing against an obsolete one.
        latest.visual_script_signature = latest_signature
        latest.visual_design = visual
        latest.generation_prompts = generation_prompts
        latest.visual_stage = "ready"
        latest.visual_stage_error = ""
        latest.visual_job_id = context.job_id
        requests = list((latest.visual_performance or {}).get("requests") or [])
        requests.append({
            "stage": "visualize", "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "provider": getattr(visual_prompt_agent.llm, "provider", ""),
            "model": getattr(visual_prompt_agent.llm, "model", ""),
            "input_chars": llm_metrics.get("input_chars") or len(json.dumps(script.model_dump(), ensure_ascii=False)),
            "output_chars": len(json.dumps({"visual": visual.model_dump(), "prompts": generation_prompts.model_dump()}, ensure_ascii=False)),
            "attempts": llm_metrics.get("attempts", 1),
            "repair_attempts": llm_metrics.get("repair_attempts", 0),
            "semantic_retries": llm_metrics.get("semantic_retries", 0),
            "fallback_used": llm_metrics.get("fallback_used", False),
            "transport_attempts": llm_metrics.get("transport_attempts", 0),
            "transport_retries": llm_metrics.get("transport_retries", 0),
            "timeout_sec": llm_metrics.get("timeout_sec", 0),
        })
        latest.visual_performance = {"requests": requests}
        project_store.set_visual_design(context.project_id, visual.model_dump())
        project_store.add_stage(context.project_id, "stage1")
        project_store.update_status(context.project_id, "scripted")
        reviewer = PromptReviewAgent()
        prompt_signature = _prompt_review_signature(latest)
        if reviewer.enabled() and reviewer.is_configured():
            latest.prompt_review = {
                "status": "generating",
                "prompt_signature": prompt_signature,
                "job_id": "",
                "provider": "kimi",
                "model": reviewer._settings()[1],
                "error": "",
            }
            _save_project_state(latest)
            review_job = job_manager.create_job(
                context.project_id,
                "prompt.review",
                "prompt_set",
                "current",
                {"prompt_signature": prompt_signature},
                provider="kimi",
            )
            latest.prompt_review["job_id"] = review_job["job_id"]
        else:
            latest.prompt_review = {
                "status": "unavailable",
                "prompt_signature": prompt_signature,
                "job_id": "",
                "provider": "kimi",
                "model": reviewer._settings()[1],
                "error": reviewer.configuration_error(),
            }
        _save_project_state(latest)
    return {
        "stale": False,
        "script_signature": expected_signature,
        "visual_design": visual.model_dump(),
        "generation_prompts": generation_prompts.model_dump(),
        "prompt_review": latest.prompt_review,
    }


async def _job_asset_generate(context, payload: dict) -> dict:
    request = RegenerateImageRequest(
        asset_type=str(payload.get("asset_type") or ""),
        asset_id=str(payload.get("asset_id") or ""),
        view_type=str(payload.get("view_type") or ""),
        prompt=str(payload.get("custom_instructions") or ""),
        seed=payload.get("seed"),
        use_standard_view_prompt=not bool(payload.get("custom_instructions")),
        provider=str(payload.get("provider") or ""),
        model=str(payload.get("model") or ""),
        resolution=str(payload.get("resolution") or ""),
    )
    context.progress("preparing", 0, 1, "正在准备图片提示词和生成参数")
    last_quality = None
    if request.asset_type == "prop" and request.view_type == "front":
        with bind_generation_hooks(_job_hooks(context)):
            response = await _generate_prop_master(
                context.project_id,
                request.asset_id,
                "" if request.use_standard_view_prompt else request.prompt.strip(),
                {"provider": request.provider, "model": request.model, "resolution": request.resolution},
                context.project_lock,
                bool(payload.get("regenerate")),
            )
        context.progress("saving", 1, 1, "道具母版已保存，等待确认")
        return {
            "asset_type": "prop",
            "asset_id": request.asset_id,
            "view_type": "front",
            "version_id": response.get("version_id", ""),
            "image_path": response.get("image_path", ""),
            "seed": response.get("seed", 0),
            "needs_confirmation": True,
            "quality_report": response.get("quality", {}),
        }
    if request.asset_type == "prop" and request.view_type in {"back", "left", "right", "top", "bottom"}:
        with bind_generation_hooks(_job_hooks(context)):
            response = await _generate_prop_reference_views(
                context.project_id,
                request.asset_id,
                (request.view_type,),
                "" if request.use_standard_view_prompt else request.prompt.strip(),
                {"provider": request.provider, "model": request.model, "resolution": request.resolution},
                context.project_lock,
            )
        context.progress("saving", 1, 1, "道具参考视图已保存")
        item = (response.get("generated") or [{}])[0]
        return {
            "asset_type": "prop",
            "asset_id": request.asset_id,
            "view_type": request.view_type,
            "version_id": item.get("version_id", ""),
            "image_path": item.get("image_path", ""),
            "generation_mode": item.get("generation_mode", ""),
            "quality_report": item.get("quality_report", (response.get("quality_reports") or {}).get(request.view_type, {})),
        }
    for attempt in range(2):
        context.check_cancelled()
        try:
            context.progress("submitting_provider", 0, 1, "正在提交图片生成")
            with bind_generation_hooks(_job_hooks(context)):
                response = await _regenerate_image_asset(
                    context.project_id, request, quality_check=True,
                    project_lock=context.project_lock,
                )
            last_quality = response.get("quality")
            if last_quality:
                context.quality(last_quality)
            state = _state_from_json(response["state_json"])
            record = _asset_record(
                state, request.asset_type, request.asset_id, request.view_type,
            )
            active = next((item for item in response.get("history", []) if item.get("active")), {})
            return {
                "asset_type": request.asset_type,
                "asset_id": request.asset_id,
                "view_type": request.view_type,
                "version_id": active.get("id", ""),
                "image_path": record.image_path if record else "",
                "quality_report": last_quality or {},
            }
        except MediaQualityFailure as exc:
            last_quality = exc.report
            context.quality(last_quality)
            if attempt == 0:
                job_store.add_event(
                    context.job_id, "quality_retry", "running", "validating",
                    "确定性质量检查失败，自动重试一次",
                )
                request.seed = secrets.randbelow(999999) + 1
                continue
            raise
    raise MediaQualityFailure("图片质量检查失败", last_quality or {})


async def _job_character_reference_group(context, payload: dict) -> dict:
    context.progress("preparing", 0, 2, "正在准备角色母版")
    with bind_generation_hooks(_job_hooks(context)):
        result = await _generate_character_reference_group(
            context.project_id,
            str(payload.get("asset_id") or ""),
            str(payload.get("custom_instructions") or ""),
            {"provider": payload.get("provider"), "model": payload.get("model"), "resolution": payload.get("resolution")},
            context.project_lock,
        )
    context.progress("saving", 2, 2, "角色参考组图已保存")
    return result


async def _job_scene_reference_group(context, payload: dict) -> dict:
    context.progress("preparing", 0, 1, "正在准备场景参考组图")
    with bind_generation_hooks(_job_hooks(context)):
        result = await _generate_scene_reference_group(
            context.project_id, str(payload.get("asset_id") or ""),
            str(payload.get("custom_instructions") or ""),
            {"provider": payload.get("provider"), "model": payload.get("model"), "resolution": payload.get("resolution")}, context.project_lock,
            str(payload.get("state_id") or ""),
        )
    context.progress("saving", 1, 1, "场景参考组图已保存")
    return result


async def _job_prop_reference_group(context, payload: dict) -> dict:
    context.progress("preparing", 0, 1, "正在准备道具参考视图")
    views = tuple(dict.fromkeys(str(view) for view in (payload.get("views") or []) if view))
    if not views:
        state = _load_project_state(context.project_id)
        required = _required_asset_views(state, "prop")
        views = tuple(view for view in required.get(str(payload.get("asset_id") or ""), ()) if view != "front")
    with bind_generation_hooks(_job_hooks(context)):
        result = await _generate_prop_reference_views(
            context.project_id,
            str(payload.get("asset_id") or ""),
            views,
            str(payload.get("custom_instructions") or ""),
            {"provider": payload.get("provider"), "model": payload.get("model"), "resolution": payload.get("resolution")},
            context.project_lock,
        )
    context.progress("saving", 1, 1, "道具参考视图已保存")
    return result


async def _job_character_reference_edit(context, payload: dict) -> dict:
    context.progress("preparing", 0, 1, "正在按角色母版修订图片")
    request = CharacterReferenceEditRequest(**payload)
    with bind_generation_hooks(_job_hooks(context)):
        result = await _edit_character_reference(context.project_id, request, context.project_lock)
    context.progress("saving", 1, 1, "角色参考修订已保存")
    return result


async def _job_scene_reference_edit(context, payload: dict) -> dict:
    context.progress("preparing", 0, 1, "正在按场景母版修订图片")
    request = SceneReferenceEditRequest(**payload)
    with bind_generation_hooks(_job_hooks(context)):
        result = await _edit_scene_reference_view(
            context.project_id,
            str(payload.get("asset_id") or ""),
            str(payload.get("state_id") or ""),
            str(payload.get("view_type") or ""),
            request.prompt,
            request.reference_version_ids,
            {"provider": request.provider, "model": request.model, "resolution": request.resolution},
            context.project_lock,
        )
    context.progress("saving", 1, 1, "场景参考修订已保存")
    return result


async def _mark_video_job_stopped(project_id: str, scene_id: int, message: str) -> None:
    try:
        state = _load_project_state(project_id)
        production = _ensure_shot_production(state, scene_id)
        if production.generation_status == "generating":
            production.generation_status = "canceled"
            production.error_message = message
            production.completed_at = datetime.now().isoformat(timespec="seconds")
            _save_project_state(state)
    except Exception:
        pass


async def _job_video_generate(context, payload: dict) -> dict:
    scene_id = int(payload.get("scene_id") or 0)
    context.progress("preparing", 0, 1, "正在准备分镜参考图")
    snapshot = payload.get("generation_snapshot") or {}
    async with context.project_lock:
        state = _load_project_state(context.project_id)
        production = _ensure_shot_production(state, scene_id)
        if not snapshot:
            await _prepare_auto_references(state)
            policy = normalize_video_policy(state.video_generation_policy, payload.get("video_policy") or production.video_override)
            snapshot = _build_video_generation_snapshot(state, scene_id, policy)
            production.last_generation_snapshot = snapshot
            _save_project_state(state)
        else:
            if int(snapshot.get("scene_id") or 0) != scene_id:
                raise ValueError("视频任务快照的镜头编号不匹配")
            policy = normalize_video_policy(snapshot.get("policy") or {}, None)
            if snapshot.get("references_pending"):
                # The toolbar submits one job before references exist.
                # Prepare them here, then freeze a fresh provider snapshot.
                submitted_snapshot = dict(snapshot)
                await _prepare_auto_references(state)
                refreshed_snapshot = _build_video_generation_snapshot(state, scene_id, policy)
                for key in (
                    "raw_prompt", "raw_negative_prompt", "prepared_shot", "provider",
                    "effective_prompt", "effective_negative_prompt", "provider_prompt",
                    "provider_negative_prompt", "negative_prompt_mode", "policy",
                ):
                    if key in submitted_snapshot:
                        refreshed_snapshot[key] = submitted_snapshot[key]
                snapshot = refreshed_snapshot
                snapshot["references_pending"] = False
                production.last_generation_snapshot = snapshot
                _save_project_state(state)
            if snapshot.get("references_pending"):
                raise ValueError("提交时参考图尚未就绪，请在资产生成完成后重新提交视频任务")
    context.check_cancelled()
    state = _load_project_state(context.project_id)
    production = _ensure_shot_production(state, scene_id)
    prompt = ShotPrompt.model_validate(snapshot.get("prepared_shot") or {
        "scene_id": scene_id,
        "prompt": snapshot.get("provider_prompt") or snapshot.get("raw_prompt") or "",
        "negative_prompt": snapshot.get("provider_negative_prompt") or "",
        "duration_sec": _shot_prompt(state, scene_id).duration_sec,
    })
    if policy.get("provider") == "seedance":
        validate_video_duration(policy, prompt.duration_sec)
    effective_prompt = {
        "effective_prompt": snapshot.get("effective_prompt") or prompt.prompt,
        "effective_negative_prompt": snapshot.get("effective_negative_prompt") or prompt.negative_prompt,
        "provider_prompt": snapshot.get("provider_prompt") or prompt.prompt,
        "provider_negative_prompt": snapshot.get("provider_negative_prompt") or prompt.negative_prompt,
        "negative_prompt_mode": snapshot.get("negative_prompt_mode") or "merged",
    }
    snapshot_references = list(snapshot.get("references") or [])
    missing_references = [
        item.get("reference_id") or item.get("asset_id") or "unknown"
        for item in snapshot_references
        if not Path(item.get("image_path") or "").is_file()
    ]
    if missing_references:
        raise ValueError("提交快照中的参考图已丢失：" + "、".join(missing_references) + "；请重新提交视频任务")
    snapshot_labels = list(snapshot.get("reference_labels") or [
        f"{item.get('asset_type', 'reference')}：{item.get('label') or item.get('asset_id', '')}（{item.get('view_type', '')}）"
        for item in snapshot_references
    ])
    references = []
    reference_labels = []
    seen_reference_keys = set()
    for index, item in enumerate(snapshot_references):
        key = _reference_submission_key(item)
        if key in seen_reference_keys:
            continue
        seen_reference_keys.add(key)
        references.append(item)
        reference_labels.append(
            snapshot_labels[index]
            if index < len(snapshot_labels)
            else _reference_label_for_item(item, len(references))
        )
    asset_references = [item["image_path"] for item in references]
    if policy["capability"].get("multi_reference"):
        reference_paths = asset_references
    elif policy["capability"].get("image_to_video"):
        reference_paths, reference_labels = asset_references[:1], reference_labels[:1]
    else:
        reference_paths, reference_labels = [], []
    if requires_reference_image(policy) and not reference_paths:
        raise ValueError("请先生成角色、场景或道具参考图")
    version_id = context.job_id[:12]
    async with context.project_lock:
        latest = _load_project_state(context.project_id)
        latest_production = _ensure_shot_production(latest, scene_id)
        if any(item.version_id == version_id for item in latest_production.video_versions):
            return {"scene_id": scene_id, "version_id": version_id, "already_saved": True}
        latest_production.generation_status = "generating"
        latest_production.error_message = ""
        latest_production.started_at = datetime.now().isoformat(timespec="seconds")
        latest_production.completed_at = ""
        _save_project_state(latest)

    try:
        last_quality = None
        for attempt in range(2):
            context.progress("submitting_provider", 0, 1, "正在提交视频生成")
            with bind_generation_hooks(_job_hooks(context)):
                asset = await video_agent_for_policy(policy).generate_shot(
                        prompt,
                        reference_paths,
                    reference_labels=reference_labels,
                    visual_design=state.visual_design,
                    scene_vision_descriptions=state.scene_vision_descriptions,
                    script=state.script,
                    project_id=context.project_id,
                    version_id=version_id,
                    request_is_prepared=True,
                    provider_prompt=snapshot.get("provider_prompt") or prompt.prompt,
                     reference_mode=snapshot.get("reference_mode") or "auto",
                     last_frame_reference=snapshot.get("last_frame_reference_path") or None,
                     return_last_frame=bool(snapshot.get("return_last_frame")),
                     generate_audio=bool(snapshot.get("native_audio_requested")),
                 )
            asset.api_used = policy["model"]
            context.check_cancelled()
            if not asset.success or not asset.local_path:
                native_asr = None
                raise RuntimeError(asset.error_message or "视频生成失败")
            context.progress("validating", 0, 1, "正在检查视频文件")
            native_asr = None
            if getattr(asset, "native_audio_enabled", False):
                configured_asr = str(os.getenv("ASR_ENABLED", "0")).strip().lower() in {"1", "true", "yes", "on"}
                current_audio = _project_audio_assets(state)
                configured_asr = bool(getattr(current_audio, "asr_enabled", False) or configured_asr)
                native_asr = await asyncio.to_thread(
                    align_native_subtitles,
                    asset.local_path,
                    list(snapshot.get("dialogue_plan") or []),
                    enabled=configured_asr,
                    model_name=str(getattr(current_audio, "asr_model", "small") or "small"),
                )
            last_quality = inspect_video(
                asset.local_path,
                expected_duration=prompt.duration_sec,
                allow_placeholder=policy["provider"] == "mock",
            )
            context.quality(last_quality)
            if last_quality["status"] == "failed":
                if attempt == 0:
                    job_store.add_event(
                        context.job_id, "quality_retry", "running", "validating",
                        "确定性质量检查失败，自动重试一次",
                    )
                    continue
                messages = [item["message"] for item in last_quality["technical_checks"] if not item["passed"]]
                raise MediaQualityFailure("；".join(messages), last_quality)
            context.progress("visual_review", 0, 1, "正在检查画面与提示词一致性")
            visual_review = await review_video_subjective(asset.local_path, effective_prompt["effective_prompt"])
            last_quality["visual_review"] = visual_review
            if visual_review.get("status") == "warning":
                last_quality["status"] = "warning"
                last_quality["warnings"] = visual_review.get("issues", [])
            context.quality(last_quality)
            break

        context.check_cancelled()
        async with context.project_lock:
            latest = _load_project_state(context.project_id)
            latest_production = _ensure_shot_production(latest, scene_id)
            context.check_cancelled()
            from src.schemas.assets import ShotVideoVersion

            now = datetime.now().isoformat(timespec="seconds")
            latest_production.continuity_source_scene_id = production.continuity_source_scene_id
            latest_production.continuity_source_version_id = production.continuity_source_version_id
            latest_production.continuity_tail_frame_path = production.continuity_tail_frame_path
            latest_production.continuity_stale = False
            if not any(item.version_id == version_id for item in latest_production.video_versions):
                version = ShotVideoVersion(
                    **asset.model_dump(), version_id=version_id, created_at=now,
                    provider=policy["provider"], model=policy["model"], resolution=policy["resolution"],
                    capability_snapshot=policy["capability"],
                    unit_price_rmb_per_sec=policy["unit_price_rmb_per_sec"],
                    estimated_cost_rmb=round(policy["unit_price_rmb_per_sec"] * float(asset.duration_sec or prompt.duration_sec), 4),
                    continuity_source_scene_id=production.continuity_source_scene_id,
                    continuity_source_version_id=production.continuity_source_version_id,
                    generation_snapshot=snapshot,
                    quality_report=last_quality or {},
                )
                provider_tail = _organize_provider_tail_frame(
                    context.project_id,
                    scene_id,
                    version_id,
                    asset.last_frame_path,
                )
                if provider_tail:
                    version.provider_last_frame_url = asset.last_frame_url
                    version.provider_last_frame_path = provider_tail
                    version.tail_frame_path = provider_tail
                extract_tail_frame(context.project_id, scene_id, version)
                latest_production.video_versions.append(version)
            latest_production.active_version_id = version_id
            latest_production.generation_status = "success"
            latest_production.error_message = ""
            latest_production.completed_at = now
            _sync_active_shot_asset(latest, scene_id)
            if getattr(asset, "native_audio_enabled", False):
                latest_audio = _project_audio_assets(latest)
                scene_audio = next(
                    (item for item in latest_audio.scenes if item.scene_id == scene_id),
                    None,
                )
                if scene_audio is None:
                    scene_audio = SceneAudio(scene_id=scene_id)
                    latest_audio.scenes.append(scene_audio)
                scene_audio.native_audio_enabled = True
                scene_audio.native_audio_path = str(asset.native_audio_path or asset.local_path or "")
                scene_audio.native_audio_source = "seedance"
                if native_asr:
                    scene_audio.subtitle_correction_status = str(native_asr.get("status") or "fallback")
                    scene_audio.asr_provider = str(native_asr.get("provider") or "faster-whisper")
                    scene_audio.asr_model = str(native_asr.get("model") or latest_audio.asr_model)
                    scene_audio.asr_segments = list(native_asr.get("segments") or [])
                    scene_audio.asr_changed_lines = list(native_asr.get("changed_lines") or [])
                    scene_audio.asr_error = str(native_asr.get("error") or "")
                    latest_audio.subtitle_correction_status = scene_audio.subtitle_correction_status
            mark_dependents_stale(latest, scene_id, version_id)
            _save_project_state(latest)
        return {
            "scene_id": scene_id,
            "version_id": version_id,
            "video_path": asset.local_path,
            "generation_snapshot": snapshot,
            **effective_prompt,
        }
    except (asyncio.CancelledError, BaseException) as exc:
        if isinstance(exc, asyncio.CancelledError) or context.cancel_event.is_set():
            await asyncio.shield(_mark_video_job_stopped(context.project_id, scene_id, "任务已由用户终止"))
        else:
            await _mark_video_job_stopped(context.project_id, scene_id, str(exc)[:300])
        raise


async def _job_audio_generate(context, payload: dict) -> dict:
    context.progress("preparing", 0, 1, "正在准备配音和 BGM")
    async with context.project_lock:
        state = _load_project_state(context.project_id)
        if payload.get("operation") == "preview":
            context.progress("mixing", 0, 1, "正在混音音频预览")
            audio = _project_audio_assets(state)
            summary = _audio_summary(state)
            if not state.script:
                _save_project_state(state)
                return {
                    "audio": audio.model_dump(),
                    "preview_mix_path": "",
                    "warning": "请先确认并解析剧本，再生成音频预览",
                }
            audio.preview_mix_path = await asyncio.to_thread(_render_project_audio_preview, state)
            context.check_cancelled()
            _save_project_state(state)
            warning = ""
            if not summary["can_assemble"]:
                warning = "当前音频仍有未生成、失败或时间冲突的台词；预览可试听，正式合成前需处理"
            return {
                "audio": audio.model_dump(),
                "preview_mix_path": audio.preview_mix_path,
                "warning": warning,
            }
        line_id = str(payload.get("line_id") or "")
        if line_id:
            context.progress("generating", 0, 1, f"正在重新生成台词 {line_id}")
            state.audio_assets = await audio_agent.run(
                state.script, state.generated_assets, state.project_id, state.audio_assets,
                force_line_ids={line_id},
                native_scene_audio={
                    asset.scene_id: asset.model_dump()
                    for asset in (state.generated_assets.shots if state.generated_assets else [])
                    if getattr(asset, "native_audio_enabled", False)
                },
            )
            result = state
        else:
            result = await orchestrator.run_stage(5, state)
        _auto_select_project_audio(result)
        await _refresh_native_asr(result)
        _layout_project_audio(result)
        context.check_cancelled()
        _save_project_state(result)
    return {"audio": result.audio_assets.model_dump() if result.audio_assets else None}


async def _refresh_native_asr(state: DramaState) -> None:
    """Re-run optional local ASR for existing native-audio video versions."""
    if not state.audio_assets or not state.generated_assets:
        return
    configured = str(os.getenv("ASR_ENABLED", "0")).strip().lower() in {"1", "true", "yes", "on"}
    configured = bool(state.audio_assets.asr_enabled or configured)
    for asset in state.generated_assets.shots:
        if not getattr(asset, "native_audio_enabled", False) or not asset.local_path:
            continue
        production = state.shot_productions.get(asset.scene_id)
        active = next(
            (item for item in (production.video_versions if production else [])
             if item.version_id == (production.active_version_id if production else "")),
            None,
        )
        snapshot = active.generation_snapshot if active else {}
        result = await asyncio.to_thread(
            align_native_subtitles,
            asset.local_path,
            list(snapshot.get("dialogue_plan") or []),
            enabled=configured,
            model_name=str(state.audio_assets.asr_model or "small"),
        )
        scene_audio = next((item for item in state.audio_assets.scenes if item.scene_id == asset.scene_id), None)
        if scene_audio is None:
            scene_audio = SceneAudio(scene_id=asset.scene_id)
            state.audio_assets.scenes.append(scene_audio)
        scene_audio.native_audio_enabled = True
        scene_audio.native_audio_path = str(asset.native_audio_path or asset.local_path)
        scene_audio.native_audio_source = "seedance"
        scene_audio.subtitle_correction_status = str(result.get("status") or "fallback")
        scene_audio.asr_provider = str(result.get("provider") or "faster-whisper")
        scene_audio.asr_model = str(result.get("model") or state.audio_assets.asr_model)
        scene_audio.asr_segments = list(result.get("segments") or [])
        scene_audio.asr_changed_lines = list(result.get("changed_lines") or [])
        scene_audio.asr_error = str(result.get("error") or "")
        state.audio_assets.subtitle_correction_status = scene_audio.subtitle_correction_status


async def _job_final_assemble(context, payload: dict) -> dict:
    context.progress("preparing", 0, 1, "正在准备成片合成")
    async with context.project_lock:
        state = _load_project_state(context.project_id)
        result = await orchestrator.run_stage(6, state)
        context.check_cancelled()
        project_store.set_final_video(context.project_id, result.output_video_path)
        _save_project_state(result)
    return {"output_video_path": result.output_video_path}


register_handlers(job_manager, {
    "script.generate": _job_script_generate,
    "script.draft.generate": _job_script_draft_generate,
    "script.draft.revise": _job_script_draft_revise,
    "script.analyze": _job_script_analyze,
    "script.visualize": _job_script_visualize,
    "prompt.review": _job_prompt_review,
    "asset.generate": _job_asset_generate,
    "character.reference.group": _job_character_reference_group,
    "scene.reference.group": _job_scene_reference_group,
    "prop.reference.group": _job_prop_reference_group,
    "character.reference.edit": _job_character_reference_edit,
    "scene.reference.edit": _job_scene_reference_edit,
    "video.generate": _job_video_generate,
    "audio.generate": _job_audio_generate,
    "final.assemble": _job_final_assemble,
})


REFERENCE_JOB_HANDLERS = {
    "character.reference.group": _job_character_reference_group,
    "scene.reference.group": _job_scene_reference_group,
    "prop.reference.group": _job_prop_reference_group,
    "character.reference.edit": _job_character_reference_edit,
    "scene.reference.edit": _job_scene_reference_edit,
}


def _ensure_reference_job_handlers() -> None:
    """Keep reference-image jobs available after a runtime manager replacement.

    Config reloads and isolated test applications can replace the mutable job
    manager after this module was imported. Those managers still need the three
    persistent reference-image handlers before request validation reaches job
    creation.
    """
    for job_type, handler in REFERENCE_JOB_HANDLERS.items():
        if job_type not in job_manager.handlers:
            job_manager.register(job_type, handler)


def _project_or_404(project_id: str) -> dict:
    try:
        project = project_store.get_project(project_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid project ID") from exc
    if not project:
        raise HTTPException(404, "Project not found")
    return project


def _character_wan_master_missing_or_stale(state, asset_id: str) -> bool:
    from src.agents.image_gen_agent import CHARACTER_PROMPT_TEMPLATE_VERSION

    master = _asset_record(state, "character", asset_id, "front_half")
    return (
        not master
        or not getattr(master, "image_path", "")
        or not Path(master.image_path).is_file()
        or not getattr(master, "version_id", "")
        or not str(getattr(master, "model", "") or "").startswith("wan2.7-image")
        or getattr(master, "prompt_template_version", 1) < CHARACTER_PROMPT_TEMPLATE_VERSION
    )


def _character_wan_master_job_spec(asset_id: str, payload: dict, task_policy: dict, regenerate: bool = False) -> dict:
    return {
        "job_type": "asset.generate",
        "target_type": "character",
        "target_id": f"{asset_id}:front_half",
        "provider": task_policy["provider"],
        "payload": {
            "asset_type": "character",
            "asset_id": asset_id,
            "view_type": "front_half",
            "regenerate": regenerate,
            "custom_instructions": str(payload.get("custom_instructions") or ""),
            "seed": None if regenerate else _stable_asset_seed("character", asset_id, "front_half"),
            **task_policy,
        },
    }


def _scene_asset_record(state, asset_id: str, view_type: str, state_id: str = ""):
    return next(
        (
            item for item in (state.scene_images.scenes if state.scene_images else [])
            if (item.location_id or str(item.scene_id)) == asset_id
            and item.view_type == view_type
            and (not state_id or (getattr(item, "state_id", "") or "") == state_id)
        ),
        None,
    )


def _scene_wan_confirmed_master(state, asset_id: str, state_id: str = ""):
    master = _scene_asset_record(state, asset_id, "scene_center", state_id)
    return (
        master
        and getattr(master, "image_path", "")
        and Path(master.image_path).is_file()
        and getattr(master, "version_id", "")
        and getattr(master, "is_master", False)
        and not getattr(master, "needs_confirmation", False)
    )


def _prop_wan_confirmed_master(state, asset_id: str):
    master = _asset_record(state, "prop", asset_id, "front")
    return (
        master
        and getattr(master, "image_path", "")
        and Path(master.image_path).is_file()
        and getattr(master, "version_id", "")
        and getattr(master, "is_master", False)
        and not getattr(master, "needs_confirmation", False)
    )


@router.post("/api/projects/{project_id}/jobs", status_code=202)
async def create_project_job(project_id: str, req: JobCreateRequest):
    _project_or_404(project_id)
    _ensure_reference_job_handlers()
    production_job_types = {
        "asset.generate", "character.reference.group", "character.reference.edit", "scene.reference.edit",
        "scene.reference.group", "prop.reference.group", "prompt.review", "video.generate", "audio.generate", "final.assemble",
    }
    if req.job_type in production_job_types:
        workflow_state = _load_project_state(project_id, require_visual=False, require_script=False)
        if workflow_state.script_phase != "confirmed":
            raise HTTPException(409, "请先确认并解析剧本")
    specs: list[dict] = []
    if req.job_type == "script.analyze":
        state = _load_project_state(project_id, require_visual=False, require_script=False)
        source_text = str(req.payload.get("source_text") or state.script_draft_text or "").strip()
        if state.script_phase not in {"draft_ready", "confirmed", "failed"}:
            raise HTTPException(409, "请先生成或保存剧本草稿，再确认解析")
        if not source_text:
            raise HTTPException(400, "剧本草稿不能为空")
        _cancel_active_visual_jobs(project_id)
        state.script_phase = "analyzing"
        state.script_draft_text = source_text
        _save_project_state(state)
        specs.append({
            "job_type": "script.analyze", "target_type": "script", "target_id": "current", "provider": "llm",
            "payload": {**req.payload, "source_text": source_text, "original_input": str(req.payload.get("original_input") or source_text)},
        })
    elif req.job_type == "prompt.review":
        state = _load_project_state(project_id)
        reviewer = PromptReviewAgent()
        if not reviewer.enabled():
            raise HTTPException(409, "Kimi 分镜审核已关闭")
        if not reviewer.is_configured():
            raise HTTPException(409, reviewer.configuration_error())
        signature = _prompt_review_signature(state)
        state.prompt_review = {
            **dict(state.prompt_review or {}),
            "status": "generating",
            "prompt_signature": signature,
            "job_id": "",
            "provider": "kimi",
            "model": reviewer._settings()[1],
            "error": "",
        }
        _save_project_state(state)
        specs.append({
            "job_type": "prompt.review",
            "target_type": "prompt_set",
            "target_id": "current",
            "provider": "kimi",
            "payload": {"prompt_signature": signature},
        })
    elif req.job_type == "asset.generate":
        state = _load_project_state(project_id)
        task_policy = normalize_policy(state.image_generation_policy, {
            "provider": req.payload.get("provider"),
            "model": req.payload.get("model"),
            "resolution": req.payload.get("resolution"),
        })
        asset_type = str(req.payload.get("asset_type") or req.target_type)
        required = _required_asset_views(state, asset_type)
        asset_id = str(req.payload.get("asset_id") or req.target_id)
        state_id = str(req.payload.get("state_id") or "")
        regenerate = bool(req.payload.get("regenerate"))
        selected = {asset_id: required.get(asset_id, ())} if asset_id else required
        if asset_id and asset_id not in required:
            raise HTTPException(404, "Asset not found in the current project manifest")
        requested_view = str(req.payload.get("view_type") or "")
        # A no-view asset request is expanded into a Wan reference group below.
        # Group/edit endpoints only accept the model's group resolution (2K for
        # Wan 2.7), so resolve the policy in group mode before creating the
        # persistent job instead of persisting an invalid 1K snapshot.
        if task_policy["provider"] == "wan" and not requested_view:
            requested_group_resolution = str(
                req.payload.get("resolution")
                or (state.image_generation_policy or {}).get("resolution")
                or ""
            ).upper()
            # 1K is a valid Wan single-image preset but not a valid group
            # preset.  Keep the user's model choice and promote only this
            # lower preset; unsupported high presets (for example 4K) still
            # fail validation instead of being silently downgraded.
            if requested_group_resolution == "1K":
                requested_group_resolution = "2K"
            task_policy = normalize_policy(state.image_generation_policy, {
                "provider": req.payload.get("provider"),
                "model": req.payload.get("model"),
                "resolution": requested_group_resolution,
            }, mode="group")
        if asset_type == "prop" and requested_view == "standard_views":
            selected = {selected_id: ("standard_views",) for selected_id in selected}
        if asset_type in {"scene", "character", "prop"} and task_policy["provider"] == "wan" and not requested_view:
            for selected_id, views in selected.items():
                if requested_view and requested_view not in views:
                    continue
                if asset_type == "scene":
                    if not _scene_wan_confirmed_master(state, selected_id, state_id):
                        master = _scene_asset_record(state, selected_id, "scene_center", state_id)
                        if not regenerate and master and master.image_path and Path(master.image_path).is_file() and getattr(master, "needs_confirmation", False):
                            continue
                        specs.append({
                            "job_type": "asset.generate",
                            "target_type": "scene",
                            "target_id": f"{selected_id}:scene_center",
                            "provider": task_policy["provider"],
                            "payload": {
                                "asset_type": "scene",
                                "asset_id": selected_id,
                                "view_type": "scene_center",
                                "state_id": state_id,
                                "regenerate": regenerate,
                                "custom_instructions": str(req.payload.get("custom_instructions") or ""),
                                "seed": None if regenerate else _stable_asset_seed("scene", selected_id, "scene_center"),
                                **task_policy,
                            },
                        })
                        continue
                if asset_type == "prop":
                    if not _prop_wan_confirmed_master(state, selected_id):
                        master = _asset_record(state, "prop", selected_id, "front")
                        if not regenerate and master and master.image_path and Path(master.image_path).is_file() and getattr(master, "needs_confirmation", False):
                            continue
                        specs.append({
                            "job_type": "asset.generate",
                            "target_type": "prop",
                            "target_id": f"{selected_id}:front",
                            "provider": task_policy["provider"],
                            "payload": {
                                "asset_type": "prop",
                                "asset_id": selected_id,
                                "view_type": "front",
                                "regenerate": regenerate,
                                "custom_instructions": str(req.payload.get("custom_instructions") or ""),
                                "seed": None if regenerate else _stable_asset_seed("prop", selected_id, "front"),
                                **task_policy,
                            },
                        })
                        continue
                    derived_views = tuple(view for view in views if view != "front")
                    existing = [_asset_record(state, "prop", selected_id, view) for view in derived_views]
                    if not regenerate and derived_views and all(
                        item and item.image_path and Path(item.image_path).is_file() and not getattr(item, "is_stale", False)
                        for item in existing
                    ):
                        continue
                    if derived_views:
                        specs.append({
                            "job_type": "prop.reference.group",
                            "target_type": "prop",
                            "target_id": selected_id,
                            "provider": "wan",
                            "payload": {
                                "asset_id": selected_id,
                                "views": list(derived_views),
                                "regenerate": regenerate,
                                "custom_instructions": str(req.payload.get("custom_instructions") or ""),
                                **task_policy,
                            },
                        })
                    continue
                existing = [_asset_record(state, asset_type, selected_id, view) for view in views]
                if not regenerate and all(item and item.image_path and Path(item.image_path).is_file() for item in existing):
                    if asset_type == "character" and not _character_wan_master_missing_or_stale(state, selected_id):
                        continue
                    if asset_type == "scene":
                        continue
                group_job_type = "character.reference.group" if asset_type == "character" else "scene.reference.group"
                specs.append({"job_type": group_job_type, "target_type": asset_type, "target_id": selected_id, "provider": "wan", "payload": {"asset_id": selected_id, "state_id": state_id if asset_type == "scene" else "", "regenerate": regenerate, "custom_instructions": str(req.payload.get("custom_instructions") or ""), **task_policy}})
        else:
            character_template_version = 0
            if asset_type == "character":
                from src.agents.image_gen_agent import CHARACTER_PROMPT_TEMPLATE_VERSION
                character_template_version = CHARACTER_PROMPT_TEMPLATE_VERSION
            for selected_id, views in selected.items():
                for view_type in views:
                    if requested_view and view_type != requested_view:
                        continue
                    existing = _scene_asset_record(state, selected_id, view_type, state_id) if asset_type == "scene" else _asset_record(state, asset_type, selected_id, view_type)
                    stale_character = (
                        asset_type == "character"
                        and existing
                        and getattr(existing, "prompt_template_version", 1) < character_template_version
                    )
                    if not regenerate and existing and existing.image_path and Path(existing.image_path).is_file() and not stale_character:
                        continue
                    specs.append({"job_type": "asset.generate", "target_type": asset_type, "target_id": f"{selected_id}:{view_type}", "provider": task_policy["provider"], "payload": {"asset_type": asset_type, "asset_id": selected_id, "view_type": view_type, "state_id": state_id if asset_type == "scene" else "", "regenerate": regenerate, "custom_instructions": str(req.payload.get("custom_instructions") or ""), "seed": None if regenerate else _stable_asset_seed(asset_type, selected_id, view_type), **task_policy}})
    elif req.job_type == "scene.reference.group":
        state = _load_project_state(project_id)
        asset_id = str(req.payload.get("asset_id") or req.target_id)
        if asset_id not in _required_asset_views(state, "scene"):
            raise HTTPException(404, "Scene asset not found")
        task_policy = normalize_policy(state.image_generation_policy, {
            "provider": req.payload.get("provider"),
            "model": req.payload.get("model"),
            "resolution": req.payload.get("resolution"),
        }, mode="group")
        specs.append({
            "job_type": req.job_type, "target_type": "scene", "target_id": asset_id, "provider": task_policy["provider"],
            "payload": {"asset_id": asset_id, "state_id": str(req.payload.get("state_id") or ""), "custom_instructions": str(req.payload.get("custom_instructions") or ""), **task_policy},
        })
    elif req.job_type == "prop.reference.group":
        state = _load_project_state(project_id)
        asset_id = str(req.payload.get("asset_id") or req.target_id)
        required = _required_asset_views(state, "prop")
        if asset_id not in required:
            raise HTTPException(404, "Prop asset not found")
        task_policy = normalize_policy(state.image_generation_policy, {
            "provider": req.payload.get("provider"),
            "model": req.payload.get("model"),
            "resolution": req.payload.get("resolution"),
        }, mode="group")
        views = [str(view) for view in (req.payload.get("views") or []) if str(view)]
        if not views:
            views = [view for view in required[asset_id] if view != "front"]
        if any(view not in {"back", "left", "right", "top", "bottom"} for view in views):
            raise HTTPException(400, "Invalid prop derived view")
        specs.append({
            "job_type": req.job_type,
            "target_type": "prop",
            "target_id": asset_id,
            "provider": task_policy["provider"],
            "payload": {
                "asset_id": asset_id,
                "views": views,
                "custom_instructions": str(req.payload.get("custom_instructions") or ""),
                **task_policy,
            },
        })
    elif req.job_type == "character.reference.group":
        state = _load_project_state(project_id)
        asset_id = str(req.payload.get("asset_id") or req.target_id)
        if asset_id not in _required_asset_views(state, "character"):
            raise HTTPException(404, "Character asset not found")
        task_policy = normalize_policy(state.image_generation_policy, {
            "provider": req.payload.get("provider"),
            "model": req.payload.get("model"),
            "resolution": req.payload.get("resolution"),
        }, mode="group")
        specs.append({
            "job_type": req.job_type, "target_type": "character", "target_id": asset_id, "provider": task_policy["provider"],
            "payload": {"asset_id": asset_id, "custom_instructions": str(req.payload.get("custom_instructions") or ""), **task_policy},
        })
    elif req.job_type == "character.reference.edit":
        state = _load_project_state(project_id)
        task_policy = normalize_policy(state.image_generation_policy, {
            "provider": req.payload.get("provider"),
            "model": req.payload.get("model"),
            "resolution": req.payload.get("resolution"),
        }, mode="reference_edit")
        asset_id = str(req.payload.get("asset_id") or req.target_id)
        view_type = str(req.payload.get("view_type") or "")
        specs.append({
            "job_type": req.job_type, "target_type": "character", "target_id": f"{asset_id}:{view_type}", "provider": task_policy["provider"], "payload": {**req.payload, **task_policy},
        })
    elif req.job_type == "scene.reference.edit":
        state = _load_project_state(project_id)
        task_policy = normalize_policy(state.image_generation_policy, {
            "provider": req.payload.get("provider"),
            "model": req.payload.get("model"),
            "resolution": req.payload.get("resolution"),
        }, mode="reference_edit")
        asset_id = str(req.payload.get("asset_id") or req.target_id)
        state_id = str(req.payload.get("state_id") or "")
        view_type = str(req.payload.get("view_type") or "")
        if asset_id not in _required_asset_views(state, "scene"):
            raise HTTPException(404, "Scene asset not found")
        if view_type not in {"scene_left_45", "scene_right_45", "scene_reverse"}:
            raise HTTPException(400, "Invalid editable scene view")
        specs.append({
            "job_type": req.job_type,
            "target_type": "scene",
            "target_id": f"{asset_id}:{view_type}",
            "provider": task_policy["provider"],
            "payload": {
                **req.payload,
                "asset_id": asset_id,
                "state_id": state_id,
                "view_type": view_type,
                **task_policy,
            },
        })
    elif req.job_type == "video.generate":
        state = _load_project_state(project_id)
        scene_id = int(req.payload.get("scene_id") or req.target_id or 0)
        scenes = [scene_id] if scene_id else [scene.scene_id for scene in state.script.scenes]
        skipped_video_shots: list[dict[str, object]] = []
        for selected_scene_id in scenes:
            production = _ensure_shot_production(state, selected_scene_id)
            policy = normalize_video_policy(state.video_generation_policy, production.video_override)
            if production.continuity_enabled and not policy["capability"].get("continuity"):
                raise HTTPException(409, f"{policy['model']} 不支持首帧参考，无法生成连续镜头")
            if scene_id or production.generation_status in {"idle", "failed", "canceled"} or not production.video_versions:
                try:
                    snapshot = _build_video_generation_snapshot(state, selected_scene_id, policy)
                except ValueError as exc:
                    if scene_id:
                        raise HTTPException(409, str(exc)) from exc
                    skipped_video_shots.append({"scene_id": selected_scene_id, "reason": str(exc)})
                    continue
                if requires_reference_image(policy) and not snapshot["references"]:
                    snapshot["references_pending"] = True
                production.last_generation_snapshot = snapshot
                specs.append({
                    "job_type": "video.generate",
                    "target_type": "shot",
                    "target_id": str(selected_scene_id),
                    "provider": policy["provider"],
                    "payload": {
                        "scene_id": selected_scene_id,
                        "video_policy": {key: value for key, value in policy.items() if key != "capability"},
                        "generation_snapshot": snapshot,
                    },
                })
    elif req.job_type == "audio.generate":
        operation = str(req.payload.get("operation") or "")
        line_id = str(req.payload.get("line_id") or req.target_id or "")
        if operation != "preview":
            state = _load_project_state(project_id)
            preflight = _validate_project_voice_profiles(state)
            required = preflight["speakers"]
            if req.payload.get("line_id"):
                scene_id, _, dialogue_index = line_id.partition(":")
                try:
                    scene = next(scene for scene in state.script.scenes if scene.scene_id == int(scene_id))
                    dialogue = scene.dialogue[int(dialogue_index) - 1]
                    required = [item for item in required if item["character"] == dialogue.character]
                except (AttributeError, IndexError, ValueError):
                    raise HTTPException(404, "Audio dialogue line not found")
            invalid = [item for item in required if not item["ready"]]
            if invalid:
                detail = "；".join(f"{item['character']}：{item['reason']}" for item in invalid)
                raise HTTPException(409, f"请先确认项目音色：{detail}")
            _save_project_state(state)
        specs.append({
            "job_type": "audio.generate",
            "target_type": "audio_line" if req.payload.get("line_id") else (req.target_type or "audio_project"),
            "target_id": line_id if req.payload.get("line_id") else req.target_id,
            "provider": "tts",
            "payload": ({"operation": "preview"} if operation == "preview" else ({"line_id": line_id} if req.payload.get("line_id") else {})),
        })
    else:
        specs.append({
            "job_type": req.job_type,
            "target_type": req.target_type,
            "target_id": req.target_id,
            "payload": req.payload,
        })
    active_jobs = job_store.list_jobs(project_id, states=ACTIVE_JOB_STATES, limit=500)
    active_by_target = {
        (item["job_type"], item["target_type"], item["target_id"]): item
        for item in active_jobs
    }
    duplicates = [
        active_by_target[(spec["job_type"], spec.get("target_type", ""), spec.get("target_id", ""))]
        for spec in specs
        if (spec["job_type"], spec.get("target_type", ""), spec.get("target_id", "")) in active_by_target
    ]
    specs = [
        spec for spec in specs
        if (spec["job_type"], spec.get("target_type", ""), spec.get("target_id", "")) not in active_by_target
    ]
    if not specs:
        if len(duplicates) == 1:
            public_job = _job_public(duplicates[0])
            result = {"job": public_job, "jobs": [public_job]}
            if req.job_type == "video.generate" and 'skipped_video_shots' in locals():
                result["skipped"] = skipped_video_shots
            return result
        if duplicates:
            batch_id = next((item["batch_id"] for item in duplicates if item["batch_id"]), "")
            batch = _batch_public(project_id, batch_id, duplicates)
            result = {"batch": batch, "jobs": batch["jobs"]}
            if req.job_type == "video.generate" and 'skipped_video_shots' in locals():
                result["skipped"] = skipped_video_shots
            return result
        batch = _batch_public(project_id, uuid.uuid4().hex[:12], [])
        result = {"batch": batch, "jobs": batch["jobs"]}
        if req.job_type == "video.generate" and 'skipped_video_shots' in locals():
            result["skipped"] = skipped_video_shots
        return result
    if len(specs) == 1:
        job = job_manager.create_job(project_id=project_id, **specs[0])
        public_job = _job_public(job)
        result = {"job": public_job, "jobs": [public_job]}
        if req.job_type == "video.generate" and 'skipped_video_shots' in locals():
            result["skipped"] = skipped_video_shots
        return result
    batch_id, jobs = job_manager.create_batch(project_id, specs)
    batch = _batch_public(project_id, batch_id, jobs)
    result = {"batch": batch, "jobs": batch["jobs"]}
    if req.job_type == "video.generate" and 'skipped_video_shots' in locals():
        result["skipped"] = skipped_video_shots
    return result


@router.get("/api/projects/{project_id}/jobs")
async def list_project_jobs(project_id: str, states: str = "", batch_id: str = "", limit: int = 100):
    _project_or_404(project_id)
    selected_states = [item for item in states.split(",") if item] or None
    jobs = job_store.list_jobs(project_id, selected_states, batch_id, limit)
    return {"project_id": project_id, "jobs": [_job_public(job) for job in jobs]}


@router.get("/api/projects/{project_id}/job-events")
async def stream_project_job_events(
    request: Request, project_id: str, after_event_id: int = 0,
):
    """Stream durable project task events so clients never need to redraw on a poll."""
    _project_or_404(project_id)
    last_event_id = request.headers.get("last-event-id", "")
    try:
        cursor = max(int(after_event_id or 0), int(last_event_id or 0))
    except ValueError:
        cursor = max(0, int(after_event_id or 0))
    # A fresh browser already hydrates task state through /jobs. Replaying the
    # full durable history can be megabytes for completed script jobs, so only
    # an explicit cursor requests replay. Reconnects always provide one.
    if not last_event_id and "after_event_id" not in request.query_params:
        cursor = job_store.latest_project_event_id(project_id)

    async def event_stream():
        nonlocal cursor
        heartbeat_at = asyncio.get_running_loop().time()
        while not await request.is_disconnected():
            events = job_store.list_project_events(project_id, cursor)
            if events:
                for item in events:
                    event = item["event"]
                    cursor = int(event["event_id"])
                    payload = json.dumps(
                        {"event": event, "job": _job_public(item["job"])},
                        ensure_ascii=False, separators=(",", ":"),
                    )
                    yield f"id: {cursor}\nevent: job\ndata: {payload}\n\n"
                heartbeat_at = asyncio.get_running_loop().time()
                continue
            now = asyncio.get_running_loop().time()
            if now - heartbeat_at >= 15:
                yield ": heartbeat\n\n"
                heartbeat_at = now
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/api/projects/{project_id}/jobs/{job_id}")
async def get_project_job(project_id: str, job_id: str):
    job = job_store.get_job(project_id, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {"job": _job_public(job), "events": job_store.list_events(project_id, job_id)}


@router.post("/api/projects/{project_id}/jobs/{job_id}/cancel")
async def cancel_project_job(project_id: str, job_id: str):
    try:
        job = job_manager.cancel(project_id, job_id)
    except KeyError as exc:
        raise HTTPException(404, "Job not found") from exc
    return {"job": _job_public(job)}


@router.post("/api/projects/{project_id}/jobs/{job_id}/retry", status_code=202)
async def retry_project_job(project_id: str, job_id: str):
    try:
        job = job_manager.retry(project_id, job_id)
    except KeyError as exc:
        raise HTTPException(404, "Job not found") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"job": _job_public(job)}


@router.post("/api/projects/{project_id}/job-batches/{batch_id}/cancel")
async def cancel_project_job_batch(project_id: str, batch_id: str):
    jobs = job_store.list_jobs(project_id, batch_id=batch_id, limit=500)
    if not jobs:
        raise HTTPException(404, "Job batch not found")
    job_manager.cancel_batch(project_id, batch_id)
    return {"batch": _batch_public(project_id, batch_id)}

__all__ = [name for name in globals() if not name.startswith("__")]
