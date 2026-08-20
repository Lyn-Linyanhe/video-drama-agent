"""Timed storyboard prompt composition for video generation."""

from __future__ import annotations

import re
from math import ceil

from src.schemas.prompt import ShotPrompt
from src.schemas.script import DialogueLine, Scene, ScriptBreakdown
from src.services.prompt_composer import has_planned_editing
from src.utils.visual_asset_manifest import binding_for_scene, props_for_scene


TEXT_OVERLAY_NOTE = (
    "所有可读中文文字优先作为后期叠加；"
    "画面只表现屏幕光、纸张、照片、地图、手部动作和角色反应，不强求模型生成准确文字。"
)


def _normalize_prompt_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", str(text or "")).strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"。{2,}", "。", text)
    text = re.sub(r"；{2,}", "；", text)
    text = text.replace("。。", "。")
    return text


def _sections(text: str) -> dict[str, str]:
    found = re.findall(r"【([^】]+)】([^【]+)", text or "")
    return {name.strip(): _normalize_prompt_text(value).strip("。； \n") for name, value in found}


def _preview_core_intent(text: str, *, limit: int = 160) -> str:
    text = _normalize_prompt_text(text)
    if not text:
        return ""
    text = re.sub(r"^核心分镜意图[:：]\s*", "", text)
    text = re.sub(r"^\d+(?:\.\d+)?s[，,]\s*", "", text)
    time_marker = re.search(r"\d+(?:\.\d+)?s[，,]", text)
    if time_marker:
        text = text[: time_marker.start()].strip()
    text = re.split(r"[。；;\n]+", text, maxsplit=1)[0].strip("。；; ，,")
    return text[:limit].rstrip("。；; ，,")


def _strip_binding_prop_section(text: str) -> str:
    """Remove reusable prop lifecycle descriptions from current-shot event parsing."""
    return re.sub(r"【绑定道具】.*?(?=【|$)", "", str(text or ""), flags=re.S)


def _strip_model_prompt_supplement(text: str) -> str:
    """Remove legacy model-added prompt supplements before any further repair."""
    return re.sub(r"【模型镜头补充】.*?(?=【|$)", "", str(text or ""), flags=re.S)


def _section_value(sections: dict[str, str], *names: str) -> str:
    for name in names:
        value = sections.get(name, "").strip()
        if value:
            return value
    return ""


def _stable_prop_context(text: str) -> str:
    """Keep prop identity while removing future lifecycle states from a shot."""
    chunks: list[str] = []
    for chunk in re.split(r"[；;\n]+", _normalize_prompt_text(text)):
        chunk = chunk.strip("。；; ")
        if not chunk:
            continue
        cut_at = len(chunk)
        for cue in (
            "遇水后", "遇水", "泡开", "裂开", "露出", "打开后", "最终", "之后",
            "后期", "后续", "随后", "后来", "掉进", "滑落", "取出",
        ):
            index = chunk.find(cue)
            if index >= 0:
                cut_at = min(cut_at, index)
        stable = chunk[:cut_at].rstrip("，,、 ")
        if stable:
            chunks.append(stable)
    return "；".join(dict.fromkeys(chunks))


def _fixed_context_lines(text: str) -> list[str]:
    """Keep deterministic visual context when a prompt is expanded into beats."""
    sections = _sections(text)
    labels = (
        "项目视觉",
        "视觉媒介",
        "出镜角色与身份锚点",
        "角色状态",
        "环境",
        "镜头语言",
        "光线与色调",
        "绑定道具",
        "关键道具",
    )
    lines: list[str] = []
    for label in labels:
        value = _section_value(sections, label)
        if label == "绑定道具":
            value = _stable_prop_context(value)
        if value:
            lines.append(f"【{label}】{value}。")
    return lines


def _dedupe_sentences(*values: str) -> str:
    seen: set[str] = set()
    parts: list[str] = []
    for value in values:
        for part in re.split(r"[。；;\n]+", value or ""):
            clean = _normalize_prompt_text(part).strip("。；; ，,")
            if not clean:
                continue
            key = re.sub(r"\s+", "", clean)
            if key in seen:
                continue
            seen.add(key)
            parts.append(clean)
    return "，".join(parts)


def _fmt_seconds(value: float) -> str:
    rounded = round(max(0.5, value), 1)
    if abs(rounded - int(rounded)) < 0.01:
        return f"{int(rounded)}s"
    return f"{rounded:.1f}s"


def _fmt_timepoint(value: float) -> str:
    rounded = round(max(0.0, value), 1)
    if abs(rounded - int(rounded)) < 0.01:
        return f"{int(rounded)}s"
    return f"{rounded:.1f}s"


def _scene_for_shot(script: ScriptBreakdown | None, scene_id: int) -> Scene | None:
    if not script:
        return None
    return next((scene for scene in script.scenes if scene.scene_id == scene_id), None)


def _text(scene: Scene | None, original: str = "") -> str:
    original = _strip_binding_prop_section(original)
    if scene is None:
        return original
    dialogue = " ".join(f"{line.character} {line.line} {line.emotion or ''}" for line in scene.dialogue)
    return " ".join([
        original,
        scene.location or "",
        scene.action_description or "",
        scene.camera_direction or "",
        scene.emotion_tone or "",
        dialogue,
    ])


def _contains(text: str, *tokens: str) -> bool:
    lowered = text.casefold()
    return any(token.casefold() in lowered for token in tokens)


def _camera_label(scene: Scene | None, text: str) -> str:
    if _contains(text, "大特写", "extreme close", "macro"):
        return "大特写/插入"
    if _contains(text, "特写", "close-up", "closeup"):
        return "特写/正面缓慢推近"
    if _contains(text, "过肩", "over shoulder", "over-the-shoulder"):
        return "中近景/过肩固定"
    if _contains(text, "全景", "远景", "wide"):
        return "中景/环境带人物"
    return str(getattr(scene, "camera_direction", "") or "中近景/正面固定")


def _visible_character_names(scene: Scene | None, text: str = "", script: ScriptBreakdown | None = None) -> list[str]:
    names: list[str] = []
    if scene is None:
        return names

    if script and script.asset_manifest:
        binding = binding_for_scene(script, scene.scene_id)
        if binding and binding.on_screen_character_ids:
            id_to_name = {item.asset_id: item.name for item in script.asset_manifest.characters}
            names = [id_to_name[item] for item in binding.on_screen_character_ids if item in id_to_name]
            if names:
                return list(dict.fromkeys(names))

    source_text = _text(scene, text)
    dialogue_speakers = {
        str(line.character or "").strip()
        for line in getattr(scene, "dialogue", []) or []
        if str(line.character or "").strip()
    }
    for raw_name in list(getattr(scene, "characters", []) or []):
        name = str(raw_name or "").strip()
        if not name:
            continue
        if name in dialogue_speakers or name in source_text:
            names.append(name)

    if not names:
        fallback = [
            str(raw_name or "").strip()
            for raw_name in list(getattr(scene, "characters", []) or [])
            if str(raw_name or "").strip()
        ]
        if len(fallback) == 1:
            names = fallback
    return list(dict.fromkeys(names))


def _scene_trigger_text(scene: Scene, original: str) -> str:
    original = _strip_model_prompt_supplement(original)
    sections = _sections(original)
    core_intent = _preview_core_intent(original)
    parts = [
        scene.location,
        scene.action_description,
        scene.camera_direction,
        scene.emotion_tone,
        _section_value(sections, "主体状态", "角色状态"),
        _section_value(sections, "镜头语言", "镜头"),
        _section_value(sections, "光线与色调", "灯光与环境"),
        _section_value(sections, "关键道具"),
        _dialogue_text(scene.dialogue),
        core_intent,
    ]
    return " ".join(part for part in parts if part)


def _current_event_text(scene: Scene, original: str) -> str:
    """Return only the current shot evidence used to trigger hard story beats."""
    original = _strip_binding_prop_section(_strip_model_prompt_supplement(original))
    sections = _sections(original)
    core_intent = _preview_core_intent(original)
    structured_parts = [
        _section_value(sections, "主体状态", "角色状态"),
        _section_value(sections, "动作时间轴"),
        _section_value(sections, "关键道具"),
        core_intent,
    ]
    parts = [part for part in structured_parts if part]
    # The compact LLM contract is intentionally allowed to omit details. Keep
    # the canonical action as a same-shot source of truth so deterministic
    # event rules still fire when a model returns a generic subject state.
    canonical_action = _normalize_prompt_text(getattr(scene, "action_description", ""))
    if canonical_action:
        parts.append(canonical_action)
    if parts:
        return " ".join(parts)
    return " ".join(part for part in [original, scene.action_description] if part)


def _has_identity_reveal_event(scene: Scene, event_text: str, visible_names: list[str]) -> bool:
    has_reveal_action = _contains(
        event_text,
        "揭露身份", "身份揭露", "摘下口罩", "摘掉口罩", "取下口罩", "露出真面目", "露出身份",
    )
    # A reveal is only actionable when the current shot actually names the
    # revealed subject.  The previous implementation hard-coded one drama's
    # character name and leaked it into unrelated projects.
    return has_reveal_action and len(visible_names) >= 2


def _has_current_storage_search_event(event_text: str) -> bool:
    return (
        _contains(event_text, "储物柜", "柜缝")
        and _contains(event_text, "躲", "藏", "蹲")
        and _contains(event_text, "手电", "脚步声", "搜寻", "钟声", "午夜")
    )


def _has_current_surveillance_event(event_text: str) -> bool:
    return (
        _contains(event_text, "摄像头", "红光", "布控")
        and _contains(event_text, "电箱", "切断线路", "折叠刀", "线路")
    )


def _has_current_hide_threat_event(event_text: str) -> bool:
    return (
        _contains(event_text, "躲藏", "躲在", "藏身", "藏起来")
        and _contains(event_text, "黑衣人", "逼近", "脚步", "搜寻")
        and _contains(event_text, "钟声", "滴水", "手电", "地下")
    )


def _has_dark_passage_event(event_text: str) -> bool:
    return (
        _contains(event_text, "灯灭", "灯光突然", "全部熄灭", "齿轮", "铜钟下沉", "下沉", "暗道入口", "进入暗道")
        and _contains(event_text, "铜钟", "暗道", "入口", "齿轮")
    )


def _has_current_phone_call_event(event_text: str) -> bool:
    return _has_phone_call_cue(event_text) and (
        _contains(
            event_text,
            "电话那头", "电话内容", "陌生声音", "神秘来电", "打电话", "找到入口", "苍老", "mystery_caller",
        )
        or _has_fire_seal_water_focus(event_text)
    )


def _has_fire_seal_water_focus(event_text: str) -> bool:
    return _contains(event_text, "水面火漆", "火漆印章", "水面", "积水边缘") and _contains(event_text, "火漆")


def _main_subject(scene: Scene | None, names: list[str] | None = None) -> str:
    characters = list(names if names is not None else getattr(scene, "characters", []) or [])
    if len(characters) >= 2:
        return "、".join(characters[:2])
    if characters:
        return characters[0]
    return "画面主体"


def _pov_subject(scene: Scene | None, names: list[str] | None = None) -> str:
    characters = list(names if names is not None else getattr(scene, "characters", []) or [])
    return characters[0] if characters else "画面主体"


def _scene_subject(scene: Scene | None, text: str = "") -> str:
    """Resolve the current shot's visible subject without story-specific names."""
    return _main_subject(scene, _visible_character_names(scene, text) or None)


def _first_named_subject(scene: Scene | None, text: str = "", *, exclude: str = "") -> str:
    names = _visible_character_names(scene, text)
    if exclude:
        names = [name for name in names if name != exclude]
    return names[0] if names else _main_subject(scene)


def _last_named_before(text: str, cue: str, names: list[str]) -> str:
    index = text.find(cue)
    if index < 0:
        return ""
    matches = [(text.rfind(name, 0, index), name) for name in names if name and text.rfind(name, 0, index) >= 0]
    return max(matches, default=(-1, ""))[1]


