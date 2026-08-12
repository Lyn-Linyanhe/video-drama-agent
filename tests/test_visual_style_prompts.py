from src.schemas.project import CreativeProfile
from src.services.asset_prompt_composer import build_scene_prompt_layers, project_style_layers
from tests.test_scene_prompt_layers import _state


def test_canonical_style_layer_is_added_to_scene_prompt_and_negative_prompt():
    state = _state()
    state.creative_profile = CreativeProfile(
        visual_style_id="cinematic_cel",
        art_style="现代电影级二维赛璐璐",
        style_modifiers=["清新"],
    )
    positive, negative = project_style_layers(state, "scene")
    result = build_scene_prompt_layers(state, "loc_room", "rain", "scene_center")
    assert "二维背景绘制" in positive
    assert "清新" in positive
    assert positive in result["effective_prompt"]
    assert negative in result["negative_prompt"]


def test_live_action_style_keeps_realism_and_rejects_illustration():
    state = _state()
    state.creative_profile = CreativeProfile(
        visual_style_id="live_action_cinematic",
        art_style="电影级真人写实",
    )
    positive, negative = project_style_layers(state, "character")
    assert "真实成年人体比例" in positive
    assert "插画" in negative
