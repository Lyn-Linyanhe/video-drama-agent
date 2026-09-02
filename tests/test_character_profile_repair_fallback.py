from types import SimpleNamespace


def test_profile_repair_metadata_preserves_failure_details():
    from src.agents.visual_prompt_agent import _apply_profile_repair_metadata

    visual = SimpleNamespace(profile_repair_status=None, profile_repair_errors=[])
    telemetry = {}
    _apply_profile_repair_metadata(visual, telemetry, "failed", ["角色A：缺少发型"])

    assert visual.profile_repair_status == "failed"
    assert visual.profile_repair_errors == ["角色A：缺少发型"]
    assert telemetry["character_profile_repair_status"] == "failed"
    assert telemetry["character_profile_repair_errors"] == ["角色A：缺少发型"]
