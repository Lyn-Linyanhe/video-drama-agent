"""FastAPI routes extracted from the original server entrypoint."""

from fastapi import APIRouter
from ..context import *
from .job_helpers import _job_public
from src.services.audio_timeline import (
    audio_line_or_404 as _audio_line_or_404,
    audio_scene as _audio_scene,
    auto_select_project_audio as _auto_select_project_audio,
    layout_project_audio as _layout_project_audio,
    project_audio_assets as _project_audio_assets,
    ensure_project_voice_profiles as _ensure_project_voice_profiles,
    validate_project_voice_profiles as _validate_project_voice_profiles,
)

router = APIRouter()

@router.post("/api/projects/{project_id}/production/audio")
async def generate_project_production_audio(project_id: str):
    """Generate audio from the server-persisted active video versions."""
    state = _load_project_state(project_id)
    preflight = _validate_project_voice_profiles(state)
    if not preflight["ready"]:
        detail = "；".join(f"{item['character']}：{item['reason']}" for item in preflight["speakers"] if not item["ready"])
        raise HTTPException(409, f"请先确认项目音色：{detail}")
    try:
        result = await orchestrator.run_stage(5, state)
    except Exception as exc:
        raise HTTPException(502, f"Audio generation failed: {exc}") from exc
    _save_project_state(result)
    return _production_payload(result)


@router.get("/api/projects/{project_id}/audio-timeline")
async def get_project_audio_timeline(project_id: str):
    state = _load_project_state(project_id)
    _ensure_project_voice_profiles(state)
    _layout_project_audio(state)
    _save_project_state(state)
    return {"project_id": project_id, "audio": _project_audio_assets(state).model_dump()}


@router.get("/api/projects/{project_id}/audio-preflight")
async def get_project_audio_preflight(project_id: str):
    state = _load_project_state(project_id)
    result = _validate_project_voice_profiles(state)
    _save_project_state(state)
    return {"project_id": project_id, **result}


@router.put("/api/projects/{project_id}/audio-timeline/lines/{scene_id}/{dialogue_index}")
async def update_project_audio_line(
    project_id: str, scene_id: int, dialogue_index: int, req: AudioLineUpdateRequest,
):
    state = _load_project_state(project_id)
    line = _audio_line_or_404(state, scene_id, dialogue_index)
    updates = req.model_dump(exclude_none=True)
    synthesis_fields = {"text", "emotion", "speed", "pitch"}
    if any(key in synthesis_fields and getattr(line, key) != value for key, value in updates.items()):
        line.needs_regeneration = True
    for key, value in updates.items():
        setattr(line, key, value)
    line.success = bool(line.audio_path)
    _layout_project_audio(state)
    _save_project_state(state)
    return {"audio": _project_audio_assets(state).model_dump(), "line": line.model_dump()}


@router.post("/api/projects/{project_id}/audio-timeline/lines/{scene_id}/{dialogue_index}/activate")
async def activate_project_audio_version(
    project_id: str, scene_id: int, dialogue_index: int, req: AudioVersionActivateRequest,
):
    state = _load_project_state(project_id)
    line = _audio_line_or_404(state, scene_id, dialogue_index)
    version = next((item for item in line.versions if item.version_id == req.version_id), None)
    if version is None:
        raise HTTPException(404, "Audio version not found")
    line.active_version_id = version.version_id
    line.audio_path, line.provider, line.voice = version.audio_path, version.provider, version.voice
    line.duration_sec, line.success, line.error_message = version.duration_sec, True, ""
    if version.text:
        line.text = version.text
        line.emotion = version.emotion
        line.speed = version.speed
        line.volume = version.volume
        line.pitch = version.pitch
    line.needs_regeneration = False
    _layout_project_audio(state)
    _save_project_state(state)
    return {"audio": _project_audio_assets(state).model_dump(), "line": line.model_dump()}


