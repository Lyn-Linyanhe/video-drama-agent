from src.graph.state import DramaState
from src.schemas.project import CreativeProfile
from src.schemas.scene_state import LocationState
from src.schemas.script import LocationAsset, PropAsset, Scene, ScriptBreakdown, ShotVisualBinding, VisualAssetManifest
from src.schemas.prop import PropImage
from src.schemas.visual import SceneVisual
from src.services.asset_prompt_composer import (
    _scene_scale_lock,
    build_scene_group_prompt,
    build_scene_prompt_layers,
    compose_asset_prompt,
    scene_environment_key_elements,
)
from src.agents.image_gen_agent import build_prop_image_prompt, build_prop_view_prompt, prop_state_lock
from src.services.media_quality import _prop_state_requirement
from src.utils.visual_asset_manifest import build_manifest_from_scenes, normalize_scene_assets
from src.schemas.scene_image import SceneImage


def _state() -> DramaState:
    location = LocationAsset(
        location_id="loc_room",
        canonical_name="旧客厅",
        identity_description="狭长客厅，左侧木门，右侧旧沙发，尽头有一扇窄窗。",
        fixed_dressing="褪色地毯、裂纹茶几、墙面旧相框固定不变。",
        anchor_points=["左侧木门", "右侧旧沙发", "尽头窄窗"],
        environment_description="空间比例狭长，墙面发黄，家具陈旧。",
        representative_scene_id=1,
        shot_ids=[1, 2],
        states=[
            LocationState(
                state_id="rain",
                label="雨夜",
                time_condition="夜晚",
                weather="窗外暴雨",
                light_direction="窗外冷蓝光从尽头照入",
                visible_state_notes="地面有少量积水反光",
                shot_ids=[1, 2],
                representative_scene_id=1,
            )
        ],
        default_state_id="rain",
    )
    script = ScriptBreakdown(
        title="场景提示词",
        genre="悬疑",
        logline="测试",
        duration_sec=10,
        narration_style="无",
        scenes=[
            Scene(scene_id=1, duration_sec=5, location="旧客厅", characters=[], action_description="雨夜客厅空镜", camera_direction="左前45度", emotion_tone="紧张"),
            Scene(scene_id=2, duration_sec=5, location="旧客厅", characters=[], action_description="雨夜客厅空镜", camera_direction="右前45度", emotion_tone="紧张"),
        ],
        asset_manifest=VisualAssetManifest(
            locations=[location],
            shot_bindings=[
                ShotVisualBinding(scene_id=1, location_id="loc_room", state_id="rain"),
                ShotVisualBinding(scene_id=2, location_id="loc_room", state_id="rain"),
            ],
        ),
    )
    return DramaState(project_id="p1", script=script)


def test_identity_and_state_are_shared_but_view_is_different():
    state = _state()

    left = build_scene_prompt_layers(state, "loc_room", "rain", "scene_left_45", "地面水痕更明显")
    right = build_scene_prompt_layers(state, "loc_room", "rain", "scene_right_45", "地面水痕更明显")

    assert left["identity"] == right["identity"]
    assert left["state"] == right["state"]
    assert left["scale"] == right["scale"]
    assert left["view"] != right["view"]
    assert left["effective_prompt"] != right["effective_prompt"]
    assert left["width"] == 1920 and left["height"] == 1080
    assert left["prompt_template_version"] >= 5
    assert "空间尺度锁" in left["effective_prompt"]
    assert "电话亭" in left["effective_prompt"]


def test_generic_venue_uses_explicit_indoor_state_for_scale_lock():
    scale = _scene_scale_lock(
        "医院",
        "物理地点身份：医院",
        "病床、床头柜和白色隔帘",
        "场景状态：室内病房，窗外暴雨",
    )

    assert "完整房间轮廓" in scale
    assert "临街建筑外立面" not in scale


def test_custom_text_cannot_remove_empty_scene_constraint():
    result = build_scene_prompt_layers(
        _state(),
        "loc_room",
        "rain",
        "scene_center",
        "加入人物和对白",
    )

    assert "不得出现任何人物" in result["effective_prompt"]
    assert "加入人物和对白" in result["effective_prompt"]