def _first_named_after(text: str, cue: str, names: list[str]) -> str:
    index = text.find(cue)
    if index < 0:
        return ""
    matches = [(text.find(name, index + len(cue)), name) for name in names if name and text.find(name, index + len(cue)) >= 0]
    return min(matches, default=(10**9, ""))[1]


def _revealed_name(text: str, names: list[str]) -> str:
    """Resolve the name attached to the current identity-reveal action."""
    if not text or not names:
        return ""

    # Prefer an explicit ``...露出<name>...`` phrase.  Search all occurrences
    # because the compact shot summary can mention ``露出真面目`` before the
    # canonical scene action names the revealed character.
    reveal_matches: list[tuple[int, int, str]] = []
    for match in re.finditer(r"露出", text):
        after = text[match.end() : match.end() + 24]
        for name in names:
            offset = after.find(name)
            if offset >= 0:
                reveal_matches.append((match.start(), offset, name))
    if reveal_matches:
        return sorted(reveal_matches, key=lambda item: (item[0], item[1]))[-1][2]

    # For a bare ``<name>露出真面目`` cue the actor immediately before the
    # cue is the only reliable subject available in the current shot.
    return _last_named_before(text, "露出真面目", names)


def _first_context_token(text: str, tokens: tuple[str, ...], fallback: str) -> str:
    for token in tokens:
        if _contains(text, token):
            return token
    return fallback


def _action_clause(text: str, cues: tuple[str, ...]) -> str:
    # Chinese commas delimit the individual action clauses in the canonical
    # scene description.  Prefer the last matching clause so a compact shot
    # summary does not mask the more specific source action appended later.
    chunks = re.split(r"[，,。；;\n]", text or "")
    for chunk in reversed(chunks):
        clean = chunk.strip("，, ")
        if clean and any(cue in clean for cue in cues):
            # Strip incidental adverbs and staging prefixes that would make a
            # stable action cue impossible to match downstream.
            clean = re.sub(r"(?:突然|忽然|猛然|随即|立刻)", "", clean)
            clean = clean.replace("桌面上的", "").replace("桌上的", "")
            return clean
    return ""


def _named_actor_for_event(scene: Scene | None, text: str, cues: tuple[str, ...]) -> str:
    names = _visible_character_names(scene, text)
    for cue in cues:
        actor = _last_named_before(text, cue, names)
        if actor:
            return actor
    return names[0] if names else _main_subject(scene)


def _prop_anchor_label(text: str, labels: list[str] | None = None) -> str:
    """Return one current-shot prop label for continuity wording."""
    for label in labels or []:
        if str(label or "").strip():
            return str(label).strip()
    for token in (
        "手机", "信封", "照片", "地图", "钥匙", "铁门", "门锁", "录音机", "录音笔",
        "磁带", "铁盒", "铜钟", "道具",
    ):
        if _contains(text, token):
            return _prop_label(token, text)
    return "关键道具"


def _prop_phrase(text: str, labels: list[str] | None = None) -> str:
    """Preserve concrete material descriptors when the current shot provides them."""
    if _contains(text, "旧牛皮纸信封"):
        return "旧牛皮纸信封"
    if _contains(text, "牛皮纸") and _contains(text, "信封"):
        return "牛皮纸信封"
    if _contains(text, "火漆印章", "水面火漆"):
        if _contains(text, "水面", "积水"):
            return "暗红色火漆印章"
        return "火漆印章"
    for token in ("信封", "照片", "地图", "钥匙", "录音机", "录音笔", "铁盒", "手机", "门锁", "铁门"):
        if _contains(text, token):
            return token
    if labels:
        return str(labels[0]).strip() or "关键道具"
    return _prop_anchor_label(text)


def _has_envelope_cue(text: str) -> bool:
    """Match a physical letter carrier without treating material as identity."""
    return _contains(text, "信封", "信件")


def _readable_back_text(text: str) -> str:
    """Extract a short back-of-prop text cue without inventing its words."""
    match = re.search(r"(?:背面写着|写着|显示|内容为|标注为)[：:\s]*([^，。；;\n]{1,32})", text or "")
    if match:
        return match.group(1).strip("‘’\"' ")
    return ""


def _dialogue_text(lines: list[DialogueLine]) -> str:
    if not lines:
        return ""
    rendered = []
    for line in lines[:3]:
        emotion = f"（{line.emotion}）" if line.emotion else ""
        rendered.append(f"{line.character}：“{line.line}”{emotion}")
    return " / ".join(rendered)


def _phone_voice_note(scene: Scene, text: str, visible_names: list[str]) -> str:
    """Describe an off-screen phone voice without exposing its speaker."""
    for line in getattr(scene, "dialogue", []) or []:
        if str(line.character or "").strip() in visible_names:
            continue
        emotion = str(line.emotion or "").strip()
        if emotion:
            descriptor = re.split(r"[、，,；;\s]", emotion, maxsplit=1)[0].strip()
            if descriptor:
                return f"电话那头只用{descriptor}声音回应，不出现新人物画面。"
    match = re.search(r"电话那头(?:传来|是)?([^，。；;\n]{1,12}?)声音", text or "")
    if match:
        descriptor = match.group(1).strip(" ：:，, ")
        if descriptor:
            return f"电话那头只用{descriptor}声音回应，不出现新人物画面。"
    return "电话那头只用声音回应，不出现新人物画面。"


def _frame_label_for_characters(scene: Scene | None, names: list[str] | None = None) -> str:
    count = len(list(names if names is not None else getattr(scene, "characters", []) or []))
    if count >= 3:
        return "中景三人/侧面固定"
    if count == 2:
        return "中景双人/侧面固定"
    if count == 1:
        return "中近景单人/正面固定"
    return "中景/侧面固定"


def _dialogue_staging(scene: Scene, dialogue: str, text: str, names: list[str] | None = None) -> str:
    visible_names = list(names if names is not None else getattr(scene, "characters", []) or [])
    label = _frame_label_for_characters(scene, visible_names)
    names = visible_names
    prop_anchor = "信封位置" if _contains(text, "信封") else "关键道具位置"
    if len(names) >= 3:
        return (
            f"{label}，{names[0]}、{names[1]}、{names[2]}的站位和相互距离清楚，"
            f"{prop_anchor}必须可追踪；{dialogue}，表演克制，不新增反打。"
        )
    if len(names) == 2:
        return f"{label}，{dialogue}，两人距离、手势和{prop_anchor}清楚，表演克制。"
    return f"{label}，{dialogue}，口型轻微自然，不夸张表演。"


def _physical_action_description(scene: Scene, subject: str, action: str, text: str) -> str:
    lock_action = _contains(text, "开锁", "撬锁", "钥匙插入", "插入锁孔", "门锁", "锁孔")
    forced_entry = _contains(text, "撞门", "踹门", "破门")
    if lock_action and _contains(text, "铁门", "门锁", "锁孔", "开锁", "撬锁"):
        sensory = "呼吸、脚步、手部开锁动作、钥匙入锁和铁门摩擦声"
    elif forced_entry:
        sensory = "急促呼吸、脚步、撞击声、门板震动和人物起止姿态"
    elif _contains(text, "奔跑", "逃", "追逐", "冲进", "冲向", "小巷"):
        sensory = "急促呼吸、脚步、水花、衣摆甩动和回头确认距离"
    elif _contains(text, "推门", "木门", "玻璃门"):
        sensory = "手推门、门轴声、脚步和人物进入空间的方向"
    elif _contains(text, "扭打", "打斗", "扑向", "砸向"):
        sensory = "撞击、失衡、手臂动作、道具掉落和人物起止姿态"
    else:
        sensory = "呼吸、脚步、手部动作和环境声"
    return f"手持跟拍/中近景动作镜头，{subject}{action}，重点表现{sensory}，动作连续可读。"


def _voiceover_text(scene: Scene | None, text: str) -> str:
    if _contains(text, "心声", "旁白", "独白", "voiceover", "voice-over"):
        return "保留心声/旁白节奏，语气贴合本镜头情绪。"
    if scene and not scene.dialogue and _contains(text, "短信", "信封", "父亲", "失踪", "照片", "报纸"):
        speaker = (scene.characters or ["角色"])[0]
        return f"心声({speaker})：用紧张克制的语气补足剧情信息，不要让口型夸张。"
    return ""


def _clean_action(text: str) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text.rstrip("。；;") or "保持当前表演状态，完成本镜头的主要动作"


def _placeholder_only_timeline(value: str) -> bool:
    """Identify a compact visual response that contains no authored beat."""
    clean = _normalize_prompt_text(value).strip("。；; ")
    if not clean or re.search(r"[；;。\n]", clean):
        return False
    return any(
        marker in clean
        for marker in (
            "保持动作起点清楚",
            "完成主要动作",
            "完成主要表演变化",
            "保持当前状态",
            "动作连贯可读",
            "动作自然连贯",
        )
    )


def _has_complex_edit_need(text: str, scene: Scene | None) -> bool:
    text = _text(scene, text)
    if has_planned_editing(text) and "【动作时间轴】" not in text:
        return True
    if scene and len(getattr(scene, "characters", []) or []) >= 2 and getattr(scene, "dialogue", []):
        return True
    return _contains(
        text,
        "闪回", "旧报纸", "旧信", "照片背面", "回头", "街道尽头", "红绿灯",
        "对峙", "把信给我", "不要交给你", "追逐", "奔跑", "打斗", "扭打",
        "摄像头", "红光", "布控", "储物柜", "手电", "躲藏", "黑衣人", "逼近", "钟声", "录音机", "磁带",
        "摘下口罩", "露出真面目", "火漆", "泡开", "铁盒", "铜钟", "暗道",
        "齿轮", "下沉", "灯灭", "进入暗道", "电话那头", "打电话", "拨号", "拨通", "通话", "接电话", "来电", "苍老",
        "大特写", "插入", "反打", "快速摇摄", "切镜头", "切到", "切回",
    )


def _timeline_from_text(timeline: str, total: float) -> list[tuple[float, str]]:
    timeline = _normalize_prompt_text(timeline)
    matches = list(re.finditer(
        r"(\d+(?:\.\d+)?)\s*[-到]\s*(\d+(?:\.\d+)?)\s*(?:s|秒)?[：:，,、\s]*(.*?)(?=(?:；|;|\n|$|\d+(?:\.\d+)?\s*[-到]\s*\d))",
        timeline,
    ))
    segments: list[tuple[float, str]] = []
    previous_end = 0.0
    for match in matches[:4]:
        start = float(match.group(1))
        end = float(match.group(2))
        if abs(start - previous_end) > 0.25:
            start = previous_end
        duration = max(0.8, min(total - previous_end, end - start))
        description = _normalize_prompt_text(match.group(3)).strip("。；; ，,")
        if duration > 0 and description:
            segments.append((duration, description))
            previous_end = round(previous_end + duration, 1)
    if segments and previous_end < total - 0.4:
        if len(segments) > 1 and previous_end > 0:
            scale = total / previous_end
            scaled = [(round(max(0.8, duration * scale), 1), description) for duration, description in segments]
            diff = round(total - sum(duration for duration, _ in scaled), 1)
            duration, description = scaled[-1]
            scaled[-1] = (round(max(0.8, duration + diff), 1), description)
            return scaled
        duration, description = segments[-1]
        segments[-1] = (round(duration + total - previous_end, 1), description)
    return segments


