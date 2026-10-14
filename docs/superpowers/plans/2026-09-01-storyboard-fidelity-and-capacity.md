# 分镜剧情保真与镜头承载量改造实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让最终分镜提示词忠实承载结构化剧本，并在镜头数量或单镜头时长不足时给出可执行的拆镜诊断，而不是静默压缩、补写或改写剧情。

**Architecture:** 先在组装层建立“当前镜头事实优先”的保真边界，禁止宽泛关键词触发带方向和叙事含义的模板；随后增加结构化剧本的镜头承载量分析，在视频生成前区分“剧情场景”和“可生成视频镜头”，高负载内容优先显式拆镜或阻止静默生成。保留原始剧本不变，所有修复只作用于结构化结果、分镜提示词和生成诊断。

**Tech Stack:** Python 3、Pydantic、pytest、现有 `ScriptAgent` / `VisualPromptAgent` / `timed_prompt_composer` / `VideoGenerationAgent` 链路、现有 JSON 项目状态持久化。

**Spec:** `docs/superpowers/plans/2026-09-01-story-prompt-quality-roadmap.md`；本计划吸收其中尚未完成的“镜头承载量、组装层禁止改写、对白/声音承载、状态隔离和最终一致性检查”要求。

## Global Constraints

- 不修改用户原始剧本正文；不自动改写 `script_draft_text`。
- 不调用或改造 Kimi 审核；本轮只做确定性保真检查和分镜组装改造。
- 不改变 DeepSeek 当前模型、思考档位和已有并行配置。
- 不重算历史项目、不修改历史视频；《麦琪的礼物》只作为独立回归样本。
- 不覆盖当前未提交改动，尤其是 `src/api/routes/assets.py`、`src/api/routes/settings.py`、`tests/test_asset_quality_final_fixes.py`、`tests/test_settings_api.py`、`tests/test_video_selection_and_seedance_errors.py`、`web/js/pages/production-actions.js`、`docs/末世十秒-成片剧本.md` 和 `.worktrees/`。
- 实施前使用独立 worktree/分支；不得执行 `git reset`、`git clean`、`checkout` 覆盖现有工作。
- 所有新生成结果写入 `tmp/` 或新项目目录，不覆盖 `output/ea4f588be40e`。
- provider 时长上限继续保持：HappyHorse/Wan 15 秒、Kling 10 秒；超过上限必须拆分或显式报告，不得只缩短文字时间轴。
- 视频生成前必须保留当前提示词签名、诊断结果和实际请求时长；本计划不改变审核门禁策略。

## 文件职责地图

- `src/agents/script_agent.py`：忠实解析已有剧本，输出剧情场景；不在解析阶段用“每场一个核心动作”压缩完整动作链。
- `src/schemas/script.py`：定义剧情场景和镜头承载诊断的数据结构，保留兼容旧项目的字段范围。
- `src/schemas/prompt.py`：定义视频生成镜头、子镜头和可选承载诊断字段。
- `src/services/timed_prompt_composer.py`：只把当前镜头的结构化事实组装为时间轴；不得根据普通关键词重新编剧。
- `src/agents/visual_prompt_agent.py`：将视觉差异和结构化事实合并；负责清理环境、角色状态和时间轴摘要冲突。
- `src/services/prompt_fidelity_audit.py`：新增确定性最终提示词保真审计，检查方向错误、剧本外模板、跨镜污染和时间冲突。
- `src/services/shot_capacity.py`：新增镜头承载量分析，输出动作/对白/道具/情绪负载和建议拆镜数量。
- `src/api/routes/jobs.py`：在生成前运行确定性诊断；本轮不自动改剧本，过载时返回明确结果。
- `tests/test_prompt_fidelity_audit.py`：保真审计单元测试。
- `tests/test_shot_capacity.py`：镜头承载量分析单元测试。
- `tests/test_prompt_policy.py`、`tests/test_generalization_regressions.py`、`tests/test_video_generation_snapshot.py`：现有回归扩展。
- `scripts/run_storyboard_quality_check.py`：新增离线样本检查脚本，不调用 Kimi，不生成视频。

---

### Task 1: 固定《麦琪的礼物》当前基线和证据快照

