"""FastAPI routes extracted from the original server entrypoint."""

from typing import Literal

from fastapi import APIRouter
from pydantic import field_validator
from ..context import *

router = APIRouter()


class CreateProjectRequest(BaseModel):
    title: str = Field(default="未命名项目")
    genre_tags: str = Field(default="")
    art_style: str = Field(default="写实")
    video_ratio: str = Field(default="9:16")
    script_source: Literal["idea", "script"] = "idea"
    audio_mode: Literal["auto", "native_dialogue", "external_tts"] = "auto"
    visual_style_id: str = "cinematic_cel"
    style_modifiers: list[str] = Field(default_factory=list)

    @field_validator("visual_style_id")
    @classmethod
    def validate_visual_style_id(cls, value: str) -> str:
        from src.services.visual_styles import get_visual_style
        normalized = value.strip()
        if not normalized:
            return "cinematic_cel"
        try:
            get_visual_style(normalized)
        except ValueError as exc:
            raise ValueError(f"visual_style_id is invalid: {normalized}") from exc
        return normalized


@router.post("/api/projects")
async def create_project_endpoint(req: CreateProjectRequest):
    """创建空项目（不生成剧本）。"""
    pid = project_store.create_project(title=req.title)
    state = DramaState(project_id=pid, input_mode=req.script_source, script_phase="source_required")
    state.creative_profile = CreativeProfile.from_request(
        req.genre_tags, req.art_style, req.video_ratio, req.audio_mode,
        visual_style_id=req.visual_style_id,
        style_modifiers=req.style_modifiers,
    )
    project_store.set_state_json(pid, _state_to_json(state))
    return {"project_id": pid, "message": "项目已创建"}

@router.get("/api/projects")
async def list_projects():
    """获取项目列表。"""
    projects = project_store.list_projects()
    return [ProjectMeta(**p) for p in projects]


@router.get("/api/projects/{project_id}")
async def get_project_detail(project_id: str):
    """获取项目的完整元数据（含剧本、视觉设计等）。"""
    try:
        meta = project_store.get_project(project_id)
        if not meta:
            raise HTTPException(404, "项目不存在")
        state = _load_project_state(project_id)
        # Loading may normalize legacy scene bindings and synchronize the
        # script/visual snapshots. Reload metadata before building the response
        # so the first project-detail request cannot return the stale snapshot
        # read immediately before migration.
        meta = project_store.get_project(project_id) or meta
        assets = project_store.get_asset_groups(project_id)
        return {
            "meta": meta,
            "assets": assets,
            "missing_media_paths": _missing_media_paths(state),
        }
    except ValueError:
        raise HTTPException(400, "无效的 project_id")

@router.get("/api/projects/{project_id}/assets")
async def get_project_assets(project_id: str):
    """获取指定项目的所有资产文件（多图分组格式）。"""
    try:
        return project_store.get_asset_groups(project_id)
    except ValueError:
        return {"error": "无效的 project_id"}