def test_scene_state_omits_default_label_when_specific_state_facts_exist():
    state = _state()
    state.script.asset_manifest.locations[0].states[0].label = "默认状态"
    state.script.asset_manifest.locations[0].states[0].time_condition = "深夜雨夜"

    layers = build_scene_prompt_layers(state, "loc_room", "rain", "scene_center")

    assert "场景状态：默认状态" not in layers["state"]
    assert "场景状态：深夜雨夜" in layers["state"]


def test_scene_state_does_not_emit_default_placeholder_without_facts():
    state = _state()
    location = state.script.asset_manifest.locations[0]
    location.states[0].label = "默认状态"
    location.states[0].time_condition = ""
    location.states[0].weather = ""
    location.states[0].light_direction = ""
    location.states[0].visible_state_notes = ""

    layers = build_scene_prompt_layers(state, "loc_room", "rain", "scene_center")

    assert "场景状态：默认状态" not in layers["state"]
    assert "场景状态：以剧本当前镜头的时间、天气、主光方向和可见临时状态为准。" in layers["state"]


def test_indoor_scale_lock_ignores_negative_mentions_of_other_venue_types():
    state = _state()
    location = state.script.asset_manifest.locations[0]
    location.canonical_name = "旧展馆地下室"
    location.identity_description = "封闭地下室，中央展台固定不变。"
    location.environment_description = "混凝土墙体和中央展台。"
    location.states[0].visible_state_notes = "不得生成整洁仓库或现代办公室。"

    layers = build_scene_prompt_layers(state, "loc_room", "rain", "scene_center")

    assert "完整房间轮廓" in layers["scale"]
    assert "临街建筑外立面" not in layers["scale"]


def test_outdoor_underground_entrance_uses_exterior_scale_lock():
    state = _state()
    location = state.script.asset_manifest.locations[0]
    location.canonical_name = "旧剧院地下通道入口外部"
    location.identity_description = "室外街巷尽头，旧砖墙内嵌一扇拱形铁门，门后石阶向下。"
    location.environment_description = "旧砖墙、拱形铁门、潮湿路面和周边街巷。"

    layers = build_scene_prompt_layers(state, "loc_room", "rain", "scene_center")

    assert "完整街巷入口边界" in layers["scale"]
    assert "镜头必须位于室外街巷地面" in layers["scale"]
    assert "禁止从室内向外拍摄" in layers["scale"]
    assert "门头、橱窗" not in layers["scale"]
    assert "完整房间轮廓" not in layers["scale"]


def test_scene_prompt_forbids_signs_and_pseudo_text_in_empty_reference_images():
    state = _state()
    location = state.script.asset_manifest.locations[0]
    location.canonical_name = "地下通道入口外部"
    location.identity_description = "室外旧砖墙入口，铁门后石阶向下。"
    location.environment_description = "潮湿地面和无标识的旧砖墙。"

    layers = build_scene_prompt_layers(state, "loc_room", "rain", "scene_center")

    assert "标牌" in layers["effective_prompt"]
    assert "伪文字" in layers["effective_prompt"]
    assert "路牌" in layers["negative_prompt"]


def test_scene_prompt_rewrites_sign_like_anchors_to_blank_structural_anchors():
    state = _state()
    location = state.script.asset_manifest.locations[0]
    location.canonical_name = "地下通道入口外部"
    location.identity_description = "旧砖墙内嵌一扇铁门，墙上有旧钟楼维修工程标识牌"
    location.environment_description = "墙面潮湿，旁边保留一块旧工程牌"
    location.fixed_dressing = "墙上一块褪色标牌"
    location.anchor_points = ["铁门", "工程标识牌"]

    layers = build_scene_prompt_layers(state, "loc_room", "rain", "scene_center")

    identity = layers["identity"]
    assert "标识牌" not in identity
    assert "工程牌" not in identity
    assert "标牌" not in identity
    assert "空白" in identity
    assert "不得出现文字或符号" in identity


