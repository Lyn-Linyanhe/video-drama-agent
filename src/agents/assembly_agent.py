"""Assembly Agent — composites video clips, adds subtitles, transitions, and audio."""

import os
from pathlib import Path
import uuid
from datetime import datetime

from src.schemas.script import ScriptBreakdown
from src.schemas.assets import GeneratedAssets
from src.schemas.audio import AudioAssets
from src.config import OUTPUT_DIR, OUTPUT_FPS, OUTPUT_CODEC, OUTPUT_BITRATE
from src.services.render_timeline import build_render_timeline
from src.services.audio_timeline import build_bgm_segments
from src.utils.video import run_ffmpeg, run_ffmpeg_shell


class AssemblyAgent:
    """Agent that assembles all generated assets into the final video.

    Responsibilities:
    - Concatenate video clips in scene order
    - Add transitions between scenes (crossfade, cut, etc.)
    - Burn subtitles from dialogue text
    - Mix background music with TTS audio
    - Output final video in the target format (9:16, H.264)

    Uses MoviePy for video editing and FFmpeg for encoding.
    """

    def __init__(self):
        self.output_dir = OUTPUT_DIR / "final"
        self.output_dir.mkdir(exist_ok=True)

    async def run(
        self,
        script: ScriptBreakdown,
        assets: GeneratedAssets,
        audio: AudioAssets,
        project_id: str = "",
        subtitle_enabled: bool = True,
        subtitle_mode: str | None = None,
        audio_override: str = "",
    ) -> str:
        """Assemble all assets into the final video file.

        Args:
            script: The script breakdown.
            assets: Generated video clips.
            audio: Audio assets (TTS, BGM).

        Returns:
            Path to the final assembled video file.
        """
        silent_dialogue = audio_override == "no_dialogue_confirmed"
        if silent_dialogue:
            subtitle_mode = "off"
            subtitle_enabled = False
            if audio:
                # Preserve BGM/SFX but remove generated dialogue tracks for
                # this one assembly only. Native audio baked into source video
                # remains provider-owned and is not rewritten here.
                from copy import deepcopy
                audio = deepcopy(audio)
                for scene_audio in audio.scenes:
                    scene_audio.dialogue_audios = []
                    scene_audio.tts_audio_path = ""
                    scene_audio.tts_text = ""
        else:
            subtitle_mode = subtitle_mode if subtitle_mode in {"off", "dialogue", "speaker"} else ("dialogue" if subtitle_enabled else "off")
            subtitle_enabled = subtitle_mode != "off"
        output_dir = self.output_dir
        if project_id:
            output_dir = OUTPUT_DIR / project_id / "assets" / "final"
            output_dir.mkdir(parents=True, exist_ok=True)

        output_filename = f"final_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}.mp4"
        output_path = str(output_dir / output_filename)

        conflicts = [line.line_id or f"{scene.scene_id}:{line.dialogue_index}"
                     for scene in (audio.scenes if audio else []) for line in scene.dialogue_audios
                     if line.timing_conflict]
        if conflicts and not silent_dialogue:
            raise ValueError(f"Audio timing conflicts must be resolved before assembly: {', '.join(conflicts[:5])}")
        stale_lines = [line.line_id or f"{scene.scene_id}:{line.dialogue_index}"
                       for scene in (audio.scenes if audio else []) for line in scene.dialogue_audios
                       if line.needs_regeneration]
        stale_lines.extend(
            item for item in self._script_audio_mismatches(script, audio)
            if item not in stale_lines
        )
        if stale_lines and not silent_dialogue:
            raise ValueError(f"Audio lines must be regenerated before assembly: {', '.join(stale_lines[:5])}")
        failed_lines = [line.line_id or f"{scene.scene_id}:{line.dialogue_index}"
                        for scene in (audio.scenes if audio else []) for line in scene.dialogue_audios
                        if line.text.strip() and not line.success]
        if failed_lines and not silent_dialogue:
            raise ValueError(f"Audio lines failed or are missing: {', '.join(failed_lines[:5])}")

        # The FFmpeg path is the authoritative renderer for timeline offsets,
        # selected BGM/SFX, and subtitle timestamps.
        if audio and audio.timeline_version >= 1:
            return await self._assemble_with_ffmpeg(script, assets, audio, output_path, output_dir, subtitle_enabled, subtitle_mode=subtitle_mode)

        try:
            return await self._assemble_with_moviepy(script, assets, audio, output_path, subtitle_enabled)
        except ImportError:
            # Fallback: simple FFmpeg concatenation
            return await self._assemble_with_ffmpeg(script, assets, audio, output_path, output_dir, subtitle_enabled, subtitle_mode=subtitle_mode)
        except Exception as e:
            print(f"  ⚠️ MoviePy 合成失败: {e}, 尝试 FFmpeg 回退...")
            return await self._assemble_with_ffmpeg(script, assets, audio, output_path, output_dir, subtitle_enabled, subtitle_mode=subtitle_mode)

    async def _assemble_with_moviepy(
        self,
        script: ScriptBreakdown,
        assets: GeneratedAssets,
        audio: AudioAssets | None,
        output_path: str,
        subtitle_enabled: bool = True,
    ) -> str:
        """Assemble using MoviePy with transitions and subtitles."""
        from moviepy import VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip, concatenate_videoclips
        from moviepy.video.fx import CrossFadeIn, CrossFadeOut

        asset_map = {a.scene_id: a for a in assets.shots}
        audio_map = {a.scene_id: a for a in (audio.scenes if audio else [])}

        clips = []
        for i, scene in enumerate(script.scenes):
            asset = asset_map.get(scene.scene_id)
            if asset is None or not asset.success or not asset.local_path:
                # Show a subtle transition card instead of raw black screen
                print(f"  ⚠️  场景 {scene.scene_id} 无视频，使用过渡画面")
                clip = self._create_text_clip(
                    f"{scene.location}\n{scene.emotion_tone}",
                    duration=scene.duration_sec,
                )
            else:
                try:
                    clip = VideoFileClip(asset.local_path)
                    # Trim if longer than target, keep as-is if shorter
                    if clip.duration > scene.duration_sec:
                        clip = clip.subclipped(0, scene.duration_sec)
                except Exception as e:
                    print(f"  ⚠️ 无法加载 {asset.local_path}: {e}")
                    clip = self._create_text_clip(
                        f"[场景 {scene.scene_id}]\n{scene.action_description}",
                        duration=scene.duration_sec,
                    )

            scene_audio = audio_map.get(scene.scene_id)
            clip = self._attach_dialogue_audio(clip, scene_audio)
            if subtitle_enabled:
                clip = self._add_scene_subtitles(clip, scene, scene_audio)

            # Legacy single-track path retained only for old serialized projects.
            if False and scene_audio and scene_audio.tts_audio_path and os.path.exists(scene_audio.tts_audio_path):
                try:
                    audio_clip = AudioFileClip(scene_audio.tts_audio_path)
                    # Mix TTS with existing audio if any
                    if clip.audio:
                        from moviepy import CompositeAudioClip
                        clip.audio = CompositeAudioClip([
                            clip.audio.with_effects([], volume=scene_audio.volume_mix.get("bgm", 0.2)),
                            audio_clip.with_effects([], volume=scene_audio.volume_mix.get("tts", 1.0)),
                        ])
                    else:
                        clip.audio = audio_clip
                except Exception as e:
                    print(f"  ⚠️ 音频加载失败: {e}")

            # Generate subtitle overlay
            subtitle_text = self._build_subtitle_text(scene)
            if False and subtitle_enabled and subtitle_text:
                try:
                    txt_clip = TextClip(
                        text=subtitle_text,
                        font="C:/Windows/Fonts/msyh.ttc",
                        font_size=32,
                        color="white",
                        stroke_color="black",
                        stroke_width=2,
                        size=(clip.w - 40, None),
                        method="caption",
                    )
                    txt_clip = txt_clip.with_position(("center", clip.h - 120)).with_duration(clip.duration)

                    clip = CompositeVideoClip([clip, txt_clip])
                except Exception as e:
                    print(f"  ⚠️ 字幕生成失败: {e}")

            # Add transition (crossfade) between scenes
            if i > 0 and scene.transition_from_prev == "fade":
                clip = clip.with_effects([CrossFadeIn(0.3), CrossFadeOut(0.3)])

            clips.append(clip)

        if not clips:
            return self._create_empty_output(output_path)

        # Concatenate all clips
        final = concatenate_videoclips(clips, method="compose")

        final = self._attach_bgm(final, audio)

        # Legacy BGM path retained for compatibility with older MoviePy versions.
        if False and audio and audio.bgm_path and os.path.exists(audio.bgm_path):
            try:
                bgm = AudioFileClip(audio.bgm_path).with_effects(
                    [], volume=0.15
                )
                if bgm.duration < final.duration:
                    bgm = bgm.with_effects([], duration=final.duration)
                else:
                    bgm = bgm.subclipped(0, final.duration)

                from moviepy import CompositeAudioClip
                if final.audio:
                    final.audio = CompositeAudioClip([final.audio, bgm])
                else:
                    final.audio = bgm
            except Exception as e:
                print(f"  ⚠️ BGM 加载失败: {e}")

        # Write output
        final.write_videofile(
            output_path,
            fps=OUTPUT_FPS,
            codec=OUTPUT_CODEC,
            bitrate=OUTPUT_BITRATE,
            audio_codec="aac",
            threads=4,
            logger=None,
        )

        # Cleanup
        for clip in clips:
            clip.close()
        final.close()

        return output_path

    @staticmethod
    def _dialogue_tracks(scene_audio):
        """Return successful line tracks, including a legacy single-track fallback."""
        if not scene_audio:
            return []
        tracks = [
            item for item in scene_audio.dialogue_audios
            if item.success and os.path.isfile(item.audio_path)
        ]
        if tracks:
            return sorted(tracks, key=lambda item: item.dialogue_index)
        if scene_audio.tts_audio_path and os.path.isfile(scene_audio.tts_audio_path):
            return [type("LegacyTrack", (), {
                "audio_path": scene_audio.tts_audio_path,
                "duration_sec": 0.0,
                "character": "",
                "text": scene_audio.tts_text,
            })()]
        return []

    @staticmethod
    def _compact_text(value: str) -> str:
        return " ".join(str(value or "").split())

    @classmethod
    def _script_audio_mismatches(cls, script: ScriptBreakdown, audio: AudioAssets | None) -> list[str]:
        if not audio:
            return []
        audio_by_scene = {scene.scene_id: scene for scene in audio.scenes}
        mismatches: list[str] = []
        for script_scene in script.scenes:
            scene_audio = audio_by_scene.get(script_scene.scene_id)
            if not scene_audio:
                continue
            lines = {line.dialogue_index: line for line in scene_audio.dialogue_audios}
            for dialogue_index, dialogue in enumerate(script_scene.dialogue, start=1):
                line = lines.get(dialogue_index)
                if not line or not line.success:
                    continue
                if (
                    cls._compact_text(line.character) != cls._compact_text(dialogue.character)
                    or cls._compact_text(line.text) != cls._compact_text(dialogue.line)
                ):
                    mismatches.append(line.line_id or f"{script_scene.scene_id}:{dialogue_index}")
        return mismatches

    def _attach_dialogue_audio(self, clip, scene_audio):
        """Append spoken lines in script order and preserve any source video audio."""
        tracks = self._dialogue_tracks(scene_audio)
        if not tracks:
            return clip
        try:
            from moviepy import AudioFileClip, CompositeAudioClip, concatenate_audioclips

            dialogue_clips = [AudioFileClip(track.audio_path) for track in tracks]
            speech = concatenate_audioclips(dialogue_clips).with_volume_scaled(
                scene_audio.volume_mix.get("tts", 1.0)
            )
            if speech.duration > clip.duration:
                # MoviePy holds the final frame for an extended clip, avoiding truncated dialogue.
                clip = clip.with_duration(speech.duration)
            if clip.audio:
                source = clip.audio.with_volume_scaled(scene_audio.volume_mix.get("sfx", 0.3))
                return clip.with_audio(CompositeAudioClip([source, speech]))
            return clip.with_audio(speech)
        except Exception as exc:
            print(f"Audio assembly skipped: {exc}")
            return clip

    def _add_scene_subtitles(self, clip, scene, scene_audio):
        """Burn each line only for its own playback interval."""
        tracks = self._dialogue_tracks(scene_audio)
        if not tracks:
            return clip
        try:
            from moviepy import CompositeVideoClip, TextClip

            overlays = []
            start = 0.0
            for track in tracks:
                duration = float(track.duration_sec or 0.0)
                if duration <= 0:
                    duration = max(1.0, len(track.text) / 4)
                text = f"{track.character}: {track.text}".strip(": ")
                if text:
                    subtitle = TextClip(
                        text=text,
                        font="C:/Windows/Fonts/msyh.ttc",
                        font_size=32,
                        color="white",
                        stroke_color="black",
                        stroke_width=2,
                        size=(clip.w - 40, None),
                        method="caption",
                    ).with_position(("center", clip.h - 120)).with_start(start).with_duration(duration)
                    overlays.append(subtitle)
                start += duration
            return CompositeVideoClip([clip, *overlays]) if overlays else clip
        except Exception as exc:
            print(f"Subtitle assembly skipped: {exc}")
            return clip

    @staticmethod
    def _attach_bgm(final, audio: AudioAssets | None):
        if not audio or not audio.bgm_path or not os.path.isfile(audio.bgm_path):
            return final
        try:
            from moviepy import AudioFileClip, CompositeAudioClip
            from moviepy.audio.fx import AudioLoop

            bgm = AudioLoop(duration=final.duration).apply(AudioFileClip(audio.bgm_path))
            bgm = bgm.with_volume_scaled(0.15)
            return final.with_audio(CompositeAudioClip([final.audio, bgm]) if final.audio else bgm)
        except Exception as exc:
            print(f"BGM assembly skipped: {exc}")
            return final

    async def _assemble_with_ffmpeg(
        self,
        script: ScriptBreakdown,
        assets: GeneratedAssets,
        audio: AudioAssets | None,
        output_path: str,
        output_dir: Path,
        subtitle_enabled: bool = True,
        subtitle_mode: str | None = None,
    ) -> str:
        """Render video boundaries first, then place the project audio timeline."""
        asset_map = {a.scene_id: a for a in assets.shots}

        clips = [
            (scene, asset_map.get(scene.scene_id)) for scene in script.scenes
            if asset_map.get(scene.scene_id) and asset_map[scene.scene_id].success
            and asset_map[scene.scene_id].local_path and os.path.exists(asset_map[scene.scene_id].local_path)
        ]
        if not clips:
            return self._create_empty_output(output_path)

        if len(clips) == 1:
            # A one-shot project has no boundary to smooth; retain the direct
            # path so its original media is passed to the shared audio mixer.
            source_path = clips[0][1].local_path
            result = run_ffmpeg_shell(
                f'ffmpeg -y -i "{source_path}" -c:v libx264 -c:a aac '
                f'-r {OUTPUT_FPS} -pix_fmt yuv420p "{output_path}"', timeout=120,
            )
            if result.returncode != 0 or not os.path.isfile(output_path):
                raise RuntimeError(f"FFmpeg single-shot render failed: {result.stderr[-500:]}")
            return self._mux_ffmpeg_audio_and_subtitles(
                script, audio, output_path, output_dir, subtitle_enabled,
                {clips[0][0].scene_id: 0}, subtitle_mode,
                {clips[0][0].scene_id: int(max(0.1, self._media_duration(source_path) or clips[0][0].duration_sec) * 1000)},
            )

        rendered_video, scene_starts = self._render_transition_video(clips, output_path, output_dir)
        actual_durations = {
            scene.scene_id: max(0.1, self._media_duration(asset.local_path) or float(asset.duration_sec or scene.duration_sec))
            for scene, asset in clips
        }
        scene_durations = build_render_timeline(
            [scene for scene, _ in clips], actual_durations,
        ).scene_durations_ms
        result_path = self._mux_ffmpeg_audio_and_subtitles(
            script, audio, rendered_video, output_dir, subtitle_enabled,
            scene_starts, subtitle_mode, scene_durations,
        )
        if result_path != output_path:
            os.replace(result_path, output_path)
        return output_path

    def _render_transition_video(self, clips, output_path: str, output_dir: Path) -> tuple[str, dict[int, int]]:
        """Compose video and its quiet source-audio bed with one FFmpeg graph."""
        temp_path = str(output_dir / f"transitions_{uuid.uuid4().hex[:8]}.mp4")
        command = ["-y"]
        durations = []
        source_audio = True
        for _, asset in clips:
            command.extend(["-i", asset.local_path])
            durations.append(max(0.1, self._media_duration(asset.local_path) or float(asset.duration_sec or 3)))
            source_audio = source_audio and self._has_audio_stream(asset.local_path)

        timeline = build_render_timeline(
            [scene for scene, _ in clips],
            {scene.scene_id: duration for (scene, _), duration in zip(clips, durations)},
        )

        filters = []
        for index, duration in enumerate(durations):
            filters.append(f"[{index}:v]setpts=PTS-STARTPTS,format=yuv420p[v{index}]")
            if source_audio:
                fade = min(0.08, duration / 2)
                filters.append(
                    f"[{index}:a]aformat=sample_rates=44100:channel_layouts=stereo,volume=0.22,"
                    f"afade=t=in:st=0:d={fade:.3f},afade=t=out:st={duration-fade:.3f}:d={fade:.3f}[a{index}]"
                )

        scene_starts = dict(timeline.scene_starts_ms)
        video_label = "v0"
        audio_label = "a0" if source_audio else ""
        for index in range(1, len(clips)):
            scene, _ = clips[index]
            transition = str(getattr(scene, "transition_from_prev", "cut") or "cut").lower()
            duration = timeline.overlap_ms.get(scene.scene_id, 0) / 1000.0
            next_video = f"vx{index}"
            if duration:
                effect = "fade" if transition == "fade" else "dissolve"
                offset = scene_starts[scene.scene_id] / 1000.0
                filters.append(f"[{video_label}][v{index}]xfade=transition={effect}:duration={duration:.3f}:offset={offset:.3f}[{next_video}]")
                if source_audio:
                    next_audio = f"ax{index}"
                    filters.append(f"[{audio_label}][a{index}]acrossfade=d={duration:.3f}:c1=tri:c2=tri[{next_audio}]")
                    audio_label = next_audio
            else:
                filters.append(f"[{video_label}][v{index}]concat=n=2:v=1:a=0[{next_video}]")
                if source_audio:
                    next_audio = f"ax{index}"
                    filters.append(f"[{audio_label}][a{index}]concat=n=2:v=0:a=1[{next_audio}]")
                    audio_label = next_audio
            video_label = next_video

        command.extend(["-filter_complex", ";".join(filters), "-map", f"[{video_label}]"])
        if source_audio:
            command.extend(["-map", f"[{audio_label}]"])
        command.extend(["-c:v", "libx264", "-c:a", "aac", "-pix_fmt", "yuv420p", temp_path])
        result = run_ffmpeg(command, timeout=180)
        if result.returncode != 0 or not os.path.isfile(temp_path) or os.path.getsize(temp_path) == 0:
            raise RuntimeError(f"FFmpeg transition render failed: {result.stderr[-500:]}")
        return temp_path, scene_starts

        # Legacy concat implementation kept below for compatibility reference.

        # Build concat file list
        concat_list_path = str(output_dir / "concat_list.txt")
        with open(concat_list_path, "w") as f:
            for scene in script.scenes:
                asset = asset_map.get(scene.scene_id)
                if asset and asset.success and asset.local_path and os.path.exists(asset.local_path):
                    f.write(f"file '{asset.local_path}'\n")
                else:
                    print(f"  ⚠️  场景 {scene.scene_id} 无视频，跳过")

        # Check if we have any files
        file_size = os.path.getsize(concat_list_path)
        if file_size == 0:
            return self._create_empty_output(output_path)

        cmd = (
            f"ffmpeg -y -f concat -safe 0 -i \"{concat_list_path}\""
            f" -c:v libx264 -preset medium -crf 23"
            f" -c:a aac -b:a 128k"
            f" -r {OUTPUT_FPS} -pix_fmt yuv420p"
            f" \"{output_path}\" 2>/dev/null"
        )
        result = run_ffmpeg_shell(cmd, timeout=120)
        if result.returncode != 0 or not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError(f"FFmpeg concat failed: {result.stderr[-500:]}")
        return self._mux_ffmpeg_audio_and_subtitles(script, audio, output_path, output_dir, subtitle_enabled)

    def _mux_ffmpeg_audio_and_subtitles(
        self,
        script: ScriptBreakdown,
        audio: AudioAssets | None,
        output_path: str,
        output_dir: Path,
        subtitle_enabled: bool,
        scene_starts: dict[int, int] | None = None,
        subtitle_mode: str | None = None,
        scene_durations: dict[int, int] | None = None,
    ) -> str:
        """Apply real dialogue audio and optional SRT subtitles after concat fallback."""
        timeline = build_render_timeline(script.scenes) if scene_starts is None else None
        resolved_scene_starts = scene_starts or (timeline.scene_starts_ms if timeline else {})
        resolved_scene_durations = scene_durations or (
            timeline.scene_durations_ms if timeline else {
                scene.scene_id: int(max(0.1, float(scene.duration_sec)) * 1000)
                for scene in script.scenes
            }
        )
        tracks_with_offsets = []
        native_audio_with_offsets = []
        sfx_with_offsets = []
        offset_ms = 0
        expected_duration = (
            max(0.1, timeline.total_duration_ms / 1000.0)
            if timeline else max(0.1, sum(float(scene.duration_sec) for scene in script.scenes))
        )
        # Use the real concatenated video length so an audio track can never
        # extend a short provider clip by holding the final video frame.
        timeline_duration = self._media_duration(output_path) or expected_duration
        audio_by_scene = {item.scene_id: item for item in (audio.scenes if audio else [])}
        library = {item.asset_id: item for item in (audio.library_assets if audio else [])}
        for scene in script.scenes:
            scene_start = resolved_scene_starts.get(scene.scene_id, offset_ms)
            scene_audio = audio_by_scene.get(scene.scene_id)
            tracks = self._dialogue_tracks(scene_audio)
            for track in tracks:
                track_start_ms = int(getattr(track, "start_ms", 0) or 0)
                delay_ms = scene_start + track_start_ms
                tracks_with_offsets.append((track, delay_ms))
                duration_ms = int(max(0.0, float(track.duration_sec or 0.0)) * 1000)
                offset_ms = max(offset_ms, delay_ms + duration_ms + getattr(track, "tail_silence_ms", 0))
            native_path = str(getattr(scene_audio, "native_audio_path", "") or "") if scene_audio else ""
            if (
                scene_audio and getattr(scene_audio, "native_audio_enabled", False)
                and native_path and os.path.isfile(native_path)
                and Path(native_path).suffix.lower() in {".mp3", ".wav", ".m4a", ".aac", ".ogg"}
            ):
                native_audio_with_offsets.append((native_path, scene_start))
            if scene_audio:
                for effect in scene_audio.sfx_tracks:
                    asset = library.get(effect.asset_id)
                    if asset and os.path.isfile(asset.audio_path):
                        sfx_with_offsets.append((asset.audio_path, scene_start + effect.start_ms, effect.volume))
            offset_ms = max(offset_ms, scene_start + resolved_scene_durations.get(
                scene.scene_id, int(scene.duration_sec * 1000)
            ))

        timeline_for_bgm = timeline
        if timeline_for_bgm is None:
            from types import SimpleNamespace
            timeline_for_bgm = SimpleNamespace(
                scene_starts_ms=resolved_scene_starts,
                scene_durations_ms=resolved_scene_durations,
            )
        scene_bgm = build_bgm_segments(script, audio, timeline_for_bgm)

        subtitle_mode = subtitle_mode if subtitle_mode in {"dialogue", "speaker"} else ("dialogue" if subtitle_enabled else "off")
        subtitle_path = self._write_subtitle_file(script, audio_by_scene, output_dir, resolved_scene_starts, subtitle_mode) if subtitle_mode != "off" else None
        temp_path = str(output_dir / f"mixed_{uuid.uuid4().hex[:8]}.mp4")
        command = ["-y", "-i", output_path]
        filters = []
        labels = []
        speech_labels = []
        # New audio timelines intentionally own the complete mix. A missing
        # value means a legacy project, for which the old source-audio behavior
        # remains readable and reproducible.
        has_source_audio = self._has_audio_stream(output_path) and (
            audio is None or audio.source_audio_enabled is not False
        )
        if has_source_audio:
            filters.append("[0:a]volume=1.0[source]")
            labels.append("[source]")
        for index, (track, delay_ms) in enumerate(tracks_with_offsets, start=1):
            command.extend(["-i", track.audio_path])
            label = f"speech{index}"
            filters.append(f"[{index}:a]adelay={delay_ms}:all=1,volume={getattr(track, 'volume', 1.0)}[{label}]")
            speech_labels.append(f"[{label}]")

        dialogue_label = ""
        if speech_labels:
            dialogue_label = "[dialogue]"
            filters.append(
                f"{''.join(speech_labels)}amix=inputs={len(speech_labels)}:duration=longest:"
                f"dropout_transition=0{dialogue_label}"
            )
            labels.append(dialogue_label)

        native_label = ""
        native_labels = []
        native_input_start = len(tracks_with_offsets) + 1
        for index, (path, delay_ms) in enumerate(native_audio_with_offsets, start=native_input_start):
            command.extend(["-i", path])
            label = f"native{index}"
            filters.append(f"[{index}:a]adelay={delay_ms}:all=1,volume=1.0[{label}]")
            native_labels.append(f"[{label}]")
        if native_labels:
            native_label = "[native_dialogue]"
            filters.append(
                f"{''.join(native_labels)}amix=inputs={len(native_labels)}:duration=longest:"
                f"dropout_transition=0{native_label}"
            )
            labels.append(native_label)

        duck_reference = dialogue_label or native_label or ("[source]" if has_source_audio else "")

        input_index = native_input_start + len(native_audio_with_offsets)
        for index, (path, delay_ms, volume) in enumerate(sfx_with_offsets, start=input_index):
            command.extend(["-i", path])
            label = f"sfx{index}"
            filters.append(f"[{index}:a]adelay={delay_ms}:all=1,volume={volume}[{label}]")
            labels.append(f"[{label}]")
        input_index += len(sfx_with_offsets)
        for index, segment in enumerate(scene_bgm, start=input_index):
            path = segment["path"]
            delay_ms = max(0, segment["start_ms"] - segment["fade_in_ms"])
            duration_ms = segment["duration_ms"] + segment["fade_in_ms"]
            volume = segment["volume"]
            fade_in_ms = segment["fade_in_ms"]
            fade_out_ms = segment["fade_out_ms"]
            command.extend(["-stream_loop", "-1", "-i", path])
            raw_label = f"bgmraw{index}"
            bgm_filter = f"[{index}:a]atrim=duration={duration_ms / 1000:.3f}"
            if fade_in_ms > 0:
                bgm_filter += f",afade=t=in:st=0:d={fade_in_ms / 1000:.3f}"
            if fade_out_ms > 0:
                bgm_filter += f",afade=t=out:st={(segment['duration_ms'] - fade_out_ms) / 1000:.3f}:d={fade_out_ms / 1000:.3f}"
            filters.append(f"{bgm_filter},adelay={delay_ms}:all=1,volume={volume}[{raw_label}]")
            label = f"bgm{index}"
            if duck_reference:
                filters.append(
                    f"[{raw_label}]{duck_reference}sidechaincompress="
                    f"threshold=0.02:ratio=8:attack=20:release=350[{label}]"
                )
            else:
                filters.append(f"[{raw_label}]anull[{label}]")
            labels.append(f"[{label}]")

        if audio and audio.bgm_path and os.path.isfile(audio.bgm_path) and not scene_bgm:
            bgm_index = input_index + len(scene_bgm)
            command.extend(["-stream_loop", "-1", "-i", audio.bgm_path])
            filters.append(f"[{bgm_index}:a]atrim=duration={timeline_duration:.3f},volume=0.15[bgm]")
            labels.append("[bgm]")

        command.extend(["-map", "0:v:0"])
        if labels:
            filters.append(
                f"{''.join(labels)}amix=inputs={len(labels)}:duration=longest:dropout_transition=0:normalize=0,"
                f"loudnorm=I=-16:TP=-1.5:LRA=11,apad,atrim=duration={timeline_duration:.3f}[mix]"
            )
            command.extend(["-filter_complex", ";".join(filters), "-map", "[mix]"])
        else:
            command.extend(["-map", "0:a?"] if has_source_audio else ["-an"])
        if subtitle_path:
            escaped = str(subtitle_path).replace("\\", "/").replace(":", "\\:")
            command.extend(["-vf", f"subtitles={escaped}"])
        command.extend(["-c:v", "libx264", "-c:a", "aac", temp_path])
        result = run_ffmpeg(command, timeout=180)
        if result.returncode != 0 or not os.path.isfile(temp_path) or os.path.getsize(temp_path) == 0:
            raise RuntimeError(f"FFmpeg audio/subtitle pass failed: {result.stderr[-500:]}")
        os.replace(temp_path, output_path)
        return output_path

    @staticmethod
    def _has_audio_stream(path: str) -> bool:
        """Avoid referencing an optional video audio stream in a filter graph."""
        try:
            from moviepy import VideoFileClip

            clip = VideoFileClip(path)
            has_audio = clip.audio is not None
            clip.close()
            return has_audio
        except Exception:
            return False

    @staticmethod
    def _media_duration(path: str) -> float:
        try:
            from moviepy import VideoFileClip

            clip = VideoFileClip(path)
            duration = float(clip.duration or 0.0)
            clip.close()
            return duration if duration > 0 else 0.0
        except Exception:
            return 0.0

    def _write_subtitle_file(self, script, audio_by_scene, output_dir: Path, scene_starts: dict[int, int] | None = None, subtitle_mode: str = "dialogue") -> Path | None:
        if scene_starts is None:
            scene_starts = build_render_timeline(script.scenes).scene_starts_ms
        entries = []
        cursor = 0.0
        for scene in script.scenes:
            scene_start = (scene_starts.get(scene.scene_id, int(cursor * 1000)) / 1000)
            scene_audio = audio_by_scene.get(scene.scene_id)
            if scene_audio and getattr(scene_audio, "asr_segments", None) and getattr(scene_audio, "subtitle_correction_status", "") in {"ready", "changed"}:
                speaker_by_line = {
                    (line.line_id or f"{scene.scene_id}:{line.dialogue_index}"): line.character
                    for line in scene_audio.dialogue_audios
                }
                for segment in scene_audio.asr_segments:
                    text = str(segment.get("text") or "").strip()
                    if not text:
                        continue
                    if subtitle_mode == "speaker":
                        character = speaker_by_line.get(str(segment.get("line_id") or ""), "")
                        text = f"{character}: {text}" if character else text
                    start = scene_start + float(segment.get("start") or (float(segment.get("start_ms") or 0) / 1000))
                    end = scene_start + float(segment.get("end") or (float(segment.get("end_ms") or 0) / 1000))
                    if end <= start:
                        end = start + 0.3
                    entries.append((start, end, text))
                    cursor = max(cursor, end)
                cursor = max(cursor, scene_start + float(scene.duration_sec))
                continue
            tracks = (
                [item for item in (scene_audio.dialogue_audios if scene_audio else []) if item.success]
                if scene_audio and getattr(scene_audio, "native_audio_enabled", False)
                else self._dialogue_tracks(scene_audio)
            )
            for track in tracks:
                duration = float(track.duration_sec or 0.0) or max(1.0, len(track.text) / 4)
                text = (f"{track.character}: {track.text}".strip(": ")
                        if subtitle_mode == "speaker" else track.text.strip())
                start = scene_start + ((getattr(track, "start_ms", 0) or 0) / 1000)
                if text:
                    entries.append((start, start + duration, text))
                cursor = max(cursor, start + duration + (getattr(track, "tail_silence_ms", 0) / 1000))
            cursor = max(cursor, scene_start + float(scene.duration_sec))
        if not entries:
            return None
        path = output_dir / f"subtitles_{uuid.uuid4().hex[:8]}.srt"
        with open(path, "w", encoding="utf-8") as handle:
            for index, (start, end, text) in enumerate(entries, start=1):
                handle.write(f"{index}\n{self._srt_timestamp(start)} --> {self._srt_timestamp(end)}\n{text}\n\n")
        return path

    @staticmethod
    def _srt_timestamp(seconds: float) -> str:
        total_ms = max(0, round(seconds * 1000))
        hours, remainder = divmod(total_ms, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        seconds, milliseconds = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

    def _create_text_clip(self, text: str, duration: int) -> "VideoClip":
        """Create a text placeholder clip when no video is available."""
        from moviepy import TextClip, ColorClip
        try:
            txt = TextClip(
                text=text,
                font="C:/Windows/Fonts/msyh.ttc",
                font_size=36,
                color="white",
                size=(1080, 1920),
                bg_color="black",
                method="caption",
                text_align="center",
            )
            return txt.with_duration(duration)
        except Exception:
            from moviepy import ColorClip
            return ColorClip(size=(1080, 1920), color=(0, 0, 0)).with_duration(duration)

    def _build_subtitle_text(self, scene) -> str:
        """Build subtitle text from scene dialogue."""
        if not scene.dialogue:
            return ""
        lines = []
        for d in scene.dialogue:
            emotion_tag = f"({d.emotion})" if d.emotion else ""
            lines.append(f"{d.character}: {d.line} {emotion_tag}")
        return "\n".join(lines)

    def _create_empty_output(self, output_path: str) -> str:
        """Create an empty output when no clips are available."""
        cmd = (
            f"ffmpeg -y -f lavfi -i color=c=black:s=1080x1920:d=3"
            f" -vf \"drawtext=text='No video generated':fontsize=48:fontcolor=white:"
            f"x=(w-text_w)/2:y=(h-text_h)/2\""
            f" -c:v libx264 -c:a aac \"{output_path}\" 2>/dev/null"
        )
        run_ffmpeg_shell(cmd, timeout=30)
        return output_path
