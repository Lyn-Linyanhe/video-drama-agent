"""TTS providers used by the project audio timeline."""

from __future__ import annotations

import asyncio
import base64
import os
from pathlib import Path

import httpx

from src.utils.tts import generate_tts


class TTSProviderError(RuntimeError):
    pass


def describe_dashscope_tts_error(detail: str) -> str:
    """Turn provider failures into errors that can be acted on in the UI."""
    message = (detail or "").strip()
    if "product is not activated" in message.lower():
        return (
            "阿里云百炼尚未开通 MiniMax/speech-2.8-turbo。"
            "请在模型广场开通该语音服务后，再重新测试或生成配音。"
        )
    return message or "百炼 MiniMax 语音服务拒绝了请求"


def audio_settings() -> dict[str, str]:
    return {
        # Both speech adapters reuse the configured DashScope video account.
        "api_key": os.getenv("HAPPYHORSE_API_KEY", "") or os.getenv("MINIMAX_API_KEY", ""),
        "base_url": os.getenv("HAPPYHORSE_BASE_URL", "https://dashscope.aliyuncs.com").rstrip("/"),
        "provider": os.getenv("DASHSCOPE_TTS_PROVIDER", "qwen3").lower(),
        "qwen_model": os.getenv("QWEN_TTS_MODEL", "qwen3-tts-flash"),
        "qwen_timeout": os.getenv("QWEN_TTS_TIMEOUT", "45"),
        "minimax_model": os.getenv("MINIMAX_TTS_MODEL", "MiniMax/speech-2.8-turbo"),
        "minimax_timeout": os.getenv("MINIMAX_TTS_TIMEOUT", "45"),
        "fallback_enabled": os.getenv("EDGE_TTS_FALLBACK", "true").lower(),
    }


def _decode_audio(value: str) -> bytes:
    value = (value or "").strip()
    if not value:
        raise TTSProviderError("MiniMax returned no audio payload")
    if len(value) % 2 == 0 and all(char in "0123456789abcdefABCDEF" for char in value):
        try:
            return bytes.fromhex(value)
        except ValueError:
            pass
    try:
        return base64.b64decode(value, validate=True)
    except Exception:
        raise TTSProviderError("MiniMax returned an unsupported audio payload")


async def generate_minimax_tts(
    text: str, voice: str, output_path: str, *, emotion: str = "calm",
    speed: float = 1.0, volume: float = 1.0, pitch: int = 0,
) -> str:
    settings = audio_settings()
    api_key = settings["api_key"]
    if not api_key:
        raise TTSProviderError("MiniMax API key is not configured")
    if not voice:
        raise TTSProviderError("MiniMax voice is not configured for this character")
    body = {
        "model": settings["minimax_model"],
        "input": {
            "text": text,
            "voice_setting": {"voice_id": voice, "speed": speed, "vol": volume, "pitch": pitch, "emotion": emotion},
            "audio_setting": {"sample_rate": 32000, "bitrate": 128000, "format": "mp3", "channel": 1},
            "subtitle_enable": False,
        },
    }
    url = f"{settings['base_url']}/api/v1/services/aigc/multimodal-generation/generation"
    try:
        async with httpx.AsyncClient(timeout=max(5, int(settings["minimax_timeout"]))) as client:
            response = await client.post(url, json=body, headers={
                "Authorization": f"Bearer {api_key}", "Content-Type": "application/json; charset=utf-8",
            })
            payload = response.json()
            if response.status_code != 200:
                raise TTSProviderError(
                    f"DashScope TTS failed [{response.status_code}]: "
                    f"{describe_dashscope_tts_error(response.text[:300])}"
                )
            output = payload.get("output") or payload.get("data") or {}
            base_resp = output.get("base_resp") or {}
            if base_resp and int(base_resp.get("status_code", 0) or 0) != 0:
                raise TTSProviderError(
                    f"DashScope TTS rejected the request [{base_resp.get('status_code')}]: "
                    f"{describe_dashscope_tts_error(base_resp.get('status_msg', 'unknown error'))}"
                )
            audio_value = output.get("audio") or output.get("audio_data") or ""
            audio_url = output.get("audio_url") or output.get("url") or ""
            if audio_url:
                download = await client.get(audio_url)
                download.raise_for_status()
                audio = download.content
            else:
                audio = _decode_audio(audio_value)
    except (httpx.HTTPError, ValueError) as exc:
        raise TTSProviderError(f"DashScope MiniMax request failed: {exc}") from exc
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(audio)
    if not target.is_file() or target.stat().st_size == 0:
        raise TTSProviderError("MiniMax returned an empty audio file")
    return str(target)


