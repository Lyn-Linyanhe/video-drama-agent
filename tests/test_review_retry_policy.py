from pathlib import Path

from PIL import Image

from src.services import media_quality
from src.services import asset_generation
from src.services.media_quality import MediaQualityFailure
from src.api import context as api_context
from src.api.context import RegenerateImageRequest
from src.api_clients.image_base import ImageGenResult
from tests.test_asset_generation_api import _client
from src.utils.asset_versions import ImageAssetVersions


def _image(path: Path, color=(80, 120, 180)) -> Path:
    Image.new("RGB", (96, 96), color).save(path)
    return path


def test_uncertain_visual_review_does_not_trigger_automatic_retry():
    report = {
        "status": "warning",
        "passed": False,
        "should_regenerate": False,
        "issues": ["审核模型无法确认人物是否完整"],
        "evidence_confidence": "low",
    }

    assert media_quality.review_decision(report) == "uncertain"
    assert media_quality.should_retry_visual_review(report) is False


def test_explicit_hard_failure_is_the_only_automatic_retry_case():
    report = {
        "status": "warning",
        "passed": False,
        "should_regenerate": True,
        "issues": ["要求背面但清晰可见正脸"],
        "hard_fail": True,
        "evidence_confidence": "high",
        "retry_scope": "view",
    }

    assert media_quality.review_decision(report) == "hard_fail"
    assert media_quality.should_retry_visual_review(report) is True


def test_candidate_version_is_kept_without_replacing_active(tmp_path):
    versions = ImageAssetVersions(tmp_path)
    active_source = _image(tmp_path / "active.png", (10, 30, 50))
    candidate_source = _image(tmp_path / "candidate.png", (200, 120, 40))

    active_path, active_history = versions.replace_with_file(
        "prop", "letter", "front", active_source, "active prompt", "generated",
        metadata={"quality_report": {"status": "passed"}},
    )
    active_id = next(item["id"] for item in active_history if item["active"])

    candidate_id, history = versions.add_candidate_file(
        "prop", "letter", "front", candidate_source, "retry prompt", "generated",
        metadata={
            "quality_report": {"status": "warning", "review_decision": "hard_fail"},
            "attempt_no": 2,
        },
    )

    assert candidate_id
    assert Path(active_path).is_file()
    assert next(item for item in history if item["id"] == active_id)["active"] is True
    candidate = next(item for item in history if item["id"] == candidate_id)
    assert candidate["active"] is False
    assert candidate["candidate"] is True
    assert Path(candidate["path"]).is_file()


def test_review_model_default_is_qwen_vl_max(monkeypatch):
    import importlib
    import src.config as config

    monkeypatch.delenv("REVIEW_LLM_MODEL", raising=False)
    importlib.reload(config)
    assert config.REVIEW_LLM_MODEL == "qwen-vl-max"


async def _noop_validate(*_args, **_kwargs):
    return {"status": "passed", "passed": True, "review_decision": "passed"}


def test_single_hard_failure_retries_once_and_keeps_first_attempt(tmp_path, monkeypatch):
    _, project_id = _client(tmp_path, monkeypatch)
    state = api_context._load_project_state(project_id)
    state.image_generation_policy = {"provider": "wan", "model": "wan2.7-image-pro", "resolution": "1K"}
    api_context._save_project_state(state)

    class FakeClient:
        provider_name = "wan"

        def __init__(self):
            self.calls = []

        async def generate_image(self, **kwargs):
            self.calls.append(kwargs)
            path = _image(tmp_path / f"attempt-{len(self.calls)}.png", (50 * len(self.calls), 50, 80))
            return ImageGenResult(True, local_path=str(path), width=96, height=96, seed=kwargs["seed"], provider_name="wan")

    fake = FakeClient()
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    decisions = iter([
        MediaQualityFailure(
            "hard fail",
            {"status": "warning", "visual_review": {
                "status": "warning", "hard_fail": True, "passed": False,
                "should_regenerate": True, "evidence_confidence": "high",
                "issues": ["要求背面但清晰可见正脸"],
            }},
        ),
        None,
    ])

    async def validate(*_args, **_kwargs):
        failure = next(decisions)
        if failure:
            raise failure
        return {"status": "passed", "passed": True, "review_decision": "passed"}

    monkeypatch.setattr(asset_generation, "_validate_generated_image", validate)
    result = __import__("asyncio").run(asset_generation.regenerate_image_asset(
        project_id,
        RegenerateImageRequest(asset_type="prop", asset_id="old_key", view_type="front"),
    ))

    assert len(fake.calls) == 2
    versions = result["history"]
    assert len(versions) == 2
    assert sum(1 for item in versions if item.get("active")) == 1
    assert any(item.get("candidate") and item.get("attempt_no") == 1 for item in versions)


def test_second_hard_failure_auto_selects_the_better_attempt(tmp_path, monkeypatch):
    _, project_id = _client(tmp_path, monkeypatch)
    state = api_context._load_project_state(project_id)
    state.image_generation_policy = {"provider": "wan", "model": "wan2.7-image-pro", "resolution": "1K"}
    api_context._save_project_state(state)

    class FakeClient:
        provider_name = "wan"

        def __init__(self):
            self.calls = []

        async def generate_image(self, **kwargs):
            self.calls.append(kwargs)
            path = _image(tmp_path / f"failed-attempt-{len(self.calls)}.png", (40 * len(self.calls), 60, 90))
            return ImageGenResult(True, local_path=str(path), width=96, height=96, seed=kwargs["seed"], provider_name="wan")

    fake = FakeClient()
    monkeypatch.setattr(asset_generation, "client_for_policy", lambda _policy: fake)
    reports = iter([
        {"status": "failed", "visual_review": {
            "status": "warning", "hard_fail": True, "passed": False,
            "score": 0.20, "evidence_confidence": "high", "issues": ["第一次严重偏差"],
        }},
        {"status": "failed", "visual_review": {
            "status": "warning", "hard_fail": True, "passed": False,
            "score": 0.62, "evidence_confidence": "high", "issues": ["第二次仍有偏差"],
        }},
    ])

    async def validate(*_args, **_kwargs):
        raise MediaQualityFailure("hard fail", next(reports))

    monkeypatch.setattr(asset_generation, "_validate_generated_image", validate)
    result = __import__("asyncio").run(asset_generation.regenerate_image_asset(
        project_id,
        RegenerateImageRequest(asset_type="prop", asset_id="old_key", view_type="front"),
    ))

    assert len(fake.calls) == 2
    assert result["quality"]["auto_selected_after_review"] is True
    assert result["quality"]["review_decision"] == "failed_after_retry"
    active = next(item for item in result["history"] if item.get("active"))
    assert active["attempt_no"] == 2
    assert active["quality_report"]["visual_review"]["score"] == 0.62
    assert {item.get("attempt_no") for item in result["history"] if item.get("candidate")} == {1, 2}
