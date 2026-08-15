"""LangGraph orchestrator — builds and runs the multi-agent pipeline.

Supports two modes:
1. Full pipeline: script → visual_prompt → image_gen → video_gen → review → audio → assembly
2. Segmented pipeline: 6 independent stages for interactive UI control

Architecture (7 agents, down from 9):
  - VisualPromptAgent merges VisualDesign + PromptEngineering (1 LLM call instead of 2)
  - ImageGenerationAgent merges Character + Scene image generation
"""

from typing import Literal
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver

from .state import DramaState
from src.config import MAX_REVIEW_LOOPS


def build_drama_graph(
    script_agent,
    visual_prompt_agent,
    image_gen_agent,
    video_gen_agent,
    review_agent,
    audio_agent,
    assembly_agent,
) -> StateGraph:
    """Build the full LangGraph StateGraph for the video drama pipeline.

    Pipeline flow (7 nodes):
        script → visual_prompt → image_gen → video_gen → review
            ├── pass → audio → assembly → END
            └── fail (under limit) → visual_prompt (retry)
            └── fail (over limit) → audio → assembly → END
    """
    # ── Node wrappers ──────────────────────────────────────────

    async def node_script(state: DramaState) -> dict:
        state.log("📝 编剧 Agent: 开始生成分镜脚本...")
        script = await script_agent.run(
            state.user_input, state.input_mode, state.creative_profile,
        )
        state.log(f"   剧本标题: {script.title} | {len(script.scenes)} 个场景 | 预估 {script.duration_sec}s")
        return {"script": script}

    async def node_visual_prompt(state: DramaState) -> dict:
        state.log("🎨🔧 视觉与提示 Agent: 设计视觉风格 + 生成视频 prompt...")
        visual, prompts = await visual_prompt_agent.run(
            state.script,
            character_images=state.character_images,
            scene_images=state.scene_images,
            prompt_overrides=state.prompt_overrides,
            creative_profile=state.creative_profile,
            source_text=state.script_draft_text or (
                state.user_input if state.input_mode == "script" else ""
            ),
        )
        state.log(f"   风格: {visual.overall_style} | 色调: {visual.color_palette}")
        state.log(f"   目标 API: {prompts.api_provider} | {len(prompts.shots)} 个镜头 prompt")
        return {"visual_design": visual, "generation_prompts": prompts}

    async def node_image_gen(state: DramaState) -> dict:
        state.log("🎭🖼️ 图像生成 Agent: 生成角色形象图 + 场景环境图...")
        char_assets, scene_assets = await image_gen_agent.run(
            state.script,
            state.visual_design,
            project_id=state.project_id,
            creative_profile=state.creative_profile,
        )
        state.log(f"   角色形象图: {len(char_assets.characters)} 个 | 场景图: {len(scene_assets.scenes)} 个")
        cost = char_assets.total_cost + scene_assets.total_cost
        state.log(f"   图像成本: ${cost:.2f}")

        # Vision analysis: extract structured descriptions from generated portraits
        if char_assets.characters and state.visual_design:
            try:
                from src.utils.llm import get_llm_client
                vision_llm = get_llm_client()
                updated_descs = dict(state.visual_design.global_character_descriptions or {})
                analyzed = set()
                for img in char_assets.characters:
                    name = img.character_name
                    if name in analyzed or img.view_type not in {"front_half", "portrait"}:
                        continue
                    if img.image_path:
                        try:
                            state.log(f"   🔍 结构化分析角色 '{name}' 的形象图...")
                            desc = await vision_llm.analyze_image(
                                img.image_path,
                                f"Analyze {name}'s portrait and output in the required structured format.",
                            )
                            if desc and len(desc) > 30:
                                # Only use Vision result if it looks structured (contains commas)
                                if desc.count(',') >= 5:
                                    updated_descs[name] = desc
                                    analyzed.add(name)
                                    state.log(f"   ✅ '{name}' 外貌已从形象图提取")
                                else:
                                    state.log(f"   ⚠️ '{name}' Vision 输出不够结构化，保留 LLM 版本")
                        except Exception as e:
                            state.log(f"   ⚠️ Vision 分析 '{name}' 失败: {e}")
                if analyzed:
                    state.visual_design.global_character_descriptions = updated_descs
            except Exception as e:
                state.log(f"   ⚠️ Vision 分析流程跳过: {e}")

        # Vision analysis: extract precise environment descriptions from scene wide-shots
        if scene_assets.scenes:
            try:
                from src.utils.llm import get_llm_client
                vision_llm = get_llm_client()
                scene_descs: dict[int, str] = {}
                for img in scene_assets.scenes:
                    sid = img.scene_id
                    if sid in scene_descs or img.view_type != "wide":
                        continue
                    if img.image_path:
                        try:
                            state.log(f"   🔍 结构化分析场景 {sid} 的背景图...")
                            desc = await vision_llm.analyze_image(
                                img.image_path,
                                f"Describe this scene environment in the required structured format.",
                                system="""You are a cinematic location scout. Describe the scene in the image using a strict structured format. Every field must be filled with objective visual facts.

Output format (one line, comma-separated):
Location type, Time of day and lighting, Architecture and materials, Foreground midground background layers, Key props and objects, Color palette and mood, Spatial depth and dimensions, Atmospheric conditions

Example:
Modern convenience store interior, late night with cool white fluorescent light and warm shelf glow, glass windows and metal shelving with tiled floor, foreground magazine rack midground aisle shelves background checkout counter, plastic bottles snack packages and a glowing refrigerator case, cool blue whites contrast with warm yellow shelf lights shallow depth of field, narrow aisle about 3 meters deep vertical 9:16 framing, light dust motes in fluorescent light slight condensation on windows""",
                            )
                            if desc and len(desc) > 20:
                                scene_descs[sid] = desc
                                state.log(f"   ✅ 场景 {sid} 环境描述已提取 (Vision)")
                        except Exception as e:
                            state.log(f"   ⚠️ Vision 分析场景 {sid} 失败: {e}")
                if scene_descs:
                    state.scene_vision_descriptions = scene_descs
            except Exception as e:
                state.log(f"   ⚠️ 场景 Vision 分析跳过: {e}")

        return {
            "character_images": char_assets,
            "scene_images": scene_assets,
            "total_cost": state.total_cost + cost,
        }

    async def node_video_gen(state: DramaState) -> dict:
        state.log("🎬 视频生成 Agent: 调用 API 生成视频...")
        if state.prompt_overrides and state.generation_prompts:
            for shot in state.generation_prompts.shots:
                if str(shot.scene_id) in state.prompt_overrides:
                    shot.prompt = state.prompt_overrides[str(shot.scene_id)]
                    state.log(f"   ✏️ 镜头 {shot.scene_id} 使用了用户修改的 prompt")

        assets = await video_gen_agent.run(
            state.generation_prompts,
            character_images=state.character_images,
            scene_images=state.scene_images,
            script=state.script,
            visual_design=state.visual_design,
            scene_vision_descriptions=state.scene_vision_descriptions,
            project_id=state.project_id,
            prompt_policy=state.prompt_policy,
            creative_profile=state.creative_profile,
        )
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
        native_scene_audio = {
            asset.scene_id: asset.model_dump()
            for asset in (state.generated_assets.shots if state.generated_assets else [])
            if getattr(asset, "native_audio_enabled", False)
        }
        audio = await audio_agent.run(
            state.script, state.generated_assets, state.project_id, state.audio_assets,
            native_scene_audio=native_scene_audio,
        )
        state.log(f"   配音场景: {len(audio.scenes)} | BGM: {audio.bgm_mood}")
        return {"audio_assets": audio}

    async def node_assembly(state: DramaState) -> dict:
        state.log("🎞️ 合成 Agent: 拼接视频 + 字幕...")
        path = await assembly_agent.run(
            state.script,
            state.generated_assets,
            state.audio_assets,
            state.project_id,
            state.subtitle_enabled,
            state.subtitle_mode,
        )
        state.log(f"   输出: {path}")
        return {"output_video_path": path}

    # ── Routing ───────────────────────────────────────────────

    def route_after_review(state: DramaState) -> Literal["visual_prompt", "audio", "__end__"]:
        if state.review_result is None:
            return "audio"
        if state.review_result.passed:
            return "audio"
        if state.review_loop_count < MAX_REVIEW_LOOPS:
            state.log(f"   ↩️ 返回视觉与提示 Agent 重试 (第 {state.review_loop_count} 次)")
            return "visual_prompt"
        state.log(f"   ⚠️ 已达最大重试次数 ({MAX_REVIEW_LOOPS})，使用现有素材继续")
        return "audio"

    # ── Build graph ───────────────────────────────────────────

    graph = StateGraph(DramaState)

    graph.add_node("script", node_script)
    graph.add_node("visual_prompt", node_visual_prompt)
    graph.add_node("image_gen", node_image_gen)
    graph.add_node("video_gen", node_video_gen)
    graph.add_node("review", node_review)
    graph.add_node("audio", node_audio)
    graph.add_node("assembly", node_assembly)

    graph.set_entry_point("script")
    graph.add_edge("script", "visual_prompt")
    graph.add_edge("visual_prompt", "image_gen")
    graph.add_edge("image_gen", "video_gen")
    graph.add_edge("video_gen", "review")

    graph.add_conditional_edges(
        "review",
        route_after_review,
        {"visual_prompt": "visual_prompt", "audio": "audio"},
    )

    graph.add_edge("audio", "assembly")
    graph.add_edge("assembly", END)

    memory = MemorySaver()
    return graph.compile(checkpointer=memory)


