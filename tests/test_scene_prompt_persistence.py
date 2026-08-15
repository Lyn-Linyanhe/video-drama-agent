import json
from pathlib import Path

import pytest
from PIL import Image

from src.api import context as api_context
from src.graph.state import DramaState
from src.schemas.project import CreativeProfile
from src.schemas.script import (
    LocationAsset,
    PropAsset,
    Scene,
    ScriptBreakdown,
    ShotVisualBinding,
    VisualAssetManifest,
)
from src.schemas.scene_image import SceneImage, SceneImageAssets
from src.schemas.scene_state import LocationState
from src.schemas.visual import VisualDesign
from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.services.asset_prompt_composer import compose_asset_prompt
from src.schemas.prop import PropImage
from src.utils import project_store as project_store_module
from src.utils.project_store import ProjectStore


def test_loading_scene_image_rebinds_nonrepresentative_shot_after_location_merge():
    inside_id = "basement_inside"
    legacy_id = "basement_passage_outside"
    script = ScriptBreakdown(
        title="场景图片归并",
        genre="悬疑",
        logline="测试旧地点图片迁移",
        duration_sec=6,
        narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=7,
                duration_sec=3,
                location="旧钟楼地下室",
                characters=[],
                action_description="地下室四周堆满旧木箱。",
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
        asset_manifest=VisualAssetManifest(
            locations=[
                LocationAsset(
                    location_id=inside_id,
                    canonical_name="旧钟楼地下室",
                    environment_description="地下室四周堆满旧木箱。",
                    representative_scene_id=7,
                    shot_ids=[7],
                    states=[LocationState(state_id="inside", shot_ids=[7], representative_scene_id=7)],
                    default_state_id="inside",
                ),
                LocationAsset(
                    location_id=legacy_id,
                    canonical_name="旧钟楼地下室入口外部",
                    representative_scene_id=11,
                    shot_ids=[11],
                    states=[LocationState(state_id="outside", shot_ids=[11], representative_scene_id=11)],
                    default_state_id="outside",
                ),
            ],
            shot_bindings=[
                ShotVisualBinding(scene_id=7, location_id=inside_id, state_id="inside"),
                ShotVisualBinding(scene_id=11, location_id=legacy_id, state_id="outside"),
            ],
        ),
    )
    state = DramaState(
        project_id="scene-rebind",
        script=script,
        scene_images=SceneImageAssets(scenes=[
            SceneImage(
                scene_id=11,
                location_id=legacy_id,
                state_id="outside",
                location="旧钟楼地下室入口外部",
                view_type="scene_center",
                image_prompt="旧提示词",
                image_path="",
                seed=1,
            )
        ]),
    )

    loaded = api_context._state_from_json(api_context._state_to_json(state))

    assert len(loaded.script.asset_manifest.locations) == 1
    merged = loaded.script.asset_manifest.locations[0]
    assert loaded.scene_images.scenes[0].location_id == merged.location_id
    assert loaded.scene_images.scenes[0].scene_id in merged.shot_ids


def _legacy_state(project_id: str) -> DramaState:
    script = ScriptBreakdown(
        title="旧场景状态",
        genre="悬疑",
        logline="测试",
        duration_sec=6,
        narration_style="无",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=3,
                location="地下室",
                characters=[],
                action_description="人物推开铁门并走入地下室。",
                camera_direction="全景",
                emotion_tone="压抑",
            )
        ],
        asset_manifest=VisualAssetManifest(
            locations=[
                LocationAsset(
                    location_id="basement",
                    canonical_name="地下室",
                    environment_description="潮湿的地下室",
                    representative_scene_id=1,
                    shot_ids=[1],
                )
            ],
            shot_bindings=[
                ShotVisualBinding(
                    scene_id=1,
                    location_id="basement",
                    visible_state_notes="人物推开铁门并走入地下室。",
                    state_override_notes="人物推开铁门并走入地下室。",
                )
            ],
        ),
    )
    return DramaState(project_id=project_id, script=script)


