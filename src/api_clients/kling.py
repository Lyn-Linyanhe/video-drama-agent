"""Kling AI video generation client — JWT auth, async task polling, download."""

import asyncio
import time
import uuid
from pathlib import Path

import httpx
import jwt

from .base import VideoAPIClient, VideoGenResult
from src.config import OUTPUT_DIR

# ============================================================
# Kling API endpoints
# ============================================================
KLING_BASE_URL = "https://api-beijing.klingai.com"
KLING_T2V_URL = f"{KLING_BASE_URL}/v1/videos/text2video"       # submit
KLING_I2V_URL = f"{KLING_BASE_URL}/v1/videos/image2video"      # image-to-video
KLING_QUERY_URL = f"{KLING_BASE_URL}/v1/videos/text2video"     # + /{task_id}
KLING_IMAGE_UPLOAD_URL = f"{KLING_BASE_URL}/v1/images/upload"  # upload reference image

# Polling config
POLL_INTERVAL = 5       # seconds between status checks
POLL_MAX_WAIT = 600     # max total wait (10 min)
JWT_TTL = 1800          # JWT token lifetime (30 min)


class KlingVideoClient(VideoAPIClient):
    """Video generation via Kling AI (可灵) API.

    Uses AK/SK → JWT Bearer token auth.
    Async workflow: submit task → poll until SUCCEEDED/FAILED → download video.
    """

    def __init__(
        self,
        access_key: str,
        secret_key: str,
        model: str = "kling-v2.6-t2v",
        mode: str = "std",          # "std" (720p) or "pro" (1080p)
        base_url: str = KLING_BASE_URL,
    ):
        self.access_key = access_key
        self.secret_key = secret_key
        self.model = model
        self.mode = mode
        self.base_url = base_url

        self._token: str | None = None
        self._token_expiry: float = 0.0

    # ---- VideoAPIClient interface -----------------------------------

    @property
    def provider_name(self) -> str:
        return "kling"

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
        """Submit a generation task, poll for completion, download result.

        When image_reference is provided (path to a local image file),
        the image-to-video endpoint is used instead of text-to-video.
        The image is uploaded to Kling first, then used as visual anchor.

        Returns VideoGenResult with local_path pointing to the downloaded mp4.
        """
        try:
            # Upload reference image if provided
            image_url = ""
            if image_reference:
                image_url = await self._upload_image(image_reference)

            # 1. Submit task (text2video or image2video)
            task_id = await self._submit_task(
                prompt=prompt,
                negative_prompt=negative_prompt,
                duration=duration_sec,
                aspect_ratio=aspect_ratio,
                seed=seed,
                image_url=image_url,
            )

            # 2. Poll until done
            video_url = await self._poll_task(task_id)

            # 3. Download to local
            local_path = await self._download_video(video_url, task_id, duration_sec)

            return VideoGenResult(
                success=True,
                video_url=video_url,
                local_path=local_path,
                duration_sec=float(duration_sec),
                api_cost=self._estimate_cost(duration_sec),
                reference_image_used=image_reference or "",
            )

        except Exception as e:
            return VideoGenResult(
                success=False,
                error_message=str(e),
            )

    async def get_cost_estimate(self, duration_sec: int) -> float:
        return self._estimate_cost(duration_sec)

    # ---- Auth -------------------------------------------------------

    def _get_token(self) -> str:
        """Return a valid JWT token, refreshing if expired (with 60s buffer)."""
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
        duration: int,
        aspect_ratio: str,
        seed: int | None,
        image_url: str = "",
    ) -> str:
        """Submit a generation task. Returns task_id.

        Uses image2video endpoint when image_url is provided,
        text2video endpoint otherwise.
        """
        # Kling API only accepts 5 or 10
        duration = 5 if duration <= 7 else 10

        body: dict = {
            "model_name": self.model,
            "mode": self.mode,
            "duration": str(duration),
            "aspect_ratio": aspect_ratio,
        }

        if image_url:
            # Image-to-video mode: use image as visual anchor
            body["image"] = image_url
            body["image_tail"] = prompt  # prompt describes motion/scene context
            endpoint = KLING_I2V_URL
            mode_label = "i2v"
        else:
            # Text-to-video mode
            body["prompt"] = prompt
            endpoint = KLING_T2V_URL
            mode_label = "t2v"

        if negative_prompt:
            body["negative_prompt"] = negative_prompt
        if seed is not None:
            body["seed"] = seed

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                endpoint,
                json=body,
                headers=self._auth_headers(),
            )
            data = resp.json()

        if resp.status_code != 200 or data.get("code") != 0:
            msg = data.get("message", data)
            raise RuntimeError(f"Kling submit ({mode_label}) failed [{resp.status_code}]: {msg}")

        task_id = data["data"]["task_id"]
        print(f"  🎬 Kling task submitted ({mode_label}): {task_id}")
        return task_id

    # ---- Poll -------------------------------------------------------

    async def _poll_task(self, task_id: str) -> str:
        """Poll task status until SUCCEEDED. Returns download URL."""
        query_url = f"{KLING_QUERY_URL}/{task_id}"
        start = time.time()

        async with httpx.AsyncClient(timeout=15) as client:
            while True:
                elapsed = time.time() - start
                if elapsed > POLL_MAX_WAIT:
                    raise TimeoutError(f"Kling task {task_id} timed out after {POLL_MAX_WAIT}s")

                resp = await client.get(query_url, headers=self._auth_headers())
                data = resp.json()

                if resp.status_code != 200 or data.get("code") != 0:
                    msg = data.get("message", data)
                    raise RuntimeError(f"Kling query failed [{resp.status_code}]: {msg}")

                status = data["data"]["task_status"]
                print(f"  ⏳ Kling {task_id}: {status} ({elapsed:.0f}s)")

                if status == "succeed":
                    videos = data["data"]["task_result"]["videos"]
                    if not videos:
                        raise RuntimeError("Kling task succeeded but no videos returned")
                    return videos[0]["url"]

                if status == "failed":
                    err = data["data"].get("task_status_msg", "unknown error")
                    raise RuntimeError(f"Kling task {task_id} failed: {err}")

                # Still pending/running — wait and retry
                await asyncio.sleep(POLL_INTERVAL)

    # ---- Upload ------------------------------------------------------

    async def _upload_image(self, image_path: str) -> str:
        """Upload a local image to Kling for use as image-to-video reference.

        Returns the uploaded image URL.
        """
        import os
        from pathlib import Path
        import base64

        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Reference image not found: {image_path}")

        # Read and encode the image
        image_data = path.read_bytes()
        ext = path.suffix.lower().lstrip('.')
        mime_types = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
        mime_type = mime_types.get(ext, "image/png")

        b64 = base64.b64encode(image_data).decode("ascii")
        data_uri = f"data:{mime_type};base64,{b64}"

        body = {
            "model_name": self.model,
            "image": data_uri,
        }

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                KLING_IMAGE_UPLOAD_URL,
                json=body,
                headers=self._auth_headers(),
            )
            data = resp.json()

        if resp.status_code != 200 or data.get("code") != 0:
            msg = data.get("message", data)
            raise RuntimeError(f"Kling image upload failed [{resp.status_code}]: {msg}")

        image_url = data["data"]["url"]
        print(f"  📤 Image uploaded: {image_path} → {image_url[:60]}...")
        return image_url

    # ---- Download ---------------------------------------------------

    async def _download_video(self, url: str, task_id: str, duration: int) -> str:
        """Download generated video to local output dir."""
        filename = f"kling_{task_id}_{uuid.uuid4().hex[:6]}.mp4"
        output_path = OUTPUT_DIR / filename

        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            output_path.write_bytes(resp.content)

        print(f"  📥 Downloaded: {output_path}")
        return str(output_path)

    # ---- Cost -------------------------------------------------------

    @staticmethod
    def _estimate_cost(duration_sec: int) -> float:
        """Rough estimate: std ≈ $0.035/s, pro ≈ $0.07/s."""
        # Kling pricing is per-second; std mode is ~¥0.25/s ≈ $0.035/s
        return round(duration_sec * 0.035, 4)
