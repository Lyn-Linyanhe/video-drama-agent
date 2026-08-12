"""Hide nonessential one-shot props from existing project manifests.

Generated files and version history remain on disk. Only active project state
and bindings are updated. Run without --apply for a dry-run report.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.schemas.script import ScriptBreakdown
from src.utils.visual_asset_manifest import prune_nonessential_props


def prune_projects(output_dir: Path, apply: bool = False) -> dict[str, list[str]]:
    changes: dict[str, list[str]] = {}
    for project_dir in sorted(path for path in output_dir.iterdir() if path.is_dir()):
        meta_path = project_dir / "meta.json"
        if not meta_path.is_file():
            continue
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        assets = meta.get("assets") or {}
        state_json = assets.get("state_json") or ""
        try:
            state = json.loads(state_json)
            script = ScriptBreakdown.model_validate(state.get("script"))
        except Exception:
            continue
        removed = prune_nonessential_props(script)
        if not removed:
            continue
        removed_ids = set(removed)
        state["script"] = script.model_dump()
        prop_images = state.get("prop_images") or {}
        prop_images["props"] = [
            item for item in (prop_images.get("props") or []) if item.get("prop_id") not in removed_ids
        ]
        if prop_images:
            state["prop_images"] = prop_images
        assets["script"] = script.model_dump()
        assets["state_json"] = json.dumps(state, ensure_ascii=False)
        meta["assets"] = assets
        changes[project_dir.name] = removed
        if apply:
            meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return changes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "output")
    args = parser.parse_args()
    changes = prune_projects(args.output_dir.resolve(), args.apply)
    print(json.dumps({"mode": "apply" if args.apply else "dry-run", "changes": changes}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
