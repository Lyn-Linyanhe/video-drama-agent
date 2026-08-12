"""Context-local progress and cancellation hooks for provider clients."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable, Iterator


@dataclass
class GenerationHooks:
    progress_callback: Callable[[str, int, int, str], None] | None = None
    provider_task_callback: Callable[[str, str], None] | None = None
    cancel_callback: Callable[[], None] | None = None

    def progress(self, stage: str, current: int, total: int, message: str = "") -> None:
        if self.progress_callback:
            self.progress_callback(stage, current, total, message)

    def provider_task(self, provider: str, task_id: str) -> None:
        if self.provider_task_callback:
            self.provider_task_callback(provider, task_id)

    def check_cancelled(self) -> None:
        if self.cancel_callback:
            self.cancel_callback()


_HOOKS: ContextVar[GenerationHooks | None] = ContextVar("generation_hooks", default=None)


def get_generation_hooks() -> GenerationHooks:
    return _HOOKS.get() or GenerationHooks()


@contextmanager
def bind_generation_hooks(hooks: GenerationHooks) -> Iterator[None]:
    token = _HOOKS.set(hooks)
    try:
        yield
    finally:
        _HOOKS.reset(token)
