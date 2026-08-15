import json
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

import api_server
from src.api.routes import settings as settings_routes


def _settings_client(tmp_path: Path, monkeypatch, data: dict | None = None):
    settings_file = tmp_path / "settings_ui.json"
    if data is not None:
        settings_file.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(settings_routes, "SETTINGS_FILE", settings_file)
    return TestClient(api_server.app), settings_file


def test_deepseek_model_defaults_to_flash_and_migrates_legacy_names():
    normalize = settings_routes._normalize_llm_model

    assert normalize("deepseek", "") == "deepseek-v4-flash"
    assert normalize("deepseek", "deepseek-chat") == "deepseek-v4-flash"
    assert normalize("deepseek", "deepseek-reasoner") == "deepseek-v4-pro"
    assert normalize("deepseek", "deepseek-v4-flash") == "deepseek-v4-flash"
    assert normalize("deepseek", "deepseek-v4-pro") == "deepseek-v4-pro"


def test_llm_timeout_profile_is_normalized_for_long_script_stages():
    from src.api.routes.settings import _normalize_llm_timeout

    assert _normalize_llm_timeout("60") == "300"
    assert _normalize_llm_timeout("600") == "600"


def test_deepseek_profile_temperature_is_normalized_to_point_two_five():
    profile = settings_routes._normalize_audio_profile({
        "llm_provider": "deepseek",
        "llm_temperature": "0.7",
    })

    assert profile["llm_temperature"] == "0.25"


def test_reload_llm_agents_replaces_stale_clients(monkeypatch):
    import src.api.routes.settings as settings_routes

    old_script = settings_routes.script_agent.target
    old_review = settings_routes.review_agent.target
    created = []

    class FakeAgent:
        def __init__(self):
            created.append(self)

    monkeypatch.setattr(settings_routes, "ScriptAgent", FakeAgent)
    monkeypatch.setattr(settings_routes, "ReviewAgent", FakeAgent)
    try:
        settings_routes._reload_llm_agents()
        assert settings_routes.script_agent.target is created[0]
        assert settings_routes.review_agent.target is created[1]
    finally:
        settings_routes.script_agent.set(old_script)
        settings_routes.review_agent.set(old_review)


def test_settings_reads_and_exports_never_expose_secrets(tmp_path, monkeypatch):
    profile = settings_routes.SettingsRequest(
        profile_name="production",
        llm_key="test-llm-secret",
        kimi_prompt_review_api_key="test-kimi-review-secret",
        kling_api_key="test-kling-secret",
        image_gen_key="test-image-secret",
    ).model_dump(exclude={"clear_secret_fields"})
    client, _ = _settings_client(
        tmp_path,
        monkeypatch,
        {"profiles": {"production": profile}, "active_profile": "production"},
    )

    listing = client.get("/api/settings").json()
    public = listing["profiles"]["production"]
    assert public["llm_key"] == ""
    assert public["kimi_prompt_review_api_key"] == ""
    assert public["kling_api_key"] == ""
    assert public["image_gen_key"] == ""
    assert public["_secret_configured"]["llm_key"] is True
    assert public["_secret_configured"]["kimi_prompt_review_api_key"] is True

    detail = client.get("/api/settings/production").json()
    assert detail["llm_key"] == ""
    exported = client.get("/api/settings/production/export").text
    assert "test-llm-secret" not in exported
    assert "test-kimi-review-secret" not in exported
    assert "test-kling-secret" not in exported
    assert "test-image-secret" not in exported


