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
    assert _sanitize_environment(env) == "旧钟楼地下通道；墙上大片水痕，灯管部分损坏，地面浅水"


def test_sanitize_empty_when_all_action():
    from src.agents.visual_prompt_agent import _sanitize_environment

    assert _sanitize_environment("黑衣人站在入口。") == ""


# ---------- Plan B: parallel two-phase visualize ----------
def _scenes(n):
    from src.schemas.script import Scene
    return [
        Scene(
            scene_id=i,
            duration_sec=3,
            location="loc",
            characters=["A"],
            action_description="x",
            camera_direction="wide",
            emotion_tone="tense",
        )
        for i in range(1, n + 1)
    ]


def test_deepseek_many_scenes_routes_to_parallel(monkeypatch):
    import asyncio

    from src.schemas.script import ScriptBreakdown
    from src.agents.visual_prompt_agent import VisualPromptAgent

    class FakeLLM:
        provider = "deepseek"

    script = ScriptBreakdown(
        title="t", genre="g", logline="l", duration_sec=18, narration_style="n", scenes=_scenes(6)
    )
    agent = VisualPromptAgent()
    agent.llm = FakeLLM()
    sentinel = ("visual", "prompts")

    async def fake(script, **kw):
        return sentinel

    monkeypatch.setattr(agent, "_run_parallel_visualize", fake)
    result = asyncio.run(agent.run(script, creative_profile=None, telemetry={}, _repair_character_profiles=False))
    assert result == sentinel


def test_parallel_visualize_merges_global_and_scenes(monkeypatch):
    import asyncio
    import re

    from src.schemas.script import ScriptBreakdown
    import src.agents.visual_prompt_agent as vpa
    from src.agents.visual_prompt_agent import VisualPromptAgent, _CompactShotVisualPrompt

    script = ScriptBreakdown(
        title="t", genre="g", logline="l", duration_sec=18, narration_style="n", scenes=_scenes(6)
    )

    class FakeLLM:
        provider = "deepseek"

        def __init__(self):
            self.calls = []

        async def generate_structured(self, **kw):
            self.calls.append(kw["output_schema"].__name__)
            if kw["output_schema"].__name__ == "_GlobalVisualOutput":
                return vpa._GlobalVisualOutput(
                    overall_style="风格",
                    color_palette="配色",
                    aspect_ratio="9:16",
                    global_negative_prompt="neg",
                    global_character_descriptions={"c1": "en"},
                    global_character_descriptions_cn={"c1": "女，25岁，高瘦，鹅蛋脸，黑发，米色风衣。"},
                )
            m = re.search(r"第\s*(\d+)\s*镜", kw["user_prompt"])
            sid = int(m.group(1)) if m else 1
            return _CompactShotVisualPrompt(
                scene_id=sid,
                subject_state=f"state {sid}",
                action_timeline=f"0-5s action {sid}",
                environment_delta=f"环境 {sid} 静态，墙面水痕",
                camera_motion="中景",
                lighting_delta="自然光",
                negative_prompt="低清晰度，模糊",
                motion_strength=0.5,
            )

    fake = FakeLLM()
    agent = VisualPromptAgent()
    agent.llm = fake
    captured = {}

    def fake_split(output, script, po, cp):
        captured["output"] = output
        return ("visual", "prompts")

    agent._split_output = fake_split
    asyncio.run(agent._run_parallel_visualize(
        script, prompt_overrides=None, creative_profile=None, telemetry={},
        source_text="", _repair_character_profiles=False,
    ))
    out = captured["output"]
    assert out.overall_style == "风格"
    assert out.global_character_descriptions_cn.get("c1")
    assert len(out.shots) == 6
    assert sorted(s.scene_id for s in out.shots) == list(range(1, 7))
    delta_calls = [c for c in fake.calls if c == "_CompactShotVisualPrompt"]
    global_calls = [c for c in fake.calls if c == "_GlobalVisualOutput"]
    assert len(global_calls) == 1
    assert len(delta_calls) == 6


