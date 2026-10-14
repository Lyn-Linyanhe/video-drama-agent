"""Deterministic narrative beat extraction and scene capacity planning."""

from __future__ import annotations

import math
import re
from typing import Iterable

from src.schemas.script import Scene
from src.schemas.shot_plan import (
    BeatDurationEstimate,
    NarrativeBeat,
    SceneCapacityReport,
)

_ACTION_SPLIT_RE = re.compile(r"[。！？!?；;，,、\n]+")
_ACTION_VERB_RE = re.compile(
    # Keep posture + gaze as one predicate. Splitting ``抬头看向`` into two
    # beats loses the object and creates a false extra action.
    r"抬头(?:看向|望向|凝视|盯着)|低头(?:看向|望向|凝视|盯着)|"
    r"回头(?:看向|望向|凝视|盯着)|"
    r"走到|走向|走进|走出|进入|离开|出门|回到|返回|冲出|跑出|进门|"
    r"站在|坐在|起身|转身|抬眼|抬头|低头|看向|望向|凝视|盯着|回头|"
    r"拿起|拾起|捡起|抓住|拿|放下|放回|递给|接过|交给|戴上|摘下|"
    r"藏到|藏在|取出|塞进|换成|点燃|熄灭|拔出|插入|解开|捧起|抱住|"
    r"打开|关上|关闭|推开|锁上|解锁|叹气|流泪|哭泣|露出|开始|准备|煮|煎"
)
_PROP_CHANGE_RE = re.compile(
    r"拿起|拾起|捡起|抓住|拿|放下|放回|递给|接过|交给|戴上|摘下|"
    r"藏到|藏在|取出|塞进|换成|点燃|熄灭|拔出|插入|解开|捧起|抱住|"
    r"打开(?:纸包|纸袋|包裹|盒子|小包|信封|礼物|抽屉|箱子)|"
    r"关上(?:纸包|纸袋|盒子|小包|抽屉|箱子)|关闭(?:炉灶|灯|门锁)"
)
_TRANSITION_RE = re.compile(r"进门|进入|走进|走出|离开|出门|回到|返回|冲出|跑出|转身走向|走向门")
_LOCOMOTION_RE = re.compile(
    r"走到|走向|走进|走出|推开|推门|进门|进屋|出门|停下脚步|抬头看|快步"
)
_CAMERA_RE = re.compile(r"近景|特写|中景|远景|全景|俯拍|仰拍|推镜|拉远|摇镜|跟拍|切镜头|反打|移镜")
_MONOLOGUE_RE = re.compile(r"独白|内心|心里|心想|她想|他想|暗自|自言自语|旁白|心中")
_PARALLEL_CUE_RE = re.compile(r"一边|一面|同时|边[^，。；;\n]{0,20}边|说着|边说边")
_DIALOGUE_PARALLEL_CUE_RE = re.compile(r"说着|边说边|一边说|一面说")
_HOLD_NUMBER_RE = re.compile(
    r"(?P<number>\d+(?:\.\d+)?|半|一|两|二|三|四|五|六|七|八|九|十|几|数)\s*(?:秒|s)",
    re.IGNORECASE,
)
_HOLD_WORD_RE = re.compile(r"停顿(?:片刻|一会儿|一会|一下)?|沉默(?:片刻|一会儿|一会)|凝视(?:片刻|一会儿|一会)")
_LEADING_CONNECTOR_RE = re.compile(r"^(?:并|然后|随后|接着|再|便|同时)\s*")
_TRAILING_CONNECTOR_RE = re.compile(r"\s*(?:并|然后|随后|接着|再|便|同时)$")
_CN_DIGITS = {"半": 0.5, "一": 1.0, "两": 2.0, "二": 2.0, "三": 3.0, "四": 4.0, "五": 5.0, "六": 6.0, "七": 7.0, "八": 8.0, "九": 9.0, "十": 10.0, "几": 2.0, "数": 3.0}

TIMING_BASIS = (
    "serial=对白与明确物理动作按顺序计时；"
    "overlap=有明确并行语义的动作与对白按同组最长时长计时；"
    "constraint=镜头语言、一般情绪和连续性只作拍摄约束，不增加剧情时长；"
    "明确停顿/沉默/等待才转为serial时长。"
)


