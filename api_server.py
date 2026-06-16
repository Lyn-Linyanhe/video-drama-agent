"""FastAPI 桥接服务 — 将 7 Agent 管线暴露为 REST API，供暗系前端调用。"""

import json
import uuid
import sys
from pathlib import Path
from typing import Optional

# ── Windows 编码补丁 ──
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

# 确保当前目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).parent))

from src.agents import (
    ScriptAgent, VisualPromptAgent, ImageGenerationAgent,
    VideoGenerationAgent, ReviewAgent, AudioAgent, AssemblyAgent,
)
from src.graph.orchestrator import DramaOrchestrator
from src.graph.state import DramaState
from src.api_clients.image_mock import MockImageClient
from src.config import OUTPUT_DIR, DEFAULT_API
from src.utils.project_store import ProjectStore

# ── 全局单例 ─────────────────────────────────────────────────
script_agent = ScriptAgent()
visual_prompt_agent = VisualPromptAgent()
image_gen_agent = ImageGenerationAgent(api_client=MockImageClient())
video_gen_agent = VideoGenerationAgent()
review_agent = ReviewAgent()
audio_agent = AudioAgent()
assembly_agent = AssemblyAgent()

orchestrator = DramaOrchestrator(
    script_agent=script_agent,
    visual_prompt_agent=visual_prompt_agent,
    image_gen_agent=image_gen_agent,
    video_gen_agent=video_gen_agent,
    review_agent=review_agent,
    audio_agent=audio_agent,
    assembly_agent=assembly_agent,
)

project_store = ProjectStore()

# ── FastAPI 应用 ─────────────────────────────────────────────
app = FastAPI(
    title="AI 短剧创作平台 API",
    description="7 Agent 协作短剧生成管线 REST API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:18888", "http://localhost:18888", "http://127.0.0.1:17900", "http://localhost:17900"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Content-Type"],
)


# ── 请求/响应模型 ───────────────────────────────────────────
class ScriptRequest(BaseModel):
    user_input: str = Field(..., description="用户输入的故事创意或文本")
    genre_tags: str = Field(default="都市", description="剧集类型")
    art_style: str = Field(default="写实", description="艺术风格")
    video_ratio: str = Field(default="9:16", description="视频比例")


class ScriptResponse(BaseModel):
    project_id: str
    script: dict | None = None
    visual_design: dict | None = None
    log: list[str] = []


class StageRequest(BaseModel):
    """通用状态传递模型。"""
    state_json: str = Field(default="", description="前一步返回的 state_json")


class StageResponse(BaseModel):
    state_json: str
    log: list[str] = []
    error: str = ""


class PromptEditRequest(BaseModel):
    state_json: str
    feedback: str = Field(default="", description="用户给 AI 的修改建议")
    shot_index: int = Field(default=0, description="要修改的镜头编号")


class ProjectMeta(BaseModel):
    id: str
    title: str
    status: str
    created_at: str
    updated_at: str
    user_input: str = ""


# ── 工具函数 ─────────────────────────────────────────────────
def _state_to_json(state: DramaState) -> str:
    """将 DramaState 序列化为 JSON 字符串。"""
    data = {
        "user_input": state.user_input,
        "project_id": state.project_id,
        "total_cost": state.total_cost,
        "review_loop_count": state.review_loop_count,
        "error_message": state.error_message,
        "execution_log": state.execution_log,
        "output_video_path": state.output_video_path,
        "prompt_overrides": state.prompt_overrides,
        "user_feedback": state.user_feedback,
    }
    # Pydantic 模型序列化
    for attr in ["script", "visual_design", "character_images",
                  "scene_images", "generation_prompts",
                  "generated_assets", "audio_assets"]:
        val = getattr(state, attr, None)
        if val is not None:
            data[attr] = val.model_dump()
    return json.dumps(data, ensure_ascii=False)


