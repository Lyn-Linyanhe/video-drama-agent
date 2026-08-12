"""Regression coverage for immutable storyboard video task inputs."""

import asyncio
from pathlib import Path

import pytest

from src.api import context as api_context
from src.api.routes import jobs
from src.agents.video_gen_agent import VideoGenerationAgent
from src.graph.state import DramaState
from src.schemas.assets import ShotAsset, ShotProductionState, ShotReference
from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.script import Scene, ScriptBreakdown
from src.schemas.project import CreativeProfile
from src.services.video_capabilities import normalize_video_policy


def test_video_policy_keeps_legacy_price_but_defaults_to_zero():
    """The UI may drop pricing while old snapshots remain readable."""
    policy = normalize_video_policy({"provider": "mock", "model": "mock", "resolution": "720p"})
    assert policy["unit_price_rmb_per_sec"] == 0.0
    legacy = normalize_video_policy({
        "provider": "mock", "model": "mock", "resolution": "720p",
        "unit_price_rmb_per_sec": 0.37,
    })
    assert legacy["unit_price_rmb_per_sec"] == 0.37


class _JobContext:
    def __init__(self, project_id: str):
        self.project_id = project_id
        self.job_id = "snapshot-job-0001"
        self.project_lock = asyncio.Lock()
        self.cancel_event = asyncio.Event()

    def progress(self, *_args, **_kwargs):
        return None

    def quality(self, *_args, **_kwargs):
        return None

    def provider_task(self, *_args, **_kwargs):
        return None

    def check_cancelled(self):
        return None


def _state(reference_path: Path) -> DramaState:
    return DramaState(
        project_id="snapshot-project",
        script=ScriptBreakdown(
            title="test",
            genre="test",
            logline="test",
            duration_sec=5,
            narration_style="none",
            scenes=[Scene(
                scene_id=1,
                duration_sec=5,
                location="room",
                characters=["Hero"],
                action_description="Hero waits.",
                camera_direction="medium",
                emotion_tone="tense",
            )],
        ),
        generation_prompts=GenerationPrompts(
            api_provider="mock",
            shots=[ShotPrompt(scene_id=1, prompt="submitted prompt", duration_sec=5)],
        ),
        shot_productions={
            1: ShotProductionState(
                scene_id=1,
                references=[ShotReference(
                    reference_id="character|Hero|front_full",
                    source="asset",
                    asset_type="character",
                    asset_id="Hero",
                    view_type="front_full",
                    asset_version_id="hero-v2",
                    image_path=str(reference_path),
                )],
            )
        },
    )


def test_video_snapshot_respects_project_audio_mode(tmp_path, monkeypatch):
    """Audio strategy is frozen from the project setting, not only the provider."""
    state = _state(tmp_path / "missing.png")
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: [])

    seedance = normalize_video_policy({"provider": "seedance", "model": "doubao-seedance-2-0-fast-260128", "resolution": "720p"})
    mock = normalize_video_policy({"provider": "mock", "model": "mock", "resolution": "720p"})

    state.creative_profile = CreativeProfile(audio_mode="auto")
    auto_seedance = api_context._build_video_generation_snapshot(state, 1, seedance)
    assert auto_seedance["audio_mode_requested"] == "auto"
    assert auto_seedance["audio_mode_effective"] == "native_dialogue"
    assert auto_seedance["native_audio_requested"] is True

    state.creative_profile = CreativeProfile(audio_mode="external_tts")
    external_seedance = api_context._build_video_generation_snapshot(state, 1, seedance)
    assert external_seedance["audio_mode_effective"] == "external_tts"
    assert external_seedance["native_audio_requested"] is False

    state.creative_profile = CreativeProfile(audio_mode="native_dialogue")
    native_mock = api_context._build_video_generation_snapshot(state, 1, mock)
    assert native_mock["audio_mode_effective"] == "external_tts"
    assert native_mock["native_audio_requested"] is False


