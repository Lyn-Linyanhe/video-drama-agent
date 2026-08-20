"""Helpers for normalized character and location assets.

The manifest is optional to preserve projects created before visual assets were
separated from the timed storyboard shot list.
"""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from src.schemas.scene_state import LocationState
from src.schemas.script import (
    LocationAsset,
    PropAsset,
    ScriptBreakdown,
    ShotVisualBinding,
    VisualAssetManifest,
    VisualCharacterAsset,
)


COMMON_TRANSIENT_PROP_NAMES = {
    "毛巾", "浴巾", "纸巾", "手帕", "水杯", "杯子", "玻璃杯", "碗", "盘子", "餐具",
    "椅子", "桌子", "台灯", "枕头", "被子", "雨伞", "普通衣物", "鞋子",
    "towel", "bath towel", "tissue", "handkerchief", "cup", "glass", "bowl", "plate",
    "chair", "table", "lamp", "pillow", "blanket", "umbrella",
}
PLOT_CRITICAL_PROP_TERMS = {
    "证据", "线索", "凶器", "密码", "录音", "遗物", "关键", "真相", "反转", "血迹",
    "暗号", "机关", "交接", "追踪", "芯片", "指纹", "唯一", "特殊", "失踪",
    "evidence", "clue", "weapon", "password", "recording", "heirloom", "critical",
    "blood", "secret", "tracking", "unique", "key", "钥匙", "合同", "照片", "录音笔",
}
ALLOWED_PROP_CRITICAL_REASONS = {"plot", "continuity", "action", "handoff", "identity"}
# A model-provided reason is only a label.  These markers are the minimum
# semantic evidence that makes an object worth carrying into the reference
# asset pipeline.  Keep this vocabulary broad and bilingual so new scripts do
# not depend on the hard-coded plot-prop name table below.
PROP_PLOT_EVIDENCE_TERMS = {
    "线索", "证据", "凶器", "密码", "暗号", "机关", "交接", "追踪", "身份",
    "揭示", "揭露", "真相", "反转", "关键", "唯一", "决定", "证明", "发现",
    "藏有", "承载", "推动剧情", "影响后续", "后续理解",
    "clue", "evidence", "weapon", "password", "code", "mechanism", "handoff",
    "identity", "reveal", "truth", "twist", "critical", "unique", "unlock",
    "reveals", "advances the plot", "plot-critical",
}
PROP_CONTINUITY_EVIDENCE_TERMS = {
    "连续", "保持一致", "跨镜头", "延续", "同一件", "身份标志", "标志性", "外观一致", "手持",
    "continuity", "consistent", "same object", "recurring", "identity marker", "held",
}
PROP_ACTION_EVIDENCE_TERMS = {
    "交给", "递给", "交换", "传递", "使用", "拿起", "握住", "打开", "关闭", "插入",
    "带走", "丢失", "掉落", "毁坏", "撕毁", "动作结果", "action", "handoff", "uses",
    "opens", "closes", "inserts", "carries", "drops", "destroys",
}
BACKGROUND_FIXTURE_PROP_TERMS = {
    "桌", "椅", "沙发", "床", "柜", "书架", "货架", "灯", "窗帘", "电脑", "显示器", "电视",
    "墙", "门", "镜", "洗手台", "马桶", "water stain", "bloodstain", "furniture", "fixture",
    "desk", "chair", "sofa", "bed", "cabinet", "shelf", "lamp", "curtain", "monitor", "television",
}
ENVIRONMENT_STATE_TERMS = {
    "血迹", "水渍", "积水", "烟雾", "雨水", "碎玻璃", "灰尘", "阴影", "blood stain", "water stain",
    "smoke", "rain", "broken glass", "dust", "shadow",
}
# These terms identify reusable visual facts in a shot description.  They are
# intentionally concrete: generic action verbs are not enough to promote a
# clause into a location identity.
STATIC_ENVIRONMENT_TERMS = {
    "路灯", "灯牌", "招牌", "霓虹", "橱窗", "屋檐", "街道", "街角", "街景", "巷",
    "玻璃门", "玻璃窗", "门头", "外立面", "铁门", "木门", "门框", "窗", "墙", "墙面",
    "广告纸", "纸箱", "自行车", "楼梯", "石阶", "通道", "地下室", "灯管", "灯光",
    "电箱", "储物柜", "摄像头", "地面", "地板", "天花板", "水痕", "积水", "水汽",
    "霉", "雨幕", "雨水", "木箱", "雕像", "钟表零件", "白布", "长桌", "台灯",
    "铜钟", "机械钟", "钟体", "展台", "齿轮", "暗道", "出口", "管线", "柜台", "货架", "沙发", "座椅", "地毯",
}
NAMED_FIXED_FIXTURE_TERMS = {
    "肖像", "肖像画", "画像", "壁画", "雕像", "海报", "奖状", "牌匾",
    "portrait", "mural", "statue", "poster", "plaque",
}
TRANSIENT_ENVIRONMENT_TERMS = {
    "信封", "信件", "照片", "地图", "钥匙", "手机", "录音机", "录音笔", "档案", "文件",
    "刀", "枪", "合同", "药瓶", "戒指", "耳钉", "项链", "报纸", "芯片", "u盘",
}
DYNAMIC_ENVIRONMENT_CUES = {
    "被推开", "推开", "打开", "关上", "走出来", "走进去", "走进", "进入", "来到", "跑向",
    "跑去", "跑进", "冲向", "冲进", "后退", "停下", "靠近", "拉着", "带着", "跟随",
    "穿过", "走过", "拐过", "经过", "攥紧", "拿出",
    "握住", "握紧", "抽出", "藏在", "蹲在", "躲在", "看向", "抬头", "低头", "转身",
    "扑向", "砸向", "掉进", "掉入", "滑落", "落在", "溅起", "传来", "散落", "摇晃",
    "开始震动", "缓缓下沉", "沉下", "重新亮", "熄灭", "又看", "露出", "一动不动", "发抖",
    "被水泡", "泡开", "映出", "黑影", "发出", "涌出", "一闪而过", "水声", "放大", "扫过",
    "晃动", "裂开", "碎片", "突然", "画面缓缓停", "火漆", "印章",
    "发丝", "头发", "黑色中长发", "风衣", "大衣", "雨伞", "口罩", "鸭舌帽", "车窗", "轿车",
    # Movement variants that commonly appear between a fixed fixture and a
    # destination (for example, "推开铁门并走入地下室").  They describe a
    # shot action and must never be persisted as reusable set dressing.
    "走入", "走出", "走上", "走下", "跑入", "跑出", "冲入", "冲出",
    "离开", "退出", "返回", "回到", "踏入", "踏出", "跨过", "奔向", "奔入",
    "爬上", "爬下", "钻入", "钻出",
    # Narrative state, sound and preparation cues must not become reusable set
    # dressing. The static noun can still be recovered from a mixed clause.
    "生怕", "准备", "声音", "响起", "传来", "脚步声", "掌声", "录音", "通话", "对白",
    "转动", "旋转", "震动", "震颤", "摩擦声", "发出",
    # These describe a temporary visual state or an event, not a reusable
    # location identity. They used to slip through the compact-manifest
    # fallback because they do not always contain an explicit actor verb.
    "忽明忽暗", "闪烁", "红光一闪", "一闪", "被雨水打得", "落下", "滴落",
    "字迹模糊", "被水汽泡得模糊", "水汽泡得模糊", "手电光", "光束扫过",
    # General action/state variants that frequently appear in compact script
    # outputs. These are transient shot facts, even when the clause also names
    # a static fixture such as a door, electrical box, or tunnel.
    "停在", "停下", "拧开", "拧动", "旋开", "摸向", "触向", "探向", "指向", "伸手",
    "抓住", "碰到", "拿起", "取出", "撑墙", "撑着墙", "站起", "起身", "消失在",
    "融为一体", "作为背景", "雨声",
    # Gaze / psychology clauses that repeatedly slip into location dressing.
    # A clause naming a static fixture (e.g. 巷口) is still a character action
    # when it also names looking/thinking; it must not become reusable set.
    "望向", "抬眼", "视线", "目光", "瞥向", "盯着", "凝视", "神色", "神情",
    "怕", "担心", "害怕", "心里",
    # Directional movement ("沿石阶向下走", "朝前走") is a shot action even
    # when it names a static fixture such as 石阶.
    "向下走", "向上走", "向前走", "往前走", "向后走", "朝前走", "朝里走", "朝外走",
}
VOICE_ONLY_NAME_TERMS = {
    "旁白", "画外音", "心声", "内心", "电话", "录音", "广播", "播报", "短信提示音",
    "narrator", "voiceover", "voice-over", "offscreen", "off-screen", "phone", "recording",
}
NON_PHYSICAL_PROP_NAMES = {
    "短信", "短信内容", "消息", "消息内容", "录音", "录音内容", "录音声音",
    "电话", "电话那头", "通话", "声音", "语音", "旁白", "心声",
    "sms", "sms content", "message", "message content", "recording",
    "recording content", "phone call", "voice", "audio",
}


def _is_non_physical_prop_name(name_key: str) -> bool:
    """Match virtual narrative content without rejecting physical devices."""
    normalized = normalize_scene_name(name_key)
    if normalized in NON_PHYSICAL_PROP_NAMES:
        return True
    # ``asset_manifest`` is model-authored, so the same virtual content often
    # arrives with a qualifier (for example ``手机短信提示`` or
    # ``prop_sms_notice``). Keep physical devices such as ``手机``、``录音机``
    # and ``录音笔`` valid by requiring a content/notification suffix.
    descriptor = re.sub(r"^prop[_\s-]*", "", normalized)
    return bool(re.fullmatch(
        r"(?:手机|屏幕)?(?:短信|消息|通知)(?:内容|信息|文本|提示|提示音|提醒)?|"
        r"(?:录音|电话|通话|声音|语音)(?:内容|信息|文本|片段|提示音|声音)?|"
        r"(?:sms|message|notification|recording|phone call|voice|audio)"
        r"(?: content| text| notification| notice| prompt| clip| message)?",
        descriptor.replace("_", " "),
    ))


def _is_non_physical_prop(prop: PropAsset) -> bool:
    """Classify model aliases/IDs for virtual content before prop generation."""
    return any(
        _is_non_physical_prop_name(value)
        for value in (getattr(prop, "name", ""), getattr(prop, "prop_id", ""))
        if str(value or "").strip()
    )
PLOT_PROP_PATTERNS = {
    "letter": ("信封", "letter", "plot"),
    "photo": ("照片", "photo", "plot"),
    "phone": ("手机", "phone", "continuity"),
    "key": ("钥匙", "key", "plot"),
    "contract": ("合同", "contract", "plot"),
    "recorder_device": ("录音机", "recorder device", "plot"),
    "recording_pen": ("录音笔", "recorder", "plot"),
    "newspaper": ("报纸", "newspaper", "plot"),
    "map": ("地图", "map", "plot"),
    "chip": ("芯片", "chip", "plot"),
    "usb_drive": ("U盘", "USB drive", "plot"),
    "diary": ("日记", "diary", "plot"),
    "file": ("文件", "document file", "plot"),
    "archive": ("档案", "archive file", "plot"),
    "medical_record": ("病历", "medical record", "plot"),
    "ring": ("戒指", "ring", "identity"),
    "earring": ("耳钉", "earring", "identity"),
    "necklace": ("项链", "necklace", "identity"),
    "watch": ("手表", "watch", "identity"),
    "knife": ("刀", "knife", "plot"),
    "gun": ("枪", "gun", "plot"),
    "medicine_bottle": ("药瓶", "medicine bottle", "plot"),
    "jade_pendant": ("玉佩", "jade pendant", "identity"),
    "ledger": ("账本", "ledger", "plot"),
}


@dataclass
class PropValidationResult:
    cleaned_script: ScriptBreakdown
    removed_props: list[dict[str, Any]]
    warnings: list[str]


def normalize_scene_name(name: str) -> str:
    """Return the stable comparison key used to deduplicate scene assets."""
    normalized = unicodedata.normalize("NFKC", name or "")
    return " ".join(normalized.strip().split()).casefold()


def _location_context(prefix: str) -> str:
    """Keep a short stable venue context while dropping broad area prefixes."""
    context = re.sub(r"(?:老城区|新城区|市中心|城郊|市区|城区)", "", prefix)
    context = context.rstrip("的旁边附近")
    if not context:
        return ""
    # A street/road marker gives a better identity than an arbitrary long
    # action phrase, e.g. both ``老城区钟楼街便利店`` and ``钟楼街便利店``
    # become ``钟楼街便利店``.
    marker = max((context.rfind(char) for char in ("街", "路", "巷")), default=-1)
    if marker >= 0:
        return context[max(0, marker - 2): marker + 1]
    return context[-4:] if len(context) > 4 else context


def _physical_location_key(name: str) -> tuple[str, str]:
    """Merge physical venue variants while keeping genuine indoor/outdoor splits."""
    clean = " ".join(unicodedata.normalize("NFKC", name or "").strip().split())
    # Parenthetical/set-dressing/camera details are state data, not identity.
    base = re.sub(r"[（(][^）)]*[）)]", "", clean)
    # Keep middle-dot qualifiers until a venue anchor is extracted. Cutting
    # at the middle dot first turns the two convenience-store variants into
    # unrelated street names and prevents stable venue merging.
    base = re.split(r"[,，、/]", base, maxsplit=1)[0].strip()
    compact = re.sub(r"\s+", "", base)

    # Transitional wording around a named underground entrance should reuse
    # the same exterior reference instead of creating a one-shot location.
    if any(token in compact for token in ("窄巷", "铁门前", "通道入口", "地下通道入口")) and any(
        token in compact for token in ("钟楼", "地下", "废弃")
    ):
        return "underground-entrance#outside", "地下通道入口外部"

    anchors = (
        "便利店", "花店", "咖啡馆", "咖啡店", "餐厅", "酒吧", "商场", "超市",
        "医院", "学校", "公司", "办公室", "公寓", "酒店", "车站", "工厂",
        "仓库", "地下室", "地下通道", "钟表行", "住宅", "房间",
    )
    matches = list(re.finditer("|".join(map(re.escape, anchors)), compact))
    if not matches:
        return normalize_scene_name(clean), clean

    match = matches[-1]
    anchor = match.group(0)
    prefix = compact[: match.start()]
    # ``废弃`` describes condition; it must not split the same underground
    # structure from the non-prefixed form.
    root = f"{_location_context(prefix.replace('废弃', ''))}{anchor}"
    root = root or anchor

    if anchor in {"地下室", "地下通道"}:
        # An ``暗道入口`` is a view from inside the basement toward its
        # passage.  Treating every ``入口`` as an exterior split creates a
        # second location with no reusable set description (and therefore a
        # generic image prompt).  Keep explicit exterior wording separate,
        # but merge interior passage wording into the room identity.
        if any(token in compact for token in ("暗道入口", "暗道内", "地下室内", "通道内")) or (
            anchor == "地下室" and "入口外部" in compact
        ):
            return f"{root}#inside", root
        explicit_entry = any(token in compact for token in ("入口", "门外", "铁门前", "通道口", "楼梯口"))
        if explicit_entry:
            return f"{root}#outside", f"{root}入口外部"
        # ``地下室外部`` is commonly used by the model for a dark/entrance
        # shot that is still inside the same room. Only explicit entry words
        # above create a separate exterior identity.
        return f"{root}#inside", root

    if any(word in compact for word in ("室内", "内部", "店内", "柜台", "货架", "大厅", "走廊", "包间")):
        return f"{root}#inside", f"{root}内部"
    if any(word in compact for word in ("室外", "外部", "门口", "入口", "门外", "屋檐下", "屋檐", "街角", "街景", "外立面", "玻璃门", "停车场")):
        return f"{root}#outside", f"{root}外部"
    return normalize_scene_name(root), root


