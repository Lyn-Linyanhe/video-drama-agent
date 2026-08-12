# Seedance API 适配实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Seedance 官方模型能力、参考图模式、首尾帧返回和任务取消接入现有视频任务链路，同时保持其他视频供应商和剧本解析行为不变。

**Architecture:** 在现有 `VIDEO_CAPABILITIES` 中增加 Seedance 专用能力字段，在 `SeedanceVideoClient` 中以显式 `reference_mode` 构造官方请求；视频快照在提交前冻结实际接受/忽略的参考图。取消由 Seedance 客户端在收到本地取消时调用官方 DELETE，其他客户端不改变。

**Tech Stack:** Python 3.12+, FastAPI, Pydantic, httpx, pytest, FFmpeg 现有工具链。

## Global Constraints

- 不修改剧本生成、剧本解析、视觉提示词生成和已有分镜数据结构。
- 不删除或覆盖 `output/` 中的资产。
- Seedance 未指定模型时默认 `doubao-seedance-2-0-fast-260128`。
- 快乐小马、万相、Mock 的现有配置、请求方式和能力校验保持独立。
- 参考模式互斥时不静默丢图，必须在快照和任务详情记录接受项、忽略项及原因。
- Seedance 视频请求默认 `generate_audio=false`。

### Task 1: Add Seedance Capability Contract

**Files:**
- Modify: `src/services/video_capabilities.py`
- Modify: `src/config.py` or settings defaults only if the existing config module owns the Seedance default
- Test: `tests/test_video_continuity_policy.py`

**Interfaces:**
- Produce `VIDEO_CAPABILITIES["seedance"]["models"]` entries with `duration_min`, `duration_max`, `resolutions`, `max_reference_images`, `supports_first_frame`, `supports_last_frame`, `supports_first_last_frame`, `multi_reference`, `image_to_video`, `text_to_video`, and `cost_tier`.
- `default_video_policy()` returns `doubao-seedance-2-0-fast-260128` when `DEFAULT_API` is Seedance and no explicit valid model is configured.
- `normalize_video_policy()` validates model-specific duration and resolution when optional override fields are supplied, without applying Seedance rules to other providers.

- [ ] **Step 1: Write failing tests** for the default model, current official model IDs, model-specific duration bounds, resolution rejection, and legacy model compatibility.
- [ ] **Step 2: Run `pytest tests/test_video_continuity_policy.py -q`** and confirm failures come from missing model entries/default behavior.
- [ ] **Step 3: Implement the capability table and provider-local validation.** Keep explicitly saved legacy models readable but exclude them from the new-model default path.
- [ ] **Step 4: Run the focused policy tests and confirm they pass.**

### Task 2: Build Explicit Seedance Reference Modes

**Files:**
- Modify: `src/api_clients/seedance.py`
- Modify: `src/api/context.py`
- Modify: `src/agents/video_gen_agent.py` only where the existing client call needs a backward-compatible `reference_mode`/metadata argument
- Test: `tests/test_seedance_client.py`
- Test: `tests/test_video_generation_snapshot.py`

**Interfaces:**
- `SeedanceVideoClient.generate_video(..., reference_mode: str = "auto", return_last_frame: bool = False, last_frame_reference: str | None = None)` remains backward-compatible for existing callers.
- Supported modes are `text_to_video`, `first_frame`, `first_last_frame`, and `multi_reference`.
- Add a pure helper such as `SeedanceVideoClient.plan_reference_inputs(...) -> dict` returning `mode`, `accepted`, `dropped`, and `reasons` so snapshot tests do not need network calls.

- [ ] **Step 1: Write failing tests** asserting:
  - normal multi-reference requests send only `reference_image` roles up to the model limit;
  - continuity requests send `first_frame` and do not mix ordinary references;
  - explicit first/last requests send exactly `first_frame` and `last_frame`;
  - dropped references include a human-readable reason and are not described as submitted references.
- [ ] **Step 2: Run the focused Seedance tests and verify the old implementation fails on the new assertions.**
- [ ] **Step 3: Implement the planner and request builder.** Use the model capability snapshot rather than a hard-coded four-image slice. Preserve the existing prompt and negative-prompt merge behavior.
- [ ] **Step 4: Update `_build_video_generation_snapshot()`** to plan references before creating `reference_labels`, persist `reference_mode`, `accepted_references`, and `dropped_references`, and pass only the accepted paths to the provider.
- [ ] **Step 5: Run Seedance and snapshot tests and confirm they pass.**

### Task 3: Add Official Last-Frame Return and Download

**Files:**
- Modify: `src/api_clients/seedance.py`
- Modify: `src/services/video_continuity.py` only if a version tail path helper needs to accept the provider-returned image
- Modify: `src/api/routes/jobs.py`
- Modify: `src/schemas/assets.py` only for additive provider-tail metadata if required by existing Pydantic models
- Test: `tests/test_seedance_client.py`
- Test: `tests/test_video_continuity_policy.py`

