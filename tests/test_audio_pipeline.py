import asyncio
import base64
import subprocess
from pathlib import Path

import pytest

import api_server
from src.api import context as api_context
from src.api.routes import audio as audio_routes
from src.services.audio_timeline import (
    auto_select_project_audio,
    audio_summary,
    ensure_project_voice_profiles,
    validate_project_voice_profiles,
)
from src.agents.audio_agent import AudioAgent
from src.agents.assembly_agent import AssemblyAgent
from src.graph.state import DramaState
from src.schemas.assets import GeneratedAssets, ShotAsset
from src.schemas.audio import AudioAssets, AudioLibraryAsset, DialogueAudio, SceneAudio, SoundEffectTrack, VoiceProfile
from src.schemas.script import DialogueLine, Scene, ScriptBreakdown
from src.services.tts_provider import (
    TTSProviderError,
    describe_dashscope_tts_error,
    generate_minimax_tts,
    generate_qwen3_tts,
    generate_timeline_tts,
)


def _script() -> ScriptBreakdown:
    return ScriptBreakdown(
        title="audio test",
        genre="test",
        logline="test",
        duration_sec=6,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=6,
                location="room",
                characters=["A", "B"],
                dialogue=[
                    DialogueLine(character="A", line="first line"),
                    DialogueLine(character="B", line="second line"),
                ],
                action_description="talking",
                camera_direction="medium",
                emotion_tone="calm",
            )
        ],
    )


def _two_scene_script() -> ScriptBreakdown:
    script = _script()
    script.scenes = [
        Scene(
            scene_id=1,
            duration_sec=3,
            location="room",
            characters=["A"],
            dialogue=[DialogueLine(character="A", line="first scene")],
            action_description="talking",
            camera_direction="medium",
            emotion_tone="calm",
        ),
        Scene(
            scene_id=2,
            duration_sec=3,
            location="hall",
            characters=["B"],
            dialogue=[DialogueLine(character="B", line="second scene")],
            action_description="replying",
            camera_direction="medium",
            emotion_tone="calm",
        ),
    ]
    return script


def test_audio_summary_reports_voice_and_line_readiness(tmp_path):
    voice = tmp_path / "voice.mp3"
    voice.write_bytes(b"voice")
    script = _script()
    audio = AudioAssets(
        scenes=[SceneAudio(scene_id=1, dialogue_audios=[DialogueAudio(
            scene_id=1, dialogue_index=1, line_id="1:1", character="A",
            text="first line", audio_path=str(voice), duration_sec=1.0, success=True,
        )])],
        voice_profiles={"A": VoiceProfile(character="A", provider="edge", voice="zh-CN-YunxiNeural", confirmed=True)},
    )
    summary = audio_summary(DramaState(script=script, audio_assets=audio))
    assert summary["voice_count"] == 2
    assert summary["confirmed_voice_count"] == 1
    assert summary["pending_line_count"] == 1
    assert summary["can_assemble"] is False


def test_audio_summary_exposes_actionable_line_breakdown(tmp_path):
    voice = tmp_path / "voice.mp3"
    voice.write_bytes(b"voice")
    script = _script()
    audio = AudioAssets(
        scenes=[SceneAudio(scene_id=1, dialogue_audios=[
            DialogueAudio(
                scene_id=1, dialogue_index=1, line_id="1:1", character="A",
                text="first line", audio_path=str(voice), duration_sec=1.0,
                success=True, timing_conflict=True,
            ),
            DialogueAudio(
                scene_id=1, dialogue_index=2, line_id="1:2", character="B",
                text="second line", success=False, error_message="provider failed",
            ),
        ])],
        voice_profiles={
            "A": VoiceProfile(character="A", provider="edge", voice="zh-CN-YunxiNeural", confirmed=True),
            "B": VoiceProfile(character="B", provider="edge", voice="zh-CN-YunxiNeural", confirmed=True),
        },
    )

    summary = audio_summary(DramaState(script=script, audio_assets=audio))

    assert summary["failed_line_count"] == 1
    assert summary["pending_line_count"] == 0
    assert summary["timing_conflict_count"] == 1
    assert summary["unresolved_line_count"] == 2
    assert summary["failed_line_ids"] == ["1:2"]
    assert summary["timing_conflict_line_ids"] == ["1:1"]


