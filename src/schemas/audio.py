"""Project audio timeline and generated audio assets."""

from pydantic import BaseModel, Field
from typing import Optional
import uuid


class AudioVersion(BaseModel):
    version_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    audio_path: str = ""
    provider: str = ""
    voice: str = ""
    duration_sec: float = Field(default=0.0, ge=0.0)
    text: str = ""
    emotion: str = "calm"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    volume: float = Field(default=1.0, gt=0.0, le=2.0)
    pitch: int = Field(default=0, ge=-12, le=12)
    created_at: str = ""
    error_message: str = ""


class VoiceProfile(BaseModel):
    character: str
    provider: str = "edge"
    voice: str = ""
    emotion: str = "calm"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    volume: float = Field(default=1.0, gt=0.0, le=2.0)
    pitch: int = Field(default=0, ge=-12, le=12)
    confirmed: bool = False


class AudioLibraryAsset(BaseModel):
    asset_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    kind: str = "bgm"  # bgm or sfx
    label: str = ""
    tags: list[str] = Field(default_factory=list)
    audio_path: str = ""
    duration_sec: float = Field(default=0.0, ge=0.0)
    sha256: str = ""


class SoundEffectTrack(BaseModel):
    track_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:16])
    asset_id: str = ""
    start_ms: int = Field(default=0, ge=0)
    volume: float = Field(default=1.0, gt=0.0, le=2.0)
    manual: bool = False


class DialogueAudio(BaseModel):
    """A single spoken dialogue line and its generated audio result."""

    scene_id: int = Field(description="Matches ScriptBreakdown.scenes[].scene_id")
    dialogue_index: int = Field(ge=1, description="1-based line order within the scene")
    character: str = Field(default="", description="Speaker name")
    text: str = Field(default="", description="Spoken text")
    voice: str = Field(default="", description="TTS voice identifier")
    audio_path: str = Field(default="", description="Generated audio file path")
    duration_sec: float = Field(default=0.0, ge=0.0)
    success: bool = Field(default=False)
    error_message: str = Field(default="", description="Generation failure, if any")
    line_id: str = ""
    provider: str = ""
    emotion: str = "calm"
    speed: float = Field(default=1.0, ge=0.5, le=2.0)
    volume: float = Field(default=1.0, gt=0.0, le=2.0)
    pitch: int = Field(default=0, ge=-12, le=12)
    lead_silence_ms: int = Field(default=0, ge=0, le=10000)
    tail_silence_ms: int = Field(default=120, ge=0, le=10000)
    start_ms: int = Field(default=0, ge=0)
    end_ms: int = Field(default=0, ge=0)
    timing_conflict: bool = False
    needs_regeneration: bool = False
    fallback_reason: str = ""
    versions: list[AudioVersion] = Field(default_factory=list)
    active_version_id: str = ""


class SceneAudio(BaseModel):
    """Audio for a single scene."""
    scene_id: int = Field(description="Matches ScriptBreakdown.scenes[].scene_id")
    tts_audio_path: str = Field(default="", description="Path to TTS voiceover audio")
    tts_text: str = Field(default="", description="The text spoken in TTS")
    native_audio_enabled: bool = Field(default=False, description="Whether this scene uses provider-native audio")
    native_audio_path: str = Field(default="", description="Video or separate provider audio carrying native dialogue")
    native_audio_source: str = Field(default="", description="Provider that generated the native audio")
    subtitle_correction_status: str = Field(default="fallback", description="ASR subtitle status")
    asr_provider: str = ""
    asr_model: str = ""
    asr_segments: list[dict] = Field(default_factory=list)
    asr_changed_lines: list[dict] = Field(default_factory=list)
    asr_error: str = ""
    dialogue_audios: list[DialogueAudio] = Field(
        default_factory=list,
        description="Per-line TTS results in script order",
    )
    character_voice_map: dict[str, str] = Field(
        default_factory=dict,
        description="Character name → TTS voice ID mapping"
    )
    sfx_paths: list[str] = Field(default_factory=list, description="Sound effect file paths")
    bgm_asset_id: str = ""
    bgm_manual: bool = False
    sfx_manual: bool = False
    bgm_volume: float = Field(default=0.18, gt=0.0, le=1.0)
    bgm_fade_ms: int = Field(default=350, ge=0, le=10000)
    sfx_tracks: list[SoundEffectTrack] = Field(default_factory=list)
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
    voice_profiles: dict[str, VoiceProfile] = Field(default_factory=dict)
    library_assets: list[AudioLibraryAsset] = Field(default_factory=list)
    preview_mix_path: str = ""
    timeline_version: int = 1
    # New audio jobs use the project timeline as the sole mix source. ``None``
    # keeps legacy projects compatible until they are regenerated.
    source_audio_enabled: Optional[bool] = None
    asr_enabled: bool = False
    asr_model: str = "small"
    subtitle_correction_status: str = "fallback"
