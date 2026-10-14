"""Provider-aware video prompt composition."""

from __future__ import annotations

import re

from src.schemas.prompt import GenerationPrompts, PromptPolicy, ShotPrompt


PROVIDER_CAPABILITIES = {
    "kling": {"negative_prompt_mode": "separate"},
    "mock": {"negative_prompt_mode": "separate"},
    "happyhorse": {"negative_prompt_mode": "merged"},
    "seedance": {"negative_prompt_mode": "merged"},
    "seedence": {"negative_prompt_mode": "merged"},
}


def provider_capabilities(provider: str) -> dict[str, str]:
    return dict(PROVIDER_CAPABILITIES.get((provider or "").lower(), {"negative_prompt_mode": "merged"}))


def _join(parts: list[str]) -> str:
    return "\n".join(part.strip() for part in parts if part and part.strip())


_NON_EXECUTION_SECTION_TITLES = (
    "时间容量诊断",
    "容量诊断",
    "审核诊断",
    "审核信息",
    "规划说明",
)


def strip_non_execution_prompt_sections(text: str) -> str:
    """Remove saved planning/audit metadata from provider payloads only."""
    cleaned = str(text or "")
    for title in _NON_EXECUTION_SECTION_TITLES:
        cleaned = re.sub(
            rf"(?ms)^【{re.escape(title)}】.*?(?=^【[^】]+】|\Z)",
            "",
            cleaned,
        )
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _join_unique(parts: list[str]) -> str:
    """Keep both legacy and project policy constraints without repeated blocks."""
    seen: set[str] = set()
    unique: list[str] = []
    for part in parts:
        normalized = (part or "").strip()
        key = " ".join(normalized.split())
        if normalized and key not in seen:
            seen.add(key)
            unique.append(normalized)
    return _join(unique)


def has_planned_editing(text: str) -> bool:
    """Detect prompts that intentionally describe a timed multi-shot sequence."""
    normalized = (text or "").casefold()
    time_markers = re.findall(r"(?<!\d)\d+(?:\.\d+)?\s*(?:s|sec|secs|second|seconds|秒)", normalized)
    # The single-take template intentionally contains negative editorial words
    # such as “不切镜头/不插入”. Remove the complete negative clause before
    # detecting a real edit instruction, otherwise the generated guard itself
    # becomes a false positive on the second composition pass.
    normalized = re.sub(
        r"(?:不|不得|禁止)[^。；;\n]{0,40}(?:切镜头|插入|反打)[^。；;\n]*",
        "",
        normalized,
    )
    for negative_phrase in ("no cuts", "no insert", "no reverse shot"):
        normalized = normalized.replace(negative_phrase, "")
    has_edit_word = any(token in normalized for token in (
        "flashback", "insert shot", "cutaway", "montage",
        "闪回", "插入", "快速摇摄", "反打", "切到", "切回", "切镜头",
    ))
    shot_type_groups = (
        ("close-up", "close up", "closeup", "大特写", "特写", "近景"),
        ("two-shot", "two shot", "双人镜头", "双人"),
        ("wide shot", "远景", "全景"),
        ("medium shot", "中景"),
    )
    shot_type_count = sum(
        any(token in normalized for token in group)
        for group in shot_type_groups
    )
    # Multiple timestamps can describe ordered beats inside one continuous take.
    # Only explicit editorial language or multiple distinct shot types should
    # switch to planned-edit mode.
    return bool(time_markers and (has_edit_word or shot_type_count >= 2))


def video_structure_instruction(shot: ShotPrompt) -> str:
    text = shot.prompt or ""
    if has_planned_editing(text):
        return _join([
            "【计划剪辑规则】",
            "严格按照提示词中编写的带时间轴序列执行计划剪辑。",
            "只允许在提示词明确写出时长、闪回、插入、摇摄或镜头变化的位置切换。",
            "不得超出书面时间轴增加镜头、额外切出、新特写、新地点、新人物或新道具。",
            "剪辑过程中保持每段时长比例、人物身份、服装、道具状态、光线和雨势连续一致。",
        ])
    return _join([
        "【单镜头连续拍摄规则】",
        "将本镜头渲染为一个连续长镜头，使用统一机位连续完成所有按时间顺序列出的动作节拍。",
        "不得使用蒙太奇、正反打、切出、突然切换到其他角度、地点、特写、车辆插入或道具插入。",
        "如果分镜包含多个动作但没有明确的剪辑切换，保持同一机位，按原顺序清晰完成所有主要可见动作，不得静默跳过后续动作。",
    ])


