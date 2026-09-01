from src.api_clients.base import VideoGenResult
from src.schemas.assets import ShotAsset, ShotVideoVersion
from src.schemas.audio import AudioAssets
from src.agents.video_gen_agent import VideoGenerationAgent
from src.schemas.prompt import ShotPrompt

import pytest


def test_video_result_and_shot_version_keep_native_audio_metadata():
    result = VideoGenResult(
        success=True,
        audio_source="native",
        native_audio_enabled=True,
        native_audio_path="clip.mp4",
        audio_present=True,
    )
    result_data = {key: value for key, value in result.__dict__.items() if key != "success"}
    asset = ShotAsset(scene_id=1, success=True, **result_data)
    asset_data = {
        key: value for key, value in asset.model_dump().items()
        if key not in {"scene_id", "success"}
    }
    version = ShotVideoVersion(
        scene_id=1,
        success=True,
        version_id="v1",
        created_at="",
        **asset_data,
    )
    assert version.audio_source == "native"
    assert version.native_audio_enabled is True
    assert version.audio_present is True


def test_legacy_audio_state_defaults_to_unknown_source_audio():
    assert AudioAssets(scenes=[]).source_audio_enabled is None


@pytest.mark.asyncio
async def test_video_agent_enables_native_audio_only_for_seedance():
    class Client:
        provider_name = "seedance"

        async def generate_video(self, **kwargs):
            self.request = kwargs
            return VideoGenResult(success=True, local_path="video.mp4", duration_sec=3)

        async def get_cost_estimate(self, duration_sec):
            return 0

    client = Client()
    agent = VideoGenerationAgent(api_client=client, max_retries=1)
    result = await agent.generate_shot(ShotPrompt(scene_id=1, prompt="test"), [])
    assert result.success is True
    assert client.request["generate_audio"] is True


def test_video_capabilities_declare_native_dialogue_support():
    from src.services.video_capabilities import normalize_video_policy

    seedance = normalize_video_policy({
        "provider": "seedance",
        "model": "doubao-seedance-2-0-fast-260128",
        "resolution": "720p",
    })
    happyhorse = normalize_video_policy({
        "provider": "happyhorse",
        "model": "happyhorse-1.1-r2v",
        "resolution": "720p",
    })

    assert seedance["capability"]["supports_native_dialogue"] is True
    assert seedance["capability"]["native_audio_status"] == "supported"
    assert happyhorse["capability"]["supports_native_dialogue"] is True
    assert happyhorse["capability"]["native_audio_status"] == "best_effort"

@pytest.mark.asyncio
async def test_happyhorse_marks_embedded_audio_as_native_when_requested(monkeypatch, tmp_path):
    from src.api_clients.happyhorse import HappyHorseVideoClient

    client = HappyHorseVideoClient(api_key='test')
    monkeypatch.setattr(client, '_submit_task', lambda **_kwargs: _async_value('horse-task'))
    monkeypatch.setattr(client, '_poll_task', lambda _task_id: _async_value('https://example.com/video.mp4'))
    reference = tmp_path / 'ref.png'
    reference.write_bytes(b'image')
    monkeypatch.setattr(client, '_download_video', lambda *_args: _async_value(str(tmp_path / 'horse.mp4')))
    monkeypatch.setattr(client, '_has_audio_stream', lambda _path: True)

    result = await client.generate_video('scene', image_references=[str(reference)], generate_audio=True)

    assert result.audio_present is True
    assert result.audio_source == 'native'
    assert result.native_audio_enabled is True
    assert result.native_audio_path.endswith('horse.mp4')


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_video_agent_disables_native_audio_for_external_tts_projects():
    from src.schemas.project import CreativeProfile

    class Client:
        provider_name = "happyhorse"

        async def generate_video(self, **kwargs):
            self.request = kwargs
            return VideoGenResult(success=True, local_path="video.mp4", duration_sec=3)

        async def get_cost_estimate(self, duration_sec):
            return 0

    client = Client()
    agent = VideoGenerationAgent(api_client=client, max_retries=1)
    result = await agent.generate_shot(
        ShotPrompt(scene_id=1, prompt="test"),
        [],
        creative_profile=CreativeProfile(audio_mode="external_tts"),
    )

    assert result.success is True
    assert client.request["generate_audio"] is False


@pytest.mark.asyncio
async def test_native_audio_metadata_records_the_actual_video_provider():
    from src.agents.audio_agent import AudioAgent
    from src.schemas.assets import GeneratedAssets, ShotAsset
    from src.schemas.script import DialogueLine, Scene, ScriptBreakdown

    script = ScriptBreakdown(
        title="native metadata", genre="test", logline="test", duration_sec=5,
        narration_style="none", scenes=[Scene(
            scene_id=1, duration_sec=5, location="room", characters=["A"],
            dialogue=[DialogueLine(character="A", line="Hello")],
            action_description="talks", camera_direction="medium", emotion_tone="calm",
        )],
    )
    asset = ShotAsset(
        scene_id=1, success=True, api_used="happyhorse",
        audio_source="native", native_audio_enabled=True,
        native_audio_path="clip.mp4", audio_present=True,
    )

    audio = await AudioAgent().run(
        script,
        GeneratedAssets(shots=[asset]),
        project_id="native-metadata",
        audio_mode="native_dialogue",
        native_scene_audio={1: asset.model_dump()},
    )

    assert audio.scenes[0].native_audio_source == "happyhorse"
    assert audio.scenes[0].dialogue_audios[0].provider == "happyhorse-native"


@pytest.mark.asyncio
async def test_switching_to_external_tts_disables_previous_source_audio():
    from src.agents.audio_agent import AudioAgent
    from src.schemas.assets import GeneratedAssets
    from src.schemas.audio import AudioAssets, SceneAudio
    from src.schemas.script import Scene, ScriptBreakdown

    script = ScriptBreakdown(
        title="switch audio", genre="test", logline="test", duration_sec=5,
        narration_style="none", scenes=[Scene(
            scene_id=1, duration_sec=5, location="room", characters=[], dialogue=[],
            action_description="silent", camera_direction="wide", emotion_tone="calm",
        )],
    )
    existing = AudioAssets(
        scenes=[SceneAudio(
            scene_id=1, native_audio_enabled=True,
            native_audio_path="old-native.mp4", native_audio_source="happyhorse",
        )],
        source_audio_enabled=True,
    )

    audio = await AudioAgent().run(
        script, GeneratedAssets(shots=[]), project_id="switch-audio",
        existing=existing, audio_mode="external_tts", native_scene_audio={},
    )

    assert audio.source_audio_enabled is False
    assert audio.scenes[0].native_audio_enabled is False
    assert audio.scenes[0].native_audio_path == ""
