from pathlib import Path

from src.graph.state import DramaState
from src.schemas.audio import AudioAssets
from src.schemas.script import DialogueLine, Scene, ScriptBreakdown
from src.services.audio_timeline import final_assembly_audio_preflight


def _script_with_dialogue() -> ScriptBreakdown:
    return ScriptBreakdown(
        title="audio gate",
        genre="test",
        logline="test",
        duration_sec=5,
        narration_style="none",
        scenes=[Scene(
            scene_id=1,
            duration_sec=5,
            location="room",
            characters=["A"],
            dialogue=[DialogueLine(character="A", line="Hello")],
            action_description="talks",
            camera_direction="medium",
            emotion_tone="calm",
        )],
    )


def test_incomplete_dialogue_audio_requires_an_explicit_choice_before_assembly():
    result = final_assembly_audio_preflight(
        DramaState(script=_script_with_dialogue(), audio_assets=AudioAssets(scenes=[])),
    )

    assert result["dialogue_count"] == 1
    assert result["ready"] is False
    assert result["requires_confirmation"] is True
    assert result["unresolved_line_count"] == 1
    assert result["override"] == ""


def test_explicit_no_dialogue_confirmation_allows_assembly_without_audio():
    result = final_assembly_audio_preflight(
        DramaState(script=_script_with_dialogue(), audio_assets=AudioAssets(scenes=[])),
        override="no_dialogue_confirmed",
    )

    assert result["ready"] is True
    assert result["requires_confirmation"] is False
    assert result["no_dialogue"] is True
    assert result["override"] == "no_dialogue_confirmed"


def test_script_without_dialogue_needs_no_audio_confirmation():
    script = _script_with_dialogue()
    script.scenes[0].dialogue = []
    result = final_assembly_audio_preflight(DramaState(script=script, audio_assets=None))

    assert result["dialogue_count"] == 0
    assert result["ready"] is True
    assert result["requires_confirmation"] is False
    assert result["no_dialogue"] is False


def test_final_assemble_endpoint_requires_confirmation_for_incomplete_dialogue_audio(tmp_path, monkeypatch):
    from tests.test_job_api import _client
    from src.api import context as api_context

    client, project_id = _client(tmp_path, monkeypatch)
    state = api_context._load_project_state(project_id, require_visual=False)
    state.script_phase = "confirmed"
    state.script.scenes[0].characters = ["A"]
    state.script.scenes[0].dialogue = [DialogueLine(character="A", line="Hello")]
    state.audio_assets = AudioAssets(scenes=[])
    api_context._save_project_state(state)

    response = client.post(
        f"/api/projects/{project_id}/jobs",
        json={"job_type": "final.assemble", "target_type": "project"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "audio_incomplete"


def test_final_assemble_endpoint_persists_explicit_no_dialogue_override(tmp_path, monkeypatch):
    from tests.test_job_api import _client
    from src.api import context as api_context

    client, project_id = _client(tmp_path, monkeypatch)
    state = api_context._load_project_state(project_id, require_visual=False)
    state.script_phase = "confirmed"
    state.script.scenes[0].characters = ["A"]
    state.script.scenes[0].dialogue = [DialogueLine(character="A", line="Hello")]
    state.audio_assets = AudioAssets(scenes=[])
    api_context._save_project_state(state)

    response = client.post(
        f"/api/projects/{project_id}/jobs",
        json={
            "job_type": "final.assemble",
            "target_type": "project",
            "payload": {"audio_override": "no_dialogue_confirmed"},
        },
    )

    assert response.status_code == 202
    job_id = response.json()["job"]["job_id"]
    stored = api_context.job_store.get_job(project_id, job_id)
    assert stored["request"]["audio_override"] == "no_dialogue_confirmed"
from src.agents.assembly_agent import AssemblyAgent
from src.graph.state import DramaState
from src.schemas.assets import GeneratedAssets
from src.schemas.audio import AudioAssets, DialogueAudio, SceneAudio
from src.schemas.script import DialogueLine, Scene, ScriptBreakdown


def _assembly_script():
    return ScriptBreakdown(
        title="assembly",
        genre="test",
        logline="test",
        duration_sec=5,
        narration_style="none",
        scenes=[Scene(
            scene_id=1,
            duration_sec=5,
            location="room",
            characters=["A"],
            dialogue=[DialogueLine(character="A", line="Hello")],
            action_description="talks",
            camera_direction="medium",
            emotion_tone="calm",
        )],
    )


async def _fake_ffmpeg(self, script, assets, audio, output_path, output_dir, subtitle_enabled=True, subtitle_mode=None):
    assert subtitle_enabled is False
    assert subtitle_mode == "off"
    assert all(not scene.dialogue_audios for scene in audio.scenes)
    assert all(not scene.tts_audio_path for scene in audio.scenes)
    return output_path


def test_assembly_override_removes_tts_and_subtitles_for_one_run(monkeypatch, tmp_path):
    agent = AssemblyAgent()
    monkeypatch.setattr(agent, "_assemble_with_ffmpeg", _fake_ffmpeg.__get__(agent, AssemblyAgent))
    audio = AudioAssets(scenes=[SceneAudio(
        scene_id=1,
        tts_audio_path=str(tmp_path / "tts.mp3"),
        tts_text="Hello",
        dialogue_audios=[DialogueAudio(
            scene_id=1, dialogue_index=1, character="A", text="Hello", success=False,
        )],
    )])

    import asyncio
    result = asyncio.run(agent.run(
        _assembly_script(),
        GeneratedAssets(shots=[]),
        audio,
        project_id="test-project",
        audio_override="no_dialogue_confirmed",
    ))

    assert result.endswith(".mp4")
    assert audio.scenes[0].dialogue_audios
    assert audio.scenes[0].tts_audio_path
