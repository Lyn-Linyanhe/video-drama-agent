# 图片视觉审核分级与人工确认实施计划

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans (recommended) to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 把图片审核从“模型返回警告后直接决定是否替换当前版本”改为可解释的分级审核、候选版本和人工确认流程，同时保留现有供应商、项目隔离、历史版本和 Mock 兼容行为。

**Architecture:** 保留 src/services/media_quality.py 作为审核规则和结果规范化的唯一入口；由 src/services/asset_generation.py 决定审核结果是否可以进入活动版本；由 src/utils/asset_versions.py 提供候选版本的原子暂存和确认；前端只展示后端给出的结论、逐项证据和可执行操作。

**Tech Stack:** Python 3、FastAPI、Pydantic、Pillow、FFmpeg、Qwen OpenAI-compatible vision API、原生 ES Modules、现有 JobStore/SSE。

**Spec:** 当前审核实现与现有测试基线，重点文件为 src/services/media_quality.py、src/services/asset_generation.py、src/utils/asset_versions.py 和 tests/test_media_quality.py。

## 实施状态（2026-08-19）

已完成审核结论规范化、单图一次重试、候选版本保留与人工接受/放弃、组图逐张审核和原子接受、显式视觉检查接口、视觉模型配置与连接测试，以及图片编辑器和设置页展示。全量回归结果为 `726 passed, 2 skipped`；Python 编译、变更前端模块语法和敏感信息扫描均通过。真实供应商验证只允许使用已有图片探测，不创建图片或视频生成任务。

组图候选接受和单图候选接受均通过 `asset_commit_transaction` 保护；任何文件、清单或项目状态写入异常都会整体回滚，避免活动视图和版本清单不一致。

## Global Constraints

- 保留现有 quality_report 字段和 passed、warning、unreviewed 等旧状态，旧项目读取不能报错。
- 不删除或覆盖 output/ 中的历史资产；审核失败不能替换当前活动图片。
- Mock 供应商不调用视觉模型，并继续保留 automatic_reference_allowed=true 的测试占位行为。
- 不更换图片生成供应商，不改变角色、场景、道具的生成提示词和版本历史接口语义。
- 用户上传图片默认仍只做技术检查；新增视觉复核必须由用户明确触发，避免上传动作产生隐性模型费用。
- 所有视觉审核请求都记录实际模型、供应商、审核时间和结果，不记录 API Key。
- 审核模型只能做自动初筛；最终确认权属于用户。

---

## 当前事实基线

当前代码和测试已经证实：

1. asset_generation.py 的 _validate_generated_image() 先执行 inspect_image()，再执行 review_image_subjective()；Mock 会直接跳过视觉模型。
2. media_quality.py 的 _parse_visual_review() 要求角色、场景和部分道具返回逐项 contract_checks；缺项、失败项、JSON 无效或评分低于 0.65 会设置 should_regenerate=true。
3. 普通单图任务在 jobs.py 中遇到 MediaQualityFailure 会换 seed 自动重试一次；第二次失败后任务失败，活动版本不替换。
4. 角色参考组图逐张审核，任意一张抛出 MediaQualityFailure 后整组不激活。
5. warning 且 should_regenerate=false 的结果仍会继续提交版本，这使“有问题但不够确定”的图片可能直接成为活动版本。
6. 上传图片接口只调用 inspect_image()，随后写入 unreviewed，不会调用视觉模型。

改造只针对第 5 项的隐式替换，以及第 2、3、4 项缺少用户可操作证据的问题。

---

## Task 1: 建立审核结论和证据契约

**Files:**

- Modify: src/services/media_quality.py
- Modify: src/schemas/assets.py
- Modify: src/schemas/character.py
- Modify: src/schemas/scene_image.py
- Modify: src/schemas/prop.py
- Test: tests/test_media_quality.py
- Test: tests/test_asset_generation_api.py

**Interfaces:**

