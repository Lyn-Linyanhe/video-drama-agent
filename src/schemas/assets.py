"""Schema for generated video assets — output of Video Generation Agent."""

from pydantic import BaseModel, Field
from typing import Optional


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


class GeneratedAssets(BaseModel):
    """Collection of generated video assets."""
    shots: list[ShotAsset] = Field(description="Generated video per scene")
    total_cost: float = Field(default=0.0, description="Total API cost in USD")
    generation_time_sec: float = Field(default=0.0, description="Total generation time")
    all_success: bool = Field(
        default=False, description="Whether all shots generated successfully"
    )
