"""Happy Horse (快马) video generation client — supports multiple API gateways.

Happy Horse is Alibaba's video generation model, supporting:
- Image-to-video (first-frame): ``happyhorse-1.1-i2v``
- Reference-to-video (multi-image): ``happyhorse-1.1-r2v`` (up to 9 reference images)

Supported API formats (auto-detected from base_url):
- **alibaba**: 阿里云百炼 DashScope 直连 — async create + poll pattern
- **apiyi**:  API易 DashScope 代理 — same pattern, different path prefix
- **wavespeed**: WaveSpeed.ai — simpler async predict + poll pattern

Async workflow: submit task → poll until complete → download video.
"""

import asyncio
import base64
import io
import json
import time
import uuid
from pathlib import Path

import httpx

from .base import VideoAPIClient, VideoGenResult
from src.config import OUTPUT_DIR
from src.services.generation_hooks import get_generation_hooks
from src.services.video_capabilities import (
    VIDEO_CAPABILITIES,
    duration_options_from_capability,
    parse_integral_duration,
)

# ============================================================
# Polling config
# ============================================================
POLL_INTERVAL = 5       # seconds between status checks
POLL_MAX_WAIT = 600     # max total wait (10 min)

# ============================================================
# Default URLs per gateway
# ============================================================
ALIBABA_DEFAULT_URL = "https://dashscope.aliyuncs.com"
APIYI_DEFAULT_URL    = "https://api.apiyi.com"
WAVESPEED_DEFAULT_URL = "https://api.wavespeed.ai"


