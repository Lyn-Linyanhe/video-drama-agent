"""Versioned storage for editable character, scene, and prop images."""

from __future__ import annotations

import json
import shutil
import tempfile
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from PIL import Image


@contextmanager
def asset_commit_transaction(project_dir: Path):
    """Restore active files, version manifest, and project state if a group commit fails."""
    project_dir = Path(project_dir)
    asset_dir = project_dir / "assets"
    meta_path = project_dir / "meta.json"
    index_path = project_dir.parent / "projects.json"
    with tempfile.TemporaryDirectory(prefix="asset-commit-") as temp_dir:
        snapshot_root = Path(temp_dir)
        snapshot_assets = snapshot_root / "assets"
        had_assets = asset_dir.exists()
        if had_assets:
            shutil.copytree(asset_dir, snapshot_assets)
        snapshots = {
            meta_path: meta_path.read_bytes() if meta_path.is_file() else None,
            index_path: index_path.read_bytes() if index_path.is_file() else None,
        }
        try:
            yield
        except BaseException:
            if asset_dir.exists():
                shutil.rmtree(asset_dir)
            if had_assets:
                shutil.copytree(snapshot_assets, asset_dir)
            for path, content in snapshots.items():
                if content is None:
                    if path.exists():
                        path.unlink()
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(content)
            raise


