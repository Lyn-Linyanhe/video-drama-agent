from src.schemas.scene_state import LocationState
from src.schemas.script import LocationAsset, Scene, ScriptBreakdown, ShotVisualBinding, VisualAssetManifest
from src.utils.visual_asset_manifest import build_manifest_from_scenes, normalize_scene_assets, stable_location_id, validate_asset_manifest


def _script(locations, bindings) -> ScriptBreakdown:
    return ScriptBreakdown(
        title="场景状态",
        genre="悬疑",
        logline="测试场景状态",
        duration_sec=10,
        narration_style="无旁白",
        scenes=[
            Scene(scene_id=1, duration_sec=5, location="便利店", characters=[], action_description="角色进入便利店", camera_direction="中景", emotion_tone="紧张"),
            Scene(scene_id=2, duration_sec=5, location="便利店", characters=[], action_description="角色拿起柜台上的纸条", camera_direction="近景", emotion_tone="紧张"),
        ],
        asset_manifest=VisualAssetManifest(locations=locations, shot_bindings=bindings),
    )


def test_legacy_location_gets_one_default_state():
    legacy_id = "store_legacy"
    script = _script(
        [
            LocationAsset(
                location_id=legacy_id,
                canonical_name="便利店",
                environment_description="狭窄便利店，柜台在右侧。",
                representative_scene_id=1,
                shot_ids=[1, 2],
            )
        ],
        [
            ShotVisualBinding(scene_id=1, location_id=legacy_id),
            ShotVisualBinding(scene_id=2, location_id=legacy_id),
        ],
    )

    normalize_scene_assets(script)

    location = script.asset_manifest.locations[0]
    assert len(location.states) == 1
    assert location.default_state_id == location.states[0].state_id
    assert {binding.state_id for binding in script.asset_manifest.shot_bindings} == {location.default_state_id}
    assert validate_asset_manifest(script) == []


def test_same_location_with_different_conditions_creates_states_not_locations():
    day_id = "store_day"
    rain_id = "store_rain"
    script = _script(
        [
            LocationAsset(
                location_id=day_id,
                canonical_name="便利店",
                time_condition="白天",
                environment_description="狭窄便利店，柜台在右侧。",
                representative_scene_id=1,
                shot_ids=[1],
            ),
            LocationAsset(
                location_id=rain_id,
                canonical_name="便利店",
                time_condition="雨夜",
                environment_description="狭窄便利店，柜台在右侧。",
                representative_scene_id=2,
                shot_ids=[2],
            ),
        ],
        [
            ShotVisualBinding(scene_id=1, location_id=day_id),
            ShotVisualBinding(scene_id=2, location_id=rain_id),
        ],
    )

    normalize_scene_assets(script)

    location = script.asset_manifest.locations[0]
    assert len(script.asset_manifest.locations) == 1
    assert location.location_id == stable_location_id("便利店")
    assert len(location.states) == 2
    assert [state.time_condition for state in location.states] == ["白天", "雨夜"]
    assert {binding.state_id for binding in script.asset_manifest.shot_bindings} == {
        state.state_id for state in location.states
    }
    assert validate_asset_manifest(script) == []


