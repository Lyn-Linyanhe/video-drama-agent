# 真实 DeepSeek 分镜提示词质量修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax and must be executed with a test checkpoint after each task.

**Goal:** 在不改写原始剧本、不调用 Kimi 的前提下，让结构化剧本中的剧情事实、角色状态、环境状态、对白和时间承载稳定进入最终分镜提示词，并能在视频生成前给出可复核的确定性诊断。

**Architecture:** 先修复“事实来源混层”问题：固定地点只提供静态环境，当前镜头只提供当前动作和状态，角色全局锚点只提供稳定身份，剧情变化写入本镜状态。然后在最终提示词组装完成后运行确定性保真审计和镜头承载量诊断；发现问题时优先返回证据和修复建议，不让组装层静默改写故事。最后使用同一份《麦琪的礼物》输入重新调用官方 DeepSeek，比较真实修复前后结果。

**Tech Stack:** Python 3.12、Pydantic、pytest、现有 `ScriptAgent`、`VisualPromptAgent`、`timed_prompt_composer`、JSON 项目状态、官方 DeepSeek OpenAI-compatible API。

**Spec:** 本计划中的 Task 2–6 接口与验收条款；真实测试证据：`tmp/real_magi_deepseek_after_fix_20260901.json`。

## 执行状态（2026-09-02）

- Task 1–7 的代码、测试和本地验证已完成，已合并到本分支提交 `52e0445`。
- 最新验证结果：`1013 passed, 2 skipped`；`compileall`、`git diff --check`、前端 UI 测试均通过。
- 离线复检证据：`tmp/magi_storyboard_quality_after_current_fix_20260902.json`；对比报告：`tmp/magi_storyboard_quality_comparison_20260902.md`。
- 当前复检结果：动态环境泄漏 `30 → 0`、角色状态冲突 `1 → 0`、对白覆盖率 `100%`、跨镜动态泄漏 `0`、时间轴冲突 `0`。
- Task 8 的官方 DeepSeek 新线上复测已实际发起，但于 2026-09-02 在 `script.analyze` 阶段返回 HTTP 402 `Insufficient Balance`；失败证据保存于 `tmp/real_magi_deepseek_after_fix_20260902.json`，未伪装成质量通过，也未重复消耗请求。
- 因此“代码/本地回归”已完成；“同一剧本的新线上模型输出对比”仍等待官方账户余额恢复后才能验收。Kimi、本轮视频生成和原始剧本均未改动。

## Global Constraints

- 不修改用户原始剧本正文，不自动改写 `script_draft_text`。
- 本轮不调用、不修改 Kimi 审核逻辑；Kimi 只在用户主动要求时运行。
- 不改变 DeepSeek 模型、官方端点和当前 `LLM_REASONING_EFFORT=high`。
- 不重算历史项目，不覆盖 `output/ea4f588be40e`，所有实验结果写入 `tmp/`。
- 保留已有 946 passed、2 skipped 的回归基线，新增测试不得降低通过数。
- 不用故事角色名、道具名或单一剧本的专用规则；《麦琪的礼物》只作为回归样本。
- 不把“分镜保真通过”误报成“视频模型一定能按提示词生成”；视频模型归因必须放在提示词层通过之后。
- 任何真实测试输出不得包含 API key、Authorization、完整请求头或敏感配置。
- 先完成确定性保真和承载诊断，再决定是否需要后续拆镜计划；本轮不做大规模自动拆镜。

## 真实测试中确认的基线问题

真实调用官方 `deepseek-v4-flash`、思考档位 `high` 的结果（总耗时约 435.68 秒）显示：

1. 剧本解析能够保留 15 条对白和“一元八角七”、吉姆进屋、发梳、表链、最终真相等关键事实，但 12 个输入段落被解析为 13 个场景，总时长变为 122 秒，镜头粒度和时长仍不稳定。
2. 最终提示词对白缺失为 0，入口方向错误和“旧报纸头条”污染本次为 0，说明之前的对白补回和入口模板修复有效。
3. `【环境】` 仍把“走到窗前”“灰猫窜过”“红地毯泪痕”等动态事实带入后续镜头；其中“走到窗前”在 9 个镜头出现。
4. 全局角色锚点同时包含长发和短发，不能表达卖发前后的互斥状态。
5. 默认视觉流程曾因角色侧写补偿请求报“人物侧写不完整”而失败；为继续评估提示词才临时关闭了可选补偿。因此必须修复默认路径，再做一次不带测试旁路的真实复测。
6. 部分提示词同时存在规范化后的时间段和旧的逐段秒数，虽然总时长对齐，但不利于审核和视频模型执行。

