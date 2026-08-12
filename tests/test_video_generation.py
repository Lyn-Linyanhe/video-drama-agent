from tests.test_shot_production_state import (
    test_generation_snapshot_freezes_current_scene_prompt_and_reference as _snapshot_freezes_reference,
    test_state_aware_scene_selection_prefers_matching_location_state as _scene_state_reference,
    test_video_prop_reference_prefers_requested_view_and_falls_back_to_confirmed_master as _prop_view_reference,
)


def test_snapshot_contains_current_reference_version(tmp_path, monkeypatch):
    _snapshot_freezes_reference(tmp_path, monkeypatch)


def test_video_prefers_matching_scene_state_view(tmp_path):
    _scene_state_reference(tmp_path)


def test_video_uses_requested_prop_view_before_master(tmp_path):
    _prop_view_reference(tmp_path)
