# 音频工作区与转场简化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 删除无真实计费依据的前端单价控件，令新视频任务使用硬切，并将音频编辑、字幕和混音预览整理为一个稳定且时间一致的工作区。

**Architecture:** 保留后端历史价格与转场字段，只改变新任务默认策略和前端呈现。新增一个纯函数渲染时间表服务，统一最终成片、音频预览和字幕导出的镜头起点；生产工作台将音频从视频预览侧栏移到独立的项目音频工作区，项目级音色/素材只渲染一次，当前镜头使用局部时间轴。

**Tech Stack:** FastAPI/Pydantic、Python 服务层、FFmpeg、原生 ES Modules、现有 SSE 任务系统、pytest。

## Global Constraints

- 不改变现有 `/api/*` 路径、SQLite 项目状态、`output/` 资产目录和任务类型。
- `unit_price_rmb_per_sec`、`estimated_cost_rmb` 和历史 `fade/dissolve` 数据继续可读；新界面不再展示新金额估算或转场编辑控件。
- Qwen3-TTS、MiniMax、Edge-TTS、逐句版本、人工 BGM/SFX 覆盖和字幕 SRT 导出能力必须保留。
- 新项目/新解析剧本使用 `cut`；现有 schema 的 `transition_duration_sec` 保留合法值，但硬切任务忽略它。
- 音频预览、字幕导出和正式成片必须使用同一个实际镜头时间表。
- 页面只允许一个音频工作区内部滚动容器，后台事件不能重建正在编辑的输入框或音频节点。

---

### Task 1: 建立价格与转场兼容回归测试

**Files:**
- Modify: `tests/test_video_generation_snapshot.py`
- Modify: `tests/test_video_continuity_policy.py`
- Modify: `tests/test_modular_app_contract.py`

**Interfaces:**
- Consumes: 现有 `normalize_video_policy()`、`ProductionShotUpdateRequest`、生产页面模块文本和视频渲染器。
- Produces: 后续实现必须满足的价格隐藏、硬切默认和历史转场兼容契约。

- [ ] **Step 1: Add failing backend tests**

```python
def test_video_policy_keeps_legacy_price_but_defaults_to_zero():
    policy = normalize_video_policy({"provider": "mock", "model": "mock", "resolution": "720p"})
    assert policy["unit_price_rmb_per_sec"] == 0.0
    legacy = normalize_video_policy({
        "provider": "mock", "model": "mock", "resolution": "720p",
        "unit_price_rmb_per_sec": 0.37,
    })
    assert legacy["unit_price_rmb_per_sec"] == 0.37


def test_legacy_scene_transition_field_remains_readable():
    scene = Scene(
        scene_id=2, duration_sec=3, location="room", characters=[], dialogue=[],
        action_description="a", camera_direction="wide", emotion_tone="calm",
        transition_from_prev="dissolve",
    )
    assert scene.transition_from_prev == "dissolve"
```

The existing `test_transition_renderer_uses_xfade_and_low_source_audio` continues to verify actual legacy FFmpeg rendering without changing its historical dissolve fixture.

- [ ] **Step 2: Add failing frontend contract assertions**

```python
def test_production_page_does_not_render_price_or_transition_editors():
    source = Path("web/js/pages/production-actions.js").read_text(encoding="utf-8")
    assert "production-video-price" not in source
    assert "production-transition-duration" not in source
    assert "production-transition\"" not in source


def test_audio_workspace_has_single_mount_and_subtitle_selector():
    source = Path("web/js/components/audio-timeline.js").read_text(encoding="utf-8")
    assert "production-audio-workspace" in source
    assert "subtitle-mode" in source
```

- [ ] **Step 3: Run the focused tests and confirm they fail**

Run:

```powershell
pytest tests/test_video_generation_snapshot.py tests/test_video_continuity_policy.py tests/test_modular_app_contract.py -q
```

Expected: FAIL because the current production module still contains the price and transition controls and no shared audio workspace mount.

---

### Task 2: Remove new price/transition controls while preserving compatibility