def test_scene_sign_sanitizer_is_generic_without_matching_non_sign_architecture():
    for sign_term in ("路牌", "广告牌", "招牌", "门牌"):
        state = _state()
        location = state.script.asset_manifest.locations[0]
        location.identity_description = f"旧街区墙面保留一块{sign_term}"
        identity = build_scene_prompt_layers(state, "loc_room", "rain", "scene_center")["identity"]
        assert sign_term not in identity
        assert "空白" in identity

    state = _state()
    location = state.script.asset_manifest.locations[0]
    location.identity_description = "街巷尽头保留一座石砌牌坊"
    identity = build_scene_prompt_layers(state, "loc_room", "rain", "scene_center")["identity"]
    assert "牌坊" in identity


def test_folded_knife_prompt_turns_state_into_explicit_shape_constraints():
    prompt = build_prop_view_prompt("刀", "旧式折叠刀，保持闭合状态", "front", style="现代电影级二维赛璐璐")

    assert "刀刃必须完全收回刀柄内部" in prompt
    assert "刀尖不可见" in prompt
    assert "从正面和侧边都不可见刀刃" in prompt
    assert "禁止展开、半展开、打开、露出刀刃" in prompt


def test_regular_prop_prompt_keeps_generic_state_lock_without_folded_knife_terms():
    prompt = build_prop_view_prompt("钥匙", "生锈铜钥匙", "front", style="现代电影级二维赛璐璐")

    assert "结构、活动部件和当前可见状态必须与文字设定完全一致" in prompt
    assert "刀刃必须完全收回刀柄内部" not in prompt


def test_regular_prop_prompt_does_not_create_a_movable_state_review_requirement():
    """The compatibility sentence for flexible props must not constrain rigid props."""
    prompt = build_prop_view_prompt("病历夹", "蓝色硬质病历夹，带金属夹", "front")

    assert _prop_state_requirement(prompt) == ""


def test_explicit_prop_state_still_creates_a_review_requirement():
    prompt = build_prop_view_prompt("病历夹", "蓝色硬质病历夹，保持闭合", "front")

    assert "闭合/收回" in _prop_state_requirement(prompt)


def test_prop_sheet_does_not_invent_unstated_flexible_prop_state():
    prompt = build_prop_image_prompt("围巾", "深蓝色针织围巾", style="现代电影级二维赛璐璐")

    assert "描述未指定时默认平整展开" not in prompt
    assert "未指定时不自行增加展开、折叠或放置状态" in prompt


def test_group_prompt_uses_layered_views_in_required_order():
    prompt, count = build_scene_group_prompt(_state(), "loc_room", state_id="rain")

    assert count == 3
    assert "参考锁定" in prompt
    assert "锁定一个可复用地点：保持相同的完整房间或建筑外壳" in prompt
    assert "地点身份：旧客厅" in prompt
    assert "场景状态：雨夜" in prompt
    assert "空间尺度锁" in prompt
    assert "电话亭" in prompt
    assert "第1张：视角要求：正面中心线大全景" in prompt
    assert "第2张：视角要求：以中心线为参考的左前方45度大全景" in prompt
    assert "第3张：视角要求：以中心线为参考的右前方45度大全景" in prompt


def test_explicit_anime_style_wins_over_stale_legacy_scene_style_text():
    state = _state()
    # This is the shape produced by older/newly-created projects when the
    # stable picker value is saved but the compatibility field still has its
    # old default value.
    state.creative_profile = CreativeProfile(
        visual_style_id="semi_realistic_anime",
        art_style="写实",
    )

    prompt, _ = build_scene_group_prompt(state, "loc_room", state_id="rain")

    assert "半写实动画概念场景" in prompt
    assert "视觉风格：写实" not in prompt
    assert "项目视觉约束：艺术风格必须为半写实动漫" in build_scene_prompt_layers(
        state, "loc_room", "rain", "scene_center"
    )["effective_prompt"]


