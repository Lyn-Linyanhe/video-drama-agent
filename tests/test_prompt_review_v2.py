import pytest

from src.agents.prompt_review_agent import PromptReviewAgent
from src.schemas.prompt import GenerationPrompts, PromptPolicy, ShotPrompt
from src.schemas.prompt_review import (
    PromptReviewEvidence,
    PromptReviewIssue,
    PromptReviewResult,
)
from src.schemas.script import (
    LocationAsset,
    PropAsset,
    Scene,
    ScriptBreakdown,
    ShotVisualBinding,
    VisualAssetManifest,
)
from src.schemas.visual import SceneVisual, VisualDesign
from src.services.prompt_review_facts import snapshot_search_text


def _script() -> ScriptBreakdown:
    return ScriptBreakdown(
        title="雨巷来信",
        genre="悬疑",
        logline="一封湿信把苏眠带回雨夜巷口。",
        duration_sec=13,
        narration_style="无旁白",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=5,
                location="雨夜巷口",
                characters=["苏眠"],
                action_description="苏眠从积水里捡起湿信封，撕开封口。",
                dialogue=[],
                camera_direction="中近景缓慢推进",
                emotion_tone="警惕",
            ),
            Scene(
                scene_id=2,
                duration_sec=8,
                location="便利店门口",
                characters=["苏眠", "店员"],
                action_description="苏眠把信纸递给店员，店员读到落款后后退半步。",
                camera_direction="双人中景后切店员近景",
                emotion_tone="不安升级",
                continuity_from_prev=True,
                continuity_reason="苏眠拿着同一封信追入便利店门口。",
            ),
        ],
        asset_manifest=VisualAssetManifest(
            characters=[],
            locations=[
                LocationAsset(
                    location_id="alley_rain",
                    canonical_name="雨夜巷口",
                    representative_scene_id=1,
                    shot_ids=[1],
                    environment_description="积水反射冷白路灯。",
                ),
                LocationAsset(
                    location_id="store_door",
                    canonical_name="便利店门口",
                    representative_scene_id=2,
                    shot_ids=[2],
                    environment_description="玻璃门内透出暖黄灯光。",
                ),
            ],
            props=[
                PropAsset(
                    prop_id="wet_letter",
                    name="湿信封",
                    description="边缘被雨水浸透的白色信封。",
                    shot_ids=[1, 2],
                    is_handheld=True,
                ),
            ],
            shot_bindings=[
                ShotVisualBinding(scene_id=1, location_id="alley_rain", prop_ids=["wet_letter"]),
                ShotVisualBinding(scene_id=2, location_id="store_door", prop_ids=["wet_letter"]),
            ],
        ),
    )


def _prompts() -> GenerationPrompts:
    return GenerationPrompts(
        api_provider="mock",
        shots=[
            ShotPrompt(
                scene_id=1,
                duration_sec=5,
                prompt=(
                    "【核心分镜意图】苏眠从积水里捡起湿信封，撕开封口。\n"
                    "【环境】雨夜巷口，积水反射冷白路灯。\n"
                    "【单镜头时间轴】0-2s 苏眠弯腰捡起信封；2-5s 她撕开封口。"
                ),
            ),
            ShotPrompt(
                scene_id=2,
                duration_sec=8,
                prompt=(
                    "【核心分镜意图】苏眠把信纸递给店员，店员读到落款后后退半步。\n"
                    "【镜头】双人中景后切店员近景。\n"
                    "【单镜头时间轴】0-4s 苏眠递出信纸；4-8s 店员读到落款后后退。"
                ),
            ),
        ],
    )


def _visual() -> VisualDesign:
    return VisualDesign(
        overall_style="写实悬疑",
        color_palette="冷蓝与暖黄对比",
        aspect_ratio="9:16",
        scenes=[
            SceneVisual(
                scene_id=1,
                composition="中近景",
                lighting="冷白路灯",
                color_tone="冷蓝",
                key_elements=["积水", "湿信封"],
            ),
            SceneVisual(
                scene_id=2,
                composition="双人中景",
                lighting="店内暖黄灯光",
                color_tone="冷暖对比",
                key_elements=["玻璃门", "信纸"],
            ),
        ],
    )


def test_fact_snapshot_preserves_source_structure_assets_and_prompt_sections():
    source_text = (
        "外景，雨夜巷口。苏眠从积水里捡起一封湿信，撕开封口。\n\n"
        "便利店门口。苏眠把信纸递给店员；店员读到落款后后退半步。"
    )

    snapshot = PromptReviewAgent.build_audit_snapshot(
        _script(), _prompts(), _visual(), PromptPolicy(), source_text=source_text,
    )

    assert snapshot.source_script.status == "available"
    assert snapshot.source_script.paragraphs[0].paragraph_id == "source:p1"
    assert snapshot.source_script.paragraphs[0].line_start == 1
    assert snapshot.source_script.paragraphs[1].line_start == 3
    assert len(snapshot.scene_facts) == 2

    first = snapshot.scene_facts[0]
    assert first.location.fact_id == "scene:1:location"
    assert first.location.value == "雨夜巷口"
    assert first.asset_binding.location_id == "alley_rain"
    assert first.asset_binding.prop_ids == ["wet_letter"]
    assert first.prompt.sections[0].label == "核心分镜意图"
    assert [(item.start_sec, item.end_sec) for item in first.prompt.timeline_segments] == [
        (0.0, 2.0),
        (2.0, 5.0),
    ]
    assert [(item.line_start, item.line_end) for item in first.source_references] == [(1, 1)]


def test_fact_snapshot_marks_source_parse_not_applicable_without_original_script():
    snapshot = PromptReviewAgent.build_audit_snapshot(
        _script(), _prompts(), _visual(), PromptPolicy(), source_text="",
    )

    assert snapshot.source_script.status == "not_applicable"
    assert snapshot.source_script.paragraphs == []


def test_snapshot_search_text_includes_complete_scene_fact_sheet_values():
    snapshot = PromptReviewAgent.build_audit_snapshot(
        _script(), _prompts(), _visual(), PromptPolicy(), source_text="",
    )

    serialized_fact_sheet = __import__("json").dumps(
        snapshot.scene_facts[0].model_dump(), ensure_ascii=False, sort_keys=True,
    )

    assert serialized_fact_sheet in snapshot_search_text(snapshot)


def test_audit_signature_ignores_stage_records_but_changes_with_audit_inputs():
    from src.schemas.prompt_review import PromptReviewStageRecord

    agent = PromptReviewAgent()
    snapshot = agent.build_audit_snapshot(_script(), _prompts(), _visual(), PromptPolicy(), source_text="原文")
    first = agent.audit_signature(snapshot)
    snapshot.stage_records.append(PromptReviewStageRecord(stage="single_shot", status="completed"))
    assert agent.audit_signature(snapshot) == first
    snapshot.generation_prompts["prompt_template_version"] = 999
    assert agent.audit_signature(snapshot) != first