def test_auto_select_audio_keeps_unmatched_bgm_silent_and_manual_choice():
    script = _script()
    audio = AudioAssets(
        scenes=[SceneAudio(scene_id=1)],
        library_assets=[AudioLibraryAsset(asset_id="bgm-1", kind="bgm", label="rain", tags=["rain"])],
    )
    state = DramaState(script=script, audio_assets=audio)
    auto_select_project_audio(state)
    assert state.audio_assets.scenes[0].bgm_asset_id == ""

    state.audio_assets.scenes[0].bgm_asset_id = "manual-bgm"
    state.audio_assets.scenes[0].bgm_manual = True
    auto_select_project_audio(state)
    assert state.audio_assets.scenes[0].bgm_asset_id == "manual-bgm"


def test_audio_preview_validates_project_without_cross_route_helper(monkeypatch):
    created = {"job_id": "preview-job", "project_id": "project-a"}
    monkeypatch.setattr(audio_routes, "_load_project_state", lambda project_id, **kwargs: DramaState(project_id=project_id))
    monkeypatch.setattr(audio_routes.job_store, "list_jobs", lambda *args, **kwargs: [])
    monkeypatch.setattr(audio_routes.job_manager, "create_job", lambda *args, **kwargs: created)
    monkeypatch.setattr(audio_routes, "_job_public", lambda job: {"job_id": job["job_id"]})

    result = asyncio.run(audio_routes.preview_project_audio_timeline("project-a"))

    assert result == {"job": {"job_id": "preview-job"}}


def test_audio_agent_keeps_each_line_and_reports_partial_failure(tmp_path, monkeypatch):
    agent = AudioAgent()

    async def fake_tts(scene_id, dialogue_index, character, text, voice, output_dir):
        if dialogue_index == 2:
            raise RuntimeError("provider unavailable")
        path = tmp_path / f"line-{dialogue_index}.mp3"
        path.write_bytes(b"audio")
        return str(path)

    async def fake_bgm(mood, output_dir):
        return ""

    monkeypatch.setattr(agent, "_generate_dialogue_tts", fake_tts)
    monkeypatch.setattr(agent, "_generate_mock_bgm", fake_bgm)
    monkeypatch.setattr(agent, "_audio_duration", lambda path: 1.25)

    result = asyncio.run(agent.run(_script(), GeneratedAssets(shots=[]), "project-a"))
    scene = result.scenes[0]
    assert len(scene.dialogue_audios) == 2
    assert scene.dialogue_audios[0].success is True
    assert scene.dialogue_audios[0].duration_sec == 1.25
    assert scene.dialogue_audios[1].success is False
    assert "provider unavailable" in scene.dialogue_audios[1].error_message
    assert scene.tts_audio_path == scene.dialogue_audios[0].audio_path


def test_audio_agent_serializes_dashscope_tts_requests(tmp_path, monkeypatch):
    agent = AudioAgent()
    active = 0
    maximum_active = 0

    async def fake_tts(scene_id, dialogue_index, character, text, voice, output_dir):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0.01)
        path = tmp_path / f"serial-{dialogue_index}.mp3"
        path.write_bytes(b"audio")
        active -= 1
        return str(path)

    monkeypatch.setattr(agent, "_generate_dialogue_tts", fake_tts)
    monkeypatch.setattr(agent, "_audio_duration", lambda path: 1.0)
    monkeypatch.setenv("DASHSCOPE_TTS_CONCURRENCY", "1")
    monkeypatch.setenv("DASHSCOPE_TTS_MIN_INTERVAL_MS", "0")

    asyncio.run(agent.run(_script(), GeneratedAssets(shots=[]), "project-a"))

    assert maximum_active == 1


