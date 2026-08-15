"""Canonical prompt composition for versioned visual assets.

The returned provider fields are the only prompts image generation may submit. This
keeps the editor preview, version history, and provider request auditable.
"""

from __future__ import annotations

import re
from typing import Any


# Bump this whenever the canonical scene identity/state/view contract changes.
# Existing versions remain readable, while newly generated assets are auditable.
SCENE_PROMPT_TEMPLATE_VERSION = 10
PROP_PROMPT_TEMPLATE_VERSION = 3


def _join(parts: list[str]) -> str:
    return "\n".join(part.strip() for part in parts if part and part.strip())


def _join_unique(parts: list[str]) -> str:
    """Join prompt layers once, preserving order and exact line content.

    LLM-authored manifests frequently copy the same environment paragraph into
    ``identity_description``, ``fixed_dressing`` and ``environment_description``.
    Repeating it three times biases the image model without adding information.
    Only exact normalized duplicates are removed; distinct clauses remain
    untouched for backwards compatibility.
    """
    seen: set[str] = set()
    unique: list[str] = []
    for part in parts:
        value = str(part or "").strip()
        key = " ".join(value.split())
        if not key or key in seen:
            continue
        seen.add(key)
        unique.append(value)
    return _join(unique)


_SCENE_SIGN_PATTERNS = (
    (r"(?:旧钟楼维修|工程|旧工程)?标识牌", "空白结构板（仅作为空间锚点，不得出现文字或符号）"),
    (r"工程牌", "空白结构板（仅作为空间锚点，不得出现文字或符号）"),
    (r"(?:广告牌|招牌|门牌|路牌|标牌)", "空白结构板（仅作为空间锚点，不得出现文字或符号）"),
)


def _sanitize_scene_anchor_text(value: Any) -> str:
    """Remove prompt contradictions that invite the model to render signage.

    Empty scene references deliberately reject text and signs. LLM manifests can
    still copy a script phrase such as ``维修工程标识牌`` into the location
    identity, so preserve the physical anchor while making its blank appearance
    explicit before composing the provider prompt.
    """
    text = str(value or "").strip()
    for pattern, replacement in _SCENE_SIGN_PATTERNS:
        text = re.sub(pattern, replacement, text)
    return text


def scene_environment_key_elements(scene_visual: Any, script: Any = None, scene_id: int | None = None) -> list[str]:
    """Keep only set facts when importing LLM visual key elements.

    ``SceneVisual.key_elements`` is also used by the storyboard prompt and
    older providers sometimes put a complete action sentence there.  Scene
    reference images are empty sets, so visible character names, transient
    props, and motion/dialogue cues must never be forwarded as environment
    requirements.  Unclassified static details are retained for compatibility.
    """
    values = getattr(scene_visual, "key_elements", []) if scene_visual is not None else []
    if not values:
        return []
    character_names: set[str] = set()
    scene = None
    manifest = getattr(getattr(script, "asset_manifest", None), "characters", None) if script is not None else None
    if manifest:
        character_names.update(
            str(name).strip()
            for item in manifest
            for name in (getattr(item, "name", ""), *(getattr(item, "aliases", []) or []))
            if str(name).strip()
        )
    if script is not None:
        scene = next(
            (item for item in (getattr(script, "scenes", []) or []) if item.scene_id == scene_id),
            None,
        )
        character_names.update(
            str(name).strip()
            for name in (getattr(scene, "characters", []) or [])
            if str(name).strip()
        )
    prop_names: set[str] = set()
    props = getattr(getattr(script, "asset_manifest", None), "props", None) if script is not None else None
    if props:
        prop_names.update(
            str(name).strip()
            for item in props
            for name in (getattr(item, "name", ""), *(getattr(item, "aliases", []) or []))
            if str(name).strip()
        )
    blocked_cues = {
        "站在", "坐在", "躺在", "蹲在", "正在", "拿着", "拿起", "握住", "握紧", "手持",
        "推开", "打开", "关上", "走进", "走入", "进入", "跑向", "冲向", "后退", "靠近",
        "看向", "回头", "抬头", "低头", "转身", "说", "说道", "对话", "低声", "喊道",
    }
    try:
        from src.utils.visual_asset_manifest import DYNAMIC_ENVIRONMENT_CUES, TRANSIENT_ENVIRONMENT_TERMS

        blocked_cues.update(DYNAMIC_ENVIRONMENT_CUES)
        transient_terms = set(TRANSIENT_ENVIRONMENT_TERMS)
    except ImportError:
        transient_terms = set()
    transient_terms.update(prop_names)

    kept: list[str] = []
    for raw in values:
        value = str(raw or "").strip()
        if not value:
            continue
        if any(_contains_named_entity(value, name) for name in character_names):
            continue
        if any(cue and cue in value for cue in blocked_cues):
            continue
        if any(_contains_named_entity(value, term) for term in transient_terms):
            continue
        if value not in kept:
            kept.append(value)
    return kept


