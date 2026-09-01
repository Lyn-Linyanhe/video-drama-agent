"""Project audio timeline rules shared by jobs and HTTP endpoints.

This module deliberately knows nothing about FastAPI routing or application
singletons.  Callers persist the mutated project state after a successful
operation.
"""

from pathlib import Path
from typing import Any
import os
import uuid

from fastapi import HTTPException

from src.config import OUTPUT_DIR


def project_audio_assets(state: Any):
    from src.schemas.audio import AudioAssets, AudioVersion

    if state.audio_assets is None:
        state.audio_assets = AudioAssets(scenes=[])
    for scene in state.audio_assets.scenes:
        for line in scene.dialogue_audios:
            line.line_id = line.line_id or f"{scene.scene_id}:{line.dialogue_index}"
            if line.audio_path and not line.versions:
                version = AudioVersion(
                    audio_path=line.audio_path,
                    provider=line.provider or "edge",
                    voice=line.voice,
                    duration_sec=line.duration_sec,
                    text=line.text,
                    emotion=line.emotion,
                    speed=line.speed,
                    volume=line.volume,
                    pitch=line.pitch,
                )
                line.versions.append(version)
                line.active_version_id = version.version_id
    return state.audio_assets


def audio_summary(state: Any) -> dict[str, Any]:
    """Return compact project-level readiness counters for the UI."""

    script = getattr(state, "script", None)
    audio = project_audio_assets(state)
    speakers = {
        dialogue.character
        for scene in (script.scenes if script else [])
        for dialogue in scene.dialogue
        if dialogue.character
    }
    profiles = audio.voice_profiles or {}
    native_scene_ids = {
        scene.scene_id for scene in audio.scenes if getattr(scene, "native_audio_enabled", False)
    }
    tts_speakers = {
        dialogue.character
        for scene in (script.scenes if script else [])
        if scene.scene_id not in native_scene_ids
        for dialogue in scene.dialogue
        if dialogue.character
    }
    confirmed = 0
    for character in speakers:
        profile = profiles.get(character)
        provider = str(getattr(profile, "provider", "") or "").lower()
        ready = character not in tts_speakers or bool(
            profile
            and getattr(profile, "confirmed", False)
            and provider in {"qwen3", "dashscope-qwen3", "minimax", "dashscope-minimax", "edge"}
            and (provider not in {"minimax", "dashscope-minimax"} or str(getattr(profile, "voice", "") or "").strip())
        )
        confirmed += int(ready)

    by_line = {
        line.line_id or f"{scene.scene_id}:{line.dialogue_index}": line
        for scene in audio.scenes
        for line in scene.dialogue_audios
    }
    expected_ids = [
        f"{scene.scene_id}:{index}"
        for scene in (script.scenes if script else [])
        for index, _dialogue in enumerate(scene.dialogue, start=1)
    ]
    failed = 0
    pending = 0
    conflicts = 0
    failed_line_ids: list[str] = []
    pending_line_ids: list[str] = []
    timing_conflict_line_ids: list[str] = []
    for line_id in expected_ids:
        line = by_line.get(line_id)
        scene_id = int(line_id.split(":", 1)[0])
        if scene_id in native_scene_ids:
            continue
        if line is None or not line.success:
            if line is not None and line.error_message:
                failed += 1
                failed_line_ids.append(line_id)
            else:
                pending += 1
                pending_line_ids.append(line_id)
        if line is not None and (line.timing_conflict or line.needs_regeneration):
            if line.timing_conflict:
                conflicts += 1
                timing_conflict_line_ids.append(line_id)
            if line.needs_regeneration and line.success:
                pending += 1
                pending_line_ids.append(line_id)

    voice_count = len(speakers)
    unresolved_line_ids = list(dict.fromkeys(
        failed_line_ids + pending_line_ids + timing_conflict_line_ids
    ))
    return {
        "confirmed_voice_count": confirmed,
        "voice_count": voice_count,
        "dialogue_count": len(expected_ids),
        "pending_line_count": pending,
        "failed_line_count": failed,
        "timing_conflict_count": conflicts,
        "pending_line_ids": pending_line_ids,
        "failed_line_ids": failed_line_ids,
        "timing_conflict_line_ids": timing_conflict_line_ids,
        "unresolved_line_count": len(unresolved_line_ids),
        "can_assemble": bool(script and confirmed == voice_count and not pending and not failed and not conflicts),
    }