def test_audio_timeline_versions_and_timing_conflicts_are_persisted(tmp_path, monkeypatch):
    agent = AudioAgent()

    async def fake_tts(scene_id, dialogue_index, character, text, voice, output_dir):
        path = tmp_path / f"overflow-{dialogue_index}.mp3"
        path.write_bytes(b"audio")
        return str(path)

    monkeypatch.setattr(agent, "_generate_dialogue_tts", fake_tts)
    monkeypatch.setattr(agent, "_audio_duration", lambda path: 4.0)
    existing = AudioAssets(scenes=[], voice_profiles={"A": VoiceProfile(character="A", provider="edge", voice="zh-CN-YunxiNeural")})

    result = asyncio.run(agent.run(_script(), GeneratedAssets(shots=[]), "project-a", existing))
    lines = result.scenes[0].dialogue_audios
    assert lines[0].versions and lines[0].active_version_id
    assert lines[0].start_ms == 0
    assert lines[1].timing_conflict is True


def test_line_regeneration_creates_an_immutable_audio_version(tmp_path, monkeypatch):
    agent = AudioAgent()
    captured = []

    async def fake_provider(text, provider, voice, output_path, **kwargs):
        captured.append(kwargs)
        Path(output_path).write_bytes(b"audio")
        return output_path, "dashscope-minimax", ""

    monkeypatch.setattr("src.agents.audio_agent.generate_timeline_tts", fake_provider)
    monkeypatch.setattr(agent, "_audio_duration", lambda path: 1.0)
    monkeypatch.setenv("MINIMAX_DEFAULT_MALE_VOICE", "male-qn-qingse")

    first = asyncio.run(agent.run(_script(), GeneratedAssets(shots=[]), "project-a"))
    first_line = first.scenes[0].dialogue_audios[0]
    first.scenes[0].bgm_asset_id = "manual-bgm"
    first.scenes[0].bgm_manual = True
    first.scenes[0].sfx_manual = True
    first.scenes[0].sfx_tracks = [SoundEffectTrack(asset_id="manual-sfx", start_ms=300)]
    first_line.emotion, first_line.speed, first_line.volume, first_line.pitch = "angry", 1.25, 0.8, -2
    first_line.needs_regeneration = True
    second = asyncio.run(agent.run(
        _script(), GeneratedAssets(shots=[]), "project-a", first, force_line_ids={"1:1"},
    ))
    line = second.scenes[0].dialogue_audios[0]
    assert len(line.versions) == 2
    assert line.versions[0].audio_path != line.versions[1].audio_path
    assert Path(line.versions[0].audio_path).is_file()
    assert Path(line.versions[1].audio_path).is_file()
    assert line.versions[-1].created_at
    assert captured[-1] == {"emotion": "angry", "speed": 1.25, "volume": 0.8, "pitch": -2}
    assert second.scenes[0].bgm_asset_id == "manual-bgm"
    assert second.scenes[0].sfx_tracks[0].asset_id == "manual-sfx"


def test_final_assembly_rejects_stale_audio_configuration(tmp_path):
    stale = AudioAssets(scenes=[SceneAudio(scene_id=1, dialogue_audios=[DialogueAudio(
        scene_id=1, dialogue_index=1, line_id="1:1", needs_regeneration=True,
    )])])
    with pytest.raises(ValueError, match="must be regenerated"):
        asyncio.run(AssemblyAgent().run(_script(), GeneratedAssets(shots=[]), stale, "project-a"))


def test_final_assembly_rejects_script_audio_text_mismatch(tmp_path):
    voice_path = tmp_path / "voice.mp3"
    voice_path.write_bytes(b"voice")
    stale = AudioAssets(scenes=[SceneAudio(scene_id=1, dialogue_audios=[DialogueAudio(
        scene_id=1, dialogue_index=1, line_id="1:1", character="A", text="old line",
        audio_path=str(voice_path), duration_sec=1.0, success=True,
    )])])
    with pytest.raises(ValueError, match="must be regenerated"):
        asyncio.run(AssemblyAgent().run(_script(), GeneratedAssets(shots=[]), stale, "project-a"))


def test_final_assembly_rejects_failed_dialogue(tmp_path):
    failed = AudioAssets(scenes=[SceneAudio(scene_id=1, dialogue_audios=[DialogueAudio(
        scene_id=1, dialogue_index=1, line_id="1:1", text="missing voice", success=False,
    )])])
    with pytest.raises(ValueError, match="failed or are missing"):
        asyncio.run(AssemblyAgent().run(_script(), GeneratedAssets(shots=[]), failed, "project-a"))