---

### Task 1：固定真实回归输入、指标和前后对照基线

**Files:**
- Create: `tests/fixtures/magi_storyboard_source_20260901.txt`
- Create: `scripts/run_storyboard_quality_check.py`
- Create: `tests/test_storyboard_quality_check.py`
- Preserve as evidence: `tmp/real_magi_deepseek_after_fix_20260901.json`
- Preserve as evidence: `tmp/real_magi_deepseek_after_fix_20260901_full.txt`

**Interfaces:**
- `scripts/run_storyboard_quality_check.py` 提供纯离线入口：
  ```python
  def audit_storyboard_quality(script: ScriptBreakdown, prompts: GenerationPrompts) -> dict: ...
  ```
- 线上实验另提供：
  ```python
  async def run_real_storyboard_generation(source_text: str, *, out_dir: Path) -> dict: ...
  ```
  该函数才允许调用 DeepSeek；默认 pytest 不调用网络。
- 指标必须按事实和文本内容计算，不能固定依赖“镜头 6/7/12”这类易变编号。

- [ ] **Step 1: 写失败测试**

为测试夹具定义事实断言：

```python
def test_quality_audit_reports_dynamic_environment_leak():
    result = audit_storyboard_quality(script, prompts)
    assert result["dynamic_environment_leaks"] >= 1


def test_quality_audit_reports_mutually_exclusive_character_states():
    result = audit_storyboard_quality(script, prompts)
    assert result["character_state_conflicts"] >= 1
```

- [ ] **Step 2: 运行测试确认基线能捕获真实问题**

运行：

```bash
pytest -q tests/test_storyboard_quality_check.py
```

预期：使用当前基线样本时，测试能够报告环境动态泄漏、长短发冲突和时间轴格式冲突；不能通过修改断言来豁免当前错误。

- [ ] **Step 3: 实现指标收集**

输出以下字段：

```text
scene_count
shot_count
script_duration_sec
prompt_duration_sec
dialogue_total
dialogue_covered
dynamic_environment_leaks
character_state_conflicts
direction_conflicts
cross_shot_leaks
timeline_conflicts
capacity_warnings
```

同时保存每一项的 `scene_id`、事实原文、提示词证据片段和证据章节。

- [ ] **Step 4: 运行离线基线并保存**

运行：

```bash
python scripts/run_storyboard_quality_check.py \
  --input tmp/real_magi_deepseek_after_fix_20260901.json \
  --out tmp/magi_storyboard_quality_reaudit_20260902.json
```

结果只写入 `tmp/`，不覆盖历史项目。历史基线指标已保存在 `tmp/magi_storyboard_quality_baseline_20260901.json`；由于原始 e2e 输入文件未随当前工作区保留，不再伪造可重复的基线命令。

- [ ] **Step 5: 提交独立基线**

```bash
git add tests/fixtures/magi_storyboard_source_20260901.txt scripts/run_storyboard_quality_check.py tests/test_storyboard_quality_check.py
git commit -m "test: capture real storyboard quality baseline"
```

---

### Task 2：修复地点环境的事实来源和动态污染

**Files:**
- Modify: `src/utils/visual_asset_manifest.py`（`DYNAMIC_ENVIRONMENT_CUES`、`_has_voice_or_action_cue`、`_clean_environment_description`、`_stable_environment_from_scenes`、`_common_environment_description`）
- Modify: `src/agents/visual_prompt_agent.py`（`_sanitize_environment`、`_assemble_compact_shot_prompt`）
- Modify: `src/schemas/prompt.py`（新增并持久化 `PromptAssemblyDiagnostic`）
- Create: `tests/test_prompt_context_isolation.py`
- Extend: `tests/test_visualize_robustness.py`
- Extend: `tests/test_asset_extraction_regressions.py`