def final_assembly_audio_preflight(state: Any, override: str = "") -> dict[str, Any]:
    """Describe whether final assembly has usable dialogue audio.

    Missing dialogue audio is intentionally a confirmation gate rather than an
    unconditional failure: the caller may either generate/fix the audio or
    explicitly choose a silent/no-dialogue assembly.
    """

    script = getattr(state, "script", None)
    dialogue_count = sum(
        len(getattr(scene, "dialogue", []) or [])
        for scene in (script.scenes if script else [])
    )
    summary = audio_summary(state)
    no_dialogue = dialogue_count == 0
    explicitly_silent = override == "no_dialogue_confirmed"
    ready = no_dialogue or explicitly_silent or bool(summary["can_assemble"])
    requires_confirmation = bool(dialogue_count and not ready)
    return {
        "ready": ready,
        "requires_confirmation": requires_confirmation,
        "no_dialogue": explicitly_silent,
        "dialogue_count": dialogue_count,
        "unresolved_line_count": int(summary.get("unresolved_line_count") or 0),
        "pending_line_ids": list(summary.get("pending_line_ids") or []),
        "failed_line_ids": list(summary.get("failed_line_ids") or []),
        "timing_conflict_line_ids": list(summary.get("timing_conflict_line_ids") or []),
        "override": override if explicitly_silent else "",
        "message": (
            "\u5f53\u524d\u5bf9\u767d\u97f3\u9891\u4e0d\u5b8c\u6574\uff0c\u8bf7\u5148\u751f\u6210\u6216\u4fee\u590d\u5bf9\u767d\u97f3\u9891\uff1b\u4e5f\u53ef\u4ee5\u786e\u8ba4\u7ee7\u7eed\u751f\u6210\u65e0\u5bf9\u767d\u7248\u672c\u3002"
            if requires_confirmation else ""
        ),
    }


def _default_voice_profile(character: str):
    """Create an unconfirmed project voice choice without generating audio."""
    from src.schemas.audio import VoiceProfile

    provider = os.getenv("DASHSCOPE_TTS_PROVIDER", "qwen3").lower()
    provider = "minimax" if provider == "dashscope-minimax" else provider
    female_terms = ("女", "妻", "妈", "姐", "姨", "公主")
    is_female = any(term in character for term in female_terms)
    if provider == "minimax":
        key = "MINIMAX_DEFAULT_FEMALE_VOICE" if is_female else "MINIMAX_DEFAULT_MALE_VOICE"
        voice = os.getenv(key, "")
    elif provider == "qwen3":
        key = "QWEN_DEFAULT_FEMALE_VOICE" if is_female else "QWEN_DEFAULT_MALE_VOICE"
        voice = os.getenv(key, "Cherry" if is_female else "Ethan")
    else:
        provider, voice = "edge", ""
    return VoiceProfile(character=character, provider=provider, voice=voice, confirmed=False)


def speaking_characters(state: Any) -> list[str]:
    if not state.script:
        return []
    return list(dict.fromkeys(
        line.character.strip()
        for scene in state.script.scenes
        for line in scene.dialogue
        if line.character and line.character.strip()
    ))


def ensure_project_voice_profiles(state: Any):
    audio = project_audio_assets(state)
    for character in speaking_characters(state):
        if character not in audio.voice_profiles:
            audio.voice_profiles[character] = _default_voice_profile(character)
    return audio


def validate_project_voice_profiles(state: Any) -> dict:
    audio = ensure_project_voice_profiles(state)
    speakers = []
    for character in speaking_characters(state):
        profile = audio.voice_profiles[character]
        provider = "minimax" if profile.provider == "dashscope-minimax" else profile.provider
        reason = ""
        if not profile.confirmed:
            reason = "请确认角色音色"
        elif provider == "minimax" and not profile.voice.strip():
            reason = "MiniMax 必须填写音色 ID"
        elif provider not in {"qwen3", "minimax", "edge"}:
            reason = "不支持的配音服务商"
        speakers.append({
            "character": character,
            "provider": provider,
            "voice": profile.voice,
            "confirmed": profile.confirmed,
            "ready": not reason,
            "reason": reason,
        })
    return {"ready": all(item["ready"] for item in speakers), "speakers": speakers}


def audio_scene(state: Any, scene_id: int):
    from src.schemas.audio import SceneAudio

    audio = project_audio_assets(state)
    item = next((entry for entry in audio.scenes if entry.scene_id == scene_id), None)
    if item is None:
        item = SceneAudio(scene_id=scene_id)
        audio.scenes.append(item)
    return item


def audio_line_or_404(state: Any, scene_id: int, dialogue_index: int):
    scene = audio_scene(state, scene_id)
    item = next((line for line in scene.dialogue_audios if line.dialogue_index == dialogue_index), None)
    if item is None:
        raise HTTPException(404, "Audio dialogue line not found; generate project audio first")
    return item


