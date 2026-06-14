"""Mock video generation client — for development and testing without API costs."""

import asyncio
import uuid
from pathlib import Path

from .base import VideoAPIClient, VideoGenResult
from src.config import OUTPUT_DIR
from src.utils.video import run_ffmpeg_shell


class MockVideoClient(VideoAPIClient):
    """Mock video client that generates placeholder color videos.

    Uses FFmpeg (bundled via imageio_ffmpeg) to create a solid-color video
    with scene text overlay. No system FFmpeg install needed.
    """

    @property
    def provider_name(self) -> str:
        return "mock"

    async def generate_video(
        self,
        prompt: str,
        negative_prompt: str = "",
        duration_sec: int = 5,
        seed: int | None = None,
        aspect_ratio: str = "9:16",
        motion_strength: float = 0.7,
    ) -> VideoGenResult:
        """Generate a mock video with a solid color and text overlay."""
        shot_id = uuid.uuid4().hex[:8]
        filename = f"mock_shot_{shot_id}.mp4"
        output_path = OUTPUT_DIR / filename

        # Simulate generation time
        await asyncio.sleep(0.5)

        try:
            # Extract a short label from the prompt (aggressively sanitize for shell safety)
            import re
            label = re.sub(r'[^a-zA-Z0-9\s\-_,.()]', '', prompt[:80])[:40]

            # Parse aspect ratio
            w, h = (1080, 1920) if aspect_ratio == "9:16" else (1920, 1080)

            # Use seed to determine color (deterministic per seed)
            seed_val = seed or hash(prompt) % 1000000
            r = (seed_val * 37) % 200 + 30
            g = (seed_val * 73) % 200 + 30
            b = (seed_val * 131) % 200 + 30

            # Generate a colored video with text using FFmpeg
            color_hex = f"{r:02x}{g:02x}{b:02x}"
            cmd = (
                f"ffmpeg -y -f lavfi -i color=c=0x{color_hex}:s={w}x{h}:d={duration_sec}"
                f" -f lavfi -i anullsrc=r=44100:cl=mono"
                f" -vf \"drawtext=text='Scene {label[:40]}':fontsize=48:fontcolor=white:"
                f"x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.5:boxborderw=10\""
                f" -c:v libx264 -preset ultrafast -c:a aac -shortest"
                f" \"{output_path}\" 2>/dev/null"
            )
            run_ffmpeg_shell(cmd, timeout=30)

            return VideoGenResult(
                success=True,
                video_url=f"file://{output_path}",
                local_path=str(output_path),
                duration_sec=float(duration_sec),
                api_cost=0.0,
            )
        except Exception as e:
            return VideoGenResult(
                success=False,
                error_message=str(e),
                api_cost=0.0,
            )

    async def get_cost_estimate(self, duration_sec: int) -> float:
        return 0.0  # Mock is free