**Interfaces:**
- 新增纯函数：
  ```python
  def extract_static_environment_facts(text: str) -> list[str]: ...
  ```
  只返回建筑结构、固定陈设、持续天气/光线和稳定空间关系。
- 新增组装函数：
  ```python
  def build_shot_environment_context(
      script: ScriptBreakdown,
      scene_id: int,
      environment_delta: str = "",
  ) -> str: ...
  ```
  来源只能是当前物理地点的稳定环境事实和本镜静态差异，不接收前镜动作文本。

- [ ] **Step 1: 写失败测试**

测试以下输入必须只保留静态事实：

```python
source = "破旧公寓起居室；走到窗前；窗台上一只灰猫窜过；红地毯上溅出泪痕；墙面斑驳；窗帘半掩"
assert extract_static_environment_facts(source) == [
    "破旧公寓起居室", "墙面斑驳", "窗帘半掩"
]
```

同时测试：

- `说话时视线仍偏向巷口` 被删除；
- `雨棚边缘雨水成线滴落` 保留；
- 同一句大小写或空白差异只保留一次；
- `location.canonical_name` 不和环境事实重复拼接；
- 后续镜头不能得到前镜的“灰猫窜过”“走到窗前”“泪痕溅落”。

- [ ] **Step 2: 运行失败测试**

```bash
pytest -q tests/test_prompt_context_isolation.py tests/test_visualize_robustness.py
```

预期：当前版本至少在环境污染断言上失败。

- [ ] **Step 3: 收紧解析层环境提取**

扩充动态/心理/动作识别，但不删除真正的持续环境事实。动态词至少覆盖：

```text
走到、走进、走出、停下、窜过、溅出、落泪、抬头、望向、视线、目光、盯着、凝视、
说话、担心、害怕、紧张、屏息、拿起、放下、推开、打开、关上、递给、握住、转身
```

句子拆分优先按句号、分号和换行；逗号只作为次级切分，避免把“深夜，室内冷光”错误拆散。恢复固定前缀时，只允许恢复静态名词短语，不恢复整句动作。

- [ ] **Step 4: 重写组装来源边界**

`_assemble_compact_shot_prompt` 不再直接拼接未经标记的：

```python
location.canonical_name + location.environment_description + shot.environment_delta
```

改为：

```python
stable_location_facts = extract_static_environment_facts(
    "；".join((location.identity_description, location.fixed_dressing, location.environment_description))
)
shot_static_delta = extract_static_environment_facts(shot.environment_delta)
environment = dedupe_preserving_order(stable_location_facts + shot_static_delta)
```

地点名称单独作为地点身份，不重复放入环境正文。不得从 `scene.action_description` 或 `prev_shot.environment_delta` 补环境。

- [ ] **Step 5: 在最终组装后再次清洗并保留诊断**

若环境差异包含动态片段，不静默吞掉后直接结束；记录：

```python
PromptAssemblyDiagnostic(
    scene_id=scene_id,
    dropped_facts=[...],
    forbidden_conflicts=[...],
    template_triggers=[...],
    status="warning" | "failed",
)
```

环境中仍出现动态片段时，状态为 `failed`，禁止把该提示词当作干净版本进入视频生成。

- [ ] **Step 6: 运行测试并提交**

```bash
pytest -q tests/test_prompt_context_isolation.py tests/test_visualize_robustness.py tests/test_asset_extraction_regressions.py
git add src/utils/visual_asset_manifest.py src/agents/visual_prompt_agent.py tests/test_prompt_context_isolation.py tests/test_visualize_robustness.py tests/test_asset_extraction_regressions.py
git commit -m "fix: isolate static environment facts from shot actions"
```

---

### Task 3：拆分角色稳定身份与本镜可变状态

**Files:**
- Modify: `src/agents/visual_prompt_agent.py`（`_canonical_character_registry`、`_assemble_compact_shot_prompt`、`_registry_needs_profile_repair`）
- Modify: `src/utils/visual_asset_manifest.py`（角色状态提取辅助函数）
- Create: `tests/test_character_state_prompting.py`
- Extend: `tests/test_scene_prompt_layers.py`

