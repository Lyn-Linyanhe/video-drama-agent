from types import SimpleNamespace

from src.services.asset_seed import derive_asset_seed, resolve_generation_seed


def test_seed_is_stable_across_processes():
    assert derive_asset_seed("p1", "prop", "key", "front", 2) == derive_asset_seed(
        "p1", "prop", "key", "front", 2
    )


def test_seed_changes_when_project_or_view_changes():
    base = derive_asset_seed("p1", "prop", "key", "front", 2)
    assert derive_asset_seed("p2", "prop", "key", "front", 2) != base
    assert derive_asset_seed("p1", "prop", "key", "back", 2) != base


def test_retry_reuses_seed_but_user_regeneration_changes_it():
    old = SimpleNamespace(seed=123)
    assert resolve_generation_seed("p1", "prop", "key", "front", 2, retry_of=old) == 123
    regenerated = resolve_generation_seed(
        "p1", "prop", "key", "front", 2, retry_of=old, user_regenerate=True
    )
    assert 1 <= regenerated <= 999_999
    assert regenerated != 123