@router.put("/api/projects/{project_id}/audio-timeline/voice-profiles/{character}")
async def update_project_voice_profile(
    project_id: str, character: str, req: VoiceProfileUpdateRequest,
):
    from src.schemas.audio import VoiceProfile

    state = _load_project_state(project_id)
    audio = _project_audio_assets(state)
    if req.provider == "minimax" and not req.voice.strip():
        raise HTTPException(400, "MiniMax voice ID is required")
    profile = VoiceProfile(
        character=character,
        provider=req.provider,
        voice=req.voice.strip(),
        confirmed=req.confirmed,
    )
    audio.voice_profiles[character] = profile
    for scene in audio.scenes:
        for line in scene.dialogue_audios:
            if line.character != character:
                continue
            line.provider = req.provider
            line.voice = profile.voice
            if line.audio_path:
                line.needs_regeneration = True
    _save_project_state(state)
    return {"audio": audio.model_dump(), "voice_profile": profile.model_dump()}


@router.get("/api/projects/{project_id}/audio-timeline/subtitles")
async def export_project_audio_subtitles(project_id: str, mode: str = "dialogue"):
    if mode not in {"dialogue", "speaker"}:
        raise HTTPException(400, "Subtitle mode must be dialogue or speaker")
    state = _load_project_state(project_id)
    if not state.script:
        raise HTTPException(409, "Please confirm and analyze the script first")
    audio = _project_audio_assets(state)
    _layout_project_audio(state)
    by_scene = {scene.scene_id: scene for scene in audio.scenes}
    output_dir = OUTPUT_DIR / project_id / "assets" / "audio" / "subtitles"
    output_dir.mkdir(parents=True, exist_ok=True)
    path = AssemblyAgent()._write_subtitle_file(state.script, by_scene, output_dir, subtitle_mode=mode)
    if not path:
        raise HTTPException(409, "No generated dialogue audio is available for subtitle export")
    _save_project_state(state)
    return {"subtitle_path": str(path), "mode": mode}


@router.post("/api/projects/{project_id}/audio-library/upload")
async def upload_project_audio_asset(
    project_id: str, file: UploadFile = File(...), kind: str = Form(...),
    label: str = Form(""), tags: str = Form(""),
):
    if kind not in {"bgm", "sfx"}:
        raise HTTPException(400, "Audio kind must be bgm or sfx")
    state = _load_project_state(project_id)
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}:
        raise HTTPException(400, "Unsupported audio file type")
    content = await file.read()
    if not content or len(content) > 80 * 1024 * 1024:
        raise HTTPException(400, "Audio file must be between 1 byte and 80 MB")
    from src.schemas.audio import AudioLibraryAsset
    resolved_label = label.strip() or Path(file.filename).stem
    resolved_tags = [item.strip() for item in tags.split(",") if item.strip()]
    if not resolved_tags:
        resolved_tags = [item for item in re.split(r"[\s_\-]+", resolved_label) if item]
    asset = AudioLibraryAsset(kind=kind, label=resolved_label, tags=resolved_tags,
                              sha256=hashlib.sha256(content).hexdigest())
    destination = OUTPUT_DIR / project_id / "assets" / "audio" / "library"
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / f"{asset.asset_id}{suffix}"
    path.write_bytes(content)
    asset.audio_path = str(path)
    asset.duration_sec = AudioAgent._audio_duration(str(path))
    _project_audio_assets(state).library_assets.append(asset)
    _auto_select_project_audio(state)
    _save_project_state(state)
    return {"asset": asset.model_dump(), "audio": _project_audio_assets(state).model_dump()}