def test_blank_save_preserves_secret_and_explicit_clear_removes_it(tmp_path, monkeypatch):
    profile = settings_routes.SettingsRequest(
        profile_name="production",
        llm_provider="deepseek",
        llm_model="deepseek-chat",
        llm_key="existing-secret",
    ).model_dump(exclude={"clear_secret_fields"})
    client, settings_file = _settings_client(
        tmp_path,
        monkeypatch,
        {"profiles": {"production": profile}, "active_profile": "production"},
    )
    env_calls = []
    monkeypatch.setattr(settings_routes, "_write_env", lambda **kwargs: env_calls.append(kwargs))
    monkeypatch.setattr(settings_routes, "_reload_env_and_config", lambda: None)

    response = client.post(
        "/api/settings",
        json={"profile_name": "production", "llm_provider": "deepseek", "llm_model": "deepseek-chat", "llm_key": ""},
    )
    assert response.status_code == 200
    saved = json.loads(settings_file.read_text(encoding="utf-8"))["profiles"]["production"]
    assert saved["llm_key"] == "existing-secret"
    assert saved["llm_model"] == "deepseek-v4-flash"
    assert env_calls[-1]["llm_key"] == "existing-secret"
    assert env_calls[-1]["llm_model"] == "deepseek-v4-flash"
    assert response.json()["profile"]["llm_key"] == ""
    assert response.json()["profile"]["llm_model"] == "deepseek-v4-flash"

    response = client.post(
        "/api/settings",
        json={
            "profile_name": "production",
            "llm_provider": "deepseek",
            "llm_model": "deepseek-chat",
            "llm_key": "",
            "clear_secret_fields": ["llm_key"],
        },
    )
    assert response.status_code == 200
    saved = json.loads(settings_file.read_text(encoding="utf-8"))["profiles"]["production"]
    assert saved["llm_key"] == ""


def test_deepseek_primary_and_kimi_review_credentials_are_saved_independently(tmp_path, monkeypatch):
    client, settings_file = _settings_client(tmp_path, monkeypatch)
    env_calls = []
    monkeypatch.setattr(settings_routes, "_write_env", lambda **kwargs: env_calls.append(kwargs))
    monkeypatch.setattr(settings_routes, "_reload_env_and_config", lambda: None)

    response = client.post(
        "/api/settings",
        json={
            "profile_name": "production",
            "llm_provider": "deepseek",
            "llm_model": "deepseek-v4-flash",
            "llm_key": "deepseek-secret",
            "kimi_prompt_review_enabled": True,
            "kimi_prompt_review_api_key": "kimi-review-secret",
            "kimi_prompt_review_model": "kimi-k2.6",
            "kimi_prompt_review_base_url": "https://api.moonshot.cn/v1",
        },
    )

    assert response.status_code == 200
    saved = json.loads(settings_file.read_text(encoding="utf-8"))["profiles"]["production"]
    assert saved["llm_provider"] == "deepseek"
    assert saved["llm_key"] == "deepseek-secret"
    assert saved["kimi_prompt_review_api_key"] == "kimi-review-secret"
    assert env_calls[-1]["llm_key"] == "deepseek-secret"
    assert env_calls[-1]["kimi_prompt_review_key"] == "kimi-review-secret"
    assert response.json()["profile"]["kimi_prompt_review_api_key"] == ""


def test_blank_kimi_review_key_is_preserved_and_can_be_cleared(tmp_path, monkeypatch):
    profile = settings_routes.SettingsRequest(
        profile_name="production",
        llm_provider="deepseek",
        llm_key="deepseek-secret",
        kimi_prompt_review_api_key="kimi-review-secret",
    ).model_dump(exclude={"clear_secret_fields"})
    client, settings_file = _settings_client(
        tmp_path,
        monkeypatch,
        {"profiles": {"production": profile}, "active_profile": "production"},
    )
    monkeypatch.setattr(settings_routes, "_write_env", lambda **kwargs: None)
    monkeypatch.setattr(settings_routes, "_reload_env_and_config", lambda: None)

    response = client.post("/api/settings", json={"profile_name": "production", "llm_provider": "deepseek"})
    assert response.status_code == 200
    saved = json.loads(settings_file.read_text(encoding="utf-8"))["profiles"]["production"]
    assert saved["kimi_prompt_review_api_key"] == "kimi-review-secret"

    response = client.post(
        "/api/settings",
        json={
            "profile_name": "production",
            "llm_provider": "deepseek",
            "clear_secret_fields": ["kimi_prompt_review_api_key"],
        },
    )
    assert response.status_code == 200
    saved = json.loads(settings_file.read_text(encoding="utf-8"))["profiles"]["production"]
    assert saved["kimi_prompt_review_api_key"] == ""


