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
from src.schemas.script import DialogueLine, Scene, ScriptBreakdown
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
    with pytest.raises(ValueError, match="不支持视频模型原生对白"):
        api_context._build_video_generation_snapshot(state, 1, mock)


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
    assert snapshot["reference_labels"][0].startswith("@character:Hero:front_full -> @ref01_character_front_full = ")
    assert "@character:Hero:front_full maps to reference image 1" in snapshot["provider_prompt"]
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
    monkeypatch.setattr("src.services.video_capabilities.dashscope_available", lambda: True)
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
    # A legacy saved task may still carry duplicated metadata for the same
    # image. The job executor must not upload it twice.
    snapshot["references"].append(dict(snapshot["references"][0]))
    snapshot["reference_labels"].append(snapshot["reference_labels"][0])
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

    with pytest.raises(ValueError, match="snapshot_prompt_mismatch"):
        await jobs._job_video_generate(
            _JobContext(state.project_id),
            {"scene_id": 1, "generation_snapshot": snapshot},
        )
    assert captured == {}


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
    expected = {**refreshed}
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
    assert result["generation_snapshot"]["prepared_shot"]["prompt"] == refreshed["prepared_shot"]["prompt"]
    assert result["generation_snapshot"]["provider_prompt"] == refreshed["provider_prompt"]
    assert result["generation_snapshot"] == expected


@pytest.mark.asyncio
async def test_video_job_rejects_snapshot_when_a_frozen_reference_file_is_missing(tmp_path, monkeypatch):
    reference_path = tmp_path / "missing.png"
    state = _state(reference_path)
    snapshot = {
        "scene_id": 1,
        "source_prompt": "submitted prompt",
        "source_duration_sec": 5,
        "effective_duration_sec": 5,
        "raw_prompt": "submitted prompt",
        "prepared_shot": ShotPrompt(scene_id=1, prompt="submitted prompt", duration_sec=5).model_dump(),
        "provider_prompt": "submitted prompt",
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
    from src.services.prompt_fidelity_gate import generation_snapshot_digest
    snapshot["prompt_snapshot_digest"] = generation_snapshot_digest(snapshot)
    monkeypatch.setattr(jobs, "_load_project_state", lambda _project_id: state)
    monkeypatch.setattr(jobs, "_save_project_state", lambda _state: None)

    with pytest.raises(ValueError, match=r"character\|Hero\|front_full"):
        await jobs._job_video_generate(
            _JobContext(state.project_id),
            {"scene_id": 1, "generation_snapshot": snapshot},
        )


def test_video_snapshot_keeps_dialogue_plan_for_external_tts(tmp_path, monkeypatch):
    state = _state(tmp_path / "missing.png")
    state.script.scenes[0].dialogue = [
        DialogueLine(character="Hero", line="Do not forget me.", emotion="quiet"),
    ]
    state.creative_profile = CreativeProfile(audio_mode="external_tts")
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: [])
    policy = normalize_video_policy({
        "provider": "seedance",
        "model": "doubao-seedance-2-0-fast-260128",
        "resolution": "720p",
    })

    snapshot = api_context._build_video_generation_snapshot(state, 1, policy)

    assert snapshot["native_audio_requested"] is False
    assert snapshot["audio_mode_effective"] == "external_tts"
    assert snapshot["dialogue_plan"] == [{
        "line_id": "1:1",
        "character": "Hero",
        "text": "Do not forget me.",
        "emotion": "quiet",
    }]
    assert "【对白】" not in snapshot["provider_prompt"]


def test_happyhorse_native_audio_mode_is_best_effort_not_rejected(tmp_path):
    from src.api import context as api_context
    from src.schemas.project import CreativeProfile
    from src.services.video_capabilities import normalize_video_policy

    test_state = _state(tmp_path / 'reference.png')
    test_state.creative_profile = CreativeProfile(audio_mode='native_dialogue')
    test_state.script.scenes[0].dialogue = [DialogueLine(character='Hero', line='Hello')]
    policy = normalize_video_policy({
        'provider': 'happyhorse', 'model': 'happyhorse-1.1-r2v', 'resolution': '1080p',
    })
    snapshot = api_context._build_video_generation_snapshot(test_state, 1, policy)

    assert snapshot['audio_mode_effective'] == 'native_dialogue'
    assert snapshot['native_audio_requested'] is True
    assert snapshot['audio_capability_status'] == 'best_effort'
    assert '【对白】' in snapshot['provider_prompt']


