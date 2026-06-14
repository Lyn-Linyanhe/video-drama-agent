"""Video Generation Agent — manages API calls, concurrency, retries, and costs."""

import asyncio
import time
from typing import Optional

from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.assets import GeneratedAssets, ShotAsset
from src.api_clients.base import VideoAPIClient
from src.api_clients.mock import MockVideoClient
from src.config import MAX_RETRIES, MAX_CONCURRENT_GENERATIONS, GENERATION_TIMEOUT, DEFAULT_API


class VideoGenerationAgent:
    """Agent that orchestrates video generation across multiple API clients.

    Responsibilities:
    - Map GenerationPrompts to API calls
    - Run independent shots concurrently
    - Retry failed generations with exponential backoff
    - Track and report costs
    - Download generated videos to local storage

    The agent operates in two modes:
    1. With real API clients (Kling, Runway, Pika) — passed via constructor
    2. With MockVideoClient — for development/testing (default)
    """

    def __init__(
        self,
        api_client: Optional[VideoAPIClient] = None,
        max_concurrent: int = MAX_CONCURRENT_GENERATIONS,
        max_retries: int = MAX_RETRIES,
    ):
        """Initialize the video generation agent.

        Args:
            api_client: A VideoAPIClient instance. Defaults to MockVideoClient.
            max_concurrent: Maximum concurrent API calls.
            max_retries: Maximum retry attempts per failed generation.
        """
        self.api_client = api_client or MockVideoClient()
        self.max_concurrent = max_concurrent
        self.max_retries = max_retries
        self._semaphore = asyncio.Semaphore(max_concurrent)

    async def run(self, prompts: GenerationPrompts) -> GeneratedAssets:
        """Generate videos for all shots in the prompt list.

        Shots are generated concurrently where possible (up to max_concurrent).
        Each shot is retried up to max_retries times on failure.

        Args:
            prompts: The generation prompts from PromptEngineeringAgent.

        Returns:
            GeneratedAssets with all generated (or failed) video shots.
        """
        start_time = time.time()
        total_cost = 0.0

        # Process all shots concurrently with semaphore limiting
        async def generate_single(shot: ShotPrompt) -> ShotAsset:
            async with self._semaphore:
                return await self._generate_with_retry(shot)

        tasks = [generate_single(shot) for shot in prompts.shots]
        shot_assets = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle exceptions from gather
        results: list[ShotAsset] = []
        for i, asset in enumerate(shot_assets):
            if isinstance(asset, Exception):
                results.append(ShotAsset(
                    scene_id=prompts.shots[i].scene_id,
                    success=False,
                    error_message=str(asset),
                ))
            else:
                results.append(asset)

        # Calculate costs
        for asset in results:
            if asset.success:
                # Estimate based on duration (mock is 0)
                total_cost += await self.api_client.get_cost_estimate(
                    int(asset.duration_sec)
                )

        elapsed = time.time() - start_time

        return GeneratedAssets(
            shots=results,
            total_cost=total_cost,
            generation_time_sec=elapsed,
            all_success=all(a.success for a in results),
        )

    async def _generate_with_retry(self, shot: ShotPrompt) -> ShotAsset:
        """Generate a single shot with retry logic.

        Also handles sub-shots: if a shot has sub_shots, each is generated
        separately (for scenes longer than API max duration).
        """
        # Handle sub-shots (long scenes split into multiple generations)
        if shot.sub_shots:
            sub_assets = []
            for sub in shot.sub_shots:
                asset = await self._retry_loop(sub)
                sub_assets.append(asset)
            # Return the first successful sub-shot or the first one
            for sa in sub_assets:
                if sa.success:
                    return sa
            return sub_assets[0] if sub_assets else ShotAsset(
                scene_id=shot.scene_id, success=False,
                error_message="All sub-shots failed"
            )

        return await self._retry_loop(shot)

    async def _retry_loop(self, shot: ShotPrompt) -> ShotAsset:
        """Retry loop with exponential backoff."""
        last_error = ""
        for attempt in range(1, self.max_retries + 1):
            try:
                result = await asyncio.wait_for(
                    self.api_client.generate_video(
                        prompt=shot.prompt,
                        negative_prompt=shot.negative_prompt,
                        duration_sec=shot.duration_sec,
                        seed=shot.seed,
                        motion_strength=shot.motion_strength,
                    ),
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
                    )
                else:
                    last_error = result.error_message

            except asyncio.TimeoutError:
                last_error = f"Generation timed out after {GENERATION_TIMEOUT}s"

            # Exponential backoff: 2^attempt seconds
            if attempt < self.max_retries:
                wait = 2 ** attempt
                await asyncio.sleep(wait)

        return ShotAsset(
            scene_id=shot.scene_id,
            success=False,
            error_message=last_error or "Max retries exceeded",
        )