def test_loading_legacy_scene_action_notes_persists_the_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(project_store_module, "OUTPUT_DIR", tmp_path)
    store = ProjectStore()
    monkeypatch.setattr(api_context.project_store, "_target", store)
    project_id = store.create_project("持久化清理")
    state = _legacy_state(project_id)
    store.set_state_json(project_id, api_context._state_to_json(state))

    loaded = api_context._load_project_state(project_id, require_visual=False)
    assert loaded.script.asset_manifest.shot_bindings[0].visible_state_notes == ""
    assert loaded.script.asset_manifest.shot_bindings[0].state_override_notes == ""

    persisted = json.loads(store.get_project(project_id)["assets"]["state_json"])
    persisted_binding = persisted["script"]["asset_manifest"]["shot_bindings"][0]
    assert persisted_binding["visible_state_notes"] == ""
    assert persisted_binding["state_override_notes"] == ""
    assert store.get_project(project_id)["assets"]["script"]["asset_manifest"]["locations"][0]["environment_description"] == "潮湿的地下室"


def test_prop_draft_prompt_uses_the_same_style_locked_composer_as_generation():
    state = _legacy_state("draft-prop")
    state.creative_profile = CreativeProfile(art_style="动漫风")
    state.script.asset_manifest.props = [
        PropAsset(
            prop_id="iron_key",
            name="铁钥匙",
            description="黑铁材质，长条钥匙齿，表面有旧锈迹。",
            critical_reason="plot",
            critical_evidence="它打开地下室铁门并推动剧情。",
            shot_ids=[1, 2],
        )
    ]
    asset = PropImage(
        prop_id="iron_key",
        prop_name="铁钥匙",
        image_prompt="",
        image_path="",
        seed=1,
    )

    composed = compose_asset_prompt(state, "prop", "iron_key", "front", asset)
    from src.api.context import ImageAssetRequest, _create_draft_image_asset

    draft = _create_draft_image_asset(
        state,
        ImageAssetRequest(asset_type="prop", asset_id="iron_key", view_type="front"),
    )

    assert "二维动画设定稿媒介" in composed["effective_prompt"]
    assert "二维动画设定稿媒介" in draft.image_prompt
    assert draft.image_prompt == composed["effective_prompt"]


def test_state_json_refreshes_project_script_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(project_store_module, "OUTPUT_DIR", tmp_path)
    store = ProjectStore()
    project_id = store.create_project("snapshot")
    store.set_script(project_id, {"title": "old", "asset_manifest": {"locations": []}})

    state_json = json.dumps({
        "script": {"title": "new", "asset_manifest": {"locations": []}},
        "visual_design": {"overall_style": "现代电影级二维赛璐璐"},
    }, ensure_ascii=False)
    store.set_state_json(project_id, state_json)

    assets = store.get_project(project_id)["assets"]
    assert assets["script"]["title"] == "new"
    assert assets["visual_design"]["overall_style"] == "现代电影级二维赛璐璐"


def test_loading_state_repairs_an_existing_stale_script_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(project_store_module, "OUTPUT_DIR", tmp_path)
    store = ProjectStore()
    monkeypatch.setattr(api_context.project_store, "_target", store)
    project_id = store.create_project("stale snapshot")
    state = _legacy_state(project_id)
    state.script.title = "new"
    store.set_state_json(project_id, api_context._state_to_json(state))
    api_context._load_project_state(project_id, require_visual=False)
    stale = store.get_project(project_id)
    stale["assets"]["script"] = {"title": "old", "asset_manifest": {"locations": []}}
    store._save_meta(project_id, stale)

    api_context._load_project_state(project_id, require_visual=False)

    assert store.get_project(project_id)["assets"]["script"]["title"] == "new"


