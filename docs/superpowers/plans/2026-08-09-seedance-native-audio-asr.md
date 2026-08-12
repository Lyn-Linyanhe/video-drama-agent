# Seedance Native Dialogue, Post-Mixed BGM, and Optional ASR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make new Seedance shots request native generated dialogue/audio, keep BGM and SFX in the project FFmpeg mix, and optionally align subtitles with local ASR without blocking internal previews.

**Architecture:** Extend the existing video result and persisted shot-version schemas with an audio-source snapshot. Seedance receives an explicit `generate_audio` flag and a dialogue block derived from the current scene; non-Seedance clients continue through the current TTS path. The assembly layer treats provider audio as a source track, mixes project BGM/SFX with ducking, and an optional local ASR adapter writes correction metadata while falling back to the script timeline on every failure.

**Tech Stack:** Python 3.12+, Pydantic, httpx, existing JobStore/LangGraph pipeline, FFmpeg/MoviePy, optional `faster-whisper`, pytest.

## Global Constraints

- Seedance native audio is enabled only for Seedance jobs; existing HappyHorse/Wan/Mock jobs retain external TTS behavior.
- BGM and SFX are always added by the project FFmpeg mix and must never be delegated to a provider.
- Original script dialogue remains immutable; provider prompt snapshots may include an adapted dialogue block and retain its source line IDs.
- ASR is optional and must not download models automatically, fail a video job, delete media, or block internal-preview export.
- Existing `AudioAssets`, `DialogueAudio`, `tts_audio_path`, video APIs, old projects, and non-Seedance history remain readable.
- No new cloud infrastructure or paid ASR service is introduced.

---

### Task 1: Add failing contract tests and audio metadata schemas

**Files:**
- Create: `tests/test_native_audio_contract.py`
- Modify: `src/api_clients/base.py`
- Modify: `src/schemas/assets.py`
- Modify: `src/schemas/audio.py`

**Interfaces:**
- `VideoGenResult` exposes `audio_source`, `native_audio_enabled`, `native_audio_path`, and `audio_present`.
- `ShotAsset` and `ShotVideoVersion` persist the same source metadata with backward-compatible defaults.
- `AudioAssets.source_audio_enabled` remains optional for legacy state and is true only when the project actually contains provider audio.

- [ ] **Step 1: Write the failing test**

```python
def test_video_result_and_shot_version_keep_native_audio_metadata():
    result = VideoGenResult(success=True, audio_source="native", native_audio_enabled=True,
                            native_audio_path="clip.mp4", audio_present=True)
    asset = ShotAsset(scene_id=1, success=True, **result.__dict__)
    version = ShotVideoVersion(scene_id=1, success=True, version_id="v1", created_at="", **asset.model_dump())
    assert version.audio_source == "native"
    assert version.native_audio_enabled is True
    assert version.audio_present is True

def test_legacy_audio_state_defaults_to_unknown_source_audio():
    assert AudioAssets(scenes=[]).source_audio_enabled is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_native_audio_contract.py -q`

Expected: FAIL because the new metadata fields are not accepted.

- [ ] **Step 3: Write minimal implementation**

Add the fields with empty/false defaults to `VideoGenResult`, `ShotAsset`, and `ShotVideoVersion`; do not change existing required fields or validation ranges.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_native_audio_contract.py -q`

Expected: PASS.

### Task 2: Enable and persist Seedance native audio

**Files:**
- Create: `tests/test_seedance_native_audio.py`
- Modify: `src/api_clients/seedance.py`
- Modify: `src/agents/video_gen_agent.py`

**Interfaces:**
- `SeedanceVideoClient.generate_video(..., generate_audio: bool = True)` sends `generate_audio` in the Ark body.
- `VideoGenerationAgent.generate_shot(..., generate_audio: bool | None = None)` forwards the choice only to Seedance.
- Seedance output with an embedded audio stream reports `audio_source="native"`; an explicit provider audio URL is downloaded beside the video when available.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_seedance_request_enables_native_audio_and_returns_source(monkeypatch):
    # Fake submit/poll/download and inspect the JSON body.
    result = await SeedanceVideoClient(api_key="k").generate_video("scene", generate_audio=True)
    assert captured_body["generate_audio"] is True
    assert result.audio_source == "native"
    assert result.native_audio_enabled is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_seedance_native_audio.py::test_seedance_request_enables_native_audio_and_returns_source -q`

Expected: FAIL because `generate_video` currently drops the flag and returns no source metadata.

- [ ] **Step 3: Write minimal implementation**

