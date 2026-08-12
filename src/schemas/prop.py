"""Plot-relevant prop reference image schemas."""

from pydantic import BaseModel, Field


class PropImage(BaseModel):
    """One generated reference image for a prop."""

    prop_id: str = Field(description="Matches VisualAssetManifest.props[].prop_id")
    prop_name: str = Field(description="Display name")
    view_type: str = Field(default="standard_views", description="Prop view type or legacy standard_views sheet")
    image_prompt: str = Field(description="Chinese prop image prompt")
    image_path: str = Field(description="Local image path")
    seed: int = Field(description="Generation seed")
    width: int = Field(default=1536, description="Image width")
    height: int = Field(default=1024, description="Image height")
    api_provider: str = Field(default="mock", description="Image provider")
    cost: float = Field(default=0.0, description="Generation cost")
    version_id: str = Field(default="", description="Active image version used for history and cache refresh")
    prompt_template_version: int = Field(default=1, description="Prompt template version")
    custom_instructions: str = Field(default="", description="User supplemental requirements")
    is_master: bool = Field(default=False)
    needs_confirmation: bool = Field(default=False)
    master_version_id: str = Field(default="")
    reference_version_ids: list[str] = Field(default_factory=list)
    derived_from_version_id: str = Field(default="")
    generation_mode: str = Field(default="single")
    model: str = Field(default="")
    resolution: str = Field(default="")
    is_stale: bool = Field(default=False)
    requirement_signature: str = Field(default="")
    quality_report: dict = Field(default_factory=dict)


class PropImageAssets(BaseModel):
    """Collection of project prop reference images."""

    props: list[PropImage] = Field(default_factory=list)
    total_cost: float = Field(default=0.0)
