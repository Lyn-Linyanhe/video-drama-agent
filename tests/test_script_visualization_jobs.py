"""Regression coverage for the split script and visual-prompt task lifecycle."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from src.api import context as api_context
from src.api.routes import jobs, production, scripts
from src.api.context import ScriptRequest, ScriptRevisionRequest
from src.graph.state import DramaState
from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.project import CreativeProfile
from src.schemas.script import (
    DialogueLine,
    LocationAsset,
    PropAsset,
    Scene,
    ScriptBreakdown,
    ShotVisualBinding,
    VisualAssetManifest,
    VisualCharacterAsset,
)
from src.schemas.visual import VisualDesign
from src.agents.visual_prompt_agent import (
    _CharacterRegistryOutput,
    VisualPromptAgent,
    _CompactShotVisualPrompt,
    _CompactVisualPromptOutput,
    _ShotVisualPrompt,
    _VisualPromptOutput,
)
from src.utils.llm import LLMClient


class _Context:
    def __init__(self, project_id: str):
        self.project_id = project_id
        self.job_id = "visual-job-0001"
        self.project_lock = asyncio.Lock()

    def progress(self, *_args, **_kwargs):
        return None

    def check_cancelled(self):
        return None


@pytest.mark.asyncio
async def test_phase_one_script_job_creates_exactly_one_visualization_job(monkeypatch):
    created = []
    cancelled = []
    saved_states = []

    monkeypatch.setattr(
        jobs.script_agent, "run", lambda *_args, **_kwargs: asyncio.sleep(0, result=_script("phase one")),
    )
    monkeypatch.setattr(jobs.project_store, "update_status", lambda *_args: None)
    monkeypatch.setattr(jobs.project_store, "set_script", lambda *_args: None)
    monkeypatch.setattr(jobs.project_store, "set_state_json", lambda *_args: saved_states.append(_args[1]))
    monkeypatch.setattr(
        jobs.job_store, "list_jobs",
        lambda *_args, **_kwargs: [{"job_id": "old-visual", "job_type": "script.visualize"}],
    )
    monkeypatch.setattr(jobs.job_manager, "cancel", lambda *_args: cancelled.append(_args[1]))
    monkeypatch.setattr(
        jobs.job_manager, "create_job",
        lambda *args, **kwargs: created.append((args, kwargs)) or {"job_id": "visual-job-1"},
    )

    result = await jobs._job_script_generate(
        _Context("visual-project"), {"user_input": "一个短剧创意", "input_mode": "idea"},
    )

    assert result["script"]["title"] == "phase one"
    assert "visual_design" not in result
    assert len(created) == 1
    assert created[0][0][1] == "script.visualize"
    assert result["visual_job_id"] == "visual-job-1"
    assert cancelled == ["old-visual"]
    assert saved_states


def _script(title: str) -> ScriptBreakdown:
    return ScriptBreakdown(
        title=title, genre="test", logline="test", duration_sec=5, narration_style="none",
        scenes=[Scene(scene_id=1, duration_sec=5, location="room", characters=[], action_description="wait", camera_direction="medium", emotion_tone="tense")],
    )


@pytest.mark.asyncio
async def test_stale_visual_task_does_not_overwrite_newer_saved_script(monkeypatch):
    state = DramaState(
        project_id="a" * 12,
        script=_script("new script"),
        visual_stage="generating",
        visual_script_signature="new-signature",
    )
    visual = VisualDesign(overall_style="写实", color_palette="冷色", scenes=[])
    prompts = GenerationPrompts(api_provider="mock", shots=[ShotPrompt(scene_id=1, prompt="old visual result", duration_sec=5)])

    monkeypatch.setattr(jobs, "_load_project_state", lambda _project_id, **_kwargs: state)
    monkeypatch.setattr(jobs, "_save_project_state", lambda _state: None)
    monkeypatch.setattr(jobs.project_store, "set_visual_design", lambda *_args: None)
    monkeypatch.setattr(jobs.project_store, "add_stage", lambda *_args: None)
    monkeypatch.setattr(jobs.project_store, "update_status", lambda *_args: None)
    monkeypatch.setattr(jobs, "_script_signature", lambda _script: "new-signature")
    monkeypatch.setattr(jobs.visual_prompt_agent, "run", lambda *_args, **_kwargs: asyncio.sleep(0, result=(visual, prompts)))

    result = await jobs._job_script_visualize(
        _Context(state.project_id),
        {"script_signature": "old-signature"},
    )

    assert result["stale"] is True
    assert state.script.title == "new script"
    assert state.generation_prompts is None
    assert state.visual_stage == "stale"


@pytest.mark.asyncio
async def test_visual_task_accepts_persisted_signature_after_load_time_manifest_repair(monkeypatch):
    """Deterministic legacy repairs must not invalidate an unchanged visual job."""
    state = DramaState(
        project_id="b" * 12,
        script=_script("unchanged script"),
        visual_stage="generating",
        # This is the signature persisted when the job was queued. Loading the
        # legacy project repairs manifest descriptions and produces a different
        # in-memory signature, without changing the authored screenplay.
        visual_script_signature="persisted-signature",
    )
    visual = VisualDesign(overall_style="写实", color_palette="冷色", scenes=[])
    prompts = GenerationPrompts(api_provider="mock", shots=[ShotPrompt(scene_id=1, prompt="repaired", duration_sec=5)])

    monkeypatch.setattr(jobs, "_load_project_state", lambda _project_id, **_kwargs: state)
    monkeypatch.setattr(jobs, "_save_project_state", lambda _state: None)
    monkeypatch.setattr(jobs.project_store, "set_visual_design", lambda *_args: None)
    monkeypatch.setattr(jobs.project_store, "add_stage", lambda *_args: None)
    monkeypatch.setattr(jobs.project_store, "update_status", lambda *_args: None)
    monkeypatch.setattr(jobs, "_script_signature", lambda _script: "normalized-signature")
    monkeypatch.setattr(
        jobs.visual_prompt_agent,
        "run",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=(visual, prompts)),
    )

    result = await jobs._job_script_visualize(
        _Context(state.project_id),
        {"script_signature": "persisted-signature"},
    )

    assert result["stale"] is False
    assert state.visual_stage == "ready"
    assert state.generation_prompts.shots[0].prompt == "repaired"
    assert state.visual_script_signature == "normalized-signature"


@pytest.mark.asyncio
async def test_canceled_visual_refresh_keeps_last_ready_prompt(monkeypatch):
    """Canceling a refresh must not invalidate the previous usable prompt set."""
    old_prompts = GenerationPrompts(
        api_provider="mock",
        shots=[ShotPrompt(scene_id=1, prompt="last ready prompt", duration_sec=5)],
    )
    state = DramaState(
        project_id="c" * 12,
        script=_script("unchanged script"),
        visual_stage="ready",
        visual_script_signature="stable-signature",
        generation_prompts=old_prompts,
    )

    async def cancel_run(*_args, **_kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(jobs, "_load_project_state", lambda _project_id, **_kwargs: state)
    monkeypatch.setattr(jobs, "_save_project_state", lambda _state: None)
    monkeypatch.setattr(jobs, "_script_signature", lambda _script: "stable-signature")
    monkeypatch.setattr(jobs.visual_prompt_agent, "run", cancel_run)

    with pytest.raises(asyncio.CancelledError):
        await jobs._job_script_visualize(
            _Context(state.project_id),
            {"script_signature": "stable-signature"},
        )

    assert state.visual_stage == "ready"
    assert state.visual_stage_error == ""
    assert state.generation_prompts is old_prompts


def test_compact_visual_output_is_assembled_into_a_complete_shot_prompt():
    script = _script("compact")
    output = _VisualPromptOutput(
        overall_style="电影写实", color_palette="冷蓝灰", aspect_ratio="9:16",
        shots=[_ShotVisualPrompt(
            scene_id=1,
            subject_state="人物站在门前，手握门把",
            action_timeline="0-2秒抬头观察；2-5秒缓慢推门",
            environment_delta="走廊尽头灯光闪烁，地面有积水",
            camera_motion="中景缓慢推进",
            lighting_delta="冷色顶光，门缝透出暖光",
            prop_state="门把有锈迹，保持位置不变",
            negative_prompt="不要人物变形",
            motion_strength=0.65,
            duration_sec=5,
        )],
    )

    _visual, prompts = VisualPromptAgent(api_provider="mock")._split_output(output, script, None)

    prompt = prompts.shots[0]
    assert "人物站在门前" in prompt.prompt
    assert "0-2秒抬头观察" in prompt.prompt
    assert "走廊尽头灯光闪烁" in prompt.prompt
    assert "中景缓慢推进" in prompt.prompt
    assert "live_action_cinematic" not in prompt.prompt
    assert prompt.negative_prompt == "不要人物变形"


@pytest.mark.asyncio
async def test_kimi_visual_prompt_generation_runs_scene_by_scene():
    script = _script("stepwise")
    script.scenes.append(
        Scene(
            scene_id=2, duration_sec=5, location="hall", characters=[],
            action_description="walk", camera_direction="wide", emotion_tone="quiet",
        )
    )

    class _FakeKimiLLM:
        provider = "kimi"
        model = "kimi-k2.6"

        def __init__(self):
            self.calls = []

        async def generate_structured(self, **kwargs):
            self.calls.append(kwargs)
            scene_id = len(self.calls)
            return _VisualPromptOutput(
                overall_style="style", color_palette="palette", aspect_ratio="9:16",
                shots=[_ShotVisualPrompt(
                    scene_id=scene_id,
                    subject_state=f"state {scene_id}",
                    action_timeline=f"0-5s action {scene_id}",
                    duration_sec=5,
                )],
            )

    telemetry = {}
    agent = VisualPromptAgent(api_provider="mock")
    agent.llm = _FakeKimiLLM()

    visual, prompts = await agent.run(script, telemetry=telemetry)

    assert len(agent.llm.calls) == 2
    assert [shot.scene_id for shot in prompts.shots] == [1, 2]
    assert [scene.scene_id for scene in visual.scenes] == [1, 2]
    assert len(telemetry["requests"]) == 2


@pytest.mark.asyncio
async def test_visual_stage_uses_compact_output_contract_for_non_kimi_provider():
    script = _script("compact contract")

    class _FakeDeepSeekLLM:
        provider = "deepseek"
        model = "deepseek-chat"

        def __init__(self):
            self.calls = []

        async def generate_structured(self, **kwargs):
            self.calls.append(kwargs)
            return _CompactVisualPromptOutput(
                overall_style="动漫风",
                color_palette="冷蓝色",
                aspect_ratio="9:16",
                shots=[_CompactShotVisualPrompt(scene_id=1, subject_state="人物站定", duration_sec=5)],
            )

    agent = VisualPromptAgent(api_provider="mock")
    agent.llm = _FakeDeepSeekLLM()

    await agent.run(script)

    assert agent.llm.calls[0]["output_schema"].__name__ == "_CompactVisualPromptOutput"
    assert "每个字段最多两句" in agent.llm.calls[0]["system_prompt"]


@pytest.mark.asyncio
async def test_visual_stage_repairs_an_incomplete_character_profile_once():
    script = ScriptBreakdown(
        title="人物侧写补全",
        genre="都市悬疑",
        logline="角色在雨夜追查线索。",
        duration_sec=5,
        narration_style="无旁白",
        scenes=[Scene(
            scene_id=1,
            duration_sec=5,
            location="便利店外",
            characters=["苏眠"],
            action_description="苏眠穿米白色长风衣站在雨中。",
            camera_direction="中景",
            emotion_tone="紧张",
        )],
        asset_manifest=VisualAssetManifest(
            characters=[VisualCharacterAsset(
                asset_id="char_su_mian",
                name="苏眠",
                appearance_mode="on_screen",
                description="苏眠：米白色长风衣。",
            )],
        ),
    )
    complete_profile = (
        "二十六岁成年女性，身形修长匀称；椭圆脸、自然白皙肤色，"
        "眉形清晰、杏眼沉静、鼻梁挺直、唇形自然；黑色中长直发，"
        "穿米白色棉质长风衣与深色长裤，佩戴细银色腕表，左眼下有一颗小痣。"
    )

    class _FakeLLM:
        provider = "deepseek"
        model = "deepseek-chat"

        def __init__(self):
            self.calls = []

        async def generate_structured(self, **kwargs):
            self.calls.append(kwargs)
            if kwargs["output_schema"].__name__ == "_CompactVisualPromptOutput":
                return _CompactVisualPromptOutput(
                    overall_style="电影级真人写实",
                    color_palette="冷蓝灰",
                    aspect_ratio="9:16",
                    global_character_descriptions_cn={"苏眠": "米白色长风衣。"},
                    shots=[_CompactShotVisualPrompt(scene_id=1, duration_sec=5)],
                )
            return _CharacterRegistryOutput(
                global_character_descriptions={"苏眠": complete_profile},
                global_character_descriptions_cn={"苏眠": complete_profile},
            )

    agent = VisualPromptAgent(api_provider="mock")
    agent.llm = _FakeLLM()

    source_text = "人物小传：苏眠从小习惯用左手，左眼下有一颗小痣。"
    visual, _prompts = await agent.run(script, source_text=source_text)

    assert len(agent.llm.calls) == 2
    assert agent.llm.calls[1]["output_schema"].__name__ == "_CharacterRegistryOutput"
    assert visual.global_character_descriptions_cn["苏眠"] == complete_profile
    repair_prompt = agent.llm.calls[1]["user_prompt"]
    assert "性别与年龄段" in repair_prompt
    assert "脸型与肤色" in repair_prompt
    assert "眉眼鼻唇" in repair_prompt
    assert "米白色长风衣" in repair_prompt
    assert source_text in repair_prompt


@pytest.mark.asyncio
async def test_character_reextract_retries_incomplete_output_with_asset_id_contract():
    script = ScriptBreakdown(
        title="人物侧写结构化修复",
        genre="都市悬疑",
        logline="角色在雨夜追查线索。",
        duration_sec=5,
        narration_style="无旁白",
        scenes=[Scene(
            scene_id=1,
            duration_sec=5,
            location="便利店外",
            characters=["苏眠"],
            action_description="苏眠穿米白色长风衣站在雨中。",
            camera_direction="中景",
            emotion_tone="紧张",
        )],
        asset_manifest=VisualAssetManifest(
            characters=[VisualCharacterAsset(
                asset_id="char_su_mian",
                name="苏眠",
                appearance_mode="on_screen",
                description="苏眠：米白色长风衣。",
            )],
        ),
    )
    complete_profile = (
        "二十六岁成年女性，身形修长匀称；椭圆脸、自然白皙肤色，"
        "眉形清晰、杏眼沉静、鼻梁挺直、唇形自然；黑色中长直发，"
        "穿米白色棉质长风衣与深色长裤，佩戴细银色腕表，左眼下有一颗小痣。"
    )

    class _FakeLLM:
        provider = "deepseek"
        model = "deepseek-chat"

        def __init__(self):
            self.calls = []

        async def generate_structured(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return _CharacterRegistryOutput()
            return _CharacterRegistryOutput(characters=[{
                "asset_id": "char_su_mian",
                "name": "苏眠",
                "description_en": complete_profile,
                "description_cn": complete_profile,
            }])

    agent = VisualPromptAgent(api_provider="mock")
    agent.llm = _FakeLLM()

    english, chinese = await agent.reextract_visible_characters(
        script,
        source_text="苏眠的完整人物小传。",
    )

    assert len(agent.llm.calls) == 2
    assert agent.llm.calls[1]["output_schema"].__name__ == "_CharacterRegistryOutput"
    assert "上一次输出不合格" in agent.llm.calls[1]["user_prompt"]
    assert english["苏眠"] == complete_profile
    assert chinese["苏眠"] == complete_profile


@pytest.mark.asyncio
async def test_visual_job_passes_saved_script_draft_to_profile_repair(monkeypatch):
    state = DramaState(
        project_id="d" * 12,
        input_mode="script",
        script_draft_text="完整剧本中的稳定人物外貌资料。",
        script=_script("source text propagation"),
        visual_stage="generating",
        visual_script_signature="stable-signature",
    )
    visual = VisualDesign(overall_style="写实", color_palette="冷色", scenes=[])
    prompts = GenerationPrompts(
        api_provider="mock",
        shots=[ShotPrompt(scene_id=1, prompt="prompt", duration_sec=5)],
    )
    captured = {}

    async def fake_run(*_args, **kwargs):
        captured.update(kwargs)
        return visual, prompts

    monkeypatch.setattr(jobs, "_load_project_state", lambda _project_id, **_kwargs: state)
    monkeypatch.setattr(jobs, "_save_project_state", lambda _state: None)
    monkeypatch.setattr(jobs.project_store, "set_visual_design", lambda *_args: None)
    monkeypatch.setattr(jobs.project_store, "add_stage", lambda *_args: None)
    monkeypatch.setattr(jobs.project_store, "update_status", lambda *_args: None)
    monkeypatch.setattr(jobs, "_script_signature", lambda _script: "stable-signature")
    monkeypatch.setattr(jobs.visual_prompt_agent, "run", fake_run)

    await jobs._job_script_visualize(
        _Context(state.project_id),
        {"script_signature": "stable-signature"},
    )

    assert captured["source_text"] == state.script_draft_text


@pytest.mark.asyncio
async def test_character_reextract_endpoint_passes_saved_script_draft(monkeypatch):
    state = DramaState(
        project_id="e" * 12,
        input_mode="script",
        script_draft_text="原始剧本中保存的完整人物小传。",
        script=_script("reextract source text"),
        visual_design=VisualDesign(overall_style="写实", color_palette="冷色", scenes=[]),
    )
    state.script.asset_manifest = VisualAssetManifest(
        characters=[VisualCharacterAsset(
            asset_id="char_lin",
            name="林澈",
            appearance_mode="on_screen",
            description="林澈：深色外套。",
        )]
    )
    captured = {}

    async def fake_reextract(*_args, **kwargs):
        captured.update(kwargs)
        return {"林澈": "profile"}, {"林澈": "完整人物侧写"}

    monkeypatch.setattr(production, "_load_project_state", lambda _project_id: state)
    monkeypatch.setattr(production, "_save_project_state", lambda _state: None)
    monkeypatch.setattr(production.project_store, "set_visual_design", lambda *_args: None)
    monkeypatch.setattr(production.visual_prompt_agent, "reextract_visible_characters", fake_reextract)

    await production.reextract_project_characters(state.project_id)

    assert captured["source_text"] == state.script_draft_text


def test_compact_visual_prompt_includes_bound_asset_anchors():
    script = _script("bound assets")
    script.asset_manifest = VisualAssetManifest(
        characters=[VisualCharacterAsset(
            asset_id="char_liu", name="刘宁", appearance_mode="on_screen",
            description="二十八岁，黑色短发，墨绿羊毛大衣，左眉有浅痣",
        )],
        locations=[LocationAsset(
            location_id="loc_corridor", canonical_name="旧公寓走廊",
            environment_description="剥落的灰墙，尽头一盏忽明忽暗的钨丝灯",
            representative_scene_id=1, shot_ids=[1],
        )],
        props=[PropAsset(
            prop_id="prop_key", name="黄铜钥匙",
            description="磨损的黄铜钥匙，红色棉线系在匙孔上", shot_ids=[1], is_handheld=True,
        )],
        shot_bindings=[ShotVisualBinding(
            scene_id=1, location_id="loc_corridor", on_screen_character_ids=["char_liu"],
            prop_ids=["prop_key"], visible_state_notes="大衣保持干燥，无围巾",
        )],
    )
    output = _VisualPromptOutput(
        overall_style="现代悬疑电影写实", color_palette="冷蓝灰", aspect_ratio="9:16",
        global_character_descriptions_cn={"刘宁": "二十八岁，黑色短发，墨绿羊毛大衣，左眉有浅痣"},
        shots=[_ShotVisualPrompt(scene_id=1, subject_state="停在门前", action_timeline="0-5秒缓慢抬手", duration_sec=5)],
    )

    _visual, prompts = VisualPromptAgent(api_provider="mock")._split_output(output, script, None)

    prompt = prompts.shots[0].prompt
    assert "刘宁" in prompt
    assert "左眉有浅痣" in prompt
    assert "旧公寓走廊" in prompt
    assert "剥落的灰墙" in prompt
    assert "黄铜钥匙" in prompt
    assert "红色棉线" in prompt


@pytest.mark.parametrize(
    ("visual_style_id", "art_style", "expected_style", "unexpected_style"),
    [
        ("cinematic_cel", "现代电影级二维赛璐璐", "现代电影级二维赛璐璐", "电影级真人写实"),
        ("live_action_cinematic", "电影级真人写实", "电影级真人写实", "现代电影级二维赛璐璐"),
    ],
)
def test_timed_prompt_keeps_project_style_and_bound_context(
    visual_style_id, art_style, expected_style, unexpected_style
):
    """Timed post-processing must retain the complete deterministic visual context."""
    script = ScriptBreakdown(
        title="风格连续性",
        genre="悬疑",
        logline="两人守在走廊门口。",
        duration_sec=6,
        narration_style="none",
        scenes=[Scene(
            scene_id=1,
            duration_sec=6,
            location="旧公寓走廊",
            characters=["刘宁", "顾衡"],
            dialogue=[DialogueLine(character="刘宁", line="你听到了吗？", emotion="紧张")],
            action_description="刘宁握住黄铜钥匙，顾衡警觉地望向走廊尽头",
            camera_direction="中景缓慢推进",
            emotion_tone="紧张",
        )],
        asset_manifest=VisualAssetManifest(
            characters=[
                VisualCharacterAsset(
                    asset_id="char_liu", name="刘宁", appearance_mode="on_screen",
                    description="黑色短发，墨绿色大衣，左眉有浅痣",
                ),
                VisualCharacterAsset(
                    asset_id="char_gu", name="顾衡", appearance_mode="on_screen",
                    description="灰色风衣，窄脸，银色眼镜",
                ),
            ],
            locations=[LocationAsset(
                location_id="loc_corridor", canonical_name="旧公寓走廊",
                environment_description="剥落灰墙，尽头钨丝灯，地面积水",
                representative_scene_id=1, shot_ids=[1],
            )],
            props=[PropAsset(
                prop_id="prop_key", name="黄铜钥匙",
                description="磨损黄铜钥匙，红色棉线系在匙孔上",
                shot_ids=[1], is_handheld=True,
            )],
            shot_bindings=[ShotVisualBinding(
                scene_id=1,
                location_id="loc_corridor",
                on_screen_character_ids=["char_liu", "char_gu"],
                prop_ids=["prop_key"],
            )],
        ),
    )
    output = _VisualPromptOutput(
        overall_style=art_style,
        color_palette="冷蓝灰",
        aspect_ratio="9:16",
        global_character_descriptions_cn={
            "刘宁": "黑色短发，墨绿色大衣，左眉有浅痣",
            "顾衡": "灰色风衣，窄脸，银色眼镜",
        },
        shots=[_ShotVisualPrompt(
            scene_id=1,
            subject_state="两人守在门口",
            action_timeline="0-3秒刘宁握紧钥匙；3-6秒顾衡转头观察",
            environment_delta="走廊尽头钨丝灯闪烁",
            camera_motion="中景缓慢推进",
            lighting_delta="冷蓝环境光与门缝暖光对照",
            prop_state="黄铜钥匙保持在刘宁右手",
            duration_sec=6,
        )],
    )
    profile = CreativeProfile(visual_style_id=visual_style_id, art_style=art_style)

    _visual, prompts = VisualPromptAgent(api_provider="mock")._split_output(
        output, script, None, profile
    )

    prompt = prompts.shots[0].prompt
    assert expected_style in prompt
    assert "保持二维动画媒介" in prompt if visual_style_id == "cinematic_cel" else "电影级真人镜头语言" in prompt
    assert "黑色短发" in prompt
    assert "剥落灰墙" in prompt
    assert "黄铜钥匙" in prompt
    assert unexpected_style not in prompt


def test_legacy_visual_prompt_is_supplemented_with_asset_anchors():
    script = _script("legacy prompt")
    script.asset_manifest = VisualAssetManifest(
        characters=[VisualCharacterAsset(
            asset_id="char_liu", name="刘宁", appearance_mode="on_screen",
            description="二十八岁，黑色短发，墨绿羊毛大衣，左眉有浅痣",
        )],
        locations=[LocationAsset(
            location_id="loc_corridor", canonical_name="旧公寓走廊",
            environment_description="剥落的灰墙，尽头一盏钨丝灯",
            representative_scene_id=1, shot_ids=[1],
        )],
        props=[PropAsset(
            prop_id="prop_key", name="黄铜钥匙", description="磨损黄铜，系红色棉线", shot_ids=[1],
        )],
        shot_bindings=[ShotVisualBinding(
            scene_id=1, location_id="loc_corridor", on_screen_character_ids=["char_liu"], prop_ids=["prop_key"],
        )],
    )
    output = _VisualPromptOutput(
        overall_style="现代悬疑电影写实", color_palette="冷蓝灰", aspect_ratio="9:16",
        global_character_descriptions_cn={"刘宁": "二十八岁，黑色短发，墨绿羊毛大衣，左眉有浅痣"},
        shots=[_ShotVisualPrompt(scene_id=1, prompt="模型提供的旧式完整镜头描述", duration_sec=5)],
    )

    _visual, prompts = VisualPromptAgent(api_provider="mock")._split_output(output, script, None)

    prompt = prompts.shots[0].prompt
    assert "模型提供的旧式完整镜头描述" not in prompt
    assert "刘宁" in prompt
    assert "旧公寓走廊" in prompt
    assert "黄铜钥匙" in prompt


@pytest.mark.asyncio
async def test_legacy_script_route_replaces_a_script_while_visualization_is_running(monkeypatch):
    state = DramaState(
        project_id="visual-project", script=_script("old script"), visual_stage="generating",
    )
    load_flags = []
    cancelled = []

    def load_state(_project_id, *, require_visual=True):
        load_flags.append(require_visual)
        if require_visual:
            raise AssertionError("phase-one script replacement must not require visual prompts")
        return state

    monkeypatch.setattr(scripts, "_load_project_state", load_state)
    monkeypatch.setattr(scripts.project_store, "get_project", lambda _project_id: {"assets": {}})
    monkeypatch.setattr(scripts.project_store, "set_script", lambda *_args: None)
    monkeypatch.setattr(scripts.project_store, "set_state_json", lambda *_args: None)
    monkeypatch.setattr(scripts.project_store, "update_status", lambda *_args: None)
    monkeypatch.setattr(
        scripts.job_store, "list_jobs",
        lambda *_args, **_kwargs: [{"job_id": "old-visual", "job_type": "script.visualize"}],
    )
    monkeypatch.setattr(scripts.job_manager, "cancel", lambda *_args: cancelled.append(_args[1]))
    monkeypatch.setattr(
        scripts.script_agent, "run", lambda *_args, **_kwargs: asyncio.sleep(0, result=_script("replacement")),
    )
    monkeypatch.setattr(scripts.job_manager, "create_job", lambda *_args, **_kwargs: {"job_id": "visual-job-2"})

    response = await scripts.generate_script(ScriptRequest(user_input="new idea", project_id=state.project_id))

    assert response.script["title"] == "replacement"
    assert response.project_id == state.project_id
    assert load_flags == [False]
    assert cancelled == ["old-visual"]


@pytest.mark.asyncio
async def test_ai_script_revision_restarts_visualization_for_the_revised_script(monkeypatch):
    state = DramaState(
        project_id="visual-project", script=_script("old script"), visual_stage="generating",
    )
    cancelled = []
    created = []

    monkeypatch.setattr(scripts.project_store, "get_project", lambda _project_id: {"assets": {"state_json": ""}})
    monkeypatch.setattr(scripts, "_state_from_json", lambda _raw: state)
    monkeypatch.setattr(scripts.project_store, "set_script", lambda *_args: None)
    monkeypatch.setattr(scripts.project_store, "set_state_json", lambda *_args: None)
    monkeypatch.setattr(
        scripts.job_store, "list_jobs",
        lambda *_args, **_kwargs: [{"job_id": "old-visual", "job_type": "script.visualize"}],
    )
    monkeypatch.setattr(
        scripts.script_agent, "revise_structure", lambda *_args, **_kwargs: asyncio.sleep(0, result=_script("revised script")),
    )
    monkeypatch.setattr(scripts.job_manager, "cancel", lambda *_args: cancelled.append(_args[1]))
    monkeypatch.setattr(
        scripts.job_manager, "create_job",
        lambda *args, **_kwargs: created.append(args) or {"job_id": "new-visual"},
    )

    result = await scripts.revise_structured_script(state.project_id, ScriptRevisionRequest(instruction="加强冲突"))

    assert result["script"]["title"] == "revised script"
    assert result["visual_stage"] == "generating"
    assert result["visual_job_id"] == "new-visual"
    assert cancelled == ["old-visual"]
    assert created and created[0][1] == "script.visualize"


class _TelemetryOutput(BaseModel):
    value: int


@pytest.mark.asyncio
async def test_structured_llm_telemetry_records_retries_and_json_repairs():
    responses = iter(["not json", '{"value": 7}'])

    class _Completions:
        async def create(self, **_kwargs):
            raw = next(responses)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(
                tool_calls=[SimpleNamespace(function=SimpleNamespace(arguments=raw))],
            ))])

    client = object.__new__(LLMClient)
    client.provider = "openai"
    client.model = "test-model"
    client._openai = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    client._ensure_client = lambda: None
    telemetry = {}

    result = await client.generate_structured(
        system_prompt="system", user_prompt="user", output_schema=_TelemetryOutput, telemetry=telemetry,
    )

    assert result.value == 7
    assert telemetry["attempts"] == 2
    assert telemetry["repair_attempts"] == 1
    assert telemetry["fallback_used"] is False
    assert telemetry["input_chars"] == len("system") + len("user")
