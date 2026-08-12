# 项目视觉风格选择与参考图实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在新建项目页面提供四种带综合参考图的主视觉风格，并把选中的风格稳定、可追溯地应用到角色、场景、道具和分镜提示词，同时兼容旧项目和旧客户端。

**Architecture:** 新增纯 Python 风格注册表服务作为唯一风格事实来源；`CreativeProfile` 保存规范化风格 ID、补充标签和旧 `art_style` 兼容值。前端通过 `/api/visual-styles` 渲染创建页卡片，提示词编译器按资产类型读取注册表的正向/负面层，任务快照保存提交时的风格约束。

**Tech Stack:** FastAPI、Pydantic、现有 `ProjectStore`/`DramaState`、原生 ES Modules、现有静态 `/static` 挂载、pytest；综合参考图使用本地 WebP 静态资源。

## Global Constraints

- 不删除现有项目创建字段、旧 `art_style` 接口、图片模型、视频模型、TTS、音频时间轴和历史生成版本。
- 参考图只用于项目创建页展示，不作为图片/视频生成请求的参考输入，不写入 `output/`。
- 项目只保存一个主风格；赛博朋克、国风、清新等旧选项保存为 `style_modifiers` 补充标签。
- 新建项目接口默认主风格为 `cinematic_cel`；底层 `CreativeProfile()` 默认仍与历史 `art_style="写实"` 对应的 `live_action_cinematic`，避免旧 API 调用突然改变画风；旧项目未知风格值也映射到 `live_action_cinematic` 并保留原文。
- 任何项目、任务或历史资产操作都必须继续经过当前项目会话和项目隔离校验。
- `output/` 目录及其中所有项目资产绝对不能删除或移动。
- 当前工作区没有有效 Git 元数据；每个任务保留可独立测试的文件检查点，不重建仓库、不执行破坏性 Git 命令。

---

## 文件结构与职责

新增或修改文件的边界如下：

- Create: `src/services/visual_styles.py`：风格 ID、旧值映射、公开注册表、资产类型提示词层和风格解析函数。
- Create: `src/api/routes/visual_styles.py`：`GET /api/visual-styles` 只读接口。
- Create: `web/assets/style-references/cinematic-cel.webp`：现代电影级二维赛璐璐综合示例图。
- Create: `web/assets/style-references/semi-realistic-anime.webp`：半写实动漫综合示例图。
- Create: `web/assets/style-references/japanese-animation.webp`：日系商业动画综合示例图。
- Create: `web/assets/style-references/live-action-cinematic.webp`：电影级真人写实综合示例图。
- Modify: `src/schemas/project.py`：扩展 `CreativeProfile` 和请求规范化方法。
- Modify: `src/api/routes/projects.py`：项目创建参数、风格查询路由注册前的状态写入。
- Modify: `src/api/context.py`：状态 JSON 兼容迁移、`ScriptRequest` 兼容字段和风格快照辅助函数。
- Modify: `api_server.py`：挂载风格路由，确认已有 `/static` 挂载可访问 `web/assets`。
- Modify: `src/services/asset_prompt_composer.py`：统一读取主风格和补充标签，追加资产类型风格层。
- Modify: `src/agents/image_gen_agent.py`：角色提示词的动漫/真人分支改为使用规范化风格模式。
- Modify: `src/services/asset_generation.py`：角色母版、组图、参考修订使用风格快照，不再用泛“动漫/写实”覆盖具体风格。
- Modify: `src/agents/visual_prompt_agent.py`：镜头完整提示词加入风格媒介约束，不改写镜头动作事实。
- Modify: `web/js/core/state-api.js`：增加 `visualStyleId`、`styleModifiers` 和风格注册表状态。
- Modify: `web/js/pages/script.js`：加载风格注册表、渲染卡片、提交主风格与补充标签。
- Modify: `web/js/pages/projects.js`：打开项目时恢复风格 ID和补充标签。
- Modify: `studio.html`：将艺术风格标签区替换为卡片容器和补充标签容器。
- Modify: `web/css/base.css`：风格卡片、图片比例、选中和失败状态样式。
- Create: `tests/test_visual_style_registry.py`：注册表与旧值映射测试。
- Create: `tests/test_project_visual_style.py`：项目创建、迁移和快照测试。
- Create: `tests/test_visual_style_frontend_contract.py`：静态页面和 ES Module 契约测试。
- Modify: `tests/test_modular_app_contract.py`：静态风格资源和 API 路径检查。
- Modify: `README.md`：新增风格注册表、参考图目录和开发规则说明。

