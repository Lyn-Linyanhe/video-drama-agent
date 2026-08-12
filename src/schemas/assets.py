"""Schema for generated video assets — output of Video Generation Agent."""

from pydantic import BaseModel, Field
from typing import Literal, Optional


class ShotAsset(BaseModel):
    """A single generated video shot."""
    scene_id: int = Field(description="Matches ScriptBreakdown.scenes[].scene_id")
    video_url: str = Field(default="", description="URL of the generated video")
    local_path: Optional[str] = Field(default=None, description="Downloaded local file path")
    duration_sec: float = Field(default=0.0, description="Actual video duration")
    candidate_index: int = Field(default=0, description="Which candidate (0-based)")
    api_used: str = Field(default="mock", description="Which API generated this")
    success: bool = Field(default=False, description="Whether generation succeeded")
    error_message: Optional[str] = Field(default=None, description="Error message if failed")
    last_frame_url: str = Field(default="", description="Provider-returned final frame URL")
    last_frame_path: Optional[str] = Field(default=None, description="Downloaded provider final frame")
    audio_source: str = Field(default="", description="native, tts, none, or unknown")
    native_audio_enabled: bool = Field(default=False)
    native_audio_path: Optional[str] = Field(default=None)
    audio_present: bool = Field(default=False)


class GeneratedAssets(BaseModel):
    """Collection of generated video assets."""
    shots: list[ShotAsset] = Field(description="Generated video per scene")
    total_cost: float = Field(default=0.0, description="Total API cost in USD")
    generation_time_sec: float = Field(default=0.0, description="Total generation time")
    all_success: bool = Field(
        default=False, description="Whether all shots generated successfully"
    )


class ShotReference(BaseModel):
    """A persisted image reference chosen for one production shot."""

    reference_id: str = Field(description="Stable reference id")
    asset_type: str = Field(description="character, scene, or prop")
    asset_id: str = Field(description="Character name, representative scene id, or prop id")
    state_id: str = Field(default="", description="Scene state id for scene references")
    view_type: str = Field(description="Character, scene, or prop view type")
    image_path: str = Field(description="Local image path")
    label: str = Field(default="", description="Display label")
    reference_tag: str = Field(default="", description="Prompt tag such as @ref01_character_front")
    source: Literal["asset", "manual"] = Field(
        default="asset",
        description="Whether this reference follows an asset version or was manually selected",
    )
    asset_version_id: str = Field(default="", description="Active source image version for asset references")
    stale: bool = Field(default=False, description="Asset reference cannot resolve to a current usable file")


class ShotVideoVersion(ShotAsset):
    """A retained result from one generation of a shot."""

    version_id: str = Field(description="Stable version id")
    created_at: str = Field(description="Local generation timestamp")
    provider: str = Field(default="")
    model: str = Field(default="")
    resolution: str = Field(default="")
    capability_snapshot: dict = Field(default_factory=dict)
    unit_price_rmb_per_sec: float = Field(default=0.0, ge=0.0)
    estimated_cost_rmb: float = Field(default=0.0, ge=0.0)
    tail_frame_path: str = Field(default="")
    provider_last_frame_url: str = Field(default="")
    provider_last_frame_path: str = Field(default="")
    continuity_source_scene_id: int = Field(default=0)
    continuity_source_version_id: str = Field(default="")
    generation_snapshot: dict = Field(default_factory=dict)
    quality_report: dict = Field(default_factory=dict)


class ShotProductionState(BaseModel):
    """Editable, persistent production state for one storyboard shot."""

    scene_id: int = Field(description="Matches ScriptBreakdown scene_id")
    references: list[ShotReference] = Field(default_factory=list)
    video_versions: list[ShotVideoVersion] = Field(default_factory=list)
    active_version_id: str = Field(default="")
    generation_status: str = Field(default="idle", description="idle, generating, success, failed")
    error_message: str = Field(default="")
    started_at: str = Field(default="")
    completed_at: str = Field(default="")
    video_override: dict = Field(default_factory=dict)
    continuity_enabled: bool = Field(default=False)
    continuity_manual: bool = Field(default=False)
    continuity_reason: str = Field(default="")
    continuity_source_scene_id: int = Field(default=0)
    continuity_source_version_id: str = Field(default="")
    continuity_tail_frame_path: str = Field(default="")
    continuity_stale: bool = Field(default=False)
    transition_type: str = Field(default="")
    transition_duration_sec: float = Field(default=0.25, ge=0.0, le=0.8)
    transition_manual: bool = Field(default=False)
    last_generation_snapshot: dict = Field(default_factory=dict)
