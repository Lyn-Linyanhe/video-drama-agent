"""Persistent generation job schemas."""

from typing import Any, Literal

from pydantic import BaseModel, Field


JobState = Literal[
    "queued", "running", "validating", "succeeded",
    "failed", "canceled", "interrupted",
]

TERMINAL_JOB_STATES = {"succeeded", "failed", "canceled", "interrupted"}
ACTIVE_JOB_STATES = {"queued", "running", "validating"}


class JobError(BaseModel):
    code: str = "operation_failed"
    category: str = "internal"
    message: str = "任务执行失败"
    suggestion: str = "请查看详情后重试"
    retriable: bool = True
    http_status: int = 0
    provider_status: str = ""
    provider: str = ""
    model: str = ""
    phase: str = ""
    timeout_sec: int = 0
    attempts: int = 0


class QualityReport(BaseModel):
    status: Literal["passed", "warning", "failed", "skipped", "unreviewed"] = "skipped"
    technical_checks: list[dict[str, Any]] = Field(default_factory=list)
    visual_review: dict[str, Any] = Field(
        default_factory=lambda: {"status": "skipped", "reason": "未配置视觉审核"}
    )
    warnings: list[str] = Field(default_factory=list)


class JobCreateRequest(BaseModel):
    job_type: Literal[
        "script.generate", "script.draft.generate", "script.draft.revise", "script.analyze", "script.visualize", "prompt.review", "asset.generate",
        "character.reference.group", "character.reference.edit",
        "scene.reference.group", "scene.reference.edit", "prop.reference.group",
        "video.generate", "audio.generate", "final.assemble",
    ]
    target_type: str = ""
    target_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)


class JobProgress(BaseModel):
    current: int = 0
    total: int = 1
    percent: int = 0


class JobPublic(BaseModel):
    job_id: str
    project_id: str
    batch_id: str = ""
    job_type: str
    target_type: str = ""
    target_id: str = ""
    state: JobState
    stage: str = ""
    progress: JobProgress = Field(default_factory=JobProgress)
    attempt: int = 1
    retry_of_job_id: str = ""
    provider: str = ""
    provider_task_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] = Field(default_factory=dict)
    error: JobError | None = None
    quality: QualityReport | None = None
    cancel_requested: bool = False
    can_cancel: bool = False
    can_retry: bool = False
    created_at: str = ""
    started_at: str = ""
    completed_at: str = ""
    updated_at: str = ""


class JobBatchPublic(BaseModel):
    batch_id: str
    project_id: str
    jobs: list[JobPublic] = Field(default_factory=list)
    total: int = 0
    completed: int = 0
    succeeded: int = 0
    failed: int = 0
    canceled: int = 0
    active: int = 0
