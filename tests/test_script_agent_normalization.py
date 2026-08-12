import pytest

from src.agents.script_agent import ScriptAgent
from src.schemas.script import Scene, ScriptBreakdown
from src.utils.visual_asset_manifest import validate_asset_manifest


def test_script_agent_normalizes_one_second_llm_transition_to_three_second_production_minimum():
    # Bypass the public schema only to emulate a legacy/raw LLM object before
    # the deterministic production normalization step.
    script = ScriptBreakdown.model_construct(
        title="test",
        genre="test",
        logline="test",
        duration_sec=1,
        narration_style="none",
        scenes=[
            Scene.model_construct(
                scene_id=1,
                duration_sec=1,
                location="room",
                characters=[],
                dialogue=[],
                action_description="A brief reaction beat.",
                camera_direction="close-up",
                emotion_tone="tense",
                transition_from_prev="fade",
                transition_duration_sec=0.25,
                continuity_from_prev=False,
                continuity_reason="",
            ),
        ],
        asset_manifest=None,
    )

    normalized = ScriptAgent._validate_visual_assets(script, require_manifest=False)
    assert normalized.scenes[0].duration_sec == 3
    assert normalized.scenes[0].transition_from_prev == "cut"


def test_script_agent_infers_manifest_when_llm_omits_visual_asset_list():
    script = ScriptBreakdown(
        title="test",
        genre="悬疑",
        logline="test",
        duration_sec=10,
        narration_style="心声",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=5,
                location="旧钟楼外部",
                characters=["苏眠"],
                dialogue=[],
                action_description="苏眠拿着第七封信走到雨夜旧钟楼门口。",
                camera_direction="近景缓慢推进",
                emotion_tone="紧张",
            ),
            Scene(
                scene_id=2,
                duration_sec=5,
                location="旧钟楼外部街角",
                characters=["苏眠", "心声(苏眠)"],
                dialogue=[],
                action_description="苏眠低头看信封背面的照片，雨水打湿纸边。",
                camera_direction="手机和信封插入特写",
                emotion_tone="压迫",
            ),
        ],
    )

    normalized = ScriptAgent._validate_visual_assets(script, require_manifest=True)

    assert normalized.asset_manifest is not None
    assert validate_asset_manifest(normalized) == []
    assert {character.name for character in normalized.asset_manifest.characters if character.appearance_mode == "on_screen"} == {"苏眠"}
    assert [binding.scene_id for binding in normalized.asset_manifest.shot_bindings] == [1, 2]
    assert normalized.asset_manifest.props


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["kimi", "deepseek"])
async def test_plain_json_script_providers_use_compact_schema_and_local_manifest(provider):
    captured = {}

    class FakeKimiLLM:
        provider = "kimi"

        async def generate_structured(self, **kwargs):
            captured.update(kwargs)
            telemetry = kwargs.get("telemetry")
            if telemetry is not None:
                telemetry.update({
                    "attempts": 1,
                    "repair_attempts": 0,
                    "fallback_used": True,
                    "input_chars": len(kwargs["system_prompt"]) + len(kwargs["user_prompt"]),
                    "output_chars": 0,
                })
            return kwargs["output_schema"](
                title="雨夜来信",
                genre="悬疑",
                logline="少女收到亡父来信后发现旧钟楼的秘密。",
                duration_sec=10,
                narration_style="无旁白",
                target_audience="短剧观众",
                scenes=[
                    Scene(
                        scene_id=1,
                        duration_sec=5,
                        location="旧钟楼外部",
                        characters=["苏眠"],
                        action_description="苏眠握着第七封信站在雨夜旧钟楼门口。",
                        camera_direction="近景缓慢推进",
                        emotion_tone="紧张",
                    ),
                    Scene(
                        scene_id=2,
                        duration_sec=5,
                        location="旧钟楼外部街角",
                        characters=["苏眠"],
                        action_description="苏眠低头看信封背面的照片，雨水打湿纸边。",
                        camera_direction="信封特写",
                        emotion_tone="压迫",
                    ),
                ],
            )

    FakeKimiLLM.provider = provider

    agent = ScriptAgent()
    agent.llm = FakeKimiLLM()
    telemetry = {}

    script = await agent._generate_validated("请结构化这个短剧", temperature=0.2, telemetry=telemetry)

    assert captured["output_schema"].__name__ == "_ScriptOnlyBreakdown"
    assert "视觉资产清单（必须输出 asset_manifest）" not in captured["user_prompt"]
    assert "asset_manifest.characters" not in captured["user_prompt"]
    assert script.asset_manifest is not None
    assert validate_asset_manifest(script) == []
    assert telemetry["compact_script_generation"] is True
