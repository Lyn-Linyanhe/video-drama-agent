"""Abstract base class for image generation API clients."""

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class ImageGenResult:
    """Result of a single image generation request."""

    success: bool
    image_url: str = ""
    local_path: str | None = None
    width: int = 1024
    height: int = 1024
    seed: int = 0
    error_message: str = ""
    api_cost: float = 0.0


class ImageAPIClient(ABC):
    """Abstract interface for image generation API clients.

    All image API implementations (OpenAI DALL-E, Stability AI, Mock)
    must implement this interface. Follows the same pattern as VideoAPIClient.
    """

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable name of this API provider."""
        ...

    @abstractmethod
    async def generate_image(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        seed: int | None = None,
    ) -> ImageGenResult:
        """Submit an image generation request and wait for completion.

        Args:
            prompt: Text prompt describing the image content.
            negative_prompt: Things to avoid in the image.
            width: Image width in pixels.
            height: Image height in pixels.
            seed: Random seed for reproducibility.

        Returns:
            ImageGenResult with success status and image path or error.
        """
        ...

    @abstractmethod
    async def get_cost_estimate(self, width: int, height: int) -> float:
        """Estimate the cost for generating an image of given dimensions.

        Args:
            width: Image width in pixels.
            height: Image height in pixels.

        Returns:
            Estimated cost in USD.
        """
        ...
