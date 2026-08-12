from tests.test_shot_production_state import (
    test_state_aware_scene_selection_prefers_matching_location_state as _uses_state_view,
    test_state_aware_scene_selection_never_uses_other_state as _never_uses_other_state,
)


def test_video_prefers_matching_state_view(tmp_path):
    _uses_state_view(tmp_path)


def test_video_never_uses_other_state(tmp_path):
    _never_uses_other_state(tmp_path)