def stable_location_id(name: str) -> str:
    """Build a filesystem-safe stable ID from a normalized scene name."""
    key = normalize_scene_name(name) or "unnamed-scene"
    return f"loc_{hashlib.sha1(key.encode('utf-8')).hexdigest()[:12]}"


def _stable_asset_id(prefix: str, name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", unicodedata.normalize("NFKC", name or "").casefold()).strip("_")
    if not slug:
        slug = hashlib.sha1((name or prefix).encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{slug[:32]}"


def _looks_voice_only_name(name: str) -> bool:
    key = normalize_scene_name(name)
    return any(term.casefold() in key for term in VOICE_ONLY_NAME_TERMS)


def _scene_text(scene) -> str:
    dialogue = " ".join(f"{line.character} {line.line}" for line in scene.dialogue)
    return " ".join(
        item
        for item in (
            scene.location,
            scene.action_description,
            scene.camera_direction,
            scene.emotion_tone,
            dialogue,
        )
        if item
    )


def _scene_visual_text(scene) -> str:
    """Return only content that can prove an asset is physically visible."""
    return " ".join(
        item
        for item in (scene.action_description, scene.camera_direction)
        if item
    )


_CHARACTER_APPEARANCE_PATTERNS = (
    r"(?:[0-9]{1,3}|[一二三四五六七八九十百]{1,5})岁",
    r"(?:米白|深灰|浅灰|黑|白|暗红|红|蓝|棕|银|深|浅)(?:色)?(?:的)?(?:长风衣|风衣|大衣|雨衣|外套|衬衫|毛衣|卫衣|西装|裙子|牛仔裤|长裤|短裤|皮鞋|运动鞋|靴子|雨靴|口罩|鸭舌帽|帽子|围巾|手套)",
    r"(?:(?:米白|深灰|浅灰|黑|白|暗红|红|蓝|棕|银|深|浅)(?:色)?)?(?:的)?(?:中长发|长发|短发|卷发|直发|黑发|白发|马尾)",
    r"(?:高瘦|瘦高|高大|魁梧|纤细|年轻|中年|少年|少女|冷峻|温和的笑|无温度的眼神)",
    r"(?:戴着?|带着?|佩戴)的?(?:口罩|鸭舌帽|帽子|眼镜|手套|围巾)",
    r"(?:口罩|鸭舌帽|帽子|眼镜|手套|围巾)",
    r"(?:脸型|面型)(?:偏|较|为|呈)?(?:长|圆|偏长|偏圆|椭圆|鹅蛋|瓜子|方|国字|菱形)(?:脸)?",
    r"肤色(?:偏|较|为|呈)?(?:白皙|偏白|自然|健康|小麦|古铜|冷白|暖白|黝黑|苍白)",
    r"(?:眉眼|眉形|眼型)(?:偏|较|为|显得|透出)?(?:冷峻|清秀|深邃|锐利|柔和|英气|凌厉|疲惫|温和|细长|浓密|上挑|下垂|杏眼|凤眼|丹凤眼)",
    r"鼻梁(?:偏|较|为)?(?:高挺|挺直|笔直|自然|低平)",
    r"(?:嘴唇|唇形)(?:偏|较|为)?(?:薄|厚|自然|紧抿|苍白|红润)",
    r"(?:左|右)?(?:眼下|眼角|眉间|眉尾|脸颊|下颌)(?:有|带有|留有)?(?:一颗|一道|一块)?(?:小)?(?:痣|疤|疤痕|胎记)",
    # Keep a broad suffix-based fallback for script-specific adjectives and
    # clothing terms that are not in the small legacy vocabulary above. The
    # relation-based extractor below is preferred, so this only covers bare
    # phrases such as ``银灰色短发`` or ``深棕色制服``.
    r"(?:[一-龥]{0,8}(?:色|黑|白|灰|红|蓝|绿|棕|紫|金|银|米|深|浅|墨|靛|猩红|卡其|酒红)[一-龥]{0,6})(?:的)?(?:发型|头发|发辫|短发|长发|卷发|直发|马尾|制服|衣服|外套|风衣|大衣|雨衣|夹克|衬衫|针织衫|毛衣|卫衣|西装|礼服|裙子|长裙|短裙|裤子|长裤|短裤|鞋子|皮鞋|运动鞋|靴子|帽子|斗篷|长袍|围巾|手套|眼镜|耳环|项链|手链|手表|腕表|胡须|眉毛|眉眼|鼻梁|嘴唇|面容|脸|身形|体态|身材|气质|眼神|笑容|疤痕|胎记|痣)",
)

# Appearance relations are intentionally language-oriented rather than plot-
# oriented. They let a new script describe arbitrary colors, fabrics, cuts,
# accessories, hair styles, or facial traits without adding another title- or
# character-specific word to the vocabulary.
_CHARACTER_APPEARANCE_CUE_RE = re.compile(
    r"(?P<cue>穿(?!过|越|梭)着?|身穿|身着|披着?|戴着?|带着?|佩戴|留着|留有|扎着|蓄着|"
    r"有着|长着|面带|脸上(?:有着|有)|眼神(?:中|里)(?:有着|有|透出|流露出)?|"
    r"气质(?:上)?(?:显得|偏|是)|身形(?:上)?(?:显得|偏|是)|体态(?:上)?(?:显得|偏|是)|"
    r"肤色(?:偏|是)|发型(?:是)?)\s*"
    r"(?P<detail>[^，,、。；;！？!?\n]+)"
)
_CHARACTER_APPEARANCE_STOP_RE = re.compile(
    r"(?:并且|同时|随后|然后|接着|而后|但|却|而|准备|转身|走向|跑向|看向|拿起|握住|"
    r"离开|进入|来到|回头|抬手|低头)"
)
_CHARACTER_LEADING_CLASSIFIER_RE = re.compile(
    r"^(?:一件|一套|一身|一袭|一条|一双|一副|一顶|一头|几缕|一枚|一只|一对)"
)
_CHARACTER_LEADING_RELATION_RE = re.compile(
    r"^(?:他|她|其|该角色|这个人|对方)?"
    r"(?:穿(?!过|越|梭)着?|身穿|身着|披着?|戴着?|带着?|佩戴|留着|留有|扎着|蓄着|"
    r"有着|长着|面带|脸上(?:有着|有)|眼神(?:中|里)(?:有着|有|透出|流露出)?|"
    r"气质(?:上)?(?:显得|偏|是)|身形(?:上)?(?:显得|偏|是)|体态(?:上)?(?:显得|偏|是)|"
    r"肤色(?:偏|是)|发型(?:是)?)\s*"
)
_CHARACTER_INDIRECT_APPEARANCE_CONTEXT_RE = re.compile(
    r"(?:身边|旁边|照片中|相片中|画面中|镜中|反光中|玻璃上|映出|看见|看到|"
    r"注意到|发现|远处|背景中|屏幕中|影像中).{0,12}$"
)
_CHARACTER_VISUAL_FACT_SHAPE_RE = re.compile(
    r"(?:衣|袍|裙|裤|衫|服|鞋|靴|帽|发|辫|眼镜|耳环|项链|手表|胡须|眉|脸|"
    r"面容|身形|体态|身材|气质|眼神|笑|表情|神色|疤|痣|胎记)"
)


def _clean_character_appearance_detail(value: str) -> str:
    """Normalize one relation-bound appearance phrase without adding prose."""
    detail = re.sub(r"\s+", "", str(value or "")).strip("，,。；;、 ")
    if not detail:
        return ""
    detail = _CHARACTER_LEADING_RELATION_RE.sub("", detail)
    detail = _CHARACTER_APPEARANCE_STOP_RE.split(detail, maxsplit=1)[0]
    detail = _CHARACTER_LEADING_CLASSIFIER_RE.sub("", detail)
    detail = detail.strip("，,。；;、的 ")
    if len(detail) < 2 or len(detail) > 32:
        return ""
    # A relation can be followed by a narrative state rather than an identity
    # fact (for example, ``有着动作``). Keep phrases with a recognizable visual
    # noun, and allow descriptive adjectives when the relation itself is a
    # visual one such as ``面带`` or ``气质``.
    if re.search(r"(?:走|跑|拿|握|看|转身|离开|进入|来到|准备|抬手|低头|打开|关闭)", detail):
        if not re.search(
            r"(?:衣|袍|裙|裤|衫|服|鞋|靴|帽|发|眼镜|耳环|项链|手表|脸|面容|"
            r"身形|体态|身材|气质|眼神|笑|疤|痣|肤色)",
            detail,
        ):
            return ""
    return detail


def _character_appearance_facts(text: str, *, allow_indirect: bool = False) -> list[str]:
    """Extract stable-looking appearance facts using generic language cues."""
    candidates: list[tuple[int, str]] = []
    source = str(text or "")
    for match in _CHARACTER_APPEARANCE_CUE_RE.finditer(source):
        if (
            not allow_indirect
            and _CHARACTER_INDIRECT_APPEARANCE_CONTEXT_RE.search(source[:match.start()])
        ):
            continue
        raw_detail = match.group("detail")
        pieces = re.split(r"[、/]+", raw_detail)
        if match.group("cue").startswith(("戴", "带", "佩戴")):
            pieces = [
                part
                for piece in pieces
                for part in re.split(r"(?:和|及|以及)", piece)
            ]
        for offset, piece in enumerate(pieces):
            piece = re.sub(
                r"的(?:人|身影|影子|人物|角色|男人|女人|黑影|背影)$",
                "",
                piece,
            )
            detail = _clean_character_appearance_detail(piece)
            if detail:
                candidates.append((match.start() + offset, detail))
    for value in _find_unique_matches(source, _CHARACTER_APPEARANCE_PATTERNS):
        value_position = source.find(value)
        if (
            not allow_indirect
            and value_position >= 0
            and _CHARACTER_INDIRECT_APPEARANCE_CONTEXT_RE.search(source[:value_position])
        ):
            continue
        embedded_cue = _CHARACTER_APPEARANCE_CUE_RE.search(value)
        if embedded_cue:
            value = _clean_character_appearance_detail(embedded_cue.group(0)) or value
        value = _clean_character_appearance_detail(value) or value
        position = source.find(value)
        candidates.append((position if position >= 0 else len(source), value))

    facts: list[str] = []
    for _position, value in sorted(candidates, key=lambda item: (item[0], -len(item[1]))):
        contained_index = next(
            (index for index, existing in enumerate(facts) if value in existing or existing in value),
            None,
        )
        if contained_index is None:
            facts.append(value)
        elif len(value) > len(facts[contained_index]):
            facts[contained_index] = value
    return facts

_PROP_PHYSICAL_DETAIL_PATTERNS = (
    r"暗红色火漆印章",
    r"生锈(?:的)?铜钥匙",
    r"泛黄旧照片",
    r"(?:一张|一幅)?(?:新|旧)(?:的)?照片",
    r"旧牛皮纸",
    r"折叠刀",
    r"黑色录音机",
    r"一叠档案",
    r"边角(?:已经)?发软",
    r"屏幕(?:亮着|亮起)",
    r"火漆印章",
    r"牛皮纸",
    r"泛黄(?:的)?照片",
    r"(?:黑色|白色|深灰色?|暗红色|红色|银色)(?:的)?(?:手机|照片|钥匙|刀|录音机|档案|信封)",
    r"(?:金属|铜|木质|玻璃|塑料|纸质)(?:材质|外壳|表面)",
)

_ENVIRONMENT_DYNAMIC_CUES = set(DYNAMIC_ENVIRONMENT_CUES) | {
    "站在", "坐在", "握住", "握紧", "拿着", "藏到", "藏在", "走向", "跑向", "推开",
    "打开", "关上", "切断", "撞到", "打斗", "追赶", "倒在", "落在", "往下走",
    "踩过", "拉到", "蹲下", "看了看",
}


def _find_unique_matches(text: str, patterns: tuple[str, ...]) -> list[str]:
    """Return ordered physical fact matches without preserving action prose."""
    matches: list[tuple[int, str]] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text or "", flags=re.IGNORECASE):
            value = re.sub(r"\s+", "", match.group(0)).strip("，。；、 ")
            value = re.sub(r"^(?:戴着?|带着?|佩戴)的?", "", value)
            value = {
                "生锈铜钥匙": "生锈的铜钥匙",
                "泛黄照片": "泛黄旧照片",
            }.get(value, value)
            if value:
                matches.append((match.start(), value))
    result: list[str] = []
    for _position, value in sorted(matches, key=lambda item: item[0]):
        if value not in result and not any(value in existing for existing in result):
            result.append(value)
    return result


_CHARACTER_PRONOUN_PREFIX = re.compile(r"^\s*(?:他|她|其|该角色|这个人|对方)")
_CHARACTER_OBSERVER_OBJECT_CUE = re.compile(r"(?:看见|看到|注意到|发现|望见|瞥见)")
_CHARACTER_IDENTITY_REVEAL_CUE = re.compile(r"(?:露出|揭下|摘下|显露).{0,20}(?:脸|真面目|身份)")
_CHARACTER_ANONYMOUS_ENTITY_RE = re.compile(
    r"(?:一个|一名|某个|某名)?[^。；;！？!?\n]{0,20}"
    r"(?:人|身影|人物|角色|男人|女人|陌生人|黑影)"
)
_CHARACTER_ANAPHORA_RE = re.compile(r"(?:他|她|对方|该人|这个人|身影|人物|黑影)")


def _character_is_clause_subject(clause: str, name: str) -> bool:
    text = str(clause or "").lstrip()
    if text.startswith(name):
        if text[len(name):].lstrip().startswith("的"):
            return False
        return True
    # In observer constructions such as ``甲看见乙穿着红衣``, 乙 is the
    # grammatical subject of the local appearance relation even though the
    # full clause starts with 甲. Recognize only an immediate visual relation
    # so object/possessive mentions do not become identity facts.
    for match in re.finditer(re.escape(name), text):
        prefix = text[:match.start()]
        suffix = text[match.end():].lstrip()
        if (
            _CHARACTER_OBSERVER_OBJECT_CUE.search(prefix[-16:])
            and _CHARACTER_LEADING_RELATION_RE.match(suffix)
        ):
            return True
    # A clause can introduce a second subject after a Chinese comma, e.g.
    # ``对话中，角色甲始终盯着……``. Do not treat possessive/object mentions
    # such as ``角色甲的父亲`` or ``看见角色甲`` as the subject.
    return any(
        part.strip().startswith(name) and not part.strip()[len(name):].lstrip().startswith("的")
        for part in re.split(r"[，,、：:]", text)
    )


def _character_subject_windows(clause: str, name: str, other_names: set[str]) -> list[str]:
    """Limit extraction to the target's local subject span.

    A whole action clause may contain two characters (``甲看见乙穿着红衣``).
    Taking the whole clause would incorrectly assign 乙's clothing to 甲. The
    nearest other named character is therefore a hard boundary; this keeps the
    rule independent of any particular name or plot.
    """
    text = str(clause or "")
    if not _character_is_clause_subject(text, name):
        return []
    windows: list[str] = []
    for target_match in re.finditer(re.escape(name), text):
        start = target_match.start()
        end = len(text)
        for other in other_names:
            if not other or other == name:
                continue
            other_match = re.search(re.escape(other), text[target_match.end():])
            if other_match:
                end = min(end, target_match.end() + other_match.start())
        window = text[start:end].strip()
        if window:
            windows.append(window)
    return windows


def _character_is_revealed_subject(clause: str, name: str) -> bool:
    """Recognize a generic identity-reveal clause such as ``露出甲的脸``."""
    return bool(
        re.search(
            rf"(?:露出|揭下|摘下|显露|揭示).{{0,24}}{re.escape(name)}"
            r"(?:的脸|本人|身份|真面目)",
            str(clause or ""),
        )
    )


def _character_facts_from_scenes(name: str, scenes: list) -> list[str]:
    """Collect stable-looking appearance facts without copying other actors.

    Script prose often places a character name in one clause and describes
    the same person in the adjacent clause (for example, ``角色甲……。他穿着
    外套``). Search one adjacent clause when it is either an explicit
    pronoun continuation or an unambiguous visual description.  A clause that
    names another character is excluded to avoid assigning their clothing to
    the current character.
    """
    facts: list[str] = []
    target_name = str(name or "").strip()
    if not target_name:
        return facts
    for scene in scenes or []:
        action = str(getattr(scene, "action_description", "") or "")
        if target_name not in action:
            continue
        clauses = [
            clause.strip()
            for clause in re.split(r"[。；;！？!?\n]+", action)
            if clause.strip()
        ]
        known_names = {
            str(value or "").strip()
            for value in (getattr(scene, "characters", []) or [])
            if str(value or "").strip()
        }
        known_names.update(
            str(getattr(line, "character", "") or "").strip()
            for line in (getattr(scene, "dialogue", []) or [])
            if str(getattr(line, "character", "") or "").strip()
        )
        other_names = known_names - {target_name}
        target_indexes = [index for index, clause in enumerate(clauses) if target_name in clause]
        for index in target_indexes:
            target_clause = clauses[index]
            target_is_subject = _character_is_clause_subject(target_clause, target_name)
            target_is_revealed = _character_is_revealed_subject(target_clause, target_name)
            if not target_is_subject and not target_is_revealed:
                continue
            candidate_indexes = {index}
            neighbors = [index + 1, index - 1, index - 2]
            for neighbor in neighbors:
                if neighbor < 0 or neighbor >= len(clauses):
                    continue
                clause = clauses[neighbor]
                if any(other and other in clause for other in other_names):
                    continue
                if not _character_appearance_facts(clause, allow_indirect=neighbor < index):
                    continue
                if neighbor > index:
                    if not (target_is_subject or target_is_revealed) or not _CHARACTER_PRONOUN_PREFIX.match(clause):
                        continue
                    if (
                        not target_is_revealed
                        and any(other and other in target_clause for other in other_names)
                        and _CHARACTER_IDENTITY_REVEAL_CUE.search(target_clause)
                    ):
                        continue
                else:
                    # A preceding descriptive clause can be attached without
                    # an explicit pronoun when it ends in a generic anonymous
                    # entity (``一个穿着外套的身影``) or this is the sole named
                    # character in the shot. Otherwise the referent is
                    # ambiguous and must not be guessed.
                    if (
                        not (target_is_subject or target_is_revealed)
                        or _CHARACTER_OBSERVER_OBJECT_CUE.search(target_clause)
                        or (
                            len(known_names) != 1
                            and not _CHARACTER_ANONYMOUS_ENTITY_RE.search(clause)
                        )
                        or (
                            neighbor == index - 2
                            and not any(
                                _CHARACTER_ANAPHORA_RE.search(bridge)
                                for bridge in clauses[neighbor + 1:index]
                            )
                        )
                    ):
                        continue
                candidate_indexes.add(neighbor)
            for candidate in sorted(candidate_indexes):
                if candidate == index and target_is_revealed:
                    continue
                candidate_windows = (
                    _character_subject_windows(clauses[candidate], target_name, other_names)
                    if candidate == index
                    else [clauses[candidate]]
                )
                for window in candidate_windows:
                    for value in _character_appearance_facts(
                        window.removeprefix(target_name),
                        allow_indirect=candidate != index,
                    ):
                        if value not in facts:
                            facts.append(value)
    return facts[:10]


_CHARACTER_FACT_FAMILY_PATTERNS = (
    re.compile(r"(?:衣|袍|裙|裤|衫|服|鞋|靴|外套|风衣|大衣|雨衣|夹克|西装)"),
    re.compile(r"(?:发型|头发|短发|长发|卷发|直发|马尾|发色)"),
    re.compile(r"(?:眼镜|口罩|帽|围巾|手套|耳环|项链|手链|手表|腕表)"),
    re.compile(r"(?:脸型|面型|面容|下颌|肤色|皮肤)"),
    re.compile(r"(?:眉|眼|鼻|唇|嘴|五官)"),
    re.compile(r"(?:身形|体型|体态|身材|高瘦|修长|纤细|健壮|魁梧|微胖)"),
    re.compile(r"(?:痣|疤|疤痕|胎记)"),
)


def _appearance_fact_families(value: str) -> set[int]:
    text = str(value or "")
    return {
        index
        for index, pattern in enumerate(_CHARACTER_FACT_FAMILY_PATTERNS)
        if pattern.search(text)
    }


def _remove_foreign_conflicting_character_facts(
    name: str,
    clauses: list[str],
    own_facts: list[str],
    scenes: list,
) -> list[str]:
    """Drop only facts provably copied from another role and contradicted here."""
    own_families = set().union(*(_appearance_fact_families(value) for value in own_facts)) if own_facts else set()
    if not own_families:
        return clauses

    other_names = {
        str(candidate or "").strip()
        for scene in scenes or []
        for candidate in (getattr(scene, "characters", []) or [])
        if str(candidate or "").strip() and str(candidate or "").strip() != name
    }
    foreign_facts = [
        fact
        for other_name in other_names
        for fact in _character_facts_from_scenes(other_name, scenes)
    ]

    kept: list[str] = []
    for clause in clauses:
        clause_families = _appearance_fact_families(clause)
        conflicts = bool(clause_families & own_families) and any(
            clause in foreign_fact or foreign_fact in clause
            for foreign_fact in foreign_facts
        )
        if not conflicts:
            kept.append(clause)
    return kept


def enrich_character_description(
    name: str,
    description: str,
    scenes: list,
) -> str:
    """Merge stable script facts into a terse character identity anchor.

    Detailed descriptions remain untouched. Short/generated descriptions keep
    their concrete facts and gain only appearance details attributed to the
    same character; plot actions, locations, props, and other actors are never
    copied into the identity profile.
    """
    character_name = str(name or "").strip()
    text = str(description or "").strip()
    facts = _character_facts_from_scenes(character_name, scenes)
    if not facts:
        return text

    compact_length = len(re.sub(r"\s+", "", text))
    if text and compact_length >= 80 and not _is_generic_character_description(text, character_name):
        return text

    generated_placeholder = _is_generic_character_description(text, character_name)
    has_name_prefix = bool(
        character_name
        and re.match(rf"^{re.escape(character_name)}\s*[：:]", text)
    )
    body = re.sub(
        rf"^{re.escape(character_name)}\s*[：:]\s*",
        "",
        text,
    ).strip("；;。 ")
    clauses = [] if generated_placeholder else [
        clause.strip()
        for clause in re.split(r"[；;。]+", body)
        if clause.strip()
    ]
    clauses = _remove_foreign_conflicting_character_facts(
        character_name,
        clauses,
        facts,
        scenes,
    )
    merged: list[str] = []
    for value in clauses + facts:
        value = str(value or "").strip("，,；;。 ")
        if not value:
            continue
        duplicate = next(
            (index for index, existing in enumerate(merged) if value in existing or existing in value),
            None,
        )
        if duplicate is None:
            merged.append(value)
        elif len(value) > len(merged[duplicate]):
            merged[duplicate] = value
    if not merged:
        return text
    prefix = f"{character_name}：" if character_name and (has_name_prefix or not text) else ""
    return f"{prefix}{'；'.join(merged)}。"


def character_profile_missing_fields(description: str) -> list[str]:
    """Return missing stable-identity fields for a Chinese character profile."""
    text = re.sub(r"\s+", "", str(description or ""))
    missing: list[str] = []
    checks = (
        (
            "性别与年龄段",
            bool(re.search(
                r"(?:男性|女性|男孩|女孩|男人|女人|少年|少女|青年|中年|老年|"
                r"(?:^|[，,；;。])(?:男|女)(?:[，,；;。]|$))",
                text,
            ))
            and bool(re.search(
                r"(?:(?:约|大约|近)?[0-9一二三四五六七八九十百]{1,5}岁|"
                r"少年|少女|青年|中年|老年|成年)",
                text,
            )),
        ),
        ("身高或体型", bool(re.search(r"(?:身高|身形|体型|体态|身材|高瘦|修长|匀称|纤细|瘦削|健壮|魁梧|微胖)", text))),
        (
            "脸型与肤色",
            bool(re.search(r"(?:脸型|面型|面容|下颌|脸部|鹅蛋脸|椭圆脸|瓜子脸|方脸|圆脸|国字脸|菱形脸|长脸)", text))
            and bool(re.search(r"(?:肤色|皮肤|白皙|冷白|暖白|小麦色|古铜色|黝黑|苍白)", text)),
        ),
        ("眉眼鼻唇", bool(re.search(r"(?:眉|眼|鼻|唇|嘴|五官)", text))),
        ("发型与发色", bool(re.search(r"(?:发型|头发|短发|长发|中长发|卷发|直发|马尾|发色|黑发|白发|棕发)", text))),
        (
            "服装颜色与材质",
            bool(re.search(r"(?:穿|服装|衣|衫|袍|裙|裤|风衣|大衣|外套|夹克|西装|制服)", text))
            and bool(re.search(r"(?:色|黑|白|灰|红|蓝|绿|棕|紫|米|卡其|棉|羊毛|毛呢|皮革|丝|麻|针织|织物|材质)", text)),
        ),
        ("配饰或识别标志", bool(re.search(r"(?:佩戴|眼镜|耳环|耳钉|项链|手链|手表|腕表|帽|口罩|围巾|手套|痣|疤|胎记|标志|辨识)", text))),
    )
    if len(text) < 80:
        missing.append("完整人物侧写（至少80个中文字符）")
    missing.extend(label for label, passed in checks if not passed)
    return missing


def _prop_facts_from_scenes(
    prop: PropAsset,
    scenes: list,
    other_prop_names: list[str] | None = None,
) -> list[str]:
    names = [str(getattr(prop, "name", "") or "").strip()]
    names.extend(str(value).strip() for value in getattr(prop, "aliases", []) or [] if str(value).strip())
    names = list(dict.fromkeys(names))
    known_physical_prop_names = [
        str(cn_name).strip()
        for cn_name, _english_name, _reason in PLOT_PROP_PATTERNS.values()
        if str(cn_name).strip()
    ]
    all_prop_names = list(dict.fromkeys(
        names
        + [str(value).strip() for value in (other_prop_names or []) if str(value).strip()]
        + known_physical_prop_names
    ))
    facts: list[str] = []
    for scene in scenes or []:
        action = str(getattr(scene, "action_description", "") or "")
        if not any(name and name.casefold() in action.casefold() for name in names):
            continue
        current_occurrences = [
            match
            for name in names
            if name
            for match in re.finditer(re.escape(name), action, flags=re.IGNORECASE)
        ]
        other_occurrences = [
            match
            for name in all_prop_names
            if name and name.casefold() not in {item.casefold() for item in names}
            for match in re.finditer(re.escape(name), action, flags=re.IGNORECASE)
        ]
        major_boundaries = "。；;！？!?\n"
        all_boundaries = major_boundaries + "，,"
        for occurrence in sorted(current_occurrences, key=lambda item: item.start()):
            sentence_start = max(
                (action.rfind(mark, 0, occurrence.start()) for mark in all_boundaries),
                default=-1,
            ) + 1
            sentence_end_candidates = [
                action.find(mark, occurrence.end())
                for mark in major_boundaries
                if action.find(mark, occurrence.end()) >= 0
            ]
            sentence_end = min(sentence_end_candidates, default=len(action))
            previous_other = [
                item.end()
                for item in other_occurrences
                if sentence_start <= item.start() < occurrence.start()
            ]
            next_other = [
                item.start()
                for item in other_occurrences
                if occurrence.end() <= item.start() < sentence_end
            ]
            context_start = max(sentence_start, max(previous_other, default=sentence_start))
            context_end = min(sentence_end, min(next_other, default=sentence_end))
            context = action[context_start:context_end]
            for value in _find_unique_matches(context, _PROP_PHYSICAL_DETAIL_PATTERNS):
                if value not in facts and not any(
                    value in existing or existing in value for existing in facts
                ):
                    facts.append(value)
    return facts[:10]


def _is_legacy_generated_character_fact_list(value: str, name: str) -> bool:
    """Recognize this module's compact ``name：fact；fact`` output only.

    This deliberately requires the canonical name prefix and semicolon-separated
    short facts. A normal user-authored paragraph, even if it mentions a colon,
    remains untouched.
    """
    text = str(value or "").strip()
    target = str(name or "").strip()
    if not text or not target:
        return False
    match = re.match(rf"^{re.escape(target)}\s*[：:]\s*(.+?)\s*[。.!！]?\s*$", text)
    if not match:
        return False
    body = match.group(1).strip()
    parts = [part.strip() for part in re.split(r"[；;]", body) if part.strip()]
    if not parts or any(len(part) > 32 for part in parts):
        return False
    # Multiple concise facts are also a natural format for user-authored
    # profiles. Preserve them and let enrichment append missing script facts.
    if len(parts) > 1:
        return False
    if any(re.search(r"[，,。！？!?]", part) for part in parts):
        return False
    if any(
        marker in body
        for marker in (
            "剧本", "分镜", "保持", "后续", "不根据", "用户", "需要", "应该", "因为", "所以",
        )
    ):
        return False
    # Older versions could persist a single extracted phrase that the current
    # vocabulary no longer recognizes (for example ``脸上的笑容``). Require a
    # visual-noun shape so a human note such as ``调查记者`` is still preserved.
    if not _character_appearance_facts(parts[0]) and not _CHARACTER_VISUAL_FACT_SHAPE_RE.search(parts[0]):
        return False
    return True


def _is_generic_character_description(value: str, name: str = "") -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    # This fallback is generated by this module.  Keep it repairable so a
    # later load can upgrade the description after extraction rules improve.
    if "剧本未提供额外稳定外观细节" in text:
        return True
    if _is_legacy_generated_character_fact_list(text, name):
        return True
    if not any(marker in text for marker in ("画面角色", "角色实体", "出现在分镜", "保持人物身份一致")):
        return False
    return not bool(_character_appearance_facts(text))


def _is_generic_prop_description(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    # Very short placeholders are emitted by older analyzers without the
    # longer ``关键道具``/``保持一致`` marker phrases handled below.
    if normalize_scene_name(text) in {"道具", "物品", "实体道具", "prop", "plot prop"}:
        return True
    if not any(marker in text for marker in ("跨镜头", "线索道具", "外观、位置", "保持一致", "关键道具")):
        return False
    return not bool(_find_unique_matches(text, _PROP_PHYSICAL_DETAIL_PATTERNS))


def _generated_prop_description_needs_repair(
    prop: PropAsset,
    value: str,
    facts: list[str],
) -> bool:
    """Detect a previously generated description carrying another prop's facts."""
    text = str(value or "").strip()
    prefix = f"{str(getattr(prop, 'name', '') or '').strip()}："
    legacy_prefix = f"{str(getattr(prop, 'name', '') or '').strip()}:"
    if not (text.startswith(prefix) or text.startswith(legacy_prefix)):
        return False
    suffix = text[len(prefix):] if text.startswith(prefix) else text[len(legacy_prefix):]
    suffix = suffix.strip().strip("。")
    expected = "；".join(facts)
    return suffix != expected


def _has_voice_or_action_cue(value: str) -> bool:
    text = str(value or "")
    person_action_cues = {
        "背靠", "站立", "站起", "靠墙", "瘫坐", "坐下", "躺着", "跪", "蜷缩", "斜靠", "倚",
        "抬手", "举手", "伸手", "挥手", "摆手", "抵住", "按住", "握住", "轻叩", "猛击", "敲门",
        "拍门", "拍打", "探头", "侧身", "俯身", "弯腰", "直起身", "打量", "扫视", "踮脚",
        "望向", "抬眼", "瞥向", "凝视", "盯着", "神色", "神情",
    }
    if any(cue in text for cue in _ENVIRONMENT_DYNAMIC_CUES | person_action_cues):
        return True
    return bool(re.search(r"(?:是|这是|那是).{0,8}(?:摄像头|声音|录音|脚步)", text))


def _static_fragment_from_clause(clause: str) -> str:
    """Return the legacy noun-only fragment formerly recovered from an action."""
    found = [
        term
        for term in sorted(STATIC_ENVIRONMENT_TERMS, key=len, reverse=True)
        if term and term in clause
    ]
    return "、".join(dict.fromkeys(found[:3]))


def _stable_prefix_before_visual_fluctuation(clause: str) -> str:
    """Keep a qualified fixed sign while discarding its temporary effect.

    ``便利店白色灯牌在积水中晃动`` still establishes a reusable sign, while
    ``在积水中晃动`` is a one-shot reflection state. This narrow rule avoids
    the old broad noun recovery that promoted arbitrary props and actions.
    """
    text = str(clause or "").strip()
    if "灯牌" not in text:
        return ""
    match = re.match(r"^(.{2,80}?灯牌)(?:(?:在|被|映在|映出).{0,80})?(?:晃动|摇晃|闪烁)$", text)
    if not match:
        return ""
    candidate = match.group(1).strip(" ，,、")
    return candidate if candidate and "灯牌" in candidate else ""


def _legacy_dynamic_fragment_sets(scenes: list) -> set[frozenset[str]]:
    """Identify persisted fragments emitted by the old action-noun recovery.

    Older versions promoted up to three static nouns from a dynamic clause,
    such as ``摄像头`` or ``电箱、墙``. Keep this detector only for migrating
    stored text; new extraction never promotes nouns from a dynamic clause.
    """
    fragments: set[frozenset[str]] = set()
    for scene in scenes or []:
        action = str(getattr(scene, "action_description", "") or "")
        for raw in re.split(r"[。；;，,、\n]+", action):
            clause = re.sub(r"\s+", " ", raw).strip(" ，,、")
            if not clause or not _has_voice_or_action_cue(clause):
                continue
            fragment = _static_fragment_from_clause(clause)
            parts = [part.strip() for part in re.split(r"[、,，]+", fragment) if part.strip()]
            if parts:
                fragments.add(frozenset(parts))
    return fragments


def _is_legacy_dynamic_fragment(value: str, fragment_sets: set[frozenset[str]]) -> bool:
    """Return whether a clause is exactly an old recovered noun fragment."""
    parts = [part.strip() for part in re.split(r"[、,，]+", str(value or "")) if part.strip()]
    if not parts or any(part not in STATIC_ENVIRONMENT_TERMS for part in parts):
        return False
    return frozenset(parts) in fragment_sets


def _contains_legacy_dynamic_fragment(value: str, fragment_sets: set[frozenset[str]]) -> bool:
    return any(
        _is_legacy_dynamic_fragment(clause, fragment_sets)
        for clause in _description_clauses(value)
    )


def _clean_environment_description(
    value: str,
    scenes: list,
    legacy_fragments: set[frozenset[str]] | None = None,
    known_character_names: set[str] | None = None,
) -> str:
    """Remove dynamic, voice-only and prop clauses from a location field."""
    text = str(value or "").strip()
    if not text:
        return ""
    legacy_fragments = legacy_fragments if legacy_fragments is not None else _legacy_dynamic_fragment_sets(scenes)
    names = {
        str(name or "").strip()
        for scene in scenes or []
        for name in (getattr(scene, "characters", []) or [])
        if str(name or "").strip()
    }
    names.update(
        str(name or "").strip()
        for name in (known_character_names or set())
        if str(name or "").strip()
    )
    cleaned: list[str] = []
    for raw in re.split(r"[。；;\n]+", text):
        clause = re.sub(r"\s+", " ", raw).strip(" ，,、")
        if len(clause) < 2:
            continue
        if _is_legacy_dynamic_fragment(clause, legacy_fragments):
            continue
        if any(name and name in clause for name in names) and not _is_named_fixed_fixture_clause(clause, names):
            continue
        if any(term and term in clause.casefold() for term in TRANSIENT_ENVIRONMENT_TERMS):
            continue
        if _has_voice_or_action_cue(clause):
            clause = _stable_prefix_before_visual_fluctuation(clause)
            if not clause:
                continue
        if not any(term.casefold() in clause.casefold() for term in STATIC_ENVIRONMENT_TERMS):
            continue
        if clause not in cleaned:
            cleaned.append(clause)
    return "；".join(cleaned)[:700]


def _character_is_visible_in_scene(scene, name: str) -> bool:
    """Conservatively decide whether a character is actually visible in one shot."""
    clean_name = str(name or "").strip()
    if not clean_name or _looks_voice_only_name(clean_name):
        return False

    scene_text = _scene_text(scene)
    dialogue_speakers = {
        str(line.character or "").strip()
        for line in scene.dialogue
        if str(line.character or "").strip()
    }
    if clean_name in dialogue_speakers:
        return True
    if clean_name in scene_text:
        return True

    visible_characters = [
        str(raw_name or "").strip()
        for raw_name in scene.characters
        if str(raw_name or "").strip() and not _looks_voice_only_name(str(raw_name or "").strip())
    ]
    if len(visible_characters) == 1:
        return clean_name == visible_characters[0]

    return False


def _infer_character_gender(script: ScriptBreakdown, name: str) -> str:
    """Infer gender markers from dialogue and action text for pronoun repair."""
    text = str(name or "")
    for scene in script.scenes:
        if name in str(getattr(scene, "action_description", "") or ""):
            text += " " + str(getattr(scene, "action_description", "") or "")
        for line in scene.dialogue:
            if str(line.character or "").strip() == name:
                text += " " + str(line.line or "")
    if re.search(r"(?:女性|女孩|女人|她|少女|女主)", text):
        return "女"
    if re.search(r"(?:男性|男孩|男人|他|少年|男主)", text):
        return "男"
    return ""


def _resolve_generic_pronouns(script: ScriptBreakdown) -> dict[str, str]:
    """Map generic character terms to canonical names before asset extraction.

    Single-character scenes establish an unambiguous mapping. Multi-character
    scenes only use gender agreement when exactly one candidate matches, so an
    ambiguous "他/她" never silently binds to the wrong asset.
    """
    generic_gender = {
        "男生": "男", "男主": "男", "男人": "男", "男子": "男", "男孩": "男", "少年": "男", "小伙": "男",
        "女生": "女", "女主": "女", "女人": "女", "女子": "女", "女孩": "女", "少女": "女", "姑娘": "女",
    }
    def visible_names(scene) -> list[str]:
        return [
            str(name or "").strip()
            for name in (scene.characters or [])
            if str(name or "").strip() and not _looks_voice_only_name(str(name or "").strip())
        ]

    resolved: dict[str, str] = {}
    for scene in script.scenes:
        visible = visible_names(scene)
        if len(visible) != 1:
            continue
        action = str(getattr(scene, "action_description", "") or "")
        for term in generic_gender:
            if term in action:
                resolved[term] = visible[0]

    for scene in script.scenes:
        visible = visible_names(scene)
        if not visible:
            continue
        action = str(getattr(scene, "action_description", "") or "")
        if not action:
            continue
        for term, gender in generic_gender.items():
            if term not in action:
                continue
            target = resolved.get(term, "")
            if not target and len(visible) == 1:
                target = visible[0]
            if not target:
                candidates = [name for name in visible if _infer_character_gender(script, name) == gender]
                if len(candidates) == 1:
                    target = candidates[0]
            if target:
                action = action.replace(term, target)
                resolved[term] = target
        scene.action_description = action
    return resolved


def _revealed_character_aliases(script: ScriptBreakdown) -> dict[str, str]:
    """Resolve explicit disguise reveals without guessing ordinary nicknames."""
    disguise_names = {"黑衣人", "蒙面人", "神秘人", "陌生人", "兜帽人", "面具人"}
    reveal_cues = ("摘下口罩", "摘下蒙面", "摘下面具", "露出真容", "露出一张", "真实身份")
    identity_markers = ("原来是", "正是", "身份是", "真实身份是", "那是")
    known_names = {
        str(name or "").strip()
        for scene in script.scenes
        for name in (scene.characters or [])
        if str(name or "").strip() and not _looks_voice_only_name(str(name or "").strip())
    }
    resolved: dict[str, str] = {}
    for scene in script.scenes:
        action = str(scene.action_description or "")
        if not any(cue in action for cue in reveal_cues):
            continue
        for alias in disguise_names.intersection(known_names):
            reveal_sentence = next((
                sentence.strip()
                for sentence in re.split(r"[。！？!?\n]+", action)
                if alias in sentence and any(cue in sentence for cue in reveal_cues)
            ), "")
            if not reveal_sentence:
                continue
            identity_text = re.split(r"[：:]", reveal_sentence)[-1]
            marker_positions = [
                (identity_text.rfind(marker), marker)
                for marker in identity_markers
                if marker in identity_text
            ]
            if marker_positions:
                position, marker = max(marker_positions, key=lambda item: item[0])
                identity_text = identity_text[position + len(marker):]
            candidates = [
                name for name in known_names
                if name != alias and name in identity_text
            ]
            if len(candidates) == 1:
                resolved[alias] = candidates[0]
    return resolved


def _merge_revealed_character_assets(script: ScriptBreakdown) -> None:
    manifest = script.asset_manifest
    if manifest is None:
        return
    aliases = _revealed_character_aliases(script)
    if not aliases:
        return
    by_name = {item.name: item for item in manifest.characters}
    removed_ids: set[str] = set()
    replacement_ids: dict[str, str] = {}
    for alias, canonical_name in aliases.items():
        alias_asset = by_name.get(alias)
        canonical_asset = by_name.get(canonical_name)
        if alias_asset is None or canonical_asset is None or alias_asset.asset_id == canonical_asset.asset_id:
            continue
        canonical_asset.aliases = list(dict.fromkeys([
            *(canonical_asset.aliases or []), alias, *(alias_asset.aliases or []),
        ]))
        removed_ids.add(alias_asset.asset_id)
        replacement_ids[alias_asset.asset_id] = canonical_asset.asset_id
    if not removed_ids:
        return
    manifest.characters = [item for item in manifest.characters if item.asset_id not in removed_ids]
    for binding in manifest.shot_bindings:
        binding.on_screen_character_ids = list(dict.fromkeys(
            replacement_ids.get(asset_id, asset_id)
            for asset_id in binding.on_screen_character_ids
        ))


def _candidate_field(candidate: Any, field: str, default: Any = "") -> Any:
    """Read an optional compact-LLM candidate from a dict or Pydantic model."""
    if isinstance(candidate, dict):
        return candidate.get(field, default)
    return getattr(candidate, field, default)


def build_manifest_from_scenes(
    script: ScriptBreakdown,
    critical_props: list[Any] | None = None,
) -> VisualAssetManifest:
    """Infer a conservative visual asset manifest when an LLM omits it.

    The generated manifest preserves the existing timed shot list and only
    creates assets that are required for the downstream reference pipeline:
    visible characters, reusable physical locations, and repeated plot props.
    """
    character_names: dict[str, str] = {}
    character_aliases: dict[str, list[str]] = {}
    voice_only_names: dict[str, str] = {}
    on_screen_by_scene: dict[int, list[str]] = {}
    voice_only_by_scene: dict[int, list[str]] = {}
    revealed_aliases = _revealed_character_aliases(script)
    pronoun_aliases = _resolve_generic_pronouns(script)

    for scene in script.scenes:
        visible: list[str] = []
        voice_only: list[str] = []
        for raw_name in scene.characters:
            source_name = str(raw_name or "").strip()
            if not source_name:
                continue
            name = revealed_aliases.get(source_name, source_name)
            if source_name != name:
                character_aliases.setdefault(name, []).append(source_name)
            if _looks_voice_only_name(source_name):
                voice_only_names.setdefault(source_name, source_name)
                voice_only.append(source_name)
            else:
                character_names.setdefault(name, name)
                if _character_is_visible_in_scene(scene, source_name):
                    visible.append(name)
        visible_set = set(visible)
        if not visible:
            visible_characters = [
                revealed_aliases.get(str(raw_name or "").strip(), str(raw_name or "").strip())
                for raw_name in scene.characters
                if str(raw_name or "").strip() and not _looks_voice_only_name(str(raw_name or "").strip())
            ]
            if len(visible_characters) == 1:
                visible = [visible_characters[0]]
                visible_set = set(visible)
        for line in scene.dialogue:
            source_speaker = str(line.character or "").strip()
            if not source_speaker:
                continue
            speaker = revealed_aliases.get(source_speaker, source_speaker)
            if speaker in visible_set and not _looks_voice_only_name(speaker):
                continue
            voice_only_names.setdefault(speaker, speaker)
            voice_only.append(speaker)
        on_screen_by_scene[scene.scene_id] = list(dict.fromkeys(visible))
        voice_only_by_scene[scene.scene_id] = list(dict.fromkeys(voice_only))

    characters = [
        VisualCharacterAsset(
            asset_id=_stable_asset_id("char", name),
            name=name,
            appearance_mode="on_screen",
            description=f"画面角色，出现在分镜 {', '.join(str(scene.scene_id) for scene in script.scenes if name in on_screen_by_scene.get(scene.scene_id, []))}",
            aliases=list(dict.fromkeys(
                character_aliases.get(name, [])
                + [term for term, resolved in pronoun_aliases.items() if resolved == name]
            )),
        )
        for name in character_names
    ]
    characters.extend(
        VisualCharacterAsset(
            asset_id=_stable_asset_id("voice", name),
            name=name,
            appearance_mode="voice_only",
            description="声音、旁白、心声或画外信息来源，不生成角色参考图。",
        )
        for name in voice_only_names
        if name not in character_names
    )
    character_id_by_name = {item.name: item.asset_id for item in characters}

    location_key_to_id: dict[str, str] = {}
    location_key_to_name: dict[str, str] = {}
    location_shots: dict[str, list[int]] = {}
    for scene in script.scenes:
        key, display_name = _physical_location_key(scene.location)
        display_name = display_name or scene.location.strip() or f"场景 {scene.scene_id}"
        key = key or normalize_scene_name(display_name) or f"scene_{scene.scene_id}"
        location_key_to_name.setdefault(key, display_name)
        location_key_to_id.setdefault(key, stable_location_id(display_name))
        location_shots.setdefault(key, []).append(scene.scene_id)

    scene_by_id = {scene.scene_id: scene for scene in script.scenes}
    locations = []
    for key, shot_ids in location_shots.items():
        representative_scene_id = min(shot_ids)
        location_scenes = [scene_by_id[scene_id] for scene_id in sorted(shot_ids)]
        environment = _stable_environment_from_scenes(location_scenes)
        location_id = location_key_to_id[key]
        time_condition = _location_time_condition_from_scenes(location_scenes)
        weather = _location_weather_from_scenes(location_scenes)
        default_state_id = stable_scene_state_id(location_id, time_condition, weather)
        locations.append(
            LocationAsset(
                location_id=location_id,
                canonical_name=location_key_to_name[key],
                identity_description=_stable_location_identity(location_key_to_name[key], environment),
                fixed_dressing=environment,
                anchor_points=_stable_location_anchor_points(environment),
                environment_description=environment,
                time_condition=time_condition,
                representative_scene_id=representative_scene_id,
                shot_ids=sorted(shot_ids),
                states=[LocationState(
                    state_id=default_state_id,
                    label=time_condition or "默认状态",
                    time_condition=time_condition,
                    weather=weather,
                    shot_ids=sorted(shot_ids),
                    representative_scene_id=representative_scene_id,
                )],
                default_state_id=default_state_id,
            )
        )

    prop_scene_ids: dict[str, set[int]] = {}
    prop_name_by_key: dict[str, str] = {}
    prop_reason_by_key: dict[str, str] = {}
    prop_evidence_by_key: dict[str, str] = {}
    prop_description_by_key: dict[str, str] = {}
    prop_aliases_by_key: dict[str, list[str]] = {}
    for scene in script.scenes:
        text = _scene_visual_text(scene)
        for prop_key, (cn_name, english_name, reason) in PLOT_PROP_PATTERNS.items():
            has_alias = prop_key == "letter" and (
                any(alias in text for alias in ("信封", "信件"))
                or bool(re.search(r"[一二三四五六七八九十百零两0-9]+封(?:旧)?信", text))
            )
            if cn_name in text or english_name.casefold() in text.casefold() or has_alias:
                prop_scene_ids.setdefault(prop_key, set()).add(scene.scene_id)
                prop_name_by_key[prop_key] = cn_name
                prop_reason_by_key[prop_key] = reason

    # Compact providers intentionally omit the full manifest to reduce latency,
    # but they can still return a small, evidence-backed list of novel physical
    # props.  Trust only candidates that name at least two source scenes and can
    # be matched to the scene text (including model-provided aliases).
    scene_by_id = {scene.scene_id: scene for scene in script.scenes}
    known_name_to_key = {
        normalize_scene_name(cn_name): prop_key
        for prop_key, (cn_name, english_name, _reason) in PLOT_PROP_PATTERNS.items()
        for cn_name in (cn_name, english_name)
        if str(cn_name or "").strip()
    }
    for candidate in critical_props or []:
        name = str(_candidate_field(candidate, "name", "") or "").strip()
        if not name or _is_non_physical_prop_name(name):
            continue
        aliases = [
            str(value).strip()
            for value in (_candidate_field(candidate, "aliases", []) or [])
            if str(value).strip()
        ]
        aliases = list(dict.fromkeys(aliases))
        search_terms = [name, *aliases]
        requested_ids = {
            int(value)
            for value in (_candidate_field(candidate, "shot_ids", []) or [])
            if str(value).strip().lstrip("-").isdigit() and int(value) in scene_by_id
        }
        matched_ids = {
            scene_id
            for scene_id, scene in scene_by_id.items()
            if (not requested_ids or scene_id in requested_ids)
            and any(
                term.casefold() in _scene_visual_text(scene).casefold()
                for term in search_terms
                if term
            )
        }
        if len(matched_ids) < 2:
            continue
        normalized_search = [normalize_scene_name(term) for term in search_terms if normalize_scene_name(term)]
        key = next(
            (
                known_key
                for known_name, known_key in known_name_to_key.items()
                if known_name and any(known_name in value for value in normalized_search)
            ),
            f"candidate:{normalize_scene_name(name)}",
        )
        prop_scene_ids.setdefault(key, set()).update(matched_ids)
        # A compact candidate may refine a broad dictionary match (for example
        # "红珐琅怀表" is more precise than the generic "手表" pattern).
        prop_name_by_key[key] = name
        prop_reason_by_key[key] = str(_candidate_field(candidate, "critical_reason", "") or "").strip().casefold()
        prop_evidence_by_key[key] = str(_candidate_field(candidate, "critical_evidence", "") or "").strip()
        prop_description_by_key[key] = str(_candidate_field(candidate, "description", "") or "").strip()
        prop_aliases_by_key[key] = aliases

    props: list[PropAsset] = []
    prop_id_by_key: dict[str, str] = {}
    for prop_key, scene_ids in prop_scene_ids.items():
        if len(scene_ids) < 2:
            continue
        name = prop_name_by_key[prop_key]
        prop_id = _stable_asset_id("prop", prop_key)
        prop_id_by_key[prop_key] = prop_id
        description = prop_description_by_key.get(prop_key) or (
            f"{name}作为跨镜头可见线索道具，外观、位置和持有状态需要保持一致。"
        )
        critical_reason = prop_reason_by_key.get(prop_key, "plot")
        critical_evidence = prop_evidence_by_key.get(prop_key) or (
            f"该道具在分镜 {', '.join(str(item) for item in sorted(scene_ids))} 中反复出现并承载剧情信息。"
        )
        props.append(
            PropAsset(
                prop_id=prop_id,
                name=name,
                description=description,
                critical_reason=critical_reason,
                critical_evidence=critical_evidence,
                shot_ids=sorted(scene_ids),
                aliases=prop_aliases_by_key.get(prop_key, []),
                is_handheld=any(
                    word in _scene_visual_text(scene_by_id[scene_id])
                    for scene_id in scene_ids
                    for word in ("拿", "举", "递", "交", "握", "翻", "打开", "塞进")
                ),
            )
        )

    bindings: list[ShotVisualBinding] = []
    for scene in script.scenes:
        key, display_name = _physical_location_key(scene.location)
        display_name = display_name or scene.location.strip() or f"场景 {scene.scene_id}"
        key = key or normalize_scene_name(display_name) or f"scene_{scene.scene_id}"
        bindings.append(
            ShotVisualBinding(
                scene_id=scene.scene_id,
                location_id=location_key_to_id[key],
                on_screen_character_ids=[
                    character_id_by_name[name]
                    for name in on_screen_by_scene.get(scene.scene_id, [])
                    if name in character_id_by_name
                ],
                voice_only_speakers=voice_only_by_scene.get(scene.scene_id, []),
                prop_ids=[
                    prop_id_by_key[prop_key]
                    for prop_key, scene_ids in prop_scene_ids.items()
                    if scene.scene_id in scene_ids and prop_key in prop_id_by_key
                ],
                # Shot action belongs to the shot prompt, never to the
                # reusable location identity or state layers.
                visible_state_notes="",
            )
        )

    return VisualAssetManifest(
        characters=characters,
        locations=locations,
        props=props,
        shot_bindings=bindings,
    )


def normalize_state_text(value: str) -> str:
    """Normalize scene-state text for deterministic comparison."""
    normalized = unicodedata.normalize("NFKC", value or "")
    return " ".join(normalized.strip().split()).casefold()


def stable_scene_state_id(
    location_id: str,
    time_condition: str = "",
    weather: str = "",
    light_direction: str = "",
    visible_state_notes: str = "",
) -> str:
    """Build a project-local stable state ID from visual state fields."""
    payload = "|".join(
        normalize_state_text(value)
        for value in (
            location_id,
            time_condition,
            weather,
            light_direction,
            visible_state_notes,
        )
    )
    digest = hashlib.sha256((payload or "default").encode("utf-8")).hexdigest()[:12]
    return f"state_{digest}"


def scene_source_signature(name: str, description: str) -> str:
    payload = f"{normalize_scene_name(name)}\n{unicodedata.normalize('NFKC', description or '').strip()}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def _description_clauses(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[。；;\n]+", value or "") if part.strip()]


