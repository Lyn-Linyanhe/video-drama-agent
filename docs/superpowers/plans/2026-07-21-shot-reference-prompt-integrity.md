# 分镜参考图与提示词完整性修复 Implementation Plan

> For agentic workers: REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 让每个分镜视频任务都使用独立提示词和正确、有序、最新的参考图。

**Architecture:** 分镜参考项分为资产绑定项与手工项。资产绑定项按稳定身份追踪资产库的最新激活版本；手工项保留路径和排序。任务提交时冻结提示词、负面约束、参考图序列和策略，任务执行与视频版本只使用该快照。

**Tech Stack:** Python 3、FastAPI、Pydantic v2、SQLite 持久化任务、原生 ES Modules、pytest。

## Global Constraints

- 保持所有既有 API 路径、请求结构、SQLite 项目状态和历史媒体兼容。
- 仅以 scene_id 读写镜头提示词，禁止按数组下标回退。
- 资产库参考图随最新激活版本更新；用户在分镜页额外添加的参考图不改变。
- 测试仅使用 Mock 视频客户端，禁止提交付费视频任务。
- 不恢复全页轮询，任务事件不得重建视频或编辑输入框。

## File Structure

- Modify src/schemas/assets.py: 参考来源、资产版本和请求快照字段。
- Modify src/api/context.py: 身份目录、旧数据迁移、逐项同步与快照构造。
- Modify src/api/routes/production.py: 当前镜头保存和提交前快照。
- Modify src/api/routes/jobs.py: 快照消费与版本审计。
- Modify src/agents/video_gen_agent.py: 不可变提示词合成。
- Modify web/js/pages/production-actions.js: 草稿预览和提交审计展示。
- Modify tests/test_shot_production_state.py、tests/test_prompt_policy.py。
- Create tests/test_video_generation_snapshot.py。

### Task 1: Persist Reference Identity And Request Snapshots

**Files:**
- Modify: src/schemas/assets.py
- Test: tests/test_shot_production_state.py

**Interfaces:**
- ShotReference gains source, asset_version_id and stale fields.
- ShotProductionState gains last_generation_snapshot.
- ShotVideoVersion gains generation_snapshot.
- ProductionReferenceRequest gains source so a user-selected catalog image can remain manual.

- [ ] **Step 1: Write the failing persistence test**

    def test_reference_source_and_snapshot_round_trip():
        state = _state()
        state.shot_productions[1] = ShotProductionState(
            scene_id=1,
            references=[ShotReference(
                reference_id="character|Hero|front_full", source="asset",
                asset_type="character", asset_id="Hero", view_type="front_full",
                asset_version_id="hero-v2", image_path="hero-v2.png",
            )],
            last_generation_snapshot={"scene_id": 1, "provider_prompt": "镜头一"},
        )
        restored = _state_from_json(_state_to_json(state))
        assert restored.shot_productions[1].references[0].asset_version_id == "hero-v2"
        assert restored.shot_productions[1].last_generation_snapshot["provider_prompt"] == "镜头一"

- [ ] **Step 2: Run test to verify it fails**

Run: pytest tests/test_shot_production_state.py::test_reference_source_and_snapshot_round_trip -v

Expected: FAIL because the current schema does not accept these fields.

- [ ] **Step 3: Implement compatible fields**

    from typing import Literal

    class ShotReference(BaseModel):
        # existing fields stay unchanged
        source: Literal["asset", "manual"] = "asset"
        asset_version_id: str = ""
        stale: bool = False

    class ShotProductionState(BaseModel):
        # existing fields stay unchanged
        last_generation_snapshot: dict = Field(default_factory=dict)

    class ShotVideoVersion(ShotAsset):
        # existing fields stay unchanged
        generation_snapshot: dict = Field(default_factory=dict)

Old project JSON must validate through the defaults without rewriting state.

- [ ] **Step 4: Verify and commit**

Run: pytest tests/test_shot_production_state.py -v

Expected: PASS.

    git add src/schemas/assets.py tests/test_shot_production_state.py
    git commit -m "feat: persist storyboard reference snapshots"

### Task 2: Reconcile Only Asset-Bound References

**Files:**
- Modify: src/api/context.py
- Test: tests/test_shot_production_state.py

**Interfaces:**
- _asset_reference_key(reference: ShotReference) returns a tuple of asset_type, asset_id and view_type.
- _reconcile_shot_references(state: DramaState, scene_id: int) returns an ordered list of ShotReference values.

