"""Capability and policy selection for project-scoped video generation."""

from __future__ import annotations

import math
from typing import Any

from fastapi import HTTPException


SEEDANCE_DEFAULT_MODEL = "doubao-seedance-2-0-fast-260128"


def _seedance_capability(
    *,
    duration_min: int,
    duration_max: int,
    resolutions: list[str],
    max_reference_images: int,
    cost_tier: str,
    text_to_video: bool = True,
    image_to_video: bool = True,
    multi_reference: bool = True,
    legacy: bool = False,
) -> dict[str, Any]:
    """Build a normalized capability record for the Ark task API."""
    return {
        "text_to_video": text_to_video,
        "image_to_video": image_to_video,
        "multi_reference": multi_reference,
        "continuity": image_to_video,
        "supports_first_frame": image_to_video,
        "supports_last_frame": image_to_video,
        "supports_first_last_frame": image_to_video,
        "max_reference_images": max_reference_images,
        "reference_modes": (
            (["text_to_video"] if text_to_video else [])
            + (["first_frame", "first_last_frame"] if image_to_video else [])
            + (["multi_reference"] if multi_reference else [])
        ),
        "duration_min": duration_min,
        "duration_max": duration_max,
        "durations": list(range(duration_min, duration_max + 1)),
        "resolutions": resolutions,
        "cost_tier": cost_tier,
        "legacy": legacy,
        "supports_native_audio": True,
        "supports_native_dialogue": True,
        "native_audio_status": "supported",
        "native_audio_reason": "Seedance client sends generate_audio and verifies the returned audio stream.",
    }


VIDEO_CAPABILITIES: dict[str, dict[str, Any]] = {
    "mock": {"models": {"mock": {
        "text_to_video": True, "image_to_video": True, "multi_reference": True,
        "continuity": True, "max_reference_images": 30,
        "reference_modes": ["text_to_video", "first_frame", "multi_reference"],
        "resolutions": ["720p", "1080p"], "durations": [3, 5, 10, 15],
        "cost_tier": "test",
    }}},
    "happyhorse": {"models": {
        "happyhorse-1.1-i2v": {
            "text_to_video": False, "image_to_video": True, "multi_reference": False,
            "continuity": True, "max_reference_images": 1,
            "reference_modes": ["first_frame"],
            "resolutions": ["720p", "1080p"],
            "duration_min": 4, "duration_max": 15,
            "durations": [], "discrete_durations": False,
            "cost_tier": "economy",
            "supports_native_audio": True,
            "supports_native_dialogue": True,
            "native_audio_status": "best_effort",
            "native_audio_reason": "Happy Horse 视频结果按实际 MP4 音轨检测；当前网关未提供独立音频 URL。",
        },
        "happyhorse-1.1-r2v": {
            "text_to_video": False, "image_to_video": True, "multi_reference": True,
            "continuity": True, "max_reference_images": 9,
            "reference_modes": ["multi_reference"],
            "resolutions": ["720p", "1080p"],
            "duration_min": 4, "duration_max": 15,
            "durations": [], "discrete_durations": False,
            "cost_tier": "balanced",
            "supports_native_audio": True,
            "supports_native_dialogue": True,
            "native_audio_status": "best_effort",
            "native_audio_reason": "Happy Horse 视频结果按实际 MP4 音轨检测；当前网关未提供独立音频 URL。",
        },
    }},
    "wan": {"models": {
        "wan2.6-i2v": {
            "text_to_video": False, "image_to_video": True, "multi_reference": False,
            "continuity": True, "max_reference_images": 1,
            "reference_modes": ["first_frame"],
            "resolutions": ["720p", "1080p"], "durations": [3, 5, 10, 15],
            "cost_tier": "balanced",
        },
        "wan2.6-t2v": {
            "text_to_video": True, "image_to_video": False, "multi_reference": False,
            "continuity": False, "max_reference_images": 0,
            "reference_modes": ["text_to_video"],
            "resolutions": ["720p", "1080p"], "durations": [3, 5, 10, 15],
            "cost_tier": "economy",
        },
    }},
    "seedance": {"models": {
        "doubao-seedance-2-5-260628": _seedance_capability(
            duration_min=4, duration_max=30,
            resolutions=["480p", "720p", "1080p", "4k"],
            max_reference_images=30, cost_tier="premium",
        ),
        "doubao-seedance-2-0-260128": _seedance_capability(
            duration_min=4, duration_max=15,
            resolutions=["480p", "720p", "1080p"],
            max_reference_images=9, cost_tier="premium",
        ),
        "doubao-seedance-2-0-fast-260128": _seedance_capability(
            duration_min=4, duration_max=15,
            resolutions=["480p", "720p", "1080p"],
            max_reference_images=9, cost_tier="balanced",
        ),
        "doubao-seedance-2-0-mini-260615": _seedance_capability(
            duration_min=4, duration_max=15,
            resolutions=["480p", "720p", "1080p"],
            max_reference_images=9, cost_tier="economy",
        ),
        "doubao-seedance-1-5-pro-251215": _seedance_capability(
            duration_min=4, duration_max=15,
            resolutions=["480p", "720p", "1080p"],
            max_reference_images=9, cost_tier="premium",
        ),
        "doubao-seedance-1-0-pro-250528": _seedance_capability(
            duration_min=3, duration_max=15,
            resolutions=["720p", "1080p"],
            max_reference_images=4, cost_tier="balanced",
        ),
        "doubao-seedance-1-0-pro-fast-251015": _seedance_capability(
            duration_min=3, duration_max=15,
            resolutions=["720p", "1080p"],
            max_reference_images=4, cost_tier="economy",
        ),
        # Historical IDs remain readable for existing project policies.
        "doubao-seedance-1-0-pro-fast-250528": _seedance_capability(
            duration_min=3, duration_max=15,
            resolutions=["720p", "1080p"],
            max_reference_images=4, cost_tier="legacy", legacy=True,
        ),
        "doubao-seedance-1-0-lite-t2v-250428": _seedance_capability(
            duration_min=3, duration_max=15,
            resolutions=["720p", "1080p"],
            max_reference_images=0, cost_tier="legacy",
            image_to_video=False, multi_reference=False, legacy=True,
        ),
        "doubao-seedance-1-0-lite-i2v-250428": _seedance_capability(
            duration_min=3, duration_max=15,
            resolutions=["720p", "1080p"],
            max_reference_images=1, cost_tier="legacy",
            text_to_video=False, multi_reference=False, legacy=True,
        ),
    }},
}


