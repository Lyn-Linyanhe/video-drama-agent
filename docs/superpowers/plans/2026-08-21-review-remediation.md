# 审核整改实施计划（2026-08-21 review remediation）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 `6acc64f`（图片视觉审核分级与资产质量门禁）与 `f31f8f4`（跨镜顺承与并行视觉逐镜进度）在子代理只读复核中发现的缺陷，使实现与 `2026-08-19-visual-review-gate.md` 计划及 README 的语义一致，通过全量回归与子代理终审后提交。

**Architecture:** 审核规则与结果规范化继续由 `src/services/media_quality.py` 唯一负责；`src/services/asset_generation.py` 决定审核结果能否进入活动版本（passed 才激活，warning / hard_fail 二次失败只落候选）；`src/utils/asset_versions.py` 提供候选版本的原子暂存；`src/agents/visual_prompt_agent.py` 与 `src/api/routes/jobs.py` 修复取消、明细与遥测问题；前端只展示后端结论。测试先行（TDD），每个任务结束由子代理做只读复核。

**Tech Stack:** Python 3.12、FastAPI、Pydantic、原生 ES Modules、pytest、现有 JobStore/SSE。

**Spec:**
- `docs/superpowers/plans/2026-08-19-visual-review-gate.md`（本计划实现的目标语义）
- `README.md:215-222`（四种审核结论的对外承诺）
- 本线程子代理复核报告（Bohr：`6acc64f` FAIL；Dalton：`f31f8f4` PASS-with-findings；Franklin：git 卫生 6 PASS 1 FAIL）

## Global Constraints

- `warning`：模型发现疑点但证据不足，**不自动重试或替换**；结果作为候选保留，由用户确认（README:220）。
- `hard_fail`：只自动重试一次；第二次仍失败时**停止重试，两个版本都保留在历史记录中**，不得覆盖当前活动图片（README:221、计划 :22/:376「审核失败不能替换当前活动图片」「候选版本不覆盖活动版本」）。
- 审核失败不能替换当前活动图片；候选版本不覆盖活动版本；组图必须原子处理。
- Mock 供应商不调用视觉模型，并**继续保留 `automatic_reference_allowed=true` 的测试占位行为**（计划 Global Constraints）。
- `unreviewed`：可查看但不能自动绑定为后续镜头参考。
- 保留旧 `quality_report` 字段与 `passed/warning/unreviewed` 旧状态，旧项目读取不报错。
- 不删除或覆盖 `output/` 中的历史资产。
- 不更换图片生成供应商，不改变生成提示词与版本历史接口语义。
- 用户上传图片默认只做技术检查；显式视觉复核才调模型。
- 所有视觉审核请求记录模型、供应商、时间、结果，不记录 API Key。
- 与本次主题无关的 `docs/superpowers/plans/2026-08-20-visualize-robustness.md`（旧计划残留）**本次不改动、不删除**，仅作为遗留项记录。
- 子代理只做只读探索与复核；代码修改、方案取舍、最终验证由主代理负责。
- 不 push、不改写历史提交；修复以新提交追加在 `f31f8f4` 之上。

## 背景（复核结论摘要，均经主代理抽查核实）