def test_evidence_validation_rejects_structured_fact_from_another_shot():
    snapshot = PromptReviewAgent.build_audit_snapshot(_script(), _prompts(), _visual(), PromptPolicy(), source_text="")
    issue = PromptReviewIssue(
        scene_id=1, severity="major", category="coverage", description="缺少动作。", suggestion="补充动作。",
        layer="prompt", scope="single_shot", issue_code="prompt.coverage_missing",
        evidence_items=[
            PromptReviewEvidence(source="structured_script", scene_id=2, fact_id="scene:2:action", excerpt="苏眠把信纸递给店员，店员读到落款后后退半步。"),
            PromptReviewEvidence(source="prompt", scene_id=1, excerpt="苏眠从积水里捡起湿信封，撕开封口。"),
        ], checked_prompt_sections=["核心分镜意图"],
    )
    import pytest
    with pytest.raises(ValueError, match="证据镜头"):
        PromptReviewAgent._validate_issue_evidence(issue, snapshot, "single_shot")


def test_evidence_validation_rejects_wrong_excerpt_for_target_structured_fact():
    snapshot = PromptReviewAgent.build_audit_snapshot(_script(), _prompts(), _visual(), PromptPolicy(), source_text="")
    issue = PromptReviewIssue(
        scene_id=1, severity="major", category="coverage", description="缺少动作。", suggestion="补充动作。",
        layer="prompt", scope="single_shot", issue_code="prompt.coverage_missing",
        evidence_items=[
            PromptReviewEvidence(
                source="structured_script", scene_id=1, fact_id="scene:1:action",
                excerpt="苏眠把信纸递给店员，店员读到落款后后退半步。",
            ),
            PromptReviewEvidence(source="prompt", scene_id=1, excerpt="苏眠从积水里捡起湿信封，撕开封口。"),
        ], checked_prompt_sections=["核心分镜意图"],
    )

    import pytest
    with pytest.raises(ValueError, match="结构化事实片段"):
        PromptReviewAgent._validate_issue_evidence(issue, snapshot, "single_shot")


def test_rereview_requires_a_decision_for_every_candidate():
    candidate = PromptReviewIssue(
        scene_id=1, severity="minor", category="clarity", description="表述不清。", suggestion="明确主语。",
        issue_code="prompt.clarity", candidate_id="candidate-1",
    )
    import pytest
    with pytest.raises(ValueError, match="覆盖全部候选"):
        PromptReviewAgent._apply_rereview_decisions([candidate], {"decisions": []})


def test_rereview_normalizes_kimi_confidence_aliases():
    candidate = PromptReviewIssue(
        scene_id=1, severity="minor", category="clarity", description="表述不清。", suggestion="明确主语。",
        issue_code="prompt.clarity", candidate_id="candidate-1",
    )

    reviewed = PromptReviewAgent._apply_rereview_decisions(
        [candidate], {"decisions": [{"candidate_id": "candidate-1", "status": "certain"}]},
    )

    assert reviewed[0].confidence == "confirmed"


def test_rereview_accepts_kimi_wrapped_output_object():
    candidate = PromptReviewIssue(
        scene_id=1, severity="minor", category="clarity", description="表述不清。", suggestion="明确主语。",
        issue_code="prompt.clarity", candidate_id="candidate-1",
    )

    reviewed = PromptReviewAgent._apply_rereview_decisions(
        [candidate], {
            "stage": "candidate_rereview",
            "output": {"decisions": [{"candidate_id": "candidate-1", "status": "confirmed"}]},
        },
    )

    assert reviewed[0].confidence == "confirmed"


def test_rereview_rejects_duplicate_candidate_ids_before_indexing():
    candidates = [
        PromptReviewIssue(
            scene_id=1, severity="minor", category="clarity", description="镜头一问题。", suggestion="修复。",
            issue_code="prompt.clarity.one", candidate_id="duplicate-id",
        ),
        PromptReviewIssue(
            scene_id=2, severity="minor", category="clarity", description="镜头二问题。", suggestion="修复。",
            issue_code="prompt.clarity.two", candidate_id="duplicate-id",
        ),
    ]

    with pytest.raises(ValueError, match="候选 ID 重复"):
        PromptReviewAgent._apply_rereview_decisions(
            candidates,
            {"decisions": [{"candidate_id": "duplicate-id", "status": "confirmed"}]},
        )


def test_stage_parser_rejects_a_primary_scene_outside_the_stage_scope():
    snapshot = PromptReviewAgent.build_audit_snapshot(_script(), _prompts(), _visual(), PromptPolicy(), source_text="")
    with pytest.raises(ValueError, match="问题镜头超出当前阶段目标范围"):
        PromptReviewAgent._parse_stage_issues(
            {"issues": [{
                "scene_id": 999, "related_scene_ids": [1, 2], "severity": "minor",
                "category": "continuity", "issue_code": "prompt.bad_scope",
                "description": "错误归因。", "suggestion": "修正归因。", "fact_ids": ["scene:1:action"],
                "evidence_items": [
                    {"source": "structured_script", "scene_id": 1, "fact_id": "scene:1:action", "excerpt": "苏眠从积水里捡起湿信封，撕开封口。"},
                    {"source": "prompt", "scene_id": 1, "excerpt": "苏眠从积水里捡起湿信封，撕开封口。"},
                ],
            }]}, snapshot, {1, 2}, "prompt", "cross_shot", "cross_shot",
        )


def test_stage_parser_rejects_provider_layer_or_scope_that_conflicts_with_stage():
    snapshot = PromptReviewAgent.build_audit_snapshot(_script(), _prompts(), _visual(), PromptPolicy(), source_text="")
    raw = {
        "scene_id": 1, "related_scene_ids": [], "severity": "minor", "category": "clarity",
        "layer": "source_parse", "scope": "source_alignment", "issue_code": "prompt.bad_stage",
        "description": "阶段标记错误。", "suggestion": "修正阶段。", "fact_ids": ["scene:1:action"],
        "evidence_items": [
            {"source": "structured_script", "scene_id": 1, "fact_id": "scene:1:action", "excerpt": "苏眠从积水里捡起湿信封，撕开封口。"},
            {"source": "prompt", "scene_id": 1, "excerpt": "苏眠从积水里捡起湿信封，撕开封口。"},
        ],
    }

    with pytest.raises(ValueError, match="责任层与当前阶段不匹配"):
        PromptReviewAgent._parse_stage_issues(
            {"issues": [raw]}, snapshot, {1}, "prompt", "single_shot", "single_shot",
        )