def test_multi_reference_prompt_preserves_full_authored_timeline():
    prompt = (
        "【时间轴分镜】 核心分镜意图：GUHENG_EXIT_BEAT 4.8s, medium side shot, "
        "store door opens, Gu Heng exits with a black umbrella, Su Mian hides the envelope. "
        "切镜头，2s，ENVELOPE_INSERT_BEAT, wet kraft envelope remains behind Su Mian, "
        "red wax seal unbroken, surveillance red light blinking. "
        "切镜头，8.2s，DIALOGUE_TWO_SHOT_BEAT, Su Mian asks whether the message was his, "
        "Gu Heng answers no, Su Mian asks why he is here. "
        "【连续锁定】 total duration about 15s, cuts only at written cut points, "
        "do not add new shots, locations, characters, vehicles, or props. "
        + "continuity filler " * 80
    )
    shot = ShotPrompt(scene_id=2, prompt=prompt, duration_sec=15)
    agent = VideoGenerationAgent()

    provider_prompt = agent._build_multi_reference_prompt(
        shot,
        ["tail_frame_tail.jpg", "scene.png", "character.png", "prop.png"],
        [
            "continuity：previous shot tail frame（tail_frame）",
            "scene：storefront rain state（scene_center）",
            "character：Su Mian front_half（front_half）",
            "prop：kraft envelope（front）",
        ],
    )

    assert "【原始时间轴提示词】" in provider_prompt
    assert "GUHENG_EXIT_BEAT" in provider_prompt
    assert "ENVELOPE_INSERT_BEAT" in provider_prompt
    assert "DIALOGUE_TWO_SHOT_BEAT" in provider_prompt
    assert "continuity filler" in provider_prompt
    assert VideoGenerationAgent._has_reference_binding(provider_prompt)


def test_multi_reference_prompt_includes_at_reference_tags():
    shot = ShotPrompt(scene_id=2, prompt="Hero studies the recorder.", duration_sec=5)
    agent = VideoGenerationAgent()

    provider_prompt = agent._build_multi_reference_prompt(
        shot,
        ["tail_frame_tail.jpg", "hero.png", "recorder.png"],
        [
            "@ref01_continuity_tail_frame = continuity: previous shot tail frame (tail_frame)",
            "@ref02_character_front_half = character: Hero front_half (front_half)",
            "@ref03_prop_front = prop: tape recorder (front)",
        ],
    )

    assert "[Reference tags]" in provider_prompt
    assert "@ref01_continuity_tail_frame maps to reference image 1" in provider_prompt
    assert "@ref02_character_front_half maps to reference image 2" in provider_prompt
    assert "@ref03_prop_front" in provider_prompt


def test_video_generation_snapshot_clamps_legacy_production_duration(tmp_path, monkeypatch):
    reference_path = tmp_path / "hero.png"
    reference_path.write_bytes(b"image")
    state = _state(reference_path)
    state.generation_prompts.shots[0].duration_sec = 30
    state.script.scenes[0].duration_sec = 30
    policy = {"provider": "mock", "model": "mock", "resolution": "720p", "unit_price_rmb_per_sec": 0.0}
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: state.shot_productions[1].references)

    snapshot = api_context._build_video_generation_snapshot(state, 1, policy)

    assert snapshot["prepared_shot"]["duration_sec"] == 15
    assert "总时长约15s" in snapshot["provider_prompt"]


def test_seedance_25_snapshot_preserves_capability_duration(tmp_path, monkeypatch):
    reference_path = tmp_path / "hero.png"
    reference_path.write_bytes(b"image")
    state = _state(reference_path)
    state.generation_prompts.shots[0].duration_sec = 30
    state.script.scenes[0].duration_sec = 30
    policy = normalize_video_policy({
        "provider": "seedance",
        "model": "doubao-seedance-2-5-260628",
        "resolution": "1080p",
    })
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: state.shot_productions[1].references)

    snapshot = api_context._build_video_generation_snapshot(state, 1, policy)

    assert snapshot["prepared_shot"]["duration_sec"] == 30


