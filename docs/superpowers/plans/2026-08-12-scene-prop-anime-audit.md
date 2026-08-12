# 场景道具动漫风与审核链路实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use test-driven-development and requesting-code-review while implementing this plan task-by-task.

**Goal:** 让原剧本在选择动漫画风后，场景与道具提示词正确继承项目风格、资产审核可追溯且通过；同时确认真人风格不回归，并移除无效的分镜“提示词策略”入口。

**Architecture:** 以项目 `CreativeProfile`/视觉风格注册表作为唯一画风来源，前端创建项目和后端资产提示词都读取同一能力契约。资产提示词先生成结构化层，再由统一 composer 组装；审核分为结构化剧本、视觉资产清单、图片技术/视觉审核和视频提示词静态审核，任何一层失败都保留可操作错误而不激活错误资产。

**Tech Stack:** FastAPI/Pydantic、原生 ES Modules、SQLite 项目状态、pytest、现有图片质量审核与快乐小马视频适配器。

## Global Constraints

- 不生成视频，只检查快乐小马的视频提示词和参考图参数。
- 不删除或覆盖 `output/` 中任何资产。
- 保留真人风格、现有图片/视频供应商和 API 兼容行为。
- 动漫风格必须项目级隔离，不能通过旧剧本人物、地点或道具硬编码实现。
- 生产代码修改前必须先有会失败的回归测试。

---

### Task 1: 现状契约审计

**Files:** `web/`, `studio.html`, `src/api/`, `src/services/`, `src/agents/`, `tests/`

- [ ] 检查项目创建页画风控件是否提交 `visual_style_id`，并追踪到项目状态和 `CreativeProfile`。
- [ ] 检查场景、道具提示词入口是否都经过 `compose_asset_prompt`/统一服务，不允许旧入口绕过风格和审核。
- [ ] 检查分镜顶部“提示词策略”的 DOM、状态字段、请求参数和后端消费点；只有在确实无消费点时移除显示与兼容残留。
- [ ] 检查快乐小马请求快照是否包含已组装的时间轴提示词、参考图顺序、画幅和时长；不创建视频任务。
- [ ] 输出审计记录到本计划对应的测试和最终报告，不凭静态猜测宣称已验证。

### Task 2: 先写跨风格回归测试

**Files:** `tests/test_visual_style_prompts.py`, `tests/test_scene_prompt_layers.py`, `tests/test_prop_prompt_composer.py`, `tests/test_video_generation_snapshot.py`, `tests/test_generalization_regressions.py`

- [ ] 添加动漫项目测试：创建 `visual_style_id=cinematic_cel` 的项目，场景和道具提示词包含动漫媒介、项目色彩与当前实体，且不包含旧剧本实体。
- [ ] 添加真人项目测试：同一剧本切换 `visual_style_id=live_action`，提示词包含真人媒介约束，且不残留动漫否定词。
- [ ] 添加项目创建请求测试：画风选择被保存并在刷新读取时保持，缺省值仅在未选择时使用。
- [ ] 添加快乐小马快照测试：参考图数量/顺序按模型上限裁剪并返回警告，最终提示词使用当前镜头内容和风格。
- [ ] 添加提示词策略测试：若策略无后端消费点，确认页面不再渲染该控件；若仍有消费点，断言实际请求包含策略字段。
- [ ] 运行新增测试，确认它们在修复前至少有一个明确失败。

### Task 3: 修复项目创建画风选择

**Files:** `web/js/pages/projects.js` 或实际项目创建模块、`studio.html`、`src/api/routes/projects.py`/`src/api/context.py`、视觉风格注册模块

- [ ] 用注册表返回的风格 ID、中文名、缩略图/示例图和描述渲染单选控件，不使用硬编码按钮点击副作用。
- [ ] 创建项目请求显式携带 `visual_style_id` 和现有画幅/类型/音频字段；后端校验未知 ID 返回 422，并在旧项目读取时迁移为默认真人风格。
- [ ] 项目切换、刷新和草稿恢复读取同一字段，不能只更新前端临时状态。
- [ ] 完成 Task 2 的动漫/真人项目测试并运行项目状态迁移测试。

### Task 4: 统一场景与道具提示词适配

**Files:** `src/services/asset_prompt_composer.py`, `src/services/prop_prompt_composer.py`, `src/agents/image_gen_agent.py`, `src/services/asset_generation.py`, `src/utils/visual_asset_manifest.py`

- [ ] 先读取项目风格快照，再分别组装场景身份、场景状态、道具身份、道具当前状态；固定约束只能补充，不能覆盖剧本事实。
- [ ] 场景组图/母版和道具单图均使用当前项目风格；没有声明的材质、天气、人物、文字和后续状态不得补入。
- [ ] 道具只保留满足关键性定义且在当前镜头可见的实体；短信、录音内容、电话和抽象信息不得生成图片。
- [ ] 所有旧入口调用统一 composer、seed 隔离和图片质量审核；真人项目继续使用原有写实媒介约束。
- [ ] 组图审核按视图使用独立提示词，母版版本变化时旧结果不得激活。
- [ ] 运行场景、道具、资产版本和跨剧本回归测试。

### Task 5: 移除或接通“提示词策略”

**Files:** 分镜工作台页面模块、`web/js/core` 状态/API 模块、`src/api/routes/production.py`、视频提示词服务、相关测试

- [ ] 使用审计结果决定：若字段没有后端消费点，删除标签、状态、请求参数和死代码；保留现有提示词编辑能力。
- [ ] 若字段确有消费点，将其改为明确的项目提示词策略并让快乐小马快照、重试和历史版本记录实际值。
- [ ] 任务事件局部更新不得重建提示词编辑区，保存未提交文本和滚动位置。
- [ ] 更新前端契约测试和快乐小马快照测试。

### Task 6: 完整审核链路与原剧本动漫资产验证

**Files:** `src/agents/script_agent.py`, `src/utils/visual_asset_manifest.py`, `src/services/media_quality.py`, `src/services/asset_generation.py`, `src/services/timed_prompt_composer.py`, `tests/`

- [ ] 明确每层审核输入/输出：结构化剧本版本、资产清单错误、提示词契约报告、图片技术/视觉报告、视频提示词静态报告。
- [ ] 审核失败时不激活资产、不伪造成功；结果保存 `quality_report`、错误原因、任务和版本关联。
- [ ] 用现有原剧本创建或恢复动漫项目，仅生成角色/场景/道具图片，不提交视频任务；逐项检查图片存在、尺寸/比例、媒介风格、视图约束和审核状态为通过或可操作未审核。
- [ ] 生成并检查快乐小马视频提示词快照：参考图、首尾帧能力、时长、画幅、连续性和项目风格一致；不调用供应商生成视频。
- [ ] 真人风格执行同样的提示词与审核快照对比，确认没有动漫媒介泄漏。

### Task 7: 全量验证与代码审查

**Files:** 测试结果、审计报告、必要的 README/文档

- [ ] 运行 `py_compile`、新增测试、全量 `pytest` 和前端模块语法检查。
- [ ] 检查 `output/` 文件数量/项目目录未减少。
- [ ] 请求独立代码审查，修复 Critical/Important 问题。
- [ ] 最终报告区分“已自动验证”“仅静态检查”“因外部供应商不可用未验证”，不把未生成视频写成已验证。