def project_style_layers(state: Any, asset_type: str) -> tuple[str, str]:
    """Return the canonical positive/negative style constraints for an asset.

    Every visual asset uses this helper so a project style selected in the UI is
    represented in both the preview prompt and the provider payload.  Legacy
    ``art_style`` values are resolved by the registry and remain compatibility
    text only.
    """
    from src.services.visual_styles import resolve_profile_style

    profile = getattr(state, "creative_profile", None)
    style = resolve_profile_style(profile) if profile is not None else resolve_profile_style(type("Profile", (), {})())
    key = asset_type if asset_type in {"character", "scene", "prop", "shot"} else "shot"
    positive = str(style.get("prompt_layers", {}).get(key, "") or "").strip()
    negative = str(style.get("negative_prompt_layers", {}).get(key, "") or "").strip()
    modifiers = [str(item).strip() for item in getattr(profile, "style_modifiers", []) or [] if str(item).strip()]
    if modifiers:
        positive = _join([positive, "项目补充风格标签：" + "、".join(dict.fromkeys(modifiers)) + "；补充标签不得改变主体身份、空间结构和镜头方向。"])
    return positive, negative


_SPATIAL_NEGATION_RE = re.compile(
    r"(?:不得|禁止|严禁|不要|别|避免|不能|不可|不应|不是|并非|无|未|非|不)[^。；，,!?！？\n]{0,10}$|"
    r"(?:no|not|without|avoid|never)(?:\s+[^,.;!?]{0,24})?$",
    re.IGNORECASE,
)


def _has_positive_term(text: Any, terms: tuple[str, ...] | list[str] | set[str]) -> bool:
    """Return whether at least one spatial cue is asserted rather than negated.

    Scene manifests often repeat both positive and negative examples (for
    example, ``室内急诊室；不要出现街道外立面``). A plain substring check
    would classify that as both indoor and outdoor. Looking at a short prefix
    keeps the rule deterministic while preserving ordinary narrative wording.
    """
    folded = str(text or "").casefold()
    for raw_term in terms:
        term = str(raw_term or "").strip().casefold()
        if not term:
            continue
        start = 0
        while True:
            index = folded.find(term, start)
            if index < 0:
                break
            prefix = folded[max(0, index - 14):index]
            if not _SPATIAL_NEGATION_RE.search(prefix):
                return True
            start = index + len(term)
    return False


def _contains_named_entity(value: Any, name: Any) -> bool:
    """Match a character name without deleting it from a longer place word.

    One-character names are common in Chinese scripts. ``林`` in ``林荫道`` is
    an environment noun, not a visible character reference; only an occurrence
    with a non-CJK boundary is treated as the entity. Longer names retain the
    historical exact-substring behaviour for compatibility.
    """
    text = str(value or "")
    needle = str(name or "").strip()
    if not needle or needle not in text:
        return False
    if len(needle) != 1:
        return True
    if needle.isascii() and needle.isalnum():
        return bool(re.search(
            rf"(?<![A-Za-z0-9_]){re.escape(needle)}(?![A-Za-z0-9_])",
            text,
            flags=re.IGNORECASE,
        ))
    index = text.find(needle)
    while index >= 0:
        before = text[index - 1] if index else ""
        after_index = index + len(needle)
        after = text[after_index] if after_index < len(text) else ""
        if not ("\u3400" <= before <= "\u9fff" or "\u3400" <= after <= "\u9fff"):
            return True
        index = text.find(needle, index + 1)
    return False


def _scene_scale_lock(location_name: str, identity_text: str, environment_text: str, state_text: str) -> str:
    location_text = str(location_name or "").casefold()
    identity_source = " ".join(
        part for part in (str(identity_text or ""), str(environment_text or ""))
        if part.strip()
    ).casefold()
    state_source = str(state_text or "").casefold()
    source_text = f"{location_text} {identity_source} {state_source}".strip()
    # Legacy manifests are not guaranteed to be Chinese.  Normalize common
    # English spatial cues before applying the existing Chinese contract so a
    # new screenplay cannot silently fall back to a neutral crop.
    english_outdoor = (
        "street", "outdoor", "outside", "exterior", "road", "alley", "plaza",
        "rooftop", "parking lot", "courtyard", "entrance",
    )
    english_indoor = (
        "indoor", "inside", "interior", "room", "hall", "hospital", "library",
        "office", "kitchen", "bedroom", "basement", "corridor",
    )
    english_underground = ("underground", "subway", "basement entrance", "tunnel")
    has_english_outdoor = _has_positive_term(source_text, english_outdoor)
    has_english_indoor = _has_positive_term(source_text, english_indoor)
    has_english_underground = _has_positive_term(source_text, english_underground)
    outdoor_tokens = (
        "入口外部", "建筑外部", "店外", "室外", "户外", "门前", "街巷", "街道", "街角", "街景", "外立面",
    )
    explicitly_outdoor_identity = has_english_outdoor or _has_positive_term(location_text, outdoor_tokens) or _has_positive_term(
        identity_source, ("入口外部", "建筑外部", "室外", "户外", "门前", "外立面")
    )
    explicitly_indoor = has_english_indoor or _has_positive_term(source_text, (
        "内部", "室内", "店内", "客厅", "卧室", "厨房", "浴室", "卫生间", "走廊", "大厅", "房间", "包间", "病房",
        "书房", "教室", "会议室", "地下室", "地下通道", "医院", "急诊室", "候诊室", "后厨", "仓库", "车库", "电梯", "车厢", "实验室", "档案室",
    ))
    underground_entry = has_english_underground or _has_positive_term(source_text, (
        "地下通道", "地下入口", "地下室入口", "铁门", "石阶", "门洞", "向下延伸",
    ))
    if explicitly_indoor and not explicitly_outdoor_identity:
        spatial_scope = "完整房间轮廓、墙体、门窗、家具和前中后景关系"
    elif explicitly_outdoor_identity:
        if underground_entry:
            spatial_scope = (
                "完整街巷入口边界、旧砖墙、门洞或铁门、通向内部的石阶或通道、地面与周边街巷；"
                "镜头必须位于室外街巷地面，向铁门拍摄，禁止从室内向外拍摄"
            )
        else:
            spatial_scope = "临街建筑外立面、门头、橱窗、屋檐和周边街景"
    # A venue name alone is not evidence of an exterior.  Keep ambiguous
    # locations neutral; explicit outdoor cues are handled above.
    else:
        spatial_scope = "完整可复用空间边界、主要结构和周边环境关系"
    return (
        "空间尺度锁：必须呈现真实"
        f"{spatial_scope}，保留足够大的地点外壳和空间纵深，不得把地点压缩成电话亭、岗亭、小盒子、"
        "单门洞、单窗洞或局部裁切。"
    )


