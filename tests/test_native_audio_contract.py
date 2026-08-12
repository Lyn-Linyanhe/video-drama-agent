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
