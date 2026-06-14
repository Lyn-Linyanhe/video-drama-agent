# 🎬 多智能体协作视频短剧生成系统

> 输入一段故事 → 9 个 AI 智能体协作 → 输出完整 60-90 秒竖屏短剧

---

## 🏗️ 架构

```
用户输入创意
    │
    ▼
┌────────────────────────────────────────────────────────┐
│               Orchestrator (LangGraph)                  │
│       线性流水线 + 审核条件回环 (最多 2 次重试)          │
└────────────────────────────────────────────────────────┘
    │
    ▼
编剧 → 视觉设计 → 角色形象图 → 场景图 → 视频提示词
                                            │
                                ┌───────────┘
                                ▼
                          视频生成 → 审核 (质量门)
                                          │
                               ✅ 通过 → 音频 → 合成 → 输出视频
                               ❌ 不通过 → 回到提示词 (重试)
```

### 9 个专业智能体

| # | 智能体 | 职责 | 输入 → 输出 |
|---|--------|------|-------------|
| 1 | 📜 **编剧** | 故事拆分为分镜脚本 | 文本 → `ScriptBreakdown` |
| 2 | 🎨 **视觉设计** | 风格 / 构图 / 色调 / 角色描述 | 剧本 → `VisualDesign` |
| 3 | 🎭 **角色形象** | 角色分析 + 生成形象图 | 剧本+视觉 → 角色 PNG |
| 4 | 🖼️ **场景环境** | 生成每个场景的背景图 | 剧本+视觉 → 场景 PNG |
| 5 | 💬 **提示工程** | 中文设计 → API 英文提示词 | 剧本+视觉+图片 → 提示词 |
| 6 | 🎬 **视频生成** | 调用视频 API 并发生成 | 提示词 → 视频片段 |
| 7 | 🔍 **审核** | 4 维度质量评分 (阈值 0.6) | 全资产 → 通过/重试 |
| 8 | 🔊 **音频** | Edge-TTS 配音 + BGM | 剧本+视频 → 配音音频 |
| 9 | 🎞️ **合成** | 拼接 + 字幕 + 转场 + 混音 | 全资产 → 最终 MP4 |

---

## 🚀 快速开始

### 一键启动（推荐）

```bash
双击项目根目录的 启动.bat
```

- 自动启动 API 服务器 + 暗系前端
- 自动打开浏览器 `http://127.0.0.1:17900`
- Mock 模式免 API Key 即可体验

### 手动启动

```bash
# 1. 激活虚拟环境
.venv\Scripts\activate

# 2. 安装依赖（首次）
pip install -r requirements.txt

# 3a. 启动暗系前端 + API (推荐)
python api_server.py
# 浏览器打开 http://127.0.0.1:17900

# 3b. 启动 Gradio 调试界面
python app.py
# 浏览器打开 http://127.0.0.1:17860
```

### 配置 API Key

```bash
cp .env.example .env
# 编辑 .env 填入 API Key
# 或在暗系前端的「设置」页面直接配置并保存
```

---

## 🖥️ 双前端体系

| 界面 | 入口 | 端口 | 风格 | 说明 |
|------|------|------|------|------|
| 🌙 **暗系前端** | `启动.bat` | `17900` | 火宝短剧风格 | 8 页面完整创作流程 |
| 🧪 **Gradio 控制台** | `python app.py` | `17860` | 调试工具风格 | 6 步分步 + 一键运行 |

### 暗系前端页面

| 页面 | 功能 |
|------|------|
| 🏠 工作台首页 | 项目列表、新建项目 |
| 📝 剧本编辑 | 输入创意 / AI 生成剧本 / 字数统计 |
| 👤 角色设置 | 角色卡片展示、重新提取、生成形象图 |
| 🏙️ 场景设置 | 场景卡片展示、一键生成背景 |
| 🎬 分镜列表 | 分镜卡片、绑定角色场景、提取分镜 |
| ⚙️ 制作预览 | 参数配置、提示词编辑、批量生成 |
| 🎞️ 作品管理 | 已合成短剧展示、下载 |

---

## 📂 项目结构

