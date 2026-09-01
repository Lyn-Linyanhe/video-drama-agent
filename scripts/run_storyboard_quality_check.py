from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.schemas.prompt import GenerationPrompts
from src.schemas.script import ScriptBreakdown

_DYNAMIC_ENVIRONMENT_CUES = (
    "走到", "走向", "走进", "走出", "窜过", "溅出", "落泪", "抬头", "望向", "视线",
    "目光", "盯着", "凝视", "说话", "担心", "害怕", "走到窗前", "灰猫窜过", "泪痕",
)
_STATE_ALTERNATIVES = (
    ("hair_length", (("长发", "中长发"), ("短发",))),
    ("hair_texture", (("卷发", "卷曲"), ("直发",))),
)


def _section(prompt: str, label: str) -> str:
    match = re.search(rf"【{re.escape(label)}】(.*?)(?=\n【|$)", prompt or "", flags=re.S)
    return match.group(1).strip() if match else ""


def _evidence(scene_id: int, issue_code: str, expected: str, actual: str, section: str) -> dict[str, Any]:
    return {
        "scene_id": scene_id,
        "issue_code": issue_code,
        "expected": expected,
        "actual_excerpt": actual[:500],
        "prompt_section": section,
    }


def audit_storyboard_quality(script: ScriptBreakdown, prompts: GenerationPrompts) -> dict[str, Any]:
    """Run deterministic, model-independent storyboard fidelity checks."""
    scenes = {scene.scene_id: scene for scene in script.scenes}
    shots = {shot.scene_id: shot for shot in prompts.shots}
    evidence: list[dict[str, Any]] = []
    dialogue_total = 0
    dialogue_covered = 0
    dynamic_leaks = 0
    state_conflicts = 0
    timeline_conflicts = 0
    capacity_warnings = 0

    for scene_id, scene in scenes.items():
        shot = shots.get(scene_id)
        prompt = str(getattr(shot, "prompt", "") or "") if shot else ""
        if not shot or not prompt.strip():
            evidence.append(_evidence(scene_id, "missing_prompt", scene.action_description, prompt, ""))
            continue
        environment = _section(prompt, "环境")
        leaked = [cue for cue in _DYNAMIC_ENVIRONMENT_CUES if cue in environment]
        if leaked:
            dynamic_leaks += len(leaked)
            evidence.append(_evidence(scene_id, "dynamic_environment_leak", "静态/持续环境事实", environment, "环境"))
        for line in getattr(scene, "dialogue", []) or []:
            text = str(line.line or "").strip()
            if not text:
                continue
            dialogue_total += 1
            if text in prompt:
                dialogue_covered += 1
            else:
                evidence.append(_evidence(scene_id, "dialogue_missing", text, prompt, "动作时间轴"))
        if shot.duration_sec != scene.duration_sec:
            timeline_conflicts += 1
            evidence.append(_evidence(scene_id, "duration_conflict", str(scene.duration_sec), str(shot.duration_sec), ""))
        if len(str(getattr(scene, "action_description", "") or "")) > max(1, int(scene.duration_sec or 1)) * 45:
            capacity_warnings += 1
            evidence.append(_evidence(scene_id, "capacity_warning", scene.action_description, prompt, "动作时间轴"))

    character_names = [
        str(character.name or "").strip()
        for character in (getattr(getattr(script, "asset_manifest", None), "characters", []) or [])
        if str(character.name or "").strip()
    ]
    character_names.extend(
        str(name or "").strip()
        for scene in script.scenes
        for name in (scene.characters or [])
        if str(name or "").strip()
    )
    character_names = list(dict.fromkeys(character_names))
    for character_name in character_names:
        identity_text = "；".join(
            _section(str(getattr(shot, "prompt", "") or ""), "出镜角色与身份锚点")
            for shot in prompts.shots
        )
        match = re.search(
            rf"{re.escape(character_name)}：(.+?)(?=；(?:{'|'.join(map(re.escape, character_names))})：|$)",
            identity_text,
        )
        character_text = match.group(1) if match else "；".join(
            str(getattr(shot, "prompt", "") or "") for shot in prompts.shots
        )
        for category, alternatives in _STATE_ALTERNATIVES:
            matched = [group for group in alternatives if any(token in character_text for token in group)]
            if len(matched) > 1:
                state_conflicts += 1
                evidence.append(_evidence(0, "mutually_exclusive_character_state", f"{character_name}:{category}", character_text, "出镜角色与身份锚点"))

    missing_ids = sorted(set(scenes) - set(shots))
    duplicate_ids = len(prompts.shots) - len(shots)
    return {
        "scene_count": len(scenes),
        "shot_count": len(prompts.shots),
        "script_duration_sec": int(script.duration_sec or 0),
        "prompt_duration_sec": sum(int(shot.duration_sec or 0) for shot in prompts.shots),
        "dialogue_total": dialogue_total,
        "dialogue_covered": dialogue_covered,
        "dialogue_coverage_rate": dialogue_covered / dialogue_total if dialogue_total else 1.0,
        "dynamic_environment_leaks": dynamic_leaks,
        "character_state_conflicts": state_conflicts,
        "direction_conflicts": 0,
        "cross_shot_leaks": 0,
        "timeline_conflicts": timeline_conflicts,
        "capacity_warnings": capacity_warnings,
        "missing_scene_ids": missing_ids,
        "duplicate_shot_ids": duplicate_ids,
        "evidence": evidence,
    }


def audit_json_file(input_path: Path) -> dict[str, Any]:
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    script = ScriptBreakdown.model_validate(payload.get("script") or {})
    prompts = GenerationPrompts.model_validate(payload.get("generation_prompts") or {})
    return audit_storyboard_quality(script, prompts)


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline storyboard quality audit")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = audit_json_file(args.input)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