@router.post("/api/projects/{project_id}/assets")
async def add_project_asset(project_id: str, req: ManualAssetRequest):
    """Add a manually managed visual asset and bind it to selected shots."""
    from src.schemas.script import (
        LocationAsset,
        PropAsset,
        VisualCharacterAsset,
    )
    from src.utils.visual_asset_manifest import normalize_scene_assets, normalize_scene_name, stable_location_id

    state = _load_project_state(project_id)
    if not state.script:
        raise HTTPException(409, "请先完成剧本解析")
    if not state.script.asset_manifest:
        raise HTTPException(409, "请先重新提取视觉资产后再手工添加")

    asset_type = req.asset_type.strip().lower()
    if asset_type not in {"character", "scene", "prop"}:
        raise HTTPException(422, "asset_type 必须为 character、scene 或 prop")

    valid_scene_ids = {scene.scene_id for scene in state.script.scenes}
    scene_ids = list(dict.fromkeys(req.scene_ids))
    unknown_scene_ids = sorted(set(scene_ids) - valid_scene_ids)
    if unknown_scene_ids:
        raise HTTPException(422, f"镜头编号不存在：{', '.join(map(str, unknown_scene_ids))}")
    if asset_type in {"scene", "prop"} and not scene_ids:
        raise HTTPException(422, "场景和道具至少需要绑定一个镜头")

    manifest = state.script.asset_manifest
    bindings = {binding.scene_id: binding for binding in manifest.shot_bindings}
    scenes_by_id = {scene.scene_id: scene for scene in state.script.scenes}
    missing_bindings = sorted(valid_scene_ids - set(bindings))
    if missing_bindings:
        raise HTTPException(409, "项目视觉资产绑定不完整，请先重新解析剧本")

    clean_name = req.name.strip()
    asset_id = f"manual_{asset_type}_{uuid.uuid4().hex[:10]}"
    if asset_type == "character":
        if any(item.name == clean_name for item in manifest.characters):
            raise HTTPException(409, "已有同名角色")
        manifest.characters.append(VisualCharacterAsset(
            asset_id=asset_id,
            name=clean_name,
            appearance_mode="on_screen",
            description=req.description.strip(),
        ))
        for scene_id in scene_ids:
            if asset_id not in bindings[scene_id].on_screen_character_ids:
                bindings[scene_id].on_screen_character_ids.append(asset_id)
            if clean_name not in scenes_by_id[scene_id].characters:
                scenes_by_id[scene_id].characters.append(clean_name)
        if state.visual_design:
            state.visual_design.global_character_descriptions[clean_name] = req.description.strip() or clean_name
            state.visual_design.global_character_descriptions_cn[clean_name] = req.description.strip() or clean_name
    elif asset_type == "scene":
        if any(normalize_scene_name(item.canonical_name) == normalize_scene_name(clean_name) for item in manifest.locations):
            raise HTTPException(409, "已有同名场景")
        manifest.locations.append(LocationAsset(
            location_id=stable_location_id(clean_name),
            canonical_name=clean_name,
            time_condition=req.time_condition.strip(),
            environment_description=req.description.strip(),
            representative_scene_id=scene_ids[0],
            shot_ids=scene_ids,
        ))
        for scene_id in scene_ids:
            bindings[scene_id].location_id = stable_location_id(clean_name)
            scenes_by_id[scene_id].location = clean_name
    else:
        if any(item.name == clean_name for item in manifest.props):
            raise HTTPException(409, "已有同名道具")
        manifest.props.append(PropAsset(
            prop_id=asset_id,
            name=clean_name,
            description=req.description.strip(),
            shot_ids=scene_ids,
            is_handheld=req.is_handheld,
        ))
        for scene_id in scene_ids:
            if asset_id not in bindings[scene_id].prop_ids:
                bindings[scene_id].prop_ids.append(asset_id)

    normalize_scene_assets(state.script)
    _save_project_state(state)
    return {
        "script": state.script.model_dump(),
        "visual_design": state.visual_design.model_dump() if state.visual_design else None,
        "state_json": _state_to_json(state),
        "production": _production_payload(state),
    }


class StateSaveRequest(BaseModel):
    state_json: str = Field(..., description="完整状态 JSON")


@router.put("/api/projects/{project_id}/state")
async def save_project_state(project_id: str, req: StateSaveRequest):
    """保存当前交互式状态。"""
    try:
        if not project_store.get_project(project_id):
            raise HTTPException(404, "项目不存在")
        supplied = _state_from_json(req.state_json)
        if supplied.project_id != project_id:
            raise HTTPException(409, "项目状态与目标项目不匹配")
        project_store.set_state_json(project_id, req.state_json)
        return {"message": "已保存"}
    except HTTPException:
        raise
    except ValueError:
        raise HTTPException(400, "无效的 project_id")

@router.delete("/api/projects/{project_id}")
async def delete_project(project_id: str):
    """删除项目及其所有资产。"""
    try:
        project_store.delete_project(project_id)
        return {"message": "已删除"}
    except ValueError:
        raise HTTPException(400, "无效的 project_id")

class RenameRequest(BaseModel):
    title: str = Field(..., description="新标题")


class StateSaveRequest(BaseModel):
    state_json: str = Field(..., description="完整状态 JSON")

@router.put("/api/projects/{project_id}/rename")
async def rename_project(project_id: str, req: RenameRequest):
    """重命名项目。"""
    try:
        ok = project_store.rename_project(project_id, req.title)
        if not ok:
            raise HTTPException(404, "项目不存在")
        return {"message": "已重命名"}
    except ValueError:
        raise HTTPException(400, "无效的 project_id")

@router.post("/api/projects/{project_id}/copy")
async def copy_project(project_id: str):
    """复制项目（含所有元数据）。"""
    try:
        new_id = project_store.copy_project(project_id)
        if not new_id:
            raise HTTPException(404, "项目不存在")
        return {"message": "已复制", "new_project_id": new_id}
    except ValueError:
        raise HTTPException(400, "无效的 project_id")

__all__ = [name for name in globals() if not name.startswith("__")]
