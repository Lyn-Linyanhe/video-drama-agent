import json

import pytest

from src.api.routes import projects


@pytest.mark.asyncio
async def test_create_project_persists_requested_audio_mode(monkeypatch):
    captured = {}

    class FakeProjectStore:
        def create_project(self, title):
            captured["title"] = title
            return "audio-mode-project"

        def set_state_json(self, project_id, state_json):
            captured["project_id"] = project_id
            captured["state_json"] = state_json

    monkeypatch.setattr(projects, "project_store", FakeProjectStore())

    response = await projects.create_project_endpoint(
        projects.CreateProjectRequest(title="音频策略", audio_mode="external_tts")
    )

    assert response["project_id"] == "audio-mode-project"
    saved = json.loads(captured["state_json"])
    assert saved["creative_profile"]["audio_mode"] == "external_tts"
