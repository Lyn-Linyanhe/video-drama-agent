from src.services.asr import align_native_subtitles, reconcile_asr_segments


def test_asr_changed_dialogue_is_reported_without_overwriting_script():
    result = reconcile_asr_segments(
        [{"text": "你好啊", "start": 0, "end": 1, "confidence": 0.9}],
        [{"line_id": "1:1", "text": "你好", "start_ms": 0, "end_ms": 1000}],
    )
    assert result["status"] == "changed"
    assert result["changed_lines"][0]["script_text"] == "你好"
    assert result["segments"][0]["text"] == "你好啊"


def test_asr_unavailable_falls_back_to_script(tmp_path, monkeypatch):
    video = tmp_path / "missing-model.mp4"
    video.write_bytes(b"not a real video")
    monkeypatch.delenv("FASTER_WHISPER_MODEL_PATH", raising=False)
    result = align_native_subtitles(str(video), [], enabled=True)
    assert result["status"] == "fallback"
    assert result["source"] == "script"