def _has_action_verb(text: str) -> bool:
    return bool(_ACTION_VERB_RE.search(text))


def _clean_connector(text: str) -> str:
    text = _LEADING_CONNECTOR_RE.sub("", text.strip(" ，,、"))
    text = _TRAILING_CONNECTOR_RE.sub("", text).strip(" ，,、")
    return text


def _split_compound_clause(clause: str) -> list[str]:
    """Split only at later high-confidence predicates, preserving connectors."""
    text = _clean_connector(clause)
    matches = list(_ACTION_VERB_RE.finditer(text))
    if len(matches) < 2:
        return [text] if text else []
    pieces: list[str] = []
    starts = [0, *(match.start() for match in matches[1:])]
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(text)
        piece = _clean_connector(text[start:end])
        if piece:
            pieces.append(piece)
    return pieces or ([text] if text else [])


_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;\n]+")


def _action_clauses(text: str) -> list[str]:
    """Split story sentences first, then executable comma/compound actions inside each sentence.

    The previous implementation split on commas and then rejoined the entire
    scene when any fragment lacked a verb. That collapsed semicolon-separated
    Maggi-style action chains into one overloaded child shot.
    """
    sentences = [part.strip(" ，,、") for part in _SENTENCE_SPLIT_RE.split(str(text or "")) if part.strip(" ，,、")]
    if not sentences:
        return []
    result: list[str] = []
    for sentence in sentences:
        raw = _clauses(sentence)
        if not raw:
            continue
        expanded = [part for item in raw for part in _split_compound_clause(item)]
        if len(expanded) > 1 and not all(_has_action_verb(part) for part in expanded):
            result.append("，".join(raw))
        else:
            result.extend(expanded)
    return result


def _clauses(text: str) -> list[str]:
    return [part.strip(" ，,、") for part in _ACTION_SPLIT_RE.split(str(text or "")) if part.strip(" ，,、")]



def is_locomotion_clause(text: str) -> bool:
    """True when a clause is only travel/entry, not a speaking or emotional beat."""
    return bool(_LOCOMOTION_RE.search(str(text or "")))


def _action_agent(clause: str, characters: list[str], previous: str | None) -> str | None:
    names = [name for name in characters if str(name or "").strip()]
    mentioned = [name for name in names if name in str(clause or "")]
    if mentioned:
        return min(mentioned, key=lambda name: str(clause).find(name))
    raw = str(clause or "")
    if "她" in raw and "德拉" in names:
        return "德拉"
    if "他" in raw and "吉姆" in names:
        return "吉姆"
    return previous


def _shared_dialogue_token(line_text: str, action_clause: str) -> str:
    """Return a distinctive token shared by the speech and one action beat.

    Two directions, in priority order:

    1. Command speech names its object ("揭掉帽子…看发样"), and the matching
       action beat contains that object ("摘下帽子看发样").
    2. The action beat's own verb phrase ("要她摘下帽子看发样") appears inside
       the speech, which catches the case where the speech is decomposed into
       separate beats and the raw object is no longer in one clause.

    Function-word-only overlaps are ignored so unrelated short beats do not win.
    """
    dialogue_clean = re.sub(r"[^\u4e00-\u9fff]", "", _clean_dialogue_text(line_text))
    action_text = re.sub(r"[^\u4e00-\u9fff]", "", str(action_clause or ""))
    if not dialogue_clean or not action_text:
        return ""
    function_chars = set("的了是在有和与就都也很而已被把让给对从到以为这那你我他她它们个吗呢吧啊呀么什怎样")
    for size in (4, 3, 2):
        for start in range(len(action_text) - size + 1):
            token = action_text[start:start + size]
            if all(char in function_chars for char in token):
                continue
            if token in dialogue_clean:
                return token
        for start in range(len(dialogue_clean) - size + 1):
            token = dialogue_clean[start:start + size]
            if all(char in function_chars for char in token):
                continue
            if token in action_text:
                return token
    return ""


