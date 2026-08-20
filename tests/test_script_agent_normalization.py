import pytest

from src.agents.script_agent import ScriptAgent
from src.schemas.script import DialogueLine, Scene, ScriptBreakdown
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


# ── Phase 1: deterministic dialogue/action separation repair ────────────────


def _scene(seed: int, **overrides):
    base = dict(
        scene_id=seed,
        duration_sec=5,
        location="便利店外部",
        characters=["顾衡", "苏眠"],
        dialogue=[],
        action_description="顾衡站在店门口。",
        camera_direction="中景",
        emotion_tone="紧张",
    )
    base.update(overrides)
    return Scene(**base)


def test_repair_strips_stage_directions_and_silence_from_dialogue():
    script = ScriptBreakdown(
        title="t", genre="悬疑", logline="l", duration_sec=10, narration_style="无",
        scenes=[
            _scene(
                1,
                dialogue=[
                    DialogueLine(character="苏眠", line="（微笑）贵妃娘娘早安，臣妾在测bug呢。"),
                    DialogueLine(character="顾衡", line="（示意别出声）"),
                    DialogueLine(character="苏眠", line="……"),
                    DialogueLine(character="顾衡", line="（低声）我知道。（叹气）"),
                ],
            ),
        ],
    )
    ScriptAgent._repair_scene_semantics(script)
    scene = script.scenes[0]
    assert [(d.character, d.line) for d in scene.dialogue] == [
        ("苏眠", "贵妃娘娘早安，臣妾在测bug呢。"),
        ("顾衡", "我知道。"),
    ]
    assert "微笑" in scene.action_description
    assert "示意别出声" in scene.action_description
    assert "低声" in scene.action_description
    assert "叹气" in scene.action_description


def test_repair_extracts_named_speaker_quotes_from_action_into_dialogue():
    script = ScriptBreakdown(
        title="t", genre="悬疑", logline="l", duration_sec=10, narration_style="无",
        scenes=[
            _scene(
                1,
                action_description=(
                    "顾衡低声说：'我当年没能救下你父亲。' "
                    "苏眠声音发颤：'那你为什么不告诉我？'"
                ),
            ),
        ],
    )
    ScriptAgent._repair_scene_semantics(script)
    scene = script.scenes[0]
    assert {d.line for d in scene.dialogue} == {
        "我当年没能救下你父亲。",
        "那你为什么不告诉我？",
    }
    assert "我当年没能救下你父亲。" not in scene.action_description
    assert "顾衡说出这句台词" in scene.action_description
    assert "苏眠说出这句台词" in scene.action_description


def test_repair_dedupes_quotes_already_present_in_dialogue():
    script = ScriptBreakdown(
        title="t", genre="悬疑", logline="l", duration_sec=10, narration_style="无",
        scenes=[
            _scene(
                1,
                dialogue=[DialogueLine(character="陆临", line="现在，你们谁也走不了了。")],
                characters=["陆临"],
                action_description="陆临的声音在黑暗里响起：“现在，你们谁也走不了了。”",
            ),
        ],
    )
    ScriptAgent._repair_scene_semantics(script)
    scene = script.scenes[0]
    assert [d.line for d in scene.dialogue] == ["现在，你们谁也走不了了。"]
    assert "现在，你们谁也走不了了。" not in scene.action_description
    assert "陆临的声音响起" in scene.action_description


def test_repair_skips_negated_or_unspeaker_quotes():
    script = ScriptBreakdown(
        title="t", genre="悬疑", logline="l", duration_sec=10, narration_style="无",
        scenes=[
            _scene(
                1,
                action_description="苏眠没有回答“你父亲写的”。顾衡抓住苏眠手腕：“走。”",
            ),
        ],
    )
    ScriptAgent._repair_scene_semantics(script)
    scene = script.scenes[0]
    assert scene.dialogue == []
    assert "苏眠没有回答“你父亲写的”" in scene.action_description


def test_scene_semantic_errors_reject_multi_location_and_sentence_location():
    script = ScriptBreakdown(
        title="t", genre="悬疑", logline="l", duration_sec=10, narration_style="无",
        scenes=[
            _scene(1, location="钟楼街便利店门口 → 街边窄巷 → 半掩铁门前"),
            _scene(2, location="深夜，他走到天台边缘，这里很安静。"),
        ],
    )
    errors = ScriptAgent._scene_semantic_errors(script)
    assert any("多个物理地点" in error for error in errors)
    assert any("完整句子" in error for error in errors)

    with pytest.raises(ValueError, match="分镜结构语义错误"):
        ScriptAgent._validate_visual_assets(script, require_manifest=False)


def test_validate_visual_assets_rebases_duration_total_to_scene_sum():
    script = ScriptBreakdown(
        title="t", genre="悬疑", logline="l", duration_sec=100, narration_style="无",
        scenes=[_scene(1, duration_sec=5), _scene(2, duration_sec=8)],
    )
    normalized = ScriptAgent._validate_visual_assets(script, require_manifest=False)
    assert normalized.duration_sec == 13


def test_analyze_existing_script_prompt_contains_parse_rules():
    captured = {}

    class FakeLLM:
        provider = "deepseek"

        async def generate_structured(self, **kwargs):
            captured.update(kwargs)
            return kwargs["output_schema"](
                title="雨夜来信",
                genre="悬疑",
                logline="l",
                duration_sec=10,
                narration_style="无",
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
                ],
            )

    async def run():
        agent = ScriptAgent()
        agent.llm = FakeLLM()
        return await agent.analyze_existing_script("旧剧本", creative_profile=None)

    import asyncio
    asyncio.run(run())
    user_prompt = captured["user_prompt"]
    assert "解析三原则" in user_prompt
    assert "先切场" in user_prompt
    assert "dialogue 只收录角色真正说出口的原话" in user_prompt
    assert "不得只写在 action_description 里" in user_prompt
    assert "（录音）" in user_prompt


@pytest.mark.asyncio
async def test_compact_script_prompt_contains_parse_rules_without_manifest_terms():
    captured = {}

    class FakeLLM:
        provider = "deepseek"

        async def generate_structured(self, **kwargs):
            captured.update(kwargs)
            return kwargs["output_schema"](
                title="雨夜来信",
                genre="悬疑",
                logline="l",
                duration_sec=10,
                narration_style="无",
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
                ],
            )

    agent = ScriptAgent()
    agent.llm = FakeLLM()
    await agent._generate_validated("请结构化这个短剧", temperature=0.2)
    user_prompt = captured["user_prompt"]
    assert "解析三原则" in user_prompt
    assert "先切场" in user_prompt
    assert "视觉资产清单（必须输出 asset_manifest）" not in user_prompt
    assert "asset_manifest.characters" not in user_prompt
