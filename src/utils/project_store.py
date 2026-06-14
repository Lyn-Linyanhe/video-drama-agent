"""Project asset store — manages project metadata and asset indexing.

Stores project metadata as JSON files for browsing history in the UI.
Each project has a unique ID and stores paths to all generated assets.
"""

from __future__ import annotations

import json
import re
import uuid
import shutil
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from src.config import OUTPUT_DIR, MAX_PROJECT_HISTORY

# Valid project_id: exactly 12 hex characters
_VALID_PROJECT_ID = re.compile(r'^[a-f0-9]{12}$')


def _validate_project_id(project_id: str) -> None:
    """Validate project_id to prevent path traversal. Raises ValueError on invalid input."""
    if not _VALID_PROJECT_ID.match(project_id):
        raise ValueError(f"Invalid project_id: must be 12 hex characters")


class ProjectStore:
    """Manages project metadata and asset indexing.

    Each project is stored as:
        output/
          projects.json          # Master index
          <project_id>/
            meta.json            # Project metadata
            characters/          # Character portrait images
            scenes/              # Scene environment images
            videos/              # Generated video clips
            audio/               # TTS audio files
            final/               # Final assembled video
    """

    def __init__(self):
        self.store_dir = OUTPUT_DIR
        self.store_dir.mkdir(exist_ok=True)
        self.index_path = self.store_dir / "projects.json"
        self._ensure_index()

    # ── Public API ───────────────────────────────────────────

    def create_project(self, title: str = "", user_input: str = "") -> str:
        """Create a new project and return its ID."""
        project_id = uuid.uuid4().hex[:12]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        meta = {
            "id": project_id,
            "title": title or "未命名项目",
            "user_input": user_input[:200],
            "created_at": now,
            "updated_at": now,
            "status": "created",  # created → scripted → assets_ready → completed → failed
            "stages_completed": [],
            "assets": {
                "script": None,
                "characters": [],
                "scenes": [],
                "videos": [],
                "audio": [],
                "final_video": None,
            },
        }

        # Create project directory
        project_dir = self.store_dir / project_id
        project_dir.mkdir(parents=True, exist_ok=True)

        # Subdirectories
        for sub in ["characters", "scenes", "videos", "audio", "final"]:
            (project_dir / sub).mkdir(exist_ok=True)

        self._save_meta(project_id, meta)
        self._add_to_index(project_id, meta)

        return project_id

    def update_status(self, project_id: str, status: str) -> None:
        """Update the project status."""
        _validate_project_id(project_id)
        meta = self._load_meta(project_id)
        if meta:
            meta["status"] = status
            meta["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._save_meta(project_id, meta)
            self._update_index(project_id, meta)

    def add_stage(self, project_id: str, stage_name: str) -> None:
        """Mark a stage as completed."""
        _validate_project_id(project_id)
        meta = self._load_meta(project_id)
        if meta:
            if stage_name not in meta["stages_completed"]:
                meta["stages_completed"].append(stage_name)
            meta["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._save_meta(project_id, meta)
            self._update_index(project_id, meta)

    def set_script(self, project_id: str, script_data: dict) -> None:
        """Save script metadata."""
        _validate_project_id(project_id)
        meta = self._load_meta(project_id)
        if meta:
            meta["assets"]["script"] = script_data
            meta["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._save_meta(project_id, meta)
            self._update_index(project_id, meta)

    def set_final_video(self, project_id: str, video_path: str) -> None:
        """Record the final video path."""
        _validate_project_id(project_id)
        meta = self._load_meta(project_id)
        if meta:
            meta["assets"]["final_video"] = video_path
            meta["status"] = "completed"
            meta["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._save_meta(project_id, meta)
            self._update_index(project_id, meta)

    def get_project(self, project_id: str) -> dict | None:
        """Get full project metadata."""
        _validate_project_id(project_id)
        return self._load_meta(project_id)

    def list_projects(self, limit: int = 50) -> list[dict]:
        """List recent projects (newest first)."""
        index = self._load_index()
        projects = index.get("projects", [])
        projects.sort(key=lambda p: p.get("updated_at", ""), reverse=True)
        return projects[:limit]

    def get_project_dir(self, project_id: str) -> Path:
        """Get the project's output directory."""
        _validate_project_id(project_id)
        return self.store_dir / project_id

    def get_asset_files(self, project_id: str, category: str) -> list[str]:
        _validate_project_id(project_id)
        """List asset files in a category folder.

        Args:
            project_id: Project ID.
            category: One of 'characters', 'scenes', 'videos', 'audio', 'final'.

        Returns:
            List of absolute file paths.
        """
        cat_dir = self.store_dir / project_id / category
        if not cat_dir.exists():
            return []
        return sorted(
            str(p) for p in cat_dir.iterdir() if p.is_file()
        )

    def delete_project(self, project_id: str) -> bool:
        """Delete a project and all its assets."""
        _validate_project_id(project_id)
        project_dir = self.store_dir / project_id
        if project_dir.exists():
            shutil.rmtree(str(project_dir), ignore_errors=True)

        # Remove from index
        index = self._load_index()
        index["projects"] = [
            p for p in index.get("projects", []) if p.get("id") != project_id
        ]
        self._save_index(index)
        return True

    def cleanup_old_projects(self, keep: int = MAX_PROJECT_HISTORY) -> int:
        """Remove oldest projects beyond the keep limit. Returns count removed."""
        projects = self.list_projects(limit=1000)
        if len(projects) <= keep:
            return 0

        removed = 0
        for proj in projects[keep:]:
            self.delete_project(proj.get("id", ""))
            removed += 1
        return removed

    # ── Internal helpers ────────────────────────────────────

    def _ensure_index(self) -> None:
        if not self.index_path.exists():
            self._save_index({"projects": []})

    def _load_index(self) -> dict:
        try:
            if self.index_path.exists():
                return json.loads(self.index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
        return {"projects": []}

    def _save_index(self, data: dict) -> None:
        self.index_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _add_to_index(self, project_id: str, meta: dict) -> None:
        index = self._load_index()
        # Remove existing entry if any
        index["projects"] = [
            p for p in index.get("projects", []) if p.get("id") != project_id
        ]
        index["projects"].append({
            "id": project_id,
            "title": meta.get("title", ""),
            "status": meta.get("status", ""),
            "created_at": meta.get("created_at", ""),
            "updated_at": meta.get("updated_at", ""),
            "user_input": meta.get("user_input", "")[:100],
        })
        self._save_index(index)

    def _update_index(self, project_id: str, meta: dict) -> None:
        self._add_to_index(project_id, meta)

    def _meta_path(self, project_id: str) -> Path:
        return self.store_dir / project_id / "meta.json"

    def _save_meta(self, project_id: str, meta: dict) -> None:
        path = self._meta_path(project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_meta(self, project_id: str) -> dict | None:
        path = self._meta_path(project_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
