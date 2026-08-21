"""Versioned contracts for the text-level storyboard prompt audit."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


IssueLayer = Literal["source_parse", "prompt"]
IssueScope = Literal["source_alignment", "single_shot", "cross_shot", "rule"]
IssueConfidence = Literal["confirmed", "suspected", "rejected", "unreviewed"]
ReviewVerdict = Literal["pass", "needs_review", "blocked", "unreviewed"]
EvidenceSource = Literal[
    "source_script",
    "structured_script",
    "prompt",
    "asset_manifest",
    "visual_design",
    "prompt_policy",
    "rule",
    "audit",
]


class PromptReviewEvidence(BaseModel):
    """A quoted, addressable fragment from the immutable audit snapshot."""

    source: EvidenceSource
    scene_id: int = Field(default=0, ge=0)
    fact_id: str = Field(default="", max_length=300)
    section: str = Field(default="", max_length=200)
    excerpt: str = Field(min_length=1, max_length=4000)
    line_start: int = Field(default=0, ge=0)
    line_end: int = Field(default=0, ge=0)
    time_start_sec: float | None = Field(default=None, ge=0)
    time_end_sec: float | None = Field(default=None, ge=0)


class AuditFact(BaseModel):
    """One structured fact with a stable id and provenance status."""

    fact_id: str = Field(min_length=1, max_length=300)
    label: str = Field(default="", max_length=200)
    value: Any = ""
    provenance: Literal[
        "structured_script",
        "asset_manifest",
        "visual_design",
        "prompt",
        "source_script",
        "derived",
        "unavailable",
    ] = "structured_script"
    status: Literal["available", "derived", "unavailable", "not_applicable"] = "available"
    scene_id: int = Field(default=0, ge=0)


class SourceParagraphSnapshot(BaseModel):
    paragraph_id: str = Field(min_length=1, max_length=100)
    line_start: int = Field(ge=1)
    line_end: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=20000)


class SourceScriptSnapshot(BaseModel):
    status: Literal["available", "not_applicable"]
    text_sha256: str = Field(default="", max_length=64)
    paragraphs: list[SourceParagraphSnapshot] = Field(default_factory=list)
    unavailable_reason: str = Field(default="", max_length=300)


class PromptSectionSnapshot(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    text: str = Field(default="", max_length=30000)
    start_offset: int = Field(default=0, ge=0)
    end_offset: int = Field(default=0, ge=0)


class PromptTimelineSegment(BaseModel):
    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)
    text: str = Field(default="", max_length=4000)
    explicit: bool = True


class PromptSnapshot(BaseModel):
    prompt_text: str = Field(default="", max_length=40000)
    negative_prompt: str = Field(default="", max_length=10000)
    duration_sec: float | None = Field(default=None, ge=0)
    sections: list[PromptSectionSnapshot] = Field(default_factory=list)
    timeline_segments: list[PromptTimelineSegment] = Field(default_factory=list)
    timeline_errors: list[str] = Field(default_factory=list)


class AssetBindingSnapshot(BaseModel):
    location_id: str = ""
    state_id: str = ""
    on_screen_character_ids: list[str] = Field(default_factory=list)
    voice_only_speakers: list[str] = Field(default_factory=list)
    prop_ids: list[str] = Field(default_factory=list)
    visible_state_notes: str = ""
    status: Literal["available", "unavailable"] = "unavailable"


class SceneFactSheet(BaseModel):
    """Complete facts needed to audit one structured shot and its prompt."""

    fact_sheet_id: str = Field(min_length=1, max_length=100)
    scene_id: int = Field(ge=1)
    location: AuditFact
    characters: list[AuditFact] = Field(default_factory=list)
    dialogue: list[AuditFact] = Field(default_factory=list)
    action: AuditFact
    camera: AuditFact
    emotion: AuditFact
    duration_sec: int = Field(ge=0)
    transition_from_prev: str = "cut"
    transition_duration_sec: float = Field(default=0.25, ge=0)
    continuity_from_prev: bool = False
    continuity_reason: str = ""
    asset_binding: AssetBindingSnapshot = Field(default_factory=AssetBindingSnapshot)
    visual_facts: list[AuditFact] = Field(default_factory=list)
    prompt: PromptSnapshot = Field(default_factory=PromptSnapshot)
    source_references: list[PromptReviewEvidence] = Field(default_factory=list)


class PromptReviewStageRecord(BaseModel):
    stage: str = Field(min_length=1, max_length=100)
    status: Literal["completed", "skipped", "unreviewed", "failed"]
    scene_ids: list[int] = Field(default_factory=list)
    candidate_count: int = Field(default=0, ge=0)
    confirmed_count: int = Field(default=0, ge=0)
    error: str = Field(default="", max_length=1000)


class PromptReviewAuditSnapshot(BaseModel):
    """The exact inputs against which a report can be traced later."""

    source_script: SourceScriptSnapshot
    structured_script: dict[str, Any] = Field(default_factory=dict)
    asset_manifest: dict[str, Any] | None = None
    visual_design: dict[str, Any] | None = None
    scene_vision_descriptions: dict[str, Any] = Field(default_factory=dict)
    generation_prompts: dict[str, Any] = Field(default_factory=dict)
    prompt_policy: dict[str, Any] = Field(default_factory=dict)
    scene_facts: list[SceneFactSheet] = Field(default_factory=list)
    rule_facts: list[AuditFact] = Field(default_factory=list)
    stage_records: list[PromptReviewStageRecord] = Field(default_factory=list)


class PromptReviewIssue(BaseModel):
    """A backward-compatible issue with v2 provenance and attribution."""

    scene_id: int = Field(default=0, ge=0, description="Primary shot; 0 means project-wide")
    related_scene_ids: list[int] = Field(default_factory=list)
    severity: Literal["critical", "major", "minor"]
    category: Literal[
        "coverage",
        "fidelity",
        "clarity",
    "continuity",
    "executability",
    "quality",
    "audit",
    ]
    description: str = Field(min_length=1, max_length=1000)
    suggestion: str = Field(min_length=1, max_length=1000)
    # v1 reports keep a plain text evidence string. New reports use the
    # addressable evidence_items list as the authoritative representation.
    evidence: str = Field(default="", max_length=1000)
    layer: IssueLayer = "prompt"
    scope: IssueScope = "single_shot"
    issue_code: str = Field(default="legacy.unspecified", min_length=1, max_length=120)
    confidence: IssueConfidence = "confirmed"
    fact_ids: list[str] = Field(default_factory=list)
    evidence_items: list[PromptReviewEvidence] = Field(default_factory=list)
    repair_action: Literal["edit_script", "edit_prompt", "inspect", "none"] = "none"
    rule_id: str = Field(default="", max_length=120)
    deterministic: bool = False
    blocks_generation: bool = False
    candidate_id: str = Field(default="", max_length=120)
    checked_prompt_sections: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _normalize_related_scene_ids(self) -> "PromptReviewIssue":
        self.related_scene_ids = sorted({scene_id for scene_id in self.related_scene_ids if scene_id > 0})
        return self

    @model_validator(mode="before")
    @classmethod
    def _accept_structured_evidence_alias(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        raw_evidence = normalized.get("evidence")
        if isinstance(raw_evidence, list):
            # Providers occasionally emit the structured list under the old
            # ``evidence`` key, or emit both keys.  The structured value is
            # authoritative when both keys are present.
            if not normalized.get("evidence_items"):
                normalized["evidence_items"] = raw_evidence
            normalized["evidence"] = ""
        return normalized


class PromptReviewShot(BaseModel):
    scene_id: int = Field(ge=1)
    score: int = Field(ge=0, le=100)
    passed: bool
    summary: str = Field(default="", max_length=1000)
    fact_sheet_id: str = ""
    duration_sec: int | None = Field(default=None, ge=0)
    issues: list[PromptReviewIssue] = Field(default_factory=list)


class PromptReviewResult(BaseModel):
    """Kimi audit report; v1 fields remain readable for existing projects."""

    report_version: int = Field(default=1, ge=1)
    audit_signature: str = Field(default="", max_length=64)
    verdict: ReviewVerdict = "needs_review"
    overall_score: int = Field(ge=0, le=100)
    passed: bool
    summary: str = Field(default="", max_length=2000)
    shots: list[PromptReviewShot] = Field(default_factory=list)
    issues: list[PromptReviewIssue] = Field(default_factory=list)
    shots_issue_index: dict[str, list[PromptReviewIssue]] = Field(default_factory=dict)
    audit_snapshot: PromptReviewAuditSnapshot | None = None
    stage_records: list[PromptReviewStageRecord] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _legacy_defaults(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if "report_version" not in normalized:
            normalized["report_version"] = 1
        if "verdict" not in normalized:
            normalized["verdict"] = "pass" if normalized.get("passed") is True else "needs_review"
        return normalized
