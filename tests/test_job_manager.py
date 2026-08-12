import asyncio
from pathlib import Path

import pytest

from src.services.job_manager import JobManager, classify_job_error
from src.services.job_store import JobStore


def test_job_store_recovers_incomplete_tasks_and_is_idempotent(tmp_path: Path):
    path = tmp_path / "jobs.sqlite3"
    store = JobStore(path, recover=False)
    job = store.create_job("a" * 12, "asset.generate", "character", "hero")
    store.transition(job["job_id"], "running", "waiting_provider")

    recovered = JobStore(path, recover=True)
    assert recovered.get_job_by_id(job["job_id"])["state"] == "interrupted"
    assert recovered.interrupt_incomplete() == 0
    assert any(event["event_type"] == "recovery" for event in recovered.list_events("a" * 12, job["job_id"]))


def test_job_store_rejects_invalid_state_transition(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3", recover=False)
    job = store.create_job("a" * 12, "asset.generate")
    store.transition(job["job_id"], "canceled")
    with pytest.raises(ValueError):
        store.transition(job["job_id"], "running")


def test_project_event_cursor_is_ordered_and_isolated(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3", recover=False)
    first = store.create_job("a" * 12, "asset.generate", "scene", "one")
    second = store.create_job("a" * 12, "asset.generate", "scene", "two")
    store.create_job("b" * 12, "asset.generate", "scene", "hidden")
    store.add_event(first["job_id"], "progress", "running", "waiting_provider", "first")
    store.add_event(second["job_id"], "progress", "running", "waiting_provider", "second")

    events = store.list_project_events("a" * 12)
    assert [item["event"]["event_id"] for item in events] == sorted(
        item["event"]["event_id"] for item in events
    )
    assert {item["job"]["job_id"] for item in events} == {first["job_id"], second["job_id"]}

    cursor = events[1]["event"]["event_id"]
    later = store.list_project_events("a" * 12, after_event_id=cursor)
    assert all(item["event"]["event_id"] > cursor for item in later)
    assert store.list_project_events("b" * 12, after_event_id=0)[0]["job"]["target_id"] == "hidden"


@pytest.mark.asyncio
async def test_manager_runs_reports_progress_and_retries(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3", recover=False)
    manager = JobManager(store)

    async def handler(context, payload):
        context.progress("waiting_provider", 1, 3, "已提交")
        await asyncio.sleep(0)
        context.progress("waiting_provider", 2, 3, "生成中")
        return {"value": payload["value"]}

    manager.register("asset.generate", handler)
    job = manager.create_job("a" * 12, "asset.generate", payload={"value": 7})
    finished = await manager.wait("a" * 12, job["job_id"], timeout=2)

    assert finished["state"] == "succeeded"
    assert finished["result"] == {"value": 7}
    events = store.list_events("a" * 12, job["job_id"])
    progress = [event["data"]["current"] for event in events if event["event_type"] == "progress"]
    assert progress == [1, 2]

    retried = manager.retry("a" * 12, job["job_id"])
    retry_finished = await manager.wait("a" * 12, retried["job_id"], timeout=2)
    assert retry_finished["state"] == "succeeded"
    assert retry_finished["retry_of_job_id"] == job["job_id"]
    assert retry_finished["attempt"] == 2


@pytest.mark.asyncio
async def test_manager_cancels_single_and_batch_jobs(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3", recover=False)
    manager = JobManager(store)
    started = asyncio.Event()

    async def handler(context, payload):
        started.set()
        while True:
            context.check_cancelled()
            await asyncio.sleep(0.01)

    manager.register("asset.generate", handler)
    batch_id, jobs = manager.create_batch(
        "a" * 12,
        [
            {"job_type": "asset.generate", "target_id": "one"},
            {"job_type": "asset.generate", "target_id": "two"},
        ],
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    manager.cancel_batch("a" * 12, batch_id)
    finished = [await manager.wait("a" * 12, job["job_id"], timeout=2) for job in jobs]
    assert {job["state"] for job in finished} == {"canceled"}


@pytest.mark.asyncio
async def test_project_isolation_blocks_cross_project_cancel(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3", recover=False)
    manager = JobManager(store)

    async def handler(context, payload):
        await asyncio.sleep(0)
        return {}

    manager.register("asset.generate", handler)
    job = manager.create_job("a" * 12, "asset.generate")
    with pytest.raises(KeyError):
        manager.cancel("b" * 12, job["job_id"])
    await manager.wait("a" * 12, job["job_id"], timeout=2)


def test_error_classification_is_actionable_and_redacts_credentials():
    class RateLimitError(RuntimeError):
        status_code = 429

    limited = classify_job_error(RateLimitError("rate limit exceeded"))
    assert limited["category"] == "rate_limit"
    assert limited["retriable"] is True

    redacted = classify_job_error(RuntimeError("Authorization: BearerValue API_KEY=sk-secretvalue123"))
    assert "BearerValue" not in redacted["message"]
    assert "sk-secretvalue123" not in redacted["message"]


def test_seedance_missing_ark_key_is_classified_as_authentication_error():
    missing_key = classify_job_error(RuntimeError("Seedance Ark API Key is not configured"))

    assert missing_key["category"] == "authentication"
    assert missing_key["code"] == "authentication_failed"
    assert missing_key["retriable"] is False


def test_seedance_ark_http_error_is_classified_as_provider_error():
    provider_error = classify_job_error(RuntimeError("Ark Seedance submit failed [500]: gateway error"))

    assert provider_error["category"] == "provider"
    assert provider_error["code"] == "provider_error"
    assert provider_error["retriable"] is True


def test_llm_timeout_error_keeps_phase_provider_and_model_context():
    from src.utils.llm import LLMRequestError

    error = LLMRequestError(
        "LLM request timed out after 300s",
        provider="deepseek",
        model="deepseek-v4-flash",
        phase="script.analyze",
        timeout_sec=300,
        attempts=2,
        timed_out=True,
    )

    classified = classify_job_error(error)

    assert classified["category"] == "timeout"
    assert classified["provider"] == "deepseek"
    assert classified["model"] == "deepseek-v4-flash"
    assert classified["phase"] == "script.analyze"
    assert classified["timeout_sec"] == 300
    assert classified["attempts"] == 2


@pytest.mark.asyncio
async def test_manager_adds_job_provider_context_to_provider_timeout(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.sqlite3", recover=False)
    manager = JobManager(store)

    async def handler(_context, _payload):
        raise TimeoutError("provider task timed out")

    manager.register("video.generate", handler)
    job = manager.create_job(
        "a" * 12,
        "video.generate",
        provider="seedance",
        payload={"model": "doubao-seedance-2-0-fast-260128"},
    )

    finished = await manager.wait("a" * 12, job["job_id"], timeout=2)

    assert finished["error"]["category"] == "timeout"
    assert finished["error"]["provider"] == "seedance"
    assert finished["error"]["model"] == "doubao-seedance-2-0-fast-260128"
