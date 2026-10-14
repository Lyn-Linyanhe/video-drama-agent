# 分镜执行提示词与对白链路重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不删除剧本事实的前提下，把最终提交给 HappyHorse 的分镜提示词改成“动作优先、对白可执行、参考图承担身份约束”的结构，并通过真实生成证明对白、动作和关键道具能够被模型正确执行。

**Architecture:** 保留完整剧本解析结果、完整分镜事实和审计证据；在生成 provider prompt 时新增一个面向模型执行的精简层。稳定人物身份由有效参考图承担，当前镜头的动作、状态、对白、镜头和关键道具进入高优先级核心块。子镜头物化时只保留本子镜头的事实和对白，不把父镜头或相邻子镜头内容泄漏进来。

**Tech Stack:** Python 3.12、Pydantic、FastAPI、pytest、HappyHorse `happyhorse-1.1-r2v`、ffprobe、faster-whisper（真实回归测试，不纳入默认 CI）。

**Spec:** 本计划基于 2026-09-06 对项目 `9559d0cd84ca` 分镜 8-1 的真实测试结果：使用 4 张相关参考图、6 秒提示词和核心对白块后，HappyHorse 返回的音频被 faster-whisper 识别为“你已经把头发剪掉了”，证明当前主要问题是提示词层次、对白绑定和参考图冗余，而不是模型完全不能生成对白。

## Global Constraints

- 不修改原始剧本、`ScriptBreakdown` 或历史视频；只修改分镜提示词组装、子镜头物化、参考图选择和回归测试。
- 不调用或依赖 Kimi 审核；本轮只做确定性提示词结构和真实 HappyHorse 验证。
- 不强行缩短所有提示词；只删除参考图已经承担的稳定身份冗余、父镜头泄漏和模型不可执行的审计/规划文本。
- 不删除动作、对白、道具、情绪转折或连续性事实；任何丢弃必须进入 `PromptAssemblyDiagnostic.dropped_facts`，且只有“稳定身份已由有效参考图覆盖”可以按规则标记为有意省略。
- 对白必须带说话人、完整原文和当前子镜头绑定；不允许用字幕替代人声，也不允许把相邻子镜头对白写进当前镜头。
- HappyHorse 生成时长保持在当前能力范围 4–15 秒；不得通过截断对白或压缩关键动作来适配时长。
- 真实 API 测试产生的文件放入独立测试版本目录，不覆盖正式激活视频，不把 API key 写入仓库、日志或测试输出。

---

## 问题基线与已确认证据

1. 当前正式分镜 8-1/8-2/8-3 的 MP4 均有 AAC 音轨，但 faster-whisper 无法识别出目标对白；项目元数据显示 native audio 为 `best_effort`，外部 TTS 尚未生成。
2. 当前正式 8-1 的 provider prompt 已包含“吉姆：你已经把头发剪掉了？”，但对白位于长提示词后部，且父镜头中存在大量人物身份、环境和跨镜头信息。
3. 使用用户给出的单镜头动作时间轴、顶部关键对白块和 4 张相关参考图的独立测试，实际识别出正确对白“你已经把头发剪掉了？”。
4. 因此验收重点不是继续增加规则，而是：
   - 将核心动作和当前对白放到 provider prompt 的最高优先级位置；
   - 让参考图真正承担稳定人物身份；
   - 让子镜头严格只携带自己的动作和对白；
   - 保留完整事实在结构化数据和审计层，而不是把所有事实原样堆给视频模型。

---

## 文件边界

### 将修改

- `src/services/timed_prompt_composer.py`
  - 负责最终执行提示词的章节顺序、时间轴、对白核心块、执行禁止项和冗余清洗。
- `src/services/shot_prompt_materializer.py`
  - 负责父镜头到子镜头的动作、对白、禁止项和连续性范围隔离。
- `src/agents/visual_prompt_agent.py`
  - 负责稳定身份文本是否进入 provider prompt、当前镜头上下文、参考图覆盖诊断和最终提示词组装。
- `src/schemas/prompt.py`
  - 为执行提示词诊断、对白绑定和身份参考覆盖增加可追踪字段；兼容旧项目读取。
