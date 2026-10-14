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


DEFAULT_SUBTITLE_STYLE = {
    "font_name": "Microsoft YaHei",
    # 34pt reads correctly at 1080p; 46pt overpowers the frame on wide shots.
    "font_size": 34,
    "primary_colour": "&H00FFFFFF",
    "outline_colour": "&H00101010",
    "outline": 2.4,
    "shadow": 0.6,
    "margin_v": 48,
}


def normalize_subtitle_style(style: dict | None) -> dict:
    """Fill a subtitle style with defaults, ignoring blank/None overrides."""
    resolved = dict(DEFAULT_SUBTITLE_STYLE)
    for key, value in (style or {}).items():
        if key not in DEFAULT_SUBTITLE_STYLE or value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        resolved[key] = value
    return resolved


def subtitle_force_style(style: dict | None) -> str:
    """Render a libass force_style string from a resolved style dict."""
    resolved = normalize_subtitle_style(style)
    return (
        f"FontName={resolved['font_name']},"
        f"FontSize={resolved['font_size']},"
        f"PrimaryColour={resolved['primary_colour']},"
        f"OutlineColour={resolved['outline_colour']},"
        f"BorderStyle=1,"
        f"Outline={resolved['outline']},"
        f"Shadow={resolved['shadow']},"
        f"Alignment=2,"
        f"MarginV={resolved['margin_v']}"
    )


