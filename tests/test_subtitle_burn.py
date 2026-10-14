"""Standalone subtitle burn: styling, narration, and non-destructive output."""

from pathlib import Path

import pytest

from src.agents.assembly_agent import (
    DEFAULT_SUBTITLE_STYLE,
    AssemblyAgent,
    normalize_subtitle_style,
    subtitle_filter,
    subtitle_force_style,
)
from src.schemas.audio import AudioAssets, DialogueAudio, SceneAudio
from src.schemas.script import DialogueLine, Scene, ScriptBreakdown


def _scene(scene_id=1, duration=4, dialogue=None, characters=None, action="说话"):
    return Scene(
        scene_id=scene_id, duration_sec=duration, location="院子",
        characters=characters or ["顾衡"],
        dialogue=dialogue if dialogue is not None else [],
        action_description=action, camera_direction="中景", emotion_tone="平静",
    )


def _script(scenes):
    return ScriptBreakdown(
        title="test", genre="剧情", logline="test",
        duration_sec=sum(item.duration_sec for item in scenes),
        narration_style="第三人称旁白", scenes=scenes,
    )


def _scene_audio(scene_id=1, text="你好", emotion="", duration=1.0):
    return SceneAudio(
        scene_id=scene_id,
        native_audio_enabled=True,
        subtitle_correction_status="fallback",
        dialogue_audios=[DialogueAudio(
            scene_id=scene_id, dialogue_index=1, character="顾衡", text=text,
            duration_sec=duration, start_ms=0, end_ms=int(duration * 1000),
            success=True, emotion=emotion,
        )],
    )


# --- style plumbing -------------------------------------------------------

def test_normalize_subtitle_style_fills_defaults_and_ignores_blanks():
    resolved = normalize_subtitle_style({"font_size": 30, "font_name": "  ", "margin_v": None})
    assert resolved["font_size"] == 30
    # Blank/None overrides must not wipe a usable default.
    assert resolved["font_name"] == DEFAULT_SUBTITLE_STYLE["font_name"]
    assert resolved["margin_v"] == DEFAULT_SUBTITLE_STYLE["margin_v"]
    assert resolved["outline"] == DEFAULT_SUBTITLE_STYLE["outline"]


def test_subtitle_force_style_reflects_overrides():
    default = subtitle_force_style(None)
    # Assert against the declared default rather than a literal, so tuning the
    # default size does not silently invalidate the style test.
    assert f"FontSize={DEFAULT_SUBTITLE_STYLE['font_size']}" in default
    assert "Alignment=2" in default

    custom = subtitle_force_style({"font_size": 28, "margin_v": 20, "outline": 2})
    assert "FontSize=28" in custom
    assert "MarginV=20" in custom
    assert "Outline=2," in custom


def test_subtitle_filter_escapes_windows_drive_letter_and_sets_style():
    built = subtitle_filter("E:/out/sub.srt", {"font_size": 32})
    # The colon in the drive letter must be escaped or ffmpeg parses it as an
    # option separator.
    assert "subtitles='E\\:/out/sub.srt'" in built
    assert "force_style=" in built
    assert "FontSize=32" in built


def test_subtitle_filter_escapes_single_quotes_in_path():
    built = subtitle_filter("/tmp/it's.srt")
    assert r"it\'s.srt" in built
    assert built.count("subtitles='") == 1


# --- narration ------------------------------------------------------------

def test_voiceover_line_detected_from_emotion_marker():
    assert AssemblyAgent.is_voiceover_line(_scene_audio(emotion="内心独白（画外音）").dialogue_audios[0])
    assert AssemblyAgent.is_voiceover_line(_scene_audio(emotion="voiceover").dialogue_audios[0])
    assert not AssemblyAgent.is_voiceover_line(_scene_audio(emotion="和蔼询问").dialogue_audios[0])


