# 场景身份、状态与参考组图一致性改造实施计划

> **给执行代理的要求：** 必须使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans，按任务逐项执行。每个步骤使用复选框跟踪，并在进入下一任务前运行本任务测试。

**目标：** 把场景资产从“地点一张图、按镜头数猜视图”改成“地点身份 + 场景状态 + 实际机位需求”，先生成并确认状态中心母版，再使用母版参考生成所需角度，保证跨分镜空间一致且不串图。

**架构：** LocationAsset 只保存稳定空间身份；新增 LocationState 保存时间、天气、光源和临时可见状态；每个 ShotVisualBinding 同时绑定 location_id 与 state_id。场景生成分成母版、确认、派生三个阶段，继续使用现有 SceneImage、ImageAssetVersions、JobStore、SSE 和项目状态 JSON。视频参考图、提示词预览和前端卡片全部按状态选择。

**技术栈：** Python 3.11、FastAPI、Pydantic、现有 SQLite/项目状态 JSON、LangGraph、WanImageClient、原生 ES Modules、pytest、Node 语法检查。

## 全局约束与本次重点修复

- 保留现有 API 路径、启动方式、数据库、输出目录、旧图片和历史版本；新增字段必须有默认值。
- 场景参考图固定横向 16:9；项目成片为 9:16 时由视频合成阶段适配，不能把场景参考图生成成竖图。
- 地点身份只按规范化物理地点去重；不同时间、天气、光源必须进入不同 state_id。
- 母版生成、母版确认、派生组图是三个明确状态；未确认母版时组图 API 必须返回 409。
- 组图只生成服务端保存的 required_views，不再根据 shot_ids 数量决定张数。
- 万相组图必须将确认母版作为第一张参考图；返回数量不足、下载失败、取消或状态变化时不激活任何派生结果。
- 所有异步任务写入前校验 project_id、state_id、master_version_id、requirement_signature，防止旧任务覆盖新母版。
- 用户手动覆盖的视图需求、提示词和参考图优先，自动推导不得覆盖人工值。

---

## 任务一：补齐场景身份、状态和版本模型

**文件：**
- 修改：src/schemas/script.py 的 LocationAsset、ShotVisualBinding
- 新增：src/schemas/scene_state.py
- 修改：src/schemas/scene_image.py 的 SceneImage
- 修改：src/graph/state.py 的 DramaState
- 修改：src/utils/visual_asset_manifest.py
- 新增测试：tests/test_scene_state_manifest.py

**接口和字段：**

~~~python
class LocationState(BaseModel):
    state_id: str
    label: str = ""
    time_condition: str = ""
    weather: str = ""
    light_direction: str = ""
    visible_state_notes: str = ""
    shot_ids: list[int] = Field(default_factory=list)
    representative_scene_id: int = 0

class SceneViewRequirement(BaseModel):
    location_id: str
    state_id: str
    views: tuple[str, ...] = ("scene_center",)
    reasons: list[str] = Field(default_factory=list)
    source: str = "auto"
    signature: str = ""
~~~

LocationAsset 新增 identity_description、fixed_dressing、anchor_points、states、default_state_id，旧 environment_description、time_condition、shot_ids 继续读取。ShotVisualBinding 新增 state_id、state_override_notes。SceneImage 新增 state_id、is_master、needs_confirmation、master_version_id、reference_version_ids、derived_from_version_id、requirement_signature，默认值依次为 ""、False、False、""、[]、""、""。

DramaState 新增：

~~~python
scene_view_requirements: dict[str, SceneViewRequirement] = field(default_factory=dict)
scene_generation_tokens: dict[str, str] = field(default_factory=dict)
~~~

内部键固定为 location_id:state_id，只在当前项目内有效；对外路径仍使用 location_id，并通过 state_id 查询参数或 JSON 字段区分状态。

- [ ] 步骤 1：先写失败测试