**Files:**
- Modify: `web/js/pages/production-actions.js:188-290`
- Modify: `src/agents/script_agent.py:70-79,350-364`
- Modify: `src/api/context.py:349-362`
- Modify: `src/api/routes/production.py:104-126`
- Test: `tests/test_video_generation_snapshot.py`
- Test: `tests/test_video_continuity_policy.py`

**Interfaces:**
- Consumes: Task 1 regression tests and existing `VideoGenerationPolicyRequest`/`ProductionShotUpdateRequest` payloads.
- Produces: UI payloads with provider/model/resolution/continuity only; new parsed scenes normalized to `cut`; old API clients still accepted.

- [ ] **Step 1: Normalize newly parsed scenes to hard cuts**

In `ScriptAgent._validate_visual_assets`, after duration normalization, set only newly validated script scenes to `cut` while keeping the legal compatibility duration:

```python
for scene in script.scenes:
    scene.duration_sec = max(3, min(30, int(scene.duration_sec)))
    scene.transition_from_prev = "cut"
    scene.transition_duration_sec = max(
        0.15, min(0.8, float(scene.transition_duration_sec or 0.25))
    )
```

Update the generation prompt so new LLM output is told to emit `cut` only. Do not rewrite already persisted projects during state loading.

- [ ] **Step 2: Keep old request fields but make them compatibility-only**

Leave `transition_type` and `transition_duration_sec` fields in `ProductionShotUpdateRequest` and keep their validation for old clients. Do not remove them from Pydantic schemas. Add a comment in `update_project_production_shot()` that the fields are legacy UI compatibility and are not rendered by the new client.

- [ ] **Step 3: Remove price and transition controls from the production renderer**

Change the video settings row to provider/model/resolution only. Change `productionSaveCurrent()` to send:

```javascript
const payload = {
  prompt: document.getElementById('production-prompt').value,
  negative_prompt: document.getElementById('production-negative').value,
  duration_sec: Number(document.getElementById('production-duration').value),
  references: current.production.references.map(item => ({reference_id: item.reference_id})),
  video_override: {
    provider: document.getElementById('production-video-provider').value,
    model: document.getElementById('production-video-model').value,
    resolution: document.getElementById('production-video-resolution').value,
  },
  continuity_enabled: document.getElementById('production-continuity').checked,
};
```

Keep the qualitative `cost_tier` label only. Do not render `estimated_cost_rmb` or a request field for manual price.

- [ ] **Step 4: Run the focused tests**

Run:

```powershell
pytest tests/test_script_agent_normalization.py tests/test_video_generation_snapshot.py tests/test_video_continuity_policy.py tests/test_modular_app_contract.py -q
```

Expected: PASS, with old renderer tests still exercising historical dissolve behavior and new script tests expecting cut.

---

### Task 3: Add the shared render timeline service

**Files:**
- Create: `src/services/render_timeline.py`
- Modify: `src/agents/assembly_agent.py:342-442,473-641`
- Modify: `src/services/audio_timeline.py:168-248`
- Test: `tests/test_audio_pipeline.py`
- Create: `tests/test_render_timeline.py`

**Interfaces:**
- Consumes: `ScriptBreakdown`, active generated video paths/versions, and legacy transition fields.
- Produces: `RenderTimeline` with `scene_starts_ms: dict[int, int]`, `scene_durations_ms: dict[int, int]`, and `total_duration_ms: int`.

- [ ] **Step 1: Write the timeline unit tests**

```python
def test_cut_timeline_uses_actual_clip_durations():
    timeline = build_render_timeline(
        [_scene(1, 3, "cut"), _scene(2, 5, "cut")],
        {1: 3.4, 2: 4.6},
    )
    assert timeline.scene_starts_ms == {1: 0, 2: 3400}
    assert timeline.total_duration_ms == 8000


def test_legacy_dissolve_timeline_overlaps_only_at_boundary():
    timeline = build_render_timeline(
        [_scene(1, 3, "cut"), _scene(2, 5, "dissolve", 0.5)],
        {1: 3.0, 2: 5.0},
    )
    assert timeline.scene_starts_ms[2] == 2500
    assert timeline.total_duration_ms == 7500
```

- [ ] **Step 2: Implement the pure timeline builder**

Define:

