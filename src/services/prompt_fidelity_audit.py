"""Deterministic, evidence-first fidelity checks for storyboard prompts."""

from __future__ import annotations

import re
from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.script import ScriptBreakdown, Scene
from src.schemas.prompt_fidelity import PromptFidelityEvidence, PromptFidelityIssue, PromptFidelityReport


def _section(prompt: str, label: str) -> str:
    match = re.search(rf"【{re.escape(label)}】(.*?)(?=\n【|$)", str(prompt or ""), re.S)
    return match.group(1).strip() if match else ""


def _issue(scene_id: int, code: str, severity: str, description: str, expected: str, actual: str, section: str, fact_id: str, *, blocks: bool = False) -> PromptFidelityIssue:
    return PromptFidelityIssue(
        scene_id=scene_id, issue_code=code, severity=severity, description=description,
        blocks_generation=blocks, evidence=PromptFidelityEvidence(
            scene_id=scene_id, fact_id=fact_id, expected_excerpt=expected,
            prompt_excerpt=actual, prompt_section=section,
        ),
    )


def _visible_asset_ids(script: ScriptBreakdown) -> set[str]:
    manifest = getattr(script, "asset_manifest", None)
    if not manifest:
        return set()
    return {str(item.asset_id).strip() for item in (*getattr(manifest, "characters", []), *getattr(manifest, "locations", []), *getattr(manifest, "props", [])) if str(getattr(item, "asset_id", "") or "").strip()}


def _action_tokens(action: str) -> list[str]:
    # Only extract unambiguous, observable anchors; semantic paraphrases stay with Kimi.
    terms = ("推门", "开门", "走出", "冲出", "离开", "拿起", "放下", "打开", "关闭", "锁上", "递给", "接过", "拔出", "塞进", "藏到身后", "监控", "猫眼")
    return list(dict.fromkeys(term for term in terms if term in str(action or "")))


def audit_prompt_fidelity(script: ScriptBreakdown, prompts: GenerationPrompts) -> PromptFidelityReport:
    scenes = {scene.scene_id: scene for scene in (script.scenes or [])}
    shots = {shot.scene_id: shot for shot in (prompts.shots or [])}
    issues: list[PromptFidelityIssue] = []
    dialogue_total = 0
    dialogue_covered = 0
    valid_assets = _visible_asset_ids(script)

    for scene_id, scene in scenes.items():
        shot = shots.get(scene_id)
        if shot is None or not str(shot.prompt or "").strip():
            issues.append(_issue(scene_id, "missing_prompt", "critical", "结构化镜头没有可用最终提示词", scene.action_description, "", "", f"scene:{scene_id}:prompt", blocks=True))
            continue
        prompt = str(shot.prompt or "")
        for line_index, line in enumerate(getattr(scene, "dialogue", []) or [], start=1):
            expected = str(getattr(line, "line", "") or "").strip()
            if not expected:
                continue
            dialogue_total += 1
            if expected in prompt:
                dialogue_covered += 1
            else:
                issues.append(_issue(scene_id, "dialogue_missing", "major", "结构化剧本对白未出现在当前镜头提示词中", expected, prompt, "动作时间轴", f"scene:{scene_id}:dialogue:{line_index}"))

        if int(getattr(shot, "duration_sec", 0) or 0) != int(getattr(scene, "duration_sec", 0) or 0):
            issues.append(_issue(scene_id, "duration_mismatch", "critical", "镜头提示词时长与结构化剧本不一致", str(scene.duration_sec), str(shot.duration_sec), "", f"scene:{scene_id}:duration", blocks=True))

        action = str(getattr(scene, "action_description", "") or "")
        action_section = _section(prompt, "动作时间轴") or prompt
        for token in _action_tokens(action):
            if token not in prompt:
                issues.append(_issue(scene_id, "action_anchor_missing", "major", f"关键可观察动作“{token}”未在提示词中出现", token, action_section, "动作时间轴", f"scene:{scene_id}:action:{token}"))

        forbidden = any(term in action for term in ("门始终关闭", "不得出门", "不能出门", "只能通过监控观察"))
        forbidden_actions = [term for term in ("推门", "开门", "打开门", "走出", "冲出") if term in prompt]
        if forbidden and forbidden_actions:
            issues.append(_issue(scene_id, "forbidden_exit_action", "critical", "提示词加入了结构化剧本明确禁止的出门/开门动作", action, "；".join(forbidden_actions), "动作时间轴", f"scene:{scene_id}:negative:closed-door", blocks=True))

        for asset_id in re.findall(r"(?:@asset:|asset_id[：:])([A-Za-z0-9_-]+)", prompt):
            if valid_assets and asset_id not in valid_assets:
                issues.append(_issue(scene_id, "invalid_asset_reference", "critical", "提示词引用了不存在的视觉资产", asset_id, asset_id, "关键道具", f"scene:{scene_id}:asset:{asset_id}", blocks=True))

    missing_scene_ids = sorted(set(scenes) - set(shots))
    for scene_id in missing_scene_ids:
        if not any(item.scene_id == scene_id and item.issue_code == "missing_prompt" for item in issues):
            scene = scenes[scene_id]
            issues.append(_issue(scene_id, "missing_prompt", "critical", "存在没有对应提示词的结构化镜头", scene.action_description, "", "", f"scene:{scene_id}:prompt", blocks=True))
    if len(prompts.shots) != len(shots):
        issues.append(_issue(0, "duplicate_shot_id", "critical", "提示词中存在重复镜头 ID", "每个 scene_id 只出现一次", str([shot.scene_id for shot in prompts.shots]), "", "prompt:shot_ids", blocks=True))

    status = "blocked" if any(item.blocks_generation for item in issues) else ("warning" if issues else "clean")
    return PromptFidelityReport(
        status=status, scene_count=len(scenes), shot_count=len(prompts.shots),
        dialogue_total=dialogue_total, dialogue_covered=dialogue_covered, issues=issues,
    )