## Task 1: 建立风格注册表与规范化函数

**Files:**
- Create: `src/services/visual_styles.py`
- Modify: `src/schemas/project.py:8-25`
- Test: `tests/test_visual_style_registry.py`

**Interfaces:**
- Produces `VISUAL_STYLES: tuple[dict, ...]`、`get_visual_styles() -> list[dict]`、`get_visual_style(style_id: str) -> dict`、`resolve_visual_style(visual_style_id: str = "", art_style: str = "", style_modifiers: list[str] | None = None) -> tuple[str, str, list[str]]`、`resolve_profile_style(profile: CreativeProfile) -> dict`。
- `CreativeProfile.from_request` 保持前四个位置参数兼容，新增关键字参数 `visual_style_id: str = ""` 和 `style_modifiers: list[str] | None = None`。

- [ ] **Step 1: Write failing registry tests**

```python
from src.services.visual_styles import get_visual_style, get_visual_styles, resolve_visual_style


def test_registry_exposes_four_primary_styles_with_reference_images():
    styles = get_visual_styles()
    assert [item["id"] for item in styles] == [
        "cinematic_cel", "semi_realistic_anime", "japanese_animation", "live_action_cinematic"
    ]
    assert all(item["reference_image"].startswith("/static/assets/style-references/") for item in styles)
    assert all(set(item["prompt_layers"]) == {"character", "scene", "prop", "shot"} for item in styles)


def test_legacy_style_values_map_without_losing_modifiers():
    assert resolve_visual_style(art_style="动漫") == ("cinematic_cel", "动漫", [])
    assert resolve_visual_style(art_style="写实、赛博朋克") == (
        "live_action_cinematic", "写实、赛博朋克", ["赛博朋克"]
    )


def test_unknown_style_keeps_text_and_uses_live_action_fallback():
    style_id, legacy, modifiers = resolve_visual_style(art_style="实验风格")
    assert (style_id, legacy, modifiers) == ("live_action_cinematic", "实验风格", [])


def test_invalid_explicit_style_id_raises_value_error():
    try:
        get_visual_style("missing-style")
    except ValueError as exc:
        assert "missing-style" in str(exc)
    else:
        raise AssertionError("expected ValueError")
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `pytest tests/test_visual_style_registry.py -q`

Expected: FAIL because `src/services/visual_styles.py` and the new profile fields do not exist.

- [ ] **Step 3: Implement the registry**

Define the four entries with complete Chinese prompt and negative layers. Keep the registry immutable at runtime by returning deep copies from `get_visual_styles()`. Normalize aliases as follows: `写实`/`电影级写实` to `live_action_cinematic`, `动漫`/`二次元` to `cinematic_cel`, `半写实`/`半写实动漫` to `semi_realistic_anime`, and `日系`/`日系动画` to `japanese_animation`. Treat `赛博朋克`、`国风`、`清新` as modifiers; never treat them as primary IDs.

- [ ] **Step 4: Extend `CreativeProfile` without breaking positional callers**

Add:

```python
visual_style_id: str = "live_action_cinematic"
style_modifiers: list[str] = Field(default_factory=list)
```

Place the new arguments after `audio_mode` in `from_request`, call `resolve_visual_style`, and return both canonical and compatibility fields. Keep `visual_style_id=""` as the `from_request` default so legacy callers that send only `art_style="写实"` resolve to `live_action_cinematic`. Set `CreateProjectRequest.visual_style_id` default to `cinematic_cel` so only the new project UI receives the recommended default. Limit modifiers to the known set and deduplicate while preserving order.

- [ ] **Step 5: Run the focused tests**

Run: `pytest tests/test_visual_style_registry.py tests/test_schemas.py -q`

Expected: PASS with the existing schema tests unchanged.

## Task 2: Persist the style and expose the read-only API

**Files:**
- Create: `src/api/routes/visual_styles.py`
- Modify: `src/api/routes/projects.py:11-29`
- Modify: `src/api/context.py:147-154,393-425,537-625`
- Modify: `api_server.py:238-243`
- Test: `tests/test_project_visual_style.py`

**Interfaces:**
- `GET /api/visual-styles` returns `{"styles": [...]}` with no provider call or generation task.
- `POST /api/projects` accepts `visual_style_id` and `style_modifiers` while still accepting `art_style`.
- `_state_from_json` resolves missing or invalid legacy profile fields before `CreativeProfile.model_validate`.

- [ ] **Step 1: Write route and persistence tests**

```python
import json
import pytest
from httpx import ASGITransport, AsyncClient
from api_server import create_app