~~~python
def test_legacy_location_gets_one_default_state():
    script = normalize_scene_assets(legacy_script())
    location = script.asset_manifest.locations[0]
    assert len(location.states) == 1
    assert location.default_state_id == location.states[0].state_id
    assert script.asset_manifest.shot_bindings[0].state_id == location.default_state_id

def test_same_location_with_different_conditions_creates_states_not_locations():
    script = normalize_scene_assets(day_and_rain_script())
    assert len(script.asset_manifest.locations) == 1
    assert len(script.asset_manifest.locations[0].states) == 2
    assert {b.state_id for b in script.asset_manifest.shot_bindings} == {"state-day", "state-rain"}
~~~

- [ ] 步骤 2：运行 pytest tests/test_scene_state_manifest.py -q，预期因字段和迁移函数不存在而失败。
- [ ] 步骤 3：实现 LocationState、SceneImage 新字段和 DramaState 持久化字段。用 NFKC、去首尾空白、合并连续空白、ASCII 小写规范化状态字段，以 sha256 前 12 位生成 state_id；状态指纹包含地点、时间、天气、光源、可见状态，不包含动作和机位。
- [ ] 步骤 4：修改 repair_asset_manifest：先补齐状态，再修复绑定，再校验。修改 validate_asset_manifest，明确报告 location_id、state_id、scene_id。可确定的 shot_ids 差异自动修复，无法判断才返回错误。
- [ ] 步骤 5：运行 pytest tests/test_scene_state_manifest.py tests/test_asset_migration.py tests/test_asset_generation_api.py::test_same_name_scenes_are_normalized_to_one_stable_asset -q。
- [ ] 步骤 6：提交，提交信息为 feat: add scene identity and state model。

---

## 任务二：修复剧本解析的场景状态提取

**文件：**
- 修改：src/agents/script_agent.py 的 VISUAL_ASSET_RULES、_validate_visual_assets
- 修改：src/utils/visual_asset_manifest.py
- 新增测试：tests/test_scene_state_extraction.py

**确定性规则：**

1. LLM 只输出地点、固定空间、时间、天气、主光方向、临时状态、机位；角色动作不能写入身份层。
2. 相同规范化地点且身份字段一致，仅条件变化时合并为一个 LocationAsset 的多个 LocationState。
3. 白天/夜晚、晴天/雨夜必须拆成不同状态；同状态不同动作仍为同一状态。
4. 状态字段冲突时采用多数镜头值；无法判断的字段置空并写入 state_override_notes，不能静默采用第一条描述。
5. 每个分镜必须有一个 location_id + state_id 绑定；缺失状态时使用 default 状态并记录修复日志。

- [ ] 步骤 1：写失败测试

~~~python
def test_action_difference_does_not_create_extra_state():
    script = parse_fixture("same_room_two_actions.json")
    assert len(script.asset_manifest.locations) == 1
    assert len(script.asset_manifest.locations[0].states) == 1

def test_day_and_rain_have_exact_shot_bindings():
    script = parse_fixture("same_room_day_rain.json")
    location = script.asset_manifest.locations[0]
    assert len(location.states) == 2
    assert sorted(sum((state.shot_ids for state in location.states), [])) == [1, 2, 3, 4]
    assert all(binding.state_id for binding in script.asset_manifest.shot_bindings)
~~~

- [ ] 步骤 2：运行 pytest tests/test_scene_state_extraction.py -q，预期失败。
- [ ] 步骤 3：更新 VISUAL_ASSET_RULES，增加状态字段契约；_validate_visual_assets 顺序固定为地点规范化、状态推导、绑定修复、道具清理、最终校验。未提供状态时从 Scene 的时间、天气、光源字段推导，全部缺失时建立 default 状态。
- [ ] 步骤 4：将 inconsistent shot 错误变成可操作结果：可推断时补写 shot_ids；不可推断时返回 location_id/state_id/scene_id 及“需要确认状态”的错误，不得只显示“解析失败”。
- [ ] 步骤 5：运行 pytest tests/test_scene_state_extraction.py tests/test_script_agent.py -q，确认关键道具清理没有回归。
- [ ] 步骤 6：提交，提交信息为 fix: extract scene states deterministically。

