"""Seedance video generation client for Volcengine Ark."""

from __future__ import annotations

import asyncio
import base64
import io
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from .base import VideoAPIClient, VideoGenResult
from src.config import OUTPUT_DIR
from src.services.generation_hooks import get_generation_hooks
from src.services.job_manager import JobCanceled


POLL_INTERVAL = 5
POLL_MAX_WAIT = 1200
ARK_DEFAULT_URL = "https://ark.cn-beijing.volces.com"
MAX_INLINE_IMAGE_BYTES = 8 * 1024 * 1024
MAX_REFERENCE_LONGEST_EDGE = 2048


class SeedanceVideoClient(VideoAPIClient):
    """Generate videos through Volcengine Ark Seedance async tasks."""

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "",
        model: str = "doubao-seedance-2-0-fast-260128",
        resolution: str = "1080p",
    ):
        self.api_key = api_key
        self.base_url = (base_url or ARK_DEFAULT_URL).rstrip("/")
        self.model = model or "doubao-seedance-2-0-fast-260128"
        normalized_resolution = str(resolution or "1080p").strip().lower()
        self.resolution = normalized_resolution if normalized_resolution.endswith(("p", "k")) else f"{normalized_resolution}p"

    @property
    def provider_name(self) -> str:
        return "seedance"

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
        reference_mode: str = "auto",
        last_frame_reference: str | None = None,
        return_last_frame: bool = False,
        generate_audio: bool = True,
    ) -> VideoGenResult:
        task_id = ""
        try:
            references = [p for p in (image_references or []) if p]
            image_reference_only = False
            if image_reference and not references:
                references = [image_reference]
                image_reference_only = True
            if reference_mode == "auto":
                if last_frame_reference:
                    reference_mode = "first_last_frame"
                elif image_reference_only or (references and self._looks_like_continuity_frame(references[0])):
                    reference_mode = "first_frame"
                elif references:
                    reference_mode = "multi_reference"
                else:
                    reference_mode = "text_to_video"
            reference_plan = self.plan_reference_inputs(
                references,
                reference_mode=reference_mode,
                last_frame_reference=last_frame_reference,
            )
            request_duration = int(duration_sec)
            task_id = await self._submit_task(
                prompt=prompt,
                negative_prompt=negative_prompt,
                duration_sec=request_duration,
                seed=seed,
                aspect_ratio=aspect_ratio,
                image_references=[item["path"] for item in reference_plan["items"]],
                reference_role="reference_image",
                reference_items=reference_plan["items"],
                return_last_frame=return_last_frame,
                generate_audio=bool(generate_audio),
            )
            hooks = get_generation_hooks()
            hooks.provider_task(self.provider_name, task_id)
            hooks.check_cancelled()

            polled = await self._poll_task(task_id)
            if isinstance(polled, dict):
                video_url = str(polled.get("video_url") or "")
                last_frame_url = str(polled.get("last_frame_url") or "")
                audio_url = str(polled.get("audio_url") or "")
            else:
                video_url = str(polled)
                last_frame_url = ""
                audio_url = ""
            hooks.progress("downloading", 0, 1, "正在下载 Seedance 视频")
            hooks.check_cancelled()
            local_path = await self._download_video(video_url, task_id)
            audio_path = None
            if audio_url:
                audio_path = await self._download_audio(audio_url, task_id)
            last_frame_path = None
            if return_last_frame and last_frame_url:
                last_frame_path = await self._download_last_frame(last_frame_url, task_id)
            hooks.progress("downloading", 1, 1, "Seedance 视频下载完成")
            audio_present = bool(audio_path) or self._has_audio_stream(local_path)
            return VideoGenResult(
                success=True,
                video_url=video_url,
                local_path=local_path,
                duration_sec=float(duration_sec),
                api_cost=await self.get_cost_estimate(duration_sec),
                reference_image_used=";".join(item["path"] for item in reference_plan["items"]),
                last_frame_url=last_frame_url,
                last_frame_path=last_frame_path,
                audio_source="native" if audio_present and generate_audio else "none",
                native_audio_enabled=bool(audio_present and generate_audio),
                native_audio_path=audio_path or (local_path if audio_present else None),
                audio_present=audio_present,
            )
        except JobCanceled:
            if task_id:
                try:
                    await self.cancel_task(task_id)
                except Exception as exc:
                    print(f"  Seedance provider cancellation failed: {exc}")
            raise
        except asyncio.CancelledError:
            if task_id:
                try:
                    await self.cancel_task(task_id)
                except Exception as exc:
                    print(f"  Seedance provider cancellation failed: {exc}")
            raise
        except Exception as exc:
            import traceback

            message = self.format_provider_error(str(exc)[:800])
            print(f"  Seedance video gen failed: {message}")
            print(f"     Traceback: {traceback.format_exc()[-300:]}")
            return VideoGenResult(success=False, error_message=message)

    async def get_cost_estimate(self, duration_sec: int) -> float:
        return 0.0

    def _auth_headers(self) -> dict[str, str]:
        api_key = str(self.api_key or "").strip()
        if not api_key:
            raise RuntimeError("Seedance Ark API Key is not configured")
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def _task_url(self, task_id: str = "") -> str:
        suffix = f"/{task_id}" if task_id else ""
        return f"{self.base_url}/api/v3/contents/generations/tasks{suffix}"

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        """Cancel a provider task using Ark's official DELETE endpoint."""
        task_id = str(task_id or "").strip()
        if not task_id:
            return {"canceled": False, "status": 0, "detail": "missing provider task id"}
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.delete(self._task_url(task_id), headers=self._auth_headers())
        if response.status_code in {200, 202, 204, 404, 409}:
            return {"canceled": True, "status": response.status_code, "detail": response.text[:300]}
        return {"canceled": False, "status": response.status_code, "detail": response.text[:500]}

    def plan_reference_inputs(
        self,
        references: list[str],
        *,
        reference_mode: str = "auto",
        last_frame_reference: str | None = None,
    ) -> dict[str, Any]:
        """Resolve local references into one official Seedance input mode."""
        from src.services.video_capabilities import SEEDANCE_DEFAULT_MODEL, VIDEO_CAPABILITIES

        capability = VIDEO_CAPABILITIES["seedance"]["models"].get(
            self.model,
            VIDEO_CAPABILITIES["seedance"]["models"][SEEDANCE_DEFAULT_MODEL],
        )
        paths = [str(path) for path in references if str(path or "").strip()]
        mode = str(reference_mode or "auto").strip().lower()
        if mode == "auto":
            if last_frame_reference:
                mode = "first_last_frame"
            elif paths and self._looks_like_continuity_frame(paths[0]):
                mode = "first_frame"
            elif paths:
                mode = "multi_reference"
            else:
                mode = "text_to_video"

        dropped: list[dict[str, str]] = []
        items: list[dict[str, str]] = []

        if mode == "text_to_video":
            dropped.extend({"path": path, "reason": "text_to_video mode does not accept reference images"} for path in paths)
            if last_frame_reference:
                dropped.append({"path": str(last_frame_reference), "reason": "text_to_video mode does not accept last_frame"})
            return {"mode": mode, "items": items, "dropped_references": dropped}

        if mode == "first_frame":
            if not capability.get("supports_first_frame", capability.get("image_to_video")):
                dropped.extend({"path": path, "reason": f"{self.model} does not support first_frame"} for path in paths)
            elif paths:
                items.append({"path": paths[0], "role": "first_frame"})
                dropped.extend({"path": path, "reason": "first_frame mode cannot mix reference_image"} for path in paths[1:])
            if last_frame_reference:
                dropped.append({"path": str(last_frame_reference), "reason": "first_frame mode cannot mix last_frame"})
            return {"mode": mode, "items": items, "dropped_references": dropped}

        if mode == "first_last_frame":
            if not capability.get("supports_first_last_frame", capability.get("image_to_video")):
                dropped.extend({"path": path, "reason": f"{self.model} does not support first_last_frame"} for path in paths)
                if last_frame_reference:
                    dropped.append({"path": str(last_frame_reference), "reason": f"{self.model} does not support first_last_frame"})
            elif paths and last_frame_reference:
                items.extend([
                    {"path": paths[0], "role": "first_frame"},
                    {"path": str(last_frame_reference), "role": "last_frame"},
                ])
                dropped.extend({"path": path, "reason": "first_last_frame mode cannot mix reference_image"} for path in paths[1:])
            else:
                if paths:
                    dropped.append({"path": paths[0], "reason": "first_last_frame requires an explicit last_frame"})
                if last_frame_reference:
                    dropped.append({"path": str(last_frame_reference), "reason": "first_last_frame requires an explicit first_frame"})
            return {"mode": mode, "items": items, "dropped_references": dropped}

        if mode != "multi_reference":
            raise ValueError(f"Unsupported Seedance reference mode: {mode}")
        if not capability.get("multi_reference", False):
            dropped.extend({"path": path, "reason": f"{self.model} does not support multi_reference"} for path in paths)
        else:
            maximum = max(1, int(capability.get("max_reference_images", 1)))
            items = [{"path": path, "role": "reference_image"} for path in paths[:maximum]]
            dropped.extend(
                {"path": path, "reason": f"{self.model} accepts at most {maximum} reference images"}
                for path in paths[maximum:]
            )
        if last_frame_reference:
            dropped.append({"path": str(last_frame_reference), "reason": "multi_reference mode cannot mix last_frame"})
        return {"mode": mode, "items": items, "dropped_references": dropped}

    async def _submit_task(
        self,
        *,
        prompt: str,
        negative_prompt: str,
        duration_sec: int,
        seed: int | None,
        aspect_ratio: str,
        image_references: list[str],
        reference_role: str,
        reference_items: list[dict[str, str]] | None = None,
        return_last_frame: bool = False,
        generate_audio: bool = False,
    ) -> str:
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if negative_prompt:
            content[0]["text"] = f"{prompt}\nAvoid: {negative_prompt}"
        items = reference_items or [
            {"path": path, "role": reference_role}
            for path in image_references
        ]
        for item in items:
            path = item["path"]
            content.append({
                "type": "image_url",
                "role": item.get("role") or reference_role,
                "image_url": {
                    "url": self._encode_image_to_data_uri(path),
                },
            })

        body: dict[str, Any] = {
            "model": self.model,
            "content": content,
            "ratio": aspect_ratio or "9:16",
            "duration": duration_sec,
            "resolution": self.resolution,
            "watermark": False,
            "generate_audio": bool(generate_audio),
            "return_last_frame": bool(return_last_frame),
        }
        if seed is not None:
            body["seed"] = seed

        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(self._task_url(), json=body, headers=self._auth_headers())
        if response.status_code not in (200, 201):
            raise RuntimeError(f"Ark Seedance submit failed [{response.status_code}]: {response.text[:800]}")
        data = response.json()
        task_id = (
            data.get("id")
            or data.get("task_id")
            or (data.get("data") or {}).get("id")
            or (data.get("data") or {}).get("task_id")
        )
        if not task_id:
            raise RuntimeError(f"Ark Seedance submit: no task_id in response: {str(data)[:300]}")
        print(f"  Seedance [Ark] task submitted: {task_id}")
        return str(task_id)

    @classmethod
    def _ark_reference_payload_plan(
        cls,
        references: list[str],
        *,
        image_reference_only: bool = False,
    ) -> tuple[list[str], str]:
        if not references:
            return [], "reference_image"
        if image_reference_only:
            return references[:1], "first_frame"
        if cls._looks_like_continuity_frame(references[0]):
            return references[:1], "first_frame"
        return references, "reference_image"

    @staticmethod
    def _looks_like_continuity_frame(image_path: str) -> bool:
        normalized = str(image_path or "").replace("\\", "/").lower()
        return "/assets/videos/continuity/" in normalized or normalized.endswith("_tail.jpg")

    @staticmethod
    def _is_sensitive_input_image_error(message: str) -> bool:
        return (
            "InputImageSensitiveContentDetected" in message
            or "input image" in message and "may contain real person" in message
        )

    @classmethod
    def is_non_retriable_error(cls, message: str) -> bool:
        """Return whether retrying the same Seedance request cannot help."""
        normalized = str(message or "")
        return cls._is_sensitive_input_image_error(normalized) or any(
            marker in normalized
            for marker in (
                "InputImageSafetyViolation",
                "ContentPolicyViolation",
                "SensitiveContentDetected",
            )
        )

    @classmethod
    def format_provider_error(cls, message: str) -> str:
        """Turn provider safety failures into an actionable project error."""
        normalized = str(message or "").strip()
        if cls._is_sensitive_input_image_error(normalized):
            return (
                "Seedance 拒绝了一个或多个参考图：检测到可能包含真人或隐私内容。"
                "请改用不含可识别真人面部的场景/物体参考图，或切换到快乐小马进行人物参考生成；"
                f"原始错误：{normalized[:700]}"
            )
        return normalized

    async def _poll_task(self, task_id: str) -> str | dict[str, str]:
        start = time.time()
        async with httpx.AsyncClient(timeout=20) as client:
            while True:
                elapsed = time.time() - start
                hooks = get_generation_hooks()
                hooks.check_cancelled()
                hooks.progress("waiting_provider", int(elapsed), POLL_MAX_WAIT, "等待 Seedance 视频生成")
                if elapsed > POLL_MAX_WAIT:
                    raise TimeoutError(f"Ark Seedance task {task_id} timed out after {POLL_MAX_WAIT}s")

                response = await client.get(self._task_url(task_id), headers=self._auth_headers())
                if response.status_code != 200:
                    raise RuntimeError(f"Ark Seedance query failed [{response.status_code}]: {response.text[:200]}")
                data = response.json()
                status = str(data.get("status") or (data.get("data") or {}).get("status") or "").lower()
                print(f"  Seedance {task_id}: {status or 'unknown'} ({elapsed:.0f}s)")

                if status in {"succeeded", "success", "completed", "done"}:
                    return {
                        "video_url": self._extract_video_url(data),
                        "last_frame_url": self._extract_last_frame_url(data),
                        "audio_url": self._extract_audio_url(data),
                    }
                if status in {"failed", "error", "cancelled", "canceled", "expired"}:
                    error = data.get("error") or data.get("message") or (data.get("data") or {}).get("error") or data
                    raise RuntimeError(f"Ark Seedance task {task_id} failed: {str(error)[:200]}")
                await asyncio.sleep(POLL_INTERVAL)

    def _extract_video_url(self, data: dict[str, Any]) -> str:
        candidates: list[Any] = [
            data.get("video_url"),
            data.get("url"),
            (data.get("output") or {}).get("video_url") if isinstance(data.get("output"), dict) else None,
            (data.get("output") or {}).get("url") if isinstance(data.get("output"), dict) else None,
            (data.get("data") or {}).get("video_url") if isinstance(data.get("data"), dict) else None,
            (data.get("data") or {}).get("url") if isinstance(data.get("data"), dict) else None,
        ]
        nested_data = data.get("data") if isinstance(data.get("data"), dict) else {}
        content = data.get("content") or nested_data.get("content")
        if isinstance(content, dict):
            video_url = content.get("video_url")
            if isinstance(video_url, dict):
                candidates.append(video_url.get("url"))
            else:
                candidates.append(video_url)
            candidates.append(content.get("url"))
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    video_url = item.get("video_url")
                    if isinstance(video_url, dict):
                        candidates.append(video_url.get("url"))
                    else:
                        candidates.append(video_url)
        for value in candidates:
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
        raise RuntimeError(f"Ark Seedance task succeeded but no video URL returned: {str(data)[:300]}")

    def _extract_last_frame_url(self, data: dict[str, Any]) -> str:
        """Extract the provider-returned final frame across response shapes."""
        nested_data = data.get("data") if isinstance(data.get("data"), dict) else {}
        candidates: list[Any] = [
            data.get("last_frame_url"),
            data.get("last_frame"),
            nested_data.get("last_frame_url"),
            nested_data.get("last_frame"),
            (data.get("output") or {}).get("last_frame_url") if isinstance(data.get("output"), dict) else None,
            (data.get("output") or {}).get("last_frame") if isinstance(data.get("output"), dict) else None,
        ]
        content = data.get("content") or nested_data.get("content")
        if isinstance(content, dict):
            candidates.extend([content.get("last_frame_url"), content.get("last_frame")])
        if isinstance(content, list):
            role_candidates: list[Any] = []
            fallback_candidates: list[Any] = []
            has_role_marker = False
            for item in content:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role") or "").lower()
                item_type = str(item.get("type") or "").lower()
                if role:
                    has_role_marker = True
                candidate = item.get("image_url") or item.get("last_frame_url") or item.get("url")
                if role == "last_frame":
                    role_candidates.append(candidate)
                elif not role and "image" in item_type:
                    # Some older response shapes expose an unlabeled output
                    # image. Only use that fallback when the provider did not
                    # label any content roles; otherwise it may be an input
                    # first_frame/reference_image item.
                    fallback_candidates.append(candidate)
            candidates.extend(role_candidates)
            if not has_role_marker:
                candidates.extend(fallback_candidates)
        for value in candidates:
            if isinstance(value, dict):
                value = value.get("url") or value.get("image_url") or value.get("last_frame_url")
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
        return ""

    def _extract_audio_url(self, data: dict[str, Any]) -> str:
        """Extract an optional provider audio artifact from Ark responses."""
        nested_data = data.get("data") if isinstance(data.get("data"), dict) else {}
        candidates: list[Any] = [
            data.get("audio_url"), data.get("audio"),
            nested_data.get("audio_url"), nested_data.get("audio"),
            (data.get("output") or {}).get("audio_url") if isinstance(data.get("output"), dict) else None,
            (data.get("output") or {}).get("audio") if isinstance(data.get("output"), dict) else None,
        ]
        content = data.get("content") or nested_data.get("content")
        if isinstance(content, dict):
            candidates.extend([content.get("audio_url"), content.get("audio")])
        if isinstance(content, list):
            for item in content:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role") or "").lower()
                item_type = str(item.get("type") or "").lower()
                if role == "audio" or "audio" in item_type:
                    candidates.extend([item.get("audio_url"), item.get("url"), item.get("audio")])
        for value in candidates:
            if isinstance(value, dict):
                value = value.get("url") or value.get("audio_url")
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
        return ""

    @staticmethod
    def _encode_image_to_data_uri(image_path: str) -> str:
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Reference image not found: {image_path}")
        ext = path.suffix.lower().lstrip(".")
        image_data = path.read_bytes()
        mime_types = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp"}
        mime_type = mime_types.get(ext, "image/jpeg")
        if len(image_data) > MAX_INLINE_IMAGE_BYTES:
            try:
                from PIL import Image

                image = Image.open(path)
                longest = max(image.size)
                if longest > MAX_REFERENCE_LONGEST_EDGE:
                    scale = MAX_REFERENCE_LONGEST_EDGE / longest
                    image = image.resize((int(image.width * scale), int(image.height * scale)), Image.LANCZOS)
                buffer = io.BytesIO()
                image.convert("RGB").save(buffer, format="JPEG", quality=90)
                image_data = buffer.getvalue()
                mime_type = "image/jpeg"
            except Exception:
                pass
        return f"data:{mime_type};base64,{base64.b64encode(image_data).decode('ascii')}"

    async def _download_video(self, url: str, task_id: str) -> str:
        filename = f"seedance_{task_id[-12:]}_{uuid.uuid4().hex[:6]}.mp4"
        output_path = OUTPUT_DIR / filename
        async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
            response = await client.get(url)
            if response.status_code == 403:
                response = await client.get(url, headers=self._auth_headers())
            response.raise_for_status()
            output_path.write_bytes(response.content)
        print(f"  Downloaded Seedance video: {filename}")
        return str(output_path)

    async def _download_last_frame(self, url: str, task_id: str) -> str:
        filename = f"seedance_{task_id[-12:]}_{uuid.uuid4().hex[:6]}_tail.jpg"
        output_path = OUTPUT_DIR / filename
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            response = await client.get(url)
            if response.status_code == 403:
                response = await client.get(url, headers=self._auth_headers())
            response.raise_for_status()
            output_path.write_bytes(response.content)
        return str(output_path)

    async def _download_audio(self, url: str, task_id: str) -> str:
        """Download a provider audio artifact when it is returned separately."""
        suffix = Path(url.split("?", 1)[0]).suffix.lower() or ".mp3"
        if suffix not in {".mp3", ".wav", ".m4a", ".aac", ".ogg"}:
            suffix = ".mp3"
        filename = f"seedance_{task_id[-12:]}_{uuid.uuid4().hex[:6]}{suffix}"
        output_path = OUTPUT_DIR / filename
        async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
            response = await client.get(url)
            if response.status_code == 403:
                response = await client.get(url, headers=self._auth_headers())
            response.raise_for_status()
            output_path.write_bytes(response.content)
        return str(output_path)

    @staticmethod
    def _has_audio_stream(path: str) -> bool:
        """Detect an embedded audio stream without requiring ffprobe on PATH."""
        try:
            from moviepy import VideoFileClip

            clip = VideoFileClip(path)
            present = clip.audio is not None
            clip.close()
            return bool(present)
        except Exception:
            return False