- `src/api/context.py`
  - 在生成快照中记录最终执行提示词的层次、有效参考图、被有意省略的稳定身份段和对白计划。
- `src/services/prompt_composer.py`
  - 只保留 provider 级风格/质量包装，禁止再次重建角色身份、场景级对白和动作时间轴。
- `tests/test_timed_prompt_composer.py`
  - 扩展提示词章节顺序、对白位置、身份锚点清洗和跨镜头隔离测试。
- `tests/test_prompt_execution_structure.py`
  - 验证从结构化剧本到子镜头 provider prompt 的完整链路。
- `tests/test_video_generation_snapshot.py`
  - 验证参考图数量、参考图类型、对白计划和 provider prompt 进入快照。
- 新建 `src/services/prompt_execution_context.py`
  - 集中实现执行提示词的核心块、参考图覆盖判断和稳定身份省略规则，避免继续把逻辑堆进超大的 composer 文件。
- 新建 `tests/test_prompt_execution_context.py`
  - 覆盖新执行层的纯函数行为。
- 新建 `scripts/run_happyhorse_dialogue_benchmark.py`
  - 只用于手动真实 HappyHorse 回归，不纳入默认 CI；输出音轨、转写、参考图和提示词摘要，不输出认证信息。

### 不修改

- 原始剧本文本和 `ScriptBreakdown` 内容。
- Kimi 审核服务和审核门禁。
- `src/api_clients/happyhorse.py` 的模型调用协议；本计划只修正传入 prompt、参考图和音频请求内容。
- 历史项目正式视频文件；历史结果仅作为回归对照。

---

## 目标执行提示词结构

最终 provider prompt 应采用下列顺序。顺序是模型执行优先级，不代表删除结构化剧本事实：

```text
【本镜执行核心】
镜头目标：本镜必须完成的单一叙事动作和情绪变化。

【动作时间轴】
0-1.5s：……
1.5-4.5s：……
4.5-6s：……

【关键对白（必须出现）】
4.8-6s，吉姆看着德拉，清楚自然地说：
“你已经把头发剪掉了？”
嘴型与声音同步；不要用字幕代替人声。

【当前镜头角色与道具】
只列本镜实际出镜角色、当前动作状态和关键道具位置；不重复完整人物档案。

【镜头与表演】
景别、机位、视线、运动、表情变化和构图。

【环境与视觉风格】
地点、当前静态环境差异、光线、色调和画幅。

【参考图约束】
人物身份、场景和道具外观以已绑定参考图为准；保持一致。

【本镜禁止】
只写当前镜头真正需要禁止的动作、额外对白、额外人物、额外地点和状态跳变。
```

以下内容默认不进入 provider prompt，但继续保留在结构化数据、生成快照或诊断中：

- 完整稳定人物档案；
- 与当前镜头无关的角色视图；
- 父镜头其他子镜头的对白和动作；
- “预计完整执行需要约 XX 秒”的审计解释；
- Kimi 或内部审核过程文本；
- 已被参考图覆盖的发色、脸型、身高等稳定身份描述。

如果参考图缺失、无效、未通过资产门禁或未被实际传入，则恢复必要的身份文本，并在诊断中标记 `identity_reference_fallback`。

---

## Task 1: 建立执行提示词上下文和章节契约

**Files:**
- Create: `src/services/prompt_execution_context.py`
- Modify: `src/schemas/prompt.py`
- Test: `tests/test_prompt_execution_context.py`

**Interfaces:**

```python
class PromptExecutionContext(BaseModel):
    scene_id: int
    shot_id: str | None = None
    core_intent: str
    action_timeline: str
    dialogue_block: str = ""
    current_characters: list[str] = Field(default_factory=list)
    current_props: list[str] = Field(default_factory=list)
    camera_block: str = ""
    environment_block: str = ""
    reference_block: str = ""
    forbidden_block: str = ""
    intentionally_omitted_sections: list[str] = Field(default_factory=list)
    identity_reference_fallback: bool = False
```

```python
def build_execution_context(
    *,
    shot: ShotPrompt,
    scene: Scene | None,
    valid_references: list[dict] | None = None,
) -> PromptExecutionContext:
    ...
```