---

## 任务三：按镜头机位推导实际视图需求

**文件：**
- 新增：src/services/scene_view_requirements.py
- 修改：src/services/asset_generation.py 的 required_asset_views
- 修改：src/api/routes/assets.py
- 修改：src/graph/state.py
- 新增测试：tests/test_scene_view_requirements.py

**固定函数和规则：**

~~~python
def derive_scene_view_requirements(state, location_id: str, state_id: str) -> SceneViewRequirement: ...
def resolve_scene_asset_key(state, location_id: str, state_id: str = "") -> tuple[str, str]: ...
~~~

- 只出现一次或只需要环境建立的地点：scene_center。
- 明确出现通道深处向外、背后、反向、出口方向：scene_center + scene_reverse。
- 同状态同时出现左前 45°和右前 45°或两个侧向机位：scene_center + scene_left_45 + scene_right_45。
- 关键地点、空间结构复杂、明确要求四角度：四个视图。
- 只有近景/特写而没有环境建立镜头时，仍只生成 scene_center。
- 视图固定顺序为 center、left_45、right_45、reverse。

新增接口：
- GET /api/projects/{project_id}/scenes/{location_id}/states/{state_id}/view-requirements
- PUT 同路径，请求体 {"views":["scene_center","scene_reverse"]}；服务端强制加入 center，拒绝未知视图和重复值，保存 source=manual。
- 现有 /api/projects/{project_id}/assets/scene/{asset_id}/prompt 和 generate 保留，增加可选 state_id；省略时使用 default_state_id。

- [ ] 步骤 1：写失败测试

~~~python
def test_one_shot_scene_only_needs_center():
    assert derive_scene_view_requirements(state_for_shots(1), "loc", "default").views == ("scene_center",)

def test_linear_space_needs_reverse():
    result = derive_scene_view_requirements(state_with_camera("从通道最深处向外拍摄"), "loc", "state")
    assert result.views == ("scene_center", "scene_reverse")

def test_manual_override_always_keeps_center(client, project_id):
    response = client.put(
        f"/api/projects/{project_id}/scenes/loc/states/default/view-requirements",
        json={"views": ["scene_right_45"]},
    )
    assert response.json()["views"][0] == "scene_center"
~~~

- [ ] 步骤 2：运行 pytest tests/test_scene_view_requirements.py -q，预期失败，因为现有 scene_group_view_count 仍按 shot_ids 数量返回 2/3/4。
- [ ] 步骤 3：删除 scene_group_view_count 的镜头数量分支，required_asset_views 改用视图需求服务。内部键使用 location_id:state_id，同时保留地点旧键解析，避免旧客户端 404。
- [ ] 步骤 4：保存 requirement_signature=sha256(location_id + state_id + views + identity_signature)[:16]。任务创建时固化签名，手动修改需求只影响后续任务。
- [ ] 步骤 5：运行 pytest tests/test_scene_view_requirements.py tests/test_asset_generation_api.py -q。
- [ ] 步骤 6：提交，提交信息为 feat: derive required scene views from camera cues。

---

## 任务四：实现中心母版、确认和参考组图任务链

**文件：**
- 修改：src/services/asset_generation.py
- 修改：src/api/routes/jobs.py
- 修改：src/api/routes/assets.py
- 修改：src/api/context.py
- 新增测试：tests/test_scene_master_generation.py

**固定函数：**

~~~python
async def generate_scene_master(project_id, location_id, state_id, custom_instructions="", override=None, project_lock=None) -> dict: ...
async def confirm_scene_master(project_id, location_id, state_id, version_id) -> dict: ...
async def generate_scene_reference_group(project_id, location_id, state_id, custom_instructions="", override=None, project_lock=None) -> dict: ...
~~~