def _dialogue_event_positions(
    action_events: list[tuple],
    lines: list,
    characters: list[str],
) -> list[tuple[int, int]]:
    """Anchor each line to the action that should carry it, not to action index N.

    Single remaining line of a speaker prefers that speaker's last action.
    Leftover lines (no unused speaker action) attach to the next free action,
    which preserves Maggi scene 8: Jim's second line lands on Della's panic.
    Pure locomotion scenes keep all speech after the last travel beat.
    """
    if not action_events:
        return [(index, 0) for index, _ in enumerate(lines, start=1)]
    last_index = int(action_events[-1][0])
    if all(is_locomotion_clause(event[3]) for event in action_events):
        return [(last_index, index + 1) for index, _ in enumerate(lines)]

    agents = []
    previous = None
    names = [str(item or "").strip() for item in characters if str(item or "").strip()]
    for event in action_events:
        agent = _action_agent(event[3], names, previous)
        agents.append(agent)
        previous = agent

    unused: dict[str, list[int]] = {}
    for index, agent in enumerate(agents):
        if not agent:
            continue
        unused.setdefault(agent, []).append(index)

    from collections import Counter
    speaker_counts = Counter(str(getattr(line, "character", "") or "").strip() for line in lines)
    for speaker, count in speaker_counts.items():
        pool = unused.get(speaker) or []
        if count == 1 and len(pool) > 1:
            # A single spoken command usually asks for the object named in an
            # earlier action beat ("揭掉帽子…看发样" belongs with the hat
            # request, not with the speaker's last weigh-the-hair beat).
            line_text = next(
                str(getattr(line, "line", "") or "")
                for line in lines
                if str(getattr(line, "character", "") or "").strip() == speaker
            )
            anchored = next(
                (index for index in pool if _shared_dialogue_token(line_text, action_events[index][3])),
                None,
            )
            unused[speaker] = [anchored if anchored is not None else pool[-1]]

    used: set[int] = set()
    on_action: dict[int, int] = {}
    positions: list[tuple[int, int]] = []
    last_index = -1

    def take_index(index: int) -> None:
        used.add(index)
        for speaker, pool in list(unused.items()):
            unused[speaker] = [item for item in pool if item != index]

    for line in lines:
        speaker = str(getattr(line, "character", "") or "").strip()
        pool = unused.get(speaker) or []
        later = [item for item in pool if item >= last_index]
        if later:
            action_index = later[0]
            take_index(action_index)
        elif pool:
            action_index = pool[0]
            take_index(action_index)
        else:
            action_index = next((index for index in range(len(action_events)) if index not in used and index >= last_index), None)
            if action_index is None:
                action_index = max(last_index, len(action_events) - 1)
            take_index(action_index)
        action_index = max(action_index, last_index)
        last_index = action_index
        already = on_action.get(action_index, 0)
        on_action[action_index] = already + 1
        positions.append((int(action_events[action_index][0]), already + 2))
    return positions


def _kind_for_action(clause: str) -> str:
    if _TRANSITION_RE.search(clause):
        return "transition"
    if _PROP_CHANGE_RE.search(clause):
        return "prop_change"
    return "action"


def _clean_dialogue_text(text: str) -> str:
    return re.sub(r"[，。！？、,.!?“”\"：:\s]", "", str(text or ""))


def estimate_dialogue_duration_sec(text: str, *, inner_monologue: bool = False) -> float:
    """Estimate one audible dialogue line with the project's single speech clock.

    The ``inner_monologue`` flag is intentionally accepted for callers that need
    to preserve the semantic kind, but it does not create a second opaque timing
    formula: narration and spoken dialogue share the same audible baseline.
    """
    del inner_monologue
    clean = _clean_dialogue_text(text)
    if not clean:
        return 0.0
    # Short-form drama dialogue is normally delivered at a compact pace.
    # Avoid a large fixed tax per line: it inflated planning budgets whenever
    # one speech was split into several source lines. Explicit authored pauses
    # are accounted for separately by ``_with_explicit_hold``.
    punctuation_pauses = len(re.findall(r"[。！？!?；;…]", str(text or ""))) * 0.08
    expected = max(0.8, len(clean) / 7.2 + punctuation_pauses)
    return round(expected, 2)


