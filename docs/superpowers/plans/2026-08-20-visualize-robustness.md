# 分镜提示词质量与解析健壮性改进 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把“环境差异混入动作/状态”“JSON 结构化解析脆弱(围栏/缺字段/超时)”这两类实测问题压下去，让分镜提示词生成更稳定、可验证。

**Architecture:** 三层同时加固——(1) 收紧发给 DeepSeek 的提示词契约，把 `environment_delta` 明确为“仅静态环境事实”；(2) 在 `generate_structured` 兜底解析里补“剥围栏 + 缺字段默认填充”，承担模型偶发不合规；(3) 在组装层加确定性污染清洗，并在 DeepSeek 多镜场景复用现有逐镜 stepwise 路径降低超时与超大 JSON 风险。

**Tech Stack:** Python 3.12, FastAPI, pydantic v2, OpenAI-compatible LLM 客户端 (deepseek)。

**Spec:** 依据实测证据(见下文「证据」)与本仓库 `src/agents/visual_prompt_agent.py`、`src/utils/llm.py` 现状。

## Global Constraints

- 不改依赖与供应商；仅 DeepSeek(及现存 Kimi) 走 stepwise，其他 provider 行为不变。
- 不持久化任何测试生成结果；测试一律用 fake LLM/纯函数，禁止真实付费调用。
- 保持 `GenerationPrompts.prompt_template_version` 语义：stepwise 输出仍为 6。
- 提示词中文；负向约束、场景/角色/道具锚点来源不变。
- 旧项目、旧版本历史与运行中任务快照不得被本次改动破坏（改动只在生成/解析路径，不动存储 schema）。

---

## 证据（为何要改）

1. 环境混入动作/状态（提示词契约问题）：
   - 现状 `_assemble_compact_shot_prompt` 的【环境】= 场景静态锚点 + `shot.environment_delta`（`src/agents/visual_prompt_agent.py:1068-1072`）。
   - 系统提示只限“≤80 汉字、最多两句”（`visual_prompt_agent.py:475-477`），未定义“只能静态环境、禁止人物动作/在场”。
   - 真实 DeepSeek 调用（同《雨夜第七封信》15 镜）复现：scene 2 `environment_delta="便利店玻璃门刚被推开…"`、scene 7 `="通道尽头铁门被推开，黑衣人逆光站在入口…"`，属动作/在位混入环境；同批 scene 6 又干净。=> 偶发，边界没定死。