# Models without a verified request-and-response audio contract must never be
# presented as native-dialogue capable.  This prevents a silent fallback to TTS.
for _provider_name, _provider_data in VIDEO_CAPABILITIES.items():
    for _model_name, _capability in _provider_data["models"].items():
        _capability.setdefault("supports_native_audio", False)
        _capability.setdefault("supports_native_dialogue", False)
        _capability.setdefault("native_audio_status", "unsupported")
        _capability.setdefault(
            "native_audio_reason",
            f"{_provider_name}/{_model_name} has no verified native-dialogue request contract.",
        )


def audio_capability(policy: dict[str, Any]) -> dict[str, Any]:
    """Return the normalized native-dialogue capability for one policy."""
    capability = dict(policy.get("capability") or {})
    return {
        "provider": str(policy.get("provider") or ""),
        "model": str(policy.get("model") or ""),
        "supported": bool(capability.get("supports_native_dialogue", False)),
        "status": str(capability.get("native_audio_status") or "unsupported"),
        "reason": str(capability.get("native_audio_reason") or ""),
    }


def dashscope_available() -> bool:
    import src.config as config

    return bool(config.HAPPYHORSE_API_KEY) and "dashscope.aliyuncs.com" in str(config.HAPPYHORSE_BASE_URL or "").lower()


def _normalize_provider(provider: str) -> str:
    provider = str(provider or "mock").lower()
    return "seedance" if provider == "seedence" else provider


def parse_integral_duration(value: object, *, field_name: str = "duration_sec") -> int:
    """Parse an executable duration without silently rounding fractional input."""
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer number of seconds; received {value!r}")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number; received {value!r}") from exc
    if not math.isfinite(numeric) or abs(numeric - round(numeric)) > 1e-6:
        raise ValueError(
            f"{field_name} must be an integer number of seconds; received {value!r}"
        )
    return int(round(numeric))


def duration_options_from_capability(capability: dict | None) -> list[int]:
    """Return the provider's discrete executable durations, if declared.

    A ``duration_min``/``duration_max`` range is intentionally not converted to
    a list here: models such as Seedance accept every integer in that range.
    Only capabilities explicitly marked ``discrete_durations`` expose a
    finite set that the planner and final request validator must honor.
    """
    data = dict(capability or {})
    if not data.get("discrete_durations"):
        return []
    values: list[int] = []
    for value in data.get("durations") or []:
        try:
            parsed = parse_integral_duration(value, field_name="duration option")
        except ValueError:
            continue
        if parsed >= 1 and parsed not in values:
            values.append(parsed)
    return sorted(values)


def duration_options_for_policy(policy: dict | None) -> list[int]:
    """Return finite duration choices for a normalized or raw policy."""
    return duration_options_from_capability((policy or {}).get("capability") or {})