def _state_from_json(json_str: str) -> DramaState:
    """从 JSON 字符串重建 DramaState。"""
    if not json_str:
        return DramaState()
    data = json.loads(json_str)

    state = DramaState(
        user_input=data.get("user_input", ""),
        project_id=data.get("project_id", ""),
        total_cost=data.get("total_cost", 0.0),
        review_loop_count=data.get("review_loop_count", 0),
        error_message=data.get("error_message", ""),
        execution_log=data.get("execution_log", []),
        output_video_path=data.get("output_video_path", ""),
        prompt_overrides=data.get("prompt_overrides", {}),
        user_feedback=data.get("user_feedback", ""),
    )

    # 重建 Pydantic 子模型
    from src.schemas.script import ScriptBreakdown
    from src.schemas.visual import VisualDesign
    from src.schemas.character import CharacterImageAssets
    from src.schemas.scene_image import SceneImageAssets
    from src.schemas.prompt import GenerationPrompts
    from src.schemas.assets import GeneratedAssets
    from src.schemas.audio import AudioAssets

    for attr, schema in [
        ("script", ScriptBreakdown),
        ("visual_design", VisualDesign),
        ("character_images", CharacterImageAssets),
        ("scene_images", SceneImageAssets),
        ("generation_prompts", GenerationPrompts),
        ("generated_assets", GeneratedAssets),
        ("audio_assets", AudioAssets),
    ]:
        if data.get(attr):
            try:
                setattr(state, attr, schema.model_validate(data[attr]))
            except Exception:
                pass
    return state


# ── API 端点 ─────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    """健康检查。"""
    return {"status": "ok", "provider": DEFAULT_API}


@app.post("/api/script", response_model=ScriptResponse)
async def generate_script(req: ScriptRequest):
    """第 1 步：生成剧本 + 视觉设计。"""
    project_id = uuid.uuid4().hex[:12]
    project_store.create_project(
        title=req.user_input[:30] + ("…" if len(req.user_input) > 30 else ""),
        user_input=req.user_input,
    )
    project_store.update_status(project_id, "生成中")

    state = DramaState(user_input=req.user_input, project_id=project_id)
    try:
        result = await orchestrator.run_stage(1, state)
    except Exception as e:
        return ScriptResponse(project_id=project_id, log=["脚本生成失败，请检查 API 配置"])

    project_store.add_stage(project_id, "stage1")
    return ScriptResponse(
        project_id=project_id,
        script=result.script.model_dump() if result.script else None,
        visual_design=result.visual_design.model_dump() if result.visual_design else None,
        log=result.execution_log,
    )


@app.post("/api/characters")
async def generate_characters(req: StageRequest):
    """第 2a 步：分析角色并生成形象图。"""
    state = _state_from_json(req.state_json)
    if not state.project_id:
        state.project_id = uuid.uuid4().hex[:12]
    try:
        result = await orchestrator.run_stage(2, state)
    except Exception as e:
        return StageResponse(state_json=req.state_json, log=[], error="操作失败，请重试")

    project_store.add_stage(result.project_id, "stage2")
    return StageResponse(
        state_json=_state_to_json(result),
        log=result.execution_log,
    )


@app.post("/api/scenes")
async def generate_scenes(req: StageRequest):
    """第 2b 步：生成场景环境图（需先执行 characters）。"""
    state = _state_from_json(req.state_json)
    if not state.character_images:
        # 如果没有角色图，先执行 stage 2
        state = await orchestrator.run_stage(2, state)
    # 已有角色图+场景图，stage 2 已经完成了场景图
    return StageResponse(
        state_json=_state_to_json(state),
        log=state.execution_log,
    )


@app.post("/api/prompts")
async def generate_prompts(req: StageRequest):
    """第 3 步：生成视频提示词。"""
    state = _state_from_json(req.state_json)
    try:
        result = await orchestrator.run_stage(3, state)
    except Exception as e:
        return StageResponse(state_json=req.state_json, log=[], error="操作失败，请重试")

    project_store.add_stage(result.project_id, "stage3")
    return StageResponse(
        state_json=_state_to_json(result),
        log=result.execution_log,
    )


