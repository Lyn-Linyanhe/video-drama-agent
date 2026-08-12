# 道具提取与按需参考图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将道具从“LLM 罗列 + 固定六视图拼图”改为经过严格关键性校验的项目资产，并采用母版确认、按镜头需求补充视图和统一版本链路。

**Architecture:** 结构化解析阶段由 LLM 输出关键道具及证据，后端只做确定性校验、镜头绑定修复和删除记录，不再按数量裁剪或重新猜剧情。道具生成统一经 `asset_generation` 与 `asset_prompt_composer`，先生成可确认的单张母版，只有视图需求达到两个及以上且模型支持时才调用参考组图；视频任务只读取已确认的独立视图，不再把联系页作为参考图。

**Tech Stack:** Python 3、Pydantic、FastAPI、SQLite/项目状态 JSON、现有图片供应商适配器、FFmpeg/现有视频任务系统、pytest、原生前端 JavaScript。

## Global Constraints

- 不设置“最多 5 个关键道具”的固定上限；只保留满足关键道具定义的物品。
- 道具必须是实际出镜实体、至少绑定两个不同镜头、每个绑定镜头有可见或操作证据，并提供 `critical_reason` 与 `critical_evidence`。
- 连续性道具可以保留，但必须在至少两个镜头中清晰出现且外观一致性具有实际意义。
- 开封、破损、沾血、递交等剧情状态留在视频镜头提示词的 `prop_state`，不生成独立状态资产。
- 首次生成使用稳定 seed；失败重试沿用原 seed；用户主动重新生成使用新随机 seed；所有历史版本保存实际 seed。
- 统一使用 SHA-256 派生 seed，删除 `sum(ord(...))` 等旧算法。
- 保留兼容 HTTP 路径，但所有兼容入口只能转发到统一服务，不得保留独立道具生成逻辑。
- 不拆分 `studio.html` 或 `api_server.py`，不引入新的图片供应商，不把 2×3 联系页传给视频模型。

## File Map

- Modify `src/schemas/prop.py`: 扩展道具图片版本和母版/参考关系字段，保留旧字段读取兼容。
- Modify `src/graph/state.py`: 增加项目级 `prop_view_requirements` 与镜头道具视图偏好状态。
- Modify `src/schemas/script.py` and the script-analysis schemas: 约束 `PropAsset` 的关键性字段和 LLM 输出契约。
- Modify `src/utils/visual_asset_manifest.py`: 实现确定性关键道具校验、绑定修复、删除原因及数量偏高警告。
- Modify `src/agents/script_agent.py`: 接入新校验结果，不因旧 `shot_ids` 不一致直接失败。
- Create or modify `src/services/asset_seed.py`: 提供唯一的 `derive_asset_seed(...)` 和主动重生成 seed 规则。
- Modify `src/services/asset_generation.py`: 统一道具提示词、母版生成、参考组图/单视图生成和版本快照。
- Modify `src/api/routes/assets.py` and `src/api/context.py`: 新增道具母版、确认、视图需求和按视图生成接口；旧接口仅转发。
- Modify `src/agents/image_gen_agent.py`: 删除或停用 `generate_props`、`_generate_prop_image`、固定六视图实现，保留底层供应商调用适配能力。
- Modify `src/agents/video_gen_agent.py`: 只选择当前镜头所需的独立道具视图和已确认母版，不使用 `standard_views` 联系页。
- Modify frontend asset modules in `web/` or the current asset section of `studio.html`: 母版确认、视图需求、按需生成、历史版本和状态展示。
- Modify `tests/test_prop_assets.py`, `tests/test_asset_generation_api.py`, `tests/test_asset_migration.py`, and add focused tests for seed, view requirements and video reference selection.
- Update `README.md` and migration notes with the new asset lifecycle and compatibility rules.

---

### Task 1: 关键道具模型、LLM 契约与确定性清理

**Files:**
- Modify: `src/schemas/script.py` and the schema module that defines `PropAsset`/`shot_bindings`.
- Modify: `src/utils/visual_asset_manifest.py`.
- Modify: `src/agents/script_agent.py`.
- Test: `tests/test_prop_assets.py`, `tests/test_script_analysis.py` (or the existing script-analysis test module).

