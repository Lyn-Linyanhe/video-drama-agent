from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from src.schemas.prompt import PromptExecutionContext, ShotPrompt
from src.schemas.script import Scene

_SECTION_RE = re.compile(r"(?ms)^【([^】]+)】\s*(.*?)(?=^【[^】]+】|\Z)")
_ASSET_TAG_RE = re.compile(r"@(character|prop|scene):([^:@\s]+)(?::([A-Za-z0-9_]+))?")
_VOICE_INSTRUCTION_RE = re.compile(r"(?:\n)?对白必须生成真实人声.*$")
_VOICE_INSTRUCTION = "对白必须生成真实人声并与嘴型同步，不得用字幕代替人声。"
_QUOTED_LINE_RE = re.compile('[' + chr(0x22) + chr(0x27) + chr(0x201C) + chr(0x201D) + r'](.+?)[' + chr(0x22) + chr(0x27) + chr(0x201C) + chr(0x201D) + ']')


def humanize_reference_tags(text: str, *, prop_labels: dict[str, str] | None = None) -> str:
    """Replace inline @asset tags with readable names for the video model."""
    source = str(text or "")
    labels = prop_labels or {}
    parts: list[str] = []
    last = 0
    for match in _ASSET_TAG_RE.finditer(source):
        kind, ident = match.group(1), match.group(2)
        if kind == "character":
            label = ident
        elif kind == "prop":
            label = labels.get(ident) or labels.get("__default_prop__") or ident
        else:
            label = ""
        prefix = source[last:match.start()]
        if label:
            overlap = 0
            for size in range(len(label), 0, -1):
                if prefix.endswith(label[:size]):
                    overlap = size
                    break
            if overlap:
                prefix = prefix[:-overlap]
            parts.append(prefix)
            parts.append(label)
        else:
            parts.append(prefix)
        last = match.end()
    parts.append(source[last:])
    cleaned = "".join(parts)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


_PROP_ID_RE = re.compile(r"prop_[A-Za-z0-9_]+")
_CHINESE_PROP_RE = re.compile(r"[\u4e00-\u9fff]{2,20}")


def _chinese_prop_name(raw: str) -> str:
    cleaned = re.sub(r"\([^)]*\)", " ", str(raw or ""))
    cleaned = re.sub(r"\b(front|back|left_profile|right_profile|front_full|front_half|back_full)\b", " ", cleaned, flags=re.I)
    for token in _CHINESE_PROP_RE.findall(cleaned):
        if token not in {"道具", "角色", "场景", "参考图"}:
            return token
    return ""


def _prop_labels_from_references(references: list[dict[str, Any]]) -> dict[str, str]:
    labels: dict[str, str] = {}
    chinese_names: list[str] = []
    for item in references:
        if str(item.get("asset_type") or "").lower() != "prop":
            continue
        raw = " ".join(str(item.get(key) or "") for key in ("asset_id", "label", "name", "reference_id"))
        chinese = _chinese_prop_name(raw)
        if chinese:
            chinese_names.append(chinese)
        for prop_id in _PROP_ID_RE.findall(raw):
            if chinese:
                labels[prop_id] = chinese
        asset_id = str(item.get("asset_id") or "").strip()
        if asset_id and chinese:
            labels[asset_id] = chinese
    unique = list(dict.fromkeys(chinese_names))
    if len(unique) == 1:
        labels.setdefault("__default_prop__", unique[0])
    return labels


def _sections(prompt: str) -> dict[str, str]:
    text = str(prompt or "").replace("\r\n", "\n").strip()
    return {match.group(1).strip(): match.group(2).strip() for match in _SECTION_RE.finditer(text)}


def _section(sections: dict[str, str], *names: str) -> str:
    for name in names:
        value = str(sections.get(name, "") or "").strip()
        if value:
            return value
    return ""


_LEFT_BRACKET = chr(0x3010)
_RIGHT_BRACKET = chr(0x3011)
_INLINE_HEADER_RE = re.compile(
    rf"{_LEFT_BRACKET}([^{_RIGHT_BRACKET}]+){_RIGHT_BRACKET}(.*?)(?={_LEFT_BRACKET}[^{_RIGHT_BRACKET}]+{_RIGHT_BRACKET}|\Z)",
    flags=re.S,
)


def _split_inline_section(text: str, *headers: str) -> tuple[str, str]:
    """Pull a mid-line 【header】 block out of a section value.

    DeepSeek sometimes writes ``【镜头语言】… 【光线与色调】…`` on one line, so
    the line-anchored section parser keeps them glued together.  The execution
    layer must still show lighting under 【环境与视觉风格】, not buried in the
    camera line.  Returns ``(remaining_text, lifted_headers_joined)``.
    """
    source = str(text or "")
    wanted = {str(header).strip() for header in headers if str(header).strip()}
    if not source or not wanted:
        return source, ""
    lifted: list[str] = []
    remainder = source
    for match in _INLINE_HEADER_RE.finditer(source):
        header = match.group(1).strip()
        if header in wanted:
            body = match.group(2).strip()
            lifted.append(f"{header}：{body}" if body else header)
            remainder = remainder.replace(match.group(0), "", 1)
    if not lifted:
        return source, ""
    cleaned = re.sub(r"[；;]\s*(?=$|\n)", "", remainder).strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned, "；".join(lifted)


