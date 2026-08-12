"""Generate visual design and Chinese video prompts in one LLM call."""

import random
import re
import hashlib
from src.schemas.script import ScriptBreakdown
from src.schemas.visual import VisualDesign, SceneVisual
from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.utils.llm import get_llm_client
from src.utils.visual_asset_manifest import (
    binding_for_scene,
    location_for_scene,
    on_screen_character_names,
    props_for_scene,
    visual_character_names,
)
from src.services.timed_prompt_composer import apply_timed_prompt_structure
from src.config import DEFAULT_API


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
- 角色外貌描述必须稳定、具体；每个镜头只描述实际出镜角色，绝不把仅配音角色写入画面。
- 动作时间轴需按镜头时长清晰描述；负面约束使用中文短语。
- 所有字段使用中文，保持同一地点的空间、陈设、时间天气和主光方向连续。
"""


class VisualPromptAgent:
    """Agent that generates visual design and Chinese video prompts in one pass.

    Replaces the two-step VisualDesignAgent → PromptEngineeringAgent pipeline
    with a single LLM call that outputs both the visual specification and the
    corresponding Chinese video generation prompts.
    """

    def __init__(self, api_provider: str | None = None):
        self.llm = get_llm_client()
        self.api_provider = api_provider or DEFAULT_API

    async def run(
        self,
        script: ScriptBreakdown,
        character_images=None,
        scene_images=None,
        prompt_overrides: dict | None = None,
        creative_profile=None,
        telemetry: dict | None = None,
        progress_callback=None,
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
        if getattr(self.llm, "provider", "") == "kimi" and len(script.scenes) > 1:
            return await self._run_stepwise(
                script,
                prompt_overrides=prompt_overrides,
                creative_profile=creative_profile,
                telemetry=telemetry,
                progress_callback=progress_callback,
            )

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
- 每个镜头必须包含绑定 prop_ids 对应道具。道具是画面资产而非背景泛称：写清它在画面中的位置、形态或材质、与人物的交互和镜头结束状态；未绑定道具不得凭空新增。"""

        output = await self.llm.generate_structured(
            system_prompt=COMPACT_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            output_schema=_CompactVisualPromptOutput,
            temperature=0.7,
            telemetry=telemetry,
            phase="script.visualize",
        )
        return self._split_output(output, script, prompt_overrides, creative_profile)

    async def _run_stepwise(
        self,
        script: ScriptBreakdown,
        *,
        prompt_overrides: dict | None,
        creative_profile=None,
        telemetry: dict | None = None,
        progress_callback=None,
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

        script.duration_sec = sum(int(scene.duration_sec or 0) for scene in script.scenes)
        return (
            VisualDesign(
                overall_style=overall_style or "鍐欏疄",
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
            ),
        )

    async def reextract_visible_characters(self, script: ScriptBreakdown) -> tuple[dict[str, str], dict[str, str]]:
        """Refresh only visible-role appearance registries without rewriting shot prompts."""
        manifest_text = self._format_manifest(script)
        user_prompt = f"""请仅重新提取以下短剧中实际出镜角色的形象设定，不要修改分镜、地点、视频提示词或任何剧情。

视觉资产清单：
{manifest_text}

只输出 appearance_mode=on_screen 的角色。global_character_descriptions 用英文，global_character_descriptions_cn 用中文；两张表必须使用资产清单中的规范角色名作为 key。每个描述必须包含稳定外貌、发型、体型、服装颜色和材质、标志特征，供角色图和视频参考图一致使用。"""
        output = await self.llm.generate_structured(
            system_prompt="你是影视角色设定师。只输出结构化角色外貌表，不得新增角色。",
            user_prompt=user_prompt,
            output_schema=_CharacterRegistryOutput,
            temperature=0.35,
        )
        return (
            self._canonical_character_registry(output.global_character_descriptions, script, english=True),
            self._canonical_character_registry(output.global_character_descriptions_cn, script, english=False),
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
                prompt = self._assemble_compact_shot_prompt(script, scene, shot, output, creative_profile)
            else:
                prompt = scene.action_description

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

        return visual, prompts

    @staticmethod
    def _assemble_compact_shot_prompt(script, scene, shot, output, creative_profile=None) -> str:
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
        style_block = f"【视觉媒介】{style['name']}（{style_id}）：{style_layer}"
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
                f"【环境】{location.canonical_name if location else scene.location}。"
                f"{getattr(location, 'environment_description', '') or ''}。"
                f"{getattr(shot, 'environment_delta', '')}"
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
            identities = {self._character_identity(character.name)}
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
    environment_delta: str = Field(default="", description="Environment details that differ in this shot")
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
    environment_delta: str = Field(default="", description="Environment details that differ in this shot")
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


class _CharacterRegistryOutput(BaseModel):
    global_character_descriptions: dict[str, str] = Field(default_factory=dict)
    global_character_descriptions_cn: dict[str, str] = Field(default_factory=dict)
