"""Move legacy generated files into the canonical per-project asset layout.

Canonical layout:
    output/<project_id>/assets/{characters,scenes,videos,audio,final}/

Only files with a deterministic project association are placed in a project.
Unassociated media is left in place and excluded from active project indexes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


PROJECT_CATEGORIES = ("characters", "scenes", "props", "videos", "audio", "final")
MEDIA_EXTENSIONS = {
    "videos": {".mp4", ".mov", ".webm", ".avi"},
    "audio": {".mp3", ".wav", ".m4a", ".aac", ".ogg"},
    "final": {".mp4", ".mov", ".webm"},
}


def move_file(source: Path, destination: Path, changes: dict[str, str], apply: bool) -> None:
    if not source.is_file():
        return
    if source.resolve() == destination.resolve():
        return
    if apply:
        destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if source.stat().st_size != destination.stat().st_size:
            raise RuntimeError(f"Refusing to overwrite a different file: {destination}")
        if apply:
            source.unlink()
    elif apply:
        shutil.move(str(source), str(destination))
    changes[str(source)] = str(destination)


def move_tree(source: Path, destination: Path, changes: dict[str, str], apply: bool) -> None:
    if not source.is_dir():
        return
    for file_path in source.rglob("*"):
        if file_path.is_file():
            move_file(file_path, destination / file_path.relative_to(source), changes, apply)
    if apply:
        for directory in sorted((p for p in source.rglob("*") if p.is_dir()), reverse=True):
            if not any(directory.iterdir()):
                directory.rmdir()
        if source.exists() and not any(source.iterdir()):
            source.rmdir()


def replace_paths(value: Any, changes: dict[str, str]) -> Any:
    if isinstance(value, str):
        return changes.get(value, value)
    if isinstance(value, list):
        return [replace_paths(item, changes) for item in value]
    if isinstance(value, dict):
        return {key: replace_paths(item, changes) for key, item in value.items()}
    return value


def _existing_source(path: Path, changes: dict[str, str]) -> Path:
    if path.exists():
        return path
    return next((Path(source) for source, target in changes.items() if target == str(path) and Path(source).exists()), path)


def normalize_indexed_images(
    project_dir: Path,
    state: dict,
    changes: dict[str, str],
    apply: bool,
) -> None:
    """Upgrade active scene IDs and create version records without indexing orphans."""
    from src.schemas.script import ScriptBreakdown
    from src.utils.visual_asset_manifest import normalize_scene_assets, normalize_scene_name

    script_data = state.get("script")
    if not script_data:
        return
    try:
        script = ScriptBreakdown.model_validate(script_data)
        normalize_scene_assets(script)
    except Exception:
        return
    if script.asset_manifest is None:
        return
    state["script"] = script.model_dump()
    locations_by_id = {item.location_id: item for item in script.asset_manifest.locations}
    locations_by_name = {
        normalize_scene_name(item.canonical_name): item for item in script.asset_manifest.locations
    }
    locations_by_scene = {
        item.representative_scene_id: item for item in script.asset_manifest.locations
    }
    bindings_by_scene = {
        binding.scene_id: binding for binding in script.asset_manifest.shot_bindings
    }

    scene_records = ((state.get("scene_images") or {}).get("scenes") or [])
    for record in scene_records:
        original_scene_id = int(record.get("scene_id") or 0)
        location = locations_by_id.get(record.get("location_id") or "")
        location = location or locations_by_name.get(normalize_scene_name(record.get("location") or ""))
        location = location or locations_by_scene.get(original_scene_id)
        if not location:
            continue
        record["location_id"] = location.location_id
        record["scene_id"] = location.representative_scene_id
        record["location"] = location.canonical_name
        binding = bindings_by_scene.get(original_scene_id) or bindings_by_scene.get(location.representative_scene_id)
        record["state_id"] = (
            getattr(binding, "state_id", "")
            or record.get("state_id")
            or location.default_state_id
        )
        old_path = Path(record.get("image_path") or "")
        if not old_path.name:
            continue
        destination = project_dir / "assets" / "scenes" / location.location_id / f"{record.get('view_type', 'wide')}.png"
        source = _existing_source(old_path, changes)
        move_file(source, destination, changes, apply)
        record["image_path"] = str(destination)

    for record in ((state.get("prop_images") or {}).get("props") or []):
        if record.get("view_type") == "standard_views":
            record.setdefault("generation_mode", "legacy_sheet")

    manifest_path = project_dir / "assets" / "asset_manifest.json"
    try:
        version_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        version_manifest = {"versions": {}}
    image_specs = (
        ("character", "character_images", "characters", "character_name"),
        ("scene", "scene_images", "scenes", "location_id"),
        ("prop", "prop_images", "props", "prop_id"),
    )
    for asset_type, container_key, records_key, identity_key in image_specs:
        for record in ((state.get(container_key) or {}).get(records_key) or []):
            path = Path(record.get("image_path") or "")
            asset_id = str(record.get(identity_key) or "")
            view_type = str(record.get("view_type") or "")
            if not asset_id or not view_type or not (path.is_file() or str(path) in changes.values()):
                continue
            version_id = record.get("version_id") or f"legacy-{hashlib.sha1(str(path).encode('utf-8')).hexdigest()[:16]}"
            record["version_id"] = version_id
            key = f"{asset_type}:{asset_id}:{view_type}"
            versions = version_manifest.setdefault("versions", {}).setdefault(key, [])
            if not any(item.get("id") == version_id for item in versions):
                versions.append({
                    "id": version_id,
                    "path": str(path),
                    "prompt": record.get("image_prompt") or "",
                    "source": "legacy",
                    "created_at": "",
                    "active": True,
                })
    if apply:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(version_manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def migrate(root: Path, apply: bool, report: dict[str, Any] | None = None) -> dict[str, int]:
    counts = {"project_files": 0, "unassigned_files": 0, "projects_updated": 0}
    if report is not None:
        report["projects"] = {}
    projects = sorted(path for path in root.iterdir() if path.is_dir() and (path / "meta.json").is_file())

    for project_dir in projects:
        project_id = project_dir.name
        asset_root = project_dir / "assets"
        changes: dict[str, str] = {}

        for category in PROJECT_CATEGORIES:
            move_tree(root / category / project_id, asset_root / category, changes, apply)
            move_tree(project_dir / category, asset_root / category, changes, apply)

        meta_path = project_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        original_meta = json.loads(json.dumps(meta, ensure_ascii=False))
        assets = meta.setdefault("assets", {})
        final_video = assets.get("final_video")
        if final_video:
            final_name = Path(final_video).name
            legacy_final = root / "final" / final_name
            canonical_final = asset_root / "final" / final_name
            move_file(legacy_final, canonical_final, changes, apply)
            if canonical_final.is_file() or (apply and str(legacy_final) in changes):
                assets["final_video"] = str(canonical_final)

        state_json = assets.get("state_json")
        if state_json:
            try:
                state = json.loads(state_json)
                state = replace_paths(state, changes)
                normalize_indexed_images(project_dir, state, changes, apply)
                state = replace_paths(state, changes)
                if assets.get("final_video"):
                    state["output_video_path"] = assets["final_video"]
                assets["state_json"] = json.dumps(state, ensure_ascii=False)
            except json.JSONDecodeError:
                pass

        if apply:
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        if report is not None:
            report["projects"][project_id] = {
                "changes": changes,
                "meta_before": original_meta,
            }
        counts["project_files"] += len(changes)
        counts["projects_updated"] += 1

    # Old image-agent runs could create a project-like folder without ever
    # creating project metadata. Count these files but leave them untouched.
    known_project_ids = {project.name for project in projects}
    for category in ("characters", "scenes"):
        source_dir = root / category
        if not source_dir.is_dir():
            continue
        for orphan_dir in list(source_dir.iterdir()):
            if orphan_dir.is_dir() and orphan_dir.name not in known_project_ids:
                orphan_files = sum(1 for path in orphan_dir.rglob("*") if path.is_file())
                counts["unassigned_files"] += orphan_files

    for category in ("videos", "audio", "final"):
        extensions = MEDIA_EXTENSIONS[category]
        source_dir = root if category == "videos" else root / category
        if not source_dir.is_dir():
            continue
        for file_path in list(source_dir.iterdir()):
            if file_path.is_file() and file_path.suffix.lower() in extensions:
                counts["unassigned_files"] += 1

    if report is not None:
        report["counts"] = counts
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="perform the migration")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "output")
    args = parser.parse_args()
    report: dict[str, Any] = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "apply" if args.apply else "dry-run",
    }
    counts = migrate(args.output_dir.resolve(), args.apply, report)
    mode = "applied" if args.apply else "dry-run"
    if args.apply:
        report_path = args.output_dir.resolve() / f"asset-migration-{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report: {report_path}")
    print(f"{mode}: {counts}")


if __name__ == "__main__":
    main()
