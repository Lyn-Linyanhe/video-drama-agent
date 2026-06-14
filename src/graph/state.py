"""LangGraph state definition — the shared state flowing through the agent pipeline."""

from typing import Optional, Annotated
from dataclasses import dataclass, field
from langgraph.graph.message import add_messages

from src.schemas.script import ScriptBreakdown
from src.schemas.visual import VisualDesign
from src.schemas.prompt import GenerationPrompts
from src.schemas.assets import GeneratedAssets
from src.schemas.review import ReviewResult
from src.schemas.audio import AudioAssets
from src.schemas.character import CharacterImageAssets
from src.schemas.scene_image import SceneImageAssets


@dataclass
class DramaState:
    """Shared state that flows through all agent nodes in the LangGraph pipeline.

    Each agent reads from and writes to specific fields. The orchestrator
    manages the flow between agents based on this state.
    """

    # === Input ===
    user_input: str = ""
    """Raw text input from the user (story, idea, description)."""

    # === Agent Outputs (populated by each agent in sequence) ===
    script: Optional[ScriptBreakdown] = None
    """Output of Script Agent — the full script breakdown."""

    visual_design: Optional[VisualDesign] = None
    """Output of Visual Design Agent — visual style and composition."""

    generation_prompts: Optional[GenerationPrompts] = None
    """Output of Prompt Engineering Agent — optimized API prompts."""

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

    # === Interactive Editing ===
    project_id: str = ""
    """Unique project identifier for asset directory isolation."""

    prompt_overrides: dict = field(default_factory=dict)
    """User-modified prompts: {shot_index: overridden_prompt_text}."""

    user_feedback: str = ""
    """User feedback for LLM to revise prompts."""

    # === Control Flow ===
    output_video_path: str = ""
    """Final assembled video file path."""

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
