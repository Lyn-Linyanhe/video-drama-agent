"""Assembly Agent — composites video clips, adds subtitles, transitions, and audio."""

import os
from pathlib import Path
import uuid

from src.schemas.script import ScriptBreakdown
from src.schemas.assets import GeneratedAssets
from src.schemas.audio import AudioAssets
from src.config import OUTPUT_DIR, OUTPUT_FPS, OUTPUT_CODEC, OUTPUT_BITRATE
from src.utils.video import run_ffmpeg_shell


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
    ) -> str:
        """Assemble all assets into the final video file.

        Args:
            script: The script breakdown.
            assets: Generated video clips.
            audio: Audio assets (TTS, BGM).

        Returns:
            Path to the final assembled video file.
        """
        output_filename = f"drama_{uuid.uuid4().hex[:8]}.mp4"
        output_path = str(self.output_dir / output_filename)

        try:
            return await self._assemble_with_moviepy(script, assets, audio, output_path)
        except ImportError:
            # Fallback: simple FFmpeg concatenation
            return await self._assemble_with_ffmpeg(script, assets, output_path)
        except Exception as e:
            print(f"  ⚠️ MoviePy 合成失败: {e}, 尝试 FFmpeg 回退...")
            return await self._assemble_with_ffmpeg(script, assets, output_path)

    async def _assemble_with_moviepy(
        self,
        script: ScriptBreakdown,
        assets: GeneratedAssets,
        audio: AudioAssets,
        output_path: str,
    ) -> str:
        """Assemble using MoviePy with transitions and subtitles."""
        from moviepy import VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip, concatenate_videoclips
        from moviepy.video.fx import CrossFadeIn, CrossFadeOut

        asset_map = {a.scene_id: a for a in assets.shots}
        audio_map = {a.scene_id: a for a in audio.scenes}

        clips = []
        for i, scene in enumerate(script.scenes):
            asset = asset_map.get(scene.scene_id)
            if asset is None or not asset.success or not asset.local_path:
                # Generate a text placeholder for missing scenes
                clip = self._create_text_clip(
                    f"[场景 {scene.scene_id}]\n{scene.action_description}",
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

            # Add scene audio (TTS)
            scene_audio = audio_map.get(scene.scene_id)
            if scene_audio and scene_audio.tts_audio_path and os.path.exists(scene_audio.tts_audio_path):
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
            if subtitle_text:
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

        # Add BGM as background
        if audio.bgm_path and os.path.exists(audio.bgm_path):
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

    async def _assemble_with_ffmpeg(
        self,
        script: ScriptBreakdown,
        assets: GeneratedAssets,
        output_path: str,
    ) -> str:
        """Simple FFmpeg concatenation fallback (no transitions/subtitles)."""
        asset_map = {a.scene_id: a for a in assets.shots}

        # Build concat file list
        concat_list_path = str(self.output_dir / "concat_list.txt")
        with open(concat_list_path, "w") as f:
            for scene in script.scenes:
                asset = asset_map.get(scene.scene_id)
                if asset and asset.success and asset.local_path:
                    f.write(f"file '{asset.local_path}'\n")

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
        run_ffmpeg_shell(cmd, timeout=120)

        return output_path

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
