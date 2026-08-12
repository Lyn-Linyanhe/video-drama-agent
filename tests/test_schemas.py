"""Tests for Pydantic schemas — validate inter-agent data contracts."""

import pytest
from src.schemas.script import ScriptBreakdown, Scene, DialogueLine
from src.schemas.visual import VisualDesign, SceneVisual
from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.assets import GeneratedAssets, ShotAsset
from src.schemas.review import ReviewResult, Issue
from src.schemas.audio import AudioAssets, SceneAudio


class TestScriptSchemas:
    """Test script breakdown schemas."""

    def test_valid_scene(self):
        scene = Scene(
            scene_id=1,
            duration_sec=5,
            location="现代办公室",
            characters=["男主", "女配"],
            dialogue=[
                DialogueLine(character="男主", line="你今天看起来不一样", emotion="好奇")
            ],
            action_description="男主走向女主，递上一杯咖啡",
            camera_direction="中景",
            emotion_tone="温馨",
        )
        assert scene.scene_id == 1
        assert scene.duration_sec == 5

    def test_scene_duration_bounds(self):
        """Production shots must be at least three seconds long."""
        with pytest.raises(Exception):  # pydantic validation error
            Scene(
                scene_id=1, duration_sec=2,
                location="test", characters=["A"],
                action_description="test", camera_direction="close-up",
                emotion_tone="neutral",
            )
        with pytest.raises(Exception):  # zero remains rejected
            Scene(
                scene_id=1, duration_sec=0,
                location="test", characters=["A"],
                action_description="test", camera_direction="close-up",
                emotion_tone="neutral",
            )

    def test_full_script_breakdown(self):
        script = ScriptBreakdown(
            title="测试短剧",
            genre="都市",
            logline="一个小测试",
            duration_sec=30,
            narration_style="第三人称",
            scenes=[
                Scene(
                    scene_id=1, duration_sec=10,
                    location="咖啡厅", characters=["男主"],
                    action_description="男主走进咖啡厅",
                    camera_direction="中景", emotion_tone="平静",
                ),
                Scene(
                    scene_id=2, duration_sec=20,
                    location="办公室", characters=["男主", "女主"],
                    action_description="男女主在办公室相遇",
                    camera_direction="特写", emotion_tone="紧张",
                    transition_from_prev="fade",
                ),
            ],
        )
        assert len(script.scenes) == 2
        assert script.duration_sec == 30


class TestVisualSchemas:
    """Test visual design schemas."""

    def test_visual_design(self):
        visual = VisualDesign(
            overall_style="写实",
            color_palette="暖色调",
            global_character_descriptions={
                "男主": "30岁男性，短发，戴眼镜，身高180cm，穿灰色西装",
            },
            scenes=[
                SceneVisual(
                    scene_id=1,
                    composition="中景，男主居中",
                    lighting="自然光从左侧打来",
                    color_tone="温暖",
                    character_appearance={
                        "男主": "30岁男性，短发，戴眼镜，身高180cm，穿灰色西装"
                    },
                    key_elements=["咖啡杯", "笔记本电脑"],
                ),
            ],
        )
        # Character descriptions must be consistent
        assert (visual.global_character_descriptions["男主"] ==
                visual.scenes[0].character_appearance["男主"])


class TestReviewSchemas:
    """Test review schemas."""

    def test_review_pass(self):
        result = ReviewResult(
            overall_score=0.85,
            passed=True,
            issues=[],
            summary="所有场景质量良好",
        )
        assert result.passed

    def test_review_fail(self):
        result = ReviewResult(
            overall_score=0.4,
            passed=False,
            issues=[
                Issue(
                    scene_id=2,
                    severity="major",
                    type="consistency",
                    description="角色外观不一致",
                    suggestion="请统一角色外貌描述",
                ),
            ],
        )
        assert not result.passed
        assert len(result.issues) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
