import asyncio
import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import api_server
from src.api import context as api_context
from src.api.routes.jobs import stream_project_job_events
import src.utils.project_store as project_store_module
from src.services.job_manager import JobManager
from src.services.job_store import JobStore
from src.utils.project_store import ProjectStore
from tests.test_asset_generation_api import _state
from src.schemas.script import VisualCharacterAsset


def _client(tmp_path: Path, monkeypatch, slow: bool = False):
    monkeypatch.setattr(project_store_module, "OUTPUT_DIR", tmp_path)
    project_store = ProjectStore()
    project_id = project_store.create_project("任务 API")
    state = _state(project_id)
    project_store.set_script(project_id, state.script.model_dump())
    project_store.set_visual_design(project_id, state.visual_design.model_dump())
    project_store.set_state_json(project_id, api_context._state_to_json(state))

    store = JobStore(tmp_path / "jobs.sqlite3", recover=False)
    manager = JobManager(store)

    async def handler(context, payload):
        context.progress("waiting_provider", 1, 2, "处理中")
        if slow:
            for _ in range(100):
                context.check_cancelled()
                await asyncio.sleep(0.01)
        return {"ok": True, "payload": payload}

    for job_type in (
        "script.generate", "script.analyze", "asset.generate",
        "video.generate", "audio.generate", "final.assemble",
    ):
        manager.register(job_type, handler)
    monkeypatch.setattr(api_context.project_store, "_target", project_store)
    monkeypatch.setattr(api_context.job_store, "_target", store)
    monkeypatch.setattr(api_context.job_manager, "_target", manager)
    return TestClient(api_server.app), project_id


def _wait_for_terminal(client: TestClient, project_id: str, job_id: str):
    for _ in range(100):
        job = client.get(f"/api/projects/{project_id}/jobs/{job_id}").json()["job"]
        if job["state"] in {"succeeded", "failed", "canceled", "interrupted"}:
            return job
        time.sleep(0.01)
    raise AssertionError("job did not finish")


def test_create_list_and_complete_project_job(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)
    response = client.post(
        f"/api/projects/{project_id}/jobs",
        json={"job_type": "audio.generate", "target_type": "project", "payload": {"voice": "auto"}},
    )
    assert response.status_code == 202
    job_id = response.json()["job"]["job_id"]
    finished = _wait_for_terminal(client, project_id, job_id)
    assert finished["state"] == "succeeded"
    assert finished["result"]["ok"] is True
    listing = client.get(f"/api/projects/{project_id}/jobs").json()["jobs"]
    assert [item["job_id"] for item in listing] == [job_id]
    assert "request" not in listing[0]


def test_public_job_includes_payload_without_exposing_internal_request(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)
    job = api_context.job_store.create_job(
        project_id,
        "asset.generate",
        "scene",
        "loc:scene_reverse",
        {"asset_type": "scene", "asset_id": "loc", "state_id": "rain"},
    )

    public = client.get(f"/api/projects/{project_id}/jobs/{job['job_id']}").json()["job"]

    assert public["payload"]["state_id"] == "rain"
    assert "request" not in public


def test_cancel_job_and_reject_cross_project_access(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch, slow=True)
    response = client.post(
        f"/api/projects/{project_id}/jobs",
        json={"job_type": "audio.generate"},
    )
    job_id = response.json()["job"]["job_id"]
    canceled = client.post(f"/api/projects/{project_id}/jobs/{job_id}/cancel")
    assert canceled.status_code == 200
    assert _wait_for_terminal(client, project_id, job_id)["state"] == "canceled"

    other_id = api_context.project_store.create_project("其他项目")
    assert client.get(f"/api/projects/{other_id}/jobs/{job_id}").status_code == 404
    assert client.post(f"/api/projects/{other_id}/jobs/{job_id}/cancel").status_code == 404


def test_video_batch_cancel_is_project_scoped(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch, slow=True)
    response = client.post(
        f"/api/projects/{project_id}/jobs",
        json={"job_type": "video.generate", "target_type": "shot", "payload": {}},
    )
    assert response.status_code == 202
    batch = response.json()["batch"]
    assert batch["total"] == 2
    canceled = client.post(
        f"/api/projects/{project_id}/job-batches/{batch['batch_id']}/cancel"
    )
    assert canceled.status_code == 200
    assert canceled.json()["batch"]["active"] <= 2
    for job in batch["jobs"]:
        assert _wait_for_terminal(client, project_id, job["job_id"])["state"] == "canceled"


def test_duplicate_active_submission_returns_existing_job(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch, slow=True)
    payload = {"job_type": "audio.generate", "target_type": "project", "target_id": "audio"}
    first = api_context.job_store.create_job(
        project_id, "audio.generate", "project", "audio", {},
    )
    second = client.post(f"/api/projects/{project_id}/jobs", json=payload).json()["job"]
    assert second["job_id"] == first["job_id"]
    client.post(f"/api/projects/{project_id}/jobs/{first['job_id']}/cancel")


