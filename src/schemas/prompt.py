"""Schema for generation prompts — output of Prompt Engineering Agent."""

from pydantic import BaseModel, Field
from typing import Literal, Optional

from pydantic import model_validator

from src.schemas.shot_plan import CapacityStatus, ShotPlan


class PromptPolicy(BaseModel):
    """Project-owned rules applied to every video prompt before submission."""

    style_prefix: str = Field(default="", max_length=4000)
    global_negative_prompt: str = Field(default="", max_length=4000)


class ShotPrompt(BaseModel):
    """Chinese prompt for a single video generation shot."""
    scene_id: int = Field(description="Matches ScriptBreakdown.scenes[].scene_id")
    shot_id: str | None = Field(default=None, description="Stable executable shot identity")
    parent_scene_id: int | None = Field(default=None, ge=1, description="Parent scene for split shots")
    shot_order: int | None = Field(default=None, ge=1, description="Stable execution order within the parent scene")
    continuity_from_shot_id: str | None = Field(default=None, description="Explicit child shot whose tail state must be carried into this shot")
    beat_ids: list[str] = Field(default_factory=list, description="Narrative beats carried by this shot")
    source_fact_ids: list[str] = Field(default_factory=list, description="Source facts carried by this shot")
    dialogue_ids: list[str] = Field(default_factory=list, description="Dialogue lines carried by this shot")
    prompt: str = Field(description="完整中文视频生成提示词")

    @model_validator(mode="after")
    def validate_parent_scene(self) -> "ShotPrompt":
        if self.parent_scene_id is not None and self.parent_scene_id != self.scene_id:
            raise ValueError("parent_scene_id must match scene_id for a ShotPrompt")
        return self
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


class PromptExecutionContext(BaseModel):
    """Provider-facing execution blocks after script facts are scoped to one shot."""

    scene_id: int
    shot_id: str | None = None
    core_intent: str = ""
    action_timeline: str = ""
    dialogue_block: str = ""
    current_characters: list[str] = Field(default_factory=list)
    current_state: str = ""
    current_props: list[str] = Field(default_factory=list)
    camera_block: str = ""
    environment_block: str = ""
    reference_block: str = ""
    forbidden_block: str = ""
    intentionally_omitted_sections: list[str] = Field(default_factory=list)
    identity_reference_fallback: bool = False



class PromptAssemblyDiagnostic(BaseModel):
    """Deterministic diagnostics emitted while assembling a shot prompt."""

    scene_id: int
    added_sections: list[str] = Field(default_factory=list)
    dropped_facts: list[str] = Field(default_factory=list)
    forbidden_conflicts: list[str] = Field(default_factory=list)
    template_triggers: list[str] = Field(default_factory=list)
    status: Literal["clean", "warning", "failed"] = "clean"


class ShotCapacityDiagnostic(BaseModel):
    """Advisory diagnosis for whether one shot can carry its source facts."""

    scene_id: int
    status: Literal["clean", "warning", "failed"] = "clean"
    action_units: int = 0
    dialogue_chars: int = 0
    prop_state_changes: int = 0
    camera_changes: int = 0
    scene_duration_sec: int = 0
    effective_duration_sec: int = 0
    provider_max_duration_sec: int | None = None
    suggested_shot_count: int = 1
    suggested_duration_sec: int = 0
    # New planning metrics are optional so legacy project payloads remain readable.
    required_total_duration_sec: float = 0.0
    source_duration_sec: float = 0.0
    minimum_duration_sec: float = 0.0
    expected_duration_sec: float = 0.0
    maximum_duration_sec: float = 0.0
    serial_duration_sec: float = 0.0
    overlap_duration_sec: float = 0.0
    metadata_duration_sec: float = 0.0
    timing_basis: str = ""
    recommended_total_duration_sec: float = 0.0
    recommended_durations_sec: list[float] = Field(default_factory=list)
    estimated_dialogue_sec: float = 0.0
    action_required_sec: float = 0.0
    emotional_turn_count: int = 0
    load_score: float = 0.0
    overloaded: bool = False
    planning_status: CapacityStatus = "clean"
    warnings: list[str] = Field(default_factory=list)


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
    assembly_diagnostics: list[PromptAssemblyDiagnostic] = Field(
        default_factory=list,
        description="Deterministic diagnostics from final prompt assembly; optional for legacy projects",
    )
    capacity_diagnostics: list[ShotCapacityDiagnostic] = Field(
        default_factory=list,
        description="Advisory per-shot content capacity diagnostics; optional for legacy projects",
    )
    shot_plans: list[ShotPlan] = Field(
        default_factory=list,
        description="Executable child-shot plans derived before prompt generation; optional for legacy projects",
    )
