"""FastAPI routes extracted from the original server entrypoint."""

from fastapi import APIRouter
from ..context import *
from src.services.prompt_fidelity_gate import prompt_fidelity_gate_detail

router = APIRouter()

@router.post("/api/videos")
async def generate_videos(req: StageRequest):
    """第 4 步：生成视频片段。"""
    state = _state_from_json(req.state_json)
    fidelity_report = audit_current_prompt_fidelity(state)
    state.prompt_fidelity = fidelity_report.model_dump()
    if fidelity_report.blocked:
        detail = prompt_fidelity_gate_detail(fidelity_report) or {}
        return StageResponse(
            state_json=_state_to_json(state),
            log=[],
            error=json.dumps(detail, ensure_ascii=False),
        )
    try:
        result = await orchestrator.run_stage(4, state)
    except Exception as e:
        return StageResponse(state_json=req.state_json, log=[], error="操作失败，请重试")

    project_store.add_stage(result.project_id, "stage4")
    project_store.set_state_json(result.project_id, _state_to_json(result))
    return StageResponse(
        state_json=_state_to_json(result),
        log=result.execution_log,
    )


@router.post("/api/audio")
async def generate_audio(req: StageRequest):
    """第 5 步：生成配音和 BGM。"""
    state = _state_from_json(req.state_json)
    try:
        result = await orchestrator.run_stage(5, state)
    except Exception as e:
        return StageResponse(state_json=req.state_json, log=[], error="操作失败，请重试")

    project_store.add_stage(result.project_id, "stage5")
    project_store.set_state_json(result.project_id, _state_to_json(result))
    return StageResponse(
        state_json=_state_to_json(result),
        log=result.execution_log,
    )


@router.post("/api/assemble")
async def assemble_video(req: StageRequest):
    """第 6 步：合成最终视频。"""
    state = _state_from_json(req.state_json)
    try:
        result = await orchestrator.run_stage(6, state)
    except Exception as e:
        return StageResponse(state_json=req.state_json, log=[], error="操作失败，请重试")

    project_store.set_final_video(result.project_id, result.output_video_path)
    project_store.update_status(result.project_id, "已完成")
    project_store.set_state_json(result.project_id, _state_to_json(result))

    return StageResponse(
        state_json=_state_to_json(result),
        log=result.execution_log,
    )


class CreateProjectRequest(BaseModel):
    title: str = Field(default="未命名项目")
    genre_tags: str = Field(default="")
    art_style: str = Field(default="写实")

__all__ = [name for name in globals() if not name.startswith("__")]