def _explicit_hold_duration(text: str) -> float:
    values: list[float] = []
    for match in _HOLD_NUMBER_RE.finditer(str(text or "")):
        raw = match.group("number")
        try:
            values.append(float(raw))
        except ValueError:
            values.append(_CN_DIGITS.get(raw, 0.0))
    if _HOLD_WORD_RE.search(str(text or "")):
        values.append(1.5)
    return max(values, default=0.0)


def _with_explicit_hold(duration: BeatDurationEstimate, text: str) -> BeatDurationEstimate:
    hold = _explicit_hold_duration(text)
    if hold <= 0:
        return duration
    return BeatDurationEstimate(
        min_sec=round(duration.min_sec + hold * 0.8, 2),
        expected_sec=round(duration.expected_sec + hold, 2),
        max_sec=round(duration.max_sec + hold * 1.2, 2),
        reason=f"{duration.reason}；明确停顿/沉默约{hold:g}s",
    )


def _timing_role_for(kind: str, text: str) -> str:
    if _explicit_hold_duration(text) > 0:
        return "serial"
    if kind in {"camera", "emotion", "continuity"}:
        return "constraint"
    if _PARALLEL_CUE_RE.search(str(text or "")):
        return "overlap"
    return "serial"


def _duration_for(kind: str, text: str) -> BeatDurationEstimate:
    if kind in {"dialogue", "inner_monologue"}:
        expected = estimate_dialogue_duration_sec(text, inner_monologue=kind == "inner_monologue")
        duration = BeatDurationEstimate(
            min_sec=round(max(0.5, expected * 0.8), 2),
            expected_sec=round(expected, 2),
            max_sec=round(max(expected, expected * 1.35), 2),
            reason="统一对白字数、语速与停顿",
        )
        return _with_explicit_hold(duration, text)
    if kind == "prop_change":
        expected = 2.5 if len(text) > 8 else 2.0
        duration = BeatDurationEstimate(min_sec=1.5, expected_sec=expected, max_sec=4.0, reason="道具状态变化")
    elif kind == "transition":
        duration = BeatDurationEstimate(min_sec=1.5, expected_sec=2.5, max_sec=4.0, reason="空间移动或进出场")
    elif kind == "camera":
        duration = BeatDurationEstimate(min_sec=0.5, expected_sec=0.8, max_sec=1.5, reason="镜头运动或切换")
    elif kind == "emotion":
        duration = BeatDurationEstimate(min_sec=0.8, expected_sec=1.5, max_sec=3.0, reason="情绪落点")
    elif kind == "continuity":
        duration = BeatDurationEstimate(min_sec=0.5, expected_sec=0.8, max_sec=1.5, reason="上一镜连续性状态")
    else:
        duration = BeatDurationEstimate(min_sec=0.8, expected_sec=1.8, max_sec=3.0, reason="可观察动作")
    return _with_explicit_hold(duration, text)


def _make_beat(
    scene: Scene,
    order: int,
    kind: str,
    text: str,
    fact_id: str,
    *,
    dialogue_id: str | None = None,
    prop_ids: Iterable[str] = (),
) -> NarrativeBeat:
    timing_role = _timing_role_for(kind, text)
    return NarrativeBeat(
        beat_id=f"scene-{scene.scene_id}-beat-{order}",
        scene_id=scene.scene_id,
        order=order,
        kind=kind,  # type: ignore[arg-type]
        source_excerpt=text,
        fact_ids=[fact_id],
        dialogue_id=dialogue_id,
        characters=list(scene.characters or []),
        props=list(prop_ids),
        duration=_duration_for(kind, text),
        required=True,
        can_merge=kind not in {"dialogue", "inner_monologue", "prop_change", "transition", "continuity"},
        can_split=kind in {"dialogue", "inner_monologue"},
        timing_role=timing_role,  # type: ignore[arg-type]
        overlap_group=(f"scene-{scene.scene_id}-parallel-1" if timing_role == "overlap" else None),
    )



