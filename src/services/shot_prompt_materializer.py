"""Materialize planned child shots into provider-ready prompt records."""

from __future__ import annotations

import re
from typing import Iterable

from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.script import Scene, ScriptBreakdown
from src.services.beat_planner import build_narrative_beats, expand_overlong_speech_beats
from src.services.shot_planner import duration_for_beats
from src.services.video_capabilities import parse_integral_duration
from src.services.prompt_execution_context import _embed_dialogue_in_timeline, _spoken_cue


def _replace_timeline(prompt: str, timeline: str) -> str:
    """Scope a parent prompt to one child unit without stale timing ranges."""
    source = str(prompt or "")
    replacement = f"【动作时间轴】{timeline}"
    action_pattern = r"【动作时间轴】.*?(?=\n【|$)"
    if re.search(action_pattern, source, re.S):
        return re.sub(action_pattern, replacement, source, count=1, flags=re.S)

    # A parent prompt may already be finalized as a single/ranged timeline.
    # Keep its reusable visual context, but remove the parent's timed segment
    # lines before inserting the child unit's own executable timeline.
    finalized_header = re.search(r"(?m)^【(?:单镜头时间轴|时间轴分镜)】", source)
    if finalized_header:
        timed_line_pattern = re.compile(
            r"(?m)^\s*(?:切镜头[，,:：]\s*)?"
            r"\d+(?:\.\d+)?s\s*(?:-\s*|到\s*)"
            r"\d+(?:\.\d+)?s[，,].*$"
        )
        cleaned = timed_line_pattern.sub("", source)
        duration_match = re.search(r"0-(\d+(?:\.\d+)?)s", timeline)
        if duration_match:
            cleaned = re.sub(
                r"总时长约\d+(?:\.\d+)?s",
                f"总时长约{duration_match.group(1)}s",
                cleaned,
                count=1,
            )
        continuity = re.search(r"(?m)^【连续锁定】", cleaned)
        if continuity:
            before = cleaned[:continuity.start()].rstrip()
            after = cleaned[continuity.start():].lstrip()
            return "\n".join(part for part in (before, replacement, after) if part).strip()
        return "\n".join(part for part in (cleaned.rstrip(), replacement) if part).strip()

    return source.rstrip() + "\n" + replacement


def _strip_parent_dialogue_sections(prompt: str) -> str:
    """Remove parent-wide dialogue blocks before creating a child prompt."""
    left, right = chr(0x3010), chr(0x3011)
    word = chr(0x5BF9) + chr(0x767D)
    pattern = rf"(?ms)^{left}[^{right}]*{word}[^{right}]*{right}.*?(?=^{left}|\Z)"
    return re.sub(pattern, "", str(prompt or "")).strip()


_EMPTY_TIMELINE_SHELL_RE = re.compile(r"(?ms)^【(?:时间轴分镜|单镜头时间轴)】\s*(?=^【|\Z)")
_CROSS_SHOT_INSTRUCTION_RE = re.compile(r"[，,]?\s*不要重复上一镜[^。；\n]*[。；]?")


