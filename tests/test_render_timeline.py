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



def test_active_video_durations_sums_child_shots_in_order(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import moviepy
    from src.services.render_timeline import active_video_durations

    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"one")
    second.write_bytes(b"two")

    opened = []

    class FakeClip:
        def __init__(self, path):
            opened.append(str(path))
            self.duration = 3.0 if str(path).endswith("first.mp4") else 4.0
        def close(self):
            pass

    monkeypatch.setattr(moviepy, "VideoFileClip", FakeClip)
    state = SimpleNamespace(
        shot_productions={1: SimpleNamespace(
            active_version_id="v2",
            video_versions=[
                SimpleNamespace(shot_id="scene-1-shot-2", local_path=str(second), success=True, version_id="v2"),
                SimpleNamespace(shot_id="scene-1-shot-1", local_path=str(first), success=True, version_id="v1"),
            ],
        )},
        generated_assets=None,
    )

    assert active_video_durations(state) == {1: 7.0}
    assert opened == [str(first), str(second)]


def test_active_video_durations_uses_active_child_version_map(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import moviepy
    from src.services.render_timeline import active_video_durations

    old = tmp_path / "old.mp4"
    new = tmp_path / "new.mp4"
    old.write_bytes(b"old")
    new.write_bytes(b"new")

    class FakeClip:
        def __init__(self, path):
            self.duration = 9.0 if str(path).endswith("old.mp4") else 3.0
        def close(self):
            pass

    monkeypatch.setattr(moviepy, "VideoFileClip", FakeClip)
    state = SimpleNamespace(
        shot_productions={1: SimpleNamespace(
            active_version_by_shot_id={"scene-1-shot-1": "new"},
            video_versions=[
                SimpleNamespace(shot_id="scene-1-shot-1", local_path=str(old), success=True, version_id="old"),
                SimpleNamespace(shot_id="scene-1-shot-1", local_path=str(new), success=True, version_id="new"),
            ],
        )},
        generated_assets=None,
    )

    assert active_video_durations(state) == {1: 3.0}


def test_active_video_durations_orders_children_by_persisted_shot_order(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import moviepy
    from src.services.render_timeline import active_video_durations

    first = tmp_path / "first.mp4"
    second = tmp_path / "second.mp4"
    first.write_bytes(b"one")
    second.write_bytes(b"two")

    opened = []

    class FakeClip:
        def __init__(self, path):
            opened.append(str(path))
            self.duration = 3.0 if str(path).endswith("first.mp4") else 4.0
        def close(self):
            pass

    monkeypatch.setattr(moviepy, "VideoFileClip", FakeClip)
    state = SimpleNamespace(
        shot_productions={1: SimpleNamespace(
            video_versions=[
                SimpleNamespace(shot_id="opaque-a", shot_order=2, local_path=str(second), success=True, version_id="b"),
                SimpleNamespace(shot_id="opaque-z", shot_order=1, local_path=str(first), success=True, version_id="a"),
            ],
        )},
        generated_assets=None,
    )

    assert active_video_durations(state) == {1: 7.0}
    assert opened == [str(first), str(second)]
