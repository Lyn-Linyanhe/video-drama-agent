# -*- coding: utf-8 -*-
import json
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import BaseModel, Field


# ---------- Task 1: robust structured fallback ----------
class _Out(BaseModel):
    color_palette: str
    shots: list[int] = Field(default_factory=list)


@pytest.mark.asyncio
@patch("src.utils.llm.LLMClient._openai_chat_content", new_callable=AsyncMock)
async def test_fallback_strips_fence_and_fills_missing_required(mock_chat):
    mock_chat.return_value = "```json\n{ \"shots\": [1,2] }\n```"
    from openai import AsyncOpenAI
    from src.utils.llm import LLMClient

    client = LLMClient()
    client.provider = "deepseek"
    client.model = "deepseek-v4-flash"
    client._openai = AsyncOpenAI(api_key="x")
    out = await client._openai_structured_fallback(
        "sys", "user", _Out, 0.0, schema_defaults={"color_palette": ""}
    )
    assert out.color_palette == ""
    assert out.shots == [1, 2]


@pytest.mark.asyncio
@patch("src.utils.llm.LLMClient._openai_chat_content", new_callable=AsyncMock)
async def test_fallback_plain_json_still_works(mock_chat):
    mock_chat.return_value = json.dumps({"color_palette": "x", "shots": []})
    from openai import AsyncOpenAI
    from src.utils.llm import LLMClient

    client = LLMClient()
    client.provider = "deepseek"
    client.model = "deepseek-v4-flash"
    client._openai = AsyncOpenAI(api_key="x")
    out = await client._openai_structured_fallback("sys", "user", _Out, 0.0)
    assert out.color_palette == "x"


@pytest.mark.asyncio
@patch("src.utils.llm.LLMClient._openai_chat_content", new_callable=AsyncMock)
async def test_fallback_bad_output_raises(mock_chat):
    mock_chat.return_value = "not json at all"
    from openai import AsyncOpenAI
    from src.utils.llm import LLMClient

    client = LLMClient()
    client.provider = "deepseek"
    client.model = "deepseek-v4-flash"
    client._openai = AsyncOpenAI(api_key="x")
    with pytest.raises(ValueError):
        await client._openai_structured_fallback("sys", "user", _Out, 0.0)


# ---------- Task 2: environment contract ----------
def test_environment_contract_forbids_action():
    from src.agents.visual_prompt_agent import (
        COMPACT_SYSTEM_PROMPT,
        _CompactShotVisualPrompt,
    )

    sys_ = COMPACT_SYSTEM_PROMPT
    assert "静态" in sys_
    assert "动作" in sys_
    assert ("在场" in sys_) or ("情绪" in sys_)
    desc = _CompactShotVisualPrompt.model_fields["environment_delta"].description
    assert "静态" in desc and "空" in desc


# ---------- Task 3: sanitizer ----------
def test_sanitize_drops_action_clause_keeps_static():
    from src.agents.visual_prompt_agent import _sanitize_environment

    env = "便利店玻璃门刚被推开；雨幕中街对面停一辆深色轿车，车窗贴深色膜；门铃与雨声交织。"
    out = _sanitize_environment(env)
    assert "被推开" not in out
    assert "深色轿车" in out
    assert "门铃" in out


def test_sanitize_keeps_clean_env():
    from src.agents.visual_prompt_agent import _sanitize_environment

    env = "旧钟楼地下通道；墙上大片水痕，灯管部分损坏，地面浅水。"
    assert _sanitize_environment(env) == env


def test_sanitize_empty_when_all_action():
    from src.agents.visual_prompt_agent import _sanitize_environment

    assert _sanitize_environment("黑衣人站在入口。") == ""
