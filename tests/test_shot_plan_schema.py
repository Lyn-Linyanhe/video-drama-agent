import pytest
from pydantic import ValidationError

from src.schemas.shot_plan import (
    BeatDurationEstimate,
    NarrativeBeat,
    SceneCapacityReport,
    ShotPlan,
    ShotUnit,
)


def test_shot_unit_keeps_parent_and_source_traceability():
    unit = ShotUnit(
        shot_id="scene-6-b",
        parent_scene_id=6,
        order=2,
        beat_ids=["scene-6-beat-3", "scene-6-beat-4"],
        source_fact_ids=["scene:6:action:hair", "scene:6:dialogue:1"],
        dialogue_ids=["scene-6-dialogue-1"],
        duration_sec=7,
        prompt_status="planned",
    )

    assert unit.shot_id == "scene-6-b"
    assert unit.parent_scene_id == 6
    assert unit.order == 2
    assert unit.beat_ids == ["scene-6-beat-3", "scene-6-beat-4"]


def test_scene_capacity_report_separates_required_available_and_per_shot_duration():
    report = SceneCapacityReport(
        scene_id=6,
        required_total_duration_sec=22,
        available_duration_sec=8,
        recommended_total_duration_sec=22,
        recommended_durations_sec=[7, 7, 8],
        recommended_shot_count=3,
        status="duration_insufficient",
    )

    assert report.required_total_duration_sec == 22
    assert report.available_duration_sec == 8
    assert sum(report.recommended_durations_sec) == report.recommended_total_duration_sec
    assert report.recommended_shot_count == 3
    assert report.status == "duration_insufficient"


def test_capacity_report_rejects_duration_vector_count_mismatch():
    with pytest.raises(ValidationError):
        SceneCapacityReport(
            scene_id=1,
            required_total_duration_sec=16,
            available_duration_sec=8,
            recommended_total_duration_sec=16,
            recommended_durations_sec=[8],
            recommended_shot_count=2,
            status="auto_split",
        )


def test_narrative_beat_requires_stable_source_identity_and_duration_estimate():
    beat = NarrativeBeat(
        beat_id="scene-10-beat-3",
        scene_id=10,
        order=3,
        kind="prop_change",
        source_excerpt="打开纸包，露出发梳",
        fact_ids=["scene:10:action:open-package"],
        required=True,
        duration=BeatDurationEstimate(
            min_sec=2.5,
            expected_sec=3.0,
            max_sec=4.0,
            reason="复杂道具操作",
        ),
    )

    assert beat.beat_id == "scene-10-beat-3"
    assert beat.duration.expected_sec == 3.0
    assert beat.required is True


def test_shot_plan_rejects_duplicate_shot_ids():
    with pytest.raises(ValidationError):
        ShotPlan(
            scene_id=10,
            parent_duration_sec=8,
            units=[
                ShotUnit(shot_id="scene-10-a", parent_scene_id=10, order=1, duration_sec=4),
                ShotUnit(shot_id="scene-10-a", parent_scene_id=10, order=2, duration_sec=4),
            ],
        )
