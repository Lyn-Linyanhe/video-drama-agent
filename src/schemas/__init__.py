"""Data schemas for inter-agent communication."""

from .script import ScriptBreakdown, Scene
from .visual import VisualDesign, SceneVisual
from .prompt import GenerationPrompts, PromptPolicy, ShotPrompt
from .assets import GeneratedAssets, ShotAsset
from .review import ReviewResult, Issue
from .audio import AudioAssets, DialogueAudio, SceneAudio
from .prop import PropImage, PropImageAssets
from .character import CharacterProfile, CharacterImage, CharacterImageAssets
from .scene_image import SceneImage, SceneImageAssets

__all__ = [
    "ScriptBreakdown", "Scene",
    "VisualDesign", "SceneVisual",
    "GenerationPrompts", "PromptPolicy", "ShotPrompt",
    "GeneratedAssets", "ShotAsset",
    "ReviewResult", "Issue",
    "AudioAssets", "DialogueAudio", "SceneAudio", "PropImage", "PropImageAssets",
    "CharacterProfile", "CharacterImage", "CharacterImageAssets",
    "SceneImage", "SceneImageAssets",
]