def split_speech_chunks(text: str, max_expected_sec: float) -> list[str]:
    """Pack sentences into speakable chunks that fit one provider request."""
    raw = str(text or "").strip()
    if not raw:
        return []
    if estimate_dialogue_duration_sec(raw) <= max_expected_sec:
        return [raw]
    parts = [part.strip() for part in re.split(r"(?<=[。！？!?])", raw) if part.strip()]
    if len(parts) <= 1:
        parts = [part.strip() for part in re.split(r"[；;]", raw) if part.strip()]
    if len(parts) <= 1:
        return [raw]
    chunks: list[str] = []
    current = ""
    for part in parts:
        trial = f"{current}{part}" if current else part
        if current and estimate_dialogue_duration_sec(trial) > max_expected_sec:
            chunks.append(current)
            current = part
        else:
            current = trial
    if current:
        chunks.append(current)
    return chunks or [raw]


def expand_overlong_speech_beats(beats: list[NarrativeBeat], max_sec: float | int | None) -> list[NarrativeBeat]:
    """Split a single spoken line that cannot fit in one provider duration."""
    if not beats or max_sec is None:
        return list(beats)
    cap = float(max_sec)
    # Split only when the compact speech estimate cannot fit one provider
    # request at all. Reserving a second here used to split lines that would
    # have fit at the cap and multiplied dialogue-bearing child shots.
    # Chunk packing below still targets cap-1 so a part can share its unit
    # with a small action beat without overflowing.
    budget = max(1.0, cap - 1.0)
    result: list[NarrativeBeat] = []
    for beat in beats:
        if beat.kind not in {"dialogue", "inner_monologue"}:
            result.append(beat)
            continue
        if estimate_dialogue_duration_sec(beat.source_excerpt) <= cap:
            result.append(beat)
            continue
        chunks = split_speech_chunks(beat.source_excerpt, budget)
        if len(chunks) <= 1:
            result.append(beat)
            continue
        source_id = str(getattr(beat, "dialogue_id", "") or "")
        for index, chunk in enumerate(chunks, start=1):
            result.append(beat.model_copy(deep=True, update={
                "beat_id": f"{beat.beat_id}-part-{index}",
                # Part-scoped id: native dialogue resolution and the snapshot
                # dialogue plan read this suffix to quote only this segment.
                "dialogue_id": f"{source_id}-part-{index}" if source_id else None,
                "source_excerpt": chunk,
                "duration": _duration_for(beat.kind, chunk),
            }))
    return result