def test_minimax_tts_uses_dashscope_video_connection(tmp_path, monkeypatch):
    captured = {}

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {"output": {"audio": base64.b64encode(b"mp3").decode("ascii")}}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json, headers):
            captured.update(url=url, body=json, headers=headers)
            return Response()

    monkeypatch.setenv("HAPPYHORSE_API_KEY", "dashscope-key")
    monkeypatch.setenv("HAPPYHORSE_BASE_URL", "https://dashscope.aliyuncs.com")
    monkeypatch.setattr("src.services.tts_provider.httpx.AsyncClient", Client)
    output = tmp_path / "voice.mp3"

    result = asyncio.run(generate_minimax_tts("你好", "male-qn-qingse", str(output), emotion="happy"))
    assert result == str(output)
    assert output.read_bytes() == b"mp3"
    assert captured["url"].endswith("/api/v1/services/aigc/multimodal-generation/generation")
    assert captured["body"]["model"] == "MiniMax/speech-2.8-turbo"
    assert captured["body"]["input"]["voice_setting"]["emotion"] == "happy"


def test_qwen3_tts_uses_its_own_dashscope_request_shape(tmp_path, monkeypatch):
    captured = {}

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return {"output": {"audio": {"data": base64.b64encode(b"qwen-mp3").decode("ascii")}}}

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json, headers):
            captured.update(url=url, body=json, headers=headers)
            return Response()

    monkeypatch.setenv("HAPPYHORSE_API_KEY", "dashscope-key")
    monkeypatch.setenv("HAPPYHORSE_BASE_URL", "https://dashscope.aliyuncs.com")
    monkeypatch.setenv("QWEN_TTS_MODEL", "qwen3-tts-flash")
    monkeypatch.setattr("src.services.tts_provider.httpx.AsyncClient", Client)
    output = tmp_path / "qwen-voice.mp3"

    result = asyncio.run(generate_qwen3_tts("你好", "Cherry", str(output), speed=1.1, volume=0.8, pitch=2))
    assert result == str(output)
    assert output.read_bytes() == b"qwen-mp3"
    assert captured["body"]["model"] == "qwen3-tts-flash"
    assert captured["body"]["input"] == {"text": "你好", "voice": "Cherry"}
    assert captured["body"]["parameters"]["rate"] == 1.1
    assert captured["body"]["parameters"]["volume"] == 40


def test_timeline_tts_records_edge_fallback_reason(tmp_path, monkeypatch):
    async def fail_minimax(*args, **kwargs):
        raise TTSProviderError("provider rate limited")

    async def fake_edge(text, voice, output_path):
        Path(output_path).write_bytes(b"edge")
        return output_path

    monkeypatch.setattr("src.services.tts_provider.generate_minimax_tts", fail_minimax)
    monkeypatch.setattr("src.services.tts_provider.generate_tts", fake_edge)
    monkeypatch.setenv("EDGE_TTS_FALLBACK", "true")
    output = tmp_path / "fallback.mp3"
    path, provider, reason = asyncio.run(generate_timeline_tts(
        "hello", "minimax", "male-qn-qingse", str(output),
    ))
    assert path == str(output)
    assert provider == "edge"
    assert "rate limited" in reason


def test_timeline_tts_preserves_primary_and_fallback_errors(tmp_path, monkeypatch):
    async def fail_primary(*args, **kwargs):
        raise TTSProviderError("Qwen request failed: 401 unauthorized")

    async def fail_edge(*args, **kwargs):
        raise RuntimeError("Edge-TTS: No audio was received")

    monkeypatch.setattr("src.services.tts_provider.generate_qwen3_tts", fail_primary)
    monkeypatch.setattr("src.services.tts_provider.generate_tts", fail_edge)
    monkeypatch.setenv("EDGE_TTS_FALLBACK", "true")

    with pytest.raises(TTSProviderError, match="Qwen request failed.*Edge-TTS"):
        asyncio.run(generate_timeline_tts(
            "hello", "qwen3", "Ethan", str(tmp_path / "failed.mp3"),
        ))


def test_missing_minimax_voice_is_an_actionable_error_not_a_silent_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("EDGE_TTS_FALLBACK", "true")
    with pytest.raises(TTSProviderError, match="voice is not configured"):
        asyncio.run(generate_timeline_tts("hello", "minimax", "", str(tmp_path / "voice.mp3")))


