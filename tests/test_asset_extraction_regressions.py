from src.schemas.script import (
    LocationAsset,
    PropAsset,
    Scene,
    ScriptBreakdown,
    ShotVisualBinding,
    VisualAssetManifest,
    VisualCharacterAsset,
)
from src.api import context as api_context
from src.services.asset_prompt_composer import build_scene_prompt_layers
from src.utils.visual_asset_manifest import (
    build_manifest_from_scenes,
    normalize_scene_assets,
    prune_nonessential_props,
    validate_asset_manifest,
)
from src.agents.script_agent import SYSTEM_PROMPT as SCRIPT_SYSTEM_PROMPT


def _script_with_locations(locations, bindings):
    scenes = [
        Scene(
            scene_id=scene_id,
            duration_sec=5,
            location=location,
            characters=[],
            action_description="环境保持连续",
            camera_direction="中景",
            emotion_tone="悬疑",
        )
        for scene_id, location in enumerate(
            [
                "老城区钟楼街便利店外部",
                "钟楼街便利店外部",
                "旧钟楼地下室",
                "旧钟楼地下室、长桌与录音机旁",
                "旧钟楼地下室外部",
            ],
            start=1,
        )
    ]
    return ScriptBreakdown(
        title="资产提取回归",
        genre="悬疑",
        logline="同一建筑跨多个镜头",
        duration_sec=25,
        narration_style="无旁白",
        scenes=scenes,
        asset_manifest=VisualAssetManifest(locations=locations, shot_bindings=bindings),
    )


def test_location_identity_merges_prefixes_and_action_suffixes():
    locations = [
        LocationAsset(
            location_id=f"loc_{scene_id}",
            canonical_name=location,
            representative_scene_id=scene_id,
            shot_ids=[scene_id],
        )
        for scene_id, location in enumerate(
            [
                "老城区钟楼街便利店外部",
                "钟楼街便利店外部",
                "旧钟楼地下室",
                "旧钟楼地下室、长桌与录音机旁",
                "旧钟楼地下室外部",
            ],
            start=1,
        )
    ]
    bindings = [
        ShotVisualBinding(scene_id=scene_id, location_id=f"loc_{scene_id}")
        for scene_id in range(1, 6)
    ]
    script = _script_with_locations(locations, bindings)

    normalize_scene_assets(script)

    assert len(script.asset_manifest.locations) == 2
    assert sorted(location.shot_ids for location in script.asset_manifest.locations) == [[1, 2], [3, 4, 5]]


def test_location_identity_merges_middle_dot_venue_variants():
    scenes = [
        Scene(
            scene_id=1,
            duration_sec=5,
            location="雨夜老城钟楼街·便利店屋檐下",
            characters=[],
            action_description="便利店白色灯牌和玻璃门固定不变",
            camera_direction="大全景",
            emotion_tone="悬疑",
        ),
        Scene(
            scene_id=2,
            duration_sec=5,
            location="雨夜老城钟楼街·便利店门口",
            characters=[],
            action_description="便利店白色灯牌和玻璃门固定不变",
            camera_direction="中景",
            emotion_tone="悬疑",
        ),
        Scene(
            scene_id=3,
            duration_sec=5,
            location="钟楼街便利店外部",
            characters=[],
            action_description="便利店白色灯牌和玻璃门固定不变",
            camera_direction="中景",
            emotion_tone="悬疑",
        ),
    ]
    script = ScriptBreakdown(
        title="地点键稳定性",
        genre="悬疑",
        logline="同一外景地点的不同镜头命名",
        duration_sec=15,
        narration_style="无旁白",
        scenes=scenes,
        asset_manifest=VisualAssetManifest(
            locations=[
                LocationAsset(
                    location_id=f"old_{scene.scene_id}",
                    canonical_name=scene.location,
                    representative_scene_id=scene.scene_id,
                    shot_ids=[scene.scene_id],
                )
                for scene in scenes
            ],
            shot_bindings=[
                ShotVisualBinding(scene_id=scene.scene_id, location_id=f"old_{scene.scene_id}")
                for scene in scenes
            ],
        ),
    )

    normalize_scene_assets(script)

    assert len(script.asset_manifest.locations) == 1
    assert script.asset_manifest.locations[0].shot_ids == [1, 2, 3]


