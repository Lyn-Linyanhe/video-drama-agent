import json
from pathlib import Path

from scripts.migrate_project_assets import migrate
from src.utils.visual_asset_manifest import stable_location_id


def _legacy_state(project_id: str, scene_path: Path, prop_path: Path) -> dict:
    return {
        "project_id": project_id,
        "script": {
            "title": "迁移测试", "genre": "悬疑", "logline": "测试", "duration_sec": 5,
            "narration_style": "无旁白", "target_audience": "通用",
            "scenes": [{
                "scene_id": 1, "duration_sec": 5, "location": "地下室", "characters": [],
                "dialogue": [], "action_description": "旧铁门", "camera_direction": "全景",
                "emotion_tone": "压抑", "transition_from_prev": "cut",
            }],
            "asset_manifest": {
                "characters": [],
                "locations": [{
                    "location_id": "basement_old", "canonical_name": "地下室",
                    "time_condition": "", "environment_description": "旧铁门",
                    "representative_scene_id": 1, "shot_ids": [1], "aliases": [],
                }],
                "props": [{
                    "prop_id": "old_key", "name": "旧钥匙", "description": "黄铜",
                    "shot_ids": [1], "is_handheld": True, "aliases": [],
                }],
                "shot_bindings": [{
                    "scene_id": 1, "location_id": "basement_old", "on_screen_character_ids": [],
                    "voice_only_speakers": [], "prop_ids": ["old_key"], "visible_state_notes": "",
                }],
            },
        },
        "scene_images": {"scenes": [{
            "scene_id": 1, "location": "地下室", "view_type": "wide",
            "image_prompt": "地下室空镜", "image_path": str(scene_path), "seed": 1,
        }]},
        "prop_images": {"props": [{
            "prop_id": "old_key", "prop_name": "旧钥匙", "view_type": "standard_views",
            "image_prompt": "六视图", "image_path": str(prop_path), "seed": 2,
        }]},
    }


def test_migration_moves_indexed_assets_and_is_idempotent(tmp_path):
    project_id = "a1b2c3d4e5f6"
    project_dir = tmp_path / project_id
    scene_path = project_dir / "scenes" / "scene_01" / "wide.png"
    prop_path = project_dir / "props" / "old_key" / "standard_views.png"
    scene_path.parent.mkdir(parents=True)
    prop_path.parent.mkdir(parents=True)
    scene_path.write_bytes(b"scene")
    prop_path.write_bytes(b"prop")
    state = _legacy_state(project_id, scene_path, prop_path)
    meta = {
        "id": project_id, "title": "迁移测试", "assets": {"state_json": json.dumps(state, ensure_ascii=False)},
    }
    (project_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    migrate(tmp_path, apply=False)
    assert scene_path.is_file()
    assert prop_path.is_file()

    migrate(tmp_path, apply=True)
    location_id = stable_location_id("地下室")
    migrated_scene = project_dir / "assets" / "scenes" / location_id / "wide.png"
    migrated_prop = project_dir / "assets" / "props" / "old_key" / "standard_views.png"
    assert migrated_scene.read_bytes() == b"scene"
    assert migrated_prop.read_bytes() == b"prop"

    migrated_meta = json.loads((project_dir / "meta.json").read_text(encoding="utf-8"))
    migrated_state = json.loads(migrated_meta["assets"]["state_json"])
    scene_record = migrated_state["scene_images"]["scenes"][0]
    prop_record = migrated_state["prop_images"]["props"][0]
    location = migrated_state["script"]["asset_manifest"]["locations"][0]
    assert scene_record["location_id"] == location_id
    assert scene_record["state_id"] == location["default_state_id"]
    assert scene_record["image_path"] == str(migrated_scene)
    assert scene_record["version_id"].startswith("legacy-")
    assert prop_record["image_path"] == str(migrated_prop)
    assert prop_record["version_id"].startswith("legacy-")
    assert prop_record["generation_mode"] == "legacy_sheet"

    migrate(tmp_path, apply=True)
    remigrated_meta = json.loads((project_dir / "meta.json").read_text(encoding="utf-8"))
    remigrated_state = json.loads(remigrated_meta["assets"]["state_json"])
    assert remigrated_state == migrated_state

    manifest = json.loads((project_dir / "assets" / "asset_manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["versions"][f"scene:{location_id}:wide"]) == 1
    assert len(manifest["versions"]["prop:old_key:standard_views"]) == 1
    assert migrated_scene.read_bytes() == b"scene"
    assert migrated_prop.read_bytes() == b"prop"
