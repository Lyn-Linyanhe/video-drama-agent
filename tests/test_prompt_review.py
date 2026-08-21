import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from src.agents.prompt_review_agent import PromptReviewAgent
from src.api.routes import jobs
from src.graph.state import DramaState
from src.schemas.prompt import GenerationPrompts, PromptPolicy, ShotPrompt
from src.schemas.prompt_review import PromptReviewIssue, PromptReviewResult
from src.services.job_manager import JobCanceled
from src.schemas.script import Scene, ScriptBreakdown
from src.schemas.visual import VisualDesign


class _Context:
    def __init__(self, project_id: str):
        self.project_id = project_id
        self.job_id = "prompt-review-001"
        self.project_lock = asyncio.Lock()

    def progress(self, *_args, **_kwargs):
        return None

    def check_cancelled(self):
        return None


def _state() -> DramaState:
    script = ScriptBreakdown(
        title="提示词审核",
        genre="悬疑",
        logline="测试",
        duration_sec=5,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=5,
                location="走廊",
                characters=["林屿"],
                action_description="林屿握住钥匙回头观察",
                camera_direction="中景推进",
                emotion_tone="紧张",
            )
        ],
    )
    return DramaState(
        project_id="a" * 12,
        script_phase="confirmed",
        script=script,
        generation_prompts=GenerationPrompts(
            api_provider="mock",
            shots=[ShotPrompt(scene_id=1, prompt="林屿在走廊握住钥匙，紧张回头，中景推进", duration_sec=5)],
        ),
    )


def test_prompt_review_accepts_executability_issues():
    issue = PromptReviewIssue(
        scene_id=1,
        severity="major",
        category="executability",
        description="镜头动作顺序不明确。",
        suggestion="明确人物动作、道具位置和镜头运动顺序。",
    )

    assert issue.category == "executability"


def test_prompt_review_normalizes_provider_category_aliases():
    payload = {
        "overall_score": 80,
        "passed": True,
        "summary": "reviewed",
        "shots": [{
            "scene_id": 1,
            "score": 80,
            "passed": True,
            "summary": "ready",
            "issues": [{
                "scene_id": 1,
                "severity": "major",
                "category": "feasibility",
                "description": "The action sequence is vague.",
                "suggestion": "State the action order.",
            }],
        }],
        "issues": [{
            "scene_id": 0,
            "severity": "minor",
            "category": "unfamiliar-provider-label",
            "description": "Provider-specific category wording.",
            "suggestion": "Use a supported review category.",
        }],
    }

    normalized = PromptReviewAgent._normalize_review_payload(payload)
    result = PromptReviewResult.model_validate(normalized)

    assert result.shots[0].issues[0].category == "executability"
    assert result.issues[0].category == "quality"


@pytest.mark.asyncio
async def test_prompt_review_batches_long_projects(monkeypatch):
    scenes = [
        Scene(
            scene_id=index,
            duration_sec=5,
            location=f"location-{index}",
            characters=["Lead"],
            action_description=f"action-{index}",
            camera_direction="medium shot",
            emotion_tone="tense",
        )
        for index in range(1, 6)
    ]
    script = ScriptBreakdown(
        title="Batch review",
        genre="thriller",
        logline="A review batching test.",
        duration_sec=25,
        narration_style="none",
        scenes=scenes,
    )
    prompts = GenerationPrompts(
        api_provider="mock",
        shots=[
            ShotPrompt(scene_id=index, prompt=f"prompt-{index}", duration_sec=5)
            for index in range(1, 6)
        ],
    )
    calls = []

    class FakeCompletions:
        async def create(self, **kwargs):
            user_content = kwargs["messages"][1]["content"]
            payload = json.loads(user_content.split("项目数据:\n", 1)[1])
            scene_ids = payload["review_scope"]["target_scene_ids"]
            calls.append(scene_ids)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps({
                    "overall_score": 90,
                    "passed": True,
                    "summary": f"reviewed {scene_ids}",
                    "shots": [
                        {
                            "scene_id": scene_id,
                            "score": 90,
                            "passed": True,
                            "summary": "ready",
                            "issues": [],
                        }
                        for scene_id in scene_ids
                    ],
                    "issues": [],
                })),
            )])

    class FakeAsyncOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setenv("KIMI_PROMPT_REVIEW_API_KEY", "test-key")
    monkeypatch.setenv("KIMI_PROMPT_REVIEW_BATCH_SIZE", "4")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=FakeAsyncOpenAI))

    result = await PromptReviewAgent().run(script, prompts, None, PromptPolicy())

    assert calls == [[1, 2, 3, 4], [5]]
    assert [shot.scene_id for shot in result.shots] == [1, 2, 3, 4, 5]
    assert result.overall_score == 90
    assert result.passed is True


