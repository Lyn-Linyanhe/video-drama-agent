import json

from src.api import context as api_context
from src.api.context import _missing_media_paths
from src.graph.state import DramaState
from src.schemas.audio import AudioAssets


def test_missing_media_paths_are_reported_without_changing_state(tmp_path):
    missing_video = tmp_path / "missing-final.mp4"
    missing_bgm = tmp_path / "missing-bgm.mp3"
    state = DramaState(
        output_video_path=str(missing_video),
        audio_assets=AudioAssets(scenes=[], bgm_path=str(missing_bgm)),
    )

    assert _missing_media_paths(state) == sorted([str(missing_bgm), str(missing_video)])
    assert state.output_video_path == str(missing_video)
    assert state.audio_assets.bgm_path == str(missing_bgm)


def test_missing_media_paths_ignore_remote_urls():
    state = DramaState(output_video_path="https://example.test/clip.mp4")

    assert _missing_media_paths(state) == []


def test_state_load_relocates_moved_output_media_paths(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    local_video = output_dir / "project-a" / "assets" / "final" / "done.mp4"
    local_audio = output_dir / "project-a" / "assets" / "audio" / "voice.mp3"
    local_video.parent.mkdir(parents=True)
    local_audio.parent.mkdir(parents=True)
    local_video.write_bytes(b"video")
    local_audio.write_bytes(b"audio")
    monkeypatch.setattr(api_context, "OUTPUT_DIR", output_dir)

    state_json = json.dumps({
        "project_id": "project-a",
        "output_video_path": r"E:\old\checkout\output\project-a\assets\final\done.mp4",
        "audio_assets": {
            "scenes": [],
            "bgm_path": r"E:\old\checkout\output\project-a\assets\audio\voice.mp3",
        },
    })

    state = api_context._state_from_json(state_json)

    assert state.output_video_path == str(local_video)
    assert state.audio_assets.bgm_path == str(local_audio)
    assert _missing_media_paths(state) == []


def test_state_load_repairs_utf8_mojibake_character_path(tmp_path, monkeypatch):
    output_dir = tmp_path / "output"
    local_image = output_dir / "project-a" / "assets" / "characters" / "苏眠" / "front_half.png"
    local_image.parent.mkdir(parents=True)
    local_image.write_bytes(b"png")
    monkeypatch.setattr(api_context, "OUTPUT_DIR", output_dir)

    mojibake_name = "苏眠".encode("utf-8").decode("latin-1")
    stale_path = (
        "E:\\old\\checkout\\output\\project-a\\assets\\characters\\"
        + mojibake_name
        + "\\front_half.png"
    )
    state_json = json.dumps({
        "project_id": "project-a",
        "character_images": {
            "characters": [{
                "character_name": "苏眠",
                "view_type": "front_half",
                "image_prompt": "portrait",
                "image_path": stale_path,
                "seed": 1,
            }],
        },
    }, ensure_ascii=False)

    state = api_context._state_from_json(state_json)

    assert state.character_images is not None
    assert state.character_images.characters[0].image_path == str(local_image)
    assert _missing_media_paths(state) == []
