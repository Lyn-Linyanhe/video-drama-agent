"""Deterministic media validation used by generation jobs."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import base64
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageStat
from src.utils.video import get_ffmpeg_path


class MediaQualityFailure(RuntimeError):
    def __init__(self, message: str, report: dict[str, Any]):
        super().__init__(message)
        self.report = report


def _quality_flag(value: Any) -> bool:
    """Parse persisted quality flags without treating ``"false"`` as true."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value or "").strip().casefold() in {"true", "1", "yes", "y", "passed", "pass"}


def image_allows_automatic_reference(image: Any) -> bool:
    """Return whether an image is safe to add as an automatic video reference.

    A visual review warning must never be overridden by a stale legacy opt-in
    flag.  Projects created before quality reports existed remain compatible
    when the field was genuinely absent; newly persisted unreviewed uploads and
    provider results stay out of automatic references until reviewed.
    """
    if getattr(image, "is_stale", False) or getattr(image, "needs_confirmation", False):
        return False

    report = getattr(image, "quality_report", None)
    report = report if isinstance(report, dict) else {}
    status = str(report.get("status") or "").strip().casefold()
    if _quality_flag(report.get("should_regenerate")):
        return False
    if "passed" in report and not _quality_flag(report.get("passed")):
        return False

    if status == "passed":
        # A report can explicitly revoke automatic use even when its aggregate
        # status was copied as passed by an older migration.
        if "automatic_reference_allowed" in report and not _quality_flag(report.get("automatic_reference_allowed")):
            return False
        return True

    if status == "unreviewed":
        # Mock output is intentionally usable in local, no-cost tests only when
        # the generator explicitly opted in. Uploaded or real-provider assets
        # must wait for a visual review.
        provider = str(getattr(image, "api_provider", "") or "").strip().casefold()
        return provider == "mock" and _quality_flag(report.get("automatic_reference_allowed"))

    if not status:
        # ``model_fields_set`` distinguishes a legacy serialized image (where
        # quality_report did not exist) from a new image with an empty report.
        fields_set = getattr(image, "model_fields_set", set())
        if "quality_report" not in fields_set:
            return True
    return False


def _check(name: str, passed: bool, message: str) -> dict[str, Any]:
    return {"name": name, "passed": passed, "message": message}


def inspect_image(
    image_path: str | Path,
    expected_width: int = 0,
    expected_height: int = 0,
    allow_placeholder: bool = False,
) -> dict[str, Any]:
    path = Path(image_path)
    checks: list[dict[str, Any]] = []
    if not path.is_file():
        return {
            "status": "failed",
            "technical_checks": [_check("file", False, "图片文件不存在")],
            "visual_review": {"status": "skipped", "reason": "技术检查未通过"},
            "warnings": [],
        }
    try:
        with Image.open(path) as source:
            source.verify()
        with Image.open(path) as source:
            image = source.convert("RGB")
            width, height = image.size
            checks.append(_check("decode", True, f"图片可解码，尺寸 {width}x{height}"))
            if expected_width and expected_height:
                expected_ratio = expected_width / expected_height
                actual_ratio = width / max(1, height)
                # Mock clients intentionally return a single placeholder size
                # for every view.  Keep the real aspect-ratio contract for
                # paid providers while allowing that deterministic test asset.
                ratio_ok = abs(actual_ratio - expected_ratio) / expected_ratio <= 0.12 or allow_placeholder
                checks.append(_check("aspect_ratio", ratio_ok, "宽高比符合目标" if ratio_ok else "宽高比偏离目标超过 12%"))
            sample = image.resize((64, 64))
            stats = ImageStat.Stat(sample)
            variation = sum(stats.stddev) / 3
            not_blank = variation >= 2.0 or allow_placeholder
            checks.append(_check("not_blank", not_blank, "画面包含有效变化" if not_blank else "图片接近空白或纯色"))
            edge_stats = ImageStat.Stat(sample.filter(ImageFilter.FIND_EDGES))
            edge_variation = sum(edge_stats.stddev) / 3
            sharp_enough = edge_variation >= 3.0 or allow_placeholder
            checks.append(_check("sharpness", sharp_enough, "未发现严重模糊" if sharp_enough else "图片可能严重模糊"))
    except Exception as exc:
        checks.append(_check("decode", False, f"图片损坏或无法解码：{str(exc)[:160]}"))
    failed = [item for item in checks if not item["passed"]]
    return {
        "status": "failed" if failed else "passed",
        "technical_checks": checks,
        "visual_review": {"status": "skipped", "reason": "当前模型未执行视觉审核"},
        "warnings": [],
    }


