from tests.test_wan_character_references import (
    test_scene_master_regeneration_marks_same_state_derivatives_stale as _master_stales_derivatives,
    test_scene_reference_edit_prepends_confirmed_master as _edit_uses_master,
)


def test_scene_edit_prepends_confirmed_master(tmp_path, monkeypatch):
    _edit_uses_master(tmp_path, monkeypatch)


def test_master_change_marks_same_state_derivatives_stale(tmp_path, monkeypatch):
    _master_stales_derivatives(tmp_path, monkeypatch)