def test_stage_parser_normalizes_kimi_category_alias_and_allows_adjacent_context():
    snapshot = PromptReviewAgent.build_audit_snapshot(_script(), _prompts(), _visual(), PromptPolicy(), source_text="")
    issue = {
        "scene_id": 1,
        "related_scene_ids": [2],
        "severity": "major",
        "category": "attribution",
        "issue_code": "wrong_attribution",
        "description": "当前镜头的动作归属不清。",
        "suggestion": "明确当前镜头的动作主体。",
        "fact_ids": ["scene:1:action"],
        "evidence_items": [
            {
                "source": "structured_script",
                "scene_id": 1,
                "fact_id": "scene:1:action",
                "excerpt": "苏眠从积水里捡起湿信封，撕开封口。",
            },
            {
                "source": "prompt",
                "scene_id": 1,
                "section": "核心分镜意图",
                "excerpt": "苏眠从积水里捡起湿信封，撕开封口。",
            },
        ],
        "checked_prompt_sections": ["核心分镜意图"],
    }

    parsed = PromptReviewAgent._parse_stage_issues(
        {"issues": [issue]}, snapshot, {1}, "prompt", "single_shot", "single_shot",
    )

    assert len(parsed) == 1
    assert parsed[0].category == "fidelity"
    assert parsed[0].related_scene_ids == [2]


def test_stage_parser_normalizes_kimi_certain_confidence_to_confirmed():
    snapshot = PromptReviewAgent.build_audit_snapshot(_script(), _prompts(), _visual(), PromptPolicy(), source_text="")
    issue = {
        "scene_id": 1,
        "severity": "major",
        "category": "coverage",
        "confidence": "certain",
        "issue_code": "missing_action_element",
        "description": "动作没有完整覆盖。",
        "suggestion": "补充动作顺序。",
        "fact_ids": ["scene:1:action"],
        "evidence_items": [
            {
                "source": "structured_script",
                "scene_id": 1,
                "fact_id": "scene:1:action",
                "excerpt": "苏眠从积水里捡起湿信封，撕开封口。",
            },
            {
                "source": "prompt",
                "scene_id": 1,
                "section": "核心分镜意图",
                "excerpt": "苏眠从积水里捡起湿信封，撕开封口。",
            },
        ],
        "checked_prompt_sections": ["核心分镜意图"],
    }

    parsed = PromptReviewAgent._parse_stage_issues(
        {"issues": [issue]}, snapshot, {1}, "prompt", "single_shot", "single_shot",
    )

    assert parsed[0].confidence == "confirmed"


def test_stage_parser_ignores_legacy_evidence_list_when_structured_evidence_is_present():
    snapshot = PromptReviewAgent.build_audit_snapshot(_script(), _prompts(), _visual(), PromptPolicy(), source_text="")
    evidence_items = [
        {
            "source": "structured_script",
            "scene_id": 1,
            "fact_id": "scene:1:action",
            "excerpt": "苏眠从积水里捡起湿信封，撕开封口。",
        },
        {
            "source": "prompt",
            "scene_id": 1,
            "section": "核心分镜意图",
            "excerpt": "苏眠从积水里捡起湿信封，撕开封口。",
        },
    ]
    raw = {
        "scene_id": 1,
        "severity": "major",
        "category": "coverage",
        "issue_code": "missing_action_element",
        "description": "动作没有完整覆盖。",
        "suggestion": "补充动作。",
        "fact_ids": ["scene:1:action"],
        "evidence": list(evidence_items),
        "evidence_items": evidence_items,
        "checked_prompt_sections": ["核心分镜意图"],
    }

    parsed = PromptReviewAgent._parse_stage_issues(
        {"issues": [raw]}, snapshot, {1}, "prompt", "single_shot", "single_shot",
    )

    assert len(parsed[0].evidence_items) == 2
    assert parsed[0].evidence == ""


def test_single_shot_issue_must_bind_to_the_current_target_scene():
    snapshot = PromptReviewAgent.build_audit_snapshot(_script(), _prompts(), _visual(), PromptPolicy(), source_text="")
    raw = {
        "scene_id": 0,
        "related_scene_ids": [2],
        "severity": "major",
        "category": "coverage",
        "issue_code": "context_only",
        "description": "目标镜头问题。",
        "suggestion": "修复目标镜头。",
        "fact_ids": ["scene:2:action"],
        "evidence_items": [
            {
                "source": "structured_script",
                "scene_id": 2,
                "fact_id": "scene:2:action",
                "excerpt": "苏眠把信纸递给店员，店员读到落款后后退半步。",
            },
            {
                "source": "prompt",
                "scene_id": 2,
                "section": "核心分镜意图",
                "excerpt": "苏眠把信纸递给店员，店员读到落款后后退半步。",
            },
        ],
        "checked_prompt_sections": ["核心分镜意图"],
    }

    with pytest.raises(ValueError, match="单镜问题必须绑定当前目标镜头"):
        PromptReviewAgent._parse_stage_issues(
            {"issues": [raw]}, snapshot, {1}, "prompt", "single_shot", "single_shot",
        )


def test_single_shot_evidence_must_bind_to_the_primary_scene():
    snapshot = PromptReviewAgent.build_audit_snapshot(_script(), _prompts(), _visual(), PromptPolicy(), source_text="")
    raw = {
        "scene_id": 1,
        "related_scene_ids": [2],
        "severity": "major",
        "category": "coverage",
        "issue_code": "context_evidence_only",
        "description": "目标镜头问题。",
        "suggestion": "修复目标镜头。",
        "fact_ids": ["scene:2:action"],
        "evidence_items": [
            {
                "source": "structured_script",
                "scene_id": 2,
                "fact_id": "scene:2:action",
                "excerpt": "苏眠把信纸递给店员，店员读到落款后后退半步。",
            },
            {
                "source": "prompt",
                "scene_id": 2,
                "section": "核心分镜意图",
                "excerpt": "苏眠把信纸递给店员，店员读到落款后后退半步。",
            },
        ],
        "checked_prompt_sections": ["核心分镜意图"],
    }

    with pytest.raises(ValueError, match="单镜证据必须绑定当前目标镜头"):
        PromptReviewAgent._parse_stage_issues(
            {"issues": [raw]}, snapshot, {1}, "prompt", "single_shot", "single_shot",
        )


