from src.api.context import _build_video_generation_snapshot
from src.agents.video_gen_agent import VideoGenerationAgent
from src.schemas.project import CreativeProfile
from src.schemas.prompt import GenerationPrompts, ShotPrompt
from tests.test_shot_production_state import _state


def test_video_snapshot_freezes_canonical_style_selection():
    state = _state()
    state.creative_profile = CreativeProfile(
        visual_style_id="semi_realistic_anime",
        art_style="半写实动漫",
        style_modifiers=["清新"],
    )
    policy = {
        "provider": "mock",
        "model": "mock",
        "capability": {"image_to_video": False, "multi_reference": False},
    }
    snapshot = _build_video_generation_snapshot(state, 1, policy)
    assert snapshot["visual_style_id"] == "semi_realistic_anime"
    assert snapshot["style_modifiers"] == ["清新"]
    assert snapshot["style_snapshot"]["prompt_layers"]["shot"]

    state.creative_profile = CreativeProfile(visual_style_id="live_action_cinematic")
    assert snapshot["style_snapshot"]["id"] == "semi_realistic_anime"


def test_video_request_injects_current_project_style_when_saved_shot_is_legacy():
    profile = CreativeProfile(
        visual_style_id="semi_realistic_anime",
        art_style="半写实动漫",
    )
    shot = ShotPrompt(scene_id=1, prompt="角色站在房间里等待。", duration_sec=5)

    prepared, _ = VideoGenerationAgent().prepare_shot_request(
        shot,
        generation_prompts=GenerationPrompts(api_provider="mock", shots=[shot]),
        prompt_policy=None,
        creative_profile=profile,
    )

    assert "半写实二维动画镜头" in prepared.prompt
    assert "真人实拍" not in prepared.prompt