@router.delete("/api/projects/{project_id}/audio-library/{asset_id}")
async def delete_project_audio_asset(project_id: str, asset_id: str):
    state = _load_project_state(project_id)
    audio = _project_audio_assets(state)
    asset = next((item for item in audio.library_assets if item.asset_id == asset_id), None)
    if asset is None:
        raise HTTPException(404, "Audio asset not found")
    audio.library_assets = [item for item in audio.library_assets if item.asset_id != asset_id]
    for scene in audio.scenes:
        if scene.bgm_asset_id == asset_id:
            scene.bgm_asset_id = ""
        scene.sfx_tracks = [item for item in scene.sfx_tracks if item.asset_id != asset_id]
    # Uploaded audio belongs to this project only; remove it after removing all references.
    library_root = (OUTPUT_DIR / project_id / "assets" / "audio" / "library").resolve()
    try:
        path = Path(asset.audio_path).resolve()
        if path.is_relative_to(library_root) and path.is_file():
            path.unlink()
    except (OSError, ValueError):
        pass
    _save_project_state(state)
    return {"audio": audio.model_dump()}


@router.post("/api/projects/{project_id}/audio-timeline/auto-select")
async def auto_select_project_audio(project_id: str):
    state = _load_project_state(project_id)
    _auto_select_project_audio(state)
    _save_project_state(state)
    return {"audio": _project_audio_assets(state).model_dump()}


@router.post("/api/projects/{project_id}/audio-timeline/preview", status_code=202)
async def preview_project_audio_timeline(project_id: str):
    # Validate only project existence here. Preview is useful before script or
    # visual generation is complete, so it must not enforce those prerequisites.
    _load_project_state(project_id, require_script=False, require_visual=False)
    active = next((item for item in job_store.list_jobs(project_id, states=ACTIVE_JOB_STATES, limit=100)
                   if item["job_type"] == "audio.generate" and item["target_id"] == "preview"), None)
    if active:
        return {"job": _job_public(active)}
    job = job_manager.create_job(
        project_id, "audio.generate", target_type="audio_preview", target_id="preview",
        provider="ffmpeg", payload={"operation": "preview"},
    )
    return {"job": _job_public(job)}


@router.put("/api/projects/{project_id}/audio-timeline/scenes/{scene_id}/bgm")
async def select_project_scene_bgm(project_id: str, scene_id: int, req: AudioSelectionRequest):
    state = _load_project_state(project_id)
    scene = _audio_scene(state, scene_id)
    if req.asset_id and not any(item.asset_id == req.asset_id and item.kind == "bgm" for item in _project_audio_assets(state).library_assets):
        raise HTTPException(404, "BGM asset not found")
    scene.bgm_asset_id = "" if req.muted else req.asset_id
    scene.bgm_manual = True
    if req.volume is not None:
        scene.bgm_volume = req.volume
    if req.fade_ms is not None:
        scene.bgm_fade_ms = req.fade_ms
    _save_project_state(state)
    return {"audio": _project_audio_assets(state).model_dump()}


@router.put("/api/projects/{project_id}/audio-timeline/scenes/{scene_id}/sfx")
async def select_project_scene_sfx(project_id: str, scene_id: int, req: AudioSfxSelectionRequest):
    from src.schemas.audio import SoundEffectTrack
    state = _load_project_state(project_id)
    audio = _project_audio_assets(state)
    requested = req.tracks or ([AudioSfxTrackRequest(asset_id=req.asset_id, start_ms=req.start_ms, volume=req.volume)] if req.asset_id else [])
    available = {item.asset_id for item in audio.library_assets if item.kind == "sfx"}
    invalid = [item.asset_id for item in requested if item.asset_id not in available]
    if invalid:
        raise HTTPException(404, "Sound effect asset not found")
    scene = _audio_scene(state, scene_id)
    scene.sfx_tracks = [
        SoundEffectTrack(asset_id=item.asset_id, start_ms=item.start_ms, volume=item.volume, manual=True)
        for item in requested
    ]
    scene.sfx_manual = True
    _save_project_state(state)
    return {"audio": audio.model_dump()}

__all__ = [name for name in globals() if not name.startswith("__")]