def test_unactivated_dashscope_product_has_an_actionable_error():
    message = describe_dashscope_tts_error(
        "The product is not activated, please confirm that you have activated products"
    )
    assert "MiniMax/speech-2.8-turbo" in message
    assert "开通" in message


def test_auto_audio_selection_respects_manual_sfx_removal():
    state = DramaState(
        project_id="project-a",
        script=_script(),
        audio_assets=AudioAssets(scenes=[SceneAudio(scene_id=1, sfx_manual=True)], library_assets=[
            AudioLibraryAsset(asset_id="sfx", kind="sfx", label="talking", tags=["talking"]),
        ]),
    )
    auto_select_project_audio(state)
    assert state.audio_assets.scenes[0].sfx_tracks == []


def test_state_round_trip_preserves_subtitle_preference_and_legacy_audio():
    state = DramaState(
        project_id="project-a",
        subtitle_enabled=False,
        audio_assets=AudioAssets(scenes=[SceneAudio(scene_id=1, tts_audio_path="legacy.mp3")]),
    )
    restored = api_context._state_from_json(api_context._state_to_json(state))
    assert restored.subtitle_enabled is False
    assert restored.audio_assets.scenes[0].tts_audio_path == "legacy.mp3"
    assert restored.audio_assets.scenes[0].dialogue_audios == []


def test_state_round_trip_uses_dialogue_subtitles_by_default_and_keeps_legacy_flag():
    state = DramaState(project_id="project-a", subtitle_enabled=True, subtitle_mode="dialogue")
    restored = api_context._state_from_json(api_context._state_to_json(state))
    assert restored.subtitle_mode == "dialogue"
    assert restored.subtitle_enabled is True

    legacy = api_context._state_from_json('{"subtitle_enabled": false}')
    assert legacy.subtitle_mode == "off"
    assert legacy.subtitle_enabled is False


def test_audio_preflight_requires_confirmed_voice_profiles():
    state = DramaState(project_id="project-a", script=_script())
    ensure_project_voice_profiles(state)
    result = validate_project_voice_profiles(state)
    assert result["ready"] is False
    assert {item["character"] for item in result["speakers"]} == {"A", "B"}
    assert all(item["reason"] == "请确认角色音色" for item in result["speakers"])

    for profile in state.audio_assets.voice_profiles.values():
        profile.confirmed = True
    assert validate_project_voice_profiles(state)["ready"] is True


def test_audio_preflight_rejects_minimax_without_voice_id():
    state = DramaState(project_id="project-a", script=_script())
    ensure_project_voice_profiles(state)
    profile = state.audio_assets.voice_profiles["A"]
    profile.provider, profile.voice, profile.confirmed = "minimax", "", True
    result = validate_project_voice_profiles(state)
    a = next(item for item in result["speakers"] if item["character"] == "A")
    assert a["ready"] is False
    assert "MiniMax" in a["reason"]


def test_srt_can_omit_or_include_speaker_name(tmp_path):
    voice = tmp_path / "voice.mp3"
    voice.write_bytes(b"voice")
    audio_by_scene = {1: SceneAudio(scene_id=1, dialogue_audios=[DialogueAudio(
        scene_id=1, dialogue_index=1, character="A", text="hello",
        audio_path=str(voice), duration_sec=1.0, success=True,
    )])}
    agent = AssemblyAgent()

    dialogue = agent._write_subtitle_file(_script(), audio_by_scene, tmp_path, subtitle_mode="dialogue")
    speaker = agent._write_subtitle_file(_script(), audio_by_scene, tmp_path, subtitle_mode="speaker")

    assert "\nhello\n" in dialogue.read_text(encoding="utf-8")
    assert "A: hello" not in dialogue.read_text(encoding="utf-8")
    assert "A: hello" in speaker.read_text(encoding="utf-8")


