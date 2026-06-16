# 多智能体协作视频短剧生成系统

> 输入一段故事创意 → 7 个 AI 智能体协作 → 输出完整 60-90 秒竖屏短剧

---

## 架构总览

```
用户输入创意
    │
    ▼
┌──────────────────────────────────────────────────────────────┐
│                  Orchestrator (LangGraph)                     │
│     线性流水线 + 审核条件回环 (最多 2 次重试)                   │
└──────────────────────────────────────────────────────────────┘
    │
    ▼
编剧 → 视觉+提示 → 多视角图像生成 → 视频生成 → 审核 (质量门)
                                                    │
                                        ✅ 通过 → 音频 → 合成 → 输出视频
                                        ❌ 不通过 → 回到视觉+提示 (重试)
```

### 7 个专业智能体（9→7 合并架构）

| # | 智能体 | 文件 | 职责 |
|---|--------|------|------|
| 1 | 📜 **编剧** | `src/agents/script_agent.py` | 故事拆分为结构化分镜脚本 (scene + dialogue + camera) |
| 2 | 🎨🔧 **视觉与提示** | `src/agents/visual_prompt_agent.py` | **合并体**：一次 LLM 调用完成视觉风格设计 + 英文 API 提示词生成 |
| 3 | 🎭🖼️ **图像生成** | `src/agents/image_gen_agent.py` | **合并体**：生成多视角角色形象图 (每人3张) + 场景环境图 (每场2张) |
| 4 | 🎬 **视频生成** | `src/agents/video_gen_agent.py` | 并发调用视频 API，自动重试 + 指数退避，支持图生视频 |
| 5 | 🔍 **审核** | `src/agents/review_agent.py` | 4 维度质量评分 (阈值 0.6)，自动检查 + LLM 审核 |
| 6 | 🔊 **音频** | `src/agents/audio_agent.py` | Edge-TTS 多角色配音 + BGM 匹配 |
| 7 | 🎞️ **合成** | `src/agents/assembly_agent.py` | MoviePy 拼接 + 字幕烧录 + 转场 + 混音 |

> **合并说明**：原 9 Agent 架构中的 VisualDesignAgent + PromptEngineeringAgent 合并为 VisualPromptAgent（消除 1 次串行 LLM 调用），CharacterAgent + SceneImageAgent 合并为 ImageGenerationAgent（消除冗余角色分析 LLM 调用，角色描述直接复用 VisualDesign 输出）。

---

## 角色一致性方案

短剧生成的核心挑战：同一角色在多个镜头中保持一致的外貌。系统从 4 个层级解决此问题：

### 层级 1：文本层 — Prompt 描述一致性

`VisualPromptAgent` 一次 LLM 调用输出 `global_character_descriptions`（英文，包含 12+ 维度的外貌特征字典），所有场景的 prompt 中引用完全相同的描述文本。

```
global_character_descriptions = {
  "林屿": "Male, 17yo, 178cm slim, oval face, fair skin, deep black eyes,
           high nose bridge, thin lips, short black hair with side part,
           sword eyebrows, white shirt + dark blue school blazer..."
}
```

### 层级 2：种子层 — Seed 确定性

同一角色在所有镜头中使用相同的随机种子：

```python
seed = sum(ord(c) for c in character_name) % 999999 + 1
```

同 prompt + 同 seed → 视频 API 倾向产生相似视觉结果。

### 层级 3：参考图层 — 多视角角色形象图

`ImageGenerationAgent` 为每个角色生成 **3 张参考图**：

| 视角 | view_type | 尺寸 | 用途 |
|------|-----------|------|------|
| 正面肖像 | `portrait` | 1024² | 面部特征锁定 |
| 3/4 侧面 | `profile` | 1024² | 面部轮廓深度 |
| 全身照 | `full_body` | 1080×1920 | 体型服装参考 |

每个场景生成 **2 张环境图**：

| 景别 | view_type | 尺寸 | 用途 |
|------|-----------|------|------|
| 全景 | `wide` | 1080×1920 | 环境建立 |
| 中景 | `medium` | 1080×1920 | 关键区域细节 |

视频生成时，`VideoGenerationAgent._build_reference_map()` 按优先级自动选图（portrait > profile > full_body），传入视频 API 的 `image_reference` 参数。

