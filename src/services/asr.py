"""Optional local ASR subtitle alignment for provider-native dialogue."""

from __future__ import annotations

import difflib
import math
import os
from pathlib import Path
from typing import Any


def _normalized(value: str) -> str:
    return "".join(str(value or "").casefold().split())


def reconcile_asr_segments(
    asr_segments: list[dict[str, Any]],
    dialogue_plan: list[dict[str, Any]],
    *,
    min_confidence: float = 0.55,
) -> dict[str, Any]:
    """Match recognized segments to script lines without mutating script text."""
    segments: list[dict[str, Any]] = []
    changed_lines: list[dict[str, Any]] = []
    low_confidence = False
    for index, raw in enumerate(asr_segments or []):
        text = str(raw.get("text") or "").strip()
        if not text:
            continue
        start = float(raw.get("start") or 0.0)
        end = max(start, float(raw.get("end") or start))
        confidence = float(raw.get("confidence") if raw.get("confidence") is not None else 0.0)
        line = dialogue_plan[index] if index < len(dialogue_plan) else {}
        script_text = str(line.get("text") or "")
        similarity = difflib.SequenceMatcher(None, _normalized(text), _normalized(script_text)).ratio() if script_text else 0.0
        segment = {
            "line_id": str(line.get("line_id") or ""),
            "text": text,
            "script_text": script_text,
            "start": round(start, 3),
            "end": round(end, 3),
            "start_ms": max(0, round(start * 1000)),
            "end_ms": max(0, round(end * 1000)),
            "confidence": round(confidence, 4),
            "similarity": round(similarity, 4),
        }
        segments.append(segment)
        low_confidence = low_confidence or confidence < min_confidence
        if script_text and similarity < 0.9:
            changed_lines.append({
                "line_id": segment["line_id"],
                "script_text": script_text,
                "recognized_text": text,
                "similarity": segment["similarity"],
            })
    if not segments or low_confidence:
        status = "fallback"
        source = "script"
    elif changed_lines:
        status = "changed"
        source = "asr"
    else:
        status = "ready"
        source = "asr"
    return {
        "status": status,
        "source": source,
        "segments": segments,
        "confidence": round(sum(item["confidence"] for item in segments) / len(segments), 4) if segments else 0.0,
        "changed_lines": changed_lines,
        "error": "" if segments else "ASR returned no speech segments",
    }


def align_native_subtitles(
    video_path: str,
    dialogue_plan: list[dict[str, Any]],
    *,
    enabled: bool = True,
    model_name: str = "small",
) -> dict[str, Any]:
    """Transcribe a local provider video when a preinstalled Whisper model exists.

    ``faster-whisper`` is deliberately optional. A model path must be supplied
    through ``FASTER_WHISPER_MODEL_PATH`` and already exist, so this function
    never downloads weights or turns subtitle correction into a paid/network job.
    """
    fallback = {
        "status": "fallback", "source": "script", "segments": [],
        "confidence": 0.0, "changed_lines": [], "error": "",
        "provider": "faster-whisper", "model": model_name,
    }
    if not enabled:
        fallback["error"] = "ASR disabled"
        return fallback
    if not video_path or not Path(video_path).is_file():
        fallback["error"] = "native video file is unavailable"
        return fallback
    model_path = str(os.getenv("FASTER_WHISPER_MODEL_PATH") or "").strip()
    if not model_path or not Path(model_path).exists():
        fallback["error"] = "faster-whisper model is not installed locally"
        return fallback
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(model_path, device="cpu", compute_type="int8")
        raw_segments, _info = model.transcribe(video_path, vad_filter=True)
        recognized = [
            {"text": segment.text, "start": segment.start, "end": segment.end,
             "confidence": _segment_confidence(segment)}
            for segment in raw_segments
        ]
        result = reconcile_asr_segments(recognized, dialogue_plan)
        result.update({"provider": "faster-whisper", "model": model_name})
        return result
    except Exception as exc:
        fallback["error"] = str(exc)[:300]
        return fallback


def _segment_confidence(segment: Any) -> float:
    """Convert faster-whisper's log probability into a 0..1 confidence."""
    value = getattr(segment, "avg_logprob", None)
    if value is None:
        return 0.0
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, math.exp(value) if value <= 0 else value))