def layout_project_audio(state: Any) -> None:
    if not state.script or not state.audio_assets:
        return
    for script_scene in state.script.scenes:
        scene_audio = audio_scene(state, script_scene.scene_id)
        cursor = 0
        limit = int(script_scene.duration_sec * 1000)
        for line in scene_audio.dialogue_audios:
            line.timing_conflict = False
            if not line.success:
                continue
            line.start_ms = cursor + line.lead_silence_ms
            line.end_ms = line.start_ms + int(line.duration_sec * 1000)
            cursor = line.end_ms + line.tail_silence_ms
            if cursor > limit:
                line.timing_conflict = True


def auto_select_project_audio(state: Any) -> None:
    if not state.script:
        return
    from src.schemas.audio import SoundEffectTrack

    audio = project_audio_assets(state)
    bgm = [item for item in audio.library_assets if item.kind == "bgm"]
    sfx = [item for item in audio.library_assets if item.kind == "sfx"]
    for scene in state.script.scenes:
        target = audio_scene(state, scene.scene_id)
        terms = f"{scene.emotion_tone} {scene.action_description} {scene.location}".lower()

        def score(item) -> int:
            return sum(2 for tag in item.tags if tag.lower() in terms)

        if not target.bgm_manual and bgm:
            best = max(bgm, key=score)
            target.bgm_asset_id = best.asset_id if score(best) > 0 else ""
        if not target.sfx_manual and not target.sfx_tracks and sfx:
            selected = sorted(sfx, key=score, reverse=True)
            target.sfx_tracks = [
                SoundEffectTrack(asset_id=item.asset_id, start_ms=0, manual=False)
                for item in selected[:2]
                if score(item) > 0
            ]