2. 结构化解析脆弱（模型偶尔不合规 + 我们解析不健壮）：
   - `generate_structured` 对 PLAIN_JSON 走 `_openai_structured_fallback`（`src/utils/llm.py:444-450, 529-573`），缺必填字段时只 `model_validate` 一次后直接 `raise ValueError("Fallback extraction failed")`。
   - 实测三条失败：模型把 JSON 用 ```json 围栏包裹；漏 `color_palette`；一次 300s 超时（`phase=script.visualize, model=deepseek-v4-flash`）。

3. 单次大 JSON 是超时主因：
   - 15 镜一屏全塞进一个超长结构化请求；`_run_stepwise`（`visual_prompt_agent.py:594-704`）已实现逐镜拆分且提供者无关，但目前仅 `provider==kimi` 时启用（`visual_prompt_agent.py:520`）。逐镜调用走回单镜 `run()`（1 镜）不会递归。=> 可扩展到 DeepSeek，且有小幅保底 bug：`visual_prompt_agent.py:689` 的 `overall_style or "鍐欏實"` 是乱码，改为“写实”。

---

## 文件结构

- 修改：`src/utils/llm.py`（generate_structured 解析健壮性）
- 修改：`src/agents/visual_prompt_agent.py`（提示词契约、环境清洗、stepwise 开关、乱码修复）
- 新增：`tests/test_visualize_robustness.py`（任务 1-4 的回归测试）
- 既有测试扩展：`tests/test_script_visualization_jobs.py`（stepwise 开关回归）

---

### Task 1: 结构化兜底解析健壮性（剥围栏 + 缺字段默认填充）

**Files:**
- Modify: `src/utils/llm.py:429-573`（`generate_structured` / `_openai_structured` / `_openai_structured_fallback`）
- Test: `tests/test_visualize_robustness.py::TestStructuredFallback`

**Interfaces:**
- Consumes: 现有 `generate_structured(system_prompt, user_prompt, output_schema, ...)`
- Produces: `generate_structured(..., schema_defaults: dict | None = None)`；`_openai_structured_fallback(..., schema_defaults=None)`。`schema_defaults` 仅在缺必填字段时补空/可用默认值。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_visualize_robustness.py
import json
from unittest.mock import AsyncMock, patch
from pydantic import BaseModel, Field

class Out(BaseModel):
    color_palette: str
    shots: list[int] = Field(default_factory=list)

@patch("src.utils.llm.LLMClient._openai_chat_content", new_callable=AsyncMock)
async def test_fallback_strips_fence_and_fills_missing_required(mock_chat):
    mock_chat.return_value = "```json\n{ \"shots\": [1,2] }\n```"
    from src.utils.llm import LLMClient
    client = LLMClient(); client.provider = "deepseek"; client.model = "deepseek-v4-flash"
    from openai import AsyncOpenAI
    client._openai = AsyncOpenAI(api_key="x")  # 仅占位，不真正调用
    out = await client._openai_structured_fallback(
        "sys", "user", Out, 0.0, schema_defaults={"color_palette": ""}
    )
    assert out.color_palette == "" and out.shots == [1, 2]
```

- [ ] **Step 2: 运行并确认失败**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_visualize_robustness.py::TestStructuredFallback -q`
Expected: FAIL —— 当前缺字段 `color_palette` 会抛 `ValidationError`，未被填充。

- [ ] **Step 3: 实现**

`src/utils/llm.py` 增加签名与逻辑：

```python
import re as _re  # 已有 import re 局部，此处沿用模块级

def _strip_json_fence(text: str) -> str:
    t = (text or "").strip()
    t = _re.sub(r"^```[a-zA-Z]*\s*", "", t)
    t = _re.sub(r"\s*```$", "", t)
    return t

async def _openai_structured_fallback(self, system_prompt, user_prompt, output_schema,
                                      temperature, *, phase="", telemetry=None,
                                      schema_defaults=None):
    """Last-resort: request plain JSON. Strip fences; fill missing required defaults."""
    import re as re
    schema_json = output_schema.model_json_schema()
    kwargs = {
        "model": self.model,
        "temperature": self._provider_temperature(temperature),
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
            {"role": "user", "content": (
                "IMPORTANT: Output ONLY valid JSON matching this schema, "
                f"no markdown, no extra text:\n{json.dumps(schema_json, ensure_ascii=False)}"
            )},
        ],
    }
    text = await self._openai_chat_content(kwargs, phase=phase, telemetry=telemetry)
    cleaned = _strip_json_fence(text)
    candidates = []
    try:
        candidates.append(json.loads(cleaned))
    except Exception:
        m = re.search(r"\{[\s\S]*\}", cleaned)
        if m:
            try: candidates.append(json.loads(m.group()))
            except Exception: pass
    for obj in candidates:
        if not isinstance(obj, dict):
            continue
        if schema_defaults and isinstance(schema_defaults, dict):
            for k, v in schema_defaults.items():
                if k not in obj:
                    obj.setdefault(k, v)
        try:
            return output_schema.model_validate(obj)
        except Exception:
            continue
    # 原样兼容旧路径
    try:
        return output_schema.model_validate_json(cleaned)
    except Exception:
        pass
    raise ValueError("Fallback extraction failed")
```

并将 `schema_defaults` 透传：`generate_structured(...)` 新增参数并传给 `_openai_structured`，`_openai_structured` 在 `PLAIN_JSON_STRUCTURED_PROVIDERS` 分支与末尾 fallback 处传入。

- [ ] **Step 4: 运行并确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_visualize_robustness.py::TestStructuredFallback -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/utils/llm.py tests/test_visualize_robustness.py
git commit -m "fix(llm): robust structured fallback (fence strip + schema defaults)"
```

---

### Task 2: 收紧分镜提示词契约（环境差异强制静态）

**Files:**
- Modify: `src/agents/visual_prompt_agent.py:469-482`（`COMPACT_SYSTEM_PROMPT`）、`:538-572`（user_prompt）、`:1162`、`:1200`（两个 `environment_delta` Field description）
- Test: `tests/test_visualize_robustness.py::TestEnvironmentContract`

**Interfaces:**
- Consumes: 现有 `_CompactShotVisualPrompt.environment_delta`
- Produces: 提示词与 schema 描述均明确“environment_delta 只写静态环境；无则空串”。

- [ ] **Step 1: 写失败测试**

