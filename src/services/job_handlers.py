"""Helpers for registering generation job handlers without HTTP coupling."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from src.services.job_manager import JobContext, JobManager


Handler = Callable[[JobContext, dict[str, Any]], Awaitable[dict[str, Any]]]


def register_handlers(manager: JobManager, handlers: dict[str, Handler]) -> None:
    for job_type, handler in handlers.items():
        manager.register(job_type, handler)
