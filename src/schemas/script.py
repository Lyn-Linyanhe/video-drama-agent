"""Schema for script breakdown — output of Script/Drama Agent."""

from pydantic import BaseModel, Field
from typing import Optional


class DialogueLine(BaseModel):
    """A single line of dialogue."""
    character: str = Field(description="Character name speaking the line")
    line: str = Field(description="The dialogue text")
    emotion: Optional[str] = Field(default=None, description="Emotion while speaking")


class Scene(BaseModel):
    """A single scene/shot in the drama."""
    scene_id: int = Field(description="Sequential scene number, 1-indexed")
    duration_sec: int = Field(description="Estimated duration in seconds", ge=2, le=30)
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
