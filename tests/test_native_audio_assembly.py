from pathlib import Path

from src.agents.assembly_agent import AssemblyAgent
from src.schemas.audio import AudioAssets, DialogueAudio, SceneAudio
from src.schemas.script import DialogueLine, Scene, ScriptBreakdown


def _script():
    return ScriptBreakdown(
        title="test", genre="悬疑", logline="test", duration_sec=3,
        narration_style="none", scenes=[Scene(
            scene_id=1, duration_sec=3, location="室内", characters=["顾衡"],
            dialogue=[DialogueLine(character="顾衡", line="你好")],
            action_description="看向门口", camera_direction="中景", emotion_tone="紧张",
        )],
    )


def test_subtitle_writer_uses_asr_segments_when_ready(tmp_path):
    audio = AudioAssets(scenes=[SceneAudio(
        scene_id=1,
        native_audio_enabled=True,
        subtitle_correction_status="changed",
        asr_segments=[{
            "line_id": "1:1", "text": "识别后的对白", "start_ms": 200, "end_ms": 1200,
            "start": 0.2, "end": 1.2,
        }],
        dialogue_audios=[DialogueAudio(
            scene_id=1, dialogue_index=1, character="顾衡", text="你好", success=True,
        )],
    )])
    path = AssemblyAgent()._write_subtitle_file(
        _script(), {1: audio.scenes[0]}, Path(tmp_path), subtitle_mode="dialogue",
    )
    content = path.read_text(encoding="utf-8")
    assert "识别后的对白" in content
    assert "00:00:00,200 --> 00:00:01,200" in content


def test_native_audio_without_asr_falls_back_to_script_subtitles(tmp_path):
    scene_audio = SceneAudio(
        scene_id=1,
        native_audio_enabled=True,
        subtitle_correction_status="fallback",
        dialogue_audios=[DialogueAudio(
            scene_id=1, dialogue_index=1, character="顾衡", text="你好",
            duration_sec=1.0, start_ms=0, end_ms=1000, success=True,
        )],
    )
    path = AssemblyAgent()._write_subtitle_file(
        _script(), {1: scene_audio}, Path(tmp_path), subtitle_mode="dialogue",
    )
    assert "你好" in path.read_text(encoding="utf-8")
