import json

from src.api.context import _state_from_json


def test_legacy_project_scene_duration_is_migrated_to_three_seconds():
    """Old projects with one/two-second beats remain loadable under the new rule."""
    state_json = json.dumps({
        "project_id": "legacy-project",
        "script": {
            "title": "legacy",
            "genre": "test",
            "logline": "test",
            "duration_sec": 2,
            "narration_style": "none",
            "scenes": [{
                "scene_id": 1,
                "duration_sec": 1,
                "location": "room",
                "characters": [],
                "dialogue": [],
                "action_description": "wait",
                "camera_direction": "wide",
                "emotion_tone": "calm",
            }],
        },
    })

    state = _state_from_json(state_json)

    assert state.script is not None
    assert state.script.scenes[0].duration_sec == 3