def test_seedance_snapshot_rejects_duration_below_selected_model_minimum(tmp_path, monkeypatch):
    reference_path = tmp_path / "hero.png"
    reference_path.write_bytes(b"image")
    state = _state(reference_path)
    state.generation_prompts.shots[0].duration_sec = 3
    state.script.scenes[0].duration_sec = 3
    policy = normalize_video_policy({
        "provider": "seedance",
        "model": "doubao-seedance-2-0-fast-260128",
        "resolution": "1080p",
    })
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: state.shot_productions[1].references)

    with pytest.raises(Exception, match="supports duration 4-15"):
        api_context._build_video_generation_snapshot(state, 1, policy)


def test_video_generation_snapshot_records_reference_tags(tmp_path, monkeypatch):
    reference_path = tmp_path / "hero.png"
    reference_path.write_bytes(b"image")
    state = _state(reference_path)
    policy = {"provider": "mock", "model": "mock", "resolution": "720p", "unit_price_rmb_per_sec": 0.0}
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: state.shot_productions[1].references)

    snapshot = api_context._build_video_generation_snapshot(state, 1, policy)

    assert snapshot["references"][0]["reference_tag"] == "@ref01_character_front_full"
    assert snapshot["reference_labels"][0].startswith("@ref01_character_front_full = ")
    assert "@ref01_character_front_full maps to reference image 1" in snapshot["provider_prompt"]


def test_seedance_snapshot_records_actual_reference_mode_and_dropped_images(tmp_path, monkeypatch):
    tail_path = tmp_path / "shot_001_tail.jpg"
    character_path = tmp_path / "hero.png"
    tail_path.write_bytes(b"tail")
    character_path.write_bytes(b"image")
    state = _state(character_path)
    policy = {
        "provider": "seedance",
        "model": "doubao-seedance-2-0-fast-260128",
        "resolution": "1080p",
        "unit_price_rmb_per_sec": 0.0,
        "capability": {
            "image_to_video": True,
            "multi_reference": True,
                "continuity": True,
                "supports_first_frame": True,
                "supports_last_frame": True,
                "supports_first_last_frame": True,
            "max_reference_images": 9,
        },
    }
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: state.shot_productions[1].references)
    monkeypatch.setattr("src.services.video_continuity.continuity_reference", lambda *_args: (str(tail_path), "previous tail"))

    snapshot = api_context._build_video_generation_snapshot(state, 1, policy)

    assert snapshot["reference_mode"] == "first_frame"
    assert [item["image_path"] for item in snapshot["references"]] == [str(tail_path)]
    assert snapshot["dropped_references"] == [{
        "path": str(character_path),
        "reason": "first_frame mode cannot mix reference_image",
    }]
    assert snapshot["return_last_frame"] is True


def test_seedance_ordinary_snapshot_does_not_request_provider_tail(tmp_path, monkeypatch):
    reference_path = tmp_path / "hero.png"
    reference_path.write_bytes(b"image")
    state = _state(reference_path)
    policy = normalize_video_policy({
        "provider": "seedance",
        "model": "doubao-seedance-2-0-fast-260128",
        "resolution": "1080p",
    })
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: state.shot_productions[1].references)

    snapshot = api_context._build_video_generation_snapshot(state, 1, policy)

    assert snapshot["reference_mode"] == "multi_reference"
    assert snapshot["return_last_frame"] is False


def test_seedance_legacy_i2v_snapshot_uses_single_first_frame(tmp_path, monkeypatch):
    reference_path = tmp_path / "hero.png"
    reference_path.write_bytes(b"image")
    state = _state(reference_path)
    policy = normalize_video_policy({
        "provider": "seedance",
        "model": "doubao-seedance-1-0-lite-i2v-250428",
        "resolution": "720p",
    })
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: state.shot_productions[1].references)

    snapshot = api_context._build_video_generation_snapshot(state, 1, policy)

    assert snapshot["reference_mode"] == "first_frame"
    assert [item["image_path"] for item in snapshot["references"]] == [str(reference_path)]
    assert snapshot["dropped_references"] == []


