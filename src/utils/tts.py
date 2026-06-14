"""TTS utility — text-to-speech generation using Edge-TTS."""

import asyncio
import uuid
from pathlib import Path
from src.config import OUTPUT_DIR
from src.utils.video import run_ffmpeg_shell


# Voice mapping for different character types
CHARACTER_VOICES = {
    # Male voices
    "男主": "zh-CN-YunxiNeural",       # 青年男声
    "霸道总裁": "zh-CN-YunxiNeural",
    "男配角": "zh-CN-YunyangNeural",    # 新闻男声
    "父亲": "zh-CN-YunyangNeural",
    "反派男": "zh-CN-YunyangNeural",
    # Female voices
    "女主": "zh-CN-XiaoxiaoNeural",     # 温柔女声
    "甜美女主": "zh-CN-XiaoxiaoNeural",
    "女配角": "zh-CN-XiaoyiNeural",     # 活泼女声
    "母亲": "zh-CN-XiaoxiaoNeural",
    "反派女": "zh-CN-XiaoyiNeural",
    # Default fallback
    "旁白": "zh-CN-YunxiNeural",
}


async def generate_tts(
    text: str,
    voice: str = "zh-CN-YunxiNeural",
    output_path: str | None = None,
) -> str:
    """Generate TTS audio using Edge-TTS (free, no API key needed).

    Args:
        text: The text to speak.
        voice: Edge-TTS voice ID (e.g. "zh-CN-YunxiNeural").
        output_path: Optional output file path. Auto-generated if not provided.

    Returns:
        Path to the generated audio file (.mp3 or .wav).
    """
    if output_path is None:
        filename = f"tts_{uuid.uuid4().hex[:8]}.mp3"
        output_path = str(OUTPUT_DIR / filename)

    try:
        import edge_tts

        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(output_path)

        return output_path
    except ImportError:
        # Fallback: create a silent audio file using FFmpeg
        return await _generate_silent_tts(text, output_path)
    except Exception as e:
        print(f"  ⚠️ TTS生成失败 ({voice}): {e}")
        return await _generate_silent_tts(text, output_path)


async def _generate_silent_tts(text: str, output_path: str) -> str:
    """Generate a silent placeholder when TTS is unavailable."""
    # Estimate duration: ~4 chars per second for Chinese
    duration = max(1, len(text) / 4)

    cmd = (
        f"ffmpeg -y -f lavfi -i anullsrc=r=44100:cl=mono -t {duration:.1f}"
        f" -c:a libmp3lame -q:a 9 \"{output_path}\" 2>/dev/null"
    )
    run_ffmpeg_shell(cmd, timeout=10)
    return output_path


def get_voice_for_character(character_name: str) -> str:
    """Get the best TTS voice for a character name.

    Args:
        character_name: Character name or role (e.g. "男主", "女主", "反派男").

    Returns:
        Edge-TTS voice ID.
    """
    # Direct match
    if character_name in CHARACTER_VOICES:
        return CHARACTER_VOICES[character_name]

    # Fuzzy match by keywords
    name_lower = character_name.lower()
    if any(k in name_lower for k in ["女", "姑娘", "公主", "夫人", "妈妈", "少女"]):
        return "zh-CN-XiaoxiaoNeural"
    if any(k in name_lower for k in ["男", "先生", "总裁", "父亲", "爸爸", "少年"]):
        return "zh-CN-YunxiNeural"
    if any(k in name_lower for k in ["反派", "坏人", "杀手", "黑帮"]):
        return "zh-CN-YunyangNeural"

    # Default
    return "zh-CN-YunxiNeural"
