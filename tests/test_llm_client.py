import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from src.utils.llm import LLMClient, LLMRequestError


class StructuredExample(BaseModel):
    title: str


def test_deepseek_requests_use_fixed_temperature_point_two_five():
    client = LLMClient.__new__(LLMClient)
    client.provider = "deepseek"

    assert client._provider_temperature(0.8) == 0.25
    assert client._provider_temperature(0.2) == 0.25


class FakeStream:
    def __init__(self, text: str):
        self.text = text

    def __aiter__(self):
        self._index = 0
        return self

    async def __anext__(self):
        if self._index >= len(self.text):
            raise StopAsyncIteration
        char = self.text[self._index]
        self._index += 1
        return SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content=char))]
        )


@pytest.mark.asyncio
async def test_deepseek_structured_output_uses_plain_json_without_tool_choice():
    captured = []

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.append(kwargs)
            if kwargs.get("stream"):
                return FakeStream("ok")
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"title":"ok"}')
                    )
                ]
            )

    client = LLMClient.__new__(LLMClient)
    client.provider = "deepseek"
    client.model = "deepseek-v4-pro"
    client._openai = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    client._anthropic = None
    telemetry = {}

    result = await client.generate_structured(
        system_prompt="system",
        user_prompt="user",
        output_schema=StructuredExample,
        telemetry=telemetry,
    )

    assert result.title == "ok"
    assert telemetry["fallback_used"] is True
    assert "tools" not in captured[0]
    assert "tool_choice" not in captured[0]
    assert captured[0]["temperature"] == 0.25


@pytest.mark.asyncio
async def test_kimi_text_generation_uses_required_temperature_one():
    captured = []

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.append(kwargs)
            if kwargs.get("stream"):
                return FakeStream("ok")
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="ok")
                    )
                ]
            )

    client = LLMClient.__new__(LLMClient)
    client.provider = "kimi"
    client.model = "kimi-k2.6"
    client._openai = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    client._anthropic = None
    client._ensure_client = lambda: None

    result = await client.generate_text("system", "user", temperature=0.2)

    assert result == "ok"
    assert captured[0]["temperature"] == 1


@pytest.mark.asyncio
async def test_kimi_structured_output_uses_plain_json_without_tool_choice():
    captured = []

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.append(kwargs)
            if kwargs.get("stream"):
                return FakeStream('{"title":"ok"}')
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content='{"title":"ok"}')
                    )
                ]
            )

    client = LLMClient.__new__(LLMClient)
    client.provider = "kimi"
    client.model = "kimi/kimi-k2.6"
    client._openai = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions())
    )
    client._anthropic = None
    telemetry = {}

    result = await client.generate_structured(
        system_prompt="system",
        user_prompt="user",
        output_schema=StructuredExample,
        telemetry=telemetry,
    )

    assert result.title == "ok"
    assert telemetry["fallback_used"] is True
    assert "tools" not in captured[0]
    assert "tool_choice" not in captured[0]
    assert captured[0]["temperature"] == 1
    assert captured[0]["stream"] is True


def test_long_llm_phases_use_a_safe_timeout_floor(monkeypatch):
    import src.utils.llm as llm_module

    monkeypatch.setattr(llm_module, "LLM_TIMEOUT", 60)
    client = LLMClient.__new__(LLMClient)

    assert client._request_timeout("script.analyze") == 300
    assert client._request_timeout("script.visualize") == 300


@pytest.mark.asyncio
async def test_llm_timeout_retries_once_and_reports_phase_context(monkeypatch):
    import src.utils.llm as llm_module

    monkeypatch.setattr(llm_module, "LLM_TIMEOUT", 60)

    class FakeCompletions:
        def __init__(self):
            self.calls = []

        async def create(self, **kwargs):
            self.calls.append(kwargs)
            raise TimeoutError("Request timed out.")

    completions = FakeCompletions()
    client = LLMClient.__new__(LLMClient)
    client.provider = "deepseek"
    client.model = "deepseek-v4-flash"
    client._openai = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    client._anthropic = None
    client._ensure_client = lambda: None

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(llm_module.asyncio, "sleep", no_sleep)

    with pytest.raises(Exception) as caught:
        await client.generate_text("system", "user", phase="script.analyze")

    assert len(completions.calls) == 2
    assert all(call["timeout"] == 300 for call in completions.calls)
    assert caught.value.__class__.__name__ == "LLMRequestError"
    assert caught.value.phase == "script.analyze"
    assert caught.value.provider == "deepseek"
    assert caught.value.model == "deepseek-v4-flash"
    assert caught.value.timeout_sec == 300