def test_startup_falls_back_when_active_profile_has_no_llm_key(tmp_path, monkeypatch):
    settings_file = tmp_path / "settings_ui.json"
    settings_file.write_text(json.dumps({
        "profiles": {
            "empty": {"llm_key": ""},
            "ready": {"llm_key": "usable-secret", "llm_provider": "deepseek"},
        },
        "active_profile": "empty",
    }), encoding="utf-8")
    calls = []
    monkeypatch.setattr(settings_routes, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(settings_routes, "_write_env", lambda **kwargs: calls.append(kwargs))

    settings_routes._restore_env_from_settings()

    restored = json.loads(settings_file.read_text(encoding="utf-8"))
    assert restored["active_profile"] == "ready"
    assert calls[-1]["llm_key"] == "usable-secret"


def test_connection_profile_lookup_falls_back_to_active_profile(tmp_path, monkeypatch):
    client, _ = _settings_client(
        tmp_path,
        monkeypatch,
        {
            "profiles": {
                "默认配置": {
                    "llm_key": "active-secret",
                    "happyhorse_api_key": "video-secret",
                },
            },
            "active_profile": "默认配置",
        },
    )

    assert settings_routes._profile_test_value(
        settings_routes.TestConnectionRequest(profile_name="mojibake-name"),
        "llm_key",
    ) == "active-secret"
    assert settings_routes._profile_test_value(
        settings_routes.TestConnectionRequest(profile_name=""),
        "happyhorse_api_key",
    ) == "video-secret"


def test_llm_connection_does_not_reuse_key_from_a_different_provider(tmp_path, monkeypatch):
    client, _ = _settings_client(
        tmp_path,
        monkeypatch,
        {
            "profiles": {
                "kimi-profile": {
                    "llm_provider": "kimi",
                    "llm_key": "moonshot-secret",
                },
            },
            "active_profile": "kimi-profile",
        },
    )
    monkeypatch.setenv("LLM_PROVIDER", "kimi")
    monkeypatch.setenv("OPENAI_API_KEY", "moonshot-env-secret")

    response = client.post("/api/test/llm", json={"provider": "deepseek", "model": "deepseek-v4-pro"})

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["status"] == 0
    assert "DeepSeek".lower() in response.json()["detail"].lower() or "deepseek" in response.json()["detail"].lower()


def test_switching_llm_provider_with_blank_key_does_not_preserve_old_secret(tmp_path, monkeypatch):
    profile = settings_routes.SettingsRequest(
        profile_name="production",
        llm_provider="kimi",
        llm_model="kimi-k2.6",
        llm_key="moonshot-secret",
    ).model_dump(exclude={"clear_secret_fields"})
    client, settings_file = _settings_client(
        tmp_path,
        monkeypatch,
        {"profiles": {"production": profile}, "active_profile": "production"},
    )
    env_calls = []
    monkeypatch.setattr(settings_routes, "_write_env", lambda **kwargs: env_calls.append(kwargs))
    monkeypatch.setattr(settings_routes, "_reload_env_and_config", lambda: None)

    response = client.post(
        "/api/settings",
        json={
            "profile_name": "production",
            "llm_provider": "deepseek",
            "llm_model": "deepseek-v4-pro",
            "llm_key": "",
        },
    )

    assert response.status_code == 200
    saved = json.loads(settings_file.read_text(encoding="utf-8"))["profiles"]["production"]
    assert saved["llm_provider"] == "deepseek"
    assert saved["llm_key"] == ""
    assert env_calls[-1]["llm_key"] == ""


def test_saving_legacy_audio_profile_migrates_key_and_drops_inactive_fields(tmp_path, monkeypatch):
    legacy_profile = {
        "profile_name": "legacy-audio",
        "llm_provider": "deepseek",
        "llm_model": "deepseek-chat",
        "minimax_api_key": "legacy-dashscope-key",
        "tts_provider": "azure",
        "tts_key": "unused-tts-key",
        "bgm_provider": "suno",
        "bgm_key": "unused-bgm-key",
    }
    client, settings_file = _settings_client(
        tmp_path,
        monkeypatch,
        {"profiles": {"legacy-audio": legacy_profile}, "active_profile": "legacy-audio"},
    )
    env_calls = []
    monkeypatch.setattr(settings_routes, "_write_env", lambda **kwargs: env_calls.append(kwargs))
    monkeypatch.setattr(settings_routes, "_reload_env_and_config", lambda: None)

    public = client.get("/api/settings/legacy-audio").json()
    assert public["happyhorse_api_key"] == ""
    assert public["_secret_configured"]["happyhorse_api_key"] is True
    assert "minimax_api_key" not in public
    assert "tts_provider" not in public
    assert "bgm_provider" not in public

    response = client.post(
        "/api/settings",
        json={"profile_name": "legacy-audio", "llm_provider": "deepseek", "llm_model": "deepseek-chat"},
    )
    assert response.status_code == 200
    saved = json.loads(settings_file.read_text(encoding="utf-8"))["profiles"]["legacy-audio"]
    assert saved["llm_model"] == "deepseek-v4-flash"
    assert saved["happyhorse_api_key"] == "legacy-dashscope-key"
    assert not set(settings_routes.LEGACY_AUDIO_PROFILE_FIELDS).intersection(saved)
    assert env_calls[-1]["happyhorse_key"] == "legacy-dashscope-key"
    assert env_calls[-1]["llm_model"] == "deepseek-v4-flash"
    assert "minimax_api_key" not in env_calls[-1]
    assert env_calls[-1]["tts_provider"] == "qwen3"


def test_import_legacy_audio_key_uses_active_dashscope_credential(tmp_path, monkeypatch):
    client, settings_file = _settings_client(tmp_path, monkeypatch)
    response = client.post(
        "/api/settings/import",
        json={
            "profile_name": "imported-audio",
            "minimax_api_key": "legacy-dashscope-key",
            "tts_provider": "edge",
            "bgm_provider": "mock",
        },
    )
    assert response.status_code == 200
    saved = json.loads(settings_file.read_text(encoding="utf-8"))["profiles"]["imported-audio"]
    assert saved["happyhorse_api_key"] == "legacy-dashscope-key"
    assert not set(settings_routes.LEGACY_AUDIO_PROFILE_FIELDS).intersection(saved)


def test_deepseek_connection_uses_selected_model_and_rejects_http_400(tmp_path, monkeypatch):
    client, _ = _settings_client(tmp_path, monkeypatch)
    captured = {}

    class FakeResponse:
        status_code = 400
        text = '{"error":{"message":"invalid request"}}'

        def json(self):
            return {"error": {"message": "invalid request"}}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json, headers):
            captured.update(url=url, body=json, headers=headers)
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    response = client.post(
        "/api/test/llm",
        json={
            "provider": "deepseek",
            "key": "synthetic-key",
            "model": "deepseek-reasoner",
            "base_url": "",
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["status"] == 400
    assert captured["url"] == "https://api.deepseek.com/v1/chat/completions"
    assert captured["body"]["model"] == "deepseek-v4-pro"


def test_deepseek_connection_maps_retired_default_model(tmp_path, monkeypatch):
    client, _ = _settings_client(tmp_path, monkeypatch)
    captured = {}

    class FakeResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json, headers):
            captured["body"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    response = client.post(
        "/api/test/llm",
        json={"provider": "deepseek", "key": "synthetic-key", "model": "deepseek-chat"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert captured["body"]["model"] == "deepseek-v4-flash"


def test_kimi_connection_uses_moonshot_defaults(tmp_path, monkeypatch):
    client, _ = _settings_client(tmp_path, monkeypatch)
    captured = {}

    class FakeResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {}

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

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    response = client.post(
        "/api/test/llm",
        json={"provider": "kimi", "key": "synthetic-key", "model": ""},
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert captured["url"] == "https://api.moonshot.cn/v1/chat/completions"
    assert captured["body"]["model"] == "kimi-k2.6"
    assert captured["headers"]["Authorization"] == "Bearer synthetic-key"


def test_llm_connection_falls_back_to_environment_key_when_profiles_are_empty(tmp_path, monkeypatch):
    client, _ = _settings_client(tmp_path, monkeypatch, {"profiles": {}, "active_profile": ""})
    captured = {}
    monkeypatch.setenv("OPENAI_API_KEY", "env-secret")

    class FakeResponse:
        status_code = 200
        text = "{}"

        def json(self):
            return {}

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

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    response = client.post("/api/test/llm", json={"provider": "kimi", "model": "kimi-k2.6"})

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert captured["headers"]["Authorization"] == "Bearer env-secret"


def test_write_env_treats_kimi_as_openai_compatible(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_routes, "BASE_DIR", tmp_path)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=old-anthropic-secret\n", encoding="utf-8")

    settings_routes._write_env(
        llm_provider="kimi",
        llm_model="",
        llm_key="moonshot-secret",
        llm_base="",
    )

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "LLM_PROVIDER=kimi" in env_text
    assert "LLM_MODEL=kimi-k2.6" in env_text
    assert "OPENAI_API_KEY=moonshot-secret" in env_text
    assert "OPENAI_BASE_URL=https://api.moonshot.cn/v1" in env_text
    assert "ANTHROPIC_API_KEY=" in env_text


def test_write_env_keeps_deepseek_primary_and_kimi_review_credentials_separate(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_routes, "BASE_DIR", tmp_path)

    settings_routes._write_env(
        llm_provider="deepseek",
        llm_model="deepseek-v4-flash",
        llm_key="deepseek-secret",
        kimi_prompt_review_enabled=True,
        kimi_prompt_review_key="kimi-review-secret",
    )

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "LLM_PROVIDER=deepseek" in env_text
    assert "OPENAI_API_KEY=deepseek-secret" in env_text
    assert "KIMI_PROMPT_REVIEW_ENABLED=true" in env_text
    assert "KIMI_PROMPT_REVIEW_API_KEY=kimi-review-secret" in env_text


def test_write_env_mirrors_proxy_for_https_requests(tmp_path, monkeypatch):
    monkeypatch.setattr(settings_routes, "BASE_DIR", tmp_path)

    settings_routes._write_env(http_proxy="http://127.0.0.1:7890")

    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "HTTP_PROXY=http://127.0.0.1:7890" in env_text
    assert "HTTPS_PROXY=http://127.0.0.1:7890" in env_text


def test_local_service_connection_checks_do_not_require_network(tmp_path, monkeypatch):
    client, _ = _settings_client(tmp_path, monkeypatch)

    assert client.post("/api/test/image", json={"provider": "mock"}).json()["success"] is True
    assert client.post("/api/test/video", json={"provider": "mock"}).json()["success"] is True
    assert client.post("/api/test/bgm", json={"provider": "mock"}).json()["success"] is True


def test_qwen3_connection_probe_accepts_the_documented_missing_voice_response(tmp_path, monkeypatch):
    client, _ = _settings_client(tmp_path, monkeypatch)

    class FakeResponse:
        status_code = 400
        text = '{"message":"The voice property is required."}'

        def json(self):
            return {"message": "The voice property is required."}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, json, headers):
            assert json["model"] == "qwen3-tts-flash"
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    response = client.post(
        "/api/test/tts",
        json={"provider": "qwen3", "key": "synthetic-key", "model": "qwen3-tts-flash"},
    )
    assert response.status_code == 200
    assert response.json()["success"] is True
    assert "Qwen3-TTS" in response.json()["detail"]


def test_happyhorse_connection_probe_uses_the_same_dashscope_task_path(tmp_path, monkeypatch):
    client, _ = _settings_client(tmp_path, monkeypatch)
    captured = {}

    class FakeResponse:
        status_code = 404
        text = '{"message":"task not found"}'

        def json(self):
            return {"message": "task not found"}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers):
            captured.update(url=url, headers=headers)
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    response = client.post(
        "/api/test/video",
        json={
            "provider": "happyhorse",
            "key": "synthetic-key",
            "base_url": "https://dashscope.aliyuncs.com",
            "model": "happyhorse-1.1-r2v",
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert captured["url"] == "https://dashscope.aliyuncs.com/api/v1/tasks/connection-probe"
    assert captured["headers"]["Authorization"] == "Bearer synthetic-key"


def test_seedance_connection_probe_uses_ark_model_list_and_checks_model(tmp_path, monkeypatch):
    client, _ = _settings_client(tmp_path, monkeypatch)
    captured = {}

    class FakeResponse:
        status_code = 200
        text = '{}'

        def json(self):
            return {"data": [{"id": "doubao-seedance-2-0-fast-260128"}]}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers):
            captured.update(url=url, headers=headers)
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    response = client.post(
        "/api/test/video",
        json={
            "provider": "seedance",
            "key": "synthetic-key",
            "base_url": "https://ark.cn-beijing.volces.com",
            "model": "doubao-seedance-2-0-fast-260128",
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert captured["url"] == "https://ark.cn-beijing.volces.com/api/v3/models"
    assert captured["headers"]["Authorization"] == "Bearer synthetic-key"


def test_seedance_connection_probe_reports_model_not_available(tmp_path, monkeypatch):
    client, _ = _settings_client(tmp_path, monkeypatch)

    class FakeResponse:
        status_code = 200
        text = '{}'

        def json(self):
            return {"data": [{"id": "doubao-seedance-2-0-fast-260128"}]}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    response = client.post(
        "/api/test/video",
        json={
            "provider": "seedance",
            "key": "synthetic-key",
            "base_url": "https://ark.cn-beijing.volces.com",
            "model": "doubao-seedance-2-5-260628",
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["category"] == "model"


def test_task_not_found_400_is_a_successful_authenticated_probe(tmp_path, monkeypatch):
    client, _ = _settings_client(tmp_path, monkeypatch)

    class FakeResponse:
        status_code = 400
        text = '{"message":"Task not found by id: connection-probe"}'

        def json(self):
            return {"message": "Task not found by id: connection-probe"}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers):
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    response = client.post("/api/test/image", json={"provider": "kling", "key": "synthetic-key"})

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert response.json()["category"] == "ok"


def test_wan_connection_probe_uses_dashscope_task_endpoint(tmp_path, monkeypatch):
    client, _ = _settings_client(tmp_path, monkeypatch)

    class FakeResponse:
        status_code = 404
        text = '{"message":"Task not found by id: connection-probe"}'

        def json(self):
            return {"message": "Task not found by id: connection-probe"}

    captured = {}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url, headers):
            captured["url"] = url
            captured["headers"] = headers
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    response = client.post(
        "/api/test/image",
        json={
            "provider": "wan",
            "key": "dashscope-key",
            "base_url": "https://dashscope.aliyuncs.com",
            "model": "wan2.7-image-pro",
        },
    )

    assert response.status_code == 200
    assert response.json()["success"] is True
    assert captured["url"] == "https://dashscope.aliyuncs.com/api/v1/tasks/connection-probe"
    assert captured["headers"]["Authorization"] == "Bearer dashscope-key"


def test_connection_probe_labels_network_failures_with_proxy_guidance(tmp_path, monkeypatch):
    client, _ = _settings_client(tmp_path, monkeypatch)

    class FailingAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            raise httpx.ConnectError("blocked")

        async def __aexit__(self, *args):
            return None

    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.setattr(httpx, "AsyncClient", FailingAsyncClient)
    response = client.post(
        "/api/test/llm",
        json={"provider": "deepseek", "key": "synthetic-key", "model": "deepseek-chat"},
    )

    assert response.status_code == 200
    assert response.json()["success"] is False
    assert response.json()["category"] == "network"
    assert "网络出口" in response.json()["detail"]