def _single_take_segments(scene: Scene, original: str, total: float, script: ScriptBreakdown | None = None) -> list[str]:
    sections = _sections(original)
    visible_names = _visible_character_names(scene, original, script)
    subject = _main_subject(scene, visible_names)
    subject_state = _section_value(sections, "主体状态", "角色状态") or scene.action_description
    # The compact visual contract may return a generic state such as
    # "保持动作起点清楚".  Keep the canonical scene action alongside it so
    # new topics (lab work, medical procedures, classroom actions, etc.) do
    # not lose their concrete verbs and objects during timed expansion.
    canonical_action = _normalize_prompt_text(getattr(scene, "action_description", ""))
    if canonical_action and canonical_action not in subject_state:
        subject_state = _dedupe_sentences(subject_state, canonical_action)
    camera = _section_value(sections, "镜头语言", "镜头") or scene.camera_direction or "中近景缓慢推近"
    lighting = _section_value(sections, "光线与色调", "灯光与环境") or scene.emotion_tone
    prop_state = _section_value(sections, "关键道具")
    timeline = _section_value(sections, "动作时间轴")
    parsed = _timeline_from_text(timeline, total)
    if not parsed:
        parsed = [
            (round(total * 0.30, 1), f"{subject_state}，保持动作起点清楚。"),
            (round(total * 0.45, 1), f"{camera}，{subject}完成主要表演变化，动作缓慢可读。"),
            (
                round(total * 0.25, 1),
                f"{subject}对上一动作结果产生明确反应，手部、视线或表情只发生一个可见变化，{prop_state or '服装和道具状态保持稳定'}。",
            ),
        ]
        diff = round(total - sum(duration for duration, _ in parsed), 1)
        parsed[-1] = (max(0.8, round(parsed[-1][0] + diff, 1)), parsed[-1][1])
    if _has_sms_visual_cue(original) and not getattr(scene, "dialogue", []) and len(visible_names) <= 1:
        parsed[0] = (parsed[0][0], _current_prop_insert_description(original, subject=subject))

    segments = []
    elapsed = 0.0
    for duration, description in parsed:
        start = elapsed
        end = round(min(total, elapsed + duration), 1)
        elapsed = end
        detail = _dedupe_sentences(description)
        segments.append(f"{_fmt_timepoint(start)}-{_fmt_timepoint(end)}，{detail}。")
    if lighting:
        segments.append(f"全程保持{lighting}。")
    return segments


def _prop_names(script: ScriptBreakdown | None, scene: Scene | None, text: str) -> list[str]:
    names = [prop.name for prop in props_for_scene(script, scene.scene_id)][:3] if script and scene else []
    # An ordinal title and its concrete noun are usually aliases emitted by
    # the parser, not two visible props.  Keep the noun so a different script
    # with its own envelope/letter still renders one current-shot anchor.
    if any("信封" in str(name) for name in names):
        names = [
            name for name in names
            if not re.search(r"(?:第)?[一二三四五六七八九十百零两0-9]+封(?:旧)?信", str(name))
        ]
    fallback: list[tuple[int, str]] = []
    for keyword in ("手机", "短信", "信封", "信件", "照片", "旧报纸", "报纸", "旧信", "钥匙", "开锁", "门锁", "锁", "铁门", "地图", "录音机", "录音笔"):
        index = text.find(keyword)
        if index >= 0 and keyword not in [item[1] for item in fallback]:
            fallback.append((index, keyword))
    letter_match = re.search(r"(?:第)?[一二三四五六七八九十百零两0-9]+封(?:旧)?信", text or "")
    if letter_match and letter_match.group(0) not in [item[1] for item in fallback]:
        fallback.append((letter_match.start(), letter_match.group(0)))
    return names or [name for _, name in sorted(fallback)[:3]]


def _has_visual_phone_cue(text: str) -> bool:
    return _has_sms_visual_cue(text) or _has_phone_call_cue(text)


def _has_sms_visual_cue(text: str) -> bool:
    return _contains(text, "短信", "短信提示", "收到短信", "看短信", "屏幕短信", "短信内容")


def _has_phone_call_cue(text: str) -> bool:
    return _contains(text, "电话", "拨通", "拨号", "通话", "来电", "接电话", "电话那头")


def _has_generic_phone_screen_cue(text: str) -> bool:
    return _contains(text, "手机", "手机屏幕", "屏幕", "蓝光", "亮屏", "凝视屏幕", "举着手机", "持手机")


def _has_explicit_prop_insert(text: str) -> bool:
    return _contains(
        text,
        "大特写", "插入", "特写", "清楚可见", "屏幕短信", "照片背面", "信封背面", "背字", "地图标注", "写着", "不要交给",
    )


def _has_emphasis_prop_cue(text: str) -> bool:
    return _contains(
        text,
        "信封", "手机", "短信", "照片", "地图", "钥匙", "开锁", "门锁", "锁", "铁门", "录音机", "录音笔", "火漆",
    )


def _prop_state_change_description(text: str, labels: list[str], subject: str = "人物") -> str:
    label_text = "、".join(labels[:3]) if labels else "关键道具"
    if _has_sms_visual_cue(text):
        prop = _prop_phrase(text, labels)
        return (
            f"大特写/插入缓慢推近，手机屏幕蓝光照亮{subject}手指和眼角，{prop}保持在当前状态，短信内容作为可读信息优先后期叠加，"
            "重点表现手部动作、屏幕光和当前状态；"
            f"{TEXT_OVERLAY_NOTE}"
        )
    if _has_phone_call_cue(text):
        return (
            "大特写/插入缓慢推近，手机拨号或通话界面只表现屏幕光、手指动作和接通状态，"
            "电话内容只用声音表现，不出现新人物画面；"
            f"{TEXT_OVERLAY_NOTE}"
        )
    if _has_generic_phone_screen_cue(text):
        prop = _prop_phrase(text, labels)
        return (
            f"大特写/插入缓慢推近，手机屏幕蓝光照亮{subject}手指和眼角，{prop}保持在当前状态，"
            "重点表现手部动作、屏幕光和当前状态；"
            f"{TEXT_OVERLAY_NOTE}"
        )
    if _contains(text, "不要交给", "背面写着", "照片背面", "信封背面", "背字"):
        text_match = re.search(r"(?:不要交给|背面写着)[^，。；;\n]{0,24}", text)
        overlay = text_match.group(0).strip("，。；; ") if text_match else "背面文字"
        prop_phrase = _prop_phrase(text, labels)
        return (
            f"大特写/插入缓慢推近，{prop_phrase}翻到背面，背面字迹区域清楚可见，"
            f"‘{overlay}’作为可读文字优先后期叠加，只表现手部翻看、纸张纹理和背面字迹，不新增人物；"
            f"{TEXT_OVERLAY_NOTE}"
        )
    envelope_falls_into_water = (
        _has_envelope_cue(text)
        and _contains(text, "掉进", "掉入", "滑落", "落进", "落入")
        and _contains(text, "积水", "水里", "水中")
    ) or (
        _has_envelope_cue(text)
        and _contains(text, "落水", "水中")
        and _contains(text, "露出地图", "露出旧地图", "旧地图", "地图")
    )
    wax_seal_opens = _contains(text, "火漆印章被水泡开", "火漆被水泡开", "火漆泡开", "信封自己裂开")
    if envelope_falls_into_water or wax_seal_opens:
        prop_phrase = _prop_phrase(text, labels)
        seal_phrase = "火漆印章被水泡开，" if _contains(text, "火漆", "印章") else "封口状态发生变化，"
        map_label = "旧地图" if _contains(text, "旧地图") else "地图"
        exposed_phrase = f"露出的{map_label}边缘清楚可见" if _contains(text, "地图") else "湿透的纸纤维和折痕清楚可见"
        return (
            f"大特写/低机位缓慢推近，{prop_phrase}掉在积水边缘，{seal_phrase}"
            f"封口裂开，{exposed_phrase}；"
            f"{TEXT_OVERLAY_NOTE}"
        )
    if _contains(text, "录音机", "磁带", "录音", "播放", "自动启动"):
        recorder = _prop_anchor_label(text, labels)
        color = _first_context_token(text, ("黑色", "红色", "白色"), "当前材质")
        return (
            f"大特写/桌面固定，{color}{recorder}的播放部件开始转动，指示灯亮起，"
            "按键和机身材质清楚，角色的手停在旁边但不要凭空触碰机器；"
            f"{TEXT_OVERLAY_NOTE}"
        )
    if _has_iron_box_reveal_event(text):
        key_label = next((label for label in labels if "钥匙" in label), "钥匙")
        box_label = next((label for label in labels if any(token in label for token in ("铁盒", "盒子", "箱子"))), "盒子")
        extras = [label for label in labels if label not in {key_label, box_label}]
        exposed = "、".join(extras[:3]) or "内部物件"
        return (
            f"大特写/插入缓慢推近，{key_label}插入{box_label}锁孔，{box_label}打开，{exposed}依次露出，"
            "照片与报纸只表现纸张、日期区域和手部反应；"
            f"{TEXT_OVERLAY_NOTE}"
        )
    return (
        f"大特写/插入缓慢推近，{label_text}清楚可见，重点表现手部动作、材质、光线反应和状态变化；"
        f"{TEXT_OVERLAY_NOTE}"
    )


def _has_iron_box_reveal_event(text: str) -> bool:
    return (
        _contains(text, "铁盒") and _contains(text, "打开", "钥匙", "锁孔", "档案", "照片", "录音笔")
        or (_contains(text, "盒子", "箱子") and _contains(text, "打开", "钥匙", "锁孔") )
    )


def _prop_label(name: str, context_text: str = "") -> str:
    """Return a readable prop label without inventing material or plot facts.

    The old implementation turned every ``信封`` into ``牛皮纸信封`` because
    one historical script used that material.  Material is now inherited only
    when the current shot or prop description explicitly declares it.
    """
    raw_name = str(name or "").strip()
    context = str(context_text or "")
    if re.fullmatch(r"(?:第)?[一二三四五六七八九十百零两0-9]+封(?:旧)?信", raw_name):
        if _contains(context, "牛皮纸", "旧牛皮纸"):
            return "牛皮纸信封"
        return "信封"
    if raw_name in {"信封", "信", "信件"} and _contains(context, "牛皮纸", "旧牛皮纸"):
        return "牛皮纸信封"
    aliases = {
        "短信": "手机屏幕提示",
        "信": "信件",
        "开锁": "门锁",
        "锁": "门锁",
    }
    return aliases.get(raw_name, raw_name)


def _prop_labels(names: list[str], context_text: str = "") -> list[str]:
    labels: list[str] = []
    for name in names:
        label = _prop_label(name, context_text)
        if label not in labels:
            labels.append(label)
    return labels


def _has_explicit_flashback(text: str) -> bool:
    return _contains(text, "闪回", "回忆画面", "记忆画面", "旧报纸", "叠加") or bool(
        re.search(r"[一二三四五六七八九十百零两0-9]+封(?:旧)?信", text or "")
    )


def _flashback_focus(text: str, preferred_text: str = "") -> str:
    """Resolve the current shot's flashback object.

    ``shot.prompt`` can contain an old model draft while ``scene.action_description``
    is the saved source of truth. Prefer the latter whenever it names a concrete
    object, then fall back to the prompt only for the trigger itself.
    """
    candidate = preferred_text if preferred_text and _has_explicit_flashback(preferred_text) else text
    if _contains(candidate, "照片背面"):
        return "照片背面"
    letter_match = re.search(r"[一二三四五六七八九十百零两0-9]+封(?:旧)?信", candidate or "")
    if letter_match:
        return letter_match.group(0)
    if _contains(candidate, "旧信"):
        return "旧信"
    if _contains(candidate, "旧报纸", "报纸头条"):
        return "旧报纸头条"
    if _contains(candidate, "照片"):
        return "旧照片"
    if _contains(candidate, "地图"):
        return "地图局部"
    if _contains(candidate, "牛皮纸") and _contains(candidate, "信封"):
        return "牛皮纸信封"
    if _contains(candidate, "信封"):
        return "信封"
    return "当前分镜明确提到的回忆线索物"