@app.post("/api/revise-prompt")
async def revise_prompt(req: PromptEditRequest):
    """AI 根据用户反馈修订指定镜头的提示词。"""
    state = _state_from_json(req.state_json)
    if not state.generation_prompts or req.shot_index >= len(state.generation_prompts.shots):
        raise HTTPException(400, "无效的镜头编号")

    original = state.generation_prompts.shots[req.shot_index].prompt
    revision_prompt = f"""用户对以下视频提示词有修改意见：

**原始提示词**：
{original}

**用户反馈**：
{req.feedback}

请根据用户反馈修改提示词，输出修改后的完整英文提示词（只输出 prompt 文本，不要其他解释）。"""

    try:
        new_prompt = await script_agent.llm.generate_text(
            system_prompt="你是一个专业的 AI 视频生成提示词工程师，擅长根据用户反馈优化视频描述。只输出修改后的提示词。",
            user_prompt=revision_prompt,
            temperature=0.5,
        )
        new_prompt = new_prompt.strip().strip('"').strip("'")
        # 更新到 state
        state.generation_prompts.shots[req.shot_index].prompt = new_prompt
        state.prompt_overrides[str(req.shot_index)] = new_prompt
    except Exception:
        raise HTTPException(500, "AI 修订失败，请重试")

    return {
        "new_prompt": new_prompt,
        "shot_index": req.shot_index,
        "state_json": _state_to_json(state),
    }


@app.post("/api/videos")
async def generate_videos(req: StageRequest):
    """第 4 步：生成视频片段。"""
    state = _state_from_json(req.state_json)
    try:
        result = await orchestrator.run_stage(4, state)
    except Exception as e:
        return StageResponse(state_json=req.state_json, log=[], error="操作失败，请重试")

    project_store.add_stage(result.project_id, "stage4")
    return StageResponse(
        state_json=_state_to_json(result),
        log=result.execution_log,
    )


@app.post("/api/audio")
async def generate_audio(req: StageRequest):
    """第 5 步：生成配音和 BGM。"""
    state = _state_from_json(req.state_json)
    try:
        result = await orchestrator.run_stage(5, state)
    except Exception as e:
        return StageResponse(state_json=req.state_json, log=[], error="操作失败，请重试")

    project_store.add_stage(result.project_id, "stage5")
    return StageResponse(
        state_json=_state_to_json(result),
        log=result.execution_log,
    )


@app.post("/api/assemble")
async def assemble_video(req: StageRequest):
    """第 6 步：合成最终视频。"""
    state = _state_from_json(req.state_json)
    try:
        result = await orchestrator.run_stage(6, state)
    except Exception as e:
        return StageResponse(state_json=req.state_json, log=[], error="操作失败，请重试")

    project_store.set_final_video(result.project_id, result.output_video_path)
    project_store.update_status(result.project_id, "已完成")

    return StageResponse(
        state_json=_state_to_json(result),
        log=result.execution_log,
    )


@app.get("/api/projects")
async def list_projects():
    """获取项目列表。"""
    projects = project_store.list_projects()
    return [ProjectMeta(**p) for p in projects]


@app.get("/api/projects/{project_id}/assets")
async def get_project_assets(project_id: str):
    """获取指定项目的所有资产文件（多图分组格式）。"""
    try:
        return project_store.get_asset_groups(project_id)
    except ValueError:
        return {"error": "无效的 project_id"}


# ── API 配置档案 ──────────────────────────────────────────────
SETTINGS_FILE = Path(__file__).parent / "output" / "settings_ui.json"

def _load_settings() -> dict:
    if SETTINGS_FILE.exists():
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"profiles": {}, "active_profile": ""}

def _save_settings(data: dict):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

class SettingsRequest(BaseModel):
    profile_name: str = "默认配置"
    # ── 全局 ──
    http_proxy: str = ""  # HTTP 代理地址
    # ── LLM ──
    llm_provider: str = "anthropic"
    llm_model: str = ""
    llm_key: str = ""
    llm_base_url: str = ""
    llm_timeout: str = "60"
    llm_stream: str = "true"
    llm_temperature: str = "0.7"
    llm_top_p: str = "0.9"
    # ── TTS ──
    tts_provider: str = "edge"  # edge / openai / azure
    tts_key: str = ""
    tts_base_url: str = ""
    tts_model: str = ""
    tts_voice: str = "zh-CN-XiaoxiaoNeural"
    tts_speed: str = "1.0"
    tts_timeout: str = "30"
    # ── BGM ──
    bgm_provider: str = "mock"  # mock / suno
    bgm_key: str = ""
    bgm_base_url: str = ""
    bgm_duration: str = "60"
    bgm_timeout: str = "120"
    # ── 视频 ──
    default_video_api: str = "mock"
    video_base_url: str = ""
    kling_access_key: str = ""
    kling_secret_key: str = ""
    kling_model: str = "kling-v2-6"
    kling_mode: str = "std"
    video_fps: str = "24"
    video_max_duration: str = "10"
    video_motion_opt: str = "false"
    video_timeout: str = "300"
    # ── 图片 ──
    image_gen_api: str = "mock"
    image_gen_key: str = ""
    image_gen_base_url: str = ""
    image_gen_model: str = ""
    image_prefix_prompt: str = ""
    image_negative_prompt: str = "low quality, blurry, ugly, deformed"
    image_char_size: str = "1024x1024"
    image_scene_size: str = "1080x1920"
    image_timeout: str = "60"

