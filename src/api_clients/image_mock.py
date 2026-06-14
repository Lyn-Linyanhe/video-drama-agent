"""Mock image generation client — for development and testing without API costs."""

import asyncio
import uuid
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .image_base import ImageAPIClient, ImageGenResult
from src.config import OUTPUT_DIR


class MockImageClient(ImageAPIClient):
    """Mock image client that generates colored placeholder images.

    Uses Pillow to create a solid-color image with character/scene label text.
    No API key or network call needed.
    """

    @property
    def provider_name(self) -> str:
        return "mock"

    async def generate_image(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        seed: int | None = None,
    ) -> ImageGenResult:
        """Generate a mock image with a solid color and text label."""
        img_id = uuid.uuid4().hex[:8]
        filename = f"mock_img_{img_id}.png"
        output_path = OUTPUT_DIR / filename

        # Simulate generation time
        await asyncio.sleep(0.3)

        try:
            # Derive a deterministic color from the seed or prompt hash
            seed_val = seed or hash(prompt) % 1000000
            r = (seed_val * 37) % 200 + 30
            g = (seed_val * 73) % 200 + 30
            b = (seed_val * 131) % 200 + 30

            # Create a solid-color image
            img = Image.new("RGB", (width, height), (r, g, b))
            draw = ImageDraw.Draw(img)

            # Draw border
            border_color = (min(r + 50, 255), min(g + 50, 255), min(b + 50, 255))
            border_w = max(width, height) // 50
            draw.rectangle(
                [border_w, border_w, width - border_w, height - border_w],
                outline=border_color,
                width=border_w // 2,
            )

            # Draw label text (first ~60 chars of prompt)
            label = prompt[:60].replace("'", "").replace('"', "")
            font_size = max(width, height) // 20

            # Try to load a common font; fall back to default
            try:
                font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", font_size)
            except (OSError, IOError):
                try:
                    font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font_size)
                except (OSError, IOError):
                    font = ImageFont.load_default()

            # Word-wrap label
            text_color = "white" if (r + g + b) / 3 < 150 else "black"
            lines = self._wrap_text(label, font, draw, width - 40)

            total_text_h = sum(
                draw.textbbox((0, 0), line, font=font)[3] - draw.textbbox((0, 0), line, font=font)[1]
                for line in lines
            )
            y = (height - total_text_h) // 2
            for line in lines:
                bbox = draw.textbbox((0, 0), line, font=font)
                tw = bbox[2] - bbox[0]
                th = bbox[3] - bbox[1]
                x = (width - tw) // 2
                draw.text((x, y), line, fill=text_color, font=font)
                y += th + 5

            img.save(str(output_path), "PNG")

            return ImageGenResult(
                success=True,
                image_url=f"file://{output_path}",
                local_path=str(output_path),
                width=width,
                height=height,
                seed=seed_val,
                api_cost=0.0,
            )
        except Exception as e:
            return ImageGenResult(
                success=False,
                error_message=str(e),
                api_cost=0.0,
            )

    async def get_cost_estimate(self, width: int, height: int) -> float:
        return 0.0  # Mock is free

    @staticmethod
    def _wrap_text(text: str, font, draw, max_width: int) -> list[str]:
        """Simple word-wrap for text lines."""
        lines = []
        current_line = ""
        for char in text:
            test_line = current_line + char
            bbox = draw.textbbox((0, 0), test_line, font=font)
            if bbox[2] - bbox[0] > max_width:
                if current_line:
                    lines.append(current_line)
                current_line = char
            else:
                current_line = test_line
        if current_line:
            lines.append(current_line)
        return lines if lines else [text]