def test_legacy_scene_view_regeneration_uses_canonical_anime_constraints():
    state = _state()
    state.creative_profile = CreativeProfile(
        visual_style_id="japanese_animation",
        art_style="写实",
    )
    asset = SceneImage(
        scene_id=1,
        location_id="loc_room",
        state_id="rain",
        location="旧客厅",
        view_type="wide",
        image_prompt="高质量影视场景空镜，真实影视质感，9:16竖屏构图。",
        base_prompt="高质量影视场景空镜，真实影视质感，9:16竖屏构图。",
        image_path="",
        seed=1,
    )

    result = compose_asset_prompt(state, "scene", "loc_room", "wide", asset)

    assert "日系商业动画背景绘制" in result["effective_prompt"]
    assert "项目视觉约束：艺术风格必须为日系商业动画" in result["project_constraints"]
    assert "艺术风格必须为写实" not in result["project_constraints"]


def test_legacy_art_style_without_picker_id_still_selects_anime_registry_entry():
    profile = CreativeProfile(art_style="动漫风")

    assert profile.visual_style_id == "cinematic_cel"


def test_scene_reference_uses_landscape_ratio_without_inheriting_video_ratio():
    state = _state()
    state.creative_profile = CreativeProfile(art_style="动漫风", video_ratio="9:16")

    layers = build_scene_prompt_layers(state, "loc_room", "rain", "scene_center")

    assert layers["width"] == 1920 and layers["height"] == 1080
    assert "横向16:9" in layers["effective_prompt"]
    assert "9:16" not in layers["effective_prompt"]
    assert "二维背景绘制" in layers["effective_prompt"]
    assert "专业影视摄影" not in layers["effective_prompt"]
    assert "照片级实拍" in layers["negative_prompt"]

    state.creative_profile = CreativeProfile(art_style="写实", video_ratio="9:16")
    live_layers = build_scene_prompt_layers(state, "loc_room", "rain", "scene_center")
    assert "电影级真人实拍环境" in live_layers["effective_prompt"]
    assert "二维背景绘制" not in live_layers["effective_prompt"]


def test_scene_asset_preview_prompt_is_specific_to_selected_view():
    state = _state()
    asset = SceneImage(
        scene_id=1,
        location_id="loc_room",
        state_id="rain",
        location="旧客厅",
        view_type="scene_center",
        image_prompt="",
        image_path="",
        seed=1,
    )

    left = compose_asset_prompt(state, "scene", "loc_room", "scene_left_45", asset)
    reverse = compose_asset_prompt(state, "scene", "loc_room", "scene_reverse", asset)

    assert left["effective_prompt"] != reverse["effective_prompt"]
    assert "左前方45度" in left["effective_prompt"]
    assert "反向中心大全景" in reverse["effective_prompt"]
    assert left["provider_prompt"] == reverse["provider_prompt"]


def test_scene_prompt_deduplicates_repeated_location_facts():
    state = _state()
    location = state.script.asset_manifest.locations[0]
    repeated = "完整空间：左侧木门、右侧旧沙发和尽头窄窗。"
    location.identity_description = repeated
    location.fixed_dressing = repeated
    location.environment_description = repeated

    layers = build_scene_prompt_layers(state, "loc_room", "rain", "scene_center")

    assert layers["effective_prompt"].count(repeated) == 1
    assert layers["identity"].count(repeated) == 1


def test_single_view_scene_provider_does_not_receive_group_prompt():
    state = _state()
    state.image_generation_policy = {
        "provider": "kling",
        "model": "kling-v1",
        "resolution": "1K",
    }
    asset = SceneImage(
        scene_id=1,
        location_id="loc_room",
        state_id="rain",
        location="旧客厅",
        view_type="scene_center",
        image_prompt="",
        image_path="",
        seed=1,
    )

    left = compose_asset_prompt(state, "scene", "loc_room", "scene_left_45", asset, provider="kling")
    reverse = compose_asset_prompt(state, "scene", "loc_room", "scene_reverse", asset, provider="kling")

    assert left["provider_prompt"] == left["effective_prompt"]
    assert reverse["provider_prompt"] == reverse["effective_prompt"]
    assert left["provider_prompt"] != reverse["provider_prompt"]


