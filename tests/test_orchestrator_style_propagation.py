import asyncio

from src.graph.orchestrator import create_stage2_graph
from src.graph.state import DramaState
from src.schemas.character import CharacterImageAssets
from src.schemas.project import CreativeProfile
from src.schemas.script import Scene, ScriptBreakdown
from src.schemas.scene_image import SceneImageAssets
from src.schemas.visual import VisualDesign


class _RecorderImageAgent:
    def __init__(self):
        self.kwargs = None

    async def run(self, *_args, **kwargs):
        self.kwargs = kwargs
        return CharacterImageAssets(), SceneImageAssets()


def test_stage2_passes_stable_project_visual_profile_to_image_generation():
    agent = _RecorderImageAgent()
    graph = create_stage2_graph(agent)
    profile = CreativeProfile(
        visual_style_id="semi_realistic_anime",
        art_style="写实",  # stale compatibility text from an older project
    )
    state = DramaState(
        project_id="style-propagation",
        script=ScriptBreakdown(
            title="风格传递",
            genre="都市",
            logline="测试",
            duration_sec=3,
            narration_style="无旁白",
            scenes=[
                Scene(
                    scene_id=1,
                    duration_sec=3,
                    location="办公室",
                    characters=[],
                    action_description="空镜",
                    camera_direction="中景",
                    emotion_tone="平静",
                )
            ],
        ),
        visual_design=VisualDesign(overall_style="写实", color_palette="冷色", scenes=[]),
        creative_profile=profile,
    )

    asyncio.run(graph.ainvoke(state, config={"configurable": {"thread_id": "style-propagation"}}))

    assert agent.kwargs["creative_profile"].visual_style_id == "semi_realistic_anime"
