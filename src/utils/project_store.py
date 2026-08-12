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
            props/               # Key prop six-view references
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
                "state_json": None,
                "characters": [],
                "scenes": [],
                "props": [],
                "videos": [],
                "audio": [],
                "final_video": None,
            },
        }

        # Create project directory
        project_dir = self.store_dir / project_id
        project_dir.mkdir(parents=True, exist_ok=True)

        # Subdirectories
        for sub in ["characters", "scenes", "props", "videos", "audio", "final"]:
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

    def set_state_json(self, project_id: str, state_json: str) -> None:
        """Save the full interactive pipeline state JSON."""
        _validate_project_id(project_id)
        meta = self._load_meta(project_id)
        if meta:
            meta.setdefault("assets", {})
            meta["assets"]["state_json"] = state_json
            # ``meta.assets.script`` and the other snapshots are consumed by
            # the project-detail endpoint. Keep them in lockstep with the
            # canonical interactive state so a normalization/migration cannot
            # be visible in generation APIs but stale in the UI.
            try:
                state_data = json.loads(state_json)
            except (TypeError, ValueError):
                state_data = {}
            for field in (
                "script",
                "visual_design",
                "generation_prompts",
                "character_images",
                "scene_images",
                "prop_images",
                "generated_assets",
                "audio_assets",
            ):
                if field in state_data:
                    meta["assets"][field] = state_data[field]
            meta["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._save_meta(project_id, meta)
            self._update_index(project_id, meta)

    def set_visual_design(self, project_id: str, visual_data: dict) -> None:
        """Save visual design metadata."""
        _validate_project_id(project_id)
        meta = self._load_meta(project_id)
        if meta:
            meta["assets"]["visual_design"] = visual_data
            meta["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._save_meta(project_id, meta)
            self._update_index(project_id, meta)

    def rename_project(self, project_id: str, new_title: str) -> bool:
        """Rename a project."""
        _validate_project_id(project_id)
        meta = self._load_meta(project_id)
        if meta:
            meta["title"] = new_title
            meta["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self._save_meta(project_id, meta)
            self._update_index(project_id, meta)
            return True
        return False

    def copy_project(self, project_id: str) -> str | None:
        """Copy a project with all its metadata. Returns new project ID."""
        _validate_project_id(project_id)
        meta = self._load_meta(project_id)
        if not meta:
            return None
        new_id = uuid.uuid4().hex[:12]
        new_meta = {**meta, "id": new_id, "title": f"{meta.get('title','')} (副本)",
                     "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                     "status": "created", "stages_completed": []}
        # 复制目录
        old_dir = self.store_dir / project_id
        new_dir = self.store_dir / new_id
        if old_dir.exists():
            shutil.copytree(str(old_dir), str(new_dir))
        else:
            new_dir.mkdir(parents=True, exist_ok=True)
            for sub in ["characters", "scenes", "props", "videos", "audio", "final"]:
                (new_dir / sub).mkdir(exist_ok=True)
        self._save_meta(new_id, new_meta)
        self._add_to_index(new_id, new_meta)
        return new_id

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
        """List asset files in a category folder (recursive into subdirectories).

        For multi-view assets (characters, scenes), returns all files under
        the category directory tree.
        """
        cat_dir = self.store_dir / project_id / "assets" / category
        if not cat_dir.exists():
            cat_dir = self.store_dir / project_id / category
        if not cat_dir.exists():
            return []
        files = []
        for p in sorted(cat_dir.rglob("*")):
            if p.is_file():
                files.append(str(p))
        return files

    def get_asset_groups(self, project_id: str) -> dict:
        """Get assets grouped by category and sub-group.

        Returns a structure suitable for frontend display:
        {
            "characters": {
                "林屿": {
                    "portrait": "path/to/portrait.png",
                    "profile": "path/to/profile.png",
                    "full_body": "path/to/full_body.png"
                }
            },
            "scenes": {
                "scene_01": {
                    "wide": "path/to/wide.png",
                    "medium": "path/to/medium.png"
                }
            },
            "props": {
                "old_recorder": {"standard_views": "path/to/standard_views.png"}
            },
            "videos": [...],
            "audio": [...],
            "final": [...]
        }
        """
        _validate_project_id(project_id)
        result: dict = {}

        for category in ("characters", "scenes", "props", "videos", "audio", "final"):
            if category in ("characters", "scenes", "props"):
                # ImageGenerationAgent stores new assets under output/<category>/<project_id>,
                # while older projects use output/<project_id>/<category>.
                groups: dict[str, dict[str, str]] = {}
                image_dirs = [
                    self.store_dir / project_id / "assets" / category,
                    self.store_dir / category / project_id,
                    self.store_dir / project_id / category,
                ]
                for cat_dir in image_dirs:
                    if not cat_dir.exists():
                        continue
                    for sub_dir in sorted(cat_dir.iterdir()):
                        if not sub_dir.is_dir():
                            continue
                        views = groups.setdefault(sub_dir.name, {})
                        for img_file in sorted(sub_dir.iterdir()):
                            if img_file.is_file() and img_file.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                                # Prefer the current ImageGenerationAgent layout when both exist.
                                views.setdefault(img_file.stem, str(img_file))
                result[category] = groups
            else:
                files = []
                seen_paths: set[str] = set()
                asset_dirs = [
                    self.store_dir / project_id / "assets" / category,
                    self.store_dir / project_id / category,
                ]
                for cat_dir in asset_dirs:
                    if not cat_dir.exists():
                        continue
                    for p in sorted(cat_dir.iterdir()):
                        if p.is_file() and str(p) not in seen_paths:
                            files.append({"name": p.name, "path": str(p)})
                            seen_paths.add(str(p))
                result[category] = files

        # Final videos have historically been written to output/final instead of
        # the project directory. The path recorded in metadata remains the
        # authoritative project association for those older projects.
        meta = self._load_meta(project_id) or {}
        state_json = (meta.get("assets") or {}).get("state_json")
        if state_json:
            try:
                state = json.loads(state_json)
            except json.JSONDecodeError:
                state = {}
            active_specs = (
                ("characters", "character_images", "characters", "character_name"),
                ("scenes", "scene_images", "scenes", "location_id"),
                ("props", "prop_images", "props", "prop_id"),
            )
            for category, container_key, records_key, identity_key in active_specs:
                if container_key not in state:
                    continue
                groups: dict[str, dict[str, str]] = {}
                records = ((state.get(container_key) or {}).get(records_key) or [])
                for record in records:
                    image_path = Path(record.get("image_path") or "")
                    if not image_path.is_file():
                        continue
                    identity = record.get(identity_key)
                    if category == "scenes" and not identity:
                        identity = f"scene_{int(record.get('scene_id') or 0):02d}"
                    if not identity:
                        continue
                    groups.setdefault(str(identity), {})[record.get("view_type") or "default"] = str(image_path)
                result[category] = groups
        final_video = (meta.get("assets") or {}).get("final_video")
        if final_video:
            final_path = Path(final_video)
            if not final_path.is_file():
                final_path = self.store_dir / "final" / final_path.name
            if final_path.is_file() and not any(item["path"] == str(final_path) for item in result["final"]):
                result["final"].append({"name": final_path.name, "path": str(final_path)})

        return result

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