@pytest.mark.anyio
async def test_style_catalog_and_project_creation_persist_selected_style():
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        catalog = await client.get("/api/visual-styles")
        assert catalog.status_code == 200
        assert {item["id"] for item in catalog.json()["styles"]} == {
            "cinematic_cel", "semi_realistic_anime", "japanese_animation", "live_action_cinematic"
        }
        created = await client.post("/api/projects", json={
            "title": "风格持久化测试",
            "visual_style_id": "japanese_animation",
            "style_modifiers": ["悬疑", "清新", "清新"],
        })
        assert created.status_code == 200
        detail = await client.get(f"/api/projects/{created.json()['project_id']}")
        profile = json.loads(detail.json()["assets"]["state_json"])["creative_profile"]
        assert profile["visual_style_id"] == "japanese_animation"
        assert profile["style_modifiers"] == ["清新"]


@pytest.mark.anyio
async def test_invalid_visual_style_id_returns_422_without_creating_project():
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.post("/api/projects", json={"title": "bad", "visual_style_id": "nope"})
        assert response.status_code == 422
        assert "visual_style_id" in response.text
```

- [ ] **Step 2: Run tests and verify the new contract fails**

Run: `pytest tests/test_project_visual_style.py -q`

Expected: FAIL because the route is not registered and `CreateProjectRequest` rejects or ignores the new fields.

- [ ] **Step 3: Add the catalog route and register it**

Create an `APIRouter` route that returns `{"styles": get_visual_styles()}`. Include `visual_styles_routes.router` in `create_app()` next to the other domain routers. Do not add API-key access or network calls.

- [ ] **Step 4: Extend project creation and legacy loading**

Add `visual_style_id: str = "cinematic_cel"` and `style_modifiers: list[str] = Field(default_factory=list)` to `CreateProjectRequest`, pass them as keyword arguments to `CreativeProfile.from_request`, and write the normalized state before returning. Extend `ScriptRequest` in `src/api/context.py` with `visual_style_id: str = ""` and `style_modifiers: list[str] = Field(default_factory=list)` so the compatibility `/api/script` path can carry an explicit selection without changing its legacy default. In `_state_from_json`, resolve old profiles that have only `art_style` and set `visual_style_id` and `style_modifiers` before validation. Preserve the original `art_style` string in the serialized compatibility field.

- [ ] **Step 5: Verify route and migration behavior**

Run: `pytest tests/test_project_visual_style.py tests/test_project_state_migrations.py tests/test_project_audio_policy.py -q`

Expected: PASS; existing audio/profile tests continue to pass and no project asset directories are touched.

## Task 3: Add static composite reference images

**Files:**
- Create: `web/assets/style-references/cinematic-cel.webp`
- Create: `web/assets/style-references/semi-realistic-anime.webp`
- Create: `web/assets/style-references/japanese-animation.webp`
- Create: `web/assets/style-references/live-action-cinematic.webp`
- Test: `tests/test_visual_style_frontend_contract.py`

**Interfaces:**
- The catalog URLs must resolve through the existing `application.mount("/static", StaticFiles(directory=web_dir))` without changing `/output`.
- Every file is a 16:9 WebP image and is not stored under `output/`.

- [ ] **Step 1: Prepare one shared visual brief**

Use one adult character in a rainy urban side street at dusk, standing near a convenience-store entrance, with the same camera position, pose, clothing silhouette, weather and composition in all four images. The only intentional changes are the medium, line treatment, rendering and photographic behavior specified by the style.

- [ ] **Step 2: Generate or curate the four images**

For each style, use the following explicit suffix in the image-generation prompt:

```text
cinematic_cel: 2D hand-drawn animation, clean controlled ink outlines, two or three layers of hard cel shading, adult realistic anime proportions, cinematic blue-gray palette with restrained warm practical lights, no 3D render, no live-action photo.
semi_realistic_anime: semi-realistic 2D anime concept art, precise linework, natural adult anatomy, painterly material detail with controlled anime facial design, cinematic depth and restrained color grading, no photo, no 3D.
japanese_animation: polished Japanese commercial animation key visual, clean linework, stable character face, simplified but consistent background painting, readable saturated accents, no photorealism, no 3D.
live_action_cinematic: photorealistic live-action film still, real adult human proportions, natural skin and fabric texture, 35mm lens, practical lighting and physically plausible reflections, no illustration, no anime, no 3D render.
```

Keep the source prompt free of text, logos, watermarks, borders and UI. If an external image generator is used during implementation, download the resulting files into the four exact paths and do not create any project generation job.

- [ ] **Step 3: Validate dimensions and static serving**

Run a small Pillow check:

```powershell
@("cinematic-cel", "semi-realistic-anime", "japanese-animation", "live-action-cinematic") | ForEach-Object {
  $file = "web/assets/style-references/$_.webp"
  if (-not (Test-Path $file)) { throw "missing $file" }
}
```

Then run `pytest tests/test_visual_style_frontend_contract.py::test_reference_assets_are_16_by_9 -q`; expected PASS only when all four images exist, are exactly 1600x900, and are WebP.

## Task 4: Replace the creation-page style controls

**Files:**
- Modify: `studio.html:342-351`
- Modify: `web/js/core/state-api.js:24-32`
- Modify: `web/js/pages/script.js:100-163,611-647`
- Modify: `web/js/pages/projects.js:90-96`
- Modify: `web/css/base.css` (new `.visual-style-*` rules)
- Test: `tests/test_visual_style_frontend_contract.py`

**Interfaces:**
- `loadVisualStyles() -> Promise<void>` fetches `/api/visual-styles` and renders `#visual-style-options`.
- `selectVisualStyle(styleId: string) -> void` updates the selected card and `appState.visualStyleId`.
- `createProject()` sends `visual_style_id` and `style_modifiers` in the existing `POST /api/projects` body.