def _is_named_fixed_fixture_clause(clause: str, names: set[str]) -> bool:
    """Allow a named person only when the sentence clearly describes fixed art."""
    text = str(clause or "")
    return bool(
        any(name and name in text for name in names)
        and any(term.casefold() in text.casefold() for term in NAMED_FIXED_FIXTURE_TERMS)
        and any(term.casefold() in text.casefold() for term in STATIC_ENVIRONMENT_TERMS)
    )


def _common_environment_description(values: list[str]) -> str:
    non_empty = [value.strip() for value in values if value and value.strip()]
    if not non_empty:
        return ""
    if len(non_empty) == 1:
        return non_empty[0]
    clause_sets = [set(_description_clauses(value)) for value in non_empty]
    common = set.intersection(*clause_sets) if clause_sets else set()
    if common:
        return "；".join(clause for clause in _description_clauses(non_empty[0]) if clause in common)
    # Inputs have already passed the deterministic environment filter and
    # belong to the same normalized location.  When wording differs enough
    # that there is no exact clause intersection, retain the cleaned anchors
    # from every variant instead of silently discarding all but the first.
    merged: list[str] = []
    for value in non_empty:
        for clause in _description_clauses(value):
            if clause and clause not in merged:
                merged.append(clause)
    return "；".join(merged)[:700]