def build_bgm_segments(script: Any, audio: Any, timeline: Any) -> list[dict[str, Any]]:
    """Build continuous BGM segments for preview and final assembly.

    Adjacent scenes using the same file are merged into one stream. When the
    file changes at a hard-cut boundary, the two streams overlap briefly so
    the outgoing and incoming music cross-fade instead of stopping abruptly.
    ``timeline`` is the shared RenderTimeline, so actual provider clip
    durations are used whenever they are available.
    """
    if not script or not audio:
        return []
    library = {item.asset_id: item for item in (audio.library_assets or [])}
    scene_map = {item.scene_id: item for item in (audio.scenes or [])}
    segments: list[dict[str, Any]] = []
    for scene in script.scenes:
        scene_audio = scene_map.get(scene.scene_id)
        asset = library.get(getattr(scene_audio, "bgm_asset_id", "")) if scene_audio else None
        path = getattr(asset, "audio_path", "") if asset else ""
        if not path or not Path(path).is_file():
            continue
        start_ms = int(getattr(timeline, "scene_starts_ms", {}).get(scene.scene_id, 0))
        duration_ms = int(getattr(timeline, "scene_durations_ms", {}).get(
            scene.scene_id, max(1, round(float(scene.duration_sec) * 1000))
        ))
        if duration_ms <= 0:
            continue
        volume = float(getattr(scene_audio, "bgm_volume", 0.18) or 0.18)
        fade_ms = int(getattr(scene_audio, "bgm_fade_ms", 350) or 350)
        end_ms = start_ms + duration_ms
        if segments and segments[-1]["path"] == path and start_ms <= segments[-1]["start_ms"] + segments[-1]["duration_ms"]:
            previous = segments[-1]
            previous["duration_ms"] = max(
                previous["duration_ms"], end_ms - previous["start_ms"]
            )
            previous["fade_ms"] = max(previous["fade_ms"], fade_ms)
            continue
        segments.append({
            "path": path,
            "start_ms": start_ms,
            "duration_ms": duration_ms,
            "volume": volume,
            "fade_ms": fade_ms,
            "fade_in_ms": 0,
            "fade_out_ms": 0,
        })

    for previous, current in zip(segments, segments[1:]):
        previous_end = previous["start_ms"] + previous["duration_ms"]
        if previous_end != current["start_ms"] or previous["path"] == current["path"]:
            continue
        requested = max(400, min(700, max(500, previous["fade_ms"], current["fade_ms"])))
        available = min(previous["duration_ms"], current["duration_ms"]) // 2
        overlap = max(1, min(requested, available))
        previous["fade_out_ms"] = overlap
        current["fade_in_ms"] = overlap
    if segments:
        # Fade the project edges when a track does not already participate in
        # a cross-fade at that boundary.
        first = segments[0]
        last = segments[-1]
        if not first["fade_in_ms"]:
            first["fade_in_ms"] = min(max(0, first["fade_ms"]), first["duration_ms"] // 2)
        if not last["fade_out_ms"]:
            last["fade_out_ms"] = min(max(0, last["fade_ms"]), last["duration_ms"] // 2)
    return segments


def render_project_audio_preview(state: Any) -> str:
    from src.utils.video import run_ffmpeg
    from src.services.render_timeline import active_video_durations, build_render_timeline

    audio = project_audio_assets(state)
    layout_project_audio(state)
    timeline = build_render_timeline(state.script.scenes, active_video_durations(state))
    output_dir = OUTPUT_DIR / state.project_id / "assets" / "audio"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"preview_mix_{uuid.uuid4().hex[:10]}.m4a"
    command, filters, labels = ["-y"], [], []
    index = 0
    dialogue_labels = []
    library = {item.asset_id: item for item in audio.library_assets}
    scene_map = {item.scene_id: item for item in audio.scenes}
    for script_scene in state.script.scenes:
        scene_start = timeline.scene_starts_ms.get(script_scene.scene_id, 0)
        scene_audio = scene_map.get(script_scene.scene_id)
        scene_speech_labels = []
        for line in (scene_audio.dialogue_audios if scene_audio else []):
            if not line.success or not Path(line.audio_path).is_file():
                continue
            command.extend(["-i", line.audio_path])
            index += 1
            label = f"voice{index}"
            filters.append(f"[{index - 1}:a]adelay={scene_start + line.start_ms}:all=1,volume={line.volume}[{label}]")
            scene_speech_labels.append(f"[{label}]")
        dialogue_label = ""
        if scene_speech_labels:
            dialogue_label = f"[dialogue{script_scene.scene_id}]"
            filters.append(
                f"{''.join(scene_speech_labels)}amix=inputs={len(scene_speech_labels)}:duration=longest:"
                f"dropout_transition=0{dialogue_label}"
            )
            labels.append(dialogue_label)
            dialogue_labels.append(dialogue_label)
        if scene_audio:
            for effect in scene_audio.sfx_tracks:
                asset = library.get(effect.asset_id)
                if asset and Path(asset.audio_path).is_file():
                    command.extend(["-i", asset.audio_path])
                    index += 1
                    label = f"sfx{index}"
                    filters.append(f"[{index - 1}:a]adelay={scene_start + effect.start_ms}:all=1,volume={effect.volume}[{label}]")
                    labels.append(f"[{label}]")
    timeline_bgm = build_bgm_segments(state.script, audio, timeline)
    if not timeline_bgm and audio.bgm_path and Path(audio.bgm_path).is_file():
        timeline_bgm = [{
            "path": audio.bgm_path,
            "start_ms": 0,
            "duration_ms": timeline.total_duration_ms,
            "volume": 0.15,
            "fade_ms": 350,
            "fade_in_ms": 0,
            "fade_out_ms": 0,
        }]
    if dialogue_labels:
        filters.append(
            f"{''.join(dialogue_labels)}amix=inputs={len(dialogue_labels)}:duration=longest:"
            "dropout_transition=0[dialogue_all]"
        )
    for segment in timeline_bgm:
        command.extend(["-stream_loop", "-1", "-i", segment["path"]])
        index += 1
        raw_label = f"bgmraw{index}"
        delay_ms = max(0, segment["start_ms"] - segment["fade_in_ms"])
        duration_ms = segment["duration_ms"] + segment["fade_in_ms"]
        bgm_filter = f"[{index - 1}:a]atrim=duration={duration_ms / 1000:.3f}"
        if segment["fade_in_ms"]:
            bgm_filter += f",afade=t=in:st=0:d={segment['fade_in_ms'] / 1000:.3f}"
        if segment["fade_out_ms"]:
            bgm_filter += f",afade=t=out:st={(segment['duration_ms'] - segment['fade_out_ms']) / 1000:.3f}:d={segment['fade_out_ms'] / 1000:.3f}"
        filters.append(
            f"{bgm_filter},adelay={delay_ms}:all=1,volume={segment['volume']}[{raw_label}]"
        )
        label = f"bgm{index}"
        if dialogue_labels:
            filters.append(
                f"[{raw_label}][dialogue_all]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=350[{label}]"
            )
        else:
            filters.append(f"[{raw_label}]anull[{label}]")
        labels.append(f"[{label}]")
    if not labels:
        raise ValueError("No generated dialogue or selected audio assets are available for preview")
    timeline_duration = max(0.1, timeline.total_duration_ms / 1000)
    filters.append(
        f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:dropout_transition=0:normalize=0,"
        f"loudnorm=I=-16:TP=-1.5:LRA=11,apad,atrim=duration={timeline_duration:.3f}[mix]"
    )
    command.extend(["-filter_complex", ";".join(filters), "-map", "[mix]", "-c:a", "aac", str(output)])
    result = run_ffmpeg(command, timeout=180)
    if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError(f"Audio preview mix failed: {result.stderr[-500:]}")
    return str(output)