def test_parallel_scene_retries_once(monkeypatch):
    import asyncio
    import re

    from src.schemas.script import ScriptBreakdown
    import src.agents.visual_prompt_agent as vpa
    from src.agents.visual_prompt_agent import VisualPromptAgent, _CompactShotVisualPrompt

    script = ScriptBreakdown(
        title="t", genre="g", logline="l", duration_sec=18, narration_style="n", scenes=_scenes(6)
    )

    class FakeLLM:
        provider = "deepseek"

        def __init__(self):
            self.tries = {}
            self.calls = []

        async def generate_structured(self, **kw):
            self.calls.append(kw["output_schema"].__name__)
            if kw["output_schema"].__name__ == "_GlobalVisualOutput":
                return vpa._GlobalVisualOutput(
                    overall_style="风格", color_palette="配色", aspect_ratio="9:16",
                    global_negative_prompt="neg",
                    global_character_descriptions_cn={"c1": "女，25岁，高瘦，鹅蛋脸，黑发，米色风衣。"},
                )
            m = re.search(r"第\s*(\d+)\s*镜", kw["user_prompt"])
            sid = int(m.group(1)) if m else 1
            self.tries[sid] = self.tries.get(sid, 0) + 1
            if sid == 3 and self.tries[sid] == 1:
                raise RuntimeError("transient")
            return _CompactShotVisualPrompt(
                scene_id=sid, subject_state=f"s {sid}", action_timeline=f"0-5s a {sid}",
                environment_delta="", camera_motion="中景", lighting_delta="自然光",
                negative_prompt="neg", motion_strength=0.5,
            )

    fake = FakeLLM()
    agent = VisualPromptAgent()
    agent.llm = fake
    captured = {}

    def fake_split(output, script, po, cp):
        captured["output"] = output
        return ("visual", "prompts")

    agent._split_output = fake_split
    asyncio.run(agent._run_parallel_visualize(
        script, prompt_overrides=None, creative_profile=None, telemetry={},
        source_text="", _repair_character_profiles=False,
    ))
    out = captured["output"]
    assert len(out.shots) == 6
    # scene 3 retried once
    assert fake.tries.get(3) == 2
    assert len([c for c in fake.calls if c == "_CompactShotVisualPrompt"]) == 7


def test_parallel_falls_back_to_single_shot(monkeypatch):
    import asyncio

    from src.schemas.script import ScriptBreakdown
    from src.agents.visual_prompt_agent import VisualPromptAgent

    script = ScriptBreakdown(
        title="t", genre="g", logline="l", duration_sec=18, narration_style="n", scenes=_scenes(6)
    )
    agent = VisualPromptAgent()

    async def boom(*a, **k):
        raise RuntimeError("phase1 down")

    async def single(*a, **k):
        return ("single-visual", "single-prompts")

    monkeypatch.setattr(agent, "_generate_global_visual", boom)
    monkeypatch.setattr(agent, "_run_single_shot", single)
    telemetry = {}
    result = asyncio.run(agent._run_parallel_visualize(
        script, prompt_overrides=None, creative_profile=None, telemetry=telemetry,
        source_text="", _repair_character_profiles=False,
    ))
    assert result == ("single-visual", "single-prompts")
    assert telemetry.get("fallback_used") is True


def test_merge_visualize_telemetry_sums():
    from src.agents.visual_prompt_agent import _merge_visualize_telemetry

    t = {}
    _merge_visualize_telemetry(t, {"attempts": 2, "input_chars": 100, "output_chars": 50,
                                   "fallback_used": True, "timeout_sec": 300})
    _merge_visualize_telemetry(t, {"attempts": 3, "input_chars": 200, "output_chars": 20})
    assert t["attempts"] == 5
    assert t["input_chars"] == 300
    assert t["output_chars"] == 70
    assert t["fallback_used"] is True
    assert t["timeout_sec"] == 300