def _flashback_description(text: str, subject: str = "人物", preferred_text: str = "") -> str:
    focus = _flashback_focus(text, preferred_text)
    details = {
        "照片背面": "特写/闪回叠加，照片翻到背面，背面字迹区域短促掠过，纸面磨损和手指压痕清楚，whoosh音效，文字内容优先后期叠加。",
        "旧报纸头条": "特写/闪回叠加，旧报纸头条版面一闪而过，泛黄纸张和黑色标题区域清楚，纸张翻动声和whoosh音效，标题文字优先后期叠加。",
        "旧照片": "特写/闪回叠加，旧照片从信封中滑出一瞬，照片边缘磨损和反光清楚，whoosh音效，画面短促不拖长。",
        "地图局部": "特写/闪回叠加，地图局部标记区域短促掠过，纸面折痕和手绘标注位置清楚，whoosh音效，标注文字优先后期叠加。",
        "牛皮纸信封": "特写/闪回叠加，牛皮纸信封封口和火漆印章一闪而过，受潮边角和纸张纹理清楚，whoosh音效，画面短促不拖长。",
        "信封": "特写/闪回叠加，信封封口和纸张纹理短促掠过，手指压痕与折痕清楚，whoosh音效，画面短促不拖长。",
        "旧信": "特写/闪回叠加，旧信纸张和折痕短促掠过，手部翻阅动作清楚，whoosh音效，画面短促不拖长。",
    }
    if re.fullmatch(r"[一二三四五六七八九十百零两0-9]+封(?:旧)?信", focus):
        return (
            f"特写/闪回叠加，{focus}摊开在桌面，纸张快速翻动，纸张边角和折痕清楚，"
            "纸张翻动声和whoosh音效，画面短促不拖长。"
        )
    fallback = f"特写/闪回叠加，{subject}关联的当前分镜回忆线索物短促掠过，材质和手部动作清楚，whoosh音效，画面短促不拖长。"
    return details.get(focus, fallback)


def _scene_source_text(scene: Scene | None) -> str:
    if scene is None:
        return ""
    dialogue = " ".join(f"{line.character} {line.line} {line.emotion or ''}" for line in scene.dialogue)
    return " ".join([
        scene.location or "",
        scene.action_description or "",
        scene.camera_direction or "",
        scene.emotion_tone or "",
        dialogue,
    ])


def _core_intent_from_finalized(text: str) -> str:
    match = re.search(r"核心分镜意图[:：](.*?)(?=\n\d|\n切镜头|\n【连续锁定】|$)", text, re.S)
    return _preview_core_intent(match.group(1)) if match else ""


def _repair_dual_point_of_view(text: str, scene: Scene | None) -> str:
    pov = _pov_subject(scene)
    return re.sub(r"模拟[^，。；\n]*、[^，。；\n]*视角", f"模拟{pov}视角", text)


def _repair_stale_prop_insert(line: str, source_text: str, scene: Scene | None = None) -> str:
    if "大特写/插入缓慢推近" not in line or _has_explicit_prop_insert(source_text):
        return line
    props_match = re.search(r"(?:切镜头，)?(?:\d+(?:\.\d+)?s，)?大特写/插入缓慢推近，(.*?)清楚可见", line)
    props_part = props_match.group(1) if props_match else ""
    labels = _prop_labels(
        [part.strip() for part in re.split(r"[、,，]", props_part) if part.strip()],
        source_text,
    )
    if not labels:
        labels = ["关键道具"]
    prefix = "切镜头，" if line.startswith("切镜头，") else ""
    duration_match = re.search(r"(\d+(?:\.\d+)?s)，", line)
    duration = f"{duration_match.group(1)}，" if duration_match else ""
    subject = _scene_subject(scene, source_text)
    return (
        f"{prefix}{duration}短促道具强调特写，{'、'.join(labels[:2])}保持在原位置，"
        f"{subject}只强调手部动作、材质和光线反应，不新增道具或文字内容。"
    )


def _repair_stale_flashback(line: str, source_text: str, scene: Scene | None = None) -> str:
    if not _contains(line, "闪回", "旧报纸", "旧信", "记忆画面", "whoosh"):
        return line
    # The core-intent line may mention a flashback as context, but it is not a
    # timed segment and must remain intact.  Only repair an actual timeline
    # entry.
    if not re.match(r"^(?:切镜头，)?\d+(?:\.\d+)?s[，,]", line.strip()):
        return line
    subject = _scene_subject(scene, source_text)
    if _contains(line, "照片翻到背面", "背面字迹") and _contains(source_text, "抽出", "查看背面", "照片背面"):
        prefix = "切镜头，" if line.startswith("切镜头，") else ""
        duration_match = re.search(r"(\d+(?:\.\d+)?s)，", line)
        duration = f"{duration_match.group(1)}，" if duration_match else ""
        prop_phrase = _prop_phrase(source_text)
        return (
            f"{prefix}{duration}大特写/手部固定，{subject}从{prop_phrase}中抽出照片，"
            "翻到背面查看字迹区域，纸面磨损、手指压痕和信封边缘清楚；文字内容优先后期叠加，不做闪回。"
        )
    if not _has_explicit_flashback(source_text):
        return ""
    prefix = "切镜头，" if line.startswith("切镜头，") else ""
    duration_match = re.search(r"(\d+(?:\.\d+)?s)，", line)
    duration = f"{duration_match.group(1)}，" if duration_match else ""
    scene_action = str(getattr(scene, "action_description", "") or "").strip()
    flashback_source = scene_action if _has_explicit_flashback(scene_action) else source_text
    return f"{prefix}{duration}{_flashback_description(flashback_source, subject)}"


def _allows_envelope_water_reveal(text: str) -> bool:
    return (
        (
            _has_envelope_cue(text)
            and _contains(text, "掉进", "掉入", "滑落", "落进", "落入")
            and _contains(text, "积水", "水里", "水中")
        )
        or _contains(text, "火漆印章被水泡开", "火漆被水泡开", "火漆泡开", "信封自己裂开")
    )


def _repair_conflicting_envelope_reveal(
    line: str,
    core_intent: str,
    scene: Scene | None = None,
) -> str:
    if not _contains(line, "牛皮纸信封掉在积水边缘", "火漆印章被水泡开", "露出的旧地图边缘"):
        return line
    if _allows_envelope_water_reveal(core_intent):
        return line

    prefix = "切镜头，" if line.startswith("切镜头，") else ""
    duration_match = re.search(r"(\d+(?:\.\d+)?s)，", line)
    duration = f"{duration_match.group(1)}，" if duration_match else ""
    subject = _scene_subject(scene, core_intent)
    prop_phrase = _prop_phrase(core_intent)
    location = str(getattr(scene, "location", "") or "当前场景").strip()
    seal_text = "暗红色火漆印章" if _contains(line, "暗红色") else "火漆印章"
    seal_note = f"{seal_text}完整，" if _contains(line, "火漆", "印章") or _contains(core_intent, "火漆", "印章") else "封口保持当前状态，"
    if prefix:
        return (
            f"{prefix}{duration}近景/正面固定，{subject}仍在{location}，右手手机屏幕蓝光映在脸上，"
            f"左手把{prop_phrase}贴近身体，{seal_note}眼神紧绷，指尖微微发抖。"
        )
    return (
        f"{duration}大特写/低机位缓慢推近，{subject}左手紧握{prop_phrase}，边角被雨水打湿但封口仍闭合，"
        f"{seal_note}右手手机屏幕蓝光照到手指，指尖微微发抖。"
    )


def _timed_line_signature(line: str) -> str:
    clean = re.sub(r"^切镜头，", "", line.strip())
    clean = re.sub(r"^\d+(?:\.\d+)?s[，,]", "", clean)
    clean = clean.replace(TEXT_OVERLAY_NOTE, "")
    clean = re.sub(r"[。；;，,\s]", "", clean)
    return clean


def _repair_duplicate_timed_segments(lines: list[str], scene: Scene | None) -> list[str]:
    repaired: list[str] = []
    previous_signature = ""
    subject = _main_subject(scene)
    seen_envelope_water_reveal = False
    for line in lines:
        is_timed_segment = re.match(r"^(?:切镜头，)?\d+(?:\.\d+)?s[，,]", line.strip()) is not None
        signature = _timed_line_signature(line) if is_timed_segment else ""
        repeated_envelope_water_reveal = (
            is_timed_segment
            and _contains(line, "牛皮纸信封掉在积水边缘", "火漆印章被水泡开", "被水泡开的暗红色火漆印章")
            and seen_envelope_water_reveal
        )
        if is_timed_segment and ((signature and signature == previous_signature) or repeated_envelope_water_reveal):
            prefix = "切镜头，" if line.startswith("切镜头，") else ""
            duration_match = re.search(r"(\d+(?:\.\d+)?s)[，,]", line)
            duration = f"{duration_match.group(1)}，" if duration_match else ""
            line = (
                f"{prefix}{duration}近景/正面固定，{subject}对上一动作结果产生明确反应，"
                "手部、视线和道具位置保持连续，不新增剧情事件。"
            )
            signature = _timed_line_signature(line)
        if is_timed_segment and _contains(line, "牛皮纸信封掉在积水边缘", "火漆印章被水泡开", "被水泡开的暗红色火漆印章"):
            seen_envelope_water_reveal = True
        if is_timed_segment:
            previous_signature = signature
        repaired.append(line)
    return repaired


def _repair_visible_character_count(text: str, scene: Scene | None) -> str:
    if len(getattr(scene, "characters", []) or []) < 3:
        return text
    text = text.replace("中景双人/侧面固定", "中景三人/侧面固定")
    text = text.replace("中景双人固定", "中景三人/侧面固定")
    return text


def _repair_voice_only_sources(line: str, scene: Scene | None) -> str:
    if "电话那头" in line and "不出现新人物画面" not in line:
        line = line.rstrip("。") + "，电话那头只用声音表现，不出现新人物画面。"
    if _contains(line, "录音", "录音机", "磁带") and "不出现新人物画面" not in line:
        line = line.rstrip("。") + "，录音中的人物只用声音表现，不出现新人物画面。"
    return line


