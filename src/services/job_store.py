"""SQLite persistence for project generation jobs."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.config import OUTPUT_DIR
from src.schemas.jobs import ACTIVE_JOB_STATES, TERMINAL_JOB_STATES


ALLOWED_TRANSITIONS = {
    "queued": {"running", "canceled", "interrupted"},
    "running": {"validating", "succeeded", "failed", "canceled", "interrupted"},
    "validating": {"succeeded", "failed", "canceled", "interrupted"},
    "succeeded": set(),
    "failed": set(),
    "canceled": set(),
    "interrupted": set(),
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobStore:
    def __init__(self, path: Path | str | None = None, recover: bool = True):
        self.path = Path(path or (OUTPUT_DIR / "jobs.sqlite3"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._migrate()
        if recover:
            self.interrupt_incomplete()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _migrate(self) -> None:
        with self._lock, self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    batch_id TEXT NOT NULL DEFAULT '',
                    job_type TEXT NOT NULL,
                    target_type TEXT NOT NULL DEFAULT '',
                    target_id TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL,
                    stage TEXT NOT NULL DEFAULT '',
                    progress_current INTEGER NOT NULL DEFAULT 0,
                    progress_total INTEGER NOT NULL DEFAULT 1,
                    attempt INTEGER NOT NULL DEFAULT 1,
                    retry_of_job_id TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '',
                    provider_task_id TEXT NOT NULL DEFAULT '',
                    request_json TEXT NOT NULL DEFAULT '{}',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error_json TEXT NOT NULL DEFAULT '{}',
                    quality_json TEXT NOT NULL DEFAULT '{}',
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    started_at TEXT NOT NULL DEFAULT '',
                    completed_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_project_updated
                    ON jobs(project_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_jobs_project_job
                    ON jobs(project_id, job_id);
                CREATE INDEX IF NOT EXISTS idx_jobs_batch
                    ON jobs(project_id, batch_id);
                CREATE INDEX IF NOT EXISTS idx_jobs_state
                    ON jobs(state);
                CREATE TABLE IF NOT EXISTS job_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    state TEXT NOT NULL DEFAULT '',
                    stage TEXT NOT NULL DEFAULT '',
                    message TEXT NOT NULL DEFAULT '',
                    data_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_job_events_job
                    ON job_events(job_id, event_id);
                PRAGMA user_version=1;
                """
            )

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:16]

    def create_job(
        self,
        project_id: str,
        job_type: str,
        target_type: str = "",
        target_id: str = "",
        request: dict[str, Any] | None = None,
        batch_id: str = "",
        attempt: int = 1,
        retry_of_job_id: str = "",
        provider: str = "",
        job_id: str = "",
    ) -> dict[str, Any]:
        job_id = job_id or self.new_id()
        now = _now()
        values = (
            job_id, project_id, batch_id, job_type, target_type, target_id,
            "queued", "queued", 0, 1, attempt, retry_of_job_id, provider,
            json.dumps(request or {}, ensure_ascii=False), now, now,
        )
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO jobs (
                    job_id, project_id, batch_id, job_type, target_type, target_id,
                    state, stage, progress_current, progress_total, attempt,
                    retry_of_job_id, provider, request_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                values,
            )
            conn.execute(
                """INSERT INTO job_events
                    (job_id, event_type, state, stage, message, created_at)
                    VALUES (?, 'created', 'queued', 'queued', '任务已加入队列', ?)""",
                (job_id, now),
            )
        return self.get_job_by_id(job_id) or {}

    def get_job(self, project_id: str, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE project_id=? AND job_id=?",
                (project_id, job_id),
            ).fetchone()
        return self._decode(row) if row else None

    def get_job_by_id(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return self._decode(row) if row else None

    def list_jobs(
        self,
        project_id: str,
        states: Iterable[str] | None = None,
        batch_id: str = "",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = ["project_id=?"]
        params: list[Any] = [project_id]
        if states:
            selected = list(states)
            clauses.append(f"state IN ({','.join('?' for _ in selected)})")
            params.extend(selected)
        if batch_id:
            clauses.append("batch_id=?")
            params.append(batch_id)
        params.append(max(1, min(limit, 500)))
        sql = f"SELECT * FROM jobs WHERE {' AND '.join(clauses)} ORDER BY created_at DESC LIMIT ?"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._decode(row) for row in rows]

    def update(self, job_id: str, **fields: Any) -> dict[str, Any]:
        allowed = {
            "state", "stage", "progress_current", "progress_total", "attempt",
            "provider", "provider_task_id", "result_json", "error_json",
            "quality_json", "cancel_requested", "started_at", "completed_at",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Unsupported job fields: {sorted(unknown)}")
        encoded = dict(fields)
        for key in ("result_json", "error_json", "quality_json"):
            if key in encoded and not isinstance(encoded[key], str):
                encoded[key] = json.dumps(encoded[key] or {}, ensure_ascii=False)
        encoded["updated_at"] = _now()
        assignments = ", ".join(f"{key}=?" for key in encoded)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE jobs SET {assignments} WHERE job_id=?",
                [*encoded.values(), job_id],
            )
        return self.get_job_by_id(job_id) or {}

    def transition(
        self,
        job_id: str,
        new_state: str,
        stage: str = "",
        message: str = "",
        **fields: Any,
    ) -> dict[str, Any]:
        current = self.get_job_by_id(job_id)
        if not current:
            raise KeyError(job_id)
        if new_state != current["state"] and new_state not in ALLOWED_TRANSITIONS[current["state"]]:
            raise ValueError(f"Invalid job transition: {current['state']} -> {new_state}")
        now = _now()
        if new_state == "running" and not current["started_at"]:
            fields["started_at"] = now
        if new_state in TERMINAL_JOB_STATES:
            fields["completed_at"] = now
        fields.update(state=new_state, stage=stage or new_state)
        updated = self.update(job_id, **fields)
        self.add_event(job_id, "state", new_state, stage or new_state, message)
        return updated

    def add_event(
        self,
        job_id: str,
        event_type: str,
        state: str = "",
        stage: str = "",
        message: str = "",
        data: dict[str, Any] | None = None,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO job_events
                    (job_id, event_type, state, stage, message, data_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    job_id, event_type, state, stage, message,
                    json.dumps(data or {}, ensure_ascii=False), _now(),
                ),
            )

    def list_events(self, project_id: str, job_id: str) -> list[dict[str, Any]]:
        if not self.get_job(project_id, job_id):
            return []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM job_events WHERE job_id=? ORDER BY event_id",
                (job_id,),
            ).fetchall()
        events = []
        for row in rows:
            item = dict(row)
            item["data"] = self._json(item.pop("data_json"))
            events.append(item)
        return events

    def list_project_events(
        self, project_id: str, after_event_id: int = 0, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Return ordered task events for one project with their latest job state."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT e.* FROM job_events e
                   INNER JOIN jobs j ON j.job_id = e.job_id
                   WHERE j.project_id=? AND e.event_id>?
                   ORDER BY e.event_id ASC LIMIT ?""",
                (project_id, max(0, int(after_event_id or 0)), max(1, min(limit, 500))),
            ).fetchall()
            job_ids = list(dict.fromkeys(row["job_id"] for row in rows))
            if not job_ids:
                return []
            placeholders = ",".join("?" for _ in job_ids)
            job_rows = conn.execute(
                f"SELECT * FROM jobs WHERE project_id=? AND job_id IN ({placeholders})",
                (project_id, *job_ids),
            ).fetchall()

        jobs = {row["job_id"]: self._decode(row) for row in job_rows}
        result = []
        for row in rows:
            event = dict(row)
            event["data"] = self._json(event.pop("data_json"))
            job = jobs.get(event["job_id"])
            if job:
                result.append({"event": event, "job": job})
        return result

    def latest_project_event_id(self, project_id: str) -> int:
        """Return the newest event cursor visible to one project."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT MAX(e.event_id) AS event_id FROM job_events e
                   INNER JOIN jobs j ON j.job_id = e.job_id
                   WHERE j.project_id=?""",
                (project_id,),
            ).fetchone()
        return int(row["event_id"] or 0) if row else 0

    def interrupt_incomplete(self) -> int:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT job_id FROM jobs WHERE state IN ('queued','running','validating')"
            ).fetchall()
            now = _now()
            for row in rows:
                conn.execute(
                    """UPDATE jobs SET state='interrupted', stage='interrupted',
                        completed_at=?, updated_at=? WHERE job_id=?""",
                    (now, now, row["job_id"]),
                )
                conn.execute(
                    """INSERT INTO job_events
                        (job_id, event_type, state, stage, message, created_at)
                        VALUES (?, 'recovery', 'interrupted', 'interrupted',
                        '服务重启，任务已中断，可手动重试', ?)""",
                    (row["job_id"], now),
                )
        return len(rows)

    @staticmethod
    def _json(value: str) -> dict[str, Any]:
        try:
            parsed = json.loads(value or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _decode(self, row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["request"] = self._json(item.pop("request_json"))
        item["result"] = self._json(item.pop("result_json"))
        item["error"] = self._json(item.pop("error_json"))
        item["quality"] = self._json(item.pop("quality_json"))
        item["cancel_requested"] = bool(item["cancel_requested"])
        return item