**Files:**
- Create: `scripts/run_storyboard_quality_check.py`
- Create: `tmp/magi_prompt_quality_baseline_20260901.json`（运行产物，不提交）
- Create: `tmp/magi_prompt_quality_baseline_20260901.md`（运行产物，不提交）
- Read only: `output/ea4f588be40e/meta.json`
- Read only: `tmp/magi_prompt_regen_20260901.json`

**Interfaces:**
- 脚本读取项目 `output/ea4f588be40e` 的 `state_json`，提取 `ScriptBreakdown`、`generation_prompts`、`script_draft_text` 和 `visual_design`。
- 脚本输出每镜的 `scene_id`、剧本动作、剧本对白、提示词全文、提示词时长、动作时间段、关键事实命中/缺失和疑似跨镜内容。
- 输出不得包含 API key、Authorization header 或完整请求信息。

- [ ] **Step 1: 写离线基线脚本的失败测试**

新增测试 `tests/test_storyboard_quality_baseline.py::test_magi_baseline_has_expected_12_scene_92_sec_snapshot`，断言读取样本后得到 12 镜、92 秒，并记录镜头 4、6、7、10、12 的已知问题标签。

- [ ] **Step 2: 运行测试确认基线可复现**

运行：

```bash
pytest -q tests/test_storyboard_quality_baseline.py::test_magi_baseline_has_expected_12_scene_92_sec_snapshot
```

预期：基线测试通过；如果项目状态文件路径或字段不兼容，先修脚本读取适配，不修改生产逻辑。

- [ ] **Step 3: 实现离线报告脚本**

对每个镜头输出以下字段：

```json
{
  "scene_id": 4,
  "script_duration_sec": 8,
  "prompt_duration_sec": 8,
  "dialogue_count": 4,
  "missing_dialogue": [],
  "unsupported_template_terms": [],
  "cross_scene_terms": [],
  "timeline_conflicts": []
}
```

- [ ] **Step 4: 运行并保存基线**

运行：

```bash
python scripts/run_storyboard_quality_check.py --project output/ea4f588be40e --out-dir tmp/magi_prompt_quality_baseline_20260901
```

预期：生成 JSON 和 Markdown 基线；此步骤不调用模型、不修改项目状态。

- [ ] **Step 5: 复核基线内容**

确认基线至少记录：镜头 4/7 的“走出”错误、镜头 6 的独白缺失、镜头 10 的额外闪回、镜头 7—12 的环境污染和镜头 3 的时长摘要冲突。

---

### Task 2: 修复解析层对镜头粒度的压缩诱导

**Files:**
- Modify: `src/agents/script_agent.py:41-50, 230-249, 268-282, 414-415`
- Test: `tests/test_script_analysis.py` 或现有脚本解析测试文件

**Interfaces:**
- `ScriptAgent.analyze_existing_script()` 继续返回 `ScriptBreakdown`。
- 对已有剧本，解析提示词必须优先忠实切分；“每场只承载一个核心动作”只能作为新创作模式的建议，不能作为已有剧本解析的压缩要求。
- 解析结果仍兼容 `Scene.duration_sec` 旧字段，但必须允许通过 `shot_load` 诊断报告内容过载。

- [ ] **Step 1: 添加解析契约测试**

新增测试，使用包含以下链条的已有剧本片段：

```text
角色打开纸包，看到礼物，先惊叫，随后抽泣，抱住礼物，最后抬头微笑。
```

断言解析输出至少保留 5 个可追踪动作事实；不能只得到“角色发现礼物并产生反应”。

- [ ] **Step 2: 添加长对白保真测试**

使用一段超过 30 字的独白，断言 `dialogue[].line` 保留原文，不因“每句台词尽量不超过20-30字”被拆写、删写或改写；舞台动作可以进入 `action_description`，但台词文本必须完整保留。

- [ ] **Step 3: 修改已有剧本解析规则**

在 `input_mode == "script"` 的解析提示词中明确：

```text
完整剧本解析不得按固定镜头数量、5-10秒或单一核心动作压缩内容；当一个段落包含多个连续动作、对白阶段或状态变化时，必须拆成多个可拍摄场景，或完整保留动作链并标记承载风险。
```

