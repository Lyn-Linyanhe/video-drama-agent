import pytest
from pydantic import ValidationError

from src.api.context import ScriptAnalyzeRequest, ScriptDraftSaveRequest, ScriptRequest
import src.config as config


def test_script_input_budget_is_explicitly_expanded():
    assert getattr(config, "LLM_MAX_INPUT_CHARS", 0) == 80000


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (ScriptRequest, "user_input"),
        (ScriptAnalyzeRequest, "source_text"),
        (ScriptDraftSaveRequest, "draft_text"),
    ],
)
def test_script_requests_accept_longer_screenplay_text(model, field):
    text = "剧本" * 35000
    try:
        model(**{field: text})
    except ValidationError as exc:
        pytest.fail(f"{model.__name__} rejected the expanded screenplay limit: {exc}")
