"""LangGraph orchestrator — builds and runs the multi-agent pipeline.

Supports two modes:
1. Full pipeline (backward compatible): script → visual → ... → assembly
2. Segmented pipeline: 6 independent stages for interactive UI control
"""

from typing import Literal
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from .state import DramaState
from src.config import MAX_REVIEW_LOOPS


def build_drama_graph(
    script_agent,
    visual_agent,
    prompt_agent,
    video_gen_agent,
    review_agent,
    audio_agent,
    assembly_agent,
    character_agent=None,
    scene_image_agent=None,
) -> StateGraph:
    """Build the full LangGraph StateGraph for the video drama pipeline.

    Pipeline flow:
        script → visual → character_images → scene_images → prompt
            → video_gen → review
                ├── pass → audio → assembly → END
                └── fail (under limit) → prompt (retry)
                └── fail (over limit) → audio → assembly → END

    Args:
        All agent instances. character_agent and scene_image_agent are optional
        (if omitted, their nodes become no-ops).
    """
    # ── Node wrappers ──────────────────────────────────────────

    async def node_script(state: DramaState) -> dict:
        state.log("📝 编剧 Agent: 开始生成分镜脚本...")
        script = await script_agent.run(state.user_input)
        state.log(f"   剧本标题: {script.title} | {len(script.scenes)} 个场景 | 预估 {script.duration_sec}s")
        return {"script": script}

    async def node_visual(state: DramaState) -> dict:
        state.log("🎨 视觉设计 Agent: 设计视觉风格...")
        visual = await visual_agent.run(state.script)
        state.log(f"   风格: {visual.overall_style} | 色调: {visual.color_palette}")
        return {"visual_design": visual}

    async def node_character(state: DramaState) -> dict:
        if character_agent is None:
            state.log("🎭 角色形象 Agent: (跳过) 未配置")
            return {}
        state.log("🎭 角色形象 Agent: 分析角色并生成形象图...")
        images = await character_agent.run(
            state.script,
            state.visual_design,
            project_id=state.project_id,
        )
        state.log(f"   角色形象图: {len(images.characters)} 个 | 成本: ${images.total_cost:.2f}")
        return {
            "character_images": images,
            "total_cost": state.total_cost + images.total_cost,
        }

    async def node_scene_image(state: DramaState) -> dict:
        if scene_image_agent is None:
            state.log("🖼️ 场景图 Agent: (跳过) 未配置")
            return {}
        state.log("🖼️ 场景图 Agent: 生成场景环境图...")
        images = await scene_image_agent.run(
            state.script,
            state.visual_design,
            project_id=state.project_id,
        )
        state.log(f"   场景图: {len(images.scenes)} 个 | 成本: ${images.total_cost:.2f}")
        return {
            "scene_images": images,
            "total_cost": state.total_cost + images.total_cost,
        }

    async def node_prompt(state: DramaState) -> dict:
        state.log("🔧 提示工程 Agent: 生成视频 prompt...")
        prompts = await prompt_agent.run(
            state.script,
            state.visual_design,
            character_images=state.character_images,
            scene_images=state.scene_images,
            prompt_overrides=state.prompt_overrides,
        )
        state.log(f"   目标 API: {prompts.api_provider} | {len(prompts.shots)} 个镜头")
        return {"generation_prompts": prompts}

    async def node_video_gen(state: DramaState) -> dict:
        state.log("🎬 视频生成 Agent: 调用 API 生成视频...")
        # Apply prompt overrides to the prompts before generation
        if state.prompt_overrides and state.generation_prompts:
            for i, shot in enumerate(state.generation_prompts.shots):
                if str(i) in state.prompt_overrides:
                    shot.prompt = state.prompt_overrides[str(i)]
                    state.log(f"   ✏️ 镜头 {i+1} 使用了用户修改的 prompt")

        assets = await video_gen_agent.run(state.generation_prompts)
        success_count = sum(1 for s in assets.shots if s.success)
        state.log(f"   成功: {success_count}/{len(assets.shots)} | 成本: ${assets.total_cost:.2f}")
        return {
            "generated_assets": assets,
            "total_cost": state.total_cost + assets.total_cost,
        }

    async def node_review(state: DramaState) -> dict:
        state.log("🔍 审核 Agent: 质量审查中...")
        result = await review_agent.run(
            state.script,
            state.visual_design,
            state.generated_assets,
        )
        state.log(f"   评分: {result.overall_score:.2f} | {'✅ 通过' if result.passed else '❌ 需修改'}")
        if not result.passed:
            for issue in result.issues:
                state.log(f"   - [{issue.severity}] {issue.description}")
        return {"review_result": result, "review_loop_count": state.review_loop_count + 1}

    async def node_audio(state: DramaState) -> dict:
        state.log("🎵 音频 Agent: 生成配音和 BGM...")
        audio = await audio_agent.run(state.script, state.generated_assets)
        state.log(f"   配音场景: {len(audio.scenes)} | BGM: {audio.bgm_mood}")
        return {"audio_assets": audio}

    async def node_assembly(state: DramaState) -> dict:
        state.log("🎞️ 合成 Agent: 拼接视频 + 字幕...")
        path = await assembly_agent.run(
            state.script,
            state.generated_assets,
            state.audio_assets,
        )
        state.log(f"   输出: {path}")
        return {"output_video_path": path}

    # ── Routing ───────────────────────────────────────────────

    def route_after_review(state: DramaState) -> Literal["prompt", "audio", "__end__"]:
        if state.review_result is None:
            return "audio"
        if state.review_result.passed:
            return "audio"
        if state.review_loop_count < MAX_REVIEW_LOOPS:
            state.log(f"   ↩️ 返回提示工程 Agent 重试 (第 {state.review_loop_count} 次)")
            return "prompt"
        state.log(f"   ⚠️ 已达最大重试次数 ({MAX_REVIEW_LOOPS})，使用现有素材继续")
        return "audio"

    # ── Build graph ───────────────────────────────────────────

    graph = StateGraph(DramaState)

    graph.add_node("script", node_script)
    graph.add_node("visual", node_visual)
    graph.add_node("character_images", node_character)
    graph.add_node("scene_images", node_scene_image)
    graph.add_node("prompt", node_prompt)
    graph.add_node("video_gen", node_video_gen)
    graph.add_node("review", node_review)
    graph.add_node("audio", node_audio)
    graph.add_node("assembly", node_assembly)

    graph.set_entry_point("script")
    graph.add_edge("script", "visual")
    graph.add_edge("visual", "character_images")
    graph.add_edge("character_images", "scene_images")
    graph.add_edge("scene_images", "prompt")
    graph.add_edge("prompt", "video_gen")
    graph.add_edge("video_gen", "review")

    graph.add_conditional_edges(
        "review",
        route_after_review,
        {"prompt": "prompt", "audio": "audio"},
    )

    graph.add_edge("audio", "assembly")
    graph.add_edge("assembly", END)

    memory = MemorySaver()
    return graph.compile(checkpointer=memory)


