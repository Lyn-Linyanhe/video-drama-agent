from pathlib import Path
import json

from src.schemas.prompt import GenerationPrompts
from src.schemas.script import ScriptBreakdown
from src.services.prompt_execution_context import build_execution_context, render_execution_prompt
from src.services.shot_prompt_materializer import materialize_shot_prompts


PROJECT_META = Path("output/9559d0cd84ca/meta.json")


def _project():
    data = json.loads(PROJECT_META.read_text(encoding="utf-8"))
    script = ScriptBreakdown.model_validate(data["assets"]["script"])
    prompts = GenerationPrompts.model_validate(data["assets"]["generation_prompts"])
    shots = materialize_shot_prompts(
        prompts,
        script,
        duration_cap_sec=15,
        provider_min_duration_sec=4,
    )
    return script, shots


def _timeline(prompt: str) -> str:
    if "【动作时间轴】" not in prompt:
        return ""
    return prompt.split("【动作时间轴】", 1)[1].split("【", 1)[0]


def _line_text(script: ScriptBreakdown, dialogue_id: str) -> tuple[str, str]:
    import re

    from src.services.beat_planner import split_speech_chunks

    match = re.match(r"scene-(\d+)-dialogue-(\d+)(?:-part-(\d+))?$", dialogue_id)
    assert match, dialogue_id
    scene = next(item for item in script.scenes if item.scene_id == int(match.group(1)))
    line = scene.dialogue[int(match.group(2)) - 1]
    text = str(line.line or "")
    part = match.group(3)
    if part:
        # 拆段台词按规划时的打包预算截取本段，子镜时间轴只含这一段。
        chunks = split_speech_chunks(text, 14.0)
        text = chunks[int(part) - 1]
    return str(line.character or ""), text


def _refs_for(scene):
    refs = [
        {"asset_type": "scene", "asset_id": "loc", "label": "场景", "image_path": "x.png"},
    ]
    for name in scene.characters or []:
        refs.append({"asset_type": "character", "asset_id": name, "label": name, "image_path": "x.png"})
    return refs


def test_maggie_multi_scene_embeds_bound_dialogue_and_does_not_leak():
    script, shots = _project()
    by_scene: dict[int, list] = {}
    for shot in shots:
        by_scene.setdefault(shot.scene_id, []).append(shot)

    # Silent establishing scene must not invent spoken lines.
    scene1 = by_scene[1][0]
    assert scene1.dialogue_ids == []
    assert "这时候" not in _timeline(scene1.prompt)
    assert "【对白】" not in scene1.prompt

    # Shop scene: each spoken line stays in its bound child only.
    scene5_lines = {
        "scene-5-dialogue-1": "你要买我的头发吗？",
        "scene-5-dialogue-2": "我买头发。揭掉帽子，让我看看发样。",
        "scene-5-dialogue-3": "二十美元。",
        "scene-5-dialogue-4": "快给我钱。",
    }
    for shot in by_scene[5]:
        timeline = _timeline(shot.prompt)
        assert "【对白】" not in shot.prompt
        for dialogue_id, line in scene5_lines.items():
            if dialogue_id in (shot.dialogue_ids or []):
                assert line in timeline
            else:
                assert line not in timeline

    # Long apartment dialogue is split, not dumped into every child.
    pray = "求求上帝，让他觉得我还是漂亮的吧"
    long_line = "吉姆，亲爱的，别那样盯着我"
    gift_tail = "多么美丽精致的礼物啊"
    assert pray in _timeline(by_scene[7][0].prompt)
    assert pray not in _timeline(by_scene[7][1].prompt)
    assert long_line in _timeline(by_scene[7][1].prompt)
    assert gift_tail in _timeline(by_scene[7][2].prompt)
    assert "你已经把头发剪掉了？" not in by_scene[7][0].prompt

    # Reveal scene keeps two different speakers in their own children.
    assert "漂亮吗，吉姆？" in _timeline(by_scene[12][1].prompt)
    assert "卖掉金表" in _timeline(by_scene[12][2].prompt)
    assert "卖掉金表" not in _timeline(by_scene[12][1].prompt)
    assert "漂亮吗，吉姆？" not in _timeline(by_scene[12][2].prompt)


def test_maggie_execution_layer_keeps_spoken_line_inside_timeline_across_dialogue_scenes():
    script, shots = _project()
    scenes = {scene.scene_id: scene for scene in script.scenes}
    checked = 0
    for shot in shots:
        if not shot.dialogue_ids:
            continue
        scene = scenes[shot.scene_id]
        rendered = render_execution_prompt(
            build_execution_context(shot=shot, scene=scene, valid_references=_refs_for(scene))
        )
        timeline = _timeline(rendered)
        assert "【关键对白" not in rendered
        assert "【对白】" not in rendered
        assert "。，这时候" not in rendered
        excerpts = []
        for dialogue_id in shot.dialogue_ids:
            speaker, line = _line_text(script, dialogue_id)
            excerpts.append((speaker, line))
            assert speaker in timeline
        assert any((line[:8] in timeline) or (line[-8:] in timeline) for _, line in excerpts if line)
        checked += 1
    assert checked >= 8
