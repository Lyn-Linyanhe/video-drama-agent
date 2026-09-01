"""Schema for visual design — output of Visual Design Agent."""

from pydantic import BaseModel, Field
from typing import Literal


class CharacterProfileRepairResult(BaseModel):
    """Outcome of optional character profile repair, with legacy unpacking support."""

    english: dict[str, str] = Field(default_factory=dict)
    chinese: dict[str, str] = Field(default_factory=dict)
    status: Literal["clean", "repaired", "partial", "failed"]
    errors: list[str] = Field(default_factory=list)

    def __iter__(self):
        # Existing callers historically unpacked ``(english, chinese)``.
        yield self.english
        yield self.chinese


class SceneVisual(BaseModel):
    """Visual design for a single scene."""
    scene_id: int = Field(description="Matches ScriptBreakdown.scenes[].scene_id")
    composition: str = Field(description="Composition description: framing, angles, depth")
    lighting: str = Field(description="Lighting setup: key light, mood, time of day")
    color_tone: str = Field(description="Color grading: warm, cold, desaturated, vibrant, etc.")
    character_appearance: dict[str, str] = Field(
        default_factory=dict,
        description="Character name → appearance description. MUST be consistent across scenes."
    )
    key_elements: list[str] = Field(description="Key visual elements that must appear")
    style_reference: str = Field(default="", description="Visual style reference description")


class VisualDesign(BaseModel):
    """Overall visual design for the entire drama."""
    overall_style: str = Field(
        description="Overall visual style: 写实, 动漫, 赛博朋克, 古风, 胶片质感, etc."
    )
    color_palette: str = Field(description="Overall color palette description")
    aspect_ratio: str = Field(default="9:16", description="Aspect ratio: 9:16 or 16:9")
    global_character_descriptions: dict[str, str] = Field(
        default_factory=dict,
        description="Master character appearance registry (English, for API use). "
                    "Each scene's character_appearance MUST reference and stay consistent with these descriptions."
    )
    global_character_descriptions_cn: dict[str, str] = Field(
        default_factory=dict,
        description="中文版本的角色外貌描述（供前端展示使用）"
    )
    scenes: list[SceneVisual] = Field(description="Visual design per scene")
    profile_repair_status: str | None = Field(default=None, description="Character profile repair status")
