from src.schemas.script import (
    LocationAsset,
    PropAsset,
    Scene,
    ScriptBreakdown,
    ShotVisualBinding,
    VisualAssetManifest,
    VisualCharacterAsset,
)
from src.utils.visual_asset_manifest import (
    normalize_scene_assets,
    repair_asset_manifest,
    validate_asset_manifest,
)


def _manifest_with_single_location(scenes, *, characters=None, props=None):
    location = LocationAsset(
        location_id="loc_test",
        canonical_name=scenes[0].location,
        representative_scene_id=scenes[0].scene_id,
        shot_ids=[scene.scene_id for scene in scenes],
    )
    return ScriptBreakdown(
        title="语义补全",
        genre="悬疑",
        logline="test",
        duration_sec=sum(scene.duration_sec for scene in scenes),
        narration_style="无",
        scenes=scenes,
        asset_manifest=VisualAssetManifest(
            characters=characters or [],
            locations=[location],
            props=props or [],
            shot_bindings=[
                ShotVisualBinding(
                    scene_id=scene.scene_id,
                    location_id="loc_test",
                    prop_ids=[prop.prop_id for prop in props or []],
                    on_screen_character_ids=[character.asset_id for character in characters or []],
                )
                for scene in scenes
            ],
        ),
    )


def test_repair_manifest_enriches_generic_character_description_from_visible_facts():
    scenes = [
        Scene(
            scene_id=1,
            duration_sec=3,
            location="雨夜便利店门口",
            characters=["苏眠"],
            action_description="苏眠站在屋檐下，穿米白色长风衣，黑色中长发贴在脸侧。",
            camera_direction="中景",
            emotion_tone="紧张",
        ),
        Scene(
            scene_id=2,
            duration_sec=3,
            location="雨夜便利店门口",
            characters=["苏眠"],
            action_description="苏眠攥紧信封，抬头看向街口。",
            camera_direction="近景",
            emotion_tone="压迫",
        ),
    ]
    script = _manifest_with_single_location(
        scenes,
        characters=[
            VisualCharacterAsset(
                asset_id="char_su_mian",
                name="苏眠",
                appearance_mode="on_screen",
                description="画面角色，出现在分镜 1, 2",
            )
        ],
    )

    repair_asset_manifest(script)

    description = script.asset_manifest.characters[0].description
    assert "米白色长风衣" in description
    assert "黑色中长发" in description
    assert "画面角色，出现在分镜" not in description


def test_repair_manifest_enriches_generic_prop_description_from_physical_facts():
    scenes = [
        Scene(
            scene_id=1,
            duration_sec=3,
            location="雨夜便利店门口",
            characters=["苏眠"],
            action_description="苏眠握着一只旧牛皮纸信封，封口盖着暗红色火漆印章。",
            camera_direction="手部特写",
            emotion_tone="紧张",
        ),
        Scene(
            scene_id=2,
            duration_sec=3,
            location="雨夜便利店门口",
            characters=["苏眠"],
            action_description="她把旧牛皮纸信封藏到身后。",
            camera_direction="中近景",
            emotion_tone="压迫",
        ),
    ]
    prop = PropAsset(
        prop_id="prop_letter",
        name="信封",
        description="信封作为跨镜头可见线索道具，外观、位置和持有状态需要保持一致。",
        critical_reason="plot",
        critical_evidence="两镜头持续出现并承载线索。",
        shot_ids=[1, 2],
    )
    script = _manifest_with_single_location(scenes, props=[prop])

    repair_asset_manifest(script)

    description = script.asset_manifest.props[0].description
    assert "旧牛皮纸" in description
    assert "暗红色火漆印章" in description
    assert "跨镜头可见线索道具" not in description


def test_repair_manifest_filters_dynamic_scene_description_but_keeps_fixed_set_facts():
    scenes = [
        Scene(
            scene_id=1,
            duration_sec=3,
            location="地下通道",
            characters=["顾衡"],
            action_description="通道两侧灯管大多损坏，墙上有大片水痕，顾衡走向墙边电箱准备切断线路。",
            camera_direction="低角度全景",
            emotion_tone="阴森",
        ),
        Scene(
            scene_id=2,
            duration_sec=3,
            location="地下通道",
            characters=["顾衡"],
            action_description="顾衡停下，远处传来脚步声。",
            camera_direction="跟拍",
            emotion_tone="紧张",
        ),
    ]
    location = LocationAsset(
        location_id="loc_test",
        canonical_name="地下通道",
        identity_description="物理地点身份：地下通道；可复用固定空间。",
        fixed_dressing="通道两侧灯管大多损坏；顾衡走向墙边电箱准备切断线路时",
        environment_description="通道两侧灯管大多损坏；顾衡走向墙边电箱准备切断线路时",
        representative_scene_id=1,
        shot_ids=[1, 2],
    )
    script = _manifest_with_single_location(scenes)
    script.asset_manifest.locations = [location]

    repair_asset_manifest(script)

    repaired = script.asset_manifest.locations[0]
    assert "灯管大多损坏" in repaired.environment_description
    assert "走向墙边电箱" not in repaired.environment_description
    assert "脚步声" not in repaired.environment_description