- review_verdict: passed、warning、hard_fail、unreviewed。
- review_reason: 面向用户的一句话原因。
- review_evidence: item、passed、evidence、severity 组成的规范化列表。
- 旧 status、passed、score、issues、summary、should_regenerate 字段继续返回。

- [ ] Step 1: 为缺证据、低置信度和未审核写失败测试。

    def test_visual_review_classifies_missing_evidence_as_hard_fail():
        report = media_quality._parse_visual_review(
            '{"passed":true,"score":1,"should_regenerate":false,"contract_checks":[]}',
            require_contract_checks=True,
            contract_kind="character",
        )
        assert report["review_verdict"] == "hard_fail"
        assert report["should_regenerate"] is True

    def test_visual_review_classifies_uncertain_score_as_warning():
        report = media_quality._parse_visual_review(
            '{"passed":false,"score":0.60,"should_regenerate":false}',
            require_contract_checks=False,
            contract_kind="image",
        )
        assert report["review_verdict"] == "warning"

- [ ] Step 2: 运行 focused tests，确认新字段尚不存在。

    pytest tests/test_media_quality.py::test_visual_review_classifies_missing_evidence_as_hard_fail -q

    预期：失败，原因是 review_verdict 尚未实现。

- [ ] Step 3: 在 media_quality.py 增加 review_verdict() 和规范化逻辑。

    REVIEW_VERDICTS = {"passed", "warning", "hard_fail", "unreviewed"}

    def review_verdict(report: dict) -> str:
        visual = report.get("visual_review") if isinstance(report, dict) else {}
        value = visual.get("review_verdict") if isinstance(visual, dict) else ""
        if value in REVIEW_VERDICTS:
            return value
        if isinstance(visual, dict) and visual.get("status") == "passed":
            return "passed"
        if isinstance(visual, dict) and visual.get("status") == "warning":
            return "warning"
        return "unreviewed"

    扩展 _parse_visual_review()：
    - 缺少必需契约、契约失败、JSON 无效、明确禁止元素或视角错误为 hard_fail；
    - 只有低分但没有具体失败证据时为 warning；
    - 完整证据且无问题时为 passed；
    - 证据项必须有 item、布尔 passed、非空 evidence 和 severity。

    不从任意自然语言中猜测 hard_fail。只有失败的必需契约、技术失败、格式无效或明确的高置信度违规才阻断。

- [ ] Step 4: 为三个图片模型增加默认字段 review_verdict、review_reason、pending_review_version_id、pending_review_group_id，旧项目自动使用空默认值。

- [ ] Step 5: 运行 pytest tests/test_media_quality.py tests/test_asset_generation_api.py -q，确认全部旧测试通过。

---

## Task 2: 分离硬失败、警告和审核不可用

**Files:**

- Modify: src/services/media_quality.py
- Modify: src/services/asset_generation.py
- Test: tests/test_media_quality.py
- Test: tests/test_asset_quality_final_fixes.py

**Interfaces:**

- classify_quality_report(report, asset_type) -> passed、warning、hard_fail 或 unreviewed。
- _validate_generated_image() 对 passed、warning、unreviewed 返回报告，只对技术失败和 hard_fail 抛出 MediaQualityFailure。

- [ ] Step 1: 增加三个结果的失败测试。

    def test_warning_does_not_request_provider_retry():
        report = {
            "status": "warning",
            "visual_review": {
                "status": "warning",
                "review_verdict": "warning",
                "should_regenerate": False,
            },
        }
        assert media_quality.classify_quality_report(report, "character") == "warning"

    def test_unreviewed_is_never_automatic_reference():
        report = {
            "status": "unreviewed",
            "visual_review": {"status": "skipped", "review_verdict": "unreviewed"},
        }
        assert media_quality.classify_quality_report(report, "prop") == "unreviewed"

- [ ] Step 2: 使用以下固定优先级实现分类：

    1. 技术检查失败 -> hard_fail；
    2. 视觉模型不可用或请求异常 -> unreviewed；
    3. JSON 无效或必需证据缺失 -> hard_fail；
    4. 必需契约明确失败 -> hard_fail；
    5. 有具体但非阻断问题或低分无失败契约 -> warning；
    6. 证据完整且无问题 -> passed。

    warning 不自动再次付费生成；只有 hard_fail 进入现有一次重试。