### 层级 4：模型层 — 图生视频 API（接口已就绪）

`VideoAPIClient` 接口已扩展 `image_reference` 参数。当前实现：
- **MockVideoClient**：Mock 视频标注 `[img2vid:角色名]`
- **KlingVideoClient**：自动路由文本生视频 / 图生视频，含图片上传

后续接入新 API 只需实现 `VideoAPIClient` 接口即可。

---

## 项目结构

```
video-drama-agent-master/
│
├── 启动.bat                      # Windows 一键启动脚本
├── app.py                        # Gradio 调试界面 (6步分步)
├── api_server.py                 # FastAPI + 暗系前端服务 (14+ 端点)
├── studio.html                   # 暗系火宝风格前端 (单文件 SPA)
├── requirements.txt              # Python 依赖
├── .env.example                  # 环境变量模板
│
├── src/
│   ├── agents/                   # 7 个智能体
│   │   ├── script_agent.py           # 1. 编剧：故事→分镜脚本
│   │   ├── visual_prompt_agent.py    # 2. 视觉与提示(合并)：视觉设计+英文Prompt
│   │   ├── image_gen_agent.py        # 3. 图像生成(合并)：多视角角色+场景图
│   │   ├── video_gen_agent.py        # 4. 视频生成：并发+重试+成本核算
│   │   ├── review_agent.py           # 5. 审核：4维评分+自动检查
│   │   ├── audio_agent.py            # 6. 音频：Edge-TTS+BGM
│   │   ├── assembly_agent.py         # 7. 合成：MoviePy拼接+字幕
│   │   │
│   │   ├── visual_agent.py           # (旧) 视觉设计 — 保留兼容
│   │   ├── prompt_agent.py           # (旧) 提示工程 — 保留兼容
│   │   ├── character_agent.py        # (旧) 角色形象 — 保留兼容
│   │   └── scene_image_agent.py      # (旧) 场景环境 — 保留兼容
│   │
│   ├── graph/                    # LangGraph 编排
│   │   ├── state.py                  # DramaState 共享状态
│   │   └── orchestrator.py           # 7节点流水线 + 6段分步Graph
│   │
│   ├── schemas/                  # Pydantic v2 数据模型
│   │   ├── script.py                 # 剧本/场景/台词
│   │   ├── visual.py                 # 视觉设计(含中英文角色描述)
│   │   ├── character.py              # 角色形象图 (含 view_type)
│   │   ├── scene_image.py            # 场景环境图 (含 view_type)
│   │   ├── prompt.py                 # 视频提示词
│   │   ├── assets.py                 # 生成视频资源
│   │   ├── review.py                 # 审核结果
│   │   └── audio.py                  # 音频资源
│   │
│   ├── api_clients/              # API 客户端
│   │   ├── base.py                   # 视频API抽象接口 (含 image_reference)
│   │   ├── kling.py                  # 可灵 Kling (文本+图生视频)
│   │   ├── mock.py                   # Mock 视频客户端
│   │   ├── image_base.py             # 图片API抽象接口
│   │   └── image_mock.py             # Mock 图片客户端
│   │
│   ├── prompts/                  # AI 提示词模板
│   │   └── templates.py              # 7 套标准化提示词
│   │
│   ├── utils/                    # 工具层
│   │   ├── llm.py                    # LLM 统一客户端 (Anthropic/OpenAI)
│   │   ├── tts.py                    # Edge-TTS 配音
│   │   ├── video.py                  # FFmpeg 工具
│   │   └── project_store.py          # 项目资产管理 (多图分组)
│   │
│   └── config.py                 # 全局配置
│
├── tests/                        # 测试
│   ├── test_orchestrator.py          # 集成测试
│   └── test_schemas.py               # Schema 单元测试
│
└── output/                       # 生成输出
    ├── projects.json                 # 项目索引
    ├── <project_id>/                 # 按项目ID分目录
    │   ├── meta.json                 # 项目元数据
    │   ├── characters/               # 角色形象图 (分组)
    │   │   └── 林屿/
    │   │       ├── portrait.png      #   正面肖像
    │   │       ├── profile.png       #   3/4侧面
    │   │       └── full_body.png     #   全身照
    │   ├── scenes/                   # 场景环境图 (分组)
    │   │   └── scene_01/
    │   │       ├── wide.png          #   全景
    │   │       └── medium.png        #   中景
    │   ├── videos/                   # 视频片段
    │   ├── audio/                    # 配音文件
    │   └── final/                    # 合成最终视频
    └── settings_ui.json              # API 配置档案
```