def test_dialogue_mode_labels_voiceover_lines_as_narration(tmp_path):
    scene_audio = _scene_audio(text="孔雀岂不也该是孔家的鸟？", emotion="内心独白（画外音）")
    path = AssemblyAgent()._write_subtitle_file(
        _script([_scene(dialogue=[DialogueLine(character="孔君平", line="孔雀岂不也该是孔家的鸟？")])]),
        {1: scene_audio}, Path(tmp_path), subtitle_mode="dialogue",
    )
    content = path.read_text(encoding="utf-8")
    # Off-screen lines must not read as the character speaking them again.
    assert "旁白：孔雀岂不也该是孔家的鸟？" in content


def test_speaker_mode_keeps_character_name_for_voiceover(tmp_path):
    scene_audio = _scene_audio(text="心里话", emotion="内心独白（画外音）")
    path = AssemblyAgent()._write_subtitle_file(
        _script([_scene(dialogue=[DialogueLine(character="孔君平", line="心里话")])]),
        {1: scene_audio}, Path(tmp_path), subtitle_mode="speaker",
    )
    assert "顾衡: 心里话" in path.read_text(encoding="utf-8")


def test_narration_mode_emits_only_narration_text(tmp_path):
    path = AssemblyAgent()._write_subtitle_file(
        _script([_scene(dialogue=[DialogueLine(character="孔君平", line="这是你家的水果吗？")])]),
        {1: _scene_audio(text="这是你家的水果吗？")},
        Path(tmp_path), subtitle_mode="narration",
        narration_lines={1: ["真正的聪明并不需要说很多话。"]},
    )
    content = path.read_text(encoding="utf-8")
    assert "旁白：真正的聪明并不需要说很多话。" in content
    assert "这是你家的水果吗？" not in content


def test_all_mode_includes_dialogue_and_narration(tmp_path):
    path = AssemblyAgent()._write_subtitle_file(
        _script([_scene(dialogue=[DialogueLine(character="孔君平", line="台词")])]),
        {1: _scene_audio(text="台词", emotion="和蔼询问")},
        Path(tmp_path), subtitle_mode="all",
        narration_lines={1: ["旁白句子"]},
    )
    content = path.read_text(encoding="utf-8")
    assert "台词" in content
    assert "旁白：旁白句子" in content


def test_narration_is_spread_inside_the_scene_window(tmp_path):
    path = AssemblyAgent()._write_subtitle_file(
        _script([_scene(duration=8)]), {},
        Path(tmp_path), subtitle_mode="narration",
        narration_lines={1: ["第一句。", "第二句。"]},
    )
    content = path.read_text(encoding="utf-8")
    # Two lines across an 8s scene: second starts at 4s, not at 0s.
    assert "00:00:00,000 --> 00:00:03,850" in content
    assert "00:00:04,000 --> 00:00:07,850" in content


def test_narration_only_returns_none_without_text(tmp_path):
    path = AssemblyAgent()._write_subtitle_file(
        _script([_scene()]), {}, Path(tmp_path), subtitle_mode="narration",
    )
    assert path is None


# --- standalone burn ------------------------------------------------------

def test_burn_subtitles_keeps_audio_and_writes_new_file(tmp_path, monkeypatch):
    import src.agents.assembly_agent as module

    video = tmp_path / "final.mp4"
    video.write_bytes(b"video")
    srt = tmp_path / "sub.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhi\n\n", encoding="utf-8")
    target = tmp_path / "final_sub.mp4"

    calls = []

    def fake_run_ffmpeg(command, timeout=None):
        calls.append(command)
        Path(command[-1]).write_bytes(b"burned")

        class Result:
            returncode = 0
            stderr = ""

        return Result()

    monkeypatch.setattr(module, "run_ffmpeg", fake_run_ffmpeg)
    result = AssemblyAgent().burn_subtitles_into_video(str(video), srt, str(target))

    assert result == str(target)
    assert target.read_bytes() == b"burned"
    command = calls[0]
    # Audio is copied, never re-mixed, so lip-sync cannot shift.
    assert "-c:a" in command and command[command.index("-c:a") + 1] == "copy"
    assert any(str(item).startswith("subtitles=") for item in command)
    # The source file must survive the burn.
    assert video.read_bytes() == b"video"


