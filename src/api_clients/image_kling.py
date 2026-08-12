"""Kling AI image generation client — API Key Bearer token auth.

Reuses the same authentication pattern as KlingVideoClient:
- New-style API Key (api-key-kling-...) → direct Bearer token
- Legacy AK/SK → JWT Bearer token

Async workflow: submit task → poll until SUCCEEDED/FAILED → download image.
"""

import asyncio
import time
import uuid
from pathlib import Path

import httpx
from PIL import Image

from src.services.generation_hooks import get_generation_hooks
import jwt

from .image_base import ImageAPIClient, ImageGenResult
from src.config import OUTPUT_DIR

# ============================================================
# Kling image API endpoints
# ============================================================
KLING_BASE_URL = "https://api-beijing.klingai.com"
KLING_IMG_CREATE = f"{KLING_BASE_URL}/v1/images/generations"
KLING_IMG_QUERY = f"{KLING_BASE_URL}/v1/images/generations"  # + /{task_id}

# Polling config
POLL_INTERVAL = 3        # seconds between status checks
POLL_MAX_WAIT = 300      # max total wait (5 min)
JWT_TTL = 1800           # JWT token lifetime (30 min)


class KlingImageClient(ImageAPIClient):
    """Image generation via Kling AI (可灵) API.

    Supports two auth methods:
    - API Key (recommended): ``api_key`` → direct Bearer token
    - AK/SK (legacy): ``access_key`` + ``secret_key`` → JWT Bearer token

    Async workflow: submit task → poll until SUCCEEDED → download to local.
    """

    def __init__(
        self,
        api_key: str = "",
        access_key: str = "",
        secret_key: str = "",
        model: str = "kling-v1",
        base_url: str = KLING_BASE_URL,
    ):
        self.api_key = api_key
        self.access_key = access_key
        self.secret_key = secret_key
        self.model = model
        self.base_url = base_url

        self._token: str | None = None
        self._token_expiry: float = 0.0

    # ---- ImageAPIClient interface -----------------------------------

    @property
    def provider_name(self) -> str:
        return "kling"

    async def generate_image(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = 1024,
        height: int = 1024,
        seed: int | None = None,
    ) -> ImageGenResult:
        """Submit an image generation task, poll for completion, download result.

        Returns ImageGenResult with local_path pointing to the downloaded png.
        """
        try:
            # 1. Submit task
            task_id = await self._submit_task(
                prompt=prompt,
                negative_prompt=negative_prompt,
                width=width,
                height=height,
                seed=seed,
            )
            hooks = get_generation_hooks()
            hooks.provider_task(self.provider_name, task_id)
            hooks.check_cancelled()

            # 2. Poll until done
            image_url = await self._poll_task(task_id)

            # 3. Download to local
            hooks.progress("downloading", 0, 1, "正在下载图片")
            hooks.check_cancelled()
            local_path = await self._download_image(image_url, task_id)
            hooks.progress("downloading", 1, 1, "图片下载完成")
            with Image.open(local_path) as downloaded:
                actual_width, actual_height = downloaded.size

            return ImageGenResult(
                success=True,
                image_url=image_url,
                local_path=local_path,
                width=actual_width,
                height=actual_height,
                seed=seed or 0,
                api_cost=self._estimate_cost(width, height),
            )

        except Exception as e:
            return ImageGenResult(
                success=False,
                error_message=str(e),
                api_cost=0.0,
            )

    async def get_cost_estimate(self, width: int, height: int) -> float:
        return self._estimate_cost(width, height)

    # ---- Auth -------------------------------------------------------

    def _get_token(self) -> str:
        """Return auth token: API key directly, or JWT from AK/SK pair."""
        # New-style API key — use directly, no expiry to track
        if self.api_key:
            return self.api_key

        # Legacy AK/SK → JWT, refresh if expired (with 60s buffer)
        now = time.time()
        if self._token is None or now > self._token_expiry - 60:
            headers = {"alg": "HS256", "typ": "JWT"}
            payload = {
                "iss": self.access_key,
                "exp": int(now) + JWT_TTL,
                "nbf": int(now) - 5,
            }
            self._token = jwt.encode(payload, self.secret_key, headers=headers)
            self._token_expiry = now + JWT_TTL
        return self._token

    def _auth_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._get_token()}",
            "Content-Type": "application/json",
        }

    # ---- Submit -----------------------------------------------------

    async def _submit_task(
        self,
        prompt: str,
        negative_prompt: str,
        width: int,
        height: int,
        seed: int | None,
    ) -> str:
        """Submit an image generation task. Returns task_id."""
        # Determine aspect ratio from dimensions
        if abs(width / height - 1.0) < 0.05:
            aspect_ratio = "1:1"
        elif width > height:
            aspect_ratio = "16:9"
        else:
            aspect_ratio = "9:16"

        body: dict = {
            "model_name": self.model,
            "prompt": prompt,
            "n": 1,
            "aspect_ratio": aspect_ratio,
        }

        if negative_prompt:
            body["negative_prompt"] = negative_prompt

        if seed is not None and seed > 0:
            body["seed"] = seed

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                KLING_IMG_CREATE,
                json=body,
                headers=self._auth_headers(),
            )
            data = resp.json()

        if resp.status_code != 200 or data.get("code") != 0:
            msg = data.get("message", data)
            raise RuntimeError(f"Kling image submit failed [{resp.status_code}]: {msg}")

        task_id = data["data"]["task_id"]
        print(f"  🎨 Kling image task submitted: {task_id}")
        return task_id

    # ---- Poll -------------------------------------------------------

    async def _poll_task(self, task_id: str) -> str:
        """Poll task status until SUCCEEDED. Returns image download URL."""
        query_url = f"{KLING_IMG_QUERY}/{task_id}"
        start = time.time()

        async with httpx.AsyncClient(timeout=15) as client:
            while True:
                elapsed = time.time() - start
                hooks = get_generation_hooks()
                hooks.check_cancelled()
                hooks.progress("waiting_provider", int(elapsed), POLL_MAX_WAIT, "等待可灵生成图片")
                if elapsed > POLL_MAX_WAIT:
                    raise TimeoutError(f"Kling image task {task_id} timed out after {POLL_MAX_WAIT}s")

                resp = await client.get(query_url, headers=self._auth_headers())
                data = resp.json()

                if resp.status_code != 200 or data.get("code") != 0:
                    msg = data.get("message", data)
                    raise RuntimeError(f"Kling image query failed [{resp.status_code}]: {msg}")

                status = data["data"]["task_status"]
                print(f"  ⏳ Kling image {task_id}: {status} ({elapsed:.0f}s)")

                if status == "succeed":
                    images = data["data"]["task_result"]["images"]
                    if not images:
                        raise RuntimeError("Kling image task succeeded but no images returned")
                    return images[0]["url"]

                if status == "failed":
                    err = data["data"].get("task_status_msg", "unknown error")
                    raise RuntimeError(f"Kling image task {task_id} failed: {err}")

                # Still pending/running — wait and retry
                await asyncio.sleep(POLL_INTERVAL)

    # ---- Download ---------------------------------------------------

    async def _download_image(self, url: str, task_id: str) -> str:
        """Download generated image to local output dir."""
        filename = f"kling_img_{task_id[-8:]}_{uuid.uuid4().hex[:4]}.png"
        output_path = OUTPUT_DIR / filename

        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            output_path.write_bytes(resp.content)

        print(f"  📥 Image downloaded: {output_path}")
        return str(output_path)

    # ---- Cost -------------------------------------------------------

    @staticmethod
    def _estimate_cost(width: int, height: int) -> float:
        """Rough estimate: Kling image ≈ ¥0.2 = ~$0.028 per image."""
        return 0.028