- [ ] **Step 1: Write frontend contract tests**

```python
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_creation_page_has_style_card_container_and_fallback_labels():
    html = (ROOT / "studio.html").read_text(encoding="utf-8")
    assert 'id="visual-style-options"' in html
    assert 'id="style-modifier-tags"' in html
    for label in ("现代电影级二维赛璐璐", "半写实动漫", "日系商业动画", "电影级真人写实"):
        assert label in html


def test_script_module_submits_canonical_style_id():
    source = (ROOT / "web/js/pages/script.js").read_text(encoding="utf-8")
    assert "loadVisualStyles" in source
    assert "visual_style_id" in source
    assert "style_modifiers" in source
```

- [ ] **Step 2: Run the contract tests and verify the old controls fail the new assertions**

Run: `pytest tests/test_visual_style_frontend_contract.py -q`

Expected: FAIL because the current HTML only has `#style-tags` text tags and `createProject()` sends only `art_style`.

- [ ] **Step 3: Add state and catalog rendering**

Add `visualStyleId: 'cinematic_cel'`, `styleModifiers: []`, and `visualStyles: []` to `defaultAppState()`. In `openNewProjectModal()`, call `loadVisualStyles()` once per page session and select the current default. Render each catalog entry as a button-like card with an `img`, `alt`, name, description and tags. Use `onerror` to replace only the image area with a same-size fallback; never replace the entire modal.

- [ ] **Step 4: Wire selection and creation**

Implement `selectVisualStyle(styleId)` with a catalog lookup and explicit invalid-ID guard. Read modifier tags from `#style-modifier-tags .selected`, call `resetProjectSession` with both fields, and include both fields in the existing JSON request. Keep `art_style` as the selected display name plus modifiers for old consumers. Show the canonical name in the project summary.

- [ ] **Step 5: Restore project style**

In `openProject()`, read `profile.visual_style_id` and `profile.style_modifiers`; if missing, use the same client alias map as the backend and select the mapped card. Do not reuse the previous project's card state when `resetProjectSession` runs.

- [ ] **Step 6: Add responsive, accessible card styles**

Add fixed `aspect-ratio: 16 / 9`, `object-fit: cover`, `min-width: 0`, visible `:focus-visible`, selected border and fallback styles. Keep text inside the card, use a single vertical scroll on the modal if needed, and do not add a second page-level scroll container.

