"""Audio Agent — generates TTS voiceover, matches BGM, and adds sound effects."""

import asyncio
import hashlib
import os
import uuid
from datetime import datetime
from pathlib import Path

from src.schemas.script import ScriptBreakdown
from src.schemas.assets import GeneratedAssets
from src.schemas.audio import AudioAssets, AudioVersion, DialogueAudio, SceneAudio, VoiceProfile
from src.utils.tts import generate_tts, get_voice_for_character
from src.services.tts_provider import generate_timeline_tts
from src.utils.video import run_ffmpeg_shell
from src.config import OUTPUT_DIR

# Mood → BGM style mapping (used when no real music library is available)
MOOD_BGM_MAP = {
    "紧张": "tense_dark",
    "温馨": "warm_acoustic",
    "悲伤": "sad_piano",
    "兴奋": "upbeat_electronic",
    "悬疑": "mystery_ambient",
    "浪漫": "romantic_strings",
    "愤怒": "intense_drums",
    "平静": "calm_ambient",
    "恐怖": "horror_drone",
    "搞笑": "comedy_playful",
    "史诗": "epic_orchestral",
}


class AudioAgent:
    """Agent that handles all audio generation for the drama.

    Generates:
    - TTS voiceover for each dialogue line (multi-character)
    - Background music matched to scene emotion
    - Sound effects for key actions (door slam, footsteps, etc.)

    Uses Edge-TTS for speech (free, no API key), and generates
    placeholder BGM/SFX when no real assets are available.
    """

    def __init__(self):
        self.output_dir = OUTPUT_DIR / "audio"
        self.output_dir.mkdir(exist_ok=True)
        self._line_context: dict[tuple[int, int], VoiceProfile] = {}

    async def run(
        self, script: ScriptBreakdown, assets: GeneratedAssets, project_id: str = "",
        existing: AudioAssets | None = None, force_line_ids: set[str] | None = None,
        native_scene_audio: dict[int, dict] | None = None,
        audio_mode: str = "auto",
    ) -> AudioAssets:
        """Generate all audio for the drama.

        Args:
            script: The script breakdown with dialogue.
            assets: The generated video assets (for timing reference).

        Returns:
            AudioAssets with TTS, BGM, and SFX paths.
        """
        output_dir = self.output_dir
        if project_id:
            output_dir = OUTPUT_DIR / project_id / "assets" / "audio"
            output_dir.mkdir(parents=True, exist_ok=True)

        existing = existing or AudioAssets(scenes=[])
        force_line_ids = force_line_ids or set()
        native_scene_audio = native_scene_audio or {}
        audio_mode = str(audio_mode or "auto").strip().lower()
        if audio_mode == "auto":
            # Legacy callers did not pass a project mode but did provide
            # provider-native scene metadata. Preserve that behavior while
            # keeping explicit external_tts authoritative.
            audio_mode = (
                "native_dialogue"
                if any(bool(item.get("native_audio_enabled")) for item in native_scene_audio.values())
                else "external_tts"
            )
        if audio_mode not in {"native_dialogue", "external_tts"}:
            audio_mode = "external_tts"
        old_lines = {
            item.line_id or f"{scene.scene_id}:{item.dialogue_index}": item
            for scene in existing.scenes for item in scene.dialogue_audios
        }
        old_scenes = {scene.scene_id: scene for scene in existing.scenes}
        voice_profiles = dict(existing.voice_profiles)
        scene_audio_list = []
        all_tasks = []

        # DashScope applies per-account request quotas.  Sending every line in
        # one asyncio.gather call makes an otherwise valid batch look like a
        # provider failure (429), which then needlessly falls back to Edge.
        try:
            dashscope_concurrency = max(1, int(os.getenv("DASHSCOPE_TTS_CONCURRENCY", "1")))
        except ValueError:
            dashscope_concurrency = 1
        try:
            dashscope_interval = max(0.0, float(os.getenv("DASHSCOPE_TTS_MIN_INTERVAL_MS", "250")) / 1000)
        except ValueError:
            dashscope_interval = 0.25
        dashscope_semaphore = asyncio.Semaphore(dashscope_concurrency)
        dashscope_rate_lock = asyncio.Lock()
        last_dashscope_request = 0.0

        async def generate_line_tts(dialogue_audio: DialogueAudio, output_path: Path):
            nonlocal last_dashscope_request
            provider = (dialogue_audio.provider or "").lower()
            if provider not in {"qwen3", "dashscope-qwen3", "minimax", "dashscope-minimax"}:
                return await self._generate_dialogue_tts(
                    dialogue_audio.scene_id, dialogue_audio.dialogue_index,
                    dialogue_audio.character, dialogue_audio.text,
                    dialogue_audio.voice, output_path,
                )
            async with dashscope_semaphore:
                async with dashscope_rate_lock:
                    loop = asyncio.get_running_loop()
                    wait = last_dashscope_request + dashscope_interval - loop.time()
                    if wait > 0:
                        await asyncio.sleep(wait)
                    last_dashscope_request = loop.time()
                return await self._generate_dialogue_tts(
                    dialogue_audio.scene_id, dialogue_audio.dialogue_index,
                    dialogue_audio.character, dialogue_audio.text,
                    dialogue_audio.voice, output_path,
                )

        # Generate audio for each scene that has dialogue
        for scene in script.scenes:
            previous_scene = old_scenes.get(scene.scene_id)
            scene_audio = (
                SceneAudio.model_validate(previous_scene.model_dump())
                if previous_scene else SceneAudio(scene_id=scene.scene_id)
            )
            # Dialogue is reconciled from the current script; manual BGM/SFX
            # settings and their mix controls remain on the copied scene object.
            scene_audio.dialogue_audios = []
            scene_audio.character_voice_map = {}
            scene_tasks: list[tuple[DialogueAudio, object]] = []
            native_info = native_scene_audio.get(scene.scene_id) or {}
            scene_audio.native_audio_enabled = bool(native_info.get("native_audio_enabled"))
            scene_audio.native_audio_path = str(native_info.get("native_audio_path") or "")
            native_provider = str(
                native_info.get("api_used")
                or native_info.get("provider")
                or native_info.get("audio_source")
                or "video-model"
            ).strip()
            scene_audio.native_audio_source = native_provider if scene_audio.native_audio_enabled else ""
            if scene_audio.native_audio_enabled:
                scene_audio.subtitle_correction_status = "fallback"

            # A native-dialogue request is explicit: a missing provider audio
            # result must remain unresolved instead of silently becoming TTS.
            native_audio_missing = audio_mode == "native_dialogue" and not scene_audio.native_audio_enabled

            # Generate TTS for each dialogue line
            for dialogue_index, dialogue in enumerate(scene.dialogue, start=1):
                line_id = f"{scene.scene_id}:{dialogue_index}"
                old = old_lines.get(line_id)
                if scene_audio.native_audio_enabled:
                    dialogue_audio = DialogueAudio(
                        scene_id=scene.scene_id,
                        dialogue_index=dialogue_index,
                        character=dialogue.character,
                        text=dialogue.line,
                        line_id=line_id,
                        provider=f"{native_provider}-native",
                        success=True,
                    )
                    scene_audio.dialogue_audios.append(dialogue_audio)
                    continue
                if native_audio_missing:
                    scene_audio.dialogue_audios.append(DialogueAudio(
                        scene_id=scene.scene_id,
                        dialogue_index=dialogue_index,
                        character=dialogue.character,
                        text=dialogue.line,
                        line_id=line_id,
                        provider="native-pending",
                        success=False,
                        error_message="视频模型未返回原生音频",
                        fallback_reason="已请求原生音频，但本次视频没有返回可用音频；请重试原生音频或主动切换到外部 TTS。",
                    ))
                    continue
                profile = voice_profiles.get(dialogue.character)
                if not profile:
                    female_terms = ("女", "妈", "母", "姐", "姨", "妻", "公主")
                    configured_provider = os.getenv("DASHSCOPE_TTS_PROVIDER", "qwen3").lower()
                    is_female = any(term in dialogue.character for term in female_terms)
                    if configured_provider in {"minimax", "dashscope-minimax"}:
                        voice_key = "MINIMAX_DEFAULT_FEMALE_VOICE" if is_female else "MINIMAX_DEFAULT_MALE_VOICE"
                        fallback_voice = ""
                    else:
                        voice_key = "QWEN_DEFAULT_FEMALE_VOICE" if is_female else "QWEN_DEFAULT_MALE_VOICE"
                        fallback_voice = "Cherry" if is_female else "Ethan"
                    configured_voice = os.getenv(
                        voice_key, fallback_voice,
                    )
                    profile = VoiceProfile(
                        character=dialogue.character, provider=configured_provider, voice=configured_voice,
                        emotion="calm",
                    )
                    voice_profiles[dialogue.character] = profile
                dialogue_audio = DialogueAudio.model_validate(old.model_dump()) if old else DialogueAudio(
                    scene_id=scene.scene_id, dialogue_index=dialogue_index,
                    character=dialogue.character, text=dialogue.line, line_id=line_id,
                    voice=profile.voice, provider=profile.provider, emotion=profile.emotion,
                    speed=profile.speed, volume=profile.volume, pitch=profile.pitch,
                )
                previous_text = dialogue_audio.text
                dialogue_audio.scene_id = scene.scene_id
                dialogue_audio.dialogue_index = dialogue_index
                dialogue_audio.character = dialogue.character
                dialogue_audio.text = dialogue.line
                dialogue_audio.line_id = line_id
                if old and previous_text != dialogue.line:
                    dialogue_audio.needs_regeneration = True
                if not dialogue_audio.voice:
                    dialogue_audio.voice = profile.voice
                if not dialogue_audio.provider:
                    dialogue_audio.provider = profile.provider
                scene_audio.dialogue_audios.append(dialogue_audio)
                if (line_id in force_line_ids or dialogue_audio.needs_regeneration
                        or not dialogue_audio.success or not dialogue_audio.audio_path):
                    # Character profiles provide defaults only. Once a line is edited,
                    # its own saved expression controls the next synthesis.
                    self._line_context[(scene.scene_id, dialogue_index)] = VoiceProfile(
                        character=dialogue.character,
                        provider=dialogue_audio.provider,
                        voice=dialogue_audio.voice,
                        emotion=dialogue_audio.emotion,
                        speed=dialogue_audio.speed,
                        volume=dialogue_audio.volume,
                        pitch=dialogue_audio.pitch,
                    )
                    scene_tasks.append((dialogue_audio, generate_line_tts(dialogue_audio, output_dir)))

                # Track character→voice mapping
                if dialogue.character not in scene_audio.character_voice_map:
                    scene_audio.character_voice_map[dialogue.character] = dialogue_audio.voice

            # Collect dialogue text for subtitle reference
            scene_audio.tts_text = " ".join(
                f"{d.character}: {d.line}" for d in scene.dialogue
            )

            all_tasks.append((scene_audio, scene_tasks))
            scene_audio_list.append(scene_audio)

        # Execute all TTS tasks concurrently
        flat_tasks = []
        task_index: list[DialogueAudio] = []
        for scene_audio, tasks in all_tasks:
            for dialogue_audio, task in tasks:
                task_index.append(dialogue_audio)
                flat_tasks.append(task)

        if flat_tasks:
            results = await asyncio.gather(*flat_tasks, return_exceptions=True)
            for dialogue_audio, result in zip(task_index, results):
                path, provider, fallback_reason = result, dialogue_audio.provider, ""
                if isinstance(result, tuple):
                    path, provider, fallback_reason = result
                if isinstance(path, str) and os.path.isfile(path) and os.path.getsize(path) > 0:
                    duration = self._audio_duration(path)
                    if duration > 0:
                        dialogue_audio.audio_path = path
                        dialogue_audio.duration_sec = duration
                        dialogue_audio.provider = provider
                        dialogue_audio.fallback_reason = fallback_reason
                        version = AudioVersion(
                            audio_path=path,
                            provider=provider,
                            voice=dialogue_audio.voice,
                            duration_sec=duration,
                            text=dialogue_audio.text,
                            emotion=dialogue_audio.emotion,
                            speed=dialogue_audio.speed,
                            volume=dialogue_audio.volume,
                            pitch=dialogue_audio.pitch,
                            created_at=datetime.now().isoformat(timespec="seconds"),
                        )
                        dialogue_audio.versions.append(version)
                        dialogue_audio.active_version_id = version.version_id
                        dialogue_audio.success = True
                        dialogue_audio.needs_regeneration = False
                        dialogue_audio.error_message = ""
                    else:
                        dialogue_audio.error_message = "TTS returned an unreadable audio file"
                else:
                    message = str(result) if isinstance(result, Exception) else "TTS returned no readable audio"
                    dialogue_audio.error_message = message[:300]
                    print(f"  ⚠️ TTS task failed: {dialogue_audio.error_message}")

        # Retain the original single-track fields for legacy state and callers.
        for scene_audio in scene_audio_list:
            first = next((item for item in scene_audio.dialogue_audios if item.success), None)
            if first:
                scene_audio.tts_audio_path = first.audio_path

        for scene, scene_audio in zip(script.scenes, scene_audio_list):
            if scene_audio.native_audio_enabled and scene_audio.dialogue_audios:
                weights = [max(1, len(item.text.strip())) for item in scene_audio.dialogue_audios]
                total_weight = sum(weights) or 1
                for item, weight in zip(scene_audio.dialogue_audios, weights):
                    item.duration_sec = round(max(0.3, float(scene.duration_sec) * weight / total_weight), 3)
            self._layout_scene(scene, scene_audio)

        overall_mood = self._determine_overall_mood(script)

        return AudioAssets(
            scenes=scene_audio_list,
            bgm_path=existing.bgm_path if existing.bgm_path and os.path.isfile(existing.bgm_path) else "",
            bgm_mood=overall_mood,
            voice_profiles=voice_profiles,
            library_assets=existing.library_assets,
            preview_mix_path=existing.preview_mix_path,
            timeline_version=existing.timeline_version + 1,
            # External TTS owns the dialogue mix.  Explicitly disable source
            # video audio so a provider that still embeds native speech cannot
            # be mixed twice during final assembly.
            source_audio_enabled=(
                audio_mode == "native_dialogue"
                and any(item.native_audio_enabled for item in scene_audio_list)
            ),
        )

    @staticmethod
    def _layout_scene(scene, scene_audio: SceneAudio) -> None:
        cursor = 0
        limit = int(scene.duration_sec * 1000)
        for line in scene_audio.dialogue_audios:
            line.timing_conflict = False
            if not line.success:
                continue
            start = cursor + line.lead_silence_ms
            end = start + int(line.duration_sec * 1000)
            line.start_ms, line.end_ms = start, end
            cursor = end + line.tail_silence_ms
            if cursor > limit:
                line.timing_conflict = True

    @staticmethod
    def _audio_duration(path: str) -> float:
        try:
            from moviepy import AudioFileClip
            clip = AudioFileClip(path)
            duration = float(clip.duration or 0.0)
            clip.close()
            return max(0.0, duration)
        except Exception:
            return 0.0

    async def _generate_dialogue_tts(
        self,
        scene_id: int,
        dialogue_index: int,
        character: str,
        text: str,
        voice: str,
        output_dir: Path,
    ) -> str:
        """Generate TTS for a single dialogue line."""
        if not text.strip():
            return ""

        speaker_id = hashlib.sha1(character.encode("utf-8")).hexdigest()[:8]
        generation_id = uuid.uuid4().hex[:12]
        filename = f"scene_{scene_id:03d}_line_{dialogue_index:03d}_speaker_{speaker_id}_{generation_id}.mp3"
        output_path = str(output_dir / filename)

        profile = self._line_context.get((scene_id, dialogue_index))
        provider = profile.provider if profile else "edge"
        selected_voice = profile.voice if profile else voice
        if provider == "edge" and not selected_voice:
            selected_voice = get_voice_for_character(character)
        return await generate_timeline_tts(
            text, provider, selected_voice, output_path,
            emotion=profile.emotion if profile else "calm",
            speed=profile.speed if profile else 1.0,
            volume=profile.volume if profile else 1.0,
            pitch=profile.pitch if profile else 0,
        )

    def _determine_overall_mood(self, script: ScriptBreakdown) -> str:
        """Determine the overall BGM mood from the script's emotional progression."""
        # Count emotion tones across scenes
        mood_counts: dict[str, int] = {}
        for scene in script.scenes:
            tone = scene.emotion_tone
            mood_counts[tone] = mood_counts.get(tone, 0) + 1

        # Get the dominant mood
        if mood_counts:
            dominant = max(mood_counts, key=mood_counts.get)
            return MOOD_BGM_MAP.get(dominant, "neutral")

        return "neutral"

    async def _generate_mock_bgm(self, mood: str, output_dir: Path) -> str:
        """Generate a silent BGM placeholder.

        In production, this would call a music generation API (e.g. Suno, Mubert)
        or select from a licensed music library.
        """
        bgm_path = str(output_dir / "bgm.mp3")

        # Generate a short silent audio as placeholder
        cmd = (
            f"ffmpeg -y -f lavfi -i anullsrc=r=44100:cl=mono -t 60"
            f" -c:a libmp3lame -q:a 9 \"{bgm_path}\" 2>/dev/null"
        )
        run_ffmpeg_shell(cmd, timeout=10)

        return bgm_path