```python
from src.agents.visual_prompt_agent import COMPACT_SYSTEM_PROMPT, _CompactShotVisualPrompt

def test_environment_contract_forbids_action():
    sys = COMPACT_SYSTEM_PROMPT
    assert "静态" in sys and "人物动作" in sys and ("在场" in sys or "动作、情绪" in sys)
    desc = _CompactShotVisualPrompt.model_fields["environment_delta"].description
    assert "静态" in desc and "空" in desc
```

- [ ] **Step 2: 运行并确认失败**

Expected: FAIL（现状 `COMPACT_SYSTEM_PROMPT` 与 field description 无“静态/禁止动作”表述）。

- [ ] **Step 3: 实现**

在 `COMPACT_SYSTEM_PROMPT` 追加明确规则：

```
- environment_delta 只能写本镜头静态/持续的环境事实（建筑结构、固定陈设、天气、时间、地形、空间关系、整体光线氛围）。严禁写入人物的动作、情绪、心理状态、对白、人物在场、道具交互或剧情结果。若无静态环境差异，environment_delta 必须留空字符串。
```

在 user_prompt（`:560-572` 附近）追加一条 bullet：

```
- environment_delta 只允许静态环境差异，禁止写入人物动作、情绪、在场或道具交互；没有静态差异时留空。
```

将两条 `environment_delta` 的 Field description 改为：

```python
environment_delta: str = Field(default="", description="仅静态/持续的环境差异（建筑、陈设、天气、地形、空间、整体光线）；禁止人物动作、情绪、在场、对白或道具交互；无则留空")
```

- [ ] **Step 4: 运行确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_visualize_robustness.py::TestEnvironmentContract -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/agents/visual_prompt_agent.py tests/test_visualize_robustness.py
git commit -m "feat(visualize): tighten environment_delta contract to static facts only"
```

---

### Task 3: 组装层确定性环境污染清洗 + 测试

**Files:**
- Modify: `src/agents/visual_prompt_agent.py:1063-1094`（`_assemble_compact_shot_prompt`）+ 新增模块级 `_sanitize_environment`
- Test: `tests/test_visualize_robustness.py::TestSanitizeEnvironment`

**Interfaces:**
- Consumes: 环境文本（静态锚点 + `environment_delta`）
- Produces: `_sanitize_environment(env: str) -> str`，删除含人物动作/在场/交互提示词的子句；空时返回 `""`。

- [ ] **Step 1: 写失败测试**

```python
from src.agents.visual_prompt_agent import _sanitize_environment

def test_sanitize_drops_action_clause_keeps_static():
    env = "便利店玻璃门刚被推开；雨幕中街对面停一辆深色轿车，车窗贴深色膜；门铃与雨声交织。"
    out = _sanitize_environment(env)
    assert "被推开" not in out
    assert "深色轿车" in out
    assert "门铃" in out

def test_sanitize_keeps_clean_env():
    env = "旧钟楼地下通道；墙上大片水痕，灯管部分损坏，地面浅水。"
    assert _sanitize_environment(env) == env

def test_sanitize_empty_when_all_action():
    assert _sanitize_environment("黑衣人站在入口。") == ""
```

- [ ] **Step 2: 运行并确认失败**

Expected: FAIL（`_sanitize_environment` 尚不存在）。

- [ ] **Step 3: 实现**

在文件顶部（`_current_prop_anchor` 附近）加：

```python
_ENV_ACTION_CUES = (
    "被推开", "推开", "踏入", "走进", "走入", "站在", "出现",
    "人影", "看到", "看向", "蹲", "冲进", "躲", "掏出", "放进",
    "示意", "转身", "掏出", "拿出", "黑衣", "人物",
)

def _sanitize_environment(env: str) -> str:
    """从环境文本中去掉人物动作/在场/交互子句，保留静态环境事实。"""
    if not env:
        return ""
    kept = []
    for clause in re.split(r"[。；;]", env):
        clause = clause.strip()
        if not clause:
            continue
        if any(cue in clause for cue in _ENV_ACTION_CUES):
            continue
        kept.append(clause)
    return "；".join(kept).strip()
```

改 `_assemble_compact_shot_prompt` 的【环境】拼接（`:1068-1072`）：

```python
raw_env = "".join([
    (f"{location.canonical_name}。" if (location and location.canonical_name) else (f"{scene.location}。" if scene.location else "")),
    f"{getattr(location, 'environment_description', '') or ''}。",
    f"{getattr(shot, 'environment_delta', '')}",
])
env_parts = [
    f"【环境】{_sanitize_environment(raw_env)}".strip()
]
```

（确保 `【环境】` 至少保留 canonical_name；若清洗后为空则只保留场景名，不产生空块。）

- [ ] **Step 4: 运行确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_visualize_robustness.py::TestSanitizeEnvironment -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/agents/visual_prompt_agent.py tests/test_visualize_robustness.py
git commit -m "feat(visualize): deterministic environment sanitizer in assembly"
```

