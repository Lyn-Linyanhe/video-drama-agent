"""Regression coverage for the user-confirmed script draft workflow."""

import asyncio

import pytest
from fastapi import HTTPException

from src.api import context as api_context
from src.api.routes import jobs, scripts
from src.graph.state import DramaState


class _Context:
    def __init__(self, project_id: str):
        self.project_id = project_id
        self.job_id = "draft-job-1"
        self.project_lock = asyncio.Lock()

    def progress(self, *_args, **_kwargs):
        return None

    def check_cancelled(self):
        return None


@pytest.mark.asyncio
async def test_idea_draft_job_persists_text_without_structured_script_or_visual_job(monkeypatch):
    saved_states = []
    created = []
    state = DramaState(project_id="a1b2c3d4e5f6")
    monkeypatch.setattr(
        jobs.script_agent,
        "generate_draft",
        lambda *_args, **_kwargs: asyncio.sleep(0, result="第一场：雨夜，主角收到匿名来信。"),
    )
    monkeypatch.setattr(jobs.project_store, "set_state_json", lambda *_args: saved_states.append(_args[1]))
    monkeypatch.setattr(jobs.project_store, "update_status", lambda *_args: None)
    monkeypatch.setattr(jobs.project_store, "set_script", lambda *_args: (_ for _ in ()).throw(AssertionError("draft must not save script")))
    monkeypatch.setattr(jobs.job_manager, "create_job", lambda *args, **kwargs: created.append(args) or {"job_id": "unexpected"})
    monkeypatch.setattr(jobs, "_load_project_state", lambda *_args, **_kwargs: state)

    result = await jobs._job_script_draft_generate(
        _Context(state.project_id),
        {"idea": "雨夜匿名信", "input_mode": "idea"},
    )

    state = api_context._state_from_json(saved_states[-1])
    assert result["draft_text"].startswith("第一场")
    assert state.script is None
    assert state.script_phase == "draft_ready"
    assert state.script_draft_text == result["draft_text"]
    assert created == []


@pytest.mark.asyncio
async def test_draft_revise_keeps_existing_draft_when_generation_fails(monkeypatch):
    state = DramaState(project_id="draft-project", script_phase="draft_ready", script_draft_text="原草稿")
    monkeypatch.setattr(jobs, "_load_project_state", lambda *_args, **_kwargs: state)
    monkeypatch.setattr(jobs.script_agent, "revise_draft", lambda *_args, **_kwargs: asyncio.sleep(0, result="改写草稿"))
    monkeypatch.setattr(jobs, "_save_project_state", lambda *_args: None)

    result = await jobs._job_script_draft_revise(_Context(state.project_id), {"instruction": "加强冲突"})

    assert result["draft_text"] == "改写草稿"
    assert state.script_draft_text == "改写草稿"
    assert state.script is None


def test_confirm_requires_nonempty_saved_draft(monkeypatch):
    state = DramaState(project_id="draft-project", script_phase="source_required")
    monkeypatch.setattr(scripts, "_load_project_state", lambda *_args, **_kwargs: state)

    with pytest.raises(Exception) as exc_info:
        asyncio.run(scripts.confirm_script_draft(state.project_id))

    assert "草稿" in str(exc_info.value)


@pytest.mark.asyncio
async def test_unconfirmed_draft_rejects_production_job_submission(monkeypatch):
    state = DramaState(project_id="a1b2c3d4e5f6", script_phase="draft_ready", script_draft_text="未确认草稿")
    monkeypatch.setattr(jobs, "_project_or_404", lambda *_args: {})
    monkeypatch.setattr(jobs, "_ensure_reference_job_handlers", lambda: None)
    monkeypatch.setattr(jobs, "_load_project_state", lambda *_args, **_kwargs: state)

    from src.schemas.jobs import JobCreateRequest

    with pytest.raises(HTTPException) as exc_info:
        await jobs.create_project_job(
            state.project_id,
            JobCreateRequest(job_type="audio.generate", target_type="project"),
        )

    assert exc_info.value.status_code == 409
    assert "确认并解析" in exc_info.value.detail
