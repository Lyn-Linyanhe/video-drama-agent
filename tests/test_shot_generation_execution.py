import pytest
import asyncio
from types import SimpleNamespace
from pathlib import Path

from src.agents.video_gen_agent import VideoGenerationAgent
from src.schemas.assets import GeneratedAssets, ShotAsset, ShotProductionState, ShotVideoVersion
from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.script import Scene, ScriptBreakdown
from src.schemas.shot_plan import ShotPlan, ShotUnit, SceneCapacityReport
from src.services.render_timeline import active_video_durations
from src.agents.assembly_agent import AssemblyAgent


def _script_and_prompts():
    scene = Scene(scene_id=1, duration_sec=8, location="室内", characters=["甲"], action_description="甲走到桌边拿起钥匙并打开盒子", camera_direction="固定中景", emotion_tone="紧张")
    script = ScriptBreakdown(title="测试", genre="剧情", logline="测试", duration_sec=8, narration_style="无旁白", scenes=[scene])
    parent = ShotPrompt(scene_id=1, prompt="【动作时间轴】0-8s 甲走到桌边拿起钥匙并打开盒子。", duration_sec=8)
    plan = ShotPlan(scene_id=1, parent_duration_sec=8, capacity=SceneCapacityReport(scene_id=1, required_total_duration_sec=8, available_duration_sec=8, recommended_total_duration_sec=8, recommended_durations_sec=[4,4], recommended_shot_count=2, status="auto_split", warnings=["拆分"]), units=[
        ShotUnit(shot_id="scene-1-shot-1", parent_scene_id=1, order=1, beat_ids=["scene-1-beat-1"], duration_sec=4),
        ShotUnit(shot_id="scene-1-shot-2", parent_scene_id=1, order=2, beat_ids=["scene-1-beat-2"], duration_sec=4, continuity_from_shot_id="scene-1-shot-1"),
    ])
    return script, GenerationPrompts(api_provider="mock", shots=[parent], shot_plans=[plan])


def test_video_agent_run_generates_one_provider_call_per_shot_unit(monkeypatch):
    script, prompts = _script_and_prompts()
    calls = []
    agent = VideoGenerationAgent(max_concurrent=2)
    monkeypatch.setattr("src.agents.video_gen_agent.repair_asset_manifest", lambda _script: None)
    monkeypatch.setattr("src.agents.video_gen_agent.validate_asset_manifest", lambda _script: [])
    monkeypatch.setattr("src.agents.video_gen_agent.apply_timed_prompt_structure", lambda *_args, **_kwargs: None)

    async def fake_generate(shot, *args, **kwargs):
        calls.append(shot)
        return ShotAsset(scene_id=shot.scene_id, success=True, duration_sec=shot.duration_sec)

    monkeypatch.setattr(agent, "_generate_with_retry", fake_generate)
    result = asyncio.run(agent.run(prompts, script=script, project_id=""))

    assert [shot.shot_id for shot in calls] == ["scene-1-shot-1", "scene-1-shot-2"]
    assert [asset.shot_id for asset in result.shots] == ["scene-1-shot-1", "scene-1-shot-2"]
    assert len(result.shots) == 2


def test_split_shots_survive_generation_active_version_and_assembly(tmp_path, monkeypatch):
    script, prompts = _script_and_prompts()
    first = tmp_path / "shot-1.mp4"
    second = tmp_path / "shot-2.mp4"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    agent = VideoGenerationAgent(max_concurrent=2)
    monkeypatch.setattr("src.agents.video_gen_agent.repair_asset_manifest", lambda _script: None)
    monkeypatch.setattr("src.agents.video_gen_agent.validate_asset_manifest", lambda _script: [])
    monkeypatch.setattr("src.agents.video_gen_agent.apply_timed_prompt_structure", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(agent, "_store_project_video", lambda asset, project_id, version_id=None: asset.local_path)

    paths = iter([str(first), str(second)])

    async def fake_generate(shot, *args, **kwargs):
        return ShotAsset(scene_id=shot.scene_id, local_path=next(paths), success=True, duration_sec=shot.duration_sec)

    monkeypatch.setattr(agent, "_generate_with_retry", fake_generate)
    generated = asyncio.run(agent.run(prompts, script=script, project_id=""))

    production = ShotProductionState(
        scene_id=1,
        video_versions=[
            ShotVideoVersion(**asset.model_dump(), version_id=f"v-{asset.shot_id}", created_at="")
            for asset in generated.shots
        ],
        active_version_by_shot_id={asset.shot_id: f"v-{asset.shot_id}" for asset in generated.shots},
    )
    state = SimpleNamespace(shot_productions={1: production}, generated_assets=generated)

    clips = AssemblyAgent()._expanded_video_clips(script, generated)

    assert generated.expected_shot_ids == ["scene-1-shot-1", "scene-1-shot-2"]
    assert [(asset.shot_id, asset.shot_order) for _, asset, _ in clips] == [
        ("scene-1-shot-1", 1), ("scene-1-shot-2", 2)
    ]


def test_prepare_shot_request_rejects_provider_over_cap_instead_of_clamping():
    from src.api_clients.happyhorse import HappyHorseVideoClient

    scene = Scene(
        scene_id=1, duration_sec=20, location="室内", characters=["甲"],
        action_description="甲等待", camera_direction="固定中景", emotion_tone="紧张",
    )
    script = ScriptBreakdown(
        title="时长", genre="剧情", logline="测试", duration_sec=20,
        narration_style="无旁白", scenes=[scene],
    )
    shot = ShotPrompt(scene_id=1, prompt="甲等待", duration_sec=20)
    agent = VideoGenerationAgent(
        api_client=HappyHorseVideoClient(api_key="test", model="happyhorse-1.1-i2v")
    )

    with pytest.raises(ValueError, match=r"15"):
        agent.prepare_shot_request(
            shot,
            generation_prompts=GenerationPrompts(api_provider="happyhorse", shots=[shot]),
            prompt_policy=None,
            script=script,
        )