新增接口：
- POST /api/projects/{project_id}/scenes/{location_id}/states/{state_id}/master/generate
- POST /api/projects/{project_id}/scenes/{location_id}/states/{state_id}/master/confirm，body {"version_id":"..."}
- POST /api/projects/{project_id}/scenes/{location_id}/states/{state_id}/reference-group/generate

生成规则：
1. 母版只生成 scene_center，is_master=true、needs_confirmation=true；成功后不自动组图。
2. 确认只接受当前项目、当前状态、当前活动版本且文件存在的 version_id。
3. 组图使用已确认母版作为 references[0]，n 来自已保存的 SceneViewRequirement，不能由请求随意指定。
4. 组图结果数量不足、下载失败、取消或签名变化时，全部结果不激活；临时文件保留到历史/失败日志。
5. 旧的 scene.reference.group 任务类型保留，但必须调用相同确认校验。

- [ ] 步骤 1：写失败测试

~~~python
def test_first_scene_generation_creates_only_unconfirmed_master(client, project_id):
    response = client.post(master_generate_url(project_id))
    images = scene_images(response.json())
    assert response.status_code == 200
    assert len(images) == 1
    assert images[0]["is_master"] is True and images[0]["needs_confirmation"] is True

def test_group_blocked_before_confirmation(client, project_id):
    response = client.post(group_generate_url(project_id))
    assert response.status_code == 409
    assert "确认中心母版" in response.json()["detail"]

def test_partial_group_activates_nothing(monkeypatch, client, project_id):
    confirm_master(client, project_id)
    monkeypatch.setattr(WanImageClient, "generate_sequence", return_two_when_three_requested)
    response = client.post(group_generate_url(project_id))
    assert response.status_code == 502
    assert not any(item.get("generation_mode") == "reference_group" for item in scene_images(response.json()))
~~~

- [ ] 步骤 2：运行 pytest tests/test_scene_master_generation.py -q，预期失败，因为当前组图调用传入空参考数组并直接激活结果。
- [ ] 步骤 3：修改 create_project_job：场景 asset.generate 在 Wan 下不再直接创建组图；无视图请求创建母版任务，有 scene_* 请求创建组图任务。新任务 payload 固化 location_id、state_id、master_version_id、requirement_signature、generation_policy。
- [ ] 步骤 4：实现母版生成、确认和 Wan 参考组图。commit 前重新读取状态并比较四个令牌；不一致返回过期结果。版本记录 group_id、master_version_id、reference_version_ids、derived_from_version_id、requirement_signature。
- [ ] 步骤 5：运行 pytest tests/test_scene_master_generation.py tests/test_job_api.py tests/test_image_cache.py -q。
- [ ] 步骤 6：提交，提交信息为 feat: add confirmed scene master workflow。

---

## 任务五：重写四层场景提示词并保证每个视角不同

**文件：**
- 修改：src/services/asset_prompt_composer.py
- 修改：src/agents/image_gen_agent.py 的场景视图常量和固定约束
- 修改：src/agents/visual_prompt_agent.py 的场景资产摘要
- 新增测试：tests/test_scene_prompt_layers.py

**固定函数：**

~~~python
def build_scene_prompt_layers(
    state, location_id: str, state_id: str, view_type: str, custom_instructions: str = ""
) -> dict: ...
~~~

返回 identity、state、view、constraints、custom、effective_prompt、negative_prompt、width、height、prompt_template_version。

- identity 只写结构、比例、固定材质、固定陈设、空间锚点。
- state 只写时间、天气、主光方向、积水/烟雾/破损等暂时状态。
- view 只写中心线、左前 45°、右前 45°、深处向外。
- constraints 固定写无人、无人物影子/倒影、无文字水印、无 UI、无畸变、深景深等中文硬约束。
- custom 作为“用户补充要求”追加，不能删除或改写锁定层。

- [ ] 步骤 1：写失败测试