- [ ] Step 3: 修改 image_allows_automatic_reference()，只有 passed 或 Mock 的显式测试豁免可以自动绑定；warning 和 unreviewed 仍可显示和手工确认，但不能静默成为新镜头参考图。

- [ ] Step 4: 运行 pytest tests/test_media_quality.py tests/test_asset_quality_final_fixes.py tests/test_asset_reference_gate.py tests/test_shot_production_state.py -q。

---

## Task 3: 把 warning 和 unreviewed 保存为候选版本

**Files:**

- Modify: src/utils/asset_versions.py
- Modify: src/services/asset_generation.py
- Modify: src/api/context.py
- Modify: src/api/routes/assets.py
- Modify: src/schemas/character.py
- Modify: src/schemas/scene_image.py
- Modify: src/schemas/prop.py
- Test: tests/test_asset_quality_final_fixes.py
- Test: tests/test_wan_character_references.py

**Interfaces:**

- ImageAssetVersions.stage_candidate(asset_type, asset_id, view_type, source_path, prompt, source, metadata) -> dict。
- ImageAssetVersions.accept_candidate(version_id) -> history。
- ImageAssetVersions.reject_candidate(version_id) -> history。
- 既有 activate() 继续支持普通历史版本切换。

- [ ] Step 1: 增加原活动版本不变的失败测试。

    def test_warning_candidate_does_not_replace_active_image():
        before = active_version_id()
        result = generate_with_warning_report()
        assert result["review_pending"] is True
        assert active_version_id() == before
        assert result["pending_review_version_id"]

- [ ] Step 2: 实现 stage_candidate()。候选文件放在现有资产 history 目录，记录 active=false、pending_review=true、review_verdict、quality_report、source 和 path，不移动或覆盖当前活动文件。

- [ ] Step 3: 实现 accept_candidate()。使用现有 activate() 的路径校验和项目锁，拒绝 hard_fail 候选；接受后只把指定候选设为 active，并清除 pending 字段。

- [ ] Step 4: 实现 reject_candidate()。保留历史记录并标记 rejected=true，清除 pending 字段，不改变 active 版本，不删除 output 资产。

- [ ] Step 5: 增加两个接口：

    POST /api/projects/{project_id}/image-history/candidate/accept
    POST /api/projects/{project_id}/image-history/candidate/reject

    请求必须包含 asset_type、asset_id、view_type、version_id，并验证项目、资产、版本和 pending 状态。

- [ ] Step 6: 运行 pytest tests/test_asset_quality_final_fixes.py tests/test_wan_character_references.py -q，确认警告结果不替换活动版本，接受后可以正常切换。

---

## Task 4: 明确单图和组图的重试与原子性

**Files:**

- Modify: src/api/routes/jobs.py
- Modify: src/services/asset_generation.py
- Modify: src/api/routes/job_helpers.py
- Test: tests/test_asset_generation_api.py
- Test: tests/test_prop_generation_flow.py
- Test: tests/test_wan_character_references.py

**Interfaces:**

- 单图任务返回 review_verdict、review_pending、pending_review_version_id、retry_reason。
- 组图任务返回 review_verdicts、review_pending、pending_review_group_id；只有完整组通过暂存后才返回可确认的 group_id。

- [ ] Step 1: 测试硬失败重试一次、warning 不重试、组图不产生部分活动版本。

- [ ] Step 2: 保留现有两次尝试，但只对 hard_fail 使用新 seed；审核不可用不重试，避免把网络故障误当作图片质量问题。

- [ ] Step 3: warning 和 unreviewed 只生成一次并暂存候选，任务显示“生成完成，等待审核确认”，不能显示成“生成失败”。

- [ ] Step 4: 组图先审核全部视图，再一次性暂存。任意 hard_fail 都保持全部活动视图不变；完整 warning 组作为一个候选组，不能只接受其中一张造成身份链断裂。