def _location_time_condition_from_scenes(scenes: list) -> str:
    """Recover a narrow, explicit time label without copying shot actions."""
    values: list[str] = []
    for scene in scenes or []:
        location = str(getattr(scene, "location", "") or "").strip()
        for value in re.findall(r"[（(]([^）)]{1,40})[）)]", location):
            value = re.sub(r"\s+", " ", value).strip(" ，,、")
            if value and value not in values:
                values.append(value)
    if len(values) <= 1:
        parenthetical = values[0] if values else ""
    else:
        # Models often alternate between a specific label (``深夜雨夜``) and
        # its shorter equivalent (``雨夜``) for the same location.
        ordered = sorted(values, key=len, reverse=True)
        parenthetical = ordered[0] if all(
            value in ordered[0] or ordered[0] in value for value in ordered[1:]
        ) else ""

    evidence = _scene_state_evidence(scenes)
    for term in ("凌晨", "深夜", "午夜", "清晨", "早晨", "上午", "正午", "中午", "下午", "傍晚", "黄昏", "夜晚", "白天"):
        if term in evidence:
            return term
    if "雨夜" in evidence:
        return "夜晚"
    return parenthetical


def _location_weather_from_scenes(scenes: list) -> str:
    """Recover only explicit reusable weather, never a transient prop/action."""
    location_text = "。".join(str(getattr(scene, "location", "") or "") for scene in scenes or [])
    outdoor_cues = ("室外", "外部", "门外", "门前", "屋檐", "街道", "街角", "街景", "小巷")
    indoor_cues = ("室内", "内部", "地下室", "地下通道", "房间", "仓库", "走廊", "大厅", "卧室", "客厅")
    evidence = _scene_state_evidence(scenes)
    # Compact LLM manifests often use a generic venue name (for example
    # "医院") and put the actual indoor/outdoor state in the action text.
    # Include both fields, while keeping an explicit exterior location as the
    # stronger signal when the two disagree.
    location_is_indoor = any(cue in location_text for cue in indoor_cues)
    location_is_outdoor = any(cue in location_text for cue in outdoor_cues)
    action_is_indoor = any(cue in evidence for cue in indoor_cues)
    action_is_outdoor = any(cue in evidence for cue in outdoor_cues)
    if location_is_indoor:
        # "地下通道内部，门外暴雨声" is still an indoor state; the exterior
        # cue only describes the weather heard/seen beyond the boundary.
        is_indoor = True
    elif location_is_outdoor:
        is_indoor = False
    else:
        is_indoor = action_is_indoor and not action_is_outdoor
    weather_term = ""
    for term in ("暴雨", "大雨", "雷雨", "中雨", "小雨", "大雪", "暴雪", "小雪", "浓雾", "大雾"):
        if term in evidence:
            weather_term = term
            break
    if not weather_term and any(term in evidence for term in ("雨夜", "雨幕", "雨水", "下雨", "降雨", "阴雨")):
        weather_term = "持续降雨"
    if not weather_term and any(term in evidence for term in ("雪夜", "雪地", "降雪")):
        weather_term = "降雪"
    if not weather_term and "雾" in evidence:
        weather_term = "有雾"
    if not is_indoor:
        return weather_term
    if weather_term and re.search(r"(?:门外|窗外|室外|屋外|外面)[^。；，,]{0,16}" + re.escape(weather_term), evidence):
        return f"室内，无直接降雨；外部{weather_term}" if "雨" in weather_term else f"室内；外部{weather_term}"
    return ""