def _coverage_addendum(
    scene: Scene | None,
    rendered: str,
    source_text: str,
    script: ScriptBreakdown | None = None,
    *,
    current_event_text: str = "",
) -> str:
    if scene is None:
        return ""

    rendered_text = rendered.casefold()
    prop_source = " ".join(part for part in (current_event_text, source_text) if part)
    candidate_prop_names = _prop_labels(
        _prop_names(script, scene, prop_source),
        prop_source,
    )
    current_scope_has_detail = _contains(
        current_event_text,
        "手机", "短信", "电话", "录音", "磁带", "照片", "地图", "钥匙", "开锁", "门锁",
        "铁门", "铁盒", "铜钟", "暗道", "火漆", "躲藏", "摄像头", "电箱", "奔跑",
        "打斗", "扭打", "回头", "入口", *candidate_prop_names,
    )
    event_scope = current_event_text if current_scope_has_detail else source_text
    additions: list[str] = []
    visible_names = _visible_character_names(scene, source_text, script)
    prop_anchor = _prop_anchor_label(event_scope, candidate_prop_names)
    if visible_names:
        if len(visible_names) >= 3:
            if not all(name and name in rendered_text for name in visible_names[:3]):
                additions.append(
                    f"中景三人/侧面固定，{visible_names[0]}、{visible_names[1]}、{visible_names[2]}的站位和相互距离清楚，"
                    f"{prop_anchor}位置必须可追踪。"
                )
        elif len(visible_names) == 2:
            if not all(name and name in rendered_text for name in visible_names[:2]):
                additions.append(
                    f"中景双人/侧面固定，{visible_names[0]}、{visible_names[1]}的站位、手势和{prop_anchor}位置清楚。"
                )
        else:
            if visible_names[0] not in rendered_text:
                additions.append(
                    f"中近景单人/正面固定，{visible_names[0]}的表演、视线和手部动作清楚。"
                )

    phone_sms_cue = _has_sms_visual_cue(event_scope)
    if phone_sms_cue and not _contains(
        rendered_text,
        "手机屏幕蓝光",
        "短信内容作为可读信息优先后期叠加",
        "短信提示音",
    ):
        additions.append(
            "大特写/插入，手机屏幕蓝光照亮手指，短信内容作为可读信息优先后期叠加。"
        )

    if _has_phone_call_cue(event_scope) and not _contains(
        rendered_text,
        "手机拨号",
        "通话界面",
        "电话那头只用声音表现",
        "电话内容只用声音表现",
    ):
        additions.append("手机拨号或通话界面只表现屏幕光和手指动作，电话内容只用声音表现，不出现新人物画面。")

    readable_back_text = _readable_back_text(event_scope)
    if _contains(event_scope, "照片背面", "信封背面", "背字", "背面写着") and not _contains(
        rendered_text,
        readable_back_text or "背面字迹区域",
    ):
        readable = readable_back_text or "背面文字"
        additions.append(
            f"大特写/插入，{prop_anchor}或照片背面字迹区域清楚可见，‘{readable}’作为可读文字优先后期叠加，只表现手部翻看和纸张纹理，不新增人物。"
        )

    if _contains(event_scope, "录音机", "磁带", "录音", "播放", "自动启动", "电话那头") and not _contains(
        rendered_text,
        "录音中的人物只用声音表现",
        "电话那头只用声音表现",
        "不出现新人物画面",
    ):
        if _contains(event_scope, "电话那头", "电话", "拨号", "拨通", "通话"):
            additions.append("电话只用声音表现，不出现新人物画面。")
        elif _contains(event_scope, "录音", "录音机", "磁带", "播放", "自动启动"):
            additions.append("录音中的人物只用声音表现，不出现新人物画面。")

    prop_names = candidate_prop_names
    missing_prop_names = [name for name in prop_names if name and name.casefold() not in rendered_text]
    generic_missing_prop_names = [
        name for name in missing_prop_names
        if name not in {"手机", "短信", "录音机", "录音笔", "电话"}
    ]
    if generic_missing_prop_names:
        additions.append(
            f"大特写/插入缓慢推近，{'、'.join(generic_missing_prop_names[:3])}保持清楚可见，"
            "重点表现手部动作、材质、光线反应和状态变化。"
        )

    if _has_iron_box_reveal_event(event_scope):
        key_label = next((name for name in prop_names if "钥匙" in name), "钥匙")
        box_label = next((name for name in prop_names if any(token in name for token in ("铁盒", "盒子", "箱子"))), "盒子")
        exposed = [name for name in prop_names if name not in {key_label, box_label}]
        if not exposed:
            exposed = [token for token in ("档案", "录音笔", "照片") if _contains(event_scope, token)]
        if not _contains(rendered_text, box_label, *exposed):
            additions.append(f"{key_label}打开{box_label}，{('、'.join(exposed[:3]) or '内部物件')}清楚露出。")

    if _contains(event_scope, "回头", "街道尽头", "红绿灯", "追逐", "逃", "身后") and not _contains(
        rendered_text,
        "回头",
        "摇摄",
        "反应镜头",
    ):
        additions.append("快速反应镜头或回头看向动静来源，保持视线和动作连续。")

    if not additions:
        return ""
    return "【覆盖补强】" + "；".join(dict.fromkeys(additions))


def _repair_context_sound(line: str, source_text: str) -> str:
    if "铁门摩擦声" not in line:
        return line
    if _contains(source_text, "开锁", "撬锁", "钥匙插入", "插入锁孔", "门锁", "锁孔"):
        return line
    return line.replace("铁门摩擦声", "环境声")


def _repair_false_reaction_pan(line: str, source_text: str, scene: Scene | None = None) -> str:
    if "模拟" not in line or "回头看向动静来源" not in line:
        return line
    if _has_reaction_pan_cue(source_text):
        return line
    prefix = "切镜头，" if line.startswith("切镜头，") else ""
    duration_match = re.search(r"(\d+(?:\.\d+)?s)[，,]", line)
    duration = f"{duration_match.group(1)}，" if duration_match else ""
    subject = _scene_subject(scene, source_text)
    prop_phrase = _prop_phrase(source_text)
    location = str(getattr(scene, "location", "") or "当前环境").strip()
    if _contains(source_text, "抬眸", "望向雨街", "坚定"):
        return (
            f"{prefix}{duration}近景/正面固定，{subject}把照片放回{prop_phrase}后抬眸望向{location}，"
            "视线从手中道具移到远处雨幕，神情由不安转为坚定，雨声和呼吸声压低。"
        )
    return (
        f"{prefix}{duration}近景/正面固定，{subject}对当前信息产生克制反应，"
        "手部、视线和道具位置保持连续，不新增动静来源。"
    )


def _is_prop_insert_line(line: str) -> bool:
    return _contains(line, "大特写", "插入", "特写", "道具", "清楚可见", "材质", "屏幕", "信封", "火漆")


def _repair_stale_event_line(
    line: str,
    scene_text: str,
    source_text: str = "",
    scene: Scene | None = None,
) -> str:
    if not re.match(r"^(?:切镜头，)?\d+(?:\.\d+)?s[，,]", line.strip()):
        return line
    stale_groups = [
        ("储物柜", "手电光", "柜缝", "搜寻", "钟声", "滴水"),
        ("摄像头", "红光", "布控", "电箱", "切断线路"),
        ("从阴影里走出", "摘下口罩", "露出真面目", "身份揭露"),
        ("铜钟", "铁盒", "暗道", "下沉", "入口", "齿轮"),
    ]
    single_focus_scene = bool(scene) and len(getattr(scene, "characters", []) or []) <= 1 and not getattr(scene, "dialogue", [])
    forced_stale_tokens = (
        "铜钟",
        "铁盒",
        "暗道",
        "摄像头",
        "红光",
        "布控",
        "电箱",
        "切断线路",
    )
    current_scope = source_text or scene_text
    trusted_source = current_scope
    is_stale = any(
        any(token in line for token in group) and not any(token in current_scope for token in group)
        for group in stale_groups
    ) or (single_focus_scene and any(token in line for token in ("储物柜", "手电光", "柜缝", "搜寻", "躲藏", "滴水")))
    is_stale = is_stale or any(token in line and token not in trusted_source for token in forced_stale_tokens)
    if is_stale and _contains(line, "摘下口罩", "露出真面目", "从阴影里走出") and _contains(
        current_scope, "露出真面目", "揭露身份", "摘下口罩", "身份"
    ):
        is_stale = False
    if is_stale and _contains(line, "扭打", "打斗", "折叠刀", "录音机砸") and _contains(
        current_scope, "打斗", "扭打", "反击", "刀掉", "刀掉落", "砸中", "砸向"
    ):
        is_stale = False
    if is_stale and _contains(line, "铜钟", "下沉", "暗道", "入口") and _has_dark_passage_event(current_scope):
        is_stale = False
    if is_stale and _contains(line, "手机拨号", "电话那头", "拿起手机拨号") and _has_current_phone_call_event(current_scope):
        is_stale = False
    if not is_stale:
        return line

    prefix = "切镜头，" if line.startswith("切镜头，") else ""
    duration_match = re.search(r"(\d+(?:\.\d+)?s)[，,]", line)
    duration = f"{duration_match.group(1)}，" if duration_match else ""
    prop_source = " ".join(part for part in [scene_text, source_text] if part)
    subject = _scene_subject(scene, current_scope)
    if _is_prop_insert_line(line) and (
        _has_sms_visual_cue(prop_source)
        or _has_phone_call_cue(prop_source)
        or _has_envelope_cue(prop_source)
    ):
        return f"{prefix}{duration}{_current_prop_insert_description(prop_source, subject=subject)}"
    return (
        f"{prefix}{duration}近景/正面固定，{subject}对当前信息产生克制反应，"
        "手部、视线和道具位置保持连续，不新增其他地点或后续剧情事件。"
    )


def _current_prop_insert_description(
    source_text: str,
    subject: str = "人物",
    labels: list[str] | None = None,
) -> str:
    prop_phrase = _prop_phrase(source_text, labels)
    if _has_envelope_cue(source_text) and _has_sms_visual_cue(source_text):
        prop_state = "，信封封口保持完整，尚未打开" if _contains(source_text, "尚未打开", "完整") else ""
        camera_prefix = "大特写/低机位缓慢推近" if _contains(source_text, "低机位", "缓慢推近") else "大特写/插入缓慢推近"
        hand_phrase = (
            f"{subject}左手紧握{prop_phrase}，右手手机屏幕蓝光照到手指"
            if _contains(source_text, "左手", "右手")
            else f"{subject}手持{prop_phrase}，手机屏幕蓝光照到手指"
        )
        return (
            f"{camera_prefix}，{hand_phrase}{prop_state}，"
            "短信内容作为可读信息优先后期叠加，重点表现手部动作、纸张材质、屏幕光和当前状态；"
            f"{TEXT_OVERLAY_NOTE}"
        )
    if _has_phone_call_cue(source_text):
        return (
            "大特写/过肩插入，手机拨号或通话界面只表现屏幕光、手指动作和接通状态，"
            "电话内容只用声音表现，不出现新人物画面；"
            f"{TEXT_OVERLAY_NOTE}"
        )
    if _has_envelope_cue(source_text) and _has_generic_phone_screen_cue(source_text):
        prop_state = "，信封封口保持完整，尚未打开" if _contains(source_text, "尚未打开", "完整") else ""
        camera_prefix = "大特写/低机位缓慢推近" if _contains(source_text, "低机位", "缓慢推近") else "大特写/插入缓慢推近"
        hand_phrase = (
            f"{subject}左手紧握{prop_phrase}，右手手机屏幕蓝光照到手指"
            if _contains(source_text, "左手", "右手")
            else f"{subject}手持{prop_phrase}，手机屏幕蓝光照到手指"
        )
        return (
            f"{camera_prefix}，{hand_phrase}{prop_state}，"
            "重点表现手部动作、纸张材质、屏幕光和当前状态；"
            f"{TEXT_OVERLAY_NOTE}"
        )
    if _contains(source_text, "照片背面", "信封背面", "背字", "背面写着"):
        camera_prefix = "大特写/低机位缓慢推近" if _contains(source_text, "低机位", "缓慢推近") else "大特写/插入缓慢推近"
        overlay = _readable_back_text(source_text) or "背面文字"
        return (
            f"{camera_prefix}，{prop_phrase}翻到背面，背面字迹区域清楚可见，"
            f"‘{overlay}’作为可读文字优先后期叠加，只表现手部翻看、纸张纹理和背面字迹，不新增人物；"
            f"{TEXT_OVERLAY_NOTE}"
        )
    if _has_envelope_cue(source_text):
        seal_clause = (
            "封口处的火漆印章完整清楚"
            if _contains(source_text, "火漆", "印章")
            else "封口和纸张状态清楚可见"
        )
        return (
            f"大特写/低机位缓慢推近，{subject}手持{prop_phrase}，{seal_clause}，"
            "重点表现手部动作、纸张材质、光线反应和当前状态；"
            f"{TEXT_OVERLAY_NOTE}"
        )
    if _has_sms_visual_cue(source_text):
        return (
            "大特写/过肩插入，手机屏幕蓝光照亮人物眉眼和手指，短信文字优先后期叠加，"
            "重点表现手部动作、屏幕光和角色反应；"
            f"{TEXT_OVERLAY_NOTE}"
        )
    if _has_generic_phone_screen_cue(source_text):
        return (
            "大特写/过肩插入，手机屏幕蓝光照亮人物眉眼和手指，"
            "重点表现手部动作、屏幕光和角色反应；"
            f"{TEXT_OVERLAY_NOTE}"
        )
    return (
        "近景/正面固定，人物对当前动作结果产生明确反应，手部、视线和道具位置保持连续，"
        "不新增剧情事件。"
    )


def _repair_unmentioned_prop_insert(line: str, source_text: str, scene: Scene | None = None) -> str:
    mismatched_props = ("录音机", "录音笔", "铁盒", "铜钟", "铜钥匙", "旧地图", "地图", "照片")
    content = line.split(TEXT_OVERLAY_NOTE, 1)[0].split("所有可读中文文字优先作为后期叠加", 1)[0]
    if not _contains(content, *mismatched_props):
        return line
    mentioned_bad_prop = next((prop for prop in mismatched_props if prop in content and prop not in source_text), "")
    if not mentioned_bad_prop:
        return line
    if not re.match(r"^(?:切镜头，)?\d+(?:\.\d+)?s[，,]", line.strip()):
        return line

    prefix = "切镜头，" if line.startswith("切镜头，") else ""
    duration_match = re.search(r"(\d+(?:\.\d+)?s)[，,]", line)
    duration = f"{duration_match.group(1)}，" if duration_match else ""
    return f"{prefix}{duration}{_current_prop_insert_description(source_text, subject=_scene_subject(scene, source_text))}"