def _scene_prompt(base_prompt: str, custom_instructions: str) -> str:
    if not custom_instructions.strip():
        return base_prompt.strip()
    return _join([
        base_prompt,
        "用户补充要求："
        f"{custom_instructions.strip()}。补充要求不得改变场景空间结构、固定陈设、空镜构图、"
        "横向16:9构图，以及禁止人物、文字、水印和 UI 元素的限制。",
    ])


def _negative_prompt_mode(state: Any, provider: str | None = None) -> str:
    provider = str(
        provider
        or (getattr(state, "image_generation_policy", {}) or {}).get("provider")
        or ""
    ).lower()
    return "merged" if provider == "wan" else "separate"


def _provider_payloads(
    state: Any,
    prompts: dict[str, str],
    negative_prompt: str,
    provider: str | None = None,
) -> tuple[dict[str, str], str, str]:
    """Build the exact provider payload using the task's effective provider.

    A one-shot override must affect negative-prompt handling just like the
    project default.  Otherwise a Wan task can receive a separate negative
    field (which it ignores), or a Kling task can receive merged text.
    """
    mode = _negative_prompt_mode(state, provider)
    if mode == "merged" and negative_prompt.strip():
        return (
            {key: _join([prompt, f"避免出现：{negative_prompt.strip()}"]) for key, prompt in prompts.items()},
            "",
            mode,
        )
    return prompts, negative_prompt, mode


def _reference_lock(asset_type: str, art_style: str = "") -> str:
    common = (
        "参考锁定：本图将作为严格的视频参考素材重复使用。优先保持身份和结构稳定，不追求夸张构图。"
        "不得擅自生成替代版本、额外主体、文字、标识、标签、边框、分屏或 UI 元素。"
    )
    if asset_type == "character":
        from src.agents.image_gen_agent import is_anime_art_style

        if is_anime_art_style(art_style):
            return (
                common
                + "锁定一个动漫角色身份：保持相同脸型设计、年龄感、发际线、发型、人体比例、服装轮廓、"
                "面料颜色、鞋子和辨识性特征，并保持相同线稿与上色语言。"
            )
        return (
            common
            + "锁定一个真人演员身份：保持相同脸部几何、年龄感、发际线、发型、人体比例、服装轮廓、"
            "面料纹理、颜色、鞋子和辨识性特征。"
        )
    if asset_type == "scene":
        return (
            common
            + "锁定一个可复用地点：保持相同的完整房间或建筑外壳、平面布局、镜头轴线、门窗、家具、"
            "锚点物件、材质、天气、时间段、主光方向和色彩方案。不得将地点缩成柜台、小亭、电话亭、盒子或裁切的门口；"
            "只能生成空场景。"
        )
    return (
        common
        + "锁定一个可复用道具：保持相同轮廓、尺寸、材质、颜色、磨损痕迹、可动部件和可见状态。"
        "只能出现单件道具，主体居中且完整可见。"
    )


def _locked_project_constraint(state: Any, asset_type: str) -> str:
    # ``art_style`` is a legacy compatibility label and can remain at its old
    # default after a project selects a stable visual-style ID.  Prompt
    # generation must use the canonical registry entry, otherwise a project
    # selected as anime can receive a contradictory live-action lock.
    from src.services.visual_styles import resolve_profile_style

    profile = getattr(state, "creative_profile", None)
    style = resolve_profile_style(profile)["name"] if profile is not None else "电影级真人写实"
    style_positive, _style_negative = project_style_layers(state, asset_type)
    return _join([_project_constraint(state, asset_type), style_positive, _reference_lock(asset_type, style)])


def scene_group_view_count(state: Any, asset_id: str, requested: int = 0) -> int:
    from src.services.scene_view_requirements import derive_scene_view_requirements

    return len(derive_scene_view_requirements(state, asset_id).views)