def test_structured_scene_fact_id_cannot_quote_prompt_content():
    snapshot = PromptReviewAgent.build_audit_snapshot(_script(), _prompts(), _visual(), PromptPolicy(), source_text="")
    issue = PromptReviewIssue(
        scene_id=1,
        severity="major",
        category="coverage",
        description="伪造的结构化事实。",
        suggestion="重新引用剧本事实。",
        issue_code="prompt.fake_structured_fact",
        fact_ids=["scene:1"],
        evidence_items=[
            PromptReviewEvidence(
                source="structured_script",
                scene_id=1,
                fact_id="scene:1",
                excerpt="0-2s 苏眠弯腰捡起信封；2-5s 她撕开封口。",
            ),
            PromptReviewEvidence(
                source="prompt",
                scene_id=1,
                section="单镜头时间轴",
                excerpt="0-2s 苏眠弯腰捡起信封；2-5s 她撕开封口。",
            ),
        ],
    )

    with pytest.raises(ValueError, match="必须使用具体结构化事实 ID"):
        PromptReviewAgent._validate_issue_evidence(issue, snapshot, "single_shot", {1})


def test_issue_fact_ids_must_be_backed_by_evidence_fact_ids():
    snapshot = PromptReviewAgent.build_audit_snapshot(_script(), _prompts(), _visual(), PromptPolicy(), source_text="")
    issue = PromptReviewIssue(
        scene_id=1,
        severity="major",
        category="coverage",
        description="事实 ID 声明错误。",
        suggestion="修正事实 ID。",
        issue_code="prompt.bad_fact_ids",
        fact_ids=["scene:1:action", "scene:1:fake"],
        evidence_items=[
            PromptReviewEvidence(
                source="structured_script",
                scene_id=1,
                fact_id="scene:1:action",
                excerpt="苏眠从积水里捡起湿信封，撕开封口。",
            ),
            PromptReviewEvidence(
                source="prompt",
                scene_id=1,
                section="核心分镜意图",
                excerpt="苏眠从积水里捡起湿信封，撕开封口。",
            ),
        ],
    )

    with pytest.raises(ValueError, match="问题事实 ID 未被证据引用"):
        PromptReviewAgent._validate_issue_evidence(issue, snapshot, "single_shot", {1})


def test_structured_script_evidence_requires_its_fact_id():
    snapshot = PromptReviewAgent.build_audit_snapshot(_script(), _prompts(), _visual(), PromptPolicy(), source_text="")
    raw = {
        "scene_id": 1,
        "severity": "major",
        "category": "coverage",
        "issue_code": "missing_action_element",
        "description": "动作没有完整覆盖。",
        "suggestion": "补充动作。",
        "fact_ids": ["scene:1:action"],
        "evidence_items": [
            {
                "source": "structured_script",
                "scene_id": 1,
                "excerpt": "苏眠从积水里捡起湿信封，撕开封口。",
            },
            {
                "source": "prompt",
                "scene_id": 1,
                "section": "核心分镜意图",
                "excerpt": "苏眠从积水里捡起湿信封，撕开封口。",
            },
        ],
        "checked_prompt_sections": ["核心分镜意图"],
    }

    with pytest.raises(ValueError, match="结构化事实证据缺少事实 ID"):
        PromptReviewAgent._parse_stage_issues(
            {"issues": [raw]}, snapshot, {1}, "prompt", "single_shot", "single_shot",
        )


def test_stage_parser_keeps_valid_candidates_when_another_candidate_is_invalid():
    snapshot = PromptReviewAgent.build_audit_snapshot(_script(), _prompts(), _visual(), PromptPolicy(), source_text="")
    valid = {
        "scene_id": 1,
        "severity": "major",
        "category": "coverage",
        "issue_code": "missing_action",
        "description": "动作没有完整覆盖。",
        "suggestion": "补充动作顺序。",
        "fact_ids": ["scene:1:action"],
        "evidence_items": [
            {
                "source": "structured_script",
                "scene_id": 1,
                "fact_id": "scene:1:action",
                "excerpt": "苏眠从积水里捡起湿信封，撕开封口。",
            },
            {
                "source": "prompt",
                "scene_id": 1,
                "section": "核心分镜意图",
                "excerpt": "苏眠从积水里捡起湿信封，撕开封口。",
            },
        ],
        "checked_prompt_sections": ["核心分镜意图"],
    }
    invalid = {**valid, "category": "provider_only_category", "issue_code": "invalid.category"}

    parsed = PromptReviewAgent._parse_stage_issues(
        {"issues": [invalid, valid]}, snapshot, {1}, "prompt", "single_shot", "single_shot",
    )

    assert len(parsed) == 1
    assert parsed[0].issue_code == "missing_action"


def test_evidence_validation_accepts_provider_whitespace_reflow_without_widening_fact_scope():
    snapshot = PromptReviewAgent.build_audit_snapshot(_script(), _prompts(), _visual(), PromptPolicy(), source_text="")
    issue = PromptReviewIssue(
        scene_id=1,
        severity="major",
        category="coverage",
        description="提示词遗漏动作。",
        suggestion="补充动作。",
        layer="prompt",
        scope="single_shot",
        issue_code="prompt.coverage_missing",
        fact_ids=["scene:1:action"],
        evidence_items=[
            PromptReviewEvidence(
                source="structured_script",
                scene_id=1,
                fact_id="scene:1:action",
                excerpt="苏眠从积水里捡起湿信封，撕开封 口。",
            ),
            PromptReviewEvidence(
                source="prompt",
                scene_id=1,
                section="核心分镜意图",
                excerpt="苏眠从积水里捡起湿信封，撕开封 口。",
            ),
        ],
        checked_prompt_sections=["核心分镜意图"],
    )

    PromptReviewAgent._validate_issue_evidence(issue, snapshot, "single_shot")


def test_unreviewed_issue_exposes_the_underlying_validation_error():
    snapshot = PromptReviewAgent.build_audit_snapshot(_script(), _prompts(), _visual(), PromptPolicy(), source_text="")

    issue = PromptReviewAgent._unreviewed_issue(
        snapshot,
        1,
        "single_shot",
        "单镜审核输出无法通过格式或证据校验。",
        "证据片段不在审核快照中",
    )

    assert "证据片段不在审核快照中" in issue.description


def test_single_shot_context_is_indexed_but_does_not_penalize_the_context_shot():
    script = _script()
    snapshot = PromptReviewAgent.build_audit_snapshot(script, _prompts(), _visual(), PromptPolicy(), source_text="")
    issue = PromptReviewIssue(
        scene_id=1,
        related_scene_ids=[2],
        severity="major",
        category="fidelity",
        description="当前镜头与相邻上下文有关。",
        suggestion="检查当前镜头。",
        layer="prompt",
        scope="single_shot",
        issue_code="prompt.contextual_issue",
        confidence="confirmed",
    )

    report = PromptReviewAgent._finalize_report(script, snapshot, [issue], audit_signature="j" * 20)

    assert [shot.score for shot in report.shots] == [85, 100]


def test_model_issue_cannot_declare_a_deterministic_generation_block():
    issue = PromptReviewAgent._normalize_v2_issue({
        "scene_id": 1, "severity": "critical", "category": "fidelity", "issue_code": "prompt.fake_block",
        "description": "模型声称这是结构错误。", "suggestion": "人工检查。", "fact_ids": ["scene:1:action"],
        "deterministic": True, "blocks_generation": True,
    }, "prompt", "single_shot", "single_shot")

    assert issue.deterministic is False
    assert issue.blocks_generation is False