# ── Segmented graphs (for interactive UI) ────────────────────────


def create_stage1_graph(script_agent, visual_prompt_agent):
    """Stage 1: script → visual_prompt → END"""
    graph = StateGraph(DramaState)

    async def node_script(state: DramaState) -> dict:
        state.log("📝 编剧 Agent: 开始生成分镜脚本...")
        script = await script_agent.run(state.user_input, state.input_mode)
        state.log(f"   剧本标题: {script.title} | {len(script.scenes)} 个场景")
        return {"script": script}

    async def node_visual_prompt(state: DramaState) -> dict:
        state.log("🎨🔧 视觉与提示 Agent: 设计视觉风格 + 生成视频 prompt...")
        visual, prompts = await visual_prompt_agent.run(
            state.script,
            prompt_overrides=state.prompt_overrides,
            source_text=state.script_draft_text or (
                state.user_input if state.input_mode == "script" else ""
            ),
        )
        state.log(f"   风格: {visual.overall_style} | {len(prompts.shots)} 个镜头 prompt")
        return {"visual_design": visual, "generation_prompts": prompts}

    graph.add_node("script", node_script)
    graph.add_node("visual_prompt", node_visual_prompt)
    graph.set_entry_point("script")
    graph.add_edge("script", "visual_prompt")
    graph.add_edge("visual_prompt", END)
    return graph.compile(checkpointer=MemorySaver())