def test_compound_message_and_recording_names_are_not_physical_props():
    script = ScriptBreakdown(
        title="非实体道具回归",
        genre="悬疑",
        logline="信息内容不应生成道具图",
        duration_sec=10,
        narration_style="无旁白",
        scenes=[
            Scene(scene_id=1, duration_sec=5, location="房间", characters=[], action_description="看到手机提示", camera_direction="特写", emotion_tone="紧张"),
            Scene(scene_id=2, duration_sec=5, location="房间", characters=[], action_description="录音内容继续播放", camera_direction="近景", emotion_tone="紧张"),
        ],
        asset_manifest=VisualAssetManifest(
            locations=[LocationAsset(location_id="room", canonical_name="房间", representative_scene_id=1, shot_ids=[1, 2])],
            props=[
                PropAsset(
                    prop_id="prop_sms_notice",
                    name="手机短信提示",
                    description="屏幕上显示一条消息内容",
                    critical_reason="plot",
                    critical_evidence="镜头1出现提示内容",
                    shot_ids=[1, 2],
                ),
                PropAsset(
                    prop_id="prop_recording_content",
                    name="录音内容",
                    description="一段声音信息被播放",
                    critical_reason="plot",
                    critical_evidence="镜头2播放录音内容",
                    shot_ids=[2],
                ),
                PropAsset(
                    prop_id="prop_phone",
                    name="手机",
                    description="黑色实体手机，屏幕可见",
                    critical_reason="continuity",
                    critical_evidence="镜头1和镜头2均由角色手持",
                    shot_ids=[1, 2],
                ),
            ],
            shot_bindings=[
                ShotVisualBinding(scene_id=1, location_id="room", prop_ids=["prop_sms_notice", "prop_phone"]),
                ShotVisualBinding(scene_id=2, location_id="room", prop_ids=["prop_recording_content", "prop_phone"]),
            ],
        ),
    )

    removed = prune_nonessential_props(script)

    assert removed == ["prop_sms_notice", "prop_recording_content"]
    assert [prop.prop_id for prop in script.asset_manifest.props] == ["prop_phone"]
    assert script.asset_manifest.shot_bindings[0].prop_ids == ["prop_phone"]
    assert script.asset_manifest.shot_bindings[1].prop_ids == ["prop_phone"]


def test_validate_asset_manifest_checks_prop_binding_in_both_directions():
    script = ScriptBreakdown(
        title="双向道具绑定",
        genre="悬疑",
        logline="prop binding contract",
        duration_sec=10,
        narration_style="无旁白",
        scenes=[
            Scene(scene_id=1, duration_sec=5, location="房间", characters=[], action_description="看到钥匙", camera_direction="特写", emotion_tone="紧张"),
            Scene(scene_id=2, duration_sec=5, location="房间", characters=[], action_description="再次看到钥匙", camera_direction="近景", emotion_tone="紧张"),
        ],
        asset_manifest=VisualAssetManifest(
            locations=[LocationAsset(location_id="room", canonical_name="房间", representative_scene_id=1, shot_ids=[1, 2])],
            props=[PropAsset(
                prop_id="prop_key",
                name="钥匙",
                description="一把旧钥匙",
                critical_reason="plot",
                critical_evidence="镜头1和镜头2均出现",
                shot_ids=[1],
            )],
            shot_bindings=[
                ShotVisualBinding(scene_id=1, location_id="room", prop_ids=["prop_key"]),
                ShotVisualBinding(scene_id=2, location_id="room", prop_ids=["prop_key"]),
            ],
        ),
    )

    errors = validate_asset_manifest(script)

    assert any("prop_key" in error and "scene 2" in error for error in errors)


def test_script_contract_does_not_force_long_screenplays_into_twelve_scenes():
    assert "不得为满足数量合并或删减镜头" in SCRIPT_SYSTEM_PROMPT
    assert "5-20 个场景" in SCRIPT_SYSTEM_PROMPT


