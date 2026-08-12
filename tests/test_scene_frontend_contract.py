from pathlib import Path


def test_scene_cards_do_not_derive_count_from_shot_count():
    source = Path("web/js/components/asset-cards.js").read_text(encoding="utf-8")

    assert "shotIds.length" not in source
    assert "requiredViews" in source or "view-requirements" in source or "group_view_count" in source


def test_scene_editor_exposes_master_confirmation():
    html = Path("studio.html").read_text(encoding="utf-8")
    cards = Path("web/js/components/asset-cards.js").read_text(encoding="utf-8")

    assert "确认母版" in html or "确认母版" in cards or "confirmSceneMaster" in cards