只保留“每场一个核心动作”在新创作 `input_mode == "idea"` 的创作建议中。

- [ ] **Step 4: 运行解析定向测试**

运行：

```bash
pytest -q tests/test_script_analysis.py
```

预期：现有结构化脚本兼容测试通过，新增长动作和长对白测试通过。

- [ ] **Step 5: 做一次不落盘的《麦琪的礼物》解析对照**

使用当前 `script_draft_text` 调用 `ScriptAgent.analyze_existing_script()` 的测试桩或离线 fixture，不覆盖 `output/ea4f588be40e`；记录新旧场景数量、总时长和每镜负载，供 Task 6 对照。

---

### Task 3: 修复组装层进出方向和剧本外模板扩写

**Files:**
- Modify: `src/services/timed_prompt_composer.py:1645-1855, 1880-1960`
- Modify: `src/services/prompt_composer.py:40-110`
- Test: `tests/test_timed_prompt_composer.py`
- Test: `tests/test_prompt_policy.py`
- Test: `tests/test_generalization_regressions.py`

**Interfaces:**
- 新增内部函数：

```python
def detect_entry_exit_direction(event_text: str) -> Literal["enter", "exit", "none"]:
    ...
```

- 入口/出口模板只能消费当前镜头 `event_text`，不得读取下一镜剧情来推断方向；测试模块定义 `render_scene_prompt(event_text: str) -> str` fixture helper，将最小化 `Scene`/`ShotPrompt` 传入实际组装函数。
- 若方向为 `none`，不得输出进门、出门、对视或“街外入口”模板。
- 模板只允许输出当前剧本明确出现的角色、动作、地点和道具。

- [ ] **Step 1: 添加失败回归测试**

至少新增：

```python
def test_shop_entry_does_not_become_exit():
    prompt = render_scene_prompt("德拉推门进入头发店")
    assert "走出" not in prompt


def test_husband_enters_home_does_not_make_wife_exit():
    prompt = render_scene_prompt("吉姆推门进屋，站在门口盯着德拉")
    assert "德拉走出" not in prompt
    assert "吉姆进屋" in prompt


def test_no_explicit_gaze_does_not_add_eye_contact():
    prompt = render_scene_prompt("人物在柜台交接物品")
    assert "对视" not in prompt


def test_unrequested_flashback_is_not_added():
    prompt = render_scene_prompt("角色打开纸包，看到发梳并哭泣")
    assert "闪回" not in prompt
```

- [ ] **Step 2: 运行测试确认当前实现失败**

运行：

```bash
pytest -q tests/test_timed_prompt_composer.py -k "entry or exit or eye_contact or flashback"
```

预期：至少暴露当前“走出/对视/闪回”错误。

- [ ] **Step 3: 实现显式方向判断**

只将以下明确语义作为进入：`进入、走进、走入、推门进、踏入`；只将以下明确语义作为离开：`走出、离开、出门、退出`。`推门`单独出现时不得推断方向。模板输出的主语必须来自当前动作句。

- [ ] **Step 4: 删除固定叙事分句**

删除或改为条件输出：

```text
两人短暂对视
德拉走出
门铃轻响
把道具藏到身后
```

只有当前镜头原文明确存在时才允许写入。

- [ ] **Step 5: 运行定向测试和完整提示词回归**

运行：

```bash
pytest -q tests/test_timed_prompt_composer.py tests/test_prompt_policy.py tests/test_generalization_regressions.py
```

预期：所有新增测试通过，旧有正例（明确玻璃门/门铃/入口动作）继续通过。

---

### Task 4: 修复环境、角色状态和时间摘要污染

**Files:**
- Modify: `src/agents/visual_prompt_agent.py:195-202, 1214-1355, 1389-1438`
- Modify: `src/services/timed_prompt_composer.py:2144-2288`
- Test: `tests/test_visualize_robustness.py`
- Test: `tests/test_prompt_policy.py`

**Interfaces:**
- 新增内部清洗接口：

```python
def sanitize_shot_state(scene: Scene, shot: ShotPrompt) -> ShotPrompt:
    ...
```