- [ ] **Step 1: Write failing update and manual-preservation tests**

    def test_reconcile_updates_changed_asset_and_keeps_manual_reference(tmp_path):
        state = _state_with_hero_and_room_assets(tmp_path, hero_version="hero-v1")
        state.shot_productions[1] = ShotProductionState(scene_id=1, references=[
            ShotReference(reference_id="character|Hero|front_full", source="asset",
                asset_type="character", asset_id="Hero", view_type="front_full",
                asset_version_id="hero-v1", image_path=str(tmp_path / "hero-v1.png")),
            ShotReference(reference_id="manual|moodboard|1", source="manual",
                asset_type="manual", asset_id="moodboard", view_type="image",
                image_path=str(tmp_path / "moodboard.png")),
        ])
        _replace_hero_active_asset(state, tmp_path, version_id="hero-v2")
        refs = _reconcile_shot_references(state, 1)
        assert refs[0].asset_version_id == "hero-v2"
        assert refs[1].image_path.endswith("moodboard.png")

    def test_default_references_do_not_match_assets_by_shared_file_path(tmp_path):
        state = _state_with_distinct_asset_identities_sharing_a_legacy_path(tmp_path)
        refs = _default_shot_references(state, 1)
        assert [(item.asset_type, item.asset_id) for item in refs] == [
            ("scene", "loc_room"), ("character", "Hero"),
        ]

- [ ] **Step 2: Run tests to verify failure**

Run: pytest tests/test_shot_production_state.py -k "reconcile or shared_file_path" -v

Expected: FAIL because the current reference builder uses a path-keyed lookup and auto import stops after one valid image.

- [ ] **Step 3: Implement identity-based reconciliation**

    def _asset_reference_key(reference: ShotReference) -> tuple[str, str, str]:
        return reference.asset_type, reference.asset_id, reference.view_type

    def _reconcile_shot_references(state: DramaState, scene_id: int) -> list[ShotReference]:
        production = _ensure_shot_production(state, scene_id)
        latest = {_asset_reference_key(item): item for item in _default_shot_references(state, scene_id)}
        reconciled = []
        for item in production.references:
            if item.source == "manual":
                reconciled.append(item)
                continue
            replacement = latest.pop(_asset_reference_key(item), None)
            reconciled.append(replacement or item.model_copy(update={"stale": True}))
        reconciled.extend(latest.values())
        production.references = reconciled
        return reconciled

Make _reference_catalog populate source="asset" and active image version_id. Replace the by_path lookup in _default_shot_references with reference ID or the three-part identity. After missing asset generation, _prepare_auto_references must call reconciliation for every scene.

- [ ] **Step 4: Add legacy migration coverage and implementation**

    def test_unresolved_legacy_path_reference_becomes_manual(tmp_path):
        state = _state()
        state.shot_productions[1] = ShotProductionState.model_validate({
            "scene_id": 1,
            "references": [{"reference_id": "old", "asset_type": "scene",
                "asset_id": "old", "view_type": "wide", "image_path": str(tmp_path / "old.png")}],
        })
        _migrate_legacy_shot_references(state)
        assert state.shot_productions[1].references[0].source == "manual"

Call _migrate_legacy_shot_references after loading shot_productions in _state_from_json. Exact identity matches become asset references with the active version; unresolved entries become manual references and retain their file paths.

- [ ] **Step 5: Verify and commit**

Run: pytest tests/test_shot_production_state.py -v

Expected: PASS.

    git add src/api/context.py tests/test_shot_production_state.py
    git commit -m "fix: reconcile storyboard references by asset identity"

### Task 3: Make Prompt Preparation Immutable

**Files:**
- Modify: src/agents/video_gen_agent.py
- Test: tests/test_prompt_policy.py

**Interfaces:**
- VideoGenerationAgent.prepare_shot_request returns an isolated ShotPrompt and effective prompt dictionary.

- [ ] **Step 1: Write the failing cross-scene mutation test**

    def test_prepare_shot_request_never_mutates_shared_scene_prompts():
        prompts = GenerationPrompts(api_provider="happyhorse", shots=[
            ShotPrompt(scene_id=1, prompt="镜头一：走进房间", duration_sec=5),
            ShotPrompt(scene_id=2, prompt="镜头二：关上门", duration_sec=5),
        ])
        prepared, _ = VideoGenerationAgent().prepare_shot_request(
            prompts.shots[1], generation_prompts=prompts, prompt_policy=PromptPolicy(),
            visual_design=None, scene_vision_descriptions=None, script=None,
        )
        assert prompts.shots[0].prompt == "镜头一：走进房间"
        assert prompts.shots[1].prompt == "镜头二：关上门"
        assert "镜头一" not in prepared.prompt
        assert "镜头二" in prepared.prompt

- [ ] **Step 2: Run test to verify failure**

