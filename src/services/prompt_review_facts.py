"""Deterministic, provenance-preserving inputs for the Kimi prompt audit."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from src.schemas.prompt import GenerationPrompts, PromptPolicy
from src.schemas.prompt_review import (
    AssetBindingSnapshot,
    AuditFact,
    PromptReviewAuditSnapshot,
    PromptReviewEvidence,
    PromptSectionSnapshot,
    PromptSnapshot,
    PromptTimelineSegment,
    SceneFactSheet,
    SourceParagraphSnapshot,
    SourceScriptSnapshot,
)
from src.schemas.script import ScriptBreakdown
from src.schemas.visual import VisualDesign
from src.utils.visual_asset_manifest import binding_for_scene


_TIMELINE_RANGE_RE = re.compile(
    r"(?P<start>\d+(?:\.\d+)?)\s*(?:-|至|到)\s*"
    r"(?P<end>\d+(?:\.\d+)?)\s*(?:s|秒)?\s*[：:，,、\s]*"
    r"(?P<text>.*?)(?=(?:\d+(?:\.\d+)?\s*(?:-|至|到)\s*\d)|$)",
    re.IGNORECASE | re.DOTALL,
)
_SECTION_RE = re.compile(r"【(?P<label>[^】\n]{1,80})】")
_REFERENCE_RE = re.compile(r"@(?P<kind>[A-Za-z_\u4e00-\u9fff]+):(?P<asset>[^\s:@]+)(?::(?P<view>[^\s@]+))?")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fact(scene_id: int, suffix: str, label: str, value: Any, provenance: str = "structured_script") -> AuditFact:
    return AuditFact(
        fact_id=f"scene:{scene_id}:{suffix}",
        label=label,
        value=value,
        provenance=provenance,
        status="available" if value not in (None, "", []) else "unavailable",
        scene_id=scene_id,
    )


def split_source_paragraphs(source_text: str) -> list[SourceParagraphSnapshot]:
    """Split authored source into stable paragraph and one-based line references."""
    lines = source_text.splitlines()
    paragraphs: list[SourceParagraphSnapshot] = []
    start = None
    for index, line in enumerate(lines, start=1):
        if line.strip() and start is None:
            start = index
        if start is not None and (not line.strip() or index == len(lines)):
            end = index if line.strip() and index == len(lines) else index - 1
            text = "\n".join(lines[start - 1:end]).strip()
            if text:
                paragraphs.append(SourceParagraphSnapshot(
                    paragraph_id=f"source:p{len(paragraphs) + 1}",
                    line_start=start,
                    line_end=end,
                    text=text,
                ))
            start = None
    return paragraphs


def parse_prompt_sections(prompt_text: str) -> list[PromptSectionSnapshot]:
    matches = list(_SECTION_RE.finditer(prompt_text or ""))
    if not matches and _text(prompt_text):
        return [PromptSectionSnapshot(label="全文", text=prompt_text, end_offset=len(prompt_text))]
    sections: list[PromptSectionSnapshot] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(prompt_text)
        text = prompt_text[start:end].strip(" ：:，,\n")
        sections.append(PromptSectionSnapshot(
            label=match.group("label").strip(),
            text=text,
            start_offset=match.start(),
            end_offset=end,
        ))
    return sections


def parse_prompt_timeline(prompt_text: str) -> list[PromptTimelineSegment]:
    segments: list[PromptTimelineSegment] = []
    for match in _TIMELINE_RANGE_RE.finditer(prompt_text or ""):
        start = float(match.group("start"))
        end = float(match.group("end"))
        if end <= start:
            continue
        segments.append(PromptTimelineSegment(
            start_sec=start,
            end_sec=end,
            text=_text(match.group("text")).strip("。；;，, "),
            explicit=True,
        ))
    return segments


def parse_prompt_timeline_errors(prompt_text: str) -> list[str]:
    """Keep invalid explicit ranges addressable instead of silently dropping them."""
    errors: list[str] = []
    for match in _TIMELINE_RANGE_RE.finditer(prompt_text or ""):
        start = float(match.group("start"))
        end = float(match.group("end"))
        if end > start:
            continue
        text = _text(match.group("text")).strip("。；;，, ")
        errors.append(f"{start:g}-{end:g}s {text}".strip())
    return errors


def _source_snapshot(source_text: str) -> SourceScriptSnapshot:
    raw = str(source_text or "")
    if not raw.strip():
        return SourceScriptSnapshot(
            status="not_applicable",
            unavailable_reason="没有可审计的原始剧本文本；不生成原文对齐结论。",
        )
    return SourceScriptSnapshot(
        status="available",
        text_sha256=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        paragraphs=split_source_paragraphs(raw),
    )


def _source_reference_candidates(scene, paragraphs: list[SourceParagraphSnapshot]) -> list[SourceParagraphSnapshot]:
    needles = [
        _text(scene.location),
        _text(scene.action_description),
        *[_text(line.line) for line in scene.dialogue],
    ]
    needles = [item for item in needles if len(item) >= 3]
    if not needles:
        return []
    return [
        paragraph
        for paragraph in paragraphs
        if any(needle in paragraph.text for needle in needles)
    ]


def _source_references(
    scene,
    paragraphs: list[SourceParagraphSnapshot],
    paragraph_scene_ids: dict[tuple[int, int], set[int]] | None = None,
) -> list[PromptReviewEvidence]:
    candidates = _source_reference_candidates(scene, paragraphs)
    # A scene-level source citation is useful only when exactly one paragraph
    # matches and no other scene claims that same paragraph.
    if len(candidates) != 1:
        return []
    paragraph = candidates[0]
    if paragraph_scene_ids is not None and paragraph_scene_ids.get(
        (paragraph.line_start, paragraph.line_end), set()
    ) != {scene.scene_id}:
        return []
    return [PromptReviewEvidence(
            source="source_script",
            scene_id=scene.scene_id,
            fact_id=f"scene:{scene.scene_id}:source",
            excerpt=paragraph.text[:4000],
            line_start=paragraph.line_start,
            line_end=paragraph.line_end,
        )]


def _binding_snapshot(script: ScriptBreakdown, scene_id: int) -> AssetBindingSnapshot:
    binding = binding_for_scene(script, scene_id)
    if binding is None:
        return AssetBindingSnapshot()
    return AssetBindingSnapshot(
        location_id=binding.location_id,
        state_id=binding.state_id,
        on_screen_character_ids=list(binding.on_screen_character_ids),
        voice_only_speakers=list(binding.voice_only_speakers),
        prop_ids=list(binding.prop_ids),
        visible_state_notes=binding.visible_state_notes,
        status="available",
    )


def _rule_fact(fact_id: str, label: str, value: str, scene_id: int = 0) -> AuditFact:
    return AuditFact(
        fact_id=fact_id,
        label=label,
        value=value,
        provenance="derived",
        status="derived",
        scene_id=scene_id,
    )


def build_rule_facts(script: ScriptBreakdown, prompts: GenerationPrompts) -> list[AuditFact]:
    """Persist structural predicates so deterministic findings quote the snapshot."""
    script_ids = [scene.scene_id for scene in script.scenes]
    prompt_by_scene: dict[int, list[Any]] = {}
    for prompt in prompts.shots:
        prompt_by_scene.setdefault(prompt.scene_id, []).append(prompt)

    manifest = script.asset_manifest
    asset_ids = {
        "character": sorted(item.asset_id for item in manifest.characters) if manifest else [],
        "location": sorted(item.location_id for item in manifest.locations) if manifest else [],
        "prop": sorted(item.prop_id for item in manifest.props) if manifest else [],
    }
    facts = [
        _rule_fact("project:script:scene_ids", "剧本镜头 ID", ",".join(map(str, script_ids))),
        _rule_fact("project:prompt:scene_ids", "提示词镜头 ID", ",".join(map(str, prompt_by_scene))),
        _rule_fact(
            "project:asset_manifest:status",
            "资产清单状态",
            "available" if manifest else "not_available",
        ),
        _rule_fact(
            "project:asset_manifest:ids",
            "资产清单 ID",
            json.dumps(asset_ids, ensure_ascii=False, sort_keys=True),
        ),
    ]
    for scene_id in sorted(set(script_ids) | set(prompt_by_scene)):
        entries = prompt_by_scene.get(scene_id, [])
        presence = "missing" if scene_id not in prompt_by_scene else "extra" if scene_id not in script_ids else "present"
        content = "missing" if not entries else "empty" if any(not _text(item.prompt) for item in entries) else "present"
        timeline_status = "missing"
        if entries:
            errors = [error for item in entries for error in parse_prompt_timeline_errors(_text(item.prompt))]
            timeline_status = "invalid_range" if errors else "present" if any(parse_prompt_timeline(_text(item.prompt)) for item in entries) else "not_present"
        facts.extend([
            _rule_fact(f"scene:{scene_id}:prompt:presence", "提示词存在状态", presence, scene_id),
            _rule_fact(f"scene:{scene_id}:prompt:count", "提示词数量", str(len(entries)), scene_id),
            _rule_fact(f"scene:{scene_id}:prompt:content", "提示词内容状态", content, scene_id),
            _rule_fact(f"scene:{scene_id}:prompt:timeline", "提示词时间轴状态", timeline_status, scene_id),
        ])
    return facts


def build_scene_fact_sheets(
    script: ScriptBreakdown,
    prompts: GenerationPrompts,
    visual_design: VisualDesign | None,
    source_snapshot: SourceScriptSnapshot,
) -> list[SceneFactSheet]:
    prompts_by_scene = {item.scene_id: item for item in prompts.shots}
    visuals_by_scene = {
        item.scene_id: item for item in (visual_design.scenes if visual_design else [])
    }
    source_candidates = {
        scene.scene_id: _source_reference_candidates(scene, source_snapshot.paragraphs)
        for scene in script.scenes
    }
    paragraph_scene_ids: dict[tuple[int, int], set[int]] = {}
    for scene_id, candidates in source_candidates.items():
        for paragraph in candidates:
            paragraph_scene_ids.setdefault(
                (paragraph.line_start, paragraph.line_end), set()
            ).add(scene_id)
    sheets: list[SceneFactSheet] = []
    for scene in script.scenes:
        prompt = prompts_by_scene.get(scene.scene_id)
        visual = visuals_by_scene.get(scene.scene_id)
        prompt_text = _text(prompt.prompt) if prompt else ""
        dialogue = [
            _fact(scene.scene_id, f"dialogue:{index}", "对白", {
                "character": line.character,
                "line": line.line,
                "emotion": line.emotion,
            })
            for index, line in enumerate(scene.dialogue, start=1)
        ]
        visual_facts = []
        if visual:
            visual_facts.extend([
                _fact(scene.scene_id, "visual:composition", "构图", visual.composition, "visual_design"),
                _fact(scene.scene_id, "visual:lighting", "灯光", visual.lighting, "visual_design"),
                _fact(scene.scene_id, "visual:color", "色调", visual.color_tone, "visual_design"),
                _fact(scene.scene_id, "visual:key_elements", "关键视觉元素", visual.key_elements, "visual_design"),
            ])
        sheets.append(SceneFactSheet(
            fact_sheet_id=f"scene:{scene.scene_id}",
            scene_id=scene.scene_id,
            location=_fact(scene.scene_id, "location", "地点", scene.location),
            characters=[_fact(scene.scene_id, f"character:{index}", "角色", name) for index, name in enumerate(scene.characters, start=1)],
            dialogue=dialogue,
            action=_fact(scene.scene_id, "action", "动作", scene.action_description),
            camera=_fact(scene.scene_id, "camera", "镜头", scene.camera_direction),
            emotion=_fact(scene.scene_id, "emotion", "情绪", scene.emotion_tone),
            duration_sec=scene.duration_sec,
            transition_from_prev=scene.transition_from_prev,
            transition_duration_sec=scene.transition_duration_sec,
            continuity_from_prev=scene.continuity_from_prev,
            continuity_reason=scene.continuity_reason,
            asset_binding=_binding_snapshot(script, scene.scene_id),
            visual_facts=visual_facts,
            prompt=PromptSnapshot(
                prompt_text=prompt_text,
                negative_prompt=_text(prompt.negative_prompt) if prompt else "",
                duration_sec=float(prompt.duration_sec) if prompt else None,
                sections=parse_prompt_sections(prompt_text),
                timeline_segments=parse_prompt_timeline(prompt_text),
                timeline_errors=parse_prompt_timeline_errors(prompt_text),
            ),
            source_references=_source_references(
                scene, source_snapshot.paragraphs, paragraph_scene_ids,
            ),
        ))
    return sheets


def build_audit_snapshot(
    script: ScriptBreakdown,
    prompts: GenerationPrompts,
    visual_design: VisualDesign | None,
    prompt_policy: PromptPolicy,
    source_text: str = "",
    scene_vision_descriptions: dict[str, Any] | None = None,
) -> PromptReviewAuditSnapshot:
    source_snapshot = _source_snapshot(source_text)
    return PromptReviewAuditSnapshot(
        source_script=source_snapshot,
        structured_script=script.model_dump(),
        asset_manifest=script.asset_manifest.model_dump() if script.asset_manifest else None,
        visual_design=visual_design.model_dump() if visual_design else None,
        scene_vision_descriptions=scene_vision_descriptions or {},
        generation_prompts=prompts.model_dump(),
        prompt_policy=prompt_policy.model_dump(),
        scene_facts=build_scene_fact_sheets(script, prompts, visual_design, source_snapshot),
        rule_facts=build_rule_facts(script, prompts),
    )


def audit_snapshot_signature(snapshot: PromptReviewAuditSnapshot) -> str:
    """Hash immutable audit inputs; stage results are deliberately excluded."""
    raw = json.dumps(
        snapshot.model_dump(exclude={"stage_records"}),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


def snapshot_search_text(snapshot: PromptReviewAuditSnapshot) -> str:
    """Flatten quoted snapshot values for deterministic evidence membership checks."""
    chunks: list[str] = []
    def flatten(value: Any) -> list[str]:
        if isinstance(value, dict):
            result: list[str] = []
            for nested in value.values():
                result.extend(flatten(nested))
            return result
        if isinstance(value, (list, tuple)):
            result = []
            for nested in value:
                result.extend(flatten(nested))
            return result
        if value is None or value is False:
            return []
        return [str(value)]

    chunks.extend(item.text for item in snapshot.source_script.paragraphs)
    chunks.extend(flatten(snapshot.structured_script))
    chunks.extend(flatten(snapshot.asset_manifest or {}))
    chunks.extend(flatten(snapshot.visual_design or {}))
    chunks.extend(flatten(snapshot.scene_vision_descriptions))
    chunks.extend(flatten(snapshot.generation_prompts))
    chunks.extend(flatten(snapshot.prompt_policy))
    chunks.extend(flatten([item.model_dump() for item in snapshot.rule_facts]))
    for sheet in snapshot.scene_facts:
        # Keep the complete fact-sheet serialization searchable for providers
        # that cite the scene-level fact ID instead of a leaf field ID.
        chunks.append(json.dumps(sheet.model_dump(), ensure_ascii=False, sort_keys=True))
        chunks.extend([
            _text(sheet.location.value),
            _text(sheet.action.value),
            _text(sheet.camera.value),
            _text(sheet.emotion.value),
            str(sheet.duration_sec),
            str(sheet.transition_from_prev),
            _text(sheet.continuity_reason),
            sheet.prompt.prompt_text,
            sheet.prompt.negative_prompt,
        ])
        chunks.extend(flatten(sheet.asset_binding.model_dump()))
        chunks.extend(flatten([item.model_dump() for item in sheet.visual_facts]))
        chunks.extend(_text(item.value) for item in sheet.characters)
        chunks.extend(_text(item.value) for item in sheet.dialogue)
        chunks.extend(item.text for item in sheet.prompt.sections)
        chunks.extend(item.text for item in sheet.prompt.timeline_segments)
        chunks.extend(sheet.prompt.timeline_errors)
        chunks.extend(
            f"{item.start_sec:g}-{item.end_sec:g}s {item.text}".strip()
            for item in sheet.prompt.timeline_segments
        )
        chunks.extend(item.excerpt for item in sheet.source_references)
    return "\n".join(item for item in chunks if item)


def referenced_asset_tokens(text: str) -> list[tuple[str, str, str]]:
    return [
        (match.group("kind"), match.group("asset"), match.group("view") or "")
        for match in _REFERENCE_RE.finditer(text or "")
    ]


__all__ = [
    "build_audit_snapshot",
    "audit_snapshot_signature",
    "build_scene_fact_sheets",
    "build_rule_facts",
    "parse_prompt_sections",
    "parse_prompt_timeline",
    "parse_prompt_timeline_errors",
    "referenced_asset_tokens",
    "snapshot_search_text",
    "split_source_paragraphs",
]
