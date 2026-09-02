"""FastAPI 桥接服务 — 将 7 Agent 管线暴露为 REST API，供暗系前端调用。"""

import json
import hashlib
import io
import os
import re
import secrets
import uuid
import sys
import zipfile
import asyncio
import hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, Optional

# ── Windows 编码补丁 ──
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

# 确保当前目录在 sys.path 中
BASE_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(BASE_DIR))

PRODUCTION_MIN_DURATION_SEC = 3
PRODUCTION_MAX_DURATION_SEC = 15

from src.agents import (
    ScriptAgent, VisualPromptAgent, ImageGenerationAgent,
    VideoGenerationAgent, ReviewAgent, AudioAgent, AssemblyAgent,
)
from src.graph.orchestrator import DramaOrchestrator
from src.graph.state import DramaState
from src.api_clients.image_mock import MockImageClient
from src.api_clients.image_kling import KlingImageClient
from src.api_clients.image_wan import WanImageClient
from src.api_clients.kling import KlingVideoClient
from src.api_clients.happyhorse import HappyHorseVideoClient
from src.api_clients.seedance import SeedanceVideoClient
from src.config import (
    OUTPUT_DIR, DEFAULT_API, IMAGE_GEN_API, IMAGE_GEN_KEY,
    KLING_API_KEY, KLING_ACCESS_KEY, KLING_SECRET_KEY,
    KLING_MODEL, KLING_MODE, KLING_IMAGE_MODEL,
    HAPPYHORSE_API_KEY, HAPPYHORSE_BASE_URL, HAPPYHORSE_MODEL, HAPPYHORSE_RESOLUTION,
    ARK_API_KEY, ARK_BASE_URL, ARK_MODEL, ARK_RESOLUTION,
    LLM_MAX_INPUT_CHARS,
)
from src.utils.project_store import ProjectStore
from src.utils.asset_versions import ImageAssetVersions
from src.schemas.jobs import ACTIVE_JOB_STATES, JobBatchPublic, JobCreateRequest, JobPublic, TERMINAL_JOB_STATES
from src.schemas.project import CreativeProfile
from src.services import JobManager, JobStore
from src.services.generation_hooks import GenerationHooks, bind_generation_hooks
from src.services.job_handlers import register_handlers
from src.services.media_quality import (
    MediaQualityFailure,
    image_allows_automatic_reference,
    inspect_image,
    inspect_video,
    review_image_subjective,
    review_video_subjective,
)
from src.services.prompt_composer import compose_video_prompt, provider_capabilities
from src.services.prompt_fidelity_gate import audit_current_prompt_fidelity, generation_snapshot_digest, prompt_fidelity_gate_detail
from src.services.tts_provider import describe_dashscope_tts_error

# ── 全局单例 ─────────────────────────────────────────────────
script_agent = ScriptAgent()
visual_prompt_agent = VisualPromptAgent()

# 图片生成客户端
if IMAGE_GEN_API == "wan" and HAPPYHORSE_API_KEY and "dashscope.aliyuncs.com" in HAPPYHORSE_BASE_URL.lower():
    image_gen_agent = ImageGenerationAgent(api_client=WanImageClient(
        api_key=HAPPYHORSE_API_KEY, model=KLING_IMAGE_MODEL, base_url=HAPPYHORSE_BASE_URL,
    ))
elif IMAGE_GEN_API == "kling" and (IMAGE_GEN_KEY or KLING_API_KEY):
    image_gen_agent = ImageGenerationAgent(api_client=KlingImageClient(
        api_key=IMAGE_GEN_KEY or KLING_API_KEY, model=KLING_IMAGE_MODEL,
    ))
else:
    image_gen_agent = ImageGenerationAgent(api_client=MockImageClient())

# 视频生成客户端
if DEFAULT_API == "kling":
    if KLING_API_KEY:
        video_gen_agent = VideoGenerationAgent(api_client=KlingVideoClient(
            api_key=KLING_API_KEY, model=KLING_MODEL, mode=KLING_MODE,
        ))
    elif KLING_ACCESS_KEY and KLING_SECRET_KEY:
        video_gen_agent = VideoGenerationAgent(api_client=KlingVideoClient(
            access_key=KLING_ACCESS_KEY, secret_key=KLING_SECRET_KEY,
            model=KLING_MODEL, mode=KLING_MODE,
        ))
    else:
        video_gen_agent = VideoGenerationAgent()
elif DEFAULT_API == "happyhorse":
    if HAPPYHORSE_API_KEY:
        video_gen_agent = VideoGenerationAgent(api_client=HappyHorseVideoClient(
            api_key=HAPPYHORSE_API_KEY,
            base_url=HAPPYHORSE_BASE_URL,
            model=HAPPYHORSE_MODEL,
            resolution=HAPPYHORSE_RESOLUTION,
        ))
    else:
        video_gen_agent = VideoGenerationAgent()
elif DEFAULT_API in {"seedance", "seedence"}:
    if ARK_API_KEY:
        video_gen_agent = VideoGenerationAgent(api_client=SeedanceVideoClient(
            api_key=ARK_API_KEY,
            base_url=ARK_BASE_URL,
            model=ARK_MODEL,
            resolution=ARK_RESOLUTION,
        ))
    else:
        video_gen_agent = VideoGenerationAgent()
else:
    video_gen_agent = VideoGenerationAgent()

review_agent = ReviewAgent()
audio_agent = AudioAgent()
assembly_agent = AssemblyAgent()

orchestrator = DramaOrchestrator(
    script_agent=script_agent,
    visual_prompt_agent=visual_prompt_agent,
    image_gen_agent=image_gen_agent,
    video_gen_agent=video_gen_agent,
    review_agent=review_agent,
    audio_agent=audio_agent,
    assembly_agent=assembly_agent,
)

project_store = ProjectStore()
job_store = JobStore(recover=False)
job_manager = JobManager(job_store)
video_jobs: set[tuple[str, int]] = set()

# ── 请求/响应模型 ───────────────────────────────────────────
class ScriptRequest(BaseModel):
    visual_style_id: str = ""
    style_modifiers: list[str] = Field(default_factory=list)
    user_input: str = Field(..., min_length=1, max_length=LLM_MAX_INPUT_CHARS, description="用户输入的故事创意或文本")
    input_mode: str = Field(default="idea", description="idea or script")
    source_filename: str = Field(default="", max_length=255)
    genre_tags: str = Field(default="都市", description="剧集类型")
    art_style: str = Field(default="写实", description="艺术风格")
    video_ratio: str = Field(default="9:16", description="视频比例")
    project_id: str = Field(default="", description="已有项目ID，留空则新建")


class ScriptResponse(BaseModel):
    project_id: str
    script: dict | None = None
    visual_design: dict | None = None
    state_json: str = ""
    log: list[str] = []
    input_mode: str = "idea"
    source_filename: str = ""


class ScriptUpdateRequest(BaseModel):
    script: dict
    visual_conflict_action: Literal["continue", "restart"] | None = None


class ScriptRevisionRequest(BaseModel):
    instruction: str = Field(..., min_length=1, max_length=4000)


class ScriptAnalyzeRequest(BaseModel):
    source_text: str = Field(..., min_length=1, max_length=LLM_MAX_INPUT_CHARS)
    source_filename: str = Field(default="", max_length=255)
    input_mode: str = Field(default="script", description="idea or script")
    original_input: str = Field(default="", max_length=LLM_MAX_INPUT_CHARS)


class ScriptDraftSaveRequest(BaseModel):
    draft_text: str = Field(..., min_length=1, max_length=LLM_MAX_INPUT_CHARS)
    input_mode: Literal["idea", "script"] = "script"
    source_filename: str = Field(default="", max_length=255)


class ScriptDraftConfirmRequest(BaseModel):
    source_filename: str = Field(default="", max_length=255)


class StageRequest(BaseModel):
    """通用状态传递模型。"""
    state_json: str = Field(default="", description="前一步返回的 state_json")


class StageResponse(BaseModel):
    state_json: str
    log: list[str] = []
    error: str = ""


class ImageAssetRequest(BaseModel):
    asset_type: str = Field(..., description="character, scene, or prop")
    asset_id: str = Field(..., description="character name, location ID, legacy scene ID, or prop ID")
    view_type: str = Field(..., description="image view type")
    state_id: str = Field(default="", description="scene state ID for scene assets")


class RegenerateImageRequest(ImageAssetRequest):
    prompt: str = Field(default="", max_length=8000)
    seed: int | None = None
    use_standard_view_prompt: bool = False
    provider: str = Field(default="", max_length=32)
    model: str = Field(default="", max_length=80)
    resolution: str = Field(default="", max_length=16)


class ImageGenerationPolicyRequest(BaseModel):
    provider: str = Field(default="", max_length=32)
    model: str = Field(default="", max_length=80)
    resolution: str = Field(default="", max_length=16)


class CharacterReferenceGroupRequest(BaseModel):
    custom_instructions: str = Field(default="", max_length=8000)
    provider: str = Field(default="", max_length=32)
    model: str = Field(default="", max_length=80)
    resolution: str = Field(default="", max_length=16)


class CharacterReferenceEditRequest(ImageAssetRequest):
    prompt: str = Field(..., min_length=1, max_length=8000)
    reference_version_ids: list[str] = Field(default_factory=list, max_length=4)
    provider: str = Field(default="", max_length=32)
    model: str = Field(default="", max_length=80)
    resolution: str = Field(default="", max_length=16)


class SceneMasterConfirmRequest(BaseModel):
    version_id: str = Field(..., min_length=8, max_length=64)
    state_id: str = Field(default="", max_length=80)


class PropMasterConfirmRequest(BaseModel):
    version_id: str = Field(..., min_length=8, max_length=64)


class SceneReferenceEditRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=8000)
    reference_version_ids: list[str] = Field(default_factory=list, max_length=4)
    provider: str = Field(default="", max_length=32)
    model: str = Field(default="", max_length=80)
    resolution: str = Field(default="", max_length=16)


class CharacterFourViewsRequest(BaseModel):
    asset_id: str = Field(..., min_length=1, max_length=255)
    custom_instructions: str = Field(default="", max_length=8000)


class GeneratePendingAssetsRequest(BaseModel):
    asset_type: str = Field(..., description="character, scene, or prop")


class GenerateAssetRequest(BaseModel):
    view_type: str = Field(default="", max_length=64)
    state_id: str = Field(default="", max_length=80)
    custom_instructions: str = Field(default="", max_length=8000)
    regenerate: bool = False
    provider: str = Field(default="", max_length=32)
    model: str = Field(default="", max_length=80)
    resolution: str = Field(default="", max_length=16)


class ProductionSettingsRequest(BaseModel):
    subtitle_enabled: bool = True
    subtitle_mode: str | None = Field(default=None, pattern="^(off|dialogue|speaker)$")
    asr_enabled: bool | None = None
    asr_model: str | None = Field(default=None, max_length=80)
    audio_mode: Literal["native_dialogue", "external_tts"] | None = None


class VideoGenerationPolicyRequest(BaseModel):
    provider: str = Field(default="", max_length=32)
    model: str = Field(default="", max_length=80)
    resolution: str = Field(default="", max_length=16)
    unit_price_rmb_per_sec: float | None = Field(default=None, ge=0.0, le=1000.0)


class AudioLineUpdateRequest(BaseModel):
    text: str | None = Field(default=None, max_length=2000)
    emotion: str | None = Field(default=None, max_length=32)
    speed: float | None = Field(default=None, ge=0.5, le=2.0)
    volume: float | None = Field(default=None, gt=0.0, le=2.0)
    pitch: int | None = Field(default=None, ge=-12, le=12)
    lead_silence_ms: int | None = Field(default=None, ge=0, le=10000)
    tail_silence_ms: int | None = Field(default=None, ge=0, le=10000)


class VoiceProfileUpdateRequest(BaseModel):
    voice: str = Field(default="", max_length=128)
    provider: str = Field(default="qwen3", pattern="^(qwen3|minimax|edge)$")
    confirmed: bool = True


class AudioVersionActivateRequest(BaseModel):
    version_id: str = Field(..., min_length=8, max_length=64)


class AudioSelectionRequest(BaseModel):
    asset_id: str = Field(default="", max_length=64)
    muted: bool = False
    volume: float | None = Field(default=None, gt=0.0, le=1.0)
    fade_ms: int | None = Field(default=None, ge=0, le=10000)


class AudioSfxTrackRequest(BaseModel):
    asset_id: str = Field(..., min_length=1, max_length=64)
    start_ms: int = Field(default=0, ge=0)
    volume: float = Field(default=1.0, gt=0.0, le=2.0)


class AudioSfxSelectionRequest(BaseModel):
    tracks: list[AudioSfxTrackRequest] = Field(default_factory=list, max_length=2)
    asset_id: str = Field(default="", max_length=64)
    start_ms: int = Field(default=0, ge=0)
    volume: float = Field(default=1.0, gt=0.0, le=2.0)


class PromptPolicyRequest(BaseModel):
    style_prefix: str = Field(default="", max_length=4000)
    global_negative_prompt: str = Field(default="", max_length=4000)


class ActivateImageVersionRequest(ImageAssetRequest):
    version_id: str = Field(..., min_length=8)


class PromptEditRequest(BaseModel):
    state_json: str
    feedback: str = Field(default="", description="用户给 AI 的修改建议")
    shot_index: int = Field(default=0, description="要修改的镜头编号")


class ProductionReferenceRequest(BaseModel):
    reference_id: str = Field(..., min_length=3, max_length=512)
    source: Literal["asset", "manual", "explicit"] = "asset"
    token: str = Field(default="", max_length=512)
    version_id: str = Field(default="", max_length=256)
    explicit_base_source: Literal["", "asset", "manual"] = ""


class ProductionShotUpdateRequest(BaseModel):
    prompt: str = Field(default="", max_length=12000)
    negative_prompt: str | None = Field(default=None, max_length=4000)
    duration_sec: int | None = Field(
        default=None,
        ge=PRODUCTION_MIN_DURATION_SEC,
        le=60,
    )
    references: list[ProductionReferenceRequest] | None = None
    video_override: VideoGenerationPolicyRequest | None = None
    continuity_enabled: bool | None = None
    transition_type: str | None = Field(default=None, max_length=16)
    transition_duration_sec: float | None = Field(default=None, ge=0.15, le=0.8)