```python
@dataclass(frozen=True)
class RenderTimeline:
    scene_starts_ms: dict[int, int]
    scene_durations_ms: dict[int, int]
    total_duration_ms: int


def build_render_timeline(scenes, clip_durations=None) -> RenderTimeline:
    """Use actual clip durations and preserve only legacy fade/dissolve overlap."""
```

For `cut`, advance by the full actual duration. For legacy `fade/dissolve`, clamp overlap to half of each adjacent clip and subtract it from the next start and total duration. Never produce negative starts or durations.

- [ ] **Step 3: Route assembly, preview and subtitles through the builder**

Replace duplicate `elapsed`/`scene_start` calculations in `_render_transition_video`, `_mux_ffmpeg_audio_and_subtitles`, `_write_subtitle_file`, and `render_project_audio_preview()` with the same `RenderTimeline`. The video renderer may still build FFmpeg filters, but it must use `timeline.scene_starts_ms` for the returned map.

- [ ] **Step 4: Add audio preview BGM/SFX consistency tests**

Mock `run_ffmpeg` and assert preview filters use timeline scene starts. Add a test that two adjacent scenes with the same BGM produce one continuous BGM segment, while a changed BGM creates two segments with boundary fades. Add a test that an unmatched BGM tag leaves `bgm_asset_id == ""`.

- [ ] **Step 5: Run service tests**

Run:

```powershell
pytest tests/test_render_timeline.py tests/test_audio_pipeline.py tests/test_video_continuity_policy.py -q
```

Expected: PASS with identical starts in preview, SRT and final-mix code paths.

---

### Task 4: Make audio generation and status semantics explicit

**Files:**
- Modify: `src/services/audio_timeline.py:142-248`
- Modify: `src/api/routes/audio.py:1-220`
- Modify: `src/api/context.py:1280-1334`
- Modify: `src/api/routes/jobs.py:723-747,1140-1166`
- Modify: `web/js/pages/production-actions.js:240,350-370`
- Modify: `web/js/components/audio-timeline.js:1-116`
- Test: `tests/test_audio_pipeline.py`

**Interfaces:**
- Consumes: `AudioAssets`, `DialogueAudio`, existing audio jobs and production settings endpoint.
- Produces: explicit TTS-only generation labels, audio status summary, subtitle mode persistence, and warning-capable preview behavior.

- [ ] **Step 1: Add backend summary and mode fields to the production payload**

Return an `audio_summary` object alongside existing `audio` data from `_production_payload()`:

```python
{
    "confirmed_voice_count": 2,
    "voice_count": 3,
    "pending_line_count": 4,
    "failed_line_count": 1,
    "timing_conflict_count": 0,
    "can_assemble": False,
}
```

Compute it from persisted `voice_profiles` and `dialogue_audios`; do not change existing fields. Keep preview generation allowed when `can_assemble` is false, and return `warning` in the preview job result when conflicts or failed lines exist.

- [ ] **Step 2: Fix auto-selection threshold**

Change `auto_select_project_audio()` so it only assigns a BGM when the best score is greater than zero. Preserve `bgm_manual` and `sfx_manual` behavior. Add tests for no-match silence and manual override preservation.

- [ ] **Step 3: Expose subtitle mode without removing the old checkbox endpoint**

Keep `productionSetSubtitle(enabled)` for compatibility, and add `productionSetSubtitleMode(mode)` that sends:

```javascript
body: JSON.stringify({
  subtitle_enabled: mode !== 'off',
  subtitle_mode: mode,
})
```

Update status text from “生成配音/BGM” to “生成缺失台词” or “生成台词”，and expose “自动选择素材” separately. Do not claim that a new BGM is generated.

- [ ] **Step 4: Run audio API tests**

Run:

```powershell
pytest tests/test_audio_pipeline.py tests/test_job_api.py -q
```

Expected: PASS, including voice preflight, line regeneration, fallback provider recording, subtitle modes and preview job creation.

---

### Task 5: Rebuild the frontend audio workspace without deleting features

**Files:**
- Modify: `web/js/pages/production-actions.js:228-280`
- Modify: `web/js/components/audio-timeline.js`
- Modify: `web/css/audio.css`
- Modify: `web/css/production.css`
- Modify: `web/js/pages/production.js:54-62`
- Test: `tests/test_modular_app_contract.py`
- Test: `tests/test_production_editor_state.py`