- [ ] **Step 7: Run frontend checks**

Run: `pytest tests/test_visual_style_frontend_contract.py tests/test_frontend_startup_state.py tests/test_script_source_flow.py -q`

Expected: PASS; the existing `StudioActions` compatibility surface remains intact.

## Task 5: Compile style constraints into asset prompts

**Files:**
- Modify: `src/services/asset_prompt_composer.py:61-109,228-335,393-430`
- Modify: `src/agents/image_gen_agent.py:60-130,154-242,277-309`
- Modify: `src/services/asset_generation.py:1296-1355,1401-1488`
- Test: `tests/test_visual_style_prompts.py`
- Modify: `tests/test_scene_prompt_layers.py`
- Modify: `tests/test_prop_prompt_composer.py`

**Interfaces:**
- `visual_styles.resolve_profile_style(profile) -> dict` returns the canonical style entry plus normalized modifiers.
- `asset_prompt_composer.project_style_layers(state, asset_type) -> tuple[str, str]` returns positive and negative style text.
- Existing `compose_asset_prompt()` continues returning `effective_prompt`, `provider_prompt`, `provider_negative_prompt`, and `style_mode`.

- [ ] **Step 1: Write prompt tests**

```python
from src.api.context import DramaState
from src.schemas.project import CreativeProfile
from src.services.asset_prompt_composer import project_style_layers
from tests.test_scene_prompt_layers import _state


def test_cel_style_is_injected_into_character_and_scene_prompts():
    state = _state()
    state.creative_profile = CreativeProfile(visual_style_id="cinematic_cel", art_style="现代电影级二维赛璐璐")
    character_positive, character_negative = project_style_layers(state, "character")
    scene_positive, scene_negative = project_style_layers(state, "scene")
    assert "2D" in character_positive or "二维" in character_positive
    assert "赛璐璐" in character_positive
    assert "二维" in scene_positive or "动画" in scene_positive
    assert character_negative and scene_negative


def test_live_action_style_does_not_receive_anime_character_branch():
    state = DramaState(project_id="p2")
    state.creative_profile = CreativeProfile(visual_style_id="live_action_cinematic", art_style="电影级真人写实")
    positive, negative = project_style_layers(state, "character")
    assert "photorealistic" in positive or "真人" in positive
    assert "动漫角色立绘" not in positive
    assert "anime" in negative or "动漫" in negative


def test_style_layer_does_not_replace_shot_specific_user_content():
    state = _state()
    state.creative_profile = CreativeProfile(visual_style_id="japanese_animation", art_style="日系商业动画")
    from src.services.asset_prompt_composer import build_scene_prompt_layers
    result = build_scene_prompt_layers(state, "loc_room", "rain", "scene_center", "镜头保持低机位")
    assert "雨夜客厅空镜" in result["effective_prompt"]
    assert "低机位" in result["effective_prompt"]
```

- [ ] **Step 2: Run prompt tests and observe the current failure**

Run: `pytest tests/test_visual_style_prompts.py tests/test_scene_prompt_layers.py tests/test_prop_prompt_composer.py -q`

Expected: FAIL for the new canonical style IDs because current prompt functions only classify free-text anime substrings and props still use a fixed写实模板.

- [ ] **Step 3: Implement the shared style layer**

Add `project_style_layers()` to read `CreativeProfile.visual_style_id`, append `style_modifiers` as a short suffix, and return only the requested asset type's positive and negative text. Update `_locked_project_constraint()` to use this helper before the existing identity lock. Keep `_provider_payloads()` as the final negative-prompt adapter so Wan's merged mode still works.

- [ ] **Step 4: Update character style branching**

Make `is_anime_art_style()` accept canonical IDs and classify all three anime styles plus `cinematic_cel` as illustrated. Keep `character_style_mode()` return values exactly `anime` and `photorealistic` for existing metadata consumers. Use the style registry's specific layer in `character_style_prefix()`, `character_style_negative()`, and `diagnose_character_prompt()` while retaining the existing view locks for back/full and side views.

- [ ] **Step 5: Update scene and prop prompt composition**

Append the scene style layer after the location identity/state layers, and append the prop style layer after the six-view or single-view object contract. Do not change view count, object identity, shot action text, or negative constraints unrelated to style. For `live_action_cinematic`, retain real-camera wording; for anime styles, explicitly reject live-action and 3D rendering.

