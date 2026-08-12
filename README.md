# 多智能体协作视频短剧生成系统

## 当前应用结构

FastAPI 与桌面工作台已经模块化，仍使用原有零构建启动方式：

```text
api_server.py              # 创建应用、挂载路由/静态文件、启动服务
src/api/context.py         # 共享存储、任务管理器、Agent 与热重载代理
src/api/routes/            # health/scripts/assets/production/audio/jobs/projects/settings
src/services/asset_generation.py # 资产生成、质量检查与版本提交的共享服务
src/services/audio_timeline.py   # 音频时间轴排布、素材选择和预览混音服务
studio.html                # 工作台 HTML 结构壳
web/js/bootstrap.js        # 原生 ES Modules 入口
web/js/core/               # 状态、请求、SSE 和导航
web/js/pages/              # 剧本、资产、项目、设置、分镜页面逻辑
web/js/components/         # 任务抽屉、图片编辑器、资产卡片、音频时间轴
web/css/                   # 基础、设置、资产、分镜和音频样式
```

模块边界：`api_server.py` 只负责应用装配和启动；HTTP 处理放入所属 `APIRouter`；跨路由业务逻辑放入 `src/services/`。`StudioActions` 仅作为现有 HTML 内联事件与动态旧模板的兼容入口；新功能必须使用页面模块、组件模块或事件委托，不能继续向全局作用域添加函数。

使用 `start_api_server.bat` 或 `启动.bat` 启动，不需要 Node.js、Vite 或前端构建步骤。新增 API 应放入所属 `APIRouter`；跨路由业务逻辑放入 `src/services/`，不要让路由互相发 HTTP 请求。新增前端功能按页面或组件进入 `web/js/`，共享状态和请求逻辑进入 `web/js/core/`。

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
| 6 | 🔊 **音频** | `src/agents/audio_agent.py`、`src/services/audio_timeline.py` | 项目音频时间轴、逐句配音、BGM/关键音效素材和字幕导出 |
| 7 | 🎞️ **合成** | `src/agents/assembly_agent.py` | FFmpeg 统一时间轴合成、字幕烧录和响度规范化 |

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

图片资产统一通过 `src/services/asset_seed.py` 派生随机种子：

```python
seed = derive_asset_seed(project_id, asset_type, asset_id, view_type, prompt_template_version)
```

首次生成使用稳定 SHA-256 seed；任务失败重试沿用原 seed；用户主动重新生成时使用新随机 seed。每个历史版本保存实际 seed，便于追踪和复现。

### 层级 3：参考图层 — 多视角角色形象图

当前资产生成走项目资产库和版本历史。角色、场景、道具都先生成可确认的关键母版，再按镜头需求补齐参考视图。

`ImageGenerationAgent` 旧批量接口仍保留兼容；项目工作台使用 `src/services/asset_generation.py` 的统一服务。

| 视角 | view_type | 尺寸 | 用途 |
|------|-----------|------|------|
| 正面半身母版 | `front_half` | 1:1 | 先生成并确认，锁定真实演员身份 |
| 正面全身 | `front_full` | 9:16 | 体型、服装、鞋和比例参考 |
| 左侧半身 | `left_profile` | 1:1 | 侧脸轮廓与发型参考 |
| 背面全身 | `back_full` | 9:16 | 后背、发型轮廓和服装背面参考 |

场景参考按“地点身份 + 状态 + 视角需求”生成：

| 景别 | view_type | 尺寸 | 用途 |
|------|-----------|------|------|
| 中心母版 | `scene_center` | 16:9 | 先生成并确认，锁定空间身份和状态 |
| 左前 45 度 | `scene_left_45` | 16:9 | 按镜头机位需求补充 |
| 右前 45 度 | `scene_right_45` | 16:9 | 按镜头机位需求补充 |
| 反向 | `scene_reverse` | 16:9 | 通道、出口、背向机位等需求 |

道具参考按 `front/back/left/right/top/bottom` 独立视图生成。`front` 是母版，必须确认后才会进入视频参考；`standard_views` 旧 2x3 联系页只作为历史预览和兼容提示词入口，不允许作为新生成入口，也不会进入视频任务。

关键道具只保留真实出镜、跨镜头绑定且有 `critical_reason` 与 `critical_evidence` 的实体物件。固定家具、背景陈设、水渍、血迹、烟雾等归场景或镜头状态；不再有固定 5 个道具上限，数量过高只产生密度警告。

视频生成时，`VideoGenerationAgent` 按当前镜头绑定选择参考图：匹配 `location_id + state_id` 的场景视图、角色多视图、镜头所需的道具独立视图；会跳过过期图、未确认母版和旧道具联系页。

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
    │   ├── assets/                   # 版本化图片资产
    │   │   └── 林屿/
    │   │       ├── front_half.png    #   角色正面半身母版
    │   │       ├── front_full.png    #   正面全身
    │   │       ├── left_profile.png  #   左侧半身
    │   │       └── back_full.png     #   背面全身
    │   ├── assets/scenes/            # 场景地点状态参考图
    │   │   └── loc_room/
    │   │       ├── scene_center.png
    │   │       ├── scene_left_45.png
    │   │       └── scene_reverse.png
    │   ├── assets/props/             # 道具独立参考视图
    │   │   └── old_key/
    │   │       ├── front.png         #   已确认母版
    │   │       └── back.png          #   按镜头需求派生
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
  输出: CharacterImageAssets { front_half/front_full/left_profile/back_full }
        SceneImageAssets { scene_center/scene_left_45/scene_right_45/scene_reverse }
        PropImageAssets { front/back/left/right/top/bottom }
  流程: master -> confirm -> required reference views
  磁盘: output/<id>/assets/characters/少年/{front_half,front_full,left_profile,back_full}.png
        output/<id>/assets/scenes/loc_room/{scene_center,scene_reverse}.png
        output/<id>/assets/props/old_key/{front,back}.png

