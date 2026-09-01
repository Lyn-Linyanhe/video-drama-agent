import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from src.api.routes import projects, scripts, settings as settings_routes


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
from src.api.routes import production
from src.graph.state import DramaState


@pytest.mark.asyncio
async def test_new_project_defaults_to_native_dialogue(monkeypatch):
    captured = {}

    class FakeProjectStore:
        def create_project(self, title):
            return "native-default-project"

        def set_state_json(self, project_id, state_json):
            captured["state_json"] = state_json

    monkeypatch.setattr(projects, "project_store", FakeProjectStore())

    response = await projects.create_project_endpoint(
        projects.CreateProjectRequest(title="默认原生对白")
    )

    assert response["project_id"] == "native-default-project"
    saved = json.loads(captured["state_json"])
    assert saved["creative_profile"]["audio_mode"] == "native_dialogue"


@pytest.mark.asyncio
async def test_production_settings_persists_audio_mode(monkeypatch):
    state = DramaState(project_id="audio-settings-project")
    saved = []

    monkeypatch.setattr(production, "_load_project_state", lambda _project_id: state)
    monkeypatch.setattr(production, "_save_project_state", lambda value: saved.append(value))
    monkeypatch.setattr(
        production,
        "_production_payload",
        lambda value: {"audio_mode": value.creative_profile.audio_mode},
    )

    response = await production.update_project_production_settings(
        "audio-settings-project",
        production.ProductionSettingsRequest(audio_mode="external_tts"),
    )

    assert response["audio_mode"] == "external_tts"
    assert state.creative_profile.audio_mode == "external_tts"
    assert saved == [state]


@pytest.mark.asyncio
async def test_direct_script_generation_defaults_new_project_to_native_dialogue(monkeypatch):
    captured = {}

    class FakeProjectStore:
        def create_project(self, title, user_input=""):
            return "direct-script-project"

        def update_status(self, *args):
            return None

        def set_script(self, *args):
            return None

        def set_state_json(self, project_id, state_json):
            captured["state_json"] = state_json

    class FakeScriptAgent:
        async def run(self, _text, _mode, profile):
            captured["audio_mode"] = profile.audio_mode
            raise RuntimeError("stop after profile capture")

    monkeypatch.setattr(scripts, "project_store", FakeProjectStore())
    monkeypatch.setattr(scripts, "script_agent", FakeScriptAgent())
    response = await scripts.generate_script(scripts.ScriptRequest(user_input="new", input_mode="idea"))
    assert captured["audio_mode"] == "native_dialogue"
    assert "脚本生成失败" in response.log[0]

@pytest.mark.asyncio
async def test_native_audio_mode_does_not_silently_generate_tts_when_provider_returns_no_audio(monkeypatch, tmp_path):
    from src.agents.audio_agent import AudioAgent
    from src.schemas.assets import GeneratedAssets
    from src.schemas.script import DialogueLine, Scene, ScriptBreakdown

    script = ScriptBreakdown(
        title="native missing", genre="test", logline="test", duration_sec=5,
        narration_style="none", scenes=[Scene(
            scene_id=1, duration_sec=5, location="room", characters=["A"],
            dialogue=[DialogueLine(character="A", line="Hello")],
            action_description="talks", camera_direction="medium", emotion_tone="calm",
        )],
    )
    agent = AudioAgent()
    calls = []

    async def fail_if_called(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("native mode must not silently invoke TTS")

    monkeypatch.setattr(agent, "_generate_dialogue_tts", fail_if_called)
    audio = await agent.run(
        script, GeneratedAssets(shots=[]), project_id="native-missing", audio_mode="native_dialogue",
        native_scene_audio={1: {"native_audio_enabled": False, "audio_source": "none"}},
    )

    assert calls == []
    line = audio.scenes[0].dialogue_audios[0]
    assert line.success is False
    assert line.provider == "native-pending"
    assert line.fallback_reason



def test_application_default_video_provider_is_happyhorse():
    """Verify the import-time default without reloading shared config in pytest."""
    env = os.environ.copy()
    env.pop("DEFAULT_VIDEO_API", None)
    result = subprocess.run(
        [sys.executable, "-c", "import src.config as config; print(config.DEFAULT_API)"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "happyhorse"


def test_settings_default_video_provider_is_happyhorse():
    assert settings_routes.SettingsRequest().default_video_api == "happyhorse"