def test_cross_shot_duplicate_related_ids_do_not_double_penalize_a_shot():
    script = _script()
    snapshot = PromptReviewAgent.build_audit_snapshot(script, _prompts(), _visual(), PromptPolicy(), source_text="")
    issue = PromptReviewIssue(
        scene_id=0, related_scene_ids=[1, 1, 2], severity="major", category="continuity",
        description="前后状态不一致。", suggestion="统一承接状态。", scope="cross_shot",
        issue_code="prompt.cross_state", confidence="confirmed",
    )
    report = PromptReviewAgent._finalize_report(script, snapshot, [issue], audit_signature="f" * 20)

    assert [shot.score for shot in report.shots] == [85, 85]


def test_same_logical_issue_with_different_evidence_is_merged_once():
    script = _script()
    snapshot = PromptReviewAgent.build_audit_snapshot(script, _prompts(), _visual(), PromptPolicy(), source_text="")
    issues = [
        PromptReviewIssue(
            scene_id=1, severity="minor", category="clarity", description="动作主语不清。", suggestion="明确主语。",
            issue_code="prompt.clarity", fact_ids=["scene:1:action"],
            evidence_items=[PromptReviewEvidence(source="structured_script", scene_id=1, fact_id="scene:1:action", excerpt="苏眠从积水里捡起湿信封，撕开封口。")],
        ),
        PromptReviewIssue(
            scene_id=1, severity="minor", category="clarity", description="动作主语不清。", suggestion="明确主语。",
            issue_code="prompt.clarity", fact_ids=["scene:1:action"],
            evidence_items=[PromptReviewEvidence(source="prompt", scene_id=1, excerpt="【核心分镜意图】苏眠从积水里捡起湿信封，撕开封口。")],
        ),
    ]

    report = PromptReviewAgent._finalize_report(script, snapshot, issues, audit_signature="g" * 20)

    assert len(report.issues) == 1
    assert report.shots[0].score == 95
    assert len(report.issues[0].evidence_items) == 2


def test_duplicate_issue_with_different_severity_keeps_only_the_highest_penalty():
    script = _script()
    snapshot = PromptReviewAgent.build_audit_snapshot(script, _prompts(), _visual(), PromptPolicy(), source_text="")
    issues = [
        PromptReviewIssue(
            scene_id=1, severity="minor", category="clarity", description="动作主语不清。", suggestion="明确主语。",
            issue_code="prompt.clarity", fact_ids=["scene:1:action"], confidence="confirmed",
        ),
        PromptReviewIssue(
            scene_id=1, severity="major", category="clarity", description="动作主语不清。", suggestion="明确主语。",
            issue_code="prompt.clarity", fact_ids=["scene:1:action"], confidence="confirmed",
        ),
    ]

    report = PromptReviewAgent._finalize_report(script, snapshot, issues, audit_signature="h" * 20)

    assert len(report.issues) == 1
    assert report.issues[0].severity == "major"
    assert report.shots[0].score == 85


@pytest.mark.asyncio
async def test_provider_failure_in_a_stage_becomes_related_unreviewed_issue():
    from types import SimpleNamespace

    class FailingClient:
        class Chat:
            class Completions:
                async def create(self, **_kwargs):
                    raise RuntimeError("provider timeout")
            completions = Completions()
        chat = Chat()

    snapshot = PromptReviewAgent.build_audit_snapshot(_script(), _prompts(), _visual(), PromptPolicy(), source_text="")
    issues, status = await PromptReviewAgent()._stage_call(
        FailingClient(), "kimi-test", "cross_shot", {}, snapshot, {1, 2}, "prompt", "cross_shot",
    )

    assert status == "unreviewed"
    assert issues[0].confidence == "unreviewed"
    assert issues[0].related_scene_ids == [1, 2]


@pytest.mark.asyncio
async def test_json_mode_fallback_retries_when_provider_rejects_response_format():
    import json
    from types import SimpleNamespace
    calls = []

    class Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError("response_format json_object is unsupported")
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({"ok": True})))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    result = await PromptReviewAgent._request_json(client, "kimi-test", "return json")

    assert result == {"ok": True}
    assert len(calls) == 2
    assert "response_format" not in calls[1]


@pytest.mark.asyncio
async def test_temperature_one_fallback_retries_when_kimi_rejects_zero(monkeypatch):
    import json
    from types import SimpleNamespace

    calls = []
    monkeypatch.setenv("KIMI_PROMPT_REVIEW_THINKING", "disabled")

    class Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise RuntimeError("invalid temperature: only 1 is allowed for this model")
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps({"ok": True})),
            )])

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    result = await PromptReviewAgent._request_json(client, "kimi-k2.6", "return json")

    assert result == {"ok": True}
    assert [call["temperature"] for call in calls] == [0.6, 1]


@pytest.mark.asyncio
async def test_kimi_request_retries_rate_limit_with_configured_spacing(monkeypatch):
    import json
    from types import SimpleNamespace

    calls = []
    sleeps = []

    class Completions:
        async def create(self, **_kwargs):
            calls.append(True)
            if len(calls) == 1:
                raise RuntimeError("Error code: 429 - request reached max RPM; try again after 1 seconds")
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps({"ok": True})),
            )])

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setenv("KIMI_PROMPT_REVIEW_REQUEST_INTERVAL_SEC", "7")
    monkeypatch.setattr("src.agents.prompt_review_agent.asyncio.sleep", fake_sleep)
    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))

    result = await PromptReviewAgent._request_json(client, "kimi-k2.6", "return json")

    assert result == {"ok": True}
    assert len(calls) == 2
    assert any(delay >= 7 for delay in sleeps)
@pytest.mark.asyncio
async def test_kimi_review_defaults_to_enabled_thinking_with_bounded_output(monkeypatch):
    import json
    from types import SimpleNamespace

    calls = []

    class Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps({"ok": True})),
            )])

    monkeypatch.delenv("KIMI_PROMPT_REVIEW_THINKING", raising=False)
    monkeypatch.delenv("KIMI_PROMPT_REVIEW_MAX_TOKENS", raising=False)
    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    result = await PromptReviewAgent._request_json(client, "kimi-k2.6", "return json")

    assert result == {"ok": True}
    assert calls[0]["temperature"] == 1
    assert calls[0]["max_tokens"] == 16384
    assert calls[0]["extra_body"] == {"thinking": {"type": "enabled"}}


