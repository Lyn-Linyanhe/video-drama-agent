"""Async project job scheduling, cancellation, retries and error normalization."""

from __future__ import annotations

import asyncio
import inspect
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.schemas.jobs import ACTIVE_JOB_STATES, JobError, TERMINAL_JOB_STATES
from src.services.job_store import JobStore


JobHandler = Callable[["JobContext", dict[str, Any]], Awaitable[dict[str, Any]]]


class JobCanceled(Exception):
    pass


def _safe_message(value: object) -> str:
    text = str(value or "任务执行失败")[:1000]
    text = re.sub(r"(?i)(authorization|api[-_ ]?key|token|secret)\s*[:=]\s*[^\s,;]+", r"\1=[已隐藏]", text)
    text = re.sub(r"api-key-[A-Za-z0-9_-]+", "api-key-[已隐藏]", text)
    text = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "sk-[已隐藏]", text)
    return text


def classify_job_error(exc: BaseException) -> dict[str, Any]:
    message = _safe_message(getattr(exc, "detail", None) or exc)
    lowered = message.lower()
    status = int(getattr(exc, "status_code", 0) or 0)
    if not status:
        embedded_status = re.search(r"\[(\d{3})\]", str(message))
        if embedded_status:
            status = int(embedded_status.group(1))
    category = "internal"
    code = "operation_failed"
    suggestion = "请查看任务详情后重试"
    retriable = True
    if any(token in lowered for token in ("ark api key", "volcengine api key", "ark authorization")):
        category, code, suggestion, retriable = "authentication", "authentication_failed", "Check the Seedance Ark API key and provider settings", False
    if status in (401, 403) or any(token in lowered for token in ("unauthorized", "invalid api key", "认证", "api key 无效")):
        category, code, suggestion, retriable = "authentication", "authentication_failed", "请检查 API 设置中的密钥和服务商", False
    elif status == 429 or "rate limit" in lowered or "限流" in lowered:
        category, code, suggestion = "rate_limit", "rate_limited", "请稍后重试或降低并发数量"
    elif any(token in lowered for token in ("quota", "insufficient", "余额", "额度")):
        category, code, suggestion, retriable = "quota", "quota_exhausted", "请检查供应商余额或调用额度", False
    elif isinstance(exc, (asyncio.TimeoutError, TimeoutError)) or "timed out" in lowered or "timeout" in lowered:
        category, code, suggestion = "timeout", "generation_timeout", "供应商响应超时，可以稍后重试"
    elif status in (400, 404, 409, 422) or isinstance(exc, (ValueError, FileNotFoundError)):
        category, code, suggestion, retriable = "validation", "input_invalid", "请修正任务输入后重试", False
    elif any(token in lowered for token in ("connection", "network", "dns", "connecterror", "网络")):
        category, code, suggestion = "network", "network_error", "请检查网络、代理和 API 地址"
    elif status >= 500 or any(token in lowered for token in ("provider", "kling", "happy horse", "wavespeed", "dashscope")):
        category, code, suggestion = "provider", "provider_error", "供应商返回异常，请稍后重试"
    if getattr(exc, "timed_out", False):
        suggestion = "LLM 请求超时：请检查网络/代理，或提高对应阶段的超时设置后重试"
    error = JobError(
        code=code,
        category=category,
        message=message,
        suggestion=suggestion,
        retriable=retriable,
        http_status=status,
    ).model_dump()
    # Preserve provider context for transport failures without exposing keys.
    for field in ("provider", "model", "phase", "timeout_sec", "attempts"):
        value = getattr(exc, field, None)
        if value not in (None, ""):
            error[field] = int(value) if field in {"timeout_sec", "attempts"} else str(value)
    return error


@dataclass
class JobContext:
    manager: "JobManager"
    job_id: str
    project_id: str
    cancel_event: asyncio.Event

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise JobCanceled("任务已由用户终止")

    def progress(
        self,
        stage: str,
        current: int,
        total: int,
        message: str = "",
    ) -> None:
        self.check_cancelled()
        total = max(1, total)
        current = max(0, min(current, total))
        previous = self.manager.store.get_job_by_id(self.job_id) or {}
        if previous.get("stage") == stage:
            current = max(current, int(previous.get("progress_current", 0)))
        self.manager.store.update(
            self.job_id,
            stage=stage,
            progress_current=current,
            progress_total=total,
        )
        if message:
            self.manager.store.add_event(
                self.job_id, "progress", previous.get("state", "running"), stage, message,
                {"current": current, "total": total},
            )

    def provider_task(self, provider: str, task_id: str) -> None:
        self.check_cancelled()
        self.manager.store.update(
            self.job_id, provider=provider, provider_task_id=task_id,
        )

    def quality(self, report: dict[str, Any]) -> None:
        self.manager.store.update(self.job_id, quality_json=report)

    @property
    def project_lock(self) -> asyncio.Lock:
        return self.manager.project_locks[self.project_id]


