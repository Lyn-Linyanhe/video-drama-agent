import re

from src.schemas.prompt import ShotPrompt
from src.schemas.script import Scene, ScriptBreakdown
from src.services.timed_prompt_composer import build_timed_shot_prompt


def _script(duration=8):
    return ScriptBreakdown(
        title="timeline", genre="test", logline="test", duration_sec=duration,
        narration_style="none",
        scenes=[Scene(
            scene_id=1, duration_sec=duration, location="室内", characters=["甲"],
            action_description="甲拿起信，闪回母亲在海边，甲读完信后哭泣",
            camera_direction="近景", emotion_tone="紧张",
        )],
    )


def _timed_lines(prompt):
    return [line for line in prompt.splitlines() if re.match(r"^(?:切镜头，)?\d+(?:\.\d+)?s", line)]


def test_complex_authored_timeline_is_rendered_as_contiguous_ranges():
    shot = ShotPrompt(
        scene_id=1,
        duration_sec=8,
        prompt="【动作时间轴】0-2秒，甲拿起信；2-4秒，闪回母亲在海边；4-6秒，甲读完信后哭泣。",
    )
    rendered = build_timed_shot_prompt(shot, _script())
    lines = _timed_lines(rendered)
    assert lines
    ranges = [re.search(r"(\d+(?:\.\d+)?s)-(\d+(?:\.\d+)?s)", line).groups() for line in lines]
    assert float(ranges[0][0][:-1]) == 0
    assert all(float(ranges[i][0][:-1]) == float(ranges[i - 1][1][:-1]) for i in range(1, len(ranges)))
    assert float(ranges[-1][1][:-1]) == 8
    assert not any(re.match(r"^(?:切镜头，)?\d+(?:\.\d+)?s[，,]", line) for line in lines)


def test_finalized_duration_only_timeline_is_canonicalized_to_ranges():
    shot = ShotPrompt(
        scene_id=1,
        duration_sec=5,
        prompt="【时间轴分镜】\n总时长约5s\n2s，动作一\n切镜头，3s，动作二",
    )
    script = _script(5)
    script.scenes[0].action_description = "动作一，动作二"
    rendered = build_timed_shot_prompt(shot, script)
    assert "0s-2s，动作一" in rendered
    assert "切镜头，2s-5s，动作二" in rendered
    assert not re.search(r"^(?:切镜头，)?\d+(?:\.\d+)?s[，,]", rendered, re.M)


def test_duration_only_provider_cap_is_canonicalized_to_ranges():
    shot = ShotPrompt(
        scene_id=1,
        duration_sec=20,
        prompt="【时间轴分镜】\n总时长约20s\n10s，动作一\n切镜头，10s，动作二",
    )
    rendered = build_timed_shot_prompt(shot, _script(20), duration_cap_sec=15)
    assert "0s-7.5s，动作一" in rendered
    assert "切镜头，7.5s-15s，动作二" in rendered
