"""Helpers shared by provider-native dialogue and subtitle reconciliation."""

from __future__ import annotations

from typing import Any


def build_native_dialogue_block(scene: Any) -> tuple[str, list[dict[str, str]]]:
    """Build a compact, auditable dialogue instruction for a video provider."""
    lines: list[dict[str, str]] = []
    rendered: list[str] = []
    for index, dialogue in enumerate(getattr(scene, "dialogue", []) or [], start=1):
        text = str(getattr(dialogue, "line", "") or "").strip()
        character = str(getattr(dialogue, "character", "") or "").strip() or "角色"
        if not text:
            continue
        # Avoid letting a malformed script turn into an oversized provider request.
        text = text[:500]
        line_id = f"{int(getattr(scene, 'scene_id', 0) or 0)}:{index}"
        emotion = str(getattr(dialogue, "emotion", "") or "").strip()
        lines.append({"line_id": line_id, "character": character, "text": text, "emotion": emotion})
        suffix = f"，{emotion}" if emotion else ""
        rendered.append(f"{line_id} {character}{suffix}：\"{text}\"")
    if not rendered:
        return "", lines
    block = (
        "【对白】\n" + "\n".join(rendered)
        + "\n对白内容只按上述台词自然表达，不新增台词，不改变角色身份。"
        + "不生成背景音乐；背景音乐和关键音效由项目后期混音加入。"
    )
    return block, lines


def append_native_dialogue_prompt(prompt: str, scene: Any) -> tuple[str, list[dict[str, str]]]:
    block, lines = build_native_dialogue_block(scene)
    if not block:
        return (prompt or "").strip(), lines
    base = (prompt or "").strip()
    return "\n\n".join(part for part in (base, block) if part), lines