def test_loading_legacy_scene_group_repairs_per_view_prompt_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr(project_store_module, "OUTPUT_DIR", tmp_path)
    store = ProjectStore()
    monkeypatch.setattr(api_context.project_store, "_target", store)
    project_id = store.create_project("组图提示词迁移")
    state = _legacy_state(project_id)
    location_id = state.script.asset_manifest.locations[0].location_id
    project_dir = tmp_path / project_id
    left_path = project_dir / "assets" / "scenes" / location_id / "scene_left_45.png"
    right_path = project_dir / "assets" / "scenes" / location_id / "scene_right_45.png"
    left_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), "white").save(left_path)
    Image.new("RGB", (64, 64), "black").save(right_path)
    shared_prompt = "旧版本保存的场景参考组图总提示词"
    state.image_generation_policy = {"provider": "wan", "model": "wan2.7-image-pro", "resolution": "2K"}
    state.scene_images = SceneImageAssets(scenes=[
        SceneImage(
            scene_id=1, location_id=location_id, state_id="", location="地下室",
            view_type="scene_left_45", image_prompt=shared_prompt, image_path=str(left_path),
            seed=1, generation_mode="reference_group", group_id="group-1", version_id="left-v1",
        ),
        SceneImage(
            scene_id=1, location_id=location_id, state_id="", location="地下室",
            view_type="scene_right_45", image_prompt=shared_prompt, image_path=str(right_path),
            seed=2, generation_mode="reference_group", group_id="group-1", version_id="right-v1",
        ),
    ])
    store.set_state_json(project_id, api_context._state_to_json(state))

    loaded = api_context._load_project_state(project_id, require_visual=False)
    images = {image.view_type: image for image in loaded.scene_images.scenes}

    assert images["scene_left_45"].image_prompt != shared_prompt
    assert images["scene_right_45"].image_prompt != shared_prompt
    assert images["scene_left_45"].image_prompt != images["scene_right_45"].image_prompt


def test_loading_legacy_scene_group_updates_version_manifest_display_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(project_store_module, "OUTPUT_DIR", tmp_path)
    store = ProjectStore()
    monkeypatch.setattr(api_context.project_store, "_target", store)
    project_id = store.create_project("组图版本提示词迁移")
    state = _legacy_state(project_id)
    location_id = state.script.asset_manifest.locations[0].location_id
    project_dir = tmp_path / project_id
    left_path = project_dir / "assets" / "scenes" / location_id / "scene_left_45.png"
    left_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (64, 64), "white").save(left_path)
    shared_prompt = "旧版本共享供应商提示词"
    state.image_generation_policy = {"provider": "wan", "model": "wan2.7-image-pro", "resolution": "2K"}
    state.scene_images = SceneImageAssets(scenes=[
        SceneImage(
            scene_id=1, location_id=location_id, state_id="", location="地下室",
            view_type="scene_left_45", image_prompt=shared_prompt, image_path=str(left_path),
            seed=1, generation_mode="reference_group", group_id="group-1", version_id="left-v1",
        ),
    ])
    manifest_path = project_dir / "assets" / "asset_manifest.json"
    manifest_path.write_text(json.dumps({
        "versions": {
            f"scene:{location_id}:scene_left_45": [{
                "id": "left-v1", "path": str(left_path), "prompt": shared_prompt,
                "active": True, "source": "reference_group",
            }]
        }
    }, ensure_ascii=False), encoding="utf-8")
    store.set_state_json(project_id, api_context._state_to_json(state))

    api_context._load_project_state(project_id, require_visual=False)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    record = manifest["versions"][f"scene:{location_id}:scene_left_45"][0]
    assert record["provider_prompt"] == shared_prompt
    assert record["display_prompt"] == record["prompt"]
    assert record["prompt"] != shared_prompt