def _strip_internal_prefix(value: str) -> str:
    value = re.sub(r"(?m)^核心分镜意图[：:]\s*", "", value or "").strip()
    return value



def _spoken_cue(dialogue_block: str) -> str:
    """Turn a dialogue block into a single inline spoken cue for the timeline."""
    text = _VOICE_INSTRUCTION_RE.sub("", str(dialogue_block or "")).strip()
    if not text:
        return ""
    left, right = chr(0x3010), chr(0x3011)
    text = re.sub(rf"(?ms)^{left}[^{right}]*{right}\s*", "", text).strip()
    compact = re.sub(r"\s+", " ", text).strip()
    compact = compact.replace(chr(0xFF5C), " ")
    speaker_line = re.match(rf"^(.+?)[{chr(0xFF1A)}:]\s*(.*)$", compact)
    if speaker_line:
        speaker = speaker_line.group(1).strip()
        line = speaker_line.group(2).strip().strip(chr(0x201C) + chr(0x201D) + '"' + "'")
        if speaker and line:
            return f'{speaker}{chr(0xFF1A)}"{line}"'
    return compact


def _already_spoken_in_timeline(timeline: str) -> bool:
    """True when the timeline already carries an inline spoken cue."""
    source = str(timeline or "")
    then = chr(0x8FD9) + chr(0x65F6) + chr(0x5019)
    if then in source:
        return True
    return bool(_QUOTED_LINE_RE.search(source))


def _embed_dialogue_in_timeline(timeline: str, dialogue_block: str) -> str:
    """Keep spoken lines next to the action they belong to, not in a later block."""
    cue = _spoken_cue(dialogue_block)
    source = str(timeline or "").strip()
    if not cue:
        return source
    quoted = _QUOTED_LINE_RE.search(cue)
    line = quoted.group(1) if quoted else ""
    if cue in source or (line and line in source) or _already_spoken_in_timeline(source):
        return source
    then = chr(0x8FD9) + chr(0x65F6) + chr(0x5019)
    period = chr(0x3002)
    comma = chr(0xFF0C)
    trailing = period + chr(0xFF1B) + chr(0xFF0C) + chr(0x3001) + ". "
    source = source.rstrip(trailing)
    closing = (chr(0x201D), chr(0x22))
    suffix = "" if cue.rstrip().endswith(closing) else period
    # 内心独白不是开口说话："这时候"会指示口型同步，必须换成"心中暗道"，
    # 并去掉说话人标签里的独白标记，避免模型让人物张嘴念出来。
    if "内心独白" in cue:
        speaker = cue.split(chr(0xFF1A), 1)[0]
        for token in ("内心独白", "（画外音）", "(画外音)"):
            speaker = speaker.replace(token, "")
        speaker = speaker.strip()
        if speaker and line:
            cue = f'{speaker}心中暗道："{line}"'
        if not source:
            return cue + suffix
        return source + comma + cue + suffix
    if not source:
        return then + cue + suffix
    return source + comma + then + cue + suffix

def _dialogue_from_scene(
    scene: Scene | None,
    dialogue_ids: Iterable[str] | None,
    *,
    empty_means_none: bool = False,
) -> str:
    if scene is None:
        return ""
    # 拆段子镜携带 -part-N 后缀 id；按基础 id 匹配台词行。拆段文本由
    # native_audio.build_dialogue_plan 在快照层按预算截取，这里不重复切割。
    from src.services.native_audio import base_dialogue_id

    allowed = {
        base_dialogue_id(item)
        for item in (dialogue_ids or [])
        if str(item or "").strip()
    }
    if empty_means_none and not allowed:
        return ""
    rendered: list[str] = []
    for index, line in enumerate(scene.dialogue or [], start=1):
        line_id = f"scene-{scene.scene_id}-dialogue-{index}"
        if allowed and line_id not in set(allowed):
            continue
        speaker = str(line.character or "未知角色").strip() or "未知角色"
        emotion = f"（{str(line.emotion).strip()}）" if str(line.emotion or "").strip() else ""
        text = str(line.line or "").strip()
        if text:
            rendered.append(f"{speaker}{emotion}：\"{text}\"")
    if not rendered:
        return ""
    return "\n".join(rendered)


