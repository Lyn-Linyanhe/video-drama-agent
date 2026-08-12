"""Public task response helpers shared by task and audio routes."""

from ..context import ACTIVE_JOB_STATES, JobPublic


def _job_public(job: dict) -> dict:
    total = max(1, int(job.get("progress_total") or 1))
    current = max(0, int(job.get("progress_current") or 0))
    percent = 100 if job.get("state") == "succeeded" else min(99, round(current / total * 100))
    return JobPublic(
        job_id=job["job_id"], project_id=job["project_id"], batch_id=job.get("batch_id", ""),
        job_type=job["job_type"], target_type=job.get("target_type", ""), target_id=job.get("target_id", ""),
        state=job["state"], stage=job.get("stage", ""),
        progress={"current": current, "total": total, "percent": percent},
        attempt=int(job.get("attempt") or 1), retry_of_job_id=job.get("retry_of_job_id", ""),
        provider=job.get("provider", ""), provider_task_id=job.get("provider_task_id", ""),
        result=job.get("result") or {}, error=job.get("error") or None, quality=job.get("quality") or None,
        cancel_requested=bool(job.get("cancel_requested")), can_cancel=job["state"] in ACTIVE_JOB_STATES,
        can_retry=job["state"] in {"failed", "canceled", "interrupted"},
        created_at=job.get("created_at", ""), started_at=job.get("started_at", ""),
        completed_at=job.get("completed_at", ""), updated_at=job.get("updated_at", ""),
    ).model_dump()