**Interfaces:**
- 新增纯函数：
  ```python
  def split_character_identity_and_state(
      character_name: str,
      stable_profile: str,
      scene_state_notes: str,
      scene_text: str,
  ) -> tuple[str, str]: ...
  ```
  返回 `(stable_identity, current_scene_state)`。
- 新增冲突检查：
  ```python
  def find_mutually_exclusive_character_states(
      script: ScriptBreakdown,
      character_name: str,
  ) -> list[dict]: ...
  ```

- [ ] **Step 1: 写失败测试**

使用通用的“状态变更”夹具，不把德拉写入实现规则：

```python
def test_global_profile_does_not_contain_conflicting_mutable_states():
    stable, current = split_character_identity_and_state(
        "角色A", "成年女性；褐色长发；短发；棕色外套", "当前为短发", "她短发站在门边"
    )
    assert "长发" not in stable
    assert "短发" not in stable
    assert "短发" in current
    assert "棕色外套" in stable
```

并测试：

- 只有一种发型且未发生状态变化时，可以保留在稳定身份；
- 服装、妆容、伤口、湿身、伪装等跨镜变化进入本镜状态；
- `appearance_mode != on_screen` 的角色不出现在画面角色锚点；
- 本镜前后状态不被错误写入当前镜头。

- [ ] **Step 2: 运行失败测试**

```bash
pytest -q tests/test_character_state_prompting.py tests/test_scene_prompt_layers.py
```

预期：当前长发/短发同时进入全局锚点的基线断言失败。

- [ ] **Step 3: 识别互斥状态**

不要按“德拉、吉姆”等名称写规则。按角色在全部场景中出现的外观短语建立类别：发型、服装、妆容、遮挡/伪装、伤口/污渍等。一个类别出现多个不同时态或互斥值时：

- 从 `global_character_descriptions_cn` 中移除该类别的可变值；
- 将当前场景中由 `visible_state_notes`、`scene.action_description` 明确支持的值写入 `【角色状态】`；
- 无法确定当前值时不猜测，只保留“保持与上一镜一致”的连续性描述。

- [ ] **Step 4: 调整模型契约**

修改视觉方案提示词，明确：

```text
全局角色描述只写跨镜稳定身份；会随剧情改变的发型、服装、妆容、伤口、湿身和伪装不得写入全局稳定锚点。
这些状态必须由对应镜头的角色状态或资产绑定 visible_state_notes 提供。
```

- [ ] **Step 5: 调整最终组装顺序**

最终提示词固定为：

```text
【出镜角色与身份锚点】稳定身份
【角色状态】本镜当前可见状态
【动作时间轴】本镜动作
```

禁止把 `global_character_descriptions_cn` 原文直接当作完整当前外观；必须经过状态拆分和当前镜头过滤。

- [ ] **Step 6: 运行测试并提交**

```bash
pytest -q tests/test_character_state_prompting.py tests/test_scene_prompt_layers.py tests/test_visual_style_prompts.py
git add src/agents/visual_prompt_agent.py src/utils/visual_asset_manifest.py tests/test_character_state_prompting.py tests/test_scene_prompt_layers.py
git commit -m "fix: separate stable character identity from scene state"
```

---

### Task 4：修复默认角色侧写补偿失败，不阻断可用分镜结果

**Files:**
- Modify: `src/agents/visual_prompt_agent.py`（`reextract_visible_characters`、`_registry_needs_profile_repair`、`_run_parallel_visualize`、`_run_single_shot`）
- Modify: `src/schemas/visual.py`（如需持久化诊断，增加可选 `profile_repair_status`；旧项目默认 `None`）
- Extend: `tests/test_visualize_robustness.py`
- Create: `tests/test_character_profile_repair_fallback.py`

**Interfaces:**
- `reextract_visible_characters` 保持现有异步入口，但失败时返回结构化结果而不是无上下文抛错：
  ```python
  CharacterProfileRepairResult(
      english: dict[str, str],
      chinese: dict[str, str],
      status: Literal["clean", "repaired", "partial", "failed"],
      errors: list[str],
  )
  ```