def test_semantically_rephrased_source_paragraphs_do_not_claim_a_false_shot_binding():
    source_text = "巷子里的女孩拆开一封湿信。\n\n店里她把纸递给店员，店员读完后退。"
    snapshot = PromptReviewAgent.build_audit_snapshot(
        _script(), _prompts(), _visual(), PromptPolicy(), source_text=source_text,
    )

    assert snapshot.scene_facts[0].source_references == []
    assert snapshot.scene_facts[1].source_references == []


def test_project_level_source_alignment_can_link_unmapped_raw_evidence_to_structured_shot():
    snapshot = PromptReviewAgent.build_audit_snapshot(
        _script(), _prompts(), _visual(), PromptPolicy(), source_text="原文第一段\n\n原文第二段",
    )
    issue = PromptReviewIssue(
        scene_id=0, related_scene_ids=[1], severity="major", category="fidelity",
        description="原文与结构化剧本不一致。", suggestion="修正解析。",
        layer="source_parse", scope="source_alignment", issue_code="parse.mismatch",
        evidence_items=[
            PromptReviewEvidence(source="source_script", scene_id=0, excerpt="原文第二段", line_start=3, line_end=3),
            PromptReviewEvidence(source="structured_script", scene_id=1, fact_id="scene:1:action", excerpt="苏眠从积水里捡起湿信封，撕开封口。"),
            PromptReviewEvidence(source="prompt", scene_id=1, excerpt="苏眠从积水里捡起湿信封，撕开封口。"),
        ],
    )

    PromptReviewAgent._validate_issue_evidence(issue, snapshot, "source_alignment", {1, 2})


def test_source_alignment_payload_instructs_project_scope_for_unmapped_raw_text():
    snapshot = PromptReviewAgent.build_audit_snapshot(
        _script(), _prompts(), _visual(), PromptPolicy(), source_text="原文第一段\n\n原文第二段",
    )

    payload = PromptReviewAgent._source_alignment_payload(snapshot)

    assert "scene_id=0" in payload["instruction"]
    assert "related_scene_ids" in payload["instruction"]


def test_source_evidence_requires_a_scene_binding_for_scene_level_issue():
    snapshot = PromptReviewAgent.build_audit_snapshot(
        _script(), _prompts(), _visual(), PromptPolicy(), source_text="原文第一段\n\n原文第二段",
    )
    issue = PromptReviewIssue(
        scene_id=1, severity="major", category="fidelity", description="原文与结构化剧本不一致。", suggestion="修正解析。",
        layer="source_parse", scope="source_alignment", issue_code="parse.mismatch",
        evidence_items=[
            PromptReviewEvidence(source="source_script", scene_id=0, excerpt="原文第二段"),
            PromptReviewEvidence(source="structured_script", scene_id=1, fact_id="scene:1:action", excerpt="苏眠从积水里捡起湿信封，撕开封口。"),
            PromptReviewEvidence(source="prompt", scene_id=1, excerpt="苏眠从积水里捡起湿信封，撕开封口。"),
        ],
    )

    with pytest.raises(ValueError, match="原文证据镜头"):
        PromptReviewAgent._validate_issue_evidence(issue, snapshot, "source_alignment", {1, 2})


def test_project_source_evidence_must_exist_in_raw_script_and_match_line_range():
    snapshot = PromptReviewAgent.build_audit_snapshot(
        _script(), _prompts(), _visual(), PromptPolicy(), source_text="真实原文第一行\n\n真实原文第三行",
    )
    issue = PromptReviewIssue(
        scene_id=0, related_scene_ids=[1], severity="major", category="fidelity",
        description="原文与结构化剧本不一致。", suggestion="修正解析。",
        layer="source_parse", scope="source_alignment", issue_code="parse.mismatch",
        evidence_items=[
            PromptReviewEvidence(source="source_script", scene_id=0, excerpt="提示词中的虚构片段", line_start=1, line_end=1),
            PromptReviewEvidence(source="structured_script", scene_id=1, fact_id="scene:1:action", excerpt="苏眠从积水里捡起湿信封，撕开封口。"),
            PromptReviewEvidence(source="prompt", scene_id=1, excerpt="苏眠从积水里捡起湿信封，撕开封口。"),
        ],
    )

    with pytest.raises(ValueError, match="原文证据不在原始剧本"):
        PromptReviewAgent._validate_issue_evidence(issue, snapshot, "source_alignment", {1, 2})


def test_source_alignment_project_issue_must_relates_its_structured_and_prompt_shots():
    snapshot = PromptReviewAgent.build_audit_snapshot(
        _script(), _prompts(), _visual(), PromptPolicy(), source_text="原文第一段\n\n原文第二段",
    )
    issue = PromptReviewIssue(
        scene_id=0, related_scene_ids=[], severity="major", category="fidelity",
        description="原文与结构化剧本不一致。", suggestion="修正解析。",
        layer="source_parse", scope="source_alignment", issue_code="parse.mismatch",
        evidence_items=[
            PromptReviewEvidence(source="source_script", scene_id=0, excerpt="原文第二段", line_start=3, line_end=3),
            PromptReviewEvidence(source="structured_script", scene_id=1, fact_id="scene:1:action", excerpt="苏眠从积水里捡起湿信封，撕开封口。"),
            PromptReviewEvidence(source="prompt", scene_id=1, excerpt="苏眠从积水里捡起湿信封，撕开封口。"),
        ],
    )

    with pytest.raises(ValueError, match="原文对齐证据镜头未关联到问题"):
        PromptReviewAgent._validate_issue_evidence(issue, snapshot, "source_alignment", {1, 2})


def test_source_evidence_line_range_must_match_the_snapshot_paragraph():
    snapshot = PromptReviewAgent.build_audit_snapshot(
        _script(), _prompts(), _visual(), PromptPolicy(), source_text="第一行\n第二行\n\n第三段",
    )
    issue = PromptReviewIssue(
        scene_id=0, related_scene_ids=[1], severity="major", category="fidelity",
        description="原文与结构化剧本不一致。", suggestion="修正解析。",
        layer="source_parse", scope="source_alignment", issue_code="parse.mismatch",
        evidence_items=[
            PromptReviewEvidence(source="source_script", scene_id=0, excerpt="第一行", line_start=1, line_end=1),
            PromptReviewEvidence(source="structured_script", scene_id=1, fact_id="scene:1:action", excerpt="苏眠从积水里捡起湿信封，撕开封口。"),
            PromptReviewEvidence(source="prompt", scene_id=1, excerpt="苏眠从积水里捡起湿信封，撕开封口。"),
        ],
    )

    with pytest.raises(ValueError, match="原文证据不在原始剧本"):
        PromptReviewAgent._validate_issue_evidence(issue, snapshot, "source_alignment", {1, 2})


