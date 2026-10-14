"""Build executable shot plans from traceable narrative beats."""

from __future__ import annotations

import math

from src.schemas.script import Scene
from src.schemas.shot_plan import NarrativeBeat, ShotPlan, ShotUnit
from src.services.beat_planner import (
    _discrete_duration_vector,
    _timing_totals,
    build_narrative_beats,
    estimate_scene_capacity,
    expand_overlong_speech_beats,
    is_locomotion_clause,
)

_SPEECH_LANDING_SEC = 1.0


def _partition_beats(beats: list[NarrativeBeat], count: int) -> list[list[NarrativeBeat]]:
    """Partition at action/dialogue boundaries, not by equal duration slices."""
    if not beats:
        return [[] for _ in range(count)]
    count = max(1, count)
    if count > len(beats):
        return [list(beats) for _ in range(count)]
    groups: list[list[NarrativeBeat]] = []
    current: list[NarrativeBeat] = []
    for beat in beats:
        starts_new_action = (
            current
            and beat.kind in {"action", "micro_action", "prop_change", "transition"}
            and current[-1].kind in {"dialogue", "inner_monologue"}
        )
        if starts_new_action:
            groups.append(current)
            current = [beat]
        else:
            current.append(beat)
    if current:
        groups.append(current)
    refined: list[list[NarrativeBeat]] = []
    for group in groups:
        speech = [beat for beat in group if beat.kind in {"dialogue", "inner_monologue"}]
        action = [beat for beat in group if beat.kind not in {"dialogue", "inner_monologue"}]
        if action and speech:
            locomotion_only = (
                len(action) > 1
                and all(is_locomotion_clause(beat.source_excerpt) for beat in action)
            )
            if locomotion_only:
                refined.append(action)
                refined.extend([item] for item in speech)
            elif len(action) > 1:
                refined.append(action[:-1])
                refined.append(action[-1:] + speech[:1])
                refined.extend([item] for item in speech[1:])
            elif len(speech) > 1:
                refined.append(action + speech[:1])
                refined.extend([item] for item in speech[1:])
            else:
                refined.append(group)
        else:
            refined.append(group)
    groups = [group for group in refined if group]
    # Story boundaries outrank a duration-derived shot count. Merging leftover
    # speech back into the previous action recreates the overloaded last shot.
    if len(groups) < count:
        while len(groups) < count and any(len(group) > 1 for group in groups):
            index = max(range(len(groups)), key=lambda i: len(groups[i]))
            group = groups.pop(index)
            mid = max(1, len(group) // 2)
            groups[index:index] = [group[:mid], group[mid:]]
    return [group for group in groups if group]


def duration_for_beats(
    group: list[NarrativeBeat],
    *,
    minimum: int = 3,
    maximum: int | None = None,
    allowed: list[int] | None = None,
) -> int:
    """Size one executable unit from its own beats, not a parent budget share."""
    if not group:
        duration = max(1, int(minimum))
    else:
        expected = float(_timing_totals(group)["expected"])
        speech_clock = sum(
            float(beat.duration.expected_sec)
            for beat in group
            if beat.kind in {"dialogue", "inner_monologue"}
        )
        need = expected
        if speech_clock:
            need = max(need, speech_clock + _SPEECH_LANDING_SEC)
        duration = max(int(minimum), math.ceil(need - 1e-9))
    if maximum is not None:
        duration = min(int(maximum), duration)
        duration = max(int(minimum), duration)
    if allowed:
        legal = [
            int(item) for item in sorted(set(allowed))
            if int(item) >= int(minimum) and (maximum is None or int(item) <= int(maximum))
        ]
        if legal:
            next_values = [item for item in legal if item >= duration]
            duration = next_values[0] if next_values else legal[-1]
    return duration


def _integer_durations(
    values: list[float],
    total: float,
    maximum: int | None,
    allowed: list[int] | None = None,
    minimum: int = 3,
) -> list[int]:
    """Round planned durations while preserving an integral scene budget."""
    if not values:
        return []
    if allowed:
        vector = _discrete_duration_vector(
            max(float(total), float(minimum) * len(values)),
            len(values),
            sorted({int(item) for item in allowed if int(item) >= minimum and (maximum is None or int(item) <= maximum)}),
        )
        if vector:
            return vector
    # Never round a required fractional duration down: a 14.5s timing
    # estimate must become at least a 15s executable budget, not 14s.
    target = max(minimum * len(values), math.ceil(float(total) - 1e-9))
    durations = [max(minimum, int(round(value))) for value in values]
    if maximum is not None:
        durations = [min(maximum, value) for value in durations]
    delta = target - sum(durations)
    index = len(durations) - 1
    while delta != 0 and durations:
        if delta > 0:
            room = (maximum - durations[index]) if maximum is not None else delta
            step = min(delta, max(0, room))
            durations[index] += step
            delta -= step
        else:
            room = durations[index] - minimum
            step = min(-delta, max(0, room))
            durations[index] -= step
            delta += step
        index = (index - 1) % len(durations)
        if all(value <= minimum or (maximum is not None and value >= maximum) for value in durations) and delta:
            break
    return durations


def build_shot_plan(
    scene: Scene,
    provider_max_duration_sec: int | float | None = None,
    *,
    provider_min_duration_sec: int | float = 3,
    prop_ids: list[str] | tuple[str, ...] = (),
    provider_durations: list[int] | tuple[int, ...] | None = None,
) -> ShotPlan:
    """Build ordered, executable units without mutating the source scene."""
    beats = build_narrative_beats(scene, prop_ids=prop_ids)
    if provider_max_duration_sec:
        beats = expand_overlong_speech_beats(beats, provider_max_duration_sec)
    capacity = estimate_scene_capacity(
        scene,
        provider_max_duration_sec=provider_max_duration_sec,
        provider_durations=provider_durations,
        provider_min_duration_sec=provider_min_duration_sec,
    )
    if capacity.status == "duration_insufficient" and not capacity.recommended_durations_sec:
        # The scene content cannot be expressed within the provider's duration
        # range at all (e.g. cap below the 3s minimum). Keep an executable
        # plan empty so downstream materialization fails hard with the capacity
        # evidence instead of silently falling back to an over-capacity parent.
        return ShotPlan(
            scene_id=int(scene.scene_id),
            parent_duration_sec=float(scene.duration_sec),
            capacity=capacity,
            units=[],
        )

    story_beats = [beat for beat in beats if beat.kind not in {"continuity", "camera", "emotion"}]
    groups = _partition_beats(story_beats or beats, capacity.recommended_shot_count)
    minimum = max(1, int(provider_min_duration_sec or 3))
    maximum = int(provider_max_duration_sec) if provider_max_duration_sec else None
    allowed = list(provider_durations) if provider_durations else None
    authored = float(scene.duration_sec or 0)
    durations: list[int] = []
    for group in groups:
        need = duration_for_beats(
            group,
            minimum=minimum,
            maximum=maximum,
            allowed=allowed,
        )
        if len(groups) == 1 and authored > 0:
            # A single remaining unit may keep cinematic authored length, but
            # never above the provider cap and never below the content clock.
            target = authored if maximum is None else min(authored, float(maximum))
            need = max(need, duration_for_beats(
                group,
                minimum=max(minimum, math.ceil(target - 1e-9)),
                maximum=maximum,
                allowed=allowed,
            ))
        durations.append(need)

    units: list[ShotUnit] = []
    for index, (group, duration) in enumerate(zip(groups, durations), start=1):
        beat_ids = [beat.beat_id for beat in group]
        fact_ids = list(dict.fromkeys(fact_id for beat in group for fact_id in beat.fact_ids))
        dialogue_ids = list(dict.fromkeys(beat.dialogue_id for beat in group if beat.dialogue_id))
        units.append(ShotUnit(
            shot_id=f"scene-{scene.scene_id}-shot-{index}",
            parent_scene_id=int(scene.scene_id),
            order=index,
            beat_ids=beat_ids,
            source_fact_ids=fact_ids,
            dialogue_ids=dialogue_ids,
            duration_sec=duration,
            continuity_from_shot_id=units[-1].shot_id if units else None,
            prompt_status="planned",
        ))
    return ShotPlan(
        scene_id=int(scene.scene_id),
        parent_duration_sec=float(scene.duration_sec),
        capacity=capacity,
        units=units,
    )
