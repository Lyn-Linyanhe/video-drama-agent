"""LangGraph state definition — the shared state flowing through the agent pipeline."""

from typing import Optional, Annotated
from dataclasses import dataclass, field
from langgraph.graph.message import add_messages

from src.schemas.script import ScriptBreakdown
from src.schemas.visual import VisualDesign
from src.schemas.prompt import GenerationPrompts, PromptPolicy
from src.schemas.assets import GeneratedAssets, ShotProductionState
from src.schemas.review import ReviewResult
from src.schemas.audio import AudioAssets
from src.schemas.character import CharacterImageAssets
from src.schemas.scene_image import SceneImageAssets
from src.schemas.prop import PropImageAssets
from src.schemas.project import CreativeProfile


@dataclass
class DramaState:
    """Shared state that flows through all agent nodes in the LangGraph pipeline.

    Each agent reads from and writes to specific fields. The orchestrator
    manages the flow between agents based on this state.
    """

    # === Input ===
    user_input: str = ""
    """Raw text input from the user (story, idea, description)."""

    input_mode: str = "idea"
    """Whether user_input is a short idea to expand or an existing screenplay."""

    source_filename: str = ""
    """Optional original screenplay filename imported by the user."""

    script_phase: str = "source_required"
    """Source, draft, confirmation, and structured-script lifecycle state."""

    script_draft_text: str = ""
    """Editable readable screenplay draft before structured analysis."""

    script_draft_instruction: str = ""
    """Last user instruction used to revise the readable draft."""

    draft_job_id: str = ""
    """Active or most recent persistent readable-draft job."""

    # === Agent Outputs (populated by each agent in sequence) ===
    script: Optional[ScriptBreakdown] = None
    """Output of Script Agent — the full script breakdown."""

    visual_design: Optional[VisualDesign] = None
    """Output of Visual Design Agent — visual style and composition."""

    generation_prompts: Optional[GenerationPrompts] = None
    """Output of Prompt Engineering Agent — optimized API prompts."""

    visual_stage: str = "ready"
    """Visual prompt lifecycle: pending, generating, ready, failed, or stale."""

    visual_script_signature: str = ""
    """Digest of the script used by the active or last completed visual task."""

    visual_job_id: str = ""
    """Persistent visual-prompt task currently associated with this project."""

    visual_stage_error: str = ""
    """Latest visual-prompt stage error without marking the script invalid."""

    visual_performance: dict = field(default_factory=dict)
    """Per-stage LLM timing and retry observations for support diagnostics."""

    prompt_review: dict = field(default_factory=dict)
    """Kimi storyboard prompt-review lifecycle and latest report."""

    generated_assets: Optional[GeneratedAssets] = None
    """Output of Video Generation Agent — generated video files."""

    review_result: Optional[ReviewResult] = None
    """Output of Review Agent — quality assessment."""

    audio_assets: Optional[AudioAssets] = None
    """Output of Audio Agent — TTS, BGM, SFX."""

    # === New: Image Assets ===
    character_images: Optional[CharacterImageAssets] = None
    """Output of Character Agent — character portrait images."""

    scene_images: Optional[SceneImageAssets] = None
    """Output of Scene Image Agent — scene environment images."""

    prop_images: Optional[PropImageAssets] = None
    """Output of Image Agent — six-view key prop reference images."""

    # === Vision Analysis Outputs ===
    scene_vision_descriptions: dict = field(default_factory=dict)
    """Vision-extracted scene environment descriptions: {scene_id: english_description}."""

    # === Interactive Editing ===
    project_id: str = ""
    """Unique project identifier for asset directory isolation."""

    creative_profile: CreativeProfile = field(default_factory=CreativeProfile)
    """Project genre, art direction, and final video aspect ratio."""

    prompt_overrides: dict = field(default_factory=dict)
    """User-modified prompts: {shot_index: overridden_prompt_text}."""

    prompt_policy: PromptPolicy = field(default_factory=PromptPolicy)
    """Project-scoped style and negative-prompt policy."""

    image_generation_policy: dict = field(default_factory=dict)
    """Project default image provider, model, and quality preset."""

    scene_view_requirements: dict = field(default_factory=dict)
    """Project-scoped scene view requirements keyed by location_id:state_id."""

    prop_view_requirements: dict = field(default_factory=dict)
    """Project-scoped prop view requirements keyed by prop_id."""

    video_generation_policy: dict = field(default_factory=dict)
    """Project default video provider, model, resolution, and local cost metadata."""

    user_feedback: str = ""
    """User feedback for LLM to revise prompts."""

    shot_productions: dict[int, ShotProductionState] = field(default_factory=dict)
    """Persistent per-shot references, generation status, and video history."""

    # === Control Flow ===
    output_video_path: str = ""
    """Final assembled video file path."""

    subtitle_enabled: bool = True
    """Whether final assembly burns dialogue subtitles into the video."""

    subtitle_mode: str = "dialogue"
    """Final subtitle mode: off, dialogue, or speaker."""

    assembly_audio_override: str = ""
    """One-shot final-assembly override, e.g. explicit no-dialogue confirmation."""

    review_loop_count: int = 0
    """Number of review-retry cycles executed. Capped at MAX_REVIEW_LOOPS."""

    error_message: str = ""
    """Global error message if pipeline fails."""

    # === Metadata ===
    total_cost: float = 0.0
    """Accumulated API cost across all agents."""

    execution_log: list[str] = field(default_factory=list)
    """Human-readable execution log for debugging and UI display."""

    def log(self, message: str) -> None:
        """Append a timestamped message to the execution log."""
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        entry = f"[{ts}] {message}"
        self.execution_log.append(entry)
        print(entry)  # Also print for real-time feedback
