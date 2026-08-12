"""Schema for generation prompts — output of Prompt Engineering Agent."""

from pydantic import BaseModel, Field
from typing import Optional


class PromptPolicy(BaseModel):
    """Project-owned rules applied to every video prompt before submission."""

    style_prefix: str = Field(default="", max_length=4000)
    global_negative_prompt: str = Field(default="", max_length=4000)


class ShotPrompt(BaseModel):
    """Chinese prompt for a single video generation shot."""
    scene_id: int = Field(description="Matches ScriptBreakdown.scenes[].scene_id")
    prompt: str = Field(description="完整中文视频生成提示词")
    negative_prompt: str = Field(
        default="低清晰度，模糊，水印，文字，标志，畸形五官，"
                "多余手指，错误肢体，动作抖动，道具变形"
    )
    seed: Optional[int] = Field(
        default=None,
        description="Random seed for reproducibility. Fixed per-character for consistency."
    )
    motion_strength: float = Field(
        default=0.7, ge=0.0, le=1.0, description="Motion strength"
    )
    duration_sec: int = Field(default=5, ge=3, le=60, description="Target duration")
    batch_size: int = Field(default=2, ge=1, le=4, description="Number of candidates to generate")
    sub_shots: list["ShotPrompt"] = Field(
        default_factory=list,
        description="If scene is longer than API max duration, split into sub-shots"
    )


class GenerationPrompts(BaseModel):
    """Collection of generation prompts for the entire drama."""
    api_provider: str = Field(description="Target API: kling, runway, pika, mock")
    global_negative_prompt: str = Field(
        default="低清晰度，模糊，水印，文字，标志，畸形五官，错误肢体，动作抖动，道具变形"
    )
    global_seed_range: tuple[int, int] = Field(
        default=(1, 999999), description="Seed range for consistency management"
    )
    prompt_template_version: int = Field(default=5, ge=1)
    prompt_language: str = Field(default="zh-CN")
    needs_refresh: bool = Field(default=False, description="旧项目提示词未满足当前中文模板时为真")
    shots: list[ShotPrompt] = Field(description="One prompt per scene/shot")
