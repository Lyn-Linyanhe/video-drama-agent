"""Image Generation Agent — generates multi-view reference images per character and scene.

Characters: 3 images (portrait / profile / full_body)
Scenes:     2 images (wide / medium)

Multiple views give video APIs richer visual reference for character consistency.
"""

import asyncio
import shutil
import time
import uuid
from pathlib import Path

from PIL import Image, ImageOps

from src.schemas.script import ScriptBreakdown
from src.schemas.visual import VisualDesign
from src.schemas.character import CharacterImage, CharacterImageAssets
from src.schemas.scene_image import SceneImage, SceneImageAssets
from src.api_clients.image_base import ImageAPIClient, ImageGenResult
from src.api_clients.image_mock import MockImageClient
from src.services.asset_seed import derive_asset_seed
from src.services.asset_prompt_composer import SCENE_PROMPT_TEMPLATE_VERSION
from src.utils.llm import get_llm_client
from src.utils.visual_asset_manifest import repair_asset_manifest, validate_asset_manifest
from src.utils.visual_asset_manifest import scene_source_signature, stable_location_id
from src.config import (
    OUTPUT_DIR, CHARACTER_IMAGE_SIZE, IMAGE_WIDTH, IMAGE_HEIGHT,
    MAX_CONCURRENT_IMAGE_GENERATIONS, IMAGE_GENERATION_TIMEOUT,
)

# ── Multi-view config ──────────────────────────────────────────────

CHARACTER_VIEWS = [
    ("front_half", "正面半身", 1024, 1024, "单张独立正面半身特写，人物正对镜头，画面只包含胸部以上至头顶，胸部以下不得入画，不出现腰部、腿部、膝盖或脚，完整呈现面部、发型和上身服装。", "全身，腰部以下，腿部，膝盖，脚，背影，侧脸，低头，遮脸"),
    ("front_full", "正面全身", IMAGE_WIDTH, IMAGE_HEIGHT, "单张独立正面全身站立，9:16 竖构图，人物居中，头顶到鞋底完整入画，脚部清晰可见，上下均保留少量白色留白。", "半身，特写，裁切脚部，裁切头部，背影，侧脸，坐姿"),
    ("left_profile", "左侧面", 1024, 1024, "单张独立左侧面半身视图，人物身体与脸部严格面向画面左侧，只呈现左侧脸轮廓、鼻梁、下颌线和发型侧面。", "正脸，三分之二正脸，右侧脸，背影，全身，回头看镜头"),
    ("back_full", "背面全身", IMAGE_WIDTH, IMAGE_HEIGHT, "单张独立正后方全身设定图：相机严格位于人物背脊中线正后方，人物完全背向镜头，头部、肩线与躯干均朝向远离相机的方向，不能转头或斜肩。画面只能看见后脑、双肩、背部、服装背面、裤装和鞋子，脸部完全不可见，零脸部可见；禁止三分之二背面、过肩回望和侧脸。9:16 竖构图，头顶到鞋底完整入画。", "正脸，侧脸，可见脸部，三分之二背面，过肩回望，回头，镜面反射中的脸部，脸颊，眼睛，鼻子，嘴巴，半身，裁切脚部，裁切头部"),
]

CHARACTER_PROMPT_TEMPLATE_VERSION = 11
CHARACTER_REALISM_PREFIX = (
    "强制风格：电影级真人写实演员定妆参考照片，真实摄影，真实皮肤毛孔，自然五官比例，真实服装面料，"
    "中性影棚灯光，纯白影棚背景。最终结果必须像在摄影棚拍摄的真实人物，不得呈现为风格化角色原画。"
)
CHARACTER_ANIME_PREFIX = (
    "强制风格：二维动漫角色设计，清晰有表现力的线稿，受控的赛璐璐阴影，统一的动漫五官比例，"
    "精修动画关键帧视觉效果，统一服装设计和干净的纯色影棚式背景。最终结果必须是明确的动漫/插画角色参考图，不得呈现为真人实拍照片。"
)
CHARACTER_SINGLE_IMAGE_RULE = (
    "输出格式：严格只输出一张独立单图，只出现一个人物和一个镜头角度，使用一个纯白影棚背景；"
    "不得分屏、不得拼接成组图、不得重复出现人物。"
)


def _visual_style_entry(art_style: str | None) -> dict:
    """Resolve a free-text/legacy style value to the canonical registry entry."""
    from src.services.visual_styles import get_visual_style, resolve_visual_style

    text = str(art_style or "").strip()
    if text:
        try:
            return get_visual_style(text)
        except ValueError:
            pass
    style_id, _legacy, _modifiers = resolve_visual_style(art_style=text)
    return get_visual_style(style_id)


def _profile_art_style(creative_profile, fallback: str = "写实") -> str:
    """Resolve the prompt style from the stable project profile when present.

    Older project states may retain ``art_style`` or ``VisualDesign.overall_style``
    from a previous selection. The stable style ID is the source of truth for
    new image requests; keep supported modifiers as readable suffixes.
    """
    if creative_profile is None:
        return str(fallback or "写实").strip() or "写实"
    from src.services.visual_styles import resolve_profile_style

    entry = resolve_profile_style(creative_profile)
    style_text = entry["name"]
    modifiers = [
        str(value).strip()
        for value in (getattr(creative_profile, "style_modifiers", None) or [])
        if str(value).strip()
    ]
    return "、".join([style_text, *[item for item in modifiers if item not in style_text]])


