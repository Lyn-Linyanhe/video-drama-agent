"""Generate visual design and Chinese video prompts in one LLM call."""

import asyncio
import time
import random
import re
import hashlib
from src.schemas.script import ScriptBreakdown
from src.schemas.visual import VisualDesign, SceneVisual
from src.schemas.prompt import GenerationPrompts, PromptAssemblyDiagnostic, ShotPrompt
from src.utils.llm import get_llm_client
from src.utils.visual_asset_manifest import (
    binding_for_scene,
    character_profile_missing_fields,
    enrich_character_description,
    location_for_scene,
    on_screen_character_names,
    props_for_scene,
    visual_character_names,
)
from src.services.timed_prompt_composer import apply_timed_prompt_structure
from src.config import DEFAULT_API


def _job_canceled_type():
    from src.services.job_manager import JobCanceled
    return JobCanceled


_ENV_ACTION_CUES = (
    "站在", "出现", "走进", "走入", "踏入", "走近", "走来", "从阴影", "逆光站",
    "脚步声", "推开", "推开门", "蹲", "冲进", "躲", "掏出", "放进", "攥",
    "握", "转身", "指向", "抬起", "停下", "示意",
    "向下走", "向前走", "往前走", "走出", "进入", "离开",
    "怕", "担心", "紧张", "不安", "屏息", "颤抖", "发抖", "浸透", "被雨水",
    "发梢滴落", "从身上滴落", "顺着发梢", "衣角滴落", "从下巴滴落", "从额头滴落",
    "望向", "抬眼", "视线", "目光", "瞥向", "凝视", "盯着", "神色", "神情", "害怕", "心里",
    "走到", "窜过", "泪痕", "泪水",
    "看着", "张望", "遥望", "低头", "抬头", "回头", "撞倒", "被撞倒", "门铃响", "铃响", "门铃轻响",
)


def _environment_clause_is_dynamic(clause: str) -> bool:
    """Detect narrative environment clauses without rejecting static location phrases."""
    text = str(clause or "")
    for cue in _ENV_ACTION_CUES:
        if cue.casefold() not in text.casefold():
            continue
        # ``走到尽头的走廊`` is a spatial noun phrase, not a character action.
        if cue == "走到" and re.search(r"走到(?:了)?(?:[^，；。]*的)?(?:走廊|通道|街道|巷道|楼梯)", text):
            continue
        return True
    return False


def _sanitize_environment_with_drops(env: str) -> tuple[str, list[str]]:
    """Return stable environment clauses and the dynamic facts removed from them."""
    if not env:
        return "", []
    kept: list[str] = []
    dropped: list[str] = []
    seen: set[str] = set()
    seen_dropped: set[str] = set()
    for raw in re.split(r"[。；;，,、\n]+", str(env)):
        clause = re.sub(r"\s+", " ", raw).strip(" ，,、")
        if not clause:
            continue
        if _environment_clause_is_dynamic(clause):
            key = clause.casefold()
            if key not in seen_dropped:
                seen_dropped.add(key)
                dropped.append(clause)
            continue
        key = clause.casefold()
        if key in seen:
            continue
        seen.add(key)
        kept.append(clause)
    return "；".join(kept), dropped


def _sanitize_environment(env: str) -> str:
    """去掉环境文本中的人物动作/在场/交互/心理子句，保留静态环境事实并去重。"""
    return _sanitize_environment_with_drops(env)[0]


def _build_shot_environment_context_with_drops(script, scene_id: int, environment_delta: str) -> tuple[str, list[str]]:
    """Build one shot's environment from reusable location facts and this shot's static delta."""
    scene = next((item for item in getattr(script, "scenes", []) if item.scene_id == scene_id), None)
    location = location_for_scene(script, scene_id)
    canonical = str(getattr(location, "canonical_name", "") or getattr(scene, "location", "") or "").strip()
    stable_environment = str(getattr(location, "environment_description", "") or "").strip()
    raw = "；".join(value for value in (canonical, stable_environment, str(environment_delta or "").strip()) if value)
    clean, dropped = _sanitize_environment_with_drops(raw)
    # A location identity is useful once, but must not be repeated in the
    # environment body when the asset description already contains it.
    clauses = [part for part in clean.split("；") if part]
    if canonical:
        canonical_key = canonical.casefold()
        clauses = [part for part in clauses if part.casefold() != canonical_key]
        has_canonical_context = any(
            canonical_key in part.casefold() for part in clauses
        )
        if not has_canonical_context:
            clauses.insert(0, canonical)
    return "；".join(dict.fromkeys(clauses)), dropped


def build_shot_environment_context(script, scene_id: int, environment_delta: str = "") -> str:
    """Public deterministic environment context builder used by assembly and tests."""
    return _build_shot_environment_context_with_drops(script, scene_id, environment_delta)[0]


_CHARACTER_MUTABLE_STATE_CUES = (
    "后来", "之后", "此前", "先是", "改成", "换成", "换上", "脱下", "剪成", "剃成",
    "摘下", "戴上", "伪装", "伪装成", "湿透", "沾满", "破损", "撕裂", "恢复", "变回",
)
_CHARACTER_STATE_ALTERNATIVES = (
    ("hair_length", ("长发", "中长发"), ("短发",)),
    ("hair_texture", ("卷发", "卷曲"), ("直发", "直发")),
)


def _stable_character_identity_description(description: str) -> str:
    """Keep stable identity facts separate from mutable plot-state appearance."""
    text = re.sub(r"\s+", " ", str(description or "")).strip("。；; ")
    if not text:
        return ""
    clauses = [
        re.sub(r"\s+", " ", clause).strip(" ，,、")
        for clause in re.split(r"[。；;，,、\n]+", text)
        if clause.strip()
    ]
    if not clauses:
        return text
    dropped: list[str] = []
    kept: list[str] = []
    for clause in clauses:
        if any(cue in clause for cue in _CHARACTER_MUTABLE_STATE_CUES):
            dropped.append(clause)
            continue
        kept.append(clause)
    for _family, *alternatives in _CHARACTER_STATE_ALTERNATIVES:
        matched = [
            group for group in alternatives
            if any(any(token in clause for token in group) for clause in clauses)
        ]
        if len(matched) > 1:
            for clause in list(kept):
                if any(any(token in clause for token in group) for group in alternatives):
                    kept.remove(clause)
                    dropped.append(clause)
    if not dropped:
        return description
    if not kept:
        # Do not erase the only available identity when a model returned one
        # mixed sentence; preserve it and make the state precedence explicit.
        return text + "；发型、服装等剧情变化以本镜角色状态为准"
    note = "发型、服装等剧情变化以本镜角色状态为准"
    return "；".join(dict.fromkeys(kept + [note]))


def _current_prop_anchor(name: str, description: str) -> str:
    """Keep reusable prop identity without leaking future plot states into early shots."""
    text = re.sub(r"\s+", " ", str(description or "")).strip("。；; ")
    if not text:
        return name
    # Prop descriptions often contain the whole story lifecycle. A per-shot prompt
    # should keep only stable identity; the current state belongs in prop_state.
    future_cues = (
        "遇水后", "遇水", "泡开", "裂开", "露出", "打开后", "最终", "之后",
        "后期", "后续", "随后", "后来", "掉进", "滑落", "取出",
    )
    cut_at = len(text)
    for cue in future_cues:
        index = text.find(cue)
        if index >= 0:
            cut_at = min(cut_at, index)
    stable = text[:cut_at].rstrip("，,；; 。")
    if not stable:
        stable = text
    return f"{name}：{stable}；本镜头具体位置和状态以【关键道具】为准"


def _visual_text_for_duration(scene, shot) -> str:
    parts = [
        getattr(scene, "location", ""),
        getattr(scene, "action_description", ""),
        getattr(scene, "camera_direction", ""),
        getattr(scene, "emotion_tone", ""),
        " ".join(f"{line.character} {line.line} {line.emotion or ''}" for line in getattr(scene, "dialogue", []) or []),
    ]
    if shot is not None:
        parts.extend([
            getattr(shot, "subject_state", ""),
            getattr(shot, "action_timeline", ""),
            getattr(shot, "camera_motion", ""),
            getattr(shot, "prop_state", ""),
            getattr(shot, "prompt", ""),
        ])
    return " ".join(str(part or "") for part in parts)


def _contains_any(text: str, *tokens: str) -> bool:
    return any(token in text for token in tokens)


def _contains_numbered_letter(text: str) -> bool:
    """Recognize an arbitrary numbered letter without story-specific titles."""
    return bool(re.search(r"[一二三四五六七八九十百零两0-9]+封(?:旧)?信", text or ""))


def _has_extended_narrative_event(text: str) -> bool:
    return _contains_any(
        text,
        "扭打", "打斗", "奔跑", "追逐", "露出真面目", "身份揭露", "摘下口罩",
        "电话那头", "录音播放", "磁带转动", "还活着", "失踪", "真相", "暗道",
    )


def _declared_timeline_duration(text: str) -> float | None:
    ends: list[float] = []
    for match in re.finditer(r"\d+(?:\.\d+)?\s*[-到至]\s*(\d+(?:\.\d+)?)\s*(?:秒|s)?", text or ""):
        ends.append(float(match.group(1)))
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*(?:秒|s)", text or ""):
        ends.append(float(match.group(1)))
    return max(ends) if ends else None


def _declared_timeline_segment_count(text: str) -> int:
    return len(list(re.finditer(r"\d+(?:\.\d+)?\s*[-到至]\s*\d+(?:\.\d+)?\s*(?:秒|s)?", text or "")))


def _dialogue_duration(scene) -> float:
    lines = getattr(scene, "dialogue", []) or []
    if not lines:
        return 0.0
    text_len = sum(len(re.sub(r"[，。！？、,.!?“”\"：:\s]", "", line.line or "")) for line in lines)
    return 1.2 + text_len / 4.2 + len(lines) * 0.55


