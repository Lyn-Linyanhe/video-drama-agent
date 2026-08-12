from fastapi.testclient import TestClient

import api_server
from src.api import context as api_context
from src.graph.state import DramaState
from src.schemas.script import (
    LocationAsset,
    PropAsset,
    Scene,
    ScriptBreakdown,
    ShotVisualBinding,
    VisualAssetManifest,
)
from src.services.prop_view_requirements import (
    PropViewRequirement,
    infer_prop_view_requirement,
    merge_prop_view_requirement,
    normalize_prop_views,
    set_prop_view_requirement,
)
from src.utils.project_store import ProjectStore
import src.utils.project_store as project_store_module


def _script(camera: str = "close-up", action: str = "A hand turns the key.") -> ScriptBreakdown:
    return ScriptBreakdown(
        title="Prop views",
        genre="Suspense",
        logline="A key reveals a secret.",
        duration_sec=8,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=4,
                location="Archive",
                characters=[],
                action_description=action,
                camera_direction=camera,
                emotion_tone="tense",
            )
        ],
        asset_manifest=VisualAssetManifest(
            locations=[
                LocationAsset(
                    location_id="archive",
                    canonical_name="Archive",
                    representative_scene_id=1,
                    shot_ids=[1],
                )
            ],
            props=[
                PropAsset(
                    prop_id="key",
                    name="Old brass key",
                    description="A brass key with green oxidation and complex teeth.",
                    shot_ids=[1],
                    critical_reason="plot",
                    critical_evidence="The key opens the archive drawer.",
                    is_handheld=True,
                )
            ],
            shot_bindings=[
                ShotVisualBinding(scene_id=1, location_id="archive", prop_ids=["key"])
            ],
        ),
    )


def _state(camera: str = "close-up", action: str = "A hand turns the key.") -> DramaState:
    return DramaState(project_id="project", user_input="test", script=_script(camera, action))


def test_default_prop_requirement_is_front_only():
    requirement = infer_prop_view_requirement(_state(), "key")

    assert requirement.views == ("front",)
    assert requirement.source == "auto"
    assert requirement.signature


def test_camera_cues_add_only_needed_prop_views():
    state = _state(
        camera="left side close-up from above",
        action="The hand flips the key to show the back engraving.",
    )

    requirement = infer_prop_view_requirement(state, "key")

    assert requirement.views == ("front", "back", "left", "top")
    assert {"back_cue", "left_cue", "top_cue"} <= set(requirement.reasons)


def test_manual_views_are_merged_with_auto_requirements():
    saved = PropViewRequirement(prop_id="key", views=("front", "bottom"), source="manual", reasons=["manual"])

    merged = merge_prop_view_requirement(saved, ("front", "back"), "key", ["back_cue"])

    assert merged.views == ("front", "back", "bottom")
    assert merged.source == "manual"
    assert {"back_cue", "manual"} <= set(merged.reasons)


def test_set_prop_view_requirement_normalizes_and_persists():
    state = _state()

    requirement = set_prop_view_requirement(state, "key", ["top", "front", "top"])

    assert requirement.views == ("front", "top")
    assert state.prop_view_requirements["key"]["source"] == "manual"
    assert normalize_prop_views(["bad"]) == ("front",)


def test_prop_view_requirement_api_round_trips(tmp_path, monkeypatch):
    monkeypatch.setattr(project_store_module, "OUTPUT_DIR", tmp_path)
    store = ProjectStore()
    monkeypatch.setattr(api_context.project_store, "_target", store)
    project_id = store.create_project("Prop views")
    state = _state()
    state.project_id = project_id
    store.set_script(project_id, state.script.model_dump())
    store.set_state_json(project_id, api_context._state_to_json(state))
    client = TestClient(api_server.app)

    initial = client.get(f"/api/projects/{project_id}/props/key/view-requirements")
    updated = client.put(
        f"/api/projects/{project_id}/props/key/view-requirements",
        json={"views": ["right", "top"]},
    )
    loaded = client.get(f"/api/projects/{project_id}/props/key/view-requirements")

    assert initial.status_code == 200
    assert initial.json()["views"] == ["front"]
    assert updated.status_code == 200
    assert updated.json()["views"] == ["front", "right", "top"]
    assert loaded.json()["source"] == "manual"