def test_manifest_fallback_does_not_promote_shot_actions_to_location_identity():
    script = ScriptBreakdown(
        title="manifest fallback",
        genre="悬疑",
        logline="test",
        duration_sec=10,
        narration_style="无",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=5,
                location="旧客厅",
                characters=[],
                action_description="人物推开木门并冲向窗边。",
                camera_direction="wide",
                emotion_tone="紧张",
            ),
            Scene(
                scene_id=2,
                duration_sec=5,
                location="旧客厅",
                characters=[],
                action_description="人物在沙发旁停下并拿出手机。",
                camera_direction="medium",
                emotion_tone="紧张",
            ),
        ],
    )

    script.asset_manifest = build_manifest_from_scenes(script)
    location = script.asset_manifest.locations[0]

    assert location.environment_description == ""
    normalized = normalize_scene_assets(script)
    normalized_location = normalized.asset_manifest.locations[0]
    assert normalized_location.identity_description == ""
    assert normalized_location.environment_description == ""


def test_manifest_fallback_keeps_static_environment_facts_without_shot_actions():
    script = ScriptBreakdown(
        title="environment fallback",
        genre="悬疑",
        logline="test",
        duration_sec=10,
        narration_style="无",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=5,
                location="钟楼街便利店门口（深夜雨夜）",
                characters=["苏眠"],
                action_description=(
                    "深夜雨幕下，老城区钟楼街路灯忽明忽暗，便利店白色灯牌在积水中晃动。"
                    "便利店玻璃门被推开，苏眠站在屋檐下，攥紧信封。"
                ),
                camera_direction="wide",
                emotion_tone="压迫",
            ),
            Scene(
                scene_id=2,
                duration_sec=5,
                location="钟楼街便利店门口（深夜雨夜）",
                characters=["苏眠"],
                action_description="苏眠后退一步，雨水沿屋檐落下。",
                camera_direction="medium",
                emotion_tone="紧张",
            ),
        ],
    )

    manifest = build_manifest_from_scenes(script)
    location = manifest.locations[0]

    assert "白色灯牌" in location.environment_description
    assert "路灯忽明忽暗" not in location.environment_description
    assert "积水中晃动" not in location.environment_description
    assert "玻璃门被推开" not in location.environment_description
    assert "苏眠站在屋檐下" not in location.environment_description


def test_manifest_fallback_never_recovers_nouns_from_dynamic_shot_clauses():
    script = ScriptBreakdown(
        title="动态场景清洗",
        genre="悬疑",
        logline="test",
        duration_sec=5,
        narration_style="无",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=5,
                location="废弃地下通道",
                characters=["顾衡", "黑衣人"],
                action_description=(
                    "通道两侧灯管大多损坏，墙上有大片水痕，地面积着浅水。"
                    "前方拐角处有红光一闪，是摄像头。"
                    "顾衡示意她别出声，走向墙边电箱准备切断线路时，"
                    "通道尽头传来铁门被推开的声音，黑衣人冲向通道深处的木门。"
                ),
                camera_direction="中景",
                emotion_tone="紧张",
            )
        ],
    )

    location = build_manifest_from_scenes(script).locations[0]

    assert "灯管大多损坏" in location.environment_description
    assert "大片水痕" in location.environment_description
    assert "地面积着浅水" in location.environment_description
    for transient in ("摄像头", "红光", "电箱", "切断线路", "铁门", "木门", "黑衣人"):
        assert transient not in location.environment_description


def test_scene_visual_dynamic_key_elements_do_not_pollute_empty_scene_prompt():
    visual = SceneVisual(
        scene_id=1,
        composition="中景",
        lighting="冷蓝色侧光",
        color_tone="低饱和",
        key_elements=["摄像头红光一闪", "便利店白色灯牌", "字迹被水汽泡得模糊"],
    )

    assert scene_environment_key_elements(visual) == ["便利店白色灯牌"]