- 失败状态写入 telemetry：
  ```text
  character_profile_repair_status
  character_profile_repair_errors
  ```

- [ ] **Step 1: 写失败测试**

覆盖：

- DeepSeek 全局角色输出缺字段，但资产清单已有可用身份描述时，视觉生成返回提示词并标记 `partial`；
- 补偿请求 JSON 无效时，保留原始可用描述，状态为 `failed`，不伪装成 `clean`；
- 完全没有任何可用出镜角色身份时，仍然抛出明确错误；
- 该失败不会触发第二次整片视觉生成，也不会覆盖用户 prompt。

- [ ] **Step 2: 运行失败测试**

```bash
pytest -q tests/test_character_profile_repair_fallback.py tests/test_visualize_robustness.py
```

- [ ] **Step 3: 实现分级降级**

规则：

```text
有资产清单描述 + 模型侧写不完整：继续组装，标记 partial
有部分模型侧写 + 补偿失败：保留已验证字段，标记 failed/partial
完全没有可用出镜角色身份：阻断并给出配置/输入错误
```

这里不是静默忽略质量问题；最终诊断必须明确显示角色侧写未完成，供视频生成前确定性审计处理。

- [ ] **Step 4: 保证测试旁路和生产路径一致**

删除真实实验脚本中 `_repair_character_profiles=False` 的默认旁路；只有单元测试通过显式 stub 才能关闭补偿。生产和线上实验均使用默认参数，确保下一轮是真正的完整链路。

- [ ] **Step 5: 运行测试并提交**

```bash
pytest -q tests/test_character_profile_repair_fallback.py tests/test_visualize_robustness.py tests/test_scene_prompt_layers.py
git add src/agents/visual_prompt_agent.py src/schemas/visual.py tests/test_character_profile_repair_fallback.py tests/test_visualize_robustness.py
git commit -m "fix: degrade character profile repair without losing storyboard output"
```

---

### Task 5：统一最终时间轴并增加镜头承载量诊断

**Files:**
- Create: `src/services/shot_capacity.py`
- Modify: `src/schemas/script.py`
- Modify: `src/schemas/prompt.py`
- Modify: `src/services/timed_prompt_composer.py`
- Modify: `src/agents/visual_prompt_agent.py`
- Create: `tests/test_shot_capacity.py`
- Extend: `tests/test_timed_prompt_composer.py`

**Interfaces:**

```python
class ShotCapacityDiagnostic(BaseModel):
    scene_id: int
    duration_sec: int
    action_units: int
    dialogue_chars: int
    prop_state_changes: int
    camera_changes: int
    suggested_shot_count: int
    suggested_duration_sec: int
    status: Literal["ok", "warning", "overloaded"]
    reasons: list[str] = []


def analyze_shot_capacity(
    scene: Scene,
    prompt: ShotPrompt | None = None,
    provider_max_duration_sec: int | None = None,
) -> ShotCapacityDiagnostic: ...
```

- [ ] **Step 1: 写失败测试**

使用受控夹具覆盖：

- 8 秒内一个动作、短对白：`ok`；
- 8 秒内四个连续动作和 100 字对白：`warning` 或 `overloaded`；
- 12 秒镜头超过 Kling 10 秒上限：必须报告 provider 超限；
- 诊断不得删除动作、对白或把时长静默压缩成更短文本；
- `suggested_shot_count` 只给建议，不自动改写 `Scene`。

- [ ] **Step 2: 运行失败测试**

```bash
pytest -q tests/test_shot_capacity.py tests/test_timed_prompt_composer.py
```

- [ ] **Step 3: 实现动作/对白/道具/镜头变化计数**

动作按明确并列结构、时间段和句号分解；不使用简单关键词数量直接宣判复杂语义。对白按字符数和说话人数量计入，关键道具按状态变化计入，镜头按显式“切镜头/景别/机位变化”计入。

- [ ] **Step 4: 统一时间轴输出**

最终提示词只允许一种格式：