**Interfaces:**
- Produce `validate_and_clean_props(script) -> PropValidationResult`.
- `PropValidationResult` contains `removed_props: list[dict]`, `warnings: list[str]`, and the cleaned script.
- A valid prop has `critical_reason` in `{plot, identity, handoff, continuity}`, non-empty `critical_evidence`, at least two valid shot bindings, and a visual description containing appearance rather than only usage.

- [ ] **Step 1: Write failing tests**

~~~python
def test_valid_eight_props_are_not_truncated():
    script = make_script_with_props(count=8, shots_per_prop=2, critical_reason="plot")
    result = validate_and_clean_props(script)
    assert len(result.cleaned_script.props) == 8
    assert result.removed_props == []

def test_single_shot_and_household_props_are_removed_with_reason():
    script = make_script_with_props([
        prop("one_shot", shot_ids=[1], critical_reason="plot"),
        prop("chair", shot_ids=[1, 2], critical_reason="continuity", description="普通家具"),
    ])
    result = validate_and_clean_props(script)
    assert {item["prop_id"] for item in result.removed_props} == {"one_shot", "chair"}
    assert all(item["reason"] for item in result.removed_props)

def test_inconsistent_llm_shot_ids_are_rebuilt_from_bindings():
    script = make_script_with_prop_bindings("letter", declared_shots=[11, 13, 14], actual_shots=[11, 13])
    result = validate_and_clean_props(script)
    assert result.cleaned_script.prop("letter").shot_ids == [11, 13]

def test_missing_critical_evidence_is_not_a_parse_fatal_error():
    result = validate_and_clean_props(make_prop_without_evidence())
    assert result.cleaned_script.props == []
    assert result.removed_props[0]["reason"] == "missing_critical_evidence"
~~~

- [ ] **Step 2: Run the focused tests and verify the old fixed-limit behavior fails**

Run: `pytest tests/test_prop_assets.py -k "truncated or one_shot or inconsistent or evidence" -q`

Expected: FAIL because the current validator still applies the fixed maximum and treats inconsistent references as a fatal parse error.

- [ ] **Step 3: Implement the contract and cleaner**

Add the fields below to the structured prop model and use a single deterministic pipeline:

~~~python
class PropAsset(BaseModel):
    prop_id: str
    name: str
    description: str
    critical_reason: Literal["plot", "identity", "handoff", "continuity"]
    critical_evidence: str
    shot_ids: list[int] = Field(default_factory=list)
    is_handheld: bool = False
    aliases: list[str] = Field(default_factory=list)

def validate_and_clean_props(script):
    cleaned = rebuild_prop_bindings(script)
    removed = remove_invalid_props(cleaned)
    warnings = prop_density_warnings(cleaned)
    return PropValidationResult(cleaned_script=cleaned, removed_props=removed, warnings=warnings)
~~~