~~~python
def test_identity_and_state_are_shared_but_view_is_different():
    left = build_scene_prompt_layers(state(), "loc", "rain", "scene_left_45", "地面积水")
    right = build_scene_prompt_layers(state(), "loc", "rain", "scene_right_45", "地面积水")
    assert left["identity"] == right["identity"]
    assert left["state"] == right["state"]
    assert left["view"] != right["view"]
    assert left["effective_prompt"] != right["effective_prompt"]

def test_custom_text_cannot_remove_empty_scene_constraint():
    result = build_scene_prompt_layers(state(), "loc", "default", "scene_center", "加入人物和对白")
    assert "不得出现任何人物" in result["effective_prompt"]
~~~

- [ ] 步骤 2：运行 pytest tests/test_scene_prompt_layers.py -q，预期失败。
- [ ] 步骤 3：让母版、组图、单图编辑、提示词预览都调用同一组装器；provider_prompt 必须等于实际传给 Wan 的中文主提示词。场景参考尺寸固定 1920x1080（再按模型 1K/2K 降采样），不能复用角色的竖屏尺寸。
- [ ] 步骤 4：版本历史保存四层字段、完整 effective_prompt、state_id、view_type、模型、清晰度；旧版本只有 image_prompt 时只读兼容。
- [ ] 步骤 5：运行 pytest tests/test_scene_prompt_layers.py tests/test_asset_generation_api.py::test_asset_prompt_preview_uses_the_same_saved_supplements_as_generation -q。
- [ ] 步骤 6：提交，提交信息为 fix: compose scene prompts by identity state and view。

---

## 任务六：场景单图参考修订、母版失效和历史版本

**文件：**
- 修改：src/services/asset_generation.py
- 修改：src/api/routes/assets.py
- 修改：src/api/context.py
- 修改：src/schemas/scene_image.py
- 新增测试：tests/test_scene_reference_edit.py

**接口：** POST /api/projects/{project_id}/scenes/{location_id}/states/{state_id}/views/{view_type}/edit，请求体：

~~~json
{"prompt":"调整墙面水痕和灯光反射","reference_version_ids":["scene-left-old"]}
~~~

服务端把确认母版放在 references[0]，再追加用户选择的视图；空提示词返回 400，未确认母版返回 409，非 Wan 模型返回 409。

- [ ] 步骤 1：写失败测试

~~~python
def test_scene_edit_prepends_confirmed_master(monkeypatch, client, project_id):
    captured = {}
    monkeypatch.setattr(WanImageClient, "edit_image", capture_edit(captured))
    response = client.post(edit_url(project_id), json={"prompt": "增加墙面水痕", "reference_version_ids": ["left-old"]})
    assert response.status_code == 200
    assert captured["references"][0] == "master.png"

def test_master_change_marks_same_state_derivatives_stale(client, project_id):
    generate_confirmed_group(client, project_id)
    regenerate_master(client, project_id)
    images = scene_images(client.get(state_url(project_id)).json())
    assert all(item["is_stale"] for item in images if not item["is_master"] and item["state_id"] == "default")
~~~

- [ ] 步骤 2：运行 pytest tests/test_scene_reference_edit.py -q，预期失败。
- [ ] 步骤 3：调用 WanImageClient.edit_image，只替换目标视图，记录 generation_mode、来源母版、附加参考、effective_prompt、模型快照。
- [ ] 步骤 4：新增 invalidate_scene_derivatives(state, location_id, state_id, master_version_id)，只标记同地点同状态非母版图为 stale；其他状态和历史文件不删除。
- [ ] 步骤 5：运行 pytest tests/test_scene_reference_edit.py tests/test_image_editor_cache_version.py -q。
- [ ] 步骤 6：提交，提交信息为 feat: edit scene views from confirmed master。

---

## 任务七：修复视频参考图按代表镜头选择的漏洞

