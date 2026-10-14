# 分镜容量规划与自动拆镜实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use test-driven development and subagent-driven development review checkpoints. This plan is executed directly on the main working tree as requested; do not create or use an isolated worktree.

**Goal:** 将剧本解析后的 Beat 事实、容量预检、可执行 ShotPlan 和视频生成任务打通，解决单镜头过载、关键对白/动作丢失、时间轴压缩以及“建议拆镜但实际仍只生成一个镜头”的问题。

**Architecture:** 保留原始剧本不可变。结构化剧本后新增可追踪 BeatPlan 和 ShotPlan；容量规划在提示词生成前完成，单镜过载且总时长足够时自动拆成可独立生成的 ShotUnit，整场时长不足时明确报告而不静默删改剧本。所有提示词、视频任务、字幕/音频和成片片段通过稳定的 shot_id、parent_scene_id、beat_id、dialogue_id 追踪。

**Tech Stack:** Python 3.12, Pydantic, pytest, FastAPI, 现有 LangGraph/视频生成 agent、现有 provider capability policy。

**Spec:** 本文件；验收以《麦琪的礼物》真实存量数据及受控容量样本为基线。

## Global Constraints

- 不修改原始剧本、script_draft_text 或用户输入；不自动删减关键剧情。
- 不迁移或重算旧项目；旧项目继续可读，新项目走新规划。
- 不改变 DeepSeek 模型、思考档位和 Kimi 审核逻辑；Kimi 仍在首次提示词生成完成后运行。
- 不通过缩短 prompt 时间轴掩盖容量不足；禁止生成零时长或超出有效时长的时间段。
- 所有 provider 时长上下限来自最终 video policy/capability snapshot，不在业务层新增 provider 硬编码。
- 生成前门禁必须同时覆盖单镜、批量、任务执行、LangGraph 直连和最终 provider request。
- 外部 TTS、native dialogue 和视觉 prompt 都是合法对白承载渠道，审计通过 dialogue_id 识别，不只做全文字符串匹配。
- 所有新增行为必须先写失败测试并观察失败，再实现最小代码。
- 不提交 API key、数据库、tmp 实验输出和用户资产。

---

### Task 1：统一容量与镜头规划数据契约

**Files:**
- Modify: `src/schemas/prompt.py`
- Modify: `src/schemas/script.py`
- Create: `src/schemas/shot_plan.py`
- Test: `tests/test_shot_plan_schema.py`

**Requirements:**
- 保留现有 `ShotCapacityDiagnostic` 兼容读取，扩展而不是创建平行容量 schema。
- 新增 `NarrativeBeat`、`BeatDurationEstimate`、`ShotUnit`、`ShotPlan`、`SceneCapacityReport`。
- `ShotUnit` 必须有 `shot_id`、`parent_scene_id`、`order`、`beat_ids`、`dialogue_ids`、`duration_sec`、`source_fact_ids`、连续性字段和提示词状态。
- `SceneCapacityReport` 必须分离 `required_total_duration_sec`、`available_duration_sec`、`recommended_total_duration_sec`、`recommended_durations_sec`、`recommended_shot_count`。
- 旧数据缺少新字段时提供默认值，不能破坏已有项目读取。

**Verification:** schema round-trip、旧 payload 兼容、父子镜头唯一性和时长字段校验。

### Task 2：Beat 提取和对白/动作事实追踪

**Files:**
- Modify: `src/services/shot_capacity.py`
- Create: `src/services/beat_planner.py`
- Test: `tests/test_beat_planner.py`
- Test: `tests/test_shot_capacity.py`

**Requirements:**
- 从结构化场景动作、对白、镜头、道具和连续性生成有序 beat。
- 每个 beat 保留原文摘录、fact_id 和 required 标记。
- 对白通过 dialogue_id 追踪；承载渠道支持 provider prompt、native dialogue、voice-only、external TTS dialogue_plan 和字幕计划。
- 不能用简单标点切分作为唯一事实来源；至少识别主要动作、道具变化、情绪落点、空间移动和声音事件。
- 容量估算输出动作成本、对白估算时长、道具变化、镜头变化和情绪转折的明细。
- 对同义动作、否定句、普通环境描述和声音事件补充回归样本。

**Verification:** 《麦琪的礼物》场景 6/7/8/10 可列出关键 beat；删除一个 beat 的变异样本不能被判定为更好通过。

### Task 3：容量预检与自动 ShotPlan 打包

**Files:**
- Create: `src/services/shot_planner.py`
- Modify: `src/services/shot_capacity.py`
- Test: `tests/test_shot_planner.py`
- Test: `tests/test_shot_capacity.py`

**Requirements:**
- 使用最终 provider capability 规划每个可执行镜头。
- 单镜过载但场景总时长足够时自动拆镜；按动作依赖、对白边界、道具状态和情绪落点切分。
- 整场时长不足时返回 `duration_insufficient`，不自动修改剧本，不静默删内容。
- 不设置固定 beat 数量上限；使用容量预算和最小/最大镜头时长。
- 每个推荐时长都合法且总和等于推荐总时长；不能把 required duration 截断成 provider cap。
- 子镜头必须有可执行的父子关系、稳定 ID、连续性和资产继承信息。

**Verification:**
- 简单场景不被过度拆分。
- 《麦琪的礼物》高负载场景产生可解释的拆镜或时长不足报告。
- 不再出现 `6s-6s`。

