from pathlib import Path
from types import SimpleNamespace

from src.api import context as api_context
from src.agents.video_gen_agent import VideoGenerationAgent
from src.schemas.character import CharacterImage, CharacterImageAssets
from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.scene_image import SceneImage, SceneImageAssets


def test_warning_or_stale_asset_never_allows_automatic_reference():
    warning = SimpleNamespace(
        quality_report={"status": "warning", "automatic_reference_allowed": True},
        is_stale=False,
        needs_confirmation=False,
    )
    stale_passed = SimpleNamespace(
        quality_report={"status": "passed"},
        is_stale=True,
        needs_confirmation=False,
    )

    assert api_context._image_allows_automatic_reference(warning) is False
    assert api_context._image_allows_automatic_reference(stale_passed) is False


def test_batch_reference_bundle_filters_unreviewed_character_and_scene(tmp_path):
    character_path = tmp_path / "character.png"
    scene_path = tmp_path / "scene.png"
    character_path.write_bytes(b"png")
    scene_path.write_bytes(b"png")
    character = CharacterImage(
        character_name="Hero",
        view_type="front_half",
        image_prompt="hero",
        image_path=str(character_path),
        seed=1,
        quality_report={"status": "warning"},
    )
    scene = SceneImage(
        scene_id=1,
        location_id="loc_room",
        location="room",
        view_type="scene_center",
        image_prompt="room",
        image_path=str(scene_path),
        seed=1,
        quality_report={"status": "passed"},
        needs_confirmation=True,
    )
    prompts = GenerationPrompts(
        api_provider="mock",
        shots=[ShotPrompt(scene_id=1, prompt="shot", duration_sec=3)],
    )
    script = SimpleNamespace(
        scenes=[SimpleNamespace(scene_id=1, characters=["Hero"], location="room")],
        asset_manifest=None,
    )

    references = VideoGenerationAgent()._build_reference_bundle(
        prompts,
        CharacterImageAssets(characters=[character]),
        SceneImageAssets(scenes=[scene]),
        script,
        None,
    )

    assert references == {1: []}