Pass `generate_audio` to `_submit_task`, default it to true for Seedance, extract `audio_url`/`audio` from all existing response shapes, download it into the project output directory, and detect an embedded stream with the existing media utilities. Add a `generate_audio` request flag in `VideoGenerationAgent._retry_loop`, defaulting to true only when `provider_name == "seedance"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_seedance_native_audio.py tests/test_seedance_client.py -q`

Expected: PASS, including existing cancellation/reference tests.

### Task 3: Build the Seedance dialogue prompt and freeze the task snapshot

**Files:**
- Create: `src/services/native_audio.py`
- Create: `tests/test_native_dialogue_prompt.py`
- Modify: `src/api/context.py`
- Modify: `src/api/routes/jobs.py`

**Interfaces:**
- `build_native_dialogue_block(scene) -> tuple[str, list[dict]]` returns a stable Chinese prompt block and source line IDs.
- `append_native_dialogue_prompt(prompt, scene) -> str` appends the block without modifying the persisted script.
- Video snapshots include `native_audio_requested`, `dialogue_plan`, and `audio_policy`.

- [ ] **Step 1: Write the failing test**

```python
def test_seedance_snapshot_contains_scene_specific_dialogue_plan():
    block, lines = build_native_dialogue_block(scene_with_dialogue(2))
    assert "【对白】" in block
    assert lines[0]["line_id"] == "2:1"
    assert "角色名" in block
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_native_dialogue_prompt.py -q`

Expected: FAIL because the helper and snapshot fields do not exist.

- [ ] **Step 3: Write minimal implementation**

Create the helper with escaped/truncated line text, speaker, emotion, and source IDs. In `_build_video_generation_snapshot`, after prompt composition, append the block only for Seedance and store the exact plan. In `_job_video_generate`, pass `generate_audio=bool(snapshot["native_audio_requested"])` and keep the adapted block in `generation_snapshot`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_native_dialogue_prompt.py tests/test_video_generation_snapshot.py -q`

Expected: PASS and snapshots for other providers remain unchanged.

### Task 4: Route native audio and external TTS correctly

**Files:**
- Create: `tests/test_audio_source_routing.py`
- Modify: `src/agents/audio_agent.py`
- Modify: `src/api/routes/jobs.py`
- Modify: `src/services/audio_timeline.py`

**Interfaces:**
- `AudioAgent.run(..., native_scene_audio: dict[int, dict] | None = None)` skips TTS only for successful native-audio scenes and preserves existing TTS for all other scenes.
- `AudioAssets.source_audio_enabled` is computed from actual native tracks, never hard-coded false.
- Native scenes are represented by a source track marker; their script dialogue remains available for subtitles and fallback.

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.asyncio
async def test_seedance_native_scene_does_not_generate_duplicate_tts(monkeypatch):
    audio = await AudioAgent().run(script, assets, native_scene_audio={1: {"path": "video.mp4", "enabled": True}})
    assert all(not line.audio_path for line in audio.scenes[0].dialogue_audios)
    assert audio.source_audio_enabled is True

@pytest.mark.asyncio
async def test_non_seedance_scene_still_uses_tts():
    audio = await AudioAgent().run(script, assets, native_scene_audio={})
    assert audio.scenes[0].dialogue_audios[0].provider
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_audio_source_routing.py -q`

Expected: FAIL because AudioAgent always generates TTS and hard-codes `source_audio_enabled=False`.

- [ ] **Step 3: Write minimal implementation**

Add native source metadata to `SceneAudio`, reconcile native scenes before scheduling TTS, and set `source_audio_enabled` to `True` only when at least one native track exists; leave it `None` for legacy states and `False` for a fully TTS-generated project.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_audio_source_routing.py tests/test_audio_pipeline.py -q`

Expected: PASS with existing TTS, timeline, and legacy tests.

### Task 5: Add optional ASR alignment with script fallback

**Files:**
- Create: `src/services/asr.py`
- Create: `tests/test_asr_subtitle_alignment.py`
- Modify: `src/schemas/audio.py`
- Modify: `src/api/routes/jobs.py`

**Interfaces:**
- `align_native_subtitles(video_path, dialogue_plan, *, enabled=True, model_name="small") -> dict` returns `{status, segments, confidence, changed_lines, error, source}`.
- The adapter uses `faster_whisper` only if importable and never downloads a model automatically; it returns `unavailable` otherwise.
- A low-confidence/error result leaves existing script subtitle timing untouched and marks `subtitle_correction_status="fallback"`.

- [ ] **Step 1: Write the failing test**

```python
def test_asr_changed_dialogue_is_reported_without_overwriting_script():
    result = reconcile_asr_segments([{"text": "你好啊", "start": 0, "end": 1, "confidence": .9}],
                                    [{"line_id": "1:1", "text": "你好", "start_ms": 0, "end_ms": 1000}])
    assert result["status"] == "changed"
    assert result["changed_lines"][0]["script_text"] == "你好"
    assert result["segments"][0]["text"] == "你好啊"