def test_ffmpeg_fallback_builds_audio_mix_and_srt(tmp_path, monkeypatch):
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"video")
    voice_path = tmp_path / "voice.mp3"
    voice_path.write_bytes(b"voice")
    calls = []

    def fake_shell(command, timeout):
        output = command.split('"')[-2]
        Path(output).write_bytes(b"base")
        return subprocess.CompletedProcess(command, 0, "", "")

    def fake_ffmpeg(command, timeout):
        calls.append(command)
        Path(command[-1]).write_bytes(b"mixed")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("src.agents.assembly_agent.run_ffmpeg_shell", fake_shell)
    monkeypatch.setattr("src.agents.assembly_agent.run_ffmpeg", fake_ffmpeg)
    assets = GeneratedAssets(shots=[ShotAsset(scene_id=1, local_path=str(video_path), success=True)])
    bgm_path = tmp_path / "bgm.mp3"
    bgm_path.write_bytes(b"bgm")
    sfx_path = tmp_path / "sfx.mp3"
    sfx_path.write_bytes(b"sfx")
    audio = AudioAssets(scenes=[SceneAudio(scene_id=1, bgm_asset_id="bgm", bgm_fade_ms=400,
        sfx_tracks=[SoundEffectTrack(asset_id="sfx", start_ms=500, volume=0.7)], dialogue_audios=[DialogueAudio(
        scene_id=1, dialogue_index=1, character="A", text="hello", audio_path=str(voice_path),
        duration_sec=1.0, success=True,
    )])], library_assets=[
        AudioLibraryAsset(asset_id="bgm", kind="bgm", audio_path=str(bgm_path)),
        AudioLibraryAsset(asset_id="sfx", kind="sfx", audio_path=str(sfx_path)),
    ])
    output = tmp_path / "final.mp4"
    monkeypatch.setattr(AssemblyAgent, "_has_audio_stream", staticmethod(lambda path: True))
    monkeypatch.setattr(AssemblyAgent, "_media_duration", staticmethod(lambda path: 4.0))

    result = asyncio.run(AssemblyAgent()._assemble_with_ffmpeg(
        _script(), assets, audio, str(output), tmp_path, subtitle_enabled=True,
    ))
    assert result == str(output)
    assert output.read_bytes() == b"mixed"
    assert "-filter_complex" in calls[0]
    assert any(value.startswith("subtitles=") for value in calls[0])
    filter_graph = calls[0][calls[0].index("-filter_complex") + 1]
    assert "[0:a]volume=1.0[source]" in filter_graph
    assert "sidechaincompress" in filter_graph
    assert "afade=t=in" in filter_graph
    assert "loudnorm=I=-16" in filter_graph
    assert "atrim=duration=4.000[mix]" in filter_graph
    assert "-shortest" not in calls[0]


def test_ffmpeg_audio_and_subtitles_use_the_same_scene_start(tmp_path, monkeypatch):
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"video")
    voice_a = tmp_path / "voice-a.mp3"
    voice_b = tmp_path / "voice-b.mp3"
    voice_a.write_bytes(b"voice-a")
    voice_b.write_bytes(b"voice-b")
    calls = []

    def fake_ffmpeg(command, timeout):
        calls.append(command)
        Path(command[-1]).write_bytes(b"mixed")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("src.agents.assembly_agent.run_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(AssemblyAgent, "_has_audio_stream", staticmethod(lambda path: False))
    monkeypatch.setattr(AssemblyAgent, "_media_duration", staticmethod(lambda path: 5.75))
    audio = AudioAssets(scenes=[
        SceneAudio(scene_id=1, dialogue_audios=[DialogueAudio(
            scene_id=1, dialogue_index=1, character="A", text="first scene",
            audio_path=str(voice_a), duration_sec=1.0, success=True, start_ms=0,
        )]),
        SceneAudio(scene_id=2, dialogue_audios=[DialogueAudio(
            scene_id=2, dialogue_index=1, character="B", text="second scene",
            audio_path=str(voice_b), duration_sec=1.0, success=True, start_ms=0,
        )]),
    ])

    AssemblyAgent()._mux_ffmpeg_audio_and_subtitles(
        _two_scene_script(), audio, str(video_path), tmp_path, True, {1: 0, 2: 2750},
    )

    filter_graph = calls[0][calls[0].index("-filter_complex") + 1]
    assert "[2:a]adelay=2750:all=1" in filter_graph
    subtitle_text = next(tmp_path.glob("subtitles_*.srt")).read_text(encoding="utf-8")
    assert "00:00:02,750 --> 00:00:03,750" in subtitle_text