class ProductionPromptRevisionRequest(BaseModel):
    feedback: str = Field(..., min_length=1, max_length=4000)


class ActivateVideoVersionRequest(BaseModel):
    version_id: str = Field(..., min_length=8, max_length=128)


class ManualAssetRequest(BaseModel):
    asset_type: str = Field(..., description="character, scene, or prop")
    name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    scene_ids: list[int] = Field(default_factory=list)
    time_condition: str = Field(default="", max_length=240)
    is_handheld: bool = False


class ProjectMeta(BaseModel):
    id: str
    title: str
    status: str
    created_at: str
    updated_at: str
    user_input: str = ""


# ── 工具函数 ─────────────────────────────────────────────────
def _state_to_json(state: DramaState) -> str:
    """将 DramaState 序列化为 JSON 字符串。"""
    data = {
        "user_input": state.user_input,
        "input_mode": state.input_mode,
        "source_filename": state.source_filename,
        "script_phase": state.script_phase,
        "script_draft_text": state.script_draft_text,
        "script_draft_instruction": state.script_draft_instruction,
        "draft_job_id": state.draft_job_id,
        "project_id": state.project_id,
        "creative_profile": state.creative_profile.model_dump(),
        "total_cost": state.total_cost,
        "review_loop_count": state.review_loop_count,
        "error_message": state.error_message,
        "execution_log": state.execution_log,
        "output_video_path": state.output_video_path,
        "subtitle_enabled": state.subtitle_enabled,
        "subtitle_mode": state.subtitle_mode,
        "assembly_audio_override": state.assembly_audio_override,
        "prompt_overrides": state.prompt_overrides,
        "prompt_policy": state.prompt_policy.model_dump(),
        "image_generation_policy": state.image_generation_policy,
        "scene_view_requirements": state.scene_view_requirements,
        "prop_view_requirements": state.prop_view_requirements,
        "video_generation_policy": state.video_generation_policy,
        "visual_stage": state.visual_stage,
        "visual_script_signature": state.visual_script_signature,
        "visual_job_id": state.visual_job_id,
        "visual_stage_error": state.visual_stage_error,
        "visual_performance": state.visual_performance,
        "prompt_review": state.prompt_review,
        "prompt_fidelity": state.prompt_fidelity,
        "user_feedback": state.user_feedback,
        "scene_vision_descriptions": state.scene_vision_descriptions,
        "shot_productions": {
            str(scene_id): production.model_dump()
            for scene_id, production in state.shot_productions.items()
        },
    }
    # Pydantic 模型序列化
    for attr in ["script", "visual_design", "character_images",
                  "scene_images", "prop_images", "generation_prompts",
                  "generated_assets", "audio_assets"]:
        val = getattr(state, attr, None)
        if val is not None:
            data[attr] = val.model_dump()
    return json.dumps(data, ensure_ascii=False)