def _qwen_audio_payload(output: dict) -> tuple[str, str]:
    """Read the documented and observed Qwen3-TTS response variants."""
    audio = output.get("audio") or output.get("audio_data") or ""
    audio_url = output.get("audio_url") or output.get("url") or ""
    if isinstance(audio, dict):
        audio_url = audio_url or audio.get("url") or audio.get("audio_url") or ""
        audio = audio.get("data") or audio.get("audio") or audio.get("audio_data") or ""
    return str(audio or ""), str(audio_url or "")


async def generate_qwen3_tts(
    text: str, voice: str, output_path: str, *, emotion: str = "calm",
    speed: float = 1.0, volume: float = 1.0, pitch: int = 0,
) -> str:
    """Generate one line through DashScope Qwen3-TTS without affecting MiniMax."""
    settings = audio_settings()
    api_key = settings["api_key"]
    if not api_key:
        raise TTSProviderError("DashScope API key is not configured")
    if not voice:
        raise TTSProviderError("Qwen3-TTS voice is not configured for this character")
    body = {
        "model": settings["qwen_model"],
        "input": {"text": text, "voice": voice},
        "parameters": {
            "response_format": "mp3",
            "sample_rate": 24000,
            "rate": speed,
            "volume": max(0, min(100, round(volume * 50))),
            "pitch": pitch,
        },
    }
    url = f"{settings['base_url']}/api/v1/services/aigc/multimodal-generation/generation"
    try:
        async with httpx.AsyncClient(timeout=max(5, int(settings["qwen_timeout"]))) as client:
            response = await client.post(url, json=body, headers={
                "Authorization": f"Bearer {api_key}", "Content-Type": "application/json; charset=utf-8",
            })
            payload = response.json()
            if response.status_code != 200:
                raise TTSProviderError(
                    f"DashScope Qwen3-TTS failed [{response.status_code}]: "
                    f"{describe_dashscope_tts_error(response.text[:300])}"
                )
            output = payload.get("output") or payload.get("data") or {}
            audio_value, audio_url = _qwen_audio_payload(output)
            if audio_url:
                download = await client.get(audio_url)
                download.raise_for_status()
                audio = download.content
            else:
                audio = _decode_audio(audio_value)
    except (httpx.HTTPError, ValueError) as exc:
        raise TTSProviderError(f"DashScope Qwen3-TTS request failed: {exc}") from exc
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(audio)
    if not target.is_file() or target.stat().st_size == 0:
        raise TTSProviderError("Qwen3-TTS returned an empty audio file")
    return str(target)


async def generate_timeline_tts(
    text: str, provider: str, voice: str, output_path: str, *, emotion: str = "calm",
    speed: float = 1.0, volume: float = 1.0, pitch: int = 0,
) -> tuple[str, str, str]:
    """Generate one line and return path, effective provider, and fallback reason."""
    requested = (provider or audio_settings()["provider"] or "qwen3").lower()
    if requested == "edge":
        try:
            return await generate_tts(text, voice, output_path), "edge", ""
        except Exception as exc:
            raise TTSProviderError(f"Edge-TTS generation failed: {exc}") from exc
    if not voice:
        raise TTSProviderError("DashScope voice is not configured for this character")
    try:
        if requested in {"minimax", "dashscope-minimax"}:
            path = await generate_minimax_tts(
                text, voice, output_path, emotion=emotion, speed=speed, volume=volume, pitch=pitch,
            )
            return path, "dashscope-minimax", ""
        path = await generate_qwen3_tts(
            text, voice, output_path, emotion=emotion, speed=speed, volume=volume, pitch=pitch,
        )
        return path, "dashscope-qwen3", ""
    except Exception as exc:
        if audio_settings()["fallback_enabled"] not in {"1", "true", "yes", "on"}:
            raise
        edge_voice = voice if voice.startswith("zh-") else "zh-CN-YunxiNeural"
        try:
            path = await generate_tts(text, edge_voice, output_path)
        except Exception as fallback_exc:
            raise TTSProviderError(
                f"{requested} TTS failed: {exc}; Edge-TTS fallback failed: {fallback_exc}"
            ) from fallback_exc
        return path, "edge", str(exc)[:300]