def create_stage1_graph(script_agent, visual_agent):
    """Stage 1 graph: script → visual → END"""
    graph = StateGraph(DramaState)

    async def node_script(state: DramaState) -> dict:
        state.log("📝 编剧 Agent: 开始生成分镜脚本...")
        script = await script_agent.run(state.user_input)
        state.log(f"   剧本标题: {script.title} | {len(script.scenes)} 个场景")
        return {"script": script}

    async def node_visual(state: DramaState) -> dict:
        state.log("🎨 视觉设计 Agent: 设计视觉风格...")
        visual = await visual_agent.run(state.script)
        state.log(f"   风格: {visual.overall_style}")
        return {"visual_design": visual}

    graph.add_node("script", node_script)
    graph.add_node("visual", node_visual)
    graph.set_entry_point("script")
    graph.add_edge("script", "visual")
    graph.add_edge("visual", END)
    return graph.compile(checkpointer=MemorySaver())


def create_stage2_graph(character_agent, scene_image_agent):
    """Stage 2 graph: character_images → scene_images → END"""
    graph = StateGraph(DramaState)

    async def node_character(state: DramaState) -> dict:
        if character_agent is None:
            state.log("🎭 (跳过) 未配置角色 Agent")
            return {}
        state.log("🎭 角色形象 Agent: 分析角色并生成形象图...")
        images = await character_agent.run(
            state.script, state.visual_design, project_id=state.project_id,
        )
        state.log(f"   角色形象图: {len(images.characters)} 个")
        return {
            "character_images": images,
            "total_cost": state.total_cost + images.total_cost,
        }

    async def node_scene(state: DramaState) -> dict:
        if scene_image_agent is None:
            state.log("🖼️ (跳过) 未配置场景 Agent")
            return {}
        state.log("🖼️ 场景图 Agent: 生成场景环境图...")
        images = await scene_image_agent.run(
            state.script, state.visual_design, project_id=state.project_id,
        )
        state.log(f"   场景图: {len(images.scenes)} 个")
        return {
            "scene_images": images,
            "total_cost": state.total_cost + images.total_cost,
        }

    graph.add_node("character_images", node_character)
    graph.add_node("scene_images", node_scene)
    graph.set_entry_point("character_images")
    graph.add_edge("character_images", "scene_images")
    graph.add_edge("scene_images", END)
    return graph.compile(checkpointer=MemorySaver())