def test_burn_subtitles_rejects_missing_inputs(tmp_path):
    agent = AssemblyAgent()
    with pytest.raises(FileNotFoundError):
        agent.burn_subtitles_into_video(
            str(tmp_path / "nope.mp4"), str(tmp_path / "nope.srt"), str(tmp_path / "out.mp4"),
        )


def test_burn_subtitles_raises_when_ffmpeg_fails(tmp_path, monkeypatch):
    import src.agents.assembly_agent as module

    video = tmp_path / "final.mp4"
    video.write_bytes(b"video")
    srt = tmp_path / "sub.srt"
    srt.write_text("1\n", encoding="utf-8")

    class Result:
        returncode = 1
        stderr = "boom"

    monkeypatch.setattr(module, "run_ffmpeg", lambda command, timeout=None: Result())
    with pytest.raises(RuntimeError):
        AssemblyAgent().burn_subtitles_into_video(
            str(video), srt, str(tmp_path / "out.mp4"),
        )


def test_measure_scene_starts_offsets_by_measured_duration(tmp_path, monkeypatch):
    import src.agents.assembly_agent as module

    first = tmp_path / "a.mp4"
    second = tmp_path / "b.mp4"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    script = _script([_scene(1, duration=4), _scene(2, duration=4)])
    by_scene = {
        1: SceneAudio(scene_id=1, native_audio_path=str(first)),
        2: SceneAudio(scene_id=2, native_audio_path=str(second)),
    }

    # Real clips came back longer than the script estimate.
    monkeypatch.setattr(
        AssemblyAgent, "_media_duration", staticmethod(lambda path: 5.0 if path == str(first) else 3.0),
    )
    starts = AssemblyAgent()._measure_scene_starts(script, by_scene)
    assert starts[1] == 0
    assert starts[2] == 5000


def test_native_dialogue_scene_falls_back_to_script_lines(tmp_path):
    # Provider-native dialogue is baked into the video and produces no
    # dialogue_audios, so the SRT must fall back to the script text instead of
    # writing an empty file.
    scene = _scene(dialogue=[
        DialogueLine(character="孔君平", line="你的父亲在家吗？"),
        DialogueLine(character="杨氏子", line="父亲刚刚出去了。"),
    ], duration=8)
    native = SceneAudio(
        scene_id=1, native_audio_enabled=True,
        subtitle_correction_status="fallback", dialogue_audios=[],
    )
    path = AssemblyAgent()._write_subtitle_file(
        _script([scene]), {1: native}, Path(tmp_path), subtitle_mode="dialogue",
    )
    assert path is not None
    content = path.read_text(encoding="utf-8")
    assert "你的父亲在家吗？" in content
    assert "父亲刚刚出去了。" in content
    # Two lines across an 8s scene: the second starts at 4s.
    assert "00:00:04,000" in content


def test_native_dialogue_fallback_marks_voiceover(tmp_path):
    scene = _scene(dialogue=[
        DialogueLine(character="孔君平", line="孔雀岂不也该是孔家的鸟？", emotion="内心独白（画外音）"),
    ])
    native = SceneAudio(
        scene_id=1, native_audio_enabled=True,
        subtitle_correction_status="fallback", dialogue_audios=[],
    )
    path = AssemblyAgent()._write_subtitle_file(
        _script([scene]), {1: native}, Path(tmp_path), subtitle_mode="dialogue",
    )
    assert "旁白：孔雀岂不也该是孔家的鸟？" in path.read_text(encoding="utf-8")


def test_script_dialogue_does_not_override_measured_audio_tracks(tmp_path):
    # When real per-line audio exists it must still win over the script text.
    scene = _scene(dialogue=[DialogueLine(character="顾衡", line="剧本原文")], duration=4)
    scene_audio = _scene_audio(text="实际录音", duration=1.0)
    path = AssemblyAgent()._write_subtitle_file(
        _script([scene]), {1: scene_audio}, Path(tmp_path), subtitle_mode="dialogue",
    )
    content = path.read_text(encoding="utf-8")
    assert "实际录音" in content
    assert "剧本原文" not in content


