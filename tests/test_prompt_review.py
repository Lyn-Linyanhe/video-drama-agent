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
from src.schemas.script import Scene, ScriptBreakdown


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
