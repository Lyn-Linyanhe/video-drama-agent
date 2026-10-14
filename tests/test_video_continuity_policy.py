import asyncio
import subprocess
from types import SimpleNamespace
from pathlib import Path

import pytest
from fastapi import HTTPException

from src.agents.assembly_agent import AssemblyAgent
from src.api_clients.happyhorse import HappyHorseVideoClient
from src.schemas.assets import GeneratedAssets, ShotAsset
from src.schemas.script import Scene, ScriptBreakdown
from src.services.video_continuity import continuity_reference, mark_dependents_stale
from src.services.video_capabilities import (
    VIDEO_CAPABILITIES,
    default_video_policy,
    normalize_video_policy,
    validate_video_duration,
)


def test_legacy_scene_transition_field_remains_readable():
    scene = Scene(
        scene_id=2,
        duration_sec=3,
        location="room",
        characters=[],
        dialogue=[],
        action_description="a",
        camera_direction="wide",
        emotion_tone="calm",
        transition_from_prev="dissolve",
    )
    assert scene.transition_from_prev == "dissolve"


def test_wan_video_policy_requires_official_dashscope(monkeypatch):
    import src.config as config

    monkeypatch.setattr(config, "HAPPYHORSE_API_KEY", "key")
    monkeypatch.setattr(config, "HAPPYHORSE_BASE_URL", "https://api.apiyi.com")
    with pytest.raises(HTTPException):
        normalize_video_policy({"provider": "wan", "model": "wan2.6-i2v", "resolution": "1080p"})


def test_wan_t2v_has_no_continuity_capability(monkeypatch):
    import src.config as config

    monkeypatch.setattr(config, "HAPPYHORSE_API_KEY", "key")
    monkeypatch.setattr(config, "HAPPYHORSE_BASE_URL", "https://dashscope.aliyuncs.com")
    policy = normalize_video_policy({"provider": "wan", "model": "wan2.6-t2v", "resolution": "720p"})
    assert policy["capability"]["continuity"] is False
    assert policy["capability"]["image_to_video"] is False


def test_all_video_models_expose_reference_limits_and_modes():
    assert VIDEO_CAPABILITIES["happyhorse"]["models"]["happyhorse-1.1-i2v"]["max_reference_images"] == 1
    assert VIDEO_CAPABILITIES["happyhorse"]["models"]["happyhorse-1.1-r2v"]["max_reference_images"] == 9
    assert VIDEO_CAPABILITIES["wan"]["models"]["wan2.6-i2v"]["max_reference_images"] == 1
    assert VIDEO_CAPABILITIES["wan"]["models"]["wan2.6-t2v"]["max_reference_images"] == 0
    assert VIDEO_CAPABILITIES["wan"]["models"]["wan2.6-t2v"]["reference_modes"] == ["text_to_video"]
    assert "multi_reference" in VIDEO_CAPABILITIES["happyhorse"]["models"]["happyhorse-1.1-r2v"]["reference_modes"]


def test_seedance_policy_accepts_seedence_alias():
    policy = normalize_video_policy({
        "provider": "seedence",
        "model": "doubao-seedance-1-0-pro-250528",
        "resolution": "1080p",
    })
    assert policy["provider"] == "seedance"
    assert policy["capability"]["image_to_video"] is True
    assert policy["capability"]["multi_reference"] is True


def test_seedance_default_model_is_fast_20(monkeypatch):
    import src.config as config

    monkeypatch.setattr(config, "DEFAULT_API", "seedance")
    monkeypatch.setattr(config, "ARK_MODEL", "")
    policy = default_video_policy()
    assert policy["provider"] == "seedance"
    assert policy["model"] == "doubao-seedance-2-0-fast-260128"


@pytest.mark.parametrize("model", [
    "doubao-seedance-2-5-260628",
    "doubao-seedance-2-0-260128",
    "doubao-seedance-2-0-fast-260128",
    "doubao-seedance-2-0-mini-260615",
    "doubao-seedance-1-5-pro-251215",
    "doubao-seedance-1-0-pro-250528",
    "doubao-seedance-1-0-pro-fast-251015",
])
def test_current_seedance_models_are_available(model):
    policy = normalize_video_policy({"provider": "seedance", "model": model})
    assert policy["provider"] == "seedance"
    assert policy["model"] == model
    assert policy["capability"]["max_reference_images"] >= 1


def test_seedance_model_specific_duration_and_resolution_validation():
    policy = normalize_video_policy({
        "provider": "seedance",
        "model": "doubao-seedance-2-5-260628",
        "resolution": "4k",
    })
    assert policy["resolution"] == "4k"
    assert validate_video_duration(policy, 30) == 30

    with pytest.raises(HTTPException):
        validate_video_duration(policy, 3)


def test_legacy_seedance_model_remains_readable_without_becoming_default():
    policy = normalize_video_policy({
        "provider": "seedance",
        "model": "doubao-seedance-1-0-pro-fast-250528",
        "resolution": "1080p",
    })
    assert policy["model"] == "doubao-seedance-1-0-pro-fast-250528"