```python
def render_execution_prompt(context: PromptExecutionContext) -> str:
    ...
```

- [ ] **Step 1: 写失败测试**
  - 输入一个包含长人物身份段、当前动作和一条对白的 `ShotPrompt`。
  - 断言渲染结果以 `【本镜执行核心】` 开头。
  - 断言 `【关键对白（必须出现）】` 出现在环境、风格和禁止块之前。
  - 断言有效角色参考图存在时，不出现完整 `【出镜角色与身份锚点】`。
  - 断言参考图无效时出现短身份回退块并设置 `identity_reference_fallback=True`。

- [ ] **Step 2: 运行失败测试**

```bash
pytest -q tests/test_prompt_execution_context.py -v
```

预期：接口尚不存在或断言失败。

- [ ] **Step 3: 实现最小上下文和渲染器**
  - 只从当前镜头提取动作、当前状态、当前对白、当前关键道具和镜头语言。
  - 稳定身份只在 `valid_references` 为空或无效时进入回退块。
  - 不做全文字符串关键词匹配来判断复杂动作是否覆盖。

- [ ] **Step 4: 运行测试**

```bash
pytest -q tests/test_prompt_execution_context.py -v
```

预期：全部通过。

- [ ] **Step 5: 检查兼容性**
  - 用旧版没有新字段的 `meta.json` 读取 `PromptExecutionContext`，确认默认值可用。
  - 用真实分镜 8-1 构建一次上下文，检查只选择场景、两名出镜角色和白金表链。

---

## Task 2: 重构最终提示词章节顺序，不再让身份锚点压住动作和对白

**Files:**
- Modify: `src/services/timed_prompt_composer.py:3244-3439`
- Modify: `src/agents/visual_prompt_agent.py:2090-2330`
- Modify: `src/services/prompt_composer.py:155-190`
- Modify: `src/services/prompt_execution_context.py`
- Test: `tests/test_timed_prompt_composer.py`
- Test: `tests/test_prompt_execution_structure.py`

**Interfaces:**

```python
def build_timed_shot_prompt(
    shot: ShotPrompt,
    script: ScriptBreakdown | None = None,
    *,
    sync_script_duration: bool = True,
    duration_cap_sec: float | None = None,
    valid_references: list[dict] | None = None,
) -> str:
    ...
```

- [ ] **Step 1: 写回归测试**
  - 构造分镜 8-1 的动作时间轴、角色和一条对白。
  - 断言最终 prompt 包含完整动作顺序：吉姆推门、站在门口、德拉起身、走近、表情变化、吉姆对白。
  - 断言对白带说话人，且位于核心执行块之后、环境/风格之前。
  - 断言不出现父镜头其它三条对白。
  - 断言有效参考图存在时不出现大段角色身份锚点。
  - 断言不会因为删除身份冗余而删除“白金表链被德拉握在手中”这类当前镜头事实。

- [ ] **Step 2: 运行回归测试确认当前行为失败或暴露问题**

```bash
pytest -q tests/test_timed_prompt_composer.py tests/test_prompt_execution_structure.py -v
```

记录现有章节顺序和失败断言，不修改测试来迁就旧行为。

- [ ] **Step 3: 改造组装层**
  - 将当前镜头核心动作和对白放入 `PromptExecutionContext`。
  - 将稳定身份段从 provider prompt 移出；在生成快照保留 `intentionally_omitted_sections` 和参考图覆盖证据。
  - 保留项目视觉、环境、镜头和当前道具状态，但去重静态重复句。
  - 将“时间容量诊断”等内部规划文案从 provider prompt 移除，保留诊断字段。
  - 禁止把未来动作、其他地点、未出镜角色或父镜头对白拼进当前镜头。

- [ ] **Step 4: 运行测试**

```bash
pytest -q tests/test_timed_prompt_composer.py tests/test_prompt_execution_structure.py -v
```

- [ ] **Step 5: 审核提示词样本**
  - 保存麦琪 8-1、8-2、8-3 的旧/新 provider prompt 对照。
  - 逐项确认：动作没有减少、对白没有减少、关键道具没有减少、稳定身份重复文本减少、当前镜头范围更清晰。

---