@pytest.mark.asyncio
async def test_prompt_review_v2_uses_three_stages_and_marks_missing_source_not_applicable(monkeypatch):
    state = _state()
    calls = []

    class FakeCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content=json.dumps({"issues": []})),
            )])

    class FakeAsyncOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setenv("KIMI_PROMPT_REVIEW_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=FakeAsyncOpenAI))

    result = await PromptReviewAgent().run(
        state.script, state.generation_prompts, None, PromptPolicy(), source_text="",
    )

    assert result.report_version == 2
    assert result.audit_snapshot.source_script.status == "not_applicable"
    assert result.verdict == "pass"
    assert [record.stage for record in result.stage_records] == [
        "source_alignment", "single_shot", "candidate_rereview",
    ]
    assert result.stage_records[0].status == "skipped"
    assert len(calls) == 1
    assert calls[0]["temperature"] == 1
    assert calls[0]["max_tokens"] == 16384
    assert calls[0]["extra_body"] == {"thinking": {"type": "enabled"}}
    assert calls[0]["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_prompt_review_v2_invalid_json_becomes_unreviewed_after_format_retry(monkeypatch):
    state = _state()
    calls = []

    class FakeCompletions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(
                message=SimpleNamespace(content="not json"),
            )])

    class FakeAsyncOpenAI:
        def __init__(self, **_kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setenv("KIMI_PROMPT_REVIEW_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(AsyncOpenAI=FakeAsyncOpenAI))

    result = await PromptReviewAgent().run(
        state.script, state.generation_prompts, None, PromptPolicy(), source_text="",
    )

    assert result.verdict == "unreviewed"
    assert any(issue.category == "audit" and issue.confidence == "unreviewed" for issue in result.issues)
    assert all(issue.category != "quality" for issue in result.issues if issue.confidence == "unreviewed")
    assert len(calls) == 4  # single-shot plus candidate rereview, each with one repair


@pytest.mark.asyncio
async def test_prompt_review_persists_kimi_report(monkeypatch):
    state = _state()
    monkeypatch.setattr(jobs, "_load_project_state", lambda *_args, **_kwargs: state)
    monkeypatch.setattr(jobs, "_save_project_state", lambda *_args, **_kwargs: None)

    class FakeReviewer:
        def enabled(self):
            return True

        def is_configured(self):
            return True

        def _settings(self):
            return ("key", "kimi-k2.6", "https://api.moonshot.cn/v1")

        async def run(self, *_args):
            return PromptReviewResult(
                overall_score=91,
                passed=True,
                summary="镜头覆盖完整",
                shots=[],
                issues=[],
            )

    monkeypatch.setattr(jobs, "PromptReviewAgent", FakeReviewer)
    signature = jobs._prompt_review_signature(state)

    result = await jobs._job_prompt_review(_Context(state.project_id), {"prompt_signature": signature})

    assert result["report"]["overall_score"] == 91
    assert state.prompt_review["status"] == "ready"
    assert state.prompt_review["provider"] == "kimi"


def test_prompt_review_signature_changes_when_source_or_visual_inputs_change():
    state = _state()
    state.script_draft_text = "原始剧本 A"
    first = jobs._prompt_review_signature(state)
    state.script_draft_text = "原始剧本 B"
    assert jobs._prompt_review_signature(state) != first
    state.script_draft_text = "原始剧本 A"
    state.visual_design = VisualDesign(
        overall_style="写实", color_palette="冷色", scenes=[],
    )
    assert jobs._prompt_review_signature(state) != first


def test_job_signature_matches_agent_audit_signature():
    state = _state()
    state.script_draft_text = "原始剧本"
    snapshot = PromptReviewAgent.build_audit_snapshot(
        state.script, state.generation_prompts, state.visual_design,
        state.prompt_policy, source_text=state.script_draft_text,
        scene_vision_descriptions=state.scene_vision_descriptions,
    )
    assert jobs._prompt_review_signature(state) == PromptReviewAgent.audit_signature(snapshot)


def test_only_current_confirmed_deterministic_review_blocks_video_generation():
    state = _state()
    issue = PromptReviewIssue(
        scene_id=1, severity="critical", category="coverage", description="空提示词。", suggestion="补充提示词。",
        issue_code="rule.prompt_empty", confidence="confirmed", deterministic=True, blocks_generation=True,
    )
    state.prompt_review = {"status": "ready", "report": {"report_version": 2, "issues": [issue.model_dump()]}}
    assert jobs._prompt_review_blocks_generation(state) is True
    issue.confidence = "suspected"
    state.prompt_review["report"]["issues"] = [issue.model_dump()]
    assert jobs._prompt_review_blocks_generation(state) is False
    state.prompt_review["status"] = "stale"
    issue.confidence = "confirmed"
    state.prompt_review["report"]["issues"] = [issue.model_dump()]
    assert jobs._prompt_review_blocks_generation(state) is False


def test_prompt_review_generation_gate_reads_shot_and_index_issues():
    state = _state()
    issue = PromptReviewIssue(
        scene_id=1, severity="critical", category="coverage", description="空提示词。", suggestion="补充提示词。",
        issue_code="rule.prompt_empty", confidence="confirmed", deterministic=True, blocks_generation=True,
    )
    report = {"report_version": 2, "issues": [], "shots": [{"issues": [issue.model_dump()]}]}
    state.prompt_review = {"status": "ready", "report": report}
    assert jobs._prompt_review_blocks_generation(state) is True

    report["shots"] = []
    report["shots_issue_index"] = {"1": [issue.model_dump()]}
    assert jobs._prompt_review_blocks_generation(state) is True


def test_direct_prompt_review_job_id_is_persisted(monkeypatch):
    state = _state()
    signature = "signature-123"
    state.prompt_review = {"status": "generating", "prompt_signature": signature, "job_id": ""}
    monkeypatch.setattr(jobs, "_load_project_state", lambda *_args, **_kwargs: state)
    monkeypatch.setattr(jobs, "_save_project_state", lambda *_args, **_kwargs: None)

    assert jobs._persist_prompt_review_job_id("project-1", signature, "job-123") is True
    assert state.prompt_review["job_id"] == "job-123"


def test_prompt_review_deduplication_compares_signatures_and_stale_writes_are_owned():
    same = {
        "job_type": "prompt.review",
        "target_type": "prompt_set",
        "target_id": "current",
        "request": {"prompt_signature": "same"},
    }
    same_spec = {**same, "payload": {"prompt_signature": "same"}}
    changed_spec = {**same, "payload": {"prompt_signature": "changed"}}
    assert jobs._active_job_matches_spec(same, same_spec) is True
    assert jobs._active_job_matches_spec(same, changed_spec) is False

    state = _state()
    state.prompt_review = {"job_id": "new-job"}
    assert jobs._prompt_review_job_owns_state(state, "old-job") is False
    assert jobs._prompt_review_job_owns_state(state, "new-job") is True
    assert jobs._prompt_review_job_can_claim_state(state, "old-job") is False
    state.prompt_review["job_id"] = ""
    assert jobs._prompt_review_job_can_claim_state(state, "new-job") is True


@pytest.mark.asyncio
async def test_superseded_same_signature_review_cannot_claim_or_overwrite(monkeypatch):
    state = _state()
    signature = jobs._prompt_review_signature(state)
    state.prompt_review = {
        "status": "generating",
        "prompt_signature": signature,
        "job_id": "new-job",
    }
    monkeypatch.setattr(jobs, "_load_project_state", lambda *_args, **_kwargs: state)
    monkeypatch.setattr(jobs, "_save_project_state", lambda *_args, **_kwargs: None)

    class FakeReviewer:
        def enabled(self):
            return True

        def is_configured(self):
            return True

        def _settings(self):
            return ("key", "kimi-k2.6", "https://api.moonshot.cn/v1")

        async def run(self, *_args):
            raise AssertionError("superseded review must not call Kimi")

    monkeypatch.setattr(jobs, "PromptReviewAgent", FakeReviewer)
    result = await jobs._job_prompt_review(_Context(state.project_id), {"prompt_signature": signature})

    assert result["superseded"] is True
    assert state.prompt_review["job_id"] == "new-job"
    assert state.prompt_review["status"] == "generating"


@pytest.mark.asyncio
async def test_prompt_review_preflight_failure_persists_unavailable(monkeypatch):
    state = _state()
    signature = jobs._prompt_review_signature(state)
    state.prompt_review = {"status": "generating", "prompt_signature": signature, "job_id": ""}
    monkeypatch.setattr(jobs, "_load_project_state", lambda *_args, **_kwargs: state)
    monkeypatch.setattr(jobs, "_save_project_state", lambda *_args, **_kwargs: None)

    class FakeReviewer:
        def enabled(self):
            return True

        def is_configured(self):
            return False

        def _settings(self):
            return ("", "kimi-k2.6", "https://api.moonshot.cn/v1")

        def configuration_error(self):
            return "Kimi 未配置"

    monkeypatch.setattr(jobs, "PromptReviewAgent", FakeReviewer)

    with pytest.raises(ValueError, match="Kimi 未配置"):
        await jobs._job_prompt_review(_Context(state.project_id), {"prompt_signature": signature})

    assert state.prompt_review["status"] == "unavailable"
    assert state.prompt_review["error"] == "Kimi 未配置"


@pytest.mark.asyncio
async def test_prompt_review_cancellation_persists_canceled(monkeypatch):
    state = _state()
    signature = jobs._prompt_review_signature(state)
    state.prompt_review = {"status": "generating", "prompt_signature": signature, "job_id": ""}
    monkeypatch.setattr(jobs, "_load_project_state", lambda *_args, **_kwargs: state)
    monkeypatch.setattr(jobs, "_save_project_state", lambda *_args, **_kwargs: None)

    class FakeReviewer:
        def enabled(self):
            return True

        def is_configured(self):
            return True

        def _settings(self):
            return ("key", "kimi-k2.6", "https://api.moonshot.cn/v1")

        async def run(self, *_args):
            raise asyncio.CancelledError()

    monkeypatch.setattr(jobs, "PromptReviewAgent", FakeReviewer)

    with pytest.raises(asyncio.CancelledError):
        await jobs._job_prompt_review(_Context(state.project_id), {"prompt_signature": signature})

    assert state.prompt_review["status"] == "canceled"
    assert state.prompt_review["error"] == "审核任务已取消"


@pytest.mark.asyncio
async def test_prompt_review_job_canceled_exception_persists_canceled(monkeypatch):
    state = _state()
    signature = jobs._prompt_review_signature(state)
    state.prompt_review = {"status": "generating", "prompt_signature": signature, "job_id": ""}
    monkeypatch.setattr(jobs, "_load_project_state", lambda *_args, **_kwargs: state)
    monkeypatch.setattr(jobs, "_save_project_state", lambda *_args, **_kwargs: None)

    class FakeReviewer:
        def enabled(self):
            return True

        def is_configured(self):
            return True

        def _settings(self):
            return ("key", "kimi-k2.6", "https://api.moonshot.cn/v1")

        async def run(self, *_args):
            raise JobCanceled("任务已由用户终止")

    monkeypatch.setattr(jobs, "PromptReviewAgent", FakeReviewer)

    with pytest.raises(JobCanceled):
        await jobs._job_prompt_review(_Context(state.project_id), {"prompt_signature": signature})

    assert state.prompt_review["status"] == "canceled"


@pytest.mark.asyncio
async def test_prompt_review_failure_is_persisted(monkeypatch):
    state = _state()
    monkeypatch.setattr(jobs, "_load_project_state", lambda *_args, **_kwargs: state)
    monkeypatch.setattr(jobs, "_save_project_state", lambda *_args, **_kwargs: None)

    class FakeReviewer:
        def enabled(self):
            return True

        def is_configured(self):
            return True

        def _settings(self):
            return ("key", "kimi-k2.6", "https://api.moonshot.cn/v1")

        async def run(self, *_args):
            raise RuntimeError("Kimi unavailable")

    monkeypatch.setattr(jobs, "PromptReviewAgent", FakeReviewer)
    signature = jobs._prompt_review_signature(state)

    with pytest.raises(RuntimeError, match="Kimi unavailable"):
        await jobs._job_prompt_review(_Context(state.project_id), {"prompt_signature": signature})

    assert state.prompt_review["status"] == "failed"
    assert "Kimi unavailable" in state.prompt_review["error"]
