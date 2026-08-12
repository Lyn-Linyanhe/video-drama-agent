import pytest

from src.api_clients.seedance import SeedanceVideoClient
import src.api_clients.seedance as seedance_mod


@pytest.mark.asyncio
async def test_seedance_request_enables_native_audio_and_returns_source(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 200
        text = "{}"

        def json(self):
            captured["body"] = self._body
            return {"id": "native-task"}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json, headers):
            captured["body"] = json
            response = FakeResponse()
            response._body = json
            return response

    monkeypatch.setattr(seedance_mod.httpx, "AsyncClient", FakeAsyncClient)
    client = SeedanceVideoClient(api_key="ark-test")
    monkeypatch.setattr(client, "_poll_task", lambda _task_id: _async_value({
        "video_url": "https://example.com/video.mp4",
        "audio_url": "https://example.com/audio.mp3",
    }))
    monkeypatch.setattr(client, "_download_video", lambda _url, _task_id: _async_value("video.mp4"))
    monkeypatch.setattr(client, "_download_audio", lambda _url, _task_id: _async_value("audio.mp3"))

    result = await client.generate_video("scene", generate_audio=True)

    assert captured["body"]["generate_audio"] is True
    assert result.audio_source == "native"
    assert result.native_audio_enabled is True
    assert result.native_audio_path == "audio.mp3"
    assert result.audio_present is True


async def _async_value(value):
    return value