def test_repair_manifest_removes_legacy_noun_fragments_from_dynamic_actions():
    scenes = [
        Scene(
            scene_id=1,
            duration_sec=3,
            location="地下通道",
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
    ]
    script = _manifest_with_single_location(scenes)
    location = script.asset_manifest.locations[0]
    location.environment_description = "通道两侧灯管大多损坏；墙上有大片水痕；地面积着浅水；摄像头；电箱、墙；通道、铁门；通道、木门"
    location.fixed_dressing = location.environment_description
    location.anchor_points = ["灯管大多损坏", "摄像头", "电箱、墙", "通道、木门"]

    repair_asset_manifest(script)

    repaired = script.asset_manifest.locations[0]
    assert "灯管大多损坏" in repaired.environment_description
    assert "大片水痕" in repaired.environment_description
    assert "地面积着浅水" in repaired.environment_description
    for transient in ("摄像头", "电箱", "铁门", "木门"):
        assert transient not in repaired.environment_description
        assert all(transient not in anchor for anchor in repaired.anchor_points)


def test_normalize_scene_assets_cleans_dynamic_clauses_from_merged_dressing_and_anchors():
    """Location merging must not reintroduce shot-only actions from legacy fields."""
    action = "\u987e\u8861\u6478\u5411\u5899\u8fb9\u7535\u7bb1"
    static_fixture = "\u5899\u9762\u6c34\u75d5"
    scene = Scene(
        scene_id=1,
        duration_sec=3,
        location="\u5730\u4e0b\u901a\u9053",
        characters=["\u987e\u8861"],
        action_description=action,
        camera_direction="\u4e2d\u666f",
        emotion_tone="\u7d27\u5f20",
    )
    script = _manifest_with_single_location([scene])
    location = script.asset_manifest.locations[0]
    location.environment_description = static_fixture
    location.fixed_dressing = f"{static_fixture}\uff1b{action}"
    location.anchor_points = [static_fixture, action]

    normalize_scene_assets(script)

    normalized = script.asset_manifest.locations[0]
    assert action not in normalized.fixed_dressing
    assert all(action not in anchor for anchor in normalized.anchor_points)
    assert static_fixture in normalized.fixed_dressing
    assert static_fixture in normalized.anchor_points


def test_repair_manifest_cleans_dynamic_dressing_and_anchors_when_environment_is_empty():
    """Repair must clear action-only legacy fields even without inferred environment text."""
    action = "\u987e\u8861\u8d70\u5165\u623f\u95f4"
    scene = Scene(
        scene_id=1,
        duration_sec=3,
        location="\u5730\u4e0b\u5ba4",
        characters=["\u987e\u8861"],
        action_description=action,
        camera_direction="\u4e2d\u666f",
        emotion_tone="\u7d27\u5f20",
    )
    script = _manifest_with_single_location([scene])
    location = script.asset_manifest.locations[0]
    location.environment_description = ""
    location.fixed_dressing = action
    location.anchor_points = [action]

    repair_asset_manifest(script)

    repaired = script.asset_manifest.locations[0]
    assert repaired.fixed_dressing == ""
    assert repaired.anchor_points == []


def test_repair_manifest_uses_bindings_as_prop_source_of_truth_and_drops_stale_prop():
    script = ScriptBreakdown(
        title="Repair",
        genre="Suspense",
        logline="A letter changes hands.",
        duration_sec=6,
        narration_style="none",
        scenes=[
            Scene(scene_id=11, duration_sec=3, location="Archive", characters=[], action_description="A desk.", camera_direction="wide", emotion_tone="tense"),
            Scene(scene_id=13, duration_sec=3, location="Archive", characters=[], action_description="An empty drawer.", camera_direction="wide", emotion_tone="tense"),
        ],
        asset_manifest=VisualAssetManifest(
            locations=[LocationAsset(location_id="archive", canonical_name="Archive", representative_scene_id=11, shot_ids=[11, 13])],
            props=[PropAsset(prop_id="seventh_letter_envelope", name="Seventh letter envelope", description="A sealed ivory envelope with a red wax stamp.", shot_ids=[11, 13], critical_reason="plot", critical_evidence="Contains the decisive clue.")],
            shot_bindings=[
                ShotVisualBinding(scene_id=11, location_id="archive", prop_ids=[]),
                ShotVisualBinding(scene_id=13, location_id="archive", prop_ids=[]),
            ],
        ),
    )

    repaired = repair_asset_manifest(script)

    assert repaired["reconciled_props"] == ["seventh_letter_envelope"]
    assert repaired["pruned_props"] == ["seventh_letter_envelope"]
    assert script.asset_manifest.props == []
    assert validate_asset_manifest(script) == []
