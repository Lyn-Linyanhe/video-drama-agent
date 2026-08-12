import pytest
import asyncio

from src.api_clients.seedance import SeedanceVideoClient
import src.api_clients.seedance as seedance_mod
from src.services.job_manager import JobCanceled
from src.services.generation_hooks import GenerationHooks


def test_seedance_auth_headers_require_api_key():
    client = SeedanceVideoClient(api_key=" ")

    with pytest.raises(RuntimeError, match="Seedance Ark API Key is not configured"):
        client._auth_headers()


def test_seedance_resolution_normalization_preserves_4k():
    assert SeedanceVideoClient(model="doubao-seedance-2-5-260628", resolution="4k").resolution == "4k"


def test_seedance_reference_encoder_preserves_small_png(tmp_path):
    from PIL import Image

    image_path = tmp_path / "reference.png"
    Image.new("RGB", (1600, 900), color=(80, 120, 160)).save(image_path)

    data_uri = SeedanceVideoClient._encode_image_to_data_uri(str(image_path))

    assert data_uri.startswith("data:image/png;base64,")


def test_seedance_extract_video_url_from_content_dict_string():
    client = SeedanceVideoClient(api_key="ark-test")

    url = client._extract_video_url({
        "status": "succeeded",
        "content": {"video_url": "https://example.com/video.mp4"},
    })

    assert url == "https://example.com/video.mp4"


def test_seedance_extract_video_url_from_content_dict_object():
    client = SeedanceVideoClient(api_key="ark-test")

    url = client._extract_video_url({
        "status": "succeeded",
        "content": {"video_url": {"url": "https://example.com/video.mp4"}},
    })

    assert url == "https://example.com/video.mp4"


def test_seedance_extract_last_frame_url_from_content_items():
    client = SeedanceVideoClient(api_key="ark-test")

    url = client._extract_last_frame_url({
        "status": "succeeded",
        "content": [{
            "type": "image_url",
            "role": "last_frame",
            "image_url": {"url": "https://example.com/tail.jpg"},
        }],
    })

    assert url == "https://example.com/tail.jpg"


def test_seedance_extract_last_frame_ignores_first_frame_content_item():
    client = SeedanceVideoClient(api_key="ark-test")

    url = client._extract_last_frame_url({
        "status": "succeeded",
        "content": [
            {
                "type": "image_url",
                "role": "first_frame",
                "image_url": {"url": "https://example.com/first.jpg"},
            },
            {
                "type": "image_url",
                "role": "last_frame",
                "image_url": {"url": "https://example.com/last.jpg"},
            },
        ],
    })

    assert url == "https://example.com/last.jpg"


def test_seedance_reference_planner_reports_mode_and_dropped_images():
    client = SeedanceVideoClient(model="doubao-seedance-2-0-fast-260128")
    plan = client.plan_reference_inputs(
        ["tail.jpg", "character-front.jpg", "scene.jpg"],
        reference_mode="first_frame",
    )

    assert plan["mode"] == "first_frame"
    assert plan["items"] == [{"path": "tail.jpg", "role": "first_frame"}]
    assert plan["dropped_references"] == [
        {"path": "character-front.jpg", "reason": "first_frame mode cannot mix reference_image"},
        {"path": "scene.jpg", "reason": "first_frame mode cannot mix reference_image"},
    ]


def test_seedance_reference_planner_caps_multi_reference_images():
    client = SeedanceVideoClient(model="doubao-seedance-2-0-fast-260128")
    paths = [f"reference-{index}.jpg" for index in range(12)]
    plan = client.plan_reference_inputs(paths, reference_mode="multi_reference")

    assert plan["mode"] == "multi_reference"
    assert len(plan["items"]) == 9
    assert len(plan["dropped_references"]) == 3
    assert all(item["role"] == "reference_image" for item in plan["items"])