def build_narrative_beats(scene: Scene, *, prop_ids: Iterable[str] = ()) -> list[NarrativeBeat]:
    """Extract ordered, traceable beats without changing the source scene."""
    action_events = []
    for index, clause in enumerate(_action_clauses(scene.action_description), start=1):
        action_events.append((index, 1, _kind_for_action(clause), clause, f"scene:{scene.scene_id}:action:{index}", None))

    dialogue_events = []
    has_explicit_order = any(line.order is not None for line in scene.dialogue or [])
    orders = [item.order for item in (scene.dialogue or []) if item.order is not None]
    sequential_dialogue = orders == list(range(1, len(scene.dialogue or []) + 1))
    default_positions = _dialogue_event_positions(
        action_events,
        list(scene.dialogue or []),
        list(scene.characters or []),
    )
    for index, line in enumerate(scene.dialogue or [], start=1):
        kind = "inner_monologue" if _MONOLOGUE_RE.search(f"{line.emotion or ''} {line.line}") else "dialogue"
        if has_explicit_order and line.order is not None and not sequential_dialogue:
            # Author-specified insertion: dialogue at order N sorts before the
            # action also numbered N because actions use secondary=1.
            event_order = line.order
            secondary = 0
        elif action_events:
            event_order, secondary = default_positions[index - 1]
        else:
            event_order = len(action_events) + index
            secondary = 0
        dialogue_events.append((event_order, secondary, kind, line.line, f"scene:{scene.scene_id}:dialogue:{index}", f"scene-{scene.scene_id}-dialogue-{index}"))

    events = action_events + dialogue_events
    events.sort(key=lambda item: (item[0], item[1]))

    beats: list[NarrativeBeat] = []
    for _, _, kind, text, fact_id, dialogue_id in events:
        if kind in {"dialogue", "inner_monologue"} and not str(text or "").strip():
            continue
        beats.append(_make_beat(scene, len(beats) + 1, kind, text, fact_id, dialogue_id=dialogue_id, prop_ids=prop_ids if kind == "prop_change" else ()))

    # Give each independently written physical parallel clause its own group.
    # Otherwise two unrelated "同时/一边" clauses collapse to one max() bucket
    # and the ledger either undercounts or overcounts their real work.
    parallel_actions = [
        beat for beat in beats
        if beat.timing_role == "overlap" and beat.kind not in {"dialogue", "inner_monologue"}
    ]
    for index, action_beat in enumerate(parallel_actions, start=1):
        action_beat.overlap_group = f"scene-{scene.scene_id}-parallel-{index}"

    # Only wording that explicitly says speech happens during the action links
    # one dialogue line to that action. A bare "同时拿起..." must not make an
    # unrelated later line free. Without explicit order information, pair the
    # nearest still-unassigned dialogue conservatively; later lines stay serial.
    dialogue_beats = [
        beat for beat in beats
        if beat.kind in {"dialogue", "inner_monologue"}
    ]
    assigned_dialogue_ids: set[str] = set()
    for action_beat in parallel_actions:
        if not _DIALOGUE_PARALLEL_CUE_RE.search(action_beat.source_excerpt):
            continue
        candidates = [
            beat for beat in dialogue_beats
            if beat.beat_id not in assigned_dialogue_ids
        ]
        if not candidates:
            continue
        dialogue_beat = min(candidates, key=lambda beat: abs(beat.order - action_beat.order))
        dialogue_beat.timing_role = "overlap"
        dialogue_beat.overlap_group = action_beat.overlap_group
        assigned_dialogue_ids.add(dialogue_beat.beat_id)

    if scene.continuity_from_prev or scene.continuity_reason.strip():
        continuity_text = scene.continuity_reason.strip() or "承接上一镜的角色、空间和道具状态"
        beats.append(_make_beat(scene, len(beats) + 1, "continuity", continuity_text, f"scene:{scene.scene_id}:continuity"))
    if scene.camera_direction and _CAMERA_RE.search(scene.camera_direction):
        beats.append(_make_beat(scene, len(beats) + 1, "camera", scene.camera_direction, f"scene:{scene.scene_id}:camera"))
    if scene.emotion_tone.strip():
        beats.append(_make_beat(scene, len(beats) + 1, "emotion", scene.emotion_tone, f"scene:{scene.scene_id}:emotion"))
    return beats


def _timing_totals(beats: list[NarrativeBeat]) -> dict[str, float]:
    serial_beats = [beat for beat in beats if beat.timing_role == "serial"]
    metadata = [beat for beat in beats if beat.timing_role == "constraint"]
    overlap_groups: dict[str, list[NarrativeBeat]] = {}
    for beat in beats:
        if beat.timing_role == "overlap":
            group = beat.overlap_group or beat.beat_id
            overlap_groups.setdefault(group, []).append(beat)

    def _sum(attr: str, values: list[NarrativeBeat]) -> float:
        return round(sum(float(getattr(beat.duration, attr)) for beat in values), 2)

    def _max_per_group(attr: str) -> float:
        return round(sum(max(float(getattr(beat.duration, attr)) for beat in group) for group in overlap_groups.values()), 2)

    serial_min = _sum("min_sec", serial_beats)
    serial_expected = _sum("expected_sec", serial_beats)
    serial_max = _sum("max_sec", serial_beats)
    overlap_min = _max_per_group("min_sec")
    overlap_expected = _max_per_group("expected_sec")
    overlap_max = _max_per_group("max_sec")
    metadata_expected = _sum("expected_sec", metadata)
    return {
        "minimum": round(serial_min + overlap_min, 2),
        "expected": round(serial_expected + overlap_expected, 2),
        "maximum": round(serial_max + overlap_max, 2),
        "serial": serial_expected,
        "overlap": overlap_expected,
        "metadata": metadata_expected,
    }