Run: pytest tests/test_prompt_policy.py::test_prepare_shot_request_never_mutates_shared_scene_prompts -v

Expected: FAIL because _inject_shot_context mutates its input.

- [ ] **Step 3: Implement the copied prompt path**

    def prepare_shot_request(self, shot, *, generation_prompts, prompt_policy,
                             visual_design, scene_vision_descriptions, script):
        isolated = shot.model_copy(deep=True)
        prompts = generation_prompts.model_copy(deep=True) if generation_prompts else GenerationPrompts(
            api_provider=self.api_client.provider_name, shots=[isolated],
        )
        prompts.shots = [isolated if item.scene_id == isolated.scene_id else item for item in prompts.shots]
        self._inject_shot_context(isolated, visual_design, scene_vision_descriptions, script)
        effective = compose_video_prompt(self.api_client.provider_name, prompts, isolated, prompt_policy)
        return isolated.model_copy(update={
            "prompt": effective["provider_prompt"],
            "negative_prompt": effective["provider_negative_prompt"],
        }), effective

Update generate_shot to pass only the returned copy to _generate_with_retry.

- [ ] **Step 4: Verify and commit**

Run: pytest tests/test_prompt_policy.py -v

Expected: PASS.

    git add src/agents/video_gen_agent.py tests/test_prompt_policy.py
    git commit -m "fix: isolate storyboard prompts before generation"

### Task 4: Freeze The Actual Provider Request At Submission

**Files:**
- Modify: src/api/context.py
- Modify: src/api/routes/production.py
- Modify: src/api/routes/jobs.py
- Create: tests/test_video_generation_snapshot.py

**Interfaces:**
- _build_video_generation_snapshot(state, scene_id, policy) returns raw prompts, effective prompts, ordered references and a policy copy.
- _policy_prompt_provider(policy) returns "happyhorse" for happyhorse and wan policies, otherwise returns policy["provider"].
- Every video.generate payload contains generation_snapshot.

- [ ] **Step 1: Write the failing Mock-provider equality test**

    async def test_video_job_uses_and_persists_frozen_snapshot(monkeypatch, project_state):
        captured = {}
        async def fake_generate_shot(shot, reference_paths, **kwargs):
            captured.update(prompt=shot.prompt, references=list(reference_paths))
            return ShotAsset(scene_id=1, local_path=str(project_state.video_path), success=True, duration_sec=5)
        monkeypatch.setattr(jobs, "video_agent_for_policy", lambda policy: type("Agent", (), {"generate_shot": fake_generate_shot})())
        snapshot = _build_video_generation_snapshot(project_state.state, 1, project_state.policy)
        project_state.state.generation_prompts.shots[0].prompt = "排队后修改不得污染任务"
        await jobs._job_video_generate(project_state.context, {"scene_id": 1, "generation_snapshot": snapshot})
        assert captured["prompt"] == snapshot["provider_prompt"]
        assert captured["references"] == [item["image_path"] for item in snapshot["references"]]
        assert project_state.reload().shot_productions[1].video_versions[-1].generation_snapshot == snapshot

- [ ] **Step 2: Run test to verify failure**

Run: pytest tests/test_video_generation_snapshot.py::test_video_job_uses_and_persists_frozen_snapshot -v

Expected: FAIL because current job execution reloads live state.

- [ ] **Step 3: Build and consume a frozen snapshot**

    def _build_video_generation_snapshot(state, scene_id, policy):
        prompt = _shot_prompt(state, scene_id).model_copy(deep=True)
        references = [item.model_dump() for item in _reconcile_shot_references(state, scene_id)
                      if Path(item.image_path).is_file() and not item.stale]
        effective = compose_video_prompt(_policy_prompt_provider(policy), state.generation_prompts, prompt, state.prompt_policy)
        return {
            "scene_id": scene_id, "raw_prompt": prompt.prompt,
            "raw_negative_prompt": prompt.negative_prompt,
            "provider_prompt": effective["provider_prompt"],
            "provider_negative_prompt": effective["provider_negative_prompt"],
            "negative_prompt_mode": effective["negative_prompt_mode"],
            "references": references,
            "policy": {key: value for key, value in policy.items() if key != "capability"},
        }

    def _policy_prompt_provider(policy):
        return "happyhorse" if policy["provider"] in {"happyhorse", "wan"} else policy["provider"]

The submit route runs _prepare_auto_references, freezes this snapshot under the project lock, saves it as last_generation_snapshot, and puts it in the durable job payload. Reject stale or unreadable required references with a 409 naming scene_id and reference identity. The job validates the snapshot scene ID, rebuilds an isolated ShotPrompt from the snapshot, uses only saved paths, labels and policy, then writes the same snapshot to the version and result. It must not refresh references or reload a live prompt after submission.