class HappyHorseVideoClient(VideoAPIClient):
    """Video generation via Happy Horse (快马) API.

    Supports three API gateways (auto-detected from base_url):

    **阿里云百炼 DashScope** (default for ``dashscope.aliyuncs.com``):
        POST /api/v1/services/aigc/video-generation/video-synthesis
        GET  /api/v1/tasks/{task_id}

    **API易** (``api.apiyi.com``):
        POST /wan/api/v1/services/aigc/video-generation/video-synthesis
        GET  /v1/tasks/{task_id}

    **WaveSpeed** (``api.wavespeed.ai`` or ``sk-ws-`` keys):
        POST /api/v3/alibaba/{model}/reference-to-video
        GET  /api/v3/predictions/{request_id}/result

    Attributes:
        api_key: Happy Horse API key (Bearer token).
        base_url: API base URL. Auto-detects format from domain.
        model: Model ID (e.g. ``happyhorse-1.1-i2v`` or ``happyhorse-1.0-r2v``).
        resolution: Output resolution ``"720p"`` or ``"1080p"`` (normalised internally).
    """

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "happyhorse-1.1-i2v",
        resolution: str = "1080p",
    ):
        self.api_key = api_key
        self.model = model
        self.resolution = resolution.lower().rstrip("p") + "p"  # normalise: 1080P → 1080p

        # Auto-detect API format from base_url (or key prefix as fallback)
        if not base_url:
            if api_key.startswith("sk-ws-"):
                base_url = WAVESPEED_DEFAULT_URL
            else:
                base_url = ALIBABA_DEFAULT_URL  # 默认阿里云直连

        self.base_url = base_url.rstrip("/")
        base_lower = self.base_url.lower()

        if "wavespeed" in base_lower:
            self._format = "wavespeed"
        elif "apiyi" in base_lower:
            self._format = "apiyi"
        else:
            self._format = "alibaba"  # dashscope.aliyuncs.com / 自定义百炼域名

    # ---- VideoAPIClient interface -----------------------------------

    @property
    def provider_name(self) -> str:
        return "happyhorse"

    async def generate_video(
        self,
        prompt: str,
        negative_prompt: str = "",
        duration_sec: int = 5,
        seed: int | None = None,
        aspect_ratio: str = "9:16",
        motion_strength: float = 0.7,
        image_reference: str | None = None,
        image_references: list[str] | None = None,
        generate_audio: bool | None = None,
    ) -> VideoGenResult:
        """Submit an image/reference-to-video task and download the result.

        When image_references is provided, images are sent in order so the
        prompt can refer to them as 图1, 图2, etc. image_reference is kept for
        backward-compatible single-reference generation.

        Returns VideoGenResult with local_path pointing to the downloaded mp4.
        """
        try:
            reference_paths = [p for p in (image_references or []) if p]
            if image_reference and not reference_paths:
                reference_paths = [image_reference]
            reference_paths = reference_paths[:9]
            image_data_uris = [self._encode_image_to_data_uri(p) for p in reference_paths]

            # Validate before any provider call. Silent clamping would make a
            # prompt planned for a longer shot look successful while the model
            # actually receives a shorter request and drops story content.
            duration = self._validate_duration(duration_sec)

            # 1. Submit task
            task_id = await self._submit_task(
                prompt=prompt,
                duration=duration,
                seed=seed,
                aspect_ratio=aspect_ratio,
                image_data_uris=image_data_uris,
            )
            hooks = get_generation_hooks()
            hooks.provider_task(self.provider_name, task_id)
            hooks.check_cancelled()

            # 2. Poll until done
            video_url = await self._poll_task(task_id)

            # 3. Download to local
            hooks.progress("downloading", 0, 1, "正在下载视频")
            hooks.check_cancelled()
            local_path = await self._download_video(video_url, task_id, duration)
            hooks.progress("downloading", 1, 1, "视频下载完成")

            audio_present = bool(generate_audio) and self._has_audio_stream(local_path)
            return VideoGenResult(
                success=True,
                video_url=video_url,
                local_path=local_path,
                duration_sec=float(duration),
                api_cost=self._estimate_cost(duration, bool(reference_paths)),
                reference_image_used=";".join(reference_paths),
                audio_source="native" if audio_present else "none",
                native_audio_enabled=audio_present,
                native_audio_path=local_path if audio_present else None,
                audio_present=audio_present,
            )

        except Exception as e:
            import traceback
            err_msg = str(e)[:200]
            print(f"  ❌ Happy Horse video gen failed: {err_msg}")
            print(f"     Traceback: {traceback.format_exc()[-300:]}")
            return VideoGenResult(
                success=False,
                error_message=err_msg,
            )

    async def get_cost_estimate(self, duration_sec: int) -> float:
        return self._estimate_cost(duration_sec)

    @staticmethod
    def _has_audio_stream(path: str) -> bool:
        """Detect an embedded audio stream in the downloaded MP4."""
        import subprocess

        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "a:0",
                 "-show_entries", "stream=index", "-of", "csv=p=0", path],
                capture_output=True, text=True, timeout=15, check=False,
            )
            return bool(result.returncode == 0 and result.stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            return False

    # ---- Auth -------------------------------------------------------

    def _auth_headers(self, extra: dict | None = None) -> dict:
        """Return headers for API requests with Bearer token."""
        h = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if extra:
            h.update(extra)
        return h

    # ---- Submit (dispatches to format-specific method) ---------------

    def _dashscope_paths(self) -> tuple[str, str]:
        """Return (create_path, query_prefix) for the current DashScope variant.

        - 阿里云百炼直连: /api/v1/services/...  /api/v1/tasks/
        - API易代理:        /wan/api/v1/services/... /v1/tasks/
        """
        if self._format == "apiyi":
            create = "/wan/api/v1/services/aigc/video-generation/video-synthesis"
            query  = "/v1/tasks"
        else:
            create = "/api/v1/services/aigc/video-generation/video-synthesis"
            query  = "/api/v1/tasks"
        return create, query

    def _validate_duration(self, value) -> int:
        try:
            duration = parse_integral_duration(value, field_name="Happy Horse duration")
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if duration < 4 or duration > 15:
            raise ValueError(
                f"Happy Horse supports duration 4-15 seconds; received {duration}"
            )
        capability = VIDEO_CAPABILITIES.get("happyhorse", {}).get("models", {}).get(self.model, {})
        supported = duration_options_from_capability(capability)
        if supported and duration not in supported:
            choices = ", ".join(str(item) for item in supported)
            raise ValueError(
                f"Happy Horse only supports durations {choices} seconds; "
                f"received {duration}"
            )
        return duration

    async def _submit_task(
        self,
        prompt: str,
        duration: int,
        seed: int | None,
        aspect_ratio: str = "9:16",
        image_data_uris: list[str] | None = None,
    ) -> str:
        """Submit a generation task. Returns task_id."""
        duration = self._validate_duration(duration)
        image_data_uris = image_data_uris or []

        if self._format == "wavespeed":
            return await self._submit_wavespeed(prompt, duration, seed, aspect_ratio, image_data_uris)
        else:
            return await self._submit_dashscope(prompt, duration, seed, image_data_uris)

    def _model_for_reference_count(self, reference_count: int) -> str:
        """Use the reference-to-video model when multiple images are supplied."""
        if reference_count > 1 and self.model.startswith("happyhorse-") and self.model.endswith("-i2v"):
            return self.model[:-4] + "-r2v"
        return self.model

    @staticmethod
    def _is_reference_model(model: str) -> bool:
        return model.lower().endswith("-r2v")

    async def _submit_wavespeed(
        self,
        prompt: str,
        duration: int,
        seed: int | None,
        aspect_ratio: str,
        image_data_uris: list[str],
    ) -> str:
        """Submit via WaveSpeed API.

        POST /api/v3/alibaba/{model}/reference-to-video
        """
        # Map model name to WaveSpeed path segment
        model_path = self._model_for_reference_count(len(image_data_uris))
        if not model_path.startswith("happyhorse-"):
            model_path = f"happyhorse-{model_path}"

        create_url = f"{self.base_url}/api/v3/alibaba/{model_path}/reference-to-video"

        body: dict = {
            "prompt": prompt,
            "resolution": self.resolution,         # "720p" / "1080p"
            "aspect_ratio": aspect_ratio or "9:16",
            "duration": duration,
        }

        if image_data_uris:
            body["images"] = image_data_uris
            mode_label = "r2v" if len(image_data_uris) > 1 else "i2v"
        else:
            mode_label = "t2v"

        if seed is not None:
            body["seed"] = seed

        print(f"  🎬 Happy Horse [WaveSpeed] submit ({mode_label}): {model_path} duration={duration}s res={self.resolution}")

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(create_url, json=body, headers=self._auth_headers())

        if resp.status_code not in (200, 201):
            print(f"  ❌ WaveSpeed submit response: {resp.status_code} - {resp.text[:500]}")
            raise RuntimeError(f"WaveSpeed submit failed [{resp.status_code}]: {resp.text[:200]}")

        data = resp.json()
        task_id = data.get("request_id") or data.get("id") or data.get("prediction_id", "")
        if not task_id:
            # Some versions return { "output": { "task_id": "..." } }
            task_id = data.get("output", {}).get("task_id", "")
        if not task_id:
            raise RuntimeError(f"WaveSpeed submit: no task_id in response: {json.dumps(data, ensure_ascii=False)[:300]}")

        print(f"  ✅ WaveSpeed task submitted: {task_id}")
        return task_id

    async def _submit_dashscope(
        self,
        prompt: str,
        duration: int,
        seed: int | None,
        image_data_uris: list[str],
    ) -> str:
        """Submit via DashScope-compatible API (阿里云百炼 / API易).

        Header: X-DashScope-Async: enable
        """
        create_path, _ = self._dashscope_paths()
        create_url = f"{self.base_url}{create_path}"

        # DashScope uses uppercase resolution
        res_upper = self.resolution.upper().rstrip("P") + "P"

        request_model = self._model_for_reference_count(len(image_data_uris))
        body: dict = {
            "model": request_model,
            "input": {
                "prompt": prompt,
            },
            "parameters": {
                "resolution": res_upper,     # "720P" / "1080P"
                "duration": duration,
                "prompt_extend": True,
                "watermark": False,
            },
        }

        if image_data_uris:
            is_r2v = self._is_reference_model(request_model)
            media_type = "reference_image" if is_r2v else "first_frame"
            body["input"]["media"] = [
                {"type": media_type, "url": uri}
                for uri in image_data_uris
            ]
            mode_label = "r2v" if is_r2v else "i2v"
        else:
            mode_label = "t2v"

        if seed is not None:
            body["parameters"]["seed"] = seed

        headers = self._auth_headers({"X-DashScope-Async": "enable"})

        label = "阿里云百炼" if self._format == "alibaba" else "API易"
        print(f"  🎬 Happy Horse [{label}] submit ({mode_label}): {self.model} duration={duration}s res={res_upper}")

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(create_url, json=body, headers=headers)

        if resp.status_code != 200:
            print(f"  ❌ DashScope submit response: {resp.status_code} - {resp.text[:500]}")
            raise RuntimeError(f"DashScope submit failed [{resp.status_code}]: {resp.text[:200]}")

        data = resp.json()
        task_id = data.get("output", {}).get("task_id", "")
        if not task_id:
            raise RuntimeError(f"DashScope submit: no task_id in response: {data}")

        print(f"  ✅ DashScope task submitted: {task_id}")
        return task_id

    # ---- Poll (dispatches to format-specific method) -----------------

    async def _poll_task(self, task_id: str) -> str:
        """Poll task status until complete. Returns video download URL."""
        if self._format == "wavespeed":
            return await self._poll_wavespeed(task_id)
        else:
            return await self._poll_dashscope(task_id)

    async def _poll_wavespeed(self, request_id: str) -> str:
        """Poll WaveSpeed prediction until completed.

        GET /api/v3/predictions/{request_id}/result
        Response: { "status": "completed", "data": { "outputs": ["url"] } }
        """
        query_url = f"{self.base_url}/api/v3/predictions/{request_id}/result"
        start = time.time()

        async with httpx.AsyncClient(timeout=15) as client:
            while True:
                elapsed = time.time() - start
                hooks = get_generation_hooks()
                hooks.check_cancelled()
                hooks.progress("waiting_provider", int(elapsed), POLL_MAX_WAIT, "等待快马生成视频")
                if elapsed > POLL_MAX_WAIT:
                    raise TimeoutError(f"WaveSpeed task {request_id} timed out after {POLL_MAX_WAIT}s")

                resp = await client.get(query_url, headers=self._auth_headers())
                data = resp.json()

                if resp.status_code != 200:
                    msg = data.get("message", str(data))
                    raise RuntimeError(f"WaveSpeed query failed [{resp.status_code}]: {msg}")

                status = data.get("status", "unknown")
                print(f"  ⏳ WaveSpeed {request_id}: {status} ({elapsed:.0f}s)")

                if status == "completed":
                    outputs = data.get("data", {}).get("outputs", [])
                    if not outputs:
                        raise RuntimeError(f"WaveSpeed task {request_id} completed but no outputs")
                    return outputs[0]

                if status in ("failed", "error"):
                    err = data.get("error") or data.get("message", "unknown error")
                    raise RuntimeError(f"WaveSpeed task {request_id} failed: {err}")

                await asyncio.sleep(POLL_INTERVAL)

    async def _poll_dashscope(self, task_id: str) -> str:
        """Poll DashScope task until SUCCEEDED. Returns OSS video download URL.

        Response: { "output": { "task_status": "SUCCEEDED", "video_url": "..." } }
        """
        _, query_prefix = self._dashscope_paths()
        query_url = f"{self.base_url}{query_prefix}/{task_id}"
        start = time.time()

        async with httpx.AsyncClient(timeout=15) as client:
            while True:
                elapsed = time.time() - start
                hooks = get_generation_hooks()
                hooks.check_cancelled()
                hooks.progress("waiting_provider", int(elapsed), POLL_MAX_WAIT, "等待快马生成视频")
                if elapsed > POLL_MAX_WAIT:
                    raise TimeoutError(f"DashScope task {task_id} timed out after {POLL_MAX_WAIT}s")

                resp = await client.get(
                    query_url,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                data = resp.json()

                if resp.status_code != 200:
                    msg = data.get("message", str(data))
                    raise RuntimeError(f"DashScope query failed [{resp.status_code}]: {msg}")

                output = data.get("output", {})
                status = output.get("task_status", "UNKNOWN")
                print(f"  ⏳ DashScope {task_id}: {status} ({elapsed:.0f}s)")

                if status == "SUCCEEDED":
                    video_url = output.get("video_url", "")
                    if not video_url:
                        raise RuntimeError(f"DashScope task {task_id} succeeded but no video_url returned")
                    return video_url

                if status == "FAILED":
                    err = output.get("message", "unknown error")
                    raise RuntimeError(f"DashScope task {task_id} failed: {err}")

                await asyncio.sleep(POLL_INTERVAL)

    # ---- Image encoding ---------------------------------------------

    @staticmethod
    def _encode_image_to_data_uri(image_path: str) -> str:
        """Read a local image, compress to JPEG, return base64 data URI.

        Large PNGs are converted to JPEG at reduced quality to stay under size limits.
        Max file size: 20 MB. Min dimensions: 300x300 px.
        """
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Reference image not found: {image_path}")

        ext = path.suffix.lower().lstrip('.')
        image_data = path.read_bytes()

        # Convert large PNGs to JPEG to reduce size
        if len(image_data) > 500_000 or ext == 'png':
            try:
                from PIL import Image
                img = Image.open(path)
                # Resize if too large (max 1280px on longest side)
                w, h = img.size
                max_dim = max(w, h)
                if max_dim > 1280:
                    scale = 1280 / max_dim
                    img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
                buf = io.BytesIO()
                img.convert("RGB").save(buf, format="JPEG", quality=75)
                image_data = buf.getvalue()
                mime_type = "image/jpeg"
                print(f"  📤 Image compressed: {path.name} ({len(path.read_bytes())}→{len(image_data)} bytes)")
            except Exception as e:
                print(f"  ⚠️ Image compression failed ({e}), using original")
                mime_types = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
                mime_type = mime_types.get(ext, "image/png")
        else:
            mime_types = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
            mime_type = mime_types.get(ext, "image/jpeg")
            print(f"  📤 Image encoded: {path.name} ({len(image_data)} bytes)")

        b64 = base64.b64encode(image_data).decode("ascii")
        data_uri = f"data:{mime_type};base64,{b64}"
        return data_uri

    # ---- Download ---------------------------------------------------

    async def _download_video(self, url: str, task_id: str, duration: int) -> str:
        """Download generated video to local output dir.

        IMPORTANT: Signed URLs (OSS, etc.) should be fetched WITHOUT auth headers
        to avoid 403 errors. WaveSpeed returns direct URLs; DashScope returns OSS presigned URLs.
        """
        short_id = task_id[-12:] if len(task_id) > 12 else task_id
        filename = f"hh_{short_id}_{uuid.uuid4().hex[:6]}.mp4"
        output_path = OUTPUT_DIR / filename

        # Try without auth first (OSS/CloudFront signed URLs reject auth headers)
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            resp = await client.get(url)

            # If 403 with no auth, retry with auth header (some providers require it)
            if resp.status_code == 403:
                print(f"  ⚠️ Direct download 403, retrying with auth header...")
                resp = await client.get(url, headers={"Authorization": f"Bearer {self.api_key}"})

            resp.raise_for_status()
            output_path.write_bytes(resp.content)

        size_mb = len(resp.content) / (1024 * 1024)
        print(f"  📥 Downloaded: {filename} ({size_mb:.1f} MB)")
        return str(output_path)

    # ---- Cost -------------------------------------------------------

    def _estimate_cost(self, duration_sec: int, has_image: str | None = None) -> float:
        """Estimate Happy Horse cost via WaveSpeed.

        WaveSpeed pricing: 720p = $0.70/5s, 1080p = $1.40/5s.
        """
        is_1080p = "1080" in self.resolution
        base_5s_cost = 1.40 if is_1080p else 0.70
        return round(duration_sec / 5.0 * base_5s_cost, 4)