class JobManager:
    def __init__(self, store: JobStore):
        self.store = store
        self.handlers: dict[str, JobHandler] = {}
        self.running: dict[str, asyncio.Task] = {}
        self.cancel_events: dict[str, asyncio.Event] = {}
        self.project_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    def register(self, job_type: str, handler: JobHandler) -> None:
        self.handlers[job_type] = handler

    def create_job(
        self,
        project_id: str,
        job_type: str,
        target_type: str = "",
        target_id: str = "",
        payload: dict[str, Any] | None = None,
        batch_id: str = "",
        attempt: int = 1,
        retry_of_job_id: str = "",
        provider: str = "",
    ) -> dict[str, Any]:
        if job_type not in self.handlers:
            raise ValueError(f"Unsupported job type: {job_type}")
        job = self.store.create_job(
            project_id=project_id,
            job_type=job_type,
            target_type=target_type,
            target_id=target_id,
            request=payload,
            batch_id=batch_id,
            attempt=attempt,
            retry_of_job_id=retry_of_job_id,
            provider=provider,
        )
        self.schedule(job["job_id"])
        return self.store.get_job_by_id(job["job_id"]) or job

    def create_batch(
        self,
        project_id: str,
        specs: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]]]:
        batch_id = uuid.uuid4().hex[:12]
        jobs = [
            self.create_job(project_id=project_id, batch_id=batch_id, **spec)
            for spec in specs
        ]
        return batch_id, jobs

    def schedule(self, job_id: str) -> None:
        if job_id in self.running:
            return
        event = asyncio.Event()
        self.cancel_events[job_id] = event
        task = asyncio.create_task(self._run(job_id, event))
        self.running[job_id] = task

    async def _run(self, job_id: str, cancel_event: asyncio.Event) -> None:
        job = self.store.get_job_by_id(job_id)
        if not job:
            return
        context = JobContext(self, job_id, job["project_id"], cancel_event)
        try:
            if job["cancel_requested"]:
                raise JobCanceled("任务在开始前已终止")
            self.store.transition(job_id, "running", "preparing", "任务开始执行")
            handler = self.handlers[job["job_type"]]
            result = await handler(context, job["request"])
            context.check_cancelled()
            current = self.store.get_job_by_id(job_id) or {}
            if current.get("state") == "running":
                self.store.transition(job_id, "validating", "saving", "正在保存任务结果")
            context.check_cancelled()
            self.store.transition(
                job_id,
                "succeeded",
                "completed",
                "任务已完成",
                result_json=result or {},
                progress_current=1,
                progress_total=1,
                error_json={},
            )
        except (JobCanceled, asyncio.CancelledError):
            current = self.store.get_job_by_id(job_id)
            if current and current["state"] not in TERMINAL_JOB_STATES:
                self.store.transition(
                    job_id,
                    "canceled",
                    "canceled",
                    "任务已由用户终止",
                    error_json=JobError(
                        code="canceled",
                        category="canceled",
                        message="任务已由用户终止",
                        suggestion="可以重新提交该任务",
                    ).model_dump(),
                )
        except Exception as exc:
            current = self.store.get_job_by_id(job_id)
            if current and current["state"] not in TERMINAL_JOB_STATES:
                error = classify_job_error(exc)
                request = current.get("request") or {}
                if not error.get("provider") and current.get("provider"):
                    error["provider"] = str(current["provider"])
                model = request.get("model") or request.get("video_model")
                if not model and isinstance(request.get("video_policy"), dict):
                    model = request["video_policy"].get("model")
                if model and not error.get("model"):
                    error["model"] = str(model)
                if request.get("phase") and not error.get("phase"):
                    error["phase"] = str(request["phase"])
                self.store.transition(
                    job_id, "failed", "failed", error["message"], error_json=error,
                )
        finally:
            self.running.pop(job_id, None)
            self.cancel_events.pop(job_id, None)

    def cancel(self, project_id: str, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(project_id, job_id)
        if not job:
            raise KeyError(job_id)
        if job["state"] in TERMINAL_JOB_STATES:
            return job
        self.store.update(job_id, cancel_requested=1)
        event = self.cancel_events.get(job_id)
        if event:
            event.set()
        task = self.running.get(job_id)
        if task:
            task.cancel()
        elif job["state"] == "queued":
            self.store.transition(job_id, "canceled", "canceled", "排队任务已终止")
        return self.store.get_job(project_id, job_id) or job

    def cancel_batch(self, project_id: str, batch_id: str) -> list[dict[str, Any]]:
        jobs = self.store.list_jobs(project_id, batch_id=batch_id, limit=500)
        return [self.cancel(project_id, job["job_id"]) for job in jobs if job["state"] in ACTIVE_JOB_STATES]

    def retry(self, project_id: str, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(project_id, job_id)
        if not job:
            raise KeyError(job_id)
        if job["state"] not in TERMINAL_JOB_STATES:
            raise ValueError("只有终止、失败或中断的任务可以重试")
        return self.create_job(
            project_id=project_id,
            job_type=job["job_type"],
            target_type=job["target_type"],
            target_id=job["target_id"],
            payload=job["request"],
            attempt=int(job["attempt"]) + 1,
            retry_of_job_id=job_id,
            provider=job["provider"],
        )

    async def wait(self, project_id: str, job_id: str, timeout: float | None = None) -> dict[str, Any]:
        async def _wait() -> dict[str, Any]:
            while True:
                job = self.store.get_job(project_id, job_id)
                if not job:
                    raise KeyError(job_id)
                if job["state"] in TERMINAL_JOB_STATES:
                    return job
                await asyncio.sleep(0.1)

        return await asyncio.wait_for(_wait(), timeout=timeout) if timeout else await _wait()
