import pytest

from src.agents.audio_agent import AudioAgent
from src.schemas.assets import GeneratedAssets
from src.schemas.script import DialogueLine, Scene, ScriptBreakdown


def _script():
    return ScriptBreakdown(
        title="test", genre="悬疑", logline="test", duration_sec=3,
        narration_style="none", scenes=[Scene(
            scene_id=1, duration_sec=3, location="室内", characters=["顾衡"],
            dialogue=[DialogueLine(character="顾衡", line="你来了。")],
            action_description="看向门口", camera_direction="中景", emotion_tone="紧张",
        )],
    )


@pytest.mark.asyncio
async def test_native_scene_does_not_generate_duplicate_tts(monkeypatch):
    async def fail_tts(*_args, **_kwargs):
        raise AssertionError("native dialogue must not invoke TTS")

    monkeypatch.setattr(AudioAgent, "_generate_dialogue_tts", fail_tts)
    audio = await AudioAgent().run(
        _script(), GeneratedAssets(shots=[]),
        native_scene_audio={1: {"native_audio_enabled": True, "native_audio_path": "video.mp4", "audio_source": "native"}},
    )
    assert audio.source_audio_enabled is True
    assert audio.scenes[0].native_audio_enabled is True
    assert audio.scenes[0].dialogue_audios[0].success is True
    assert audio.scenes[0].dialogue_audios[0].audio_path == ""


@pytest.mark.asyncio
async def test_non_seedance_scene_still_uses_tts(monkeypatch):
    async def fake_tts(*_args, **_kwargs):
        return "", "edge", ""

    monkeypatch.setattr(AudioAgent, "_generate_dialogue_tts", fake_tts)
    audio = await AudioAgent().run(_script(), GeneratedAssets(shots=[]), native_scene_audio={})
    assert audio.source_audio_enabled is False
