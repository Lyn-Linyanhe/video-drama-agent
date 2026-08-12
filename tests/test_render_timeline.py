from src.schemas.script import Scene
from src.services.render_timeline import build_render_timeline


def _scene(scene_id: int, duration: int, transition: str = "cut", transition_duration: float = 0.25):
    return Scene(
        scene_id=scene_id,
        duration_sec=duration,
        location="room",
        characters=[],
        dialogue=[],
        action_description="action",
        camera_direction="wide",
        emotion_tone="calm",
        transition_from_prev=transition,
        transition_duration_sec=transition_duration,
    )


def test_cut_timeline_uses_actual_clip_durations():
    timeline = build_render_timeline(
        [_scene(1, 3), _scene(2, 5)],
        {1: 3.4, 2: 4.6},
    )
    assert timeline.scene_starts_ms == {1: 0, 2: 3400}
    assert timeline.scene_durations_ms == {1: 3400, 2: 4600}
    assert timeline.total_duration_ms == 8000


def test_legacy_dissolve_timeline_overlaps_only_at_boundary():
    timeline = build_render_timeline(
        [_scene(1, 3), _scene(2, 5, "dissolve", 0.5)],
        {1: 3.0, 2: 5.0},
    )
    assert timeline.scene_starts_ms[2] == 2500
    assert timeline.overlap_ms[2] == 500
    assert timeline.total_duration_ms == 7500