def _scene_state_evidence(scenes: list) -> str:
    """Build complete location/action evidence without truncating long shots."""
    return "。".join(
        f"{getattr(scene, 'location', '')}。{str(getattr(scene, 'action_description', '') or '')}"
        for scene in scenes or []
    )


def _recover_static_fixture_clause(clause: str, names: set[str]) -> str:
    """Keep reusable set dressing when a clause also names an actor or plot prop."""
    candidate = str(clause or "").strip(" ，,、")
    for name in sorted((name for name in names if name), key=len, reverse=True):
        if candidate.startswith(name):
            candidate = candidate[len(name):].lstrip(" ，,、")
            break
    # Once the leading actor is removed, another named person means the clause
    # still describes an interaction or movement.  Treating a static noun in
    # that sentence (for example "two alleys") as set dressing contaminates
    # every later view of the location.
    if any(name and name in candidate for name in names):
        return ""
    candidate = re.sub(r"^(?:转身)?(?:看向|望向|注视|走向|靠近|来到|进入|推开)", "", candidate).strip(" ，,、")
    parts = [part.strip() for part in re.split(r"[、]|(?:和|与|及)", candidate) if part.strip()]
    stable_parts: list[str] = []
    for part in parts:
        if any(term.casefold() in part.casefold() for term in TRANSIENT_ENVIRONMENT_TERMS):
            break
        stable_parts.append(part)
    candidate = "和".join(stable_parts).strip(" ，,、")
    if not candidate or any(term.casefold() in candidate.casefold() for term in TRANSIENT_ENVIRONMENT_TERMS):
        return ""
    if not any(term.casefold() in candidate.casefold() for term in STATIC_ENVIRONMENT_TERMS):
        return ""
    if _has_voice_or_action_cue(candidate):
        candidate = _stable_prefix_before_visual_fluctuation(candidate)
    return candidate.strip(" ，,、")