def test_parallel_visualize_aggregates_telemetry():
    import asyncio
    import re

    from src.schemas.script import ScriptBreakdown
    import src.agents.visual_prompt_agent as vpa
    from src.agents.visual_prompt_agent import VisualPromptAgent, _CompactShotVisualPrompt

    script = ScriptBreakdown(
        title="t", genre="g", logline="l", duration_sec=18, narration_style="n", scenes=_scenes(6)
    )

    class FakeLLM:
        provider = "deepseek"

        async def generate_structured(self, **kw):
            if kw["output_schema"].__name__ == "_GlobalVisualOutput":
                kw["telemetry"]["attempts"] = 1
                kw["telemetry"]["input_chars"] = 100
                return vpa._GlobalVisualOutput(overall_style="s", color_palette="c", aspect_ratio="9:16",
                                               global_negative_prompt="n",
                                               global_character_descriptions_cn={"c1": "女，25岁，鹅蛋脸，黑发。"})
            kw["telemetry"]["attempts"] = 1
            kw["telemetry"]["input_chars"] = 100
            m = re.search(r"第\s*(\d+)\s*镜", kw["user_prompt"])
            sid = int(m.group(1)) if m else 1
            return _CompactShotVisualPrompt(scene_id=sid, subject_state=f"s{sid}", action_timeline=f"0-5s a{sid}",
                                            environment_delta="", camera_motion="中景", lighting_delta="自然光",
                                            negative_prompt="neg", motion_strength=0.5)

    agent = VisualPromptAgent()
    agent.llm = FakeLLM()
    captured = {}

    def fake_split(output, script, po, cp):
        captured["output"] = output
        return ("visual", "prompts")

    agent._split_output = fake_split
    telemetry = {}
    asyncio.run(agent._run_parallel_visualize(
        script, prompt_overrides=None, creative_profile=None, telemetry=telemetry,
        source_text="", _repair_character_profiles=False,
    ))
    # 1 phase1 + 6 phase2 calls, each contributing attempts=1, input=100
    assert telemetry.get("attempts") == 7
    assert telemetry.get("input_chars") == 700


def test_parallel_scene_persistent_failure_falls_back(monkeypatch):
    import asyncio
    import re

    from src.schemas.script import ScriptBreakdown
    import src.agents.visual_prompt_agent as vpa
    from src.agents.visual_prompt_agent import VisualPromptAgent, _CompactShotVisualPrompt

    script = ScriptBreakdown(
        title="t", genre="g", logline="l", duration_sec=18, narration_style="n", scenes=_scenes(6)
    )

    class FakeLLM:
        provider = "deepseek"

        async def generate_structured(self, **kw):
            if kw["output_schema"].__name__ == "_GlobalVisualOutput":
                return vpa._GlobalVisualOutput(
                    overall_style="s", color_palette="c", aspect_ratio="9:16",
                    global_negative_prompt="n",
                    global_character_descriptions_cn={"c1": "女，25岁，鹅蛋脸，黑发。"},
                )
            m = re.search(r"第\s*(\d+)\s*镜", kw["user_prompt"])
            sid = int(m.group(1)) if m else 1
            if sid == 3:
                raise RuntimeError("persistent delta fail")
            return _CompactShotVisualPrompt(
                scene_id=sid, subject_state=f"s{sid}", action_timeline=f"0-5s a{sid}",
                environment_delta="", camera_motion="中景", lighting_delta="自然光",
                negative_prompt="neg", motion_strength=0.5,
            )

    agent = VisualPromptAgent()
    agent.llm = FakeLLM()

    async def single(*a, **k):
        return ("single-visual", "single-prompts")

    monkeypatch.setattr(agent, "_run_single_shot", single)
    telemetry = {}
    result = asyncio.run(agent._run_parallel_visualize(
        script, prompt_overrides=None, creative_profile=None, telemetry=telemetry,
        source_text="", _repair_character_profiles=False,
    ))
    assert result == ("single-visual", "single-prompts")
    assert telemetry.get("fallback_used") is True


