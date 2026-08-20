# 终审交接单（2026-08-21 review handoff）

> 用途：本线程的子代理派发工具持续 `unsupported call`，独立终审无法在本线程执行。任何新会话/工具恢复后，按本交接单直接派只读子代理即可完成终审。无需其它背景。

## 待终审对象

- 仓库：`E:\影视agent\video-drama-agent-main(2)\video-drama-agent-main`
- 范围：`git diff f31f8f4..HEAD`（两个提交）
  - `f66b441` fix(quality-gate): warning/hard_fail 不再覆盖活动版本，修复审核结论一致性（11 文件）
  - `0ced1ba` fix(visualize): 取消不再触发额外调用，视觉明细按最新运行展示，回落统计合并（5 文件）
- 规格对照：
  - `docs/superpowers/plans/2026-08-21-review-remediation.md`（本计划，任务 1-6 已完成并勾选）
  - `docs/superpowers/plans/2026-08-19-visual-review-gate.md`
  - `README.md:215-222`

## 已完成的验证（交接时状态）

- 全量 pytest：771 passed, 2 skipped（提交后复跑确认）
- `git diff --check` 干净；`python -m compileall src` 通过；敏感信息扫描无命中
- 工作区干净，`git status --short` 为空
- 初始 3 个子代理复核发现的问题（S1/S2/G1-G3/F1-F3/G4-G7/空白）均已处理；本轮又通过全激活点审计发现并修复 edit 流程 warning 激活漏洞（S1 遗漏），已并入 f66b441

## 派发模板（直接复制）

只读终审任务（绝不修改文件、不运行测试、不 push）。仓库：E:\影视agent\video-drama-agent-main(2)\video-drama-agent-main。

终审 git diff f31f8f4..HEAD（f66b441 + 0ced1ba），对照 docs/superpowers/plans/2026-08-21-review-remediation.md、2026-08-19-visual-review-gate.md、README.md:215-222。

重点：
1. 门禁覆盖：warning / hard_fail 二次失败只落候选不激活；passed/unreviewed 仍激活。逐一核对 src/services/asset_generation.py 全部 replace_with_file 激活点（场景/道具母版、单图 regenerate、场景/道具/角色组图、场景/角色 edit）是否都被 _quality_allows_activation 保护；upload 与用户 accept/activate 是否不受影响。
2. 候选响应契约与调用方兼容性（jobs.py asset.generate、generate_asset_views 分派器、前端）。
3. media_quality.py review_decision / image_allows_automatic_reference 有无回归（旧报告兼容、skipped/unreviewed、mock 自动参考）。
4. assets.py classify_quality_report 四档闭合。
5. visual_prompt_agent.py 取消透传与回落遥测合并、jobs.py visual_performance upsert 自洽性。
6. asset_versions.py list_versions 条件写 manifest、pending_review 接线兼容性。
7. 测试改动是否真实覆盖新行为、有无把错误行为固化为测试、有无盲区。

输出：按「严重 / 一般 / 建议」列发现，每条 file:line + 原文 + 问题 + 建议；无的档位写「无」。总体结论 PASS / PASS-with-findings / FAIL。中文。

## 终审通过后的收尾（可选）

- 若 PASS：旧会话目标「子代理做审核」可视为收尾；无需改动代码。
- 若 PASS-with-findings：按严重/一般档修复后补一个小提交，再复审一次。
- 遗留项：`docs/superpowers/plans/2026-08-20-visualize-robustness.md`（旧计划残留，未勾选、与本次主题无关）未处理，需用户决定去留。