def _stable_environment_from_scenes(scenes: list) -> str:
    """Extract reusable set facts without copying a whole shot action.

    Compact LLM providers intentionally omit ``asset_manifest``.  The previous
    fallback therefore left every location's environment empty, while the old
    fallback (before the cleanup) copied complete action paragraphs into the
    location and polluted every reference view.  Keep only concrete clauses
    containing a static-space term, and discard clauses that mention a visible
    character or a plot prop.  The result is a bounded, deterministic hint, not
    a second narrative description.
    """
    collected: list[str] = []
    seen: set[str] = set()
    for scene in scenes or []:
        names = {
            str(value or "").strip()
            for value in getattr(scene, "characters", []) or []
            if str(value or "").strip()
        }
        names.update(
            str(getattr(line, "character", "") or "").strip()
            for line in getattr(scene, "dialogue", []) or []
            if str(getattr(line, "character", "") or "").strip()
        )
        names.update({
            "人物", "两人", "他们", "她们", "他", "她",
            "黑衣人", "蒙面人", "神秘人", "陌生人", "兜帽人", "面具人",
        })
        names.update({"男生", "女生", "男主", "女主", "男人", "女人", "男子", "女子", "男孩", "女孩", "少年", "少女", "小伙", "姑娘"})
        action = str(getattr(scene, "action_description", "") or "")
        # Commas are useful boundaries for Chinese action prose: the static
        # tail often follows a short movement clause in the same sentence.
        clauses = re.split(r"[。；;，,、\n]+", action)
        for raw in clauses:
            clause = re.sub(r"\s+", " ", raw).strip(" ，,、")
            if len(clause) < 4:
                continue
            if any(name and name in clause for name in names) or any(
                term in clause.casefold() for term in TRANSIENT_ENVIRONMENT_TERMS
            ):
                clause = _recover_static_fixture_clause(clause, names)
                if not clause:
                    continue
            if _has_voice_or_action_cue(clause):
                # A reusable location identity may only use an independent
                # static environment clause. Never recover bare nouns from a
                # narrative action, otherwise a one-shot camera, prop or door
                # leaks into every scene reference for the same location.
                clause = _stable_prefix_before_visual_fluctuation(clause)
                if not clause:
                    continue
            if not any(term in clause for term in STATIC_ENVIRONMENT_TERMS):
                continue
            key = normalize_scene_name(clause)
            if key in seen:
                continue
            seen.add(key)
            collected.append(clause)
            if len("；".join(collected)) >= 700:
                return "；".join(collected)[:700]
    return "；".join(collected)


def _stable_location_anchor_points(environment: str) -> list[str]:
    """Extract reusable spatial anchors from filtered static environment text."""
    anchors: list[str] = []
    for raw in re.split(r"[；;。\n]+", str(environment or "")):
        clause = re.sub(r"\s+", " ", raw).strip(" ，,、")
        if len(clause) < 2:
            continue
        if not any(term.casefold() in clause.casefold() for term in STATIC_ENVIRONMENT_TERMS):
            continue
        if _has_voice_or_action_cue(clause):
            continue
        if clause not in anchors:
            anchors.append(clause[:80])
        if len(anchors) >= 6:
            break
    return anchors


def _stable_location_identity(location_name: str, environment: str) -> str:
    """Build a stable physical-space identity for compact-manifest projects."""
    if not str(environment or "").strip():
        return ""
    return (
        f"物理地点身份：{location_name}；可复用固定空间，保持同一建筑或房间边界、"
        "空间比例、入口方向和主要陈设不变。"
    )


def _is_generic_location_identity(value: str, location_name: str, environment: str) -> bool:
    """Identify only our boilerplate identity, preserving detailed LLM/user facts."""
    text = re.sub(r"\s+", "", str(value or ""))
    generated = re.sub(r"\s+", "", _stable_location_identity(location_name, environment))
    return bool(text and text == generated)


def _location_identity_needs_repair(identity: str, location_name: str, environment: str, scenes: list) -> bool:
    """Reject narrative contamination without erasing detailed spatial facts."""
    text = str(identity or "").strip()
    if not text or _is_generic_location_identity(text, location_name, environment):
        return True
    names = {
        str(name or "").strip()
        for scene in scenes or []
        for name in (getattr(scene, "characters", []) or [])
        if str(name or "").strip()
    }
    if any(name in text for name in names):
        return True
    if any(term.casefold() in text.casefold() for term in TRANSIENT_ENVIRONMENT_TERMS):
        return True
    static_fact_count = sum(
        1 for term in STATIC_ENVIRONMENT_TERMS if term and term.casefold() in text.casefold()
    )
    return _has_voice_or_action_cue(text) and static_fact_count < 2


def _safe_location_text(value: str, scene_actions: set[str]) -> str:
    """Drop legacy fields that are verbatim copies of a shot action."""
    text = str(value or "").strip()
    if not text or text in scene_actions:
        return ""
    return text


def _merge_environment_text(*values: str) -> str:
    clauses: list[str] = []
    for value in values:
        for clause in _description_clauses(value):
            clause = clause.strip()
            if clause and clause not in clauses:
                clauses.append(clause)
    return "；".join(clauses)[:700]


def enrich_manifest_descriptions(
    script: ScriptBreakdown,
    *,
    source_text: str = "",
) -> ScriptBreakdown:
    """Fill generic manifest descriptions from already parsed shot facts.

    This is deliberately deterministic. It repairs the common LLM failure mode
    where structurally valid assets contain placeholders, while leaving a
    detailed human/LLM description intact.
    """
    manifest = getattr(script, "asset_manifest", None)
    if manifest is None:
        return script

    scenes_by_id = {scene.scene_id: scene for scene in script.scenes}
    bindings_by_scene = {binding.scene_id: binding for binding in manifest.shot_bindings}
    character_names_by_id = {item.asset_id: item.name for item in manifest.characters}
    known_character_names = {
        str(name or "").strip()
        for scene in script.scenes
        for name in (getattr(scene, "characters", []) or [])
        if str(name or "").strip()
    }
    known_character_names.update(
        str(getattr(line, "character", "") or "").strip()
        for scene in script.scenes
        for line in (getattr(scene, "dialogue", []) or [])
        if str(getattr(line, "character", "") or "").strip()
    )
    for character in manifest.characters:
        if str(character.name or "").strip():
            known_character_names.add(str(character.name).strip())
        known_character_names.update(
            str(alias or "").strip()
            for alias in (character.aliases or [])
            if str(alias or "").strip()
        )

    character_fact_scenes = list(script.scenes)
    saved_source = str(source_text or "").strip()
    if saved_source:
        character_fact_scenes.append(SimpleNamespace(
            action_description=saved_source,
            characters=[
                item.name
                for item in manifest.characters
                if str(getattr(item, "name", "") or "").strip()
            ],
            dialogue=[],
        ))

    for character in manifest.characters:
        if character.appearance_mode != "on_screen":
            continue
        repaired = enrich_character_description(
            character.name,
            character.description,
            character_fact_scenes,
        )
        if repaired and repaired != character.description:
            character.description = repaired
        elif _is_generic_character_description(character.description, character.name):
            character.description = (
                f"{character.name}：剧本未提供额外稳定外观细节；后续镜头保持同一脸型、发型、"
                "体态和服装，不根据单个动作临时改造身份。"
            )

    for prop in manifest.props:
        bound_scenes = [
            scenes_by_id[scene_id]
            for scene_id in prop.shot_ids
            if scene_id in scenes_by_id
        ]
        facts = _prop_facts_from_scenes(
            prop,
            bound_scenes,
            [item.name for item in manifest.props if item.prop_id != prop.prop_id],
        )
        needs_repair = _is_generic_prop_description(prop.description) or _generated_prop_description_needs_repair(
            prop,
            prop.description,
            facts,
        )
        if not needs_repair:
            continue
        if facts:
            prop.description = f"{prop.name}：{'；'.join(facts)}。"
        else:
            prop.description = (
                f"{prop.name}：单一实体道具；保持同一轮廓、材质、颜色、尺寸比例和可见状态，"
                "不添加文字、水印或其他道具。"
            )

    for location in manifest.locations:
        location_scenes = [
            scenes_by_id[scene_id]
            for scene_id in (location.shot_ids or [location.representative_scene_id])
            if scene_id in scenes_by_id
        ]
        inferred_environment = _stable_environment_from_scenes(location_scenes)
        legacy_fragments = _legacy_dynamic_fragment_sets(location_scenes)

        existing_environment = str(location.environment_description or "").strip()
        cleaned_environment = _clean_environment_description(
            existing_environment,
            location_scenes,
            legacy_fragments,
            known_character_names,
        )
        if cleaned_environment:
            environment = _merge_environment_text(cleaned_environment, inferred_environment)
        elif existing_environment and not _has_voice_or_action_cue(existing_environment) and not (
            "可复用固定空间" in existing_environment or "固定空间" in existing_environment
        ) and not _contains_legacy_dynamic_fragment(existing_environment, legacy_fragments):
            # Preserve a genuinely custom description even when it uses words
            # outside the deterministic vocabulary.
            environment = existing_environment
        else:
            environment = inferred_environment

        # Keep all three reusable location fields on the same deterministic
        # cleaning path.  Previously this block was guarded by ``if
        # environment``; legacy projects with an empty inferred environment
        # therefore retained action prose in ``fixed_dressing`` and anchors.
        location.environment_description = environment

        existing_dressing = str(location.fixed_dressing or "").strip()
        cleaned_dressing = _clean_environment_description(
            existing_dressing,
            location_scenes,
            legacy_fragments,
            known_character_names,
        )
        if cleaned_dressing:
            location.fixed_dressing = _merge_environment_text(cleaned_dressing, environment)
        elif (
            not existing_dressing
            or _has_voice_or_action_cue(existing_dressing)
            or _contains_legacy_dynamic_fragment(existing_dressing, legacy_fragments)
        ):
            location.fixed_dressing = environment

        clean_anchors: list[str] = []
        for anchor in location.anchor_points or []:
            cleaned_anchor = _clean_environment_description(
                anchor,
                location_scenes,
                legacy_fragments,
                known_character_names,
            )
            candidate = cleaned_anchor or (
                str(anchor).strip()
                if not _is_legacy_dynamic_fragment(str(anchor), legacy_fragments)
                and not _has_voice_or_action_cue(str(anchor))
                and any(term in str(anchor) for term in STATIC_ENVIRONMENT_TERMS)
                else ""
            )
            if candidate:
                clean_anchors.extend(_description_clauses(candidate))
        location.anchor_points = list(dict.fromkeys(
            clean_anchors + _stable_location_anchor_points(environment)
        ))[:6]

        identity = str(location.identity_description or "").strip()
        if _location_identity_needs_repair(identity, location.canonical_name, environment, location_scenes):
            location.identity_description = _stable_location_identity(location.canonical_name, environment)

    # Keep this local read so linters and future callers can detect that all
    # bindings were considered; it also documents that voice-only speakers are
    # never promoted into character descriptions.
    _ = {
        speaker
        for binding in bindings_by_scene.values()
        for speaker in binding.voice_only_speakers
        if speaker and speaker not in character_names_by_id.values()
    }
    return script