def test_repeated_location_text_does_not_create_ambiguous_shot_source_reference():
    source_text = "雨夜巷口出现一盏灯。\n\n雨夜巷口又传来脚步声。"
    snapshot = PromptReviewAgent.build_audit_snapshot(
        _script(), _prompts(), _visual(), PromptPolicy(), source_text=source_text,
    )

    assert snapshot.scene_facts[0].source_references == []


def test_project_level_confirmed_issue_changes_verdict_even_without_shot_relation():
    script = _script()
    snapshot = PromptReviewAgent.build_audit_snapshot(script, _prompts(), _visual(), PromptPolicy(), source_text="")
    issue = PromptReviewIssue(
        scene_id=0, severity="major", category="fidelity", description="全片结构化剧本存在原文遗漏。", suggestion="回到剧本解析阶段修正。",
        layer="source_parse", scope="source_alignment", issue_code="parse.project_omission", confidence="confirmed",
    )

    report = PromptReviewAgent._finalize_report(script, snapshot, [issue], audit_signature="i" * 20)

    assert report.overall_score == 100
    assert report.verdict == "needs_review"
    assert report.passed is False


@pytest.mark.asyncio
async def test_candidate_rereview_receives_auditable_fact_and_prompt_snapshot(monkeypatch):
    import json
    import sys
    from types import SimpleNamespace

    captured = []

    class FakeCompletions:
        async def create(self, **kwargs):
            captured.append(json.loads(kwargs["messages"][1]["content"].split("\n", 1)[1]))
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps({
                    "decisions": [{"candidate_id": "candidate-1", "status": "confirmed"}],
                })),
            )])

    class FakeAsyncOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    snapshot = PromptReviewAgent.build_audit_snapshot(_script(), _prompts(), _visual(), PromptPolicy(), source_text="原始剧本")
    candidate = PromptReviewIssue(
        scene_id=1, severity="minor", category="clarity", description="表述不清。", suggestion="明确主语。",
        issue_code="prompt.clarity", candidate_id="candidate-1",
    )
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=FakeAsyncOpenAI))
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key="test")

    reviewed, status = await PromptReviewAgent()._rereview_candidates(client, "kimi-test", snapshot, [candidate])

    assert status == "completed"
    assert reviewed[0].confidence == "confirmed"
    payload = captured[0]
    assert payload["fact_index"][0]["prompt"]["prompt_text"]
    assert payload["source_script"]["status"] == "available"


@pytest.mark.asyncio
async def test_failed_candidate_rereview_indexes_the_unreviewed_state_to_affected_shots(monkeypatch):
    import sys
    from types import SimpleNamespace

    class FakeCompletions:
        async def create(self, **_kwargs):
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="not json"))])

    class FakeAsyncOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    snapshot = PromptReviewAgent.build_audit_snapshot(_script(), _prompts(), _visual(), PromptPolicy(), source_text="")
    candidate = PromptReviewIssue(
        scene_id=1, related_scene_ids=[2], severity="minor", category="continuity",
        description="承接待确认。", suggestion="人工复核。", issue_code="prompt.continuity", candidate_id="candidate-1",
    )
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=FakeAsyncOpenAI))
    from openai import AsyncOpenAI
    client = AsyncOpenAI(api_key="test")

    reviewed, status = await PromptReviewAgent()._rereview_candidates(client, "kimi-test", snapshot, [candidate])
    report = PromptReviewAgent._finalize_report(_script(), snapshot, reviewed, audit_signature="e" * 20)

    assert status == "unreviewed"
    assert any(item.confidence == "unreviewed" for item in report.shots_issue_index["1"])
    assert any(item.confidence == "unreviewed" for item in report.shots_issue_index["2"])


@pytest.mark.asyncio
async def test_provider_failure_in_candidate_rereview_keeps_candidates_for_manual_review():
    from types import SimpleNamespace

    class FailingCompletions:
        async def create(self, **_kwargs):
            raise RuntimeError("provider timeout")

    snapshot = PromptReviewAgent.build_audit_snapshot(_script(), _prompts(), _visual(), PromptPolicy(), source_text="")
    candidate = PromptReviewIssue(
        scene_id=1, related_scene_ids=[2], severity="major", category="continuity",
        description="承接待确认。", suggestion="人工复核。", issue_code="prompt.continuity", candidate_id="candidate-1",
    )
    client = SimpleNamespace(chat=SimpleNamespace(completions=FailingCompletions()))

    reviewed, status = await PromptReviewAgent()._rereview_candidates(client, "kimi-test", snapshot, [candidate])

    assert status == "unreviewed"
    assert reviewed[0].confidence == "suspected"
    assert reviewed[-1].confidence == "unreviewed"
    assert reviewed[-1].related_scene_ids == [1, 2]


def test_legacy_v1_review_remains_readable_without_v2_fields():
    legacy = PromptReviewResult.model_validate({
        "overall_score": 82,
        "passed": True,
        "summary": "旧报告",
        "shots": [],
        "issues": [],
    })

    assert legacy.report_version == 1
    assert legacy.verdict == "pass"
    assert PromptReviewResult.model_validate(legacy.model_dump()).overall_score == 82


def test_structured_evidence_alias_replaces_an_empty_evidence_items_list():
    evidence = {
        "source": "prompt",
        "scene_id": 1,
        "excerpt": "实际提示词片段",
    }
    issue = PromptReviewIssue.model_validate({
        "scene_id": 1,
        "severity": "minor",
        "category": "clarity",
        "description": "表述不清。",
        "suggestion": "明确主语。",
        "evidence": [evidence],
        "evidence_items": [],
    })

    assert len(issue.evidence_items) == 1
    assert issue.evidence_items[0].excerpt == "实际提示词片段"


def test_v2_report_keeps_structured_issue_evidence_and_shot_index():
    evidence = PromptReviewEvidence(
        source="structured_script",
        scene_id=1,
        fact_id="scene:1:action",
        excerpt="苏眠从积水里捡起湿信封，撕开封口。",
    )
    issue = PromptReviewIssue(
        scene_id=1,
        severity="major",
        category="coverage",
        description="提示词缺少撕开封口这一动作。",
        suggestion="在动作意图中补入撕开封口。",
        layer="prompt",
        scope="single_shot",
        issue_code="prompt.coverage_missing",
        confidence="confirmed",
        fact_ids=["scene:1:action"],
        evidence_items=[evidence],
        repair_action="edit_prompt",
    )
    report = PromptReviewResult(
        report_version=2,
        audit_signature="a" * 20,
        overall_score=85,
        passed=False,
        verdict="needs_review",
        summary="一项待人工确认的问题。",
        shots=[],
        issues=[issue],
        shots_issue_index={"1": [issue]},
    )

    dumped = report.model_dump()
    assert dumped["issues"][0]["evidence_items"][0]["fact_id"] == "scene:1:action"
    assert dumped["shots_issue_index"]["1"][0]["issue_code"] == "prompt.coverage_missing"