@pytest.mark.parametrize("legacy_template_version", [1, 6, 8])
def test_loading_legacy_scene_master_rebuilds_canonical_empty_scene_prompt(
    tmp_path, monkeypatch, legacy_template_version,
):
    monkeypatch.setattr(project_store_module, "OUTPUT_DIR", tmp_path)
    store = ProjectStore()
    monkeypatch.setattr(api_context.project_store, "_target", store)
    project_id = store.create_project("场景母版提示词迁移")
    state = _legacy_state(project_id)
    state.creative_profile = CreativeProfile(
        visual_style_id="semi_realistic_anime",
        art_style="动漫风",
        video_ratio="9:16",
    )
    location_id = state.script.asset_manifest.locations[0].location_id
    image_path = tmp_path / project_id / "assets" / "scenes" / location_id / "scene_center.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1920, 1080), "white").save(image_path)
    stale_prompt = "旧版场景母版提示词：人物推开铁门并走入地下室，9:16竖屏。"
    state.scene_images = SceneImageAssets(scenes=[
        SceneImage(
            scene_id=1,
            location_id=location_id,
            state_id="",
            location="地下室",
            view_type="scene_center",
            image_prompt=stale_prompt,
            base_prompt=stale_prompt,
            image_path=str(image_path),
            seed=1,
            generation_mode="master",
            version_id="master-v1",
            # Version 6 is the current on-disk format from the previous
            # migration, but it may still contain the placeholder state label.
            prompt_template_version=legacy_template_version,
            is_master=True,
            needs_confirmation=False,
        )
    ])
    store.set_state_json(project_id, api_context._state_to_json(state))

    loaded = api_context._load_project_state(project_id, require_visual=False)
    image = loaded.scene_images.scenes[0]
    location = loaded.script.asset_manifest.locations[0]

    assert image.image_prompt != stale_prompt
    assert "半写实动画概念场景" in image.image_prompt
    assert "人物推开铁门并走入地下室" not in image.image_prompt
    assert "不得出现任何人物" in image.image_prompt
    assert image.prompt_template_version >= 6
    assert location.identity_description
    assert location.fixed_dressing
    assert location.anchor_points


def test_loading_current_scene_master_refreshes_stale_base_prompt_without_touching_custom_text(
    tmp_path, monkeypatch,
):
    """Current-version masters can still carry an old base prompt after a style/state migration."""
    monkeypatch.setattr(project_store_module, "OUTPUT_DIR", tmp_path)
    store = ProjectStore()
    monkeypatch.setattr(api_context.project_store, "_target", store)
    project_id = store.create_project("当前场景母版快照")
    state = _legacy_state(project_id)
    state.creative_profile = CreativeProfile(
        visual_style_id="cinematic_cel",
        art_style="写实",  # stale compatibility text must not win over the stable ID
        video_ratio="9:16",
    )
    location_id = state.script.asset_manifest.locations[0].location_id
    image_path = tmp_path / project_id / "assets" / "scenes" / location_id / "scene_center.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1920, 1080), "white").save(image_path)
    stale_prompt = "旧版提示词：真人照片，人物推开铁门并走入地下室。"
    state.scene_images = SceneImageAssets(scenes=[
        SceneImage(
            scene_id=1,
            location_id=location_id,
            state_id="",
            location="地下室",
            view_type="scene_center",
            image_prompt=stale_prompt,
            base_prompt=stale_prompt,
            custom_instructions="保留墙面水痕和旧灯管，不改变空间结构",
            image_path=str(image_path),
            seed=1,
            generation_mode="master",
            version_id="master-current-v1",
            prompt_template_version=8,
            is_master=True,
            needs_confirmation=False,
        )
    ])
    store.set_state_json(project_id, api_context._state_to_json(state))

    loaded = api_context._load_project_state(project_id, require_visual=False)
    image = loaded.scene_images.scenes[0]

    assert image.base_prompt != stale_prompt
    assert "二维背景绘制" in image.base_prompt
    assert "真人照片" not in image.base_prompt
    assert "保留墙面水痕和旧灯管" in image.image_prompt


