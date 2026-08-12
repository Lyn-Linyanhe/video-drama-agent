"""Project-level creative choices that must survive every generation stage."""

from typing import Literal

from pydantic import BaseModel, Field, model_validator


class CreativeProfile(BaseModel):
    genre_tags: list[str] = Field(default_factory=list)
    art_style: str = "写实"
    video_ratio: str = "9:16"
    audio_mode: Literal["auto", "native_dialogue", "external_tts"] = "auto"
    visual_style_id: str = "live_action_cinematic"
    style_modifiers: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize_visual_style(self) -> "CreativeProfile":
        """Normalize direct construction while allowing an explicit stable ID to win."""
        from src.services.visual_styles import resolve_visual_style

        explicit_id = self.visual_style_id if "visual_style_id" in self.model_fields_set else ""
        style_id, art_style, modifiers = resolve_visual_style(
            visual_style_id=explicit_id,
            art_style=self.art_style,
            style_modifiers=self.style_modifiers,
        )
        self.visual_style_id = style_id
        self.art_style = art_style
        self.style_modifiers = modifiers
        return self

    @classmethod
    def from_request(
        cls,
        genre_tags: str = "",
        art_style: str = "",
        video_ratio: str = "",
        audio_mode: str = "auto",
        visual_style_id: str = "",
        style_modifiers: list[str] | None = None,
    ) -> "CreativeProfile":
        tags = [item.strip() for item in genre_tags.replace("，", "、").split("、") if item.strip()]
        ratio = video_ratio if video_ratio in {"9:16", "16:9"} else "9:16"
        mode = audio_mode if audio_mode in {"auto", "native_dialogue", "external_tts"} else "auto"
        from src.services.visual_styles import resolve_visual_style

        requested_style = art_style.strip()
        if not requested_style and not visual_style_id.strip():
            requested_style = "写实"
        style_id, compatibility_style, modifiers = resolve_visual_style(
            visual_style_id=visual_style_id,
            art_style=requested_style,
            style_modifiers=style_modifiers,
        )
        return cls(
            genre_tags=tags,
            art_style=compatibility_style,
            video_ratio=ratio,
            audio_mode=mode,
            visual_style_id=style_id,
            style_modifiers=modifiers,
        )