- 环境段只允许地点固定结构、持续天气/光线和固定陈设；动态动作、心理、瞬时结果必须留在当前镜头动作或主体状态。
- 角色稳定锚点只保存跨镜不变的身份外观；长发/短发、服装变更等剧情状态进入当前镜头状态。

- [ ] **Step 1: 添加失败测试**

测试以下场景：

```text
前镜：窗台灰猫窜过，角色走到窗前；
当前镜：餐桌旁两人对话。
```

断言当前镜 `【环境】` 不含“灰猫窜过”“走到窗前”。

新增测试断言同一角色的稳定锚点不同时包含“长发”和“短发”；当前状态可以包含其中一个。

新增测试断言镜头 3 时长为 6 秒时，最终提示词不再同时出现 `7-8秒` 和 `4.5s-6s` 两套动作时间。

- [ ] **Step 2: 运行测试确认当前实现失败**

运行：

```bash
pytest -q tests/test_visualize_robustness.py tests/test_prompt_policy.py -k "environment or continuity or duration or hair"
```

- [ ] **Step 3: 隔离稳定资产和当前状态**

组装 `【出镜角色与身份锚点】` 时只读稳定外貌；将 `costume_states`、发型变化、当前服装和当前道具状态写入 `【主体状态】` 或 `【关键道具】`。禁止从前镜环境段复制动态句子。

- [ ] **Step 4: 规范化后只保留一份时间轴**

当 authored 时间轴被重平衡或 provider cap 改写后，删除旧的模型“动作意图”时间戳摘要，重新生成唯一的累计范围时间轴；不得同时保留原始 7-8 秒和修正后的 4.5-6 秒。

- [ ] **Step 5: 运行定向测试**

预期：环境动态污染、长短发锚点冲突和时间摘要冲突的测试全部通过，现有时间轴保留测试不回退。

---

### Task 5: 补齐对白、独白、电话和画外音的最终承载

**Files:**
- Modify: `src/services/timed_prompt_composer.py:454-479, 1808-1910`
- Modify: `src/agents/visual_prompt_agent.py:1165-1190, 1389-1421`
- Modify: `src/schemas/prompt.py`
- Test: `tests/test_prompt_policy.py`
- Test: `tests/test_native_dialogue_prompt.py`
- Create: `tests/test_dialogue_prompt_coverage.py`

**Interfaces:**
- 在最终提示词中增加稳定章节：

```text
【对白/声音】
```

- 可选结构字段：

```python
dialogue_audio: list[str]
voiceover: list[str]
```

如果不扩展持久化 schema，至少由组装层直接从 `scene.dialogue` 生成完整章节。
- `voice_only` 只影响画面可见性，不得删除完整台词文本。

- [ ] **Step 1: 添加失败测试**

使用《麦琪的礼物》第 6 镜独白，断言最终 prompt 包含完整“一元八角七”独白；使用电话/录音样例，断言完整声音内容存在且同时有“不出现新人物画面”的约束。

- [ ] **Step 2: 运行测试确认失败**

运行：

```bash
pytest -q tests/test_dialogue_prompt_coverage.py
```

预期：当前镜头 6 独白测试失败，证明问题来自最终承载而不是剧本解析字段。

- [ ] **Step 3: 实现对白声音章节**

对白按结构化剧本原顺序输出：`角色：“原文”`；旁白、电话、录音和画外音按声音来源输出，不把 voice-only speaker 写成画面人物；不再使用固定前三句或只返回声音形容词。

- [ ] **Step 4: 将对白时长纳入承载诊断**

对白章节保留完整内容；若对白时长超过当前镜头可承载量，交给 Task 6 报告拆镜建议，不删台词。

- [ ] **Step 5: 运行音频/对白相关回归**

运行：

```bash
pytest -q tests/test_dialogue_prompt_coverage.py tests/test_native_dialogue_prompt.py tests/test_prompt_policy.py
```

---

### Task 6: 新增通用镜头承载量分析器

**Files:**
- Create: `src/services/shot_capacity.py`
- Modify: `src/schemas/script.py`
- Modify: `src/schemas/prompt.py`
- Create: `tests/test_shot_capacity.py`

**Interfaces:**