@app.get("/api/settings")
async def list_settings():
    return _load_settings()

@app.get("/api/settings/{profile_name}")
async def get_setting(profile_name: str):
    data = _load_settings()
    profile = data.get("profiles", {}).get(profile_name)
    if not profile:
        raise HTTPException(404, f"档案 '{profile_name}' 不存在")
    return profile

@app.post("/api/settings")
async def save_settings(req: SettingsRequest):
    data = _load_settings()
    data["profiles"][req.profile_name] = req.model_dump()
    data["active_profile"] = req.profile_name
    _save_settings(data)
    # 同步写入 .env
    _write_env(
        llm_provider=req.llm_provider, llm_model=req.llm_model,
        llm_key=req.llm_key, llm_base=req.llm_base_url,
        default_video_api=req.default_video_api,
        kling_ak=req.kling_access_key, kling_sk=req.kling_secret_key,
        img_api=req.image_gen_api, img_key=req.image_gen_key,
        http_proxy=req.http_proxy, tts_provider=req.tts_provider,
    )
    return {"message": f"配置档案 '{req.profile_name}' 已保存，重启服务生效", "profiles": list(data["profiles"].keys())}

@app.delete("/api/settings/{profile_name}")
async def delete_setting(profile_name: str):
    data = _load_settings()
    if profile_name in data.get("profiles", {}):
        del data["profiles"][profile_name]
        if data.get("active_profile") == profile_name:
            data["active_profile"] = ""
        _save_settings(data)
    return {"message": f"已删除 '{profile_name}'"}

@app.get("/api/settings/{profile_name}/export")
async def export_profile(profile_name: str):
    """导出单个档案为 JSON"""
    data = _load_settings()
    profile = data.get("profiles", {}).get(profile_name)
    if not profile:
        raise HTTPException(404, "档案不存在")
    from fastapi.responses import Response
    return Response(
        content=json.dumps(profile, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={profile_name}.json"}
    )

@app.post("/api/settings/import")
async def import_profile(data: dict):
    """导入外部档案 JSON"""
    name = data.get("profile_name", "导入的配置")
    profiles = _load_settings()
    profiles["profiles"][name] = data
    _save_settings(profiles)
    return {"message": f"已导入 '{name}'", "profiles": list(profiles["profiles"].keys())}

# ── 测试连接端点 ──────────────────────────────────────────────
class TestConnectionRequest(BaseModel):
    provider: str = "anthropic"
    key: str = ""
    base_url: str = ""

@app.post("/api/test/llm")
async def test_llm(req: TestConnectionRequest):
    """测试 LLM 连接"""
    import httpx
    if req.provider == "anthropic":
        url = "https://api.anthropic.com/v1/messages"
        headers = {"x-api-key": req.key, "anthropic-version": "2023-06-01"}
        body = {"model": "claude-haiku-4-5-20251001", "max_tokens": 10, "messages": [{"role":"user","content":"hi"}]}
    else:
        url = f"{req.base_url or 'https://api.openai.com/v1'}/chat/completions"
        headers = {"Authorization": f"Bearer {req.key}"}
        body = {"model": "gpt-4o-mini", "max_tokens": 10, "messages": [{"role":"user","content":"hi"}]}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, json=body, headers=headers)
            return {"success": r.status_code < 500, "status": r.status_code, "detail": "连接正常" if r.status_code < 500 else str(r.text)[:200]}
    except Exception as e:
        return {"success": False, "status": 0, "detail": str(e)[:200]}