# --- endpoint wiring ------------------------------------------------------

def _burn_client(tmp_path, monkeypatch):
    from tests.test_job_api import _client
    from src.api import context as api_context

    client, project_id = _client(tmp_path, monkeypatch)
    state = api_context._load_project_state(project_id, require_visual=False)
    state.script_phase = "confirmed"
    # Give the scene a line and matching audio so the SRT writer has content.
    state.script.scenes[0].dialogue = [DialogueLine(character="A", line="你好")]
    state.audio_assets = AudioAssets(scenes=[SceneAudio(
        scene_id=state.script.scenes[0].scene_id,
        native_audio_enabled=True,
        subtitle_correction_status="fallback",
        dialogue_audios=[DialogueAudio(
            scene_id=state.script.scenes[0].scene_id, dialogue_index=1,
            character="A", text="你好", duration_sec=1.0,
            start_ms=0, end_ms=1000, success=True,
        )],
    )])
    video = tmp_path / "final.mp4"
    video.write_bytes(b"video")
    state.output_video_path = str(video)
    api_context._save_project_state(state)
    return client, project_id, video


def test_burn_endpoint_takes_project_state_not_script(tmp_path, monkeypatch):
    """The route must hand the script to the measurer, not the whole drama state."""
    import src.agents.assembly_agent as module

    client, project_id, video = _burn_client(tmp_path, monkeypatch)
    captured = {}

    real_measure = AssemblyAgent._measure_scene_starts

    def spy(self, script, by_scene):
        captured["has_scenes"] = hasattr(script, "scenes")
        return real_measure(self, script, by_scene)

    monkeypatch.setattr(AssemblyAgent, "_measure_scene_starts", spy)

    def fake_burn(self, video_path, subtitle_path, output_path, subtitle_style=None):
        captured["burned"] = True
        Path(output_path).write_bytes(b"burned")
        return output_path

    monkeypatch.setattr(AssemblyAgent, "burn_subtitles_into_video", fake_burn)

    response = client.post(f"/api/projects/{project_id}/subtitles/burn?mode=dialogue")
    assert response.status_code == 200, response.text
    assert captured.get("has_scenes") is True
    assert captured.get("burned") is True
    body = response.json()
    # A new file is written; the source video is untouched.
    assert body["output_video_path"].endswith("_sub.mp4")
    assert body["source_video_path"] == str(video)
    assert video.read_bytes() == b"video"


def test_burn_endpoint_rejects_unknown_mode(tmp_path, monkeypatch):
    client, project_id, _video = _burn_client(tmp_path, monkeypatch)
    response = client.post(f"/api/projects/{project_id}/subtitles/burn?mode=bogus")
    assert response.status_code == 400


def test_burn_endpoint_requires_an_assembled_video(tmp_path, monkeypatch):
    from tests.test_job_api import _client
    from src.api import context as api_context

    client, project_id = _client(tmp_path, monkeypatch)
    state = api_context._load_project_state(project_id, require_visual=False)
    state.output_video_path = ""
    api_context._save_project_state(state)
    response = client.post(f"/api/projects/{project_id}/subtitles/burn?mode=dialogue")
    assert response.status_code == 409


def test_settings_endpoint_persists_narration_and_style(tmp_path, monkeypatch):
    from tests.test_job_api import _client

    client, project_id = _client(tmp_path, monkeypatch)
    response = client.put(
        f"/api/projects/{project_id}/production/settings",
        json={
            "subtitle_mode": "all",
            "subtitle_narration_text": "真正的聪明并不需要说很多话。",
            "subtitle_style": {"font_size": 30, "margin_v": 40},
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["subtitle_mode"] == "all"
    assert body["subtitle_narration_text"] == "真正的聪明并不需要说很多话。"
    assert body["subtitle_style"]["font_size"] == 30
    assert body["subtitle_style"]["margin_v"] == 40
    # Unspecified keys fall back to the acceptance-tested defaults.
    assert body["subtitle_style"]["font_name"] == DEFAULT_SUBTITLE_STYLE["font_name"]