```
video-drama-agent-master/
│
├── 启动.bat                   # 一键启动脚本
├── app.py                     # Gradio 调试界面 (6步分步)
├── api_server.py              # FastAPI 桥接 + 暗系前端服务
├── studio.html                # 暗系火宝风格前端 (单文件)
├── requirements.txt           # Python 依赖
├── .env.example               # 环境变量模板
│
├── src/
│   ├── agents/                # 9 个专业智能体
│   │   ├── script_agent.py        # 编剧
│   │   ├── visual_agent.py        # 视觉设计
│   │   ├── character_agent.py     # 角色形象图生成
│   │   ├── scene_image_agent.py   # 场景背景图生成
│   │   ├── prompt_agent.py        # 提示词工程
│   │   ├── video_gen_agent.py     # 视频生成
│   │   ├── review_agent.py        # 质量审核
│   │   ├── audio_agent.py         # 配音
│   │   └── assembly_agent.py      # 合成
│   │
│   ├── graph/                 # LangGraph 编排
│   │   ├── state.py               # 共享状态 DramaState
│   │   └── orchestrator.py        # 流水线 + 6 分段 Graph
│   │
│   ├── schemas/               # Pydantic 数据模型
│   │   ├── script.py              # 剧本 / 场景 / 台词
│   │   ├── visual.py              # 视觉设计
│   │   ├── character.py           # 角色形象
│   │   ├── scene_image.py         # 场景图片
│   │   ├── prompt.py              # 视频提示词
│   │   ├── assets.py              # 生成的视频资源
│   │   ├── review.py              # 审核结果
│   │   └── audio.py               # 音频资源
│   │
│   ├── api_clients/           # API 客户端
│   │   ├── base.py                # 视频 API 抽象接口
│   │   ├── kling.py               # 可灵 (Kling) 客户端
│   │   ├── mock.py                # Mock 视频客户端
│   │   ├── image_base.py          # 图片 API 抽象接口
│   │   └── image_mock.py          # Mock 图片客户端
│   │
│   ├── prompts/               # AI 提示词模板
│   │   └── templates.py           # 7 套标准化提示词
│   │
│   ├── utils/                 # 工具层
│   │   ├── llm.py                 # LLM 统一客户端
│   │   ├── tts.py                 # Edge-TTS 配音
│   │   ├── video.py               # FFmpeg 工具
│   │   └── project_store.py       # 项目资产管理
│   │
│   └── config.py              # 全局配置
│
├── tests/                     # 测试
│   ├── test_orchestrator.py       # 集成测试
│   └── test_schemas.py            # Schema 单元测试
│
└── output/                    # 生成输出
    ├── projects.json              # 项目索引
    ├── <project_id>/              # 按项目 ID 分目录
    │   ├── meta.json              # 项目元数据
    │   ├── characters/            # 角色形象图
    │   ├── scenes/                # 场景环境图
    │   ├── videos/                # 视频片段
    │   ├── audio/                 # 配音文件
    │   └── final/                 # 合成后最终视频
    └── settings_ui.json           # API 配置档案
```

---

## 🧠 7 套 AI 提示词模板

内置在 `src/prompts/templates.py` (Python) 和 `studio.html` (JavaScript)：

| # | 模板 | 说明 |
|---|------|------|
| 1 | `SCRIPT_GEN_TEMPLATE` | AI 生成完整短剧剧本 (60-90秒) |
| 2 | `ROLE_EXTRACT_TEMPLATE` | 从剧本提取角色 (JSON) |
| 3 | `SCENE_EXTRACT_TEMPLATE` | 从剧本提取场景 (JSON) |
| 4 | `SHOT_EXTRACT_TEMPLATE` | 从剧本提取分镜 (JSON) |
| 5 | `CHARACTER_IMAGE_TEMPLATE` | 生成角色形象绘图提示词 |
| 6 | `SCENE_IMAGE_TEMPLATE` | 生成场景背景绘图提示词 |
| 7 | `SHOT_IMAGE_TEMPLATE` | 生成分镜画面绘图提示词 |

---

## ⚙️ 配置说明

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

## 🔌 API 端点

启动 `api_server.py` 后可用，完整文档：`http://127.0.0.1:17900/docs`

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/health` | 健康检查 |
| `POST` | `/api/script` | 生成剧本 + 视觉设计 |
| `POST` | `/api/characters` | 提取角色 + 生成形象图 |
| `POST` | `/api/scenes` | 生成场景背景图 |
| `POST` | `/api/prompts` | 生成视频提示词 |
| `POST` | `/api/revise-prompt` | AI 修订提示词 |
| `POST` | `/api/videos` | 生成视频片段 |
| `POST` | `/api/audio` | 生成配音 |
| `POST` | `/api/assemble` | 合成最终视频 |
| `GET` | `/api/projects` | 项目列表 |
| `GET` | `/api/projects/{id}/assets` | 项目资产文件 |

---

## 🧪 运行测试

```bash
pytest tests/ -v
```

---

## 💰 成本估算

| 模式 | 每条短剧成本 |
|------|-------------|
| Mock (全部模拟) | $0 |
| Mock 视频 + 真实 LLM | ~$0.01 - 0.05 |
| 可灵 API + 真实 LLM | ~$0.5 - 2.0 |

---

## 🛠️ 技术栈

| 层 | 技术 |
|----|------|
| 智能体编排 | LangGraph + LangChain |
| 数据模型 | Pydantic v2 |
| LLM | Anthropic Claude / OpenAI 兼容 |
| 视频生成 | 可灵 Kling / Mock |
| 图片生成 | Mock / OpenAI DALL-E |
| 视频处理 | MoviePy + FFmpeg |
| 语音合成 | Edge-TTS (微软免费) |
| 前端 | Tailwind CSS + 原生 JS / Gradio |
| API 服务 | FastAPI + Uvicorn |
| 持久化 | JSON 文件 (项目资产索引) |

---

## 🔮 后续扩展

- [ ] 可灵 / Runway / Pika 图生视频模式
- [ ] SD / DALL-E 真实图片 API 对接
- [ ] 角色一致性模型 (IP-Adapter / InstantID)
- [ ] 公共角色库 + 场景模板库
- [ ] 批量生成 + 队列管理
- [ ] WebSocket 实时进度推送
- [ ] 多语言剧本支持