# ---------- Plan B: per-scene progress + request detail ----------
def test_parallel_visualize_reports_scene_progress_and_details():
    import asyncio
    import re

    from src.schemas.script import ScriptBreakdown
    import src.agents.visual_prompt_agent as vpa
    from src.agents.visual_prompt_agent import VisualPromptAgent, _CompactShotVisualPrompt

    script = ScriptBreakdown(
        title="t", genre="g", logline="l", duration_sec=18, narration_style="n", scenes=_scenes(6)
    )

    class FakeLLM:
        provider = "deepseek"

        async def generate_structured(self, **kw):
            if kw["output_schema"].__name__ == "_GlobalVisualOutput":
                kw["telemetry"]["attempts"] = 1
                kw["telemetry"]["input_chars"] = 100
                return vpa._GlobalVisualOutput(overall_style="s", color_palette="c", aspect_ratio="9:16",
                                               global_negative_prompt="n",
                                               global_character_descriptions_cn={"c1": "女，25岁，鹅蛋脸，黑发。"})
            kw["telemetry"]["attempts"] = 1
            kw["telemetry"]["input_chars"] = 100
            m = re.search(r"第\s*(\d+)\s*镜", kw["user_prompt"])
            sid = int(m.group(1)) if m else 1
            return _CompactShotVisualPrompt(scene_id=sid, subject_state=f"s{sid}", action_timeline=f"0-5s a{sid}",
                                            environment_delta="", camera_motion="中景", lighting_delta="自然光",
                                            negative_prompt="neg", motion_strength=0.5)

    agent = VisualPromptAgent()
    agent.llm = FakeLLM()

    def fake_split(output, script, po, cp):
        return ("visual", "prompts")

    agent._split_output = fake_split
    telemetry = {}
    progress_calls = []

    def progress_callback(stage, current, total, message):
        progress_calls.append((stage, current, total, message))

    asyncio.run(agent._run_parallel_visualize(
        script, prompt_overrides=None, creative_profile=None, telemetry=telemetry,
        source_text="", _repair_character_profiles=False, progress_callback=progress_callback,
    ))

    # Progress: one monotonic update per scene.
    assert len(progress_calls) == 6
    currents = [call[1] for call in progress_calls]
    assert currents == list(range(1, 7))
    assert all(call[0] == "visualize_scene" for call in progress_calls)
    assert all(call[2] == 6 for call in progress_calls)
    assert "已完成 6/6 镜" in progress_calls[-1][3]

    # Requests: one global + one per scene, each with detail fields.
    requests = telemetry.get("requests") or []
    stages = [r.get("stage") for r in requests]
    assert stages.count("visualize_global") == 1
    assert stages.count("visualize_scene") == 6
    scene_reqs = [r for r in requests if r.get("stage") == "visualize_scene"]
    assert sorted(r["scene_id"] for r in scene_reqs) == list(range(1, 7))
    for r in scene_reqs:
        assert r["attempts"] == 1
        assert isinstance(r["elapsed_ms"], int)
        assert r["input_chars"] == 100
        assert r["fallback_used"] is False