```text
0.00s-2.00s，...
2.00s-4.00s，...
```

`timed_prompt_composer` 在规范化后删除旧的“动作意图”时间摘要，避免同一镜同时保留 `7-8秒` 和 `4.5-6秒` 两套时间。对于原始模型已提供的有效时间轴，保留其动作内容，只转换为累计起止区间。

- [ ] **Step 5: 接入生成前诊断但不自动拆镜**

解析或提示词生成完成后，计算每镜诊断并持久化。用户看到：

```text
第 8 镜承载偏高：包含长对白、进屋、站定、凝视和解释动作，建议拆为 2-3 镜；当前内容未被删除。
```

只有超过所选 provider 的硬时长上限或存在结构错误时，才进入现有确定性阻断；普通 `warning` 不自动改剧本。

- [ ] **Step 6: 运行测试并提交**

```bash
pytest -q tests/test_shot_capacity.py tests/test_timed_prompt_composer.py tests/test_video_generation_snapshot.py
git add src/services/shot_capacity.py src/schemas/script.py src/schemas/prompt.py src/services/timed_prompt_composer.py src/agents/visual_prompt_agent.py tests/test_shot_capacity.py tests/test_timed_prompt_composer.py
git commit -m "feat: add transparent shot capacity and canonical timelines"
```

---

### Task 6：增加最终分镜提示词确定性保真审计

**Files:**
- Create: `src/services/prompt_fidelity_audit.py`
- Create: `src/schemas/prompt_fidelity.py`
- Modify: `src/agents/visual_prompt_agent.py`
- Modify: `src/api/routes/jobs.py`
- Create: `tests/test_prompt_fidelity_audit.py`
- Create: `tests/test_prompt_generation_gate.py`

**Interfaces:**

```python
class PromptFidelityIssue(BaseModel):
    issue_code: str
    severity: Literal["critical", "major", "minor"]
    scene_id: int
    fact_id: str = ""
    expected_excerpt: str
    prompt_excerpt: str
    prompt_section: str
    repair_action: Literal["edit_prompt", "edit_structure", "manual_review"]


def audit_prompt_fidelity(
    script: ScriptBreakdown,
    prompts: GenerationPrompts,
    *,
    capacity_by_scene: dict[int, ShotCapacityDiagnostic] | None = None,
) -> list[PromptFidelityIssue]: ...
```

- [ ] **Step 1: 写失败测试**

至少覆盖：

- 结构化对白在最终提示词缺失；
- `enter` 事实被提示词写成 `exit`；
- 当前镜头出现上一镜动态环境；
- 同一角色的互斥外观状态同时出现在全局锚点；
- 未来道具状态或剧本外闪回出现；
- 时间轴终点超过镜头时长；
- 资产引用无效；
- 正常的同义改写不误报 critical。

- [ ] **Step 2: 运行失败测试**

```bash
pytest -q tests/test_prompt_fidelity_audit.py tests/test_prompt_generation_gate.py
```

- [ ] **Step 3: 实现审计规则**

只对高置信事实做确定性判断：

```text
missing_dialogue
entry_exit_conflict
cross_shot_dynamic_leak
mutable_character_state_conflict
future_event_leak
invalid_asset_reference
timeline_overflow
scene_shot_mismatch
```

动作同义词、复杂叙事顺序和表演好坏不由关键词审计直接判定，留给后续人工/Kimi 工具。

每个问题必须带双侧证据：结构化剧本期望片段和实际提示词片段，并标明 `prompt_section`。没有证据时只能标记 `unreviewed`，不能自动生成严重问题。

- [ ] **Step 4: 在提示词生成后接入**

`VisualPromptAgent._split_output` 返回前运行审计并将诊断挂到本次生成结果；项目状态持久化时保存当前 prompt signature、诊断、容量报告和生成版本。

- [ ] **Step 5: 在视频生成前接入硬错误检查**

保持当前审核生命周期决策：

- 首次 Kimi 审核未完成仍按现有规则处理；
- 后续用户修改只提示，不因为 Kimi 报告过期而强制审核；
- 但当前版本的确定性 `critical` 结构错误仍阻止视频生成；
- 普通容量 `warning` 只提示，不阻止生成；
- `changed_since_review` 不因本次确定性 warning 自动变成禁止状态。