---

### Task 4: DeepSeek 多镜复用 stepwise + 修复乱码兜底

**Files:**
- Modify: `src/agents/visual_prompt_agent.py:520`（dispatch 门槛）、`:689`（乱码）
- Test: `tests/test_visualize_robustness.py::TestStepwiseDispatch`、扩展 `tests/test_script_visualization_jobs.py`

**Interfaces:**
- Consumes: 现有 `_run_stepwise(script, prompt_overrides=..., creative_profile=..., ...)`
- Produces: provider ∈ {kimi, deepseek} 且 `len(scenes) > 5` 时走 stepwise；逐镜 `run()` 走回单镜路径，不递归。

- [ ] **Step 1: 写失败测试**

```python
import asyncio
from unittest.mock import AsyncMock, patch
from src.agents.visual_prompt_agent import VisualPromptAgent
from src.graph.state  # not used in unit; keep pure dispatch test via monkeypatch of run

def test_deepseek_many_scenes_uses_stepwise(monkeypatch):
    from src.utils.llm import get_llm_client
    class FakeLLM: provider = "deepseek"
    agent = VisualPromptAgent()
    agent.llm = FakeLLM()
    called = {}
    async def fake_run(script, **kw):
        called["n"] = called.get("n", 0) + 1
        # 最小对象：只需未触发递归；这里直接返回 None 表示“单镜路径命中”
        return None, None
    agent.run = fake_run  # stepwise 内部调单镜 run
    # 构造 ≥6 场景的 script 后调用 agent._run_stepwise
    # 注：真实断言以 pytest 注入 stub script 为准，见完整测试文件
```

> 说明：stepwise 需要真实 `ScriptBreakdown`。完整断言放到 `_run_stepwise` 的复用测试，用 `tests/test_script_visualization_jobs.py` 现有 kimi stepwise 用例平行复制一份 provider=deepseek、`len(scenes)>5` 的用例，并 `monkeypatch agent.run` 断言逐镜计次 == 场景数。

- [ ] **Step 2: 运行并确认失败**

Expected: FAIL/路径未变（deepseek 仍走单次大调用）。

- [ ] **Step 3: 实现**

`:520` 处：

```python
provider_now = getattr(self.llm, "provider", "")
if provider_now in {"kimi", "deepseek"} and len(script.scenes) > 5:
    return await self._run_stepwise(...)
```

`:689` 处把乱码兜底改为：

```python
overall_style=overall_style or "写实",
```

- [ ] **Step 4: 运行确认通过**

Run: `.\.venv\Scripts\python.exe -m pytest tests/test_visualize_robustness.py tests/test_script_visualization_jobs.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add src/agents/visual_prompt_agent.py tests/test_visualize_robustness.py tests/test_script_visualization_jobs.py
git commit -m "feat(visualize): enable stepwise for deepseek multi-scene; fix mojibake fallback"
```

---

## 回归

- 全量：`.\.venv\Scripts\python.exe -m pytest -q`
- 浏览器冒烟：启动后端加载任一已解析项目，确认分镜工作台仍可显示生成提示词；不改任何存储 schema，历史版本与生产快照不受影响。

## Self-Review（合理性核验）

1. **Spec 覆盖：** 每个实测缺陷都有任务对应——环境混入动作（Task 2 契约 + Task 3 清洗）；围栏/缺字段解析失败（Task 1）；300s 超时与超大 JSON（Task 4 stepwise）；乱码兜底（Task 4）。无遗漏。
2. **占位扫描：** Task 1-3 均含可运行代码与真实测试；Task 4 的 stepwise 断言依赖 `ScriptBreakdown`，已注明复用现有 kimi 用例扩展，不以空模块图蒙混。
3. **类型一致性：** `schema_defaults` 在 generate_structured/_openai_structured/_openai_structured_fallback 三处签名一致；`_sanitize_environment` 输入输出均为 str；stepwise 复用现有签名。
4. **边界风险：** Task 3 清洗用保守 cue 表，干净环境文本不变（有测试）；Task 4 阈值 `>5` 镜才拆分，短剧本保持单次，避免过度改动；其它 provider 行为不变。
5. **成本：** 测试用 fake；无新付费调用。stepwise 单镜请求更小，超时风险下降，token 总量近似。
