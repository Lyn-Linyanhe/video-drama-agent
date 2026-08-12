"""Schema for quality review — output of Review Agent."""

from pydantic import BaseModel, Field
from typing import Literal


class Issue(BaseModel):
    """A single issue found during review."""
    scene_id: int = Field(description="Which scene has the issue")
    severity: Literal["critical", "major", "minor"] = Field(description="Issue severity")
    type: Literal["consistency", "quality", "timing", "content"] = Field(
        description="Issue category"
    )
    description: str = Field(description="What the issue is")
    suggestion: str = Field(description="How to fix it")


class ReviewResult(BaseModel):
    """Result of quality review."""
    overall_score: float = Field(description="0-1 overall quality score", ge=0.0, le=1.0)
    passed: bool = Field(description="Whether the generation passes review")
    issues: list[Issue] = Field(default_factory=list, description="Issues found")
    summary: str = Field(default="", description="Review summary in natural language")
