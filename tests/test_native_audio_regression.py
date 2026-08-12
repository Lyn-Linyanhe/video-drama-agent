from src.api.context import ProductionSettingsRequest


def test_production_settings_accept_asr_preference():
    request = ProductionSettingsRequest(asr_enabled=True, asr_model="small")
    assert request.asr_enabled is True
    assert request.asr_model == "small"
