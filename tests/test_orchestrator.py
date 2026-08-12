"""Integration test for the full multi-agent pipeline (using mock API)."""

import asyncio
import os
import pytest
from pathlib import Path

from src.agents import (
    ScriptAgent,
    VisualPromptAgent,
    ImageGenerationAgent,
    VideoGenerationAgent,
    ReviewAgent,
    AudioAgent,
    AssemblyAgent,
)
from src.graph.orchestrator import DramaOrchestrator


def _require_integration_tests() -> None:
    if os.getenv("RUN_INTEGRATION_TESTS") != "1":
        pytest.skip("Set RUN_INTEGRATION_TESTS=1 to call configured LLM and media APIs")


@pytest.fixture
def orchestrator():
    """Create a real orchestrator with all agents."""
    return DramaOrchestrator(
        script_agent=ScriptAgent(),
        visual_prompt_agent=VisualPromptAgent(),
        image_gen_agent=ImageGenerationAgent(),
        video_gen_agent=VideoGenerationAgent(),
        review_agent=ReviewAgent(),
        audio_agent=AudioAgent(),
        assembly_agent=AssemblyAgent(),
    )


@pytest.mark.integration
@pytest.mark.asyncio
async def test_full_pipeline_basic(orchestrator):
    """Test the full pipeline with a short input.

    NOTE: This test requires a valid LLM API key to be set in .env.
    If no API key is configured, this test will be skipped.
    """
    _require_integration_tests()
    import os
    if not os.getenv("ANTHROPIC_API_KEY") and not os.getenv("OPENAI_API_KEY"):
        pytest.skip("No LLM API key configured — set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env")

    user_input = "一个少年在雨夜中奔跑，突然撞到了一个陌生人。两人对视的瞬间，少年发现对方的眼睛是他自己的。"

    state = await orchestrator.generate(user_input)

    # Verify all agents ran
    assert state.script is not None, "Script Agent should produce output"
    assert len(state.script.scenes) > 0, "Should have at least 1 scene"
    assert state.visual_design is not None, "Visual Design Agent should produce output"
    assert state.generation_prompts is not None, "Prompt Agent should produce output"
    assert state.generated_assets is not None, "Video Gen Agent should produce output"
    assert state.review_result is not None, "Review Agent should produce output"
    assert state.audio_assets is not None, "Audio Agent should produce output"

    # Check execution log is populated
    assert len(state.execution_log) > 0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_empty_input(orchestrator):
    """Test that empty input is handled properly."""
    _require_integration_tests()
    # The orchestrator should not crash on empty input
    state = await orchestrator.generate("")
    # It should still attempt to run (the LLM will produce garbage, but that's OK)
    # The key is it doesn't throw an exception


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