@pytest.mark.asyncio
async def test_stopping_child_video_job_does_not_cancel_other_child_state(tmp_path, monkeypatch):
    reference_path = tmp_path / "ref.png"
    reference_path.write_bytes(b"image")
    state = _state(reference_path)
    state.shot_productions[1].generation_status = "generating"
    state.shot_productions[1].generation_status_by_shot_id = {
        "scene-1-shot-1": "generating", "scene-1-shot-2": "generating"
    }
    monkeypatch.setattr(jobs, "_load_project_state", lambda _project_id: state)
    monkeypatch.setattr(jobs, "_save_project_state", lambda _state: None)

    await jobs._mark_video_job_stopped("project", 1, "failed", "scene-1-shot-2")

    assert state.shot_productions[1].generation_status_by_shot_id["scene-1-shot-2"] == "canceled"
    assert state.shot_productions[1].generation_status_by_shot_id["scene-1-shot-1"] == "generating"
    assert state.shot_productions[1].generation_status == "generating"



def test_prepare_shot_request_rejects_selected_seedance_duration_over_cap():
    class _Seedance:
        provider_name = "seedance"
        model = "doubao-seedance-2-0-fast-260128"
        resolution = "720p"

    scene = Scene(
        scene_id=1,
        duration_sec=20,
        location="室内",
        characters=["甲"],
        action_description="甲完成动作。",
        camera_direction="中景",
        emotion_tone="专注",
    )
    script = ScriptBreakdown(
        title="test",
        genre="剧情",
        logline="test",
        duration_sec=20,
        narration_style="无旁白",
        scenes=[scene],
    )
    shot = ShotPrompt(scene_id=1, prompt="甲完成动作。", duration_sec=20)
    agent = VideoGenerationAgent.__new__(VideoGenerationAgent)
    agent.api_client = _Seedance()

    with pytest.raises(ValueError, match=r"20.*15|15"):
        agent.prepare_shot_request(
            shot,
            generation_prompts=GenerationPrompts(api_provider="seedance", shots=[shot]),
            prompt_policy=None,
            script=script,
        )


def test_child_shot_snapshot_only_appends_its_bound_dialogue(tmp_path, monkeypatch):
    state = _state(tmp_path / "missing.png")
    state.script.scenes[0].dialogue = [
        DialogueLine(character="Hero", line="First line."),
        DialogueLine(character="Partner", line="Second line."),
    ]
    state.creative_profile = CreativeProfile(audio_mode="native_dialogue")
    child = ShotPrompt(
        scene_id=1,
        shot_id="scene-1-shot-2",
        parent_scene_id=1,
        shot_order=2,
        dialogue_ids=["scene-1-dialogue-2"],
        prompt="Only the second exchange happens in this child shot.",
        duration_sec=5,
    )
    monkeypatch.setattr(api_context, "_shot_prompt", lambda *_args, **_kwargs: child)
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_args, **_kwargs: [])
    policy = normalize_video_policy({
        "provider": "happyhorse",
        "model": "happyhorse-1.1-r2v",
        "resolution": "1080p",
    })

    snapshot = api_context._build_video_generation_snapshot(
        state,
        1,
        policy,
        shot_id=child.shot_id,
    )

    assert snapshot["dialogue_plan"] == [{
        "line_id": "1:2",
        "character": "Partner",
        "text": "Second line.",
        "emotion": "",
    }]
    assert "Second line." in snapshot["provider_prompt"]
    assert "First line." not in snapshot["provider_prompt"]