## Task 3: 修复子镜头物化，让对白和动作真正按子镜头隔离

**Files:**
- Modify: `src/services/shot_prompt_materializer.py:14-147`
- Modify: `src/services/timed_prompt_composer.py` 中对白和时间轴渲染函数
- Test: `tests/test_prompt_execution_structure.py`
- Test: `tests/test_timed_prompt_composer.py`

**Interfaces:**

```python
def _dialogue_section(
    scene: Scene | None,
    dialogue_ids: Iterable[str],
    *,
    include_timing: bool = True,
) -> str:
    ...
```

```python
def materialize_shot_prompts(
    prompts: GenerationPrompts,
    script: ScriptBreakdown,
    *,
    duration_cap_sec: float | None = None,
    duration_options: Iterable[int | float] | None = None,
    provider_min_duration_sec: int | float = 3,
) -> list[ShotPrompt]:
    ...
```

- [ ] **Step 1: 写失败测试**
  - 父镜头含 4 条对白，子镜头 1 只绑定 8:1，子镜头 2 绑定 8:2/8:3，子镜头 3 绑定 8:4。
  - 断言每个子镜头只出现自己的对白。
  - 断言子镜头 1 的动作时间轴不包含子镜头 2/3 的动作。
  - 断言禁止块只写通用范围禁止，不把未来完整对白原文塞进负面提示词。
  - 断言对白紧邻当前动作时间轴，而不是追加到长提示词末尾。

- [ ] **Step 2: 实现子镜头核心块**
  - 先去除父级对白段和规划诊断段。
  - 按 `unit.beat_ids` 取当前动作事实。
  - 按 `unit.dialogue_ids` 取当前对白，保持原文、speaker、emotion 和顺序。
  - 使用统一的 `render_execution_prompt` 将动作和对白放入核心块。
  - 连续性只追加上一子镜头的结束状态，不追加上一子镜头的全部叙事文本。

- [ ] **Step 3: 运行测试**

```bash
pytest -q tests/test_prompt_execution_structure.py tests/test_timed_prompt_composer.py -v
```

- [ ] **Step 4: 用麦琪分镜 8 做静态验收**
  - 输出 8-1/8-2/8-3 的 dialogue_ids、prompt 中对白块和动作块。
  - 若某个子镜头对白为空，报告为结构问题，不生成“看起来有对白”的提示词。

---

## Task 4: 让对白时长估算服务于合理拆镜，而不是制造夸张总时长

**Files:**
- Modify: `src/agents/visual_prompt_agent.py:573-671`
- Modify: `src/services/shot_prompt_materializer.py` 的时长校验
- Modify: `src/schemas/prompt.py` 的诊断字段，新增执行层字段并为旧项目提供默认值
- Test: `tests/test_prompt_execution_structure.py`
- Test: `tests/test_timed_prompt_composer.py`

**设计规则:**

- 中文对白估算以实际可听语音为基准，不把人物身份、镜头说明、情绪说明计入对白时长。
- 初始估算使用“有效汉字数 / 4.2–4.8 字每秒 + 标点停顿 + 最小起止缓冲”的范围，而不是固定把所有文本按字符总量线性放大。
- 当前镜头有一条短对白时，允许与动作并行，不把动作时长和对白时长简单相加。
- 当前镜头有多个互相打断或轮流说话的对白时，按顺序累加，并在同一子镜头超过 15 秒时按对白边界拆镜。
- 不拆断单句对白；只能在明确标点、说话人切换或动作完成点拆分。
- 任何时长扩展必须保留 `source_duration_sec`、`recommended_total_duration_sec`、`recommended_durations_sec` 和扩展理由。

- [ ] **Step 1: 写时长回归测试**
  - 8-1：一条“你已经把头发剪掉了？”不应诊断为 20 秒以上。
  - 8-2：两人轮流两句对白应被识别为高负载，但不能把整段父镜头全部计入子镜头。
  - 8-3：长对白应建议独立子镜头或合理扩时，不能压进 3 秒。
  - 动作和短对白并行时，预计时长不能机械相加。

