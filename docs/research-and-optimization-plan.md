# 剧本解析与分镜提示词优化：调研提炼 + 实施方案

> 状态：Phase 1（剧本解析）与 Phase 2（分镜提示词）均已实现并提交（ca16fa3 / b7df06b / e4fe663 / 2780e2f），全量回归 798 passed, 2 skipped。本文档沉淀网上调研的可落地要点、现有产物审计证据，以及两阶段实施方案与验证结论。

## 一、网上调研提炼（来源与可落地点）

### 1. 剧本解析方向
- **DramaFlow（ctyun.cn/developer/article/836367719657541）**：先按场次切分，再把每场内容分成三类语义块（人物发言 / 动作 / 环境），每个语义块稳定编号作为跨环节锚点；把握不足的片段标记“待确认”交人工，而非硬猜。
  - 可落地：`scene` 内对白、动作、环境三字段天然对应三类语义块；在解析提示词里显式要求“先切场、再分块”，并给一个对照示例。
- **dramark `@角色` 语法**：对白归属是高频出错点，动作误当台词是常见失败模式。
  - 可落地：提示词明确“只有真正说出口的话进 dialogue；括号舞台指示、沉默省略号、旁白、录音、VO 不得进 dialogue”。
- **ScreenJSON**：scene-grouped chunking、角色索引、JSON 带引用。
  - 可落地：解析提示词要求“说话人使用剧本原名的规范称呼，VO/录音/电话用统一后缀，不得新建别名”。
- **deepwiki video-agent-claude-skill**：schema 用宽松 str，枚举校验放在解析后的 agent 层。
  - 可落地：本仓库已如此（compact 输出 → 本地 manifest 校验），保持。

### 2. 分镜提示词方向
- **即梦/Seedance 官方范式（github alchaincyf/seedance-skill references/prompt-spec.md）**：`[时长][画幅][一句话场景定位] → 参考绑定 → 时间戳段落（镜头怎么动 + 主体做什么，两样必须同段写，只写一样模型会自由发挥）→ 全局约束`；负面约束要具体到“排除哪个错误品类”，不要写“不要低质量”。
  - 可落地：动作时间轴每段时间戳必须同时包含主体动作与镜头运动；负面提示词按“物理连续性 / 角色一致性 / 道具一致性 / 画质伪影”分类给具体禁止项。
- **郭帆导演 Skills 02（github shuhaolin63-hash/GuoFanDirector_Skills）**：`[CHARACTER: 名字]` + 发型/肤质/配饰/穿搭四要素锁定；9:16 构图规则；景别边界与占比；运镜 80% 固定或小幅度。
  - 可落地：角色锚点字段已经四要素齐全；可增加“同一角色跨镜头外貌描述逐字一致，以后端首镜记录为准”。
- **分镜导演 Skill（github HEEEEeeeeN/ComfyUI-H3-Conditioning-Cache-AI-Drama-Production-Suite）**：10 字段标准分镜表；空间锚点卡（固定地标/人物位置/退场人物状态/光位基线）；长镜头 4-6s 甜点、8s 硬上限；5 维 20 项评审。
  - 可落地：空间锚点与现有 canonical_name/environment_delta/lighting_delta 对应；长镜头建议仅作参考（Kling/Seedance 单段 5-10s 是常态，不强行套 8s 上限）。
- **universal-rules.md（smixs/visual-skills）**：一镜一个主要动作、character consistency anchor、无矛盾、具体物理细节胜过抽象概念。
- **StoryAnchors（ar5iv 2505.08350）**：NQ / Subject Consistency / Prompt-Frame Alignment / Shot Diversity 评估维度。

## 二、现有产物审计证据（output/ 29 个项目，约 340 个 scene、727 条 dialogue）

### 剧本解析侧（第一优先）
1. **整句台词只进 action_description**：`output/b40a69614e28` s4/s6/s9（“走。”“去哪？”“旧钟楼。”；“我当年没能救下你父亲。”…），dialogue 为空 → TTS/字幕断档。
2. **动作/舞台指示当对白**：`（示意别出声）`、`（眼眶发红）`、`……` 出现在 dialogue 字段；动作前缀 `（微笑）` 混入台词。
3. **对白双写**：`output/f851058a0c7a` s7/s10 同一句台词同时出现在 dialogue 与 action_description。
4. **场景切分过粗 / 地点串接**：`output/2fb507d203a3` s3 location=`钟楼街便利店门口 → 街边窄巷 → 半掩铁门前`；s4 单场 14s 含进门/下台阶/发现摄像头/躲藏。
5. **location 与 environment 混淆**：`output/f851058a0c7a` locations[0] environment_description 整段是“苏眠站在店檐下……死死盯着屏幕，指尖发抖”。
6. **资产问题**：2 个项目 asset_manifest=null；多个项目 props=[]（信封/铜钥匙/录音机全缺）；“黑衣人”角色 8 个项目 manifest 缺失；同一人物拆成“黑衣人（陆临）”+“陆临”两个资产。
7. **时长不一致**：10/27 项目 duration_sec 与场景时长总和不一致（差 3 倍）。

### 分镜提示词侧（第二优先，等审计子代理返回补充）
- （待 Goodall 探子返回后补充具体证据）

## 三、实施方案