def test_multi_reference_prompt_maps_each_reference_once_and_does_not_repeat_execution_prompt():
    shot = ShotPrompt(
        scene_id=1,
        prompt="【动作时间轴】0-5s 德拉把白金表链放在掌心。",
        duration_sec=5,
    )
    labels = [
        "@character:德拉:front_half -> @ref01_character_front_half = 角色：德拉半身正面",
        "@prop:白金表链:front -> @ref02_prop_front = 道具：白金表链正面",
    ]

    rendered = VideoGenerationAgent()._build_multi_reference_prompt(
        shot, ["della.png", "chain.png"], reference_labels=labels,
    )

    assert rendered.count("@character:德拉:front_half maps to reference image 1") == 1
    assert rendered.count("@character:德拉:front_half") == 1
    assert rendered.count("@ref01_character_front_half") == 1
    assert rendered.count("@ref01_character_front_half maps to reference image 1") == 1
    assert rendered.count("@prop:白金表链:front maps to reference image 2") == 1
    assert rendered.count("@ref02_prop_front maps to reference image 2") == 1
    assert rendered.count("【动作时间轴】0-5s 德拉把白金表链放在掌心。") == 1
    assert "[图层分工]" not in rendered
    assert "[连续性约束]" not in rendered


def test_multi_reference_prompt_does_not_duplicate_existing_structure_rules():
    shot = ShotPrompt(
        scene_id=1, duration_sec=5,
        prompt=(
            "【动作时间轴】0-5s 德拉抬头。\n"
            "【视频质量】电影级竖屏9:16镜头。\n"
            "【单镜头连续拍摄规则】保持同一机位连续完成动作。"
        ),
    )

    rendered = VideoGenerationAgent()._build_multi_reference_prompt(
        shot, ["della.png"], reference_labels=["character: 德拉 front_half"],
    )

    assert rendered.count("【单镜头连续拍摄规则】") == 1



def test_multi_reference_prompt_structured_project_prioritizes_execution_and_omits_identity_dump():
    from src.schemas.script import DialogueLine, Scene, ScriptBreakdown

    scene = Scene(
        scene_id=8, duration_sec=6, location="破旧廉租公寓", characters=["吉姆", "德拉"],
        dialogue=[DialogueLine(character="吉姆", line="你已经把头发剪掉了？")],
        action_description="吉姆推门进屋，站在门口；德拉握着白金表链起身走近，表情从期待变为慌乱。",
        camera_direction="固定侧面中景", emotion_tone="紧张",
    )
    script = ScriptBreakdown(
        title="麦琪的礼物", genre="剧情", logline="", duration_sec=6,
        narration_style="无旁白", scenes=[scene],
    )
    shot = ShotPrompt(
        scene_id=8, shot_id="scene-8-shot-1", dialogue_ids=["scene-8-dialogue-1"], duration_sec=6,
        prompt=(
            "【项目视觉】电影级真人写实。\n"
            "【出镜角色与身份锚点】吉姆：非常长的身份档案。德拉：非常长的身份档案。\n"
            "【动作时间轴】0-6s 吉姆推门进屋，站在门口；德拉握着白金表链起身走近，表情从期待变为慌乱。\n"
            "【镜头语言】固定侧面中景。\n"
            "【环境】破旧廉租公寓，晚间暖黄灯光。\n"
            "【对白】吉姆：\"你已经把头发剪掉了？\""
        ),
    )
    rendered = VideoGenerationAgent()._build_multi_reference_prompt(
        shot,
        ["scene.png", "jim.png", "della.png", "chain.png"],
        [
            "@scene:loc-8:scene_center -> 场景：破旧廉租公寓",
            "@character:吉姆:left_profile -> 角色：吉姆",
            "@character:德拉:left_profile -> 角色：德拉",
            "@prop:prop_watch_chain:front -> 道具：白金表链",
        ],
        script=script,
    )

    assert "【执行提示词】" in rendered
    # 自动推导的执行核心只是动作时间轴首句的逐字前缀时不再单列，
    # 因此这里验证执行层整体出现在环境段之前即可。
    assert rendered.index("【动作时间轴】") < rendered.index("【环境与视觉风格】")
    # Approved design: the spoken line is embedded inline in the action
    # timeline, not re-stated in a separate 【关键对白（必须出现）】 block.
    assert "【关键对白（必须出现）】" not in rendered
    assert "这时候吉姆" in rendered
    assert rendered.index("这时候吉姆") < rendered.index("【参考图约束】")
    assert "非常长的身份档案" not in rendered
    assert "吉姆推门进屋" in rendered
    assert "白金表链" in rendered
    assert rendered.count("你已经把头发剪掉了？") == 1


