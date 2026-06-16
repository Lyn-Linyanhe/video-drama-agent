"""Abstract base class for video generation API clients."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class VideoGenResult:
    """Result of a single video generation request."""
    success: bool
    video_url: str = ""
    local_path: str | None = None
    duration_sec: float = 0.0
    error_message: str = ""
    api_cost: float = 0.0
    reference_image_used: str = ""  # Path to reference image if image-to-video mode


class VideoAPIClient(ABC):
    """Abstract interface for video generation API clients.

    All video API implementations (Kling, Runway, Pika, Mock) must
    implement this interface. This allows the VideoGenerationAgent
    to work with any API without coupling.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable name of this API provider."""
        ...

    @abstractmethod
    async def generate_video(
        self,
        prompt: str,
        negative_prompt: str = "",
        duration_sec: int = 5,
        seed: int | None = None,
        aspect_ratio: str = "9:16",
        motion_strength: float = 0.7,
        image_reference: str | None = None,
    ) -> VideoGenResult:
        """Submit a video generation request and wait for completion.

        Args:
            prompt: Text prompt describing the video content.
            negative_prompt: Things to avoid in the video.
            duration_sec: Target video duration.
            seed: Random seed for reproducibility.
            aspect_ratio: Video aspect ratio (e.g. "9:16").
            motion_strength: How much motion (0-1).
            image_reference: Optional path to a reference image for
                image-to-video generation (character portrait, scene bg).
                When provided, the API should use this as a visual anchor.

        Returns:
            VideoGenResult with success status and video URL or error.
        """
        ...

    @abstractmethod
    async def get_cost_estimate(self, duration_sec: int) -> float:
        """Estimate the cost for generating a video of given duration.

        Args:
            duration_sec: Target video duration.

        Returns:
            Estimated cost in USD.
        """
        ...
