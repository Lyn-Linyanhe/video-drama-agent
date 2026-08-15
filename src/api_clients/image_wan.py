"""Official DashScope Wan 2.7 image generation, group, and edit client."""

from __future__ import annotations

import asyncio
import base64
import io
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from .image_base import ImageGenResult, ImageGroupResult
from src.config import OUTPUT_DIR
from src.services.generation_hooks import get_generation_hooks


class WanImageClient:
    provider_name = "wan"
    # Wan 2.7 image tasks use the asynchronous image-generation endpoint.
    # The multimodal path is synchronous and can hold the connection past the
    # client timeout before returning any task identifier.
    _CREATE_PATH = "/api/v1/services/aigc/image-generation/generation"

    def __init__(self, api_key: str, model: str = "wan2.7-image-pro", base_url: str = "https://dashscope.aliyuncs.com"):
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")

    def _headers(self, asynchronous: bool = False) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if asynchronous:
            headers["X-DashScope-Async"] = "enable"
        return headers

    async def generate_image(self, prompt: str, negative_prompt: str = "", width: int = 1024, height: int = 1024, seed: int | None = None) -> ImageGenResult:
        result = await self._create(
            [{"text": self._merged_prompt(prompt, negative_prompt)}],
            {"size": self._size_for_dimensions(width, height), "n": 1},
        )
        return result.images[0] if result.success else ImageGenResult(False, error_message=result.error_message, provider_name=self.provider_name)

    async def generate_sequence(self, prompt: str, reference_paths: list[str], n: int, resolution: str) -> ImageGroupResult:
        content = [{"image": self._data_url(Path(path))} for path in reference_paths]
        content.append({"text": prompt})
        requested_output_count = max(1, int(n))
        # In sequential mode DashScope counts the supplied reference images
        # toward n, but does not return those references as output URLs.  The
        # API currently accepts at most four sequence items.  For example,
        # asking for three new views with one master uses n=4 and returns the
        # three new views.  For a four-view character group, n=4 intentionally
        # yields three new views because the fourth view is the locked master.
        provider_n = min(4, requested_output_count + len(reference_paths))
        result = await self._create(
            content,
            {"enable_sequential": True, "n": provider_n, "size": resolution},
        )
        if not isinstance(result.metadata, dict):
            result.metadata = {}
        result.metadata.setdefault("requested_output_count", requested_output_count)
        result.metadata.setdefault("provider_requested_n", provider_n)
        return result

    async def edit_image(self, prompt: str, reference_paths: list[str], resolution: str) -> ImageGenResult:
        content = [{"image": self._data_url(Path(path))} for path in reference_paths]
        content.append({"text": prompt})
        result = await self._create(content, {"n": 1, "size": resolution})
        return result.images[0] if result.success else ImageGenResult(False, error_message=result.error_message, provider_name=self.provider_name)

    async def _create(self, content: list[dict], parameters: dict) -> ImageGroupResult:
        payload = {"model": self.model, "input": {"messages": [{"role": "user", "content": content}]}, "parameters": {**parameters, "watermark": False}}
        reference_count = sum(1 for item in content if isinstance(item, dict) and item.get("image"))
        sequential = bool(parameters.get("enable_sequential"))
        metadata: dict[str, Any] = {
            "requested_n": int(parameters.get("n") or 0),
            "reference_count": reference_count,
            "sequential": sequential,
            # DashScope's sequential image contract counts the input image in
            # n.  The returned URLs contain only newly generated images.
            "sequence_reference_counted": bool(sequential and reference_count),
        }
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=15.0)) as client:
                response = await client.post(
                    f"{self.base_url}{self._CREATE_PATH}", json=payload, headers=self._headers(asynchronous=True),
                )
                body = self._response_body(response)
            metadata["response_shape"] = self._shape(body)
            if response.status_code >= 400:
                return ImageGroupResult(
                    False,
                    [],
                    self._provider_error(body, f"Wan task creation failed [{response.status_code}]"),
                    self.provider_name,
                    metadata,
                )
            if not isinstance(body, dict):
                return ImageGroupResult(
                    False,
                    [],
                    f"Wan task creation returned an invalid response: {body!r}",
                    self.provider_name,
                    metadata,
                )
            task_id = str((body.get("output") or {}).get("task_id") or "")
            if task_id:
                metadata["provider_task_id"] = task_id
                get_generation_hooks().provider_task(self.provider_name, task_id)
                body = await self._poll(task_id)
                metadata["response_shape"] = self._shape(body)
            urls = self._urls(body)
            metadata["parsed_url_count"] = len(urls)
            images = [await self._download(url) for url in urls]
            metadata["downloaded_image_count"] = len(images)
            if not images:
                return ImageGroupResult(
                    False,
                    [],
                    f"Wan response did not include images (response_shape={metadata.get('response_shape')})",
                    self.provider_name,
                    metadata,
                )
            return ImageGroupResult(True, images, provider_name=self.provider_name, metadata=metadata)
        except httpx.TimeoutException as exc:
            return ImageGroupResult(False, [], f"Wan task submission timed out: {exc!r}", self.provider_name, metadata)
        except Exception as exc:
            return ImageGroupResult(False, [], f"Wan request exception: {exc!r}", self.provider_name, metadata)

    async def _poll(self, task_id: str) -> dict:
        deadline = time.monotonic() + 300
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=15.0)) as client:
            while time.monotonic() < deadline:
                get_generation_hooks().check_cancelled()
                response = await client.get(f"{self.base_url}/api/v1/tasks/{task_id}", headers=self._headers())
                body = self._response_body(response)
                if response.status_code >= 400:
                    raise RuntimeError(self._provider_error(body, f"Wan task {task_id} query failed [{response.status_code}]"))
                if not isinstance(body, dict):
                    raise RuntimeError(f"Wan task {task_id} query returned an invalid response: {body!r}")
                status = str((body.get("output") or {}).get("task_status") or "")
                if status == "SUCCEEDED":
                    return body
                if status in {"FAILED", "CANCELED"}:
                    raise RuntimeError(self._provider_error(body, f"Wan task {task_id} {status.lower()}"))
                await asyncio.sleep(2)
        raise TimeoutError(f"Wan task {task_id} timed out after 300 seconds")

    async def _download(self, url: str) -> ImageGenResult:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.get(url)
            response.raise_for_status()
        directory = OUTPUT_DIR / "wan-downloads"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{uuid.uuid4().hex}.png"
        path.write_bytes(response.content)
        with Image.open(path) as image:
            width, height = image.size
        return ImageGenResult(True, image_url=url, local_path=str(path), width=width, height=height, provider_name=self.provider_name)

    @staticmethod
    def _urls(body: dict) -> list[str]:
        values: list[str] = []
        output = body.get("output") or {}

        def append(value: object) -> None:
            if isinstance(value, str) and value.strip():
                values.append(value.strip())
            elif isinstance(value, dict):
                for key in ("url", "image", "image_url", "uri"):
                    if value.get(key):
                        append(value[key])

        def append_item(item: object) -> None:
            if isinstance(item, str):
                append(item)
                return
            if not isinstance(item, dict):
                return
            # The multimodal response uses content[].image, while the async
            # response normally uses results[].url.  Accept the equivalent
            # aliases returned by compatible DashScope gateways as well.
            for key in ("image", "url", "image_url", "uri"):
                if item.get(key):
                    append(item[key])

        for choice in output.get("choices") or []:
            if not isinstance(choice, dict):
                continue
            message = choice.get("message") or {}
            for item in (message.get("content") or []) if isinstance(message, dict) else []:
                append_item(item)
        for key in ("results", "images", "outputs", "data"):
            entries = output.get(key) or []
            if isinstance(entries, (str, dict)):
                entries = [entries]
            for item in entries:
                append_item(item)
        return list(dict.fromkeys(values))

    @staticmethod
    def _shape(value: object, depth: int = 0) -> object:
        """Return a bounded response shape without retaining URLs or text."""
        if depth > 2:
            return type(value).__name__
        if isinstance(value, dict):
            return {
                str(key): WanImageClient._shape(item, depth + 1)
                for key, item in list(value.items())[:20]
            }
        if isinstance(value, list):
            keys = sorted({str(key) for item in value[:5] if isinstance(item, dict) for key in item.keys()})
            return {"type": "list", "length": len(value), "item_keys": keys}
        return type(value).__name__

    @staticmethod
    def _data_url(path: Path) -> str:
        if not path.is_file():
            raise FileNotFoundError(f"Wan reference image is missing: {path}")
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ValueError(f"Wan reference image exceeds the 20MB limit: {path.name}")
        with Image.open(path) as image:
            width, height = image.size
            if not (240 <= width <= 8000 and 240 <= height <= 8000 and 1 / 8 <= width / height <= 8):
                raise ValueError(f"Wan reference image dimensions are unsupported: {width}x{height}")
            has_alpha = image.mode in {"RGBA", "LA"} or "transparency" in image.info
            if has_alpha:
                flattened = Image.new("RGB", image.size, "white")
                rgba = image.convert("RGBA")
                flattened.paste(rgba, mask=rgba.getchannel("A"))
                buffer = io.BytesIO()
                flattened.save(buffer, format="JPEG", quality=95)
                mime, raw = "image/jpeg", buffer.getvalue()
            else:
                mime = {
                    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                    ".webp": "image/webp", ".bmp": "image/bmp",
                }.get(path.suffix.lower())
                if not mime:
                    raise ValueError(f"Wan reference image format is unsupported: {path.suffix}")
                raw = path.read_bytes()
        return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"

    @staticmethod
    def _response_body(response: httpx.Response) -> dict | str:
        try:
            return response.json()
        except ValueError:
            return response.text[:500]

    @staticmethod
    def _provider_error(body: object, prefix: str) -> str:
        if not isinstance(body, dict):
            return f"{prefix}: {str(body)[:500]}"
        output = body.get("output") or {}
        details = [
            str(body.get("code") or ""),
            str(body.get("message") or output.get("message") or ""),
            f"request_id={body.get('request_id') or body.get('requestId')}" if (body.get("request_id") or body.get("requestId")) else "",
        ]
        return f"{prefix}: {' | '.join(item for item in details if item) or body}"

    @staticmethod
    def _preset(width: int, height: int) -> str:
        longest = max(width, height)
        if longest > 2048:
            return "4K"
        return "2K" if longest > 1024 else "1K"

    @staticmethod
    def _size_for_dimensions(width: int, height: int) -> str:
        """Encode a custom Wan size without discarding its aspect ratio."""
        width, height = int(width), int(height)
        if width <= 0 or height <= 0:
            raise ValueError("Wan image dimensions must be positive")
        return f"{width}*{height}"

    @staticmethod
    def _merged_prompt(prompt: str, negative_prompt: str) -> str:
        return prompt if not negative_prompt.strip() else f"{prompt}\n避免出现：{negative_prompt.strip()}"