SCENE_VIEW_DESCRIPTIONS = {
    "scene_center": "正面中心线大全景，镜头正对空间中心轴，严格居中对称，展示前景、中景、后景和主要空间锚点。",
    "scene_left_45": "以中心线为参考的左前方45度大全景，保持同一空间结构，只改变机位到左前侧观察。",
    "scene_right_45": "以中心线为参考的右前方45度大全景，保持同一空间结构，只改变机位到右前侧观察。",
    "scene_reverse": "从空间最深处向外拍摄的反向中心大全景，展示出口方向和反向空间关系。",
    # Compatibility aliases for old projects.  They deliberately use separate
    # contracts so a legacy medium image cannot contain both a wide-establishing
    # and a medium-detail instruction.
    "scene_wide": "横向大全景建立镜头，完整展示空间边界、前中后景和主要固定结构。",
    "scene_medium": "横向中景细节镜头，聚焦核心区域、材质与固定陈设，同时保留可辨识的空间关系。",
}


def _scene_location_context(state: Any, location_id: str, state_id: str = ""):
    from src.utils.visual_asset_manifest import stable_location_id

    manifest = getattr(getattr(state, "script", None), "asset_manifest", None)
    location_asset = next((item for item in (getattr(manifest, "locations", []) or []) if item.location_id == location_id), None)
    if location_asset is None:
        candidates = getattr(manifest, "locations", []) or []
        location_asset = next(
            (
                item for item in candidates
                if stable_location_id(getattr(item, "canonical_name", "")) == location_id
                or location_id in (getattr(item, "aliases", []) or [])
            ),
            None,
        )
    scenes = getattr(getattr(state, "script", None), "scenes", []) or []
    resolved_state_id = state_id or getattr(location_asset, "default_state_id", "") if location_asset else state_id
    location_state = next(
        (item for item in getattr(location_asset, "states", []) if item.state_id == resolved_state_id),
        None,
    ) if location_asset else None
    scene = next(
        (
            item for item in scenes
            if item.scene_id == (getattr(location_state, "representative_scene_id", 0) or getattr(location_asset, "representative_scene_id", 0))
        ),
        None,
    )
    if scene is None:
        scene = next(
            (
                item for item in scenes
                if stable_location_id(getattr(item, "location", "")) == location_id
                or getattr(item, "location", "") == location_id
            ),
            None,
        )
    return location_asset, location_state, scene, resolved_state_id


