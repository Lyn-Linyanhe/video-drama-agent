# Visual Stage Boundary Fixes Implementation Plan

> **For agentic workers:** Use test-first implementation. No video-generation task may be submitted while executing this plan.

**Goal:** Keep visual decomposition inside the confirmed script timeline, reduce visual-stage request size, prevent cross-shot content leakage, and exclude non-physical concepts from the visual prop manifest.

**Architecture:** Keep the existing compact visual prompt contract and deterministic backend assembly. Add bounded formatting before the LLM request, sanitize each returned shot against its own source scene, and apply a total-duration ceiling after visual decomposition. Extend the existing manifest cleanup rather than adding a second asset pipeline.

**Tech Stack:** Python, Pydantic, pytest, existing LLM client and visual prompt agent.

## Global Constraints

- The confirmed script's total duration is the authoritative upper bound for visual prompts.
- Existing prompt sections and project style constraints remain intact; only stale or placeholder content may be removed or replaced with the current scene action.
- Non-physical items such as SMS content, recordings, calls, and voices must not produce entity reference images; physical devices such as phones, recorders, and recording pens remain eligible when explicitly present.
- Tests must not call a video provider or create a `video.generate` job.

### Task 1: Add regression tests

**Files:**
- Modify: `tests/test_prompt_policy.py`
- Modify: `tests/test_prop_assets.py`

Add tests for the duration ceiling, per-shot stale-content filtering, placeholder fallback, and virtual-prop removal. Run only these tests and confirm they fail before implementation.

### Task 2: Harden visual prompt preparation

**Files:**
- Modify: `src/agents/visual_prompt_agent.py`

Bound repeated manifest descriptions and scene fields before the LLM request. Add deterministic shot-field sanitization based on the current scene's source text and known placeholder phrases. Preserve the existing compact output schema and prompt assembly sections. Cap the sum of visual durations at the original script total without changing a shorter valid result.

### Task 3: Filter non-physical props

**Files:**
- Modify: `src/utils/visual_asset_manifest.py`

Remove SMS/recording-content pattern entries from automatic physical-prop inference and reject virtual-only prop names during manifest validation. Keep physical phones, recording machines, and recording pens when their names identify an actual device.

### Task 4: Verify image-only behavior

Run the focused visual and manifest tests plus syntax checks. Do not run video generation tests, create video jobs, or call any video provider.