**文件：**
- 修改：src/agents/video_gen_agent.py 的 _build_reference_bundle、_build_reference_identity_bundle、_choose_scene_reference
- 修改：src/utils/visual_asset_manifest.py，新增 state_for_scene
- 修改：src/api/context.py 的视频参考快照
- 新增测试：tests/test_scene_reference_binding.py

**当前明确漏洞：**现有代码只收集 wide/medium，忽略 scene_center/scene_left_45/scene_right_45/scene_reverse；并按 scene_id 找图，无法区分同地点不同状态。

选择顺序固定为：
1. 当前 location_id + state_id 的首选机位且 is_stale=false；
2. 当前状态已确认母版且 is_stale=false；
3. 旧项目没有 state_id 时才回退地点旧 wide/medium；
4. 绝不回退到其他状态图片。

函数签名：

~~~python
def _choose_scene_reference(
    scene_images, location_id: str, state_id: str, preferred_view: str
) -> SceneImage | None: ...
~~~

- [ ] 步骤 1：写失败测试

~~~python
def test_video_prefers_matching_state_view():
    selected = VideoGenerationAgent._choose_scene_reference(images(), "loc", "rain", "scene_left_45")
    assert selected.state_id == "rain" and selected.view_type == "scene_left_45"

def test_video_never_uses_other_state():
    selected = VideoGenerationAgent._choose_scene_reference(images_without_rain_view(), "loc", "rain", "scene_right_45")
    assert selected.is_master is True and selected.state_id == "rain"

def test_snapshot_contains_state_and_version():
    snapshot = build_snapshot(state_with_scene_state(), 2)
    assert snapshot["references"][0]["state_id"] == "rain"
    assert snapshot["references"][0]["version_id"]
~~~

- [ ] 步骤 2：运行 pytest tests/test_scene_reference_binding.py -q，预期失败。
- [ ] 步骤 3：路径表保留完整 SceneImage 元数据，不再只存 path；参考身份写入 scene|location_id|state_id|view_type|version_id；视频任务快照固化状态和版本。
- [ ] 步骤 4：运行 pytest tests/test_scene_reference_binding.py tests/test_video_generation.py -q，确认角色和道具参考顺序不变。
- [ ] 步骤 5：提交，提交信息为 fix: bind video references to scene state。

---

## 任务八：前端场景卡片、编辑器和任务按钮改为状态流程

**文件：**
- 修改：web/js/pages/script.js 的 sceneAssetsFromScript
- 修改：web/js/components/asset-cards.js 的 renderScenes
- 修改：web/js/components/image-editor.js
- 修改：web/js/pages/shots.js
- 修改：web/js/core/job-events.js
- 修改：studio.html 的场景编辑区
- 新增测试：tests/test_scene_frontend_contract.py

**页面行为：**
- 同地点不同状态显示为不同状态行，显示地点、状态、绑定镜头、身份摘要、状态摘要、所需视图和母版状态。
- 首次操作为“生成中心母版”；有母版但未确认时为“确认母版”；确认后才显示“生成所需视图”。
- 组图失败只更新当前状态卡片；SSE 不重建资产页、图片编辑弹窗、输入框和历史列表。
- 编辑器展示四层提示词、当前母版版本、派生版本和过期原因；历史版本放右侧。
- 视图需求从 API 读取，不使用 imgs.length 或 shotIds.length 推断数量。

- [ ] 步骤 1：写失败契约测试

~~~python
def test_scene_cards_do_not_derive_count_from_shot_count():
    source = Path("web/js/components/asset-cards.js").read_text(encoding="utf-8")
    assert "shotIds.length" not in source
    assert "requiredViews" in source or "view-requirements" in source

def test_scene_editor_exposes_master_confirmation():
    html = Path("studio.html").read_text(encoding="utf-8")
    assert "确认母版" in html or "confirmSceneMaster" in html
~~~