@pytest.mark.asyncio
async def test_seedance_first_last_request_uses_official_roles_and_flags(monkeypatch, tmp_path):
    first = tmp_path / "first.jpg"
    last = tmp_path / "last.jpg"
    first.write_bytes(b"first")
    last.write_bytes(b"last")
    captured = {}

    class FakeResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {"id": "task-first-last"}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json, headers):
            captured.update(body=json)
            return FakeResponse()

    monkeypatch.setattr(seedance_mod.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(SeedanceVideoClient, "_encode_image_to_data_uri", staticmethod(lambda path: f"data:image/jpeg;base64,{path}"))

    client = SeedanceVideoClient(api_key="ark-test")
    task_id = await client._submit_task(
        prompt="test",
        negative_prompt="",
        duration_sec=5,
        seed=None,
        aspect_ratio="9:16",
        image_references=[],
        reference_role="reference_image",
        reference_items=[
            {"path": str(first), "role": "first_frame"},
            {"path": str(last), "role": "last_frame"},
        ],
        return_last_frame=True,
    )

    assert task_id == "task-first-last"
    assert [item["role"] for item in captured["body"]["content"][1:]] == ["first_frame", "last_frame"]
    assert captured["body"]["return_last_frame"] is True
    assert captured["body"]["generate_audio"] is False


@pytest.mark.asyncio
async def test_seedance_result_keeps_provider_last_frame(monkeypatch):
    client = SeedanceVideoClient(api_key="ark-test")
    monkeypatch.setattr(client, "_submit_task", lambda **_kwargs: _async_value("task-tail"))
    monkeypatch.setattr(client, "_poll_task", lambda _task_id: _async_value({
        "video_url": "https://example.com/video.mp4",
        "last_frame_url": "https://example.com/tail.jpg",
    }))
    monkeypatch.setattr(client, "_download_video", lambda _url, _task_id: _async_value("video.mp4"))
    monkeypatch.setattr(client, "_download_last_frame", lambda _url, _task_id: _async_value("tail.jpg"))

    result = await client.generate_video(prompt="test", return_last_frame=True)

    assert result.success is True
    assert result.last_frame_url == "https://example.com/tail.jpg"
    assert result.last_frame_path == "tail.jpg"


@pytest.mark.asyncio
async def test_seedance_cancel_task_calls_official_delete(monkeypatch):
    captured = {}

    class FakeResponse:
        status_code = 204
        text = ""

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def delete(self, url, headers):
            captured.update(url=url, headers=headers)
            return FakeResponse()

    monkeypatch.setattr(seedance_mod.httpx, "AsyncClient", FakeAsyncClient)
    client = SeedanceVideoClient(api_key="ark-test")

    result = await client.cancel_task("task-cancel")

    assert result["canceled"] is True
    assert captured["url"].endswith("/tasks/task-cancel")
    assert captured["headers"]["Authorization"] == "Bearer ark-test"


@pytest.mark.asyncio
async def test_seedance_poll_cancellation_deletes_provider_task(monkeypatch):
    client = SeedanceVideoClient(api_key="ark-test")
    canceled = []
    monkeypatch.setattr(client, "_submit_task", lambda **_kwargs: _async_value("task-cancel"))
    monkeypatch.setattr(client, "_poll_task", lambda _task_id: _raise_cancelled())
    monkeypatch.setattr(client, "cancel_task", lambda task_id: _record_cancel(canceled, task_id))

    with pytest.raises(asyncio.CancelledError):
        await client.generate_video(prompt="test")

    assert canceled == ["task-cancel"]


@pytest.mark.asyncio
async def test_seedance_hook_cancellation_after_submit_deletes_provider_task(monkeypatch):
    client = SeedanceVideoClient(api_key="ark-test")
    canceled = []
    monkeypatch.setattr(client, "_submit_task", lambda **_kwargs: _async_value("task-hook-cancel"))
    monkeypatch.setattr(client, "cancel_task", lambda task_id: _record_cancel(canceled, task_id))

    hooks = GenerationHooks(cancel_callback=lambda: (_ for _ in ()).throw(JobCanceled("cancel")))
    monkeypatch.setattr(seedance_mod, "get_generation_hooks", lambda: hooks)

    with pytest.raises(JobCanceled):
        await client.generate_video(prompt="test")

    assert canceled == ["task-hook-cancel"]


@pytest.mark.asyncio
async def test_seedance_image_reference_only_uses_first_frame_role(monkeypatch, tmp_path):
    image = tmp_path / "reference.jpg"
    image.write_bytes(b"fake-image")
    captured = {}

    class FakeResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {"id": "task-123"}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json, headers):
            captured.update(url=url, body=json, headers=headers)
            return FakeResponse()

    monkeypatch.setattr(seedance_mod.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(SeedanceVideoClient, "_encode_image_to_data_uri", staticmethod(lambda _path: "data:image/jpeg;base64,abc"))

    client = SeedanceVideoClient(api_key="ark-test")
    task_id = await client._submit_task(
        prompt="test",
        negative_prompt="",
        duration_sec=5,
        seed=None,
        aspect_ratio="9:16",
        image_references=[str(image)],
        reference_role="first_frame",
    )

    assert task_id == "task-123"
    image_content = captured["body"]["content"][1]
    assert image_content["role"] == "first_frame"
    assert image_content["image_url"] == {"url": "data:image/jpeg;base64,abc"}
    assert "role" not in image_content["image_url"]


@pytest.mark.asyncio
async def test_seedance_multi_references_use_reference_image_role(monkeypatch, tmp_path):
    image_a = tmp_path / "a.jpg"
    image_b = tmp_path / "b.jpg"
    image_a.write_bytes(b"a")
    image_b.write_bytes(b"b")
    captured = {}

    class FakeResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {"id": "task-123"}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json, headers):
            captured.update(body=json)
            return FakeResponse()

    monkeypatch.setattr(seedance_mod.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(SeedanceVideoClient, "_encode_image_to_data_uri", staticmethod(lambda path: f"data:image/jpeg;base64,{path}"))
    monkeypatch.setattr(SeedanceVideoClient, "_poll_task", lambda self, task_id: _async_value("https://example.com/video.mp4"))
    monkeypatch.setattr(SeedanceVideoClient, "_download_video", lambda self, url, task_id: _async_value("local.mp4"))

    client = SeedanceVideoClient(api_key="ark-test")
    result = await client.generate_video(
        prompt="test",
        image_references=[str(image_a), str(image_b)],
    )

    assert result.success is True
    image_contents = captured["body"]["content"][1:]
    assert [item["role"] for item in image_contents] == ["reference_image", "reference_image"]


@pytest.mark.asyncio
async def test_seedance_continuity_frame_is_not_mixed_with_reference_images(monkeypatch, tmp_path):
    continuity_dir = tmp_path / "assets" / "videos" / "continuity"
    continuity_dir.mkdir(parents=True)
    tail = continuity_dir / "shot_001_version_tail.jpg"
    character = tmp_path / "character.jpg"
    tail.write_bytes(b"tail")
    character.write_bytes(b"character")
    captured = {}

    class FakeResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {"id": "task-123"}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json, headers):
            captured.update(body=json)
            return FakeResponse()

    monkeypatch.setattr(seedance_mod.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(SeedanceVideoClient, "_encode_image_to_data_uri", staticmethod(lambda path: f"data:image/jpeg;base64,{path}"))
    monkeypatch.setattr(SeedanceVideoClient, "_poll_task", lambda self, task_id: _async_value("https://example.com/video.mp4"))
    monkeypatch.setattr(SeedanceVideoClient, "_download_video", lambda self, url, task_id: _async_value("local.mp4"))

    client = SeedanceVideoClient(api_key="ark-test")
    result = await client.generate_video(
        prompt="test",
        image_references=[str(tail), str(character)],
    )

    assert result.success is True
    image_contents = captured["body"]["content"][1:]
    assert len(image_contents) == 1
    assert image_contents[0]["role"] == "first_frame"


@pytest.mark.asyncio
async def test_seedance_sensitive_input_fails_without_text_only_retry(monkeypatch, tmp_path):
    image = tmp_path / "reference.jpg"
    image.write_bytes(b"reference")
    captured_bodies = []

    class FakeResponse:
        def __init__(self, status_code, text, data=None):
            self.status_code = status_code
            self.text = text
            self._data = data or {}

        def json(self):
            return self._data

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json, headers):
            captured_bodies.append(json)
            if len(captured_bodies) == 1:
                return FakeResponse(
                    400,
                    '{"error":{"code":"InputImageSensitiveContentDetected.PrivacyInformation",'
                    '"message":"The request failed because the input image content[1] may contain real person"}}',
                )
            return FakeResponse(200, "{}", {"id": "task-123"})

    monkeypatch.setattr(seedance_mod.httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(SeedanceVideoClient, "_encode_image_to_data_uri", staticmethod(lambda path: f"data:image/jpeg;base64,{path}"))
    monkeypatch.setattr(SeedanceVideoClient, "_poll_task", lambda self, task_id: _async_value("https://example.com/video.mp4"))
    monkeypatch.setattr(SeedanceVideoClient, "_download_video", lambda self, url, task_id: _async_value("local.mp4"))

    client = SeedanceVideoClient(api_key="ark-test")
    result = await client.generate_video(
        prompt="test",
        image_references=[str(image)],
    )

    assert result.success is False
    assert len(captured_bodies) == 1
    assert "InputImageSensitiveContentDetected" in result.error_message


async def _async_value(value):
    return value


async def _raise_cancelled():
    raise asyncio.CancelledError()


async def _record_cancel(canceled, task_id):
    canceled.append(task_id)
    return {"canceled": True}
