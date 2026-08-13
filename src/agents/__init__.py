"""Multi-agent system for video drama generation."""

from .script_agent import ScriptAgent
from .visual_agent import VisualDesignAgent
from .prompt_agent import PromptEngineeringAgent
from .video_gen_agent import VideoGenerationAgent
from .review_agent import ReviewAgent
from .audio_agent import AudioAgent
from .assembly_agent import AssemblyAgent
from .character_agent import CharacterAgent
from .scene_image_agent import SceneImageAgent

# Merged agents (9 → 7 agent architecture)
from .visual_prompt_agent import VisualPromptAgent
from .image_gen_agent import ImageGenerationAgent
from .prompt_review_agent import PromptReviewAgent

__all__ = [
    "ScriptAgent",
    "VisualDesignAgent",
    "PromptEngineeringAgent",
    "VideoGenerationAgent",
    "ReviewAgent",
    "AudioAgent",
    "AssemblyAgent",
    "CharacterAgent",
    "SceneImageAgent",
    # Merged
    "VisualPromptAgent",
    "ImageGenerationAgent",
    "PromptReviewAgent",
]