def _total_duration_from_prompt(text: str) -> float | None:
    match = re.search(r"总时长约(\d+(?:\.\d+)?)s", text or "")
    return float(match.group(1)) if match else None


def _repair_timed_duration_coverage(lines: list[str], total: float | None) -> list[str]:
    if total is None or total <= 0:
        return lines
    timed_indexes: list[int] = []
    durations: list[float] = []
    for index, line in enumerate(lines):
        match = re.match(r"^(切镜头，)?(\d+(?:\.\d+)?)s[，,]", line.strip())
        if match:
            timed_indexes.append(index)
            durations.append(float(match.group(2)))
    if not timed_indexes:
        return lines

    current_total = sum(durations)
    if abs(current_total - total) <= 0.4:
        return lines

    repaired = list(lines)
    if len(timed_indexes) == 1:
        index = timed_indexes[0]
        repaired[index] = re.sub(
            r"^((?:切镜头，)?)(\d+(?:\.\d+)?)s([，,])",
            rf"\g<1>{_fmt_seconds(total)}\g<3>",
            repaired[index],
            count=1,
        )
        return repaired

    if current_total <= 0:
        return lines
    scaled = [round(max(0.8, duration * total / current_total), 1) for duration in durations]
    diff = round(total - sum(scaled), 1)
    scaled[-1] = round(max(0.8, scaled[-1] + diff), 1)
    for index, duration in zip(timed_indexes, scaled):
        repaired[index] = re.sub(
            r"^((?:切镜头，)?)(\d+(?:\.\d+)?)s([，,])",
            rf"\g<1>{_fmt_seconds(duration)}\g<3>",
            repaired[index],
            count=1,
        )
    return repaired


def _lint_and_repair_prompt(text: str, scene: Scene | None) -> str:
    text = re.sub(r"(?m)^时间轴分镜】", "【时间轴分镜】", text)
    text = re.sub(r"(?m)^单镜头时间轴】", "【单镜头时间轴】", text)
    core_intent = _core_intent_from_finalized(text)
    scene_text = _scene_source_text(scene)
    source_text = " ".join([core_intent, scene_text])
    lines: list[str] = []
    for line in text.splitlines():
        line = _repair_conflicting_envelope_reveal(line, source_text, scene)
        line = _repair_stale_event_line(line, scene_text, source_text, scene)
        line = _repair_context_sound(line, source_text)
        line = _repair_voice_only_sources(line, scene)
        line = _repair_false_reaction_pan(line, source_text, scene)
        line = _repair_unmentioned_prop_insert(line, source_text, scene)
        line = _repair_stale_flashback(line, source_text, scene)
        if not line:
            continue
        if "完成情绪落点" in line:
            line = line.replace("完成情绪落点，", "对上一动作结果产生明确反应，")
            line = line.replace("完成情绪落点", "对上一动作结果产生明确反应")
        if "形状、材质、颜色、状态不变" in line:
            line = line.replace(
                "形状、材质、颜色、状态不变；",
                "重点表现手部动作、材质、光线反应和当前状态；",
            )
            line = line.replace(
                "形状、材质、颜色、状态不变",
                "重点表现手部动作、材质、光线反应和当前状态",
            )
        lines.append(line)

    lines = _repair_duplicate_timed_segments(lines, scene)
    lines = _repair_timed_duration_coverage(lines, _total_duration_from_prompt(text))
    repaired = "\n".join(lines)
    repaired = _repair_dual_point_of_view(repaired, scene)
    repaired = _repair_visible_character_count(repaired, scene)
    if scene:
        for name in getattr(scene, "characters", []) or []:
            if name:
                repaired = repaired.replace(f"{name}{name}", name)
    repaired = repaired.replace(
        "不得额外增加镜头、反打、插入物、车辆镜头、新地点、新人物或新道具。",
        "不得额外增加镜头；不得增加未写明的反打、插入物、车辆镜头、新地点、新人物或新道具。",
    )
    repaired = repaired.replace(
        "手机短信、报纸标题、信件文字、照片背字、地图标注等可读中文优先作为后期叠加对象；视频画面只表现屏幕光、纸张、照片、地图、手部动作和角色反应，不强求模型生成准确文字。",
        TEXT_OVERLAY_NOTE,
    )
    repaired = repaired.replace(
        "，重点表现呼吸、脚步、手部操作和铁门摩擦声，动作连续可读。",
        "，重点表现呼吸、脚步、手部动作和环境声，动作连续可读。",
    )
    coverage = _coverage_addendum(scene, repaired, source_text, current_event_text=core_intent)
    if coverage:
        repaired = "\n".join([repaired, coverage])
    return _normalize_prompt_text(repaired)


def _repair_finalized_timed_prompt(text: str, scene: Scene | None) -> str:
    text = re.sub(r"(?m)^时间轴分镜】", "【时间轴分镜】", text)
    text = re.sub(r"(?m)^单镜头时间轴】", "【单镜头时间轴】", text)
    core_intent = _core_intent_from_finalized(text)
    scene_text = _scene_source_text(scene)
    source_text = " ".join([core_intent, scene_text])
    has_stale_artifacts = (
        re.search(r"模拟[^，。；\n]*、[^，。；\n]*视角", text) is not None
        or "不得额外增加镜头、反打、插入物" in text
        or "手机短信、报纸标题、信件文字、照片背字、地图标注" in text
        or "形状、材质、颜色、状态不变" in text
        or "手部操作和铁门摩擦声" in text
        or "完成情绪落点" in text
        or (
            _contains(text, "牛皮纸信封掉在积水边缘", "火漆印章被水泡开", "露出的旧地图边缘")
            and not _allows_envelope_water_reveal(core_intent)
        )
    )
    if not has_stale_artifacts:
        return _lint_and_repair_prompt(text, scene)

    text = _repair_dual_point_of_view(text, scene)
    lines: list[str] = []
    for line in text.splitlines():
        line = _repair_conflicting_envelope_reveal(line, core_intent, scene)
        line = _repair_stale_event_line(line, scene_text, source_text, scene)
        line = _repair_stale_flashback(line, source_text, scene)
        if not line:
            continue
        line = _repair_stale_prop_insert(line, source_text, scene)
        lines.append(line)

    repaired = "\n".join(lines)
    repaired = repaired.replace(
        "不得额外增加镜头、反打、插入物、车辆镜头、新地点、新人物或新道具。",
        "不得额外增加镜头；不得增加未写明的反打、插入物、车辆镜头、新地点、新人物或新道具。",
    )
    repaired = repaired.replace(
        "手机短信、报纸标题、信件文字、照片背字、地图标注等可读中文优先作为后期叠加对象；视频画面只表现屏幕光、纸张、照片、地图、手部动作和角色反应，不强求模型生成准确文字。",
        TEXT_OVERLAY_NOTE,
    )
    repaired = repaired.replace(
        "，重点表现呼吸、脚步、手部操作和铁门摩擦声，动作连续可读。",
        "，重点表现呼吸、脚步、手部动作和环境声，动作连续可读。",
    )
    repaired = repaired.replace(
        "完成情绪落点，",
        "对上一动作结果产生明确反应，",
    )
    repaired = repaired.replace(
        "形状、材质、颜色、状态不变；",
        "重点表现手部动作、材质、光线反应和当前状态；",
    )
    coverage = _coverage_addendum(scene, repaired, source_text, current_event_text=core_intent)
    if coverage:
        repaired = "\n".join([repaired, coverage])
    return _lint_and_repair_prompt(repaired, scene)


def _has_entrance_cue(text: str) -> bool:
    # Exit / chase scenes mention 走出/店门 without an entrance beat; never
    # substitute the generic door-entry narrative for them.
    if _contains(text, "追出", "追上", "塞进", "追出门", "追出店门"):
        return False
    return (
        _contains(text, "推门", "玻璃门", "门铃", "黑伞", "监控", "街对面")
        and not _contains(text, "把信给我", "交出信封", "不要交给你")
    )


def _has_reaction_pan_cue(text: str) -> bool:
    if _contains(text, "藏到身后", "藏在身后", "往身后藏"):
        text = text.replace("藏到身后", "").replace("藏在身后", "").replace("往身后藏", "")
    if _contains(text, "抬眸望向雨街", "望向雨街", "抬眸", "望向雨夜街道"):
        text = text.replace("抬眸望向雨街", "").replace("望向雨街", "").replace("抬眸", "").replace("望向雨夜街道", "")
    return (
        _contains(text, "回头", "街道尽头", "红绿灯", "跟着", "奔跑", "追逐", "逃")
        or (_contains(text, "身后") and _contains(text, "动静", "脚步", "黑影", "有人", "声音"))
    )