def test_manifest_fallback_hydrates_scene_identity_dressing_anchors_and_state():
    script = ScriptBreakdown(
        title="manifest identity hydration",
        genre="悬疑",
        logline="test",
        duration_sec=10,
        narration_style="无",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=5,
                location="钟楼街便利店门口（深夜雨夜）",
                characters=[],
                action_description="雨幕下，便利店白色灯牌映在积水路面，玻璃门紧闭。",
                camera_direction="wide",
                emotion_tone="压迫",
            ),
            Scene(
                scene_id=2,
                duration_sec=5,
                location="钟楼街便利店门口（深夜雨夜）",
                characters=[],
                action_description="路灯照亮便利店屋檐，门头和橱窗保持不变。",
                camera_direction="medium",
                emotion_tone="紧张",
            ),
        ],
    )

    location = build_manifest_from_scenes(script).locations[0]

    assert location.identity_description
    assert "便利店" in location.identity_description
    assert location.fixed_dressing
    assert location.anchor_points
    assert location.states
    assert location.default_state_id == location.states[0].state_id


def test_manifest_fallback_keeps_explicit_rainy_night_from_plain_location_text():
    script = ScriptBreakdown(
        title="rainy night state",
        genre="悬疑",
        logline="test",
        duration_sec=10,
        narration_style="无",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=5,
                location="雨夜老城钟楼街·便利店屋檐下",
                characters=[],
                action_description="深夜十一点四十分，海城大雨，便利店白色灯牌映在积水路面。",
                camera_direction="wide",
                emotion_tone="压迫",
            ),
            Scene(
                scene_id=2,
                duration_sec=5,
                location="雨夜老城钟楼街·便利店门口",
                characters=[],
                action_description="雨幕持续，玻璃门紧闭。",
                camera_direction="medium",
                emotion_tone="紧张",
            ),
        ],
    )

    location = build_manifest_from_scenes(script).locations[0]

    assert "深夜" in location.time_condition
    assert "深夜" in location.states[0].time_condition
    assert "大雨" in location.states[0].weather


def test_normalize_rehydrates_missing_rainy_night_state_in_existing_manifest():
    script = ScriptBreakdown(
        title="legacy rainy night state",
        genre="悬疑",
        logline="test",
        duration_sec=5,
        narration_style="无",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=5,
                location="雨夜老城钟楼街·便利店门口",
                characters=[],
                action_description="深夜十一点四十分，海城大雨，便利店白色灯牌映在积水路面。",
                camera_direction="wide",
                emotion_tone="压迫",
            )
        ],
    )
    script.asset_manifest = build_manifest_from_scenes(script)
    script.asset_manifest.locations[0].time_condition = ""
    script.asset_manifest.locations[0].states[0].label = "默认状态"
    script.asset_manifest.locations[0].states[0].time_condition = ""
    script.asset_manifest.locations[0].states[0].weather = ""

    normalize_scene_assets(script)
    location = script.asset_manifest.locations[0]

    assert location.time_condition == "深夜"
    assert location.states[0].time_condition == "深夜"
    assert location.states[0].weather == "大雨"


def test_normalize_merges_identical_location_states_and_updates_bindings():
    scenes = [
        Scene(
            scene_id=scene_id,
            duration_sec=3,
            location=location,
            characters=[],
            action_description="深夜大雨，便利店灯牌映在积水中。",
            camera_direction="wide",
            emotion_tone="压迫",
        )
        for scene_id, location in enumerate(
            ["雨夜钟楼街·便利店屋檐", "雨夜钟楼街·便利店门口"],
            start=1,
        )
    ]
    locations = [
        LocationAsset(
            location_id=f"legacy_{scene.scene_id}",
            canonical_name=scene.location,
            representative_scene_id=scene.scene_id,
            shot_ids=[scene.scene_id],
            states=[LocationState(
                state_id=f"state_{scene.scene_id}",
                label="雨夜",
                time_condition="深夜",
                weather="大雨",
                shot_ids=[scene.scene_id],
                representative_scene_id=scene.scene_id,
            )],
            default_state_id=f"state_{scene.scene_id}",
        )
        for scene in scenes
    ]
    script = ScriptBreakdown(
        title="相同场景状态",
        genre="悬疑",
        logline="同一地点连续镜头",
        duration_sec=6,
        narration_style="无",
        scenes=scenes,
        asset_manifest=VisualAssetManifest(
            locations=locations,
            shot_bindings=[
                ShotVisualBinding(
                    scene_id=scene.scene_id,
                    location_id=f"legacy_{scene.scene_id}",
                    state_id=f"state_{scene.scene_id}",
                )
                for scene in scenes
            ],
        ),
    )

    normalize_scene_assets(script)

    location = script.asset_manifest.locations[0]
    assert len(location.states) == 1
    assert location.states[0].shot_ids == [1, 2]
    assert {item.state_id for item in script.asset_manifest.shot_bindings} == {location.states[0].state_id}