### Phase 1：先改剧本解析（前提）
1. **解析提示词增强**（`src/agents/script_agent.py`）：
   - `analyze_existing_script` 用户提示词 + `_generate_compact` 追加“解析三原则”：
     a. **先切场**：时间/地点/人物组/节拍变化即新场景；一个场景只能有一个物理地点（禁止 `→` 串接）；跨地点动作（走出/追出/进入）按新地点拆场。
     b. **对白与动作分离**：只有真正说出口的话进 dialogue；括号舞台指示、沉默省略号、旁白、系统提示、录音、VO、心理活动不得进 dialogue；动作前缀 `（微笑）` 不得写入台词文本；台词不得只写在 action_description 里，也不得双写。
     c. **地点与环境分离**：location 只写稳定物理地点；environment 只写静态事实；人物动作/情绪/对白/瞬时状态不进 location 与 environment。
   - 给出一个“正确拆场 + 三类语义块”示例（few-shot）。
2. **确定性后处理修复**（`script_agent.py` 新增 `_repair_scene_semantics`，零成本、不新增 LLM 调用）：
   - 剥离 dialogue 文本首尾括号舞台指示 → 并入 action_description；删除纯省略号/空台词；
   - 从 action_description 中按“具名角色 + 说/道/喊/问/答 + 引号”模式抽取整句台词进 dialogue（保守：仅当引号完整且说话人是场景内角色），避免 TTS 丢台词。
3. **确定性语义校验（触发一次重试）**：location 含 `→`/多地点；action 中仍有未抽取的具名角色整句引文（应已被 2 修复）；这些错误以现有 `semantic_retries` 通道反馈给模型修正。

### Phase 2：再改分镜提示词（依赖 Phase 1 的干净输入）
1. **动作时间轴**：每段时间戳必须“镜头怎么动 + 主体做什么”同段写（对应 Seedance 官方范式）；一镜一个主要动作。
2. **环境段**：强调 environment_delta 只写静态事实、禁止瞬时状态；同一 location 跨镜头环境事实不得冲突（已有清洗，继续强化提示词）。
3. **负面提示词**：按品类给具体禁止项（物理连续性 / 角色一致性 / 道具一致性 / 画质伪影），去掉抽象否定。
4. **角色锚点**：同一角色跨镜头描述逐字一致，后端按首镜锁定（现有 canonical registry 继续）。
5. **空间锚点**：评估是否需要“承接上一镜/交接下一镜”字段（现有【顺承上一镜】已覆盖一部分）。

### 验证
- 新增/扩充单测：`test_script_agent_normalization.py`（语义修复）、`test_prompt_policy.py`（分镜提示词）。
- 端到端：`tmp/e2e_verify.py` 用《雨夜拿铁》剧本跑真实 DeepSeek（含“追出塞拿铁/望向/视线/发梢滴落/推门门铃”陷阱），断言环境无动作词、无重复句、入口模板不误触发。
- 全量 `pytest -q` 保持全绿（当前基线 783 passed, 2 skipped）。


## 四、实施进度与验证结论（2026-08-21）

### Phase 1：剧本解析（已提交）
- `SCENE_PARSE_RULES` 解析三原则提示词（先切场/对白与动作分离/地点与环境分离），接入 analyze 与 compact 两条路径。
- 确定性修复 `_repair_scene_semantics`：剥离台词括号舞台指示、删除纯沉默台词、从 action 提取具名角色整句台词进 dialogue、对已在 dialogue 的引文去重（防双写）。
- 语义校验 `_scene_semantic_errors`：location 含 “→” 串接多地点或完整句子时触发一次重试。
- `script.duration_sec` 归一为场景时长之和，防止视觉阶段按虚高总量拉伸。
- 提交：ca16fa3、e4fe663（否定守卫补强）。

### Phase 2：分镜提示词（已提交）
- `_ENV_ACTION_CUES` 与 manifest `DYNAMIC_ENVIRONMENT_CUES` 补充：看着/张望/遥望/低头/抬头/回头/撞倒/被撞倒/门铃响/铃响/门铃轻响。
- `COMPACT_SYSTEM_PROMPT`：每段时间戳必须同时写镜头运动与主体动作、一镜一主要动作、负面词按具体错误品类、environment_delta 禁止未来状态/后续镜头事件、角色锚点逐字复用。
- 逐镜 delta 提示词追加“本镜硬性规则”。
- 提交：b7df06b、2780e2f。

### 真实 DeepSeek 端到端（sub.geiliapi.com/v1，deepseek-v4-flash，reasoning_effort=low）
- 剧本解析：《巷口咖啡》6 场，77.3s 完成，全部检查 NONE（台词提取正确、舞台指示留在动作、信上引文不误抽、地点干净、时长一致）。
- 全链路《雨夜拿铁》：142.8s 完成，6 镜环境均为静态事实、无动作/心理词、无精确重复；追出场景无入口模板误触发；推门场景含推门/门铃语义。检查器 1 条提示为模板原文措辞过严的误报（已人工核实语义正确）。
- 偶发：中转站输出不稳定（一次 per-scene JSON 提取失败触发单镜回落；一次角色侧写缺字段触发重试失败），属外部 LLM 偶发质量，非代码缺陷；提速/提稳方案见 docs/script-parse-speedup-plan.md。

### 已知残留观察（后续项）
- 环境段同义不同措辞的重复（如“街角路灯亮着”与“街角路灯昏黄”、“墙面水痕”两次）仍是精确去重覆盖不到的部分，建议后续做轻量同义归并。
- 中转站延迟/成功率问题按 speedup plan 处理（配置级优先：low 档 + max_tokens + json_object）。
