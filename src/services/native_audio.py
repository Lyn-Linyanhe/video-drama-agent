"""Helpers shared by provider-native dialogue and subtitle reconciliation."""

from __future__ import annotations

import re
from typing import Any

from src.services.beat_planner import split_speech_chunks

_DIALOGUE_ID_RE = re.compile(r"^scene-(?P<scene>\d+)-dialogue-(?P<index>\d+)(?:-part-(?P<part>\d+))?$")


def base_dialogue_id(dialogue_id: str) -> str:
    """Strip the ``-part-N`` suffix from a split-speech dialogue id."""
    return _DIALOGUE_ID_RE.sub(lambda match: f"scene-{match.group('scene')}-dialogue-{match.group('index')}", str(dialogue_id or ""))


def build_dialogue_plan(
    scene: Any,
    dialogue_ids: list[str] | None = None,
    *,
    speech_budget_sec: float | None = None,
) -> list[dict[str, str]]:
    """Preserve dialogue for the current executable shot.

    Parent shots pass ``None`` and retain the complete scene dialogue. Child
    shots pass their bound ``scene-N-dialogue-M`` ids so unrelated parent
    dialogue is not appended to every generated child. An empty list therefore
    intentionally means that the child is silent.

    A child carrying a split-speech id (``scene-N-dialogue-M-part-K``) receives
    only its own segment. The segment is recomputed with the same packer the
    planner used, so ``speech_budget_sec`` must be the provider duration cap
    that was active when the beats were split.
    """
    scene_id = int(getattr(scene, "scene_id", 0) or 0)
    allowed = set(dialogue_ids) if dialogue_ids is not None else None
    part_budget = (
        max(1.0, float(speech_budget_sec) - 1.0)
        if speech_budget_sec is not None
        else None
    )
    lines: list[dict[str, str]] = []
    for index, dialogue in enumerate(getattr(scene, "dialogue", []) or [], start=1):
        source_id = f"scene-{scene_id}-dialogue-{index}"
        matched = [item for item in (allowed or set()) if base_dialogue_id(item) == source_id]
        if allowed is not None and not matched:
            continue
        text = str(getattr(dialogue, "line", "") or "").strip()
        if not text:
            continue
        part_numbers = sorted(
            int(_DIALOGUE_ID_RE.match(item).group("part"))
            for item in matched
            if _DIALOGUE_ID_RE.match(item) and _DIALOGUE_ID_RE.match(item).group("part")
        )
        if part_numbers:
            text = _resolve_dialogue_parts(text, part_numbers, part_budget)
        speaker = str(getattr(dialogue, "character", "") or "").strip()
        if not speaker:
            scene_characters = [
                str(name or "").strip()
                for name in (getattr(scene, "characters", []) or [])
                if str(name or "").strip()
            ]
            speaker = scene_characters[0] if len(scene_characters) == 1 else "未知角色"
        lines.append({
            "line_id": f"{scene_id}:{index}" + (f"-part-{part_numbers[0]}" if part_numbers else ""),
            "character": speaker,
            "text": text,
            "emotion": str(getattr(dialogue, "emotion", "") or "").strip(),
        })
    return lines


def _resolve_dialogue_parts(text: str, part_numbers: list[int], part_budget: float | None) -> str:
    """Return the requested segment(s) of a line split by the speech packer."""
    if part_budget is None:
        return text
    chunks = split_speech_chunks(text, part_budget)
    selected = [chunks[number - 1] for number in part_numbers if 1 <= number <= len(chunks)]
    return "".join(selected) if selected else text


def build_native_dialogue_block(
    scene: Any,
    dialogue_ids: list[str] | None = None,
    *,
    speech_budget_sec: float | None = None,
) -> tuple[str, list[dict[str, str]]]:
    """Build an auditable dialogue instruction for a native-audio provider."""
    lines = build_dialogue_plan(scene, dialogue_ids=dialogue_ids, speech_budget_sec=speech_budget_sec)
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


def append_native_dialogue_prompt(
    prompt: str,
    scene: Any,
    dialogue_ids: list[str] | None = None,
    *,
    speech_budget_sec: float | None = None,
) -> tuple[str, list[dict[str, str]]]:
    block, lines = build_native_dialogue_block(scene, dialogue_ids=dialogue_ids, speech_budget_sec=speech_budget_sec)
    if not block:
        return (prompt or "").strip(), lines
    base = (prompt or "").strip()
    existing = re.search(
        r"【(关键对白（必须出现）|对白(?:/独白)?)】.*?"
        r"(?=【(?!关键对白（必须出现）|对白(?:/独白)?)[^】]+】|"
        r"\[(?:Reference binding|Reference tags|Reference hierarchy|图层分工|连续性约束)\]|\Z)",
        base,
        flags=re.S,
    )
    canonical_body = block.split("\n", 1)[1] if "\n" in block else block
    if existing:
        heading = existing.group(1)
        replacement_heading = "关键对白（必须出现）" if heading == "关键对白（必须出现）" else "对白"
        replacement = f"【{replacement_heading}】\n{canonical_body}"
        updated = base[:existing.start()] + replacement + base[existing.end():]
        return updated.strip(), lines

    # When the prompt has an execution timeline, insert native dialogue beside it
    # so the provider sees speech as part of the core action rather than an appendix.
    # 父级模板可能残留空的【时间轴分镜】壳段且排在真正的时间轴之前：必须
    # 扫描所有时间轴段——先判断台词是否已内嵌在任意段里，再选最后一个
    # 非空段作为插入锚点，避免台词重复或插进空壳后面。
    section_re = re.compile(
        r"(?ms)【(?:动作时间轴|时间轴分镜|单镜头时间轴)】.*?"
        r"(?=【[^】]+】|\[(?:Reference binding|Reference tags|Reference hierarchy)\]|\Z)",
    )
    spans = list(section_re.finditer(base))

    def _holds_all(span_text: str) -> bool:
        return all(
            line["text"].strip() and line["text"].strip() in span_text
            for line in lines
        )

    if any(_holds_all(span.group(0)) for span in spans):
        return base.strip(), lines
    nonempty = [
        span for span in spans
        if re.sub(r"【[^】]+】", "", span.group(0)).strip()
    ]
    timeline = nonempty[-1] if nonempty else (spans[0] if spans else None)
    if timeline:
        prioritized = "【关键对白（必须出现）】\n" + canonical_body
        updated = base[:timeline.end()].rstrip() + "\n\n" + prioritized + "\n" + base[timeline.end():].lstrip()
        return updated.strip(), lines
    return "\n\n".join(part for part in (base, block) if part), lines