- [ ] Step 5: 运行 pytest tests/test_asset_generation_api.py tests/test_prop_generation_flow.py tests/test_wan_character_references.py -q。

---

## Task 5: 独立视觉审核配置和连接测试

**Files:**

- Modify: src/config.py
- Modify: src/api/routes/settings.py
- Modify: web/js/pages/settings.js
- Modify: tests/test_media_quality.py
- Test: tests/test_settings.py

**Interfaces:**

- GET /api/settings/visual-review-capabilities。
- POST /api/settings/test-visual-review。
- 状态值为 ok、disabled、missing_key、auth_error、model_unavailable、network_error、request_error。

- [ ] Step 1: 增加代理地址不能启用 Qwen 的测试。

    def test_visual_review_does_not_infer_qwen_from_nonofficial_gateway(monkeypatch):
        monkeypatch.setattr(config, "HAPPYHORSE_API_KEY", "key")
        monkeypatch.setattr(config, "HAPPYHORSE_BASE_URL", "https://proxy.example.com")
        assert media_quality._vision_capability()[0] is False

- [ ] Step 2: 增加显式 REVIEW_LLM_PROVIDER、REVIEW_LLM_MODEL、REVIEW_LLM_API_KEY、REVIEW_LLM_BASE_URL 配置字段。保留当前官方 DashScope Happy Horse 回退一个迁移周期，同时返回 legacy_inferred=true 并在设置页提示。用户保存一次显式视觉配置后，不再依赖视频凭证推断。

- [ ] Step 3: 连接测试只发送一张小的有效图片和短文本到视觉聊天接口，不调用图片或视频生成接口，统一返回分类错误并脱敏。

- [ ] Step 4: 设置页显示启用状态、供应商、模型、端点主机、最近测试结果、继承视频凭证警告和“连接测试会消耗视觉模型 tokens”提示。

- [ ] Step 5: 运行 pytest tests/test_media_quality.py tests/test_settings.py -q。

---

## Task 6: 在资产界面展示证据并提供操作

**Files:**

- Modify: web/js/components/image-editor.js
- Modify: web/js/components/asset-cards.js
- Modify: web/js/components/task-drawer.js
- Modify: web/js/core/job-events.js
- Modify: web/css/assets.css
- Test: tests/test_frontend_modules.py

**Interfaces:**

- renderQualityReport(report) 返回稳定 HTML，包含结论、模型、评分、技术检查、契约证据。
- 候选按钮调用候选接受/拒绝接口，只刷新受影响的资产卡和编辑器。

- [ ] Step 1: 为 passed、warning、hard_fail、unreviewed 写模块测试，断言页面显示实际 evidence 而不是只有“失败”。

- [ ] Step 2: 在图片编辑器增加质量面板，显示审核是否真正执行、模型名称、结论、评分、技术检查、逐项契约证据和具体问题。

- [ ] Step 3: warning 和 unreviewed 显示“接受为当前版本”“重新生成”“放弃候选”；hard_fail 禁止接受；组图使用一个组级接受按钮。

- [ ] Step 4: 修改任务抽屉和 SSE 局部补丁，把 review_pending 显示为“生成完成，等待审核确认”，不能显示成失败；不重建正在编辑的提示词和光标。

- [ ] Step 5: 运行 pytest tests/test_frontend_modules.py -q。

---

## Task 7: 为上传图片增加显式视觉复核，不增加隐性费用

**Files:**

- Modify: src/api/routes/assets.py
- Modify: web/js/components/image-editor.js
- Modify: web/js/components/asset-cards.js
- Test: tests/test_asset_quality_final_fixes.py

**Interfaces:**

- POST /api/projects/{project_id}/image-history/review。
- 请求包含 asset_type、asset_id、view_type，可选 version_id。
- 只写入审核报告，不生成、不替换活动版本。

- [ ] Step 1: 测试上传接口不会调用视觉模型，仍返回 unreviewed。

- [ ] Step 2: 实现显式复核接口，复用同一资产类型提示词和审核契约，审核历史或活动图片，不改变图片路径。

