# Contest Visuals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 生成三张可替换参赛说明文档配图的高分辨率 PNG，并保留原 DOCX 不变。

**Architecture:** 三张图采用统一的深色桌面软件视觉语言：一张讲技术架构，一张讲产品形态，一张讲用户创作闭环。生成图不承担长中文说明，图号、标题和详细解释保留在 Word 正文中；所有候选先保存至项目目录，选定后才插入 DOCX 副本。

**Tech Stack:** 内置 image generation、PNG、Word DOCX 媒体资源。

## Global Constraints

- 不使用聊天记录中暴露的 API Key；用户如需自有 API，只能通过本机环境变量配置新密钥。
- 不修改前四张签名图，也不覆盖原始 DOCX。
- 画幅固定为横向 16:9，优先 1920×1080 或更高。
- 不生成水印、虚构品牌、数据指标、获奖标识、人物肖像主视觉或密集中文 UI 文案。
- 图 1、图 2、图 3 分别保存为 `contest-architecture-v2.png`、`contest-workbench-v2.png`、`contest-workflow-v2.png`。

---

### Task 1: 生成提示词包

**Files:**
- Create: `docs/superpowers/prompts/2026-07-27-contest-visuals.md`

**Produces:** 三段可直接用于 gpt-image-2 的英文提示词，包含统一风格、构图、禁止项和图外文字策略。

- [ ] **Step 1: 保存三段提示词**

每段提示词必须包含：`Use case`、`Asset type`、`Primary request`、`Style/medium`、`Composition/framing`、`Constraints` 和 `Avoid`。

- [ ] **Step 2: 人工检查提示词**

检查三段提示词是否都要求横向 16:9、深蓝软件视觉、无密集文字、无水印，并分别对应技术架构、产品工作台和端到端流程。

### Task 2: 生成并筛选技术架构图

**Files:**
- Create: `assets/contest/contest-architecture-v2.png`

**Produces:** 清晰的多 Agent 分层架构图，只有一条审核回流线，所有模块均可在普通文档版面中辨认。

- [ ] **Step 1: 生成候选图**

使用 Task 1 的 Architecture Prompt，输出横向 16:9 PNG。

- [ ] **Step 2: 验收候选图**

确认从左到右存在创作入口、Agent 主链路、审核回流、状态/资产支撑层和模型服务层；不存在交叉箭头、代码小字或不可读长文本。

### Task 3: 生成并筛选产品工作台图

**Files:**
- Create: `assets/contest/contest-workbench-v2.png`

**Produces:** 一张高保真桌面端 AI 短剧创作工作台概念界面。

- [ ] **Step 1: 生成候选图**

使用 Task 1 的 Workbench Prompt，输出横向 16:9 PNG。

- [ ] **Step 2: 验收候选图**

确认画面具有导航、剧本/镜头编辑区、资产参考、预览和轻量时间线；界面为桌面创作工具，不是营销页面或手机应用。

### Task 4: 生成并筛选创作闭环图

**Files:**
- Create: `assets/contest/contest-workflow-v2.png`

**Produces:** 五步式用户创作流程图，能够替换现有截图拼贴图。

- [ ] **Step 1: 生成候选图**

使用 Task 1 的 Workflow Prompt，输出横向 16:9 PNG。

- [ ] **Step 2: 验收候选图**

确认五个卡片按创意、剧本、资产、分镜、成片顺序排列，使用统一直线箭头，只有一条可辨认的审核回流线。

### Task 5: 交付与文档替换准备

**Files:**
- Create: `assets/contest/README.md`
- Preserve: `E:/影视agent/2026中国高校计算机大赛人工智能创意赛初赛（鸿蒙赛道）作品说明文档模板 (1)(2)(1).docx`

**Produces:** 图像文件、生成提示词和对应替换说明。

- [ ] **Step 1: 写入替换对照**

`README.md` 明确记录：`contest-architecture-v2.png` 替换原 image5；`contest-workbench-v2.png` 替换原 image6；`contest-workflow-v2.png` 替换原 image7。

- [ ] **Step 2: 最终验收**

在白色 Word 页面和深色背景预览中检查三图的边缘、对比度、文字可读性和统一性；保留原 DOCX，只在用户选定图片后创建新副本插图。
