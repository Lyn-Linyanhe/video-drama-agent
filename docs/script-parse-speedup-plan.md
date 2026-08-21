# 完整剧本解析提速方案与 A1 验证记录

> 状态：调研完成，结构性提速方案待评审（2026-08-21）。第 7 节记录已落实的 A1 配置调整：默认 `LLM_REASONING_EFFORT` 由 `max` 改为 `high`；其余提速方案仍未改代码。
> **修正记录（2026-08-21）**：原“默认 4096 截断导致 Fallback extraction failed”假设，经实测**不成立**（见第 6 节），已从根因假设中移除。

## 1. 现状：时间花在哪（实测数据）

### 请求路径
- deepseek 走 `_openai_structured_fallback`（plain-JSON）：一次 `_request_completion`（非流式）→ 本地 JSON 修复 → 失败后 script 层最多 1 次语义重试 → 最坏 2 次 LLM 调用。
- 超时机制：`script.*` 阶段 `timeout_sec = max(LLM_TIMEOUT, 300)` = 300 秒，一次硬截止跨最多 2 次传输重试共享。

### 早期基线（6 场剧本，sub.geiliapi.com/v1，deepseek-v4-flash）
| 场景 | off | low | medium | high | max |
|---|---|---|---|---|---|
| 小请求（3 字段 JSON） | 1.3s | 1.8s | 0.9s | 0.9s | 0.9s |
| 完整剧本解析 | 未测 | 未测 | ~130s 返回，JSON 解析失败 | ~115s 返回，JSON 解析失败 | 300s 硬超时 |

- 小请求各档位无差别；大请求 max 与 high 差别巨大（300s 超时 vs ~2min 返回）。
- 这是原始基线中的一次解析失败记录。第 6 节的原始响应诊断已证明“默认 4096 截断”不是根因；第 7 节的新重复样本也包含完整成功结果。因此不要把本段的早期失败归因为某个档位必然无法输出合法 JSON。

## 2. 官方文档关键事实（附来源）

1. **思考强度映射（deepseek-v4-flash 与 pro 一致）**：`low→low`，`medium→high`，`high→high`，`xhigh→high`，`max→max`；思考模式默认开启、默认 effort=high。
   - 说明：我们实测 medium 与 high 表现一致是官方预期行为；真正可选的档位是 low / high / max（或关闭 thinking）。
   - 来源：https://github.com/thevibeworks/deepseek-docs/blob/main/content/zh-cn/guides/thinking_mode.md
2. **思考模式不支持 temperature/top_p 等参数**（设置不报错但不生效）；思维链通过 `reasoning_content` 返回，与 `content` 同级。
   - 来源：同上
3. **JSON Output 官方支持**：`response_format={"type":"json_object"}`，要求 prompt 中含 "json" 字样并给出输出样例。
   - 来源：https://api-docs.deepseek.com/zh-cn/guides/json_mode/
4. **输出上限**：deepseek-v4-flash 最大输出约 384K token；第三方聚合数据显示默认 max_tokens 仅 4096。
   - **注意**：曾假设“默认 4096 截断导致 Fallback extraction failed”，该假设**实测不成立**（诊断见第 6 节：finish_reason=stop、返回完整合法 JSON）。显式 max_tokens 仍可作低成本保险，但不再视为根因。
   - 来源：https://empiriolabs.ai/zh-CN/models/deepseek-v4-flash 、https://www.orcarouter.ai/zh-TW/models/deepseek/deepseek-v4-flash-0731 、https://deepwiki.com/arikusi/deepseek-mcp-server/6.5-thinking-mode-and-reasoning
5. **关闭思考**：OpenAI SDK 用 `extra_body={"thinking": {"type": "disabled"}}`；Anthropic 格式用 effort=none。
   - 来源：https://github.com/thevibeworks/deepseek-docs/blob/main/content/zh-cn/guides/thinking_mode.md 、https://github.com/esengine/DeepSeek-Reasonix/blob/main/docs/REASONING_PROVIDERS.md

## 3. 提速方案（按投入/收益排序）

### A. 配置级（零代码或极小改动，最快见效）
0. **失败诊断埋点（先做）**：在 `_openai_structured_fallback` 解析失败时，把原始返回的 finish_reason、content 长度、content 头尾各 200 字符写入 telemetry/日志。
   - 理由：实测显示失败是偶发的，且与截断无关；只有抓到失败现场才能确认根因（怀疑：偶发空 content / 非 JSON 前缀 / 中转站异常响应）。