- [ ] **Step 6: 运行测试并提交**

```bash
pytest -q tests/test_prompt_fidelity_audit.py tests/test_prompt_generation_gate.py tests/test_prompt_review_gate.py
git add src/services/prompt_fidelity_audit.py src/schemas/prompt_fidelity.py src/agents/visual_prompt_agent.py src/api/routes/jobs.py tests/test_prompt_fidelity_audit.py tests/test_prompt_generation_gate.py
git commit -m "feat: audit final storyboard fidelity before video generation"
```

---

### Task 7：补齐前端诊断展示，不修改 Kimi 流程

**Files:**
- Modify: `web/js/pages/production-actions.js`
- Modify: `web/js/pages/production.js`（如果当前状态卡片位于此文件）
- Create: `tests/test_storyboard_quality_ui.mjs`

**Interfaces:**
- 前端消费后端返回的：
  ```json
  {
    "prompt_fidelity": {"status": "clean|warning|failed", "issues": []},
    "shot_capacity": {"items": []},
    "character_profile_repair": {"status": "clean|partial|failed"}
  }
  ```
- 不把这些字段映射成 Kimi 的 `pass`、`needs_review` 或 `stale`，避免两类审核语义混淆。

- [ ] **Step 1: 写 UI 失败测试**

断言以下文案和动作：

```text
提示词存在确定性保真问题：禁止生成
镜头承载偏高：建议拆镜，但当前内容未被删除
角色状态信息不完整：可继续查看，但建议补充
```

并断言 `changed_since_review` 仍显示“最近一次 Kimi 报告对应旧版本”，不显示“审核过期，禁止生成”。

- [ ] **Step 2: 运行失败测试**

```bash
node tests/test_storyboard_quality_ui.mjs
```

- [ ] **Step 3: 实现最小展示**

按三组展示：

1. 剧情保真问题；
2. 镜头承载提示；
3. 角色/资产上下文问题。

每项显示镜头、期望事实、实际提示词章节、建议动作。普通 warning 不增加重复确认弹窗。

- [ ] **Step 4: 运行测试并提交**

```bash
node tests/test_storyboard_quality_ui.mjs
git add web/js/pages/production-actions.js web/js/pages/production.js tests/test_storyboard_quality_ui.mjs
git commit -m "feat: show storyboard fidelity diagnostics"
```

---

### Task 8：用同一输入做真实 DeepSeek 修复后复测

**Files:**
- Modify: `scripts/run_real_magi_deepseek_20260901.py`（仅作为测试脚本，不进入生产代码）
- Create: `tmp/real_magi_deepseek_after_fix_20260901.json`
- Create: `tmp/real_magi_deepseek_after_fix_20260901_full.txt`
- Create: `tmp/magi_storyboard_quality_comparison_20260901.md`

**Interfaces:**
- 真实测试必须使用：
  ```text
  LLM_PROVIDER=deepseek
  LLM_MODEL=deepseek-v4-flash
  OPENAI_BASE_URL=https://api.deepseek.com/v1
  LLM_REASONING_EFFORT=high
  ```
- 测试脚本默认不设置 `_repair_character_profiles=False`，必须走和产品一致的默认链路。
- 不调用 Kimi、不生成视频、不写入 `output/`。

- [ ] **Step 1: 先运行修复前基线指标**

保存：

```text
解析耗时、视觉生成耗时、总耗时
解析场景数、最终镜头数、解析总时长、提示词总时长
对白覆盖率、环境动态泄漏数、角色状态冲突数
方向冲突数、跨镜污染数、时间轴冲突数、容量 warning 数
角色侧写补偿状态
```

- [ ] **Step 2: 运行修复后的完整真实链路**

```bash
python scripts/run_real_magi_deepseek_20260901.py \
  --source tests/fixtures/magi_storyboard_source_20260901.txt \
  --out tmp/real_magi_deepseek_after_fix_20260901.json
```

