import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw

from src.services import media_quality
from src.services import asset_generation
from src.services.media_quality import inspect_image, inspect_video, review_image_subjective


def test_image_quality_rejects_blank_and_accepts_detailed_image(tmp_path: Path):
    blank = tmp_path / "blank.png"
    Image.new("RGB", (256, 256), "white").save(blank)
    assert inspect_image(blank, 256, 256)["status"] == "failed"

    detailed = tmp_path / "detailed.png"
    image = Image.new("RGB", (256, 256), "white")
    draw = ImageDraw.Draw(image)
    for offset in range(0, 256, 8):
        draw.line((0, offset, 255, 255 - offset), fill=(offset, 30, 255 - offset), width=3)
    image.save(detailed)
    report = inspect_image(detailed, 256, 256)
    assert report["status"] == "passed"
    assert all(item["passed"] for item in report["technical_checks"])


@pytest.mark.skipif(not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg unavailable")
def test_video_quality_detects_static_video_but_allows_mock_placeholder(tmp_path: Path):
    video = tmp_path / "static.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=3",
            "-pix_fmt", "yuv420p", "-y", str(video),
        ],
        check=True,
        timeout=20,
    )
    assert inspect_video(video, expected_duration=3)["status"] == "failed"
    assert inspect_video(video, expected_duration=3, allow_placeholder=True)["status"] == "passed"


def test_video_quality_accepts_nonempty_file_when_ffprobe_is_missing(tmp_path: Path, monkeypatch):
    video = tmp_path / "provider_result.mp4"
    video.write_bytes(b"not empty")
    original_which = shutil.which
    monkeypatch.setattr(shutil, "which", lambda name: None if name == "ffprobe" else original_which(name))

    report = inspect_video(video, expected_duration=3)

    assert report["status"] == "warning"
    assert report["warnings"]
    assert all(item["passed"] for item in report["technical_checks"])


@pytest.mark.asyncio
async def test_subjective_review_is_explicitly_skipped_for_non_vision_model(tmp_path: Path, monkeypatch):
    import src.config as config

    image = tmp_path / "image.png"
    Image.new("RGB", (64, 64), "blue").save(image)
    monkeypatch.setattr(config, "REVIEW_LLM_ENABLED", True)
    monkeypatch.setattr(config, "REVIEW_LLM_PROVIDER", "")
    monkeypatch.setattr(config, "REVIEW_LLM_API_KEY", "")
    monkeypatch.setattr(config, "HAPPYHORSE_API_KEY", "")
    monkeypatch.setattr(config, "LLM_PROVIDER", "deepseek")
    monkeypatch.setattr(config, "LLM_MODEL", "deepseek-chat")
    report = await review_image_subjective(image, "蓝色道具")
    assert report["status"] == "skipped"
    assert "未启用视觉审核" in report["reason"]


def test_image_review_prompt_uses_the_asset_contract_not_a_video_contact_sheet():
    character = media_quality.build_image_review_prompt("character", "front_half", "侦探，短发，黑色风衣")
    scene = media_quality.build_image_review_prompt("scene", "scene_reverse", "雨夜的空地铁站")
    prop = media_quality.build_image_review_prompt("prop", "left", "黄铜钥匙")

    assert "视频抽帧拼图" not in character
    assert "单个角色" in character
    assert "正面半身" in character
    assert "空场景" in scene
    assert "禁止人物" in scene
    assert "单一道具" in prop
    assert "禁止人物、手或其他物体" in prop


def test_scene_review_prompt_rejects_text_indoor_rain_and_missing_anchors():
    prompt = media_quality.build_image_review_prompt(
        "scene",
        "scene_center",
        "地下室空场景；禁止文字；中央长桌和巨大铜钟必须出现；室内无直接降雨。",
    )

    assert "文字、字母、数字、乱码" in prompt
    assert "标牌、门牌、路牌" in prompt
    assert "室内场景不得出现直接降雨或露天雨幕" in prompt
    assert "室外场景必须与指定天气" in prompt
    assert "明确列出的固定陈设或空间锚点缺失" in prompt
    assert "必须判定为不通过" in prompt


def test_scene_review_prompt_requires_visual_evidence_for_spatial_direction():
    prompt = media_quality.build_image_review_prompt(
        "scene",
        "scene_center",
        "山间旅馆地下入口外部；镜头从室外看向门后向下延伸的石阶。",
    )

    assert "上/下、内/外、左/右" in prompt
    assert "只复述生成要求不能作为证据" in prompt
    assert "contract_checks" in prompt