def create_stage2_graph(image_gen_agent):
    """Stage 2: image_gen → END (generates both characters and scenes)"""
    graph = StateGraph(DramaState)

    async def node_image_gen(state: DramaState) -> dict:
        state.log("🎭🖼️ 图像生成 Agent: 生成角色形象图 + 场景环境图...")
        char_assets, scene_assets = await image_gen_agent.run(
            state.script,
            state.visual_design,
            project_id=state.project_id,
            creative_profile=state.creative_profile,
        )
        state.log(f"   角色形象图: {len(char_assets.characters)} 个 | 场景图: {len(scene_assets.scenes)} 个")
        cost = char_assets.total_cost + scene_assets.total_cost
        return {
            "character_images": char_assets,
            "scene_images": scene_assets,
            "total_cost": state.total_cost + cost,
        }

    graph.add_node("image_gen", node_image_gen)
    graph.set_entry_point("image_gen")
    graph.add_edge("image_gen", END)
    return graph.compile(checkpointer=MemorySaver())


def create_stage3_graph():
    """Stage 3: pass-through no-op (prompts already generated in stage 1).

    Kept for backward compatibility with the 6-stage API. Simply logs and returns.
    """
    graph = StateGraph(DramaState)

    async def node_pass_through(state: DramaState) -> dict:
        if state.generation_prompts:
            state.log(f"🔧 提示词: {len(state.generation_prompts.shots)} 个镜头 prompt (已在 Stage 1 生成)")
        else:
            state.log("🔧 提示词: 尚未生成，请先执行 Stage 1")
        return {}

    graph.add_node("pass_through", node_pass_through)
    graph.set_entry_point("pass_through")
    graph.add_edge("pass_through", END)
    return graph.compile(checkpointer=MemorySaver())


def create_stage4_graph(video_gen_agent):
    """Stage 4: video generation → END"""
    graph = StateGraph(DramaState)

    async def node_video(state: DramaState) -> dict:
        state.log("🎬 视频生成 Agent: 调用 API 生成视频...")
        if state.prompt_overrides and state.generation_prompts:
            for shot in state.generation_prompts.shots:
                if str(shot.scene_id) in state.prompt_overrides:
                    shot.prompt = state.prompt_overrides[str(shot.scene_id)]
        assets = await video_gen_agent.run(
            state.generation_prompts,
            character_images=state.character_images,
            scene_images=state.scene_images,
            script=state.script,
            visual_design=state.visual_design,
            scene_vision_descriptions=state.scene_vision_descriptions,
            project_id=state.project_id,
            prompt_policy=state.prompt_policy,
            creative_profile=state.creative_profile,
        )
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
    """Stage 5: audio generation → END"""
    graph = StateGraph(DramaState)

    async def node_audio(state: DramaState) -> dict:
        state.log("🎵 音频 Agent: 生成配音和 BGM...")
        native_scene_audio = {
            asset.scene_id: asset.model_dump()
            for asset in (state.generated_assets.shots if state.generated_assets else [])
            if getattr(asset, "native_audio_enabled", False)
        }
        audio = await audio_agent.run(
            state.script, state.generated_assets, state.project_id, state.audio_assets,
            native_scene_audio=native_scene_audio,
        )
        state.log(f"   配音场景: {len(audio.scenes)} | BGM: {audio.bgm_mood}")
        return {"audio_assets": audio}

    graph.add_node("audio", node_audio)
    graph.set_entry_point("audio")
    graph.add_edge("audio", END)
    return graph.compile(checkpointer=MemorySaver())


