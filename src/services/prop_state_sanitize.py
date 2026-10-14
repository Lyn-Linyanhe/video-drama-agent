"""Strip action-process language from prop and continuity prompt sections."""

from __future__ import annotations

import re

_PROP_ACTION_PATTERNS = tuple(
    re.compile(pattern)
    for pattern in (
        r"捧起",
        r"入袋",
        r"放入",
        r"放进",
        r"塞进",
        r"塞入",
        r"取出",
        r"拿出",
        r"拿起",
        r"拿下",
        r"掏出",
        r"(?<![尚未没不未])打开",
        r"拆开",
        r"展开",
        r"撕开",
        r"递给",
        r"递向",
        r"贴胸",
        r"紧贴在",
        r"搂在",
        r"挑选",
        r"放钱",
        r"滑落",
        r"掉进",
        r"掉落",
        r"遇水",
        r"泡开",
        r"裂开",
        r"随后",
        r"之后",
        r"后来",
        r"最终",
        r"后期",
        r"后续",
    )
)

_CONTINUITY_ACTION_PATTERNS = _PROP_ACTION_PATTERNS + tuple(
    re.compile(pattern)
    for pattern in (
        r"推门",
        r"进屋",
        r"起身",
        r"站起来",
        r"跳起来",
        r"倒向",
        r"走向",
        r"走去",
        r"跑向",
    )
)

_CLAUSE_SPLIT = re.compile(r"[；;。！？!\n]+")
_SECTION_HEADERS = ("绑定道具", "关键道具", "顺承上一镜", "顺承")


def _clause_matches(clause: str, patterns: tuple[re.Pattern[str], ...]) -> bool:
    text = str(clause or "").strip()
    if not text:
        return False
    return any(pattern.search(text) for pattern in patterns)


def strip_action_process_clauses(
    text: str,
    *,
    patterns: tuple[re.Pattern[str], ...] = _PROP_ACTION_PATTERNS,
) -> str:
    """Keep appearance/position clauses; drop process/lifecycle clauses."""
    chunks: list[str] = []
    for raw in _CLAUSE_SPLIT.split(str(text or "")):
        clause = raw.strip(" 。；;，,、")
        if not clause or _clause_matches(clause, patterns):
            continue
        chunks.append(clause)
    return "；".join(dict.fromkeys(chunks))


def sanitize_prop_state(text: str) -> str:
    """Prop sections may describe look/place/holder, never the handling process."""
    return strip_action_process_clauses(text, patterns=_PROP_ACTION_PATTERNS)


def sanitize_end_state(text: str) -> str:
    """Continuity may carry the previous ending pose, not the previous action chain."""
    return strip_action_process_clauses(text, patterns=_CONTINUITY_ACTION_PATTERNS)


def sanitize_prompt_prop_sections(prompt: str) -> str:
    """Rewrite inherited prop/continuity sections in an assembled prompt."""
    source = str(prompt or "")
    for header in _SECTION_HEADERS:
        pattern = re.compile(rf"(?ms)^【{re.escape(header)}】\s*(.*?)(?=^【|\Z)")

        def _replace(match: re.Match[str], *, section: str = header) -> str:
            sanitizer = sanitize_end_state if section.startswith("顺承") else sanitize_prop_state
            body = sanitizer(match.group(1))
            if not body:
                return ""
            return f"【{section}】{body}\n"

        source = pattern.sub(_replace, source)
    return re.sub(r"\n{3,}", "\n\n", source).strip()
