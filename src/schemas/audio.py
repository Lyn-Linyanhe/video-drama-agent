"""Schema for audio assets — output of Audio Agent."""

from pydantic import BaseModel, Field
from typing import Optional


class SceneAudio(BaseModel):
    """Audio for a single scene."""
    scene_id: int = Field(description="Matches ScriptBreakdown.scenes[].scene_id")
    tts_audio_path: str = Field(default="", description="Path to TTS voiceover audio")
    tts_text: str = Field(default="", description="The text spoken in TTS")
    character_voice_map: dict[str, str] = Field(
        default_factory=dict,
        description="Character name → TTS voice ID mapping"
    )
    sfx_paths: list[str] = Field(default_factory=list, description="Sound effect file paths")
    volume_mix: dict[str, float] = Field(
        default_factory=lambda: {"tts": 1.0, "sfx": 0.3, "bgm": 0.2},
        description="Volume mixing parameters"
    )


class AudioAssets(BaseModel):
    """Audio assets for the entire drama."""
    scenes: list[SceneAudio] = Field(description="Audio per scene")
    bgm_path: str = Field(default="", description="Background music file path")
    bgm_mood: str = Field(default="neutral", description="BGM mood description")
    narration_audio_path: Optional[str] = Field(
        default=None, description="Narration voiceover path"
    )