def _estimated_content_duration(scene, shot) -> float:
    text = _visual_text_for_duration(scene, shot)
    estimate = 4.0
    visible_count = len(getattr(scene, "characters", []) or [])
    if visible_count >= 2:
        estimate += min(2.0, (visible_count - 1) * 0.8)

    dialogue_time = _dialogue_duration(scene)
    if dialogue_time:
        estimate = max(estimate, dialogue_time + 1.4)

    if _contains_any(text, "手机", "短信", "屏幕"):
        estimate += 1.0
    if _contains_any(text, "信封", "火漆") or _contains_numbered_letter(text):
        estimate += 0.9
    if _contains_any(text, "回头", "看向", "抬眼", "震惊", "后退"):
        estimate += 1.0
    if _contains_any(text, "照片背面", "旧报纸", "旧信", "闪回", "地图标注") or _contains_numbered_letter(text):
        estimate += 1.8
    if _contains_any(text, "奔跑", "追逐", "开锁", "铁门", "储物柜", "手电"):
        estimate += 2.0
    if _contains_any(text, "露出真面目", "摘下口罩", "索要", "对峙"):
        estimate += 2.2
    if _contains_any(text, "扭打", "打斗", "扑向", "砸向", "踢开", "手腕"):
        estimate += 4.0
    if _contains_any(text, "掉进积水", "掉入积水", "落水", "火漆印章被水泡开", "露出旧地图"):
        estimate += 2.0
    if _contains_any(text, "电话那头", "录音机", "磁带转动", "还活着", "失踪", "真相", "暗道"):
        estimate += 1.6

    if not dialogue_time and not _has_extended_narrative_event(text):
        estimate = min(estimate, 8.0)
    return max(3.0, min(30.0, estimate))


def _planned_scene_duration(scene, shot) -> int:
    estimate = _estimated_content_duration(scene, shot)
    timeline_duration = _declared_timeline_duration(getattr(shot, "action_timeline", "") if shot else "")
    timeline_segments = _declared_timeline_segment_count(getattr(shot, "action_timeline", "") if shot else "")
    model_duration = float(getattr(shot, "duration_sec", 0) or 0) if shot else 0.0

    if timeline_segments >= 3 and timeline_duration and 1 <= timeline_duration <= 30 and timeline_duration >= estimate - 4.0:
        return int(max(3, min(30, round(timeline_duration))))
    if timeline_duration and 1 <= timeline_duration <= 30 and abs(timeline_duration - estimate) <= 4.0:
        return int(max(3, min(30, round(timeline_duration))))
    if model_duration and 1 <= model_duration <= 30 and abs(model_duration - estimate) <= 2.0:
        return int(max(3, min(30, round(model_duration))))

    current = float(getattr(scene, "duration_sec", 0) or 0)
    if current and abs(current - estimate) <= 1.0:
        return int(max(3, min(30, round(current))))
    return int(max(3, min(30, round(estimate))))


def _compact_visual_text(value: str, limit: int) -> str:
    """Bound repeated LLM context without changing the beginning of a fact."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    tail = min(80, max(24, limit // 6))
    head = max(1, limit - tail - 5)
    return f"{text[:head]} ... {text[-tail:]}"


GENERIC_SHOT_PLACEHOLDERS = (
    "完成本镜头的主要动作",
    "保持动作起点清楚",
    "保持当前动作",
    "执行主要动作",
    "做出相应动作",
    "完成相应动作",
    "画面主体完成动作",
    "按剧情推进",
    "根据剧情推进",
    "相应表演",
    "镜头内容",
)


def _strip_generic_shot_clauses(value: str) -> str:
    """Drop empty model boilerplate while keeping any concrete current-shot text."""
    chunks = re.split(r"([；;。！？!\n])", str(value or ""))
    kept: list[str] = []
    for index in range(0, len(chunks), 2):
        clause = chunks[index].strip()
        separator = chunks[index + 1] if index + 1 < len(chunks) else ""
        if not clause or any(marker in clause for marker in GENERIC_SHOT_PLACEHOLDERS):
            continue
        kept.append(clause + (separator if separator and index + 1 < len(chunks) else ""))
    return "".join(kept).strip("；;。！？!\n ")


def _compact_compare_text(value: str) -> str:
    return re.sub(r"[\s，。；：:、,！？!?（）()\[\]【】\"'‘’“”]", "", str(value or "")).casefold()


def _shot_source_text(scene) -> str:
    dialogue = " ".join(
        f"{getattr(line, 'character', '')} {getattr(line, 'line', '')}"
        for line in getattr(scene, "dialogue", []) or []
    )
    return " ".join(
        str(value or "")
        for value in (
            getattr(scene, "location", ""),
            getattr(scene, "action_description", ""),
            getattr(scene, "camera_direction", ""),
            getattr(scene, "emotion_tone", ""),
            dialogue,
        )
    )


def _stale_shot_phrases(script: ScriptBreakdown, scene_id: int) -> list[str]:
    """Return distinctive facts from other shots that must not leak here."""
    current = next((scene for scene in script.scenes if scene.scene_id == scene_id), None)
    current_text = _compact_compare_text(_shot_source_text(current)) if current else ""
    phrases: set[str] = set()
    for other in script.scenes:
        if other.scene_id == scene_id:
            continue
        candidates = [getattr(other, "location", ""), getattr(other, "action_description", "")]
        candidates.extend(re.split(r"[。；;，,\n]+", getattr(other, "action_description", "") or ""))
        for candidate in candidates:
            candidate = str(candidate or "").strip()
            normalized = _compact_compare_text(candidate)
            # A location label may be short (e.g. "便利店外") but is still a
            # strong cross-shot boundary; action clauses need a little more
            # evidence to avoid removing shared verbs or character names.
            minimum_length = 3 if candidate == getattr(other, "location", "") else 6
            if len(normalized) >= minimum_length and normalized not in current_text:
                phrases.add(normalized)
    return sorted(phrases, key=len, reverse=True)


def _sanitize_shot_output(shot, script: ScriptBreakdown, scene) -> None:
    """Keep compact LLM fields scoped to the current source scene."""
    stale_phrases = _stale_shot_phrases(script, scene.scene_id)
    # The compact visual contract intentionally omits legacy presentation
    # fields such as ``prompt`` and ``key_elements``.  Keep this sanitizer
    # compatible with both schemas instead of assigning undeclared Pydantic
    # attributes to the compact model.
    model_fields = getattr(shot.__class__, "model_fields", {})
    for field in (
        "subject_state",
        "action_timeline",
        "environment_delta",
        "camera_motion",
        "lighting_delta",
        "prop_state",
        "prompt",
    ):
        if field not in model_fields:
            continue
        value = _strip_generic_shot_clauses(getattr(shot, field, ""))
        normalized = _compact_compare_text(value)
        if normalized and any(phrase in normalized for phrase in stale_phrases):
            value = ""
        setattr(shot, field, value)

    if "key_elements" not in model_fields:
        return
    key_elements = []
    for item in getattr(shot, "key_elements", []) or []:
        value = _strip_generic_shot_clauses(item)
        normalized = _compact_compare_text(value)
        if value and not any(phrase in normalized for phrase in stale_phrases):
            key_elements.append(value)
    shot.key_elements = list(dict.fromkeys(key_elements))


_SHOT_DELTA_FIELDS = (
    "subject_state",
    "action_timeline",
    "prop_state",
    "environment_delta",
)
_DYNAMIC_SHOT_CUES = (
    "开始", "启动", "亮起", "闪烁", "熄灭", "转动", "播放", "推开", "打开", "关上",
    "走出", "走进", "走向", "跑", "冲", "停下", "伸手", "拿起", "拿出", "握紧", "握住",
    "抬头", "回头", "后退", "靠近", "拉住", "躲", "摘下", "露出", "扑向", "砸向", "掉落",
    "滑落", "裂开", "下沉", "拨号", "拨通", "聆听", "听见", "按下", "移动", "转身",
    "站起", "倒下", "响起", "升起", "发出",
)


def _shot_delta_clauses(value: str) -> list[str]:
    """Split model deltas into independently removable action clauses."""
    return [
        clause.strip()
        for clause in re.split(r"[；;。！？!\n]+", str(value or ""))
        if clause and clause.strip()
    ]


def _shot_delta_key(value: str) -> str:
    """Normalize a clause while ignoring a model-authored time-range prefix."""
    text = re.sub(
        r"^\s*\d+(?:\.\d+)?\s*(?:[-到至]\s*\d+(?:\.\d+)?)?\s*(?:秒|s)?\s*",
        "",
        str(value or ""),
        flags=re.IGNORECASE,
    )
    return _compact_compare_text(text)


def _is_dynamic_shot_clause(value: str) -> bool:
    return any(cue in str(value or "") for cue in _DYNAMIC_SHOT_CUES)


def _repeated_delta_matches(current_key: str, previous_key: str) -> bool:
    if not current_key or not previous_key:
        return False
    shorter = min(len(current_key), len(previous_key))
    return current_key == previous_key or (
        shorter >= 10 and (current_key in previous_key or previous_key in current_key)
    )


def _dedupe_repeated_shot_deltas(
    output: "_VisualPromptOutput",
    script: ScriptBreakdown,
) -> None:
    """Remove later duplicated model actions unless the current scene supports them.

    The LLM can repeat a plausible action in adjacent shots even when the later
    screenplay paragraph only describes the resulting state.  We keep the first
    occurrence, preserve static identity anchors, and let the deterministic
    assembler fall back to the later scene's own action text.
    """
    by_scene = {candidate.scene_id: candidate for candidate in output.shots}
    seen: list[str] = []
    for scene in script.scenes:
        shot = by_scene.get(scene.scene_id)
        if shot is None:
            continue
        source_text = _compact_compare_text(_shot_source_text(scene))
        for field in _SHOT_DELTA_FIELDS:
            if field not in getattr(shot.__class__, "model_fields", {}):
                continue
            original = str(getattr(shot, field, "") or "")
            clauses = _shot_delta_clauses(original)
            kept: list[str] = []
            for clause in clauses:
                key = _shot_delta_key(clause)
                if len(key) < 10 or not _is_dynamic_shot_clause(clause):
                    kept.append(clause)
                    continue
                # A phrase explicitly supported by this scene is legitimate,
                # even when the preceding shot used the same action.
                if key in source_text:
                    kept.append(clause)
                    seen.append(key)
                    continue
                if any(_repeated_delta_matches(key, previous) for previous in seen):
                    continue
                kept.append(clause)
                seen.append(key)
            setattr(shot, field, "；".join(kept))


def _fit_duration_budget(
    prompts: list[ShotPrompt],
    script: ScriptBreakdown,
    source_scene_durations: dict[int, int],
    source_total: int,
) -> None:
    """Use confirmed script durations as the visual stage's schedule of record."""
    if not prompts or source_total <= 0:
        return
    target_total = max(3 * len(prompts), min(30 * len(prompts), int(source_total)))
    # A generated action timeline may be compressed into the source window, but
    # it must never change the window or the downstream subtitle/audio budget.
    for item in prompts:
        item.duration_sec = max(3, min(30, int(source_scene_durations.get(item.scene_id, item.duration_sec or 3))))
    normalized_total = sum(int(item.duration_sec or 0) for item in prompts)
    if normalized_total > target_total:
        overflow = normalized_total - target_total
        for item in reversed(prompts):
            reducible = max(0, int(item.duration_sec or 0) - 3)
            reduction = min(reducible, overflow)
            item.duration_sec -= reduction
            overflow -= reduction
            if overflow <= 0:
                break
    elif normalized_total < target_total:
        remaining = target_total - normalized_total
        for item in reversed(prompts):
            capacity = max(0, 30 - int(item.duration_sec or 0))
            increase = min(capacity, remaining)
            item.duration_sec += increase
            remaining -= increase
            if remaining <= 0:
                break
    duration_by_scene = {item.scene_id: int(item.duration_sec or 3) for item in prompts}
    for scene in script.scenes:
        if scene.scene_id in duration_by_scene:
            scene.duration_sec = duration_by_scene[scene.scene_id]
    script.duration_sec = target_total

