# 道具绑定自动对账 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 LLM 返回的道具声明与镜头绑定不一致导致的剧本解析失败，并只保留至少跨两个镜头的关键道具。

**Architecture:** 在 `visual_asset_manifest` 中新增一个纯本地对账步骤，以镜头 `prop_ids` 重建道具 `shot_ids`。随后复用并收紧现有 `prune_nonessential_props()`：仅保留至少被两个不同镜头明确绑定、具有有效关键理由和视觉身份描述的道具。解析 Agent 在验证前依次执行地点规范化、道具对账和筛选。

**Tech Stack:** Python 3.12、Pydantic 模型、pytest。

## Global Constraints

- 不调用 LLM 补修资产清单，不增加模型费用或额外延迟。
- 镜头 `prop_ids` 是道具镜头归属的唯一事实来源。
- 真正的引用完整性错误仍由 `validate_asset_manifest()` 拒绝。
- 仅修改道具提取与校验链路，不改变资产生成 API。

---

### Task 1: 为道具对账和单镜头筛选建立回归测试

**Files:**
- Modify: `tests/test_prop_assets.py`

**Interfaces:**
- Consumes: `reconcile_prop_bindings(script: ScriptBreakdown) -> list[str]`
- Produces: 覆盖 LLM 道具 `shot_ids` 与镜头 `prop_ids` 不一致时的可恢复行为。

- [ ] **Step 1: Write the failing test**

```python
def test_reconcile_prop_bindings_uses_shot_bindings_as_source_of_truth():
    script = _script_with_two_shots()
    prop = script.asset_manifest.props[0]
    prop.shot_ids = [1, 2, 3]
    script.asset_manifest.shot_bindings[1].prop_ids = []

    reconciled = reconcile_prop_bindings(script)

    assert reconciled == ["old_brass_key"]
    assert prop.shot_ids == [1]
    assert validate_asset_manifest(script) == []


def test_prune_nonessential_props_removes_even_justified_single_shot_prop():
    script = _script_with_two_shots()
    prop = script.asset_manifest.props[0]
    prop.critical_reason = "plot"
    prop.critical_evidence = "钥匙打开密室并推动后续剧情。"
    script.asset_manifest.shot_bindings[1].prop_ids = []
    reconcile_prop_bindings(script)

    removed = prune_nonessential_props(script)

    assert removed == ["old_brass_key"]
    assert script.asset_manifest.props == []
    assert all("old_brass_key" not in binding.prop_ids for binding in script.asset_manifest.shot_bindings)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_prop_assets.py -k "reconcile_prop_bindings or single_shot_prop" -v`

Expected: FAIL because `reconcile_prop_bindings` does not exist and single-shot justified props are retained.

### Task 2: 实现确定性道具对账和关键道具最小跨镜数

**Files:**
- Modify: `src/utils/visual_asset_manifest.py:188-238`

**Interfaces:**
- Produces: `reconcile_prop_bindings(script: ScriptBreakdown) -> list[str]`
- Changes: `prune_nonessential_props(script: ScriptBreakdown) -> list[str]` requires at least two bound scenes.

- [ ] **Step 1: Write minimal implementation**

```python
def reconcile_prop_bindings(script: ScriptBreakdown) -> list[str]:
    if not script.asset_manifest:
        return []
    bound_shots_by_prop: dict[str, set[int]] = {}
    for binding in script.asset_manifest.shot_bindings:
        for prop_id in dict.fromkeys(binding.prop_ids):
            bound_shots_by_prop.setdefault(prop_id, set()).add(binding.scene_id)
    changed: list[str] = []
    for prop in script.asset_manifest.props:
        actual_shots = sorted(bound_shots_by_prop.get(prop.prop_id, set()))
        if prop.shot_ids != actual_shots:
            prop.shot_ids = actual_shots
            changed.append(prop.prop_id)
    return changed
```

Add `len(bound_shots) < 2` to the removal condition in `prune_nonessential_props()`.

- [ ] **Step 2: Run targeted tests**

Run: `pytest tests/test_prop_assets.py -k "reconcile_prop_bindings or single_shot_prop or whitelist" -v`

Expected: PASS.

### Task 3: 将对账放入剧本解析保存前链路并验证回归

**Files:**
- Modify: `src/agents/script_agent.py:8,214-227`
- Test: `tests/test_prop_assets.py`

**Interfaces:**
- Consumes: `reconcile_prop_bindings(script)` after location normalization and before pruning/validation.
- Produces: 解析链路可保存已对账资产清单，仍拒绝未知道具引用。

- [ ] **Step 1: Update parser validation sequence**

```python
normalize_scene_assets(script)
reconcile_prop_bindings(script)
prune_nonessential_props(script)
errors = validate_asset_manifest(script)
```

- [ ] **Step 2: Run focused and full regression tests**

Run: `pytest tests/test_prop_assets.py tests/test_visual_asset_manifest.py -v`

Expected: PASS.

Run: `pytest -q`

Expected: PASS.