@app.post("/api/test/tts")
async def test_tts(req: TestConnectionRequest):
    """测试 TTS 连接（Edge-TTS 离线可用）"""
    if not req.key and not req.base_url:
        return {"success": True, "status": 200, "detail": "Edge-TTS 离线模式可用"}
    try:
        import edge_tts
        return {"success": True, "status": 200, "detail": "Edge-TTS 已安装可用"}
    except ImportError:
        return {"success": False, "status": 500, "detail": "edge-tts 未安装"}

@app.post("/api/test/image")
async def test_image(req: TestConnectionRequest):
    """测试图片 API"""
    if not req.key:
        return {"success": False, "status": 0, "detail": "请先配置 API Key"}
    import httpx
    url = req.base_url or "https://api.openai.com/v1/images/generations"
    headers = {"Authorization": f"Bearer {req.key}"}
    body = {"model": "dall-e-3", "prompt": "test", "n": 1, "size": "1024x1024", "response_format": "url"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(url, json=body, headers=headers)
            return {"success": r.status_code < 500, "status": r.status_code, "detail": "连接正常" if r.status_code < 500 else str(r.text)[:200]}
    except Exception as e:
        return {"success": False, "status": 0, "detail": str(e)[:200]}

@app.post("/api/test/video")
async def test_video(req: TestConnectionRequest):
    """测试视频 API（可灵 JWT）"""
    if not req.key:
        return {"success": False, "status": 0, "detail": "请先配置可灵 AK/SK"}
    import httpx, jwt as pyjwt, time
    try:
        token = pyjwt.encode(
            {"iss": req.key, "exp": int(time.time()) + 1800, "nbf": int(time.time()) - 5},
            req.base_url or "secret",  # base_url field reused for secret key
            algorithm="HS256"
        )
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get("https://api.klingai.com/v1/videos/text2video", headers={"Authorization": f"Bearer {token}"})
            return {"success": r.status_code < 500, "status": r.status_code, "detail": "认证正常" if r.status_code != 401 else "AK/SK 无效"}
    except Exception as e:
        return {"success": False, "status": 0, "detail": str(e)[:200]}

def _write_env(llm_provider="", llm_model="", llm_key="", llm_base="",
               default_video_api="mock", kling_ak="", kling_sk="",
               img_api="mock", img_key="", http_proxy="", tts_provider="edge"):
    """写入 .env 文件。"""
    env_path = Path(__file__).parent / ".env"
    lines = []
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").split("\n")
    to_set = {
        "HTTP_PROXY": http_proxy,
        "LLM_PROVIDER": llm_provider,
        "LLM_MODEL": llm_model,
        "ANTHROPIC_API_KEY": llm_key if llm_provider == "anthropic" else "",
        "OPENAI_API_KEY": llm_key if llm_provider == "openai" else "",
        "OPENAI_BASE_URL": llm_base,
        "DEFAULT_VIDEO_API": default_video_api,
        "KLING_ACCESS_KEY": kling_ak,
        "KLING_SECRET_KEY": kling_sk,
        "IMAGE_GEN_API": img_api,
        "IMAGE_GEN_KEY": img_key,
        "TTS_PROVIDER": tts_provider,
    }
    new_lines, seen = [], set()
    for line in lines:
        key = line.split("=")[0].strip() if "=" in line and not line.startswith("#") else ""
        if key in to_set:
            new_lines.append(f"{key}={to_set[key]}")
            seen.add(key)
        else:
            new_lines.append(line)
    for key, val in to_set.items():
        if key not in seen and val:
            new_lines.append(f"{key}={val}")
    env_path.write_text("\n".join(new_lines), encoding="utf-8")


# ── 静态文件服务（前端 studio.html） ─────────────────────────
if Path("studio.html").exists():
    @app.get("/")
    @app.get("/studio")
    async def serve_studio():
        return FileResponse("studio.html")


# ── 启动入口 ─────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    import socket
    PORT = 18888
    for _offset in range(10):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(('127.0.0.1', PORT + _offset))
            s.close()
            PORT = PORT + _offset
            break
        except OSError:
            s.close()
    print(f"[AI Short Drama API] http://127.0.0.1:{PORT}")
    print(f"[Frontend] http://127.0.0.1:{PORT}")
    print(f"[API Docs] http://127.0.0.1:{PORT}/docs")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
