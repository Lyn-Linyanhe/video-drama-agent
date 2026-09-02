"""Deterministic, evidence-first fidelity checks for storyboard prompts."""

from __future__ import annotations

import re
from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.script import ScriptBreakdown, Scene
from src.schemas.prompt_fidelity import PromptFidelityEvidence, PromptFidelityIssue, PromptFidelityReport


def _section(prompt: str, label: str) -> str:
    match = re.search(rf"【{re.escape(label)}】(.*?)(?=\n【|$)", str(prompt or ""), re.S)
    return match.group(1).strip() if match else ""


def _issue(scene_id: int, code: str, severity: str, description: str, expected: str, actual: str, section: str, fact_id: str, *, blocks: bool = False, repair_action: str | None = None) -> PromptFidelityIssue:
    if repair_action is None:
        repair_action = "edit_prompt" if code in {
            "dialogue_missing", "action_anchor_missing", "forbidden_exit_action",
            "entry_exit_conflict", "cross_shot_dynamic_leak", "future_event_leak",
            "mutable_character_state_conflict", "prompt_assembly_failed",
        } else "edit_structure"
    return PromptFidelityIssue(
        scene_id=scene_id, issue_code=code, severity=severity, description=description,
        blocks_generation=blocks, repair_action=repair_action, evidence=PromptFidelityEvidence(
            scene_id=scene_id, fact_id=fact_id, expected_excerpt=expected,
            prompt_excerpt=actual, prompt_section=section,
        ),
    )


def _visible_asset_ids(script: ScriptBreakdown) -> set[str]:
    manifest = getattr(script, "asset_manifest", None)
    if manifest is None:
        return set()
    ids: set[str] = set()
    for item in getattr(manifest, "characters", []) or []:
        value = str(getattr(item, "asset_id", "") or "").strip()
        if value:
            ids.add(value)
    for item in getattr(manifest, "locations", []) or []:
        value = str(getattr(item, "location_id", "") or "").strip()
        if value:
            ids.add(value)
    for item in getattr(manifest, "props", []) or []:
        value = str(getattr(item, "prop_id", "") or "").strip()
        if value:
            ids.add(value)
    return ids


def _positive_prompt_evidence(prompt: str) -> str:
    """Keep observable prompt evidence while excluding negative guardrails."""
    action_section = _section(prompt, "动作时间轴")
    evidence = action_section if action_section else str(prompt or "")
    if not action_section:
        evidence = "\n".join(
            line for line in evidence.splitlines()
            if not re.search(r"(?:不得|禁止|不能|不应|不新增|不切镜头)", line)
        )
    return re.sub(r"(?:不得|禁止|不能|不应|不新增|不切镜头)[^。；;\n]*", "", evidence)


def _action_tokens(action: str) -> list[str]:
    # Only extract unambiguous, observable anchors; semantic paraphrases stay with Kimi.
    terms = ("推门", "开门", "走出", "冲出", "离开", "拿起", "放下", "打开", "关闭", "锁上", "递给", "接过", "拔出", "塞进", "藏到身后", "监控", "猫眼")
    return list(dict.fromkeys(term for term in terms if term in str(action or "")))



_ENTRY_TERMS = ("进入", "走进", "走入", "踏入", "进屋", "进门", "冲入")
_EXIT_TERMS = ("走出", "离开", "退出", "出门", "冲出")
_DYNAMIC_CUES = ("下雨", "雨水", "雨势", "风", "敲", "晃", "撞", "滴落", "冲刷", "呼啸", "燃烧", "闪烁", "持续", "奔跑", "跑", "走", "抬头", "转身", "挥动")
_FUTURE_ACTION_CUES = ("打开", "拿出", "取出", "递给", "接过", "拔出", "推门", "走出", "离开", "奔跑", "转身", "发现", "揭示", "倒下")
_MUTABLE_STATE_GROUPS = (
    (("长发", "中长发"), ("短发",)),
    (("卷发", "卷曲"), ("直发",)),
)
_TIMELINE_RANGE_RE = re.compile(r"(?P<start>\d+(?:\.\d+)?)\s*(?:-|–|—|~|至|到)\s*(?P<end>\d+(?:\.\d+)?)\s*(?:s|秒)?")


def _clauses(text: str) -> list[str]:
    return [part.strip(" ，,、") for part in re.split(r"[。！？!?；;\n]+", str(text or "")) if part.strip(" ，,、")]


