"""Video utility functions — FFmpeg wrapper and helpers."""

import subprocess
import os


def get_ffmpeg_path() -> str:
    """Get the path to FFmpeg binary.

    Tries in order:
    1. imageio_ffmpeg (bundled with MoviePy) — no system install needed
    2. System ffmpeg on PATH
    """
    # Try imageio_ffmpeg first (already installed with MoviePy)
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        pass

    # Fall back to system ffmpeg
    return "ffmpeg"


def run_ffmpeg(cmd_parts: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    """Run an FFmpeg command with the detected binary.

    Args:
        cmd_parts: FFmpeg arguments as a list, e.g. ["-i", "input.mp4", "output.mp4"]
        timeout: Timeout in seconds.

    Returns:
        CompletedProcess result.
    """
    ffmpeg = get_ffmpeg_path()
    full_cmd = [ffmpeg] + cmd_parts
    return subprocess.run(
        full_cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_ffmpeg_shell(cmd_str: str, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run an FFmpeg command string (for complex pipelines).

    Args:
        cmd_str: Full FFmpeg command string.
        timeout: Timeout in seconds.

    Returns:
        CompletedProcess result.
    """
    ffmpeg = get_ffmpeg_path()
    # Replace "ffmpeg" at the start with the actual path
    if cmd_str.startswith("ffmpeg "):
        cmd_str = cmd_str.replace("ffmpeg ", f'"{ffmpeg}" ', 1)
    return subprocess.run(
        cmd_str,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