def _dialogue_from_prompt(prompt: str) -> str:
    sections = _sections(prompt)
    value = _section(sections, "关键对白（必须出现）", "关键对白", "对白", "对白/独白")
    if not value:
        return ""
    return _VOICE_INSTRUCTION_RE.sub("", value).strip()


def _valid_character_refs(scene: Scene | None, references: list[dict[str, Any]]) -> bool:
    names = [str(item or "").strip() for item in (getattr(scene, "characters", []) or []) if str(item or "").strip()]
    if not names:
        return True
    char_refs = [item for item in references if str(item.get("asset_type") or "").lower() == "character"]
    if len(char_refs) < len(names):
        return False
    haystacks = [" ".join(str(item.get(key) or "") for key in ("asset_id", "label", "reference_id")) for item in char_refs]
    return all(any(name in hay for hay in haystacks) for name in names)


def extract_camera_scoped_text(prompt: str) -> str:
    """Return only the camera/timeline sections of a shot prompt.

    视图选择（正面/侧面/背面参考图）只应依据镜头语言与时间轴判断；
    环境叙述里的"背影""侧脸"等词（如"暖光落在孩子侧脸上"）不该把
    正面构图的镜头带偏。
    """
    source = str(prompt or "")
    camera_headers = ("镜头与表演", "镜头语言")
    timeline_headers = ("动作时间轴", "时间轴分镜", "单镜头时间轴")
    collected: list[str] = []
    for headers in (camera_headers, timeline_headers):
        for header in headers:
            match = re.search(
                rf"(?ms)【{header}】(.*?)(?=【[^】]+】|\Z)",
                source,
            )
            if match and match.group(1).strip():
                collected.append(f"{header}：{match.group(1).strip()}")
    return "\n".join(collected) if collected else source


def _current_props(sections: dict[str, str]) -> str:
    return _section(sections, "关键道具", "绑定道具", "道具锚点", "道具")


def build_execution_context(
    *,
    shot: ShotPrompt,
    scene: Scene | None,
    valid_references: list[dict[str, Any]] | None = None,
) -> PromptExecutionContext:
    """Build the short, ordered execution layer without deleting source facts."""
    sections = _sections(shot.prompt)
    references = [item for item in (valid_references or []) if isinstance(item, dict) and item.get("image_path")]
    prop_labels = _prop_labels_from_references(references)
    identity_covered = _valid_character_refs(scene, references)
    scoped = bool(getattr(shot, "shot_id", None))
    action = _section(sections, "动作时间轴", "时间轴分镜", "单镜头时间轴")
    if not action:
        # A scoped child must not inherit the parent scene's full action chain.
        if not scoped:
            action = str(getattr(scene, "action_description", "") or "").strip()
    action = action.strip()
    if scoped:
        if shot.dialogue_ids:
            dialogue = _dialogue_from_prompt(shot.prompt)
            if not dialogue and not _already_spoken_in_timeline(action):
                dialogue = _dialogue_from_scene(
                    scene,
                    shot.dialogue_ids,
                    empty_means_none=True,
                )
        else:
            dialogue = ""
    else:
        dialogue = _dialogue_from_scene(scene, shot.dialogue_ids) or _dialogue_from_prompt(shot.prompt)
    cue = _spoken_cue(dialogue)
    quoted = ""
    if cue:
        match = _QUOTED_LINE_RE.search(cue)
        quoted = match.group(1) if match else ""
    if _already_spoken_in_timeline(action) or (quoted and quoted in action):
        dialogue = ""
    else:
        action = _embed_dialogue_in_timeline(action, dialogue)
    core = _strip_internal_prefix(_section(sections, "核心分镜意图", "本镜执行核心"))
    if not core:
        core = action.split("；", 1)[0].strip() if action else "完成本镜已绑定的动作和对白。"
    props = _current_props(sections)
    characters = [str(item).strip() for item in (getattr(scene, "characters", []) or []) if str(item).strip()]
    omitted = []
    if identity_covered:
        omitted.append("出镜角色与身份锚点（由有效人物参考图承担）")
    fallback = not identity_covered and bool(characters)
    reference_block = "参考图承担人物身份、场景空间和关键道具外观；只按当前镜头动作使用参考图。"
    if not references:
        reference_block = "当前镜头没有有效参考图，必须依据必要的当前镜头文字约束保持人物、场景和道具一致。"
    identity_fallback = ""
    if fallback:
        identity_fallback = "【身份回退约束】参考图未完整覆盖当前出镜角色；仅使用必要身份信息保持角色可识别，不新增跨镜头状态。"
        reference_block = identity_fallback + "\n" + reference_block
    forbidden = _section(sections, "本镜禁止执行", "本镜禁止", "避免出现")
    if forbidden:
        forbidden = forbidden.strip()
    camera_raw = _section(sections, "镜头语言", "镜头")
    # DeepSeek may glue 【光线与色调】 into the camera line; lift it into the
    # environment block so the provider sees camera and lighting separately.
    camera_raw, lifted_lighting = _split_inline_section(camera_raw, "光线与色调", "光线", "色调")
    environment_raw = _section(sections, "环境")
    lighting_raw = _section(sections, "光线与色调") or lifted_lighting
    return PromptExecutionContext(
        scene_id=shot.scene_id,
        shot_id=shot.shot_id,
        core_intent=humanize_reference_tags(core, prop_labels=prop_labels),
        action_timeline=humanize_reference_tags(action, prop_labels=prop_labels),
        dialogue_block=humanize_reference_tags(dialogue, prop_labels=prop_labels),
        current_characters=characters,
        current_state=humanize_reference_tags(_section(sections, "主体状态", "角色状态"), prop_labels=prop_labels),
        current_props=[humanize_reference_tags(props, prop_labels=prop_labels)] if props else [],
        camera_block=humanize_reference_tags(camera_raw, prop_labels=prop_labels),
        environment_block=humanize_reference_tags(
            "；".join(item for item in (environment_raw, lighting_raw) if item),
            prop_labels=prop_labels,
        ),
        reference_block=reference_block,
        forbidden_block=humanize_reference_tags(forbidden, prop_labels=prop_labels),
        intentionally_omitted_sections=omitted,
        identity_reference_fallback=fallback,
    )