def normalize_scene_assets(script: ScriptBreakdown) -> ScriptBreakdown:
    """Merge same-name locations and ensure every shot has one stable binding."""
    manifest = script.asset_manifest
    if manifest is None:
        return script

    _merge_revealed_character_assets(script)

    name_by_id = {character.asset_id: character.name for character in manifest.characters}
    id_by_name = {character.name: character.asset_id for character in manifest.characters}
    known_character_names = {
        str(character.name or "").strip()
        for character in manifest.characters
        if str(character.name or "").strip()
    }
    existing_by_id = {location.location_id: location for location in manifest.locations}
    grouped: dict[str, list[LocationAsset]] = {}
    display_names: dict[str, str] = {}
    for location in manifest.locations:
        key, display_name = _physical_location_key(location.canonical_name)
        if not key:
            continue
        grouped.setdefault(key, []).append(location)
        display_names.setdefault(
            key, display_name
        )

    bindings_by_scene: dict[int, ShotVisualBinding] = {}
    for binding in manifest.shot_bindings:
        current = bindings_by_scene.get(binding.scene_id)
        if current is None:
            bindings_by_scene[binding.scene_id] = binding
            continue
        current.on_screen_character_ids = list(dict.fromkeys(current.on_screen_character_ids + binding.on_screen_character_ids))
        current.voice_only_speakers = list(dict.fromkeys(current.voice_only_speakers + binding.voice_only_speakers))
        current.prop_ids = list(dict.fromkeys(current.prop_ids + binding.prop_ids))

    # Legacy projects stored the complete shot action in the binding's
    # ``visible_state_notes`` field.  Clear only exact action copies here; a
    # genuine state override remains available to the scene-state layer.
    for scene in script.scenes:
        binding = bindings_by_scene.get(scene.scene_id)
        if not binding:
            continue
        action = str(scene.action_description or "").strip()
        if action and str(binding.visible_state_notes or "").strip() == action:
            binding.visible_state_notes = ""
        if action and str(binding.state_override_notes or "").strip() == action:
            binding.state_override_notes = ""

    for scene in script.scenes:
        binding = bindings_by_scene.get(scene.scene_id)
        location = existing_by_id.get(binding.location_id) if binding else None
        name = location.canonical_name if location else scene.location
        key, inferred_display_name = _physical_location_key(name)
        key = key or normalize_scene_name(f"场景 {scene.scene_id}")
        name = name.strip() or f"场景 {scene.scene_id}"
        if key not in grouped:
            grouped[key] = [LocationAsset(
                location_id=stable_location_id(name),
                canonical_name=name,
                environment_description="",
                representative_scene_id=scene.scene_id,
                shot_ids=[scene.scene_id],
            )]
            display_names[key] = inferred_display_name or name
        if binding is None:
            bindings_by_scene[scene.scene_id] = ShotVisualBinding(
                scene_id=scene.scene_id,
                location_id=stable_location_id(display_names[key]),
            )
        else:
            visible_names = [
                name_by_id[character_id]
                for character_id in binding.on_screen_character_ids
                if character_id in name_by_id and _character_is_visible_in_scene(scene, name_by_id[character_id])
            ]
            if visible_names:
                binding.on_screen_character_ids = [
                    id_by_name[name]
                    for name in visible_names
                    if name in id_by_name
                ]

    key_to_id: dict[str, str] = {}
    old_to_new: dict[str, str] = {}
    old_to_state: dict[str, str] = {}
    state_id_replacements_by_location: dict[str, dict[str, str]] = {}
    default_state_by_location: dict[str, str] = {}
    normalized_locations: list[LocationAsset] = []
    for key, locations in grouped.items():
        name = display_names[key]
        location_id = stable_location_id(name)
        old_ids = {item.location_id for item in locations}
        shot_ids = {
            scene.scene_id
            for scene in script.scenes
            if (binding := bindings_by_scene.get(scene.scene_id))
            and binding.location_id in old_ids
        }
        if not shot_ids:
            continue
        scene_actions = {
            str(scene.action_description or "").strip()
            for scene in script.scenes
            if (binding := bindings_by_scene.get(scene.scene_id))
            and binding.location_id in {item.location_id for item in locations}
            and str(scene.action_description or "").strip()
        }
        grouped_scenes = [
            scene
            for scene in script.scenes
            if (binding := bindings_by_scene.get(scene.scene_id))
            and binding.location_id in {item.location_id for item in locations}
        ]
        inferred_environment = _stable_environment_from_scenes(grouped_scenes)
        legacy_fragments = _legacy_dynamic_fragment_sets(grouped_scenes)
        inferred_time_condition = _location_time_condition_from_scenes(grouped_scenes)
        inferred_weather = _location_weather_from_scenes(grouped_scenes)
        time_values = {item.time_condition.strip() for item in locations if item.time_condition.strip()}
        if not time_values and inferred_time_condition:
            time_values.add(inferred_time_condition)
        state_shots: dict[str, set[int]] = {}
        state_meta: dict[str, dict[str, str]] = {}
        for item in locations:
            states_by_id = {
                state.state_id: state
                for state in (getattr(item, "states", []) or [])
                if getattr(state, "state_id", "")
            }
            state_id = getattr(item, "default_state_id", "") or (
                item.states[0].state_id if getattr(item, "states", None) else ""
            )
            if not state_id:
                state_id = stable_scene_state_id(location_id, item.time_condition)
            old_to_state[item.location_id] = state_id
            state_meta.setdefault(state_id, {
                "time_condition": item.time_condition.strip() or inferred_time_condition,
                "label": item.time_condition.strip() or inferred_time_condition or "默认状态",
                "weather": inferred_weather,
                "light_direction": "",
                "visible_state_notes": "",
            })
            for state in getattr(item, "states", []) or []:
                if state.state_id == state_id:
                    state_meta[state_id] = {
                        "time_condition": state.time_condition.strip() or item.time_condition.strip() or inferred_time_condition,
                        "label": state.label.strip() or item.time_condition.strip() or "默认状态",
                        "weather": state.weather.strip() or inferred_weather,
                        "light_direction": state.light_direction.strip(),
                        "visible_state_notes": state.visible_state_notes.strip(),
                    }
                    break
            for scene_id in sorted(
                scene.scene_id
                for scene in script.scenes
                if (binding := bindings_by_scene.get(scene.scene_id))
                and binding.location_id == item.location_id
            ):
                binding = bindings_by_scene[scene_id]
                scene = next((candidate for candidate in script.scenes if candidate.scene_id == scene_id), None)
                effective_state_id = binding.state_id or state_id
                source_state = states_by_id.get(effective_state_id)
                if source_state is not None:
                    state_meta[effective_state_id] = {
                        "time_condition": source_state.time_condition.strip() or item.time_condition.strip() or inferred_time_condition,
                        "label": source_state.label.strip() or source_state.time_condition.strip() or item.time_condition.strip() or inferred_time_condition or "默认状态",
                        "weather": source_state.weather.strip() or inferred_weather,
                        "light_direction": source_state.light_direction.strip(),
                        "visible_state_notes": _safe_location_text(source_state.visible_state_notes, scene_actions),
                    }
                elif effective_state_id not in state_meta:
                    notes = _safe_location_text(
                        binding.state_override_notes.strip()
                        or binding.visible_state_notes.strip(),
                        scene_actions,
                    )
                    state_meta[effective_state_id] = {
                        "time_condition": item.time_condition.strip() or inferred_time_condition,
                        "label": notes[:40] or item.time_condition.strip() or inferred_time_condition or "默认状态",
                        "weather": inferred_weather,
                        "light_direction": "",
                        "visible_state_notes": notes,
                    }
                state_shots.setdefault(effective_state_id, set()).add(scene_id)
        if not state_shots:
            state_id = stable_scene_state_id(location_id, next(iter(time_values), ""))
            state_shots[state_id] = set(shot_ids)
            state_meta[state_id] = {
                "time_condition": next(iter(time_values), ""),
                "label": next(iter(time_values), "") or "默认状态",
                "weather": inferred_weather,
                "light_direction": "",
                "visible_state_notes": "",
            }
        deduplicated_shots: dict[str, set[int]] = {}
        deduplicated_meta: dict[str, dict[str, str]] = {}
        canonical_by_signature: dict[tuple[str, str, str, str], str] = {}
        state_id_replacements: dict[str, str] = {}
        ordered_states = sorted(
            state_shots.items(),
            key=lambda item: min(item[1]) if item[1] else 10**9,
        )
        for state_id, shots in ordered_states:
            meta = state_meta.get(state_id, {})
            signature = (
                normalize_state_text(meta.get("time_condition", "")),
                normalize_state_text(meta.get("weather", "")),
                normalize_state_text(meta.get("light_direction", "")),
                normalize_state_text(meta.get("visible_state_notes", "")),
            )
            canonical_id = canonical_by_signature.setdefault(signature, state_id)
            state_id_replacements[state_id] = canonical_id
            deduplicated_shots.setdefault(canonical_id, set()).update(shots)
            deduplicated_meta.setdefault(canonical_id, meta)
        state_shots = deduplicated_shots
        state_meta = deduplicated_meta
        states = [
            LocationState(
                state_id=state_id,
                label=state_meta.get(state_id, {}).get("label", "") or "默认状态",
                time_condition=state_meta.get(state_id, {}).get("time_condition", ""),
                weather=state_meta.get(state_id, {}).get("weather", ""),
                light_direction=state_meta.get(state_id, {}).get("light_direction", ""),
                visible_state_notes=state_meta.get(state_id, {}).get("visible_state_notes", ""),
                shot_ids=sorted(shots),
                representative_scene_id=min(shots) if shots else min(shot_ids),
            )
            for state_id, shots in sorted(state_shots.items(), key=lambda item: min(item[1]) if item[1] else 10**9)
        ]
        default_state_id = states[0].state_id
        safe_identity = [
            _safe_location_text(getattr(item, "identity_description", ""), scene_actions)
            for item in locations
        ]

        def clean_reusable_location_text(value: str) -> str:
            """Remove shot actions while preserving non-narrative custom text."""
            raw = str(value or "").strip()
            if not raw or raw in scene_actions:
                return ""
            cleaned = _clean_environment_description(
                raw,
                grouped_scenes,
                legacy_fragments,
                known_character_names,
            )
            if cleaned:
                return cleaned
            if _has_voice_or_action_cue(raw) or _contains_legacy_dynamic_fragment(raw, legacy_fragments):
                return ""
            return raw

        safe_environment = [
            clean_reusable_location_text(getattr(item, "environment_description", ""))
            for item in locations
        ]
        if not any(safe_environment) and inferred_environment:
            safe_environment.append(inferred_environment)
        environment_text = _common_environment_description(safe_environment)
        identity_text = _common_environment_description(safe_identity)
        if not identity_text and environment_text:
            identity_text = _stable_location_identity(name, environment_text)
        fixed_dressing_text = _common_environment_description(
            [clean_reusable_location_text(getattr(item, "fixed_dressing", "")) for item in locations]
        )
        if not fixed_dressing_text:
            fixed_dressing_text = environment_text
        anchor_points: list[str] = []
        for item in locations:
            for anchor in getattr(item, "anchor_points", []) or []:
                cleaned_anchor = clean_reusable_location_text(anchor)
                if cleaned_anchor:
                    anchor_points.extend(_description_clauses(cleaned_anchor))
        anchor_points = list(dict.fromkeys(anchor_points))
        if not anchor_points:
            anchor_points = _stable_location_anchor_points(environment_text)
        normalized_locations.append(LocationAsset(
            location_id=location_id,
            canonical_name=name,
            identity_description=identity_text,
            fixed_dressing=fixed_dressing_text,
            anchor_points=anchor_points,
            time_condition=next(iter(time_values)) if len(time_values) == 1 else "",
            environment_description=_common_environment_description(safe_environment),
            representative_scene_id=min(shot_ids),
            shot_ids=sorted(shot_ids),
            states=states,
            default_state_id=default_state_id,
            aliases=list(dict.fromkeys(
                [item.canonical_name for item in locations if item.canonical_name != name]
                + [alias for item in locations for alias in item.aliases]
            )),
        ))
        key_to_id[key] = location_id
        old_to_new.update({item.location_id: location_id for item in locations})
        default_state_by_location[location_id] = default_state_id
        state_id_replacements_by_location[location_id] = state_id_replacements

    normalized_ids = {item.location_id for item in normalized_locations}
    for scene in script.scenes:
        binding = bindings_by_scene[scene.scene_id]
        if binding.location_id in old_to_new:
            if not binding.state_id:
                binding.state_id = old_to_state.get(binding.location_id, "")
            binding.location_id = old_to_new[binding.location_id]
        elif binding.location_id not in normalized_ids:
            scene_key, _ = _physical_location_key(scene.location)
            binding.location_id = key_to_id[scene_key]
        if not binding.state_id:
            binding.state_id = default_state_by_location.get(binding.location_id, "")
        replacements = state_id_replacements_by_location.get(binding.location_id, {})
        binding.state_id = replacements.get(binding.state_id, binding.state_id)

    manifest.locations = sorted(normalized_locations, key=lambda item: item.representative_scene_id)
    manifest.shot_bindings = [bindings_by_scene[scene.scene_id] for scene in script.scenes]
    return script


def reconcile_prop_bindings(script: ScriptBreakdown) -> list[str]:
    """Rebuild prop shot lists from the actual per-shot bindings."""
    if not script.asset_manifest:
        return []

    bound_shots_by_prop: dict[str, set[int]] = {}
    for binding in script.asset_manifest.shot_bindings:
        for prop_id in dict.fromkeys(binding.prop_ids):
            bound_shots_by_prop.setdefault(prop_id, set()).add(binding.scene_id)

    changed: list[str] = []
    for prop in script.asset_manifest.props:
        actual_shot_ids = sorted(bound_shots_by_prop.get(prop.prop_id, set()))
        if prop.shot_ids != actual_shot_ids:
            prop.shot_ids = actual_shot_ids
            changed.append(prop.prop_id)
        _sync_prop_evidence_shot_list(prop, actual_shot_ids)
    return changed


_PROP_EVIDENCE_SHOT_LIST = re.compile(
    r"(?P<prefix>分镜|镜头|shots?|scenes?)\s*(?P<ids>#?\d+(?:(?:\s*[,，、和及/&]\s*|(?=镜头))#?\d+)*)",
    re.IGNORECASE,
)


def _sync_prop_evidence_shot_list(prop: Any, shot_ids: list[int]) -> None:
    """Update only an explicit evidence shot-number list after reconciliation.

    Evidence prose remains authored text.  When it contains the conventional
    ``分镜 1, 2``/``shots 1, 2`` list emitted by the manifest builder, replace
    that denormalized list with the binding source of truth so the UI cannot
    report a stale shot as evidence.  Sentences without this explicit form are
    left untouched rather than guessing at narrative numbers.
    """
    evidence = str(getattr(prop, "critical_evidence", "") or "").strip()
    if not evidence or not shot_ids:
        return
    formatted_ids = ", ".join(str(scene_id) for scene_id in shot_ids)

    def replace(match: re.Match[str]) -> str:
        prefix = match.group("prefix")
        return f"{prefix} {formatted_ids}"

    updated, count = _PROP_EVIDENCE_SHOT_LIST.subn(replace, evidence, count=1)
    if count:
        prop.critical_evidence = updated


