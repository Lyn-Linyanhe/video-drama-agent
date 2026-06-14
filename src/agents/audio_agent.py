"""Audio Agent — generates TTS voiceover, matches BGM, and adds sound effects."""

import asyncio
from pathlib import Path

from src.schemas.script import ScriptBreakdown
from src.schemas.assets import GeneratedAssets
from src.schemas.audio import AudioAssets, SceneAudio
from src.utils.tts import generate_tts, get_voice_for_character
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

    async def run(
        self, script: ScriptBreakdown, assets: GeneratedAssets
    ) -> AudioAssets:
        """Generate all audio for the drama.

        Args:
            script: The script breakdown with dialogue.
            assets: The generated video assets (for timing reference).

        Returns:
            AudioAssets with TTS, BGM, and SFX paths.
        """
        scene_audio_list = []
        all_tasks = []

        # Generate audio for each scene that has dialogue
        for scene in script.scenes:
            scene_audio = SceneAudio(scene_id=scene.scene_id)
            scene_tasks = []

            # Generate TTS for each dialogue line
            for dialogue in scene.dialogue:
                voice = get_voice_for_character(dialogue.character)
                task = self._generate_dialogue_tts(
                    scene.scene_id,
                    dialogue.character,
                    dialogue.line,
                    voice,
                )
                scene_tasks.append(task)

                # Track character→voice mapping
                if dialogue.character not in scene_audio.character_voice_map:
                    scene_audio.character_voice_map[dialogue.character] = voice

            # Collect dialogue text for subtitle reference
            scene_audio.tts_text = " ".join(
                f"{d.character}: {d.line}" for d in scene.dialogue
            )

            all_tasks.append((scene_audio, scene_tasks))
            scene_audio_list.append(scene_audio)

        # Execute all TTS tasks concurrently
        flat_tasks = []
        task_index = []
        for scene_audio, tasks in all_tasks:
            for t in tasks:
                task_index.append(scene_audio)
                flat_tasks.append(t)

        if flat_tasks:
            results = await asyncio.gather(*flat_tasks, return_exceptions=True)
            for scene_audio, result in zip(task_index, results):
                if isinstance(result, str):
                    scene_audio.tts_audio_path = result
                elif isinstance(result, Exception):
                    print(f"  ⚠️ TTS task failed: {result}")

        # Generate BGM (mock placeholder for now)
        overall_mood = self._determine_overall_mood(script)
        bgm_path = await self._generate_mock_bgm(overall_mood)

        return AudioAssets(
            scenes=scene_audio_list,
            bgm_path=bgm_path,
            bgm_mood=overall_mood,
        )

    async def _generate_dialogue_tts(
        self,
        scene_id: int,
        character: str,
        text: str,
        voice: str,
    ) -> str:
        """Generate TTS for a single dialogue line."""
        if not text.strip():
            return ""

        filename = f"scene{scene_id:02d}_{character}_{hash(text) % 10000:04d}.mp3"
        output_path = str(self.output_dir / filename)

        return await generate_tts(text, voice, output_path)

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

    async def _generate_mock_bgm(self, mood: str) -> str:
        """Generate a silent BGM placeholder.

        In production, this would call a music generation API (e.g. Suno, Mubert)
        or select from a licensed music library.
        """
        bgm_path = str(self.output_dir / f"bgm_{mood}.mp3")

        # Generate a short silent audio as placeholder
        cmd = (
            f"ffmpeg -y -f lavfi -i anullsrc=r=44100:cl=mono -t 60"
            f" -c:a libmp3lame -q:a 9 \"{bgm_path}\" 2>/dev/null"
        )
        run_ffmpeg_shell(cmd, timeout=10)

        return bgm_path