@pytest.mark.parametrize("model", [
    "doubao-seedance-2-0-260128",
    "doubao-seedance-2-0-fast-260128",
])
def test_seedance_2_models_are_available(model):
    policy = normalize_video_policy({
        "provider": "seedance",
        "model": model,
        "resolution": "1080p",
    })
    assert policy["provider"] == "seedance"
    assert policy["capability"]["image_to_video"] is True
    assert policy["capability"]["multi_reference"] is True


def test_happyhorse_reference_model_rewrite_only_applies_to_happyhorse_models():
    assert HappyHorseVideoClient(model="happyhorse-1.1-i2v")._model_for_reference_count(2) == "happyhorse-1.1-r2v"
    assert HappyHorseVideoClient(model="doubao-seedance-1-0-lite-i2v-250428")._model_for_reference_count(2) == "doubao-seedance-1-0-lite-i2v-250428"


def test_transition_renderer_uses_xfade_and_low_source_audio(tmp_path, monkeypatch):
    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    captured = []

    def fake_ffmpeg(command, timeout):
        captured.append(command)
        Path(command[-1]).write_bytes(b"video")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("src.agents.assembly_agent.run_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(AssemblyAgent, "_media_duration", staticmethod(lambda _path: 3.0))
    monkeypatch.setattr(AssemblyAgent, "_has_audio_stream", staticmethod(lambda _path: True))
    script = ScriptBreakdown(
        title="test", genre="test", logline="test", duration_sec=6, narration_style="none",
        scenes=[
            Scene(scene_id=1, duration_sec=3, location="room", characters=[], action_description="a", camera_direction="wide", emotion_tone="calm"),
            Scene(scene_id=2, duration_sec=3, location="room", characters=[], action_description="b", camera_direction="wide", emotion_tone="calm", transition_from_prev="dissolve"),
        ],
    )
    clips = [(script.scenes[0], ShotAsset(scene_id=1, local_path=str(first), success=True)), (script.scenes[1], ShotAsset(scene_id=2, local_path=str(second), success=True))]
    _, starts = AssemblyAgent()._render_transition_video(clips, str(tmp_path / "out.mp4"), tmp_path)
    graph = command_value(captured[0], "-filter_complex")
    assert "xfade=transition=dissolve" in graph
    assert "volume=0.22" in graph
    assert starts[2] == 2750


def command_value(command, flag):
    return command[command.index(flag) + 1]


def test_child_shot_without_explicit_dependency_does_not_inherit_previous_tail(tmp_path, monkeypatch):
    from src.schemas.assets import ShotProductionState, ShotVideoVersion

    state = SimpleNamespace(
        project_id="child-no-continuity",
        shot_productions={
            1: ShotProductionState(
                scene_id=1,
                video_versions=[
                    ShotVideoVersion(scene_id=1, shot_id="scene-1-shot-1", local_path=str(tmp_path / "one.mp4"), success=True, version_id="v1", created_at=""),
                ],
            )
        },
    )
    (tmp_path / "one.mp4").write_bytes(b"video")
    monkeypatch.setattr("src.services.video_continuity.extract_tail_frame", lambda *_args: (_ for _ in ()).throw(AssertionError("tail must not be read")))

    path, label = continuity_reference(
        state, 1, {"model": "mock", "capability": {"continuity": True}},
        shot_id="scene-1-shot-2",
    )

    assert (path, label) == ("", "")


def test_child_dependents_are_marked_stale_when_source_version_changes():
    from src.schemas.assets import ShotProductionState, ShotVideoVersion

    state = SimpleNamespace(
        shot_productions={
            1: ShotProductionState(
                scene_id=1,
                video_versions=[
                    ShotVideoVersion(scene_id=1, shot_id="scene-1-shot-1", local_path="one.mp4", success=True, version_id="new-source", created_at=""),
                    ShotVideoVersion(
                        scene_id=1,
                        shot_id="scene-1-shot-2",
                        continuity_from_shot_id="scene-1-shot-1",
                        continuity_source_version_id="old-source",
                        local_path="two.mp4",
                        success=True,
                        version_id="dependent",
                        created_at="",
                    ),
                ],
            )
        }
    )

    mark_dependents_stale(state, 1, "new-source", source_shot_id="scene-1-shot-1")

    assert state.shot_productions[1].continuity_stale is True


def test_child_shot_continuity_uses_previous_child_tail(tmp_path, monkeypatch):
    from src.graph.state import DramaState
    from src.schemas.assets import ShotProductionState, ShotVideoVersion

    state = SimpleNamespace(
        project_id="child-continuity",
        shot_productions={
            1: ShotProductionState(
                scene_id=1,
                video_versions=[
                    ShotVideoVersion(scene_id=1, shot_id="scene-1-shot-1", local_path=str(tmp_path / "one.mp4"), success=True, version_id="v1", created_at=""),
                ],
                active_version_id="v1",
            )
        },
    )
    (tmp_path / "one.mp4").write_bytes(b"video")
    tail = tmp_path / "tail.jpg"
    tail.write_bytes(b"image")
    monkeypatch.setattr("src.services.video_continuity.extract_tail_frame", lambda *_args: str(tail))

    path, label = continuity_reference(
        state, 1, {"model": "mock", "capability": {"continuity": True}},
        shot_id="scene-1-shot-2", continuity_from_shot_id="scene-1-shot-1",
    )

    assert path == str(tail)
    assert "子镜头" in label