def _strip_non_execution_sections(prompt: str) -> str:
    """Remove planning/audit prose that the video model cannot execute."""
    cleaned = _strip_parent_dialogue_sections(prompt)
    for header in ("时间容量诊断", "本镜禁止执行", "本镜禁止", "出镜角色与身份锚点", "连续锁定"):
        cleaned = re.sub(
            rf"(?ms)^【{re.escape(header)}】.*?(?=^【|\Z)",
            "",
            cleaned,
        )
    # 空的时间轴壳段没有任何可执行信息，还会困住原生对白追加的定位逻辑。
    cleaned = _EMPTY_TIMELINE_SHELL_RE.sub("", cleaned)
    # 每个子镜独立提交给模型；"不要重复上一镜"在请求里没有指代物，
    # 已生成的旧父级稿也带这句话，这里统一做短语级清除。
    cleaned = _CROSS_SHOT_INSTRUCTION_RE.sub("", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _strip_stale_continuity_segment(prompt: str) -> str:
    """Drop the parent's 【顺承上一镜】 from scene-internal later children.

    该段描述的是"上一个场景"的结尾状态：对本场第一个子镜是合法的入镜
    上下文；对场内后续子镜则是过期状态，且附带跨镜指令句。后续子镜的
    入镜状态由尾帧参考图与本镜时间轴承载。
    """
    return re.sub(r"(?ms)^【顺承上一镜】.*?(?=^【|\Z)", "", str(prompt or "")).strip()


_PLANNING_EXCERPT_RE = re.compile(
    r"新登场|开启新节拍|时间跳到|承接上一镜|范围外事实|规划说明"
)
_CAMERA_EXCERPT_RE = re.compile(
    r"近景|特写|中景|远景|全景|俯拍|仰拍|推镜|拉远|摇镜|跟拍|切镜头|反打|移镜|dolly|竖幅"
)


def _is_executable_action_excerpt(beat) -> bool:
    kind = str(getattr(beat, "kind", "") or "")
    text = str(getattr(beat, "source_excerpt", "") or "").strip()
    if not text:
        return False
    if getattr(beat, "dialogue_id", None) or kind in {"dialogue", "inner_monologue", "continuity", "camera", "emotion"}:
        return False
    if _PLANNING_EXCERPT_RE.search(text) or _CAMERA_EXCERPT_RE.search(text):
        return False
    return True


def _strip_scene_start_state_sections(prompt: str) -> str:
    """Drop the scene-level 【主体状态/角色状态】 from later child shots.

    Those sections describe the whole scene's opening state. A later child
    starts mid-scene (the parcel is already on the table), so inheriting the
    opening state contradicts its own timeline; its entering state is carried
    by the continuity tail-frame reference and its own action timeline.
    """
    cleaned = str(prompt or "")
    for header in ("主体状态", "角色状态"):
        cleaned = re.sub(rf"(?ms)^【{header}】.*?(?=^【|\Z)", "", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _establishing_camera(scene: Scene, excerpts: list[str] | None = None) -> str:
    """One locked-off view for the current location; doorway framing only when this shot actually enters."""
    camera = str(getattr(scene, "camera_direction", "") or "").strip()
    location = str(getattr(scene, "location", "") or "")
    joined = "；".join(item for item in (excerpts or []) if item)
    if any(token in camera for token in ("正反打", "反打")):
        return "固定双人中近景，看清门口两人站位、表情和口型；不要切镜头，不要正反打。"
    # 只有"开门/推门/进屋"这类门体动作才配门口构图；"站在院门口"不算。
    if any(token in joined for token in ("推门", "进屋", "开门", "拉开木门")):
        return "固定全景到中景，看清门口、进屋人物和室内空间关系；不要切镜头。"
    # 单段 dolly/pan 是一个连续运镜，可直接执行；只有"切"才是多视角拼接，
    # 需要退回默认模板——除非它带显式时间锚点（如"0-2s …，2s 切…"），
    # 那是深思熟虑的计划剪辑，应原样下发以便【计划剪辑规则】拿到切换位置。
    if camera and ("切" not in camera or re.search(r"\d+(?:\.\d+)?s", camera)):
        return camera
    # 只认明确的街道词；场地串里的"(上午,室外)"这类括注不算，
    # 否则院子戏会被误配成"看清街道或店门、招牌"。
    if any(token in location for token in ("门口", "人行道", "街道")):
        return "固定全景到中景，看清当前街道或店门、人物全身和招牌；不要切镜头。"
    if any(token in location for token in ("店", "柜台", "铺")):
        return "固定双人中景，看清柜台、人物和手中关键道具；不要切镜头。"
    return "固定中景，看清本镜人物、空间和关键道具；不要切镜头。"


def _child_camera_block(unit, scene: Scene | None, excerpts: list[str] | None = None) -> str:
    if scene is None:
        return ""
    excerpts = [item for item in (excerpts or []) if item]
    # Every child shot is sent to the model as a standalone request, so the
    # camera line must describe *this* shot's own framing.  Cross-shot wording
    # like "保持当前机位 / 不要重新起镜 / 切到新地点" has no referent for a
    # model that never sees the previous shot; continuity is carried by the
    # tail-frame reference image instead, so it is dropped here.
    camera = _establishing_camera(scene, excerpts)
    dialogue_ids = list(getattr(unit, "dialogue_ids", None) or [])
    if dialogue_ids:
        spoken, monologue = [], []
        for dialogue_id in dialogue_ids:
            speaker, emotion = _speaker_for_dialogue_id(scene, dialogue_id)
            target = monologue if "内心独白" in (emotion or "") else spoken
            target.append(speaker or "未知角色")
        notes = []
        if spoken:
            notes.append("、".join(dict.fromkeys(spoken)) + "在画面中自然说话，口型与声音同步")
        if monologue:
            notes.append("、".join(dict.fromkeys(monologue)) + "保持口型不动，独白以画外音呈现")
        if notes:
            # 机位句自带句号/分号时先剥掉再拼接，避免“。；”连标点。
            base = camera.rstrip("。；;，, ")
            camera = (f"{base}；" if base else "") + "；".join(notes) + "。"
    return camera


def _speaker_for_dialogue_id(scene: Scene | None, dialogue_id: str) -> tuple[str, str]:
    if scene is None:
        return "未知角色", ""
    match = re.search(r"dialogue-(\d+)(?:-part-\d+)?$", str(dialogue_id or ""))
    if not match:
        return "未知角色", ""
    index = int(match.group(1))
    lines = list(scene.dialogue or [])
    if not (1 <= index <= len(lines)):
        return "未知角色", ""
    line = lines[index - 1]
    speaker = str(line.character or "").strip()
    if not speaker:
        names = [str(name or "").strip() for name in (scene.characters or []) if str(name or "").strip()]
        speaker = names[0] if len(names) == 1 else "未知角色"
    return speaker, str(line.emotion or "").strip()


def _dialogue_section(scene: Scene | None, dialogue_ids: Iterable[str], selected_beats: list | None = None) -> str:
    if scene is None:
        return ""
    rendered: list[str] = []
    if selected_beats:
        for beat in selected_beats:
            dialogue_id = str(getattr(beat, "dialogue_id", "") or "")
            excerpt = str(getattr(beat, "source_excerpt", "") or "").strip()
            if not dialogue_id or not excerpt:
                continue
            speaker, emotion = _speaker_for_dialogue_id(scene, dialogue_id)
            label = f"{speaker}｜{emotion}" if emotion else speaker
            rendered.append(f"{label}：\n“{excerpt}”")
    if not rendered and dialogue_ids:
        allowed = set(dialogue_ids)
        for index, line in enumerate(scene.dialogue or [], start=1):
            dialogue_id = f"scene-{scene.scene_id}-dialogue-{index}"
            if allowed and dialogue_id not in allowed:
                continue
            speaker, emotion = _speaker_for_dialogue_id(scene, dialogue_id)
            content = str(line.line or "").strip()
            if content:
                label = f"{speaker}｜{emotion}" if emotion else speaker
                rendered.append(f"{label}：\n“{content}”")
    return ("\n".join(rendered)) if rendered else ""


def _insert_after_action_section(prompt: str, block: str) -> str:
    """Keep executable dialogue next to the action timeline, not at prompt tail."""
    source = str(prompt or "").strip()
    if not block:
        return source
    match = re.search(
        r"(?ms)^【动作时间轴】.*?(?=^【[^】]+】|\Z)",
        source,
    )
    if not match:
        return "\n".join(part for part in (block, source) if part).strip()
    return "\n".join((source[:match.end()].rstrip(), block, source[match.end():].lstrip())).strip()



def _dialogue_hold_excerpt(scene: Scene | None, unit) -> str:
    """Keep a dialogue-only child shot still, instead of inventing a new action."""
    speaker = ""
    if scene is not None:
        wanted = {
            re.sub(r"-part-\d+$", "", str(item or ""))
            for item in (unit.dialogue_ids or [])
        }
        for index, line in enumerate(scene.dialogue or [], start=1):
            dialogue_id = f"scene-{scene.scene_id}-dialogue-{index}"
            if dialogue_id in wanted:
                speaker = str(line.character or "").strip()
                break
    who = speaker or "说话人"
    # 每个子镜独立提交给模型，措辞必须自洽：只描述本镜要发生的事，
    # 不引用"上一镜"这类请求里并不存在的上下文。
    return (
        f"{who}保持当前站位、表情和关键道具，把本镜对白完整清楚地说完；"
        "对方只听、不打断、不开始新的进门或走动。"
    )


def _unit_scope_prompt(
    parent: ShotPrompt,
    unit,
    beat_map: dict[str, object],
    all_excerpts: list[str],
    *,
    scene: Scene | None = None,
    dialogue_excerpts: set[str] | None = None,
    duration_sec: int | None = None,
) -> str:
    selected_beats = [beat_map[beat_id] for beat_id in unit.beat_ids if beat_id in beat_map]
    excerpts = list(dict.fromkeys(
        str(getattr(beat, "source_excerpt", "") or "").strip()
        for beat in selected_beats
        if _is_executable_action_excerpt(beat)
    ))
    if not excerpts:
        if unit.dialogue_ids:
            excerpts = [_dialogue_hold_excerpt(scene, unit)]
        else:
            excerpts = ["本子镜头只执行已绑定事实"]
    effective_duration = int(duration_sec if duration_sec is not None else unit.duration_sec)
    period = chr(0x3002)
    joined = chr(0xFF1B).join(excerpts).rstrip()
    joined = joined.rstrip(period + chr(0xFF01) + chr(0xFF1F) + chr(0xFF1B) + chr(0xFF0C) + ". ")
    if joined:
        joined += period
    timeline = f"0-{effective_duration}s " + joined
    base_prompt = _strip_non_execution_sections(parent.prompt)
    if int(getattr(unit, "order", 1) or 1) > 1:
        base_prompt = _strip_scene_start_state_sections(base_prompt)
        base_prompt = _strip_stale_continuity_segment(base_prompt)
    prompt = _replace_timeline(base_prompt, timeline)
    # _replace_timeline 的"已完成时间轴"分支会先删掉父级时间行再追加本镜
    # 时间轴，父级壳段在这之后才变空；这里再清一次。
    prompt = _EMPTY_TIMELINE_SHELL_RE.sub("", prompt)
    child_camera = _child_camera_block(unit, scene, excerpts)
    # 紧凑模板父级的"道具锚点：…"行紧跟核心分镜意图，且位于同一文本块内；
    # 下面的剥离会把整块吃掉，先抢救出来转成正式【关键道具】段。
    anchor_lines = re.findall(r"(?m)^道具锚点：(.+)$", prompt)
    prompt = re.sub(r"(?ms)^核心分镜意图：.*?(?=^【|\Z)", "", prompt)
    if anchor_lines:
        anchors = "；".join(line.strip().rstrip("。") for line in anchor_lines)
        prompt += chr(10) + "【关键道具】" + anchors + "。"
    if child_camera:
        if re.search(r"(?ms)^【镜头语言】", prompt):
            prompt = re.sub(r"(?ms)^【镜头语言】.*?(?=^【|\Z)", f"【镜头语言】{child_camera}", prompt, count=1)
        else:
            prompt += "\n【镜头语言】" + child_camera
    omitted_action = [
        item for item in all_excerpts
        if item
        and item not in excerpts
        and item not in (dialogue_excerpts or set())
        and not _PLANNING_EXCERPT_RE.search(item)
        and not _CAMERA_EXCERPT_RE.search(item)
    ]
    if omitted_action or (dialogue_excerpts and any(item not in excerpts for item in (dialogue_excerpts or set()))):
        rules = ["不得执行本镜范围之外的动作或状态"]
        if dialogue_excerpts:
            rules.append("不得说出本镜未绑定对白")
        prompt += "\n【本镜禁止执行】" + "；".join(rules) + "。"
    dialogue = _dialogue_section(scene, unit.dialogue_ids, selected_beats)
    if dialogue:
        cue = _spoken_cue(dialogue)
        if cue:
            timeline = _embed_dialogue_in_timeline(timeline, cue)
            prompt = _replace_timeline(prompt, timeline)
        else:
            prompt = _insert_after_action_section(prompt, dialogue)
    prompt = _strip_parent_dialogue_sections(prompt)
    if unit.continuity_from_shot_id:
        prompt += f"\n【子镜头连续性】承接 {unit.continuity_from_shot_id} 的结尾状态，保持角色站位、空间和道具状态连续。"
    return prompt



def _finalize_child_prompt(
    prompt: str,
    unit,
    beat_map: dict[str, object],
    *,
    scene: Scene | None = None,
    duration_sec: int | None = None,
) -> str:
    """Strip leftover dialogue blocks and keep spoken lines inside the timeline."""
    cleaned = _strip_parent_dialogue_sections(prompt)
    selected_beats = [beat_map[beat_id] for beat_id in (unit.beat_ids or []) if beat_id in beat_map]
    dialogue = _dialogue_section(scene, unit.dialogue_ids, selected_beats)
    left, right = chr(0x3010), chr(0x3011)
    action_word = chr(0x52A8) + chr(0x4F5C) + chr(0x65F6) + chr(0x95F4) + chr(0x8F74)
    action_pattern = rf"{left}{action_word}{right}.*?(?=\n{left}|$)"
    match = re.search(action_pattern, cleaned, re.S)
    timeline = ""
    if match:
        timeline = match.group(0).split(right, 1)[-1].strip()
    if not timeline:
        return _EMPTY_TIMELINE_SHELL_RE.sub("", _strip_parent_dialogue_sections(cleaned)).strip()
    period = chr(0x3002)
    timeline = timeline.rstrip(period + chr(0xFF01) + chr(0xFF1F) + chr(0xFF1B) + chr(0xFF0C) + ". ")
    # A timeline already ending in a spoken quote carries its own terminal
    # punctuation inside the quotation; appending another period reads as a
    # provider-visible "?..。" glitch.
    if timeline and not timeline.endswith((chr(0x201D), chr(0x22))):
        timeline += period
    if dialogue:
        cue = _spoken_cue(dialogue)
        if cue:
            timeline = _embed_dialogue_in_timeline(timeline, cue)
    cleaned = _replace_timeline(cleaned, timeline)
    return _EMPTY_TIMELINE_SHELL_RE.sub("", _strip_parent_dialogue_sections(cleaned)).strip()


def materialize_shot_prompts(
    prompts: GenerationPrompts,
    script: ScriptBreakdown,
    *,
    duration_cap_sec: float | None = None,
    duration_options: Iterable[int | float] | None = None,
    provider_min_duration_sec: int | float = 3,
) -> list[ShotPrompt]:
    """Flatten plans and keep child duration fields aligned with a provider cap."""
    cap: int | None = None
    if duration_cap_sec is not None:
        cap = parse_integral_duration(
            duration_cap_sec,
            field_name="provider duration cap",
        )
        if cap < 3:
            raise ValueError(
                f"provider duration cap {cap}s is below the executable 3s minimum"
            )
    minimum = parse_integral_duration(
        provider_min_duration_sec,
        field_name="provider minimum duration",
    )
    if minimum < 1:
        raise ValueError("provider minimum duration must be at least 1 second")
    if cap is not None and cap < minimum:
        raise ValueError(
            f"provider duration cap {cap}s is below the executable minimum {minimum}s"
        )
    allowed_values: set[int] = set()
    for value in (duration_options or []):
        try:
            parsed = parse_integral_duration(value, field_name="duration option")
        except ValueError:
            continue
        if parsed < minimum or (cap is not None and parsed > cap):
            continue
        allowed_values.add(parsed)
    allowed_durations = sorted(allowed_values)

    def validate_duration(value: int | float, label: str) -> int:
        effective = parse_integral_duration(value, field_name=f"{label} duration")
        if effective < minimum:
            effective = minimum
        if cap is not None and effective > cap:
            raise ValueError(
                f"{label} 时长 {effective}s 超过当前模型上限 {cap}s，"
                "必须重新规划子镜头拆分，禁止截断后静默生成"
            )
        if allowed_durations and effective not in allowed_durations:
            # Planning uses integer seconds to preserve the story beat budget,
            # while providers such as HappyHorse expose a discrete duration
            # menu.  Round upward to the first legal slot so the child gets
            # enough execution time; never round downward and silently lose
            # action or dialogue.  If no legal slot remains under the cap,
            # keep the hard failure so the caller can re-plan the shot.
            next_values = [item for item in allowed_durations if item >= effective]
            if not next_values:
                choices = "、".join(str(item) for item in allowed_durations)
                raise ValueError(f"{label} 时长 {effective}s 不在当前模型支持集合（{choices}）内，且没有更长可用时长")
            effective = next_values[0]
        return effective
    plans = {plan.scene_id: plan for plan in (prompts.shot_plans or [])}
    scenes = {scene.scene_id: scene for scene in script.scenes}
    result: list[ShotPrompt] = []
    hard_failure_statuses = {
        "duration_insufficient",
        "dialogue_overflow",
        "beat_overflow",
        "timeline_invalid",
    }
    for parent in prompts.shots:
        plan = plans.get(parent.scene_id)
        if not plan or not plan.units:
            if plan is not None and plan.capacity.status in hard_failure_statuses:
                warnings = "；".join(plan.capacity.warnings or []) or "容量不足"
                raise ValueError(
                    "场景 "
                    f"{plan.scene_id} 容量诊断失败（{plan.capacity.status}：{warnings}），"
                    "无法生成可执行子镜头；请修正场景时长或内容后重新规划，"
                    "禁止回退到超出模型上限的父镜头"
                )
            validate_duration(parent.duration_sec, f"父镜头 {parent.scene_id}")
            result.append(parent.model_copy(deep=True))
            continue
        scene = scenes.get(parent.scene_id)
        beats = build_narrative_beats(scene) if scene is not None else []
        if scene is not None and cap:
            beats = expand_overlong_speech_beats(beats, cap)
        beat_map = {beat.beat_id: beat for beat in beats}
        planned_ids = [beat_id for unit in plan.units for beat_id in (unit.beat_ids or [])]
        stale_ids = any(beat_id not in beat_map for beat_id in planned_ids)
        mismatched_dialogue = False
        for unit in plan.units:
            derived = [
                getattr(beat_map[beat_id], "dialogue_id", None)
                for beat_id in (unit.beat_ids or [])
                if beat_id in beat_map and getattr(beat_map[beat_id], "dialogue_id", None)
            ]
            stored = list(unit.dialogue_ids or [])
            if stored and derived and stored != derived:
                mismatched_dialogue = True
                break
        if scene is not None and (stale_ids or mismatched_dialogue):
            from src.services.shot_planner import build_shot_plan
            rebuilt = build_shot_plan(
                scene,
                cap,
                provider_min_duration_sec=minimum,
                provider_durations=allowed_durations or None,
            )
            if rebuilt.units:
                plan = rebuilt
                beats = build_narrative_beats(scene)
                if cap:
                    beats = expand_overlong_speech_beats(beats, cap)
                beat_map = {beat.beat_id: beat for beat in beats}
        all_excerpts = list(dict.fromkeys(
            str(getattr(beat, "source_excerpt", "") or "").strip()
            for beat in beats
            if str(getattr(beat, "source_excerpt", "") or "").strip()
        ))
        dialogue_excerpts = {
            str(getattr(beat, "source_excerpt", "") or "").strip()
            for beat in beats
            if getattr(beat, "dialogue_id", None)
            and str(getattr(beat, "source_excerpt", "") or "").strip()
        }
        for unit in plan.units:
            stored = validate_duration(unit.duration_sec, f"子镜头 {unit.shot_id}")
            group = [beat_map[beat_id] for beat_id in (unit.beat_ids or []) if beat_id in beat_map]
            needed = duration_for_beats(
                group,
                minimum=minimum,
                maximum=cap,
                allowed=allowed_durations or None,
            ) if group else stored
            effective_duration = validate_duration(
                max(stored, needed),
                f"子镜头 {unit.shot_id}",
            )
            saved_prompt = str(getattr(unit, 'prompt', '') or '').strip()
            if saved_prompt:
                child_prompt = saved_prompt
            else:
                child_prompt = _unit_scope_prompt(
                    parent,
                    unit,
                    beat_map,
                    all_excerpts,
                    scene=scene,
                    dialogue_excerpts=dialogue_excerpts,
                    duration_sec=effective_duration,
                )
            child_prompt = _finalize_child_prompt(
                child_prompt,
                unit,
                beat_map,
                scene=scene,
                duration_sec=effective_duration,
            )
            result.append(parent.model_copy(deep=True, update={
                "shot_id": unit.shot_id,
                "parent_scene_id": unit.parent_scene_id,
                "shot_order": unit.order,
                "continuity_from_shot_id": unit.continuity_from_shot_id,
                "beat_ids": list(unit.beat_ids),
                "source_fact_ids": list(unit.source_fact_ids),
                "dialogue_ids": list(unit.dialogue_ids),
                "duration_sec": effective_duration,
                "prompt": child_prompt,
            }))
    return result
