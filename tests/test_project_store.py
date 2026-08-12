from src.utils import project_store


def test_asset_groups_support_current_and_legacy_image_layouts(tmp_path, monkeypatch):
    monkeypatch.setattr(project_store, "OUTPUT_DIR", tmp_path)
    store = project_store.ProjectStore()
    project_id = store.create_project(title="asset test")

    current_character = tmp_path / "characters" / project_id / "hero"
    current_character.mkdir(parents=True)
    (current_character / "portrait.png").touch()

    legacy_character = tmp_path / project_id / "characters" / "hero"
    legacy_character.mkdir(parents=True)
    (legacy_character / "profile.png").touch()

    current_scene = tmp_path / "scenes" / project_id / "scene_01"
    current_scene.mkdir(parents=True)
    (current_scene / "wide.png").touch()

    legacy_scene = tmp_path / project_id / "scenes" / "scene_01"
    legacy_scene.mkdir(parents=True)
    (legacy_scene / "medium.png").touch()

    groups = store.get_asset_groups(project_id)

    assert set(groups["characters"]["hero"]) == {"portrait", "profile"}
    assert set(groups["scenes"]["scene_01"]) == {"wide", "medium"}