- [ ] **Step 4: Add two-scene isolation coverage**

In the new test module, define run_video_job(project_state, snapshot) as:

    async def run_video_job(project_state, snapshot):
        return await jobs._job_video_generate(
            project_state.context,
            {"scene_id": snapshot["scene_id"], "generation_snapshot": snapshot},
        )

    async def test_two_scene_jobs_send_distinct_prompts_and_references(mock_video_agent, project_state):
        first = _build_video_generation_snapshot(project_state.state, 1, project_state.policy)
        second = _build_video_generation_snapshot(project_state.state, 2, project_state.policy)
        await run_video_job(project_state, first)
        await run_video_job(project_state, second)
        assert mock_video_agent.calls[0]["prompt"] != mock_video_agent.calls[1]["prompt"]
        assert mock_video_agent.calls[0]["references"] != mock_video_agent.calls[1]["references"]

- [ ] **Step 5: Verify and commit**

Run: pytest tests/test_video_generation_snapshot.py tests/test_shot_production_state.py tests/test_prompt_policy.py -v

Expected: PASS.

    git add src/api/context.py src/api/routes/production.py src/api/routes/jobs.py tests/test_video_generation_snapshot.py
    git commit -m "fix: freeze storyboard video generation requests"

### Task 5: Show Draft And Submitted Audit Separately

**Files:**
- Modify: web/js/pages/production-actions.js
- Test: tests/test_video_generation_snapshot.py

**Interfaces:**
- productionSubmittedSnapshot(current) returns active-version or last submitted snapshot.
- productionAuditHtml(current, draft) renders submitted audit or non-submitted draft preview.

- [ ] **Step 1: Add production-payload audit test**

    def test_production_payload_exposes_last_submitted_snapshot(project_state):
        project_state.state.shot_productions[1].last_generation_snapshot = {
            "scene_id": 1, "provider_prompt": "已提交镜头一",
        }
        payload = _production_payload(project_state.state)
        assert payload["productions"][0]["production"]["last_generation_snapshot"]["provider_prompt"] == "已提交镜头一"

- [ ] **Step 2: Implement audit rendering and safe reference addition**

    function productionSubmittedSnapshot(current) {
      const active = current.production.video_versions
        .find(item => item.version_id === current.production.active_version_id);
      return active?.generation_snapshot || current.production.last_generation_snapshot || null;
    }

    function productionAuditHtml(current, draft) {
      const snapshot = productionSubmittedSnapshot(current);
      const data = snapshot || draft;
      const title = snapshot ? '实际提交给视频模型' : '当前草稿预览（尚未提交）';
      return '<div class="production-feedback"><div>' + title + '</div><pre>'
        + escapeHtml(data.provider_prompt || '') + '</pre></div>';
    }

Save current.production.references.map(({ reference_id, source }) => ({ reference_id, source })). ProductionReferenceRequest accepts source with default "asset"; update_project_production_shot copies the request source onto its validated catalog item. productionAddReference must mark a user-selected catalog entry as source: "manual" before saving. Reject an unknown catalog ID before productionAddReference mutates the current array. Show “手工” only for source === "manual". Keep existing focus, scroll and draft protections.

- [ ] **Step 3: Verify and commit**

Run: node --check web/js/pages/production-actions.js

Expected: exit code 0.

Run: pytest tests/test_video_generation_snapshot.py -v

Expected: PASS.

    git add web/js/pages/production-actions.js tests/test_video_generation_snapshot.py
    git commit -m "fix: audit submitted storyboard video requests"

### Task 6: Full Regression And Browser Smoke Test

**Files:**
- Modify only when the new tests expose a defect in Tasks 1-5.
- Test: tests/

- [ ] **Step 1: Run all backend tests**

Run: pytest -q

Expected: PASS, including production, assets, project isolation, SSE and prompt-policy tests.

- [ ] **Step 2: Start local service**

Run: start_api_server.bat

Expected: health route returns HTTP 200 and the root workbench loads.

- [ ] **Step 3: Browser acceptance using Mock video**

1. Use two scenes with distinct prompts and asset-bound images.
2. Add a manual reference to scene 1 and confirm scene 2 never receives it.
3. Update scene 1's active character image and confirm only its asset-bound reference changes.
4. Submit Mock jobs for both scenes and compare submitted audit panels: prompts and reference sequences differ.
5. Edit an unsaved prompt during a task event and verify focus, text and scroll remain unchanged.

- [ ] **Step 4: Commit regression-only corrections**

    git add src tests web
    git commit -m "test: verify storyboard reference prompt integrity"