def _script_signature(script) -> str:
    """Return a deterministic revision key for visual task write protection."""
    if script is None:
        return ""
    payload = json.dumps(script.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


_MEDIA_PATH_FIELDS = {
    "output_video_path",
    "image_path",
    "local_path",
    "audio_path",
    "bgm_path",
    "narration_audio_path",
    "preview_mix_path",
    "tail_frame_path",
    "continuity_tail_frame_path",
}


def _repair_utf8_mojibake(value: str) -> str:
    """Recover text that was UTF-8 encoded and decoded as Latin-1.

    A few older project snapshots were written through a Windows console with
    the wrong code page.  This is deliberately conservative: only return the
    decoded value when it contains CJK text and the original value can be
    represented as a single-byte string.  Normal Unicode paths are unchanged.
    """
    if not value:
        return value
    try:
        repaired = value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value
    if repaired == value or not re.search(r"[\u3400-\u9fff]", repaired):
        return value
    return repaired


def _relocate_output_media_path(path: str) -> str:
    """Map stale absolute output paths to this checkout's output directory."""
    if not path or re.match(r"^https?://", path, re.IGNORECASE):
        return path
    if Path(path).is_file():
        return path
    normalized = str(path).replace("\\", "/")
    match = re.search(r"(?:^|/)output/(.+)$", normalized, re.IGNORECASE)
    if not match:
        return path
    suffix = match.group(1)
    # Try the persisted suffix first, then the common UTF-8/Latin-1 repair.
    # This also fixes paths from another checkout because the output root is
    # always resolved against the currently running workspace.
    for candidate_suffix in (suffix, _repair_utf8_mojibake(suffix)):
        candidate = OUTPUT_DIR / Path(*candidate_suffix.split("/"))
        if candidate.is_file():
            return str(candidate)
    return path


def _repair_state_media_paths(state: DramaState) -> None:
    """Repair moved-project media paths in-place after loading persisted state."""

    def visit(value):
        if value is None or isinstance(value, (str, int, float, bool)):
            return
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if isinstance(value, dict):
            for item in value.values():
                visit(item)
            return
        dataclass_fields = getattr(value, "__dataclass_fields__", None)
        if dataclass_fields:
            for field in dataclass_fields:
                current = getattr(value, field, None)
                if field in _MEDIA_PATH_FIELDS and isinstance(current, str):
                    setattr(value, field, _relocate_output_media_path(current))
                else:
                    visit(current)
            return
        fields = getattr(value.__class__, "model_fields", None)
        if not fields:
            return
        for field in fields:
            current = getattr(value, field, None)
            if field in _MEDIA_PATH_FIELDS and isinstance(current, str):
                setattr(value, field, _relocate_output_media_path(current))
            else:
                visit(current)

    if state.output_video_path:
        state.output_video_path = _relocate_output_media_path(state.output_video_path)
    visit(state)


def _normalize_legacy_generation_durations(data: dict) -> None:
    """Clamp old script/prompt durations before their stricter schemas load."""

    def normalize_items(items, maximum):
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                value = int(round(float(item.get("duration_sec", PRODUCTION_MIN_DURATION_SEC))))
            except (TypeError, ValueError):
                value = PRODUCTION_MIN_DURATION_SEC
            item["duration_sec"] = max(PRODUCTION_MIN_DURATION_SEC, min(maximum, value))
            normalize_items(item.get("sub_shots"), maximum)

    script = data.get("script")
    if isinstance(script, dict):
        normalize_items(script.get("scenes"), 30)
    prompts = data.get("generation_prompts")
    if isinstance(prompts, dict):
            normalize_items(prompts.get("shots"), 60)


def _repair_character_description_consumers(
    state: DramaState,
    previous_manifest_descriptions: dict[str, str] | None = None,
) -> None:
    """Propagate repaired manifest descriptions to persisted prompt consumers.

    The manifest is the source of truth for deterministic repairs. Visual
    registries and old prompt snapshots are denormalized copies, so update
    only values that still match a generated/fallback description. Detailed
    user-authored descriptions remain untouched.
    """
    script = getattr(state, "script", None)
    manifest = getattr(script, "asset_manifest", None) if script else None
    if not manifest:
        return

    from src.utils.visual_asset_manifest import _is_generic_character_description

    previous = {
        str(name or "").strip(): str(description or "").strip()
        for name, description in (previous_manifest_descriptions or {}).items()
        if str(name or "").strip() and str(description or "").strip()
    }

    def is_replaceable_legacy(name: str, value: str) -> bool:
        text = str(value or "").strip()
        return bool(
            text
            and (
                text == previous.get(name, "")
                or _is_generic_character_description(text, name)
            )
        )

    repaired: dict[str, str] = {}
    legacy_values: dict[str, set[str]] = {}
    for character in getattr(manifest, "characters", []) or []:
        name = str(getattr(character, "name", "") or "").strip()
        description = str(getattr(character, "description", "") or "").strip()
        if not name or not description or getattr(character, "appearance_mode", "") != "on_screen":
            continue
        repaired[name] = description
        values = legacy_values.setdefault(name, set())
        if previous.get(name):
            values.add(previous[name])

    visual = getattr(state, "visual_design", None)
    if visual is not None:
        for field_name in ("global_character_descriptions_cn", "global_character_descriptions"):
            registry = getattr(visual, field_name, None)
            if isinstance(registry, dict):
                # Older visual registries sometimes used the stable asset ID
                # as the key.  A later edit may rename the display name while
                # keeping that ID, so synchronize the alias from the manifest
                # before normal name-based repair.  The manifest remains the
                # source of truth for this deterministic alias only.
                for character in getattr(manifest, "characters", []) or []:
                    canonical_name = str(getattr(character, "name", "") or "").strip()
                    asset_id = str(getattr(character, "asset_id", "") or "").strip()
                    description = str(getattr(character, "description", "") or "").strip()
                    if (
                        canonical_name
                        and asset_id
                        and asset_id != canonical_name
                        and description
                        and asset_id in registry
                    ):
                        current = str(registry.get(asset_id, "") or "").strip()
                        if current and current != description and is_replaceable_legacy(canonical_name, current):
                            legacy_values.setdefault(canonical_name, set()).add(current)
                            registry[asset_id] = description
                for name in repaired:
                    current = str(registry.get(name, "") or "").strip()
                    if is_replaceable_legacy(name, current):
                        legacy_values.setdefault(name, set()).add(current)
        for scene_visual in getattr(visual, "scenes", []) or []:
            appearances = getattr(scene_visual, "character_appearance", None)
            if not isinstance(appearances, dict):
                continue
            for name in repaired:
                current = str(appearances.get(name, "") or "").strip()
                if is_replaceable_legacy(name, current):
                    legacy_values.setdefault(name, set()).add(current)

    def should_replace(name: str, current: str) -> bool:
        text = str(current or "").strip()
        return bool(
            text
            and (
                text in legacy_values.get(name, set())
                or _is_generic_character_description(text, name)
            )
        )

    def replace_text(text: str, *, anchor_only: bool = False) -> str:
        value = str(text or "")
        if not value:
            return value
        lines = value.splitlines() if anchor_only else [value]
        for index, line in enumerate(lines):
            if anchor_only and "【出镜角色与身份锚点】" not in line:
                continue
            for name, new_description in repaired.items():
                new_body = re.sub(
                    rf"^{re.escape(name)}\s*[：:]\s*",
                    "",
                    new_description,
                )
                old_values = set(legacy_values.get(name, set()))
                for registry in (
                    getattr(getattr(state, "visual_design", None), "global_character_descriptions_cn", {}) or {},
                    getattr(getattr(state, "visual_design", None), "global_character_descriptions", {}) or {},
                ):
                    current = str(registry.get(name, "") or "").strip()
                    if current and current != new_description and should_replace(name, current):
                        old_values.add(current)
                old_values.discard(new_description)
                for old_description in sorted(old_values, key=len, reverse=True):
                    if not old_description:
                        continue
                    old_body = re.sub(
                        rf"^{re.escape(name)}\s*[：:]\s*",
                        "",
                        old_description,
                    )
                    # Replace the exact generated description first.  It can
                    # be nested after a label (``设定：name：body``) or after
                    # an already-prefixed anchor (``name：name：body``).
                    # Keeping only ``new_body`` avoids duplicating the role
                    # name in those containers.
                    line = line.replace(old_description, new_body)
                    for separator in ("：", ":"):
                        # Historical image prompts used both ``name:body``
                        # and ``label:name:body`` forms.  The latter does not
                        # contain ``name:name:body``, so replacing only the
                        # full generated description misses it.  Replace the
                        # canonical name/body pair as well as the exact old
                        # description; all candidates come from persisted
                        # generated registries, not arbitrary user text.
                        line = line.replace(
                            f"{name}{separator}{old_body}",
                            f"{name}{separator}{new_body}",
                        )
                        line = line.replace(
                            f"{name}{separator}{name}{separator}{old_body}",
                            f"{name}{separator}{new_body}",
                        )
                        line = line.replace(
                            f"{name}{separator}{old_description}",
                            f"{name}{separator}{new_body}",
                        )
                line = re.sub(
                    rf"{re.escape(name)}([：:])\s*{re.escape(name)}\1",
                    rf"{name}\1",
                    line,
                )
            lines[index] = line
        return "\n".join(lines) if anchor_only else lines[0]

    if visual is not None:
        for field_name in ("global_character_descriptions_cn", "global_character_descriptions"):
            registry = getattr(visual, field_name, None)
            if not isinstance(registry, dict):
                continue
            for name, new_description in repaired.items():
                current = str(registry.get(name, "") or "").strip()
                if not current or should_replace(name, current):
                    registry[name] = new_description
        for scene_visual in getattr(visual, "scenes", []) or []:
            appearances = getattr(scene_visual, "character_appearance", None)
            if not isinstance(appearances, dict):
                continue
            for name, new_description in repaired.items():
                current = str(appearances.get(name, "") or "").strip()
                if current and should_replace(name, current):
                    appearances[name] = new_description

    prompts = getattr(state, "generation_prompts", None)
    if prompts is not None:
        def repair_shot(shot: Any) -> None:
            shot.prompt = replace_text(getattr(shot, "prompt", ""), anchor_only=True)
            for child in getattr(shot, "sub_shots", []) or []:
                repair_shot(child)

        for shot in getattr(prompts, "shots", []) or []:
            repair_shot(shot)

    images = getattr(getattr(state, "character_images", None), "characters", None) or []
    for image in images:
        for field_name in ("image_prompt", "base_prompt"):
            if hasattr(image, field_name):
                setattr(image, field_name, replace_text(getattr(image, field_name, "")))


def _state_from_json(json_str: str) -> DramaState:
    """从 JSON 字符串重建 DramaState。"""
    if not json_str:
        return DramaState()
    data = json.loads(json_str)

    # Persisted projects can predate the provider-wide three-second floor.
    # Normalize only timed script/prompt fields before Pydantic validation so
    # historical projects remain readable without changing media durations.
    _normalize_legacy_generation_durations(data)
    profile_data = dict(data.get("creative_profile") or {})
    if profile_data.get("audio_mode") not in {"auto", "native_dialogue", "external_tts"}:
        profile_data["audio_mode"] = "auto"
    # Older projects persisted only the free-form ``art_style`` label. Resolve
    # the stable style fields before Pydantic validation while keeping that
    # compatibility text unchanged for existing consumers.
    if not str(profile_data.get("visual_style_id") or "").strip():
        from src.services.visual_styles import resolve_visual_style

        style_id, _compatibility_text, modifiers = resolve_visual_style(
            art_style=str(profile_data.get("art_style") or ""),
            style_modifiers=profile_data.get("style_modifiers") or [],
        )
        profile_data["visual_style_id"] = style_id
        profile_data["style_modifiers"] = modifiers

    state = DramaState(
        user_input=data.get("user_input", ""),
        input_mode=data.get("input_mode", "idea"),
        source_filename=data.get("source_filename", ""),
        script_phase=(
            "confirmed"
            if data.get("script") and data.get("script_phase") in {None, "", "source_required"}
            else (data.get("script_phase") or "source_required")
        ),
        script_draft_text=data.get("script_draft_text", ""),
        script_draft_instruction=data.get("script_draft_instruction", ""),
        draft_job_id=data.get("draft_job_id", ""),
        project_id=data.get("project_id", ""),
        creative_profile=CreativeProfile.model_validate(profile_data),
        total_cost=data.get("total_cost", 0.0),
        review_loop_count=data.get("review_loop_count", 0),
        error_message=data.get("error_message", ""),
        execution_log=data.get("execution_log", []),
        output_video_path=data.get("output_video_path", ""),
        subtitle_enabled=bool(data.get("subtitle_enabled", True)),
        subtitle_mode=(
            data.get("subtitle_mode")
            if data.get("subtitle_mode") in {"off", "dialogue", "speaker"}
            else ("dialogue" if bool(data.get("subtitle_enabled", True)) else "off")
        ),
        assembly_audio_override=str(data.get("assembly_audio_override") or ""),
        prompt_overrides=data.get("prompt_overrides", {}),
        user_feedback=data.get("user_feedback", ""),
        scene_vision_descriptions=data.get("scene_vision_descriptions", {}),
        visual_stage=data.get("visual_stage") or "ready",
        visual_script_signature=data.get("visual_script_signature", ""),
        visual_job_id=data.get("visual_job_id", ""),
        visual_stage_error=data.get("visual_stage_error", ""),
        visual_performance=data.get("visual_performance") or {},
        prompt_review=data.get("prompt_review") or {},
        prompt_fidelity=data.get("prompt_fidelity") or {},
    )

    # 重建 Pydantic 子模型
    from src.schemas.script import ScriptBreakdown
    from src.schemas.visual import VisualDesign
    from src.schemas.character import CharacterImageAssets
    from src.schemas.scene_image import SceneImageAssets
    from src.schemas.prop import PropImageAssets
    from src.schemas.prompt import GenerationPrompts, PromptPolicy
    from src.schemas.assets import GeneratedAssets, ShotProductionState
    from src.schemas.audio import AudioAssets

    state.image_generation_policy = data.get("image_generation_policy") or {}
    state.scene_view_requirements = data.get("scene_view_requirements") or {}
    state.prop_view_requirements = data.get("prop_view_requirements") or {}
    state.video_generation_policy = data.get("video_generation_policy") or {}

    for attr, schema in [
        ("script", ScriptBreakdown),
        ("visual_design", VisualDesign),
        ("character_images", CharacterImageAssets),
        ("scene_images", SceneImageAssets),
        ("prop_images", PropImageAssets),
        ("generation_prompts", GenerationPrompts),
        ("generated_assets", GeneratedAssets),
        ("audio_assets", AudioAssets),
    ]:
        if data.get(attr):
            try:
                setattr(state, attr, schema.model_validate(data[attr]))
            except Exception:
                pass
    try:
        state.prompt_policy = PromptPolicy.model_validate(data.get("prompt_policy") or {})
    except Exception:
        state.prompt_policy = PromptPolicy()
    if not data.get("creative_profile"):
        state.creative_profile = CreativeProfile(
            genre_tags=[item.strip() for item in str(getattr(state.script, "genre", "") or "").replace("，", "、").split("、") if item.strip()],
            art_style=str(getattr(state.visual_design, "overall_style", "") or "写实"),
            video_ratio=str(getattr(state.visual_design, "aspect_ratio", "") or "9:16"),
        )
    if state.script and state.script.asset_manifest:
        from src.utils.visual_asset_manifest import (
            enrich_manifest_descriptions,
            normalize_scene_assets,
            normalize_scene_name,
            remove_non_physical_props,
            reconcile_prop_bindings,
            state_for_scene,
        )

        normalize_scene_assets(state.script)
        previous_character_descriptions = {
            str(character.name or "").strip(): str(character.description or "").strip()
            for character in state.script.asset_manifest.characters
            if str(character.name or "").strip()
        }
        # Apply the same deterministic description repair used after parsing.
        # Without this load-time pass, legacy scene actions and generic prop
        # placeholders remain in the persisted manifest and are reused by
        # every subsequent image prompt after a refresh or restart.
        enrich_manifest_descriptions(
            state.script,
            source_text=(
                state.script_draft_text
                or (state.user_input if state.input_mode == "script" else "")
            ),
        )
        _repair_character_description_consumers(state, previous_character_descriptions)
        # Apply deterministic manifest cleanup while loading legacy projects so
        # stale virtual entries (SMS/recording content) cannot reach generation
        # or the asset UI after a restart.
        remove_non_physical_props(state.script)
        # The denormalized prop shot list and its evidence prose must follow the
        # per-shot binding source of truth after every reload.  Without this,
        # an old LLM manifest can keep displaying stale evidence numbers even
        # though generation uses the corrected bindings.
        reconcile_prop_bindings(state.script)
        by_name = {
            normalize_scene_name(location.canonical_name): location
            for location in state.script.asset_manifest.locations
        }
        # A location's representative scene is only one of its references.
        # Older scene images can point at any other shot in ``shot_ids``; map
        # every bound shot so a location merge also rebinds those images.
        by_scene_id = {}
        for location in state.script.asset_manifest.locations:
            scene_ids = list(getattr(location, "shot_ids", []) or [])
            scene_ids.append(getattr(location, "representative_scene_id", 0))
            for scene_id in scene_ids:
                if scene_id:
                    by_scene_id.setdefault(scene_id, location)
        if state.scene_images:
            for image in state.scene_images.scenes:
                location = by_name.get(normalize_scene_name(image.location)) or by_scene_id.get(image.scene_id)
                if location:
                    image.location_id = location.location_id
                    image.scene_id = location.representative_scene_id
                    image.location = location.canonical_name
                    if not image.state_id:
                        binding_state = state_for_scene(state.script, image.scene_id)
                        image.state_id = binding_state.state_id if binding_state else location.default_state_id
    for scene_id, production in (data.get("shot_productions") or {}).items():
        try:
            state.shot_productions[int(scene_id)] = ShotProductionState.model_validate(production)
        except (TypeError, ValueError):
            pass
    _migrate_legacy_shot_references(state)
    _normalize_prompt_overrides(state)
    _repair_scene_group_prompt_snapshots(state)
    _repair_legacy_storyboard_environment_prompts(state)
    _repair_state_media_paths(state)
    _normalize_internal_style_labels(state)
    _refresh_prompt_language_status(state)
    return state


def _refresh_prompt_language_status(state: DramaState) -> None:
    """Flag legacy English/mixed storyboard prompts without rewriting them."""
    prompts = state.generation_prompts
    if not prompts or not prompts.shots:
        return
    def is_chinese_prompt(text: str) -> bool:
        return len(re.findall(r"[\u3400-\u9fff]", text or "")) >= 12

    prompts.needs_refresh = any(not is_chinese_prompt(shot.prompt) for shot in prompts.shots)
    prompts.prompt_language = "mixed" if prompts.needs_refresh else "zh-CN"


_INTERNAL_STYLE_ID_LABEL = re.compile(
    r"(【(?:项目)?视觉媒介】[^：\n]*?)[（(][A-Za-z][A-Za-z0-9_-]*[）)](?:：|:)"
)


def _normalize_internal_style_labels(state: DramaState) -> None:
    """Remove registry IDs from persisted user/provider-facing prompt text.

    Stable style IDs remain in ``creative_profile.visual_style_id`` for
    configuration and lookup. They are implementation details, however, and
    must not appear in the Chinese storyboard prompt shown to users or sent to
    a provider. Normalize legacy shots recursively so old projects converge
    when they are opened.
    """
    prompts = getattr(state, "generation_prompts", None)
    if not prompts:
        return

    def normalize(shot: Any) -> None:
        prompt = str(getattr(shot, "prompt", "") or "")
        if prompt:
            shot.prompt = _INTERNAL_STYLE_ID_LABEL.sub(r"\1：", prompt)
        for child in getattr(shot, "sub_shots", []) or []:
            normalize(child)

    for shot in getattr(prompts, "shots", []) or []:
        normalize(shot)


def _repair_legacy_storyboard_environment_prompts(state: DramaState) -> None:
    """Remove shot-only actions copied into legacy storyboard environment sections.

    The compact visual pipeline now keeps reusable location identity separate
    from each shot's action. Older snapshots were assembled from a manifest
    that did not enforce that boundary, so loading a project must repair only
    the ``[环境]`` section while preserving the action, camera and user edits
    in every other section.
    """
    prompts = getattr(state, "generation_prompts", None)
    script = getattr(state, "script", None)
    manifest = getattr(script, "asset_manifest", None) if script else None
    if not prompts or not manifest or not getattr(prompts, "shots", None):
        return

    from src.utils.visual_asset_manifest import (
        _clean_environment_description,
        _description_clauses,
        _legacy_dynamic_fragment_sets,
    )

    env_header = "\u3010\u73af\u5883\u3011"
    scenes_by_id = {scene.scene_id: scene for scene in getattr(script, "scenes", [])}
    locations_by_id = {location.location_id: location for location in getattr(manifest, "locations", [])}
    bindings_by_scene = {
        binding.scene_id: binding
        for binding in getattr(manifest, "shot_bindings", [])
    }

    def location_for_scene(scene_id: int):
        binding = bindings_by_scene.get(scene_id)
        if binding and binding.location_id in locations_by_id:
            return locations_by_id[binding.location_id]
        return next(
            (
                location
                for location in locations_by_id.values()
                if scene_id in (getattr(location, "shot_ids", []) or [])
            ),
            None,
        )

    def clean_environment_body(body: str, scene: Any, location: Any) -> str:
        location_shot_ids = set(getattr(location, "shot_ids", []) or [])
        location_scenes = [
            item for item in scenes_by_id.values()
            if item.scene_id in location_shot_ids
        ] or ([scene] if scene is not None else [])
        if not location_scenes:
            return body.strip()
        known_names = {
            str(name or "").strip()
            for item in location_scenes
            for name in (getattr(item, "characters", []) or [])
            if str(name or "").strip()
        }
        legacy_fragments = _legacy_dynamic_fragment_sets(location_scenes)
        cleaned = _clean_environment_description(
            body,
            location_scenes,
            legacy_fragments,
            known_names,
        )
        stable_values = [
            str(getattr(location, "environment_description", "") or ""),
            str(getattr(location, "fixed_dressing", "") or ""),
        ]
        stable_clauses: list[str] = []
        for value in stable_values:
            stable = _clean_environment_description(
                value,
                location_scenes,
                legacy_fragments,
                known_names,
            )
            for clause in _description_clauses(stable):
                if clause not in stable_clauses:
                    stable_clauses.append(clause)

        clauses: list[str] = []
        for value in [*stable_clauses, cleaned]:
            for clause in _description_clauses(value):
                if clause not in clauses:
                    clauses.append(clause)
        location_name = str(
            getattr(location, "canonical_name", "")
            or getattr(scene, "location", "")
            or ""
        ).strip()
        if location_name:
            clauses.insert(0, location_name)
        return "；".join(dict.fromkeys(item for item in clauses if item)).strip("；")

    for shot in prompts.shots:
        prompt = str(getattr(shot, "prompt", "") or "")
        if env_header not in prompt:
            continue
        scene = scenes_by_id.get(getattr(shot, "scene_id", None))
        location = location_for_scene(getattr(shot, "scene_id", None))
        if scene is None or location is None:
            continue

        match = re.search(
            re.escape(env_header) + r"(?P<body>.*?)(?=\u3010|$)",
            prompt,
            flags=re.S,
        )
        if not match:
            continue
        old_body = match.group("body")
        new_body = clean_environment_body(old_body, scene, location)
        if not new_body or (
            new_body == old_body.strip()
            and old_body.endswith(("\n", "\r"))
        ):
            continue
        leading_ws = old_body[: len(old_body) - len(old_body.lstrip())]
        trailing_ws = old_body[len(old_body.rstrip()):]
        if not trailing_ws:
            trailing_ws = "\n"
        replacement_body = leading_ws + new_body + trailing_ws
        start, end = match.span("body")
        shot.prompt = prompt[:start] + replacement_body + prompt[end:]


def _normalize_prompt_overrides(state: DramaState) -> None:
    """Use scene IDs everywhere while preserving only unambiguous legacy key 0."""
    if not state.script or not state.prompt_overrides:
        return
    scene_ids = {scene.scene_id for scene in state.script.scenes}
    normalized: dict[str, str] = {}
    for key, value in state.prompt_overrides.items():
        try:
            scene_id = int(key)
        except (TypeError, ValueError):
            continue
        if scene_id in scene_ids and isinstance(value, str) and value.strip():
            normalized[str(scene_id)] = value.strip()
    # The former zero-based convention can be migrated safely only for key 0.
    if "0" in state.prompt_overrides and state.script.scenes:
        first_id = str(state.script.scenes[0].scene_id)
        normalized.setdefault(first_id, str(state.prompt_overrides["0"]).strip())
    state.prompt_overrides = normalized


def _repair_scene_group_prompt_snapshots(state: DramaState) -> None:
    """Repair legacy scene prompt snapshots without touching media.

    Older scene reference groups stored the shared supplier prompt on every
    ``SceneImage`` and in the image version manifest. The supplier contract
    remains a shared prompt, while the editor must expose each view's camera
    constraint. Older scene masters also stored a complete shot action instead
    of the reusable empty-location contract. This migration is best-effort so
    malformed history cannot prevent a project from loading.
    """
    images = getattr(getattr(state, "scene_images", None), "scenes", None) or []
    if not images:
        return

    manifests: dict[str, dict] = {}
    dirty_manifests: set[str] = set()
    try:
        from src.utils import project_store as project_store_module

        output_root = Path(getattr(project_store_module, "OUTPUT_DIR", OUTPUT_DIR))
    except Exception:
        output_root = Path(OUTPUT_DIR)

    def manifest_path_for(image: Any) -> Path | None:
        raw_path = str(getattr(image, "image_path", "") or "").strip()
        candidates: list[Path] = []
        if raw_path:
            image_path = Path(raw_path)
            if not image_path.is_absolute():
                image_path = output_root / str(state.project_id or "") / image_path
            for parent in image_path.parents:
                if parent.name.lower() == "assets":
                    candidates.append(parent / "asset_manifest.json")
                    break
        if state.project_id:
            candidates.append(output_root / state.project_id / "assets" / "asset_manifest.json")
        for candidate in candidates:
            try:
                if candidate.is_file():
                    return candidate
            except OSError:
                continue
        return None

    def load_manifest(path: Path) -> dict | None:
        key = str(path)
        if key in manifests:
            return manifests[key]
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(value, dict) or not isinstance(value.get("versions"), dict):
            return None
        manifests[key] = value
        return value

    for image in images:
        try:
            view_type = str(getattr(image, "view_type", "") or "")
            generation_mode = str(getattr(image, "generation_mode", "") or "").lower()
            if not view_type.startswith("scene_"):
                continue
            try:
                template_version = int(getattr(image, "prompt_template_version", 1) or 1)
            except (TypeError, ValueError):
                template_version = 1
            is_group_snapshot = generation_mode == "reference_group"
            # A master can already carry the current template version while
            # still retaining a stale base prompt from an earlier project
            # style/state migration.  The template version alone therefore
            # cannot decide whether the snapshot is safe to keep.
            is_master_snapshot = generation_mode == "master"
            from src.services.asset_prompt_composer import SCENE_PROMPT_TEMPLATE_VERSION, compose_asset_prompt

            if not (is_group_snapshot or is_master_snapshot):
                continue
            asset_id = str(getattr(image, "location_id", "") or "").strip() or str(getattr(image, "scene_id", ""))
            if not asset_id:
                continue
            legacy_prompt = str(getattr(image, "image_prompt", "") or "").strip()

            composition = compose_asset_prompt(
                state,
                "scene",
                asset_id,
                view_type,
                image,
                provider="wan",
            )
            display_prompt = str(composition.get("effective_prompt") or "").strip()
            if not display_prompt:
                continue
            if is_master_snapshot and view_type == "scene_center":
                display_prompt = (
                    "高质量专业场景中心母版。只生成一张16:9横向无人物空场景图，"
                    "机位为正面中心线大全景，镜头正对空间中心轴，严格居中对称。"
                    "这张图将作为后续多视角参考组图的母版，必须清晰锁定空间结构、固定陈设、材质、时间天气和主光方向。\n"
                    + display_prompt
                )
            image.image_prompt = display_prompt
            base_prompt = str(composition.get("base_prompt") or "").strip()
            if base_prompt:
                image.base_prompt = base_prompt
            try:
                image.prompt_template_version = int(
                    composition.get("prompt_template_version") or image.prompt_template_version
                )
            except (TypeError, ValueError):
                pass

            manifest_path = manifest_path_for(image)
            if not manifest_path:
                continue
            manifest = load_manifest(manifest_path)
            if manifest is None:
                continue
            version_id = str(getattr(image, "version_id", "") or "").strip()
            image_path = str(getattr(image, "image_path", "") or "")
            key = f"scene:{asset_id}:{view_type}"
            records = manifest.get("versions", {}).get(key)
            if not isinstance(records, list):
                # Loading normalizes legacy location IDs before this helper
                # runs. Fall back to the old key when its record still points
                # at this image/version.
                records = None
                for candidate_key, candidate_records in manifest.get("versions", {}).items():
                    if not str(candidate_key).endswith(f":{view_type}") or not isinstance(candidate_records, list):
                        continue
                    if any(
                        isinstance(item, dict)
                        and (
                            (version_id and str(item.get("id") or "") == version_id)
                            or (image_path and str(item.get("path") or "") == image_path)
                        )
                        for item in candidate_records
                    ):
                        key = str(candidate_key)
                        records = candidate_records
                        break
            if not isinstance(records, list):
                continue
            record = next(
                (
                    item for item in records
                    if isinstance(item, dict)
                    and version_id
                    and str(item.get("id") or "") == version_id
                ),
                None,
            )
            if record is None:
                record = next(
                    (
                        item for item in records
                        if isinstance(item, dict)
                        and image_path
                        and str(item.get("path") or "") == image_path
                    ),
                    None,
                )
            if record is None:
                record = next(
                    (item for item in records if isinstance(item, dict) and item.get("active")),
                    None,
                )
            if record is None:
                continue
            # Preserve the old shared supplier prompt before replacing the
            # editor-facing prompt with the selected view's prompt.
            provider_prompt = str(
                record.get("provider_prompt")
                or record.get("prompt")
                or legacy_prompt
                or ""
            ).strip()
            if provider_prompt and not record.get("provider_prompt"):
                record["provider_prompt"] = provider_prompt
            record["prompt"] = display_prompt
            record["display_prompt"] = display_prompt
            dirty_manifests.add(str(manifest_path))
        except Exception:
            # A single malformed legacy image cannot block project startup.
            continue

    for key in dirty_manifests:
        manifest = manifests.get(key)
        if manifest is None:
            continue
        try:
            Path(key).write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except (OSError, TypeError, ValueError):
            continue


def _load_project_state(
    project_id: str,
    require_visual: bool = True,
    require_script: bool = True,
) -> DramaState:
    try:
        meta = project_store.get_project(project_id)
    except ValueError as exc:
        raise HTTPException(400, "Invalid project ID") from exc
    if not meta:
        raise HTTPException(404, "Project not found")
    state_json = (meta.get("assets") or {}).get("state_json") or ""
    state = _state_from_json(state_json)
    state.project_id = project_id
    if require_script and not state.script:
        raise HTTPException(409, "请先完成剧本解析")
    if require_visual and (state.visual_stage != "ready" or not state.generation_prompts):
        raise HTTPException(409, "请先完成剧本解析和分镜提示词生成")
    # Loading performs deterministic migrations (scene binding cleanup,
    # path relocation, prompt normalization). Persist the normalized state so
    # legacy action text cannot reappear after the next restart. Empty or
    # uninitialized projects are intentionally skipped.
    if state_json:
        normalized_json = _state_to_json(state)
        persisted_meta = project_store.get_project(project_id) or {}
        persisted_assets = persisted_meta.get("assets") or {}
        expected_snapshots = {
            "script": state.script.model_dump() if state.script is not None else None,
            "visual_design": state.visual_design.model_dump() if state.visual_design is not None else None,
            "generation_prompts": state.generation_prompts.model_dump() if state.generation_prompts is not None else None,
            "character_images": state.character_images.model_dump() if state.character_images is not None else None,
            "scene_images": state.scene_images.model_dump() if state.scene_images is not None else None,
            "prop_images": state.prop_images.model_dump() if state.prop_images is not None else None,
            "generated_assets": state.generated_assets.model_dump() if state.generated_assets is not None else None,
            "audio_assets": state.audio_assets.model_dump() if state.audio_assets is not None else None,
        }
        snapshots_stale = any(
            field in persisted_assets and persisted_assets.get(field) != value
            for field, value in expected_snapshots.items()
        ) or any(
            field not in persisted_assets and value is not None
            for field, value in expected_snapshots.items()
        )
        if normalized_json != state_json or snapshots_stale:
            project_store.set_state_json(project_id, normalized_json)
    return state


def _save_project_state(state: DramaState) -> None:
    project_store.set_state_json(state.project_id, _state_to_json(state))


def _reconcile_state_after_script_change(
    state: DramaState,
    script,
    visual_design=None,
    generation_prompts=None,
) -> DramaState:
    """Preserve matching image identities and invalidate downstream media."""
    from src.utils.visual_asset_manifest import normalize_scene_assets, normalize_scene_name, scene_source_signature, state_for_scene

    old_script = state.script
    state.prompt_fidelity = {}
    state.prompt_review = {
        "status": "pending",
        "prompt_signature": "",
        "job_id": "",
        "error": "",
    }
    normalize_scene_assets(script)
    manifest = script.asset_manifest
    allowed_characters = {
        _character_identity(item.name)
        for item in (manifest.characters if manifest else [])
        if item.appearance_mode == "on_screen"
    }
    if state.character_images:
        state.character_images.characters = [
            item for item in state.character_images.characters
            if _character_identity(item.character_name) in allowed_characters
        ]

    allowed_props = {item.prop_id for item in (manifest.props if manifest else [])}
    if state.prop_images:
        state.prop_images.props = [
            item for item in state.prop_images.props if item.prop_id in allowed_props
        ]

    old_location_names: dict[str, str] = {}
    if old_script and old_script.asset_manifest:
        for location in old_script.asset_manifest.locations:
            old_location_names[location.location_id] = normalize_scene_name(location.canonical_name)
            old_location_names[str(location.representative_scene_id)] = normalize_scene_name(location.canonical_name)
    new_locations = {
        normalize_scene_name(location.canonical_name): location
        for location in (manifest.locations if manifest else [])
    }
    if state.scene_images:
        kept = []
        for image in state.scene_images.scenes:
            key = normalize_scene_name(image.location) or old_location_names.get(
                image.location_id or str(image.scene_id), ""
            )
            location = new_locations.get(key)
            if not location:
                continue
            image.location_id = location.location_id
            image.scene_id = location.representative_scene_id
            image.location = location.canonical_name
            if not image.state_id:
                state_asset = state_for_scene(script, image.scene_id)
                image.state_id = state_asset.state_id if state_asset else location.default_state_id
            current_signature = scene_source_signature(
                location.canonical_name, location.environment_description,
            )
            image.is_stale = bool(image.source_signature and image.source_signature != current_signature)
            kept.append(image)
        state.scene_images.scenes = kept

    state.script = script
    if visual_design is not None:
        state.visual_design = visual_design
    if generation_prompts is not None:
        state.generation_prompts = generation_prompts
    elif visual_design is None:
        state.visual_design = None
        state.generation_prompts = None
    state.generated_assets = None
    state.review_result = None
    state.audio_assets = None
    state.shot_productions = {}
    state.output_video_path = ""
    state.scene_vision_descriptions = {}
    state.prompt_overrides = {}
    state.review_loop_count = 0
    state.error_message = ""
    return state


def _shot_prompt(state: DramaState, scene_id: int):
    prompt = next(
        (item for item in state.generation_prompts.shots if item.scene_id == scene_id),
        None,
    )
    if prompt is None:
        raise HTTPException(404, "Storyboard shot not found")
    return prompt


def _script_scene(state: DramaState, scene_id: int):
    scene = next((item for item in state.script.scenes if item.scene_id == scene_id), None)
    if scene is None:
        raise HTTPException(404, "Storyboard scene not found")
    return scene


def _image_allows_automatic_reference(image) -> bool:
    return image_allows_automatic_reference(image)


def _reference_catalog(state: DramaState) -> dict[str, object]:
    """Build the editable reference inventory from currently saved image assets."""
    from src.schemas.assets import ShotReference

    references: dict[str, ShotReference] = {}
    auto_reference_ids: set[str] = set()
    characters: list[dict] = []
    scenes: list[dict] = []
    props: list[dict] = []
    for image in (state.character_images.characters if state.character_images else []):
        if not image.image_path or not Path(image.image_path).is_file():
            continue
        ref = ShotReference(
            reference_id=f"character|{image.character_name}|{image.view_type}",
            asset_type="character",
            asset_id=image.character_name,
            view_type=image.view_type,
            image_path=image.image_path,
            label=f"{image.character_name} {image.view_type}",
            source="asset",
            asset_version_id=image.version_id,
        )
        references[ref.reference_id] = ref
        if _image_allows_automatic_reference(image):
            auto_reference_ids.add(ref.reference_id)
        characters.append(ref.model_dump())
    for image in (state.scene_images.scenes if state.scene_images else []):
        if not image.image_path or not Path(image.image_path).is_file():
            continue
        scene_asset_id = image.location_id or str(image.scene_id)
        state_id = image.state_id or ""
        reference_id = (
            f"scene|{scene_asset_id}|{state_id}|{image.view_type}"
            if state_id
            else f"scene|{scene_asset_id}|{image.view_type}"
        )
        ref = ShotReference(
            reference_id=reference_id,
            asset_type="scene",
            asset_id=scene_asset_id,
            state_id=state_id,
            view_type=image.view_type,
            image_path=image.image_path,
            label=f"{image.location or f'场景 {image.scene_id}'} {state_id or 'default'} {image.view_type}",
            source="asset",
            asset_version_id=image.version_id,
        )
        references[ref.reference_id] = ref
        if _image_allows_automatic_reference(image):
            auto_reference_ids.add(ref.reference_id)
        # Accept legacy scene IDs such as scene|1|wide after projects migrate
        # to stable location IDs such as scene|loc_xxx|wide.
        if image.scene_id:
            references.setdefault(f"scene|{image.scene_id}|{image.view_type}", ref)
        if image.location_id:
            references.setdefault(f"scene|{image.location_id}|{image.view_type}", ref)
            if state_id:
                references.setdefault(f"scene|{image.location_id}|{state_id}|{image.view_type}", ref)
        scenes.append(ref.model_dump())
    for image in (state.prop_images.props if state.prop_images else []):
        if not video_gen_agent._is_valid_video_prop_reference(image):
            continue
        ref = ShotReference(
            reference_id=f"prop|{image.prop_id}|{image.view_type}",
            asset_type="prop",
            asset_id=image.prop_id,
            view_type=image.view_type,
            image_path=image.image_path,
            label=f"道具 {image.prop_name}",
            source="asset",
            asset_version_id=image.version_id,
        )
        references[ref.reference_id] = ref
        if _image_allows_automatic_reference(image):
            auto_reference_ids.add(ref.reference_id)
        props.append(ref.model_dump())
    return {
        "by_id": references,
        "characters": characters,
        "scenes": scenes,
        "props": props,
        "auto_reference_ids": auto_reference_ids,
    }


def _missing_media_paths(state: DramaState) -> list[str]:
    """List missing local media without mutating the persisted project state."""
    missing: set[str] = set()

    def collect(path: str | None) -> None:
        if not path or re.match(r"^https?://", path, re.IGNORECASE):
            return
        if not Path(path).is_file():
            missing.add(path)

    collect(state.output_video_path)
    for collection in (
        state.character_images.characters if state.character_images else [],
        state.scene_images.scenes if state.scene_images else [],
        state.prop_images.props if state.prop_images else [],
    ):
        for item in collection:
            collect(getattr(item, "image_path", ""))
    for item in (state.generated_assets.shots if state.generated_assets else []):
        collect(getattr(item, "local_path", ""))
    for production in state.shot_productions.values():
        for reference in production.references:
            collect(reference.image_path)
        for version in production.video_versions:
            collect(version.local_path)
    if state.audio_assets:
        audio = state.audio_assets
        collect(audio.bgm_path)
        collect(audio.narration_audio_path)
        collect(audio.preview_mix_path)
        for item in audio.library_assets:
            collect(item.audio_path)
        for scene in audio.scenes:
            collect(scene.tts_audio_path)
            for path in scene.sfx_paths:
                collect(path)
            for line in scene.dialogue_audios:
                collect(line.audio_path)
                for version in line.versions:
                    collect(version.audio_path)
    return sorted(missing)


def _asset_reference_key(reference) -> tuple:
    if reference.asset_type == "scene":
        state_id = getattr(reference, "state_id", "") or ""
        if state_id:
            return reference.asset_type, reference.asset_id, state_id, reference.view_type
        return reference.asset_type, reference.asset_id, reference.view_type
    return reference.asset_type, reference.asset_id, reference.view_type


def _reference_value(reference, key: str, default: str = "") -> str:
    if isinstance(reference, dict):
        return str(reference.get(key) or default)
    return str(getattr(reference, key, default) or default)


def _public_reference_token(reference) -> str:
    """Return the stable @ token rendered by the production prompt editor."""
    token = _reference_value(reference, "reference_token").strip()
    if token:
        return token
    asset_type = _reference_value(reference, "asset_type", "asset")
    asset_id = _reference_value(reference, "asset_id") or _reference_value(reference, "reference_id")
    view_type = _reference_value(reference, "view_type", "default")
    return f"@{asset_type}:{asset_id}:{view_type}"


def _reference_submission_key(reference) -> tuple[str, str]:
    """One physical asset version occupies one provider reference slot."""
    return (
        _reference_value(reference, "reference_id"),
        _reference_value(reference, "asset_version_id"),
    )


def _deduplicate_shot_references(references):
    """Keep the first occurrence and order for repeated asset-version bindings."""
    unique = []
    seen: set[tuple[str, str]] = set()
    for reference in references or []:
        key = _reference_submission_key(reference)
        if key in seen:
            continue
        seen.add(key)
        unique.append(reference)
    return unique


def _reference_text_candidates(reference) -> list[str]:
    """Names likely to occur in a generated Chinese or English shot description."""
    candidates = [
        _reference_value(reference, "asset_id"),
        _reference_value(reference, "label"),
    ]
    label = _reference_value(reference, "label")
    view_type = _reference_value(reference, "view_type")
    if view_type and label.lower().endswith(view_type.lower()):
        candidates.append(label[: -len(view_type)].strip())
    candidates.extend(
        candidate.removeprefix("道具 ").removeprefix("角色 ").strip()
        for candidate in list(candidates)
        if candidate
    )
    normalized: list[str] = []
    for candidate in sorted(candidates, key=len, reverse=True):
        if candidate and candidate not in normalized:
            normalized.append(candidate)
    return normalized


def _replace_reference_mentions(text: str, reference, token: str) -> tuple[str, bool]:
    """Replace every textual occurrence of one asset with its inline @ token."""
    for candidate in _reference_text_candidates(reference):
        escaped = re.escape(candidate)
        if re.fullmatch(r"[A-Za-z0-9_ -]+", candidate):
            pattern = rf"(?<![@A-Za-z0-9_:-]){escaped}(?![A-Za-z0-9_-])"
        else:
            # Chinese names have no word boundary, but they must never be
            # matched again inside an earlier @character:Name:view token.
            pattern = rf"(?<![@A-Za-z0-9_:-]){escaped}"
        replaced, count = re.subn(pattern, token, text, flags=re.IGNORECASE)
        if count:
            return replaced, True
    return text, False


def _character_inline_view_rank(prompt: str, reference) -> int:
    """Choose the one character image that represents this shot in the text."""
    view_type = _reference_value(reference, "view_type").lower()
    normalized_prompt = str(prompt or "")
    if any(term in normalized_prompt for term in ("背面", "背影", "背后", "背身")):
        preference = ("back", "profile", "front_full", "front_half")
    elif any(term in normalized_prompt for term in ("侧面", "侧脸", "侧身", "过肩")):
        preference = ("profile", "front_half", "front_full", "back")
    elif any(term in normalized_prompt for term in ("全身", "远景", "大全景")):
        preference = ("front_full", "front_half", "profile", "back")
    else:
        preference = ("front_half", "front_full", "profile", "back")
    for index, marker in enumerate(preference):
        if marker in view_type:
            return index
    return len(preference)


def _automatic_references_for_inline_text(prompt: str, references):
    """Keep one semantic text anchor per character while retaining all uploads."""
    automatic = [
        reference
        for reference in _deduplicate_shot_references(references)
        if _reference_value(reference, "source", "asset") == "asset"
    ]
    character_groups: dict[str, list] = {}
    for reference in automatic:
        if _reference_value(reference, "asset_type") == "character":
            identity = _reference_value(reference, "asset_id") or _reference_value(reference, "reference_id")
            character_groups.setdefault(identity, []).append(reference)
    primary_characters = {
        identity: min(group, key=lambda reference: _character_inline_view_rank(prompt, reference))
        for identity, group in character_groups.items()
    }
    selected = []
    seen_characters: set[str] = set()
    for reference in automatic:
        if _reference_value(reference, "asset_type") != "character":
            selected.append(reference)
            continue
        identity = _reference_value(reference, "asset_id") or _reference_value(reference, "reference_id")
        if identity in seen_characters:
            continue
        selected.append(primary_characters[identity])
        seen_characters.add(identity)
    return selected


def _inline_automatic_asset_mentions(prompt: str, references) -> str:
    """Embed automatic image assets where their text constraints are expressed.

    The prompt remains plain text for persistence and providers.  The frontend
    renders the stable @ tokens as image chips, while the task snapshot maps
    each distinct asset version to one uploaded reference image.
    """
    text = str(prompt or "").strip()
    automatic = _automatic_references_for_inline_text(text, references)
    if not automatic:
        return text

    prefixes: list[str] = []
    for reference in automatic:
        if _reference_value(reference, "asset_type") != "scene":
            continue
        token = _public_reference_token(reference)
        if token not in text:
            prefixes.append(
                f"本镜场景设定在：{token}。空间结构、环境细节、光线、天气、时间与主色调必须以该场景图为准。"
            )

    for reference in automatic:
        asset_type = _reference_value(reference, "asset_type")
        if asset_type == "scene":
            continue
        token = _public_reference_token(reference)
        if token in text:
            continue
        text, found = _replace_reference_mentions(text, reference, token)
        if found:
            continue
        if asset_type == "character":
            prefixes.append(f"出镜人物：{token}。脸型、发型、服装、体态与身份特征必须保持一致。")
        elif asset_type == "prop":
            prefixes.append(f"关键道具：{token}。造型、材质、尺寸与画面中的使用状态必须保持一致。")

    return "\n".join([*prefixes, text]).strip()


def _default_shot_references(state: DramaState, scene_id: int):
    from src.schemas.prompt import GenerationPrompts

    prompt = _shot_prompt(state, scene_id)
    catalog = _reference_catalog(state)
    identities = video_gen_agent._build_reference_identity_bundle(
        GenerationPrompts(
            api_provider=state.generation_prompts.api_provider,
            shots=[prompt],
        ),
        state.character_images,
        state.scene_images,
        state.script,
        state.prop_images,
    ).get(scene_id, [])
    by_identity = {_asset_reference_key(reference): reference for reference in catalog["by_id"].values()}
    return [
        by_identity[identity].model_copy(deep=True)
        for identity in identities
        if identity in by_identity
        and by_identity[identity].reference_id in catalog["auto_reference_ids"]
    ]


def _reconcile_shot_references(state: DramaState, scene_id: int):
    """Refresh only asset-bound references while retaining manual entries and order."""
    production = _ensure_shot_production(state, scene_id)
    latest_by_identity = {
        _asset_reference_key(reference): reference
        for reference in _default_shot_references(state, scene_id)
    }
    reconciled = []
    for reference in production.references:
        if reference.source == "manual":
            reconciled.append(reference)
            continue
        if reference.source == "explicit":
            reconciled.append(_resolve_explicit_reference_version(state, reference))
            continue
        latest = latest_by_identity.pop(_asset_reference_key(reference), None)
        reconciled.append(latest or reference.model_copy(update={"stale": True}))
    reconciled.extend(latest_by_identity.values())
    production.references = _deduplicate_shot_references(reconciled)
    return production.references


def _resolve_explicit_reference_version(state: DramaState, reference):
    """Keep an explicit @ reference on the selected image version when possible."""
    version_id = str(getattr(reference, "asset_version_id", "") or "").strip()
    if not version_id or reference.asset_type not in {"character", "scene", "prop"}:
        return reference
    try:
        from src.utils.asset_versions import ImageAssetVersions

        versions = ImageAssetVersions(project_store.get_project_dir(state.project_id))
        record = versions.find_version(reference.asset_type, reference.asset_id, reference.view_type, version_id)
    except (OSError, TypeError, ValueError):
        record = None
    if record is None:
        # Legacy projects may have a version id in state without a migrated
        # asset-manifest record. Keep a still-existing path usable rather than
        # breaking an otherwise valid reference; missing files remain stale.
        return reference.model_copy(update={
            "stale": not bool(reference.image_path and Path(reference.image_path).is_file()),
        })
    path = str(record.get("path") or "")
    if not path:
        return reference.model_copy(update={"stale": True})
    try:
        resolved_path = Path(path).resolve()
        resolved_path.relative_to(versions.asset_root.resolve())
    except (OSError, ValueError):
        return reference.model_copy(update={"stale": True})
    return reference.model_copy(update={
        "image_path": str(resolved_path),
        "stale": not resolved_path.is_file(),
    })


def _policy_prompt_provider(policy: dict) -> str:
    return "happyhorse" if policy.get("provider") in {"happyhorse", "wan"} else str(policy.get("provider") or "mock")


def _clamp_production_duration(value) -> int:
    try:
        duration = int(round(float(value)))
    except (TypeError, ValueError):
        duration = 5
    return max(PRODUCTION_MIN_DURATION_SEC, min(duration, PRODUCTION_MAX_DURATION_SEC))


def _duration_for_video_policy(value, policy: dict) -> int:
    """Use model capability bounds for Seedance while preserving legacy limits elsewhere."""
    if str(policy.get("provider") or "").lower() == "seedance":
        from src.services.video_capabilities import validate_video_duration

        return validate_video_duration(policy, value)
    return _clamp_production_duration(value)


def _safe_reference_tag_part(value: object, fallback: str = "ref") -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return normalized or fallback


def _reference_tag_for_item(item: dict, index: int) -> str:
    asset_type = _safe_reference_tag_part(item.get("asset_type") or "reference", "reference")
    view_type = _safe_reference_tag_part(item.get("view_type") or item.get("source") or "image", "image")
    if asset_type == "continuity":
        return f"@ref{index:02d}_continuity_{view_type}"
    return f"@ref{index:02d}_{asset_type}_{view_type}"


def _reference_label_for_item(item: dict, index: int) -> str:
    tag = str(item.get("reference_tag") or _reference_tag_for_item(item, index))
    label = str(item.get("label") or item.get("asset_id") or "reference").strip()
    asset_type = str(item.get("asset_type") or "reference").strip()
    view_type = str(item.get("view_type") or "").strip()
    token = _public_reference_token(item)
    prefix = f"{token} -> " if token else ""
    return prefix + f"{tag} = {asset_type}: {label}" + (f" ({view_type})" if view_type else "")


def _visual_style_snapshot(profile) -> dict:
    """Freeze the canonical project style used by a generation request."""
    from copy import deepcopy
    from src.services.visual_styles import get_visual_style, resolve_visual_style

    style_id, legacy, modifiers = resolve_visual_style(
        visual_style_id=getattr(profile, "visual_style_id", "") if profile is not None else "",
        art_style=getattr(profile, "art_style", "") if profile is not None else "",
        style_modifiers=getattr(profile, "style_modifiers", []) if profile is not None else [],
    )
    style = get_visual_style(style_id)
    return {
        "id": style_id,
        "name": style["name"],
        "legacy_art_style": legacy,
        "modifiers": list(modifiers),
        "prompt_layers": deepcopy(style.get("prompt_layers", {})),
        "negative_prompt_layers": deepcopy(style.get("negative_prompt_layers", {})),
    }


def _build_video_generation_snapshot(state: DramaState, scene_id: int, policy: dict) -> dict:
    """Freeze the exact prompt and ordered image references for one video task."""
    from src.services.video_capabilities import video_agent_for_policy
    from src.services.video_continuity import continuity_reference
    from src.services.native_audio import append_native_dialogue_prompt, build_dialogue_plan

    raw_prompt = _shot_prompt(state, scene_id).model_copy(deep=True)
    raw_prompt.duration_sec = _duration_for_video_policy(raw_prompt.duration_sec, policy)
    reconciled_references = _deduplicate_shot_references(_reconcile_shot_references(state, scene_id))
    raw_prompt.prompt = _inline_automatic_asset_mentions(raw_prompt.prompt, reconciled_references)
    unavailable_explicit = [
        reference.reference_token or reference.reference_id
        for reference in reconciled_references
        if reference.source == "explicit"
        and (not reference.image_path or not Path(reference.image_path).is_file() or reference.stale)
    ]
    if unavailable_explicit:
        raise ValueError("显式引用不可用：" + "、".join(unavailable_explicit))
    references = [
        reference.model_dump()
        for reference in reconciled_references
        if reference.image_path and Path(reference.image_path).is_file() and not reference.stale
    ]
    continuity_reference_added = False
    if policy.get("capability", {}).get("continuity"):
        tail_path, tail_label = continuity_reference(state, scene_id, policy)
        if tail_path and Path(tail_path).is_file():
            continuity_reference_added = True
            references.insert(0, {
                "reference_id": f"continuity|{scene_id}",
                "source": "continuity",
                "asset_type": "continuity",
                "asset_id": str(scene_id),
                "view_type": "tail_frame",
                "asset_version_id": "",
                "image_path": tail_path,
                "label": tail_label or "前一镜尾帧",
                "stale": False,
            })
    # Keep the semantic priority stable before applying provider limits:
    # continuity tail > explicit @ mention > manual selection > automatic binding.
    priority = {"continuity": 0, "explicit": 1, "manual": 2, "asset": 3}
    references.sort(key=lambda item: priority.get(str(item.get("source") or "asset"), 3))
    capability = policy.get("capability") or {}
    reference_mode = "text_to_video"
    dropped_references: list[dict[str, str]] = []
    video_agent = video_agent_for_policy(policy)
    if references and policy.get("provider") == "seedance":
        # Seedance model families do not all accept the same image input
        # shape.  In particular, the legacy lite-i2v model accepts exactly
        # one first frame and rejects the multi-reference content role.  Pick
        # the mode from the frozen capability snapshot so the task payload,
        # UI warning, and provider request stay consistent.
        if continuity_reference_added:
            reference_mode = "first_frame"
        elif capability.get("multi_reference"):
            reference_mode = "multi_reference"
        elif capability.get("supports_first_frame", capability.get("image_to_video")):
            reference_mode = "first_frame"
        else:
            reference_mode = "text_to_video"
        plan = video_agent.api_client.plan_reference_inputs(
            [item["image_path"] for item in references],
            reference_mode=reference_mode,
        )
        accepted_items = list(plan.get("items") or [])
        accepted_paths = {str(item.get("path")) for item in accepted_items}
        accepted_roles = {str(item.get("path")): str(item.get("role") or "") for item in accepted_items}
        dropped_references = [dict(item) for item in plan.get("dropped_references") or []]
        explicit_paths = {
            str(item.get("image_path"))
            for item in references
            if item.get("source") == "explicit"
        }
        dropped_explicit = [
            item for item in dropped_references if str(item.get("path") or "") in explicit_paths
        ]
        if dropped_explicit:
            labels = [str(item.get("reference_token") or item.get("reference_id") or item.get("path"))
                      for item in references if str(item.get("image_path")) in {str(d.get("path") or "") for d in dropped_explicit}]
            raise ValueError("显式引用因模型限制无法提交：" + "、".join(labels))
        missing_explicit = [
            item for item in references
            if item.get("source") == "explicit"
            and str(item.get("image_path")) not in accepted_paths
        ]
        if missing_explicit:
            labels = [str(item.get("reference_token") or item.get("reference_id") or item.get("label"))
                      for item in missing_explicit]
            raise ValueError("显式引用未被供应商接受：" + "、".join(labels))
        references = [
            item for item in references
            if str(item.get("image_path")) in accepted_paths
        ]
        for item in references:
            role = accepted_roles.get(str(item.get("image_path")))
            if role:
                item["provider_role"] = role
    elif references:
        # Every non-Seedance provider follows the same frozen capability
        # contract.  The old path only reduced single-reference models and
        # left text-to-video/reference-to-video limits to the client, which
        # made the UI and task snapshot disagree with the actual request.
        model = str(policy.get("model") or "selected model")
        supports_references = (
            bool(capability.get("image_to_video"))
            if "image_to_video" in capability
            else True
        )
        if not supports_references:
            explicit_references = [item for item in references if item.get("source") == "explicit"]
            if explicit_references:
                labels = [str(item.get("reference_token") or item.get("reference_id") or item.get("label"))
                          for item in explicit_references]
                raise ValueError(f"显式引用不支持参考图：{', '.join(labels)}")
            reference_mode = "text_to_video"
            dropped_references = [
                {"path": item["image_path"], "reason": f"{model} does not support reference images"}
                for item in references
            ]
            references = []
        else:
            reference_mode = (
                "multi_reference"
                if capability.get("multi_reference")
                else ("first_frame" if continuity_reference_added else "single_reference")
            )
            configured_limit = capability.get("max_reference_images")
            legacy_reference_capability = not any(
                key in capability for key in ("image_to_video", "multi_reference", "max_reference_images")
            )
            try:
                maximum = int(configured_limit) if configured_limit is not None else (
                    len(references)
                    if legacy_reference_capability or capability.get("multi_reference")
                    else 1
                )
            except (TypeError, ValueError):
                maximum = (
                    len(references)
                    if legacy_reference_capability or capability.get("multi_reference")
                    else 1
                )
            maximum = max(0, maximum)
            if maximum == 0:
                explicit_references = [item for item in references if item.get("source") == "explicit"]
                if explicit_references:
                    labels = [str(item.get("reference_token") or item.get("reference_id") or item.get("label"))
                              for item in explicit_references]
                    raise ValueError("显式引用因模型限制无法提交：" + "、".join(labels))
                dropped_references = [
                    {"path": item["image_path"], "reason": f"{model} does not support reference images"}
                    for item in references
                ]
                references = []
                reference_mode = "text_to_video"
            elif len(references) > maximum:
                dropped_references = [
                    {
                        "path": item["image_path"],
                        "reason": f"{model} accepts a maximum of {maximum} reference images",
                    }
                    for item in references[maximum:]
                ]
                dropped_explicit = [
                    item for item in references[maximum:]
                    if item.get("source") == "explicit"
                ]
                if dropped_explicit:
                    labels = [str(item.get("reference_token") or item.get("reference_id") or item.get("label"))
                              for item in dropped_explicit]
                    raise ValueError("显式引用因模型限制无法提交：" + "、".join(labels))
                references = references[:maximum]
    for index, item in enumerate(references, start=1):
        item["reference_tag"] = _reference_tag_for_item(item, index)
        item["inline_token"] = _public_reference_token(item)
    prepared, effective = video_agent.prepare_shot_request(
        raw_prompt,
        generation_prompts=state.generation_prompts,
        prompt_policy=state.prompt_policy,
        visual_design=state.visual_design,
        scene_vision_descriptions=state.scene_vision_descriptions,
        script=state.script,
        creative_profile=state.creative_profile,
    )
    if references:
        prepared = prepared.model_copy(update={
            "motion_strength": video_agent._motion_strength_for_shot(prepared, has_references=True),
        })
    labels = [
        f"{item.get('asset_type', 'reference')}：{item.get('label') or item.get('asset_id', '')}（{item.get('view_type', '')}）"
        for item in references
    ]
    labels = [_reference_label_for_item(item, index) for index, item in enumerate(references, start=1)]
    final_provider_prompt = prepared.prompt
    if references:
        final_provider_prompt = video_agent._build_multi_reference_prompt(
            prepared,
            [item["image_path"] for item in references],
            labels,
            state.visual_design,
            state.scene_vision_descriptions,
            state.script,
        )
    scene = _script_scene(state, scene_id)
    dialogue_plan = build_dialogue_plan(scene)
    audio_mode_requested = str(getattr(state.creative_profile, "audio_mode", "auto") or "auto").lower()
    if audio_mode_requested not in {"auto", "native_dialogue", "external_tts"}:
        audio_mode_requested = "auto"
    capability = policy.get("capability") or {}
    provider_name = str(policy.get("provider") or "").lower()
    provider_supports_native = (
        bool(capability.get("supports_native_dialogue"))
        if "supports_native_dialogue" in capability
        else provider_name == "seedance"
    )
    if audio_mode_requested == "native_dialogue" and not provider_supports_native:
        model_name = str(policy.get("model") or policy.get("provider") or "当前模型")
        raise ValueError(
            f"{model_name} 不支持视频模型原生对白，请切换为外部 TTS 配音"
        )
    native_audio_requested = (
        audio_mode_requested == "native_dialogue"
        or (audio_mode_requested == "auto" and provider_supports_native)
    )
    audio_mode_effective = "native_dialogue" if native_audio_requested else "external_tts"
    if native_audio_requested:
        final_provider_prompt, _native_lines = append_native_dialogue_prompt(
            final_provider_prompt, scene,
        )
        prepared = prepared.model_copy(update={"prompt": final_provider_prompt})
        effective["provider_prompt"] = final_provider_prompt
        effective["effective_prompt"] = final_provider_prompt
    style_snapshot = _visual_style_snapshot(state.creative_profile)
    explicit_references = [item for item in references if item.get("source") == "explicit"]
    snapshot = {
        "scene_id": scene_id,
        "source_prompt": _shot_prompt(state, scene_id).prompt,
        "source_duration_sec": int(_shot_prompt(state, scene_id).duration_sec or 0),
        "effective_duration_sec": int(raw_prompt.duration_sec or 0),
        "raw_prompt": raw_prompt.prompt,
        "raw_negative_prompt": raw_prompt.negative_prompt,
        "prepared_shot": prepared.model_dump(),
        "provider": _policy_prompt_provider(policy),
        "effective_prompt": effective["effective_prompt"],
        "effective_negative_prompt": effective["effective_negative_prompt"],
        "provider_prompt": final_provider_prompt,
        "provider_negative_prompt": prepared.negative_prompt,
        "negative_prompt_mode": effective["negative_prompt_mode"],
        "references": references,
        "explicit_references": explicit_references,
        "manual_references": [item for item in references if item.get("source") == "manual"],
        "automatic_references": [item for item in references if item.get("source") == "asset"],
        "accepted_references": references,
        "dropped_references": dropped_references,
        "reference_mode": reference_mode,
        "max_reference_images": int(capability.get("max_reference_images", 0) or 0),
        "reference_modes": list(capability.get("reference_modes") or []),
        "return_last_frame": bool(
            policy.get("provider") == "seedance"
            and continuity_reference_added
            and capability.get("supports_last_frame", False)
        ),
        "reference_labels": labels,
        "policy": {key: value for key, value in policy.items() if key != "capability"},
        "visual_style_id": style_snapshot["id"],
        "style_modifiers": style_snapshot["modifiers"],
        "style_snapshot": style_snapshot,
        "native_audio_requested": native_audio_requested,
        "audio_mode_requested": audio_mode_requested,
        "audio_mode_effective": audio_mode_effective,
        "audio_capability_status": str(capability.get("native_audio_status") or "unsupported"),
        "audio_capability_reason": str(capability.get("native_audio_reason") or ""),
        "audio_policy": "native_dialogue_post_mix_bgm" if native_audio_requested else "external_tts_post_mix_bgm",
        "dialogue_plan": dialogue_plan,
    }
    snapshot["prompt_snapshot_digest"] = generation_snapshot_digest(snapshot)
    return snapshot


def _migrate_legacy_shot_references(state: DramaState) -> None:
    """Classify path-only legacy references without discarding ambiguous media."""
    catalog = _reference_catalog(state)["by_id"]
    by_identity = {_asset_reference_key(reference): reference for reference in catalog.values()}
    for production in state.shot_productions.values():
        migrated = []
        for reference in production.references:
            if reference.source in {"manual", "explicit"}:
                migrated.append(reference)
                continue
            resolved = catalog.get(reference.reference_id) or by_identity.get(_asset_reference_key(reference))
            if resolved is None:
                migrated.append(reference.model_copy(update={"source": "manual"}))
            else:
                migrated.append(resolved.model_copy(deep=True))
        production.references = migrated


def _ensure_shot_production(state: DramaState, scene_id: int):
    """Initialize new production state without changing legacy video assets."""
    from src.schemas.assets import ShotProductionState, ShotVideoVersion

    production = state.shot_productions.get(scene_id)
    if production is None:
        production = ShotProductionState(
            scene_id=scene_id,
            references=_default_shot_references(state, scene_id),
        )
        scene = _script_scene(state, scene_id)
        production.continuity_enabled = bool(getattr(scene, "continuity_from_prev", False))
        production.continuity_reason = str(getattr(scene, "continuity_reason", "") or "")
        production.continuity_source_scene_id = scene_id - 1 if production.continuity_enabled else 0
        production.transition_type = str(getattr(scene, "transition_from_prev", "cut") or "cut").lower()
        legacy_assets = state.generated_assets.shots if state.generated_assets else []
        for index, asset in enumerate(item for item in legacy_assets if item.scene_id == scene_id and item.success):
            version = ShotVideoVersion(
                **asset.model_dump(),
                version_id=f"legacy-{scene_id}-{index}",
                created_at="",
            )
            production.video_versions.append(version)
        if production.video_versions:
            production.active_version_id = production.video_versions[-1].version_id
            production.generation_status = "success"
        state.shot_productions[scene_id] = production
    if production.generation_status == "generating" and (state.project_id, scene_id) not in video_jobs:
        active_jobs = job_store.list_jobs(
            state.project_id, states=ACTIVE_JOB_STATES, limit=100,
        ) if state.project_id else []
        active = any(
            item["job_type"] == "video.generate" and item["target_id"] == str(scene_id)
            for item in active_jobs
        )
        if not active:
            production.generation_status = "failed"
            production.error_message = "服务重启或生成任务中断，请重新生成"
    return production


def _sync_active_shot_asset(state: DramaState, scene_id: int) -> None:
    from src.schemas.assets import GeneratedAssets, ShotAsset

    production = _ensure_shot_production(state, scene_id)
    active = next(
        (item for item in production.video_versions if item.version_id == production.active_version_id),
        None,
    )
    if state.generated_assets is None:
        state.generated_assets = GeneratedAssets(shots=[])
    state.generated_assets.shots = [item for item in state.generated_assets.shots if item.scene_id != scene_id]
    if active:
        state.generated_assets.shots.append(ShotAsset.model_validate(active.model_dump()))
    state.generated_assets.all_success = bool(state.generated_assets.shots) and all(
        item.success for item in state.generated_assets.shots
    )


async def _prepare_auto_references(state: DramaState) -> None:
    """Generate missing references and merge newly required props into each shot."""
    if state.visual_design is None:
        raise HTTPException(409, "项目缺少视觉设计，请先重新解析剧本")

    from src.utils.visual_asset_manifest import representative_scene_id, visual_character_names, visual_prop_ids

    required_characters = set(visual_character_names(state.script))
    if not required_characters:
        required_characters = set(state.visual_design.global_character_descriptions)
    required_scene_ids = {representative_scene_id(state.script, scene.scene_id) for scene in state.script.scenes}
    required_props = set(visual_prop_ids(state.script))
    existing_characters = {
        image.character_name
        for image in (state.character_images.characters if state.character_images else [])
        if image.image_path and Path(image.image_path).is_file()
    }
    existing_scenes = {
        image.scene_id
        for image in (state.scene_images.scenes if state.scene_images else [])
        if image.image_path and Path(image.image_path).is_file()
    }
    existing_props = {
        image.prop_id
        for image in (state.prop_images.props if state.prop_images else [])
        if video_gen_agent._is_valid_video_prop_reference(image)
    }
    need_characters = not required_characters.issubset(existing_characters)
    need_scenes = not required_scene_ids.issubset(existing_scenes)
    need_props = not required_props.issubset(existing_props)

    if need_characters or need_scenes or need_props:
        # Video jobs must not use the old direct image-agent path: it skipped
        # ImageAssetVersions and therefore produced references invisible to the
        # asset editor/history. Reuse the normal versioned asset generation path.
        from src.services.asset_generation import generate_pending_project_assets

        for asset_type, needed in (
            ("character", need_characters),
            ("scene", need_scenes),
            ("prop", need_props),
        ):
            if needed:
                await generate_pending_project_assets(state.project_id, asset_type)

        refreshed = _load_project_state(state.project_id)
        state.character_images = refreshed.character_images
        state.scene_images = refreshed.scene_images
        state.prop_images = refreshed.prop_images

    for scene in state.script.scenes:
        _reconcile_shot_references(state, scene.scene_id)


def _production_payload(state: DramaState) -> dict:
    from src.services.video_capabilities import audio_capability, normalize_video_policy, public_video_capabilities
    from src.services.audio_timeline import audio_summary

    # Legacy projects may not have a persisted project video policy yet.  The
    # generation path already normalizes that empty value, so expose and persist
    # the same effective default here to keep the UI selectors and task payload
    # on one contract.
    normalized_project_policy = normalize_video_policy(state.video_generation_policy)
    state.video_generation_policy = {
        key: value for key, value in normalized_project_policy.items()
        if key != "capability"
    }

    fidelity_report = audit_current_prompt_fidelity(state)
    state.prompt_fidelity = fidelity_report.model_dump()

    productions = []
    provider = video_gen_agent.api_client.provider_name
    for scene in state.script.scenes:
        production = _ensure_shot_production(state, scene.scene_id)
        _reconcile_shot_references(state, scene.scene_id)
        prompt = _shot_prompt(state, scene.scene_id)
        video_policy = normalize_video_policy(state.video_generation_policy, production.video_override)
        display_duration = (
            int(round(float(prompt.duration_sec)))
            if video_policy.get("provider") == "seedance"
            else _clamp_production_duration(prompt.duration_sec)
        )
        display_prompt = prompt.model_copy(update={
            "duration_sec": display_duration,
            "prompt": _inline_automatic_asset_mentions(prompt.prompt, production.references),
        })
        effective = compose_video_prompt(
            _policy_prompt_provider(video_policy),
            state.generation_prompts,
            display_prompt,
            state.prompt_policy,
            creative_profile=state.creative_profile,
        )
        policy_public = {key: value for key, value in video_policy.items() if key != "capability"}
        estimated_cost_rmb = round(float(policy_public["unit_price_rmb_per_sec"]) * float(display_prompt.duration_sec), 4)
        productions.append({
            "scene": scene.model_dump(),
            "prompt": display_prompt.model_dump(),
            "effective_prompt": effective,
            "production": production.model_dump(),
            "video_policy": policy_public,
            "video_capability": video_policy["capability"],
            "estimated_cost_rmb": estimated_cost_rmb,
        })
    catalog = _reference_catalog(state)
    return {
        "project_id": state.project_id,
        "productions": productions,
        "asset_catalog": {
            "characters": catalog["characters"],
            "scenes": catalog["scenes"],
            "props": catalog["props"],
        },
        "audio": state.audio_assets.model_dump() if state.audio_assets else None,
        "audio_summary": audio_summary(state),
        "audio_mode": getattr(state.creative_profile, "audio_mode", "auto"),
        "audio_capability": audio_capability(normalized_project_policy),
        "prompt_policy": state.prompt_policy.model_dump(),
        "image_generation_policy": state.image_generation_policy,
        "video_generation_policy": state.video_generation_policy,
        "video_capabilities": public_video_capabilities(),
        "prompt_quality": {
            "template_version": state.generation_prompts.prompt_template_version,
            "language": state.generation_prompts.prompt_language,
            "needs_refresh": state.generation_prompts.needs_refresh,
        },
        "prompt_review": state.prompt_review,
        "prompt_fidelity": {
            **state.prompt_fidelity,
            "status": "failed" if state.prompt_fidelity.get("status") == "blocked" else state.prompt_fidelity.get("status", "clean"),
        },
        "shot_capacity": {
            "items": [item.model_dump() for item in (state.generation_prompts.capacity_diagnostics or [])],
        },
        "character_profile_repair": {
            "status": getattr(state.visual_design, "profile_repair_status", None) or "not_available",
            "errors": list(getattr(state.visual_design, "profile_repair_errors", []) or []),
        },
        "provider_capabilities": provider_capabilities(provider),
        "subtitle_enabled": state.subtitle_enabled,
        "subtitle_mode": state.subtitle_mode,
        "output_video_path": state.output_video_path,
        "missing_media_paths": _missing_media_paths(state),
    }


async def _run_shot_video_job(project_id: str, scene_id: int, version_id: str) -> None:
    """Background task that persists one generated video version."""
    try:
        state = _load_project_state(project_id)
        production = _ensure_shot_production(state, scene_id)
        prompt = _shot_prompt(state, scene_id).model_copy(deep=True)
        valid_references = [
            item
            for item in _deduplicate_shot_references(production.references)
            if Path(item.image_path).is_file()
        ]
        prompt.prompt = _inline_automatic_asset_mentions(prompt.prompt, valid_references)
        reference_paths = [item.image_path for item in valid_references]
        reference_labels = [
            _reference_label_for_item(item.model_dump(), index)
            for index, item in enumerate(valid_references, start=1)
        ]
        asset = await video_gen_agent.generate_shot(
            prompt,
            reference_paths,
            reference_labels=reference_labels,
            visual_design=state.visual_design,
            scene_vision_descriptions=state.scene_vision_descriptions,
            script=state.script,
            project_id=project_id,
            version_id=version_id,
            generation_prompts=state.generation_prompts,
            prompt_policy=state.prompt_policy,
            creative_profile=state.creative_profile,
        )

        latest = _load_project_state(project_id)
        latest_production = _ensure_shot_production(latest, scene_id)
        now = datetime.now().isoformat(timespec="seconds")
        latest_production.completed_at = now
        if asset.success:
            from src.schemas.assets import ShotVideoVersion

            version = ShotVideoVersion(
                **asset.model_dump(), version_id=version_id, created_at=now,
            )
            try:
                quality_report = inspect_video(asset.local_path, expected_duration=prompt.duration_sec)
                visual_review = await review_video_subjective(
                    asset.local_path,
                    (latest_production.last_generation_snapshot or {}).get("provider_prompt") or prompt.prompt,
                )
                quality_report["visual_review"] = visual_review
                if visual_review.get("status") == "warning":
                    quality_report["status"] = "warning"
                    quality_report["warnings"] = visual_review.get("issues", [])
                version.quality_report = quality_report
            except Exception as review_exc:
                version.quality_report = {
                    "status": "skipped",
                    "warnings": [f"视频审核未完成：{str(review_exc)[:180]}"],
                }
            latest_production.video_versions.append(version)
            latest_production.active_version_id = version_id
            latest_production.generation_status = "success"
            latest_production.error_message = ""
            _sync_active_shot_asset(latest, scene_id)
        else:
            latest_production.generation_status = "failed"
            latest_production.error_message = asset.error_message or "视频生成失败"
        _save_project_state(latest)
    except Exception as exc:
        try:
            latest = _load_project_state(project_id)
            production = _ensure_shot_production(latest, scene_id)
            production.generation_status = "failed"
            production.error_message = str(exc)
            production.completed_at = datetime.now().isoformat(timespec="seconds")
            _save_project_state(latest)
        except Exception:
            pass
    finally:
        video_jobs.discard((project_id, scene_id))


def _resolve_image_asset(
    project_id: str,
    request: ImageAssetRequest,
    create_missing: bool = False,
):
    """Load one editable image from the saved project state and validate its key."""
    meta = project_store.get_project(project_id)
    if not meta:
        raise HTTPException(404, "Project not found")
    state_json = (meta.get("assets") or {}).get("state_json") or ""
    state = _state_from_json(state_json)
    if request.asset_type == "character":
        if request.view_type not in {
            "front_half", "front_full", "left_profile", "back_full",
            "portrait", "profile", "full_body",
        }:
            raise HTTPException(400, "Invalid character view")
        records = state.character_images.characters if state.character_images else []
        asset = next(
            (item for item in records if item.character_name == request.asset_id and item.view_type == request.view_type),
            None,
        )
        if not asset:
            requested_identity = _character_identity(request.asset_id)
            asset = next(
                (
                    item for item in records
                    if _character_identity(item.character_name) == requested_identity
                    and item.view_type == request.view_type
                ),
                None,
            )
    elif request.asset_type == "scene":
        if request.view_type not in {"wide", "medium", "scene_center", "scene_left_45", "scene_right_45", "scene_reverse"}:
            raise HTTPException(400, "Invalid scene view")
        location_asset = next(
            (
                item for item in (state.script.asset_manifest.locations if state.script and state.script.asset_manifest else [])
                if item.location_id == request.asset_id
            ),
            None,
        )
        if location_asset:
            scene_id = location_asset.representative_scene_id
            location_id = location_asset.location_id
        else:
            try:
                scene_id = int(request.asset_id)
            except ValueError as exc:
                raise HTTPException(400, "Invalid scene asset ID") from exc
            location_id = ""
        records = state.scene_images.scenes if state.scene_images else []
        asset = next(
            (
                item for item in records
                if item.view_type == request.view_type
                and (not request.state_id or item.state_id == request.state_id)
                and ((location_id and item.location_id == location_id) or (not location_id and item.scene_id == scene_id))
            ),
            None,
        )
    elif request.asset_type == "prop":
        if request.view_type not in {"standard_views", "front", "back", "left", "right", "top", "bottom"}:
            raise HTTPException(400, "Invalid prop view")
        records = state.prop_images.props if state.prop_images else []
        asset = next(
            (item for item in records if item.prop_id == request.asset_id and item.view_type == request.view_type),
            None,
        )
    else:
        raise HTTPException(400, "Invalid image asset type")
    if not asset:
        if not create_missing:
            raise HTTPException(404, "Image asset not found")
        asset = _create_draft_image_asset(state, request)

    versions = ImageAssetVersions(project_store.get_project_dir(project_id))
    active_path = versions.active_path(request.asset_type, request.asset_id, request.view_type)
    try:
        active_path.resolve().relative_to(versions.asset_root.resolve())
    except ValueError as exc:
        raise HTTPException(400, "Unsafe image asset path") from exc
    return state, asset, versions, active_path


def _create_draft_image_asset(state: DramaState, request: ImageAssetRequest):
    """Create an editable asset record when a card has no generated image yet."""
    from src.services.asset_seed import derive_asset_seed

    if request.asset_type == "character":
        from src.schemas.character import CharacterImage, CharacterImageAssets

        from src.agents.image_gen_agent import CHARACTER_PROMPT_TEMPLATE_VERSION, character_view_spec
        from src.services.asset_prompt_composer import compose_asset_prompt

        _, width, height, _, _ = character_view_spec(request.view_type)
        asset = CharacterImage(
            character_name=request.asset_id,
            view_type=request.view_type,
            image_prompt="",
            image_path="",
            seed=derive_asset_seed(state.project_id, "character", request.asset_id, request.view_type, CHARACTER_PROMPT_TEMPLATE_VERSION),
            width=width,
            height=height,
            prompt_template_version=CHARACTER_PROMPT_TEMPLATE_VERSION,
        )
        # Draft cards must use the same canonical composer as the generation
        # service.  Reading only the legacy ``art_style`` field can contradict
        # a project's stable ``visual_style_id`` after a style migration.
        asset.image_prompt = compose_asset_prompt(
            state, "character", request.asset_id, request.view_type, asset,
        )["effective_prompt"]
        if state.character_images is None:
            state.character_images = CharacterImageAssets()
        state.character_images.characters.append(asset)
        return asset

    if request.asset_type == "prop":
        from src.agents.image_gen_agent import PROP_IMAGE_HEIGHT, PROP_IMAGE_WIDTH
        from src.services.asset_prompt_composer import PROP_PROMPT_TEMPLATE_VERSION, compose_asset_prompt
        from src.schemas.prop import PropImage, PropImageAssets

        prop = next(
            (item for item in (state.script.asset_manifest.props if state.script and state.script.asset_manifest else []) if item.prop_id == request.asset_id),
            None,
        )
        if prop is None:
            raise HTTPException(404, "Prop asset not found in the visual manifest")
        width, height = (PROP_IMAGE_WIDTH, PROP_IMAGE_HEIGHT) if request.view_type == "standard_views" else (1024, 1024)
        asset = PropImage(
            prop_id=prop.prop_id,
            prop_name=prop.name,
            view_type=request.view_type,
            image_prompt="",
            image_path="",
            seed=derive_asset_seed(state.project_id, "prop", prop.prop_id, request.view_type, 2),
            width=width,
            height=height,
            prompt_template_version=PROP_PROMPT_TEMPLATE_VERSION,
        )
        # Draft cards must show the same style-locked prompt that the
        # generation service will submit. The old compatibility builders
        # omitted the project's anime/live-action constraint here.
        asset.image_prompt = compose_asset_prompt(
            state, "prop", prop.prop_id, request.view_type, asset,
        )["effective_prompt"]
        if state.prop_images is None:
            state.prop_images = PropImageAssets()
        state.prop_images.props.append(asset)
        return asset

    from src.schemas.scene_image import SceneImage, SceneImageAssets

    from src.utils.visual_asset_manifest import scene_source_signature, stable_location_id, stable_scene_state_id

    location_asset = next(
        (
            item for item in (state.script.asset_manifest.locations if state.script and state.script.asset_manifest else [])
            if item.location_id == request.asset_id
        ),
        None,
    )
    if location_asset:
        scene_id = location_asset.representative_scene_id
    else:
        scene_id = int(request.asset_id)
    scene = next((item for item in (state.script.scenes if state.script else []) if item.scene_id == scene_id), None)
    location = scene.location if scene else f"场景 {scene_id}"
    if location_asset:
        location = location_asset.canonical_name
    elif state.script and state.script.asset_manifest:
        from src.utils.visual_asset_manifest import location_for_scene

        location_asset = location_for_scene(state.script, scene_id)
        if location_asset:
            location = location_asset.canonical_name
    state_id = request.state_id.strip()
    if location_asset and not state_id:
        state_id = location_asset.default_state_id or (
            location_asset.states[0].state_id if getattr(location_asset, "states", None) else ""
        )
    if state.script and state.script.asset_manifest and not state_id:
        from src.utils.visual_asset_manifest import state_for_scene

        state_asset = state_for_scene(state.script, scene_id)
        if state_asset:
            state_id = state_asset.state_id
    if not state_id and location_asset:
        state_id = stable_scene_state_id(location_asset.location_id, location_asset.time_condition, "", "", "")
    from src.services.asset_prompt_composer import (
        SCENE_PROMPT_TEMPLATE_VERSION,
        build_scene_prompt_layers,
    )

    if request.view_type.startswith("scene_"):
        layer = build_scene_prompt_layers(state, request.asset_id, state_id, request.view_type)
        prompt = layer["effective_prompt"]
        view_count, width, height = 1, layer["width"], layer["height"]
    else:
        layer = build_scene_prompt_layers(state, request.asset_id, state_id, "scene_center")
        prompt = "\n".join((
            "高质量影视场景空镜（按当前项目风格与场景状态重建）",
            layer["effective_prompt"],
            f"兼容景别：{'全景建立镜头' if request.view_type == 'wide' else '中景细节镜头'}。",
        ))
        view_count, width, height = 0, layer["width"], layer["height"]
    asset = SceneImage(
        scene_id=scene_id,
        location_id=location_asset.location_id if location_asset else stable_location_id(location),
        state_id=state_id,
        location=location,
        view_type=request.view_type,
        image_prompt=prompt,
        base_prompt=prompt,
        image_path="",
        seed=derive_asset_seed(state.project_id, "scene", location_asset.location_id if location_asset else stable_location_id(location), request.view_type, 4 if request.view_type.startswith("scene_") else 1),
        width=width,
        height=height,
        is_master=request.view_type == "scene_center",
        needs_confirmation=request.view_type == "scene_center",
        master_version_id="",
        reference_version_ids=[],
        derived_from_version_id="",
        requirement_signature="",
        group_view_count=view_count,
        prompt_template_version=SCENE_PROMPT_TEMPLATE_VERSION,
        source_signature=scene_source_signature(
            location,
            getattr(location_asset, "environment_description", "") if location_asset else "",
        ),
    )
    if state.scene_images is None:
        state.scene_images = SceneImageAssets()
    state.scene_images.scenes.append(asset)
    return asset


def _character_identity(name: str) -> str:
    chinese = "".join(re.findall(r"[\u3400-\u9fff]+", name))
    return chinese or re.sub(r"\s*\([^)]*\)\s*", "", name).strip().lower()


def _character_description(state: DramaState, name: str) -> str:
    """Return the richest stable appearance description available for a role.

    Projects keep the same description in several denormalized places: the
    visual-stage registry, the asset manifest and (for older projects) a
    legacy English registry.  Previously the first matching registry value was
    returned immediately.  A short visual-stage value could therefore hide a
    more detailed manifest description, leaving the image prompt dominated by
    generic asset constraints.  Merge matching values conservatively and keep
    the manifest as a fallback source without inventing facts.
    """
    visual = state.visual_design
    identity = _character_identity(name)

    def matches(candidate_name: Any) -> bool:
        candidate = str(candidate_name or "").strip()
        return bool(candidate) and (candidate == name or _character_identity(candidate) == identity)

    def body(value: Any, candidate_name: str = "") -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        # Descriptions repaired from manifests are often stored as
        # ``角色名：事实``.  The prompt already supplies the role name, so
        # remove only that leading label and preserve all other wording.
        prefixes = [str(candidate_name or "").strip(), str(name or "").strip()]
        for prefix in sorted({item for item in prefixes if item}, key=len, reverse=True):
            text = re.sub(rf"^{re.escape(prefix)}\s*[：:]\s*", "", text)
        return text.strip("；;。 ")

    def generic(value: str) -> bool:
        try:
            from src.utils.visual_asset_manifest import _is_generic_character_description

            return _is_generic_character_description(value, name)
        except Exception:
            return False

    chinese_values: list[str] = []
    legacy_values: list[str] = []
    if visual:
        for field_name, target in (
            ("global_character_descriptions_cn", chinese_values),
            ("global_character_descriptions", legacy_values),
        ):
            registry = getattr(visual, field_name, {}) or {}
            if not isinstance(registry, dict):
                continue
            for candidate_name, value in registry.items():
                if matches(candidate_name):
                    cleaned = body(value, str(candidate_name))
                    if cleaned and cleaned not in target:
                        target.append(cleaned)

    manifest = getattr(getattr(state, "script", None), "asset_manifest", None)
    matched = None
    if manifest:
        for character in getattr(manifest, "characters", []) or []:
            candidate_names = [
                getattr(character, "name", ""),
                getattr(character, "asset_id", ""),
                *(getattr(character, "aliases", []) or []),
            ]
            if name in candidate_names or identity in {_character_identity(item) for item in candidate_names}:
                matched = character
                break

    manifest_values: list[str] = []
    if matched is not None:
        description = body(getattr(matched, "description", ""), getattr(matched, "name", ""))
        if description and description not in manifest_values:
            manifest_values.append(description)
        costume_states = getattr(matched, "costume_states", {}) or {}
        if isinstance(costume_states, dict):
            for state_name, state_desc in costume_states.items():
                detail = body(f"{state_name}：{state_desc}")
                if detail and detail not in manifest_values:
                    manifest_values.append(detail)

    # Chinese is the preferred display/provider language.  When it is absent,
    # retain the legacy registry value; otherwise add only manifest details
    # that are not already covered by the preferred text.  Generic placeholders
    # never take precedence over a concrete manifest description.
    preferred_values = chinese_values or legacy_values
    preferred = preferred_values[0] if preferred_values else ""
    if preferred and generic(preferred) and manifest_values:
        preferred = ""
    merged: list[str] = []
    for value in ([preferred] if preferred else []) + manifest_values + preferred_values[1:]:
        if not value or generic(value):
            continue
        if value in merged or any(value in existing or existing in value for existing in merged):
            continue
        merged.append(value)
    if merged:
        return "；".join(merged).strip("；;。 ") + "。"

    parts: list[str] = []
    names_for_scene_match = {name, identity}
    if matched is not None:
        canonical = getattr(matched, "name", "") or name
        parts.append(f"{canonical}。")
        description = str(getattr(matched, "description", "") or "").strip()
        if description:
            parts.append(description)
        costume_states = getattr(matched, "costume_states", {}) or {}
        if costume_states:
            states = "；".join(
                f"{state_name}：{state_desc}"
                for state_name, state_desc in costume_states.items()
                if str(state_desc).strip()
            )
            if states:
                parts.append(f"服装/状态：{states}")
        names_for_scene_match.update(
            item for item in [
                getattr(matched, "name", ""),
                getattr(matched, "asset_id", ""),
                *(getattr(matched, "aliases", []) or []),
            ]
            if item
        )
        names_for_scene_match.update(_character_identity(item) for item in list(names_for_scene_match))

    scene_clues: list[str] = []
    explicit_names = {
        item for item in names_for_scene_match
        if item and (_character_identity(item) == item or any("\u3400" <= char <= "\u9fff" for char in item))
    }
    for scene in (getattr(getattr(state, "script", None), "scenes", []) or []):
        scene_names = set(getattr(scene, "characters", []) or [])
        # Normalize legacy names without mutating the set while its iterator
        # is active. Older projects may store names such as ``顾衡 (Gu Heng)``.
        scene_names.update(_character_identity(item) for item in list(scene_names))
        action = str(getattr(scene, "action_description", "") or "").strip()
        if not action:
            continue
        if scene_names.intersection(names_for_scene_match) or any(alias and alias in action for alias in explicit_names):
            sentences = [
                item.strip()
                for item in re.split(r"(?<=[。！？；])", action.replace("\n", " "))
                if item.strip()
            ]
            for sentence in sentences:
                if any(alias and alias in sentence for alias in explicit_names):
                    if sentence not in scene_clues:
                        scene_clues.append(sentence[:140])
                if len(scene_clues) >= 4:
                    break
        if len(scene_clues) >= 3:
            break
    if scene_clues:
        parts.append("剧本可见外貌线索：" + " / ".join(scene_clues))

    if parts:
        return "；".join(part.strip("；。 ") for part in parts if part.strip()) + "。"
    return f"{name}，角色外貌以剧本设定为准。"


def _save_image_asset_state(
    project_id: str,
    state: DramaState,
    asset,
    active_path: Path,
    prompt: str,
    result=None,
    custom_instructions: str | None = None,
    prompt_template_version: int | None = None,
    version_id: str = "",
    quality_report: dict | None = None,
) -> str:
    if hasattr(asset, "base_prompt") and not asset.base_prompt:
        # Legacy scene records stored only the effective prompt. Preserve that
        # original text before writing a new effective prompt with supplements.
        asset.base_prompt = asset.image_prompt
    asset.image_path = str(active_path)
    asset.image_prompt = prompt
    if version_id and hasattr(asset, "version_id"):
        asset.version_id = version_id
    if hasattr(asset, "source_signature"):
        from src.utils.visual_asset_manifest import scene_source_signature

        description = prompt
        if state.script and state.script.asset_manifest:
            location = next(
                (item for item in state.script.asset_manifest.locations if item.location_id == getattr(asset, "location_id", "")),
                None,
            )
            if location:
                description = location.environment_description
        asset.source_signature = scene_source_signature(getattr(asset, "location", ""), description)
        asset.is_stale = False
    if custom_instructions is not None and hasattr(asset, "custom_instructions"):
        asset.custom_instructions = custom_instructions
    if prompt_template_version is not None and hasattr(asset, "prompt_template_version"):
        asset.prompt_template_version = prompt_template_version
    if quality_report is not None and hasattr(asset, "quality_report"):
        asset.quality_report = quality_report
    if result is not None:
        asset.seed = result.seed
        asset.width = result.width
        asset.height = result.height
        asset.api_provider = getattr(result, "provider_name", "") or image_gen_agent.api_client.provider_name
        asset.cost = result.api_cost
    state_json = _state_to_json(state)
    project_store.set_state_json(project_id, state_json)
    return state_json


def _extract_script_text(filename: str, content: bytes) -> str:
    """Extract plain text from the supported screenplay source formats."""
    suffix = Path(filename).suffix.lower()
    if suffix in {".txt", ".md"}:
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        raise ValueError("Text file encoding is not supported")
    if suffix == ".docx":
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                document = ET.fromstring(archive.read("word/document.xml"))
        except (KeyError, zipfile.BadZipFile, ET.ParseError) as exc:
            raise ValueError("Invalid DOCX file") from exc
        namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paragraphs = []
        for paragraph in document.iter(f"{namespace}p"):
            text = "".join(node.text or "" for node in paragraph.iter(f"{namespace}t"))
            if text.strip():
                paragraphs.append(text)
        return "\n".join(paragraphs)
    raise ValueError("Only TXT, DOCX, and MD files are supported")


# ── API 端点 ─────────────────────────────────────────────────

class MutableProxy:
    """Stable reference whose target can be replaced after a config reload."""

    def __init__(self, target):
        object.__setattr__(self, "_target", target)

    def set(self, target):
        object.__setattr__(self, "_target", target)
        return target

    @property
    def target(self):
        return object.__getattribute__(self, "_target")

    def __getattr__(self, name):
        return getattr(self.target, name)

    def __setattr__(self, name, value):
        if name == "_target":
            object.__setattr__(self, name, value)
        else:
            setattr(self.target, name, value)


script_agent = MutableProxy(script_agent)
visual_prompt_agent = MutableProxy(visual_prompt_agent)
image_gen_agent = MutableProxy(image_gen_agent)
video_gen_agent = MutableProxy(video_gen_agent)
review_agent = MutableProxy(review_agent)
audio_agent = MutableProxy(audio_agent)
assembly_agent = MutableProxy(assembly_agent)
orchestrator = MutableProxy(orchestrator)
project_store = MutableProxy(project_store)
job_store = MutableProxy(job_store)
job_manager = MutableProxy(job_manager)


@dataclass(frozen=True)
class AppContext:
    """Explicit ownership boundary shared by all API domain routers."""

    script_agent: MutableProxy
    visual_prompt_agent: MutableProxy
    image_gen_agent: MutableProxy
    video_gen_agent: MutableProxy
    review_agent: MutableProxy
    audio_agent: MutableProxy
    assembly_agent: MutableProxy
    orchestrator: MutableProxy
    project_store: MutableProxy
    job_store: MutableProxy
    job_manager: MutableProxy
    video_jobs: set


app_context = AppContext(
    script_agent=script_agent,
    visual_prompt_agent=visual_prompt_agent,
    image_gen_agent=image_gen_agent,
    video_gen_agent=video_gen_agent,
    review_agent=review_agent,
    audio_agent=audio_agent,
    assembly_agent=assembly_agent,
    orchestrator=orchestrator,
    project_store=project_store,
    job_store=job_store,
    job_manager=job_manager,
    video_jobs=video_jobs,
)

__all__ = [name for name in globals() if not name.startswith("__")]