- [ ] **Step 6: Update character group and edit prompts**

Pass the canonical style entry into `_character_reference_group_prompt()` and `_character_reference_edit_prompt()`. The group prompt must preserve the existing four-view order, master reference, `enable_sequential` and `n=4`; only replace the generic style sentence with the selected style layer.

- [ ] **Step 7: Run the prompt regression suite**

Run: `pytest tests/test_visual_style_prompts.py tests/test_image_gen_agent_four_views.py tests/test_character_master_ui.py tests/test_scene_prompt_layers.py tests/test_prop_prompt_composer.py -q`

Expected: PASS, including existing reference-group and prop-view behavior.

## Task 6: Apply style constraints to visual shot prompts and task snapshots

**Files:**
- Modify: `src/agents/visual_prompt_agent.py:768-808`
- Modify: `src/api/context.py` snapshot helper used by image/video generation
- Modify: `src/services/asset_generation.py` metadata creation sites around lines 360, 938, 1063, 1259, 1426 and 1487
- Modify: `src/api/routes/production.py:198-207,238-250`
- Test: `tests/test_visual_style_snapshots.py`

**Interfaces:**
- `_assemble_compact_shot_prompt()` receives the project style profile and appends exactly one `【视觉媒介】` block.
- `_build_video_generation_snapshot()` returns `visual_style_id`, `style_modifiers`, and `style_snapshot` without changing reference ordering or model fields.

- [ ] **Step 1: Write snapshot and shot tests**

```python
def test_visual_shot_contains_style_without_copying_another_scene_action():
    from src.agents.visual_prompt_agent import VisualPromptAgent, _CompactShotVisualPrompt
    from src.schemas.project import CreativeProfile
    from src.schemas.visual import VisualDesign
    state = _state()
    state.creative_profile = CreativeProfile(visual_style_id="semi_realistic_anime", art_style="半写实动漫")
    shot = _CompactShotVisualPrompt(
        scene_id=1, subject_state="顾衡在雨中", action_timeline="顾衡推开铁门", environment_delta="",
        camera_motion="低机位", lighting_delta="冷蓝光", prop_state="", negative_prompt="", motion_strength=0.5,
    )
    output = VisualDesign(
        overall_style="半写实动漫", color_palette="冷蓝灰", aspect_ratio="9:16", scenes=[]
    )
    prompt = VisualPromptAgent._assemble_compact_shot_prompt(state.script, state.script.scenes[0], shot, output)
    assert "半写实动漫" in prompt or "半写实" in prompt
    assert "顾衡推开铁门" in prompt
    assert "苏眠站在雨檐下" not in prompt


def test_video_snapshot_freezes_style_selection():
    from src.api.context import _build_video_generation_snapshot
    from src.schemas.project import CreativeProfile
    state = _state()
    state.creative_profile = CreativeProfile(visual_style_id="live_action_cinematic", art_style="电影级真人写实")
    snapshot = _build_video_generation_snapshot(state, 1, state.video_generation_policy)
    assert snapshot["visual_style_id"] == "live_action_cinematic"
    assert snapshot["style_snapshot"]["id"] == "live_action_cinematic"
```

- [ ] **Step 2: Run tests and verify the snapshot fields are absent**

Run: `pytest tests/test_visual_style_snapshots.py tests/test_video_generation_snapshot.py -q`

Expected: FAIL only on the new style assertions; existing video snapshot assertions must remain green.

- [ ] **Step 3: Add one deterministic style block to compact shot assembly**

Use the style registry's `shot` layer and modifiers to append `【视觉媒介】`. Do not pass the project-creation reference image, and do not include unrelated scene or shot action text. Keep `prompt_template_version` incremented only once for the new layer so historical snapshots remain readable.

- [ ] **Step 4: Freeze style metadata for image and video jobs**

At each existing metadata/snapshot construction site, serialize the canonical style ID, modifier list, public style name and positive/negative layer strings. Use a copied dictionary so changing the project profile after submission cannot mutate the saved task. Keep provider, model, resolution, references and seed fields untouched.

- [ ] **Step 5: Verify no automatic regeneration**

Add a test that changes `state.creative_profile.visual_style_id` after creating an image/version snapshot and asserts the old metadata remains `cinematic_cel`. Add a project-level test that opening a project with a new style does not enqueue `asset.generate` or `video.generate`.