1. **S1（严重）**：`asset_generation.py:278-288` 仅 `hard_fail` 抛异常，`warning` 正常返回后被 `:2246` 等 `replace_with_file` 无条件激活，与 README:220 冲突。
2. **S2（严重）**：`asset_generation.py:2216-2231` 等所有「先重试一次、再失败」路径走 `_auto_selected_quality` 后继续激活；角色组多失败 `:2491-2499` 不重试直接激活。与 README:221、计划 :22/:376 冲突；测试 `test_review_retry_policy.py:108-129`、`test_wan_character_references.py`（`test_character_group_multiple_hard_failures_activate_usable_group_without_extra_retries`）固化了错误行为。
3. **G1**：`assets.py:608-609` 用 `review_decision()` 返回值（可能为 `"uncertain"`）写 `review_verdict`，产生第五个非法值；前端无此映射。
4. **G2**：`media_quality.py:49-51` 先判 `verdict != "passed"` 返回 False，使 `:66-71` 的 mock opt-in 分支不可达；`test_shot_production_state.py:819-844` 因 report 缺 `review_verdict` 未暴露回归。
5. **G3**：缺证据时 `_parse_visual_review` 标 `review_verdict="hard_fail"`、`should_regenerate=True`，但 `review_decision="uncertain"`，导致不重试且被激活（`media_quality.py:698-774`、`asset_generation.py:287-288`）。
6. **G4**：多失败时场景组 `:1603-1617`、道具组 `:1051-1062` 返回候选不激活，角色组 `:2491-2499` 直接激活；edit 二次失败 `raise`，master/group/single 却 auto-select 激活，规则不一致。
7. **G5**：job 层 `jobs.py:610-646` 对所有 `MediaQualityFailure` 无条件重试一次，削弱「仅 hard_fail 重试」语义；且与 `asset_generation.py` 内部 `attempt_no=2` 形成双重重试。
8. **G6**：`asset_versions.py:86-87` `list_versions` 每次读取都 `_set_active` + `_save_manifest`（读带写副作用，并发有风险）；`assets.py:158-161` 以 `create_missing=True` 解析。
9. **G7**：`pending_review` 只被置 False 从不置 True（`asset_versions.py:208,263,291,316`）；`classify_quality_report`（`media_quality.py:489-511`）无调用方。
10. **F1（f31f8f4）**：`visual_prompt_agent.py:780-874` 中 `JobCanceled` 被通用 `except Exception` 吞掉，取消时多付 4×1 次重试 + 1 次全片回落调用。
11. **F2**：`jobs.py:497-517` 逐镜去重（`seen_keys` 丢弃同 key 新记录）与总记录追加（`stage="visualize"`）矛盾，重复运行时前端 `job-events.js:278` 取 `[0]` 显示旧数据。
12. **F3**：`visual_prompt_agent.py:867-874` 回落前写入的 `attempts/fallback_used` 被 `llm.py:265-277` `generate_structured` 开头重置，明细失真；`test_visualize_robustness.py` stub 掉真实调用未覆盖。
13. **空白**：`docs/superpowers/plans/2026-08-19-visual-review-gate.md:110`（行尾空格）、`:390`（EOF 空行）、`tests/test_prompt_review_continuity.py:243`（EOF 空行）。

---

### Task 0: 现状核对与基线

**Files:**
- 只读：仓库全貌、`git status`、相关文件现状

**Interfaces:**
- 产出：各修复点的精确 `file:line` 与调用链清单（由探子返回，主代理据此实现）

- [x] Step 1: 记录基线：`git status --short`、`git log --oneline -3`、`.venv` 可用性。
- [x] Step 2: 探子并行核对：S1/S2 全部激活路径、G1-G3 精确修复点、F1-F3 精确修复点（含相关测试现状）。
- [x] Step 3: 运行一次全量 pytest 基线（只读/临时产物），记录当前通过数，作为回归对照。

---

### Task 1: `warning` 不再静默激活（S1）

**Files:**
- Modify: `src/services/asset_generation.py`（`_validate_generated_image` warning 分支、单图/场景母版/道具母版/场景组/道具组/角色组激活路径）
- Modify: `src/utils/asset_versions.py`（候选落盘/激活语义，必要时新增「仅落候选」接口）
- Test: `tests/test_review_retry_policy.py`、`tests/test_wan_character_references.py`、`tests/test_asset_quality_final_fixes.py`、`tests/test_shot_production_state.py`

**Interfaces:**
- Consumes: `_validate_generated_image` 返回的 `report`（`review_verdict` ∈ passed/warning/hard_fail/unreviewed）
- Produces: 最终报告 `review_decision` 在 warning 时为 `warning_candidate`（或等价值），`quality` 中标记 `candidate_version_id`，活动版本不变

- [x] Step 1: 为单图 regenerate 写失败测试：warning 结果 → 活动版本不变、出现候选版本、`review_decision` 为候选语义。
- [x] Step 2: 运行该测试，确认 RED（当前 warning 直接激活）。
- [x] Step 3: 实现：warning 分支不进入激活，落候选。
- [x] Step 4: 为场景母版、道具母版、场景组、道具组、角色组各写/补等价测试并逐个转绿。
- [x] Step 5: 运行相关测试文件全量，确认无回归；`git diff --check` 干净。
- [x] Step 6: 派子代理复核本任务 diff（只读），修复其发现后进入下一任务。

---

### Task 2: `hard_fail` 二次失败不再激活（S2）