def subtitle_filter(subtitle_path, style: dict | None = None) -> str:
    """Build the `subtitles=` video filter, escaping the platform path.

    Shared by full assembly and the standalone burn endpoint so the Windows
    drive-letter escaping only has to be right in one place.
    """
    escaped = str(subtitle_path).replace("\\", "/").replace(":", "\\:")
    escaped = escaped.replace("'", r"\'")
    return f"subtitles='{escaped}':force_style='{subtitle_force_style(style)}'"


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
        generation_prompts=None,
        subtitle_style: dict | None = None,
    ) -> str:
        """Assemble all assets into the final video file.

        Args:
            script: The script breakdown.
            assets: Generated video clips.
            audio: Audio assets (TTS, BGM).

        Returns:
            Path to the final assembled video file.
        """
        if generation_prompts is not None:
            expected_from_plan = [
                str(unit.shot_id)
                for plan in (getattr(generation_prompts, "shot_plans", []) or [])
                for unit in (getattr(plan, "units", []) or [])
                if getattr(unit, "shot_id", None)
            ]
            if expected_from_plan:
                existing = list(getattr(assets, "expected_shot_ids", []) or [])
                merged_expected = list(dict.fromkeys(existing + expected_from_plan))
                assets = assets.model_copy(update={"expected_shot_ids": merged_expected})

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
            return await self._assemble_with_ffmpeg(script, assets, audio, output_path, output_dir, subtitle_enabled, subtitle_mode=subtitle_mode, subtitle_style=subtitle_style)

        try:
            return await self._assemble_with_moviepy(script, assets, audio, output_path, subtitle_enabled)
        except ImportError:
            # Fallback: simple FFmpeg concatenation
            return await self._assemble_with_ffmpeg(script, assets, audio, output_path, output_dir, subtitle_enabled, subtitle_mode=subtitle_mode, subtitle_style=subtitle_style)
        except Exception as e:
            print(f"  ⚠️ MoviePy 合成失败: {e}, 尝试 FFmpeg 回退...")
            return await self._assemble_with_ffmpeg(script, assets, audio, output_path, output_dir, subtitle_enabled, subtitle_mode=subtitle_mode, subtitle_style=subtitle_style)

    @staticmethod
    def _shot_order(asset) -> tuple[int, str]:
        explicit_order = getattr(asset, "shot_order", None)
        if explicit_order is not None:
            try:
                return (int(explicit_order), str(getattr(asset, "shot_id", "") or ""))
            except (TypeError, ValueError):
                pass
        # Legacy projects may not have persisted order yet.
        shot_id = str(getattr(asset, "shot_id", "") or "")
        match = __import__("re").search(r"(?:shot|unit)-(\d+)$", shot_id)
        return (int(match.group(1)) if match else 10**9, shot_id)

    def _expanded_video_clips(self, script: ScriptBreakdown, assets: GeneratedAssets):
        """Expand child-shot assets into an ordered render list.

        A scene can now own several executable ShotUnits.  Assembly must not
        collapse them into one ``scene_id`` map: doing so silently discards all
        but the last child.  The returned synthetic scene ids are render-only;
        the third tuple element retains the original scene id for audio/timeline
        binding.
        """
        expected = {str(item) for item in (getattr(assets, "expected_shot_ids", []) or []) if str(item)}
        expanded = []
        for source_scene in script.scenes:
            scene_expected = {
                shot_id for shot_id in expected
                if shot_id.startswith(f"scene-{source_scene.scene_id}-")
            }
            candidates = [
                asset for asset in (assets.shots or [])
                if int(getattr(asset, "scene_id", 0) or 0) == int(source_scene.scene_id)
            ]
            child_candidates = [asset for asset in candidates if getattr(asset, "shot_id", None)]
            if child_candidates or scene_expected:
                # Repeated ids are retained generations of one executable
                # unit. Last-write-wins matches the active version persisted
                # by the production state and prevents duplicate rendering.
                latest_by_shot: dict[str, object] = {}
                for asset in child_candidates:
                    shot_id = str(asset.shot_id)
                    usable = bool(asset.success and asset.local_path and os.path.exists(asset.local_path))
                    if usable or shot_id not in latest_by_shot:
                        latest_by_shot[shot_id] = asset
                child_candidates = list(latest_by_shot.values())
                successful_ids = {
                    str(asset.shot_id) for asset in child_candidates
                    if asset.success and asset.local_path and os.path.exists(asset.local_path)
                }
                if scene_expected:
                    missing = sorted(scene_expected - successful_ids)
                    if missing:
                        raise ValueError("子镜头未完成：" + "、".join(missing))
                failed = [
                    str(asset.shot_id) for asset in child_candidates
                    if not asset.success or not asset.local_path or not os.path.exists(asset.local_path)
                ]
                if failed:
                    raise ValueError("子镜头未完成：" + "、".join(sorted(set(failed))))
                candidates = [asset for asset in child_candidates if asset.success and asset.local_path and os.path.exists(asset.local_path)]
                candidates.sort(key=self._shot_order)
            else:
                candidates = [asset for asset in candidates if asset.success and asset.local_path and os.path.exists(asset.local_path)]
            for order, asset in enumerate(candidates, start=1):
                render_scene_id = int(source_scene.scene_id) * 1000 + order
                render_scene = source_scene.model_copy(update={
                    "scene_id": render_scene_id,
                    "transition_from_prev": source_scene.transition_from_prev if order == 1 else "cut",
                    "transition_duration_sec": source_scene.transition_duration_sec if order == 1 else 0.25,
                })
                expanded.append((render_scene, asset, int(source_scene.scene_id)))
        return expanded

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

        expanded = self._expanded_video_clips(script, assets)
        audio_map = {a.scene_id: a for a in (audio.scenes if audio else [])}

        clips = []
        for index, (scene, asset, parent_scene_id) in enumerate(expanded):
            try:
                clip = VideoFileClip(asset.local_path)
                if clip.duration > scene.duration_sec:
                    clip = clip.subclipped(0, scene.duration_sec)
            except Exception as e:
                print(f"  ⚠️ 无法加载 {asset.local_path}: {e}")
                continue

            scene_audio = audio_map.get(parent_scene_id)
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
            if index > 0 and scene.transition_from_prev == "fade":
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

            dialogue_clips = []
            for track in tracks:
                audio_clip = AudioFileClip(track.audio_path)
                start_ms = int(getattr(track, "start_ms", 0) or 0)
                dialogue_clips.append(audio_clip.with_start(max(0.0, start_ms / 1000.0)))
            speech = CompositeAudioClip(dialogue_clips).with_volume_scaled(
                scene_audio.volume_mix.get("tts", 1.0)
            )
            if speech.duration > clip.duration:
                # MoviePy holds the final frame for an extended clip, avoiding truncated dialogue.
                clip = clip.with_duration(speech.duration)
            if clip.audio:
                source = clip.audio.with_volume_scaled(1.0)
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
        subtitle_style: dict | None = None,
    ) -> str:
        """Render video boundaries first, then place the project audio timeline."""
        expanded = self._expanded_video_clips(script, assets)
        clips = [(scene, asset) for scene, asset, _parent_scene_id in expanded]
        parent_by_render_id = {scene.scene_id: parent_id for scene, _asset, parent_id in expanded}
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
            parent_id = parent_by_render_id[clips[0][0].scene_id]
            return self._mux_ffmpeg_audio_and_subtitles(
                script, audio, output_path, output_dir, subtitle_enabled,
                {parent_id: 0}, subtitle_mode,
                {parent_id: int(max(0.1, self._media_duration(source_path) or clips[0][0].duration_sec) * 1000)},
                subtitle_style,
            )

        rendered_video, scene_starts = self._render_transition_video(clips, output_path, output_dir)
        actual_durations = {
            scene.scene_id: max(0.1, self._media_duration(asset.local_path) or float(asset.duration_sec or scene.duration_sec))
            for scene, asset in clips
        }
        render_timeline = build_render_timeline([scene for scene, _ in clips], actual_durations)
        parent_starts: dict[int, int] = {}
        parent_ends: dict[int, int] = {}
        for scene, _asset in clips:
            parent_id = parent_by_render_id[scene.scene_id]
            start = render_timeline.scene_starts_ms[scene.scene_id]
            end = start + render_timeline.scene_durations_ms[scene.scene_id]
            parent_starts.setdefault(parent_id, start)
            parent_ends[parent_id] = max(parent_ends.get(parent_id, 0), end)
        parent_durations = {
            parent_id: max(1, parent_ends[parent_id] - parent_starts[parent_id])
            for parent_id in parent_starts
        }
        result_path = self._mux_ffmpeg_audio_and_subtitles(
            script, audio, rendered_video, output_dir, subtitle_enabled,
            {parent_id: parent_starts[parent_id] for parent_id in parent_starts},
            subtitle_mode, parent_durations, subtitle_style,
        )
        if result_path != output_path:
            os.replace(result_path, output_path)
        return output_path

    def _render_transition_video(self, clips, output_path: str, output_dir: Path) -> tuple[str, dict[int, int]]:
        """Compose video and its quiet source-audio bed with one FFmpeg graph."""
        temp_path = str(output_dir / f"transitions_{uuid.uuid4().hex[:8]}.mp4")
        command = ["-y"]
        durations = []
        for _, asset in clips:
            command.extend(["-i", asset.local_path])
            durations.append(max(0.1, self._media_duration(asset.local_path) or float(asset.duration_sec or 3)))

        timeline = build_render_timeline(
            [scene for scene, _ in clips],
            {scene.scene_id: duration for (scene, _), duration in zip(clips, durations)},
        )

        filters = []
        clip_audio = [self._has_audio_stream(asset.local_path) for _, asset in clips]
        for index, duration in enumerate(durations):
            filters.append(f"[{index}:v]setpts=PTS-STARTPTS,format=yuv420p[v{index}]")
            fade = min(0.08, duration / 2)
            if clip_audio[index]:
                audio_source = f"[{index}:a]"
            else:
                # Keep the audio timeline present even when one provider clip
                # has no native track; otherwise the old all-or-nothing gate
                # silently discarded audio from every other child shot.
                audio_source = f"anullsrc=r=44100:cl=stereo,atrim=duration={duration:.3f},"
            if clip_audio[index]:
                filters.append(
                    f"{audio_source}aformat=sample_rates=44100:channel_layouts=stereo,volume=0.22,"
                    f"afade=t=in:st=0:d={fade:.3f},afade=t=out:st={duration-fade:.3f}:d={fade:.3f}[a{index}]"
                )
            else:
                filters.append(
                    f"{audio_source}asetpts=PTS-STARTPTS,volume=0.22,"
                    f"afade=t=in:st=0:d={fade:.3f},afade=t=out:st={duration-fade:.3f}:d={fade:.3f}[a{index}]"
                )

        scene_starts = dict(timeline.scene_starts_ms)
        video_label = "v0"
        audio_label = "a0"
        for index in range(1, len(clips)):
            scene, _ = clips[index]
            transition = str(getattr(scene, "transition_from_prev", "cut") or "cut").lower()
            duration = timeline.overlap_ms.get(scene.scene_id, 0) / 1000.0
            next_video = f"vx{index}"
            if duration:
                effect = "fade" if transition == "fade" else "dissolve"
                offset = scene_starts[scene.scene_id] / 1000.0
                filters.append(f"[{video_label}][v{index}]xfade=transition={effect}:duration={duration:.3f}:offset={offset:.3f}[{next_video}]")
                next_audio = f"ax{index}"
                filters.append(f"[{audio_label}][a{index}]acrossfade=d={duration:.3f}:c1=tri:c2=tri[{next_audio}]")
                audio_label = next_audio
            else:
                filters.append(f"[{video_label}][v{index}]concat=n=2:v=1:a=0[{next_video}]")
                next_audio = f"ax{index}"
                filters.append(f"[{audio_label}][a{index}]concat=n=2:v=0:a=1[{next_audio}]")
                audio_label = next_audio
            video_label = next_video

        command.extend(["-filter_complex", ";".join(filters), "-map", f"[{video_label}]", "-map", f"[{audio_label}]"])
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
        subtitle_style: dict | None = None,
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
            command.extend(["-vf", subtitle_filter(subtitle_path, subtitle_style)])
        command.extend(["-c:v", "libx264", "-c:a", "aac", temp_path])
        result = run_ffmpeg(command, timeout=180)
        if result.returncode != 0 or not os.path.isfile(temp_path) or os.path.getsize(temp_path) == 0:
            raise RuntimeError(f"FFmpeg audio/subtitle pass failed: {result.stderr[-500:]}")
        os.replace(temp_path, output_path)
        return output_path

    def burn_subtitles_into_video(
        self,
        video_path: str,
        subtitle_path,
        output_path: str,
        subtitle_style: dict | None = None,
    ) -> str:
        """Burn an existing SRT onto an existing video without re-mixing audio.

        Assembly only burns subtitles as a side effect of its full render, so
        there was no way to caption a video that was already produced. This
        keeps the audio streams untouched and always writes a new file.
        """
        if not os.path.isfile(video_path):
            raise FileNotFoundError(f"video not found: {video_path}")
        if not subtitle_path or not os.path.isfile(str(subtitle_path)):
            raise FileNotFoundError(f"subtitle file not found: {subtitle_path}")
        command = [
            "-y", "-i", video_path,
            "-vf", subtitle_filter(subtitle_path, subtitle_style),
            "-map", "0:v:0", "-map", "0:a?",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
            "-c:a", "copy", "-pix_fmt", "yuv420p",
            output_path,
        ]
        result = run_ffmpeg(command, timeout=600)
        if result.returncode != 0 or not os.path.isfile(output_path) or os.path.getsize(output_path) == 0:
            raise RuntimeError(f"FFmpeg subtitle burn failed: {result.stderr[-500:]}")
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

    SUBTITLE_MODES = ("dialogue", "speaker", "narration", "all")

    def _script_dialogue_entries(self, scene, scene_start: float, subtitle_mode: str, scene_duration: float | None = None):
        """Time script dialogue across a scene's own window.

        Used when a scene has native provider dialogue and therefore no
        per-line audio tracks to measure against.
        """
        lines = [item for item in (getattr(scene, "dialogue", None) or []) if str(item.line or "").strip()]
        if not lines:
            return []
        scene_duration = max(1.0, float(scene_duration or scene.duration_sec or 0.0))
        slot = scene_duration / len(lines)
        entries = []
        for index, item in enumerate(lines):
            text = f"{item.character}: {item.line}".strip(": ") if subtitle_mode == "speaker" else item.line.strip()
            if subtitle_mode != "speaker" and self.is_voiceover_line(item):
                text = f"{self.NARRATION_PREFIX}：{item.line}".strip()
            start = scene_start + slot * index
            entries.append((start, min(start + max(1.0, slot - 0.15), scene_start + scene_duration), text))
        return entries

    def measured_scene_durations(self, script, audio_by_scene) -> dict[int, float]:
        """Measure each scene from its real active clips, in seconds.

        `scene.duration_sec` is a planning estimate: a scene planned at 7s can
        render as 12s once its child shots come back, so anything timed against
        the estimate drifts and overlaps. Callers that burn onto an existing
        file need the measured value instead.
        """
        durations: dict[int, float] = {}
        for scene in script.scenes:
            scene_audio = (audio_by_scene or {}).get(scene.scene_id)
            # Native-dialogue scenes carry the rendered video path, TTS scenes
            # carry an audio path; either gives a real length.
            candidates = []
            if scene_audio:
                candidates.append(getattr(scene_audio, "native_audio_path", ""))
                candidates.extend(getattr(scene_audio, "video_paths", None) or [])
            measured = 0.0
            for path in candidates:
                if path and os.path.isfile(str(path)):
                    measured = self._media_duration(str(path))
                    if measured > 0:
                        break
            durations[scene.scene_id] = measured or float(scene.duration_sec or 0.1)
        return durations

    def _measure_scene_starts(self, script, audio_by_scene) -> dict[int, int]:
        """Time each scene from the real active clip lengths, in milliseconds."""
        durations = self.measured_scene_durations(script, audio_by_scene)
        timeline = build_render_timeline(script.scenes, durations)
        return dict(timeline.scene_starts_ms)

    def _write_subtitle_file(self, script, audio_by_scene, output_dir: Path, scene_starts: dict[int, int] | None = None, subtitle_mode: str = "dialogue", narration_lines: dict[int, list] | None = None, scene_durations: dict[int, float] | None = None) -> Path | None:
        """Write an SRT from script dialogue, optionally including narration.

        Narration has no audio of its own in this pipeline, so it is emitted
        from text alone and spread across the scene it belongs to.
        `scene_durations` should carry measured lengths when the SRT must line
        up with an already-rendered video.
        """
        include_dialogue = subtitle_mode in {"dialogue", "speaker", "all"}
        include_narration = subtitle_mode in {"narration", "all"}
        if scene_starts is None:
            scene_starts = build_render_timeline(script.scenes).scene_starts_ms

        def scene_length(scene) -> float:
            measured = (scene_durations or {}).get(scene.scene_id)
            # The script estimate can be several seconds short of the rendered
            # scene, which is what makes subtitle slots overlap.
            return max(1.0, float(measured or scene.duration_sec or 0.0))

        entries = []
        cursor = 0.0
        for scene in script.scenes:
            scene_start = (scene_starts.get(scene.scene_id, int(cursor * 1000)) / 1000)
            scene_audio = audio_by_scene.get(scene.scene_id)
            if include_narration:
                entries.extend(
                    self._narration_entries(
                        scene, (narration_lines or {}).get(scene.scene_id) or [],
                        scene_start, scene_length(scene),
                    )
                )
            if not include_dialogue:
                cursor = max(cursor, scene_start + scene_length(scene))
                continue
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
                cursor = max(cursor, scene_start + scene_length(scene))
                continue
            tracks = (
                [item for item in (scene_audio.dialogue_audios if scene_audio else []) if item.success]
                if scene_audio and getattr(scene_audio, "native_audio_enabled", False)
                else self._dialogue_tracks(scene_audio)
            )
            if not tracks and getattr(scene, "dialogue", None):
                # Provider-native dialogue is baked into the video and produces
                # no dialogue_audios at all (unlike TTS lines). Without this the
                # whole native-dialogue project writes an empty SRT.
                entries.extend(self._script_dialogue_entries(scene, scene_start, subtitle_mode, scene_length(scene)))
                cursor = max(cursor, scene_start + scene_length(scene))
                continue
            for track in tracks:
                duration = float(track.duration_sec or 0.0) or max(1.0, len(track.text) / 4)
                if subtitle_mode == "speaker":
                    text = f"{track.character}: {track.text}".strip(": ")
                elif self.is_voiceover_line(track):
                    # Off-screen lines read as narration, not as a second take of
                    # the same line by a character who is visibly silent.
                    text = f"{self.NARRATION_PREFIX}：{track.text}".strip()
                else:
                    text = track.text.strip()
                start = scene_start + ((getattr(track, "start_ms", 0) or 0) / 1000)
                if text:
                    entries.append((start, start + duration, text))
                cursor = max(cursor, start + duration + (getattr(track, "tail_silence_ms", 0) / 1000))
            cursor = max(cursor, scene_start + scene_length(scene))
        if not entries:
            return None
        path = output_dir / f"subtitles_{uuid.uuid4().hex[:8]}.srt"
        with open(path, "w", encoding="utf-8") as handle:
            for index, (start, end, text) in enumerate(entries, start=1):
                handle.write(f"{index}\n{self._srt_timestamp(start)} --> {self._srt_timestamp(end)}\n{text}\n\n")
        return path

    NARRATION_PREFIX = "旁白"

    VOICEOVER_MARKERS = ("画外音", "内心独白", "独白", "voiceover", "voice_over")

    @classmethod
    def is_voiceover_line(cls, track) -> bool:
        """Detect a line the script marks as off-screen narration.

        The storyboard tags these in the emotion field (e.g. 「内心独白（画外音）」).
        They are currently burned as ordinary dialogue, which makes a character
        look like they repeat a joke their inner voice just told.
        """
        emotion = str(getattr(track, "emotion", "") or "").lower()
        return any(marker in emotion for marker in cls.VOICEOVER_MARKERS)

    def _narration_entries(self, scene, lines, scene_start: float, scene_duration: float | None = None):
        """Lay narration text into the scene's own window.

        Narration has no recorded audio, so it is timed by splitting the scene
        duration across its lines rather than measuring anything.
        """
        texts = []
        for item in lines:
            text = str(item or "").strip()
            if text:
                texts.append(text)
        if not texts:
            return []
        scene_duration = max(1.0, float(scene_duration or scene.duration_sec or 0.0))
        slot = scene_duration / len(texts)
        entries = []
        for index, text in enumerate(texts):
            start = scene_start + slot * index
            end = start + max(1.0, slot - 0.15)
            entries.append((start, min(end, scene_start + scene_duration), f"{self.NARRATION_PREFIX}：{text}"))
        return entries

    def _srt_timestamp(self, seconds: float) -> str:
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
