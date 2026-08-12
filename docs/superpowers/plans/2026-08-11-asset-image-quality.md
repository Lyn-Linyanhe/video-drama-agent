# Asset Image Quality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent unreviewed or technically invalid generated image assets from becoming active versions while retaining current asset APIs.

**Architecture:** Centralize the final image quality result in `src.services.asset_generation`, with technical checks required before all commits and visual review normalized to `passed`, `warning`, or `unreviewed`. Group generators validate every returned image before their existing atomic commit loops. The schema fields and version metadata retain the complete report and provider prompt inputs.

**Tech Stack:** Python 3, FastAPI, Pydantic, pytest, Pillow.

## Global Constraints

- Do not generate video or delete/overwrite existing `output/` assets.
- Keep existing API fields and add fields only.
- Mock image providers may use placeholder images; real-provider technical failures must never activate.
- A missing visual-review configuration is `unreviewed`, never `passed`.

---

### Task 1: Review configuration and contracts

**Files:**
- Modify: `src/config.py`, `src/api/routes/settings.py`, `src/services/media_quality.py`
- Test: `tests/test_media_quality.py`

- [ ] Write failing tests for official DashScope `HAPPYHORSE_API_KEY` fallback and asset-specific review prompts.
- [ ] Run `pytest tests/test_media_quality.py -q` and verify the new assertions fail.
- [ ] Add explicit official-DashScope-only fallback configuration and an `asset_type` review prompt contract; retain the video contact-sheet contract.
- [ ] Run `pytest tests/test_media_quality.py -q` and verify pass.

### Task 2: Single image activation and persistence

**Files:**
- Modify: `src/services/asset_generation.py`, `src/api/routes/jobs.py`, `src/api/context.py`, `src/schemas/character.py`, `src/schemas/scene_image.py`, `src/schemas/prop.py`
- Test: `tests/test_asset_generation_api.py`

- [ ] Write failing tests for default technical checks, failed real-provider non-activation, skipped visual review becoming `unreviewed`, and serialized reports.
- [ ] Run those focused tests and verify failure.
- [ ] Default `regenerate_image_asset` and jobs to quality checks, gate activation on technical and visual outcomes, and persist report/prompt/negative mode in state and version metadata.
- [ ] Run the focused tests and verify pass.

### Task 3: Atomic reference groups and scene response

**Files:**
- Modify: `src/services/asset_generation.py`
- Test: `tests/test_wan_character_references.py`, `tests/test_prop_generation_flow.py`, `tests/test_scene_master_generation.py`

- [ ] Write failing tests where one technically invalid group member blocks every activation and assert scene response reports actual quality.
- [ ] Run focused group tests and verify failure.
- [ ] Validate technical and visual reports for each member before the existing commit loops, attach metadata, and return actual scene reports.
- [ ] Run all focused media quality and character/scene/prop generation tests and verify pass.