**Interfaces:**
- Seedance create requests set `return_last_frame=true` only when requested by the snapshot.
- Successful results expose `last_frame_url` and an optional downloaded `last_frame_path` without breaking `VideoGenResult` consumers.
- The video job stores the downloaded provider tail on the created `ShotVideoVersion` and keeps the existing FFmpeg-extracted tail as fallback when the provider returns no tail.

- [ ] **Step 1: Write failing tests** for the request flag, response extraction from the documented output shape, and fallback behavior when no provider tail is returned.
- [ ] **Step 2: Run the focused tests and verify they fail.**
- [ ] **Step 3: Implement provider-tail extraction/download and additive result fields.** Do not delete the existing local tail extraction path.
- [ ] **Step 4: Store the provider tail after the video version is created and run focused tests.**

### Task 4: Implement Provider DELETE Cancellation

**Files:**
- Modify: `src/api_clients/seedance.py`
- Modify: `src/services/generation_hooks.py` only if a cancellation hook needs an additive provider-cancel callback
- Modify: `src/services/job_manager.py` only if the existing task cancellation cannot reliably reach the client cleanup path
- Test: `tests/test_seedance_client.py`
- Test: `tests/test_job_manager.py`

**Interfaces:**
- `SeedanceVideoClient.cancel_task(task_id: str) -> None`/async equivalent calls `DELETE /api/v3/contents/generations/tasks/{task_id}` with the same Bearer headers.
- Cancellation is idempotent for already-completed, already-canceled, 404, and 409 provider responses.
- Local job state remains canceled even if provider cancellation reports a diagnostic failure.

- [ ] **Step 1: Write failing tests** for DELETE URL/auth headers, idempotent statuses, and cancellation during provider polling.
- [ ] **Step 2: Run focused tests and verify they fail.**
- [ ] **Step 3: Implement cleanup in the Seedance client around task cancellation.** Keep other provider clients untouched.
- [ ] **Step 4: Run Seedance and job-manager cancellation tests.**

### Task 5: Audio Flag, Image Quality, and Error Classification

**Files:**
- Modify: `src/api_clients/seedance.py`
- Modify: `src/api/routes/settings.py`
- Modify: `src/services/job_manager.py` only for Seedance-specific normalized error details if necessary
- Test: `tests/test_settings_api.py`
- Test: `tests/test_seedance_client.py`

**Interfaces:**
- Every new Seedance request sends `generate_audio=false` unless an explicit future policy enables it.
- Connection tests use an authenticated non-billable endpoint/path and distinguish endpoint, credential, model-access, and validation failures.
- Sensitive reference-image rejection is returned as an explicit failure or `degraded_reference_mode` result; it never silently retries text-only while claiming references were used.

- [ ] **Step 1: Write failing tests** for `generate_audio=false`, no silent text-only retry, and error categories.
- [ ] **Step 2: Run focused settings/client tests and verify failures.**
- [ ] **Step 3: Implement request flags and explicit error handling.** Preserve existing connection behavior for Happy Horse, Wan, Kling, and Mock.
- [ ] **Step 4: Run focused tests.**

### Task 6: Dynamic UI/Request Metadata Compatibility

**Files:**
- Modify: `src/api/routes/production.py` only if response payload needs to expose the additive reference decision fields
- Modify: `src/api/routes/jobs.py` only if job payload needs to expose the additive snapshot fields
- Modify: `web/js/pages/production-actions.js`
- Modify: `studio.html` only if the existing settings model select is still hard-coded there
- Test: existing browser/API production tests plus a focused API response test

**Interfaces:**
- `/api/video-capabilities` returns the new Seedance model and capability metadata.
- Production payloads expose actual model, resolution, duration limits, reference mode, accepted count, dropped count, and reasons.
- Existing provider selectors remain functional and do not receive Seedance-only options.

- [ ] **Step 1: Write failing API/UI contract tests** for dynamic Seedance model options and reference-overflow metadata.
- [ ] **Step 2: Run the focused contract tests and verify failures.**
- [ ] **Step 3: Bind existing production controls to capability metadata without rebuilding unrelated editing controls.**
- [ ] **Step 4: Run the API tests and JavaScript syntax check.**

### Task 7: Full Regression and Documentation

**Files:**
- Modify: `README.md` with Seedance configuration, supported modes, and cancellation notes
- Test: all existing `tests/`

- [ ] **Step 1: Run all Seedance, production, job, settings, and continuity tests.**
- [ ] **Step 2: Run the full `pytest -q`.**
- [ ] **Step 3: Run syntax checks for changed JavaScript files.**
- [ ] **Step 4: Review the diff for accidental changes under `output/` and verify no script-analysis code was modified.**
- [ ] **Step 5: Record any external-provider limitations separately from code failures.**
