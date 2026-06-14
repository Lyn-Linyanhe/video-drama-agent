"""Schema for generation prompts — output of Prompt Engineering Agent."""

from pydantic import BaseModel, Field
from typing import Optional


class ShotPrompt(BaseModel):
    """Optimized prompt for a single video generation shot."""
    scene_id: int = Field(description="Matches ScriptBreakdown.scenes[].scene_id")
    prompt: str = Field(description="Optimized positive prompt for the video API")
    negative_prompt: str = Field(
        default="blurry, distorted, low quality, watermark, text, logo, "
                "deformed face, extra fingers, ugly, bad anatomy"
    )
    seed: Optional[int] = Field(
        default=None,
        description="Random seed for reproducibility. Fixed per-character for consistency."
    )
    motion_strength: float = Field(
        default=0.7, ge=0.0, le=1.0, description="Motion strength"
    )
    duration_sec: int = Field(default=5, ge=1, le=60, description="Target duration")
    batch_size: int = Field(default=2, ge=1, le=4, description="Number of candidates to generate")
    sub_shots: list["ShotPrompt"] = Field(
        default_factory=list,
        description="If scene is longer than API max duration, split into sub-shots"
    )


class GenerationPrompts(BaseModel):
    """Collection of generation prompts for the entire drama."""
    api_provider: str = Field(description="Target API: kling, runway, pika, mock")
    global_negative_prompt: str = Field(
        default="blurry, distorted, low quality, watermark, text, logo, ugly, bad anatomy"
    )
    global_seed_range: tuple[int, int] = Field(
        default=(1, 999999), description="Seed range for consistency management"
    )
    shots: list[ShotPrompt] = Field(description="One prompt per scene/shot")