def _location_reference_text(value: Any, scenes: list[Any]) -> str:
    """Return reusable location text without transient shot facts.

    Loading a project repairs the persisted manifest, but prompt composition is
    also a boundary for legacy callers that construct a state in memory or
    carry an older manifest snapshot. Reuse the canonical manifest cleaner here
    so stale actions cannot reach an image provider even before a reload.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    from src.utils.visual_asset_manifest import (
        TRANSIENT_ENVIRONMENT_TERMS,
        _clean_environment_description,
        _has_voice_or_action_cue,
    )

    has_dynamic_cue = _has_voice_or_action_cue(text)
    has_transient_term = any(
        term and term.casefold() in text.casefold()
        for term in TRANSIENT_ENVIRONMENT_TERMS
    )
    cleaned = _clean_environment_description(text, scenes)
    if cleaned and not has_dynamic_cue and not has_transient_term:
        # Preserve punctuation and detailed custom wording when the value is
        # already static. This keeps legacy prompt deduplication stable.
        return text
    if cleaned:
        return cleaned
    # Preserve detailed custom/English identity text when it is not visibly
    # narrative, but never fall back to a known action, virtual content, or
    # transient prop clause.
    if has_dynamic_cue or has_transient_term:
        return ""
    return text


def _location_scenes(location_asset: Any, scene: Any, script: Any = None) -> list[Any]:
    scenes = list(getattr(getattr(script, "script", None), "scenes", []) or []) if script is not None else []
    if script is not None and not scenes:
        scenes = list(getattr(script, "scenes", []) or [])
    shot_ids = set(getattr(location_asset, "shot_ids", []) or [])
    selected = [item for item in scenes if getattr(item, "scene_id", None) in shot_ids]
    if selected:
        return selected
    return [scene] if scene is not None else []


def _non_action_environment(location_asset: Any, scene: Any, script: Any = None) -> str:
    """Return reusable environment text, excluding copied shot actions."""
    environment = str(getattr(location_asset, "environment_description", "") or "").strip()
    action = str(getattr(scene, "action_description", "") or "").strip()
    if environment and action and environment == action:
        return ""
    return _location_reference_text(environment, _location_scenes(location_asset, scene, script))


def build_scene_prompt_layers(
    state: Any,
    location_id: str,
    state_id: str = "",
    view_type: str = "scene_center",
    custom_instructions: str = "",
) -> dict[str, Any]:
    """Build auditable scene prompt layers for one location state and view."""
    location_asset, location_state, scene, resolved_state_id = _scene_location_context(state, location_id, state_id)
    location_scenes = _location_scenes(location_asset, scene, state)
    location_name = (
        getattr(location_asset, "canonical_name", "")
        or getattr(scene, "location", "")
        or location_id
    )
    identity_parts = [
        _sanitize_scene_anchor_text(f"地点身份：{location_name}"),
        _sanitize_scene_anchor_text(_location_reference_text(getattr(location_asset, "identity_description", ""), location_scenes)),
        _sanitize_scene_anchor_text(_non_action_environment(location_asset, scene, state)),
        _sanitize_scene_anchor_text(_location_reference_text(getattr(location_asset, "fixed_dressing", ""), location_scenes)),
        _sanitize_scene_anchor_text(
            "空间锚点：" + "、".join(
                cleaned
                for anchor in (getattr(location_asset, "anchor_points", []) or [])
                for cleaned in [_location_reference_text(anchor, location_scenes)]
                if cleaned
            )
        ),
    ]
    identity = _join_unique(identity_parts)
    if not identity.strip():
        identity = f"地点身份：{location_id}，以剧本设定的固定空间结构和陈设为准。"

    visible_state_notes = str(getattr(location_state, "visible_state_notes", "") or "").strip()
    shot_action = str(getattr(scene, "action_description", "") or "").strip()
    if visible_state_notes and shot_action and visible_state_notes == shot_action:
        visible_state_notes = ""
    state_label = str(getattr(location_state, "label", "") or "").strip()
    state_facts = [
        getattr(location_state, "time_condition", "") or getattr(location_asset, "time_condition", ""),
        getattr(location_state, "weather", ""),
        getattr(location_state, "light_direction", ""),
        visible_state_notes,
    ]
    if state_label in {"默认状态", "default", "default state"}:
        state_label = ""
    state_parts = [
        state_label,
        *state_facts,
    ]
    state_layer = _join([f"场景状态：{part}" for part in state_parts if str(part).strip()])
    if not state_layer:
        state_layer = "场景状态：以剧本当前镜头的时间、天气、主光方向和可见临时状态为准。"
    scale_lock = _scene_scale_lock(
        location_name,
        getattr(location_asset, "identity_description", ""),
        _non_action_environment(location_asset, scene, state),
        state_layer,
    )

    view_layer = f"视角要求：{SCENE_VIEW_DESCRIPTIONS.get(view_type, SCENE_VIEW_DESCRIPTIONS['scene_center'])}"
    style_positive, style_negative = project_style_layers(state, "scene")
    from src.agents.image_gen_agent import is_anime_art_style

    medium_constraint = (
        "二维背景绘制媒介，稳定透视，层次清晰，光影自然，构图稳定。"
        if is_anime_art_style(style_positive)
        else "专业影视摄影媒介，真实材质，层次清晰，光影自然，构图稳定。"
    )
    constraints = (
        "硬性限制：不得出现任何人物、人物影子、人物倒影、手脚、面部、文字、水印、标签、UI、拼图、分屏、边框；"
        "不得出现标牌、门牌、路牌、广告牌、招牌或任何可读/伪文字；"
        "不得改变地点身份、空间比例、固定陈设、材质、天气、时间段和主光方向；"
        f"横向16:9空镜参考图，{medium_constraint}深景深，结构清晰。"
    )
    custom = (
        f"用户补充要求：{custom_instructions.strip()}。补充要求只可增加可见细节，不得删除或覆盖身份、状态、视角和硬性限制。"
        if custom_instructions.strip()
        else ""
    )
    # Keep the human-readable style name in the effective prompt as well as the
    # medium-specific layer.  The explicit sentence is important for providers
    # that otherwise interpret a cinematic lighting description as live-action.
    project_constraint = _project_constraint(state, "scene")
    effective = _join([identity, state_layer, scale_lock, project_constraint, style_positive, view_layer, constraints, custom])
    negative = _join(["人物，人群，人影，人物倒影，手，脚，人脸，文字，伪文字，标牌，门牌，路牌，广告牌，招牌，水印，标签，UI元素，拼贴画，分屏，边框，透视畸变，鱼眼，模糊，低清晰度", style_negative])
    return {
        "identity": identity,
        "state": state_layer,
        "scale": scale_lock,
        "view": view_layer,
        "style": style_positive,
        "constraints": constraints,
        "custom": custom,
        "effective_prompt": effective,
        "negative_prompt": negative,
        "width": 1920,
        "height": 1080,
        "prompt_template_version": SCENE_PROMPT_TEMPLATE_VERSION,
        "state_id": resolved_state_id,
        "view_type": view_type,
    }


def build_scene_group_prompt(
    state: Any,
    asset_id: str,
    custom_instructions: str = "",
    requested_count: int = 0,
    state_id: str = "",
    view_types: tuple[str, ...] = (),
) -> tuple[str, int]:
    """Canonical provider prompt for a coherent empty-location reference group."""
    manifest = getattr(getattr(state, "script", None), "asset_manifest", None)
    location_asset = next((item for item in (getattr(manifest, "locations", []) or []) if item.location_id == asset_id), None)
    scene = next((item for item in (getattr(getattr(state, "script", None), "scenes", []) or []) if item.scene_id == getattr(location_asset, "representative_scene_id", 0)), None)
    location = getattr(location_asset, "canonical_name", "") or getattr(scene, "location", "") or asset_id
    environment = _non_action_environment(location_asset, scene, state) or "以场景身份、固定陈设与项目设定为准"
    time_condition = getattr(location_asset, "time_condition", "") or "以剧本设定为准"
    visual = getattr(state, "visual_design", None)
    profile = getattr(state, "creative_profile", None)
    from src.services.visual_styles import resolve_profile_style

    style = (
        resolve_profile_style(profile)["name"]
        if profile is not None
        else getattr(visual, "overall_style", "") or "电影级真人写实"
    )
    style_positive, style_negative = project_style_layers(state, "scene")
    palette = getattr(visual, "color_palette", "") or "以项目视觉方案为准"
    count = len(view_types) if view_types else scene_group_view_count(state, asset_id, requested_count)
    if view_types:
        view_ids = view_types
    else:
        from src.services.scene_view_requirements import derive_scene_view_requirements

        view_ids = derive_scene_view_requirements(state, asset_id, state_id).views
    if view_ids:
        layers = [build_scene_prompt_layers(state, asset_id, state_id, view_id, custom_instructions) for view_id in view_ids]
        shared_identity = layers[0]["identity"]
        shared_state = layers[0]["state"]
        shared_scale = layers[0]["scale"]
        view_lines = "\n".join(
            f"第{index}张：{layer['view']}"
            for index, layer in enumerate(layers, start=1)
        )
        prompt = _join([
            f"高质量专业场景参考组图。本次连续生成{len(view_ids)}张独立的16:9横向空镜图，它们必须属于同一地点、同一状态，不是拼贴画。",
            shared_identity,
            shared_state,
            shared_scale,
            "固定视图顺序：",
            view_lines,
            layers[0]["constraints"],
            _project_constraint(state, "scene"),
            style_positive,
            f"风格反向约束：避免出现{style_negative}。" if style_negative else "",
            _reference_lock("scene"),
            layers[0]["custom"],
        ])
        return prompt, len(view_ids)
    view_labels = {
        "scene_center": "正面中心线大全景，镜头正对空间中心轴，严格居中对称。",
        "scene_left_45": "以中心线为参考的左前方45度大全景，镜头对准核心区域。",
        "scene_right_45": "以中心线为参考的右前方45度大全景，镜头对准核心区域。",
        "scene_reverse": "从空间最深处向外拍摄的正中心全景。",
    }
    views = [f"第{index}张：{view_labels[view_id]}" for index, view_id in enumerate(view_ids, start=1)]
    extra = f"\n用户补充要求：{custom_instructions.strip()}。补充要求不得改变机位顺序、空间结构、固定陈设、时间天气或主光方向。" if custom_instructions.strip() else ""
    from src.agents.image_gen_agent import is_anime_art_style

    medium_text = "二维背景绘制，结构清晰，光影自然，深景深" if is_anime_art_style(style) else "专业影视摄影，真实材质，光影自然，深景深"
    return f"""高质量专业场景设定组图。本次连续生成{count}张独立的16:9横向大全景图，它们必须是同一地点的关联参考组，不是拼贴画，不要边框、编号、标签或文字。地点：{location}。环境设定：{environment}。时间与天气：{time_condition}。视觉风格：{style}；色彩与光影：{palette}。