**Files:**
- Modify: `src/services/asset_generation.py`（全部「重试后仍失败」路径、角色组多失败路径、`_auto_selected_quality`）
- Test: `tests/test_review_retry_policy.py`（改 `:108-129` 断言）、`tests/test_wan_character_references.py`（改名/改断言）、`tests/test_asset_generation_api.py`

**Interfaces:**
- Consumes: Task 1 的候选落盘接口
- Produces: 二次失败后 `quality.review_decision="failed_after_retry"`，活动版本不变，候选保留

- [x] Step 1: 改写 `test_review_retry_policy.py:108-129`：二次失败 → 活动版本不变、候选保留、不激活失败图。
- [x] Step 2: 运行确认 RED。
- [x] Step 3: 实现：所有「重试后仍失败」路径改为只落候选；角色组与场景/道具组行为统一。
- [x] Step 4: 更新 `test_wan_character_references.py` 相关用例（删除/改写「失败仍激活」断言）。
- [x] Step 5: 相关测试全绿 + `git diff --check` 干净。
- [x] Step 6: 派子代理复核本任务 diff，修复发现。

---

### Task 3: 审核结论一致性（G1-G3）

**Files:**
- Modify: `src/api/routes/assets.py`（`/image-history/review` 端点 `:608-609`）
- Modify: `src/services/media_quality.py`（`image_allows_automatic_reference` `:49-51`、缺证据判定 `:762-774`）
- Modify: `src/services/asset_generation.py`（缺证据时门禁行为 `:287-288`）
- Test: `tests/test_asset_quality_final_fixes.py`（补 warning/skipped 分支）、`tests/test_shot_production_state.py:819-844`（补 `review_verdict` 字段）、`tests/test_media_quality.py`（补缺证据门禁断言）

**Interfaces:**
- Produces: `review_verdict` 恒为四档合法值；mock `unreviewed` 仍可 opt-in 自动参考；缺证据 = `hard_fail` 且走重试/不激活

- [x] Step 1: G1 测试：review 端点对 warning/skipped 返回的 `review_verdict` ∈ 四档合法值 → RED。
- [x] Step 2: 实现 G1（改用 `visual_review.get("review_verdict")` 或映射 `uncertain→warning`）。
- [x] Step 3: G2 测试：mock 输出带 `review_verdict="unreviewed"` 时仍可自动参考 → RED。
- [x] Step 4: 实现 G2（mock opt-in 分支在 `verdict != "passed"` 检查之前或特判 `unreviewed + mock + automatic_reference_allowed`）。
- [x] Step 5: G3 测试：缺证据报告 → `review_decision=="hard_fail"`、触发重试、不激活 → RED。
- [x] Step 6: 实现 G3（缺证据时 `hard_fail` 标志/decision 一致化）。
- [x] Step 7: 相关测试全绿 + `git diff --check` 干净。
- [x] Step 8: 派子代理复核本任务 diff，修复发现。

---

### Task 4: 并行视觉修复（F1-F3，f31f8f4）

**Files:**
- Modify: `src/agents/visual_prompt_agent.py`（`one()`/`_run_parallel_visualize` 取消处理、回落遥测合并）
- Modify: `src/api/routes/jobs.py`（`visual_performance.requests` 合并语义 `:497-517`）
- Test: `tests/test_visualize_robustness.py`（新增取消与明细/遥测用例，去掉对真实路径的 stub 盲区）

**Interfaces:**
- Produces: 取消立即失败不再多打调用；重复运行明细显示最新一次；回落统计保留已发生调用计数

- [x] Step 1: F1 测试：并行阶段抛 `JobCanceled` → 不重试、不回落、快速失败 → RED。
- [x] Step 2: 实现 F1（`except JobCanceled` 在通用 `except Exception` 之前，或先 `check_cancelled()`）。
- [x] Step 3: F2 测试：同一 `(stage, scene_id)` 第二次运行 → 明细更新为新记录、前端总记录为最新 → RED。
- [x] Step 4: 实现 F2（同 key upsert 语义：新记录替换旧记录而非丢弃；总记录也替换）。
- [x] Step 5: F3 测试：真实回落路径（不 stub `_run_single_shot`）→ `attempts` 含并行阶段调用数、`fallback_used=True` → RED。
- [x] Step 6: 实现 F3（回落调用使用独立 metric dict，返回后合并；或 `generate_structured` 不重置外部统计）。
- [x] Step 7: 相关测试全绿 + `git diff --check` 干净（含 `tests/test_prompt_review_continuity.py:243` EOF 空行）。
- [x] Step 8: 派子代理复核本任务 diff，修复发现。