def _prop_has_semantic_critical_evidence(
    prop: PropAsset,
    reason: str,
    evidence: str,
    scenes: list[Any] | None = None,
) -> bool:
    """Require an observable story/continuity role beyond an LLM label.

    The parser is allowed to discover arbitrary prop names, so this check does
    not use a whitelist.  It combines the model's evidence with the source
    actions for the bound scenes and applies a reason-specific vocabulary.  A
    phrase such as ``appears in two shots`` is intentionally insufficient for
    ``plot``; the same phrase is valid for ``continuity`` only when it states
    an identity/continuity requirement.
    """
    names = [
        str(getattr(prop, "name", "") or "").strip(),
        *[
            str(value).strip()
            for value in (getattr(prop, "aliases", []) or [])
            if str(value).strip()
        ],
    ]
    relevant_actions: list[str] = []
    for scene in scenes or []:
        action = str(getattr(scene, "action_description", "") or "")
        clauses = re.split(r"[。；;，,、\n]+", action)
        relevant_actions.extend(
            clause.strip()
            for clause in clauses
            if clause.strip() and any(name.casefold() in clause.casefold() for name in names if name)
        )
    joined = " ".join([str(evidence or ""), *names, *relevant_actions]).casefold()
    if not joined.strip():
        return False

    def has_any(terms: set[str]) -> bool:
        return any(term.casefold() in joined for term in terms)

    if reason == "continuity" or reason == "identity":
        return has_any(PROP_CONTINUITY_EVIDENCE_TERMS)
    if reason == "action":
        return has_any(PROP_ACTION_EVIDENCE_TERMS) or has_any(PROP_PLOT_EVIDENCE_TERMS)
    if reason == "handoff":
        return has_any({"交接", "递给", "交换", "传递", "handoff", "passes", "hands"})
    if reason == "plot":
        return has_any(PROP_PLOT_EVIDENCE_TERMS | PLOT_CRITICAL_PROP_TERMS)
    return False


def _prop_removal_reason(
    prop,
    name_key: str,
    text: str,
    reason: str,
    evidence: str,
    visible_shots: set[int],
    bound_shots: set[int],
    scenes: list[Any] | None = None,
) -> str:
    legacy_plot_signal = any(term.casefold() in text for term in PLOT_CRITICAL_PROP_TERMS)
    is_background_fixture = any(term.casefold() in name_key for term in BACKGROUND_FIXTURE_PROP_TERMS)
    is_environment_state = any(term.casefold() in text for term in ENVIRONMENT_STATE_TERMS)
    if _is_non_physical_prop(prop):
        return "non_physical_content"
    has_visual_identity = len(re.sub(r"\s+", "", prop.description or "")) >= 8
    if not visible_shots:
        return "unused"
    if visible_shots != bound_shots:
        return "inconsistent_shot_bindings"
    if len(bound_shots) < 2:
        return "single_shot"
    if name_key in COMMON_TRANSIENT_PROP_NAMES:
        return "transient_common_item"
    if is_background_fixture:
        return "background_fixture"
    if is_environment_state:
        return "environment_state"
    has_explicit_contract = bool(reason or evidence)
    if not has_explicit_contract and legacy_plot_signal:
        return ""
    if reason not in ALLOWED_PROP_CRITICAL_REASONS:
        return "missing_critical_reason"
    if not evidence:
        return "missing_critical_evidence"
    if not has_visual_identity:
        return "missing_visual_identity"
    if not _prop_has_semantic_critical_evidence(prop, reason, evidence, scenes):
        return "missing_critical_evidence"
    return ""


def _prop_density_warnings(script: ScriptBreakdown, kept_count: int) -> list[str]:
    threshold = max(8, math.ceil(len(script.scenes) / 2))
    if kept_count <= threshold:
        return []
    return [f"prop_density_high:{kept_count}>{threshold}"]


def validate_and_clean_props(script: ScriptBreakdown) -> PropValidationResult:
    """Rebuild prop bindings and remove only invalid non-reusable props."""
    if not script.asset_manifest:
        return PropValidationResult(cleaned_script=script, removed_props=[], warnings=[])
    reconcile_prop_bindings(script)
    manifest = script.asset_manifest
    removed: list[dict[str, Any]] = []
    kept: list[object] = []
    bindings_by_scene = {binding.scene_id: binding for binding in manifest.shot_bindings}
    for prop in manifest.props:
        name_key = normalize_scene_name(prop.name)
        text = f"{prop.name} {prop.description}".casefold()
        reason = str(getattr(prop, "critical_reason", "") or "").strip().casefold()
        evidence = str(getattr(prop, "critical_evidence", "") or "").strip()
        visible_shots = set(prop.shot_ids)
        bound_shots = {
            scene_id for scene_id, binding in bindings_by_scene.items()
            if prop.prop_id in binding.prop_ids
        }
        bound_scenes = [
            scene
            for scene in (getattr(script, "scenes", []) or [])
            if scene.scene_id in bound_shots
        ]
        removal_reason = _prop_removal_reason(
            prop,
            name_key,
            text,
            reason,
            evidence,
            visible_shots,
            bound_shots,
            bound_scenes,
        )
        if removal_reason:
            removed.append({
                "prop_id": prop.prop_id,
                "name": prop.name,
                "reason": removal_reason,
                "shot_ids": sorted(visible_shots),
            })
            continue
        kept.append(prop)
    if not removed:
        return PropValidationResult(cleaned_script=script, removed_props=[], warnings=_prop_density_warnings(script, len(kept)))
    removed_ids = {item["prop_id"] for item in removed}
    manifest.props = kept
    for binding in manifest.shot_bindings:
        binding.prop_ids = [prop_id for prop_id in binding.prop_ids if prop_id not in removed_ids]
    reconcile_prop_bindings(script)
    return PropValidationResult(cleaned_script=script, removed_props=removed, warnings=_prop_density_warnings(script, len(kept)))


def prune_nonessential_props(script: ScriptBreakdown) -> list[str]:
    """Keep only visible, justified, reusable story props."""
    result = validate_and_clean_props(script)
    return [item["prop_id"] for item in result.removed_props]


def remove_non_physical_props(script: ScriptBreakdown) -> list[str]:
    """Remove only unambiguous virtual content from legacy manifests.

    Legacy projects may not have descriptions or criticality fields, so loading
    them must not apply the stricter new-prop validation. This narrow migration
    only removes SMS/recording content while preserving historical physical
    props until the project is explicitly re-parsed.
    """
    manifest = script.asset_manifest if script else None
    if not manifest:
        return []
    removed_ids = {
        prop.prop_id
        for prop in manifest.props
        if _is_non_physical_prop(prop)
    }
    if not removed_ids:
        return []
    manifest.props = [prop for prop in manifest.props if prop.prop_id not in removed_ids]
    for binding in manifest.shot_bindings:
        binding.prop_ids = [prop_id for prop_id in binding.prop_ids if prop_id not in removed_ids]
    reconcile_prop_bindings(script)
    return sorted(removed_ids)


def repair_asset_manifest(script: ScriptBreakdown) -> dict[str, list[str]]:
    """Deterministically repair reusable manifest relations before generation.

    Per-shot bindings are the source of truth for where a prop is actually
    visible. This keeps legacy or partially repaired projects from failing a
    generation task solely because a denormalized ``prop.shot_ids`` list is
    stale.
    """
    normalize_scene_assets(script)
    enrich_manifest_descriptions(script)
    reconciled = reconcile_prop_bindings(script)
    manifest = script.asset_manifest
    pruned = []
    if manifest:
        # Old projects may lack the newer critical-reason fields and can contain
        # deliberately retained single-shot evidence props. Preserve those, but
        # never generate a reference for an unbound orphan prop.
        pruned = [item.prop_id for item in manifest.props if not item.shot_ids]
        if pruned:
            pruned_ids = set(pruned)
            manifest.props = [item for item in manifest.props if item.prop_id not in pruned_ids]
            for binding in manifest.shot_bindings:
                binding.prop_ids = [prop_id for prop_id in binding.prop_ids if prop_id not in pruned_ids]
    # Removing dropped props changes the source bindings. Run a final pass so
    # every remaining prop's denormalized list exactly matches those bindings.
    reconciled.extend(item for item in reconcile_prop_bindings(script) if item not in reconciled)
    return {"reconciled_props": reconciled, "pruned_props": pruned}


def has_manifest(script: ScriptBreakdown | None) -> bool:
    return bool(script and script.asset_manifest)


def binding_for_scene(script: ScriptBreakdown | None, scene_id: int) -> ShotVisualBinding | None:
    if not has_manifest(script):
        return None
    for binding in script.asset_manifest.shot_bindings:
        if binding.scene_id == scene_id:
            return binding
    return None


def on_screen_character_ids(script: ScriptBreakdown | None, scene_id: int) -> list[str]:
    binding = binding_for_scene(script, scene_id)
    if binding is not None:
        return binding.on_screen_character_ids
    if script:
        for scene in script.scenes:
            if scene.scene_id == scene_id:
                return scene.characters
    return []


def on_screen_character_names(script: ScriptBreakdown | None, scene_id: int) -> list[str]:
    if not has_manifest(script):
        return on_screen_character_ids(script, scene_id)
    names_by_id = {item.asset_id: item.name for item in script.asset_manifest.characters}
    return [names_by_id[item] for item in on_screen_character_ids(script, scene_id) if item in names_by_id]


def location_for_scene(script: ScriptBreakdown | None, scene_id: int):
    binding = binding_for_scene(script, scene_id)
    if binding is None or not script or not script.asset_manifest:
        return None
    for location in script.asset_manifest.locations:
        if location.location_id == binding.location_id:
            return location
    return None


def state_for_scene(script: ScriptBreakdown | None, scene_id: int) -> LocationState | None:
    binding = binding_for_scene(script, scene_id)
    location = location_for_scene(script, scene_id)
    if binding is None or location is None:
        return None
    for state in location.states:
        if state.state_id == binding.state_id:
            return state
    if location.states:
        return location.states[0]
    return None


def representative_scene_id(script: ScriptBreakdown | None, scene_id: int) -> int:
    location = location_for_scene(script, scene_id)
    return location.representative_scene_id if location else scene_id


def visual_character_names(script: ScriptBreakdown | None) -> list[str]:
    if not has_manifest(script):
        return []
    return [item.name for item in script.asset_manifest.characters if item.appearance_mode == "on_screen"]


def prop_ids_for_scene(script: ScriptBreakdown | None, scene_id: int) -> list[str]:
    binding = binding_for_scene(script, scene_id)
    return binding.prop_ids if binding is not None else []


def props_for_scene(script: ScriptBreakdown | None, scene_id: int) -> list:
    if not has_manifest(script):
        return []
    by_id = {item.prop_id: item for item in script.asset_manifest.props}
    return [by_id[prop_id] for prop_id in prop_ids_for_scene(script, scene_id) if prop_id in by_id]


def visual_prop_ids(script: ScriptBreakdown | None) -> list[str]:
    if not has_manifest(script):
        return []
    return [item.prop_id for item in script.asset_manifest.props]


def validate_asset_manifest(script: ScriptBreakdown | None) -> list[str]:
    """Return validation errors without blocking legacy projects."""
    if not has_manifest(script):
        return []

    manifest = script.asset_manifest
    errors: list[str] = []
    shot_ids = {scene.scene_id for scene in script.scenes}
    character_modes = {item.asset_id: item.appearance_mode for item in manifest.characters}
    location_ids = {item.location_id for item in manifest.locations}
    prop_ids = {item.prop_id for item in manifest.props}
    bound_shot_ids: set[int] = set()

    for binding in manifest.shot_bindings:
        if binding.scene_id not in shot_ids:
            errors.append(f"shot binding references missing scene {binding.scene_id}")
        if binding.scene_id in bound_shot_ids:
            errors.append(f"scene {binding.scene_id} has multiple visual bindings")
        bound_shot_ids.add(binding.scene_id)
        if binding.location_id not in location_ids:
            errors.append(f"scene {binding.scene_id} references missing location {binding.location_id}")
        else:
            location = next((item for item in manifest.locations if item.location_id == binding.location_id), None)
            state_ids = {state.state_id for state in (getattr(location, "states", []) or [])}
            if state_ids:
                if not binding.state_id:
                    errors.append(
                        f"scene {binding.scene_id} location {binding.location_id} has no state_id"
                    )
                elif binding.state_id not in state_ids:
                    errors.append(
                        f"scene {binding.scene_id} location {binding.location_id} references missing state {binding.state_id}"
                    )
        for character_id in binding.on_screen_character_ids:
            if character_modes.get(character_id) != "on_screen":
                errors.append(
                    f"scene {binding.scene_id} uses non-visual character {character_id} as on-screen"
                )
        for prop_id in binding.prop_ids:
            if prop_id not in prop_ids:
                errors.append(f"scene {binding.scene_id} references missing prop {prop_id}")
            else:
                prop = next(item for item in manifest.props if item.prop_id == prop_id)
                if binding.scene_id not in set(prop.shot_ids or []):
                    errors.append(
                        f"prop {prop_id} is bound to scene {binding.scene_id} but does not declare that shot"
                    )

    for scene_id in sorted(shot_ids - bound_shot_ids):
        errors.append(f"scene {scene_id} has no visual binding")

    for location in manifest.locations:
        if not location.shot_ids:
            errors.append(f"location {location.location_id} is unused")
        if location.representative_scene_id not in location.shot_ids:
            errors.append(
                f"location {location.location_id} representative scene is not one of its shots"
            )
        for scene_id in location.shot_ids:
            binding = binding_for_scene(script, scene_id)
            if binding is None or binding.location_id != location.location_id:
                errors.append(f"location {location.location_id} has inconsistent shot {scene_id}")
        for state in getattr(location, "states", []) or []:
            for scene_id in state.shot_ids:
                binding = binding_for_scene(script, scene_id)
                if binding is None or binding.location_id != location.location_id or binding.state_id != state.state_id:
                    errors.append(
                        f"location {location.location_id} state {state.state_id} has inconsistent shot {scene_id}"
                    )

    for prop in manifest.props:
        if not prop.shot_ids:
            errors.append(f"prop {prop.prop_id} is unused")
        for scene_id in prop.shot_ids:
            binding = binding_for_scene(script, scene_id)
            if binding is None or prop.prop_id not in binding.prop_ids:
                errors.append(f"prop {prop.prop_id} has inconsistent shot {scene_id}")

    return errors
