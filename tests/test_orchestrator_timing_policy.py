import asyncio
from types import SimpleNamespace

from src.graph import orchestrator
from src.graph.state import DramaState
from src.schemas.prompt import GenerationPrompts
from src.schemas.script import Scene, ScriptBreakdown
from src.schemas.visual import VisualDesign


class _FakeGraph:
    def __init__(self, _state_type):
        self.nodes = {}

    def add_node(self, name, fn):
        self.nodes[name] = fn

    def add_edge(self, *_args, **_kwargs):
        return None

    def add_conditional_edges(self, *_args, **_kwargs):
        return None

    def set_entry_point(self, *_args, **_kwargs):
        return None

    def compile(self, **_kwargs):
        return self.nodes


class _ScriptAgent:
    async def run(self, _user_input, _input_mode):
        return ScriptBreakdown(
            title="fixture",
            genre="剧情",
            logline="fixture",
            duration_sec=8,
            narration_style="无旁白",
            scenes=[Scene(
                scene_id=1,
                duration_sec=8,
                location="室内",
                characters=["角色A"],
                action_description="角色A站在门边",
                camera_direction="中景",
                emotion_tone="平静",
            )],
        )


class _VisualPromptAgent:
    def __init__(self):
        self.kwargs = None

    async def run(self, _script, **kwargs):
        self.kwargs = kwargs
        return VisualDesign(
            overall_style="写实",
            color_palette="冷色",
            aspect_ratio="9:16",
            scenes=[],
        ), GenerationPrompts(
            api_provider="mock",
            shots=[],
        )


def test_stage1_passes_video_generation_policy_to_visual_prompt_agent(monkeypatch):
    monkeypatch.setattr(orchestrator, "StateGraph", _FakeGraph)
    visual_agent = _VisualPromptAgent()
    nodes = orchestrator.create_stage1_graph(_ScriptAgent(), visual_agent)
    state = DramaState(
        user_input="fixture",
        input_mode="script",
        video_generation_policy={
            "provider": "happyhorse",
            "model": "happyhorse-test",
            "capability": {"duration_max": 15},
        },
    )

    asyncio.run(nodes["visual_prompt"](state))

    assert visual_agent.kwargs["video_generation_policy"] == state.video_generation_policy


class _VideoAgent:
    def __init__(self):
        self.calls = []

    async def run(self, *_args, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(shots=[], total_cost=0.0)


def test_stage4_selects_project_video_agent_for_execution(monkeypatch):
    monkeypatch.setattr(orchestrator, "StateGraph", _FakeGraph)
    default_agent = _VideoAgent()
    selected_agent = _VideoAgent()
    selected_policies = []

    monkeypatch.setattr(
        orchestrator,
        "video_agent_for_policy",
        lambda policy: (selected_policies.append(policy) or selected_agent),
        raising=False,
    )
    nodes = orchestrator.create_stage4_graph(default_agent)
    state = DramaState(
        project_id="project-happyhorse",
        video_generation_policy={
            "provider": "happyhorse",
            "model": "happyhorse-test",
            "resolution": "1080p",
        },
        generation_prompts=GenerationPrompts(api_provider="mock", shots=[]),
    )

    asyncio.run(nodes["video_gen"](state))

    assert selected_policies == [state.video_generation_policy]
    assert len(selected_agent.calls) == 1
    assert default_agent.calls == []
    assert selected_agent.calls[0]["video_generation_policy"] == state.video_generation_policy


class _ConfiguredVideoAgent(_VideoAgent):
    def __init__(self):
        super().__init__()
        self.api_client = SimpleNamespace(
            provider_name="happyhorse",
            model="happyhorse-1.1-r2v",
            resolution="1080p",
        )


def test_full_graph_derives_default_video_policy_for_empty_state(monkeypatch):
    monkeypatch.setattr(orchestrator, "StateGraph", _FakeGraph)
    visual_agent = _VisualPromptAgent()
    default_agent = _ConfiguredVideoAgent()
    nodes = orchestrator.build_drama_graph(
        _ScriptAgent(),
        visual_agent,
        object(),
        default_agent,
        object(),
        object(),
        object(),
    )
    state = DramaState(
        user_input="fixture",
        input_mode="script",
        script=ScriptBreakdown(
            title="fixture",
            genre="剧情",
            logline="fixture",
            duration_sec=20,
            narration_style="无旁白",
            scenes=[Scene(
                scene_id=1,
                duration_sec=20,
                location="室内",
                characters=["角色A"],
                action_description="角色A站在门边，随后走到桌边。",
                camera_direction="中景",
                emotion_tone="平静",
            )],
        ),
    )

    asyncio.run(nodes["visual_prompt"](state))

    policy = visual_agent.kwargs["video_generation_policy"]
    assert policy["provider"] == "happyhorse"
    assert policy["model"] == "happyhorse-1.1-r2v"
    assert policy["capability"]["duration_max"] == 15
    assert policy["capability"]["durations"] == []
    assert policy["capability"]["discrete_durations"] is False


def test_stage4_passes_derived_default_policy_for_empty_state(monkeypatch):
    monkeypatch.setattr(orchestrator, "StateGraph", _FakeGraph)
    default_agent = _ConfiguredVideoAgent()
    nodes = orchestrator.create_stage4_graph(default_agent)
    state = DramaState(
        project_id="legacy-empty-policy",
        generation_prompts=GenerationPrompts(api_provider="mock", shots=[]),
    )

    asyncio.run(nodes["video_gen"](state))

    policy = default_agent.calls[0]["video_generation_policy"]
    assert policy["provider"] == "happyhorse"
    assert policy["model"] == "happyhorse-1.1-r2v"
    assert policy["capability"]["duration_max"] == 15
    assert policy["capability"]["durations"] == []
    assert policy["capability"]["discrete_durations"] is False