def _base_beats(scene: Scene, original: str, script: ScriptBreakdown | None = None) -> list[tuple[str, str]]:
    sections = _sections(original)
    text = _scene_trigger_text(scene, original)
    event_text = _current_event_text(scene, original)
    visible_names = _visible_character_names(scene, original, script)
    subject = _main_subject(scene, visible_names)
    pov_subject = _pov_subject(scene, visible_names)
    action = _clean_action(scene.action_description)
    camera = _camera_label(scene, text)
    props = _prop_names(script, scene, text)
    current_prop_state = _section_value(sections, "关键道具")
    dialogue = _dialogue_text(scene.dialogue)
    voiceover = _voiceover_text(scene, text)
    beats: list[tuple[str, str]] = []
    special_event = False

    if _contains(event_text, "延续", "上一镜", "尾帧"):
        beats.append(("continuity", f"延续上一镜画面状态，{camera}，主光方向、雨势、站位和动作起点保持一致。"))

    if _has_current_surveillance_event(event_text):
        operator = _named_actor_for_event(scene, event_text, ("切断", "电箱", "摄像头"))
        camera_signal = _first_context_token(event_text, ("红色摄像头指示灯", "摄像头红光", "摄像头"), "监控信号")
        tool = _first_context_token(event_text, ("折叠刀", "刀", "工具"), "工具")
        power_box = _first_context_token(event_text, ("墙边电箱", "电箱", "线路"), "线路")
        beats.append((
            "physical_action",
            f"中近景/手持轻微晃动，{subject}{action}，前方拐角处{camera_signal}一闪而过，"
            f"{operator}压低手势示意安静，{tool}和{power_box}位置清楚，动作连续可读。",
        ))
        special_event = True

    if _has_current_storage_search_event(event_text):
        storage = _first_context_token(event_text, ("废弃储物柜", "储物柜", "柜子", "柜缝"), "遮挡物")
        threat = _first_context_token(event_text, ("黑衣人", "搜寻者", "追踪者"), "逼近者")
        sound = _first_context_token(event_text, ("午夜钟声", "钟声", "滴水", "脚步声"), "环境声")
        hand_light = _first_context_token(event_text, ("手电光", "手电", "灯光"), "光线")
        beats.append((
            "physical_action",
            f"中近景/低机位固定，{subject}躲在{storage}后，{sound}清楚，"
            f"{threat}的{hand_light}从柜缝扫过人物眼睛，人物互相示意安静，环境声打断搜寻。",
        ))
        special_event = True

    if _has_current_hide_threat_event(event_text):
        threat = _first_context_token(event_text, ("黑衣人", "搜寻者", "追踪者"), "逼近者")
        sound = _first_context_token(event_text, ("钟声", "滴水", "手电", "脚步"), "环境声")
        prop_anchor = _prop_anchor_label(event_text)
        beats.append((
            "physical_action",
            f"中近景/低机位固定，{subject}压低身体躲藏，{threat}脚步逐渐逼近，远处{sound}打断环境声；"
            f"人物呼吸、视线方向、手部遮掩动作和{prop_anchor}位置保持清楚，不新增新地点或未写明道具。",
        ))
        special_event = True

    if _contains(event_text, "录音机", "自己启动", "自动启动", "磁带转动", "录音继续播放"):
        recorder = _first_context_token(event_text, ("录音机", "录音笔", "磁带", "播放器"), "录音设备")
        beats.append(("prop_insert", _prop_state_change_description(event_text, [recorder], subject)))
        special_event = True

    if _has_identity_reveal_event(scene, event_text, visible_names):
        revealed = _revealed_name(event_text, visible_names) or (visible_names[-1] if visible_names else subject)
        target = visible_names[0] if visible_names else subject
        guard = visible_names[1] if len(visible_names) > 1 else subject
        reveal_action = "摘下口罩" if _contains(event_text, "摘下口罩", "摘掉口罩", "取下口罩") else "完成遮挡解除"
        reveal_clause = re.split(
            r"(?:索要|逼近|信封|第[一二三四五六七八九十百零两]+封信|[；;。])",
            action,
            maxsplit=1,
        )[0].strip() or action
        reveal_dialogue = dialogue or f"{revealed}露出当前镜头确认的真实身份，{target}后退，{guard}保持警戒。"
        beats.append((
            "dialogue_two_shot",
            (
                f"{_frame_label_for_characters(scene, visible_names)}，{reveal_clause}；当前镜头中的遮挡角色{reveal_action}，露出{revealed}的身份；"
                f"{target}后退半步，{guard}保持警戒并与{revealed}形成清晰站位，{reveal_dialogue}。"
                f"当前出镜角色、视线和{_prop_anchor_label(event_text, props)}位置清楚。"
            ),
        ))
        special_event = True

    if _contains(event_text, "扭打", "打斗", "与之打斗", "踢开", "扑向", "砸向", "反击", "折叠刀掉", "刀掉落", "刀掉"):
        fight_clause = _action_clause(event_text, ("冲上前", "扭打", "打斗"))
        counter_clause = _action_clause(event_text, ("抓起", "砸向", "反击"))
        attacker = next((name for name in visible_names if f"{name}冲上前" in event_text), "")
        defender = next((name for name in visible_names if attacker and f"{attacker}冲上前和{name}" in event_text), "")
        attacker = attacker or _last_named_before(event_text, "扭打", visible_names)
        defender = defender or _first_named_after(event_text, "和", visible_names)
        if not defender or defender == attacker:
            defender = next((name for name in visible_names if name != attacker), subject)
        holder = _last_named_before(event_text, "抓起", visible_names) or subject
        weapon = _first_context_token(event_text, ("折叠刀", "刀", "录音机", "录音笔"), "道具")
        impact = _first_context_token(event_text, ("砸向", "砸中", "反击"), "反击")
        beats.append((
            "physical_action",
            f"中近景/手持轻微晃动，{fight_clause or f'{attacker}与{defender}发生冲突'}，"
            f"{counter_clause or f'{holder}用{weapon}{impact}'}，动作起止清楚。",
        ))
        special_event = True

    envelope_falls_into_water = (
        _has_envelope_cue(event_text)
        and _contains(event_text, "掉进", "掉入", "滑落", "落进", "落入")
        and _contains(event_text, "积水", "水里", "水中")
    ) or (
        _has_envelope_cue(event_text)
        and _contains(event_text, "落水", "水中")
        and _contains(event_text, "露出地图", "露出旧地图", "旧地图", "地图")
    )
    wax_seal_opens = _contains(event_text, "火漆印章被水泡开", "火漆被水泡开", "火漆泡开", "信封自己裂开")
    if envelope_falls_into_water or wax_seal_opens:
        prop_labels = _prop_labels(_prop_names(script, scene, event_text), event_text)
        beats.append(("prop_insert", _prop_state_change_description(event_text, prop_labels, subject)))
        special_event = True

    if _has_iron_box_reveal_event(event_text):
        actor = _named_actor_for_event(scene, event_text, ("打开", "钥匙", "锁孔"))
        key_label = _first_context_token(event_text, ("生锈铜钥匙", "铜钥匙", "钥匙"), "钥匙")
        box_label = _first_context_token(event_text, ("铁盒", "盒子", "箱子"), "盒子")
        extra_label = _first_context_token(event_text, ("档案", "录音笔", "新照片", "照片"), "内部物件")
        beats.append((
            "physical_action",
            f"中景/侧面固定，{actor}完成主要动作，用{key_label}打开{box_label}，"
            f"{extra_label}露出，角色与周围固定陈设的空间关系清楚。",
        ))
        beats.append(("prop_insert", _prop_state_change_description(event_text, [key_label, box_label, extra_label], actor)))
        special_event = True

    if _has_dark_passage_event(event_text):
        structure = _first_context_token(event_text, ("铜钟", "闸门", "暗门", "门"), "固定结构")
        passage = _first_context_token(event_text, ("暗道", "通道", "入口"), "通道")
        entering = "、".join(visible_names[:2]) or subject
        lighting_event = _first_context_token(event_text, ("灯光突然全部熄灭", "灯灭", "灯光"), "环境光线")
        beats.append((
            "physical_action",
            f"中远景/固定，{lighting_event}后短暂闪烁回亮，机械声响起，"
            f"{structure}缓缓下沉或移开，露出通往更深处的{passage}入口，{entering}进入{passage}，站位和动作保持连续。",
        ))
        special_event = True

    if _has_current_phone_call_event(event_text):
        caller = _named_actor_for_event(scene, event_text, ("拨号", "打电话", "拿起手机"))
        visible_dialogue = _dialogue_text([
            line for line in getattr(scene, "dialogue", []) or []
            if str(line.character or "").strip() in visible_names
        ])
        call_line = visible_dialogue or "电话内容只用声音表现，不出现新人物画面。"
        voice_note = _phone_voice_note(scene, event_text, visible_names)
        beats.append((
            "dialogue_close",
            f"中近景/侧面固定，{caller}独自站在原地拿起手机拨号，局部冷光短暂亮起；"
            f"{call_line}{voice_note}",
        ))
        if _allows_envelope_water_reveal(event_text) or _has_fire_seal_water_focus(event_text):
            beats.append((
                "prop_insert",
                f"大特写/缓慢推近，水面上的{_prop_phrase(event_text)}边缘清楚，积水反光与潮湿材质可见，画面停住。",
            ))
        special_event = True

    if special_event:
        if dialogue and not any(kind.startswith("dialogue") for kind, _ in beats):
            beats.append(("dialogue_two_shot", _dialogue_staging(scene, dialogue, text, visible_names)))
        return beats[:5]

    if _has_entrance_cue(event_text):
        actor = _named_actor_for_event(scene, event_text, ("推开", "走出", "进来"))
        companions = [name for name in visible_names if name != actor]
        companion = companions[0] if companions else subject
        venue = _first_context_token(event_text, ("便利店", "商店", "建筑", "房间"), str(scene.location or "场所"))
        door = _first_context_token(event_text, ("玻璃门", "木门", "铁门", "门"), "门")
        umbrella = _first_context_token(event_text, ("黑伞", "雨伞", "伞"), "")
        umbrella_phrase = f"撑着{umbrella}" if umbrella else ""
        prop_anchor = _prop_anchor_label(event_text, props)
        surveillance = "，抬眼看门口监控，再用余光瞥向街对面" if _contains(event_text, "监控", "街对面") else ""
        bell = "门铃轻响，" if _contains(event_text, "门铃", "玻璃门") else ""
        hide_phrase = ""
        if _contains(event_text, "藏到身后", "藏在身后", "往身后藏"):
            hide_phrase = f"{companion}把{prop_anchor}藏到身后，"
        beats.append((
            "physical_action",
            f"中近景/侧面固定，{venue}{door}被推开，{bell}{actor}{umbrella_phrase}走出；"
            f"{hide_phrase}两人短暂对视，{actor}{surveillance}，"
            f"人物站位、视线方向和{prop_anchor}位置清楚。",
        ))
        special_event = True

    if special_event:
        if dialogue and not any(kind.startswith("dialogue") for kind, _ in beats):
            beats.append(("dialogue_two_shot", _dialogue_staging(scene, dialogue, text, visible_names)))
        return beats[:5]

    if _has_sms_visual_cue(text):
        beats.append((
            "phone",
            "中近景/过肩固定，手机屏幕蓝光照亮人物眉眼，短信提示音尖锐短促，手部和手机位置稳定。",
        ))
    elif _has_phone_call_cue(text):
        beats.append((
            "phone",
            "中近景/过肩固定，手机拨号或通话界面投出冷光，接通提示音短促，手部和手机位置稳定；电话内容只用声音表现。",
        ))

    if _contains(event_text, "奔跑", "跑", "逃", "追逐", "冲进", "冲向", "小巷", "开锁", "钥匙", "铁门", "推门", "撞门", "撬锁"):
        beats.append((
            "physical_action",
            _physical_action_description(scene, subject, action, " ".join([event_text, scene.action_description or ""])),
        ))

    if _has_reaction_pan_cue(event_text):
        beats.append((
            "reaction_pan",
            f"快速摇摄或反应镜头，模拟{pov_subject}视角回头看向动静来源，衣服摩擦声和心跳声增强。",
        ))

    prop_labels = _prop_labels(props, text)
    if prop_labels and _has_explicit_prop_insert(text):
        beats.append((
            "prop_insert",
            _prop_state_change_description(text, prop_labels, subject),
        ))
    elif prop_labels and _has_emphasis_prop_cue(text) and len(beats) < 4:
        state_note = f"；当前状态：{current_prop_state}" if current_prop_state else ""
        beats.append((
            "prop_emphasis",
            f"短促道具强调特写，{'、'.join(prop_labels[:2])}保持在原位置{state_note}；只强调手部动作、材质和光线反应，不新增道具或文字内容。",
        ))

    if _has_explicit_flashback(text):
        beats.append((
            "flashback",
            _flashback_description(text, subject, action),
        ))

    if dialogue:
        if len(visible_names) >= 2 or _contains(text, "对峙", "伸出手", "后退", "把信给我", "交给你"):
            beats.append((
                "dialogue_two_shot",
                _dialogue_staging(scene, dialogue, text, visible_names),
            ))
        else:
            beats.append(("dialogue_close", f"中近景/正面固定，{dialogue}，口型轻微自然，不夸张表演。"))

    # Compact visual responses occasionally contain only a placeholder
    # timeline.  Preserve the current scene's concrete action for arbitrary
    # topics instead of returning a dialogue-only shot.  The exact-placeholder
    # guard prevents this fallback from overriding authored multi-beat plans.
    canonical_action = _normalize_prompt_text(getattr(scene, "action_description", ""))
    timeline_contract = _section_value(sections, "动作时间轴")
    if (
        canonical_action
        and _placeholder_only_timeline(timeline_contract)
        and not any(kind == "physical_action" for kind, _ in beats)
    ):
        beats.append((
            "physical_action",
            f"中景连续动作，{canonical_action}，动作起止清楚，保持人物、道具和空间关系连续。",
        ))

    if not beats:
        beats.append(("establish", f"{camera}，固定镜头或缓慢推近，建立{scene.location}中的{subject}。"))
        beats.append(("action", f"中近景，{subject}{action}，动作只做一个主要节拍。"))
    elif not any(kind.startswith("dialogue") for kind, _ in beats):
        beats.append((
            "emotion_landing",
            f"近景/正面固定，{subject}对上一动作结果产生明确反应，手部或视线发生一个可见变化，"
            f"{scene.emotion_tone or '情绪'}从内收推进，不新增剧情事件。",
        ))

    return beats[:5]


def _durations(total: float, count: int) -> list[float]:
    if count <= 1:
        return [total]
    if count == 2:
        ratios = [0.42, 0.58]
    elif count == 3:
        ratios = [0.25, 0.35, 0.40]
    elif count == 4:
        ratios = [0.20, 0.25, 0.22, 0.33]
    else:
        ratios = [0.18, 0.20, 0.18, 0.20, 0.24]
    durations = [max(0.8, round(total * ratio, 1)) for ratio in ratios[:count]]
    diff = round(total - sum(durations), 1)
    durations[-1] = max(0.8, round(durations[-1] + diff, 1))
    return durations


