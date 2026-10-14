"""Schema for script breakdown — output of Script/Drama Agent."""

from pydantic import BaseModel, Field
from typing import Optional

from src.schemas.scene_state import LocationState


class DialogueLine(BaseModel):
    """A single line of dialogue."""
    character: str = Field(description="Character name speaking the line")
    line: str = Field(description="The dialogue text")
    emotion: Optional[str] = Field(default=None, description="Emotion while speaking")
    order: Optional[int] = Field(default=None, ge=1, description="Optional source narrative order within a scene")


class Scene(BaseModel):
    """A single scene/shot in the drama."""
    scene_id: int = Field(description="Sequential scene number, 1-indexed")
    # Every generated video provider in the current pipeline has a three-second
    # practical floor. Legacy values are normalized while loading persisted state.
    duration_sec: int = Field(description="Estimated duration in seconds", ge=3, le=30)
    location: str = Field(description="Scene location / setting")
    characters: list[str] = Field(description="Characters appearing in this scene")
    dialogue: list[DialogueLine] = Field(default_factory=list, description="Dialogue lines")
    action_description: str = Field(description="What happens in this scene")
    camera_direction: str = Field(
        description="Camera direction: close-up, medium shot, wide shot, etc."
    )
    emotion_tone: str = Field(description="Emotional tone: tense, warm, sad, exciting, etc.")
    transition_from_prev: str = Field(
        default="cut", description="Transition from previous scene: cut, fade, dissolve, etc."
    )
    transition_duration_sec: float = Field(default=0.25, ge=0.15, le=0.8)
    continuity_from_prev: bool = Field(
        default=False, description="Whether this shot directly continues the previous shot's visual state"
    )
    continuity_reason: str = Field(default="", description="Reason for visual continuity decision")


class VisualCharacterAsset(BaseModel):
    """A named character entity classified by whether it needs visual assets."""

    asset_id: str = Field(description="Stable character asset identifier")
    name: str = Field(description="Canonical character name")
    appearance_mode: str = Field(
        description="on_screen, voice_only, still_photo, or mentioned"
    )
    description: str = Field(default="", description="Brief production-facing identity note")
    aliases: list[str] = Field(default_factory=list, description="Alternate references in script")
    costume_states: dict[str, str] = Field(
        default_factory=dict,
        description="Optional named appearance states for the same character",
    )


class LocationAsset(BaseModel):
    """A reusable physical location/background asset."""

    location_id: str = Field(description="Stable location asset identifier")
    canonical_name: str = Field(description="Normalized location name")
    identity_description: str = Field(default="", description="Stable physical-space identity")
    fixed_dressing: str = Field(default="", description="Fixed set dressing that belongs to the location")
    anchor_points: list[str] = Field(default_factory=list, description="Spatial anchors used for continuity")
    time_condition: str = Field(default="", description="Time, weather, and lighting condition")
    environment_description: str = Field(default="", description="Fixed environment and dressing")
    representative_scene_id: int = Field(description="Shot used to store the location images")
    shot_ids: list[int] = Field(default_factory=list, description="Shots using this location")
    states: list[LocationState] = Field(default_factory=list, description="Concrete visual states of this location")
    default_state_id: str = Field(default="", description="Default LocationState.state_id for legacy callers")
    aliases: list[str] = Field(default_factory=list, description="Equivalent location names in script")


class PropAsset(BaseModel):
    """A plot-relevant physical prop that needs visual continuity."""

    prop_id: str = Field(description="Stable prop identifier")
    name: str = Field(description="Canonical prop name")
    description: str = Field(default="", description="Production-facing material, form, and condition")
    critical_reason: str = Field(
        default="",
        description="Why this requires a reusable asset: plot, continuity, action, or identity",
    )
    critical_evidence: str = Field(
        default="",
        description="Concrete on-screen narrative evidence supporting the critical reason",
    )
    shot_ids: list[int] = Field(default_factory=list, description="Shots where the prop is visibly relevant")
    is_handheld: bool = Field(default=False, description="Whether a character holds or operates the prop")
    aliases: list[str] = Field(default_factory=list, description="Alternate references in script")


class ShotVisualBinding(BaseModel):
    """Binds one timed shot to reusable visual assets."""

    scene_id: int = Field(description="Matches Scene.scene_id")
    location_id: str = Field(description="Referenced LocationAsset.location_id")
    state_id: str = Field(default="", description="Referenced LocationState.state_id")
    state_override_notes: str = Field(default="", description="Manual state clarification for this shot")
    on_screen_character_ids: list[str] = Field(
        default_factory=list,
        description="Only characters physically visible in this shot",
    )
    voice_only_speakers: list[str] = Field(
        default_factory=list,
        description="Narration, phone, recording, or off-screen speakers",
    )
    prop_ids: list[str] = Field(
        default_factory=list,
        description="Only plot-relevant props visibly present or used in this shot",
    )
    visible_state_notes: str = Field(
        default="",
        description="Costume/disguise or visibility note for this shot",
    )


class VisualAssetManifest(BaseModel):
    """Normalized visual assets and their bindings to the timed shot list."""

    characters: list[VisualCharacterAsset] = Field(default_factory=list)
    locations: list[LocationAsset] = Field(default_factory=list)
    props: list[PropAsset] = Field(default_factory=list)
    shot_bindings: list[ShotVisualBinding] = Field(default_factory=list)


class ScriptBreakdown(BaseModel):
    """Full script breakdown — the core output of the Script Agent."""
    title: str = Field(description="Drama title")
    genre: str = Field(description="Genre: 都市, 古装, 悬疑, 甜宠, 逆袭, etc.")
    logline: str = Field(description="One-sentence summary of the story")
    duration_sec: int = Field(description="Estimated total duration in seconds")
    narration_style: str = Field(
        description="Narration style: first-person, third-person, no narration, etc."
    )
    target_audience: str = Field(default="通用", description="Target audience")
    scenes: list[Scene] = Field(description="Ordered list of scenes", min_length=1, max_length=20)
    asset_manifest: Optional[VisualAssetManifest] = Field(
        default=None,
        description="Normalized visual assets. Omitted for legacy projects.",
    )