def create_stage3_graph(prompt_agent):
    """Stage 3 graph: prompt generation → END"""
    graph = StateGraph(DramaState)

    async def node_prompt(state: DramaState) -> dict:
        state.log("🔧 提示工程 Agent: 生成视频 prompt...")
        prompts = await prompt_agent.run(
            state.script, state.visual_design,
            character_images=state.character_images,
            scene_images=state.scene_images,
            prompt_overrides=state.prompt_overrides,
        )
        state.log(f"   {len(prompts.shots)} 个镜头 prompt 已生成")
        return {"generation_prompts": prompts}

    graph.add_node("prompt", node_prompt)
    graph.set_entry_point("prompt")
    graph.add_edge("prompt", END)
    return graph.compile(checkpointer=MemorySaver())


def create_stage4_graph(video_gen_agent):
    """Stage 4 graph: video generation → END"""
    graph = StateGraph(DramaState)

    async def node_video(state: DramaState) -> dict:
        state.log("🎬 视频生成 Agent: 调用 API 生成视频...")
        if state.prompt_overrides and state.generation_prompts:
            for i, shot in enumerate(state.generation_prompts.shots):
                if str(i) in state.prompt_overrides:
                    shot.prompt = state.prompt_overrides[str(i)]
        assets = await video_gen_agent.run(state.generation_prompts)
        success_count = sum(1 for s in assets.shots if s.success)
        state.log(f"   成功: {success_count}/{len(assets.shots)} | 成本: ${assets.total_cost:.2f}")
        return {
            "generated_assets": assets,
            "total_cost": state.total_cost + assets.total_cost,
        }

    graph.add_node("video_gen", node_video)
    graph.set_entry_point("video_gen")
    graph.add_edge("video_gen", END)
    return graph.compile(checkpointer=MemorySaver())


def create_stage5_graph(audio_agent):
    """Stage 5 graph: audio generation → END"""
    graph = StateGraph(DramaState)

    async def node_audio(state: DramaState) -> dict:
        state.log("🎵 音频 Agent: 生成配音和 BGM...")
        audio = await audio_agent.run(state.script, state.generated_assets)
        state.log(f"   配音场景: {len(audio.scenes)} | BGM: {audio.bgm_mood}")
        return {"audio_assets": audio}

    graph.add_node("audio", node_audio)
    graph.set_entry_point("audio")
    graph.add_edge("audio", END)
    return graph.compile(checkpointer=MemorySaver())