def test_asr_unavailable_falls_back_to_script():
    assert align_native_subtitles("missing.mp4", [], enabled=True)["status"] == "fallback"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_asr_subtitle_alignment.py -q`

Expected: FAIL because no adapter or reconciliation function exists.

- [ ] **Step 3: Write minimal implementation**

Implement import-gated `faster_whisper` transcription and deterministic text/time matching. Add ASR status, provider, model, segments, and changed-line metadata to `AudioAssets` and `SceneAudio`; invoke it after a successful native Seedance video is saved. Catch every adapter/model/media exception, persist fallback metadata, and continue the job.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_asr_subtitle_alignment.py tests/test_audio_pipeline.py -q`

Expected: PASS without requiring a Whisper model or network access.

### Task 6: Keep BGM/SFX as post-mix and make native audio visible in assembly/UI

**Files:**
- Create: `tests/test_native_audio_assembly.py`
- Modify: `src/agents/assembly_agent.py`
- Modify: `src/schemas/audio.py`
- Modify: `web/js/components/audio-timeline.js`
- Modify: `web/js/pages/production-actions.js`

**Interfaces:**
- FFmpeg uses the provider audio stream when `source_audio_enabled` is true, overlays project BGM/SFX, and applies existing ducking/loudness normalization.
- Subtitle generation chooses ASR segments when status is `ready`/`changed`, otherwise uses script timeline and labels the fallback state.
- UI labels Seedance tracks as `原生对白` and exposes ASR status and the existing subtitle mode/export controls.

- [ ] **Step 1: Write the failing test**

```python
def test_native_audio_is_kept_while_bgm_is_added(monkeypatch, tmp_path):
    # Capture FFmpeg command from the existing mixer.
    AssemblyAgent()._mux_ffmpeg_audio_and_subtitles(script, native_audio, "video.mp4", tmp_path, True)
    assert "[0:a]" in captured_command
    assert "sidechaincompress" in captured_command

def test_subtitle_writer_uses_asr_segments_when_ready():
    path = AssemblyAgent()._write_subtitle_file(script, audio_by_scene, tmp_path, subtitle_mode="dialogue")
    assert "recognized text" in path.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_native_audio_assembly.py -q`

Expected: FAIL because native source metadata is ignored and subtitles only read `DialogueAudio`.

- [ ] **Step 3: Write minimal implementation**

Use `source_audio_enabled`/`SceneAudio.native_audio_path` to include the video source track, retain BGM/SFX filters, and switch subtitle entries to persisted ASR segments when available. Keep the existing `off/dialogue/speaker` behavior and warning path.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_native_audio_assembly.py tests/test_audio_pipeline.py -q`

Expected: PASS.

### Task 7: Regression coverage, compatibility migration, and documentation

**Files:**
- Create: `tests/test_native_audio_regression.py`
- Modify: `README.md`
- Modify: `requirements.txt` (optional comment only; do not make Whisper mandatory)

- [ ] **Step 1: Write the failing test**

```python
def test_old_project_without_native_fields_still_loads_and_uses_tts():
    state = _state_from_json(old_state_json)
    assert state.audio_assets.source_audio_enabled is None
    assert state.subtitle_mode == "dialogue"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_native_audio_regression.py -q`

Expected: FAIL only if compatibility defaults are missing.

- [ ] **Step 3: Write minimal implementation**

Document `pip install faster-whisper` as optional, the no-network model requirement, Seedance native-dialogue behavior, BGM ownership, ASR fallback, and how to export script-vs-original subtitles. Add explicit migration defaults in state deserialization only where Pydantic cannot supply them.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest -q`

Expected: all existing and new tests pass; then run `node --check` on changed frontend modules.

## Self-Review

- Seedance request flag, prompt dialogue source IDs, video/audio metadata, TTS routing, BGM post-mix, ASR confidence/fallback, UI status, old-project compatibility, and full regression testing are each covered by a task.
- No task requires a new provider or infrastructure; `faster-whisper` remains optional and never auto-downloads a model.
- All interfaces use the existing task, project-state, SSE, and FFmpeg paths so task progress and local asset persistence remain unchanged.