def test_character_draft_uses_stable_visual_style_id_over_legacy_art_style():
    """A selected anime style must also control a newly opened character draft card."""
    from src.api.context import ImageAssetRequest, _create_draft_image_asset

    state = DramaState(
        project_id="draft-style-id",
        creative_profile=CreativeProfile(
            visual_style_id="cinematic_cel",
            art_style="写实",  # stale compatibility text from an older project
            video_ratio="9:16",
        ),
        visual_design=VisualDesign(
            overall_style="现代电影级二维赛璐璐",
            color_palette="冷蓝灰",
            global_character_descriptions_cn={"苏眠": "女性，黑色中长发，穿米白色风衣。"},
            scenes=[],
        ),
    )

    asset = _create_draft_image_asset(
        state,
        ImageAssetRequest(asset_type="character", asset_id="苏眠", view_type="front_half"),
    )

    assert "二维手绘动画媒介" in asset.image_prompt
    assert "MANDATORY STYLE: photorealistic" not in asset.image_prompt
    assert "项目视觉约束：艺术风格必须为写实" not in asset.image_prompt


def test_loading_repairs_legacy_storyboard_environment_snapshot_without_erasing_action():
    """Old storyboard [environment] sections must not retain shot-only actions."""
    action = "\u987e\u8861\u6491\u5899\u7ad9\u8d77\uff0c\u6307\u5411\u4e2d\u592e\u88c2\u5f00\u7684\u5e9f\u5f03\u94dc\u949f\u3002"
    transient = "\u4f17\u4eba\u6d88\u5931\u5728\u6697\u9053\u540e"
    location = "\u65e7\u949f\u697c\u5730\u4e0b\u5ba4"
    script = ScriptBreakdown(
        title="\u65e7\u63d0\u793a\u8bcd\u8fc1\u79fb",
        genre="\u60ac\u7591",
        logline="\u65e7\u5206\u955c\u5feb\u7167",
        duration_sec=3,
        narration_style="\u65e0\u65c1\u767d",
        scenes=[Scene(
            scene_id=1,
            duration_sec=3,
            location=location,
            characters=["\u987e\u8861"],
            action_description=action,
            camera_direction="\u4e2d\u666f",
            emotion_tone="\u7d27\u5f20",
        )],
        asset_manifest=VisualAssetManifest(
            locations=[LocationAsset(
                location_id="loc_basement",
                canonical_name=location,
                representative_scene_id=1,
                shot_ids=[1],
                fixed_dressing=f"\u5ba4\u5185\u5806\u6ee1\u6728\u7bb1\uff1b{action}\uff1b{transient}",
                environment_description=f"\u5ba4\u5185\u5806\u6ee1\u6728\u7bb1\uff1b{action}\uff1b{transient}",
            )],
            shot_bindings=[ShotVisualBinding(scene_id=1, location_id="loc_basement")],
        ),
    )
    prompt = (
        "\u3010\u65f6\u95f4\u8f74\u5206\u955c\u3011\n"
        f"\u3010\u73af\u5883\u3011{location}\u3002\u5ba4\u5185\u5806\u6ee1\u6728\u7bb1\uff1b{action}\uff1b{transient}\u3002"
        "\u5730\u4e0b\u5ba4\u4e2d\u592e\u94dc\u949f\u88c2\u5f00\u4e00\u9053\u7ec6\u7f1d\u3002\n"
        "\u3010\u955c\u5934\u8bed\u8a00\u3011\u4e2d\u666f\u56fa\u5b9a\n"
        f"\u3010\u6838\u5fc3\u5206\u955c\u610f\u56fe\u3011{action}"
    )
    state = DramaState(
        project_id="legacy-storyboard-environment",
        script=script,
        generation_prompts=GenerationPrompts(
            api_provider="mock",
            shots=[ShotPrompt(scene_id=1, prompt=prompt, duration_sec=3)],
        ),
    )

    loaded = api_context._state_from_json(api_context._state_to_json(state))
    repaired = loaded.generation_prompts.shots[0].prompt
    environment = repaired.split("\u3010\u73af\u5883\u3011", 1)[1].split("\u3010\u955c\u5934\u8bed\u8a00\u3011", 1)[0]

    assert action not in environment
    assert transient not in environment
    assert "\u5ba4\u5185\u5806\u6ee1\u6728\u7bb1" in environment
    assert action in repaired
    assert "\u73af\u5883\u3011" in repaired and "\n\u3010\u955c\u5934\u8bed\u8a00\u3011" in repaired