def test_parallel_scene_failure_keeps_partial_requests_and_falls_back(monkeypatch):
    import asyncio
    import re

    from src.schemas.script import ScriptBreakdown
    import src.agents.visual_prompt_agent as vpa
    from src.agents.visual_prompt_agent import VisualPromptAgent, _CompactShotVisualPrompt

    script = ScriptBreakdown(
        title="t", genre="g", logline="l", duration_sec=18, narration_style="n", scenes=_scenes(6)
    )

    class FakeLLM:
        provider = "deepseek"

        async def generate_structured(self, **kw):
            if kw["output_schema"].__name__ == "_GlobalVisualOutput":
                return vpa._GlobalVisualOutput(overall_style="s", color_palette="c", aspect_ratio="9:16",
                                               global_negative_prompt="n",
                                               global_character_descriptions_cn={"c1": "女，25岁，鹅蛋脸，黑发。"})
            m = re.search(r"第\s*(\d+)\s*镜", kw["user_prompt"])
            sid = int(m.group(1)) if m else 1
            if sid == 3:
                raise RuntimeError("persistent delta fail")
            kw["telemetry"]["attempts"] = 1
            return _CompactShotVisualPrompt(scene_id=sid, subject_state=f"s{sid}", action_timeline=f"0-5s a{sid}",
                                            environment_delta="", camera_motion="中景", lighting_delta="自然光",
                                            negative_prompt="neg", motion_strength=0.5)

    agent = VisualPromptAgent()
    agent.llm = FakeLLM()

    async def single(*a, **k):
        return ("single-visual", "single-prompts")

    monkeypatch.setattr(agent, "_run_single_shot", single)
    telemetry = {}
    progress_calls = []
    result = asyncio.run(agent._run_parallel_visualize(
        script, prompt_overrides=None, creative_profile=None, telemetry=telemetry,
        source_text="", _repair_character_profiles=False,
        progress_callback=lambda stage, current, total, message: progress_calls.append((stage, current, total)),
    ))
    assert result == ("single-visual", "single-prompts")
    assert telemetry.get("fallback_used") is True
    # The aggregate attempt counter must include calls that already happened
    # before the fallback, not just the single-shot call.
    assert int(telemetry.get("attempts") or 0) >= 5

    requests = telemetry.get("requests") or []
    scene_reqs = [r for r in requests if r.get("stage") == "visualize_scene"]
    # Failed shot 3 is not recorded; the others are kept for traceability.
    assert 3 not in [r["scene_id"] for r in scene_reqs]
    assert len(scene_reqs) == 5
    # Progress only advanced for completed shots.
    assert [call[1] for call in progress_calls] == [1, 2, 3, 4, 5]


def test_parallel_cancel_does_not_retry_or_fallback(monkeypatch):
    import asyncio
    import re

    from src.schemas.script import ScriptBreakdown
    import src.agents.visual_prompt_agent as vpa
    from src.agents.visual_prompt_agent import VisualPromptAgent, _CompactShotVisualPrompt
    from src.services.job_manager import JobCanceled

    script = ScriptBreakdown(
        title="t", genre="g", logline="l", duration_sec=18, narration_style="n", scenes=_scenes(1)
    )
    agent = VisualPromptAgent()
    scene_calls = []

    async def fake_global(*_a, **_k):
        return vpa._GlobalVisualOutput(
            overall_style="s", color_palette="c", aspect_ratio="9:16",
            global_negative_prompt="n", global_character_descriptions_cn={},
        )

    class FakeLLM:
        provider = "deepseek"

        async def generate_structured(self, **kw):
            scene_calls.append(kw["output_schema"].__name__)
            m = re.search(r"第\s*(\d+)\s*镜", kw["user_prompt"])
            sid = int(m.group(1)) if m else 1
            return _CompactShotVisualPrompt(
                scene_id=sid, subject_state=f"s{sid}", action_timeline=f"0-5s a{sid}",
                environment_delta="", camera_motion="中景", lighting_delta="自然光",
                negative_prompt="neg", motion_strength=0.5,
            )

    def progress(*_args, **_kwargs):
        raise JobCanceled("任务已由用户终止")

    async def single(*_a, **_k):
        raise AssertionError("fallback must not run on cancel")

    agent._generate_global_visual = fake_global
    agent.llm = FakeLLM()
    monkeypatch.setattr(agent, "_run_single_shot", single)
    with pytest.raises(JobCanceled):
        asyncio.run(agent._run_parallel_visualize(
            script, prompt_overrides=None, creative_profile=None, telemetry={},
            source_text="", _repair_character_profiles=False, progress_callback=progress,
        ))
    assert len(scene_calls) == 1


