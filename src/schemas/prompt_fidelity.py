"""Contracts for deterministic structured-script to prompt fidelity checks."""

from typing import Literal
from pydantic import BaseModel, Field


class PromptFidelityEvidence(BaseModel):
    scene_id: int = 0
    fact_id: str = ""
    expected_excerpt: str = Field(min_length=1, max_length=4000)
    prompt_excerpt: str = Field(default="", max_length=4000)
    prompt_section: str = ""


class PromptFidelityIssue(BaseModel):
    scene_id: int
    issue_code: str
    severity: Literal["critical", "major", "minor"]
    description: str
    deterministic: bool = True
    blocks_generation: bool = False
    evidence: PromptFidelityEvidence


class PromptFidelityReport(BaseModel):
    status: Literal["clean", "warning", "blocked"] = "clean"
    scene_count: int = 0
    shot_count: int = 0
    dialogue_total: int = 0
    dialogue_covered: int = 0
    issues: list[PromptFidelityIssue] = Field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"