def test_duplicate_legacy_state_ids_are_scoped_to_their_location():
    first = LocationAsset(
        location_id="legacy_first",
        canonical_name="第一房间",
        time_condition="白天",
        environment_description="第一房间固定书桌。",
        representative_scene_id=1,
        shot_ids=[1],
        states=[LocationState(state_id="state_default", label="白天", shot_ids=[1], representative_scene_id=1)],
        default_state_id="state_default",
    )
    second = LocationAsset(
        location_id="legacy_second",
        canonical_name="第二房间",
        time_condition="夜晚",
        environment_description="第二房间固定壁炉。",
        representative_scene_id=2,
        shot_ids=[2],
        states=[LocationState(state_id="state_default", label="夜晚", shot_ids=[2], representative_scene_id=2)],
        default_state_id="state_default",
    )
    script = ScriptBreakdown(
        title="状态作用域",
        genre="悬疑",
        logline="同名旧状态不能跨地点串线",
        duration_sec=6,
        narration_style="无旁白",
        scenes=[
            Scene(scene_id=1, duration_sec=3, location="第一房间", characters=[], action_description="书桌旁", camera_direction="中景", emotion_tone="平静"),
            Scene(scene_id=2, duration_sec=3, location="第二房间", characters=[], action_description="壁炉旁", camera_direction="中景", emotion_tone="平静"),
        ],
        asset_manifest=VisualAssetManifest(
            locations=[first, second],
            shot_bindings=[
                ShotVisualBinding(scene_id=1, location_id="legacy_first", state_id="state_default"),
                ShotVisualBinding(scene_id=2, location_id="legacy_second", state_id="state_default"),
            ],
        ),
    )

    normalize_scene_assets(script)

    locations_by_id = {item.location_id: item for item in script.asset_manifest.locations}
    for binding in script.asset_manifest.shot_bindings:
        location = locations_by_id[binding.location_id]
        assert binding.state_id in {state.state_id for state in location.states}
    assert validate_asset_manifest(script) == []


def test_build_manifest_keeps_first_shot_to_visible_character_only():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="苏眠先看短信。",
        duration_sec=10,
        narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=5,
                location="钟楼街便利店外部",
                characters=["苏眠", "顾衡"],
                action_description="苏眠站在便利店屋檐下，右手举着手机看短信，左手紧攥信封。",
                camera_direction="中近景/过肩固定",
                emotion_tone="紧张",
            ),
            Scene(
                scene_id=2,
                duration_sec=5,
                location="钟楼街便利店外部",
                characters=["苏眠", "顾衡"],
                action_description="顾衡推开便利店玻璃门走出，苏眠把信封藏到身后。",
                camera_direction="中景双人/侧面固定",
                emotion_tone="紧张试探",
            ),
        ],
    )

    manifest = build_manifest_from_scenes(script)
    first_binding = next(binding for binding in manifest.shot_bindings if binding.scene_id == 1)
    visible_names = [
        character.name
        for character in manifest.characters
        if character.asset_id in first_binding.on_screen_character_ids
    ]

    assert visible_names == ["苏眠"]
    assert "顾衡" in {character.name for character in manifest.characters}


def test_validate_manifest_reports_missing_location_state_binding():
    location = LocationAsset(
        location_id="store",
        canonical_name="便利店",
        representative_scene_id=1,
        shot_ids=[1],
        states=[LocationState(state_id="day", label="白天", shot_ids=[1], representative_scene_id=1)],
        default_state_id="day",
    )
    script = ScriptBreakdown(
        title="坏状态",
        genre="悬疑",
        logline="测试坏状态",
        duration_sec=5,
        narration_style="无旁白",
        scenes=[Scene(scene_id=1, duration_sec=5, location="便利店", characters=[], action_description="进入", camera_direction="中景", emotion_tone="紧张")],
        asset_manifest=VisualAssetManifest(
            locations=[location],
            shot_bindings=[ShotVisualBinding(scene_id=1, location_id="store", state_id="night")],
        ),
    )

    errors = validate_asset_manifest(script)

    assert "scene 1 location store references missing state night" in errors
    assert "location store state day has inconsistent shot 1" in errors


