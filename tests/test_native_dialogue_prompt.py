from types import SimpleNamespace

from src.services.native_audio import append_native_dialogue_prompt, build_native_dialogue_block


def _scene():
    return SimpleNamespace(
        scene_id=2,
        dialogue=[
            SimpleNamespace(character="顾衡", line="你终于来了。", emotion="压低声音"),
            SimpleNamespace(character="苏眠", line="我一直在这里。", emotion=None),
        ],
    )


def test_seedance_dialogue_block_keeps_scene_specific_source_ids():
    block, lines = build_native_dialogue_block(_scene())
    assert "【对白】" in block
    assert "顾衡" in block
    assert lines[0]["line_id"] == "2:1"
    assert lines[1]["line_id"] == "2:2"


def test_append_native_dialogue_prompt_does_not_replace_original_prompt():
    prompt, lines = append_native_dialogue_prompt("一个雨夜的地下通道", _scene())
    assert prompt.startswith("一个雨夜的地下通道")
    assert "不新增台词" in prompt
    assert lines[0]["text"] == "你终于来了。"