def is_anime_art_style(art_style: str | None) -> bool:
    """Return whether a project style asks for anime/illustrated character art."""
    text = str(art_style or "").strip().casefold()
    if text in {"cinematic_cel", "semi_realistic_anime", "japanese_animation"}:
        return True
    return any(token in text for token in (
        "动漫", "二次元", "漫画", "卡通", "动画", "二维", "赛璐璐", "anime", "manga", "cartoon", "cel shading",
    ))


def character_style_mode(art_style: str | None) -> str:
    """Expose the deterministic style branch used in the prompt snapshot."""
    return "anime" if is_anime_art_style(art_style) else "photorealistic"


def character_style_prefix(art_style: str | None) -> str:
    style = _visual_style_entry(art_style)
    medium = style["prompt_layers"]["character"]
    prefix = CHARACTER_ANIME_PREFIX if is_anime_art_style(art_style) else CHARACTER_REALISM_PREFIX
    return f"{prefix} 项目风格执行层：{medium}"


def character_style_negative(art_style: str | None) -> str:
    style = _visual_style_entry(art_style)
    registry_negative = style["negative_prompt_layers"]["character"]
    if is_anime_art_style(art_style):
        return (
            "照片级写实，真人实拍，真人演员，真人试镜照，真人摄影，真人照片，"
            "真实皮肤纹理，摄影棚试镜照，写实影视剧照，真人五官比例，真实皮肤毛孔，"
            f"{registry_negative}，"
        )
    return (
        "风格化角色原画，概念艺术，赛璐璐阴影，绘画脸部，塑料数字皮肤，夸张眼睛，"
        "动漫，二次元，漫画，插画，卡通，动画角色，游戏角色，CG渲染，3D建模，虚拟偶像，数字人，娃娃脸，塑料皮肤，"
        "夸张大眼，尖下巴，赛璐璐，上色线稿，厚涂，概念艺术，角色原画，手办质感，非真人照片，"
        f"{registry_negative}，"
    )


def diagnose_character_prompt(art_style: str | None, prompt: str, negative_prompt: str = "") -> dict[str, object]:
    """Check the effective positive character prompt for style contradictions.

    Negative prompts intentionally contain the opposite style (for example, an anime
    job excludes photorealism), so diagnostics inspect the positive prompt and only
    report the negative text separately for auditability.
    """
    style_mode = character_style_mode(art_style)
    positive = str(prompt or "")
    folded = positive.casefold()
    if style_mode == "anime":
        expected = ("anime", "动漫", "二次元", "动画")
        opposite = ("photorealistic", "live-action", "真人摄影", "真人照片", "真实演员", "写实照片")
    else:
        expected = ("photorealistic", "真人", "写实", "live-action")
        opposite = ("动漫", "二次元", "漫画", "卡通", "动画角色", "anime")
    matched = [term for term in expected if term.casefold() in folded]
    missing = [] if matched else list(expected)

    def is_unnegated(term: str) -> bool:
        needle = term.casefold()
        start = 0
        negations = ("禁止", "不得", "不要", "不应", "不是", "非", "not ", "no ")
        while True:
            index = folded.find(needle, start)
            if index < 0:
                return False
            prefix = folded[max(0, index - 16):index]
            if not any(marker in prefix for marker in negations):
                return True
            start = index + len(needle)

    contradictory = [term for term in opposite if is_unnegated(term)]
    status = "passed" if not missing and not contradictory else "failed"
    return {
        "status": status,
        "style_mode": style_mode,
        "expected_style": str(art_style or "写实").strip() or "写实",
        "missing_style_terms": missing,
        "contradictory_prompt_terms": contradictory,
        "negative_prompt_supplied": bool(str(negative_prompt or "").strip()),
    }


def character_view_spec(view_type: str) -> tuple[str, int, int, str, str]:
    """Return the display label, size, required framing and view-specific exclusions."""
    for candidate_type, label, width, height, instruction, exclusions in CHARACTER_VIEWS:
        if candidate_type == view_type:
            return label, width, height, instruction, exclusions
    raise ValueError(f"Unsupported character view type: {view_type}")


def character_gender_lock(name: str, description: str) -> str:
    """Extract a hard gender lock from common Chinese/English appearance text."""
    text = f"{name} {description}".lower()
    female_tokens = ("女性", "女生", "女孩", "女人", "女主", "女，", "女,", "female", "woman", "girl")
    male_tokens = ("男性", "男生", "男孩", "男人", "男主", "男，", "男,", "male", "man", "boy")
    if any(token in text for token in female_tokens) and not any(token in text for token in male_tokens):
        return "性别锁定：女性；本角色必须保持女性身份，不得变成男性、中性或少年男性。"
    if any(token in text for token in male_tokens) and not any(token in text for token in female_tokens):
        return "性别锁定：男性；本角色必须保持男性身份，不得变成女性、中性或少女。"
    return "性别锁定：严格保持角色描述中的性别与年龄感，不得改变性别。"