def test_normalize_scene_assets_repairs_binding_state_ids_missing_from_location_states():
    old_location_id = "basement_old"
    script = ScriptBreakdown(
        title="地下室状态修复",
        genre="悬疑",
        logline="测试 LLM 漏写 location.states 的状态",
        duration_sec=20,
        narration_style="无旁白",
        scenes=[
            Scene(scene_id=16, duration_sec=5, location="旧钟楼地下室", characters=[], action_description="地下室暗处，顾衡护住苏眠。", camera_direction="中景", emotion_tone="紧张"),
            Scene(scene_id=17, duration_sec=5, location="旧钟楼地下室", characters=[], action_description="齿轮机关启动，铜钟缓慢下沉。", camera_direction="广角", emotion_tone="压迫"),
            Scene(scene_id=18, duration_sec=5, location="旧钟楼地下室", characters=[], action_description="打斗后地下室一片狼藉。", camera_direction="近景", emotion_tone="危险"),
            Scene(scene_id=19, duration_sec=5, location="旧钟楼地下室", characters=[], action_description="积水里火漆印章被泡开。", camera_direction="道具特写", emotion_tone="悬疑"),
        ],
        asset_manifest=VisualAssetManifest(
            locations=[
                LocationAsset(
                    location_id=old_location_id,
                    canonical_name="旧钟楼地下室",
                    representative_scene_id=16,
                    shot_ids=[16, 17, 18, 19],
                    states=[
                        LocationState(
                            state_id="state_basement_table_lit",
                            label="台灯亮起",
                            shot_ids=[16],
                            representative_scene_id=16,
                        )
                    ],
                    default_state_id="state_basement_table_lit",
                )
            ],
            shot_bindings=[
                ShotVisualBinding(scene_id=16, location_id=old_location_id, state_id="state_basement_dark", visible_state_notes="地下室暗处"),
                ShotVisualBinding(scene_id=17, location_id=old_location_id, state_id="state_basement_gear_activated", visible_state_notes="齿轮机关启动"),
                ShotVisualBinding(scene_id=18, location_id=old_location_id, state_id="state_basement_aftermath", visible_state_notes="打斗后一片狼藉"),
                ShotVisualBinding(scene_id=19, location_id=old_location_id, state_id="state_basement_pool_closeup", visible_state_notes="积水道具特写"),
            ],
        ),
    )

    normalize_scene_assets(script)

    location = script.asset_manifest.locations[0]
    assert {state.state_id for state in location.states} == {
        "state_basement_dark",
        "state_basement_gear_activated",
        "state_basement_aftermath",
        "state_basement_pool_closeup",
    }
    assert {
        binding.scene_id: binding.state_id
        for binding in script.asset_manifest.shot_bindings
    } == {
        16: "state_basement_dark",
        17: "state_basement_gear_activated",
        18: "state_basement_aftermath",
        19: "state_basement_pool_closeup",
    }
    assert validate_asset_manifest(script) == []


def test_dark_passage_entrance_stays_inside_the_basement_location():
    """An interior passage entrance must reuse the basement identity.

    The old location heuristic treated every ``入口`` token as an exterior
    split.  ``地下室暗道入口`` is an interior view of the same room, so that
    split discarded the reusable set description and produced a nearly empty
    scene prompt.
    """
    script = ScriptBreakdown(
        title="地下室暗道入口",
        genre="悬疑",
        logline="测试地点归并",
        duration_sec=6,
        narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=7,
                duration_sec=3,
                location="旧钟楼地下室（台灯暖光）",
                characters=[],
                action_description="地下室四周堆满旧木箱，中央有一张长桌。",
                camera_direction="中景",
                emotion_tone="压迫",
            ),
            Scene(
                scene_id=11,
                duration_sec=3,
                location="旧钟楼地下室暗道入口（黑暗）",
                characters=[],
                action_description="暗道入口出现在地下室深处。",
                camera_direction="中景",
                emotion_tone="紧张",
            ),
        ],
    )

    manifest = build_manifest_from_scenes(script)

    assert len(manifest.locations) == 1
    assert manifest.locations[0].canonical_name == "旧钟楼地下室"
    assert "旧木箱" in manifest.locations[0].environment_description
    assert [binding.location_id for binding in manifest.shot_bindings] == [
        manifest.locations[0].location_id,
        manifest.locations[0].location_id,
    ]