def _requested_duration_for_kind(text: str, kind: str) -> float | None:
    cues = {
        "prop_insert": ("大特写", "插入", "特写", "清楚可见", "写着", "地图标注", "照片背面"),
        "prop_emphasis": ("道具", "信封", "手机", "短信", "照片", "地图", "钥匙", "录音机", "录音笔", "火漆"),
        "flashback": ("闪回", "回忆", "旧报纸", "旧信", "照片背面", "叠加"),
        "reaction_pan": ("回头", "摇摄", "身后", "街道尽头", "红绿灯"),
        "dialogue_two_shot": ("对话", "对峙", "说", "问", "走", "去哪"),
        "dialogue_close": ("对话", "说", "问"),
    }.get(kind, ())
    if not cues:
        return None
    pattern = r"(\d+(?:\.\d+)?)\s*(?:s|秒)[^。；;\n]{0,28}(" + "|".join(re.escape(cue) for cue in cues) + ")"
    match = re.search(pattern, text)
    if not match:
        pattern = r"(" + "|".join(re.escape(cue) for cue in cues) + r")[^。；;\n]{0,28}(\d+(?:\.\d+)?)\s*(?:s|秒)"
        match = re.search(pattern, text)
        if match:
            return float(match.group(2))
        return None
    return float(match.group(1))


def _duration_weight(kind: str, description: str, source_text: str) -> float:
    evidence_reveal = _contains(source_text, "线索", "证据", "发现", "看清", "确认", "写着", "地图标注", "照片背面", "真相")
    urgent_action = _contains(source_text, "奔跑", "逃", "追逐", "开锁", "铁门", "撞门", "追上", "来不及")
    if _contains(description, "长特写", "停留", "读清", "仔细看", "清楚可见"):
        return 1.15
    if kind == "physical_action":
        return 1.45 if urgent_action else 1.10
    if kind == "prop_emphasis":
        return 0.75 if evidence_reveal else 0.50
    if kind == "prop_insert":
        return 1.10 if evidence_reveal else 0.80
    if kind == "flashback":
        return 0.90 if evidence_reveal else 0.65
    if kind == "reaction_pan":
        return 0.85 if urgent_action else 0.70
    if kind in {"dialogue_two_shot", "dialogue_close"}:
        return 1.35
    if kind == "phone":
        return 0.75
    if kind in {"establish", "continuity"}:
        return 0.80
    return 1.0


def _duration_bounds(kind: str, total: float) -> tuple[float, float]:
    if kind == "prop_emphasis":
        return 0.8, min(2.0, total)
    if kind == "prop_insert":
        return 1.0, min(3.2, total)
    if kind == "flashback":
        return 0.8, min(2.4, total)
    if kind == "reaction_pan":
        return 0.8, min(2.2, total)
    if kind == "phone":
        return 1.0, min(2.8, total)
    if kind == "physical_action":
        return 1.8, total
    if kind in {"dialogue_two_shot", "dialogue_close"}:
        return 1.5, total
    return 0.8, total


def _fit_duration(value: float, kind: str, total: float) -> float:
    lower, upper = _duration_bounds(kind, total)
    return round(max(lower, min(upper, value)), 1)


def _enrichment_duration(kind: str, source_text: str, total: float) -> float:
    if _requested_duration_for_kind(source_text, kind) is not None:
        return 0.0
    defaults = {
        "prop_emphasis": 1.2,
        "prop_insert": 2.0,
        "flashback": 1.5,
    }
    return defaults.get(kind, 0.0)


def _elastic_total_duration(base_total: float, beats: list[tuple[str, str]], source_text: str) -> float:
    """Keep the generation duration fixed; use beats only to divide that duration."""
    return base_total


def _sync_duration_targets(
    shot: ShotPrompt,
    script: ScriptBreakdown | None,
    scene: Scene,
    old_total: float,
    new_total: float,
    *,
    sync_script: bool = True,
) -> None:
    if new_total <= old_total + 0.01:
        return
    rounded = int(round(new_total))
    shot.duration_sec = rounded
    if not sync_script:
        return
    previous_scene_duration = int(scene.duration_sec or old_total)
    scene.duration_sec = max(previous_scene_duration, rounded)
    if script:
        current_total = int(script.duration_sec or 0)
        script.duration_sec = max(current_total, sum(int(item.duration_sec or 0) for item in script.scenes))


def _max_beats_for_duration(total: float) -> int:
    if total <= 5.5:
        return 2
    if total <= 8.0:
        return 3
    if total <= 11.0:
        return 4
    return 5


def _fit_beats_to_duration(beats: list[tuple[str, str]], total: float) -> list[tuple[str, str]]:
    limit = _max_beats_for_duration(total)
    if len(beats) <= limit:
        return beats

    trimmed = list(beats)
    removal_priority = [
        "emotion_landing",
        "prop_emphasis",
        "reaction_pan",
        "flashback",
        "continuity",
    ]
    for kind_to_remove in removal_priority:
        while len(trimmed) > limit:
            index = next((idx for idx, (kind, _) in enumerate(trimmed) if kind == kind_to_remove), None)
            if index is None:
                break
            trimmed.pop(index)
        if len(trimmed) <= limit:
            return trimmed
    return trimmed[:limit]


def _weighted_durations(total: float, beats: list[tuple[str, str]], source_text: str) -> list[float]:
    if not beats:
        return []
    requested: list[float | None] = []
    for kind, _ in beats:
        value = _requested_duration_for_kind(source_text, kind)
        requested.append(_fit_duration(value, kind, total) if value is not None else None)

    fixed_total = sum(value for value in requested if value is not None)
    missing = [index for index, value in enumerate(requested) if value is None]
    if not missing:
        scale = total / fixed_total if fixed_total > total and fixed_total > 0 else 1.0
        return [_fit_duration((value or 0.8) * scale, beats[index][0], total) for index, value in enumerate(requested)]

    remaining = max(0.0, total - fixed_total)
    weights = [_duration_weight(beats[index][0], beats[index][1], source_text) for index in missing]
    weight_total = sum(weights) or 1.0
    durations = [0.0] * len(beats)
    for index, value in enumerate(requested):
        if value is not None:
            durations[index] = value
    for index, weight in zip(missing, weights):
        durations[index] = _fit_duration(remaining * weight / weight_total, beats[index][0], total)

    diff = round(total - sum(durations), 1)
    adjustable = next(
        (index for index in reversed(range(len(beats))) if beats[index][0] in {"physical_action", "dialogue_two_shot", "dialogue_close", "action", "emotion_landing"}),
        len(beats) - 1,
    )
    durations[adjustable] = round(max(0.8, durations[adjustable] + diff), 1)
    return durations


def build_timed_shot_prompt(
    shot: ShotPrompt,
    script: ScriptBreakdown | None = None,
    *,
    sync_script_duration: bool = True,
) -> str:
    """Return a Tapnow-style timed prompt without increasing the shot duration."""
    original = _normalize_prompt_text(shot.prompt or "")
    original = _strip_model_prompt_supplement(original)
    original = re.sub(r"(?m)^时间轴分镜】", "【时间轴分镜】", original)
    original = re.sub(r"(?m)^单镜头时间轴】", "【单镜头时间轴】", original)

    scene = _scene_for_shot(script, shot.scene_id)
    if "【单镜头时间轴】" in original or "【时间轴分镜】" in original:
        return _repair_finalized_timed_prompt(original, scene)
    if has_planned_editing(original) and "【动作时间轴】" not in original:
        if scene is not None and "核心分镜意图" in original:
            return _lint_and_repair_prompt(original, scene)
        return original

    if scene is None:
        return original

    total = max(2.0, min(float(shot.duration_sec or scene.duration_sec or 5), 15.0))
    if not _has_complex_edit_need(original, scene):
        sections = _sections(original)
        visual = _section_value(sections, "项目视觉")
        character = _section_value(sections, "出镜角色与身份锚点")
        subject_state = _section_value(sections, "主体状态", "角色状态")
        action_intent = _section_value(sections, "动作时间轴")
        camera_guidance = _section_value(sections, "镜头语言", "镜头")
        environment = _dedupe_sentences(
            _section_value(sections, "环境"),
            scene.location,
        )
        prop_state = _dedupe_sentences(
            _section_value(sections, "绑定道具"),
            _section_value(sections, "关键道具"),
        )
        legacy_note = _section_value(sections, "模型镜头补充")
        simple_intent = _preview_core_intent(subject_state or action_intent or original)
        segments = _single_take_segments(scene, original, total, script)
        locks = [
            f"总时长约{_fmt_seconds(total)}，单镜头连续完成，不切镜头，不插入额外特写或反打。",
            "只表现一个主要动作节拍；禁止新增人物、车辆、黑影、监控画面、回忆画面、新地点或新道具。",
            "人物外貌、服装、发型、道具状态、手机屏幕亮度、主光方向、雨势/天气和场景空间关系保持连续。",
            TEXT_OVERLAY_NOTE,
        ]
        rendered = "\n".join(part for part in [
            "【单镜头时间轴】",
            f"核心分镜意图：{simple_intent}" if simple_intent else "",
            f"视觉风格：{visual}。" if visual else "",
            f"角色锚点：{character}。" if character else "",
            f"主体状态：{subject_state}。" if subject_state else "",
            f"动作意图：{action_intent}。" if action_intent else "",
            f"环境：{environment}。" if environment else "",
            f"镜头：{camera_guidance}。" if camera_guidance else "",
            f"道具锚点：{prop_state}。" if prop_state else "",
            f"模型补充：{legacy_note}。" if legacy_note else "",
            *segments,
            "【连续锁定】",
            *locks,
        ] if part)
        return _lint_and_repair_prompt(rendered, scene)

    source_text = _text(scene, original)
    beats = _fit_beats_to_duration(_base_beats(scene, original, script), total)
    elastic_total = _elastic_total_duration(total, beats, source_text)
    _sync_duration_targets(
        shot,
        script,
        scene,
        total,
        elastic_total,
        sync_script=sync_script_duration,
    )
    total = elastic_total
    beats = _fit_beats_to_duration(beats, total)
    durations = _weighted_durations(total, beats, source_text)
    segments = []
    for index, ((kind, description), duration) in enumerate(zip(beats, durations), start=1):
        prefix = "" if index == 1 else "切镜头，"
        if kind == "flashback":
            prefix = "切镜头，"
        segments.append(f"{prefix}{_fmt_seconds(duration)}，{description}")

    original_line = ""
    sections = _sections(original)
    fixed_context = _fixed_context_lines(original)
    subject_intent = _section_value(sections, "主体状态", "角色状态")
    action_intent = _section_value(sections, "动作时间轴")
    intent_source = subject_intent or action_intent or original.strip()
    preview = _preview_core_intent(intent_source)
    if preview:
        original_line = f"核心分镜意图：{preview}"
    locks = [
        f"总时长约{_fmt_seconds(total)}，严格按上述时间轴执行，只在写明“切镜头”的位置切换。",
        "不得额外增加镜头；不得增加未写明的反打、插入物、车辆镜头、新地点、新人物或新道具。",
        "人物外貌、服装、发型、道具状态、手机屏幕亮度、主光方向、雨势/天气和场景空间关系保持连续。",
        TEXT_OVERLAY_NOTE,
    ]
    rendered = "\n".join(
        part for part in [
            "【时间轴分镜】",
            *fixed_context,
            original_line,
            *segments,
            "【连续锁定】",
            *locks,
        ] if part
    )
    return _lint_and_repair_prompt(rendered, scene)


def apply_timed_prompt_structure(
    prompts: list[ShotPrompt],
    script: ScriptBreakdown | None,
    *,
    protected_scene_ids: set[int] | None = None,
) -> None:
    """Mutate shot prompts in place unless the user supplied an explicit override."""
    protected_scene_ids = protected_scene_ids or set()
    for shot in prompts:
        if shot.scene_id in protected_scene_ids:
            continue
        shot.prompt = build_timed_shot_prompt(shot, script)