def build_character_image_prompt(
    name: str,
    description: str,
    view_type: str,
    custom_instructions: str = "",
    art_style: str = "写实",
) -> str:
    """Build the canonical Chinese prompt for one independently generated character view."""
    label, _, _, view_instruction, _ = character_view_spec(view_type)
    face_requirement = "面部清晰、服装可辨识" if view_type != "back_full" else "相机正对人物背部中心，后脑发型与服装背面清晰，脸部、脸颊、五官和表情完全不可见"
    lighting_requirement = "中性摄影棚灯光，柔和前侧平光" if view_type != "back_full" else "中性均匀摄影棚顶光与背部轮廓光，不使用会暴露脸部的正面补光"
    identity_requirement = (
        "当前这张单图中的面部特征、肤色、发型、体型、服装颜色、服装材质、鞋子和标志性特征必须严格符合角色描述，便于后续作为同一真人身份参考"
        if view_type != "back_full"
        else "当前这张单图中的发型轮廓、体型、服装颜色、服装材质、鞋子和标志性特征必须严格符合角色描述；本图不得为了展示身份而露出面部"
    )
    custom_section = ""
    if custom_instructions.strip():
        custom_section = f"\n用户补充要求：{custom_instructions.strip()}。补充要求不得改变当前视图的镜头方向、构图范围、背景与禁用项。"
    gender_lock = character_gender_lock(name, description)
    style_text = str(art_style or "写实").strip() or "写实"
    anime_style = is_anime_art_style(style_text)
    style_prefix = character_style_prefix(style_text)
    if anime_style:
        identity_requirement = (
            "当前这张单图中的角色脸型、发型、体型、服装颜色、服装材质和标志性特征必须严格符合角色描述，"
            "便于后续作为同一动画角色身份参考"
            if view_type != "back_full" else
            "当前这张单图中的发型轮廓、体型、服装颜色、服装材质和标志性特征必须严格符合角色描述；本图不得为了展示身份而露出面部"
        )
    identity_anchor = "同一动画角色身份，同一张脸，同一年龄感" if anime_style else "同一个真人身份，同一张脸，同一年龄感"
    single_image_rule = (
        "输出格式：严格只输出一张单独的二维角色设定图，一个角色、一个镜头角度、一个干净纯色背景，"
        "不得拼图、分屏、重复人物。"
        if anime_style else CHARACTER_SINGLE_IMAGE_RULE
    )
    style_title = "高质量专业角色设定图（动漫风格版）" if anime_style else "高质量专业角色设定图（真人摄影版）"
    style_description = (
        f"必须使用项目艺术风格“{style_text}”，呈现统一的动漫/二次元角色设计、清晰线稿、稳定的动画脸型比例和适度赛璐璐光影；"
        "禁止生成真人照片、真实演员或摄影写实画面。"
        if anime_style else
        "必须是真人写实照片、真人演员试镜照、摄影棚定妆照；禁止任何绘画化、角色原画化、数字人化或夸张美术化处理。"
    )
    medium_description = "动漫角色立绘、动画设定稿、干净线稿与统一上色" if anime_style else "真人演员定妆照、真实摄影、写实影视质感、自然皮肤纹理、真实五官比例、真实服装面料"
    if view_type == "front_half":
        framing_guard = "构图严格为胸部以上，胸部以下不得入画，不出现腰部、腿部、膝盖或脚，不得为了展示全身而缩远镜头"
    elif view_type == "left_profile":
        framing_guard = "构图严格为侧面半身，腰部以下不得入画，不得缩远为全身"
    elif anime_style:
        framing_guard = "人物不可裁剪、不可遮挡、不得偏离项目艺术风格"
    else:
        framing_guard = "人物不可裁剪、不可遮挡、不可过度风格化"
    role_information = f"{name}，{description}。"
    framing_override = ""
    if view_type == "front_half":
        role_information += "下身与鞋子信息仅作身份档案，不得在本图绘制或展示。"
        framing_override = "构图覆盖要求：严格胸像特写，头部和肩部填充画面，在胸部中段裁切；不得出现腰部、臀部、腿部、膝盖、袜子或鞋子。"
    elif view_type == "left_profile":
        framing_override = "构图覆盖要求：严格侧面胸像，在胸部以下裁切；不得出现下半身。"
    return f"""{style_prefix}
{single_image_rule}
{style_title}。
【角色稳定外貌锚点】{role_information}
高质量专业角色设定图，{label}，只输出当前一个镜头角度的一张人物图；纯白色干净背景，{lighting_requirement}，{medium_description}。
视图要求：{view_instruction}
{framing_override}
角色信息：{role_information}
一致性要求：{gender_lock}；{identity_requirement}；{identity_anchor}，同一发际线、眉眼鼻唇比例和下颌轮廓；人物空手，自然站姿，表情克制。
风格硬性要求：{style_description}
限制：画面仅出现该角色与纯白背景，不得出现道具、武器、食物、饮料、其他角色、环境布景、文字、水印、标签或 UI 元素；{face_requirement}，{framing_guard}。{custom_section}
最终必须严格遵守上述{label}的镜头方向和构图范围。"""


def build_character_negative_prompt(view_type: str, art_style: str = "写实") -> str:
    """Build shared and view-specific exclusions for a character reference image."""
    _, _, _, _, view_exclusions = character_view_spec(view_type)
    return f"{CHARACTER_COMMON_NEGATIVE_PROMPT}{character_style_negative(art_style)}，{view_exclusions}"

CHARACTER_COMMON_NEGATIVE_PROMPT = (
        "分屏，拼接组图，重复人物，单张图片中出现多个镜头角度，"
        "道具，武器，食物，饮料，手持物，复杂动作，夸张表情，面部遮挡，环境背景，其他角色，"
        "多个画面，重复人物，文字，水印，标签，UI元素，额外肢体，服装不一致，发型不一致，面部不一致，"
        "性别改变，年龄改变，男变女，女变男，中性化，换脸，换身份，换发型，换服装，"
)

# Backward-compatible alias for integrations that imported the old constant.
CHARACTER_NEGATIVE_PROMPT = CHARACTER_COMMON_NEGATIVE_PROMPT + character_style_negative("写实")

SCENE_VIEWS = [
    ("wide", "全景建立镜头", "全景建立镜头，完整展示场景空间结构、前中后景层次与主要环境元素。"),
    ("medium", "中景细节镜头", "中景细节镜头，聚焦场景核心区域、材质、陈设与光影细节，同时保持空间关系清晰。"),
]