def test_loading_legacy_state_cleans_virtual_props_without_dropping_devices():
    script = ScriptBreakdown(
        title="旧项目清洗",
        genre="悬疑",
        logline="旧清单中含有虚拟内容",
        duration_sec=10,
        narration_style="无旁白",
        scenes=[
            Scene(scene_id=1, duration_sec=5, location="房间", characters=[], action_description="看见手机", camera_direction="特写", emotion_tone="紧张"),
            Scene(scene_id=2, duration_sec=5, location="房间", characters=[], action_description="继续播放录音", camera_direction="近景", emotion_tone="紧张"),
        ],
        asset_manifest=VisualAssetManifest(
            locations=[LocationAsset(location_id="room", canonical_name="房间", representative_scene_id=1, shot_ids=[1, 2])],
            props=[
                PropAsset(prop_id="prop_sms_notice", name="手机短信提示", shot_ids=[1, 2], critical_reason="plot", critical_evidence="提示剧情"),
                PropAsset(prop_id="prop_recording", name="录音内容", shot_ids=[2], critical_reason="plot", critical_evidence="播放内容"),
                PropAsset(
                    prop_id="prop_phone",
                    name="手机",
                    description="黑色实体手机，屏幕可见",
                    shot_ids=[1, 2],
                    critical_reason="continuity",
                    critical_evidence="连续出现",
                ),
            ],
            shot_bindings=[
                ShotVisualBinding(scene_id=1, location_id="room", prop_ids=["prop_sms_notice", "prop_phone"]),
                ShotVisualBinding(scene_id=2, location_id="room", prop_ids=["prop_recording", "prop_phone"]),
            ],
        ),
    )
    state = api_context.DramaState(project_id="legacy-cleanup", script=script)

    restored = api_context._state_from_json(api_context._state_to_json(state))

    assert [prop.prop_id for prop in restored.script.asset_manifest.props] == ["prop_phone"]
    assert restored.script.asset_manifest.shot_bindings[0].prop_ids == ["prop_phone"]
    assert restored.script.asset_manifest.shot_bindings[1].prop_ids == ["prop_phone"]


def test_loading_legacy_state_repairs_scene_and_prop_descriptions():
    """Loading a saved project must apply the same manifest cleanup as parsing."""
    script = ScriptBreakdown(
        title="加载时清单修复",
        genre="悬疑",
        logline="历史描述中混入镜头动作",
        duration_sec=10,
        narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=5,
                location="潮湿地下通道",
                characters=["苏眠"],
                action_description="苏眠走向墙边电箱，准备切断线路，录音里的声音在通道中响起。",
                camera_direction="中景",
                emotion_tone="紧张",
            ),
            Scene(
                scene_id=2,
                duration_sec=5,
                location="潮湿地下通道",
                characters=["苏眠"],
                action_description="苏眠握紧钥匙，墙面水痕和积水保持可见。",
                camera_direction="近景",
                emotion_tone="压迫",
            ),
        ],
        asset_manifest=VisualAssetManifest(
            characters=[VisualCharacterAsset(asset_id="char_su", name="苏眠", appearance_mode="on_screen")],
            locations=[LocationAsset(
                location_id="loc_tunnel",
                canonical_name="潮湿地下通道",
                representative_scene_id=1,
                shot_ids=[1, 2],
                environment_description="通道两侧灯管大多损坏；苏眠走向墙边电箱准备切断线路时；录音里的声音在通道中响起",
                fixed_dressing="通道两侧灯管大多损坏；苏眠走向墙边电箱准备切断线路时",
            )],
            props=[PropAsset(
                prop_id="prop_key",
                name="钥匙",
                shot_ids=[1, 2],
                description="道具",
                critical_reason="plot",
                critical_evidence="该道具在分镜 1, 3 中反复出现并承载剧情信息。",
            )],
            shot_bindings=[
                ShotVisualBinding(scene_id=1, location_id="loc_tunnel", prop_ids=["prop_key"]),
                ShotVisualBinding(scene_id=2, location_id="loc_tunnel", prop_ids=["prop_key"]),
            ],
        ),
    )

    restored = api_context._state_from_json(api_context._state_to_json(api_context.DramaState(script=script)))
    location = restored.script.asset_manifest.locations[0]
    prop = restored.script.asset_manifest.props[0]

    assert "走向墙边电箱" not in location.environment_description
    assert "录音里的声音" not in location.environment_description
    assert "灯管大多损坏" in location.environment_description
    assert "钥匙" in prop.description
    assert prop.description != "道具"
    assert prop.critical_evidence == "该道具在分镜 1, 2 中反复出现并承载剧情信息。"


