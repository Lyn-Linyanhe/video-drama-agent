"""Data schemas for inter-agent communication."""

from .script import ScriptBreakdown, Scene
from .visual import VisualDesign, SceneVisual
from .prompt import GenerationPrompts, ShotPrompt
from .assets import GeneratedAssets, ShotAsset
from .review import ReviewResult, Issue
from .audio import AudioAssets, SceneAudio
from .character import CharacterProfile, CharacterImage, CharacterImageAssets
from .scene_image import SceneImage, SceneImageAssets

__all__ = [
    "ScriptBreakdown", "Scene",
    "VisualDesign", "SceneVisual",
    "GenerationPrompts", "ShotPrompt",
    "GeneratedAssets", "ShotAsset",
    "ReviewResult", "Issue",
    "AudioAssets", "SceneAudio",
    "CharacterProfile", "CharacterImage", "CharacterImageAssets",
    "SceneImage", "SceneImageAssets",
]