---

## 数据流追踪

以 "一个少年在雨夜奔跑" 为例，完整数据流：

```
Stage 1: ScriptAgent
  输入: "一个少年在雨夜奔跑，突然撞到一个陌生人..."
  输出: ScriptBreakdown { title, genre, scenes[5] { location, characters, dialogue, ... } }

Stage 1: VisualPromptAgent (同一次 LLM 调用)
  输入: ScriptBreakdown
  输出: VisualDesign {
          overall_style: "都市悬疑",
          global_character_descriptions: { "少年": "Male, 17yo, ..." },
          global_character_descriptions_cn: { "少年": "男性，17岁..." }
        }
        GenerationPrompts {
          shots[5] { prompt: "A young man... running in rain --seed 4251", ... }
        }

Stage 2: ImageGenerationAgent
  输入: ScriptBreakdown + VisualDesign
  输出: CharacterImageAssets { characters[3] { view_type: portrait/profile/full_body } }
        SceneImageAssets { scenes[10] { view_type: wide/medium } }
  磁盘: output/<id>/characters/少年/{portrait,profile,full_body}.png
        output/<id>/scenes/scene_01/{wide,medium}.png

Stage 3: 透传 (prompts 已在 Stage 1 生成)

Stage 4: VideoGenerationAgent
  输入: GenerationPrompts + CharacterImageAssets + SceneImageAssets
  选图: _build_reference_map() → scene_01: "output/.../scene_01/wide.png"
  API:  KlingVideoClient.generate_video(prompt="...", image_reference="wide.png")
  输出: GeneratedAssets { shots[5] { video_url, local_path, duration_sec } }

Stage 5: ReviewAgent
  输入: ScriptBreakdown + VisualDesign + GeneratedAssets
  自动检查: 缺失场景(critical) + 时长偏差(minor) + 成功率
  LLM审核: 内容一致性 + 视觉质量 + 角色一致性 + 时长
  输出: ReviewResult { overall_score, passed, issues[] }
  路由: passed → audio | failed + retries_left → visual_prompt | failed + max → audio

Stage 6: AudioAgent
  输入: ScriptBreakdown + GeneratedAssets
  TTS:  edge_tts.Communicate(text, "zh-CN-YunxiNeural") → scene01_少年.mp3
  BGM:  MOOD_BGM_MAP → mood="tense_dark" → bgm_tense_dark.mp3
  输出: AudioAssets { scenes[], bgm_path, bgm_mood }

Stage 7: AssemblyAgent
  输入: ScriptBreakdown + GeneratedAssets + AudioAssets
  MoviePy: concatenate_videoclips + 字幕烧录 + crossfade + BGM混音
  输出: output/<id>/final/drama_<uuid>.mp4
```

---

## 双前端体系

| 界面 | 入口 | 端口 | 风格 | 适用场景 |
|------|------|------|------|----------|
| 🌙 **暗系前端** | `启动.bat` | `18888` | 火宝短剧风格，8 页面完整创作流程 | 创作者端 |
| 🧪 **Gradio 控制台** | `python app.py` | `17860` | 调试工具风格，6 步分步 + 一键运行 | 开发者调试 |

### 暗系前端页面

| 页面 | 功能 | 对应 API |
|------|------|----------|
| 🏠 工作台首页 | 项目列表、新建项目 | `GET /api/projects` |
| 📝 剧本编辑 | 输入创意 / AI 生成剧本 / 字数统计 | `POST /api/script` |
| 👤 角色设置 | 角色卡片展示、多视角形象图预览 | `POST /api/characters` |
| 🏙️ 场景设置 | 场景卡片展示、多景别背景预览 | `POST /api/scenes` |
| 🎬 分镜列表 | 分镜卡片、绑定角色场景 | `POST /api/prompts` |
| ⚙️ 制作预览 | 参数配置、提示词编辑、批量生成 | `POST /api/videos` |
| 🎞️ 作品管理 | 已合成短剧展示、下载 | `POST /api/assemble` |

