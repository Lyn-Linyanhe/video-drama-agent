"""Video generation agent."""

import asyncio
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

from src.api_clients.base import VideoAPIClient
from src.api_clients.mock import MockVideoClient
from src.config import (
    GENERATION_TIMEOUT,
    MAX_CONCURRENT_GENERATIONS,
    MAX_RETRIES,
    OUTPUT_DIR,
)
from src.schemas.assets import GeneratedAssets, ShotAsset
from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.prompt import PromptPolicy
from src.services.prompt_composer import compose_video_prompt, video_structure_instruction
from src.services.timed_prompt_composer import apply_timed_prompt_structure, build_timed_shot_prompt
from src.services.media_quality import image_allows_automatic_reference
from src.utils.visual_asset_manifest import (
    binding_for_scene,
    on_screen_character_names,
    prop_ids_for_scene,
    props_for_scene,
    repair_asset_manifest,
    representative_scene_id,
    state_for_scene,
    validate_asset_manifest,
)


class VideoGenerationAgent:
    """Orchestrates video generation across API clients."""

    def __init__(
        self,
        api_client: Optional[VideoAPIClient] = None,
        max_concurrent: int = MAX_CONCURRENT_GENERATIONS,
        max_retries: int = MAX_RETRIES,
    ):
        self.api_client = api_client or MockVideoClient()
        self.max_concurrent = max_concurrent
        self.max_retries = max_retries
        self._semaphore = asyncio.Semaphore(max_concurrent)

    @staticmethod
    def _native_audio_requested_for_profile(creative_profile) -> bool:
        """Resolve the project audio choice for providers with native audio."""
        mode = str(getattr(creative_profile, "audio_mode", "auto") or "auto").strip().lower()
        return mode != "external_tts"

    async def run(
        self,
        prompts: GenerationPrompts,
        character_images=None,
        scene_images=None,
        script=None,
        visual_design=None,
        scene_vision_descriptions=None,
        project_id: str = "",
        prop_images=None,
        prompt_policy: PromptPolicy | None = None,
        creative_profile=None,
    ) -> GeneratedAssets:
        start_time = time.time()
        total_cost = 0.0

        repair_asset_manifest(script)
        manifest_errors = validate_asset_manifest(script)
        if manifest_errors:
            raise ValueError("视觉资产清单不完整：" + "；".join(manifest_errors[:5]))

        if visual_design:
            self._inject_character_descriptions(prompts, visual_design, script)
        if scene_vision_descriptions:
            self._inject_scene_descriptions(prompts, scene_vision_descriptions)
        self._inject_prop_descriptions(prompts, script)
        apply_timed_prompt_structure(prompts.shots, script)

        reference_map = self._build_reference_bundle(prompts, character_images, scene_images, script, prop_images)

        for shot in prompts.shots:
            if reference_map.get(shot.scene_id):
                shot.motion_strength = self._motion_strength_for_shot(shot, has_references=True)

        async def generate_single(shot: ShotPrompt) -> ShotAsset:
            async with self._semaphore:
                refs = reference_map.get(shot.scene_id, [])
                shot = self._composed_shot(
                    prompts,
                    shot,
                    prompt_policy,
                    creative_profile=creative_profile,
                )
                return await self._generate_with_retry(
                    shot,
                    refs,
                    visual_design=visual_design,
                    scene_vision_descriptions=scene_vision_descriptions,
                    script=script,
                    generate_audio=self._native_audio_requested_for_profile(creative_profile),
                )

        shot_assets = await asyncio.gather(
            *[generate_single(shot) for shot in prompts.shots],
            return_exceptions=True,
        )

        results: list[ShotAsset] = []
        failed_indices: list[int] = []
        for i, asset in enumerate(shot_assets):
            if isinstance(asset, Exception):
                results.append(
                    ShotAsset(
                        scene_id=prompts.shots[i].scene_id,
                        success=False,
                        error_message=str(asset),
                    )
                )
                failed_indices.append(i)
            else:
                results.append(asset)
                if not asset.success:
                    failed_indices.append(i)

        if failed_indices:
            print(f"  重试 {len(failed_indices)} 个失败镜头...")
            for idx in failed_indices:
                shot = self._composed_shot(
                    prompts,
                    prompts.shots[idx],
                    prompt_policy,
                    creative_profile=creative_profile,
                )
                refs = reference_map.get(shot.scene_id, [])
                shot.motion_strength = self._retry_motion_strength(shot.motion_strength, bool(refs))
                for _ in range(3):
                    await asyncio.sleep(2)
                    asset = await self._retry_loop(shot, refs, None)
                    if asset.success:
                        results[idx] = asset
                        print(f"  镜头 {shot.scene_id} 重试成功")
                        break
                if not results[idx].success:
                    print(f"  镜头 {shot.scene_id} 最终失败")

        # API clients download clips to OUTPUT_DIR. Move successful clips into
        # the owning project so they remain discoverable after the project is reopened.
        for asset in results:
            if asset.success:
                asset.local_path = self._store_project_video(asset, project_id)

        for asset in results:
            if asset.success:
                total_cost += await self.api_client.get_cost_estimate(int(asset.duration_sec))

        elapsed = time.time() - start_time
        final_success = sum(1 for a in results if a.success)
        print(f"    最终成功 {final_success}/{len(results)}")

        return GeneratedAssets(
            shots=results,
            total_cost=total_cost,
            generation_time_sec=elapsed,
            all_success=all(a.success for a in results),
        )

    async def generate_shot(
        self,
        shot: ShotPrompt,
        reference_paths: list[str],
        *,
        reference_labels: list[str] | None = None,
        visual_design=None,
        scene_vision_descriptions=None,
        script=None,
        project_id: str = "",
        version_id: str = "",
        generation_prompts: GenerationPrompts | None = None,
        prompt_policy: PromptPolicy | None = None,
        request_is_prepared: bool = False,
        provider_prompt: str = "",
        reference_mode: str = "auto",
        last_frame_reference: str | None = None,
        return_last_frame: bool = False,
        generate_audio: bool | None = None,
        creative_profile=None,
    ) -> ShotAsset:
        """Generate one storyboard shot with an explicit ordered reference bundle."""
        if generate_audio is None and creative_profile is not None:
            generate_audio = self._native_audio_requested_for_profile(creative_profile)
        if not request_is_prepared:
            shot, _ = self.prepare_shot_request(
                shot,
                generation_prompts=generation_prompts,
                prompt_policy=prompt_policy,
                visual_design=visual_design,
                scene_vision_descriptions=scene_vision_descriptions,
                script=script,
                creative_profile=creative_profile,
            )
        if reference_paths:
            shot = shot.model_copy(update={
                "motion_strength": self._motion_strength_for_shot(shot, has_references=True),
            })
        async with self._semaphore:
            asset = await self._generate_with_retry(
                shot,
                reference_paths,
                reference_labels,
                visual_design,
                scene_vision_descriptions,
                script,
                provider_prompt=provider_prompt,
                reference_mode=reference_mode,
                last_frame_reference=last_frame_reference,
                return_last_frame=return_last_frame,
                generate_audio=generate_audio,
            )
        if asset.success:
            asset.local_path = self._store_project_video(
                asset,
                project_id,
                version_id=version_id or uuid.uuid4().hex[:12],
            )
        return asset

    def prepare_shot_request(
        self,
        shot: ShotPrompt,
        *,
        generation_prompts: GenerationPrompts | None,
        prompt_policy: PromptPolicy | None,
        visual_design=None,
        scene_vision_descriptions=None,
        script=None,
        creative_profile=None,
    ) -> tuple[ShotPrompt, dict]:
        """Compose a provider request without mutating persisted storyboard prompts."""
        isolated = shot.model_copy(deep=True)
        prompts = generation_prompts.model_copy(deep=True) if generation_prompts else GenerationPrompts(
            api_provider=self.api_client.provider_name,
            shots=[isolated],
        )
        prompts.shots = [
            isolated if item.scene_id == isolated.scene_id else item
            for item in prompts.shots
        ]
        self._inject_shot_context(
            isolated,
            visual_design,
            scene_vision_descriptions,
            script,
        )
        provider = (self.api_client.provider_name or "").lower()
        provider_duration_cap = {"happyhorse": 15.0, "wan": 15.0, "kling": 10.0}.get(provider)
        if provider_duration_cap is not None:
            isolated.duration_sec = int(min(isolated.duration_sec, provider_duration_cap))
        isolated.prompt = build_timed_shot_prompt(
            isolated,
            script,
            sync_script_duration=False,
            duration_cap_sec=provider_duration_cap,
        )
        effective = compose_video_prompt(
            self.api_client.provider_name,
            prompts,
            isolated,
            prompt_policy,
            creative_profile=creative_profile,
        )
        return isolated.model_copy(update={
            "prompt": effective["provider_prompt"],
            "negative_prompt": effective["provider_negative_prompt"],
        }), effective

    def _composed_shot(
        self,
        prompts: GenerationPrompts,
        shot: ShotPrompt,
        prompt_policy: PromptPolicy | None,
        creative_profile=None,
    ) -> ShotPrompt:
        effective = compose_video_prompt(
            self.api_client.provider_name,
            prompts,
            shot,
            prompt_policy,
            creative_profile=creative_profile,
        )
        return shot.model_copy(update={
            "prompt": effective["provider_prompt"],
            "negative_prompt": effective["provider_negative_prompt"],
        })

    def _finalize_provider_prompt(
        self,
        base_prompt: str,
        shot: ShotPrompt,
        reference_paths: list[str],
        reference_labels: list[str] | None = None,
        *,
        visual_design=None,
        scene_vision_descriptions=None,
        script=None,
    ) -> str:
        prompt = (base_prompt or "").strip()
        if not reference_paths or self._has_reference_binding(prompt):
            return prompt
        binding = self._build_reference_binding_prompt(
            shot,
            reference_paths,
            reference_labels,
            visual_design=visual_design,
            scene_vision_descriptions=scene_vision_descriptions,
            script=script,
        )
        if not binding:
            return prompt
        return "\n".join(part for part in [prompt, binding] if part).strip()

    @staticmethod
    def _motion_strength_for_shot(shot: ShotPrompt, has_references: bool) -> float:
        """Choose motion that protects identity instead of always pushing references hard."""
        if not has_references:
            return shot.motion_strength

        current = max(0.0, min(float(shot.motion_strength), 1.0))
        text = " ".join([
            str(shot.prompt or ""),
            str(shot.negative_prompt or ""),
        ]).casefold()
        target = 0.76
        if any(token in text for token in (
            "dialogue", "speaks", "whispers", "conversation",
            "对话", "说话", "低声", "独白", "凝视", "注视",
        )):
            target = 0.62
        if any(token in text for token in (
            "close-up", "closeup", "close shot", "portrait",
            "特写", "近景", "脸部", "面部",
        )):
            target = min(target, 0.66)
        if any(token in text for token in (
            "walk", "turn", "raise", "push", "pull", "open", "enter", "exit",
            "走", "转身", "抬手", "推", "拉", "打开", "进入", "离开",
        )):
            target = max(target, 0.78)
        if any(token in text for token in (
            "run", "chase", "fight", "dash", "tracking", "handheld", "fast pan",
            "奔跑", "追逐", "打斗", "冲", "跟拍", "手持", "快速摇",
        )):
            target = max(target, 0.88)

        if abs(current - 0.7) <= 0.001:
            return round(target, 2)
        if current > 0.7:
            return round(max(current, target), 2)
        return round(current, 2)

    @staticmethod
    def _retry_motion_strength(current: float, has_references: bool) -> float:
        if has_references:
            return round(min(max(float(current), 0.0), 0.66), 2)
        return round(min(max(float(current), 0.0), 0.7), 2)

    @staticmethod
    def _store_project_video(
        asset: ShotAsset,
        project_id: str,
        version_id: str = "",
    ) -> str | None:
        """Move a downloaded clip into the project's video asset directory."""
        if not asset.local_path or not project_id:
            return asset.local_path

        source = Path(asset.local_path)
        if not source.is_file():
            return asset.local_path

        provider = re.sub(r"[^a-z0-9]+", "_", (asset.api_used or "video").lower()).strip("_") or "video"
        source_id = re.sub(r"[^a-zA-Z0-9]+", "_", source.stem).strip("_")[-40:]
        version_suffix = re.sub(r"[^a-zA-Z0-9_-]+", "_", version_id).strip("_")[:40]
        filename = f"{provider}_{source_id}{source.suffix.lower()}"
        destination_dir = OUTPUT_DIR / project_id / "assets" / "videos" / f"shot_{asset.scene_id:03d}"
        if version_suffix:
            destination_dir = destination_dir / version_suffix
        destination = destination_dir / filename
        destination.parent.mkdir(parents=True, exist_ok=True)
        native_path = Path(asset.native_audio_path) if asset.native_audio_path else None
        native_is_video = bool(native_path and native_path.is_file() and native_path.resolve() == source.resolve())
        try:
            if source.resolve() != destination.resolve():
                shutil.move(str(source), str(destination))
            if native_path and native_path.is_file() and not native_is_video:
                native_destination = destination.parent / f"native_audio{native_path.suffix.lower() or '.mp3'}"
                if native_path.resolve() != native_destination.resolve():
                    shutil.move(str(native_path), str(native_destination))
                asset.native_audio_path = str(native_destination)
            elif native_is_video:
                asset.native_audio_path = str(destination)
            return str(destination)
        except OSError as exc:
            print(f"  Warning: could not organize video asset {source.name}: {exc}")
            return asset.local_path

    async def _generate_with_retry(
        self,
        shot: ShotPrompt,
        reference_paths: list[str],
        reference_labels: list[str] | None = None,
        visual_design=None,
        scene_vision_descriptions=None,
        script=None,
        provider_prompt: str = "",
        reference_mode: str = "auto",
        last_frame_reference: str | None = None,
        return_last_frame: bool = False,
        generate_audio: bool | None = None,
    ) -> ShotAsset:
        prompt = self._finalize_provider_prompt(
            provider_prompt or shot.prompt,
            shot,
            reference_paths,
            reference_labels,
            visual_design=visual_design,
            scene_vision_descriptions=scene_vision_descriptions,
            script=script,
        )

        if shot.sub_shots and not provider_prompt:
            sub_assets = []
            for sub in shot.sub_shots:
                sub_prompt = self._finalize_provider_prompt(
                    sub.prompt,
                    sub,
                    reference_paths,
                    reference_labels,
                    visual_design=visual_design,
                    scene_vision_descriptions=scene_vision_descriptions,
                    script=script,
                )
                asset = await self._retry_loop(
                    sub,
                    reference_paths,
                    sub_prompt,
                    reference_mode=reference_mode,
                    last_frame_reference=last_frame_reference,
                    return_last_frame=return_last_frame,
                    generate_audio=generate_audio,
                )
                sub_assets.append(asset)
            for asset in sub_assets:
                if asset.success:
                    return asset
            return sub_assets[0] if sub_assets else ShotAsset(
                scene_id=shot.scene_id,
                success=False,
                error_message="All sub-shots failed",
            )

        return await self._retry_loop(
            shot,
            reference_paths,
            prompt,
            reference_mode=reference_mode,
            last_frame_reference=last_frame_reference,
            return_last_frame=return_last_frame,
            generate_audio=generate_audio,
        )

    async def _retry_loop(
        self,
        shot: ShotPrompt,
        reference_paths: list[str],
        prompt: str | None = None,
        *,
        reference_mode: str = "auto",
        last_frame_reference: str | None = None,
        return_last_frame: bool = False,
        generate_audio: bool | None = None,
    ) -> ShotAsset:
        last_error = ""
        final_prompt = prompt or shot.prompt
        image_reference = reference_paths[0] if reference_paths else None

        for attempt in range(1, self.max_retries + 1):
            attempt_prompt = self._prompt_for_attempt(final_prompt, attempt, bool(reference_paths))
            attempt_motion = (
                shot.motion_strength if attempt == 1
                else self._retry_motion_strength(shot.motion_strength, bool(reference_paths))
            )
            try:
                request = {
                    "prompt": attempt_prompt,
                    "negative_prompt": shot.negative_prompt,
                    "duration_sec": shot.duration_sec,
                    "seed": shot.seed,
                    "motion_strength": attempt_motion,
                    "image_reference": image_reference,
                    "image_references": reference_paths or None,
                }
                if self.api_client.provider_name == "seedance":
                    request.update({
                        "reference_mode": reference_mode,
                        "last_frame_reference": last_frame_reference,
                        "return_last_frame": return_last_frame,
                        "generate_audio": True if generate_audio is None else bool(generate_audio),
                    })
                elif self.api_client.provider_name == "happyhorse":
                    request["generate_audio"] = True if generate_audio is None else bool(generate_audio)
                result = await asyncio.wait_for(
                    self.api_client.generate_video(**request),
                    timeout=GENERATION_TIMEOUT,
                )

                if result.success:
                    return ShotAsset(
                        scene_id=shot.scene_id,
                        video_url=result.video_url,
                        local_path=result.local_path,
                        duration_sec=result.duration_sec,
                        api_used=self.api_client.provider_name,
                        success=True,
                        last_frame_url=getattr(result, "last_frame_url", ""),
                        last_frame_path=getattr(result, "last_frame_path", None),
                        audio_source=getattr(result, "audio_source", ""),
                        native_audio_enabled=bool(getattr(result, "native_audio_enabled", False)),
                        native_audio_path=getattr(result, "native_audio_path", None),
                        audio_present=bool(getattr(result, "audio_present", False)),
                    )

                last_error = result.error_message
            except asyncio.TimeoutError:
                last_error = f"Generation timed out after {GENERATION_TIMEOUT}s"

            if (
                self.api_client.provider_name == "seedance"
                and getattr(self.api_client, "is_non_retriable_error", lambda _message: False)(last_error)
            ):
                break
            if attempt < self.max_retries:
                await asyncio.sleep(2**attempt)

        return ShotAsset(
            scene_id=shot.scene_id,
            success=False,
            error_message=last_error or "Max retries exceeded",
        )

    @staticmethod
    def _has_reference_binding(prompt: str) -> bool:
        normalized = prompt or ""
        return "[Reference binding]" in normalized or "[Reference hierarchy]" in normalized

    def _build_reference_binding_prompt(
        self,
        shot: ShotPrompt,
        reference_paths: list[str],
        reference_labels: list[str] | None = None,
        *,
        visual_design=None,
        scene_vision_descriptions=None,
        script=None,
    ) -> str:
        if not reference_paths:
            return ""

        scene = self._scene_for_id(script, shot.scene_id)
        scene_desc = ""
        if scene_vision_descriptions:
            scene_desc = scene_vision_descriptions.get(shot.scene_id, "")
        if not scene_desc and scene:
            scene_desc = scene.location

        scene_visual = self._scene_visual_for_id(visual_design, shot.scene_id)
        scene_style = ""
        if scene_visual:
            scene_style = ", ".join(
                x for x in [scene_visual.composition, scene_visual.lighting, scene_visual.color_tone] if x
            )

        lines = [
            "[Reference binding]",
            "Keep the upload order fixed: Image 1, Image 2, Image 3.",
        ]
        tag_lines = []
        for idx, path in enumerate(reference_paths[:9], start=1):
            label = ""
            if reference_labels and idx - 1 < len(reference_labels):
                label = str(reference_labels[idx - 1] or "").strip()
            if not label:
                label = self._infer_reference_label(path, idx, len(reference_paths))
            for tag in self._reference_tags_from_label(label):
                tag_lines.append(f"{tag} maps to Image {idx}: {label}.")
            lines.append(f"Image {idx}: {label}.")
        if tag_lines:
            lines.insert(2, "[Reference tags]")
            lines[3:3] = tag_lines

        if scene_desc or scene_style:
            scene_line = "Image 1 is the scene/camera anchor"
            if scene_desc:
                scene_line += f"; scene: {scene_desc}"
            if scene_style:
                scene_line += f"; style: {scene_style}"
            lines.append(scene_line + ".")

        lines.extend([
            "Keep the shot prompt above as the primary instruction.",
            "Use the reference images only to lock identity, spatial relation, and state continuity.",
            "Do not mix different reference views, costumes, backgrounds, or prop states into a single impossible frame.",
        ])
        return "\n".join(lines)

    @staticmethod
    def _infer_reference_label(path: str, index: int, total: int) -> str:
        normalized = Path(path).stem.lower()
        if index == 1:
            if any(token in normalized for token in ("tail", "prev", "previous", "continuity")):
                return "previous-frame tail reference"
            if VideoGenerationAgent._looks_like_scene_reference(path):
                return "scene reference"
            if total == 1:
                return "main reference"
        if any(token in normalized for token in ("portrait", "face", "character", "hero", "actor")):
            return "character reference"
        if any(token in normalized for token in ("prop", "object", "item")):
            return "prop reference"
        if any(token in normalized for token in ("scene", "wide", "background", "set")):
            return "scene reference"
        return "reference"

    @staticmethod
    def _reference_tag_from_label(label: str) -> str:
        # Explicit asset tokens are user-facing identifiers, so they may use
        # Chinese names, colons, hyphens, and other non-whitespace characters.
        # The old ASCII-only expression silently truncated tokens such as
        # ``@Hero-front-full`` to ``@Hero`` and rejected ``@角色:正面``.
        match = re.match(r"\s*(@[^\s=]+)", label or "")
        return match.group(1) if match else ""

    @staticmethod
    def _reference_tags_from_label(label: str) -> list[str]:
        """Return the visible @ token and the frozen internal tag, if both exist."""
        prefix = str(label or "").split("=", 1)[0]
        tags: list[str] = []
        for match in re.finditer(r"@[^\s=]+", prefix):
            tag = match.group(0)
            if tag not in tags:
                tags.append(tag)
        return tags

    @staticmethod
    def _prompt_for_attempt(prompt: str, attempt: int, has_references: bool) -> str:
        if attempt <= 1:
            return prompt
        retry_lines = [
            "[Retry correction]",
            "Regenerate the same shot, not a new shot.",
            "Use steadier motion, simpler action timing and fewer newly invented details.",
        ]
        if has_references:
            retry_lines.extend([
                "Strictly preserve the reference identity, costume, scene layout, lighting direction and prop state.",
                "Reduce flicker, face drift, clothing swaps, warped hands, extra limbs and object deformation.",
            ])
        return "\n".join(part.strip() for part in [prompt, "\n".join(retry_lines)] if part and part.strip())

    def _inject_character_descriptions(self, prompts, visual_design, script) -> None:
        if not visual_design:
            return

        char_descs = (
            visual_design.global_character_descriptions_cn
            or visual_design.global_character_descriptions
        )
        for shot in prompts.shots:
            if "【角色锚点】" in shot.prompt:
                continue
            names = on_screen_character_names(script, shot.scene_id)
            char_lines = []
            for name in names:
                desc = char_descs.get(name, "")
                if desc:
                    char_lines.append(f"{name}: {desc}")

            if char_lines:
                shot.prompt = (
                    "【角色锚点】"
                    + "；".join(char_lines)
                    + "。全程保持上述角色的五官比例、发型、服装颜色与材质、体型和标志特征一致。\n"
                    + shot.prompt
                )
                print(f"  镜头 {shot.scene_id}: 注入角色外貌描述")

    def _inject_scene_descriptions(self, prompts, scene_vision_descriptions: dict) -> None:
        for shot in prompts.shots:
            if "【场景锚点】" in shot.prompt:
                continue
            desc = scene_vision_descriptions.get(shot.scene_id)
            if desc:
                shot.prompt = f"【场景锚点】{desc}。保持背景建筑、陈设、时间天气和主光方向连续。\n{shot.prompt}"
                print(f"  镜头 {shot.scene_id}: 注入 Vision 场景描述")

    def _inject_prop_descriptions(self, prompts, script) -> None:
        """Add bound prop anchors even when the corresponding image is unavailable."""
        for shot in prompts.shots:
            self._inject_prop_description(shot, script)

    def _inject_prop_description(self, shot: ShotPrompt, script) -> None:
        if not script or "【道具锚点】" in shot.prompt:
            return
        props = props_for_scene(script, shot.scene_id)
        if not props:
            return
        prop_lines = [
            f"{prop.name}：{prop.description or '外观以资产设定为准'}"
            for prop in props
        ]
        binding = binding_for_scene(script, shot.scene_id)
        state_note = f"本镜头状态：{binding.visible_state_notes}。" if binding and binding.visible_state_notes else ""
        shot.prompt = (
            "【道具锚点】"
            + "；".join(prop_lines)
            + "。道具必须在画面中清晰可辨，保持材质、颜色、尺寸和状态一致；"
            "按剧情表现人物与道具的接触、拿取或摆放，镜头结束时保留符合剧情的状态。\n"
            + state_note
            + shot.prompt
        )

    def _inject_shot_context(self, shot, visual_design, scene_vision_descriptions, script) -> None:
        """Apply the same Chinese context to the workspace's single-shot path."""
        self._inject_character_descriptions(
            GenerationPrompts(api_provider=self.api_client.provider_name, shots=[shot]),
            visual_design,
            script,
        )
        if scene_vision_descriptions:
            self._inject_scene_descriptions(
                GenerationPrompts(api_provider=self.api_client.provider_name, shots=[shot]),
                scene_vision_descriptions,
            )
        self._inject_prop_description(shot, script)

    def _build_multi_reference_prompt_legacy(
        self,
        shot: ShotPrompt,
        reference_paths: list[str],
        reference_labels: list[str] | None = None,
        visual_design=None,
        scene_vision_descriptions=None,
        script=None,
    ) -> str:
        scene = self._scene_for_id(script, shot.scene_id)
        scene_desc = ""
        if scene_vision_descriptions:
            scene_desc = scene_vision_descriptions.get(shot.scene_id, "")
        if not scene_desc and scene:
            scene_desc = scene.location

        scene_visual = self._scene_visual_for_id(visual_design, shot.scene_id)
        scene_style = ""
        if scene_visual:
            scene_style = ", ".join(
                x for x in [scene_visual.composition, scene_visual.lighting, scene_visual.color_tone] if x
            )

        lines = [
            "竖屏9:16，电影级质感，柔和荧光灯光效。",
            "[图层分工]",
        ]

        if reference_labels:
            for idx, label in enumerate(reference_labels[:len(reference_paths)], start=1):
                lines.append(f"参考图{idx}：{label}。")
            first_ref_is_scene = False
        else:
            first_ref_is_scene = bool(reference_paths) and self._looks_like_scene_reference(reference_paths[0])
        if first_ref_is_scene:
            if scene_style:
                lines.append(f"图1为{scene_desc or '背景'}全景，{scene_style}。")
            else:
                lines.append(f"图1为{scene_desc or '背景'}全景。")

        char_descs = [] if reference_labels else self._character_reference_descriptions(shot, visual_design, script)
        max_character_refs = max(0, len(reference_paths) - 1) if first_ref_is_scene else len(reference_paths)
        char_descs = char_descs[:max_character_refs]
        for idx, desc in enumerate(char_descs, start=2 if first_ref_is_scene else 1):
            lines.append(f"图{idx}为{desc}。")

        lines.extend(
            [
                "[动态过程]",
                "[Reference hierarchy]",
                "Use the continuity tail or scene reference to lock camera position, spatial layout, lighting direction and action continuity.",
                "Use character references only to lock identity, face structure, hairstyle, costume silhouette, colors and body proportion.",
                "Use prop references only to lock object shape, material, color, scale, wear marks and current state.",
                "Do not merge different reference views into one impossible face, costume, room layout or prop design.",
                video_structure_instruction(shot),
                self._compact_reference_prompt(shot.prompt),
                "[一致性约束]",
                "全程保持各参考图对应角色的五官比例、发型、服装款式和材质细节不变。",
                "背景与人物必须自然融合，禁止出现抠图边缘、换脸、换衣服、背景突变。",
            ]
        )

        return " ".join(lines)

    def _character_reference_descriptions(self, shot: ShotPrompt, visual_design=None, script=None) -> list[str]:
        names = []
        if script:
            names = on_screen_character_names(script, shot.scene_id)

        if not names and visual_design and visual_design.global_character_descriptions:
            names = list(visual_design.global_character_descriptions.keys())

        descriptions = []
        cn_descs = getattr(visual_design, "global_character_descriptions_cn", {}) or {}
        en_descs = getattr(visual_design, "global_character_descriptions", {}) or {}
        for name in names:
            desc = cn_descs.get(name) or en_descs.get(name) or name
            descriptions.append(desc)
        return descriptions

    def _scene_for_id(self, script, scene_id: int):
        if not script:
            return None
        for scene in script.scenes:
            if scene.scene_id == scene_id:
                return scene
        return None

    def _scene_visual_for_id(self, visual_design, scene_id: int):
        if not visual_design:
            return None
        for scene in visual_design.scenes:
            if scene.scene_id == scene_id:
                return scene
        return None

    @staticmethod
    def _looks_like_scene_reference(path: str) -> bool:
        normalized = path.replace("\\", "/").lower()
        return "/scenes/" in normalized or "/scene_" in normalized or normalized.endswith("/wide.png")

    def _build_reference_bundle(
        self,
        prompts: GenerationPrompts,
        character_images=None,
        scene_images=None,
        script=None,
        prop_images=None,
    ) -> dict[int, list[str]]:
        char_paths: dict[str, dict[str, str]] = {}
        if character_images:
            for img in character_images.characters:
                path = getattr(img, "image_path", "") or ""
                if path and Path(path).exists() and image_allows_automatic_reference(img):
                    char_paths.setdefault(img.character_name, {})
                    char_paths[img.character_name][img.view_type] = path

        scene_candidates = []
        if scene_images:
            for img in scene_images.scenes:
                path = getattr(img, "image_path", "") or ""
                if path and Path(path).exists() and image_allows_automatic_reference(img):
                    if self._is_scene_reference_view(img.view_type):
                        scene_candidates.append(img)

        prop_candidates: dict[str, list] = {}
        if prop_images:
            for img in prop_images.props:
                if self._is_valid_video_prop_reference(img):
                    prop_candidates.setdefault(img.prop_id, []).append(img)

        ref_map: dict[int, list[str]] = {}
        for shot in prompts.shots:
            refs: list[str] = []
            binding = binding_for_scene(script, shot.scene_id)
            location_id = binding.location_id if binding else ""
            state_id = getattr(binding, "state_id", "") if binding else ""
            scene_reference = self._choose_scene_reference(
                scene_candidates,
                location_id=location_id,
                state_id=state_id,
                preferred_view=self._preferred_scene_view_for_shot(script, shot.scene_id),
                scene_id=representative_scene_id(script, shot.scene_id),
            )
            if scene_reference and scene_reference.image_path not in refs:
                refs.append(scene_reference.image_path)

            for name in on_screen_character_names(script, shot.scene_id):
                candidate_by_view = char_paths.get(name, {})
                for chosen in self._character_reference_paths(candidate_by_view):
                    if chosen not in refs:
                        refs.append(chosen)
                    if len(refs) >= 9:
                        break
                if len(refs) >= 9:
                    break

            for prop_id in prop_ids_for_scene(script, shot.scene_id):
                prop_reference = self._choose_prop_reference(
                    prop_candidates.get(prop_id, []),
                    self._preferred_prop_view_for_shot(script, shot.scene_id),
                )
                if prop_reference and prop_reference.image_path not in refs:
                    refs.append(prop_reference.image_path)
                if len(refs) >= 9:
                    break

            if not refs and not getattr(script, "asset_manifest", None):
                for candidate_by_view in char_paths.values():
                    for chosen in self._character_reference_paths(candidate_by_view):
                        if chosen not in refs:
                            refs.append(chosen)
                        if len(refs) >= 9:
                            break
                    if refs:
                        break

            ref_map[shot.scene_id] = refs

        return ref_map

    def _build_reference_identity_bundle(
        self,
        prompts: GenerationPrompts,
        character_images=None,
        scene_images=None,
        script=None,
        prop_images=None,
    ) -> dict[int, list[tuple[str, str, str]]]:
        """Return the same ordered bundle as paths, but keyed by stable asset identity."""
        character_refs: dict[str, dict[str, tuple[str, str, str]]] = {}
        if character_images:
            for image in character_images.characters:
                if (
                    image.image_path
                    and Path(image.image_path).exists()
                    and image_allows_automatic_reference(image)
                ):
                    character_refs.setdefault(image.character_name, {})[image.view_type] = (
                        "character", image.character_name, image.view_type,
                    )

        scene_refs = []
        if scene_images:
            for image in scene_images.scenes:
                if (
                    image.image_path
                    and Path(image.image_path).exists()
                    and image_allows_automatic_reference(image)
                    and self._is_scene_reference_view(image.view_type)
                ):
                    scene_refs.append(image)

        prop_refs: dict[str, list] = {}
        if prop_images:
            for image in prop_images.props:
                if self._is_valid_video_prop_reference(image):
                    prop_refs.setdefault(image.prop_id, []).append(image)

        bundle: dict[int, list[tuple[str, str, str]]] = {}
        for shot in prompts.shots:
            references: list[tuple] = []
            binding = binding_for_scene(script, shot.scene_id)
            location_id = binding.location_id if binding else ""
            state_id = getattr(binding, "state_id", "") if binding else ""
            scene_reference = self._choose_scene_reference(
                scene_refs,
                location_id=location_id,
                state_id=state_id,
                preferred_view=self._preferred_scene_view_for_shot(script, shot.scene_id),
                scene_id=representative_scene_id(script, shot.scene_id),
            )
            if scene_reference:
                scene_asset_id = scene_reference.location_id or str(scene_reference.scene_id)
                if scene_reference.state_id:
                    references.append((
                        "scene",
                        scene_asset_id,
                        scene_reference.state_id,
                        scene_reference.view_type,
                    ))
                else:
                    references.append(("scene", scene_asset_id, scene_reference.view_type))
            for name in on_screen_character_names(script, shot.scene_id):
                for reference in self._character_reference_paths(character_refs.get(name, {})):
                    if reference and reference not in references:
                        references.append(reference)
                    if len(references) >= 9:
                        break
                if len(references) >= 9:
                    break
            for prop_id in prop_ids_for_scene(script, shot.scene_id):
                prop_reference = self._choose_prop_reference(
                    prop_refs.get(prop_id, []),
                    self._preferred_prop_view_for_shot(script, shot.scene_id),
                )
                if prop_reference:
                    reference = ("prop", prop_reference.prop_id, prop_reference.view_type)
                    if reference not in references:
                        references.append(reference)
                if len(references) >= 9:
                    break
            if not references and not getattr(script, "asset_manifest", None):
                for candidates in character_refs.values():
                    for reference in self._character_reference_paths(candidates):
                        if reference and reference not in references:
                            references.append(reference)
                        if len(references) >= 9:
                            break
                    if references:
                        break
            bundle[shot.scene_id] = references
        return bundle

    @staticmethod
    def _choose_character_reference(candidate_by_view: dict[str, str]) -> str | None:
        for view_type in (
            "front_full", "full_body", "front_half", "portrait",
            "left_profile", "profile", "back_full",
        ):
            path = candidate_by_view.get(view_type)
            if path:
                return path
        return next(iter(candidate_by_view.values()), None)

    @staticmethod
    def _character_reference_paths(candidate_by_view: dict[str, str]) -> list[str]:
        ordered: list[str] = []
        for view_type in (
            "front_half",
            "front_full",
            "left_profile",
            "back_full",
            "portrait",
            "profile",
            "full_body",
        ):
            path = candidate_by_view.get(view_type)
            if path and path not in ordered:
                ordered.append(path)
        for path in candidate_by_view.values():
            if path and path not in ordered:
                ordered.append(path)
        return ordered

    @staticmethod
    def _is_scene_reference_view(view_type: str) -> bool:
        return view_type in {"wide", "medium", "scene_center", "scene_left_45", "scene_right_45", "scene_reverse"} or view_type.startswith("scene_")

    @staticmethod
    def _is_video_prop_reference_view(view_type: str) -> bool:
        return view_type in {"front", "back", "left", "right", "top", "bottom"}

    @staticmethod
    def _is_valid_video_prop_reference(image) -> bool:
        path = getattr(image, "image_path", "") or ""
        if not path or not Path(path).exists():
            return False
        if not VideoGenerationAgent._is_video_prop_reference_view(getattr(image, "view_type", "")):
            return False
        if getattr(image, "is_stale", False) or getattr(image, "needs_confirmation", False):
            return False
        if getattr(image, "view_type", "") == "front":
            return bool(getattr(image, "is_master", False))
        return True

    @staticmethod
    def _choose_prop_reference(candidates: list, preferred_view: str = "front"):
        if not candidates:
            return None
        by_view = {getattr(item, "view_type", ""): item for item in candidates}
        preferred = by_view.get(preferred_view)
        if preferred and preferred_view != "front":
            return preferred
        return by_view.get("front")

    @staticmethod
    def _choose_scene_reference(
        candidate_by_view,
        location_id: str = "",
        state_id: str = "",
        preferred_view: str = "scene_center",
        scene_id: int = 0,
    ):
        if isinstance(candidate_by_view, dict):
            for view_type in (
                preferred_view,
                "scene_center",
                "wide",
                "medium",
                "scene_left_45",
                "scene_right_45",
                "scene_reverse",
            ):
                path = candidate_by_view.get(view_type)
                if path:
                    return path
            for view_type, path in candidate_by_view.items():
                if view_type.startswith("scene_") and path:
                    return path
            return next(iter(candidate_by_view.values()), None)

        candidates = [
            item for item in candidate_by_view or []
            if VideoGenerationAgent._is_usable_scene_reference(item)
        ]
        if not candidates:
            return None

        def same_location(item) -> bool:
            if location_id and getattr(item, "location_id", "") == location_id:
                return True
            if scene_id:
                return getattr(item, "scene_id", 0) == scene_id
            return True

        scoped = [item for item in candidates if same_location(item)]
        if not scoped:
            return None

        strict_state_matches = [item for item in scoped if (getattr(item, "state_id", "") or "") == (state_id or "")]
        if state_id and strict_state_matches:
            scoped = strict_state_matches
        elif state_id:
            scoped = [
                item for item in scoped
                if not (getattr(item, "state_id", "") or "")
                and getattr(item, "view_type", "") in {"wide", "medium"}
            ]
            if not scoped:
                return None
        else:
            has_any_state = any((getattr(item, "state_id", "") or "") for item in scoped)
            if not has_any_state:
                scoped = [item for item in scoped if not getattr(item, "state_id", "")]
                if not scoped:
                    scoped = candidates

        preferred_order = (
            preferred_view,
            "scene_center",
            "scene_left_45",
            "scene_right_45",
            "scene_reverse",
            "wide",
            "medium",
        )
        for view_type in preferred_order:
            for item in scoped:
                if item.view_type == view_type:
                    return item
        for item in scoped:
            if str(getattr(item, "view_type", "")).startswith("scene_"):
                return item
        return scoped[0] if scoped else None

    @staticmethod
    def _is_usable_scene_reference(image) -> bool:
        path = getattr(image, "image_path", "") or ""
        if not path or not Path(path).exists():
            return False
        if getattr(image, "is_stale", False) or getattr(image, "needs_confirmation", False):
            return False
        return VideoGenerationAgent._is_scene_reference_view(getattr(image, "view_type", ""))

    @staticmethod
    def _preferred_scene_view_for_shot(script, scene_id: int) -> str:
        scene = None
        if script:
            scene = next((item for item in getattr(script, "scenes", []) if item.scene_id == scene_id), None)
        cues = " ".join(
            str(part or "") for part in (
                getattr(scene, "camera_direction", ""),
                getattr(scene, "action_description", ""),
                getattr(scene, "location", ""),
            )
        ).casefold()
        if any(token in cues for token in ("向外", "出口", "背后", "反向", "回头", "深处")):
            return "scene_reverse"
        if any(token in cues for token in ("左前", "左侧", "左边", "左")):
            return "scene_left_45"
        if any(token in cues for token in ("右前", "右侧", "右边", "右")):
            return "scene_right_45"
        return "scene_center"

    @staticmethod
    def _preferred_prop_view_for_shot(script, scene_id: int) -> str:
        scene = None
        if script:
            scene = next((item for item in getattr(script, "scenes", []) if item.scene_id == scene_id), None)
        text = " ".join([
            str(getattr(scene, "camera_direction", "") or ""),
            str(getattr(scene, "action_description", "") or ""),
        ]).lower()
        if any(token in text for token in ("back", "rear", "behind", "背面", "背后", "翻到背面", "反面")):
            return "back"
        if any(token in text for token in ("left side", "left-side", "from left", "左侧", "左边", "从左")):
            return "left"
        if any(token in text for token in ("right side", "right-side", "from right", "右侧", "右边", "从右")):
            return "right"
        if any(token in text for token in ("top", "overhead", "from above", "俯拍", "顶部", "上方", "顶面")):
            return "top"
        if any(token in text for token in ("bottom", "underside", "from below", "底部", "底面", "下方")):
            return "bottom"
        return "front"

    def _build_multi_reference_prompt(
        self,
        shot: ShotPrompt,
        reference_paths: list[str],
        reference_labels: list[str] | None = None,
        visual_design=None,
        scene_vision_descriptions=None,
        script=None,
    ) -> str:
        scene = self._scene_for_id(script, shot.scene_id)
        scene_desc = ""
        if scene_vision_descriptions:
            scene_desc = scene_vision_descriptions.get(shot.scene_id, "")
        if not scene_desc and scene:
            scene_desc = scene.location

        scene_visual = self._scene_visual_for_id(visual_design, shot.scene_id)
        scene_style = ""
        if scene_visual:
            scene_style = ", ".join(
                x for x in [scene_visual.composition, scene_visual.lighting, scene_visual.color_tone] if x
            )

        first_label = str(reference_labels[0]).lower() if reference_labels else ""
        continuity_tail = (
            bool(reference_paths)
            and reference_paths[0].replace("\\", "/").lower().endswith("_tail.jpg")
        ) or first_label.startswith("continuity") or "previous frame" in first_label or "上一镜" in first_label
        first_ref_is_scene = bool(reference_paths) and self._looks_like_scene_reference(reference_paths[0])
        # This prompt already contains the complete ordered reference binding.
        # The generation path checks this marker to avoid appending a second,
        # divergent binding block before the provider request.
        lines = ["竖屏9:16，电影级质感，柔和漫射光。", "[Reference binding]"]
        if continuity_tail:
            lines.append("参考图1是上一镜尾帧，必须严格延续上一镜的机位、景别、人物站位、动作进度、光线和时间，不要重新起镜，不要跳切。")
        if reference_labels:
            for idx, label in enumerate(reference_labels[:len(reference_paths)], start=1):
                lines.append(f"参考图{idx}：{label}。")
            tag_lines = []
            for idx, label in enumerate(reference_labels[:len(reference_paths)], start=1):
                for tag in self._reference_tags_from_label(str(label or "")):
                    tag_lines.append(f"{tag} maps to reference image {idx}.")
            if tag_lines:
                lines[1:1] = ["[Reference tags]", *tag_lines]
        elif first_ref_is_scene:
            if scene_style:
                lines.append(f"参考图1为{scene_desc or '背景'}全景，{scene_style}。")
            else:
                lines.append(f"参考图1为{scene_desc or '背景'}全景。")
        if first_ref_is_scene or continuity_tail:
            lines.append("镜头关系必须延续参考图的空间位置和运动方向，人物转身、回头、推门、举手等动作要和参考图一致。")

        char_descs = [] if reference_labels else self._character_reference_descriptions(shot, visual_design, script)
        max_character_refs = max(0, len(reference_paths) - 1) if (first_ref_is_scene or continuity_tail) else len(reference_paths)
        char_descs = char_descs[:max_character_refs]
        for idx, desc in enumerate(char_descs, start=2 if (first_ref_is_scene or continuity_tail) else 1):
            lines.append(f"参考图{idx}为{desc}。")

        lines.extend(
            [
                "[图层分工]",
                "[Reference hierarchy]",
                "Use the continuity tail or scene reference to lock camera position, spatial layout, lighting direction and action continuity.",
                "Use character references only to lock identity, face structure, hairstyle, costume silhouette, colors and body proportion.",
                "Use prop references only to lock object shape, material, color, scale, wear marks and current state.",
                "Do not merge different reference views into one impossible face, costume, room layout or prop design.",
                video_structure_instruction(shot),
                "【原始时间轴提示词】",
                (shot.prompt or "").strip(),
                "[连续性约束]",
                "同一角色在所有参考图中必须保持五官比例、发型、服装版型、颜色、材质、污渍和道具位置一致。",
                "如果参考图里同时存在正面、侧面和背面，请严格按对应视角呈现，不要把正脸特征拼到背面，也不要把背面衣褶硬搬到正面。",
                "背景与人物必须自然融合，禁止出现抠图边缘、换脸、换衣服、背景突变。",
            ]
        )

        return " ".join(lines)

    @staticmethod
    def _compact_reference_prompt(prompt: str, max_length: int = 700) -> str:
        """Keep the highest-value prompt clauses instead of hard-truncating the tail."""
        raw = re.sub(r"\r\n?", "\n", prompt or "").strip()
        if not raw:
            return ""

        normalized = re.sub(r"[ \t]+", " ", raw)
        if len(normalized) <= max_length:
            return normalized

        clauses = [line.strip() for line in re.split(r"\n+", raw) if line.strip()]
        if len(clauses) <= 1:
            clauses = [part.strip() for part in re.split(r"[。！？!?；;]+", normalized) if part.strip()]
        if not clauses:
            return normalized[:max_length].rstrip("；，, ")

        priority_markers = (
            "参考图",
            "连续",
            "保持",
            "禁止",
            "不要",
            "不新增",
            "上一镜",
            "尾帧",
            "机位",
            "景别",
            "镜头",
            "动作",
            "人物",
            "角色",
            "道具",
            "场景",
            "背景",
            "光线",
            "时间",
            "视角",
            "状态",
        )

        def clause_score(text: str, index: int) -> int:
            score = 0
            if index == 0:
                score += 100
            if index == len(clauses) - 1:
                score += 80
            if any(marker in text for marker in priority_markers):
                score += 20
            if len(text) <= 180:
                score += 8
            elif len(text) <= 260:
                score += 4
            if "连续" in text or "保持" in text or "禁止" in text:
                score += 6
            return score

        ranked = sorted(
            ((clause_score(text, idx), idx, text) for idx, text in enumerate(clauses)),
            key=lambda item: (-item[0], item[1]),
        )

        selected_indices: set[int] = set()
        for _, idx, _ in ranked:
            candidate_indices = sorted(selected_indices | {idx})
            candidate = "；".join(clauses[i] for i in candidate_indices)
            if len(candidate) <= max_length or not selected_indices:
                selected_indices.add(idx)
            if len(selected_indices) >= 8:
                break

        if not selected_indices:
            selected_indices = {0}
            if len(clauses) > 1:
                selected_indices.add(len(clauses) - 1)

        selected = [clauses[i] for i in sorted(selected_indices)]
        result = "；".join(selected)

        if len(result) > max_length:
            compressed: list[str] = []
            for clause in selected:
                if len(clause) <= 180:
                    compressed.append(clause)
                    continue
                subclauses = [part.strip() for part in re.split(r"[，,]", clause) if part.strip()]
                if not subclauses:
                    compressed.append(clause[:180].rstrip("，, "))
                    continue
                important = [
                    part for part in subclauses
                    if any(marker in part for marker in priority_markers)
                ]
                chosen = important or subclauses[:3]
                compressed.append("，".join(chosen)[:180].rstrip("，, "))
            result = "；".join(compressed)

        if len(result) > max_length:
            result = result[:max_length].rstrip("；，, ")
        return result