```python
class ShotCapacityMetrics(BaseModel):
    scene_id: int
    duration_sec: float
    action_fact_count: int
    dialogue_char_count: int
    estimated_dialogue_sec: float
    prop_state_change_count: int
    camera_change_count: int
    emotional_turn_count: int
    complexity_flags: list[str] = []
    load_score: float
    recommended_shot_count: int
    recommended_duration_sec: float
    overloaded: bool


def analyze_shot_capacity(scene: Scene) -> ShotCapacityMetrics:
    ...


def analyze_script_capacity(script: ScriptBreakdown) -> list[ShotCapacityMetrics]:
    ...
```

- [ ] **Step 1: 写纯函数失败测试**

覆盖以下案例：

1. 单动作、无对白、8 秒 → `overloaded == False`、建议 1 镜；
2. 4 个连续动作、无对白、8 秒 → 至少标记高负载；
3. 长对白 + 4 个动作、8 秒 → `recommended_shot_count >= 2`；
4. 《麦琪的礼物》第 7、8、10、12 镜 → 标记过载；
5. 同义表达和标点变化不应导致动作数量暴增；
6. 仅有固定环境描述不应提高动作负载。

- [ ] **Step 2: 运行测试确认失败**

运行：

```bash
pytest -q tests/test_shot_capacity.py
```

预期：函数尚未实现，测试失败。

- [ ] **Step 3: 实现保守的事实计数**

只统计高置信信号：明确动作连接词、明确对白长度、道具状态变化词、明确景别/切镜词和情绪状态转折；不要使用题材、角色名或普通道具名作为过载依据。

对白估时使用固定、可复现的中文语速参数，并在报告中记录参数；不得通过删减对白让镜头变成“不超载”。

- [ ] **Step 4: 实现建议规则**

输出建议满足：

```text
recommended_shot_count >= 1
recommended_duration_sec >= scene.duration_sec
overloaded == load_score 超过阈值或对白估时明显超过可用时长
```

阈值必须集中定义并在测试中说明，不散落在模板代码中。

- [ ] **Step 5: 运行容量分析测试**

预期：所有纯函数测试通过，结果不依赖线上模型。

---

### Task 7: 接入解析结果和视频生成前诊断，不静默拆改

**Files:**
- Modify: `src/api/routes/jobs.py:265-320, 704-820, 1037-1279`
- Modify: `src/graph/orchestrator.py:35-75, 250-290`
- Modify: `web/js/pages/production-actions.js`（仅在确认不覆盖其他未提交 UI 改动后修改）
- Test: `tests/test_shot_capacity_gate.py`
- Test: `tests/test_video_generation_snapshot.py`

**Interfaces:**

```python
def build_storyboard_capacity_report(script: ScriptBreakdown) -> dict:
    ...
```

报告至少包含：

```json
{
  "status": "clean|warning|overloaded",
  "total_scene_count": 12,
  "total_duration_sec": 92,
  "overloaded_scene_ids": [7, 8, 10, 12],
  "items": [...]
}
```

- [ ] **Step 1: 添加门前诊断测试**

断言：

- 普通干净镜头可以生成；
- 高负载镜头返回 `warning/overloaded`，但不自动删对白；
- provider 超时长镜头要求拆分或明确确认，不把提示词截短后伪装为通过；
- 生成快照记录当前承载报告和实际请求时长。

- [ ] **Step 2: 运行测试确认失败**

运行：

```bash
pytest -q tests/test_shot_capacity_gate.py tests/test_video_generation_snapshot.py
```

- [ ] **Step 3: 接入解析完成后的诊断**

脚本解析完成后生成容量报告并持久化到项目状态；报告只提示，不自动改写用户剧本。若镜头结构字段不合法，仍按现有错误处理阻断。

- [ ] **Step 4: 接入视频生成前快照**

在单镜和批量生成前记录：当前镜头的容量状态、当前提示词签名、provider、model、resolution、aspect ratio、references 和实际提交时长。

- [ ] **Step 5: 前端展示最小提示**

只展示：

```text
第7镜承载过载：包含长对白和多个动作阶段，建议拆为3个镜头，当前仍保留全部内容。
```

本轮不自动改剧本、不强制调用 Kimi；是否继续生成沿用现有产品门禁决定。

- [ ] **Step 6: 运行接口和快照回归**