def test_happyhorse_reference_snapshot_caps_r2v_to_provider_limit(tmp_path, monkeypatch):
    state = _state(tmp_path / "reference-0.png")
    references = []
    for index in range(12):
        path = tmp_path / f"reference-{index}.png"
        path.write_bytes(b"image")
        references.append(ShotReference(
            reference_id=f"reference-{index}",
            source="asset",
            asset_type="character",
            asset_id=f"Hero-{index}",
            view_type="front_full",
            image_path=str(path),
        ))
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: references)
    policy = normalize_video_policy({"provider": "happyhorse", "model": "happyhorse-1.1-r2v", "resolution": "1080p"})

    snapshot = api_context._build_video_generation_snapshot(state, 1, policy)

    assert len(snapshot["references"]) == 9
    assert len(snapshot["accepted_references"]) == 9
    assert len(snapshot["dropped_references"]) == 3
    assert all("maximum of 9" in item["reason"] for item in snapshot["dropped_references"])


def test_wan_t2v_snapshot_marks_all_reference_images_as_ignored(tmp_path, monkeypatch):
    state = _state(tmp_path / "reference.png")
    reference = ShotReference(
        reference_id="character|Hero|front_full",
        source="asset",
        asset_type="character",
        asset_id="Hero",
        view_type="front_full",
        image_path=str(tmp_path / "reference.png"),
    )
    reference.image_path and Path(reference.image_path).write_bytes(b"image")
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: [reference])
    policy = normalize_video_policy({"provider": "wan", "model": "wan2.6-t2v", "resolution": "1080p"})

    snapshot = api_context._build_video_generation_snapshot(state, 1, policy)

    assert snapshot["reference_mode"] == "text_to_video"
    assert snapshot["references"] == []
    assert snapshot["accepted_references"] == []
    assert snapshot["dropped_references"] == [{
        "path": str(reference.image_path),
        "reason": "wan2.6-t2v does not support reference images",
    }]


@pytest.mark.asyncio
async def test_video_job_uses_and_persists_frozen_snapshot(tmp_path, monkeypatch):
    reference_path = tmp_path / "hero.png"
    reference_path.write_bytes(b"image")
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")
    state = _state(reference_path)
    policy = {"provider": "mock", "model": "mock", "resolution": "720p", "unit_price_rmb_per_sec": 0.0}
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: state.shot_productions[1].references)
    snapshot = api_context._build_video_generation_snapshot(state, 1, policy)
    state.generation_prompts.shots[0].prompt = "later draft must not be sent"

    captured = {}

    class _VideoAgent:
        async def generate_shot(self, shot, reference_paths, **kwargs):
            captured["prompt"] = shot.prompt
            captured["references"] = list(reference_paths)
            captured["provider_prompt"] = kwargs.get("provider_prompt")
            return ShotAsset(scene_id=1, local_path=str(video_path), success=True, duration_sec=5)

    monkeypatch.setattr(jobs, "_load_project_state", lambda _project_id: state)
    monkeypatch.setattr(jobs, "_save_project_state", lambda _state: None)
    monkeypatch.setattr(jobs, "_prepare_auto_references", lambda _state: asyncio.sleep(0))
    monkeypatch.setattr(jobs, "video_agent_for_policy", lambda _policy: _VideoAgent())
    monkeypatch.setattr(jobs, "continuity_reference", lambda *_args: ("", ""))
    monkeypatch.setattr(jobs, "inspect_video", lambda *_args, **_kwargs: {"status": "passed", "technical_checks": []})
    monkeypatch.setattr(jobs, "review_video_subjective", lambda *_args, **_kwargs: asyncio.sleep(0, result={"status": "passed"}))
    monkeypatch.setattr(jobs, "extract_tail_frame", lambda *_args: None)
    monkeypatch.setattr(jobs, "mark_dependents_stale", lambda *_args: None)

    result = await jobs._job_video_generate(
        _JobContext(state.project_id),
        {"scene_id": 1, "generation_snapshot": snapshot},
    )

    assert captured["provider_prompt"] == snapshot["provider_prompt"]
    assert captured["references"] == [item["image_path"] for item in snapshot["references"]]
    assert state.shot_productions[1].video_versions[-1].generation_snapshot == snapshot
    assert result["generation_snapshot"] == snapshot


