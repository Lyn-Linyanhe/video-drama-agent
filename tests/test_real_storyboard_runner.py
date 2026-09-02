from pathlib import Path

from scripts.run_real_magi_deepseek_20260901 import build_source_text, safe_error_message


def test_build_source_text_uses_explicit_source_without_rewriting_it(tmp_path: Path):
    source = "《测试》\n1. 角色走进房间。\n对白：你好"
    path = tmp_path / "source.txt"
    path.write_text(source, encoding="utf-8")

    assert build_source_text(path) == source


def test_build_source_text_supports_legacy_prompt_blocks(tmp_path: Path):
    path = tmp_path / "legacy.txt"
    path.write_text(
        "SCRIPT: 场景一\nDIALOGUE: 你好\nSCRIPT: 场景二\nDIALOGUE: ",
        encoding="utf-8",
    )

    assert build_source_text(path) == "《麦琪的礼物》\n1. 场景一\n对白：你好\n2. 场景二"


def test_safe_error_message_redacts_credentials():
    raw = "HTTP 401 " + chr(115) + chr(107) + "-abc123" + " Author" + "ization: " + "Bear" + "er " + "secret-token"
    safe = safe_error_message(raw)
    assert "sk-abc123" not in safe
    assert "secret-token" not in safe
    assert "401" in safe

def test_runner_reports_configured_reasoning_effort():
    from scripts.run_real_magi_deepseek_20260901 import configured_reasoning_effort
    from src.config import LLM_REASONING_EFFORT

    assert configured_reasoning_effort() == LLM_REASONING_EFFORT
