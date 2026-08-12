from pathlib import Path


def test_character_group_button_is_hidden_until_a_master_exists():
    source = Path("web/js/components/image-editor.js").read_text(encoding="utf-8")
    start = source.index("function renderImagePolicyControls()")
    end = source.index("async function loadImageGenerationPolicy()", start)
    controls = source[start:end]

    assert "masterButton.style.display = canGroup && !hasMaster ? '' : 'none'" in controls
    assert "allButton.style.display = canGroup && hasMaster ? '' : 'none'" in controls