def _normalized(text: str) -> str:
    return re.sub(r"\s+", "", str(text or ""))


def _direction(text: str, terms: tuple[str, ...]) -> str:
    return next((term for term in terms if term in str(text or "")), "")


def _alias_matches(line: str, alias: str) -> bool:
    if not alias:
        return False
    if re.fullmatch(r"[A-Za-z0-9_-]+", alias):
        return bool(re.search(rf"(?<![A-Za-z0-9_-]){re.escape(alias)}(?![A-Za-z0-9_-])", line))
    return alias in line


def _prompt_excerpt(prompt: str, target: str) -> tuple[str, str]:
    """Return the exact prompt text and section containing a normalized target."""
    normalized_target = _normalized(target)
    for match in re.finditer(r"【([^】]+)】(.*?)(?=\n【|$)", str(prompt or ""), re.S):
        section, body = match.group(1), match.group(2)
        if normalized_target in _normalized(body):
            compact = re.sub(r"\s+", "", body)
            start = compact.find(normalized_target)
            if start >= 0:
                positions = [index for index, char in enumerate(body) if not char.isspace()]
                end = positions[min(start + len(normalized_target) - 1, len(positions) - 1)] + 1
                begin = positions[start]
                return body[begin:end], section
            return target, section
    return target, ""


def _identity_anchor_line(anchor: str, aliases: list[str]) -> str:
    for line in re.split(r"[\n]+", str(anchor or "")):
        line = line.strip()
        if any(_alias_matches(line, alias) for alias in aliases):
            return line
    return ""


def _snapshot_prompt_section(prompt: str) -> tuple[str, str]:
    for label in ("动作时间轴", "单镜头时间轴", "时间轴分镜"):
        value = _section(prompt, label)
        if value:
            return value, label
    return "", ""


def _append_direction_conflicts(issues: list[PromptFidelityIssue], scene: Scene, action_evidence: str) -> None:
    scripted_entry = bool(_direction(scene.action_description, _ENTRY_TERMS))
    scripted_exit = bool(_direction(scene.action_description, _EXIT_TERMS))
    if scripted_entry and scripted_exit:
        return
    expected_direction = _direction(scene.action_description, _ENTRY_TERMS) or _direction(scene.action_description, _EXIT_TERMS)
    if not expected_direction:
        return
    expected_is_entry = scripted_entry
    actual_direction = _direction(action_evidence, _EXIT_TERMS if expected_is_entry else _ENTRY_TERMS)
    if actual_direction:
        issues.append(_issue(
            scene.scene_id,
            "entry_exit_conflict",
            "critical",
            "提示词出现了与结构化剧本相反的进出方向",
            expected_direction,
            actual_direction,
            "动作时间轴",
            f"scene:{scene.scene_id}:direction",
            blocks=True,
        ))


def _append_timeline_overflow(issues: list[PromptFidelityIssue], scene: Scene, shot: ShotPrompt, prompt: str) -> None:
    timeline, section = _snapshot_prompt_section(prompt)
    if not timeline:
        return
    for match in _TIMELINE_RANGE_RE.finditer(timeline):
        end = float(match.group("end"))
        if end <= float(getattr(shot, "duration_sec", 0) or 0):
            continue
        excerpt = match.group(0)
        issues.append(_issue(
            scene.scene_id,
            "timeline_overflow",
            "critical",
            "动作时间轴结束时间超过当前镜头时长",
            f"镜头时长不超过 {shot.duration_sec}s",
            excerpt,
            section,
            f"scene:{scene.scene_id}:timeline:{excerpt}",
            blocks=True,
        ))