---

### Task 5: 规则统一与清理（G4-G7、G5、空白、文档对齐）

**Files:**
- Modify: `src/services/asset_generation.py`（角色组多失败行为统一）
- Modify: `src/api/routes/jobs.py`（job 层重试只针对 hard_fail/技术失败）
- Modify: `src/utils/asset_versions.py`（读接口去写副作用；`pending_review` 清理或接线）
- Modify: `src/services/media_quality.py`（`classify_quality_report` 接线或删除）
- Modify: `docs/superpowers/plans/2026-08-19-visual-review-gate.md:110/:390`（空白）
- Test: 相关测试 + 新测试

**Interfaces:**
- Produces: 组图规则一致；job 层不再双重/无条件重试；读接口无写副作用；无死代码

- [x] Step 1: G4 测试：角色组多失败 → 与场景/道具组一致（不激活）→ RED → 实现。
- [x] Step 2: G5 测试：job 层对 warning 失败不重试、对 hard_fail 重试一次 → RED → 实现（注意与内部重试去重）。
- [x] Step 3: G6 测试：`list_versions`/`get_image_history` 读取不持久化 → RED → 实现。
- [x] Step 4: G7 清理：`pending_review` 语义核实后接线或删除；`classify_quality_report` 接线到 review 端点或删除，并同步计划文档措辞。
- [x] Step 5: 修复 2 处文档空白；README 与实际语义对齐（如候选/确认的界面表述）。
- [x] Step 6: 相关测试全绿 + `git diff --check` 干净。
- [x] Step 7: 派子代理复核本任务 diff，修复发现。

---

### Task 6: 全量回归、提交与终审

**Files:**
- 提交产物：2 个修复提交（或按需合并）

**Interfaces:**
- Produces: `fix(quality-gate): ...` + `fix(visualize): ...`（提交信息与内容一致）

- [ ] Step 1: `pytest -q` 全量回归，与 Task 0 基线对比。
- [ ] Step 2: `python -m compileall src`；变更 JS 文件 `node --check`；新增文本扫描 API Key/密钥。
- [ ] Step 3: `git diff --check`、`git status --short` 确认待提交内容。
- [ ] Step 4: 按主题分 2 个提交：`fix(quality-gate)`（Task 1/2/3/5 的代码+测试+文档）与 `fix(visualize)`（Task 4），提交信息说明修复内容与夹带处理。
- [ ] Step 5: 派终审子代理（2-3 个并行）：git 卫生终审 + 两个修复提交内容终审；发现即修、修后复审。
- [ ] Step 6: 全部通过后汇报收尾；遗留项（`2026-08-20-visualize-robustness.md` 去留、旧会话目标关闭方式）列出供用户决定。

---

## Rulings（本计划已做的取舍）

- 不删除 `2026-08-20-visualize-robustness.md`：它是旧计划残留、与两提交主题无关，删除会丢失可能仍在用的意图；本次不触碰。代价：仓库仍有一份未勾选的旧计划，后续需要用户决定去留。
- 不改写 `6acc64f`/`f31f8f4` 历史提交：复核发现的问题用新提交修复，保留审计轨迹。代价：历史提交仍含缺陷，`git log` 需结合修复提交阅读。
- 直接在主分支开发（不建 worktree/分支）：仓库为本地单项目、无服务在跑、历史均为 main 直提，沿用现有工作流。代价：修复过程若中途中断，工作区会出现未提交改动。

## 自审

- 每个行为变化都先写失败测试（RED）再实现（GREEN），避免「测试固化错误行为」重演。
- 每个 Task 结束都有独立子代理只读复核，复核范围限定该 Task 的 diff，防止回退到「无复核」状态。
- 所有修改保持向后兼容：旧字段不删、旧状态可读、历史资产不覆盖。
- 危险点：激活路径分散在 6 处（单图/母版×2/组图×2/角色组），S1/S2 改动必须逐处核对，Task 0 的探子会给出精确清单，Task 1/2 复核会逐一验证。
