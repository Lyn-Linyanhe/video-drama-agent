from tests.test_asset_migration import test_migration_moves_indexed_assets_and_is_idempotent as _migration_idempotent


def test_scene_migration_is_idempotent(tmp_path):
    _migration_idempotent(tmp_path)