def _video_quality_block(shot: ShotPrompt) -> str:
    """Add stable cinematic constraints without overwriting the authored shot."""
    text = (shot.prompt or "").casefold()
    blocks = [
        "【视频质量】",
        "电影级竖屏9:16镜头，自然光照，主体分离清晰，运动符合物理规律。",
        "整段视频保持人物身份、脸部比例、发型、服装颜色、场景布局、道具形状和道具状态一致。",
        video_structure_instruction(shot),
        "不得出现画面字幕、标志、水印或随机新增物体。",
    ]
    if any(token in text for token in ("dialogue", "speaks", "whispers", "conversation", "对话", "说", "低声", "独白")):
        blocks.append("对白/情绪镜头：限制镜头运动幅度，保持面部结构和嘴唇区域稳定。")
    if any(token in text for token in ("close-up", "closeup", "close shot", "特写", "近景", "脸部")):
        blocks.append("近距离构图：优先保持眼睛、嘴部、手部和服装边缘稳定，避免大幅运动。")
    if any(token in text for token in ("run", "chase", "fight", "push", "pull", "turn", "奔跑", "追逐", "打斗", "推", "拉", "转身")):
        blocks.append("动作镜头：明确起始和结束姿态，使动作清晰可读，避免运动拖影。")
    return "\n".join(blocks)


def _project_visual_style_block(profile) -> tuple[str, str]:
    """Return the canonical project medium constraints for a video request."""
    if profile is None:
        return "", ""
    from src.services.visual_styles import resolve_profile_style

    style = resolve_profile_style(profile)
    style_name = style["name"]
    positive = str(style.get("prompt_layers", {}).get("shot", "") or "").strip()
    negative = str(style.get("negative_prompt_layers", {}).get("shot", "") or "").strip()
    modifiers = [
        str(item).strip()
        for item in getattr(profile, "style_modifiers", []) or []
        if str(item).strip()
    ]
    modifier_text = f" 补充风格标签：{'、'.join(dict.fromkeys(modifiers))}。" if modifiers else ""
    # 风格层文本自带句号时不再补一个，避免"光影语言。。"式双句号。
    positive = positive.rstrip("。.")
    return (
        f"【项目视觉媒介】{style_name}：{positive}。{modifier_text}",
        negative,
    )


def compose_video_prompt(
    provider: str,
    prompts: GenerationPrompts,
    shot: ShotPrompt,
    policy: PromptPolicy | None = None,
    creative_profile=None,
) -> dict[str, str]:
    """Return the exact provider payload and its auditable composition metadata."""
    policy = policy or PromptPolicy()
    style_block, style_negative = _project_visual_style_block(creative_profile)
    executable_shot = shot.model_copy(update={
        "prompt": strip_non_execution_prompt_sections(shot.prompt),
    })
    effective_prompt = _join_unique([
        policy.style_prefix,
        style_block,
        executable_shot.prompt,
        _video_quality_block(executable_shot),
    ])
    effective_negative = _join_unique([
        policy.global_negative_prompt,
        prompts.global_negative_prompt,
        shot.negative_prompt,
        style_negative,
    ])
    mode = provider_capabilities(provider)["negative_prompt_mode"]
    provider_negative = effective_negative
    if mode == "merged" and effective_negative:
        effective_prompt = _join([effective_prompt, f"【避免出现】{effective_negative}"])
        provider_negative = ""
    return {
        "provider": provider,
        "effective_prompt": effective_prompt,
        "effective_negative_prompt": effective_negative,
        "provider_prompt": effective_prompt,
        "provider_negative_prompt": provider_negative,
        "negative_prompt_mode": mode,
    }
