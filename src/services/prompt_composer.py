"""Provider-aware video prompt composition."""

from __future__ import annotations

import re

from src.schemas.prompt import GenerationPrompts, PromptPolicy, ShotPrompt


PROVIDER_CAPABILITIES = {
    "kling": {"negative_prompt_mode": "separate"},
    "mock": {"negative_prompt_mode": "separate"},
    "happyhorse": {"negative_prompt_mode": "merged"},
    "seedance": {"negative_prompt_mode": "merged"},
    "seedence": {"negative_prompt_mode": "merged"},
}


def provider_capabilities(provider: str) -> dict[str, str]:
    return dict(PROVIDER_CAPABILITIES.get((provider or "").lower(), {"negative_prompt_mode": "merged"}))


def _join(parts: list[str]) -> str:
    return "\n".join(part.strip() for part in parts if part and part.strip())


def _join_unique(parts: list[str]) -> str:
    """Keep both legacy and project policy constraints without repeated blocks."""
    seen: set[str] = set()
    unique: list[str] = []
    for part in parts:
        normalized = (part or "").strip()
        key = " ".join(normalized.split())
        if normalized and key not in seen:
            seen.add(key)
            unique.append(normalized)
    return _join(unique)


def has_planned_editing(text: str) -> bool:
    """Detect prompts that intentionally describe a timed multi-shot sequence."""
    normalized = (text or "").casefold()
    time_markers = re.findall(r"(?<!\d)\d+(?:\.\d+)?\s*(?:s|sec|secs|second|seconds|秒)", normalized)
    if len(time_markers) >= 2:
        return True
    has_edit_word = any(token in normalized for token in (
        "flashback", "insert shot", "cutaway", "montage",
        "闪回", "插入", "快速摇摄", "反打", "切到", "切回", "切镜头",
    ))
    return bool(time_markers and has_edit_word)


def video_structure_instruction(shot: ShotPrompt) -> str:
    text = shot.prompt or ""
    if has_planned_editing(text):
        return _join([
            "[Planned edit rule]",
            "Follow the authored time-coded sequence exactly as a planned edit.",
            "Cuts are allowed only where the prompt explicitly specifies a duration, flashback, insert, pan or camera change.",
            "Do not add extra shots, extra cutaways, extra close-ups, new locations, new characters or new objects beyond the written timeline.",
            "Keep every segment's duration proportion, character identity, costume, prop state, lighting and rain continuity consistent across edits.",
        ])
    return _join([
        "[Single-take rule]",
        "Render this as one continuous take with one camera setup and one primary action beat.",
        "No montage, no shot-reverse-shot, no cutaway, no sudden switch to another camera angle, location, close-up, vehicle insert or object insert.",
        "If the storyboard text contains several beats but no explicit time-coded edit plan, keep the same camera and perform only the primary visible action slowly and clearly.",
    ])


def _video_quality_block(shot: ShotPrompt) -> str:
    """Add stable cinematic constraints without overwriting the authored shot."""
    text = (shot.prompt or "").casefold()
    blocks = [
        "[Video quality]",
        "Cinematic vertical 9:16 shot, natural lighting, clear subject separation, physically plausible motion.",
        "Keep character identity, face proportions, hairstyle, costume colors, scene layout, prop shape and prop state consistent for the full clip.",
        video_structure_instruction(shot),
        "No on-screen captions, logos, watermarks or random new objects.",
    ]
    if any(token in text for token in ("dialogue", "speaks", "whispers", "conversation", "对话", "说", "低声", "独白")):
        blocks.append("Dialogue/emotion shot: keep camera movement restrained, preserve facial structure and lip area stability.")
    if any(token in text for token in ("close-up", "closeup", "close shot", "特写", "近景", "脸部")):
        blocks.append("Close framing: prioritize stable eyes, mouth, hands and clothing edges over large motion.")
    if any(token in text for token in ("run", "chase", "fight", "push", "pull", "turn", "奔跑", "追逐", "打斗", "推", "拉", "转身")):
        blocks.append("Action shot: make the movement readable with clear start and end poses, avoiding motion smear.")
    return "\n".join(blocks)


def _project_visual_style_block(profile) -> tuple[str, str]:
    """Return the canonical project medium constraints for a video request."""
    if profile is None:
        return "", ""
    from src.services.visual_styles import resolve_profile_style

    style = resolve_profile_style(profile)
    style_id = style["id"]
    style_name = style["name"]
    positive = str(style.get("prompt_layers", {}).get("shot", "") or "").strip()
    negative = str(style.get("negative_prompt_layers", {}).get("shot", "") or "").strip()
    modifiers = [
        str(item).strip()
        for item in getattr(profile, "style_modifiers", []) or []
        if str(item).strip()
    ]
    modifier_text = f" 补充风格标签：{'、'.join(dict.fromkeys(modifiers))}。" if modifiers else ""
    return (
        f"【项目视觉媒介】{style_name}（{style_id}）：{positive}。{modifier_text}",
        negative,
    )


def compose_video_prompt(
    provider: str,
    prompts: GenerationPrompts,
    shot: ShotPrompt,
    policy: PromptPolicy | None = None,
    creative_profile=None,
) -> dict[str, str]:
    """Return the exact provider payload and its auditable composition metadata."""
    policy = policy or PromptPolicy()
    style_block, style_negative = _project_visual_style_block(creative_profile)
    effective_prompt = _join_unique([
        policy.style_prefix,
        style_block,
        shot.prompt,
        _video_quality_block(shot),
    ])
    effective_negative = _join_unique([
        policy.global_negative_prompt,
        prompts.global_negative_prompt,
        shot.negative_prompt,
        style_negative,
    ])
    mode = provider_capabilities(provider)["negative_prompt_mode"]
    provider_negative = effective_negative
    if mode == "merged" and effective_negative:
        effective_prompt = _join([effective_prompt, f"【避免出现】{effective_negative}"])
        provider_negative = ""
    return {
        "provider": provider,
        "effective_prompt": effective_prompt,
        "effective_negative_prompt": effective_negative,
        "provider_prompt": effective_prompt,
        "provider_negative_prompt": provider_negative,
        "negative_prompt_mode": mode,
    }