def inspect_video(
    video_path: str | Path,
    expected_duration: float = 0,
    allow_placeholder: bool = False,
) -> dict[str, Any]:
    path = Path(video_path)
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []
    if not path.is_file() or path.stat().st_size == 0:
        return {
            "status": "failed",
            "technical_checks": [_check("file", False, "视频文件不存在或为空")],
            "visual_review": {"status": "skipped", "reason": "技术检查未通过"},
            "warnings": [],
        }
    checks.append(_check("file", True, f"Video file exists, size {path.stat().st_size} bytes"))
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {
            "status": "warning",
            "technical_checks": [
                *checks,
                _check("probe_skipped", True, "ffprobe was not found; deep video validation was skipped"),
            ],
            "visual_review": {"status": "skipped", "reason": "ffprobe unavailable"},
            "warnings": ["ffprobe is not installed; accepted the generated non-empty video file"],
        }
    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "error", "-show_entries",
                "format=duration:stream=codec_type,width,height", "-of", "json", str(path),
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        payload = json.loads(result.stdout or "{}")
        streams = payload.get("streams", [])
        has_video = any(item.get("codec_type") == "video" for item in streams)
        duration = float((payload.get("format") or {}).get("duration") or 0)
        checks.append(_check("decode", has_video and duration > 0, f"视频流可解码，时长 {duration:.1f}s"))
        if expected_duration and duration:
            duration_ok = abs(duration - expected_duration) / expected_duration <= 0.55 or allow_placeholder
            checks.append(_check("duration", duration_ok, "时长在允许误差内" if duration_ok else "视频时长偏差过大"))
        frames = subprocess.run(
            [
                get_ffmpeg_path(), "-v", "error", "-i", str(path), "-vf",
                "fps=1,scale=32:32,format=gray", "-frames:v", "3",
                "-f", "rawvideo", "pipe:1",
            ],
            capture_output=True,
            timeout=20,
            check=True,
        ).stdout
        frame_size = 32 * 32
        samples = [frames[index:index + frame_size] for index in range(0, len(frames), frame_size)]
        samples = [sample for sample in samples if len(sample) == frame_size]
        if samples:
            means = [sum(sample) / frame_size for sample in samples]
            not_black = max(means) >= 8 or allow_placeholder
            checks.append(_check("not_black", not_black, "未发现持续黑屏" if not_black else "视频采样帧持续黑屏"))
        if len(samples) >= 2:
            differences = [
                sum(abs(a - b) for a, b in zip(samples[index - 1], samples[index])) / frame_size
                for index in range(1, len(samples))
            ]
            has_motion = max(differences) >= 0.8 or allow_placeholder
            checks.append(_check("not_frozen", has_motion, "采样帧存在画面变化" if has_motion else "视频可能为完全静帧"))
    except Exception as exc:
        checks.append(_check("decode", False, f"视频损坏或无法探测：{str(exc)[:160]}"))
    failed = [item for item in checks if not item["passed"]]
    return {
        "status": "failed" if failed else "passed",
        "technical_checks": checks,
        "visual_review": {"status": "skipped", "reason": "当前模型未执行视觉审核"},
        "warnings": [],
    }


def resolve_review_config() -> dict[str, str]:
    """Resolve review settings without treating a non-DashScope gateway as Qwen."""
    import src.config as config

    provider = str(getattr(config, "REVIEW_LLM_PROVIDER", "") or "").lower()
    model = str(getattr(config, "REVIEW_LLM_MODEL", "") or "").lower()
    api_key = str(getattr(config, "REVIEW_LLM_API_KEY", "") or "")
    base_url = str(getattr(config, "REVIEW_LLM_BASE_URL", "") or "")
    if provider:
        return {"provider": provider, "model": model, "api_key": api_key, "base_url": base_url}
    happyhorse_base_url = str(getattr(config, "HAPPYHORSE_BASE_URL", "") or "").rstrip("/").lower()
    happyhorse_key = str(getattr(config, "HAPPYHORSE_API_KEY", "") or "")
    if happyhorse_key and happyhorse_base_url in {"https://dashscope.aliyuncs.com", "http://dashscope.aliyuncs.com"}:
        return {
            "provider": "qwen",
            "model": "qwen-vl-plus",
            "api_key": happyhorse_key,
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        }
    return {"provider": "", "model": model, "api_key": api_key, "base_url": base_url}