def test_parallel_fallback_keeps_prior_attempts_and_marks_fallback(monkeypatch):
    import asyncio

    from src.schemas.script import ScriptBreakdown
    from src.agents.visual_prompt_agent import VisualPromptAgent

    script = ScriptBreakdown(
        title="t", genre="g", logline="l", duration_sec=18, narration_style="n", scenes=_scenes(2)
    )
    agent = VisualPromptAgent()

    async def boom(*_a, **_k):
        raise RuntimeError("phase down")

    async def single(*_a, **_k):
        # Simulates the real generate_structured resetting its own telemetry
        # dict and recording one single-shot call.
        _k["telemetry"]["attempts"] = 3
        _k["telemetry"]["input_chars"] = 50
        return ("single-visual", "single-prompts")

    monkeypatch.setattr(agent, "_generate_global_visual", boom)
    monkeypatch.setattr(agent, "_run_single_shot", single)
    telemetry = {"requests": [{"stage": "visualize_scene", "scene_id": 1, "attempts": 2}]}
    result = asyncio.run(agent._run_parallel_visualize(
        script, prompt_overrides=None, creative_profile=None, telemetry=telemetry,
        source_text="", _repair_character_profiles=False,
    ))
    assert result == ("single-visual", "single-prompts")
    assert telemetry["fallback_used"] is True
    assert telemetry["attempts"] == 5
    assert telemetry["input_chars"] == 50


def test_sanitize_drops_gaze_and_psychology_clauses():
    from src.agents.visual_prompt_agent import _sanitize_environment

    env = "再抬眼望向空旷巷口；说话时视线仍偏向巷口；怕它被雨水浸透；街角路灯亮着"
    out = _sanitize_environment(env)
    assert "望向" not in out
    assert "视线" not in out
    assert "怕它" not in out
    assert "街角路灯亮着" in out


def test_sanitize_dedupes_repeated_clauses():
    from src.agents.visual_prompt_agent import _sanitize_environment

    env = "门灯照亮湿润台阶。门灯照亮湿润台阶；街角路灯亮着。街角路灯亮着"
    out = _sanitize_environment(env)
    assert out.count("门灯照亮湿润台阶") == 1
    assert out.count("街角路灯亮着") == 1


def test_assembled_prompt_env_is_clean_and_deduplicated():
    from types import SimpleNamespace

    from src.schemas.script import (
        LocationAsset,
        Scene,
        ScriptBreakdown,
        ShotVisualBinding,
        VisualAssetManifest,
    )
    from src.agents.visual_prompt_agent import (
        VisualPromptAgent,
        _CompactShotVisualPrompt,
    )

    script = ScriptBreakdown(
        title="t", genre="g", logline="l", duration_sec=5, narration_style="n",
        scenes=[Scene(
            scene_id=1, duration_sec=5, location="老城区巷口咖啡店门外",
            characters=["林默"], action_description="林默站在屋檐下看表。",
            camera_direction="中景", emotion_tone="克制",
        )],
        asset_manifest=VisualAssetManifest(
            locations=[LocationAsset(
                location_id="loc_cafe", canonical_name="巷口咖啡店",
                representative_scene_id=1, shot_ids=[1],
                environment_description="街角路灯亮着；再抬眼望向空旷巷口；说话时视线仍偏向巷口；怕它被雨水浸透；街角路灯亮着",
            )],
            shot_bindings=[ShotVisualBinding(scene_id=1, location_id="loc_cafe")],
        ),
    )
    shot = _CompactShotVisualPrompt(
        scene_id=1,
        subject_state="林默站在屋檐下看表",
        action_timeline="0-5秒，林默站在屋檐下看表",
        environment_delta="门灯照亮湿润台阶",
        camera_motion="中景",
        lighting_delta="冷青夜色",
        negative_prompt="低清晰度，模糊",
        motion_strength=0.5,
    )
    output = SimpleNamespace(
        overall_style="写实", color_palette="冷色", aspect_ratio="9:16",
        global_character_descriptions_cn={}, global_negative_prompt="", shots=[],
    )
    rendered = VisualPromptAgent._assemble_compact_shot_prompt(
        script, script.scenes[0], shot, output,
    )
    env_line = next(line for line in rendered.splitlines() if line.startswith("【环境】"))
    assert "望向" not in env_line
    assert "视线" not in env_line
    assert "怕它" not in env_line
    assert env_line.count("路灯") == 1
    assert env_line.count("门灯") == 1
