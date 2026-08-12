from src.schemas.prop import PropImage
from src.services.asset_prompt_composer import compose_asset_prompt
from tests.test_asset_generation_api import _client
from tests.test_prop_view_requirements import _state


def test_prop_prompt_contains_identity_and_view_constraints():
    state = _state()
    asset = PropImage(prop_id="key", prop_name="Old brass key", view_type="back", image_prompt="", image_path="", seed=1)

    prompt = compose_asset_prompt(state, "prop", "key", "back", asset)

    assert prompt["view_type"] == "back"
    assert "standard_views" not in prompt["provider_prompts"]
    assert prompt["provider_prompts"]["back"] == prompt["provider_prompt"]
    assert prompt["requirement_signature"]
    assert "Reference lock" in prompt["provider_prompt"]
    assert "Lock one reusable prop" in prompt["provider_prompt"]


def test_user_text_is_additive_and_does_not_replace_system_constraints():
    state = _state()
    asset = PropImage(prop_id="key", prop_name="Old brass key", view_type="front", image_prompt="", image_path="", seed=1)

    prompt = compose_asset_prompt(state, "prop", "key", "front", asset, "metal surface has scratches")

    assert "metal surface has scratches" in prompt["effective_prompt"]
    assert "Old brass key" in prompt["effective_prompt"]
    assert prompt["width"] == 1024 and prompt["height"] == 1024


def test_standard_views_generation_is_preview_only(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)

    response = client.post(
        f"/api/projects/{project_id}/assets/prop/old_key/generate",
        json={"view_type": "standard_views"},
    )

    assert response.status_code == 409
    assert "preview-only" in response.json()["detail"]