def test_character_four_view_job_submission_creates_all_required_jobs(tmp_path, monkeypatch):
    client, project_id = _client(tmp_path, monkeypatch)
    state = api_context._load_project_state(project_id)
    state.script.asset_manifest.characters.append(VisualCharacterAsset(
        asset_id="hero", name="主角", appearance_mode="on_screen", description="测试角色",
    ))
    api_context._save_project_state(state)

    response = client.post(
        f"/api/projects/{project_id}/jobs",
        json={
            "job_type": "asset.generate",
            "target_type": "character",
            "target_id": "主角",
            "payload": {"asset_type": "character", "asset_id": "主角", "regenerate": True},
        },
    )

    assert response.status_code == 202
    jobs = response.json()["batch"]["jobs"]
    assert len(jobs) == 4
    assert {job["target_id"].split(":", 1)[1] for job in jobs} == {
        "front_half", "front_full", "left_profile", "back_full",
    }


@pytest.mark.parametrize(
    "job_type,target_type",
    [
        ("character.reference.group", "character"),
        ("scene.reference.group", "scene"),
        ("character.reference.edit", "character"),
        ("scene.reference.edit", "scene"),
    ],
)
def test_reference_job_types_are_not_rejected_by_request_schema(
    tmp_path, monkeypatch, job_type, target_type,
):
    client, project_id = _client(tmp_path, monkeypatch)
    state = api_context._load_project_state(project_id)
    state.script.asset_manifest.characters.append(VisualCharacterAsset(
        asset_id="hero", name="hero", appearance_mode="on_screen", description="test character",
    ))
    api_context._save_project_state(state)
    target_id = "hero"
    if target_type == "scene":
        target_id = state.script.asset_manifest.locations[0].location_id
    payload = {"asset_id": target_id}
    if job_type == "character.reference.edit":
        payload["view_type"] = "front_half"
    if job_type == "scene.reference.edit":
        payload.update({"view_type": "scene_reverse", "prompt": "增加墙面水痕"})

    response = client.post(
        f"/api/projects/{project_id}/jobs",
        json={"job_type": job_type, "target_type": target_type, "target_id": target_id, "payload": payload},
    )

    # Mock intentionally does not support reference-based generation. The
    # important contract is that FastAPI reaches capability validation instead
    # of rejecting a valid persistent job type with a 422 schema error.
    assert response.status_code == 409


def test_asset_job_freezes_the_project_image_policy(tmp_path, monkeypatch):
    import src.config as config

    monkeypatch.setattr(config, "HAPPYHORSE_API_KEY", "test-key")
    monkeypatch.setattr(config, "HAPPYHORSE_BASE_URL", "https://dashscope.aliyuncs.com")
    client, project_id = _client(tmp_path, monkeypatch)
    state = api_context._load_project_state(project_id)
    state.image_generation_policy = {
        "provider": "wan", "model": "wan2.7-image-pro", "resolution": "2K",
    }
    api_context._save_project_state(state)

    response = client.post(
        f"/api/projects/{project_id}/jobs",
        json={
            "job_type": "asset.generate", "target_type": "prop", "target_id": "old_key",
            "payload": {"asset_type": "prop", "asset_id": "old_key"},
        },
    )

    assert response.status_code == 202
    job = api_context.job_store.get_job(project_id, response.json()["job"]["job_id"])
    assert job["request"] == {
        "asset_type": "prop", "asset_id": "old_key", "view_type": "front",
        "regenerate": False, "custom_instructions": "", "seed": job["request"]["seed"],
        "provider": "wan", "model": "wan2.7-image-pro", "resolution": "2K",
    }


@pytest.mark.asyncio
async def test_project_job_event_stream_uses_durable_cursor(tmp_path, monkeypatch):
    _, project_id = _client(tmp_path, monkeypatch)
    job = api_context.job_store.create_job(
        project_id,
        "asset.generate",
        "scene",
        "scene-1",
        {"asset_type": "scene", "asset_id": "scene-1", "state_id": "rain"},
    )

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    request = Request({
        "type": "http", "method": "GET", "path": "/", "headers": [],
        "query_string": b"after_event_id=0", "client": ("testclient", 123), "server": ("testserver", 80),
        "scheme": "http", "http_version": "1.1",
    }, receive)
    response = await stream_project_job_events(request, project_id)
    first = await anext(response.body_iterator)
    await response.body_iterator.aclose()

    event_id = int(first.split("\n", 1)[0].removeprefix("id: "))
    payload = json.loads(first.split("data: ", 1)[1])
    assert "event: job" in first
    assert event_id == payload["event"]["event_id"]
    assert payload["job"]["job_id"] == job["job_id"]
    assert payload["job"]["payload"]["state_id"] == "rain"