---

## API 端点

启动 `api_server.py` 后可用，完整 Swagger 文档：`http://127.0.0.1:18888/docs`

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/health` | 健康检查 |
| `POST` | `/api/script` | Stage 1: 生成剧本 + 视觉设计 + 提示词 |
| `POST` | `/api/characters` | Stage 2: 生成多视角角色形象图 |
| `POST` | `/api/scenes` | Stage 2: 场景环境图（同阶段复用） |
| `POST` | `/api/prompts` | Stage 3: 透传（提示词已在 Stage 1 生成） |
| `POST` | `/api/revise-prompt` | AI 修订提示词 |
| `POST` | `/api/videos` | Stage 4: 生成视频片段（含图参考） |
| `POST` | `/api/audio` | Stage 5: 生成配音 |
| `POST` | `/api/assemble` | Stage 6: 合成最终视频 |
| `GET` | `/api/projects` | 项目列表 |
| `GET` | `/api/projects/{id}/assets` | 项目资产（多图分组格式） |
| `GET/POST` | `/api/settings` | API 配置档案管理 |
| `DELETE` | `/api/settings/{name}` | 删除配置档案 |
| `POST` | `/api/test/llm` | 测试 LLM 连接 |
| `POST` | `/api/test/video` | 测试视频 API 连接 |
| `POST` | `/api/test/image` | 测试图片 API 连接 |
| `POST` | `/api/test/tts` | 测试 TTS 连接 |

---

## 配置说明

| 环境变量 | 说明 | 默认值 |
|----------|------|--------|
| `LLM_PROVIDER` | LLM 服务商 (`anthropic` / `openai`) | `anthropic` |
| `LLM_MODEL` | 模型名称 | `claude-sonnet-4-6` |
| `ANTHROPIC_API_KEY` | Anthropic API Key | — |
| `OPENAI_API_KEY` | OpenAI API Key | — |
| `OPENAI_BASE_URL` | 自定义 OpenAI 端点 | — |
| `DEFAULT_VIDEO_API` | 视频 API (`mock` / `kling`) | `mock` |
| `KLING_ACCESS_KEY` | 可灵 Access Key | — |
| `KLING_SECRET_KEY` | 可灵 Secret Key | — |
| `IMAGE_GEN_API` | 图片 API (`mock` / `openai`) | `mock` |
| `MAX_CONCURRENT_GENERATIONS` | 最大并发数 | `5` |
| `MAX_REVIEW_LOOPS` | 审核最大重试次数 | `2` |

**Mock 模式**：无需任何 API Key，生成彩色占位视频/图片，适合开发测试。

---

## 快速开始

### 一键启动（推荐）

```bash
双击项目根目录的 启动.bat
```

- 自动启动 API 服务器 + 暗系前端
- 自动打开浏览器 `http://127.0.0.1:18888`
- Mock 模式免 API Key 即可体验

### 手动启动

```bash
# 1. 激活虚拟环境
.venv\Scripts\activate

# 2. 安装依赖（首次）
pip install -r requirements.txt

# 3a. 启动暗系前端 + API (推荐)
python api_server.py

# 3b. 启动 Gradio 调试界面
python app.py
```

### 配置 API Key

```bash
cp .env.example .env
# 编辑 .env 填入 API Key
# 或在暗系前端的「设置」页面直接配置并保存
```

---

## 运行测试

```bash
pytest tests/ -v
```

---

## 技术栈

| 层 | 技术 |
|----|------|
| 智能体编排 | LangGraph + LangChain (StateGraph + 条件路由) |
| 数据模型 | Pydantic v2 (严格 schema 约束) |
| LLM | Anthropic Claude / OpenAI 兼容 (统一抽象层) |
| 视频生成 | 可灵 Kling (文本+图生视频) / Mock |
| 图片生成 | Mock / OpenAI DALL-E |
| 视频处理 | MoviePy + FFmpeg (imageio_ffmpeg) |
| 语音合成 | Edge-TTS (微软免费，多角色音色映射) |
| 前端 | Tailwind CSS + 原生 JS (SPA) / Gradio |
| API 服务 | FastAPI + Uvicorn |
| 持久化 | JSON 文件 (项目资产分组索引) |