def default_video_policy() -> dict[str, Any]:
    import src.config as config

    default_api = _normalize_provider(str(config.DEFAULT_API or "mock"))
    if default_api == "happyhorse":
        model = str(config.HAPPYHORSE_MODEL or "happyhorse-1.1-r2v")
        if model not in VIDEO_CAPABILITIES["happyhorse"]["models"]:
            model = "happyhorse-1.1-r2v"
        return {
            "provider": "happyhorse", "model": model,
            "resolution": str(config.HAPPYHORSE_RESOLUTION or "1080p"),
            "unit_price_rmb_per_sec": 0.0,
        }
    if default_api == "seedance":
        model = str(getattr(config, "ARK_MODEL", "") or SEEDANCE_DEFAULT_MODEL)
        if model not in VIDEO_CAPABILITIES["seedance"]["models"]:
            model = SEEDANCE_DEFAULT_MODEL
        return {
            "provider": "seedance", "model": model,
            "resolution": str(getattr(config, "ARK_RESOLUTION", "1080p") or "1080p"),
            "unit_price_rmb_per_sec": 0.0,
        }
    return {"provider": "mock", "model": "mock", "resolution": "1080p", "unit_price_rmb_per_sec": 0.0}


def public_video_capabilities() -> dict[str, Any]:
    result = {key: {"models": value["models"]} for key, value in VIDEO_CAPABILITIES.items()}
    result["wan"]["available"] = dashscope_available()
    result["wan"]["unavailable_reason"] = (
        "万相视频仅支持官方 DashScope/百炼地址和有效 API Key"
        if not result["wan"]["available"]
        else ""
    )
    result["seedance"]["available"] = True
    result["seedance"]["aliases"] = ["seedence"]
    return result


def normalize_video_policy(policy: dict | None, override: dict | None = None) -> dict[str, Any]:
    merged = {**default_video_policy(), **(policy or {})}
    for key, value in (override or {}).items():
        if value not in (None, ""):
            merged[key] = value
    provider = _normalize_provider(str(merged.get("provider") or "mock"))
    model = str(merged.get("model") or "")
    if provider not in VIDEO_CAPABILITIES or model not in VIDEO_CAPABILITIES[provider]["models"]:
        raise HTTPException(422, "Unsupported video provider or model")
    if provider == "wan" and not dashscope_available():
        raise HTTPException(409, "万相视频需要官方 DashScope/百炼地址和 API Key")
    capability = VIDEO_CAPABILITIES[provider]["models"][model]
    resolution = str(merged.get("resolution") or capability["resolutions"][-1]).lower()
    if resolution not in capability["resolutions"]:
        raise HTTPException(422, f"{model} does not support {resolution}")
    price = float(merged.get("unit_price_rmb_per_sec") or 0.0)
    return {
        "provider": provider, "model": model, "resolution": resolution,
        "unit_price_rmb_per_sec": price, "capability": dict(capability),
    }


def requires_reference_image(policy: dict[str, Any]) -> bool:
    """Whether the selected model cannot run without an image reference.

    Seedance exposes both text-to-video and image-to-video modes.  A failed
    privacy check must therefore leave a deliberate text-only path available;
    image-only providers such as HappyHorse still require a reference.
    """
    capability = policy.get("capability") or {}
    return bool(capability.get("image_to_video") and not capability.get("text_to_video", False))


def validate_video_duration(policy: dict[str, Any], duration_sec: int | float) -> int:
    """Validate a shot duration against the selected provider capability."""
    try:
        duration = parse_integral_duration(duration_sec)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    capability = policy.get("capability") or {}
    minimum = int(capability.get("duration_min", 3))
    maximum = int(capability.get("duration_max", 15))
    if duration < minimum or duration > maximum:
        model = policy.get("model") or "selected model"
        raise HTTPException(
            422,
            f"{model} supports duration {minimum}-{maximum} seconds; received {duration}",
        )
    supported = duration_options_from_capability(capability)
    if supported and duration not in supported:
        model = policy.get("model") or "selected model"
        choices = "、".join(str(item) for item in supported)
        raise HTTPException(
            422,
            f"{model} 仅支持 {choices} 秒时长；收到 {duration} 秒",
        )
    return duration


def video_agent_for_policy(policy: dict[str, Any]):
    from src.agents.video_gen_agent import VideoGenerationAgent
    from src.api_clients.happyhorse import HappyHorseVideoClient
    from src.api_clients.mock import MockVideoClient
    from src.api_clients.seedance import SeedanceVideoClient
    import src.config as config

    if policy["provider"] == "mock":
        return VideoGenerationAgent(api_client=MockVideoClient())
    if policy["provider"] == "seedance":
        return VideoGenerationAgent(api_client=SeedanceVideoClient(
            api_key=getattr(config, "ARK_API_KEY", ""),
            base_url=getattr(config, "ARK_BASE_URL", ""),
            model=policy["model"],
            resolution=policy["resolution"],
        ))
    return VideoGenerationAgent(api_client=HappyHorseVideoClient(
        api_key=config.HAPPYHORSE_API_KEY,
        base_url=config.HAPPYHORSE_BASE_URL,
        model=policy["model"],
        resolution=policy["resolution"],
    ))