- [ ] **Step 2: 实现估算和拆镜边界**
  - 将对白时长、动作时长、镜头切换、状态变化作为可解释的独立项。
  - 由 `beat_planner` 先按动作/对白边界规划子镜头，再由 provider 上限校验。
  - 若只差少量时间，优先在合法范围内扩时；若超过 15 秒，优先拆镜，不截断。

- [ ] **Step 3: 运行测试**

```bash
pytest -q tests/test_prompt_execution_structure.py tests/test_timed_prompt_composer.py -v
```

- [ ] **Step 4: 对照麦琪旧诊断**
  - 记录 8-1、8-2、8-3 的旧/新 `expected_duration_sec`、`recommended_durations_sec`。
  - 人工确认新结果没有把对白时长夸大，也没有把真实长对白压成 3 秒。

---

## Task 5: 参考图选择改成“当前镜头最小充分集合”

**Files:**
- Modify: `src/api/context.py:2104-2117` 及相关参考图快照函数
- Modify: `src/services/media_quality.py` 中参考图资格判断（仅补充诊断，不改变已有资产门禁）
- Create: `src/services/reference_bundle.py`
- Test: `tests/test_video_generation_snapshot.py`
- Test: `tests/test_prompt_execution_structure.py`

**设计规则:**

- 当前镜头必须优先包含：场景参考图、当前出镜角色、当前关键道具。
- 同一角色默认只选一张最匹配当前镜头视角的参考图；只有当前动作确实需要身份/姿态补充时才增加第二张。
- 不把未出镜角色、其他镜头道具、所有历史视图和无关连续性图自动塞入当前镜头。
- 首尾帧/连续性尾帧单独计入，不混淆为普通角色或道具参考图。
- 最终快照必须记录：选择理由、被丢弃的参考图、实际发送顺序和路径。

- [ ] **Step 1: 写参考图测试**
  - 分镜 8-1 预期选择 4 张左右：场景、吉姆、德拉、白金表链。
  - 断言未出镜角色和无关道具不进入 `references`。
  - 断言每个路径存在且不超过 HappyHorse 的 9 张上限。
  - 断言参考图不足时设置身份文本回退，而不是静默生成。

- [ ] **Step 2: 实现最小充分集合**
  - 从 `shot.dialogue_ids`、当前动作事实和 `scene.characters/props` 推导候选资产。
  - 依据当前 camera/view_type 选择视角最匹配的版本。
  - 保存 `reference_selection_reasons` 和 `dropped_references`。

- [ ] **Step 3: 运行测试**

```bash
pytest -q tests/test_video_generation_snapshot.py tests/test_prompt_execution_structure.py -v
```

---

## Task 6: 接入生成快照并保持旧项目兼容

**Files:**
- Modify: `src/api/context.py`
- Modify: `src/api/routes/jobs.py` 生成快照调用处
- Modify: `src/schemas/prompt.py`
- Test: `tests/test_video_generation_snapshot.py`

- [ ] **Step 1: 写快照测试**
  - 断言快照同时保存：
    - `provider_prompt`；
    - `dialogue_plan`；
    - `references`；
    - `reference_selection_reasons`；
    - `intentionally_omitted_sections`；
    - `identity_reference_fallback`；
    - `prompt_template_version`。
  - 旧快照缺字段时读取不报错，并以兼容默认值处理。

- [ ] **Step 2: 实现快照字段**
  - 不让 provider prompt 成为唯一事实来源。
  - 将完整结构化剧本事实和执行层提示词分别保存。
  - 生成失败时仍保留请求前快照，便于判断是提示词、参考图还是模型结果问题。

- [ ] **Step 3: 运行测试**

```bash
pytest -q tests/test_video_generation_snapshot.py -v
```

---

## Task 7: 真实 HappyHorse 回归测试

**Files:**
- Create: `scripts/run_happyhorse_dialogue_benchmark.py`
- Test data: `tmp/` 或 `output/<project>/assets/videos/shot_008/dialogue-test-*`
- No production source changes in this task.

- [ ] **Step 1: 实现手动 benchmark**
  - 固定麦琪分镜 8-1 的四张最小充分参考图。
  - 生成两个版本：
    1. 旧 provider prompt；
    2. 新执行 prompt。
  - 记录：版本、prompt 摘要、参考图数量/类型、时长、音轨、ASR 转写、ASR 置信度、文件路径。
  - 认证字段只从环境变量读取，输出中禁止出现 key。