def test_scene_review_prompt_scopes_evidence_to_the_requirements_present():
    prompt = media_quality.build_image_review_prompt(
        "scene",
        "scene_center",
        "未知空间的空场景，只有地点身份和正面中心线视角，不指定室内外、固定陈设或方向锚点。",
    )

    categories = prompt.split("Required scene evidence categories:", 1)[1].split(".", 1)[0]
    assert "identity" in categories
    assert "view" in categories
    assert "forbidden" in categories
    assert "boundary" not in categories


def test_scene_review_prompt_does_not_reject_explicit_indoor_rain_or_invent_weather():
    indoor_rain = media_quality.build_image_review_prompt(
        "scene",
        "scene_center",
        "室内温室空场景；人工降雨装置正在工作；不出现人物。",
    )
    unspecified = media_quality.build_image_review_prompt(
        "scene",
        "scene_center",
        "未知空间空场景；只核对地点身份和中心线视角，不指定天气。",
    )

    assert "室内场景不得出现直接降雨或露天雨幕" not in indoor_rain
    assert "按生成要求核对室内外边界和天气" in indoor_rain
    assert "室外场景必须与指定天气" not in unspecified
    assert "未指定时不自行推断室内外或天气" in unspecified


def test_prop_review_prompt_requires_explicit_movable_state_evidence():
    prompt = media_quality.build_image_review_prompt(
        "prop",
        "front",
        "单一旧式折叠刀，保持闭合状态，刀刃完全收回，不出现第二件物体。",
    )

    assert "道具状态要求为：闭合/收回状态" in prompt
    assert "铰链与手柄保持合上" in prompt
    assert "只复述文字要求不能作为证据" in prompt


def test_prop_review_rejects_missing_or_failed_state_contract():
    missing = media_quality._parse_visual_review(
        '{"passed":true,"score":1,"should_regenerate":false,"issues":[]}',
        require_contract_checks=True,
        contract_kind="prop",
    )
    assert missing["passed"] is False
    assert "道具状态" in missing["issues"][0]

    checks = [
        {"item": "道具身份", "passed": True, "evidence": "单一折叠刀可见"},
        {"item": "道具状态", "passed": False, "evidence": "刀刃已展开"},
        {"item": "禁用元素", "passed": True, "evidence": "未见人物或第二件物体"},
    ]
    failed = media_quality._parse_visual_review(
        json.dumps({"passed": True, "score": 1, "should_regenerate": False, "contract_checks": checks}, ensure_ascii=False),
        require_contract_checks=True,
        contract_kind="prop",
    )
    assert failed["passed"] is False
    assert failed["should_regenerate"] is True
    assert "道具状态" in failed["issues"][0]


def test_scene_review_without_contract_checks_cannot_pass():
    report = media_quality._parse_visual_review(
        '{"passed":true,"score":1,"should_regenerate":false,"issues":[]}',
        require_contract_checks=True,
    )

    assert report["status"] == "warning"
    assert report["passed"] is False
    assert report["should_regenerate"] is True
    assert "逐项视觉证据" in report["issues"][0]


def test_scene_review_with_a_failed_contract_check_cannot_pass():
    checks = [
        {"item": "地点身份", "passed": True, "evidence": "旧砖墙入口可见"},
        {"item": "室内外边界", "passed": True, "evidence": "镜头位于室外"},
        {"item": "固定陈设", "passed": True, "evidence": "单扇铁门可见"},
        {"item": "要求视角", "passed": True, "evidence": "正面中心线"},
        {"item": "禁用元素", "passed": True, "evidence": "未见人物"},
        {"item": "空间方向", "passed": False, "evidence": "石阶向上而非向下"},
    ]
    report = media_quality._parse_visual_review(
        json.dumps({
            "passed": True,
            "score": 1,
            "should_regenerate": False,
            "contract_checks": checks,
            "issues": [],
        }, ensure_ascii=False),
        require_contract_checks=True,
    )

    assert report["status"] == "warning"
    assert report["passed"] is False
    assert report["should_regenerate"] is True
    assert "空间方向" in report["issues"][0]


def test_character_review_accepts_natural_qwen_contract_labels():
    checks = [
        {"item": "正面半身视图", "passed": True, "evidence": "从胸口以上入画"},
        {"item": "身份一致性", "passed": True, "evidence": "脸部、发型和服装一致"},
        {"item": "背景纯净度", "passed": True, "evidence": "纯白背景，无杂物"},
        {"item": "真人写实风格", "passed": True, "evidence": "真实皮肤和服装材质"},
    ]

    report = media_quality._parse_visual_review(
        json.dumps({
            "passed": True,
            "score": 1,
            "should_regenerate": False,
            "contract_checks": checks,
            "issues": [],
        }, ensure_ascii=False),
        require_contract_checks=True,
        contract_kind="character",
    )

    assert report["status"] == "passed"
    assert report["passed"] is True