预期：现有资源、设置、视频选择和音频相关测试不回退。

---

### Task 8: 建立《麦琪的礼物》修复前后对照验收

**Files:**
- Create: `scripts/run_magi_prompt_regression.py`
- Create: `tests/test_magi_prompt_regression.py`
- Create: `tmp/magi_prompt_after_fix_20260901.json`（运行产物，不提交）
- Create: `tmp/magi_prompt_after_fix_20260901.md`（运行产物，不提交）

**Interfaces:**
- 回归脚本只使用固定剧本 fixture 和当前组装器；不调用 Kimi；可选调用 DeepSeek 只作为单独线上实验，不进入默认 CI。
- 对照结果同时保存旧版基线、新版提示词和确定性诊断。

- [ ] **Step 1: 添加验收断言**

至少断言：

```text
镜头4：有“进入/推门进店”，没有“德拉走出/无依据对视”
镜头6：完整独白和“一元八角七”存在
镜头7：有“吉姆进屋”，没有“德拉走出”
镜头10：有打开纸包到发梳反应链，没有无依据旧报纸闪回
镜头12：完整真相对白、表链、发梳、对视和相视而笑存在
所有镜头：不出现前镜动态环境污染
所有最终时间轴：没有超出当前镜头时长的旧时间摘要
```

- [ ] **Step 2: 运行失败基线**

先对当前版本运行，确保断言能捕获此前已确认的问题；不能直接把断言写成对当前错误的豁免。

- [ ] **Step 3: 逐项修复后运行验收**

运行：

```bash
python scripts/run_magi_prompt_regression.py --fixture output/ea4f588be40e/meta.json --out-dir tmp/magi_prompt_after_fix_20260901
pytest -q tests/test_magi_prompt_regression.py
```

预期：所有剧情保真断言通过，镜头数量和过载报告单独展示，不把“通过保真”误报成“镜头粒度已经合理”。

- [ ] **Step 4: 运行完整回归**

```bash
pytest -q
```

验收基线：当前全量 `912 passed, 2 skipped` 不得回退；新增测试通过后，报告新的总数。

---

## 验收标准

### 第一阶段：快速正反馈

- 《麦琪的礼物》镜头 4 不再把进入写成走出；
- 镜头 7 不再把吉姆进屋写成德拉走出；
- 不再无依据添加“两人短暂对视”；
- 镜头 10 不再无依据添加旧报纸闪回；
- 后半段环境不再出现前镜灰猫、走到窗前和泪痕动态；
- 最终时间轴不再同时保留两套冲突时长。

### 第二阶段：剧情内容完整

- 镜头 6 完整独白进入最终提示词；
- 所有结构化对白都能在最终提示词或明确的声音字段中定位；
- voice-only 不再等于丢失台词；
- 当前镜头不得出现下一镜动作、其他地点或未出镜角色。

### 第三阶段：镜头粒度透明

- 每镜生成承载报告；
- 高负载镜头明确给出建议镜头数和建议总时长；
- 过载时不静默删动作、对白或结尾状态；
- provider 时长上限触发时拆镜或明确报告，不只压缩时间轴；
- 能比较修复前后《麦琪的礼物》的保真度与镜头负载，而不是只比较总秒数。

## 实施顺序与停止点

1. 先执行 Task 1 建立基线；
2. 优先执行 Task 3、Task 4，获得最直接的提示词正反馈；
3. 执行 Task 5，解决对白明显缺失；
4. 执行 Task 2，降低解析层对压缩的诱导；
5. 执行 Task 6、Task 7，建立镜头承载量透明诊断；
6. 执行 Task 8，用《麦琪的礼物》做修复前后验收；
7. 如果 Task 8 仍显示镜头 7、8、10、12 过载，再另立“Scene → Shot/SubShot 拆分实现计划”，不在本计划中直接引入大规模自动拆镜。

遇到以下情况必须停止并报告，不得猜测：

- 生产状态字段与旧项目序列化结构不兼容；
- `web/js/pages/production-actions.js` 与其他未提交窗口改动发生冲突；
- provider 真实时长行为与当前快照记录不一致；
- 任何修复需要修改原始剧本或 Kimi 审核逻辑。