- [ ] **Step 6: Run production regressions**

Run: `pytest tests/test_visual_style_snapshots.py tests/test_video_generation_snapshot.py tests/test_video_generation.py tests/test_native_audio_contract.py -q`

Expected: PASS without creating network tasks or touching `output/`.

## Task 7: Finish frontend recovery, static serving, and documentation

**Files:**
- Modify: `web/js/pages/projects.js:90-96`
- Modify: `web/js/core/state-api.js:24-32`
- Modify: `tests/test_modular_app_contract.py`
- Modify: `README.md`

- [ ] **Step 1: Test static URLs and project switching**

```python
def test_style_reference_urls_are_served_by_fastapi():
    from api_server import app
    from fastapi.testclient import TestClient
    client = TestClient(app)
    response = client.get("/static/assets/style-references/cinematic-cel.webp")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"


def test_project_switch_restores_style_id_without_stale_card_markup():
    source = (ROOT / "web/js/pages/projects.js").read_text(encoding="utf-8")
    assert "visual_style_id" in source
    assert "style_modifiers" in source
    assert "resetProjectSession" in source
```

- [ ] **Step 2: Run the static and switching tests**

Run: `pytest tests/test_modular_app_contract.py tests/test_visual_style_frontend_contract.py -q`

Before running, extend `tests/test_modular_app_contract.py::EXPECTED_PATHS` with `/api/visual-styles` and change its OpenAPI path count assertion from `87` to `88`. Expected: PASS after the four files are present and the project restore path reads the canonical profile.

- [ ] **Step 3: Document the extension rule**

Add a README section that names `src/services/visual_styles.py` as the only place to add a main style, requires a stable ID and four asset-type layers, and states that reference images belong under `web/assets/style-references/`, never under `output/`. Document the compatibility mapping and the fact that style changes do not regenerate history.

- [ ] **Step 4: Run JavaScript syntax checks**

Run: `node --check web/js/core/state-api.js` and `node --check web/js/pages/script.js`.

Expected: both commands exit with code 0. If Node is unavailable, run the existing `node scripts/module_ui_smoke.js` and record the environment limitation rather than installing a build tool.

## Task 8: Full validation and handoff

**Files:**
- Test: all existing `tests/test_*.py`
- Verify: `studio.html`, `/api/visual-styles`, `/static/assets/style-references/*`, and `output/`

- [ ] **Step 1: Run focused backend and frontend tests together**

Run: `pytest tests/test_visual_style_registry.py tests/test_project_visual_style.py tests/test_visual_style_prompts.py tests/test_visual_style_snapshots.py tests/test_visual_style_frontend_contract.py -q`

Expected: PASS with no external provider calls.

- [ ] **Step 2: Run the full regression suite**

Run: `pytest -q`

Expected: all existing project isolation, job, asset, prompt, audio, Seedance and production tests pass.

- [ ] **Step 3: Verify asset preservation**

Record the project IDs and file count under `output/` before and after the test run. Assert that no file under `output/` was deleted, moved or rewritten by style selection tests. Remove any temporary test projects through the project API only after verifying they have no existing user assets; never use recursive deletion on `output/`.

- [ ] **Step 4: Browser smoke test without generation**

Start the existing API server, open the root page, create four temporary projects selecting each style, refresh and reopen each project, and verify the selected card, prompt metadata and style name. Stop before clicking any image or video generation button. Delete only the temporary empty projects through the UI/API.

- [ ] **Step 5: Record the final result**

Report changed files, test commands and results, static reference URLs, and explicitly state that no image/video generation task was submitted and no `output/` asset was removed.

## Self-review checklist

- Spec coverage: UI cards and combined images are covered by Tasks 3-4; registry/API and legacy mapping by Tasks 1-2; prompt consistency by Task 5; visual shot and task snapshots by Task 6; static serving, docs and browser acceptance by Tasks 7-8.
- Placeholder scan: no `TODO`, `TBD`, or unresolved implementation marker is used in the plan.
- Type consistency: `visual_style_id`, `style_modifiers`, `resolve_visual_style`, `project_style_layers`, `loadVisualStyles`, and `selectVisualStyle` are defined once and reused consistently.
- Scope: the plan does not add custom uploads, provider changes, automatic regeneration, or output cleanup.