def test_single_prop_prompt_does_not_claim_to_be_a_six_view_sheet():
    state = _state()
    state.script.asset_manifest.props = [
        PropAsset(
            prop_id="old_key",
            name="旧钥匙",
            description="黄铜材质，暗绿色铜锈，复杂齿痕。",
            critical_reason="plot",
            critical_evidence="它打开地下室铁盒并揭示新的线索。",
            shot_ids=[1, 2],
        )
    ]
    asset = PropImage(
        prop_id="old_key",
        prop_name="旧钥匙",
        image_prompt="",
        image_path="",
        seed=1,
    )

    prompt = compose_asset_prompt(state, "prop", "old_key", "front", asset)["effective_prompt"]

    assert "当前只生成一个绝对正前方视图" in prompt
    assert "2×3六视图" not in prompt
    assert "单张独立道具参考图" in prompt


def test_normalize_clears_legacy_shot_action_from_binding_state_notes():
    state = _state()
    action = state.script.scenes[0].action_description
    state.script.asset_manifest.shot_bindings[0].visible_state_notes = action
    state.script.asset_manifest.shot_bindings[0].state_override_notes = action

    normalize_scene_assets(state.script)

    binding = state.script.asset_manifest.shot_bindings[0]
    assert binding.visible_state_notes == ""
    assert binding.state_override_notes == ""


def test_prop_prompt_switches_between_anime_and_live_action_without_style_leakage():
    state = _state()
    state.script.asset_manifest.props = [
        PropAsset(
            prop_id="old_key",
            name="旧钥匙",
            description="黄铜材质，暗绿色铜锈，复杂齿痕。",
            critical_reason="plot",
            critical_evidence="它打开地下室铁盒并揭示新的线索。",
            shot_ids=[1, 2],
        )
    ]
    asset = PropImage(
        prop_id="old_key",
        prop_name="旧钥匙",
        image_prompt="",
        image_path="",
        seed=1,
    )

    state.creative_profile = CreativeProfile(art_style="动漫风", video_ratio="9:16")
    anime = compose_asset_prompt(state, "prop", "old_key", "front", asset)
    assert "二维动画设定稿媒介" in anime["effective_prompt"]
    assert "电影级实物道具" not in anime["effective_prompt"]
    assert "高质量写实道具" not in anime["effective_prompt"]
    assert "产品影棚摄影" not in anime["effective_prompt"]

    state.creative_profile = CreativeProfile(art_style="写实", video_ratio="9:16")
    live_action = compose_asset_prompt(state, "prop", "old_key", "front", asset)
    assert "电影级实物道具" in live_action["effective_prompt"]
    assert "产品影棚摄影" in live_action["effective_prompt"]
    assert "二维动画设定稿媒介" not in live_action["effective_prompt"]
def test_negative_movable_state_language_is_not_misread_as_open():
    lock = prop_state_lock("可折叠道具", "禁止打开，不可展开，保持收起")
    requirement = _prop_state_requirement("可折叠道具，禁止打开，不可展开，保持收起")

    assert "完全闭合" in lock
    assert "闭合/收回" in requirement


def test_explicit_open_state_still_wins_over_a_negated_close_clause():
    lock = prop_state_lock("可折叠道具", "保持打开状态，禁止闭合")
    requirement = _prop_state_requirement("可折叠道具，保持打开状态，禁止闭合")

    assert "完全展开" in lock
    assert "展开/打开" in requirement