def test_character_review_accepts_english_clean_background_label():
    checks = [
        {"item": "Identity", "passed": True, "evidence": "same face"},
        {"item": "Anime vs Live-Action Medium", "passed": True, "evidence": "live action"},
        {"item": "Requested View: Front Half", "passed": True, "evidence": "front bust"},
        {"item": "Clean Background", "passed": True, "evidence": "seamless white background"},
    ]

    report = media_quality._parse_visual_review(
        json.dumps({"passed": True, "score": 1, "should_regenerate": False, "contract_checks": checks}),
        require_contract_checks=True,
        contract_kind="character",
    )

    assert report["passed"] is True


def test_character_review_accepts_profile_alias_and_natural_style_background_labels():
    checks = [
        {"item": "身份锁定（性别与年龄感）", "passed": True, "evidence": "身份和年龄感一致"},
        {"item": "真人摄影风格", "passed": True, "evidence": "真实皮肤和服装材质"},
        {"item": "left_profile", "passed": True, "evidence": "严格左侧面轮廓"},
        {"item": "纯白干净背景", "passed": True, "evidence": "背景无杂物"},
    ]

    report = media_quality._parse_visual_review(
        json.dumps({"passed": True, "score": 1, "should_regenerate": False, "contract_checks": checks}, ensure_ascii=False),
        require_contract_checks=True,
        contract_kind="character",
    )

    assert report["passed"] is True


def test_character_review_prompt_requires_fixed_contract_categories():
    prompt = media_quality.build_image_review_prompt("character", "left_profile", "同一角色的左侧面半身图")

    assert "Required character evidence categories: identity, style, view, forbidden" in prompt
    assert "must be exactly one of" in prompt


def test_scene_review_rejects_arbitrary_labels_even_when_six_checks_are_present():
    checks = [
        {"item": f"自定义检查{i}", "passed": True, "evidence": "可见证据"}
        for i in range(6)
    ]

    report = media_quality._parse_visual_review(
        json.dumps({
            "passed": True,
            "score": 1,
            "should_regenerate": False,
            "contract_checks": checks,
            "issues": [],
        }, ensure_ascii=False),
        require_contract_checks=True,
    )

    assert report["status"] == "warning"
    assert report["passed"] is False
    assert report["should_regenerate"] is True
    assert "六类场景契约" in report["issues"][0]


def test_scene_review_treats_string_false_as_failed_contract_check():
    checks = [
        {"item": "地点身份", "passed": True, "evidence": "旧砖墙入口可见"},
        {"item": "室内外边界", "passed": "false", "evidence": "画面实际是室内走廊"},
        {"item": "固定陈设/空间锚点", "passed": True, "evidence": "长桌可见"},
        {"item": "要求视角", "passed": True, "evidence": "正面中心线"},
        {"item": "禁用元素", "passed": True, "evidence": "未见人物与文字"},
        {"item": "空间方向", "passed": True, "evidence": "入口朝向正确"},
    ]

    report = media_quality._parse_visual_review(
        json.dumps({
            "passed": True,
            "score": 1,
            "should_regenerate": "false",
            "contract_checks": checks,
            "issues": [],
        }, ensure_ascii=False),
        require_contract_checks=True,
    )

    assert report["status"] == "warning"
    assert report["passed"] is False
    assert report["should_regenerate"] is True
    assert "室内外边界" in report["issues"][0]


def test_scene_review_can_validate_only_contract_categories_that_apply():
    checks = [
        {"item": "地点身份", "passed": True, "evidence": "可见的固定空间"},
        {"item": "要求视角", "passed": True, "evidence": "正面中心线"},
        {"item": "禁用元素", "passed": True, "evidence": "没有人物和文字"},
    ]
    report = media_quality._parse_visual_review(
        json.dumps({"passed": True, "score": 1, "should_regenerate": False, "contract_checks": checks}, ensure_ascii=False),
        require_contract_checks=True,
        contract_kind="scene",
        required_contract_categories={"identity", "view", "forbidden"},
    )

    assert report["passed"] is True


