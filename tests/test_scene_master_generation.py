from tests.test_wan_character_references import (
    test_scene_master_confirm_then_group_uses_master_reference as _master_group_flow,
    test_scene_reference_group_requires_confirmed_master as _group_requires_master,
)


def test_group_blocked_before_confirmation(tmp_path, monkeypatch):
    _group_requires_master(tmp_path, monkeypatch)


def test_master_confirm_then_reference_group_uses_master_reference(tmp_path, monkeypatch):
    _master_group_flow(tmp_path, monkeypatch)