SCENE_GROUP_VIEWS = (
    ("scene_center", "正面中轴大全景"),
    ("scene_left_45", "左前方45度大全景"),
    ("scene_right_45", "右前方45度大全景"),
    ("scene_reverse", "空间深处向外全景"),
)

SCENE_NEGATIVE_PROMPT = "人物，角色，行人，肢体，脸部，文字，水印，字幕，标签，UI元素，品牌标识，低清晰度，模糊，畸变，杂乱构图"

PROP_VIEW_TYPE = "standard_views"
PROP_IMAGE_WIDTH = 1536
PROP_IMAGE_HEIGHT = 1024
PROP_NEGATIVE_PROMPT = (
    "人物，角色，人群，人影，手，脚，人脸，人体局部，场景，建筑，自然景观，其他道具，"
    "货架，浴室，洗手台，桌面陈列，生活方式摄影，多个物体，多个副本，成组堆叠，"
    "拼图，多宫格，分屏，文字，水印，logo，标签，UI元素，剧情事件，透视畸变，倾斜，旋转，裁切，低清晰度，模糊"
)

PROP_VIEWS = [
    ("front", "绝对正前方", "相机严格位于物体正前方，与物体中心等高，只显示正面"),
    ("back", "绝对正后方", "相机严格位于物体正后方，与物体中心等高，只显示背面"),
    ("left", "绝对左侧", "相机严格位于物体左侧，与物体中心等高，只显示左侧面"),
    ("right", "绝对右侧", "相机严格位于物体右侧，与物体中心等高，只显示右侧面"),
    ("top", "绝对正上方", "相机严格位于物体正上方垂直俯拍，只显示顶面"),
    ("bottom", "绝对正下方", "相机严格位于物体正下方垂直仰拍，只显示底面"),
]


def _prop_state_mode(name: str, description: str) -> str:
    """Resolve an explicit movable state before applying name-based defaults."""
    text = f"{name} {description}".casefold()
    negated_open = ("禁止打开", "不得打开", "不可打开", "不应打开", "不要打开", "禁止展开", "不得展开", "不可展开", "不应展开", "不要展开", "禁止露出刀刃", "不得露出刀刃")
    negated_closed = ("禁止闭合", "不得闭合", "不可闭合", "不应闭合", "不要闭合", "禁止收起", "不得收起", "不可收起")
    positive_closed = ("保持闭合", "完全闭合", "处于闭合", "保持关闭", "完全关闭", "保持收起", "完全收回", "刀刃完全收回", "未打开", "未展开")
    positive_open = ("保持打开", "完全打开", "处于打开", "保持展开", "完全展开", "刀刃完全展开", "打开状态", "展开状态")
    if any(token in text for token in negated_open) or any(token in text for token in positive_closed):
        return "closed"
    if any(token in text for token in negated_closed) or any(token in text for token in positive_open):
        return "open"
    return ""


def prop_state_lock(name: str, description: str) -> str:
    """Turn mutable prop wording into a concrete, provider-checkable state lock."""
    text = f"{name} {description}".casefold()
    explicit_mode = _prop_state_mode(name, description)
    if explicit_mode == "open":
        return (
            "可动部件状态锁：道具必须保持完全展开/打开状态，活动部件清晰可见并与主体连接，"
            "不得回收、半闭合或改变当前形态。"
        )
    if explicit_mode == "closed":
        if any(token in text for token in ("折叠刀", "折叠式刀", "折刀")):
            return (
                "可动部件状态锁：这是闭合的折叠刀；刀刃必须完全收回刀柄内部，刀尖不可见；"
                "从正面和侧边都不可见刀刃。禁止展开、半展开、打开、露出刀刃。"
            )
        return (
            "可动部件状态锁：道具必须保持完全闭合/收起状态，所有活动部件回到主体内部，"
            "不得打开、半展开或露出内部结构。"
        )
    if any(token in text for token in ("折叠刀", "折叠式刀", "折刀")):
        return (
            "可动部件状态锁：这是闭合的折叠刀；刀刃必须完全收回刀柄内部，刀尖不可见，"
            "刀柄与刀背形成一个单一短厚闭合外形；从正面和侧边都不可见刀刃。禁止展开、半展开、打开、露出刀刃、"
            "刀刃与刀柄分离、刀鞘或第二件物体。"
        )
    if any(token in text for token in ("闭合", "收起", "未打开", "未展开")):
        return (
            "可动部件状态锁：道具必须保持完全闭合/收起状态，所有活动部件回到主体内部，"
            "不得打开、半展开或露出内部结构。"
        )
    if any(token in text for token in ("展开", "打开", "刀刃露出", "打开状态")):
        return (
            "可动部件状态锁：道具必须保持完全展开/打开状态，活动部件清晰可见并与主体连接，"
            "不得回收、半闭合或改变当前形态。"
        )
    return "可动部件状态锁：结构、活动部件和当前可见状态必须与文字设定完全一致，不得擅自改变。"