def _has_positive_requirement(text: str, terms: tuple[str, ...]) -> bool:
    """Return whether a term is requested rather than only mentioned as a negation."""
    folded = str(text or "").casefold()
    negation_markers = (
        "不指定", "不明确", "不要求", "不含", "不包含", "不得", "禁止", "不要", "无需", "没有", "无", "未",
        "not ", "no ", "without ",
    )
    for term in terms:
        start = 0
        needle = term.casefold()
        while True:
            index = folded.find(needle, start)
            if index < 0:
                break
            prefix = folded[max(0, index - 12):index]
            if not any(marker in prefix for marker in negation_markers):
                return True
            start = index + len(needle)
    return False


def _scene_required_contract_categories(expected_prompt: str, view_type: str = "") -> set[str]:
    """Derive only the scene evidence categories actually requested by a prompt.

    Identity, requested view, and forbidden elements are universal. Indoor/outdoor
    boundary, fixed anchors, and spatial direction are conditional so a generic
    location does not fail review merely because the script never specified them.
    """
    categories = {"identity", "view", "forbidden"}
    text = str(expected_prompt or "")
    if _has_positive_requirement(text, ("室内", "室外", "户外", "室内外边界", "indoor", "outdoor", "indoor/outdoor")):
        categories.add("boundary")
    if _has_positive_requirement(text, ("固定陈设", "空间锚点", "环境锚点", "固定结构", "anchor", "fixed dressing")):
        categories.add("anchors")
    if _has_positive_requirement(text, ("中心线", "朝向", "方向", "向外", "向下", "向上", "向内", "左前", "右前", "深处", "门后", "石阶", "空间关系", "orientation", "direction")):
        categories.add("direction")
    return categories


def _character_contract_category(label: str) -> str:
    text = str(label or "").strip().casefold().replace(" ", "")
    if any(token in text for token in ("角色身份", "人物身份", "characteridentity", "identity")):
        return "identity"
    if any(token in text for token in ("风格媒介", "艺术风格", "动漫", "写实", "style", "medium")):
        return "style"
    if any(token in text for token in ("视图", "构图", "机位", "view", "framing", "orientation")):
        return "view"
    if any(token in text for token in ("禁用元素", "禁止元素", "forbidden", "negative", "prohibited")):
        return "forbidden"
    return ""


def _view_orientation_contract(asset_type: str, view_type: str) -> str:
    """Provide a provider-independent orientation rule for visual review."""
    if asset_type != "character":
        return ""
    rules = {
        "back_full": (
            "STRICT ORIENTATION CONTRACT: strict rear full-body view; no visible face, "
            "profile, eyes, nose, mouth, facial reflection, head turn, or three-quarter rear view; "
            "crown to shoes must be visible."
        ),
        "left_profile": (
            "STRICT ORIENTATION CONTRACT: strict left profile; only the left facial contour may be visible; "
            "no frontal face, right profile, rear view, three-quarter view, or head turn."
        ),
        "right_profile": (
            "STRICT ORIENTATION CONTRACT: strict right profile; only the right facial contour may be visible; "
            "no frontal face, left profile, rear view, three-quarter view, or head turn."
        ),
        "front_full": (
            "STRICT ORIENTATION CONTRACT: strict front full-body view; face and both shoes must be visible; "
            "no profile, rear view, crop, or extra person."
        ),
        "front_half": (
            "STRICT ORIENTATION CONTRACT: strict front bust view cropped at mid-chest; no waist, legs, knees, or shoes."
        ),
    }
    return rules.get(view_type, "")


def _review_prompt_excerpt(expected_prompt: str, limit: int = 6000) -> str:
    """Keep both identity/state context and trailing view/negative constraints."""
    text = str(expected_prompt or "")
    if len(text) <= limit:
        return text
    head = max(1, int(limit * 0.68))
    tail = max(1, limit - head)
    return f"{text[:head]}\n...[middle constraints elided]...\n{text[-tail:]}"


