import json

from fastapi.testclient import TestClient
import pytest

import api_server
from src.api.context import _state_from_json
from src.api.routes import projects


def test_visual_style_catalog_exposes_all_primary_ids():
    client = TestClient(api_server.app)

    response = client.get("/api/visual-styles")

    assert response.status_code == 200
    assert {item["id"] for item in response.json()["styles"]} == {
        "cinematic_cel",
        "semi_realistic_anime",
        "japanese_animation",
        "live_action_cinematic",
    }


@pytest.mark.asyncio
async def test_project_creation_persists_style_and_deduplicates_modifiers(monkeypatch):
    saved = {}

    class FakeProjectStore:
        def create_project(self, title):
            return "style-project"

        def set_state_json(self, project_id, state_json):
            saved["project_id"] = project_id
            saved["state_json"] = state_json

    monkeypatch.setattr(projects, "project_store", FakeProjectStore())

    response = await projects.create_project_endpoint(
        projects.CreateProjectRequest(
            title="Style persistence",
            visual_style_id="japanese_animation",
            style_modifiers=["清新", "清新"],
        )
    )

    assert response["project_id"] == "style-project"
    profile = json.loads(saved["state_json"])["creative_profile"]
    assert profile["visual_style_id"] == "japanese_animation"
    assert profile["style_modifiers"] == ["清新"]


def test_invalid_explicit_style_id_is_a_validation_error():
    client = TestClient(api_server.app)

    response = client.post("/api/projects", json={"title": "bad", "visual_style_id": "nope"})

    assert response.status_code == 422
    assert "visual_style_id" in response.text


def test_legacy_profile_art_style_is_preserved_while_style_id_is_resolved():
    state = _state_from_json(json.dumps({
        "creative_profile": {"art_style": "动漫风、赛博朋克"},
    }, ensure_ascii=False))

    assert state.creative_profile.art_style == "动漫风、赛博朋克"
    assert state.creative_profile.visual_style_id == "cinematic_cel"
    assert state.creative_profile.style_modifiers == ["赛博朋克"]
