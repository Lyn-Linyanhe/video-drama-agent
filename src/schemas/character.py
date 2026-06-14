"""Character analysis and image generation schemas."""

from pydantic import BaseModel, Field


class CharacterProfile(BaseModel):
    """Detailed analysis of a single character's appearance and traits."""

    name: str = Field(description="角色名称")
    gender: str = Field(description="性别：男/女/其他")
    age_range: str = Field(description="年龄段，如'25-30岁'")
    height: str = Field(description="身高体型，如'168cm，苗条'")
    face_shape: str = Field(description="脸型，如'瓜子脸'/'方脸'/'圆脸'")
    skin_tone: str = Field(description="肤色，如'白皙'/'小麦色'")
    eyes: str = Field(description="眼睛特征，大小/颜色/形状")
    nose: str = Field(description="鼻子特征")
    lips: str = Field(description="嘴唇特征")
    hair_style: str = Field(description="发型发色，如'黑色长直发'/'棕色波浪卷'")
    eyebrows: str = Field(description="眉形")
    body_type: str = Field(description="身材类型，如'苗条'/'魁梧'/'丰满'/'瘦削'")
    clothing_style: str = Field(description="服装风格与颜色")
    accessories: str = Field(default="", description="配饰，如眼镜/耳环/手表")
    distinguishing_features: str = Field(
        default="", description="标志性特征，如'左眼下方有一颗痣'"
    )
    personality: str = Field(description="性格关键词")
    role_type: str = Field(description="角色类型：主角/配角/反派/路人")


class CharacterImage(BaseModel):
    """A generated character portrait image."""

    character_name: str = Field(description="角色名称")
    image_prompt: str = Field(description="用于生成该图的英文提示词")
    image_path: str = Field(description="本地文件路径")
    seed: int = Field(description="生成使用的随机种子")
    width: int = Field(default=1024, description="图片宽度")
    height: int = Field(default=1024, description="图片高度")
    api_provider: str = Field(default="mock", description="使用的API")
    cost: float = Field(default=0.0, description="生成费用(USD)")


class CharacterImageAssets(BaseModel):
    """Collection of all character images for a project."""

    characters: list[CharacterImage] = Field(
        default_factory=list, description="角色形象图列表"
    )
    total_cost: float = Field(default=0.0, description="总费用")
    generation_time_sec: float = Field(default=0.0, description="生成耗时(秒)")