def test_deterministic_rules_only_report_structural_prompt_problems():
    script = _script()
    prompts = _prompts()
    prompts.shots[0].prompt = ""
    prompts.shots[0].duration_sec = 4
    prompts.shots[1].prompt = (
        "【单镜头时间轴】0-5s 苏眠递信；4-9s 店员后退。"
        " @prop:missing_asset:front"
    )
    snapshot = PromptReviewAgent.build_audit_snapshot(
        script, prompts, _visual(), PromptPolicy(), source_text="",
    )

    codes = {item.issue_code for item in PromptReviewAgent._deterministic_issues(script, prompts, snapshot)}

    assert {"rule.prompt_empty", "rule.duration_conflict", "rule.timeline_overlap", "rule.timeline_budget", "rule.invalid_reference"} <= codes


def test_deterministic_rule_evidence_uses_snapshot_rule_facts_for_missing_and_empty_prompts():
    script = _script()
    prompts = _prompts()
    prompts.shots = [prompts.shots[0]]
    prompts.shots[0].prompt = ""
    snapshot = PromptReviewAgent.build_audit_snapshot(
        script, prompts, _visual(), PromptPolicy(), source_text="",
    )

    issues = {item.issue_code: item for item in PromptReviewAgent._deterministic_issues(script, prompts, snapshot)}

    assert snapshot.rule_facts
    assert any(
        evidence.source == "rule"
        and evidence.fact_id == "scene:2:prompt:presence"
        and evidence.excerpt == "missing"
        for evidence in issues["rule.prompt_missing"].evidence_items
    )
    assert any(
        evidence.source == "rule"
        and evidence.fact_id == "scene:1:prompt:content"
        and evidence.excerpt == "empty"
        for evidence in issues["rule.prompt_empty"].evidence_items
    )


def test_invalid_timeline_range_is_retained_in_snapshot_and_reported_as_a_rule():
    script = _script()
    prompts = _prompts()
    prompts.shots[1].prompt = "【单镜头时间轴】5-2s 店员后退。"
    snapshot = PromptReviewAgent.build_audit_snapshot(
        script, prompts, _visual(), PromptPolicy(), source_text="",
    )

    assert snapshot.scene_facts[1].prompt.timeline_errors == ["5-2s 店员后退"]
    codes = {item.issue_code for item in PromptReviewAgent._deterministic_issues(script, prompts, snapshot)}
    assert "rule.timeline_invalid_range" in codes


def test_invalid_asset_references_are_not_reported_when_no_manifest_exists():
    script = _script()
    script.asset_manifest = None
    prompts = _prompts()
    prompts.shots[0].prompt += " @prop:manual_letter:front"
    snapshot = PromptReviewAgent.build_audit_snapshot(
        script, prompts, _visual(), PromptPolicy(), source_text="",
    )

    codes = {item.issue_code for item in PromptReviewAgent._deterministic_issues(script, prompts, snapshot)}
    assert "rule.invalid_reference" not in codes


def test_timeline_budget_evidence_quotes_the_segment_that_exceeds_the_budget():
    script = _script()
    prompts = _prompts()
    prompts.shots[1].prompt = "【单镜头时间轴】0-9s 店员后退；0-2s 苏眠递信。"
    snapshot = PromptReviewAgent.build_audit_snapshot(
        script, prompts, _visual(), PromptPolicy(), source_text="",
    )

    issues = PromptReviewAgent._deterministic_issues(script, prompts, snapshot)
    budget = next(item for item in issues if item.issue_code == "rule.timeline_budget")
    assert "0-9s" in budget.evidence_items[0].excerpt


def test_confirmed_project_level_cross_shot_issue_penalizes_both_related_shots():
    script = _script()
    snapshot = PromptReviewAgent.build_audit_snapshot(
        script, _prompts(), _visual(), PromptPolicy(), source_text="",
    )
    cross_shot_issue = PromptReviewIssue(
        scene_id=0,
        related_scene_ids=[1, 2],
        severity="major",
        category="continuity",
        description="前后镜头的信封状态无法承接。",
        suggestion="统一前镜结束状态与后镜起始状态。",
        layer="prompt",
        scope="cross_shot",
        issue_code="prompt.cross_shot_state",
        confidence="confirmed",
    )

    report = PromptReviewAgent._finalize_report(
        script, snapshot, [cross_shot_issue], audit_signature="d" * 20,
    )

    assert [shot.score for shot in report.shots] == [85, 85]
    assert report.overall_score == 85
    assert report.verdict == "needs_review"


def test_confirmed_issues_drive_duration_weighted_scores_while_suspected_issues_do_not():
    script = _script()
    snapshot = PromptReviewAgent.build_audit_snapshot(
        script, _prompts(), _visual(), PromptPolicy(), source_text="",
    )
    issues = [
        PromptReviewIssue(
            scene_id=1,
            severity="critical",
            category="fidelity",
            description="确认的剧情错位。",
            suggestion="修改提示词。",
            layer="prompt",
            scope="single_shot",
            issue_code="prompt.out_of_script",
            confidence="confirmed",
        ),
        PromptReviewIssue(
            scene_id=2,
            severity="minor",
            category="clarity",
            description="确认的表述歧义。",
            suggestion="明确动作主语。",
            layer="prompt",
            scope="single_shot",
            issue_code="prompt.clarity",
            confidence="confirmed",
        ),
        PromptReviewIssue(
            scene_id=1,
            severity="major",
            category="coverage",
            description="未获复核支持的候选问题。",
            suggestion="人工查看。",
            layer="prompt",
            scope="single_shot",
            issue_code="prompt.coverage_missing",
            confidence="suspected",
        ),
    ]

    report = PromptReviewAgent._finalize_report(script, snapshot, issues, audit_signature="b" * 20)

    assert [shot.score for shot in report.shots] == [60, 95]
    assert report.overall_score == 82
    assert report.verdict == "needs_review"
    assert report.passed is False


def test_unreviewed_audit_output_is_visible_without_changing_content_score():
    script = _script()
    snapshot = PromptReviewAgent.build_audit_snapshot(
        script, _prompts(), _visual(), PromptPolicy(), source_text="",
    )
    report = PromptReviewAgent._finalize_report(
        script,
        snapshot,
        [PromptReviewIssue(
            scene_id=1,
            severity="minor",
            category="quality",
            description="单镜审核输出无法通过证据校验。",
            suggestion="重新运行审核。",
            layer="prompt",
            scope="single_shot",
            issue_code="audit.unreviewed_output",
            confidence="unreviewed",
            rule_id="audit.output_validation",
        )],
        audit_signature="c" * 20,
    )

    assert [shot.score for shot in report.shots] == [100, 100]
    assert report.verdict == "unreviewed"
    assert report.passed is False