- [ ] 步骤 2：运行 pytest tests/test_scene_frontend_contract.py -q，预期失败。
- [ ] 步骤 3：新增 loadSceneRequirements、renderSceneStateCard、generateSceneMaster、confirmSceneMaster、generateSceneReferenceGroup。任务事件只按 location_id:state_id 更新目标卡片；草稿聚焦时后台事件不得覆盖输入。
- [ ] 步骤 4：运行 node --check web/js/pages/script.js、asset-cards.js、image-editor.js、shots.js、job-events.js，并执行桌面浏览器验收。
- [ ] 步骤 5：提交，提交信息为 feat: expose scene state and master workflow in UI。

---

## 任务九：旧项目迁移、幂等性、接口文档和全量回归

**文件：**
- 修改：src/utils/project_store.py 与现有资产迁移入口
- 修改：src/api/context.py 的状态加载迁移
- 修改：README.md
- 新增测试：tests/test_scene_migration_idempotency.py

**迁移规则：**
- dry-run 先输出地点、状态、文件、路径冲突和无法归属列表；未经确认不移动文件。
- 已有稳定 location_id 的图片保留原路径并补状态；旧 wide.png 只能作为“待确认母版候选”。
- 无法判断状态的图片保留磁盘文件并从活动卡片隐藏，不删除。
- 第二次执行不再次移动、不重复创建版本、不改变活动状态。

- [ ] 步骤 1：写失败测试

~~~python
def test_scene_migration_is_idempotent(tmp_path):
    first = migrate_scene_assets(legacy_state(), tmp_path, dry_run=False)
    second = migrate_scene_assets(first.state, tmp_path, dry_run=False)
    assert second.moved_files == []
    assert second.state == first.state

def test_ambiguous_file_is_hidden_but_not_deleted(tmp_path):
    result = migrate_scene_assets(ambiguous_legacy_state(), tmp_path, dry_run=False)
    assert result.ambiguous_files
    assert Path(result.ambiguous_files[0]).exists()
~~~

- [ ] 步骤 2：运行 pytest tests/test_scene_migration_idempotency.py -q，预期失败。
- [ ] 步骤 3：实现迁移、项目状态读取兼容和 API 200/400/409/404 契约测试；旧 /api/scenes 和旧资产路径默认解析到 default_state_id。
- [ ] 步骤 4：更新 README，写明字段、母版确认顺序、视图需求覆盖、历史版本和迁移命令。
- [ ] 步骤 5：全量运行：

~~~text
pytest -q
python -m compileall src
node --check web/js/pages/script.js
node --check web/js/components/asset-cards.js
node --check web/js/components/image-editor.js
node --check web/js/pages/shots.js
node --check web/js/core/job-events.js
~~~

浏览器验收必须覆盖：项目隔离、同地点多状态、母版确认、组图参考图、部分失败不激活、单图修订、母版失效、视频参考回退、SSE 局部更新、历史图片显示。

- [ ] 步骤 6：提交，提交信息为 feat: complete scene identity-state migration and regression coverage。

## 复核结论：原方案中已发现并修正的问题

1. **状态来源不明确：**增加剧本解析契约和确定性后处理，避免把动作、机位误当地点身份。
2. **状态 ID 可能串项目：**状态键只在项目状态内使用，任务额外携带 project_id 和状态签名。
3. **旧接口不能区分状态：**保留旧地点路径，新增 state_id 参数，缺省使用默认状态。
4. **母版可能被自动跳过：**拆成母版生成、确认、组图三个任务，组图前强制 409。
5. **组图结果顺序/数量有风险：**固化视图顺序和 requirement_signature，数量不符或状态变化时不激活。
6. **视频参考图漏用新视图：**明确修复当前只收集 wide/medium 的问题。
7. **每个镜头提示词可能相同：**四层组装器和视角差异测试保证有效提示词真正不同。
8. **母版变化可能污染其他状态：**失效传播限定在同地点同状态派生图。
9. **前端按图片数显示：**改为服务端视图需求，SSE 只刷新目标状态卡片。
10. **迁移不可重复：**增加 dry-run、歧义隐藏和第二次零改动测试。