def render_execution_prompt(context: PromptExecutionContext) -> str:
    """Render sections in execution priority order for the provider."""
    timeline = context.action_timeline.strip()
    core = context.core_intent.strip()
    left, right = chr(0x3010), chr(0x3011)

    def _without_time_prefix(value: str) -> str:
        return re.sub(rf"^\d+(?:\.\d+)?{chr(0x79D2)}?[-~至]\d*(?:\.\d+)?{chr(0x79D2)}?\s*", "", value).strip()

    def _without_embedded_dialogue(value: str) -> str:
        then = chr(0x8FD9) + chr(0x65F6) + chr(0x5019)
        return re.sub(rf"[{chr(0xFF0C)}{chr(0x3001)}]?{then}.+$", "", value).strip()

    # Drop the core only when it merely restates the timeline's own action
    # (ignoring the leading time range and any inline spoken line).  A short
    # first-beat summary must stay: providers expect the intent line first.
    def _core_repeats_timeline(core_value: str, timeline_value: str) -> bool:
        if not core_value:
            return False
        action_only = _without_embedded_dialogue(_without_time_prefix(timeline_value))
        core_only = _without_embedded_dialogue(_without_time_prefix(core_value))
        core_only = core_only.rstrip(
            chr(0x3002) + chr(0xFF01) + chr(0xFF1F) + ". "
        )
        action_stripped = action_only.rstrip(
            chr(0x3002) + chr(0xFF01) + chr(0xFF1F) + ". "
        )
        if core_only == action_stripped:
            return True
        # 自动推导的核心常取动作第一个分句；逐字前缀且停在分句边界时，
        # 时间轴首句本身就是这个意图，不再单列核心制造重复。
        boundary = chr(0xFF1B) + chr(0xFF0C) + chr(0x3001) + ";,"
        return (
            len(core_only) >= 10
            and action_stripped.startswith(core_only)
            and (
                len(action_stripped) == len(core_only)
                or action_stripped[len(core_only)] in boundary
            )
        )

    if core and timeline and _core_repeats_timeline(core, timeline):
        core = ""
    parts = []
    if core:
        parts.append(f"{left}本镜执行核心{right}\n" + core)
    timeline = _embed_dialogue_in_timeline(timeline, context.dialogue_block)
    if core and timeline and _core_repeats_timeline(core, timeline):
        core = ""
        parts[:] = [part for part in parts if f"{left}本镜执行核心" not in str(part)]
    parts.append(f"{left}动作时间轴{right}\n" + timeline)
    if context.dialogue_block:
        parts.append(_VOICE_INSTRUCTION)
    if context.current_characters or context.current_state or context.current_props:
        current = []
        if context.current_characters:
            current.append("出镜角色：" + "、".join(context.current_characters))
        if context.current_state:
            current.append("当前状态：" + context.current_state)
        if context.current_props:
            current.append("关键道具：" + "；".join(context.current_props))
        parts.append("【当前镜头角色与道具】\n" + "\n".join(current))
    if context.camera_block:
        parts.append("【镜头与表演】\n" + context.camera_block)
    if context.environment_block:
        parts.append("【环境与视觉风格】\n" + context.environment_block)
    if context.reference_block:
        parts.append("【参考图约束】\n" + context.reference_block)
    if context.forbidden_block:
        parts.append("【本镜禁止】\n" + context.forbidden_block)
    return "\n\n".join(part.strip() for part in parts if part.strip())