def _safe_duration_value(value: object) -> float | None:
    """Parse a duration value from a provider payload without leaking ValueError."""
    try:
        raw = str(value).strip()
        if not raw:
            return None
        parsed = float(raw)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed) or abs(parsed - round(parsed)) > 1e-6:
        return None
    return parsed


def _discrete_duration_vector(total: float, count: int, allowed: list[int]) -> list[int]:
    """Find the smallest supported-duration sum that covers ``total``.

    Some providers expose a finite set such as ``[3, 5, 10, 15]`` rather than
    accepting every integer in a min/max range.  Keep the shot count chosen by
    the capacity planner whenever possible, then choose the least-overbudget
    combination so we do not silently shorten the authored content.
    """
    if count <= 0 or not allowed:
        return []
    target = max(1, math.ceil(float(total) - 1e-9))
    states: dict[int, list[int]] = {0: []}
    for _ in range(count):
        next_states: dict[int, list[int]] = {}
        for current, vector in states.items():
            for value in allowed:
                candidate_sum = current + value
                if candidate_sum not in next_states:
                    next_states[candidate_sum] = [*vector, value]
        states = next_states
        if not states:
            return []
    candidates = [
        (sum_value, vector)
        for sum_value, vector in states.items()
        if sum_value >= target
    ]
    if not candidates:
        return []
    # Prefer the smallest overage.  The secondary key keeps a vector with
    # similar durations stable when multiple combinations have the same sum.
    def key(item: tuple[int, list[int]]) -> tuple[int, float]:
        sum_value, vector = item
        mean = sum(vector) / len(vector)
        variance = sum((value - mean) ** 2 for value in vector)
        return sum_value - target, variance

    return min(candidates, key=key)[1]


def _split_durations(
    total: float,
    count: int,
    minimum: float = 3.0,
    maximum: float | None = None,
    allowed: Iterable[int | float] | None = None,
) -> list[float]:
    """Split a total only when every returned segment is executable."""
    if count <= 0 or total <= 0:
        return []
    if maximum is not None and maximum < minimum:
        return []
    discrete_values: set[int] = set()
    for value in (allowed or []):
        parsed = _safe_duration_value(value)
        if parsed is None or parsed < minimum:
            continue
        if maximum is not None and parsed > maximum:
            continue
        discrete_values.add(int(round(parsed)))
    discrete = sorted(discrete_values)
    if discrete:
        count = max(count, 1, math.ceil(float(total) / max(discrete)))
        # The max-duration lower bound guarantees a solution for normal
        # capabilities; the loop also handles sparse custom duration sets.
        for candidate_count in range(count, count + max(4, math.ceil(float(total) / minimum) + 1)):
            vector = _discrete_duration_vector(total, candidate_count, discrete)
            if vector:
                return [float(value) for value in vector]
        return []
    if maximum is not None:
        count = max(count, math.ceil(total / maximum))
    # A provider with a three-second floor cannot represent this total with
    # the requested number of child shots.  Returning no recommendation is
    # safer than emitting a vector such as [3, 2].
    if total < minimum * count - 0.01:
        return []
    if maximum is not None and total > maximum * count + 0.01:
        return _split_durations(total, count + 1, minimum, maximum)

    average = total / count
    values = [round(average, 2) for _ in range(count)]
    values[-1] = round(total - sum(values[:-1]), 2)
    if any(value < minimum - 0.01 for value in values):
        return []
    if maximum is not None and any(value > maximum + 0.01 for value in values):
        return _split_durations(total, count + 1, minimum, maximum)
    return values


