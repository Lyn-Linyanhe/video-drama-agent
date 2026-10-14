# HappyHorse 镜头视角控制修复计划

> **Goal:** 减少无叙事理由的正脸输出，并保证过肩、侧面、背面等明确视角不会被组装模板覆盖。

**Architecture:** 只修复确定性提示词与参考图排序逻辑：镜头语义中将视点/角度置于景别之前；无明确视角时使用中性三分之二侧前方；有明确非正面视角时优先匹配人物参考图。暂不改变 HappyHorse 的 `prompt_extend` 默认值，后续单独做 A/B。

**Tech Stack:** Python、pytest、现有 `timed_prompt_composer` 与 `VideoGenerationAgent`。

## Global Constraints

- 不修改原始剧本。
- 不调用 Kimi，不改审核链。
- 不随机改变左右轴；明确剧本视角优先。
- 正面镜头仍可由明确剧本/镜头语言显式指定。
- `prompt_extend` 本轮保持现状，单独实验。

## Task 1: 视角模板确定性修复

**Files:**
- Modify: `src/services/timed_prompt_composer.py`
- Test: `tests/test_timed_prompt_composer.py`

- [ ] 过肩优先于特写。
- [ ] 单人无明确角度时不再默认正面。
- [ ] 对白特写和情绪落点沿用当前镜头视角，缺失时使用三分之二侧前方。
- [ ] 明确正面镜头保持正面。

## Task 2: 参考图按视角排序

**Files:**
- Modify: `src/agents/video_gen_agent.py`
- Test: `tests/test_video_generation_snapshot.py` 或 `tests/test_timed_prompt_composer.py`

- [ ] 侧面、过肩优先 profile。
- [ ] 背面优先 back。
- [ ] 无明确角度保持兼容排序。
- [ ] 参考图排序只影响上传顺序，不删除身份参考图。

## Task 3: 验证

- [ ] 目标单测先失败，再实现通过。
- [ ] 运行视角相关测试。
- [ ] 运行全量 pytest。
- [ ] `git diff --check`。
- [ ] 记录当前项目静态基线；不声称未经真实 HappyHorse A/B 证实的模型效果。