1. **默认档位改为 high，不再使用 max**
   - 理由：本轮样本中，`max` 一次被当前 relay 以 400 拒绝、一次以 524 失败；`high` 三次中有两次完整成功。`high` 因而是比 `max` 更保守的默认候选；`low` 可作为后续专项提速候选，但尚未证明能在忠实性上替代 `high`。
   - 验证：同一剧本 × low/high/medium/max 重复运行的实测记录见第 7 节。
2. **显式设置 max_tokens（建议 16000）——仅作保险**
   - 实测证明当前失败并非截断（见第 6 节），此项不再视为根因修复；仅防止极端长输出被截断，成本极低。
3. **plain-JSON 路径加 `response_format={"type":"json_object"}`**
   - 官方强制合法 JSON，显著降低本地修复/重试概率；需先验证中转站透传支持。
4. **超时与重试校准**
   - LLM_TIMEOUT 放宽到 600s、传输重试 1→2，避免“差一点就超时”的白等；这是防浪费而非提速。
5. **端点 A/B**：sub.geiliapi.com/v1 与官方 api.deepseek.com/v1 各跑 3 次，记录 P50/P95；若直连更快更稳则切换。

### B. 结构性（改代码，收益大）
6. **两阶段解析：轻量大纲 + 并行逐场细节**
   - 第一阶段只让模型输出场景大纲（scene_id/location/characters/一句话动作，小请求 3-10s）；
   - 第二阶段对每个场景并行生成细节（dialogue/action/camera/emotion，每场一个 5-15s 小请求）。
   - 优点：小请求快、单场失败重试成本降到秒级、可与本地预切场结合。
   - 缺点：调用次数 N+1，需确认中转站支持并发；总时延约等于 大纲 + max(单场)，预计 60-120s，且更可控。
7. **解析结果缓存**
   - 按“剧本文本 hash + 模型 + 提示词版本 + 思考档位”缓存 ScriptBreakdown；用户改稿只重算 diff。重复解析成本≈0。
8. **本地预切场**
   - 用规则先切场景边界（时间/地点/人物组/节拍变化），LLM 只负责填语义块；减少模型决策量与错误重试（Phase 1 已加 location 串接校验，可升级为自动拆分）。
9. **流式 + 边收边验**
   - deepseek 目前非流式（streaming 仅 kimi）；流式可提前拿到首 token、及早发现截断/错误，配合硬截止更稳（总时延未必下降）。
10. **候选并行（应急）**：同请求发 2 路不同 temperature，先返回且本地校验通过者胜出；成本翻倍，仅当成功率过低时使用。

### C. 长期
11. 用官方 JSON mode / function calling（tool_choice）替代 plain-JSON fallback，减少解析层损耗。
12. 模型选型对比：deepseek-v4-flash vs kimi-k2.6 vs qwen 在同一中转站的延迟与成功率。
13. 异步预解析：用户粘贴剧本后立即后台解析，UI 先展示进度，不阻塞交互。

## 4. 推荐组合
- **最小改动（1 天内，纯配置）**：A0 诊断埋点 + A1（默认 `high`）+ A3 + A4，A2 作保险。当前 relay 的长尾波动很大，暂不承诺单次解析 60-120s；应先以连续成功率和 P95 验证端点稳定性。
- **推荐实施（1 周内）**：最小改动 + B6 两阶段并行 + B7 缓存。
- **激进（2 周+）**：B8 预切场 + B9 流式 + C11 官方 JSON mode。

## 5. 验证方案
- 测试集：6 场 / 12 场 / 20 场剧本各 1 个（含对白、录音、多地点陷阱）。
- 指标：P50/P95 总耗时、首 token 时间、成功解析率、语义重试率、输出 token 是否触顶（finish_reason）。
- 控制变量：同一剧本、同一温度、同一提示词版本；每档位 3 次重复。
- 成功标准：单次解析 ≤120s、连续 5 次成功率 100%、无语义重试。


## 6. 实测修正记录：截断假设不成立

- 原假设：默认 max_tokens=4096，5-20 场剧本 JSON 超限被截断，导致 `Fallback extraction failed`。
- 实测方法：直接调用 `_request_completion` 抓取原始返回（同一提示词、同一 schema、同一端点），不做任何本地修复前先检查 finish_reason 与内容完整性。
- 实测结果（deepseek-v4-flash，sub.geiliapi.com/v1）：
  | 档位 | 结果 | finish_reason | content 长度 | 解析 |
  |---|---|---|---|---|
  | high | 成功，227.6s | stop | 4934 字符 | json.loads + schema 校验均通过 |
  | medium | 成功，116.9s | stop | 5022 字符 | json.loads + schema 校验均通过 |