SYSTEM_PROMPT = """你是专业的影视视觉设计师和 AI 视频提示词工程师。你的任务是一次性完成两项工作：

1. **视觉设计**: 为短剧建立统一的视觉风格、色调、角色外貌锚点和场景视觉方案。
2. **视频提示词**: 将视觉设计转化为目标视频 API 可直接使用的中文文本生视频 prompt。

## 当前阶段
视频生成会同时使用项目中的角色、场景和道具参考图。你仍必须把角色锚点、场景锚点、动作时间轴和镜头语言完整写进中文 prompt，使文字约束与参考图相互印证。

## 核心原则

### 角色一致性（极其重要）
- 为每个角色创建详细的中文外貌锚点，供图片和视频模型复用。
- 必须包含：性别、年龄段、身高或体型、脸型、肤色、眼鼻唇、发型、眉形、服装颜色与材质、配饰和标志特征。
- 不要只写“漂亮、英俊、优雅、冷酷”等泛化词；这些只能作为气质补充。
- 服装必须具体到颜色、材质和款式，例如“米白色棉质衬衫、深蓝色羊毛西装外套、墨绿色真丝旗袍配红色发簪”。
- 同一角色在所有场景的 character_appearance 中必须复用同一组核心锚点，不得随意改变脸型、发型、服装主色或标志物。
- 同时提供 global_character_descriptions_cn 字段保存中文版本，供前端展示。

### 视频 Prompt 标准结构
每个 shots[].prompt 必须使用完整中文，并覆盖以下要素：
1. 主体与资产锚点：当前镜头出现的角色、不可变外貌锚点、服装颜色、标志物和绑定道具。
2. 动作时间轴：按 3 秒左右拆分动作推进，如 0-3 秒 / 3-6 秒 / 6-9 秒。
3. 环境：地点、时间、空间层次、道具位置、天气或环境状态。
4. 镜头与运动：景别、机位角度、运镜方式、焦距/景深感。
5. 灯光与氛围：光源方向、色温、色调、情绪氛围。
6. 风格与技术：电影感、竖屏 9:16、高细节、24fps、自然运动模糊。
7. 连续性约束：角色外貌和服装不变、道具材质和状态不变、主光方向一致。

推荐格式：
【主体与资产锚点】...
【0-3秒】...
【3-6秒】...
【6-9秒】...
【镜头】...
【灯光与环境】...
【风格技术】电影感，竖屏9:16，高细节，24fps，自然运动模糊。
【连续性】角色外貌、服装材质、道具状态和主光方向保持一致，禁止场景突变。

### 动作时间轴规则
- 不写跳跃动作：“他拿起杯子”。
- 改写为：“手指伸向杯沿，指尖碰触玻璃杯，掌心握住杯身，再缓慢将杯子从桌面抬起”。
- 5 秒镜头可拆成 0-2s / 2-5s；10 秒镜头可拆成 0-3s / 3-6s / 6-10s。
- 每段时间轴都应包含主体、动作变化、表情或镜头运动。

### 视觉风格指南
- 都市甜宠：柔和暖光、浅景深、克制眼神交流、轻微微笑、手部动作特写。
- 逆袭打脸：低机位、戏剧性明暗对比、果断姿态、证据揭示、自信行走、旁人反应。
- 悬疑反转：冷色调、高反差阴影、线索特写、缓慢推镜、不安静默、物件揭示。
- 古装或穿越：层叠传统服饰、发饰、庭院纵深、灯笼光、薄雾、衣袖自然摆动。
- 家庭伦理：温暖室内光、餐桌距离感、克制表情、旧物特写、长时间沉默。

### Negative Prompt 规则
- negative_prompt 必须使用中文短语，不写否定句。
- 按镜头内容选择高频失效项，不要无意义堆叠。
- 通用可选项: 低清晰度、模糊、噪点、压缩伪影、畸形五官、错误肢体、多余手指、扭曲四肢、重复身体、人物裁切、比例失调、动作抖动、不自然运动、表情僵硬、文字、水印、标志、字幕、签名、人物换脸、服装变化、道具变形、道具消失、场景突变。

### 构图指南（竖屏9:16）
- 人物居中或上三分之二，下方留白用于字幕。
- 特写占画面70%以上，中景50%，远景30%。
- 避免横向宽场面主导画面。

### Seed管理
- 同一主角在不同场景中尽量使用同一 seed 或稳定 seed 规律。
- 不同场景可以用不同 seed 增加画面多样性，但不能破坏角色锚点。"""


COMPACT_SYSTEM_PROMPT = """你是专业的影视视觉设计师和 AI 视频提示词工程师。

你的任务是为已完成结构化的短剧输出紧凑、可验证的视觉差异数据。后端会根据资产清单组装最终发送给视频模型的完整中文提示词。

严格遵守以下输出契约：
- 输出整体视觉风格、色彩方案、全局负面约束，以及实际出镜角色的稳定中文外貌描述。
- 每个 shots[] 只填写该镜头独有的 subject_state、action_timeline、environment_delta、camera_motion、lighting_delta、prop_state、negative_prompt 和 motion_strength。
- shots[].prompt 必须保持空字符串；不要复述角色资产、场景资产、道具资产、画幅或通用技术约束。
- 差异字段保持短小：subject_state、environment_delta、camera_motion、lighting_delta、prop_state 各不超过 80 个汉字，action_timeline 不超过 180 个汉字，negative_prompt 不超过 100 个汉字；每个字段最多两句。
- 不得新增剧本或资产清单中不存在的角色、地点、道具或剧情事件。
- 角色外貌描述必须是可直接交给图片模型的完整稳定锚点，每个实际出镜角色写成约 80-180 个汉字，至少覆盖：性别与年龄段、身高或体型、脸型与肤色、眉眼鼻唇等面部特征、发型与发色、服装款式/主色/材质、配饰和一项可辨识标志。剧本没有明确的外貌字段时，可以依据人物身份、情绪和项目风格补充一套克制且稳定的设计，但不得使用“外貌以剧本为准”“未提供外貌”等占位语，也不得把动作、地点、道具或其他角色的特征写进来。 同一角色在所有镜头的外貌描述必须逐字复用同一组锚点，不得改写、缩写或混入其他角色的特征。每个镜头只描述实际出镜角色，绝不把仅配音角色写入画面。
- 动作时间轴需按镜头时长清晰描述，每个时间戳段落必须同时写出镜头运动与主体动作（禁止只写其一，只写一样模型会自由发挥）；一镜可以推进多个按顺序发生的动作节拍，必须完整保留当前镜头明确的动作链；负面约束使用中文短语并按具体错误品类写（如“人物凭空消失”“道具变形”“服装变色”），不要只写抽象否定。
- 所有字段使用中文，保持同一地点的空间、陈设、时间天气和主光方向连续。
- environment_delta 只能写本镜头静态/持续的环境事实（建筑结构、固定陈设、天气、时间、地形、空间关系、整体光线氛围）。严禁写入人物的动作、情绪、心理状态、对白、人物在场、道具交互或剧情结果。若无静态环境差异，environment_delta 必须留空字符串。 也禁止写入本镜头尚未发生的未来状态或属于后续镜头的事件（例如道具被撞倒、暗道开启等提前出现）。
"""


_PARALLEL_VISUALIZE_CONCURRENCY = 4


def _build_continuity_segment(prev_shot) -> str:
    """Build a deterministic carry-over segment from the previous shot's state fields."""
    prev_state = []
    for label, value in (
        ("主体状态", getattr(prev_shot, "subject_state", "")),
        ("环境", getattr(prev_shot, "environment_delta", "")),
        ("光线", getattr(prev_shot, "lighting_delta", "")),
        ("关键道具", getattr(prev_shot, "prop_state", "")),
    ):
        value = str(value or "").strip()
        if value:
            prev_state.append(f"{label}：{value}")
    if prev_state:
        return f"【顺承上一镜】本镜承接上一镜结尾画面状态，开头保持与上一镜一致——{'；'.join(prev_state)}。"
    return "【顺承上一镜】本镜承接上一镜结尾画面状态，开场保持与上一镜一致的姿态、站位、空间与光线。"



