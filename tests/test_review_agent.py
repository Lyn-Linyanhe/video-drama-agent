from pathlib import Path

import pytest

from src.agents.review_agent import ReviewAgent
from src.schemas.assets import GeneratedAssets, ShotAsset
from src.schemas.review import ReviewResult
from src.schemas.script import Scene, ScriptBreakdown
from src.schemas.visual import SceneVisual, VisualDesign


class DummyLLM:
    async def generate_structured(self, **kwargs):
        return ReviewResult(
            overall_score=0.9,
            passed=True,
            issues=[],
            summary="文本审核通过",
        )


def _script() -> ScriptBreakdown:
    return ScriptBreakdown(
        title="测试短剧",
        genre="悬疑",
        logline="男主发现异常",
        duration_sec=5,
        narration_style="第三人称",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=5,
                location="办公室",
                characters=["男主"],
                action_description="男主推门进入办公室，看到桌上的红色信封",
                camera_direction="中景推进到特写",
                emotion_tone="紧张",
            )
        ],
    )


def _visual() -> VisualDesign:
    return VisualDesign(
        overall_style="写实悬疑",
        color_palette="冷色调",
        global_character_descriptions={"男主": "30岁男性，黑色夹克"},
        scenes=[
            SceneVisual(
                scene_id=1,
                composition="男主居中，桌面信封在前景",
                lighting="低照度侧光",
                color_tone="冷蓝色",
                character_appearance={"男主": "30岁男性，黑色夹克"},
                key_elements=["红色信封", "办公桌"],
            )
        ],
    )


@pytest.mark.asyncio
async def test_review_agent_adds_video_visual_issues(tmp_path: Path, monkeypatch):
    video = tmp_path / "shot.mp4"
    video.write_bytes(b"placeholder")
    calls = []

    async def fake_review_video_subjective(video_path, expected_prompt):
        calls.append((video_path, expected_prompt))
        return {"status": "warning", "issues": ["画面中没有红色信封"]}

    monkeypatch.setattr(
        "src.agents.review_agent.review_video_subjective",
        fake_review_video_subjective,
    )

    agent = ReviewAgent()
    agent.llm = DummyLLM()
    result = await agent.run(
        _script(),
        _visual(),
        GeneratedAssets(shots=[
            ShotAsset(
                scene_id=1,
                local_path=str(video),
                duration_sec=5,
                api_used="mock",
                success=True,
            )
        ]),
    )

    assert calls
    assert "红色信封" in calls[0][1]
    assert result.issues
    assert result.issues[0].type == "quality"
    assert "没有红色信封" in result.issues[0].description
    assert result.overall_score == 0.68


@pytest.mark.asyncio
async def test_review_agent_flags_missing_local_video(monkeypatch):
    async def fake_review_video_subjective(video_path, expected_prompt):
        raise AssertionError("visual review should not run without local_path")

    monkeypatch.setattr(
        "src.agents.review_agent.review_video_subjective",
        fake_review_video_subjective,
    )

    agent = ReviewAgent()
    agent.llm = DummyLLM()
    result = await agent.run(
        _script(),
        _visual(),
        GeneratedAssets(shots=[
            ShotAsset(
                scene_id=1,
                duration_sec=5,
                api_used="mock",
                success=True,
            )
        ]),
    )

    assert result.issues
    assert "缺少本地视频文件" in result.issues[0].description