在执行该命令前，修改测试脚本加入 `argparse`，明确实现 `--source`（输入剧本路径）和 `--out`（结果 JSON 路径）；命令行参数必须与脚本实现一致，不能写成未支持的示例。

若官方 API 超时或返回无效 JSON，只记录失败原因和 telemetry，不用离线结果冒充真实通过。

- [ ] **Step 3: 对照实际结果**

验收目标：

```text
对白覆盖率 = 100%
结构化剧本与最终提示词总时长一致
entry_exit_conflict = 0
cross_shot_dynamic_leak = 0
mutable_character_state_conflict = 0
future_event_leak = 0
invalid_asset_reference = 0
timeline_overflow = 0
角色侧写补偿不再以可恢复缺陷阻断整条分镜生成
```

镜头数量不要求强行等于旧的 12；只要求拆分有结构化剧本事实依据、没有静默删除剧情，并把承载量独立报告出来。

- [ ] **Step 4: 编写对照报告**

报告必须明确区分：

```text
模型实际输出的问题
组装层新增的问题
确定性修复消除的问题
仍需人工判断的问题
```

不能把环境污染或角色状态冲突归因给视频生成模型。

- [ ] **Step 5: 运行完整回归**

```bash
pytest -q
```

预期：原有 946 passed、2 skipped 不回退；新增测试全部通过。

- [ ] **Step 6: 提交代码，不提交线上实验产物**

```bash
git status --short
git diff --check
git add src tests web scripts/run_storyboard_quality_check.py
git commit -m "fix: improve storyboard fidelity from real DeepSeek evidence"
```

`tmp/real_magi_deepseek_after_fix_20260901.json`、完整提示词和比较报告保留在本机，不提交包含大模型输出的仓库历史，除非后续明确需要。

---

## 执行顺序与优先级

### P0：最快看到正反馈

1. Task 2：环境事实隔离；
2. Task 3：长发/短发等互斥角色状态隔离；
3. Task 4：默认角色侧写补偿失败降级；
4. Task 8：用官方 DeepSeek 复测。

这四项直接对应真实测试中最影响成片的三个问题：后半段环境乱入、角色状态冲突、默认流程失败。

### P1：确保长期不再回归

5. Task 5：时间轴规范化和镜头承载诊断；
6. Task 6：最终提示词确定性保真审计；
7. Task 7：前端展示。

### 暂不做

- 不先改 DeepSeek 模型或思考档位；本次真实测试已证明模型可以保留关键对白和剧情事实。
- 不先改 Kimi 审核；用户已明确暂时不管 Kimi，且当前问题在 Kimi 之前就能通过组装层复现。
- 不自动拆所有高负载镜头；先报告承载量，避免系统替用户重新编剧。
- 不改原始剧本；剧本由用户负责。
- 不把视频模型生成失败直接归因于视频模型，必须先通过提示词层保真审计。

## 停止条件

出现以下任一情况时停止继续扩展并汇报，不猜测：

- 结构化剧本旧项目反序列化不兼容；
- 角色状态无法从当前剧本或资产绑定确定，只能靠模型猜测；
- 环境静态事实和动态事实无法通过现有字段区分，需要改动原始剧本数据结构；
- `web/js/pages/production-actions.js` 与其他窗口未提交改动冲突；
- 官方 DeepSeek 真实复测连续两次失败，且失败原因是外部 API 状态而非代码；
- 任何修复要求修改原始剧本或 Kimi 审核逻辑。

## 计划自审

- **问题覆盖：** 真实测试发现的环境污染、角色状态冲突、角色侧写补偿失败、镜头数量/时长不稳定和时间轴格式冲突，分别由 Task 2、3、4、5、8 覆盖。
- **质量闭环：** Task 1 固定指标，Task 2-7 修复与审计，Task 8 重新真实调用 DeepSeek，避免只靠单元测试宣称改善。
- **范围边界：** 没有改变 DeepSeek 配置、Kimi 逻辑、原始剧本或视频模型调用；后续修改门禁仍遵守“首次 Kimi 强制，后续改动只提示”的既定决策。
- **可回归性：** 所有核心问题都有失败测试、实现测试和真实复测指标；不依赖固定镜头编号，不会因解析拆分而误判。