- [ ] Step 3: UI 使用“视觉检查（会调用模型）”按钮，显示模型和最近结果；未配置视觉模型时禁用并说明原因。

- [ ] Step 4: 运行 pytest tests/test_asset_quality_final_fixes.py -q。

---

## Task 8: 全量验证与发布

**Files:**

- Modify: README.md
- Modify: docs/README.md
- Test: tests/test_media_quality.py
- Test: tests/test_asset_generation_api.py
- Test: tests/test_frontend_modules.py

- [ ] Step 1: 运行 pytest -q，预期所有现有测试通过。

- [ ] Step 2: 运行 python -m compileall src，以及变更 JS 文件的 node --check；扫描新增文本中不得出现 API Key。

- [ ] Step 3: 使用 Mock 项目验证不会发送视觉请求、资产仍可查看、历史和自动参考兼容。

- [ ] Step 4: 使用一张已有本地图片做一次真实 Qwen 视觉连接测试，不创建图片或视频生成任务，验证返回模型、结论和证据。

- [ ] Step 5: 构造一次 warning，确认旧活动版本不变；构造一次 hard_fail，确认只重试一次；接受候选后确认只有目标视图切换。

- [ ] Step 6: 在 README 中记录四种审核结论、一次重试规则、组图原子性、上传图片默认不审核和视觉测试消耗 tokens 的事实。

---

## 方案自审

### 规则是否合理

- 技术检查仍由 Pillow 和 FFmpeg 等确定性工具完成，不把文件损坏、空白和尺寸问题交给模型猜。
- 场景的室内外边界、固定陈设和空间方向仍只在提示词明确要求时检查，避免泛化场景被无关约束误拒。
- 角色背面、侧面和全身视图保持严格，因为这些错误会直接破坏视频参考图用途。
- 单纯低分或“可能不一致”降级为 warning；只有明确失败证据才 hard_fail，降低误杀。
- 每个阻断结论必须带可见证据，用户可以判断是提示词、生成模型还是审核模型的问题。

### 审核模型是否合适

- qwen-vl-plus 适合作为第一轮可见缺陷和结构化证据审核；当前真实请求已经返回 HTTP 200，模型可用性得到实测确认。
- 它不被当作人物身份、细微风格和复杂空间关系的绝对裁判。此类不确定结果进入 warning，交给用户确认。
- 不默认切换更贵模型，因为模型升级不能解决版本替换和用户不可见的问题，只会增加延迟和费用。

### 不通过后的处理是否合理

- 技术失败和明确 hard_fail 自动重试一次，因为新 seed 可能纠正供应商随机错误。
- warning 不自动重试，因为问题可能来自审核模型的不确定，而不是图片本身；自动重试会增加费用且不能保证改善。
- 组图必须原子处理，否则可能出现三张新视图和一张旧视图混用，破坏人物或场景一致性。
- 候选版本不覆盖活动版本，用户可以接受、放弃或重新生成，失败可恢复且历史可追溯。
- unreviewed 可查看但不能自动绑定，避免审核服务暂时不可用时静默降低后续视频质量。

### 兼容性、成本和边界

- 旧 quality_report、历史版本切换、Mock 和项目隔离均保留。
- 视觉审核配置与视频凭证解耦，旧官方 DashScope 配置提供一次迁移回退，不会突然让已有项目失效。
- 上传图片不产生隐性模型调用，手动视觉检查明确提示会消耗 tokens。
- 测试不创建真实图片或视频任务；真实验证只使用已有本地图片和单次视觉请求。
- 方案不改善图片生成模型本身；供应商持续违反正确提示词时，审核只能报告并阻止污染活动版本，不能替供应商修正画面。

### 最终结论

这不是单纯增加“审核更严格”的方案，而是修正当前已确认的状态管理缺陷：warning 不能再静默激活，unreviewed 不能再被当作通过，hard_fail 才触发有限重试，组图保持原子性，用户能看到逐项证据并决定候选版本。

