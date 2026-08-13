"""Schemas for Kimi's text-level storyboard prompt review."""

from typing import Literal

from pydantic import BaseModel, Field


class PromptReviewIssue(BaseModel):
    scene_id: int = Field(default=0, ge=0, description="0 means project-wide")
    severity: Literal["critical", "major", "minor"]
    category: Literal[
        "coverage",
        "fidelity",
        "clarity",
        "continuity",
        "executability",
        "quality",
    ]
    description: str = Field(min_length=1, max_length=1000)
    suggestion: str = Field(min_length=1, max_length=1000)
    evidence: str = Field(default="", max_length=1000)


class PromptReviewShot(BaseModel):
    scene_id: int = Field(ge=1)
    score: int = Field(ge=0, le=100)
    passed: bool
    summary: str = Field(default="", max_length=1000)
    issues: list[PromptReviewIssue] = Field(default_factory=list)


class PromptReviewResult(BaseModel):
    """Kimi's review of script-to-prompt coverage and prompt usability."""

    overall_score: int = Field(ge=0, le=100)
    passed: bool
    summary: str = Field(default="", max_length=2000)
    shots: list[PromptReviewShot] = Field(default_factory=list)
    issues: list[PromptReviewIssue] = Field(default_factory=list)