def create_stage6_graph(assembly_agent):
    """Stage 6 graph: assembly → END"""
    graph = StateGraph(DramaState)

    async def node_assembly(state: DramaState) -> dict:
        state.log("🎞️ 合成 Agent: 拼接视频 + 字幕...")
        path = await assembly_agent.run(
            state.script, state.generated_assets, state.audio_assets,
        )
        state.log(f"   输出: {path}")
        return {"output_video_path": path}

    graph.add_node("assembly", node_assembly)
    graph.set_entry_point("assembly")
    graph.add_edge("assembly", END)
    return graph.compile(checkpointer=MemorySaver())


class DramaOrchestrator:
    """High-level orchestrator for the drama generation pipeline.

    Supports two modes:
    - Full pipeline via generate()
    - Segmented pipeline via 6 stage graphs for interactive UI
    """

    def __init__(
        self,
        script_agent,
        visual_agent,
        prompt_agent,
        video_gen_agent,
        review_agent,
        audio_agent,
        assembly_agent,
        character_agent=None,
        scene_image_agent=None,
    ):
        self.script_agent = script_agent
        self.visual_agent = visual_agent
        self.prompt_agent = prompt_agent
        self.video_gen_agent = video_gen_agent
        self.review_agent = review_agent
        self.audio_agent = audio_agent
        self.assembly_agent = assembly_agent
        self.character_agent = character_agent
        self.scene_image_agent = scene_image_agent

        # Full pipeline graph
        self.graph = build_drama_graph(
            script_agent, visual_agent, prompt_agent,
            video_gen_agent, review_agent, audio_agent, assembly_agent,
            character_agent, scene_image_agent,
        )

        # Segment graphs
        self.stage1 = create_stage1_graph(script_agent, visual_agent)
        self.stage2 = create_stage2_graph(character_agent, scene_image_agent)
        self.stage3 = create_stage3_graph(prompt_agent)
        self.stage4 = create_stage4_graph(video_gen_agent)
        self.stage5 = create_stage5_graph(audio_agent)
        self.stage6 = create_stage6_graph(assembly_agent)

    async def generate(self, user_input: str) -> DramaState:
        """Run the full multi-agent pipeline."""
        initial_state = DramaState(user_input=user_input)
        initial_state.log(f"🚀 开始处理: {user_input[:100]}...")

        try:
            result = await self.graph.ainvoke(
                initial_state,
                config={"configurable": {"thread_id": "drama-main"}},
            )
            if isinstance(result, dict):
                final_state = DramaState(**result)
            else:
                final_state = result
            final_state.log("✅ 视频短剧生成完成!")
            return final_state
        except Exception as e:
            initial_state.log(f"❌ 生成失败: {e}")
            initial_state.error_message = str(e)
            return initial_state

    async def run_stage(self, stage: int, state: DramaState) -> DramaState:
        """Run a single stage of the pipeline.

        Args:
            stage: Stage number 1-6.
            state: Current DramaState (must contain outputs from previous stages).

        Returns:
            Updated DramaState after the stage completes.
        """
        graphs = {
            1: self.stage1,
            2: self.stage2,
            3: self.stage3,
            4: self.stage4,
            5: self.stage5,
            6: self.stage6,
        }
        graph = graphs.get(stage)
        if graph is None:
            raise ValueError(f"Invalid stage: {stage}. Must be 1-6.")

        tid = f"stage{stage}-{state.project_id or 'main'}"

        try:
            result = await graph.ainvoke(
                state,
                config={"configurable": {"thread_id": tid}},
            )
            if isinstance(result, dict):
                return DramaState(**result)
            return result
        except Exception as e:
            state.log(f"❌ Stage {stage} 失败: {e}")
            state.error_message = str(e)
            return state
