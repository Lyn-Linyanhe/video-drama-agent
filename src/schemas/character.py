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
    view_type: str = Field(
        default="front_half",
        description="视角类型: front_half(正面半身), front_full(正面全身), left_profile(左侧面), back_full(背面全身); 兼容 portrait/profile/full_body"
    )
    image_prompt: str = Field(description="用于生成该图的中文提示词")
    image_path: str = Field(description="本地文件路径")
    seed: int = Field(description="生成使用的随机种子")
    width: int = Field(default=1024, description="图片宽度")
    height: int = Field(default=1024, description="图片高度")
    api_provider: str = Field(default="mock", description="使用的API")
    cost: float = Field(default=0.0, description="生成费用(USD)")
    version_id: str = Field(default="", description="当前活动图片版本，用于历史和缓存刷新")
    prompt_template_version: int = Field(default=1, description="角色图片提示词模板版本")
    custom_instructions: str = Field(default="", description="用户补充的角色图片生成要求")

    model: str = Field(default="", description="Concrete image model used for this version")
    resolution: str = Field(default="", description="Requested quality preset")
    generation_mode: str = Field(default="single", description="single, reference_group, or reference_edit")
    group_id: str = Field(default="", description="Shared identifier for one reference group")
    master_version_id: str = Field(default="", description="Locked identity-reference version")
    reference_version_ids: list[str] = Field(default_factory=list, description="Additional reference versions")
    quality_report: dict = Field(default_factory=dict, description="Technical and visual image quality report")

    def is_portrait(self) -> bool:
        return self.view_type in {"front_half", "portrait"}

    def is_profile(self) -> bool:
        return self.view_type in {"left_profile", "profile"}

    def is_full_body(self) -> bool:
        return self.view_type in {"front_full", "back_full", "full_body"}


class CharacterImageAssets(BaseModel):
    """Collection of all character images for a project."""

    reference_masters: dict[str, str] = Field(default_factory=dict, description="Character to active master version")

    characters: list[CharacterImage] = Field(
        default_factory=list, description="角色形象图列表"
    )
    total_cost: float = Field(default=0.0, description="总费用")
    generation_time_sec: float = Field(default=0.0, description="生成耗时(秒)")