def build_prop_image_prompt(name: str, description: str, style: str = "", custom_instructions: str = "") -> str:
    """Build the user-facing contract for a locally composed six-view sheet."""
    anime_style = is_anime_art_style(style)
    medium_title = "高质量二维动画道具多角度设定图" if anime_style else "高质量写实道具多角度展示图"
    lighting = (
        "纯白色纯净背景，柔和均匀的二维设定稿光线，轮廓清晰、色块稳定。"
        if anime_style
        else "纯白色纯净背景，中性专业产品影棚摄影。"
    )
    custom_text = ""
    if custom_instructions.strip():
        custom_text = f"\n用户补充要求：{custom_instructions.strip()}。补充要求不得改变六视图网格、纯白背景、正交视角与无人物限制。"
    return f"""{medium_title}。系统分别生成六张独立单视图，再以 2 行 3 列的干净网格本地拼接，确保每格只出现同一件道具的一个标准正交视图。{lighting}
六视图必须依次包含：绝对正前方视图、绝对正后方视图、绝对左侧视图、绝对右侧视图、绝对正上方俯拍视图、绝对正下方仰拍视图。所有视图必须是同一件道具，材质、颜色、比例、结构和磨损状态完全一致。
摄影与构图：使用超长焦镜头或移轴镜头效果，将透视变形降到最低；本该平行的边缘保持平行，接近正交投影；每个视图如经三脚架精密校准拍摄，物体居中、完整、端正，无倾斜、旋转或透视畸变。
道具信息：{name}，{description}。
固定形态：柔软或可变形道具在六个方向必须保持剧本明确给出的展开、折叠或放置状态；未指定时不自行增加展开、折叠或放置状态。刚性道具的位置、结构和可动部件状态不得变化。
限制：每张原始视图只出现一件道具和纯白背景；不得出现人物、场景、浴室、货架、洗手台、桌面陈列、植物、洗护用品、其他道具、多个副本、文字、水印、Logo 或 UI 元素。{custom_text}
最终由系统拼接为一张横向 2x3 六视图参考图。"""


def build_prop_view_prompt(
    name: str,
    description: str,
    view_type: str,
    custom_instructions: str = "",
    style: str = "",
) -> str:
    """Build one strict single-object orthographic view prompt."""
    view = next((item for item in PROP_VIEWS if item[0] == view_type), None)
    if view is None:
        raise ValueError(f"Unsupported prop view: {view_type}")
    _, label, camera = view
    custom_text = f"补充要求：{custom_instructions.strip()}。" if custom_instructions.strip() else ""
    anime_style = is_anime_art_style(style)
    title = "单件二维动画道具设定参考图" if anime_style else "单件道具标准产品参考图"
    lighting = (
        "纯白无缝背景，柔和均匀的二维设定稿光线，轮廓清晰、色块稳定。"
        if anime_style
        else "纯白无缝背景，中性均匀产品影棚摄影光线，无环境反射，无戏剧化光影。"
    )
    state_lock = prop_state_lock(name, description)
    return f"""{title}，当前只生成一个{label}视图，不是拼图，不是多宫格。
道具：{name}。可见外观：{description}。
镜头方向：{camera}。接近正交投影，透视变形最小，物体完整、居中、端正，占画面约70%，四周留白。
一致性：颜色、材质、尺寸比例、结构、磨损与固定形态严格锁定；柔软道具保持同一展开或折叠状态。
{state_lock}
背景与灯光：{lighting}
限制：画面中只能有这一件道具，不得出现第二件物体、多个副本、货架、浴室、洗手台、桌面陈列、植物、洗护用品、人物、手、文字、水印、标签或边框。{custom_text}
最终只输出一张{label}单视图。"""

# ── LLM prompt for scene descriptions ──────────────────────────────

SCENE_PROMPT_SYSTEM = """你是一个影视场景设计师和 AI 图像提示词工程师。你的任务是为短剧场景生成英文环境视觉描述提示词。

要求：
1. 所有提示词必须用英文
2. 描述场景的空间布局、建筑风格、前景/中景/背景层次、关键道具、光线氛围
3. 不包含人物（纯场景空镜）
4. 风格必须与整体视觉设计一致
5. 固定为横向场景参考构图 (16:9)
6. 必须明确 no characters, no people, no figures
7. 必须包含 cinematic, photorealistic, 16:9 landscape composition, consistent art style, high detail, no text, no watermark

推荐结构：
A cinematic pure background scene depicting [location] at [time].
The scene shows [environment details, architecture, objects, lighting].
No characters, no people, no figures.
Style: photorealistic, rich details, high quality, atmospheric lighting, consistent art style, no text, no watermark."""

# Keep the effective scene-prompt contract in Chinese even when loading legacy
# source files that still contain the former English-oriented instruction above.
SCENE_PROMPT_SYSTEM = """你是专业影视场景设计师和中文图像提示词工程师。
请为短剧生成可直接用于图片模型的中文场景空镜描述。全部使用中文，描述空间布局、建筑或环境材质、前中后景层次、关键陈设、时间、光线、色彩、情绪与景别；仅描述无人物场景；保持整体视觉风格一致并固定为横向16:9场景参考图。输出一个完整自然段，不要解释、标题或规则列表。"""