**Interfaces:**
- Consumes: Task 4 `audio_summary`, `subtitle_mode`, existing audio actions and `RenderTimeline`-derived values in the production payload.
- Produces: one `#production-audio-workspace` mount, stable draft protection, compact tracks, one project sidebar, and visible subtitle mode selection.

- [ ] **Step 1: Add a single workspace mount outside the video preview**

In `renderProductionWorkspace()`, remove the old inline `productionAudioHtml()` block from the video preview aside and add:

```html
<section id="production-audio-workspace" class="production-panel production-audio-workspace"></section>
```

Keep the video preview and version list in the right aside. `renderProductionAudioTimeline()` must mount into this section and must not create a second summary block.

- [ ] **Step 2: Render the compact three-region audio layout**

Implement these helpers in `audio-timeline.js`:

```javascript
function audioStatusBar(audio, summary, subtitleMode) { /* status and actions */ }
function audioDialogueTrack(scene, current) { /* line rows + advanced details */ }
function audioBgmTrack(scene, library) { /* selected BGM and fade */ }
function audioSfxTrack(scene, library) { /* positioned effects */ }
function audioProjectSidebar(audio) { /* voice profiles and library once */ }
```

`productionAudioTimelineHtml()` combines them into a grid with one internal scroll container. Every existing action (save, regenerate, activate version, upload, delete, auto-select, preview, SRT export) remains wired to `StudioActions`.

- [ ] **Step 3: Preserve focused drafts and media nodes**

Keep `captureProductionAudioDraft()`/`restoreProductionAudioDraft()`, extending the snapshot to include the active element, text selection, `scrollTop` of the track scroll container, and open `<details>` IDs. When only a task status changes, patch status text and button labels instead of replacing the workspace. When a saved line response changes its data, rerender only that line and restore the snapshot.

- [ ] **Step 4: Add responsive styles**

Use a desktop grid with a main timeline column and a fixed-width project sidebar; below `900px`, stack the sidebar below the timeline. Set `max-height: calc(100vh - 250px)` and `overflow: auto` on exactly `.audio-workspace-scroll`, not on each nested section. Keep existing dark theme variables, card radius and button styles.

- [ ] **Step 5: Run frontend contract and syntax checks**

Run:

```powershell
node --check web/js/components/audio-timeline.js
node --check web/js/pages/production-actions.js
node --check web/js/pages/production.js
pytest tests/test_modular_app_contract.py tests/test_production_editor_state.py -q
```

Expected: PASS, with no duplicate audio summary, no price/transition controls, and a single audio scroll container.

---

### Task 6: Full integration verification and documentation

**Files:**
- Modify: `README.md` (audio workspace and transition behavior section)
- Modify: `tests/test_modular_app_contract.py` (final route/static assertions)

**Interfaces:**
- Consumes: all previous tasks and existing API contracts.
- Produces: documented behavior and a clean regression result.

- [ ] **Step 1: Run all Python tests**

Run:

```powershell
pytest -q
```

Expected: all tests pass; any existing skips remain unrelated to this change.

- [ ] **Step 2: Run module checks**

Run:

```powershell
node --check web/js/bootstrap.js
Get-ChildItem web/js -Recurse -Filter *.js | ForEach-Object { node --check $_.FullName }
```

Expected: no syntax errors.

- [ ] **Step 3: Perform browser smoke checks**

Verify manually:

1. Open a project and confirm no account price or transition editor is shown.
2. Switch shots while editing a dialogue; text, focus, selection and audio scroll position remain stable after a job event.
3. Confirm voice profiles appear once, upload/select BGM and SFX, then verify manual choices survive auto-select.
4. Switch subtitle mode among off/dialogue/speaker and verify preview/SRT/final assembly use the selected text.
5. Generate an audio preview with an unresolved timing conflict; it remains available with a warning, while formal assembly is blocked.

- [ ] **Step 4: Update README**

Document that new projects use hard cuts, historical transitions remain readable, audio generation is TTS-only, BGM/SFX come from the project library, and preview/final output share the render timeline.