def create_stage6_graph(assembly_agent):
    """Stage 6: assembly → END"""
    graph = StateGraph(DramaState)

    async def node_assembly(state: DramaState) -> dict:
        state.log("🎞️ 合成 Agent: 拼接视频 + 字幕...")
        path = await assembly_agent.run(
            state.script, state.generated_assets, state.audio_assets, state.project_id,
            state.subtitle_enabled, state.subtitle_mode,
        )
        state.log(f"   输出: {path}")
        return {"output_video_path": path}

    graph.add_node("assembly", node_assembly)
    graph.set_entry_point("assembly")
    graph.add_edge("assembly", END)
    return graph.compile(checkpointer=MemorySaver())


# ── Orchestrator ─────────────────────────────────────────────────


class DramaOrchestrator:
    """High-level orchestrator for the drama generation pipeline.

    Supports two modes:
    - Full pipeline via generate()
    - Segmented pipeline via 6 stage graphs for interactive UI

    Accepts either merged agents (visual_prompt_agent, image_gen_agent) or
    legacy agents (visual_agent, prompt_agent, character_agent, scene_image_agent).
    """

    def __init__(
        self,
        script_agent,
        video_gen_agent,
        review_agent,
        audio_agent,
        assembly_agent,
        # Merged agents (preferred)
        visual_prompt_agent=None,
        image_gen_agent=None,
        # Legacy agents (for backward compat)
        visual_agent=None,
        prompt_agent=None,
        character_agent=None,
        scene_image_agent=None,
    ):
        self.script_agent = script_agent
        self.video_gen_agent = video_gen_agent
        self.review_agent = review_agent
        self.audio_agent = audio_agent
        self.assembly_agent = assembly_agent

        # Use merged agents or wrap legacy ones
        self.visual_prompt_agent = visual_prompt_agent
        self.image_gen_agent = image_gen_agent

        if visual_prompt_agent is None and visual_agent is not None:
            # Legacy mode: wrap visual_agent + prompt_agent as VisualPromptAgent
            self.visual_prompt_agent = _LegacyVisualPromptWrapper(visual_agent, prompt_agent)
        if image_gen_agent is None and character_agent is not None:
            # Legacy mode: wrap character_agent + scene_image_agent as ImageGenerationAgent
            self.image_gen_agent = _LegacyImageGenWrapper(character_agent, scene_image_agent)

        # Full pipeline graph
        self.graph = build_drama_graph(
            script_agent, self.visual_prompt_agent, self.image_gen_agent,
            video_gen_agent, review_agent, audio_agent, assembly_agent,
        )

        # Segment graphs
        self.stage1 = create_stage1_graph(script_agent, self.visual_prompt_agent)
        self.stage2 = create_stage2_graph(self.image_gen_agent)
        self.stage3 = create_stage3_graph()
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
            state: Current DramaState.

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


# ── Legacy wrappers (backward compat) ─────────────────────────────


class _LegacyVisualPromptWrapper:
    """Wraps legacy VisualDesignAgent + PromptEngineeringAgent as unified interface."""

    def __init__(self, visual_agent, prompt_agent):
        self._visual = visual_agent
        self._prompt = prompt_agent

    async def run(self, script, character_images=None, scene_images=None, prompt_overrides=None):
        visual = await self._visual.run(script)
        prompts = await self._prompt.run(
            script, visual,
            character_images=character_images,
            scene_images=scene_images,
            prompt_overrides=prompt_overrides,
        )
        return visual, prompts


class _LegacyImageGenWrapper:
    """Wraps legacy CharacterAgent + SceneImageAgent as unified interface."""

    def __init__(self, character_agent, scene_image_agent):
        self._character = character_agent
        self._scene = scene_image_agent

    async def run(self, script, visual, project_id="", creative_profile=None):
        # CharacterAgent predates the stable project profile and keeps its
        # existing call contract. SceneImageAgent accepts the profile so the
        # legacy scene path cannot fall back to stale VisualDesign text.
        char_assets = await self._character.run(script, visual, project_id=project_id)
        scene_assets = await self._scene.run(
            script,
            visual,
            project_id=project_id,
            creative_profile=creative_profile,
        )
        return char_assets, scene_assets