def _scene_weather_contract(expected_prompt: str) -> str:
    """Scope indoor/outdoor weather checks to facts actually authored.

    A global "indoor rain is invalid" rule breaks greenhouses, stages, and
    controlled experiments.  Conversely, an unspecified location should not
    acquire an invented weather requirement merely because the reviewer
    prompt has a default outdoor clause.
    """
    text = str(expected_prompt or "").casefold()
    indoor = any(token in text for token in ("室内", "室内场景", "房间", "走廊", "实验室", "温室", "地下室", "indoor", "inside"))
    outdoor = any(token in text for token in ("室外", "户外", "露天", "街道", "街角", "外立面", "雨夜街", "outdoor", "outside"))
    explicit_no_rain = any(token in text for token in ("无直接降雨", "不得出现直接降雨", "不要出现直接降雨", "禁止直接降雨", "no direct rain"))
    weather = any(token in text for token in ("雨", "雪", "雾", "晴", "天气", "夜", "白天", "rain", "snow", "fog", "weather"))
    if explicit_no_rain:
        return "室内场景不得出现直接降雨或露天雨幕；室外场景必须与指定天气（包括大雨）一致。"
    if indoor and not outdoor and weather:
        return "按生成要求核对室内外边界和天气；若明确要求室内人工降雨或其他受控天气，不得仅因室内空间而判定为错误。"
    if outdoor and weather:
        return "室外场景必须与生成要求中明确的天气和时间一致；未写明的天气细节不得臆造。"
    return "按生成要求核对室内外边界和天气；未指定时不自行推断室内外或天气。"


def build_image_review_prompt(asset_type: str, view_type: str, expected_prompt: str) -> str:
    """Build the visual-review contract for one generated asset image."""
    contracts = {
        "character": "This is a single character asset image, not a video contact sheet. Verify identity, hairstyle, clothing, anime versus live-action medium, 单个角色, clean background, and requested view: 正面半身, 正面全身, 左侧面, or strict 背面.",
        "scene": (
            "This is a single scene asset image, not a video contact sheet. Verify 空场景, location structure and "
            "anchors, time/weather, requested medium, and 禁止人物. 以下任一问题都必须判定为不通过并要求重新生成："
            "画面出现要求禁止的文字、字母、数字、乱码、水印或 UI；不得出现标牌、门牌、路牌、广告牌、招牌、门牌号或伪文字；"
            f"{_scene_weather_contract(expected_prompt)}"
            "生成要求中明确列出的固定陈设或空间锚点缺失；地点身份、室内外边界、中心轴或要求的视角明显错误。"
        ),
        "prop": "This is a single prop asset image, not a video contact sheet. Verify 单一道具, appearance, material, color, requested view, clean background, and 禁止人物、手或其他物体.",
    }
    scene_evidence_contract = ""
    if asset_type == "scene":
        scene_evidence_contract = scene_evidence_contract + (
            "\n通过前必须逐项检查并填写 contract_checks：地点身份、室内外边界、固定陈设/空间锚点、"
            "要求视角、禁用元素，以及空间方向。楼梯、走廊、门洞和出入口必须根据画面可见几何关系核对"
            "上/下、内/外、左/右和朝向；只复述生成要求不能作为证据。任何一项证据缺失、模糊或与要求相反，"
            "passed 必须为 false 且 should_regenerate 必须为 true。"
        )
    prop_state_contract = ""
    state_requirement = _prop_state_requirement(expected_prompt) if asset_type == "prop" else ""
    if state_requirement:
        prop_state_contract = (
            f"\n这是带有明确可动状态要求的道具，必须逐项核对并填写 contract_checks：道具身份、道具状态、禁用元素。"
            f"道具状态要求为：{state_requirement}。只复述文字要求不能作为证据，必须描述图像中实际可见的铰链、刀刃、盖子或其他可动部件状态。"
            "任一道具状态证据缺失、模糊或与要求相反，passed 必须为 false 且 should_regenerate 必须为 true。"
        )
    required_contract_categories: set[str] | None = None
    if asset_type == "scene":
        required_contract_categories = _scene_required_contract_categories(expected_prompt, view_type)
        labels = {
            "identity": "identity", "boundary": "boundary", "anchors": "anchors",
            "view": "view", "forbidden": "forbidden", "direction": "direction",
        }
        category_text = ", ".join(
            labels[item] for item in ("identity", "boundary", "anchors", "view", "forbidden", "direction")
            if item in required_contract_categories
        )
        scene_evidence_contract = (
            f"\nRequired scene evidence categories: {category_text}. "
            "Return one contract_checks item for every listed category, using visible image evidence only. "
            "A category not listed is not a failure. Any listed category that is missing, ambiguous, or contradictory "
            "requires passed=false and should_regenerate=true."
        )
        if "direction" in required_contract_categories:
            scene_evidence_contract += " 空间方向证据必须核对上/下、内/外、左/右和镜头朝向；只复述生成要求不能作为证据。"
    elif asset_type == "character":
        required_contract_categories = {"identity", "style", "view", "forbidden"}
    elif asset_type == "prop":
        required_contract_categories = {"identity", "view", "forbidden"}
    if state_requirement:
        assert required_contract_categories is not None
        required_contract_categories.add("state")

    orientation_contract = _view_orientation_contract(asset_type, view_type)
    return (
        f"{contracts.get(asset_type, 'Verify this single generated image against its requested subject and view.')}\n"
        f"Requested view: {view_type or 'unspecified'}.\nGeneration requirements:\n{_review_prompt_excerpt(expected_prompt)}\n"
        f"{scene_evidence_contract}{prop_state_contract}\n{orientation_contract}"
        'Return JSON only: {"passed":true,"score":0.0,"should_regenerate":false,'
        '"contract_checks":[{"item":"criterion","passed":true,"evidence":"visible image evidence"}],'
        '"issues":["specific issue"],"summary":"brief summary"}.'
    )


