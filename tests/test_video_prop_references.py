from tests.test_shot_production_state import (
    test_reference_catalog_lists_only_video_ready_prop_references as _catalog_lists_video_ready,
    test_video_reference_bundle_ignores_prop_contact_sheet as _ignores_contact_sheet,
)


def test_video_uses_confirmed_independent_prop_reference(tmp_path):
    _ignores_contact_sheet(tmp_path)


def test_video_reference_catalog_excludes_legacy_sheet_and_pending_master(tmp_path):
    _catalog_lists_video_ready(tmp_path)