@pytest.mark.asyncio
async def test_completion_enforces_deadline_when_sdk_ignores_timeout(monkeypatch):
    """The application deadline must hold even when an SDK call never returns."""
    import src.utils.llm as llm_module

    class HangingCompletions:
        async def create(self, **_kwargs):
            await asyncio.Event().wait()

    client = LLMClient.__new__(LLMClient)
    client.provider = "deepseek"
    client.model = "deepseek-v4-flash"
    client._openai = SimpleNamespace(chat=SimpleNamespace(completions=HangingCompletions()))
    client._anthropic = None
    client._request_timeout = lambda _phase: 0.01
    monkeypatch.setattr(llm_module, "MAX_LLM_TRANSPORT_RETRIES", 0)

    with pytest.raises(LLMRequestError) as caught:
        await asyncio.wait_for(
            client._request_completion({"model": "test"}, phase="script.visualize"),
            timeout=0.1,
        )

    assert caught.value.timed_out is True
    assert caught.value.phase == "script.visualize"
    assert caught.value.timeout_sec == 0.01


def test_openai_client_disables_sdk_retries(monkeypatch):
    import openai
    import src.config as config
    import src.utils.llm as llm_module

    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(openai, "AsyncOpenAI", FakeOpenAI)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(config, "OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setattr(llm_module, "OPENAI_API_KEY", "test-key")
    monkeypatch.setattr(llm_module, "OPENAI_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setattr(llm_module, "LLM_TIMEOUT", 60)

    client = LLMClient.__new__(LLMClient)
    client.provider = "deepseek"
    client.model = "deepseek-v4-flash"
    client._openai = None
    client._anthropic = None
    client._init_openai()

    assert captured["max_retries"] == 0
    assert captured["timeout"] == 120


def test_reasoning_effort_kwargs_deepseek_enabled(monkeypatch):
    from src.utils import llm as llm_mod

    monkeypatch.setattr(llm_mod, "LLM_REASONING_EFFORT", "max")
    assert llm_mod._reasoning_effort_kwargs("deepseek") == {"reasoning_effort": "max"}
    monkeypatch.setattr(llm_mod, "LLM_REASONING_EFFORT", "high")
    assert llm_mod._reasoning_effort_kwargs("deepseek") == {"reasoning_effort": "high"}


def test_reasoning_effort_kwargs_disabled_or_other_provider(monkeypatch):
    from src.utils import llm as llm_mod

    monkeypatch.setattr(llm_mod, "LLM_REASONING_EFFORT", "off")
    assert llm_mod._reasoning_effort_kwargs("deepseek") == {}
    monkeypatch.setattr(llm_mod, "LLM_REASONING_EFFORT", "high")
    assert llm_mod._reasoning_effort_kwargs("kimi") == {}
    monkeypatch.setattr(llm_mod, "LLM_REASONING_EFFORT", "")
    assert llm_mod._reasoning_effort_kwargs("deepseek") == {}


@pytest.mark.asyncio
async def test_generate_text_sends_reasoning_effort_for_deepseek(monkeypatch):
    from src.utils import llm as llm_mod

    monkeypatch.setattr(llm_mod, "LLM_REASONING_EFFORT", "high")
    client = LLMClient.__new__(LLMClient)
    client.provider = "deepseek"
    client.model = "deepseek-v4-flash"
    client._openai = None
    captured = {}

    class FakeCreate:
        async def __call__(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))])

    class FakeCompletions:
        def __init__(self):
            self.create = FakeCreate()

    class FakeOpenAI:
        def __init__(self):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setattr(client, "_openai", FakeOpenAI())
    out = await client.generate_text("sys", "user", temperature=0)
    assert out == "ok"
    assert captured.get("reasoning_effort") == "high"