def _vision_capability() -> tuple[bool, str]:
    import src.config as config

    if not getattr(config, "REVIEW_LLM_ENABLED", True):
        return False, "review disabled"

    review = resolve_review_config()
    review_provider = review["provider"]
    review_model = review["model"]
    review_key = review["api_key"]
    if review_provider == "qwen":
        return bool(review_key and review_model), f"qwen/{review_model or 'unconfigured'}"
    if review_provider == "openai":
        supported = bool(review_key and review_model.startswith(("gpt-4o", "gpt-4.1", "gpt-5")))
        return supported, f"openai/{review_model or 'unconfigured'}"

    provider = str(config.LLM_PROVIDER or "").lower()
    model = str(config.LLM_MODEL or "").lower()
    supported = provider == "openai" and model.startswith(("gpt-4o", "gpt-4.1", "gpt-5"))
    return supported, f"{provider}/{model}" if provider or model else "unconfigured"


def _coerce_review_bool(value: Any, default: bool = False) -> bool:
    """Parse provider booleans without treating the string ``\"false\"`` as true."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().casefold()
    if text in {"true", "1", "yes", "y", "是", "通过", "ok", "pass", "passed"}:
        return True
    if text in {"false", "0", "no", "n", "否", "不通过", "fail", "failed"}:
        return False
    return default


def _scene_contract_category(label: str) -> str:
    """Map model-authored labels to the six required scene evidence categories."""
    text = str(label or "").strip().casefold().replace(" ", "")
    if any(token in text for token in ("地点身份", "场景身份", "locationidentity", "location", "identity")):
        return "identity"
    if any(token in text for token in ("室内外", "空间边界", "indooroutdoor", "indoor", "outdoor", "boundary")):
        return "boundary"
    if any(token in text for token in ("固定陈设", "空间锚点", "场景锚点", "fixeddressing", "anchor", "fixture")):
        return "anchors"
    if any(token in text for token in ("要求视角", "镜头视角", "camera", "view", "shotview")):
        return "view"
    if any(token in text for token in ("禁用元素", "禁止元素", "负面约束", "forbidden", "negative", "prohibited")):
        return "forbidden"
    if any(token in text for token in ("空间方向", "方向", "朝向", "spatialdirection", "orientation", "direction")):
        return "direction"
    return ""


def _prop_state_requirement(expected_prompt: str) -> str:
    """Return an explicit movable-state requirement when the prompt has one.

    The generic prop template mentions both folding and unfolding as a
    compatibility note.  That line must not by itself force a state review;
    only an explicit requirement in the asset-specific prompt should do so.
    """
    text = str(expected_prompt or "")
    # The shared prop template contains a compatibility note for soft or
    # flexible objects ("保持同一展开或折叠状态").  It is intentionally not a
    # requirement: treating it as one makes rigid props such as folders,
    # badges, or keys fail review for a state they never declared.  Only an
    # asset-specific description or an explicit state lock should reach the
    # checks below.
    text = re.sub(r"柔软(?:或可变形)?道具保持同一展开或折叠状态[。；;]?", "", text)
    # Prefer explicit state locks and honor negation before generic template text.
    if any(token in text for token in ("完全闭合/收起状态", "刀刃必须完全收回", "保持闭合", "保持收起", "完全收回")):
        return "闭合/收回状态（刀刃不可见，铰链与手柄保持合上）"
    if any(token in text for token in ("完全展开/打开状态", "保持打开状态", "完全展开", "刀刃完全展开")):
        return "展开/打开状态（可动部件处于要求的打开位置）"
    if re.search(r"(?:禁止|不得|不可|不应|不要|严禁)\s*(?:完全)?(?:打开|展开|露出刀刃)", text):
        return "闭合/收回状态（刀刃不可见，铰链与手柄保持合上）"
    if re.search(r"(?:禁止|不得|不可|不应|不要|严禁)\s*(?:闭合|收起)", text):
        return "展开/打开状态（可动部件处于要求的打开位置）"
    if re.search(r"(?:保持|必须|要求|状态为|状态：)[^。\n]{0,24}(?:闭合|合上|收回|刀刃完全收回)", text):
        return "闭合/收回状态（刀刃不可见，铰链与手柄保持合上）"
    if re.search(r"(?:保持|必须|要求|状态为|状态：)[^。\n]{0,24}(?:展开|打开|刀刃露出)", text):
        return "展开/打开状态（可动部件处于要求的打开位置）"
    return ""


def _prop_contract_category(label: str) -> str:
    """Map model-authored labels to the prop evidence contract categories."""
    text = str(label or "").strip().casefold().replace(" ", "")
    if any(token in text for token in ("道具身份", "单一道具", "物体身份", "propidentity", "objectidentity")):
        return "identity"
    if any(token in text for token in ("道具状态", "可动状态", "闭合", "展开", "收回", "propstate", "movablestate", "state")):
        return "state"
    if any(token in text for token in ("禁用元素", "禁止元素", "forbidden", "negative", "prohibited")):
        return "forbidden"
    return ""


def _parse_visual_review(
    raw: str,
    *,
    require_contract_checks: bool = False,
    contract_kind: str = "scene",
    required_contract_categories: set[str] | None = None,
) -> dict[str, Any]:
    text = (raw or "").strip()
    match = re.search(r"\{[\s\S]*\}", text)
    try:
        payload = json.loads(match.group(0) if match else text)
    except (json.JSONDecodeError, AttributeError):
        return {
            "status": "warning",
            "passed": False,
            "score": 0.5,
            "should_regenerate": True,
            "issues": [text[:300] or "视觉审核返回格式无效"],
            "summary": "视觉审核返回格式无效",
        }

    issues = [str(item)[:300] for item in payload.get("issues", []) if str(item).strip()]
    contract_checks: list[dict[str, Any]] = []
    contract_failed = False
    if require_contract_checks:
        raw_checks = payload.get("contract_checks")
        if not isinstance(raw_checks, list):
            raw_checks = []
        for item in raw_checks:
            if not isinstance(item, dict):
                continue
            name = str(item.get("item") or "").strip()[:100]
            evidence = str(item.get("evidence") or "").strip()[:300]
            if not name or not evidence:
                continue
            contract_checks.append({
                "item": name,
                "passed": _coerce_review_bool(item.get("passed", False)),
                "evidence": evidence,
            })
        category_mapper = (
            _prop_contract_category if contract_kind == "prop"
            else _character_contract_category if contract_kind == "character"
            else _scene_contract_category
        )
        categories = {category_mapper(item["item"]) for item in contract_checks}
        labels: dict[str, str] = {}
        contract_name = "scoped visual contract"
        if required_contract_categories is not None:
            required_categories = set(required_contract_categories)
        elif contract_kind == "prop":
            required_categories = {"identity", "state", "forbidden"}
            labels = {
                "identity": "道具身份",
                "state": "道具状态",
                "forbidden": "禁用元素",
            }
            contract_name = "三类道具契约"
        elif contract_kind == "character":
            required_categories = {"identity", "style", "view", "forbidden"}
            labels = {
                "identity": "角色身份", "style": "风格媒介", "view": "要求视图", "forbidden": "禁用元素",
            }
            contract_name = "四类角色契约"
        else:
            required_categories = {"identity", "boundary", "anchors", "view", "forbidden", "direction"}
            labels = {
                "identity": "地点身份", "boundary": "室内外边界", "anchors": "固定陈设/空间锚点",
                "view": "要求视角", "forbidden": "禁用元素", "direction": "空间方向",
            }
            contract_name = "六类场景契约"
        if required_contract_categories is not None:
            required_categories = set(required_contract_categories)
        labels = {
            **labels,
            "identity": labels.get("identity", "identity"),
            "boundary": labels.get("boundary", "boundary"),
            "anchors": labels.get("anchors", "anchors"),
            "view": labels.get("view", "view"),
            "forbidden": labels.get("forbidden", "forbidden"),
            "direction": labels.get("direction", "direction"),
            "style": labels.get("style", "style"),
            "state": labels.get("state", "state"),
        }
        if required_contract_categories is not None:
            contract_name = "scoped visual contract"
        missing_categories = required_categories - categories
        if missing_categories:
            contract_failed = True
            order = (
                ("identity", "state", "view", "forbidden") if contract_kind == "prop"
                else ("identity", "style", "view", "forbidden") if contract_kind == "character"
                else ("identity", "boundary", "anchors", "view", "forbidden", "direction")
            )
            missing_text = "、".join(labels[key] for key in order if key in missing_categories)
            issues.insert(0, f"{('场景' if contract_kind != 'prop' else '道具')}审核缺少逐项视觉证据（{contract_name}）：{missing_text}，不能判定为通过")
        failed_checks = [item for item in contract_checks if not item["passed"]]
        if failed_checks:
            contract_failed = True
            issues = [
                f"{item['item']}不符合：{item['evidence']}"[:300]
                for item in failed_checks
            ] + issues
    score = payload.get("score", payload.get("overall_score", 1.0 if not issues else 0.55))
    try:
        score = max(0.0, min(1.0, float(score)))
    except (TypeError, ValueError):
        score = 1.0 if not issues else 0.55
    if contract_failed:
        score = min(score, 0.55)
    should_regenerate = _coerce_review_bool(payload.get("should_regenerate", False)) or score < 0.65 or contract_failed
    passed = _coerce_review_bool(payload.get("passed", not issues and not should_regenerate), default=not issues and not should_regenerate) and not should_regenerate
    return {
        "status": "passed" if passed else "warning",
        "passed": passed,
        "score": score,
        "should_regenerate": should_regenerate,
        "issues": issues,
        "summary": str(payload.get("summary", "") or "")[:500],
        "contract_checks": contract_checks,
    }


async def _review_image_with_openai_compatible(
    image_path: str | Path,
    expected_prompt: str,
    required_view_type: str = "",
    asset_type: str = "image",
) -> dict[str, Any]:
    import src.config as config
    from openai import AsyncOpenAI

    review = resolve_review_config()
    provider = review["provider"]
    if provider not in {"qwen", "openai"}:
        return {"status": "skipped", "reason": f"review provider {provider or 'unset'} is not supported"}

    if provider == "qwen":
        api_key = review["api_key"]
        base_url = review["base_url"] or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        model = review["model"] or "qwen-vl-plus"
    else:
        api_key = str(getattr(config, "REVIEW_LLM_API_KEY", "") or getattr(config, "OPENAI_API_KEY", "") or "")
        base_url = str(getattr(config, "REVIEW_LLM_BASE_URL", "") or getattr(config, "OPENAI_BASE_URL", "") or "")
        model = str(getattr(config, "REVIEW_LLM_MODEL", "") or getattr(config, "LLM_MODEL", "") or "gpt-4o-mini")

    if not api_key:
        return {"status": "skipped", "reason": f"{provider} review API key is not configured"}

    path = Path(image_path)
    data = path.read_bytes()
    ext = path.suffix.lower().lstrip(".")
    mime_type = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}.get(ext, "image/jpeg")
    data_uri = f"data:{mime_type};base64,{base64.b64encode(data).decode('ascii')}"

    orientation_rule = ""
    if required_view_type == "back_full":
        orientation_rule = "如果要求是正后方全身图，任何可见正脸、侧脸、回头或三分之二背面都必须列为问题。"
    elif required_view_type == "left_profile":
        orientation_rule = "如果要求是左侧面图，任何正脸、右侧脸、背影或回头都必须列为问题。"

    client_kwargs: dict[str, Any] = {
        "api_key": api_key,
        "timeout": max(30, int(getattr(config, "REVIEW_LLM_TIMEOUT", 120) or 120)),
    }
    if base_url:
        client_kwargs["base_url"] = base_url
    client = AsyncOpenAI(**client_kwargs)

    prompt = (
        "请审核这张由视频抽帧拼成的画面，判断生成视频是否明显异常或偏离要求。\n"
        "只报告肉眼可见、足以影响使用的问题；不要因为风格差异吹毛求疵。\n"
        "重点检查：黑屏/空白、严重静帧、人物崩脸、手和肢体异常、多出人物、关键角色或道具缺失、场景明显不符、动作与镜头意图明显不符。\n"
        f"生成要求：\n{_review_prompt_excerpt(expected_prompt)}\n"
        f"{orientation_rule}\n"
        '只输出 JSON：{"passed":true,"score":0.0,"should_regenerate":false,"issues":["具体问题"],"summary":"一句话总结"}'
    )
    if asset_type in {"character", "scene", "prop"}:
        prompt = build_image_review_prompt(asset_type, required_view_type, expected_prompt)
        if orientation_rule:
            prompt = f"{prompt}\n{orientation_rule}"
    response = await client.chat.completions.create(
        model=model,
        temperature=0.1,
        max_tokens=800,
        messages=[
            {"role": "system", "content": "你是谨慎的短剧视觉资产质量审核员。只根据图片证据判断，输出严格 JSON。"},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_uri}},
            ]},
        ],
    )
    contract_required = asset_type in {"character", "scene"} or (
        asset_type == "prop" and bool(_prop_state_requirement(expected_prompt))
    )
    required_categories = None
    if asset_type == "scene":
        required_categories = _scene_required_contract_categories(expected_prompt, required_view_type)
    elif asset_type == "character":
        required_categories = {"identity", "style", "view", "forbidden"}
    elif asset_type == "prop":
        required_categories = {"identity", "view", "forbidden"}
        if _prop_state_requirement(expected_prompt):
            required_categories.add("state")
    return _parse_visual_review(
        response.choices[0].message.content or "",
        require_contract_checks=contract_required,
        contract_kind=asset_type,
        required_contract_categories=required_categories,
    )


async def review_image_subjective(
    image_path: str | Path,
    expected_prompt: str,
    required_view_type: str = "",
    asset_type: str = "image",
) -> dict[str, Any]:
    supported, capability = _vision_capability()
    if not supported:
        return {"status": "skipped", "reason": f"当前模型 {capability} 未启用视觉审核"}

    review_provider = resolve_review_config()["provider"]
    if review_provider in {"qwen", "openai"}:
        try:
            return await _review_image_with_openai_compatible(image_path, expected_prompt, required_view_type, asset_type)
        except Exception as exc:
            return {"status": "skipped", "reason": f"视觉审核调用失败：{str(exc)[:180]}"}

    try:
        from src.utils.llm import get_llm_client

        raw = await get_llm_client().analyze_image(
            str(image_path),
            prompt=build_image_review_prompt(asset_type, required_view_type, expected_prompt),
            system="你是谨慎的影视视觉质检员。只报告画面中明确可见的问题，不猜测，只输出 JSON。",
        )
        contract_required = asset_type in {"character", "scene"} or (
            asset_type == "prop" and bool(_prop_state_requirement(expected_prompt))
        )
        required_categories = None
        if asset_type == "scene":
            required_categories = _scene_required_contract_categories(expected_prompt, required_view_type)
        elif asset_type == "character":
            required_categories = {"identity", "style", "view", "forbidden"}
        elif asset_type == "prop":
            required_categories = {"identity", "view", "forbidden"}
            if _prop_state_requirement(expected_prompt):
                required_categories.add("state")
        return _parse_visual_review(
            raw,
            require_contract_checks=contract_required,
            contract_kind=asset_type,
            required_contract_categories=required_categories,
        )
    except Exception as exc:
        return {"status": "skipped", "reason": f"视觉审核调用失败：{str(exc)[:180]}"}


async def review_video_subjective(video_path: str | Path, expected_prompt: str) -> dict[str, Any]:
    supported, capability = _vision_capability()
    if not supported:
        return {"status": "skipped", "reason": f"当前模型 {capability} 未启用视觉审核"}
    contact_sheet = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as handle:
            contact_sheet = Path(handle.name)
        subprocess.run(
            [
                get_ffmpeg_path(), "-v", "error", "-i", str(video_path), "-vf",
                "fps=1,scale=360:-1,tile=2x2", "-frames:v", "1", "-y", str(contact_sheet),
            ],
            capture_output=True,
            timeout=25,
            check=True,
        )
        return await review_image_subjective(contact_sheet, expected_prompt)
    except Exception as exc:
        return {"status": "skipped", "reason": f"视频视觉审核失败：{str(exc)[:180]}"}
    finally:
        if contact_sheet:
            contact_sheet.unlink(missing_ok=True)