def _merge_visualize_telemetry(target: dict, inc: dict) -> None:
    """Accumulate per-call LLM metrics into a shared telemetry dict (no reset)."""
    if not isinstance(target, dict) or not isinstance(inc, dict):
        return
    for key in (
        "attempts",
        "repair_attempts",
        "input_chars",
        "output_chars",
        "transport_attempts",
        "transport_retries",
    ):
        value = inc.get(key, 0) or 0
        if value:
            target[key] = target.get(key, 0) + value
    if inc.get("fallback_used"):
        target["fallback_used"] = True
    if inc.get("timeout_sec") and not target.get("timeout_sec"):
        target["timeout_sec"] = inc["timeout_sec"]
    for key in ("provider", "model"):
        if inc.get(key) and not target.get(key):
            target[key] = inc[key]


class VisualPromptAgent:
    """Agent that generates visual design and Chinese video prompts in one pass.

    Replaces the two-step VisualDesignAgent → PromptEngineeringAgent pipeline
    with a single LLM call that outputs both the visual specification and the
    corresponding Chinese video generation prompts.
    """

    def __init__(self, api_provider: str | None = None):
        self.llm = get_llm_client()
        self.api_provider = api_provider or DEFAULT_API
        self.character_profile_repair_status = "not_started"

    async def run(
        self,
        script: ScriptBreakdown,
        character_images=None,
        scene_images=None,
        prompt_overrides: dict | None = None,
        creative_profile=None,
        telemetry: dict | None = None,
        progress_callback=None,
        source_text: str = "",
        _repair_character_profiles: bool = True,
    ) -> tuple[VisualDesign, GenerationPrompts]:
        """Generate visual design and API prompts from the script.

        Args:
            script: The script breakdown.
            character_images: Optional CharacterImageAssets for reference.
            scene_images: Optional SceneImageAssets for reference.
            prompt_overrides: Optional dict {shot_index: overridden_prompt}.

        Returns:
            Tuple of (VisualDesign, GenerationPrompts).
        """
        provider_now = getattr(self.llm, "provider", "")
        if provider_now == "kimi" and len(script.scenes) > 1:
            return await self._run_stepwise(
                script,
                prompt_overrides=prompt_overrides,
                creative_profile=creative_profile,
                telemetry=telemetry,
                progress_callback=progress_callback,
                source_text=source_text,
                _repair_character_profiles=_repair_character_profiles,
            )
        if provider_now == "deepseek" and len(script.scenes) > 1:
            return await self._run_parallel_visualize(
                script,
                prompt_overrides=prompt_overrides,
                creative_profile=creative_profile,
                telemetry=telemetry,
                progress_callback=progress_callback,
                source_text=source_text,
                _repair_character_profiles=_repair_character_profiles,
            )
        return await self._run_single_shot(
            script,
            prompt_overrides=prompt_overrides,
            creative_profile=creative_profile,
            telemetry=telemetry,
            source_text=source_text,
            _repair_character_profiles=_repair_character_profiles,
        )

    async def _run_single_shot(
        self,
        script: ScriptBreakdown,
        *,
        prompt_overrides: dict | None,
        creative_profile=None,
        telemetry: dict | None = None,
        source_text: str = "",
        _repair_character_profiles: bool = True,
    ) -> tuple[VisualDesign, GenerationPrompts]:
        """Generate the whole script in a single structured call (original path)."""
        scenes_text = self._format_scenes(script)
        manifest_text = self._format_manifest(script)

        profile = creative_profile
        profile_style = getattr(profile, "art_style", "") or "写实"
        profile_ratio = getattr(profile, "video_ratio", "") or "9:16"
        profile_genres = "、".join(getattr(profile, "genre_tags", []) or []) or script.genre
        user_prompt = f"""请为以下短剧设计视觉方案并生成 {self.api_provider} API 可直接使用的中文视频提示词：

**剧本标题**: {script.title}
**类型**: {script.genre}
**总时长**: {script.duration_sec}秒
**旁白风格**: {script.narration_style}
**项目类型约束**: {profile_genres}
**项目艺术风格约束**: {profile_style}
**项目最终视频画幅**: {profile_ratio}

**场景列表**:
{scenes_text}

**视觉资产绑定（权威约束）**:
{manifest_text}

请一次性输出：
1. 整体视觉风格和色调方案
2. 所有角色的详细外貌描述 (global_character_descriptions)
3. 每个场景的紧凑视觉差异：主体状态、动作时间轴、环境差异、镜头运动、光线差异、关键道具状态、中文 negative_prompt、motion_strength

注意：
- 所有视频 prompt 和 negative_prompt 必须用中文
- 目标 API 是 {self.api_provider}，按中文提示词最佳实践写 prompt
- 同一角色在所有场景中用相同seed，外貌描述完全一致
- 不要输出可由剧本和项目约束直接推导的完整长 prompt；后端会统一组装角色锚点、场景锚点、画幅、技术约束和连续性约束
- negative_prompt 根据场景内容定制，使用中文短语，默认包含低清晰度、模糊、水印、文字、畸形五官、错误肢体、动作抖动、人物换脸换装、道具变形
- overall_style 与 aspect_ratio 必须分别严格等于项目艺术风格和项目最终视频画幅"""
        user_prompt += """
- 不得新增视觉角色或地点。若视觉资产绑定存在，global_character_descriptions 和 global_character_descriptions_cn 只能包含 appearance_mode=on_screen 的角色。
- shots[].scene_id 必须与输入镜头编号一一对应；每个差异字段只能依据同编号镜头段落，禁止引用其他编号镜头的地点、动作、道具或剧情。
- 每个镜头的 character_appearance 只能包含该镜头 on_screen_character_ids 对应的角色；voice_only_speakers 仅可作为声音/对白上下文，绝不可写成画面主体、外貌锚点或参考图对象。
- 每个镜头的环境必须遵循它绑定的 location asset；同一 location 的不同镜头保持建筑、陈设、时间天气和主光方向连续。"""
        user_prompt += """
- 每个镜头必须包含绑定 prop_ids 对应道具。道具是画面资产而非背景泛称：写清它在画面中的位置、形态或材质、与人物的交互和镜头结束状态；未绑定道具不得凭空新增。
- environment_delta 只允许静态环境差异，禁止写入人物动作、情绪、在场或道具交互；没有静态差异时留空。"""

        output = await self.llm.generate_structured(
            system_prompt=COMPACT_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            output_schema=_CompactVisualPromptOutput,
            temperature=0.7,
            telemetry=telemetry,
            phase="script.visualize",
            schema_defaults={"color_palette": ""},
        )
        if _repair_character_profiles and self._registry_needs_profile_repair(
            output.global_character_descriptions_cn,
            script,
        ):
            repaired_en, repaired_cn = await self.reextract_visible_characters(
                script,
                source_text=source_text,
            )
            output.global_character_descriptions = repaired_en
            output.global_character_descriptions_cn = repaired_cn
        return self._split_output(output, script, prompt_overrides, creative_profile)

    async def _generate_global_visual(self, script, manifest_text, profile, telemetry):
        """Phase 1: one call for global visual design + all on-screen character profiles."""
        profile_style = getattr(profile, "art_style", "") or "\u5199\u5b9e"
        profile_ratio = getattr(profile, "video_ratio", "") or "9:16"
        user = f"""\u4ec5\u8f93\u51fa\u5168\u5c40\u89c6\u89c9\u65b9\u6848\u548c\u6240\u6709\u5b9e\u9645\u51fa\u955c\u89d2\u8272\u7684\u5916\u8c8c\uff0c\u4e0d\u8981\u8f93\u51fa\u4efb\u4f55\u955c\u5934\uff08shots\uff09\u3002
**\u5267\u672c\u6807\u9898**: {script.title}
**\u7c7b\u578b**: {script.genre}
**\u603b\u65f6\u957f**: {script.duration_sec}\u79d2
**\u9879\u76ee\u827a\u672f\u98ce\u683c**: {profile_style}
**\u9879\u76ee\u6700\u7ec8\u89c6\u9891\u753b\u5e45**: {profile_ratio}

**\u89c6\u89c9\u8d44\u4ea7\u7ed1\u5b9a\uff08\u6743\u5a01\uff09**:
{manifest_text}

\u5fc5\u987b\u8f93\u51fa 6 \u4e2a\u952e\uff1a
1. overall_style\uff08= \u9879\u76ee\u827a\u672f\u98ce\u683c\uff09
2. color_palette\uff08\u4e2d\u6587\u914d\u8272\uff09
3. aspect_ratio\uff08= \u9879\u76ee\u753b\u5e45\uff09
4. global_negative_prompt\uff08\u4e2d\u6587\u8d1f\u9762\u77ed\u8bed\uff09
5. global_character_descriptions\uff1a\u952e\u4e3a\u89d2\u8272 asset_id\uff0c\u503c\u4e3a\u82f1\u6587\u5916\u8c8c
6. global_character_descriptions_cn\uff1a\u952e\u4e3a\u89d2\u8272 asset_id\uff0c\u503c\u4e3a\u5b8c\u6574\u4e2d\u6587\u5916\u8c8c\u4fa7\u5199\uff08\u7ea680-180\u5b57\uff0c\u542b\u6027\u522b\u5e74\u9f84\u8eab\u9ad8\u8138\u578b\u4e94\u5b98\u53d1\u578b\u670d\u88c5\u914d\u9970\u8fa8\u8bc6\u6807\u5fd7\uff09
\u53ea\u8f93\u51fa\u4e00\u4e2a JSON \u5bf9\u8c61\uff0c\u4e0d\u8981 markdown\u3002"""
        return await self.llm.generate_structured(
            system_prompt=COMPACT_SYSTEM_PROMPT,
            user_prompt=user,
            output_schema=_GlobalVisualOutput,
            temperature=0.7,
            telemetry=telemetry,
            phase="script.visualize.global",
        )

    async def _run_parallel_scene_deltas(self, script, manifest_text, global_out, profile, telemetry=None, progress_callback=None):
        """Phase 2: per-scene compact deltas, run concurrently with bounded retry."""
        global_cn = global_out.global_character_descriptions_cn or {}
        global_cn_text = "\n".join(f"- {k}: {v}" for k, v in global_cn.items()) or ("\u65e0")
        profile_style = getattr(profile, "art_style", "") or "\u5199\u5b9e"
        profile_ratio = getattr(profile, "video_ratio", "") or "9:16"
        sem = asyncio.Semaphore(_PARALLEL_VISUALIZE_CONCURRENCY)
        completed = 0
        total = len(script.scenes)
        if telemetry is not None and "requests" not in telemetry:
            telemetry["requests"] = []

        async def one(scene):
            nonlocal completed
            t0 = time.perf_counter()
            single = script.model_copy(deep=True)
            single.scenes = [scene.model_copy(deep=True)]
            single.duration_sec = scene.duration_sec
            scenes_text = self._format_scenes(single)
            user = f"""\u53ea\u8f93\u51fa\u7b2c {scene.scene_id} \u955c\u7684\u7d27\u51d1\u5dee\u5f02\uff0c\u4e0d\u8981\u8f93\u51fa\u5168\u5c40\u5b57\u6bb5\uff0c\u4e0d\u8981\u590d\u8ff0\u89d2\u8272\u5916\u8c8c/\u573a\u666f\u8d44\u4ea7/\u753b\u5e45\u3002
**\u5267\u672c**: {script.title} | {script.genre} | \u672c\u955c\u65f6\u957f {scene.duration_sec}s
**\u9879\u76ee\u827a\u672f\u98ce\u683c**: {profile_style} | \u753b\u5e45 {profile_ratio}

**\u672c\u955c\u573a\u666f\u4e0e\u955c\u5934**:
{scenes_text}

**\u89c6\u89c9\u8d44\u4ea7\u7ed1\u5b9a**:
{manifest_text}

**\u5df2\u786e\u8ba4\u89d2\u8272\u5916\u8c8c\u951a\u70b9\uff08\u76f4\u63a5\u6cbf\u7528\uff0c\u4e0d\u8981\u6539\u5199\uff09**:
{global_cn_text}

**本镜硬性规则**：每个时间戳段落必须同时写清镜头运动与主体动作，禁止只写其一；environment_delta 只能写本镜头已存在且持续的静态事实，禁止未来状态或后续镜头事件；不得把其他镜头的事件、地点或角色写入本镜。

\u53ea\u8f93\u51fa\u672c\u955c shot \u5dee\u5f02\uff1ascene_id\u3001subject_state\u3001action_timeline\u3001environment_delta(\u4ec5\u9759\u6001\u73af\u5883\u5dee\u5f02\uff0c\u7981\u6b62\u4eba\u7269\u52a8\u4f5c/\u60c5\u7eea/\u5728\u573a)\u3001camera_motion\u3001lighting_delta\u3001prop_state\u3001negative_prompt\u3001motion_strength\u3002\u53ea\u8f93\u51fa\u4e00\u4e2a JSON \u5bf9\u8c61\u3002"""
            metric = {}
            async with sem:
                last = None
                for attempt in (1, 2):
                    try:
                        shot = await self.llm.generate_structured(
                            system_prompt=COMPACT_SYSTEM_PROMPT,
                            user_prompt=user,
                            output_schema=_CompactShotVisualPrompt,
                            temperature=0.7,
                            telemetry=metric,
                            phase="script.visualize.delta",
                        )
                        completed += 1
                        if progress_callback:
                            progress_callback(
                                "visualize_scene",
                                completed,
                                total,
                                f"已完成 {completed}/{total} 镜视觉差异",
                            )
                        if telemetry is not None:
                            telemetry.setdefault("requests", []).append({
                                "stage": "visualize_scene",
                                "scene_id": scene.scene_id,
                                "elapsed_ms": round((time.perf_counter() - t0) * 1000),
                                "attempts": metric.get("attempts", 0),
                                "repair_attempts": metric.get("repair_attempts", 0),
                                "fallback_used": bool(metric.get("fallback_used", False)),
                                "input_chars": metric.get("input_chars", 0),
                                "output_chars": metric.get("output_chars", 0),
                                "transport_attempts": metric.get("transport_attempts", 0),
                                "transport_retries": metric.get("transport_retries", 0),
                                "timeout_sec": metric.get("timeout_sec", 0),
                            })
                        return shot, metric
                    except _job_canceled_type():
                        raise
                    except Exception as exc:
                        last = exc
                raise last

        results = await asyncio.gather(
            *(one(scene) for scene in script.scenes), return_exceptions=True
        )
        failures = [r for r in results if isinstance(r, Exception)]
        if failures:
            raise failures[0]
        results.sort(key=lambda r: r[0].scene_id)
        if telemetry is not None:
            for _shot, metric in results:
                _merge_visualize_telemetry(telemetry, metric)
        return [shot for shot, _metric in results]

    async def _run_parallel_visualize(self, script, *, prompt_overrides, creative_profile, telemetry, source_text, _repair_character_profiles, progress_callback=None):
        """Plan B: two-phase parallel; falls back to single-shot on persistent failure."""
        try:
            manifest_text = self._format_manifest(script)
            profile = creative_profile
            profile_ratio = getattr(profile, "video_ratio", "") or "9:16"
            gmetrics = {}
            g_started = time.perf_counter()
            global_out = await self._generate_global_visual(script, manifest_text, profile, gmetrics)
            if telemetry is not None:
                telemetry.setdefault("requests", []).append({
                    "stage": "visualize_global",
                    "scene_id": 0,
                    "elapsed_ms": round((time.perf_counter() - g_started) * 1000),
                    "attempts": gmetrics.get("attempts", 0),
                    "repair_attempts": gmetrics.get("repair_attempts", 0),
                    "fallback_used": bool(gmetrics.get("fallback_used", False)),
                    "input_chars": gmetrics.get("input_chars", 0),
                    "output_chars": gmetrics.get("output_chars", 0),
                    "transport_attempts": gmetrics.get("transport_attempts", 0),
                    "transport_retries": gmetrics.get("transport_retries", 0),
                    "timeout_sec": gmetrics.get("timeout_sec", 0),
                })
            shots = await self._run_parallel_scene_deltas(script, manifest_text, global_out, profile, telemetry, progress_callback=progress_callback)
            if telemetry is not None:
                _merge_visualize_telemetry(telemetry, gmetrics)
            output = _CompactVisualPromptOutput(
                overall_style=global_out.overall_style,
                color_palette=global_out.color_palette,
                aspect_ratio=global_out.aspect_ratio or profile_ratio,
                global_negative_prompt=global_out.global_negative_prompt,
                global_character_descriptions=global_out.global_character_descriptions or {},
                global_character_descriptions_cn=global_out.global_character_descriptions_cn or {},
                shots=shots,
            )
            if _repair_character_profiles and self._registry_needs_profile_repair(
                output.global_character_descriptions_cn, script
            ):
                repaired_en, repaired_cn = await self.reextract_visible_characters(
                    script, source_text=source_text
                )
                output.global_character_descriptions = repaired_en
                output.global_character_descriptions_cn = repaired_cn
            return self._split_output(output, script, prompt_overrides, creative_profile)
        except _job_canceled_type():
            raise
        except Exception:
            attempted = 0
            if telemetry is not None:
                # Keep the aggregate attempt counter accurate when phase 2 fails
                # partway: sum the per-request records that already happened so
                # the single-shot fallback counter does not hide prior calls.
                attempted = sum(
                    int(req.get("attempts") or 0)
                    for req in (telemetry.get("requests") or [])
                    if isinstance(req, dict)
                )
            # Run the fallback against a dedicated metric dict: generate_structured
            # resets attempts/fallback_used at call start, so sharing telemetry
            # would wipe the parallel-phase counts recorded above.
            fallback_metrics: dict = {}
            result = await self._run_single_shot(
                script,
                prompt_overrides=prompt_overrides,
                creative_profile=creative_profile,
                telemetry=fallback_metrics,
                source_text=source_text,
                _repair_character_profiles=_repair_character_profiles,
            )
            if telemetry is not None:
                prior_attempts = int(telemetry.get("attempts") or 0)
                telemetry["fallback_used"] = True
                _merge_visualize_telemetry(telemetry, fallback_metrics)
                telemetry["attempts"] = max(prior_attempts, attempted) + int(fallback_metrics.get("attempts") or 0)
            return result

    async def _run_stepwise(
        self,
        script: ScriptBreakdown,
        *,
        prompt_overrides: dict | None,
        creative_profile=None,
        telemetry: dict | None = None,
        progress_callback=None,
        source_text: str = "",
        _repair_character_profiles: bool = True,
    ) -> tuple[VisualDesign, GenerationPrompts]:
        """For Kimi, generate one scene per request to reduce provider timeout risk."""
        if telemetry is not None:
            telemetry.update({
                "attempts": 0,
                "repair_attempts": 0,
                "fallback_used": False,
                "input_chars": 0,
                "output_chars": 0,
                "requests": [],
            })

        total = len(script.scenes)
        merged_scene_visuals: list[SceneVisual] = []
        merged_shots: list[ShotPrompt] = []
        global_en: dict[str, str] = {}
        global_cn: dict[str, str] = {}
        merged_assembly_diagnostics: list[PromptAssemblyDiagnostic] = []
        overall_style = getattr(creative_profile, "art_style", "") or ""
        color_palette = ""
        aspect_ratio = getattr(creative_profile, "video_ratio", "") or "9:16"
        global_negative_prompt = ""

        for index, scene in enumerate(script.scenes, start=1):
            if progress_callback:
                progress_callback(
                    "waiting_provider",
                    index - 1,
                    total,
                    f"正在生成第 {scene.scene_id} 场视觉与分镜提示词",
                )
            single_script = script.model_copy(deep=True)
            single_script.scenes = [scene.model_copy(deep=True)]
            single_script.duration_sec = scene.duration_sec
            call_metrics: dict = {}
            visual, prompts = await self.run(
                single_script,
                prompt_overrides=prompt_overrides,
                creative_profile=creative_profile,
                telemetry=call_metrics,
                source_text=source_text,
                _repair_character_profiles=False,
            )

            if not overall_style:
                overall_style = visual.overall_style
            if not color_palette:
                color_palette = visual.color_palette
            if visual.aspect_ratio:
                aspect_ratio = visual.aspect_ratio
            global_en.update(visual.global_character_descriptions or {})
            global_cn.update(visual.global_character_descriptions_cn or {})
            if prompts.global_negative_prompt and not global_negative_prompt:
                global_negative_prompt = prompts.global_negative_prompt
            merged_scene_visuals.extend(visual.scenes)
            merged_shots.extend(prompts.shots)
            merged_assembly_diagnostics.extend(prompts.assembly_diagnostics)
            if prompts.shots:
                scene.duration_sec = prompts.shots[0].duration_sec

            if telemetry is not None:
                telemetry["attempts"] += call_metrics.get("attempts", 0)
                telemetry["repair_attempts"] += call_metrics.get("repair_attempts", 0)
                telemetry["fallback_used"] = telemetry["fallback_used"] or call_metrics.get("fallback_used", False)
                telemetry["input_chars"] += call_metrics.get("input_chars", 0)
                telemetry["output_chars"] += call_metrics.get("output_chars", 0)
                telemetry["requests"].append({
                    "stage": "visualize_scene",
                    "scene_id": scene.scene_id,
                    "attempts": call_metrics.get("attempts", 0),
                    "repair_attempts": call_metrics.get("repair_attempts", 0),
                    "fallback_used": call_metrics.get("fallback_used", False),
                    "input_chars": call_metrics.get("input_chars", 0),
                    "output_chars": call_metrics.get("output_chars", 0),
                })

        if _repair_character_profiles and self._registry_needs_profile_repair(global_cn, script):
            repaired_en, repaired_cn = await self.reextract_visible_characters(
                script,
                source_text=source_text,
            )
            global_en.update(repaired_en)
            global_cn.update(repaired_cn)

        script.duration_sec = sum(int(scene.duration_sec or 0) for scene in script.scenes)
        return (
            VisualDesign(
                overall_style=overall_style or "写实",
                color_palette=color_palette,
                aspect_ratio=aspect_ratio,
                global_character_descriptions=global_en,
                global_character_descriptions_cn=global_cn,
                scenes=merged_scene_visuals,
            ),
            GenerationPrompts(
                api_provider=self.api_provider,
                prompt_template_version=6,
                prompt_language="zh-CN",
                needs_refresh=False,
                global_negative_prompt=global_negative_prompt,
                shots=merged_shots,
                assembly_diagnostics=merged_assembly_diagnostics,
            ),
        )

    async def reextract_visible_characters(
        self,
        script: ScriptBreakdown,
        *,
        source_text: str = "",
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Refresh only visible-role appearance registries without rewriting shot prompts."""
        self.character_profile_repair_status = "generating"
        manifest_text = self._format_manifest(script)
        scenes_text = self._format_scenes(script)
        source_section = (
            f"\n\n用户确认的完整剧本文本：\n{_compact_visual_text(source_text, 16000)}"
            if str(source_text or "").strip()
            else ""
        )
        user_prompt = f"""请仅重新提取以下短剧中实际出镜角色的形象设定，不要修改分镜、地点、视频提示词或任何剧情。

视觉资产清单：
{manifest_text}

结构化镜头：
{scenes_text}{source_section}

只输出 appearance_mode=on_screen 的角色。优先填写 characters 列表，每项必须包含资产清单中的 asset_id、规范角色名 name、英文 description_en 和中文 description_cn；旧版 global_character_descriptions 与 global_character_descriptions_cn 字典仅用于兼容。
每个中文人物侧写必须写成 80-180 个汉字，并完整覆盖：性别与年龄段、身高或体型、脸型与肤色、眉眼鼻唇等五官、发型与发色、服装款式/主色/材质、配饰和至少一项可辨识标志。
剧本未明确的外貌槽位可以依据人物身份、性格和项目风格做克制、稳定的视觉设计，但不得使用“未提供外貌”“以剧本为准”等占位语；不得写入动作、地点、剧情状态、手持道具或其他角色特征。"""
        system_prompt = (
            "你是影视角色设定师。只输出结构化角色外貌表，不得新增角色。"
            "人物侧写是后续角色母版、分镜参考图和视频一致性的权威身份档案，必须具体、完整、可视化。"
        )

        async def request_profiles(prompt: str):
            return await self.llm.generate_structured(
                system_prompt=system_prompt,
                user_prompt=prompt,
                output_schema=_CharacterRegistryOutput,
                temperature=0.35,
                phase="character.reextract",
            )

        def canonicalize(output: "_CharacterRegistryOutput") -> tuple[dict[str, str], dict[str, str]]:
            english_values = dict(output.global_character_descriptions or {})
            chinese_values = dict(output.global_character_descriptions_cn or {})
            for item in output.characters or []:
                key = str(item.asset_id or item.name or "").strip()
                if not key:
                    continue
                if str(item.description_en or "").strip():
                    english_values[key] = item.description_en.strip()
                if str(item.description_cn or "").strip():
                    chinese_values[key] = item.description_cn.strip()
            return (
                self._canonical_character_registry(english_values, script, english=True),
                self._canonical_character_registry(chinese_values, script, english=False),
            )

        def validation_errors(chinese: dict[str, str]) -> dict[str, list[str]]:
            manifest = script.asset_manifest
            if not manifest:
                return {}
            return {
                character.name: character_profile_missing_fields(chinese.get(character.name, ""))
                for character in manifest.characters
                if character.appearance_mode == "on_screen"
                and character_profile_missing_fields(chinese.get(character.name, ""))
            }

        output = await request_profiles(user_prompt)
        english, chinese = canonicalize(output)
        errors = validation_errors(chinese)
        if errors:
            visible_characters = {
                character.name: character
                for character in (script.asset_manifest.characters if script.asset_manifest else [])
                if character.appearance_mode == "on_screen"
            }
            semaphore = asyncio.Semaphore(2)

            async def repair_one(name: str, fields: list[str]):
                character = visible_characters[name]
                relevant_script = script.model_copy(deep=True)
                relevant_script.scenes = [
                    scene.model_copy(deep=True)
                    for scene in script.scenes
                    if name in (scene.characters or []) or name in str(scene.action_description or "")
                ]
                relevant_scenes = self._format_scenes(relevant_script)
                prompt = f"""上一次输出不合格，请只补全以下一个实际出镜角色的人物侧写。

asset_id: {character.asset_id}
name: {character.name}
当前侧写: {chinese.get(name, '') or character.description}
缺失项: {'、'.join(fields)}

与该角色有关的结构化镜头：
{relevant_scenes}

用户确认剧本文本：
{_compact_visual_text(source_text, 6000)}

description_cn 必须为 80-180 个汉字，完整覆盖性别与年龄段、身高或体型、脸型与肤色、眉眼鼻唇、发型与发色、服装款式/主色/材质、配饰和可辨识标志。
保留当前侧写与剧本中已经明确的事实；缺失槽位可做克制、稳定的视觉设计。不得写动作、地点、剧情状态、手持道具或其他角色特征。"""
                async with semaphore:
                    result = await self.llm.generate_structured(
                        system_prompt=system_prompt,
                        user_prompt=prompt,
                        output_schema=_CharacterRegistryOutput,
                        temperature=0.3,
                        phase="character.reextract.single",
                    )
                repaired_english, repaired_chinese = canonicalize(result)
                return (
                    name,
                    repaired_english.get(name, ""),
                    repaired_chinese.get(name, ""),
                )

            repaired_entries = await asyncio.gather(*(
                repair_one(name, fields)
                for name, fields in errors.items()
            ))
            for name, english_description, chinese_description in repaired_entries:
                english[name] = english_description
                chinese[name] = chinese_description
            errors = validation_errors(chinese)
        if errors:
            visible_characters = {
                character.name: character
                for character in (script.asset_manifest.characters if script.asset_manifest else [])
                if character.appearance_mode == "on_screen"
            }
            unresolved_without_identity: list[str] = []
            for name in errors:
                character = visible_characters.get(name)
                asset_description = str(getattr(character, "description", "") or "").strip()
                current_description = str(chinese.get(name, "") or "").strip()
                if asset_description or current_description:
                    # An asset-authored or model-authored visual anchor is enough
                    # to continue. Keep it, surface partial status, and avoid
                    # turning a recoverable quality defect into a hard failure.
                    chinese[name] = asset_description or current_description
                    english.setdefault(name, getattr(character, "description", "") or name)
                else:
                    unresolved_without_identity.append(name)
            if unresolved_without_identity:
                self.character_profile_repair_status = "failed"
                error_text = "；".join(
                    f"{name}缺少{'、'.join(errors[name])}"
                    for name in unresolved_without_identity
                )
                raise ValueError(f"人物侧写不完整：{error_text}")
            self.character_profile_repair_status = "partial"
            return english, chinese
        self.character_profile_repair_status = "complete"
        return english, chinese

    def _registry_needs_profile_repair(
        self,
        registry: dict[str, str],
        script: ScriptBreakdown,
    ) -> bool:
        manifest = script.asset_manifest
        if not manifest:
            return False
        canonical = self._canonical_character_registry(registry or {}, script, english=False)
        return any(
            character_profile_missing_fields(canonical.get(character.name, ""))
            for character in manifest.characters
            if character.appearance_mode == "on_screen"
        )

    def _format_scenes(self, script: ScriptBreakdown) -> str:
        lines = []
        for scene in script.scenes:
            lines.append(f"--- 镜头 {scene.scene_id}（固定时长 {scene.duration_sec}s，仅处理本镜头） ---")
            location = location_for_scene(script, scene.scene_id)
            visible_names = on_screen_character_names(script, scene.scene_id)
            binding = binding_for_scene(script, scene.scene_id)
            lines.append(f"地点: {_compact_visual_text(location.canonical_name if location else scene.location, 120)}")
            lines.append(f"画面可见角色: {', '.join(visible_names) if visible_names else '无'}")
            if binding and binding.voice_only_speakers:
                lines.append(f"仅声音角色: {', '.join(binding.voice_only_speakers)}")
            if binding and binding.visible_state_notes:
                lines.append(f"镜头状态: {_compact_visual_text(binding.visible_state_notes, 260)}")
            props = props_for_scene(script, scene.scene_id)
            if props:
                lines.append("关键道具: " + "；".join(
                    f"{prop.name}（外观以资产设定为准）" for prop in props
                ))
            lines.append(f"动作: {_compact_visual_text(scene.action_description, 700)}")
            lines.append(f"镜头: {_compact_visual_text(scene.camera_direction, 160)}")
            lines.append(f"情绪: {_compact_visual_text(scene.emotion_tone, 160)}")
            if scene.dialogue:
                for d in scene.dialogue:
                    lines.append(f"  {d.character}: {_compact_visual_text(d.line, 260)}")
            lines.append("")
        return "\n".join(lines)

    def _format_manifest(self, script: ScriptBreakdown) -> str:
        if not script.asset_manifest:
            return "旧项目未提供资产清单，按分镜中的角色与地点处理。"
        lines = ["可见角色："]
        for character in script.asset_manifest.characters:
            if character.appearance_mode != "on_screen":
                continue
            lines.append(
                f"- {character.asset_id}: {character.name} ({character.appearance_mode}) {_compact_visual_text(character.description, 260)}"
            )
        lines.append("可复用地点：")
        for location in script.asset_manifest.locations:
            lines.append(
                f"- {location.location_id}: {_compact_visual_text(location.canonical_name, 120)} | {_compact_visual_text(location.time_condition, 120)} | 使用镜头 {location.shot_ids}"
            )
        lines.append("关键道具：")
        for prop in script.asset_manifest.props:
            lines.append(
                f"- {prop.prop_id}: {prop.name} | {_compact_visual_text(prop.description or '外观以剧本设定为准', 240)} | 使用镜头 {prop.shot_ids}"
            )
        return "\n".join(lines)

    def _split_output(
        self,
        output: "_VisualPromptOutput",
        script: ScriptBreakdown,
        prompt_overrides: dict | None,
        creative_profile=None,
    ) -> tuple[VisualDesign, GenerationPrompts]:
        """Split the combined LLM output into VisualDesign and GenerationPrompts."""

        # Build VisualDesign
        scene_visuals = []
        prompt_shots = []
        source_scene_durations = {
            scene.scene_id: max(3, min(30, int(scene.duration_sec or 3)))
            for scene in script.scenes
        }
        source_total = int(script.duration_sec or sum(source_scene_durations.values()))

        # Index only the first record for each source scene. Duplicate model
        # records must not silently replace an otherwise valid shot.
        shot_by_scene = {}
        for candidate in output.shots:
            shot_by_scene.setdefault(candidate.scene_id, candidate)

        # Sanitize all selected records before assembling prompts so the
        # cross-shot pass can compare the complete ordered set.
        for scene in script.scenes:
            shot = shot_by_scene.get(scene.scene_id)
            if shot is not None:
                _sanitize_shot_output(shot, script, scene)
        _dedupe_repeated_shot_deltas(output, script)

        prev_shot = None
        for scene in script.scenes:
            shot = shot_by_scene.get(scene.scene_id)

            allowed_names = set(on_screen_character_names(script, scene.scene_id))
            raw_appearance = getattr(shot, 'character_appearance', {}) if shot else {}
            character_appearance = (
                {name: desc for name, desc in raw_appearance.items() if name in allowed_names}
                if script.asset_manifest else raw_appearance
            )
            scene_visuals.append(SceneVisual(
                scene_id=scene.scene_id,
                composition=getattr(shot, 'composition', 'medium shot') if shot else 'medium shot',
                lighting=getattr(shot, 'lighting', 'natural light') if shot else 'natural light',
                color_tone=getattr(shot, 'color_tone', 'neutral') if shot else 'neutral',
                character_appearance=character_appearance,
                key_elements=getattr(shot, 'key_elements', []) if shot else [],
                style_reference=getattr(shot, 'style_reference', '') if shot else '',
            ))

            duration = _planned_scene_duration(scene, shot)
            scene.duration_sec = duration
            seed = None if shot is None else shot.seed
            if seed is None:
                # Derive seed from the main character name so the same
                # character always gets the same seed across all scenes.
                # This improves face consistency in independent generations.
                visible_names = on_screen_character_names(script, scene.scene_id)
                main_char = visible_names[0] if visible_names else f"scene_{scene.scene_id}"
                digest = hashlib.sha256(main_char.strip().lower().encode("utf-8")).hexdigest()
                seed = int(digest[:12], 16) % 999999 + 1

            if shot:
                prompt = self._assemble_compact_shot_prompt(script, scene, shot, output, creative_profile, prev_shot)
            else:
                prompt = scene.action_description
            prev_shot = shot

            prompt_shots.append(ShotPrompt(
                scene_id=scene.scene_id,
                prompt=prompt,
                negative_prompt=getattr(
                    shot, 'negative_prompt',
                    '低清晰度，模糊，水印，文字，畸形五官，错误肢体，多余手指，动作抖动，道具变形'
                ) if shot else '低清晰度，模糊，水印，文字，畸形五官，错误肢体，多余手指，动作抖动，道具变形',
                seed=seed,
                motion_strength=getattr(shot, 'motion_strength', 0.7) if shot else 0.7,
                duration_sec=duration,
                batch_size=getattr(shot, 'batch_size', 2) if shot else 2,
            ))

        assembly_diagnostics: list[PromptAssemblyDiagnostic] = []
        for scene in script.scenes:
            shot = shot_by_scene.get(scene.scene_id)
            environment_delta = getattr(shot, "environment_delta", "") if shot else ""
            _environment, dropped = _build_shot_environment_context_with_drops(
                script, scene.scene_id, environment_delta
            )
            assembly_diagnostics.append(PromptAssemblyDiagnostic(
                scene_id=scene.scene_id,
                added_sections=["environment"],
                dropped_facts=dropped,
                status="warning" if dropped else "clean",
            ))

        _fit_duration_budget(prompt_shots, script, source_scene_durations, source_total)

        allowed_global_names = visual_character_names(script)
        global_en = output.global_character_descriptions or {}
        global_cn = output.global_character_descriptions_cn or {}
        if script.asset_manifest:
            global_en = self._canonical_character_registry(global_en, script, english=True)
            global_cn = self._canonical_character_registry(global_cn, script, english=False)

        visual = VisualDesign(
            overall_style=getattr(creative_profile, "art_style", "") or output.overall_style,
            color_palette=output.color_palette,
            aspect_ratio=getattr(creative_profile, "video_ratio", "") or output.aspect_ratio or "9:16",
            global_character_descriptions=global_en,
            global_character_descriptions_cn=global_cn,
            scenes=scene_visuals,
        )

        prompts = GenerationPrompts(
            api_provider=self.api_provider,
            prompt_template_version=6,
            prompt_language="zh-CN",
            needs_refresh=False,
            global_negative_prompt=output.global_negative_prompt or (
                "低清晰度，模糊，水印，文字，标志，畸形五官，多余手指，"
                "错误肢体，动作抖动，道具变形"
            ),
            shots=prompt_shots,
            assembly_diagnostics=assembly_diagnostics,
        )
        if source_total <= 0:
            script.duration_sec = sum(int(scene.duration_sec or 0) for scene in script.scenes)

        protected_scene_ids: set[int] = set()

        # Apply user prompt overrides
        if prompt_overrides:
            for shot in prompts.shots:
                if str(shot.scene_id) in prompt_overrides:
                    shot.prompt = prompt_overrides[str(shot.scene_id)]
                    protected_scene_ids.add(shot.scene_id)

        apply_timed_prompt_structure(
            prompts.shots,
            script,
            protected_scene_ids=protected_scene_ids,
        )

        # The timed composer rewrites each shot prompt, so carry-over segments
        # must be appended after that step instead of being embedded earlier.
        delta_by_scene = {item.scene_id: item for item in (output.shots or [])}
        prev_delta = None
        for scene in script.scenes:
            if getattr(scene, "continuity_from_prev", False) and prev_delta is not None:
                target = next((sp for sp in prompts.shots if sp.scene_id == scene.scene_id), None)
                if (
                    target is not None
                    and scene.scene_id not in protected_scene_ids
                    and "【顺承上一镜】" not in target.prompt
                ):
                    segment = _build_continuity_segment(prev_delta)
                    target.prompt = target.prompt.rstrip() + "\n" + segment
            prev_delta = delta_by_scene.get(scene.scene_id, prev_delta)

        return visual, prompts

    @staticmethod
    def _assemble_compact_shot_prompt(script, scene, shot, output, creative_profile=None, prev_shot=None) -> str:
        """Expand compact LLM scene differences with deterministic production context."""
        from src.services.visual_styles import get_visual_style, resolve_visual_style

        if creative_profile is not None:
            style_id, legacy_style, modifiers = resolve_visual_style(
                visual_style_id=getattr(creative_profile, "visual_style_id", ""),
                art_style=getattr(creative_profile, "art_style", ""),
                style_modifiers=getattr(creative_profile, "style_modifiers", []),
            )
        else:
            style_id, legacy_style, modifiers = resolve_visual_style(
                art_style=getattr(output, "overall_style", "") or "写实",
            )
        style = get_visual_style(style_id)
        style_layer = style["prompt_layers"]["shot"]
        modifier_text = "、".join(modifiers)
        # Keep the stable style id in project state, but do not expose the
        # internal registry key in user/provider-facing Chinese prompts.
        style_block = f"【视觉媒介】{style['name']}：{style_layer}"
        if modifier_text:
            style_block += f" 补充标签：{modifier_text}。"
        location = location_for_scene(script, scene.scene_id)
        binding = binding_for_scene(script, scene.scene_id)
        visible_names = on_screen_character_names(script, scene.scene_id)
        character_registry = getattr(output, "global_character_descriptions_cn", {}) or {}
        characters_by_name = {
            character.name: character
            for character in (script.asset_manifest.characters if script.asset_manifest else [])
        }
        parts = [
            f"【项目视觉】{legacy_style or output.overall_style}；{output.color_palette}；{output.aspect_ratio}画幅。",
            style_block,
            f"【主体状态】{getattr(shot, 'subject_state', '') or scene.action_description}。",
            f"【动作时间轴】{getattr(shot, 'action_timeline', '') or scene.action_description}。",
            (
                "【环境】" + build_shot_environment_context(
                    script,
                    scene.scene_id,
                    getattr(shot, "environment_delta", ""),
                )
            ).strip(),
            f"【镜头语言】{getattr(shot, 'camera_motion', '') or getattr(shot, 'composition', '') or scene.camera_direction}。",
            f"【光线与色调】{getattr(shot, 'lighting_delta', '') or getattr(shot, 'lighting', '') or scene.emotion_tone}。",
        ]
        if visible_names:
            appearances = []
            for name in visible_names:
                description = character_registry.get(name) or getattr(characters_by_name.get(name), "description", "")
                appearances.append(f"{name}：{description}" if description else name)
            parts.insert(1, f"【出镜角色与身份锚点】{'；'.join(appearances)}。")
        if binding and binding.visible_state_notes:
            parts.insert(2, f"【角色状态】{binding.visible_state_notes}。")
        bound_props = props_for_scene(script, scene.scene_id)
        if bound_props:
            prop_anchors = "；".join(
                _current_prop_anchor(prop.name, prop.description)
                for prop in bound_props
            )
            parts.append(f"【绑定道具】{prop_anchors}。")
        prop_state = getattr(shot, 'prop_state', '')
        if prop_state:
            parts.append(f"【关键道具】{prop_state}。")
        prev_state = []
        if getattr(scene, "continuity_from_prev", False) and prev_shot is not None:
            for label, value in (
                ("主体状态", getattr(prev_shot, "subject_state", "")),
                ("环境", getattr(prev_shot, "environment_delta", "")),
                ("光线", getattr(prev_shot, "lighting_delta", "")),
                ("关键道具", getattr(prev_shot, "prop_state", "")),
            ):
                value = str(value or "").strip()
                if value:
                    prev_state.append(f"{label}：{value}")
        if prev_state:
            parts.append(f"【顺承上一镜】本镜承接上一镜结尾画面状态，开头保持与上一镜一致——{'；'.join(prev_state)}。")
        elif getattr(scene, "continuity_from_prev", False) and prev_shot is not None:
            # 旧契约镜头（single-shot 回落）没有结构化画面状态字段时，
            # 仍保留承接语义，但不泄露上一镜动作/剧情细节。
            parts.append("【顺承上一镜】本镜承接上一镜结尾画面状态，开场保持与上一镜一致的姿态、站位、空间与光线。")
        return "\n".join(part for part in parts if part.strip())

    @staticmethod
    def _character_identity(name: str) -> str:
        chinese = "".join(re.findall(r"[\u3400-\u9fff]+", name or ""))
        return chinese or re.sub(r"\s*\([^)]*\)\s*", "", name or "").strip().lower()

    def _canonical_character_registry(
        self,
        registry: dict[str, str],
        script: ScriptBreakdown,
        *,
        english: bool,
    ) -> dict[str, str]:
        """Map LLM aliases back to manifest names and never drop visible roles."""
        manifest = script.asset_manifest
        if not manifest:
            return registry
        entries = list(registry.items())
        canonical: dict[str, str] = {}
        for character in manifest.characters:
            if character.appearance_mode != "on_screen":
                continue
            identities = {
                self._character_identity(character.name),
                self._character_identity(character.asset_id),
            }
            identities.update(self._character_identity(alias) for alias in character.aliases)
            description = next(
                (value for key, value in entries if self._character_identity(key) in identities and value.strip()),
                "",
            )
            if not description:
                if english:
                    description = character.description or f"{character.name}, visible on-screen character; keep appearance consistent with the script."
                else:
                    description = character.description or f"{character.name}，实际出镜角色；外观以剧本设定为准，并保持镜头间一致。"
            if not english:
                description = enrich_character_description(
                    character.name,
                    description,
                    script.scenes,
                )
                description = _stable_character_identity_description(description)
            canonical[character.name] = description
        return canonical


# ── Combined output schema (internal to this module) ──────────────

from pydantic import BaseModel, Field


class _ShotVisualPrompt(BaseModel):
    """Per-shot visual design plus a Chinese video API prompt."""
    scene_id: int = Field(description="Matches ScriptBreakdown.scenes[].scene_id")
    # Visual design
    composition: str = Field(default="medium shot", description="Composition / framing")
    lighting: str = Field(default="natural light", description="Lighting setup")
    color_tone: str = Field(default="neutral", description="Color grading")
    character_appearance: dict[str, str] = Field(
        default_factory=dict,
        description="Character name → appearance description (consistent across scenes)"
    )
    key_elements: list[str] = Field(default_factory=list, description="Must-appear elements")
    style_reference: str = Field(default="", description="Style reference")
    # Compact per-shot differences. The backend assembles the final provider prompt.
    subject_state: str = Field(default="", description="Visible subject state unique to this shot")
    action_timeline: str = Field(default="", description="Time-based action progression unique to this shot")
    environment_delta: str = Field(default="", description="仅静态/持续的环境差异（建筑、陈设、天气、地形、空间、整体光线）；禁止人物动作、情绪、在场、对白或道具交互；无则留空")
    camera_motion: str = Field(default="", description="Camera framing or movement unique to this shot")
    lighting_delta: str = Field(default="", description="Lighting details unique to this shot")
    prop_state: str = Field(default="", description="Bound prop position, material, or interaction state")
    prompt: str = Field(default="", description="Legacy complete Chinese prompt; optional for backward compatibility")
    negative_prompt: str = Field(
        default="低清晰度，模糊，水印，文字，畸形五官，错误肢体，多余手指，动作抖动，道具变形"
    )
    seed: int | None = Field(default=None, description="Random seed")
    motion_strength: float = Field(default=0.7, ge=0.0, le=1.0)
    duration_sec: int = Field(default=5, ge=3, le=30)
    batch_size: int = Field(default=2, ge=1, le=4)


class _VisualPromptOutput(BaseModel):
    """Combined output: overall visual design + per-shot prompts."""
    overall_style: str = Field(description="Overall visual style: 写实, 动漫, 古风, etc.")
    color_palette: str = Field(description="Overall color palette description")
    aspect_ratio: str = Field(default="9:16")
    global_character_descriptions: dict[str, str] = Field(
        default_factory=dict,
        description="Master character appearance registry (English, for API use)"
    )
    global_character_descriptions_cn: dict[str, str] = Field(
        default_factory=dict,
        description="中文版本的角色外貌描述（供前端展示）"
    )
    global_negative_prompt: str = Field(
        default="低清晰度，模糊，水印，文字，畸形五官，错误肢体，多余手指，动作抖动，道具变形"
    )
    shots: list[_ShotVisualPrompt] = Field(description="Per-scene visual + prompt")


class _CompactShotVisualPrompt(BaseModel):
    """Only the per-shot deltas needed to assemble a final provider prompt."""
    scene_id: int = Field(description="Matches ScriptBreakdown.scenes[].scene_id")
    subject_state: str = Field(default="", description="Visible subject state unique to this shot")
    action_timeline: str = Field(default="", description="Time-based action progression unique to this shot")
    environment_delta: str = Field(default="", description="仅静态/持续的环境差异（建筑、陈设、天气、地形、空间、整体光线）；禁止人物动作、情绪、在场、对白或道具交互；无则留空")
    camera_motion: str = Field(default="", description="Camera framing or movement unique to this shot")
    lighting_delta: str = Field(default="", description="Lighting details unique to this shot")
    prop_state: str = Field(default="", description="Bound prop position, material, or interaction state")
    negative_prompt: str = Field(
        default="低清晰度，模糊，水印，文字，畸形五官，错误肢体，多余手指，动作抖动，道具变形"
    )
    seed: int | None = Field(default=None, description="Random seed")
    motion_strength: float = Field(default=0.7, ge=0.0, le=1.0)
    duration_sec: int = Field(default=5, ge=3, le=30)
    batch_size: int = Field(default=2, ge=1, le=4)


class _CompactVisualPromptOutput(BaseModel):
    """Compact visual-stage response; final prompt assembly remains deterministic."""
    overall_style: str = Field(description="Overall visual style: 写实, 动漫, 古风, etc.")
    color_palette: str = Field(description="Overall color palette description")
    aspect_ratio: str = Field(default="9:16")
    global_character_descriptions: dict[str, str] = Field(default_factory=dict)
    global_character_descriptions_cn: dict[str, str] = Field(default_factory=dict)
    global_negative_prompt: str = Field(
        default="低清晰度，模糊，水印，文字，畸形五官，错误肢体，多余手指，动作抖动，道具变形"
    )
    shots: list[_CompactShotVisualPrompt] = Field(description="One compact delta record per scene")


class _GlobalVisualOutput(BaseModel):
    """Phase-1-only global visual result; no per-shot deltas."""
    overall_style: str = ""
    color_palette: str = ""
    aspect_ratio: str = "9:16"
    global_negative_prompt: str = ""
    global_character_descriptions: dict[str, str] = Field(default_factory=dict)
    global_character_descriptions_cn: dict[str, str] = Field(default_factory=dict)


class _CharacterProfileEntry(BaseModel):
    asset_id: str = Field(description="Stable character asset ID from the manifest")
    name: str = Field(description="Canonical character name from the manifest")
    description_en: str = Field(description="Complete stable appearance profile in English")
    description_cn: str = Field(description="80-180 Chinese characters covering all stable appearance fields")


class _CharacterRegistryOutput(BaseModel):
    characters: list[_CharacterProfileEntry] = Field(default_factory=list)
    global_character_descriptions: dict[str, str] = Field(default_factory=dict)
    global_character_descriptions_cn: dict[str, str] = Field(default_factory=dict)