Remove `MAX_CRITICAL_PROPS` pruning. Keep a non-blocking density warning when the count exceeds `max(8, ceil(scene_count / 2))`. Ensure the script agent stores `removed_props` and warnings in the analysis result and continues when only prop consistency errors are found.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_prop_assets.py tests/test_script_analysis.py -q`

Expected: PASS, including old-project compatibility fixtures.

- [ ] **Step 5: Commit**

~~~text
git add src/schemas/script.py src/utils/visual_asset_manifest.py src/agents/script_agent.py tests/test_prop_assets.py tests/test_script_analysis.py
git commit -m "fix: validate critical props from evidence and bindings"
~~~

### Task 2: 道具视图需求与项目状态

**Files:**
- Modify: `src/graph/state.py` and project-state serialization in `src/api/context.py`.
- Modify: `src/schemas/prop.py`.
- Test: `tests/test_prop_view_requirements.py` (create).

**Interfaces:**
- `PropViewRequirement(prop_id, views, reasons, source, signature)` where `views` is a unique subset of `front/back/left/right/top/bottom`.
- `set_prop_view_requirements(project_id, prop_id, requirement)` persists the value and returns the normalized requirement.
- Default requirement is `front`; auto requirements are derived from shot camera/action descriptions, while manual additions are retained.

- [ ] **Step 1: Write failing tests**

~~~python
def test_default_prop_requirement_is_front_only():
    assert infer_prop_view_requirement(prop_id="key", shots=[front_shot()]).views == ["front"]

def test_back_and_side_views_are_added_only_when_camera_requires_them():
    requirement = infer_prop_view_requirement(prop_id="key", shots=[back_shot(), left_side_shot()])
    assert requirement.views == ["front", "back", "left"]

def test_manual_views_survive_reanalysis():
    saved = PropViewRequirement(prop_id="key", views=["front", "top"], source="manual")
    merged = merge_prop_view_requirement(saved, auto_views=["front"])
    assert merged.views == ["front", "top"]
~~~

- [ ] **Step 2: Run and observe failure**

Run: `pytest tests/test_prop_view_requirements.py -q`

Expected: FAIL because the project state has no view requirement model or merge function.

- [ ] **Step 3: Implement normalization and persistence**

Use the following stable ordering and signature inputs:

~~~python
PROP_VIEWS = ("front", "back", "left", "right", "top", "bottom")

def normalize_prop_views(views):
    return [view for view in PROP_VIEWS if view in set(views)] or ["front"]

def merge_prop_view_requirement(saved, auto_views):
    views = normalize_prop_views([*auto_views, *(saved.views if saved else [])])
    source = "manual" if saved and saved.source == "manual" else "auto"
    signature = hashlib.sha256("|".join(views).encode("utf-8")).hexdigest()[:16]
    return PropViewRequirement(prop_id=saved.prop_id if saved else "", views=views, source=source, signature=signature)
~~~

Persist `prop_view_requirements` in project state and preserve manual entries when a new script version is analyzed. Do not generate state variants such as opened/broken/bloody; those remain shot-level `prop_state`.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_prop_view_requirements.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

~~~text
git add src/graph/state.py src/api/context.py src/schemas/prop.py tests/test_prop_view_requirements.py
git commit -m "feat: persist per-shot prop view requirements"
~~~

### Task 3: 统一 seed 与旧道具生成路径清理

**Files:**
- Create: `src/services/asset_seed.py`.
- Modify: `src/services/asset_generation.py`, `src/api/context.py`, `src/agents/character_agent.py`, `src/agents/image_gen_agent.py`, `src/api/routes/assets.py`.
- Test: `tests/test_asset_seed.py` (create), existing prop generation tests.

**Interfaces:**
- `derive_asset_seed(project_id, asset_type, asset_id, view_type, prompt_template_version) -> int`.
- `resolve_generation_seed(..., retry_of: Optional[AssetVersion], user_regenerate: bool) -> int`.

- [ ] **Step 1: Write failing tests**

~~~python
def test_seed_is_stable_across_processes():
    assert derive_asset_seed("p1", "prop", "key", "front", 2) == derive_asset_seed("p1", "prop", "key", "front", 2)

def test_active_retry_reuses_seed_but_user_regeneration_changes_it():
    old = AssetVersion(seed=123)
    assert resolve_generation_seed("p1", "prop", "key", "front", 2, retry_of=old, user_regenerate=False) == 123
    assert resolve_generation_seed("p1", "prop", "key", "front", 2, retry_of=old, user_regenerate=True) != 123
~~~

- [ ] **Step 2: Run and observe failure**

Run: `pytest tests/test_asset_seed.py -q`

Expected: FAIL because no shared seed service exists.

- [ ] **Step 3: Implement SHA-256 derivation and route all asset types through it**

~~~python
def derive_asset_seed(project_id, asset_type, asset_id, view_type, prompt_template_version):
    key = "|".join(str(value).strip() for value in (project_id, asset_type, asset_id, view_type, prompt_template_version))
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % 999_999 + 1
~~~

Use a new random seed only for explicit user regeneration. Replace all `sum(ord(...))` occurrences for character, scene and prop generation and compatibility endpoints. Remove the prop-specific generation behavior from `ImageGenerationAgent.generate_props` and `_generate_prop_image`; the agent may delegate to the service while the service owns persistence and prompt composition. Change `_legacy_regenerate_image_asset` to a thin call to the unified service.

- [ ] **Step 4: Run regression tests and search for old algorithms**

Run: `pytest tests/test_asset_seed.py tests/test_prop_assets.py tests/test_asset_generation_api.py -q`

Then run: `rg -n "sum\(ord|generate_props|_generate_prop_image|_legacy_regenerate_image_asset" src`

Expected: no production call site uses old seed arithmetic or a separate prop-generation implementation.

- [ ] **Step 5: Commit**

~~~text
git add src/services/asset_seed.py src/services/asset_generation.py src/api/context.py src/agents/character_agent.py src/agents/image_gen_agent.py src/api/routes/assets.py tests/test_asset_seed.py
git commit -m "refactor: unify asset seeds and prop generation entrypoints"
~~~

### Task 4: 母版确认与按需参考组图

**Files:**
- Modify: `src/schemas/prop.py`, `src/services/asset_generation.py`.
- Modify: `src/api/routes/assets.py`, `src/api/context.py`.
- Test: `tests/test_prop_generation_flow.py` (create).

**Interfaces:**
- `POST /api/projects/{project_id}/props/{prop_id}/master/generate`
- `POST /api/projects/{project_id}/props/{prop_id}/master/confirm`
- `GET/PUT /api/projects/{project_id}/props/{prop_id}/view-requirements`
- `POST /api/projects/{project_id}/props/{prop_id}/views/{view_type}/generate`
- Existing `/api/projects/{project_id}/assets/prop/{prop_id}/generate` remains a compatibility wrapper.

- [ ] **Step 1: Write failing tests**

~~~python
def test_master_generation_is_single_image_and_requires_confirmation(client, project_id):
    response = client.post(f"/api/projects/{project_id}/props/key/master/generate")
    assert response.status_code == 202
    assert response.json()["generated"][0]["view_type"] == "front"
    assert response.json()["generated"][0]["needs_confirmation"] is True

def test_group_generation_requires_confirmed_master_and_exact_views(client, project_id):
    assert client.post(f"/api/projects/{project_id}/props/key/views/back/generate").status_code == 409
    confirm_master(client, project_id, "key")
    response = client.post(f"/api/projects/{project_id}/props/key/views/generate", json={"views": ["back", "left"]})
    assert response.status_code == 202
    assert response.json()["generation_mode"] == "reference_group"

def test_short_group_result_does_not_activate_partial_views(mock_wan, client, project_id):
    mock_wan.return_n_images(1)
    response = client.post(f"/api/projects/{project_id}/props/key/views/generate", json={"views": ["back", "left"]})
    assert response.status_code == 502
    assert no_new_active_prop_views(project_id, "key")
~~~

- [ ] **Step 2: Run and observe failure**

Run: `pytest tests/test_prop_generation_flow.py -q`

Expected: FAIL because the current implementation always creates a six-view contact sheet and has no master confirmation state.

- [ ] **Step 3: Implement generation state and service flow**

Persist these fields on every image version:

~~~python
is_master: bool = False
needs_confirmation: bool = False
master_version_id: str | None = None
reference_version_ids: list[str] = Field(default_factory=list)
derived_from_version_id: str | None = None
generation_mode: Literal["master", "single", "reference_group", "legacy_sheet"] = "single"
model: str | None = None
resolution: str | None = None
is_stale: bool = False
requirement_signature: str | None = None
~~~

Generate the master as one clean front image. Block view generation with HTTP 409 until the master is confirmed. For two or more requested views, call the existing Wan reference-group client with the confirmed master as the first reference, request exactly the needed count, verify every returned image downloads successfully, and activate nothing when the count/order is incomplete. For one requested view, use a single reference-edit request and update only that view.

- [ ] **Step 4: Run focused flow tests**

Run: `pytest tests/test_prop_generation_flow.py tests/test_asset_generation_api.py -q`

Expected: PASS, including cancellation, project isolation and failed download cases.

- [ ] **Step 5: Commit**

~~~text
git add src/schemas/prop.py src/services/asset_generation.py src/api/routes/assets.py src/api/context.py tests/test_prop_generation_flow.py
git commit -m "feat: add confirmed prop master and on-demand reference views"
~~~

### Task 5: 统一道具提示词与完整版本快照

**Files:**
- Modify: `src/services/asset_prompt_composer.py`, `src/services/asset_generation.py`.
- Modify: `src/schemas/prop.py` and task result schemas.
- Test: `tests/test_prop_prompt_composer.py` (create).

**Interfaces:**
- `compose_asset_prompt(state, asset_type="prop", asset_id, view_type, custom_instructions="", generation_mode="master") -> PromptComposition`.
- `PromptComposition` contains `effective_prompt`, `effective_negative_prompt`, `negative_prompt_mode`, `template_version` and `requirement_signature`.

- [ ] **Step 1: Write failing tests**

~~~python
def test_prop_prompt_contains_identity_and_view_constraints():
    prompt = compose_asset_prompt(state, "prop", "key", "back", generation_mode="single")
    assert "道具身份" in prompt.effective_prompt
    assert "背面" in prompt.effective_prompt
    assert "不得生成联系页" in prompt.effective_prompt

def test_user_text_is_additive_and_does_not_replace_system_constraints():
    prompt = compose_asset_prompt(state, "prop", "key", "front", custom_instructions="金属表面有划痕")
    assert "金属表面有划痕" in prompt.effective_prompt
    assert "单个道具" in prompt.effective_prompt
~~~

- [ ] **Step 2: Run and observe failure**

Run: `pytest tests/test_prop_prompt_composer.py -q`

Expected: FAIL because the old prop prompt path still produces `standard_views` contact-sheet instructions.

- [ ] **Step 3: Implement one composer and persist its snapshot**

The service must save the exact composition beside each task/version, including provider/model/resolution, mode, seed and requirement signature. For providers without independent negative prompts, append a Chinese “避免/禁止” paragraph to the main prompt and record `negative_prompt_mode="merged"`; never claim an unused negative field was sent.

- [ ] **Step 4: Run prompt and snapshot tests**

Run: `pytest tests/test_prop_prompt_composer.py tests/test_asset_generation_api.py -q`

Expected: PASS, with compatibility endpoint responses matching the unified prompt snapshot.

- [ ] **Step 5: Commit**

~~~text
git add src/services/asset_prompt_composer.py src/services/asset_generation.py src/schemas/prop.py tests/test_prop_prompt_composer.py
git commit -m "fix: unify prop prompt composition and version snapshots"
~~~

### Task 6: 视频任务按镜头选择独立道具参考图

**Files:**
- Modify: `src/agents/video_gen_agent.py` and the production/video service that builds references.
- Modify: `src/api/context.py` if reference selection is assembled there.
- Test: `tests/test_video_prop_references.py` (create).

**Interfaces:**
- `select_prop_references(state, shot_id) -> list[PropImage]`.
- Selection order: active non-stale view explicitly requested by the shot, confirmed master, then no reference; legacy `standard_views` is preview-only.

- [ ] **Step 1: Write failing tests**

~~~python
def test_video_uses_requested_independent_view_before_master():
    refs = select_prop_references(state_with_prop_views(), shot_id=12)
    assert refs[0].view_type == "back"
    assert all(ref.view_type != "standard_views" for ref in refs)

def test_video_falls_back_to_confirmed_master_when_no_view_is_needed():
    refs = select_prop_references(state_with_confirmed_master(), shot_id=3)
    assert [ref.view_type for ref in refs] == ["front"]
~~~

- [ ] **Step 2: Run and observe failure**

Run: `pytest tests/test_video_prop_references.py -q`

Expected: FAIL because current video reference construction still passes the contact sheet.

- [ ] **Step 3: Implement selection and state checks**

Reject stale or unconfirmed versions, preserve manual shot references, and pass each selected image as its own reference in the provider-specific order. Keep existing character and scene reference behavior unchanged.

- [ ] **Step 4: Run video regression tests**

Run: `pytest tests/test_video_prop_references.py tests/test_video_generation.py -q`

Expected: PASS; no video payload contains a prop `standard_views` path.

- [ ] **Step 5: Commit**

~~~text
git add src/agents/video_gen_agent.py src/api/context.py tests/test_video_prop_references.py
git commit -m "fix: pass only shot-required prop references to video generation"
~~~

### Task 7: 前端道具资产工作流

**Files:**
- Modify: the current prop asset UI in `studio.html` and/or `web/js/pages/assets.js`, `web/js/components/asset-card.js`.
- Modify: related asset CSS under `web/css/` or the current stylesheet.
- Test: browser smoke test or existing frontend test harness.

**Interfaces:**
- UI consumes the view-requirements and master/view generation endpoints from Task 4.
- UI displays `needs_confirmation`, `is_stale`, `generation_mode`, `model`, `resolution`, `seed`, and history source links.

- [ ] **Step 1: Add a failing browser assertion**

~~~javascript
await expect(page.getByText("确认道具母版")).toBeVisible();
await expect(page.getByText("standard_views")).toHaveCount(0);
~~~

- [ ] **Step 2: Run the smoke test and verify the current UI fails**

Run the existing browser test command documented in `README.md`, or run the local Playwright smoke test against the started FastAPI server.

Expected: FAIL because the card currently exposes the six-view sheet as the primary asset.

- [ ] **Step 3: Implement stable local UI updates**

Add actions for “生成母版”, “确认母版”, “补充所需视图”, per-view regenerate and history activation. Keep the card single-column without a page-level vertical scroll; show contact sheets only in history/preview. SSE updates must patch the prop card and must not replace an open prompt editor.

- [ ] **Step 4: Run browser smoke coverage**

Verify: master generation, confirmation, one-view generation, two-view group generation, failed group result, history switch, project switch isolation, and manual shot reference preservation.

- [ ] **Step 5: Commit**

~~~text
git add studio.html web tests/browser
git commit -m "feat: expose confirmed prop master and on-demand views"
~~~

### Task 8: 迁移、兼容层、文档与全量验证

**Files:**
- Modify: migration module used by `tests/test_asset_migration.py` and project-state loader.
- Modify: `src/api/routes/assets.py` compatibility handlers.
- Modify: `README.md`.
- Test: `tests/test_asset_migration.py`, full test suite.

**Interfaces:**
- Existing `standard_views` files remain readable as historical previews only.
- Re-running migration is idempotent and never moves the same file twice.

- [ ] **Step 1: Write migration and compatibility tests**

~~~python
def test_legacy_standard_views_are_history_only(tmp_path):
    migrate_project(tmp_path)
    state = load_project_state(tmp_path)
    assert state.prop_images[0].generation_mode == "legacy_sheet"
    assert state.prop_images[0].is_master is False

def test_prop_migration_is_idempotent(tmp_path):
    first = migrate_project(tmp_path)
    second = migrate_project(tmp_path)
    assert second.moved_files == []
    assert load_project_state(tmp_path) == load_project_state(tmp_path)
~~~

- [ ] **Step 2: Run migration tests and fix compatibility behavior**

Run: `pytest tests/test_asset_migration.py tests/test_job_api.py -q`

Expected: PASS; old endpoints delegate to the unified service and old files are not selected for new video tasks.

- [ ] **Step 3: Update documentation**

Document the lifecycle `master -> confirmed -> requested views`, the meaning of stable/retry/user-regenerate seeds, the four valid critical reasons, and the fact that `standard_views` is preview-only.

- [ ] **Step 4: Run the complete verification suite**

Run:

~~~text
pytest -q
python -m compileall src
rg -n "sum\(ord|MAX_CRITICAL_PROPS|standard_views.*video|generate_prop_sheet" src tests
~~~

Expected: all tests pass; the search has no active production call site for old seed arithmetic, fixed prop truncation, or contact-sheet video references. If the environment lacks `langgraph`, install the project dependency before rerunning; do not mark the implementation complete with an import-blocked test run.

- [ ] **Step 5: Commit the integrated change**

~~~text
git add src tests studio.html web README.md
git commit -m "feat: implement evidence-based props and on-demand reference assets"
~~~

## Self-Review Checklist

- The plan removes the fixed five-item limit while preserving hard validation and explicit removal reasons.
- It covers LLM contract changes, state persistence, prompt unification, version metadata, seed semantics, image generation, video reference selection, UI, migration and tests.
- All named interfaces are defined before later tasks consume them.
- No task depends on a hidden future refactor; compatibility endpoints remain available throughout.
- `standard_views` remains readable for history but cannot become a new video reference.
- No unresolved placeholders or “implement later” steps remain.