@pytest.mark.asyncio
async def test_video_job_prepares_references_for_pending_snapshot(tmp_path, monkeypatch):
    reference_path = tmp_path / "hero.png"
    reference_path.write_bytes(b"image")
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"video")
    state = _state(reference_path)
    policy = {"provider": "mock", "model": "mock", "resolution": "720p", "unit_price_rmb_per_sec": 0.0}
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: state.shot_productions[1].references)
    pending = {
        "scene_id": 1,
        "references_pending": True,
        "policy": policy,
    }
    refreshed = api_context._build_video_generation_snapshot(state, 1, policy)
    refreshed["references_pending"] = False
    pending["prepared_shot"] = {**refreshed["prepared_shot"], "prompt": "frozen prompt"}
    pending["provider_prompt"] = "frozen provider prompt"
    expected = {
        **refreshed,
        "prepared_shot": pending["prepared_shot"],
        "provider_prompt": pending["provider_prompt"],
    }
    calls = []
    captured = {}

    async def prepare_references(_state):
        calls.append(True)

    class _VideoAgent:
        async def generate_shot(self, shot, reference_paths, **kwargs):
            captured["references"] = list(reference_paths)
            return ShotAsset(scene_id=1, local_path=str(video_path), success=True, duration_sec=5)

    monkeypatch.setattr(jobs, "_prepare_auto_references", prepare_references)
    monkeypatch.setattr(jobs, "_build_video_generation_snapshot", lambda *_args: refreshed)
    monkeypatch.setattr(jobs, "_load_project_state", lambda _project_id: state)
    monkeypatch.setattr(jobs, "_save_project_state", lambda _state: None)
    monkeypatch.setattr(jobs, "video_agent_for_policy", lambda _policy: _VideoAgent())
    monkeypatch.setattr(jobs, "inspect_video", lambda *_args, **_kwargs: {"status": "passed", "technical_checks": []})
    monkeypatch.setattr(jobs, "review_video_subjective", lambda *_args, **_kwargs: asyncio.sleep(0, result={"status": "passed"}))
    monkeypatch.setattr(jobs, "extract_tail_frame", lambda *_args: None)
    monkeypatch.setattr(jobs, "mark_dependents_stale", lambda *_args: None)

    result = await jobs._job_video_generate(
        _JobContext(state.project_id),
        {"scene_id": 1, "generation_snapshot": pending},
    )

    assert calls == [True]
    assert captured["references"] == [str(reference_path)]
    assert result["generation_snapshot"]["prepared_shot"]["prompt"] == "frozen prompt"
    assert result["generation_snapshot"]["provider_prompt"] == "frozen provider prompt"
    assert result["generation_snapshot"] == expected


@pytest.mark.asyncio
async def test_video_job_rejects_snapshot_when_a_frozen_reference_file_is_missing(tmp_path, monkeypatch):
    reference_path = tmp_path / "missing.png"
    state = _state(reference_path)
    snapshot = {
        "scene_id": 1,
        "prepared_shot": ShotPrompt(scene_id=1, prompt="submitted", duration_sec=5).model_dump(),
        "provider_prompt": "submitted",
        "provider_negative_prompt": "",
        "references": [{
            "reference_id": "character|Hero|front_full",
            "image_path": str(reference_path),
            "asset_type": "character",
            "asset_id": "Hero",
            "view_type": "front_full",
        }],
        "policy": {"provider": "mock", "model": "mock", "resolution": "720p", "unit_price_rmb_per_sec": 0.0},
    }
    monkeypatch.setattr(jobs, "_load_project_state", lambda _project_id: state)
    monkeypatch.setattr(jobs, "_save_project_state", lambda _state: None)

    with pytest.raises(ValueError, match=r"character\|Hero\|front_full"):
        await jobs._job_video_generate(
            _JobContext(state.project_id),
            {"scene_id": 1, "generation_snapshot": snapshot},
        )