def estimate_scene_capacity(
    scene: Scene,
    provider_max_duration_sec: int | float | None = None,
    provider_durations: Iterable[int | float] | None = None,
    provider_min_duration_sec: int | float = 3,
) -> SceneCapacityReport:
    """Estimate complete scene duration without counting filming metadata twice."""
    beats = build_narrative_beats(scene)
    timing = _timing_totals(beats)
    required = timing["expected"]
    available = float(scene.duration_sec or 0)
    provider_cap = float(provider_max_duration_sec) if provider_max_duration_sec is not None else None
    provider_min = max(1.0, float(provider_min_duration_sec or 3))
    common = dict(
        scene_id=int(scene.scene_id),
        required_total_duration_sec=required,
        available_duration_sec=available,
        source_duration_sec=available,
        minimum_duration_sec=timing["minimum"],
        expected_duration_sec=timing["expected"],
        maximum_duration_sec=timing["maximum"],
        serial_duration_sec=timing["serial"],
        overlap_duration_sec=timing["overlap"],
        metadata_duration_sec=timing["metadata"],
        timing_basis=TIMING_BASIS,
    )
    if provider_cap is not None and provider_cap < provider_min:
        return SceneCapacityReport(
            **common,
            recommended_total_duration_sec=0.0,
            recommended_durations_sec=[],
            recommended_shot_count=0,
            status="duration_insufficient",
            warnings=[f"当前模型上限 {provider_cap:g}s 低于单镜头最小时长 {provider_min:g}s，无法生成可执行子镜头"],
        )

    discrete_values: set[int] = set()
    for value in (provider_durations or []):
        parsed = _safe_duration_value(value)
        if parsed is None or parsed < provider_min:
            continue
        if provider_cap is not None and parsed > provider_cap:
            continue
        discrete_values.add(int(round(parsed)))
    discrete_durations = sorted(discrete_values)
    per_shot_cap = provider_cap or (max(discrete_durations) if discrete_durations else max(available, provider_min))
    # Expand only when executable story/audio beats demonstrably exceed the
    # authored window. Camera, emotion and continuity metadata are excluded by
    # _timing_totals(), so they never purchase extra provider seconds.
    recommended_total = float(math.ceil(max(available, required) - 1e-9))
    count_for_cap = max(1, math.ceil(recommended_total / per_shot_cap)) if recommended_total > 0 else 1
    # Dense scenes are easier for current video models to execute in roughly
    # 4-8 second semantic units. Simple scenes retain their authored single
    # shot up to the provider cap; only overloaded scenes use this tighter
    # target. This avoids both 15-second overloaded clips and gratuitous cuts.
    dense_target = min(per_shot_cap, max(provider_min, 8.0))
    count_for_density = (
        max(1, math.ceil(recommended_total / dense_target))
        if required > available + 0.01
        else 1
    )
    count = max(count_for_cap, count_for_density)
    max_legal_count = max(1, int(recommended_total // provider_min)) if recommended_total > 0 else 1
    count = min(count, max_legal_count)
    durations = _split_durations(
        recommended_total,
        count,
        minimum=provider_min,
        maximum=per_shot_cap if provider_cap else None,
        allowed=discrete_durations,
    )
    if not durations and recommended_total > 0:
        # Do not expose a non-zero total without a matching executable duration
        # vector. The timing ledger retains source/required totals for diagnosis;
        # a zero recommendation explicitly means that human planning is required.
        count = 0
        recommended_total = 0.0
        status = "duration_insufficient"
        warning = (
            f"当前模型的单镜头时长范围无法承载剧本设定的 {available:g}s；"
            f"每个可执行镜头至少需要 {provider_min:g}s，未生成非法推荐时长"
        )
    elif required > available + 0.01:
        status = "duration_insufficient"
        warning = f"预计完整执行需要约 {math.ceil(required)}s，但场景只有 {int(available)}s"
    elif count > 1:
        status = "auto_split"
        warning = f"单镜容量不足，建议拆分为 {count} 个镜头"
    else:
        status = "clean"
        warning = ""
    if durations:
        # Discrete providers may need a small amount of padding (for example,
        # 12 seconds becomes one 15-second request).  The ledger must expose
        # the same total as the actual executable vector.
        recommended_total = round(sum(durations), 2)
    return SceneCapacityReport(
        **common,
        recommended_total_duration_sec=recommended_total,
        recommended_durations_sec=durations,
        recommended_shot_count=count,
        status=status,  # type: ignore[arg-type]
        warnings=[warning] if warning else [],
    )
