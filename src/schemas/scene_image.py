"""Scene environment image generation schemas."""

from pydantic import BaseModel, Field


class SceneImage(BaseModel):
    """A generated scene environment/location image."""

    scene_id: int = Field(description="对应的场景编号")
    location_id: str = Field(default="", description="稳定场景资产 ID；旧项目可为空")
    state_id: str = Field(default="", description="场景状态 ID；旧项目可为空")
    location: str = Field(description="场景地点描述")
    view_type: str = Field(
        default="wide",
        description="景别类型: wide(全景建立), medium(中景细节)"
    )
    image_prompt: str = Field(description="用于生成该场景图的中文提示词")
    base_prompt: str = Field(default="", description="不含用户补充要求的场景基础提示词")
    custom_instructions: str = Field(default="", description="用户补充的场景图片生成要求")
    image_path: str = Field(description="本地文件路径")
    seed: int = Field(description="生成使用的随机种子")
    width: int = Field(default=1080, description="图片宽度")
    height: int = Field(default=1920, description="图片高度")
    api_provider: str = Field(default="mock", description="使用的API")
    cost: float = Field(default=0.0, description="生成费用(USD)")
    version_id: str = Field(default="", description="当前活动图片版本，用于历史和缓存刷新")
    source_signature: str = Field(default="", description="生成时采用的场景设定签名")
    is_stale: bool = Field(default=False, description="场景设定更新后图片是否需要重生成")
    is_master: bool = Field(default=False, description="Whether this image is a confirmed/unconfirmed state master")
    needs_confirmation: bool = Field(default=False, description="Whether the state master must be confirmed before derivatives")
    master_version_id: str = Field(default="", description="Master version used to derive this image")
    reference_version_ids: list[str] = Field(default_factory=list, description="Reference versions used by this image")
    derived_from_version_id: str = Field(default="", description="Immediate source version for reference edits")
    requirement_signature: str = Field(default="", description="Scene view requirement signature used for generation")
    generation_mode: str = Field(default="single", description="single or reference_group")
    group_id: str = Field(default="", description="Shared identifier for one scene reference group")
    group_view_count: int = Field(default=0, ge=0, le=4, description="Number of views in the active group")
    model: str = Field(default="", description="Actual image model used for this version")
    resolution: str = Field(default="", description="Actual image resolution preset")
    prompt_template_version: int = Field(default=1, description="Canonical scene prompt template version")
    quality_report: dict = Field(default_factory=dict, description="Technical and visual image quality report")


class SceneImageAssets(BaseModel):
    """Collection of all scene environment images for a project."""

    scenes: list[SceneImage] = Field(
        default_factory=list, description="场景图列表"
    )
    total_cost: float = Field(default=0.0, description="总费用")
    generation_time_sec: float = Field(default=0.0, description="生成耗时(秒)")