def test_snapshot_keeps_one_automatic_character_view_but_preserves_manual_extra(tmp_path, monkeypatch):
    jim_profile = tmp_path / "jim_profile.png"
    jim_full = tmp_path / "jim_full.png"
    della_profile = tmp_path / "della_profile.png"
    della_full = tmp_path / "della_full.png"
    scene = tmp_path / "scene.png"
    chain = tmp_path / "chain.png"
    extra = tmp_path / "extra.png"
    for path in (jim_profile, jim_full, della_profile, della_full, scene, chain, extra):
        path.write_bytes(b"image")
    state = DramaState(
        project_id="snapshot-project",
        script=ScriptBreakdown(
            title="test", genre="test", logline="test", duration_sec=6, narration_style="none",
            scenes=[Scene(
                scene_id=8, duration_sec=6, location="公寓", characters=["吉姆", "德拉"],
                action_description="侧面中景，吉姆推门进屋。", camera_direction="侧面中景", emotion_tone="紧张",
            )],
        ),
        generation_prompts=GenerationPrompts(
            api_provider="happyhorse",
            shots=[ShotPrompt(scene_id=8, shot_id="scene-8-shot-1", prompt="侧面中景，吉姆推门进屋。", duration_sec=6)],
        ),
        shot_productions={
            8: ShotProductionState(
                scene_id=8,
                references=[
                    ShotReference(reference_id="scene|loc|scene_center", source="asset", asset_type="scene", asset_id="loc", view_type="scene_center", image_path=str(scene)),
                    ShotReference(reference_id="character|吉姆|left_profile", source="asset", asset_type="character", asset_id="吉姆", view_type="left_profile", image_path=str(jim_profile)),
                    ShotReference(reference_id="character|吉姆|front_full", source="asset", asset_type="character", asset_id="吉姆", view_type="front_full", image_path=str(jim_full)),
                    ShotReference(reference_id="character|德拉|left_profile", source="asset", asset_type="character", asset_id="德拉", view_type="left_profile", image_path=str(della_profile)),
                    ShotReference(reference_id="character|德拉|front_full", source="asset", asset_type="character", asset_id="德拉", view_type="front_full", image_path=str(della_full)),
                    ShotReference(reference_id="prop|prop_watch_chain|front", source="asset", asset_type="prop", asset_id="prop_watch_chain", view_type="front", image_path=str(chain)),
                    ShotReference(reference_id="manual-extra", source="manual", asset_type="prop", asset_id="extra", view_type="front", image_path=str(extra)),
                ],
            )
        },
    )
    monkeypatch.setattr(api_context, "_reconcile_shot_references", lambda *_: state.shot_productions[8].references)
    policy = normalize_video_policy({"provider": "happyhorse", "model": "happyhorse-1.1-r2v", "resolution": "1080p"})

    snapshot = api_context._build_video_generation_snapshot(state, 8, policy, shot_id="scene-8-shot-1")
    refs = snapshot["references"]
    character_views = {(item["asset_id"], item["view_type"]) for item in refs if item["asset_type"] == "character"}

    assert ("吉姆", "left_profile") in character_views
    assert ("德拉", "left_profile") in character_views
    assert ("吉姆", "front_full") not in character_views
    assert ("德拉", "front_full") not in character_views
    assert any(item["source"] == "manual" for item in refs)
    assert any(item["asset_type"] == "scene" for item in refs)
    assert any(item["asset_id"] == "prop_watch_chain" for item in refs)


def test_multi_reference_prompt_merges_spatial_rule_without_duplication():
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

    # The standalone Chinese spatial-rule sentence was removed together with the
    # tail-frame lock rewrite.  The hierarchy rule must now be stated exactly
    # once, inside the [Reference hierarchy] block, and name reference 1 as the
    # highest-priority continuity source.
    rule = "Reference 1 is the continuity tail and has highest priority"
    assert provider_prompt.count(rule) == 1
    assert "[Reference hierarchy]" in provider_prompt
    tail = provider_prompt.split("[Reference hierarchy]", 1)[1].split("【", 1)[0]
    assert rule in tail
    # The old Chinese rule must not come back as a duplicate line.
    assert "镜头关系必须延续参考图的空间位置和运动方向" not in provider_prompt
    # Scene references must never be allowed to override the locked tail camera.
    assert "Scene references must not override the tail-frame camera" in provider_prompt