def _append_character_state_conflict(issues: list[PromptFidelityIssue], scene: Scene, prompt: str, script: ScriptBreakdown) -> None:
    manifest = getattr(script, "asset_manifest", None)
    anchor = _section(prompt, "出镜角色与身份锚点")
    if manifest is None or not anchor:
        return
    for character in getattr(manifest, "characters", []) or []:
        if str(getattr(character, "appearance_mode", "") or "") != "on_screen":
            continue
        aliases = [str(getattr(character, "name", "") or "").strip(), str(getattr(character, "asset_id", "") or "").strip()]
        aliases.extend(str(item).strip() for item in (getattr(character, "aliases", []) or []))
        line = _identity_anchor_line(anchor, [item for item in aliases if item])
        if not line:
            continue
        for left, right in _MUTABLE_STATE_GROUPS:
            left_term = _direction(line, left)
            right_term = _direction(line, right)
            if not left_term or not right_term:
                continue
            issues.append(_issue(
                scene.scene_id,
                "mutable_character_state_conflict",
                "critical",
                "同一角色的全局身份锚点同时固定了互斥外观状态",
                f"{getattr(character, 'name', '')} 只应固定一个外观状态族",
                line,
                "出镜角色与身份锚点",
                f"scene:{scene.scene_id}:character:{getattr(character, 'asset_id', '')}:state",
                blocks=True,
            ))


def _append_cross_shot_dynamic_leak(issues: list[PromptFidelityIssue], scene: Scene, prompt: str, previous: Scene | None) -> None:
    if previous is None:
        return
    environment = _section(prompt, "环境")
    if scene.continuity_from_prev and not any(marker in environment for marker in ("上一镜动作", "上一镜中", "刚才的动作")):
        return
    if not environment:
        return
    current_action = _normalized(scene.action_description)
    for clause in _clauses(previous.action_description):
        if len(_normalized(clause)) < 6 or not any(cue in clause for cue in _DYNAMIC_CUES):
            continue
        if _normalized(clause) in _normalized(environment) and _normalized(clause) not in current_action:
            actual, section = _prompt_excerpt(prompt, clause)
            issues.append(_issue(
                scene.scene_id,
                "cross_shot_dynamic_leak",
                "major",
                "当前镜头环境段复用了上一镜的动态动作事实",
                clause,
                actual,
                section or "环境",
                f"scene:{scene.scene_id}:cross-shot:{previous.scene_id}:dynamic",
            ))
            break


def _append_future_event_leak(issues: list[PromptFidelityIssue], scene: Scene, prompt: str, later_scenes: list[Scene]) -> None:
    current_action = _normalized(scene.action_description)
    if any(cue in scene.action_description for cue in ("闪回", "回忆", "往事", "插入旧照片")):
        return
    positive = _normalized(re.sub(r"(?:不得|禁止|不能|不应|不新增|不切镜头)[^。；;\n]*", "", str(prompt or "")))
    for future in later_scenes:
        candidates = list(_clauses(future.action_description))
        candidates.extend(str(getattr(line, "line", "") or "").strip() for line in (getattr(future, "dialogue", []) or []))
        for clause in candidates:
            normalized = _normalized(clause)
            if len(normalized) < 8 or normalized in current_action or not any(cue in clause for cue in _FUTURE_ACTION_CUES):
                continue
            if normalized in positive:
                actual, section = _prompt_excerpt(prompt, clause)
                issues.append(_issue(
                    scene.scene_id,
                    "future_event_leak",
                    "major",
                    "当前镜头提前出现了后续镜头的完整事件",
                    clause,
                    actual,
                    section or "动作时间轴",
                    f"scene:{scene.scene_id}:future:{future.scene_id}",
                ))
                return