class ImageAssetVersions:
    """Keeps one active PNG and a switchable history for each image view."""

    def __init__(self, project_dir: Path):
        self.asset_root = project_dir / "assets"
        self.manifest_path = self.asset_root / "asset_manifest.json"

    def active_path(self, asset_type: str, asset_id: str, view_type: str) -> Path:
        if asset_type == "character":
            return self.asset_root / "characters" / asset_id / f"{view_type}.png"
        if asset_type == "scene":
            scene_dir = asset_id if asset_id.startswith("loc_") else f"scene_{int(asset_id):02d}"
            return self.asset_root / "scenes" / scene_dir / f"{view_type}.png"
        if asset_type == "prop":
            return self.asset_root / "props" / asset_id / f"{view_type}.png"
        raise ValueError("Unsupported image asset type")

    def list_versions(
        self,
        asset_type: str,
        asset_id: str,
        view_type: str,
        prompt: str,
    ) -> list[dict[str, Any]]:
        key = self._key(asset_type, asset_id, view_type)
        active_path = self.active_path(asset_type, asset_id, view_type)
        manifest = self._load_manifest()
        versions = manifest.setdefault("versions", {}).setdefault(key, [])
        if active_path.is_file() and not any(record.get("path") == str(active_path) for record in versions):
            versions.append(self._record(active_path, prompt, "legacy", active=True))
        # Projects can be moved between machines.  In that case an old
        # absolute path may still carry ``active=true`` even though the
        # current active file has a different root.  Normalize the marker
        # whenever history is read so exactly one record represents the
        # active slot (or none when the slot is missing).
        self._set_active(versions, str(active_path) if active_path.is_file() else "")
        self._save_manifest(manifest)
        return sorted(
            (
                {
                    **record,
                    "file_available": Path(str(record.get("path") or "")).is_file(),
                }
                for record in versions
            ),
            key=lambda record: record.get("created_at", ""),
            reverse=True,
        )

    def find_version(
        self,
        asset_type: str,
        asset_id: str,
        view_type: str,
        version_id: str,
    ) -> dict[str, Any] | None:
        """Find a historical record without moving it into the active slot."""
        manifest = self._load_manifest()
        versions = manifest.get("versions", {}).get(self._key(asset_type, asset_id, view_type), [])
        return next((dict(record) for record in versions if record.get("id") == version_id), None)

    def replace_with_file(
        self,
        asset_type: str,
        asset_id: str,
        view_type: str,
        source_path: Path,
        prompt: str,
        source: str,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[Path, list[dict[str, Any]]]:
        active_path = self.active_path(asset_type, asset_id, view_type)
        active_path.parent.mkdir(parents=True, exist_ok=True)
        manifest = self._load_manifest()
        key = self._key(asset_type, asset_id, view_type)
        versions = manifest.setdefault("versions", {}).setdefault(key, [])
        staged_path = active_path.parent / f".{active_path.stem}_{uuid.uuid4().hex}.tmp.png"
        try:
            self._convert_to_png(source_path, staged_path)
            self._archive_active(active_path, versions, prompt)
            staged_path.replace(active_path)
        finally:
            staged_path.unlink(missing_ok=True)
        versions.append(self._record(active_path, prompt, source, active=True, metadata=metadata))
        self._set_active(versions, str(active_path))
        self._save_manifest(manifest)
        return active_path, sorted(versions, key=lambda record: record.get("created_at", ""), reverse=True)

    def add_candidate_file(
        self,
        asset_type: str,
        asset_id: str,
        view_type: str,
        source_path: Path,
        prompt: str,
        source: str,
        metadata: dict[str, Any] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Persist a generated attempt without changing the active image.

        Provider output must be archived before a retry starts.  This method
        deliberately does not move the active file and does not delete the
        provider file, so a later activation can still use the candidate.
        """
        if not Path(source_path).is_file():
            raise FileNotFoundError(str(source_path))
        active_path = self.active_path(asset_type, asset_id, view_type)
        history_dir = active_path.parent / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        candidate_path = history_dir / f"{active_path.stem}_candidate_{uuid.uuid4().hex}.png"
        # Candidate archival must not consume the provider output: a passing
        # view may still be needed by the group commit or as a repair reference.
        with Image.open(source_path) as image:
            image.convert("RGBA").save(candidate_path, "PNG")
        manifest = self._load_manifest()
        key = self._key(asset_type, asset_id, view_type)
        versions = manifest.setdefault("versions", {}).setdefault(key, [])
        record = self._record(
            candidate_path,
            prompt,
            source,
            active=False,
            metadata={"candidate": True, **(metadata or {})},
        )
        versions.append(record)
        self._set_active(versions, str(active_path) if active_path.is_file() else "")
        self._save_manifest(manifest)
        return str(record["id"]), sorted(versions, key=lambda item: item.get("created_at", ""), reverse=True)

    def accept_candidate(
        self,
        asset_type: str,
        asset_id: str,
        view_type: str,
        version_id: str,
        current_prompt: str,
    ) -> tuple[Path, str, list[dict[str, Any]]]:
        """Promote any existing candidate while preserving its review report."""
        manifest = self._load_manifest()
        versions = manifest.setdefault("versions", {}).setdefault(
            self._key(asset_type, asset_id, view_type), []
        )
        selected = next((record for record in versions if record.get("id") == version_id), None)
        if not selected:
            raise ValueError("Image history version not found")
        if not selected.get("candidate"):
            raise ValueError("Only a pending candidate can be accepted")
        selected_path = Path(str(selected.get("path") or ""))
        if not selected_path.is_file():
            raise ValueError("Image history file is missing")
        active_path = self.active_path(asset_type, asset_id, view_type)
        active_path.parent.mkdir(parents=True, exist_ok=True)
        if selected_path != active_path:
            self._archive_active(active_path, versions, current_prompt)
            shutil.move(str(selected_path), str(active_path))
            selected["path"] = str(active_path)
        selected["candidate"] = False
        selected["pending_review"] = False
        selected["rejected"] = False
        selected["accepted_at"] = datetime.now().isoformat(timespec="seconds")
        self._set_active(versions, str(active_path))
        self._save_manifest(manifest)
        return active_path, str(selected.get("prompt") or ""), sorted(
            versions, key=lambda record: record.get("created_at", ""), reverse=True
        )

    def accept_candidate_group(
        self,
        asset_type: str,
        asset_id: str,
        group_id: str,
        current_prompts: dict[str, str] | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Accept the latest candidate for every view in one reference group."""
        if not str(group_id or "").strip():
            raise ValueError("Group candidate is missing group_id")
        manifest = self._load_manifest()
        all_versions = manifest.setdefault("versions", {})
        selected_by_view: dict[str, tuple[list[dict[str, Any]], dict[str, Any]]] = {}
        prefix = f"{asset_type}:{asset_id}:"
        for key, records in all_versions.items():
            if not key.startswith(prefix) or not isinstance(records, list):
                continue
            view_type = key[len(prefix):]
            candidates = [
                record for record in records
                if record.get("candidate")
                and not record.get("rejected")
                and str(record.get("group_id") or "") == str(group_id)
            ]
            if candidates:
                selected_by_view[view_type] = (
                    records,
                    max(candidates, key=lambda record: (int(record.get("attempt_no") or 0), str(record.get("created_at") or ""))),
                )
        if not selected_by_view:
            raise ValueError("Group candidate not found")
        for view_type, (_records, selected) in selected_by_view.items():
            if not Path(str(selected.get("path") or "")).is_file():
                raise ValueError(f"Group candidate file is missing for view {view_type}")

        current_prompts = current_prompts or {}
        accepted_paths: dict[str, Path] = {}
        for view_type, (records, selected) in selected_by_view.items():
            active_path = self.active_path(asset_type, asset_id, view_type)
            active_path.parent.mkdir(parents=True, exist_ok=True)
            selected_path = Path(str(selected["path"]))
            if selected_path != active_path:
                self._archive_active(active_path, records, current_prompts.get(view_type, ""))
                shutil.move(str(selected_path), str(active_path))
                selected["path"] = str(active_path)
            selected["candidate"] = False
            selected["pending_review"] = False
            selected["rejected"] = False
            selected["accepted_at"] = datetime.now().isoformat(timespec="seconds")
            self._set_active(records, str(active_path))
            accepted_paths[view_type] = active_path
        self._save_manifest(manifest)
        return {
            view_type: sorted(records, key=lambda record: record.get("created_at", ""), reverse=True)
            for view_type, (records, _selected) in selected_by_view.items()
        }

    def reject_candidate(
        self,
        asset_type: str,
        asset_id: str,
        view_type: str,
        version_id: str,
    ) -> list[dict[str, Any]]:
        """Mark a candidate rejected while keeping its file in history."""
        manifest = self._load_manifest()
        versions = manifest.setdefault("versions", {}).setdefault(
            self._key(asset_type, asset_id, view_type), []
        )
        selected = next((record for record in versions if record.get("id") == version_id), None)
        if not selected:
            raise ValueError("Image history version not found")
        if not selected.get("candidate"):
            raise ValueError("Only a pending candidate can be rejected")
        selected["pending_review"] = False
        selected["rejected"] = True
        selected["rejected_at"] = datetime.now().isoformat(timespec="seconds")
        self._set_active(versions, str(self.active_path(asset_type, asset_id, view_type)))
        self._save_manifest(manifest)
        return sorted(versions, key=lambda record: record.get("created_at", ""), reverse=True)

    def update_quality_report(
        self,
        asset_type: str,
        asset_id: str,
        view_type: str,
        version_id: str,
        quality_report: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Persist a review result on one history record without moving files."""
        manifest = self._load_manifest()
        versions = manifest.setdefault("versions", {}).setdefault(
            self._key(asset_type, asset_id, view_type), []
        )
        selected = next((record for record in versions if record.get("id") == version_id), None)
        if not selected:
            raise ValueError("Image history version not found")
        selected["quality_report"] = quality_report if isinstance(quality_report, dict) else {}
        selected["reviewed_at"] = datetime.now().isoformat(timespec="seconds")
        selected["pending_review"] = False
        active_path = self.active_path(asset_type, asset_id, view_type)
        self._set_active(versions, str(active_path) if active_path.is_file() else "")
        self._save_manifest(manifest)
        return sorted(versions, key=lambda record: record.get("created_at", ""), reverse=True)

    def activate(
        self,
        asset_type: str,
        asset_id: str,
        view_type: str,
        version_id: str,
        current_prompt: str,
    ) -> tuple[Path, str, list[dict[str, Any]]]:
        active_path = self.active_path(asset_type, asset_id, view_type)
        manifest = self._load_manifest()
        key = self._key(asset_type, asset_id, view_type)
        versions = manifest.setdefault("versions", {}).setdefault(key, [])
        selected = next((record for record in versions if record.get("id") == version_id), None)
        if not selected:
            raise ValueError("Image history version not found")

        selected_path = Path(selected["path"])
        if not selected_path.is_file():
            raise ValueError("Image history file is missing")
        if selected_path != active_path:
            self._archive_active(active_path, versions, current_prompt)
            shutil.move(str(selected_path), str(active_path))
            selected["path"] = str(active_path)
        self._set_active(versions, str(active_path))
        self._save_manifest(manifest)
        return active_path, str(selected.get("prompt") or ""), sorted(
            versions, key=lambda record: record.get("created_at", ""), reverse=True
        )

    def _archive_active(self, active_path: Path, versions: list[dict[str, Any]], prompt: str) -> None:
        if not active_path.is_file():
            return
        history_dir = active_path.parent / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        archived = history_dir / f"{active_path.stem}_{stamp}_{uuid.uuid4().hex[:8]}.png"
        shutil.move(str(active_path), str(archived))
        record = next((item for item in versions if item.get("path") == str(active_path)), None)
        if record:
            record["path"] = str(archived)
            record["active"] = False
        else:
            versions.append(self._record(archived, prompt, "legacy", active=False))

    @staticmethod
    def _convert_to_png(source_path: Path, destination: Path) -> None:
        try:
            with Image.open(source_path) as image:
                image.convert("RGBA").save(destination, "PNG")
        finally:
            if source_path.exists() and source_path != destination:
                source_path.unlink()

    @staticmethod
    def _record(path: Path, prompt: str, source: str, active: bool, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        record = {
            "id": uuid.uuid4().hex,
            "path": str(path),
            "prompt": prompt,
            "source": source,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "active": active,
        }
        if metadata:
            record.update({key: value for key, value in metadata.items() if value not in (None, "", [], {})})
        return record

    @staticmethod
    def _set_active(versions: list[dict[str, Any]], active_path: str) -> None:
        for record in versions:
            record["active"] = record.get("path") == active_path

    @staticmethod
    def _key(asset_type: str, asset_id: str, view_type: str) -> str:
        return f"{asset_type}:{asset_id}:{view_type}"

    def _load_manifest(self) -> dict[str, Any]:
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"versions": {}}

    def _save_manifest(self, manifest: dict[str, Any]) -> None:
        self.asset_root.mkdir(parents=True, exist_ok=True)
        self.manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