项目视觉风格：{style}。
项目风格执行层：{style_positive}
风格反向约束：避免出现{style_negative}。
固定视图顺序：{' '.join(views)}
所有视图必须保持同一空间结构、建筑比例、主体位置、材质、色彩、天气、时间段、光源方向和美术风格；完整展示前中后景、顶面与地面的空间层次，环境清晰、细节丰富、{medium_text}、超清画质。只生成无人物空场景：不得出现人物、人群、背影、剪影、人脸、手脚、人物倒影、人物影子、屏幕或镜中人物、剧情事件或人物活动。禁止把各视图画成不同地点；禁止倾斜构图、鱼眼、广角畸变、透视扭曲、极端俯仰视角、模糊、浅景深、低画质、文字、水印、签名、边框、标签和UI元素。""" + extra, count


def _project_constraint(state: Any, asset_type: str) -> str:
    profile = getattr(state, "creative_profile", None)
    from src.services.visual_styles import resolve_profile_style

    style_entry = resolve_profile_style(profile) if profile is not None else {"name": "电影级真人写实"}
    canonical_style = style_entry["name"]
    legacy_style = str(getattr(profile, "art_style", "") or "").strip() if profile is not None else ""
    generic_legacy_styles = {
        "写实", "电影级写实", "电影级真人写实", "真人",
        "动漫", "动漫风", "二次元", "漫画", "动画",
        canonical_style,
    }
    legacy_supplement = (
        f"兼容风格补充：{legacy_style}。"
        if legacy_style and legacy_style not in generic_legacy_styles
        else ""
    )
    ratio = getattr(profile, "video_ratio", "") or "9:16"
    if asset_type == "scene":
        return (
            f"项目视觉约束：艺术风格必须为{canonical_style}。{legacy_supplement}"
            "本场景参考图固定使用横向16:9构图与1920×1080基准，不继承最终视频画幅，"
            "最终视频画幅设置只供后续视频镜头使用。"
        )
    if asset_type == "character":
        constraint = _character_project_constraint(canonical_style, ratio)
        return f"{constraint} {legacy_supplement}".strip()
    return (
        f"项目视觉约束：艺术风格必须为{canonical_style}。{legacy_supplement}"
        "本图是单张独立道具参考图，保持当前视图的单图构图与道具身份一致性，不得改成多视图拼图，"
        "不继承最终视频画幅。"
    )


def _character_project_constraint(style: str, ratio: str) -> str:
    from src.agents.image_gen_agent import is_anime_art_style

    if is_anime_art_style(style):
        return (
            f"项目视觉约束：艺术风格必须为{style}；本图是动画角色一致性参考图，必须继承该动漫/二次元风格，"
            "保持统一角色脸型、发型、服装和线稿上色，不得改成真人照片、真人演员或摄影写实画面。"
            f"保持当前单视图固定比例，不改为{ratio}视频画幅。"
        )
    return (
        f"项目视觉约束：艺术风格必须为{style}；本图是角色一致性参考图，必须使用真人写实摄影风格，"
        "必须像真实演员在摄影棚拍摄的定妆参考照，禁止任何绘画化、角色原画化、虚拟偶像化、数字人化或夸张美术化处理。"
        f"保持当前单视图固定比例，不改为{ratio}视频画幅。"
        "真人硬性约束：真实演员照片、真人实拍定妆参考、写实影棚摄影、真实皮肤毛孔、真实服装面料和中性影棚灯光。"
    )


def compose_asset_prompt(
    state: Any,
    asset_type: str,
    asset_id: str,
    view_type: str,
    asset: Any,
    custom_instructions: str | None = None,
    provider: str | None = None,
) -> dict[str, Any]:
    """Compose the preview and exact provider payload for one asset view."""
    saved_custom = str(getattr(asset, "custom_instructions", "") or "")
    custom = saved_custom if custom_instructions is None else custom_instructions.strip()
    style_mode = ""
    style_diagnostics: dict[str, object] = {}
    character_description = ""

    if asset_type == "character":
        from src.agents.image_gen_agent import (
            CHARACTER_PROMPT_TEMPLATE_VERSION,
            build_character_image_prompt,
            build_character_negative_prompt,
            character_style_mode,
            diagnose_character_prompt,
            character_view_spec,
        )
        from src.api.context import _character_description

        _, width, height, _, _ = character_view_spec(view_type)
        from src.services.visual_styles import resolve_profile_style

        profile = getattr(state, "creative_profile", None)
        style = resolve_profile_style(profile)["name"] if profile is not None else "电影级真人写实"
        style_mode = character_style_mode(style)
        character_description = _character_description(state, asset_id)
        base = _join([build_character_image_prompt(asset_id, character_description, view_type, art_style=style), _locked_project_constraint(state, asset_type)])
        effective = _join([build_character_image_prompt(
            asset_id, character_description, view_type, custom, art_style=style,
        ), _locked_project_constraint(state, asset_type)])
        negative = build_character_negative_prompt(view_type, art_style=style)
        style_diagnostics = diagnose_character_prompt(style, effective, negative)
        provider_prompts: dict[str, str] = {view_type: effective}
        template_version = CHARACTER_PROMPT_TEMPLATE_VERSION
    elif asset_type == "prop":
        from src.agents.image_gen_agent import (
            PROP_IMAGE_HEIGHT,
            PROP_IMAGE_WIDTH,
            PROP_NEGATIVE_PROMPT,
            PROP_VIEWS,
            build_prop_image_prompt,
            build_prop_view_prompt,
        )

        prop = next(
            (item for item in (state.script.asset_manifest.props if state.script and state.script.asset_manifest else []) if item.prop_id == asset_id),
            None,
        )
        if prop is None:
            raise ValueError("Prop asset not found in the visual manifest")
        from src.services.visual_styles import resolve_profile_style

        prop_style = resolve_profile_style(getattr(state, "creative_profile", None))[
            "name"
        ]
        if view_type == "standard_views":
            base = _join([
                build_prop_image_prompt(prop.name, prop.description, prop_style),
                _locked_project_constraint(state, asset_type),
            ])
            effective = _join([
                build_prop_image_prompt(prop.name, prop.description, prop_style, custom),
                _locked_project_constraint(state, asset_type),
            ])
            provider_prompts = {
                prop_view: _join([
                    build_prop_view_prompt(prop.name, prop.description, prop_view, custom, prop_style),
                    _locked_project_constraint(state, asset_type),
                ])
                for prop_view, _, _ in PROP_VIEWS
            }
            width, height = PROP_IMAGE_WIDTH, PROP_IMAGE_HEIGHT
        else:
            base = _join([
                build_prop_view_prompt(prop.name, prop.description, view_type, style=prop_style),
                _locked_project_constraint(state, asset_type),
            ])
            effective = _join([
                build_prop_view_prompt(prop.name, prop.description, view_type, custom, prop_style),
                _locked_project_constraint(state, asset_type),
            ])
            provider_prompts = {view_type: effective}
            width, height = 1024, 1024
        _style_positive, style_negative = project_style_layers(state, "prop")
        negative = _join([PROP_NEGATIVE_PROMPT, style_negative])
        template_version = PROP_PROMPT_TEMPLATE_VERSION
    elif asset_type == "scene":
        from src.agents.image_gen_agent import SCENE_NEGATIVE_PROMPT
        from src.services.asset_prompt_composer import build_scene_prompt_layers

        if view_type.startswith("scene_"):
            # The editor needs a prompt for the selected view.  The supplier,
            # however, still receives one shared group prompt so a sequence
            # request keeps the location identity and view order together.
            layer = build_scene_prompt_layers(
                state,
                asset_id,
                getattr(asset, "state_id", ""),
                view_type,
                custom,
            )
            group_prompt, count = build_scene_group_prompt(
                state,
                asset_id,
                custom,
                getattr(asset, "group_view_count", 0),
                getattr(asset, "state_id", ""),
            )
            _style_positive, style_negative = project_style_layers(state, "scene")
            effective = layer["effective_prompt"]
            negative = layer["negative_prompt"]
            effective_provider = str(
                provider
                or (getattr(state, "image_generation_policy", {}) or {}).get("provider")
                or ""
            ).lower()
            # Wan supports coherent sequence/group requests.  Legacy callers
            # with no provider snapshot keep the group contract; single-image
            # providers must receive only the selected view to avoid asking
            # Kling/Mock for an unsupported multi-view output.
            provider_source = group_prompt if effective_provider in {"", "wan"} else effective
            provider_prompts, provider_negative, mode = _provider_payloads(
                state,
                {view_type: provider_source},
                negative,
                provider,
            )
            return {
                "asset_type": asset_type, "asset_id": asset_id, "view_type": view_type,
                "base_prompt": _join([
                    layer["identity"], layer["state"], layer["scale"],
                    layer["style"], layer["view"], layer["constraints"],
                ]),
                "effective_prompt": effective, "prompt": effective,
                "display_prompt": effective,
                "custom_instructions": custom,
                "negative_prompt": negative, "effective_negative_prompt": negative,
                "provider_prompt": provider_prompts[view_type], "provider_prompts": provider_prompts,
                "provider_negative_prompt": provider_negative, "negative_prompt_mode": mode,
                "width": layer["width"], "height": layer["height"],
                "prompt_template_version": SCENE_PROMPT_TEMPLATE_VERSION,
                "project_constraints": f"场景组图固定为横向16:9；当前组共{count}张，不跟随最终视频画幅。",
                "group_view_count": count,
            }

        # Legacy projects may contain a ``wide``/``medium`` prompt generated
        # before the project style picker existed.  Rebuild that prompt from
        # the current scene identity/state layers so a stale "真实影视质感"
        # fragment cannot override a selected anime style.  The old view names
        # remain supported for compatibility; they map to the stable center
        # scene contract and keep their coarse shot label as an extra detail.
        if getattr(state, "script", None) and getattr(state.script, "asset_manifest", None):
            legacy_label = "全景建立镜头" if view_type == "wide" else "中景细节镜头"
            legacy_view_type = "scene_wide" if view_type == "wide" else "scene_medium"
            layers = build_scene_prompt_layers(
                state,
                asset_id,
                getattr(asset, "state_id", ""),
                legacy_view_type,
            )
            base = _join([
                "高质量影视场景空镜（按当前项目风格与场景状态重建）",
                layers["effective_prompt"],
                f"兼容景别：{legacy_label}。",
            ])
            effective = _scene_prompt(base, custom)
            negative = _join([SCENE_NEGATIVE_PROMPT, layers["negative_prompt"]])
            width, height = layers["width"], layers["height"]
            template_version = SCENE_PROMPT_TEMPLATE_VERSION
        else:
            # Old projects without a manifest still receive the canonical
            # project style lock and the fixed landscape scene contract.
            legacy_view_constraint = (
                "横向大全景建立镜头，完整展示空间边界、前中后景和主要固定结构。"
                if view_type == "wide"
                else "横向中景细节镜头，聚焦核心区域、材质与固定陈设，同时保留可辨识的空间关系。"
            )
            base = _join([
                str(getattr(asset, "base_prompt", "") or asset.image_prompt or "").strip(),
                "场景参考图固定横向16:9构图，保持地点身份、空间结构和空镜限制。",
                legacy_view_constraint,
                _locked_project_constraint(state, asset_type),
            ])
            effective = _scene_prompt(base, custom)
            _style_positive, style_negative = project_style_layers(state, "scene")
            negative = _join([SCENE_NEGATIVE_PROMPT, style_negative])
            width, height = 1920, 1080
            template_version = SCENE_PROMPT_TEMPLATE_VERSION
        provider_prompts = {view_type: effective}
    else:
        raise ValueError("asset_type must be character, scene, or prop")

    requirement_signature = ""
    if asset_type == "prop":
        try:
            from src.services.prop_view_requirements import infer_prop_view_requirement

            requirement_signature = infer_prop_view_requirement(state, asset_id).signature
        except Exception:
            requirement_signature = ""
    provider_prompts, provider_negative, mode = _provider_payloads(state, provider_prompts, negative, provider)

    return {
        "asset_type": asset_type,
        "asset_id": asset_id,
        "view_type": view_type,
        "base_prompt": base,
        "custom_instructions": custom,
        "effective_prompt": effective,
        "display_prompt": effective,
        "prompt": effective,
        "negative_prompt": negative,
        "effective_negative_prompt": negative,
        "provider_prompt": provider_prompts.get(view_type, effective),
        "provider_prompts": provider_prompts,
        "provider_negative_prompt": provider_negative,
        "negative_prompt_mode": mode,
        "width": width,
        "height": height,
        "prompt_template_version": template_version,
        "character_description": character_description,
        "project_constraints": _locked_project_constraint(state, asset_type),
        "requirement_signature": requirement_signature,
        "style_mode": style_mode,
        "style_diagnostics": style_diagnostics,
    }
