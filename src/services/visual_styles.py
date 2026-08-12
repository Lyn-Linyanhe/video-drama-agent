"""Canonical visual-style definitions and legacy compatibility helpers."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.schemas.project import CreativeProfile


_KNOWN_MODIFIERS = ("赛博朋克", "国风", "清新")

VISUAL_STYLES: tuple[dict, ...] = (
    {
        "id": "cinematic_cel",
        "name": "现代电影级二维赛璐璐",
        "description": "清晰线稿与电影级构图，适合都市悬疑和现实题材。",
        "reference_image": "/static/assets/style-references/cinematic-cel.webp?v=20260810-2",
        "tags": ["清晰线稿", "赛璐璐阴影", "电影光影"],
        "prompt_layers": {
            "character": "二维手绘动画媒介，清晰受控的轮廓线，成年人物自然比例，两到三层硬边赛璐璐阴影。",
            "scene": "二维背景绘制，稳定透视，冷暖对照的电影级光影，环境结构清晰且有景深层次。",
            "prop": "二维动画设定稿媒介，轮廓明确，以简洁色块和硬边阴影表现材质与结构。",
            "shot": "保持二维动画媒介和稳定人物比例，采用电影级构图、明确镜头运动与一致的光影语言。",
        },
        "negative_prompt_layers": {
            "character": "真人照片，三维渲染，Q版比例，塑料皮肤，风格漂移。",
            "scene": "照片级实拍，三维游戏场景，透视畸变，风格漂移。",
            "prop": "产品实拍，三维建模展示，结构漂移，风格漂移。",
            "shot": "真人电影实拍，三维角色，媒介混用，角色比例漂移。",
        },
    },
    {
        "id": "semi_realistic_anime",
        "name": "半写实动漫",
        "description": "保留动画线稿，并以细腻材质和光影强化电影感。",
        "reference_image": "/static/assets/style-references/semi-realistic-anime.webp?v=20260810-2",
        "tags": ["细腻线稿", "自然比例", "电影氛围"],
        "prompt_layers": {
            "character": "半写实二维动漫，精准线稿，自然成年人体比例，保留动画化五官并呈现细腻服装和材质细节。",
            "scene": "半写实动画概念场景，稳定空间结构，细腻材质绘制，柔硬结合的电影光影与克制调色。",
            "prop": "半写实二维道具设定图，清晰线稿与可辨识材质，结构比例稳定，细节服务剧情。",
            "shot": "半写实二维动画镜头，人物与环境保持统一绘制媒介，自然景深和电影级光线层次。",
        },
        "negative_prompt_layers": {
            "character": "真人摄影，三维CG，过度磨皮，Q版人体，塑料质感。",
            "scene": "实拍照片，三维游戏资产，扭曲透视，低细节背景。",
            "prop": "实物电商摄影，三维渲染，过度塑料高光，结构变形。",
            "shot": "真人实拍，三维角色，风格混杂，夸张卡通比例。",
        },
    },
    {
        "id": "japanese_animation",
        "name": "日系商业动画",
        "description": "干净线条、稳定面部设计和清晰色块，适合角色驱动叙事。",
        "reference_image": "/static/assets/style-references/japanese-animation.webp?v=20260810-2",
        "tags": ["干净线条", "稳定脸型", "清晰色块"],
        "prompt_layers": {
            "character": "日系商业二维动画角色设计，干净利落的线条，稳定脸型和成年人物比例，清晰色块与一致服装细节。",
            "scene": "日系商业动画背景绘制，空间逻辑清楚，色彩关系简洁统一，背景细节为人物表演服务。",
            "prop": "日系商业动画道具设定，轮廓简洁易读，色块明确，材质和比例与角色设计一致。",
            "shot": "日系商业二维动画镜头，稳定角色造型，易读构图和有节奏的镜头运动，统一色彩与背景绘制。",
        },
        "negative_prompt_layers": {
            "character": "真人照片，三维渲染，写实皮肤纹理，Q版比例，脸型漂移。",
            "scene": "摄影实拍，三维游戏画面，杂乱写实材质，透视变形。",
            "prop": "产品摄影，三维模型，复杂不一致材质，结构变形。",
            "shot": "真人电影画面，三维人物，媒介混用，角色脸型漂移。",
        },
    },
    {
        "id": "live_action_cinematic",
        "name": "电影级真人写实",
        "description": "真实人物、自然材质和可信镜头语言，适合现实主义短剧。",
        "reference_image": "/static/assets/style-references/live-action-cinematic.webp?v=20260810-2",
        "tags": ["真实人物", "自然材质", "电影镜头"],
        "prompt_layers": {
            "character": "电影级真人写实，真实成年人体比例，自然皮肤、发丝和织物纹理，可信服装与妆容细节。",
            "scene": "电影级真人实拍环境，物理可信的材质、光源和反射，真实空间比例与自然景深。",
            "prop": "电影级实物道具，真实材质、磨损和尺寸关系，符合镜头光线与剧情年代。",
            "shot": "电影级真人镜头语言，真实镜头和自然景深，物理可信光影，人物、场景和道具的材质统一。",
        },
        "negative_prompt_layers": {
            "character": "插画，动漫，三维渲染，塑料皮肤，夸张卡通比例。",
            "scene": "插画背景，动漫场景，三维游戏画面，模型感，透视畸变。",
            "prop": "插画道具，动漫设计图，三维模型渲染，塑料质感。",
            "shot": "二维插画，动漫人物，三维CG，媒介混用，不可信光影。",
        },
    },
)

_STYLE_BY_ID = {style["id"]: style for style in VISUAL_STYLES}
_STYLE_ALIASES = {
    "写实": "live_action_cinematic",
    "电影级写实": "live_action_cinematic",
    "电影级真人写实": "live_action_cinematic",
    "真人": "live_action_cinematic",
    "动漫": "cinematic_cel",
    "动漫风": "cinematic_cel",
    "二次元": "cinematic_cel",
    "现代电影级二维赛璐璐": "cinematic_cel",
    "半写实": "semi_realistic_anime",
    "半写实动漫": "semi_realistic_anime",
    "日系": "japanese_animation",
    "日系动画": "japanese_animation",
    "日系商业动画": "japanese_animation",
}


def get_visual_styles() -> list[dict]:
    """Return a caller-owned copy of the public style catalog."""
    return deepcopy(list(VISUAL_STYLES))


def get_visual_style(style_id: str) -> dict:
    """Return one caller-owned style definition or raise for an unknown stable ID."""
    normalized_id = str(style_id or "").strip()
    try:
        return deepcopy(_STYLE_BY_ID[normalized_id])
    except KeyError as exc:
        raise ValueError(f"Unknown visual_style_id: {normalized_id!r}") from exc


def _known_modifiers(values: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if text in _KNOWN_MODIFIERS and text not in normalized:
            normalized.append(text)
    return normalized


def _style_text_parts(art_style: str) -> list[str]:
    normalized = str(art_style or "").replace("，", "、").replace(",", "、")
    return [part.strip() for part in normalized.split("、") if part.strip()]


def _compatibility_text(base: str, modifiers: list[str]) -> str:
    if not modifiers:
        return base
    existing = _style_text_parts(base)
    additional = [modifier for modifier in modifiers if modifier not in existing]
    return "、".join(existing + additional) if existing else "、".join(modifiers)


def resolve_visual_style(
    visual_style_id: str = "",
    art_style: str = "",
    style_modifiers: list[str] | None = None,
) -> tuple[str, str, list[str]]:
    """Resolve stable style ID, legacy display text, and supported modifier tags."""
    explicit_id = str(visual_style_id or "").strip()
    legacy_text = str(art_style or "").strip()
    modifiers = _known_modifiers(style_modifiers)

    if explicit_id:
        get_visual_style(explicit_id)
        style_id = explicit_id
    else:
        parts = _style_text_parts(legacy_text)
        style_id = next((_STYLE_ALIASES[part] for part in parts if part in _STYLE_ALIASES), "")
        if not style_id and legacy_text in _STYLE_ALIASES:
            style_id = _STYLE_ALIASES[legacy_text]
        if not style_id:
            style_id = "live_action_cinematic"

    for part in _style_text_parts(legacy_text):
        if part in _KNOWN_MODIFIERS and part not in modifiers:
            modifiers.append(part)

    compatibility_base = legacy_text or get_visual_style(style_id)["name"]
    return style_id, _compatibility_text(compatibility_base, modifiers), modifiers


def resolve_profile_style(profile: "CreativeProfile") -> dict:
    """Return the canonical entry for a project profile, including legacy-only profiles."""
    style_id, _, _ = resolve_visual_style(
        visual_style_id=getattr(profile, "visual_style_id", ""),
        art_style=getattr(profile, "art_style", ""),
        style_modifiers=getattr(profile, "style_modifiers", None),
    )
    return get_visual_style(style_id)