- 结论：
  1. 返回完整（finish_reason=stop），无截断证据；**4096 截断假设不成立**。
  2. `Fallback extraction failed` 为**偶发**（同一配置多次运行有成功有失败），真实根因尚未抓到现场；最可能的候选是偶发空 content / 非 JSON 前缀 / 中转站异常响应。
  3. 下一步先做 A0 失败埋点，抓到失败现场后再定修复；在此之前不要为“防截断”专门投入。

## 7. 重复档位测试与默认值决定（2026-08-21）

### 控制变量

- 输入：同一份 6 段剧本《巷口咖啡》，包含雨棚环境、两段交接对白、推门门铃、天台档案和录音内容。
- 请求：同一 `deepseek-v4-flash`、`sub.geiliapi.com/v1`、同一提示词和 schema；每轮新进程串行执行。
- 产品等价设置：`script.analyze` 的 300 秒总请求窗口、`MAX_LLM_TRANSPORT_RETRIES=1`（最多两次传输请求）。
- “成功”指 `ScriptAgent.analyze_existing_script` 返回并通过既有 JSON/schema/本地资产校验。质量观察是同一输出的人工抽查，不是预注册的统一评分；它不改变传输成功统计，只用于默认值取舍的定性参考。

### 产品等价样本

| 档位 | 样本 | 结果 | 实际耗时 | 传输尝试 | 人工内容抽查（非统一评分） |
|---|---:|---|---:|---:|---|
| high | 1 | 成功 | 158.3s | 2（重试 1） | 6 场，基础检查通过 |
| high | 2 | 失败 | 256.8s | 2 | 总窗口内超时 |
| high | 3 | 成功 | 169.5s | 2（重试 1） | 7 场的合理拆分，基础内容完整 |
| low | 1 | 成功 | 227.0s | 2（重试 1） | 录音场写成“未指定”地点，不适合资产锚定 |
| low | 2 | 成功 | 53.1s | 1 | 把“担心被浸透”改成“已经浸湿边角”，发生事实状态漂移 |
| low | 3 | 失败 | 253.3s | 2 | HTTP 524 超时 |
| medium | 1 | 失败 | 253.2s | 2 | HTTP 524 超时 |
| medium | 2 | 成功 | 104.7s | 1 | 地点字段混入灯光/背景状态，地点身份不够干净 |
| max | 1 | 失败 | 6.9s | 1 | relay 返回 400：该后端不接受 `max` |
| max | 2 | 失败 | 253.2s | 2 | HTTP 524 超时 |

补充兼容性探针：显式 `xhigh` 也在 253.4s 以 524 失败；显式 `none` 立即返回 400。两次 400 的验证信息不同，但没有服务端日志，不能据此确定具体原因。此前临时传入未受客户端白名单支持的 `xhigh` 时，客户端会省略字段，因此该次 78.8s 成功不能计为 xhigh 成绩。

### 结论

1. 基于当前样本，本轮将默认 `LLM_REASONING_EFFORT` 从 `max` 改为 `high`：`max` 两次都未完成，而 `high` 三次中有两次完成。这是当前 relay 下较保守的默认取舍，不代表已经证明 `high` 在所有端点或剧本上更优。
2. 这不是“high 在统计上已证明更快或更聪明”的结论。524 和重试引入了明显波动，现有样本不足以区分档位本身的延迟差异；成功样本的耗时也不能代表 P95。
3. `low` 展示了 53.1s 的快路径；人工抽查的两条成功输出分别观察到事实状态漂移和非物理地点锚点。因此本轮不应仅因速度替换默认 `high`。后续若要降档，必须采用更大剧本集、可复核的忠实性评分和连续成功率作为门槛。
4. 两条 `medium` 样本未显示相对 `high` 的明确稳定优势。`max`、`xhigh`、`none` 在本轮 probe 中均未形成可用成功样本，故未被选为当前 relay 的产品默认值；这不是对这些档位或模型能力的普遍否定。

### 已落地的范围

- `src/config.py` 的无环境变量默认值改为 `high`。
- `.env.example` 改为 `LLM_REASONING_EFFORT=high`；本机 `.env` 已同步，重启长期运行的 API/Worker 后生效。
- 新增回归测试，保证在没有环境变量和本地 `.env` 覆盖时默认值仍为 `high`。

### 未纳入本轮的后续项

- A0 失败现场埋点仍应优先做：当前 524 样本的产品 telemetry 在异常路径上尚未带回完整的原始响应信息。
- 本轮没有改 DeepSeek 提示词、拆分架构、超时策略或 Kimi 审核链路；这些必须独立评审，不能把 relay 524 误当作提示词问题。
