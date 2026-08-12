from src.schemas.project import CreativeProfile
from src.services.visual_styles import get_visual_style, get_visual_styles, resolve_profile_style, resolve_visual_style


def test_registry_exposes_four_primary_styles_with_reference_images():
    styles = get_visual_styles()
    assert [item["id"] for item in styles] == [
        "cinematic_cel",
        "semi_realistic_anime",
        "japanese_animation",
        "live_action_cinematic",
    ]
    assert all(item["reference_image"].startswith("/static/assets/style-references/") for item in styles)
    assert all(set(item["prompt_layers"]) == {"character", "scene", "prop", "shot"} for item in styles)
    assert all(set(item["negative_prompt_layers"]) == {"character", "scene", "prop", "shot"} for item in styles)


def test_registry_returns_defensive_copies():
    styles = get_visual_styles()
    styles[0]["prompt_layers"]["character"] = "changed"
    assert get_visual_style("cinematic_cel")["prompt_layers"]["character"] != "changed"


def test_legacy_style_values_map_without_losing_modifiers():
    assert resolve_visual_style(art_style="动漫") == ("cinematic_cel", "动漫", [])
    assert resolve_visual_style(art_style="写实、赛博朋克") == (
        "live_action_cinematic",
        "写实、赛博朋克",
        ["赛博朋克"],
    )


def test_unknown_style_keeps_text_and_uses_live_action_fallback():
    assert resolve_visual_style(art_style="实验风格") == ("live_action_cinematic", "实验风格", [])


def test_invalid_explicit_style_id_raises_value_error():
    try:
        get_visual_style("missing-style")
    except ValueError as exc:
        assert "missing-style" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_profile_keeps_existing_positional_request_arguments_and_normalizes_style():
    profile = CreativeProfile.from_request("都市、悬疑", "半写实动漫", "16:9", "auto")
    assert profile.genre_tags == ["都市", "悬疑"]
    assert profile.visual_style_id == "semi_realistic_anime"
    assert profile.style_modifiers == []
    assert resolve_profile_style(profile)["id"] == "semi_realistic_anime"


def test_explicit_style_without_legacy_text_uses_the_selected_style_name():
    profile = CreativeProfile.from_request(visual_style_id="japanese_animation")
    assert profile.visual_style_id == "japanese_animation"
    assert profile.art_style == "日系商业动画"