def test_manifest_builder_drops_transient_actions_from_reusable_scene_dressing():
    """Scene actions must not become fixed dressing or location anchors."""
    scenes = [
        Scene(
            scene_id=6,
            duration_sec=5,
            location="废弃铁门前",
            characters=["苏眠"],
            action_description="墙皮贴满褪色广告纸；脚下是旧纸箱与生锈自行车；最终停在一扇半掩的铁门前。",
            camera_direction="手持跟拍",
            emotion_tone="压迫",
        ),
        Scene(
            scene_id=7,
            duration_sec=5,
            location="废弃铁门前",
            characters=["顾衡"],
            action_description="顾衡用铜钥匙拧开半掩的铁门，门后涌出潮湿冷气。",
            camera_direction="近景",
            emotion_tone="紧张",
        ),
        Scene(
            scene_id=8,
            duration_sec=5,
            location="废弃地下通道",
            characters=["顾衡"],
            action_description="墙面水痕；地面积水；顾衡摸向墙边电箱。",
            camera_direction="中景",
            emotion_tone="紧张",
        ),
        Scene(
            scene_id=14,
            duration_sec=5,
            location="旧钟楼地下室",
            characters=["顾衡"],
            action_description="室内堆满木箱；顾衡撑墙站起，指向中央裂开的废弃铜钟。",
            camera_direction="中景",
            emotion_tone="震惊",
        ),
        Scene(
            scene_id=16,
            duration_sec=5,
            location="旧钟楼地下室",
            characters=["陆临"],
            action_description="众人消失在暗道后，陆临站在原地，雨水与钟楼阴影融为一体。",
            camera_direction="远景",
            emotion_tone="悬念",
        ),
    ]
    script = ScriptBreakdown(
        title="固定陈设边界",
        genre="悬疑",
        logline="动作不能污染场景资产",
        duration_sec=25,
        narration_style="无旁白",
        scenes=scenes,
    )

    manifest = build_manifest_from_scenes(script)
    entrance = next(location for location in manifest.locations if 6 in location.shot_ids)
    tunnel = next(location for location in manifest.locations if 8 in location.shot_ids)
    basement = next(location for location in manifest.locations if 14 in location.shot_ids)
    reusable_text = "；".join(
        [
            entrance.fixed_dressing,
            entrance.environment_description,
            *entrance.anchor_points,
            tunnel.fixed_dressing,
            tunnel.environment_description,
            *tunnel.anchor_points,
            basement.fixed_dressing,
            basement.environment_description,
            *basement.anchor_points,
        ]
    )

    for transient in ("最终停在", "拧开", "摸向", "撑墙站起", "消失在", "融为一体"):
        assert transient not in reusable_text
    assert "墙皮贴满褪色广告纸" in reusable_text
    assert "墙面水痕" in reusable_text
    assert "室内堆满木箱" in reusable_text or "废弃铜钟" in reusable_text


def test_scene_prompt_layers_do_not_forward_transient_location_actions():
    """The final scene prompt must exclude action text even for old manifests."""
    scene = Scene(
        scene_id=1,
        duration_sec=5,
        location="旧钟楼地下室",
        characters=["顾衡"],
        action_description="顾衡撑墙站起，众人消失在暗道后。",
        camera_direction="中景",
        emotion_tone="悬念",
    )
    script = ScriptBreakdown(
        title="提示词边界",
        genre="悬疑",
        logline="场景提示词不应包含动作",
        duration_sec=5,
        narration_style="无旁白",
        scenes=[scene],
        asset_manifest=VisualAssetManifest(
            characters=[VisualCharacterAsset(asset_id="char_gu", name="顾衡", appearance_mode="on_screen")],
            locations=[
                LocationAsset(
                    location_id="loc_basement",
                    canonical_name="旧钟楼地下室",
                    representative_scene_id=1,
                    shot_ids=[1],
                    fixed_dressing="室内堆满木箱；撑墙站起；众人消失在暗道后",
                    environment_description="室内堆满木箱；撑墙站起；众人消失在暗道后",
                    anchor_points=["撑墙站起", "中央长桌"],
                )
            ],
            shot_bindings=[ShotVisualBinding(scene_id=1, location_id="loc_basement")],
        ),
    )
    state = api_context.DramaState(
        project_id="scene-prompt-boundary",
        script=script,
    )

    layers = build_scene_prompt_layers(state, "loc_basement")

    assert "撑墙站起" not in layers["identity"]
    assert "众人消失在暗道后" not in layers["identity"]
    assert "中央长桌" in layers["identity"]


def test_environment_cleaner_drops_gaze_and_psychology_clauses():
    from src.utils import visual_asset_manifest as vam

    scenes = [
        Scene(
            scene_id=1,
            duration_sec=5,
            location="老城区巷口",
            characters=["林默"],
            action_description="林默再抬眼望向空旷巷口，说话时视线仍偏向巷口。",
            camera_direction="中景",
            emotion_tone="克制",
        )
    ]
    value = (
        "再抬眼望向空旷巷口；说话时视线仍偏向巷口；怕它被雨水浸透；"
        "沿石阶向下走；雨水从发梢滴落；街角路灯亮着；墙面大片水痕"
    )
    cleaned = vam._clean_environment_description(value, scenes)

    assert "望向" not in cleaned
    assert "视线" not in cleaned
    assert "怕它" not in cleaned
    assert "沿石阶向下走" not in cleaned
    assert "雨水从发梢滴落" not in cleaned
    assert "街角路灯亮着" in cleaned
    assert "墙面大片水痕" in cleaned
