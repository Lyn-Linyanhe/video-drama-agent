"""Normalize existing project video and final-video filenames.

Existing clips do not carry a trustworthy scene ID in their filename, so they
are named ``clip_<sequence>_<provider>_<id>.mp4`` rather than pretending to
be a particular shot. Newly generated files use the stricter ``shot_`` rule.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any


def replace_paths(value: Any, changes: dict[str, str]) -> Any:
    if isinstance(value, str):
        return changes.get(value, value)
    if isinstance(value, list):
        return [replace_paths(item, changes) for item in value]
    if isinstance(value, dict):
        return {key: replace_paths(item, changes) for key, item in value.items()}
    return value


def provider_for(path: Path) -> str:
    prefix = path.stem.split("_", 1)[0].lower()
    return {"hh": "happyhorse", "kling": "kling"}.get(prefix, "video")


def rename_file(source: Path, destination: Path, changes: dict[str, str], apply: bool) -> None:
    if source == destination:
        return
    if destination.exists():
        raise RuntimeError(f"Destination already exists: {destination}")
    if apply:
        shutil.move(str(source), str(destination))
    changes[str(source)] = str(destination)


def normalize(root: Path, apply: bool) -> dict[str, int]:
    counts = {"videos": 0, "finals": 0, "projects": 0}
    for project_dir in sorted(root.glob("*/")):
        meta_path = project_dir / "meta.json"
        if not meta_path.is_file():
            continue
        changes: dict[str, str] = {}
        video_dir = project_dir / "assets" / "videos"
        if video_dir.is_dir():
            for index, video in enumerate(sorted(video_dir.glob("*")), start=1):
                if not video.is_file() or video.suffix.lower() not in {".mp4", ".mov", ".webm", ".avi"}:
                    continue
                if video.name.startswith("shot_") or video.name.startswith("clip_"):
                    continue
                token = hashlib.sha1(video.stem.encode("utf-8")).hexdigest()[:12]
                destination = video_dir / f"clip_{index:03d}_{provider_for(video)}_{token}{video.suffix.lower()}"
                rename_file(video, destination, changes, apply)
                counts["videos"] += 1

        final_dir = project_dir / "assets" / "final"
        if final_dir.is_dir():
            for final in sorted(final_dir.glob("*")):
                if not final.is_file() or final.suffix.lower() not in {".mp4", ".mov", ".webm"}:
                    continue
                if final.name.startswith("final_"):
                    continue
                stamp = datetime.fromtimestamp(final.stat().st_mtime).strftime("%Y%m%d%H%M%S")
                token = hashlib.sha1(final.stem.encode("utf-8")).hexdigest()[:8]
                destination = final_dir / f"final_{stamp}_{token}{final.suffix.lower()}"
                rename_file(final, destination, changes, apply)
                counts["finals"] += 1

        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assets = meta.setdefault("assets", {})
        if assets.get("final_video") in changes:
            assets["final_video"] = changes[assets["final_video"]]
        if assets.get("state_json"):
            try:
                state = replace_paths(json.loads(assets["state_json"]), changes)
                assets["state_json"] = json.dumps(state, ensure_ascii=False)
            except json.JSONDecodeError:
                pass
        if apply:
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        counts["projects"] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "output")
    args = parser.parse_args()
    print(("applied" if args.apply else "dry-run") + f": {normalize(args.output_dir.resolve(), args.apply)}")


if __name__ == "__main__":
    main()
