"""Helpers shared by provider-native dialogue and subtitle reconciliation."""

from __future__ import annotations

from typing import Any


def build_dialogue_plan(scene: Any) -> list[dict[str, str]]:
    """Preserve the complete structured dialogue independently of visual prompts."""
    lines: list[dict[str, str]] = []
    for index, dialogue in enumerate(getattr(scene, "dialogue", []) or [], start=1):
        text = str(getattr(dialogue, "line", "") or "").strip()
        if not text:
            continue
        lines.append({
            "line_id": f"{int(getattr(scene, 'scene_id', 0) or 0)}:{index}",
            "character": str(getattr(dialogue, "character", "") or "").strip() or "角色",
            "text": text,
            "emotion": str(getattr(dialogue, "emotion", "") or "").strip(),
        })
    return lines


def build_native_dialogue_block(scene: Any) -> tuple[str, list[dict[str, str]]]:
    """Build an auditable dialogue instruction for a native-audio provider."""
    lines = build_dialogue_plan(scene)
    rendered: list[str] = []
    for line in lines:
        suffix = f"，{line['emotion']}" if line["emotion"] else ""
        rendered.append(f"{line['line_id']} {line['character']}{suffix}：\"{line['text']}\"")
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