class ImageGenerationAgent:
    """Generates multi-view character portraits and scene environment images.

    Each character gets 4 reference images (front half, front full, left profile, back full).
    Each scene gets 2 reference images (wide, medium).

    All images are generated concurrently for maximum throughput.
    """

    def __init__(self, api_client: ImageAPIClient | None = None):
        self.llm = get_llm_client()
        self.api_client = api_client or MockImageClient()
        self._semaphore: asyncio.Semaphore | None = None
        self._semaphore_loop = None
        self.output_dir = OUTPUT_DIR

    def _generation_semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._semaphore is None or self._semaphore_loop is not loop:
            self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_IMAGE_GENERATIONS)
            self._semaphore_loop = loop
        return self._semaphore

    async def run(
        self,
        script: ScriptBreakdown,
        visual: VisualDesign,
        project_id: str = "",
        include_characters: bool = True,
        include_scenes: bool = True,
        creative_profile=None,
    ) -> tuple[CharacterImageAssets, SceneImageAssets]:
        """Generate selected character and scene reference images.

        Returns:
            Tuple of (CharacterImageAssets, SceneImageAssets).
        """
        start_time = time.time()
        repair_asset_manifest(script)
        manifest_errors = validate_asset_manifest(script)
        if manifest_errors:
            raise ValueError("视觉资产清单不完整：" + "；".join(manifest_errors[:5]))

        # Output directories
        char_base = self.output_dir / "characters"
        scene_base = self.output_dir / "scenes"
        if project_id:
            asset_root = self.output_dir / project_id / "assets"
            char_base = asset_root / "characters"
            scene_base = asset_root / "scenes"

        char_descriptions = visual.global_character_descriptions or getattr(
            visual, "global_character_descriptions_cn", {}
        ) or {}
        chinese_descriptions = getattr(visual, "global_character_descriptions_cn", {}) or {}
        art_style = _profile_art_style(
            creative_profile,
            getattr(visual, "overall_style", "") or "写实",
        )
        # The legacy stage-2 entrypoint historically received only
        # ``VisualDesign`` and rebuilt scene prompts ad hoc.  Carry the
        # project profile when available so it uses the same canonical style
        # registry as the current asset routes; compatibility callers still
        # get a profile derived from the visual style text.
        if creative_profile is None:
            from src.schemas.project import CreativeProfile

            creative_profile = CreativeProfile.from_request(
                art_style=art_style,
                video_ratio=getattr(visual, "aspect_ratio", "9:16"),
            )

        # ── Build all tasks ──────────────────────────────────────

        # Each character gets four independent reference views.
        char_tasks = []
        if include_characters:
            if script.asset_manifest:
                character_specs = [
                    (item.name, char_descriptions.get(item.name) or item.description)
                    for item in script.asset_manifest.characters
                    if item.appearance_mode == "on_screen"
                ]
            else:
                character_specs = list(char_descriptions.items())
            for name, desc in character_specs:
                if not desc:
                    # A manifest identity is useful fallback context, but visual
                    # generation is still permitted when a visual LLM omitted it.
                    desc = name
                identity = "".join(char for char in name if "\u3400" <= char <= "\u9fff")
                chinese_desc = chinese_descriptions.get(name) or chinese_descriptions.get(identity) or desc
                char_dir = char_base / name
                char_dir.mkdir(parents=True, exist_ok=True)
                for view_type, label, w, h, suffix, _ in CHARACTER_VIEWS:
                    char_tasks.append(
                        self._generate_character_image(
                            name,
                            chinese_desc,
                            view_type,
                            label,
                            w,
                            h,
                            suffix,
                            derive_asset_seed(project_id, "character", name, view_type, CHARACTER_PROMPT_TEMPLATE_VERSION),
                            char_dir,
                            art_style=art_style,
                        )
                    )

        # Each scene × 2 views.  Both the current and legacy stage-2 paths use
        # the canonical scene composer; no LLM rewrite is allowed to leak a
        # shot action into reusable location identity or drop the project
        # medium/style lock.
        visual_map = {v.scene_id: v for v in visual.scenes}
        scene_tasks = []
        if include_scenes:
            if script.asset_manifest:
                scene_specs = []
                scene_by_id = {scene.scene_id: scene for scene in script.scenes}
                for location in script.asset_manifest.locations:
                    representative = scene_by_id.get(location.representative_scene_id)
                    if representative is None:
                        continue
                    scene_specs.append((
                        location.location_id or stable_location_id(location.canonical_name),
                        representative.model_copy(
                            update={
                                "location": location.canonical_name,
                                # A location reference must not inherit a
                                # transient shot action when no reusable
                                # environment description was extracted.
                                "action_description": location.environment_description,
                            }
                        ),
                    ))
            else:
                scene_specs = [(stable_location_id(scene.location), scene) for scene in script.scenes]
            for location_id, scene in scene_specs:
                scene_dir = scene_base / location_id
                scene_dir.mkdir(parents=True, exist_ok=True)
                scene_visual = visual_map.get(scene.scene_id)
                scene_tasks.append(
                    self._generate_scene_images(
                        scene,
                        scene_visual,
                        visual,
                        scene_dir,
                        location_id,
                        script=script,
                        creative_profile=creative_profile,
                        project_id=project_id,
                    )
                )

        # ── Run all concurrently ─────────────────────────────────
        all_tasks = char_tasks + scene_tasks
        results = await asyncio.gather(*all_tasks, return_exceptions=True)

        # ── Collect results ──────────────────────────────────────
        characters: list[CharacterImage] = []
        scene_images: list[SceneImage] = []
        total_cost = 0.0

        failures: list[str] = []
        for r in results:
            if isinstance(r, list):
                # Scene tasks return lists of SceneImage
                for item in r:
                    if isinstance(item, SceneImage):
                        scene_images.append(item)
                        total_cost += item.cost
            elif isinstance(r, CharacterImage):
                characters.append(r)
                total_cost += r.cost
            elif isinstance(r, SceneImage):
                scene_images.append(r)
                total_cost += r.cost
            elif isinstance(r, Exception):
                print(f"  ⚠️ Image generation failed: {r}")
                failures.append(str(r))

        if failures:
            raise RuntimeError("图片生成失败：" + "；".join(failures[:3]))

        elapsed = time.time() - start_time

        return (
            CharacterImageAssets(
                characters=characters,
                total_cost=total_cost,
                generation_time_sec=elapsed,
            ),
            SceneImageAssets(
                scenes=scene_images,
                total_cost=total_cost,
                generation_time_sec=elapsed,
            ),
        )

    async def generate_single_image(
        self,
        prompt: str,
        negative_prompt: str = "",
        width: int = IMAGE_WIDTH,
        height: int = IMAGE_HEIGHT,
        seed: int | None = None,
    ):
        """Generate one user-edited image prompt without rebuilding all assets."""
        async with self._generation_semaphore():
            try:
                return await asyncio.wait_for(
                    self.api_client.generate_image(
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        width=width,
                        height=height,
                        seed=seed,
                    ),
                    timeout=IMAGE_GENERATION_TIMEOUT,
                )
            except TimeoutError as exc:
                raise RuntimeError(f"图片生成超时（{IMAGE_GENERATION_TIMEOUT} 秒）") from exc

    async def generate_prop_sheet(
        self,
        name: str,
        description: str,
        custom_instructions: str = "",
        seed: int | None = None,
        view_prompts: dict[str, str] | None = None,
        negative_prompt: str | None = None,
        art_style: str = "",
        project_id: str = "",
    ) -> ImageGenResult:
        """Generate six independent prop views and compose a deterministic 2x3 sheet."""
        base_seed = seed or derive_asset_seed(project_id, "prop", name, "standard_views", 2)
        style_text = art_style.strip() or "写实"
        if negative_prompt is None:
            style_negative = _visual_style_entry(style_text)["negative_prompt_layers"]["prop"]
            negative_prompt = f"{PROP_NEGATIVE_PROMPT}，{style_negative}"
        tasks = [
            self.generate_single_image(
                prompt=(view_prompts or {}).get(
                    view_type,
                    build_prop_view_prompt(
                        name,
                        description,
                        view_type,
                        custom_instructions,
                        style_text,
                    ),
                ),
                negative_prompt=negative_prompt,
                width=1024,
                height=1024,
                seed=base_seed,
            )
            for view_type, _, _ in PROP_VIEWS
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        failures = [
            str(item) if isinstance(item, Exception) else item.error_message or "图片服务未返回结果"
            for item in results
            if isinstance(item, Exception) or not item.success or not item.local_path
        ]
        if failures:
            for item in results:
                if isinstance(item, ImageGenResult) and item.local_path:
                    Path(item.local_path).unlink(missing_ok=True)
            return ImageGenResult(success=False, error_message="；".join(failures[:3]))

        canvas = Image.new("RGB", (PROP_IMAGE_WIDTH, PROP_IMAGE_HEIGHT), "white")
        tile_width = PROP_IMAGE_WIDTH // 3
        tile_height = PROP_IMAGE_HEIGHT // 2
        source_paths: list[Path] = []
        try:
            for index, result in enumerate(results):
                source_path = Path(result.local_path)
                source_paths.append(source_path)
                with Image.open(source_path) as source:
                    fitted = ImageOps.contain(
                        source.convert("RGB"),
                        (tile_width - 32, tile_height - 32),
                        method=Image.Resampling.LANCZOS,
                    )
                column = index % 3
                row = index // 3
                x = column * tile_width + (tile_width - fitted.width) // 2
                y = row * tile_height + (tile_height - fitted.height) // 2
                canvas.paste(fitted, (x, y))
            sheet_path = self.output_dir / f"prop_sheet_{uuid.uuid4().hex}.png"
            canvas.save(sheet_path, "PNG")
        finally:
            for source_path in source_paths:
                source_path.unlink(missing_ok=True)

        return ImageGenResult(
            success=True,
            image_url=f"file://{sheet_path}",
            local_path=str(sheet_path),
            width=PROP_IMAGE_WIDTH,
            height=PROP_IMAGE_HEIGHT,
            seed=base_seed,
            api_cost=sum(item.api_cost for item in results),
        )

    # ── Character image generation ────────────────────────────────

    async def _generate_character_image(
        self,
        name: str,
        description: str,
        view_type: str,
        label: str,
        width: int,
        height: int,
        prompt_suffix: str,
        seed: int,
        out_dir: Path,
        art_style: str = "写实",
    ) -> CharacterImage:
        """Generate a single character reference image for one view."""
        image_prompt = build_character_image_prompt(name, description, view_type, art_style=art_style)

        async with self._generation_semaphore():
            try:
                result = await asyncio.wait_for(
                    self.api_client.generate_image(
                        prompt=image_prompt,
                        negative_prompt=build_character_negative_prompt(view_type, art_style=art_style),
                        width=width,
                        height=height,
                        seed=seed,
                    ),
                    timeout=IMAGE_GENERATION_TIMEOUT,
                )
            except TimeoutError as exc:
                raise RuntimeError(f"角色“{name}”的{label}生成超时（{IMAGE_GENERATION_TIMEOUT} 秒）") from exc

        if not result.success:
            raise RuntimeError(f"角色“{name}”的{label}生成失败：{result.error_message or '图片服务未返回结果'}")

        # Keep the legacy stage-2 entrypoint on the same quality contract as
        # the project asset routes.  This is intentionally lazy-imported to
        # avoid the context/agent import cycle during application startup.
        from src.services.asset_generation import _validate_generated_image

        quality_report = await _validate_generated_image(
            result,
            image_prompt,
            "character",
            view_type,
            getattr(self.api_client, "provider_name", ""),
            expected_width=width,
            expected_height=height,
        )

        local_path = result.local_path or ""
        if result.success and local_path:
            dest = out_dir / f"{view_type}.png"
            try:
                shutil.move(local_path, str(dest))
                local_path = str(dest)
            except Exception:
                pass

        return CharacterImage(
            character_name=name,
            view_type=view_type,
            image_prompt=image_prompt,
            image_path=local_path,
            seed=seed,
            width=width,
            height=height,
            api_provider=self.api_client.provider_name,
            cost=result.api_cost,
            prompt_template_version=CHARACTER_PROMPT_TEMPLATE_VERSION,
            quality_report=quality_report,
        )

    # ── Scene image generation ────────────────────────────────────

    async def _generate_scene_images(
        self,
        scene,
        scene_visual,
        visual: VisualDesign,
        out_dir: Path,
        location_id: str = "",
        script: ScriptBreakdown | None = None,
        creative_profile=None,
        project_id: str = "",
    ) -> list[SceneImage]:
        """Generate all view images for one scene. Returns list of SceneImage."""
        from src.graph.state import DramaState
        from src.schemas.project import CreativeProfile
        from src.utils.visual_asset_manifest import build_manifest_from_scenes
        from src.services.asset_prompt_composer import compose_asset_prompt, scene_environment_key_elements

        location_id = location_id or stable_location_id(scene.location)
        if script is None:
            # Direct legacy callers may pass only one Scene. Build a temporary
            # manifest so the prompt still follows the same identity/state/view
            # contract without changing their public call signature.
            script = ScriptBreakdown(
                title="兼容场景",
                genre="",
                logline="",
                duration_sec=max(3, int(getattr(scene, "duration_sec", 3) or 3)),
                narration_style="",
                scenes=[scene],
            )
            script.asset_manifest = build_manifest_from_scenes(script)
        if not getattr(script, "asset_manifest", None):
            script.asset_manifest = build_manifest_from_scenes(script)
        if creative_profile is None:
            creative_profile = CreativeProfile.from_request(
                art_style=getattr(visual, "overall_style", "") or "写实",
                video_ratio=getattr(visual, "aspect_ratio", "9:16"),
            )
        prompt_state = DramaState(
            project_id=project_id,
            script=script,
            visual_design=visual,
            creative_profile=creative_profile,
        )

        visual_custom = ""
        if scene_visual:
            visual_custom = "；".join(
                part for part in (
                    f"构图：{scene_visual.composition}",
                    f"光线：{scene_visual.lighting}",
                    f"色调：{scene_visual.color_tone}",
                    f"关键环境元素：{'、'.join(scene_environment_key_elements(scene_visual, script, scene.scene_id))}",
                )
                if part.split("：", 1)[-1].strip()
            )

        tasks = []
        for view_type, view_label, view_instruction in SCENE_VIEWS:
            composition = compose_asset_prompt(
                prompt_state,
                "scene",
                location_id,
                view_type,
                SceneImage(
                    scene_id=scene.scene_id,
                    location_id=location_id,
                    state_id="",
                    location=scene.location,
                    view_type=view_type,
                    image_prompt="",
                    image_path="",
                    seed=0,
                ),
                visual_custom,
                provider=getattr(self.api_client, "provider_name", ""),
            )
            prompt = composition["effective_prompt"]
            seed = derive_asset_seed(project_id, "scene", location_id, view_type, 1)
            tasks.append(
                self._generate_scene_single(
                    scene.scene_id, location_id, scene.location, view_type, view_label,
                    prompt,
                    seed,
                    out_dir,
                    negative_prompt=composition.get("provider_negative_prompt", composition["negative_prompt"]),
                    prompt_template_version=composition.get("prompt_template_version", SCENE_PROMPT_TEMPLATE_VERSION),
                    base_prompt=composition.get("base_prompt", ""),
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)
        failures = [result for result in results if isinstance(result, Exception)]
        if failures:
            details = "; ".join(str(error) for error in failures[:2])
            raise RuntimeError(f"场景 {scene.scene_id} 图片生成失败：{details}") from failures[0]
        return [r for r in results if isinstance(r, SceneImage)]

    async def _generate_scene_single(
        self,
        scene_id: int,
        location_id: str,
        location: str,
        view_type: str,
        label: str,
        prompt: str,
        seed: int,
        out_dir: Path,
        negative_prompt: str = SCENE_NEGATIVE_PROMPT,
        prompt_template_version: int = SCENE_PROMPT_TEMPLATE_VERSION,
        base_prompt: str = "",
    ) -> SceneImage:
        """Generate a single scene view image."""
        async with self._generation_semaphore():
            try:
                result = await asyncio.wait_for(
                    self.api_client.generate_image(
                        prompt=prompt,
                        negative_prompt=negative_prompt,
                        width=1920,
                        height=1080,
                        seed=seed,
                    ),
                    timeout=IMAGE_GENERATION_TIMEOUT,
                )
            except TimeoutError as exc:
                raise RuntimeError(f"场景 {scene_id} 的{label}生成超时（{IMAGE_GENERATION_TIMEOUT} 秒）") from exc

        if not result.success:
            raise RuntimeError(f"场景 {scene_id} 的{label}生成失败：{result.error_message or '图片服务未返回结果'}")

        # The compatibility stage shares the same technical/visual review as
        # the current project asset service so generated references cannot
        # silently enter video generation without a report.
        from src.services.asset_generation import _validate_generated_image

        quality_report = await _validate_generated_image(
            result,
            prompt,
            "scene",
            view_type,
            getattr(self.api_client, "provider_name", ""),
            expected_width=1920,
            expected_height=1080,
        )

        local_path = result.local_path or ""
        if result.success and local_path:
            dest = out_dir / f"{view_type}.png"
            try:
                shutil.move(local_path, str(dest))
                local_path = str(dest)
            except Exception:
                pass

        return SceneImage(
            scene_id=scene_id,
            location_id=location_id,
            location=location,
            view_type=view_type,
            image_prompt=prompt,
            base_prompt=base_prompt,
            image_path=local_path,
            seed=seed,
            width=1920,
            height=1080,
            api_provider=self.api_client.provider_name,
            cost=result.api_cost,
            source_signature=scene_source_signature(location, prompt),
            prompt_template_version=prompt_template_version,
            quality_report=quality_report,
        )
