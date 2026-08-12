"""Capability-driven image provider selection for project-scoped generation."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


CAPABILITIES: dict[str, dict[str, Any]] = {
    "mock": {"models": {"mock": {"single": True, "group": False, "reference_edit": False, "resolutions": ["1K"]}}},
    "kling": {"models": {
        "kling-v1": {"single": True, "group": False, "reference_edit": False, "resolutions": ["1K"]},
        "kling-v1-5": {"single": True, "group": False, "reference_edit": False, "resolutions": ["1K"]},
    }},
    "wan": {"models": {
        # Wan's official multimodal/group/edit paths accept only the 2K
        # preset (2048*2048). Custom landscape sizes are available only for
        # text-only single-image generation.
        "wan2.7-image-pro": {"single": True, "group": True, "reference_edit": True, "resolutions": ["1K", "2K", "4K"], "group_resolutions": ["2K"]},
        "wan2.7-image": {"single": True, "group": True, "reference_edit": True, "resolutions": ["1K", "2K"], "group_resolutions": ["2K"]},
    }},
}


def dashscope_available() -> bool:
    import src.config as config
    return bool(config.HAPPYHORSE_API_KEY) and "dashscope.aliyuncs.com" in str(config.HAPPYHORSE_BASE_URL or "").lower()


def default_policy() -> dict[str, str]:
    import src.config as config
    provider = str(config.IMAGE_GEN_API or "mock").lower()
    if provider == "kling":
        return {"provider": "kling", "model": str(config.KLING_IMAGE_MODEL or "kling-v1"), "resolution": "1K"}
    if provider == "wan":
        model = str(config.KLING_IMAGE_MODEL or "wan2.7-image-pro")
        if model not in CAPABILITIES["wan"]["models"]:
            model = "wan2.7-image-pro"
        return {"provider": "wan", "model": model, "resolution": "1K"}
    return {"provider": "mock", "model": "mock", "resolution": "1K"}


def public_capabilities() -> dict[str, Any]:
    result = {provider: {"models": models["models"]} for provider, models in CAPABILITIES.items()}
    result["wan"]["available"] = dashscope_available()
    result["wan"]["unavailable_reason"] = "万相需要使用官方 DashScope/百炼视频连接与 API Key" if not result["wan"]["available"] else ""
    return result


def normalize_policy(policy: dict | None, override: dict | None = None, mode: str = "single") -> dict[str, str]:
    combined = {**default_policy(), **(policy or {})}
    for key, value in (override or {}).items():
        if value:
            combined[key] = str(value)
    provider = str(combined.get("provider") or "mock").lower()
    model = str(combined.get("model") or "")
    if provider not in CAPABILITIES or model not in CAPABILITIES[provider]["models"]:
        raise HTTPException(422, "Unsupported image provider or model")
    capability = CAPABILITIES[provider]["models"][model]
    if provider == "wan" and not dashscope_available():
        raise HTTPException(409, "万相需要官方 DashScope/百炼视频连接与 API Key")
    if not capability.get(mode):
        raise HTTPException(409, f"当前图片模型不支持{mode}模式")
    allowed = capability.get("group_resolutions") if mode in {"group", "reference_edit"} else capability["resolutions"]
    resolution = str(combined.get("resolution") or allowed[0]).upper()
    if resolution not in allowed:
        raise HTTPException(422, f"{model} 的{mode}模式不支持 {resolution}")
    return {"provider": provider, "model": model, "resolution": resolution}


def client_for_policy(policy: dict[str, str]):
    import src.config as config
    if policy["provider"] == "wan":
        from src.api_clients.image_wan import WanImageClient
        return WanImageClient(config.HAPPYHORSE_API_KEY, policy["model"], config.HAPPYHORSE_BASE_URL)
    if policy["provider"] == "kling":
        from src.api_clients.image_kling import KlingImageClient
        key = config.IMAGE_GEN_KEY or config.KLING_API_KEY
        if not key:
            raise HTTPException(409, "请先配置可灵图片 API Key")
        return KlingImageClient(api_key=key, model=policy["model"])
    from src.api_clients.image_mock import MockImageClient
    return MockImageClient()


def dimensions_for_ratio(width: int, height: int, resolution: str) -> tuple[int, int]:
    longest = {"1K": 1024, "2K": 2048, "4K": 4096}[resolution]
    scale = longest / max(width, height)
    return max(64, round(width * scale)), max(64, round(height * scale))


def dimensions_for_provider(
    width: int,
    height: int,
    resolution: str,
    provider: str = "",
) -> tuple[int, int]:
    """Return dimensions accepted by the selected image provider.

    DashScope Wan accepts custom ``width*height`` values for text-only image
    generation, but its documented landscape/portrait presets use a minimum
    short edge larger than the generic 1024-based scaling above.  Keeping this
    mapping here makes the stored asset dimensions match the actual provider
    request instead of relying on the prompt to imply an aspect ratio.
    """
    if str(provider or "").lower() != "wan":
        return dimensions_for_ratio(width, height, resolution)

    ratio = float(width or 1) / float(height or 1)
    if 0.95 <= ratio <= 1.05:
        orientation = "square"
    elif ratio > 1:
        orientation = "landscape"
    else:
        orientation = "portrait"
    wan_sizes = {
        "1K": {
            "square": (1024, 1024),
            "landscape": (1696, 960),
            "portrait": (960, 1696),
        },
        "2K": {
            "square": (2048, 2048),
            "landscape": (2688, 1536),
            "portrait": (1536, 2688),
        },
        "4K": {
            "square": (4096, 4096),
            "landscape": (4096, 2304),
            "portrait": (2304, 4096),
        },
    }
    try:
        return wan_sizes[str(resolution).upper()][orientation]
    except KeyError:
        return dimensions_for_ratio(width, height, resolution)