def test_quality_report_string_false_never_allows_automatic_reference():
    image = type(
        "Image",
        (),
        {
            "quality_report": {"status": "passed", "passed": "false"},
            "api_provider": "wan",
            "model_fields_set": {"quality_report"},
            "is_stale": False,
            "needs_confirmation": False,
        },
    )()

    assert media_quality.image_allows_automatic_reference(image) is False


def test_character_review_prompt_contains_strict_back_and_profile_orientation_contracts():
    back = media_quality.build_image_review_prompt("character", "back_full", "同一角色的正后方全身图")
    profile = media_quality.build_image_review_prompt("character", "left_profile", "同一角色的严格左侧面半身图")

    assert "STRICT ORIENTATION CONTRACT" in back
    assert "no visible face" in back.lower()
    assert "STRICT ORIENTATION CONTRACT" in profile
    assert "strict left profile" in profile.lower()


def test_review_prompt_preserves_tail_constraints_for_long_prompts():
    expected = "地点身份：长场景。" + ("固定陈设和环境细节。" * 260) + "TAIL_VIEW_CONSTRAINT: strict rear view"

    prompt = media_quality.build_image_review_prompt("scene", "scene_reverse", expected)

    assert "TAIL_VIEW_CONSTRAINT: strict rear view" in prompt


def test_official_dashscope_happyhorse_credential_enables_qwen_review(monkeypatch):
    import src.config as config

    monkeypatch.setattr(config, "REVIEW_LLM_PROVIDER", "")
    monkeypatch.setattr(config, "REVIEW_LLM_MODEL", "")
    monkeypatch.setattr(config, "REVIEW_LLM_API_KEY", "")
    monkeypatch.setattr(config, "HAPPYHORSE_API_KEY", "official-key")
    monkeypatch.setattr(config, "HAPPYHORSE_BASE_URL", "https://dashscope.aliyuncs.com")

    review = media_quality.resolve_review_config()

    assert review["provider"] == "qwen"
    assert review["model"] == "qwen-vl-plus"
    assert review["api_key"] == "official-key"
    assert review["base_url"] == "https://dashscope.aliyuncs.com/compatible-mode/v1"


@pytest.mark.asyncio
async def test_main_openai_vision_fallback_uses_the_asset_review_contract(tmp_path, monkeypatch):
    import src.config as config
    import src.utils.llm as llm_module

    image = tmp_path / "prop.png"
    Image.new("RGB", (64, 64), "blue").save(image)
    captured = {}

    class FakeClient:
        async def analyze_image(self, _path, prompt, system):
            captured["prompt"] = prompt
            captured["system"] = system
            return '{"passed":true,"score":1,"should_regenerate":false,"issues":[]}'

    monkeypatch.setattr(config, "REVIEW_LLM_ENABLED", True)
    monkeypatch.setattr(config, "REVIEW_LLM_PROVIDER", "")
    monkeypatch.setattr(config, "REVIEW_LLM_API_KEY", "")
    monkeypatch.setattr(config, "HAPPYHORSE_API_KEY", "")
    monkeypatch.setattr(config, "LLM_PROVIDER", "openai")
    monkeypatch.setattr(config, "LLM_MODEL", "gpt-4o")
    monkeypatch.setattr(llm_module, "get_llm_client", lambda: FakeClient())

    report = await review_image_subjective(image, "黄铜钥匙", "left", asset_type="prop")

    assert report["status"] == "passed"
    assert "单一道具" in captured["prompt"]
    assert "Requested view: left" in captured["prompt"]
    assert "禁止人物、手或其他物体" in captured["prompt"]


@pytest.mark.asyncio
async def test_generated_asset_validation_passes_the_actual_view_to_review(tmp_path, monkeypatch):
    image_path = tmp_path / "prop.png"
    image = Image.new("RGB", (256, 256), "white")
    draw = ImageDraw.Draw(image)
    for offset in range(0, 256, 8):
        draw.line((0, offset, 255, 255 - offset), fill=(offset, 30, 255 - offset), width=3)
    image.save(image_path)
    captured = {}

    async def capture_review(_path, _prompt, required_view_type, asset_type):
        captured["view_type"] = required_view_type
        captured["asset_type"] = asset_type
        return {"status": "passed", "passed": True, "should_regenerate": False, "issues": []}

    monkeypatch.setattr(asset_generation, "review_image_subjective", capture_review)

    await asset_generation._validate_generated_image(
        SimpleNamespace(local_path=str(image_path)),
        "黄铜钥匙左侧视图",
        "prop",
        "left",
        "wan",
        256,
        256,
    )

    assert captured == {"view_type": "left", "asset_type": "prop"}