Stage 3: 透传 (prompts 已在 Stage 1 生成)

Stage 4: VideoGenerationAgent
  输入: GenerationPrompts + CharacterImageAssets + SceneImageAssets + PropImageAssets
  选图: _build_reference_bundle() → 按镜头绑定选择 scene/character/prop 独立参考图
  规则: 跳过未确认母版、过期派生图和道具 standard_views 联系页
  API:  VideoAPIClient.generate_video(prompt="...", image_references=[...])
  输出: GeneratedAssets { shots[5] { video_url, local_path, duration_sec } }

Stage 5: ReviewAgent
  输入: ScriptBreakdown + VisualDesign + GeneratedAssets
  自动检查: 缺失场景(critical) + 时长偏差(minor) + 成功率
  LLM审核: 内容一致性 + 视觉质量 + 角色一致性 + 时长
  输出: ReviewResult { overall_score, passed, issues[] }
  路由: passed → audio | failed + retries_left → visual_prompt | failed + max → audio

Stage 6: AudioAgent
  输入: ScriptBreakdown + GeneratedAssets
  项目音频时间轴: 每句台词记录真实时长、版本、音色和时间冲突
  素材库: 用户上传 BGM/关键音效，自动推荐可手动覆盖，人工选择优先
  字幕: 从已激活台词版本的时间轴导出，可选择只显示台词或角色名+台词
  输出: AudioAssets { scenes[], library_assets[], voice_profiles[], preview_mix_path }

Stage 7: AssemblyAgent
  输入: ScriptBreakdown + GeneratedAssets + AudioAssets
  FFmpeg: 使用统一镜头时间轴混合对白、BGM 和音效，并进行响度规范化；新音频任务默认关闭视频供应商原声，避免片段拼接时环境声突变
  新解析项目默认使用硬切；旧项目保存的 fade/dissolve 字段仍可兼容读取
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

### 资产迁移

旧项目文件可以先 dry-run 审计，不会移动文件：

```bash
python scripts/migrate_project_assets.py
```

确认后再执行实际迁移：

```bash
python scripts/migrate_project_assets.py --apply
```

迁移是幂等的，重复运行不会二次移动同一文件，也不会重复创建版本记录。旧场景图会保留磁盘文件并补齐规范化 `location_id` 与默认 `state_id`；旧道具 `standard_views` 会保留为 `legacy_sheet` 历史/预览资产。新的视频任务只使用已确认的独立道具视图，不再把 `standard_views` 作为视频参考图。

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
# 项目视觉风格

新建项目的主视觉风格由 `GET /api/visual-styles` 提供，当前包含现代电影级二维赛璐璐、半写实动漫、日系商业动画和电影级真人写实四个稳定 ID。项目只保存一个主风格，赛博朋克、国风、清新等旧选项作为 `style_modifiers` 补充标签；旧项目的 `art_style` 会按兼容映射懒迁移，不会改变历史图片或视频。

新增主风格只应修改 `src/services/visual_styles.py`：必须提供稳定 `id`、名称、角色/场景/道具/分镜四层正向约束和对应负向约束。创建页综合参考图放在 `web/assets/style-references/`，每张为 1600x900 WebP，仅用于界面选择展示，不作为生成参考图，也不得写入 `output/`。图片和视频任务提交时会冻结风格快照，之后修改项目风格不会重写历史版本。

# Seedance video configuration

Seedance is an independent video provider. New tasks default to
`doubao-seedance-2-0-fast-260128` when no model is selected. The current model
IDs and their capabilities are returned by `GET /api/video-capabilities`.

The production workspace validates duration and resolution against the chosen
model; invalid values return a validation error instead of being truncated.
Reference modes are explicit: `text_to_video`, `first_frame`,
`first_last_frame`, or `multi_reference`. The frozen task snapshot records
accepted references and every dropped reference with its reason, and the task
detail panel displays that decision.

Seedance requests use `generate_audio=true` for provider-native dialogue; BGM
and key sound effects remain owned by the project audio timeline. When
requested, `return_last_frame=true` saves the
provider tail frame into the project video version and falls back to FFmpeg
extraction if no provider tail is returned. Canceling a running task calls the
official `DELETE /api/v3/contents/generations/tasks/{task_id}` endpoint while
keeping local cancellation state authoritative.
# Seedance 音频策略

选择 Seedance 新任务时，视频请求会启用供应商原生对白（`generate_audio=true`）。项目不会把 BGM 或关键音效交给视频模型，而是在成片 FFmpeg 混音阶段统一加入；可灵、快乐小马、万相和 Mock 仍沿用项目 TTS 时间轴。

原生对白会随镜头版本保存，字幕默认沿用剧本对白和时间估算。需要校正原生对白时，可在项目音频设置中启用 ASR，并设置已下载到本机的 `FASTER_WHISPER_MODEL_PATH`；系统不会自动下载模型。ASR 不可用、低置信度或识别失败时，自动回退剧本字幕，不阻断内部试片。
