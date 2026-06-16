"""Scene environment image generation schemas."""

from pydantic import BaseModel, Field


class SceneImage(BaseModel):
    """A generated scene environment/location image."""

    scene_id: int = Field(description="对应的场景编号")
    location: str = Field(description="场景地点描述")
    view_type: str = Field(
        default="wide",
        description="景别类型: wide(全景建立), medium(中景细节)"
    )
    image_prompt: str = Field(description="用于生成该场景图的英文提示词")
    image_path: str = Field(description="本地文件路径")
    seed: int = Field(description="生成使用的随机种子")
    width: int = Field(default=1080, description="图片宽度")
    height: int = Field(default=1920, description="图片高度")
    api_provider: str = Field(default="mock", description="使用的API")
    cost: float = Field(default=0.0, description="生成费用(USD)")


class SceneImageAssets(BaseModel):
    """Collection of all scene environment images for a project."""

    scenes: list[SceneImage] = Field(
        default_factory=list, description="场景图列表"
    )
    total_cost: float = Field(default=0.0, description="总费用")
    generation_time_sec: float = Field(default=0.0, description="生成耗时(秒)")