### Task 4：按 ShotPlan 生成提示词并禁止超载压缩

**Files:**
- Modify: `src/agents/visual_prompt_agent.py`
- Modify: `src/services/timed_prompt_composer.py`
- Test: `tests/test_timed_prompt_composer.py`
- Test: `tests/test_prompt_fidelity_audit.py`

**Requirements:**
- 每个提示词只读取当前 ShotUnit 的 beat、当前状态和上一镜结束状态。
- 不再把完整场景动作复制到每个子镜头。
- authored timeline 非法时报告，不通过全局字符串替换或时间压缩静默修复。
- 时间轴统一为严格递增的范围；每段 `start < end <= effective_duration`。
- 禁止全局 `走出`→`进入` 等宽泛替换；只处理明确识别的模板片段。
- 生成后输出 beat coverage、dialogue coverage、future leak、timeline diagnostics。

**Verification:** 镜头 3 不出现零时长段；镜头 7 不同时出现吉姆进门和德拉走出；镜头 8/10 的关键 beat 不提前或遗漏。

### Task 5：接入真实视频任务、音频字幕和成片拼接

**Files:**
- Modify: `src/agents/video_gen_agent.py`
- Modify: `src/api/routes/production.py`
- Modify: `src/api/routes/jobs.py`
- Modify: `src/api/context.py`
- Modify: `src/graph/orchestrator.py` if direct path remains
- Test: `tests/test_shot_generation_execution.py`
- Test: `tests/test_video_generation_snapshot.py`

**Requirements:**
- ShotUnit 作为实际视频任务单位；不能只填 `sub_shots` 而不创建任务。
- 任务、GeneratedAssets、字幕、TTS/native dialogue 和拼接顺序都绑定 shot_id/beat_id/dialogue_id。
- 子镜头局部失败不得静默导致父场景少片段；必须显示缺失 ID 和缺失时长。
- snapshot 记录 requested/effective/provider_returned/assembled duration。
- 所有生成入口使用共享 readiness 审计；最终 provider request 再校验一次。
- 参考图、provider、model、resolution、aspect ratio、prompt extension 与 ShotUnit 保持一致。

**Verification:** 受控 2 子镜场景必须创建 2 个真实任务并进入拼接；失败一个子镜头时成片状态不得伪装完整。

### Task 6：前端容量诊断与用户决策

**Files:**
- Modify: `web/js/pages/production-actions.js`
- Modify: relevant production UI files discovered during implementation
- Test: `tests/test_shot_capacity_ui.mjs`

**Requirements:**
- 展示场景当前时长、预计需求、推荐镜头数、推荐时长和拆镜原因。
- `clean` 直接进入生成；轻微 `warning` 允许用户确认；未拆分硬过载、非法时间轴和整场明显不足默认为 `blocked` 或 `manual_decision_required`。
- 用户选择保留原时长时明确记录风险，不自动修改剧本。
- 子镜头列表可查看、单独重生成和显示缺失状态。

**Verification:** clean/warning/blocked/duration_insufficient UI 状态和按钮行为。

### Task 7：回归基线与真实验证

**Files:**
- Create: `scripts/run_shot_capacity_benchmark.py`
- Create: `tests/fixtures/magi_capacity_gold.json`
- Modify: `docs/superpowers/plans/2026-09-01-real-deepseek-storyboard-quality-fix.md` only for evidence links if needed
- Test: `tests/test_magi_capacity_regression.py`

**Requirements:**
- 不重算旧项目，只从 `output/ea4f588be40e/meta.json` 读取基线。
- 使用语义锚点而非固定 scene_id 绑定《麦琪的礼物》验收。
- 比较旧版和新版：镜头数、beat 覆盖、对白承载、动作覆盖、时间轴合法率、环境污染、跨镜泄漏、实际任务数和最终成片时长。
- 运行至少两轮代码审查和一轮真实链路审查。
- 更新当前实际测试基线：`1014 passed, 2 skipped`，不得使用过时的 912。

**Acceptance:**
- 关键动作和对白事实覆盖率 100%。
- 关键 beat 没有静默丢失。
- 非法时间轴和零时长段为 0。
- 未拆分的 provider 超限镜头为 0。
- 实际视频任务数等于可执行 ShotUnit 数量。
- 失败片段不会被静默跳过。
- 新版《麦琪的礼物》能够明确展示自动拆镜或总时长不足，而不是继续生成一个过载 6～8 秒镜头。

---

## 审核检查点

1. Task 1 后：schema 兼容性和父子 ID 审核。
2. Task 2 后：Beat/对白/动作事实是否完整，是否存在标点误切。
3. Task 3 后：自动拆镜是否真实产生 ShotPlan，required/available/per-shot 时长是否分离。
4. Task 4 后：提示词是否只包含本镜 beat，是否仍有模板污染和零时长。
5. Task 5 后：子镜头是否真实创建视频任务并进入音频字幕和拼接链。
6. Task 6 后：前端是否能清楚表达风险而不是静默放行。
7. Task 7 后：全量测试、真实《麦琪的礼物》链路和最终独立审查。

## 预期决策

- 自动拆镜属于本次范围，不延期到另一个计划。
- 总时长不足只报告和请求用户决策，不改剧本。
- 轻微容量风险不强制 Kimi；首次提示词生成仍按既有规则走 Kimi。
- 旧项目不迁移；《麦琪的礼物》旧数据只做基线，新运行才验证新链路。