def audit_prompt_fidelity(script: ScriptBreakdown, prompts: GenerationPrompts) -> PromptFidelityReport:
    scenes = {scene.scene_id: scene for scene in (script.scenes or [])}
    shots = {shot.scene_id: shot for shot in (prompts.shots or [])}
    issues: list[PromptFidelityIssue] = []
    dialogue_total = 0
    dialogue_covered = 0
    valid_assets = _visible_asset_ids(script)

    for diagnostic in (getattr(prompts, "assembly_diagnostics", []) or []):
        if getattr(diagnostic, "status", "") != "failed":
            continue
        scene_id = int(getattr(diagnostic, "scene_id", 0) or 0)
        conflicts = [
            str(item).strip()
            for item in (getattr(diagnostic, "forbidden_conflicts", []) or [])
            if str(item).strip()
        ]
        dropped = [
            str(item).strip()
            for item in (getattr(diagnostic, "dropped_facts", []) or [])
            if str(item).strip()
        ]
        actual = "；".join(conflicts or dropped) or "assembly_diagnostic.status=failed"
        issues.append(_issue(
            scene_id,
            "prompt_assembly_failed",
            "critical",
            "最终分镜提示词组装后仍存在未清除的环境动态事实或禁止冲突",
            "环境段只能包含静态环境事实",
            actual,
            "环境",
            f"scene:{scene_id}:assembly",
            blocks=True,
        ))

    scene_ids = list(scenes)
    prompt_ids = [int(getattr(item, "scene_id", 0) or 0) for item in (prompts.shots or [])]
    for ids, label in ((scene_ids, "结构化剧本"), (prompt_ids, "提示词")):
        if sorted(ids) != list(range(1, len(ids) + 1)):
            issues.append(_issue(
                0,
                "non_contiguous_scene_ids",
                "critical",
                f"{label}镜头编号不是从 1 开始的连续序列",
                "scene_id=1..N 且顺序连续",
                str(ids),
                "",
                f"project:{label}:scene_ids",
                blocks=True,
            ))

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
        action_for_tokens = re.sub(r"(?:门|窗|闸门)(?:始终|保持)关闭", "", action)
        action_section = _positive_prompt_evidence(prompt)
        _append_direction_conflicts(issues, scene, action_section)
        _append_timeline_overflow(issues, scene, shot, prompt)
        _append_character_state_conflict(issues, scene, prompt, script)
        previous_scene = scenes.get(scene_id - 1)
        _append_cross_shot_dynamic_leak(issues, scene, prompt, previous_scene)
        _append_future_event_leak(issues, scene, prompt, [item for item_id, item in scenes.items() if item_id > scene_id])
        for token in _action_tokens(action_for_tokens):
            if token not in action_section:
                issues.append(_issue(scene_id, "action_anchor_missing", "major", f"关键可观察动作“{token}”未在提示词中出现", token, action_section, "动作时间轴", f"scene:{scene_id}:action:{token}"))

        forbidden = any(term in action for term in ("门始终关闭", "不得出门", "不能出门", "只能通过监控观察"))
        forbidden_actions = [term for term in ("推门", "开门", "打开门", "走出", "冲出") if term in action_section]
        if forbidden and forbidden_actions:
            issues.append(_issue(scene_id, "forbidden_exit_action", "critical", "提示词加入了结构化剧本明确禁止的出门/开门动作", action, "；".join(forbidden_actions), "动作时间轴", f"scene:{scene_id}:negative:closed-door", blocks=True))

        for asset_id in re.findall(r"(?:@asset:|(?:asset_id|location_id|prop_id)[：:])([A-Za-z0-9_-]+)", prompt):
            if getattr(script, "asset_manifest", None) is not None and asset_id not in valid_assets:
                issues.append(_issue(scene_id, "invalid_asset_reference", "critical", "提示词引用了不存在的视觉资产", asset_id, asset_id, "关键道具", f"scene:{scene_id}:asset:{asset_id}", blocks=True))

    missing_scene_ids = sorted(set(scenes) - set(shots))
    for scene_id in missing_scene_ids:
        if not any(item.scene_id == scene_id and item.issue_code == "missing_prompt" for item in issues):
            scene = scenes[scene_id]
            issues.append(_issue(scene_id, "missing_prompt", "critical", "存在没有对应提示词的结构化镜头", scene.action_description, "", "", f"scene:{scene_id}:prompt", blocks=True))
    extra_scene_ids = sorted(set(shots) - set(scenes))
    for scene_id in extra_scene_ids:
        issues.append(_issue(
            scene_id,
            "scene_shot_mismatch",
            "critical",
            "分镜提示词引用了结构化剧本不存在的镜头编号",
            "合法镜头编号：" + "、".join(str(item) for item in sorted(scenes)) if scenes else "结构化剧本不包含该镜头",
            f"scene_id={scene_id}",
            "",
            f"scene:{scene_id}:shot",
            blocks=True,
        ))
    if len(prompts.shots) != len(shots):
        issues.append(_issue(0, "duplicate_shot_id", "critical", "提示词中存在重复镜头 ID", "每个 scene_id 只出现一次", str([shot.scene_id for shot in prompts.shots]), "", "prompt:shot_ids", blocks=True))

    status = "blocked" if any(item.blocks_generation for item in issues) else ("warning" if issues else "clean")
    return PromptFidelityReport(
        status=status, scene_count=len(scenes), shot_count=len(prompts.shots),
        dialogue_total=dialogue_total, dialogue_covered=dialogue_covered, issues=issues,
    )