- [ ] **Step 2: 运行真实测试**

```bash
python scripts/run_happyhorse_dialogue_benchmark.py --project-id 9559d0cd84ca --scene-id 8 --shot-id scene-8-shot-1
```

- [ ] **Step 3: 验收门槛**
  - 新 prompt 至少一次识别出目标对白或得到人工确认的人声对白。
  - 新 prompt 不得丢失推门、站门口、德拉起身、走近、表链和表情变化。
  - 旧 prompt 仅作为对照，不因旧结果失败而判定新流程失败。
  - 若 API 结果随机，至少生成 3 个 seed/候选，报告成功率而不是只展示单个样本。

---

## Task 8: 全量测试、回归审查和发布前检查

**Files:**
- All files from Tasks 1–7
- Update: `docs/superpowers/plans/` only if acceptance findings require plan amendment

- [ ] **Step 1: 运行针对性测试**

```bash
pytest -q \
  tests/test_prompt_execution_context.py \
  tests/test_prompt_execution_structure.py \
  tests/test_timed_prompt_composer.py \
  tests/test_video_generation_snapshot.py
```

- [ ] **Step 2: 运行原有提示词和拆镜回归测试**

```bash
pytest -q tests/test_prompt_coverage_audit.py tests/test_prompt_review_v2.py tests/test_prompt_review_gate.py
```

Kimi 测试只验证数据结构和兼容性，不调用线上 Kimi。

- [ ] **Step 3: 运行全量测试**

```bash
pytest -q
```

- [ ] **Step 4: 生成静态对照报告**
  - 麦琪分镜 8-1/8-2/8-3：旧 prompt、新 prompt、动作事实、对白事实、参考图列表、时长诊断。
  - 对每个子镜头标记：动作是否完整、对白是否完整、关键道具是否保留、是否有跨镜泄漏。

- [ ] **Step 5: 做一次人工审核**
  - 不只看 prompt 是否变短，而是逐句问：
    1. 这镜要表现什么，模型能否一眼看到？
    2. 当前对白是谁说的，是否在核心块？
    3. 参考图是否已经承担身份约束？
    4. 关键道具是否有明确动作和位置？
    5. 是否把下一镜内容混入本镜？
    6. 时长是否足以完成动作和对白？

- [ ] **Step 6: 交付前结论**
  - 只有当静态测试通过、真实 HappyHorse 至少验证一次对白、且快照证据完整时，才把新模板用于正式项目重生成。
  - 历史视频不重算；新模板通过后，再单独重生成麦琪分镜 8 的测试版本。

---

## 验收标准

1. 有效参考图存在时，最终 provider prompt 不再被长篇稳定身份锚点占据；完整身份信息仍在结构化数据和快照中可追溯。
2. 当前子镜头的对白出现在核心执行块中，带说话人、完整原文、时间范围和人声同步要求。
3. 子镜头之间没有对白、动作、道具状态和未来事件泄漏。
4. 关键动作、情绪转折、白金表链等当前镜头事实不因提示词精简而丢失。
5. 长对白不会被压进 3 秒；短对白不会被估算成不合理的超长时长。
6. 参考图为当前镜头的最小充分集合，并且最终发送顺序可审计。
7. 真实 HappyHorse 测试中，新 prompt 至少能够重复得到目标对白或明确的人声结果；测试输出能区分“有音轨”和“有正确对白”。
8. 旧项目可以读取新字段缺省值，历史项目不会因为新模板升级而被静默改写。
9. 任何提示词事实变化都可以从 `provider_prompt`、`dialogue_plan`、`references` 和诊断字段中复核。

## 预计实施顺序

```text
先做执行上下文与纯函数测试
→ 重排提示词章节
→ 修复子镜头对白隔离
→ 校准对白/动作容量
→ 收紧参考图选择
→ 接入快照
→ 真实 HappyHorse A/B
→ 全量测试和麦琪回归
```

本计划不包含：修改剧本、重跑全片、Kimi 审核、视频模型 API 协议改造、音频后期 TTS 全链路改造。




