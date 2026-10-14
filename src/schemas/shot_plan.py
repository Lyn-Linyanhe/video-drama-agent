"""Traceable narrative beat and executable shot planning schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


BeatKind = Literal[
    "establish",
    "action",
    "micro_action",
    "dialogue",
    "inner_monologue",
    "prop_change",
    "emotion",
    "camera",
    "transition",
    "continuity",
]

PlanningStatus = Literal[
    "planned",
    "ready",
    "generated",
    "failed",
]

TimingRole = Literal["serial", "overlap", "constraint"]

CapacityStatus = Literal[
    "clean",
    "warning",
    "auto_split",
    "duration_insufficient",
    "dialogue_overflow",
    "beat_overflow",
    "timeline_invalid",
    "manual_decision_required",
]


class BeatDurationEstimate(BaseModel):
    """A transparent duration range for one narrative beat."""

    min_sec: float = Field(ge=0)
    expected_sec: float = Field(ge=0)
    max_sec: float = Field(ge=0)
    reason: str = Field(default="")

    @model_validator(mode="after")
    def validate_order(self) -> "BeatDurationEstimate":
        if self.min_sec > self.expected_sec or self.expected_sec > self.max_sec:
            raise ValueError("beat duration must satisfy min_sec <= expected_sec <= max_sec")
        return self


class NarrativeBeat(BaseModel):
    """An ordered, source-traceable unit of narrative content."""

    beat_id: str = Field(min_length=1)
    scene_id: int = Field(ge=1)
    order: int = Field(ge=1)
    kind: BeatKind
    source_excerpt: str = Field(default="")
    fact_ids: list[str] = Field(default_factory=list)
    dialogue_id: str | None = None
    characters: list[str] = Field(default_factory=list)
    props: list[str] = Field(default_factory=list)
    state_before: str = Field(default="")
    state_after: str = Field(default="")
    required: bool = True
    can_merge: bool = True
    can_split: bool = False
    timing_role: TimingRole = "serial"
    overlap_group: str | None = None
    duration: BeatDurationEstimate


class ShotUnit(BaseModel):
    """One independently executable video-generation unit."""

    shot_id: str = Field(min_length=1)
    parent_scene_id: int = Field(ge=1)
    order: int = Field(ge=1)
    beat_ids: list[str] = Field(default_factory=list)
    source_fact_ids: list[str] = Field(default_factory=list)
    dialogue_ids: list[str] = Field(default_factory=list)
    duration_sec: int = Field(ge=3, le=60, default=5)
    prompt: str = Field(default="")
    continuity_from_shot_id: str | None = None
    continuity_to_shot_id: str | None = None
    prompt_status: PlanningStatus = "planned"


class ShotPlan(BaseModel):
    """Parent scene plan containing ordered, executable shot units."""

    scene_id: int = Field(ge=1)
    parent_duration_sec: float = Field(ge=0)
    capacity: "SceneCapacityReport"
    units: list[ShotUnit] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_units(self) -> "ShotPlan":
        ids = [unit.shot_id for unit in self.units]
        if len(ids) != len(set(ids)):
            raise ValueError("shot_id must be unique within a shot plan")
        if any(unit.parent_scene_id != self.scene_id for unit in self.units):
            raise ValueError("all shot units must belong to the plan scene_id")
        orders = [unit.order for unit in self.units]
        if orders and orders != list(range(1, len(orders) + 1)):
            raise ValueError("shot unit order must be contiguous and start at 1")
        return self


class SceneCapacityReport(BaseModel):
    """Scene-level capacity report before prompt generation.

    ``required_total_duration_sec`` and ``available_duration_sec`` are retained
    for legacy callers.  The timing ledger below makes clear which seconds are
    serial story beats, which can overlap, and which are only filming metadata.
    """

    scene_id: int = Field(ge=1)
    required_total_duration_sec: float = Field(default=0, ge=0)
    available_duration_sec: float = Field(default=0, ge=0)
    source_duration_sec: float | None = Field(default=None, ge=0)
    minimum_duration_sec: float = Field(default=0, ge=0)
    expected_duration_sec: float = Field(default=0, ge=0)
    maximum_duration_sec: float = Field(default=0, ge=0)
    serial_duration_sec: float = Field(default=0, ge=0)
    overlap_duration_sec: float = Field(default=0, ge=0)
    metadata_duration_sec: float = Field(default=0, ge=0)
    timing_basis: str = Field(default="")
    recommended_total_duration_sec: float = Field(default=0, ge=0)
    recommended_durations_sec: list[float] = Field(default_factory=list)
    recommended_shot_count: int = Field(default=0, ge=0)
    status: CapacityStatus = "clean"
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_recommendation(self) -> "SceneCapacityReport":
        # Legacy payloads only have required/available.  Treat that historical
        # estimate as an exact range rather than inventing a wider confidence
        # interval, while exposing the new fields to current callers.
        if self.source_duration_sec is None:
            self.source_duration_sec = self.available_duration_sec
        if self.expected_duration_sec <= 0 and self.required_total_duration_sec > 0:
            self.expected_duration_sec = self.required_total_duration_sec
        if self.required_total_duration_sec <= 0 and self.expected_duration_sec > 0:
            self.required_total_duration_sec = self.expected_duration_sec
        # An explicit zero is meaningful for an all-overlap scene; only backfill
        # legacy payloads that omitted the field entirely.
        if "serial_duration_sec" not in self.model_fields_set and self.expected_duration_sec > 0:
            self.serial_duration_sec = self.expected_duration_sec
        if self.minimum_duration_sec <= 0 and self.expected_duration_sec > 0:
            self.minimum_duration_sec = self.expected_duration_sec
        if self.maximum_duration_sec <= 0 and self.expected_duration_sec > 0:
            self.maximum_duration_sec = self.expected_duration_sec

        if not (
            self.minimum_duration_sec
            <= self.expected_duration_sec
            <= self.maximum_duration_sec
        ):
            raise ValueError(
                "scene duration must satisfy minimum_duration_sec <= expected_duration_sec <= maximum_duration_sec"
            )
        if self.recommended_shot_count != len(self.recommended_durations_sec):
            raise ValueError("recommended_shot_count must match recommended_durations_sec")
        if any(duration < 3 for duration in self.recommended_durations_sec):
            raise ValueError("recommended durations must be at least 3 seconds")
        if self.recommended_durations_sec:
            total = sum(self.recommended_durations_sec)
            if abs(total - self.recommended_total_duration_sec) > 0.01:
                raise ValueError("recommended durations must sum to recommended total duration")
        elif self.recommended_total_duration_sec:
            raise ValueError("recommended durations are required when recommended total is non-zero")
        return self


ShotPlan.model_rebuild()
