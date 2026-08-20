import json
import asyncio
import re

from src.agents.video_gen_agent import VideoGenerationAgent
from src.graph.state import DramaState
from src.schemas.prompt import GenerationPrompts, PromptPolicy, ShotPrompt
from src.schemas.project import CreativeProfile
from src.schemas.script import DialogueLine, Scene, ScriptBreakdown
from src.schemas.visual import VisualDesign
from src.services.prompt_composer import compose_video_prompt
from src.services.timed_prompt_composer import build_timed_shot_prompt
from src.agents.image_gen_agent import build_character_image_prompt, build_character_negative_prompt
from src.agents.visual_prompt_agent import (
    SYSTEM_PROMPT as VISUAL_PROMPT_SYSTEM,
    VisualPromptAgent,
    _ShotVisualPrompt,
    _VisualPromptOutput,
)
from src.schemas.assets import ShotAsset
from src.utils.visual_asset_manifest import build_manifest_from_scenes

import api_server
from src.api import context as api_context


def _script() -> ScriptBreakdown:
    return ScriptBreakdown(
        title="test",
        genre="test",
        logline="test",
        duration_sec=10,
        narration_style="none",
        scenes=[
            Scene(scene_id=1, duration_sec=5, location="a", characters=[], action_description="one", camera_direction="wide", emotion_tone="calm"),
            Scene(scene_id=2, duration_sec=5, location="b", characters=[], action_description="two", camera_direction="wide", emotion_tone="calm"),
        ],
    )


def _prompts() -> GenerationPrompts:
    return GenerationPrompts(
        api_provider="happyhorse",
        global_negative_prompt="default avoid",
        shots=[ShotPrompt(scene_id=1, prompt="scene action", negative_prompt="bad hands")],
    )


def test_happyhorse_merges_negative_constraints_into_the_actual_prompt():
    result = compose_video_prompt("happyhorse", _prompts(), _prompts().shots[0], PromptPolicy(
        style_prefix="cinematic style", global_negative_prompt="project avoid",
    ))
    assert result["negative_prompt_mode"] == "merged"
    assert result["provider_negative_prompt"] == ""
    assert "cinematic style" in result["provider_prompt"]
    assert "project avoid" in result["provider_prompt"]
    assert "default avoid" in result["provider_prompt"]
    assert "bad hands" in result["provider_prompt"]


def test_kling_keeps_negative_constraints_separate():
    result = compose_video_prompt("kling", _prompts(), _prompts().shots[0], PromptPolicy(
        global_negative_prompt="project avoid",
    ))
    assert result["negative_prompt_mode"] == "separate"
    assert "project avoid" not in result["provider_prompt"]
    assert "project avoid" in result["provider_negative_prompt"]
    assert "default avoid" in result["provider_negative_prompt"]
    assert "bad hands" in result["provider_negative_prompt"]


def test_duplicate_global_negative_constraints_are_not_repeated():
    prompts = _prompts()
    prompts.global_negative_prompt = "same constraint"
    result = compose_video_prompt(
        "kling", prompts, prompts.shots[0], PromptPolicy(global_negative_prompt="same constraint"),
    )
    assert result["provider_negative_prompt"].count("same constraint") == 1


def test_video_prompt_does_not_expose_internal_visual_style_id():
    profile = CreativeProfile(
        visual_style_id="live_action_cinematic",
        art_style="电影级真人写实",
    )
    result = compose_video_prompt(
        "happyhorse",
        _prompts(),
        _prompts().shots[0],
        creative_profile=profile,
    )

    assert "电影级真人写实" in result["effective_prompt"]
    assert "电影级真人写实（live_action_cinematic）" not in result["effective_prompt"]
    assert "live_action_cinematic" not in result["effective_prompt"]


def test_video_prompt_uses_chinese_for_fixed_quality_and_timeline_constraints():
    result = compose_video_prompt(
        "happyhorse",
        _prompts(),
        _prompts().shots[0],
    )

    fixed_english = (
        "[Video quality]",
        "[Planned edit rule]",
        "[Single-take rule]",
        "Cinematic vertical",
        "Follow the authored",
        "No montage",
    )
    assert not any(marker in result["provider_prompt"] for marker in fixed_english)
    assert "【视频质量】" in result["provider_prompt"]
    assert "【单镜头连续拍摄规则】" in result["provider_prompt"]


def test_loading_legacy_project_prompt_removes_internal_visual_style_id():
    state = DramaState(
        creative_profile=CreativeProfile(
            visual_style_id="live_action_cinematic",
            art_style="电影级真人写实",
        ),
        generation_prompts=GenerationPrompts(
            api_provider="happyhorse",
            shots=[ShotPrompt(
                scene_id=1,
                prompt="【视觉媒介】电影级真人写实（live_action_cinematic）：电影级真人镜头语言。",
            )],
        ),
    )

    loaded = api_context._state_from_json(api_context._state_to_json(state))

    assert "live_action_cinematic" not in loaded.generation_prompts.shots[0].prompt
    assert "【视觉媒介】电影级真人写实：" in loaded.generation_prompts.shots[0].prompt
    assert loaded.creative_profile.visual_style_id == "live_action_cinematic"


def test_video_prompt_adds_cinematic_quality_block_without_dropping_scene_action():
    result = compose_video_prompt("mock", _prompts(), _prompts().shots[0], PromptPolicy())

    assert "【视频质量】" in result["provider_prompt"]
    assert "整段视频保持人物身份" in result["provider_prompt"]
    assert "【单镜头连续拍摄规则】" in result["provider_prompt"]
    assert "连续长镜头" in result["provider_prompt"]
    assert "不得使用蒙太奇" in result["provider_prompt"]
    assert "scene action" in result["provider_prompt"]


def test_timecoded_video_prompt_uses_planned_edit_rule_instead_of_single_take():
    prompts = _prompts()
    shot = ShotPrompt(
        scene_id=1,
        prompt="2s close-up on Gu Heng, 1.5s fast pan as Su Mian turns back, 3s two-shot confrontation, flashback insert.",
    )
    result = compose_video_prompt("mock", prompts, shot, PromptPolicy())

    assert "【计划剪辑规则】" in result["provider_prompt"]
    assert "只允许在提示词明确写出时长" in result["provider_prompt"]
    assert "【单镜头连续拍摄规则】" not in result["provider_prompt"]


def test_plain_script_shot_is_structured_as_timed_prompt_with_elastic_duration_for_added_detail():
    script = ScriptBreakdown(
        title="test",
        genre="test",
        logline="test",
        duration_sec=8,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=8,
                location="rainy street",
                characters=["苏眠", "顾衡"],
                action_description="顾衡向苏眠伸出手，苏眠后退一步举起信封对峙。",
                camera_direction="中近景/侧面固定",
                emotion_tone="紧张",
            )
        ],
    )
    shot = ShotPrompt(scene_id=1, prompt="顾衡要求苏眠交出信封。", duration_sec=8)

    rendered = build_timed_shot_prompt(shot, script)

    assert "【时间轴分镜】" in rendered
    assert "总时长约8s" in rendered
    assert "切镜头" in rendered
    assert "不得额外增加镜头" in rendered
    assert shot.duration_sec == 8
    assert script.scenes[0].duration_sec == 8


def test_existing_tapnow_style_timeline_is_preserved():
    prompt = "1.5s，特写，镜头慢慢推进；切镜头，2s，大特写/插入，手机屏幕短信；切镜头，1.5s，闪回叠加。"
    shot = ShotPrompt(scene_id=1, prompt=prompt, duration_sec=5)

    assert build_timed_shot_prompt(shot, _script()) == prompt


def test_timed_prompt_adds_phone_flashback_and_text_overlay_guidance():
    script = ScriptBreakdown(
        title="test",
        genre="test",
        logline="test",
        duration_sec=8,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=8,
                location="convenience store entrance",
                characters=["苏眠"],
                action_description="苏眠右手举着手机，看到短信后想起旧报纸和六封旧信，脸色从犹豫转为坚定。",
                camera_direction="特写",
                emotion_tone="紧张不安",
            )
        ],
    )
    shot = ShotPrompt(scene_id=1, prompt="苏眠收到第七封信短信，闪回父亲失踪。", duration_sec=8)

    rendered = build_timed_shot_prompt(shot, script)

    assert "手机屏幕蓝光" in rendered
    assert "特写/闪回叠加" in rendered
    assert "六封旧信摊开在桌面" in rendered
    assert "旧报纸、旧信、照片或记忆画面" not in rendered
    assert "后期叠加" in rendered
    assert "总时长约8s" in rendered
    assert shot.duration_sec == 8
    assert script.scenes[0].duration_sec == 8


def test_stale_followup_events_are_removed_from_the_first_shot():
    script = ScriptBreakdown(
        title="test",
        genre="test",
        logline="test",
        duration_sec=8,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=8,
                location="钟楼街便利店外部",
                characters=["苏眠"],
                action_description="苏眠全身湿透，左手紧攥信封，右手举着亮屏手机显示短信，躲在废弃储物柜后。",
                camera_direction="中近景/低机位固定",
                emotion_tone="紧张",
            )
        ],
    )
    shot = ShotPrompt(
        scene_id=1,
        prompt=(
            "核心分镜意图：苏眠全身湿透，雨水顺着发梢和风衣下摆不停滴落，左手紧攥信封，右手举着亮屏手机显示短信 "
            "8s，中近景/低机位固定，苏眠躲在废弃储物柜后，积水滴落声清楚，黑衣人的手电光从柜缝扫过人物眼睛，"
            "顾衡捂住苏眠的嘴，午夜钟声打断搜寻。"
        ),
        duration_sec=8,
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "储物柜后" not in rendered
    assert "黑衣人" not in rendered
    assert "顾衡捂住苏眠的嘴" not in rendered
    assert "午夜钟声" not in rendered
    assert "短信" in rendered
    assert "信封" in rendered
    assert "苏眠" in rendered


def test_prompt_details_do_not_invent_flashback_from_backstory_keywords():
    script = ScriptBreakdown(
        title="test",
        genre="悬疑",
        logline="test",
        duration_sec=12,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=12,
                location="雨夜街边",
                characters=["苏眠", "顾衡"],
                dialogue=[
                    DialogueLine(character="顾衡", line="你不该一个人来。", emotion="担忧"),
                    DialogueLine(character="苏眠", line="短信不是你发的？", emotion="怀疑"),
                    DialogueLine(character="顾衡", line="不是。", emotion="冷静"),
                ],
                action_description="苏眠想到父亲失踪的线索后质问顾衡，脸色从冷笑转为不安。",
                camera_direction="中近景双人固定",
                emotion_tone="警惕",
            )
        ],
    )
    shot = ShotPrompt(
        scene_id=1,
        prompt="苏眠从冷笑质问转为不安，顾衡低声说话，保持警戒姿态。",
        duration_sec=12,
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "闪回" not in rendered
    assert "旧报纸" not in rendered
    assert "记忆画面" not in rendered
    assert "大特写/插入" not in rendered
    assert "短促道具强调特写" in rendered
    assert "中景双人/侧面固定" in rendered


def test_reaction_pan_uses_one_clear_point_of_view():
    script = ScriptBreakdown(
        title="test",
        genre="悬疑",
        logline="test",
        duration_sec=8,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=8,
                location="雨夜街边",
                characters=["苏眠", "顾衡"],
                action_description="苏眠和顾衡听到身后动静，苏眠猛地回头看向街道尽头红绿灯。",
                camera_direction="中近景",
                emotion_tone="紧张",
            )
        ],
    )
    shot = ShotPrompt(scene_id=1, prompt="两人警觉回头。", duration_sec=8)

    rendered = build_timed_shot_prompt(shot, script)

    assert "模拟苏眠视角" in rendered
    assert "模拟苏眠、顾衡视角" not in rendered


def test_asset_style_prompt_is_polished_into_single_take_without_extra_cuts():
    script = ScriptBreakdown(
        title="test",
        genre="悬疑",
        logline="test",
        duration_sec=10,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=10,
                location="钟楼街便利店外部",
                characters=["苏眠"],
                action_description="苏眠站在便利店屋檐下，左手紧攥信封，右手举着手机，凝视屏幕后准备行动。",
                camera_direction="中景缓慢推近至近景",
                emotion_tone="冷冽悬疑雨夜",
            )
        ],
    )
    prompt = (
        "【项目视觉】写实；冷冽悬疑色调；9:16画幅。\n"
        "【出镜角色与身份锚点】苏眠：女性，约29岁，黑色中长发因雨淋湿贴在脸侧，身穿米白色长风衣。。\n"
        "【主体状态】苏眠站在便利店屋檐下，左手紧攥信封，右手举着手机，凝视屏幕，指尖微微发抖。\n"
        "【动作时间轴】0-3秒看手机短信，抬头警觉环顾；4-8秒镜头缓慢推近，表现内心犹豫与决心；8-10秒握紧信封准备行动。\n"
        "【环境】钟楼街便利店外部。雨夜，街道路面反光，路灯忽明忽暗。。\n"
        "【关键道具】信封：左手紧握；手机：右手举起。"
    )
    shot = ShotPrompt(scene_id=1, prompt=prompt, duration_sec=10)

    rendered = build_timed_shot_prompt(shot, script)

    assert "【单镜头时间轴】" in rendered
    assert "0s-3s" in rendered
    assert "3s-8s" in rendered
    assert "8s-10s" in rendered
    assert not any(line.startswith("切镜头，") for line in rendered.splitlines())
    assert "不切镜头" in rendered
    assert "。。" not in rendered


def test_visual_prompt_agent_persists_polished_timed_prompt_for_ui_display():
    script = ScriptBreakdown(
        title="test",
        genre="悬疑",
        logline="test",
        duration_sec=10,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=10,
                location="钟楼街便利店外部",
                characters=["苏眠"],
                action_description="苏眠站在便利店屋檐下，左手紧攥信封，右手举着手机，凝视屏幕后准备行动。",
                camera_direction="中景缓慢推近至近景",
                emotion_tone="冷冽悬疑雨夜",
            )
        ],
    )
    output = _VisualPromptOutput(
        overall_style="写实",
        color_palette="冷冽悬疑色调",
        aspect_ratio="9:16",
        shots=[
            _ShotVisualPrompt(
                scene_id=1,
                subject_state="苏眠站在便利店屋檐下，左手紧攥信封，右手举着手机。",
                action_timeline="0-3秒看手机短信，抬头警觉环顾；3-8秒镜头缓慢推近，表现犹豫；8-10秒握紧信封准备行动。",
                environment_delta="雨夜，街道路面反光，路灯忽明忽暗。",
                camera_motion="中景缓慢推近至近景",
                lighting_delta="便利店灯牌顶光和街灯侧逆光",
                prop_state="信封左手紧握，手机右手举起。",
                duration_sec=10,
            )
        ],
    )
    agent = object.__new__(VisualPromptAgent)
    agent.api_provider = "mock"

    _, prompts = agent._split_output(output, script, prompt_overrides=None)

    rendered = prompts.shots[0].prompt
    assert "【单镜头时间轴】" in rendered
    assert "总时长约10s" in rendered
    assert prompts.shots[0].duration_sec == 10
    assert script.scenes[0].duration_sec == 10
    assert "0s-3s" in rendered
    assert "3s-8s" in rendered
    assert "8s-10s" in rendered
    assert "【动作时间轴】" not in rendered


def test_visual_prompt_does_not_exceed_confirmed_script_duration_budget():
    script = ScriptBreakdown(
        title="时间预算",
        genre="悬疑",
        logline="两个镜头共十秒。",
        duration_sec=10,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=5,
                location="便利店外",
                characters=["苏眠"],
                action_description="苏眠在便利店外查看手机。",
                camera_direction="中近景",
                emotion_tone="紧张",
            ),
            Scene(
                scene_id=2,
                duration_sec=5,
                location="钟楼地下室",
                characters=["顾衡"],
                action_description="顾衡在钟楼地下室打开铁盒。",
                camera_direction="近景",
                emotion_tone="冷静",
            ),
        ],
    )
    output = _VisualPromptOutput(
        overall_style="动漫风",
        color_palette="冷蓝色",
        aspect_ratio="9:16",
        shots=[
            _ShotVisualPrompt(
                scene_id=1,
                subject_state="苏眠在便利店外查看手机。",
                action_timeline="0-10秒查看手机；10-20秒抬头；20-30秒继续查看手机。",
                duration_sec=30,
            ),
            _ShotVisualPrompt(
                scene_id=2,
                subject_state="顾衡在钟楼地下室打开铁盒。",
                action_timeline="0-10秒寻找铁盒；10-20秒插入钥匙；20-30秒打开铁盒。",
                duration_sec=30,
            ),
        ],
    )
    agent = object.__new__(VisualPromptAgent)
    agent.api_provider = "mock"

    _, prompts = agent._split_output(output, script, prompt_overrides=None)

    assert sum(shot.duration_sec for shot in prompts.shots) == 10
    assert script.duration_sec == 10
    assert [shot.duration_sec for shot in prompts.shots] == [5, 5]


def test_visual_prompt_removes_cross_shot_action_and_generic_placeholders():
    script = ScriptBreakdown(
        title="镜头隔离",
        genre="悬疑",
        logline="两个地点依次发生动作。",
        duration_sec=10,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=5,
                location="便利店外",
                characters=["苏眠"],
                action_description="苏眠在便利店外查看手机短信。",
                camera_direction="中近景",
                emotion_tone="紧张",
            ),
            Scene(
                scene_id=2,
                duration_sec=5,
                location="钟楼地下室",
                characters=["顾衡"],
                action_description="顾衡在钟楼地下室打开铁盒。",
                camera_direction="近景",
                emotion_tone="冷静",
            ),
        ],
    )
    output = _VisualPromptOutput(
        overall_style="动漫风",
        color_palette="冷蓝色",
        aspect_ratio="9:16",
        shots=[
            _ShotVisualPrompt(scene_id=1, subject_state="苏眠查看手机短信。", duration_sec=5),
            _ShotVisualPrompt(
                scene_id=2,
                subject_state="完成本镜头的主要动作",
                action_timeline="保持动作起点清楚；便利店外查看手机短信。",
                duration_sec=5,
            ),
        ],
    )
    agent = object.__new__(VisualPromptAgent)
    agent.api_provider = "mock"

    _, prompts = agent._split_output(output, script, prompt_overrides=None)

    assert output.shots[1].subject_state == ""
    assert output.shots[1].action_timeline == ""
    rendered = prompts.shots[1].prompt
    assert "便利店外查看手机短信" not in rendered
    assert "完成本镜头的主要动作" not in rendered
    assert "保持动作起点清楚" not in rendered
    assert "顾衡在钟楼地下室打开铁盒" in rendered


def test_visual_prompt_drops_repeated_model_action_not_supported_by_later_scene():
    """A repeated generated action must not leak into a later shot by paraphrase."""
    script = ScriptBreakdown(
        title="重复动作隔离",
        genre="悬疑",
        logline="装置启动后角色聆听声音。",
        duration_sec=10,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=5,
                location="地下室长桌",
                characters=["苏眠"],
                action_description="桌上的装置刚刚启动，红色指示灯亮起。",
                camera_direction="特写",
                emotion_tone="紧张",
            ),
            Scene(
                scene_id=2,
                duration_sec=5,
                location="地下室长桌",
                characters=["苏眠"],
                action_description="苏眠站在桌旁，屏息聆听装置发出的声音。",
                camera_direction="中景",
                emotion_tone="压抑",
            ),
        ],
    )
    output = _VisualPromptOutput(
        overall_style="动漫风",
        color_palette="冷蓝色",
        aspect_ratio="9:16",
        shots=[
            _ShotVisualPrompt(
                scene_id=1,
                action_timeline="0-2秒装置的指示灯开始闪烁，红光亮起；2-5秒角色停住。",
                duration_sec=5,
            ),
            _ShotVisualPrompt(
                scene_id=2,
                action_timeline="0-2秒装置的指示灯开始闪烁，红光亮起；2-5秒苏眠屏息聆听。",
                duration_sec=5,
            ),
        ],
    )
    agent = object.__new__(VisualPromptAgent)
    agent.api_provider = "mock"

    _visual, prompts = agent._split_output(output, script, prompt_overrides=None)

    assert "装置的指示灯开始闪烁" in output.shots[0].action_timeline
    assert "装置的指示灯开始闪烁" not in output.shots[1].action_timeline
    assert "苏眠屏息聆听" in output.shots[1].action_timeline


def test_visual_prompt_agent_keeps_confirmed_simple_scene_duration():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="苏眠收到短信。",
        duration_sec=10,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=10,
                location="钟楼街便利店外部",
                characters=["苏眠"],
                action_description="苏眠站在便利店屋檐下，左手紧攥信封，右手举着手机。",
                camera_direction="中近景固定",
                emotion_tone="紧张",
            )
        ],
    )
    output = _VisualPromptOutput(
        overall_style="写实",
        color_palette="冷冽悬疑",
        aspect_ratio="9:16",
        shots=[
            _ShotVisualPrompt(
                scene_id=1,
                subject_state="苏眠站在便利店屋檐下，左手紧攥信封，右手举着手机。",
                action_timeline="0-3秒看手机；3-7秒反应。",
                duration_sec=7,
            )
        ],
    )
    agent = object.__new__(VisualPromptAgent)
    agent.api_provider = "mock"

    _, prompts = agent._split_output(output, script, prompt_overrides=None)

    assert prompts.shots[0].duration_sec == 10
    assert "总时长约10s" in prompts.shots[0].prompt
    assert script.scenes[0].duration_sec == 10
    assert script.duration_sec == 10


def test_visual_prompt_agent_rejects_unreasonable_short_complex_action_duration():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="陆临露出真面目，苏眠用录音机反击。",
        duration_sec=15,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=15,
                location="旧钟楼地下室",
                characters=["苏眠", "顾衡", "陆临"],
                dialogue=[
                    DialogueLine(character="陆临", line="顾警官，七年了，你还是这么喜欢坏事。", emotion="温和却阴冷"),
                    DialogueLine(character="苏眠", line="陆临哥？", emotion="震惊"),
                    DialogueLine(character="陆临", line="眠眠，把第七封信给我。你不需要知道真相。", emotion="微笑"),
                ],
                action_description=(
                    "黑衣人摘下口罩露出陆临的脸，顾衡冲上前和陆临扭打，"
                    "苏眠抓起录音机砸向陆临手腕，信封掉进积水，火漆印章被水泡开，露出旧地图。"
                ),
                camera_direction="中景三人/侧面固定",
                emotion_tone="身份反转与动作冲突",
            )
        ],
    )
    output = _VisualPromptOutput(
        overall_style="写实",
        color_palette="冷冽悬疑",
        aspect_ratio="9:16",
        shots=[
            _ShotVisualPrompt(
                scene_id=1,
                subject_state="陆临露出真面目，三人对峙后发生扭打。",
                action_timeline="0-2秒陆临摘下口罩；2-5秒顾衡和陆临扭打；5-7秒苏眠用录音机反击。",
                duration_sec=7,
            )
        ],
    )
    agent = object.__new__(VisualPromptAgent)
    agent.api_provider = "mock"

    _, prompts = agent._split_output(output, script, prompt_overrides=None)

    assert prompts.shots[0].duration_sec >= 13
    assert "总时长约7s" not in prompts.shots[0].prompt
    assert script.scenes[0].duration_sec == prompts.shots[0].duration_sec
    assert script.duration_sec == prompts.shots[0].duration_sec


def test_short_visual_action_timeline_is_scaled_to_script_duration():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="苏眠收到短信。",
        duration_sec=10,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=10,
                location="钟楼街便利店外部",
                characters=["苏眠"],
                action_description="苏眠站在便利店屋檐下，左手紧攥信封，右手举着手机。",
                camera_direction="中近景固定",
                emotion_tone="紧张",
            )
        ],
    )
    prompt = (
        "【主体状态】苏眠站在便利店屋檐下，左手紧攥信封，右手举着手机。\n"
        "【动作时间轴】0-3秒看手机短信；3-7秒抬眼反应。"
    )
    shot = ShotPrompt(scene_id=1, prompt=prompt, duration_sec=10)

    rendered = build_timed_shot_prompt(shot, script)

    assert "0s-4.3s" in rendered
    assert "4.3s-10s" in rendered
    assert not any(line.startswith("3s-10s") for line in rendered.splitlines())
    assert "总时长约10s" in rendered


def test_visual_prompt_agent_does_not_rewrite_user_prompt_overrides():
    script = _script()
    output = _VisualPromptOutput(
        overall_style="写实",
        color_palette="neutral",
        aspect_ratio="9:16",
        shots=[_ShotVisualPrompt(scene_id=1, subject_state="one", duration_sec=5)],
    )
    agent = object.__new__(VisualPromptAgent)
    agent.api_provider = "mock"

    _, prompts = agent._split_output(output, script, prompt_overrides={"1": "用户手动提示词：保持原样。"})

    assert prompts.shots[0].prompt == "用户手动提示词：保持原样。"


def test_already_polished_timed_prompt_is_idempotent():
    prompt = "【单镜头时间轴】\n0s-5s：苏眠保持同一动作。\n【连续锁定】\n总时长约5s，单镜头连续完成。"
    shot = ShotPrompt(scene_id=1, prompt=prompt, duration_sec=5)

    assert build_timed_shot_prompt(shot, _script()) == prompt


def test_model_prompt_supplement_is_stripped_before_timed_repair():
    script = ScriptBreakdown(
        title="test",
        genre="悬疑",
        logline="test",
        duration_sec=8,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=8,
                location="钟楼街便利店外部",
                characters=["苏眠"],
                action_description="苏眠站在便利店屋檐下，左手紧攥信封，右手手机屏幕光照亮她的脸。",
                camera_direction="中近景/正面固定",
                emotion_tone="紧张不安",
            )
        ],
    )
    shot = ShotPrompt(
        scene_id=1,
        prompt=(
            "【单镜头时间轴】\n"
            "0s-4s：苏眠保持同一动作。\n"
            "【模型镜头补充】顾衡推门而出，黑衣人从阴影里走出，午夜钟声响起。\n"
            "【连续锁定】\n总时长约8s，单镜头连续完成。"
        ),
        duration_sec=8,
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "模型镜头补充" not in rendered
    assert "顾衡" not in rendered
    assert "黑衣人" not in rendered
    assert "午夜钟声" not in rendered
    assert "苏眠" in rendered


def test_stale_finalized_timeline_prompt_is_repaired_conservatively():
    script = ScriptBreakdown(
        title="test",
        genre="悬疑",
        logline="test",
        duration_sec=12,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=12,
                location="雨夜铁门前",
                characters=["苏眠", "顾衡"],
                dialogue=[
                    DialogueLine(character="顾衡", line="走。", emotion="急促"),
                    DialogueLine(character="苏眠", line="去哪？", emotion="喘气"),
                    DialogueLine(character="顾衡", line="旧钟楼。", emotion="坚定"),
                ],
                action_description="两人在奔跑，苏眠紧张喘息，顾衡努力开锁，铁门发出刺耳摩擦声。",
                camera_direction="中近景手持跟拍",
                emotion_tone="紧张逃离",
            )
        ],
    )
    prompt = (
        "【时间轴分镜】\n"
        "核心分镜意图：两人在奔跑，苏眠紧张喘息，顾衡努力开锁，铁门发出刺耳摩擦声\n"
        "2.4s，快速摇摄或反应镜头，模拟苏眠、顾衡视角回头看向动静来源，衣服摩擦声和心跳声增强。\n"
        "切镜头，3s，大特写/插入缓慢推近，信封、第七封信、钥匙清楚可见，形状、材质、颜色、状态不变。\n"
        "切镜头，2.6s，特写/闪回叠加，旧报纸、旧信、照片或记忆画面快速闪回，纸张翻动声和whoosh音效。\n"
        "切镜头，4s，中景双人/侧面固定，顾衡：“走。”（急促） / 苏眠：“去哪？”（喘气） / 顾衡：“旧钟楼。”（坚定）。\n"
        "【连续锁定】\n"
        "总时长约12s，严格按上述时间轴执行，只在写明“切镜头”的位置切换。\n"
        "不得额外增加镜头、反打、插入物、车辆镜头、新地点、新人物或新道具。"
    )
    shot = ShotPrompt(scene_id=1, prompt=prompt, duration_sec=12)

    rendered = build_timed_shot_prompt(shot, script)

    assert "模拟苏眠视角" in rendered
    assert "模拟苏眠、顾衡视角" not in rendered
    assert "闪回" not in rendered
    assert "旧报纸" not in rendered
    assert "短促道具强调特写" in rendered
    assert "信封、钥匙" in rendered
    assert "牛皮纸信封" not in rendered
    assert "旧报纸、旧信、照片或记忆画面" not in rendered
    assert "不得额外增加镜头；不得增加未写明" in rendered


def test_flashback_chooses_one_specific_visual_cue_from_script():
    script = ScriptBreakdown(
        title="test",
        genre="悬疑",
        logline="test",
        duration_sec=6,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=6,
                location="旧钟楼",
                characters=["苏眠"],
                action_description="苏眠看到照片背面，短暂闪回照片背面的警告。",
                camera_direction="特写",
                emotion_tone="震惊",
            )
        ],
    )
    shot = ShotPrompt(scene_id=1, prompt="闪回照片背面。", duration_sec=6)

    rendered = build_timed_shot_prompt(shot, script)

    assert "照片翻到背面" in rendered
    assert "旧报纸、旧信、照片或记忆画面" not in rendered
    assert "或" not in next(line for line in rendered.splitlines() if "闪回" in line)


def test_explicit_prop_closeup_duration_is_respected():
    script = ScriptBreakdown(
        title="test",
        genre="悬疑",
        logline="test",
        duration_sec=10,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=10,
                location="旧钟楼",
                characters=["苏眠"],
                action_description="苏眠停下脚步，3s大特写地图标注，随后抬头确认方向。",
                camera_direction="中近景",
                emotion_tone="紧张",
            )
        ],
    )
    shot = ShotPrompt(scene_id=1, prompt="3s大特写地图标注，苏眠确认方向。", duration_sec=10)

    rendered = build_timed_shot_prompt(shot, script)

    assert "3s，大特写/插入缓慢推近" in rendered
    assert "地图" in rendered
    assert "总时长约10s" in rendered
    assert shot.duration_sec == 10
    assert script.scenes[0].duration_sec == 10


def test_unrequested_prop_emphasis_stays_shorter_than_dialogue_landing():
    script = ScriptBreakdown(
        title="test",
        genre="悬疑",
        logline="test",
        duration_sec=12,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=12,
                location="雨夜街边",
                characters=["苏眠", "顾衡"],
                dialogue=[
                    DialogueLine(character="顾衡", line="不是我发的。", emotion="压低声音"),
                    DialogueLine(character="苏眠", line="那是谁？", emotion="不安"),
                ],
                action_description="苏眠握紧信封，顾衡低声解释。",
                camera_direction="中景双人固定",
                emotion_tone="紧张",
            )
        ],
    )
    shot = ShotPrompt(scene_id=1, prompt="苏眠握紧信封，顾衡低声解释。", duration_sec=12)

    rendered = build_timed_shot_prompt(shot, script)

    assert "短促道具强调特写" in rendered
    assert "2s，短促道具强调特写" in rendered or "1." in next(
        line for line in rendered.splitlines() if "短促道具强调特写" in line
    )
    assert "中景双人/侧面固定" in rendered


def test_urgent_action_scene_allocates_time_to_script_task_not_only_props():
    script = ScriptBreakdown(
        title="test",
        genre="悬疑",
        logline="test",
        duration_sec=12,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=12,
                location="雨夜铁门前",
                characters=["苏眠", "顾衡"],
                dialogue=[
                    DialogueLine(character="顾衡", line="走。", emotion="急促"),
                    DialogueLine(character="苏眠", line="去哪？", emotion="喘气"),
                    DialogueLine(character="顾衡", line="旧钟楼。", emotion="坚定"),
                ],
                action_description="两人在奔跑，苏眠紧张喘息，顾衡努力开锁，铁门发出刺耳摩擦声。",
                camera_direction="中近景手持跟拍",
                emotion_tone="紧张逃离",
            )
        ],
    )
    shot = ShotPrompt(
        scene_id=1,
        prompt="两人在奔跑，苏眠紧张喘息，顾衡努力开锁，铁门发出刺耳摩擦声。",
        duration_sec=12,
    )

    rendered = build_timed_shot_prompt(shot, script)
    action_line = next(line for line in rendered.splitlines() if "手持跟拍/中近景动作镜头" in line)
    prop_line = next(line for line in rendered.splitlines() if "短促道具强调特写" in line)
    action_duration = float(action_line.split("s", 1)[0].replace("切镜头，", ""))
    prop_duration = float(prop_line.split("s", 1)[0].replace("切镜头，", ""))

    assert "开锁" in action_line
    assert action_duration > prop_duration
    assert prop_duration <= 2.0
    assert "总时长约12s" in rendered
    assert shot.duration_sec == 12
    assert script.scenes[0].duration_sec == 12
    assert script.duration_sec == 12


def test_timed_prompt_adds_two_person_confrontation_from_dialogue():
    script = ScriptBreakdown(
        title="test",
        genre="test",
        logline="test",
        duration_sec=5,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=5,
                location="rainy street",
                characters=["苏眠", "顾衡"],
                dialogue=[
                    DialogueLine(character="顾衡", line="把信给我。", emotion="沉重"),
                    DialogueLine(character="苏眠", line="照片背面写着，不要交给你。", emotion="警惕"),
                ],
                action_description="顾衡向苏眠伸出手，苏眠后退一步举起信封对峙。",
                camera_direction="中景双人/侧面固定",
                emotion_tone="对峙",
            )
        ],
    )
    shot = ShotPrompt(scene_id=1, prompt="顾衡要求苏眠交出信封。", duration_sec=5)

    rendered = build_timed_shot_prompt(shot, script)

    assert "中景双人/侧面固定" in rendered
    assert "顾衡：“把信给我。”" in rendered
    assert "信封位置清楚" in rendered
    assert "总时长约5s" in rendered
    assert shot.duration_sec == 5


def test_suspense_climax_prompt_uses_event_driven_beats():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="陆临露出真面目，苏眠用录音机反击。",
        duration_sec=15,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=15,
                location="旧钟楼地下室",
                characters=["苏眠", "顾衡", "陆临"],
                dialogue=[
                    DialogueLine(character="陆临", line="顾警官，七年了，你还是这么喜欢坏事。", emotion="温和却阴冷"),
                    DialogueLine(character="苏眠", line="陆临哥？", emotion="震惊"),
                    DialogueLine(character="陆临", line="眠眠，把第七封信给我。你不需要知道真相。", emotion="微笑"),
                ],
                action_description=(
                    "黑衣人摘下口罩露出陆临的脸，陆临逼近索要第七封信；"
                    "顾衡突然冲上前和陆临扭打，折叠刀掉在地上，陆临扑向苏眠，"
                    "苏眠抓起桌上的黑色录音机砸向陆临手腕，牛皮纸信封掉进积水，"
                    "火漆印章被水泡开，信封自己裂开，里面露出旧地图。"
                ),
                camera_direction="中景三人/侧面固定",
                emotion_tone="身份反转与动作冲突",
            )
        ],
    )
    shot = ShotPrompt(
        scene_id=1,
        prompt="陆临露出真面目，顾衡与陆临扭打，苏眠用录音机反击，信封落水露出地图。",
        duration_sec=15,
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "中景三人/侧面固定" in rendered
    assert "摘下口罩" in rendered
    assert "顾衡冲上前和陆临扭打" in rendered
    assert "苏眠抓起黑色录音机砸向陆临手腕" in rendered
    assert "火漆印章被水泡开" in rendered
    assert "露出的旧地图边缘" in rendered
    assert "中景双人/侧面固定" not in rendered
    assert "形状、材质、颜色、状态不变" not in rendered


def test_single_take_prompt_does_not_repeat_future_prop_lifecycle():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="苏眠收到短信。",
        duration_sec=8,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=8,
                location="钟楼街便利店外部",
                characters=["苏眠"],
                action_description="苏眠站在便利店屋檐下，左手紧攥信封，右手举着手机，凝视屏幕后准备行动。",
                camera_direction="中景缓慢推近至近景",
                emotion_tone="冷冽悬疑雨夜",
            )
        ],
    )
    prompt = (
        "【主体状态】苏眠站在便利店屋檐下，左手紧攥信封，右手举着手机。\n"
        "【动作时间轴】0-3秒看手机短信；3-8秒镜头缓慢推近。\n"
        "【绑定道具】第七封信牛皮纸信封：旧牛皮纸信封，边角发软，封口暗红色火漆印章完整，遇水后裂开，露出旧地图。\n"
        "【关键道具】第七封信牛皮纸信封：苏眠左手中紧攥，封口暗红色火漆印章完整，尚未打开。"
    )
    shot = ShotPrompt(scene_id=1, prompt=prompt, duration_sec=8)

    rendered = build_timed_shot_prompt(shot, script)

    assert "遇水后裂开" not in rendered
    assert "尚未打开" in rendered
    assert "完成情绪落点" not in rendered


def test_running_prompt_does_not_inherit_iron_door_sound_when_no_door_exists():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="小巷追逐。",
        duration_sec=8,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=8,
                location="雨夜小巷",
                characters=["苏眠", "顾衡"],
                action_description="苏眠被顾衡拉着冲进狭窄小巷，边跑边回头，黑衣人跟在后面。",
                camera_direction="手持跟拍",
                emotion_tone="逃逸压迫感",
            )
        ],
    )
    shot = ShotPrompt(scene_id=1, prompt="苏眠被拉着跑，回头看到黑衣人。", duration_sec=8)

    rendered = build_timed_shot_prompt(shot, script)

    assert "水花" in rendered
    assert "铁门摩擦声" not in rendered


def test_wet_street_envelope_closeup_does_not_become_map_reveal():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="苏眠站在便利店屋檐下。",
        duration_sec=8,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=8,
                location="钟楼街便利店外部",
                characters=["苏眠"],
                action_description=(
                    "苏眠站在便利店屋檐下，左手紧握旧牛皮纸信封，封口暗红色火漆印章清晰可见，"
                    "右手举手机，指尖微微发抖；雨夜地面积水反光。"
                ),
                camera_direction="大特写/低机位缓慢推近",
                emotion_tone="冷冽悬疑雨夜",
            )
        ],
    )
    shot = ShotPrompt(
        scene_id=1,
        prompt="大特写，苏眠左手紧握旧牛皮纸信封，火漆印章完整清楚，便利店外雨夜地面积水反光。",
        duration_sec=8,
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "火漆印章被水泡开" not in rendered
    assert "露出的旧地图边缘" not in rendered
    assert "火漆印章完整" in rendered


def test_short_image_to_video_prompt_limits_automatic_cut_count():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="苏眠站在便利店屋檐下。",
        duration_sec=7,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=7,
                location="钟楼街便利店外部",
                characters=["苏眠"],
                action_description=(
                    "苏眠独自站在便利店屋檐下，雨水打湿头发和衣服，左手紧攥信封，"
                    "右手持手机，看见短信后回头看向动静来源。"
                ),
                camera_direction="中近景/过肩固定",
                emotion_tone="紧张、悬疑、压抑",
            )
        ],
    )
    shot = ShotPrompt(
        scene_id=1,
        prompt="苏眠看手机短信，回头看向街道动静，左手信封保持完整。",
        duration_sec=7,
    )

    rendered = build_timed_shot_prompt(shot, script)
    timed_lines = [
        line for line in rendered.splitlines()
        if line.startswith("切镜头，") or re.match(r"^\d+(?:\.\d+)?s，", line)
    ]

    assert len(timed_lines) <= 3
    assert "对上一动作结果产生明确反应" not in rendered


def test_finalized_prompt_repairs_envelope_map_reveal_when_core_intent_is_holding_phone():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="苏眠站在便利店屋檐下。",
        duration_sec=10,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=10,
                location="钟楼街便利店外部",
                characters=["苏眠"],
                action_description="苏眠风衣被打湿，紧攥信封，右手握手机，盯着屏幕，眼神紧绷，指尖发抖。",
                camera_direction="大特写/低机位缓慢推近",
                emotion_tone="冷冽悬疑雨夜",
            )
        ],
    )
    prompt = (
        "【时间轴分镜】\n"
        "核心分镜意图：苏眠风衣被打湿，紧攥信封，右手握手机，盯着屏幕，眼神紧绷，指尖发抖\n"
        "3.2s，大特写/低机位缓慢推近，牛皮纸信封掉在积水边缘，暗红色火漆印章被水泡开，封口裂开，湿透的纸纤维和露出的旧地图边缘清楚可见；所有可读中文文字优先作为后期叠加；画面只表现屏幕光、纸张、照片、地图、手部动作和角色反应，不强求模型生成准确文字。\n"
        "切镜头，6.8s，大特写/低机位缓慢推近，牛皮纸信封掉在积水边缘，暗红色火漆印章被水泡开，封口裂开，湿透的纸纤维和露出的旧地图边缘清楚可见；所有可读中文文字优先作为后期叠加；画面只表现屏幕光、纸张、照片、地图、手部动作和角色反应，不强求模型生成准确文字。\n"
        "【连续锁定】\n总时长约10s，严格按上述时间轴执行，只在写明“切镜头”的位置切换。"
    )
    shot = ShotPrompt(scene_id=1, prompt=prompt, duration_sec=10)

    rendered = build_timed_shot_prompt(shot, script)

    assert "火漆印章被水泡开" not in rendered
    assert "露出的旧地图边缘" not in rendered
    assert "火漆印章完整" in rendered
    assert "右手手机屏幕蓝光" in rendered


def test_finalized_prompt_repairs_unmentioned_recorder_and_short_coverage():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="苏眠站在便利店屋檐下。",
        duration_sec=8,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=8,
                location="钟楼街便利店外部",
                characters=["苏眠"],
                action_description="苏眠站在便利店屋檐下，全身湿透，左手紧攥信封，右手持手机，指尖微颤，神情紧张。",
                camera_direction="大特写/低机位缓慢推近",
                emotion_tone="紧张、悬疑、压抑",
            )
        ],
    )
    prompt = (
        "【时间轴分镜】\n"
        "核心分镜意图：苏眠站在便利店屋檐下，全身湿透，左手紧攥信封，右手持手机，指尖微颤，神情紧张\n"
        "3.2s，大特写/插入缓慢推近，录音机清楚可见，重点表现手部动作、材质、光线反应和状态变化；所有可读中文文字优先作为后期叠加；画面只表现屏幕光、纸张、照片、地图、手部动作和角色反应，不强求模型生成准确文字。\n"
        "【连续锁定】\n"
        "总时长约8s，严格按上述时间轴执行，只在写明“切镜头”的位置切换。"
    )
    shot = ShotPrompt(scene_id=1, prompt=prompt, duration_sec=8)

    rendered = build_timed_shot_prompt(shot, script)

    assert "录音机" not in rendered
    assert "8s，大特写/低机位缓慢推近" in rendered
    assert "信封" in rendered
    assert "旧牛皮纸信封" not in rendered
    assert "手机屏幕蓝光" in rendered
    assert "3.2s" not in rendered


def test_prompt_lint_replaces_duplicate_timed_segments_with_reaction_beat():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="苏眠站在便利店屋檐下。",
        duration_sec=10,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=10,
                location="钟楼街便利店外部",
                characters=["苏眠"],
                action_description="苏眠紧攥信封，右手握手机，盯着屏幕，眼神紧绷，指尖发抖。",
                camera_direction="大特写/低机位缓慢推近",
                emotion_tone="冷冽悬疑雨夜",
            )
        ],
    )
    repeated_line = "大特写/低机位缓慢推近，苏眠左手紧握旧牛皮纸信封，暗红色火漆印章完整清楚；右手手机屏幕蓝光照到手指。"
    prompt = (
        "【时间轴分镜】\n"
        "核心分镜意图：苏眠紧攥信封，右手握手机，盯着屏幕，眼神紧绷，指尖发抖\n"
        f"3.2s，{repeated_line}\n"
        f"切镜头，6.8s，{repeated_line}\n"
        "【连续锁定】\n总时长约10s，严格按上述时间轴执行，只在写明“切镜头”的位置切换。"
    )
    shot = ShotPrompt(scene_id=1, prompt=prompt, duration_sec=10)

    rendered = build_timed_shot_prompt(shot, script)

    assert rendered.count(repeated_line) == 1
    assert "切镜头，6.8s，近景/正面固定，苏眠对上一动作结果产生明确反应" in rendered


def test_prompt_lint_repairs_three_person_finalized_frame_label():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="陆临露出真面目。",
        duration_sec=9,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=9,
                location="旧钟楼地下室",
                characters=["苏眠", "顾衡", "陆临"],
                dialogue=[
                    DialogueLine(character="陆临", line="顾警官，七年了，你还是这么喜欢坏事。", emotion="温和却阴冷"),
                    DialogueLine(character="苏眠", line="陆临哥？", emotion="震惊"),
                ],
                action_description="黑衣人摘下口罩露出陆临的脸，三人在地下室对峙。",
                camera_direction="中景三人/侧面固定",
                emotion_tone="身份反转",
            )
        ],
    )
    prompt = (
        "【时间轴分镜】\n"
        "核心分镜意图：陆临露出真面目，苏眠和顾衡震惊\n"
        "9s，中景双人/侧面固定，陆临摘下口罩，苏眠后退，顾衡挡在她前方。\n"
        "【连续锁定】\n总时长约9s，严格按上述时间轴执行，只在写明“切镜头”的位置切换。"
    )
    shot = ShotPrompt(scene_id=1, prompt=prompt, duration_sec=9)

    rendered = build_timed_shot_prompt(shot, script)

    assert "中景三人/侧面固定" in rendered
    assert "中景双人/侧面固定" not in rendered


def test_gu_heng_entrance_does_not_become_su_mian_pov_turnback():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="顾衡出现。",
        duration_sec=10,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=10,
                location="钟楼街便利店外部",
                characters=["苏眠", "顾衡"],
                dialogue=[
                    DialogueLine(character="顾衡", line="你不该一个人来。", emotion="平静"),
                    DialogueLine(character="苏眠", line="短信不是你发的？", emotion="冷笑"),
                    DialogueLine(character="顾衡", line="不是。", emotion="平静"),
                ],
                action_description=(
                    "顾衡推开便利店玻璃门走出，苏眠见他后立刻将信封藏到身后，两人对视，"
                    "顾衡先看门口监控，又瞥向街对面。"
                ),
                camera_direction="中景双人/侧面固定",
                emotion_tone="紧张试探",
            )
        ],
    )
    shot = ShotPrompt(
        scene_id=1,
        prompt="顾衡推门而出，苏眠藏起信封，两人对视，顾衡先看监控，又瞥向街对面。",
        duration_sec=10,
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "模拟苏眠视角回头看向动静来源" not in rendered
    assert "顾衡走出" in rendered
    assert "黑伞" not in rendered
    assert "看门口监控" in rendered
    assert "瞥向街对面" in rendered
    assert "信封位置清楚" in rendered
    assert "门铃轻响" in rendered
    assert "苏眠把信封藏到身后" in rendered


def test_photo_back_check_is_current_action_not_turnback_or_flashback():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="苏眠查看照片背面。",
        duration_sec=10,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=10,
                location="钟楼街便利店外部",
                characters=["苏眠"],
                action_description=(
                    "苏眠站在便利店屋檐下，左手紧握旧牛皮纸信封，右手举着手机看屏幕；"
                    "随后从信封中抽出泛黄照片，查看背面字迹又放回，最后抬眸望向雨街。"
                ),
                camera_direction="中近景到特写",
                emotion_tone="由不安转为坚定",
            )
        ],
    )
    prompt = (
        "【时间轴分镜】\n"
        "核心分镜意图：苏眠站在便利店屋檐下，左手紧握旧牛皮纸信封，右手举着手机看屏幕，随后从信封中抽出泛黄照片，查看背面字迹又放回。最后抬眸望向雨街，神情由不安转为坚定\n"
        "2.6s，中近景/过肩固定，手机屏幕蓝光照亮人物眉眼，短信提示音尖锐短促，手部和手机位置稳定。\n"
        "切镜头，2.2s，快速摇摄或反应镜头，模拟苏眠视角回头看向动静来源，衣服摩擦声和心跳声增强。\n"
        "切镜头，2s，大特写/插入缓慢推近，牛皮纸信封、手机、照片清楚可见，重点表现手部动作、材质、光线反应和状态变化；所有可读中文文字优先作为后期叠加；画面只表现屏幕光、纸张、照片、地图、手部动作和角色反应，不强求模型生成准确文字。\n"
        "切镜头，3.2s，特写/闪回叠加，照片翻到背面，背面字迹区域短促掠过，纸面磨损和手指压痕清楚，whoosh音效，文字内容优先后期叠加。\n"
        "【连续锁定】\n总时长约10s，严格按上述时间轴执行，只在写明“切镜头”的位置切换。"
    )
    shot = ShotPrompt(scene_id=1, prompt=prompt, duration_sec=10)

    rendered = build_timed_shot_prompt(shot, script)

    assert "模拟苏眠视角回头看向动静来源" not in rendered
    assert "闪回叠加" not in rendered
    assert "whoosh" not in rendered
    assert "抽出泛黄照片" in rendered
    assert "翻到背面查看字迹区域" in rendered
    assert "不做闪回" in rendered


def test_prompt_lint_marks_recording_and_phone_voice_as_audio_only():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="录音和电话只用声音表现。",
        duration_sec=12,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=12,
                location="旧钟楼地下室",
                characters=["苏眠", "顾衡", "陆临"],
                action_description="录音机播放苏远山声音，陆临拿起手机，电话那头传来苍老声音。",
                camera_direction="中近景固定",
                emotion_tone="悬疑",
            )
        ],
    )
    prompt = (
        "【时间轴分镜】\n"
        "核心分镜意图：录音机播放苏远山声音，陆临拨通电话\n"
        "6s，大特写/桌面固定，黑色录音机播放苏远山声音。\n"
        "切镜头，6s，中近景/侧面固定，陆临拿起手机拨号，电话那头传来苍老声音。\n"
        "【连续锁定】\n总时长约12s，严格按上述时间轴执行，只在写明“切镜头”的位置切换。"
    )
    shot = ShotPrompt(scene_id=1, prompt=prompt, duration_sec=12)

    rendered = build_timed_shot_prompt(shot, script)

    assert "录音中的人物只用声音表现，不出现新人物画面" in rendered
    assert "电话那头只用声音表现，不出现新人物画面" in rendered


def test_video_agent_uses_the_same_effective_prompt_for_provider_submission():
    agent = VideoGenerationAgent()
    agent.api_client = type("HappyHorseClient", (), {"provider_name": "happyhorse"})()
    shot = agent._composed_shot(_prompts(), _prompts().shots[0], PromptPolicy(style_prefix="style"))
    assert "style" in shot.prompt
    assert "【避免出现】" in shot.prompt
    assert shot.negative_prompt == ""


def test_prepare_shot_request_never_mutates_shared_scene_prompts():
    prompts = GenerationPrompts(
        api_provider="happyhorse",
        shots=[
            ShotPrompt(scene_id=1, prompt="镜头一：走进房间", duration_sec=5),
            ShotPrompt(scene_id=2, prompt="镜头二：关上门", duration_sec=5),
        ],
    )

    prepared, _ = VideoGenerationAgent().prepare_shot_request(
        prompts.shots[1],
        generation_prompts=prompts,
        prompt_policy=PromptPolicy(),
        visual_design=None,
        scene_vision_descriptions=None,
        script=None,
    )

    assert prompts.shots[0].prompt == "镜头一：走进房间"
    assert prompts.shots[1].prompt == "镜头二：关上门"
    assert "镜头一" not in prepared.prompt
    assert "镜头二" in prepared.prompt


def test_prepare_shot_request_adds_timed_structure_from_script_without_mutating_source():
    script = ScriptBreakdown(
        title="test",
        genre="test",
        logline="test",
        duration_sec=8,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=8,
                location="rainy street",
                characters=["苏眠", "顾衡"],
                action_description="顾衡向苏眠伸出手，苏眠后退一步举起信封对峙。",
                camera_direction="中近景/侧面固定",
                emotion_tone="紧张",
            )
        ],
    )
    prompts = GenerationPrompts(
        api_provider="mock",
        shots=[ShotPrompt(scene_id=1, prompt="顾衡要求苏眠交出信封。", duration_sec=8)],
    )

    prepared, _ = VideoGenerationAgent().prepare_shot_request(
        prompts.shots[0],
        generation_prompts=prompts,
        prompt_policy=PromptPolicy(),
        visual_design=None,
        scene_vision_descriptions=None,
        script=script,
    )

    assert prompts.shots[0].prompt == "顾衡要求苏眠交出信封。"
    assert "【时间轴分镜】" in prepared.prompt
    assert "总时长约8s" in prepared.prompt
    assert prepared.duration_sec == 8
    assert script.scenes[0].duration_sec == 8


def test_first_shot_keeps_future_character_out_and_pushes_sms_to_insert_closeup():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="苏眠先看短信。",
        duration_sec=8,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=8,
                location="钟楼街便利店外部",
                characters=["苏眠", "顾衡"],
                action_description="苏眠站在便利店屋檐下，右手举着手机看短信，左手紧攥信封。",
                camera_direction="中近景/过肩固定",
                emotion_tone="紧张不安",
            )
        ],
    )
    script.asset_manifest = build_manifest_from_scenes(script)
    shot = ShotPrompt(
        scene_id=1,
        prompt="苏眠站在便利店屋檐下看短信，左手紧握信封。",
        duration_sec=8,
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "顾衡" not in rendered
    assert "大特写/插入" in rendered
    assert "手机屏幕蓝光" in rendered
    assert "短信内容作为可读信息优先后期叠加" in rendered


def test_dirty_first_phone_shot_does_not_trigger_later_lock_or_surveillance_events():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="苏眠先看短信。",
        duration_sec=6,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=6,
                location="钟楼街便利店外部",
                characters=["苏眠"],
                action_description="苏眠站在便利店屋檐下，左手紧攥信封，右手手机屏幕亮起显示短信。",
                camera_direction="中近景/过肩固定",
                emotion_tone="紧张不安",
            )
        ],
    )
    shot = ShotPrompt(
        scene_id=1,
        prompt=(
            "核心分镜意图：苏眠站在便利店屋檐下，左手紧攥信封，右手手机屏幕光照亮她的脸，表情紧张不安\n"
            "1.7s，中近景/过肩固定，手机屏幕蓝光照亮人物眉眼。\n"
            "切镜头，2s，大特写/插入缓慢推近，手部开锁动作、钥匙入锁、红色摄像头指示灯、电箱和折叠刀清楚可见。"
        ),
        duration_sec=6,
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "开锁" not in rendered
    assert "钥匙入锁" not in rendered
    assert "电箱" not in rendered
    assert "折叠刀" not in rendered
    assert "摄像头" not in rendered
    assert "手机屏幕蓝光" in rendered
    assert "信封" in rendered


def test_gu_heng_entrance_does_not_pull_underground_surveillance_template():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="顾衡走出便利店。",
        duration_sec=10,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=10,
                location="钟楼街便利店外部",
                characters=["苏眠", "顾衡"],
                dialogue=[
                    DialogueLine(character="顾衡", line="你不该一个人来。", emotion="低沉"),
                    DialogueLine(character="苏眠", line="短信不是你发的？", emotion="警惕"),
                    DialogueLine(character="顾衡", line="不是。", emotion="平静"),
                ],
                action_description="顾衡推开便利店玻璃门走出，苏眠把信封藏到身后，两人对视，顾衡看门口监控又瞥向街对面。",
                camera_direction="中景双人/侧面固定",
                emotion_tone="紧张试探",
            )
        ],
    )
    shot = ShotPrompt(
        scene_id=1,
        prompt="顾衡推门而出，苏眠藏起信封，两人对视，顾衡看监控又瞥向街对面。",
        duration_sec=10,
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "顾衡走出" in rendered
    assert "黑伞" not in rendered
    assert "电箱" not in rendered
    assert "折叠刀" not in rendered
    assert "红色摄像头指示灯一闪而过" not in rendered
    assert "门铃轻响" in rendered
    assert "苏眠把信封藏到身后" in rendered


def test_alley_chase_does_not_pull_lu_lin_reveal_or_basement_staging():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="两人逃进小巷。",
        duration_sec=8,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=8,
                location="雨夜小巷",
                characters=["苏眠", "顾衡"],
                action_description="顾衡抓住苏眠手腕，两人冲进狭窄小巷，苏眠边跑边回头，黑衣人从远处跟上。",
                camera_direction="手持跟拍",
                emotion_tone="逃逸压迫感",
            )
        ],
    )
    shot = ShotPrompt(
        scene_id=1,
        prompt="顾衡拉着苏眠冲进小巷，苏眠边跑边回头，黑衣人远远跟在后面。",
        duration_sec=8,
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "冲进狭窄小巷" in rendered or "手持跟拍" in rendered
    assert "摘下口罩" not in rendered
    assert "露出陆临" not in rendered
    assert "长桌" not in rendered
    assert "地下室" not in rendered


def test_plain_lu_lin_phone_call_does_not_become_final_entrance_scene():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="陆临拨电话。",
        duration_sec=6,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=6,
                location="旧钟楼地下室",
                characters=["陆临"],
                action_description="陆临独自站在台灯旁，拿起手机拨通一个号码，压低声音说话。",
                camera_direction="中近景/侧面固定",
                emotion_tone="冷静阴沉",
            )
        ],
    )
    shot = ShotPrompt(scene_id=1, prompt="陆临独自拿起手机拨号，压低声音说话。", duration_sec=6)

    rendered = build_timed_shot_prompt(shot, script)

    assert "陆临" in rendered
    assert "苏眠和顾衡冲入暗道" not in rendered
    assert "被水泡开的暗红色火漆印章" not in rendered
    assert "苏眠左手紧握旧牛皮纸信封" not in rendered
    assert "短信内容作为可读信息" not in rendered


def test_finalized_prompt_adds_coverage_backfill_for_missing_key_props():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="苏眠先看短信。",
        duration_sec=6,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=6,
                location="钟楼街便利店外部",
                characters=["苏眠"],
                action_description="苏眠站在便利店屋檐下，右手举着手机看短信，左手紧攥旧牛皮纸信封。",
                camera_direction="中近景固定",
                emotion_tone="紧张不安",
            )
        ],
    )
    shot = ShotPrompt(
        scene_id=1,
        prompt=(
            "【时间轴分镜】\n"
            "核心分镜意图：苏眠站在屋檐下。\n"
            "6s，中近景/正面固定，苏眠站在便利店屋檐下，只表现紧张反应。\n"
            "【连续锁定】\n"
            "总时长约6s，严格按上述时间轴执行，只在写明“切镜头”的位置切换。"
        ),
        duration_sec=6,
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "【覆盖补强】" in rendered
    assert "手机屏幕蓝光" in rendered
    assert "短信内容作为可读信息优先后期叠加" in rendered
    assert "信封" in rendered


def test_finalized_first_shot_replaces_stale_storage_cabinet_event():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="苏眠先看短信。",
        duration_sec=8,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=8,
                location="钟楼街便利店外部",
                characters=["苏眠"],
                action_description="苏眠全身湿透，雨水顺着发梢和风衣下摆滴落，左手紧攥信封，右手举着亮屏手机显示短信。",
                camera_direction="中近景/低机位固定",
                emotion_tone="紧张不安",
            )
        ],
    )
    shot = ShotPrompt(
        scene_id=1,
        prompt=(
            "【时间轴分镜】\n"
            "核心分镜意图：苏眠全身湿透，雨水顺着发梢和风衣下摆不停滴落，左手紧攥信封，右手举着亮屏手机显示短信\n"
            "8s，中近景/低机位固定，苏眠躲在废弃储物柜后，积水滴落声清楚，黑衣人的手电光从柜缝扫过人物眼睛，顾衡捂住苏眠的嘴，午夜钟声打断搜寻。\n"
            "【连续锁定】\n"
            "总时长约8s，严格按上述时间轴执行，只在写明“切镜头”的位置切换。"
        ),
        duration_sec=8,
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "储物柜" not in rendered
    assert "手电光" not in rendered
    assert "顾衡捂住苏眠的嘴" not in rendered
    assert "午夜钟声" not in rendered
    assert "手机屏幕蓝光" in rendered
    assert "短信内容作为可读信息优先后期叠加" in rendered
    assert "信封" in rendered


def test_core_intent_line_only_keeps_current_shot_and_does_not_pull_later_characters():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="苏眠先看短信。",
        duration_sec=15,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=15,
                location="钟楼街便利店外部",
                characters=["苏眠", "顾衡", "陆临"],
                action_description="苏眠紧攥信封，指尖发抖，神情慌张；顾衡撑伞，面色严肃；陆临在街对面远处，幽灵般监视。",
                camera_direction="中景固定",
                emotion_tone="雨夜悬疑",
            )
        ],
    )
    shot = ShotPrompt(
        scene_id=1,
        prompt="苏眠紧攥信封，指尖发抖，神情慌张；顾衡撑伞，面色严肃；陆临在街对面远处，幽灵般监视。",
        duration_sec=15,
    )

    rendered = build_timed_shot_prompt(shot, script)
    core_line = next(line for line in rendered.splitlines() if line.startswith("核心分镜意图："))

    assert "顾衡" not in core_line
    assert "陆临" not in core_line
    assert "黑衣人" not in core_line
    assert "苏眠" in core_line
    assert "信封" in core_line


def test_back_note_about_gu_heng_becomes_a_visible_insert_shot():
    script = ScriptBreakdown(
        title="雨夜第七封信",
        genre="悬疑",
        logline="信封背面有警告字样。",
        duration_sec=8,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=8,
                location="旧钟楼地下室",
                characters=["苏眠", "顾衡"],
                action_description="苏眠翻到照片背面，看到背面写着不要交给顾衡。",
                camera_direction="特写",
                emotion_tone="悬疑",
            )
        ],
    )
    script.asset_manifest = build_manifest_from_scenes(script)
    shot = ShotPrompt(
        scene_id=1,
        prompt="苏眠把照片翻到背面，看到不要交给顾衡。",
        duration_sec=8,
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "不要交给顾衡" in rendered
    assert "大特写/插入" in rendered
    assert "背面字迹区域清楚可见" in rendered
    assert "不新增人物" in rendered


def test_prepared_provider_prompt_is_preserved_and_only_receives_reference_binding():
    captured = {}

    class CaptureClient:
        provider_name = "mock"

        async def generate_video(self, **kwargs):
            captured.update(kwargs)
            return type("Result", (), {
                "success": True,
                "video_url": "",
                "local_path": None,
                "duration_sec": 5,
                "error_message": "",
            })()

    agent = VideoGenerationAgent(api_client=CaptureClient(), max_retries=1)
    shot = ShotPrompt(scene_id=1, prompt="already final", duration_sec=5)
    result = asyncio.run(agent.generate_shot(
        shot,
        ["reference.png"],
        reference_labels=["scene anchor"],
        request_is_prepared=True,
        provider_prompt="the exact provider prompt",
    ))

    assert result.success is True
    assert captured["prompt"].splitlines()[0] == "the exact provider prompt"
    assert "[Reference binding]" in captured["prompt"]
    assert "Image 1: scene anchor." in captured["prompt"]
    assert captured["image_references"] == ["reference.png"]


def test_compact_reference_prompt_preserves_key_constraints_without_hard_cutting():
    agent = VideoGenerationAgent()
    prompt = (
        "【时间轴分镜】\n"
        "总时长约12s，镜头1表现人物站在雨夜街口，动作继续推进，保持原有机位和角色站位。\n"
        "这是大量无关说明，用来模拟过长的 prompt，应该被压缩掉但不影响关键约束的保留。\n"
        "镜头2必须保持参考图1的空间关系、光线方向、角色五官比例、发型和服装不变，禁止新增人物和新道具。\n"
        "补充很多无关重复文字，补充很多无关重复文字，补充很多无关重复文字，补充很多无关重复文字。\n"
        "【连续锁定】\n"
        "全程保持道具状态、人物表情和背景连续，不要把上一镜的动作拆成新的镜头。"
    )

    compacted = agent._compact_reference_prompt(prompt, max_length=220)

    assert len(compacted) <= 220
    assert "镜头1" in compacted
    assert "参考图1" in compacted
    assert "禁止新增人物" in compacted or "禁止新增人物和新道具" in compacted
    assert "【连续锁定】" in compacted or "全程保持" in compacted


def test_reference_motion_strength_is_tuned_by_shot_type():
    agent = VideoGenerationAgent()

    dialogue = ShotPrompt(scene_id=1, prompt="Hero dialogue close-up, calm face.", duration_sec=5)
    action = ShotPrompt(scene_id=2, prompt="Hero runs into a chase with handheld tracking.", duration_sec=5)

    assert agent._motion_strength_for_shot(dialogue, has_references=True) == 0.62
    assert agent._motion_strength_for_shot(action, has_references=True) == 0.88


def test_retry_attempt_adds_quality_correction_and_steadier_motion():
    captured = []

    class FlakyClient:
        provider_name = "mock"

        async def generate_video(self, **kwargs):
            captured.append(kwargs)
            return type("Result", (), {
                "success": len(captured) == 2,
                "video_url": "",
                "local_path": None,
                "duration_sec": 5,
                "error_message": "temporary failure",
            })()

    agent = VideoGenerationAgent(api_client=FlakyClient(), max_retries=2)
    shot = ShotPrompt(scene_id=1, prompt="Hero dialogue close-up.", duration_sec=5)
    shot.motion_strength = agent._motion_strength_for_shot(shot, has_references=True)

    result = asyncio.run(agent._retry_loop(shot, ["reference.png"], "base provider prompt"))

    assert result.success is True
    assert captured[0]["prompt"] == "base provider prompt"
    assert "[Retry correction]" in captured[1]["prompt"]
    assert "Strictly preserve the reference identity" in captured[1]["prompt"]
    assert captured[1]["motion_strength"] <= captured[0]["motion_strength"]


def test_prompt_overrides_are_normalized_to_scene_ids_without_wrong_scene_migration():
    state = DramaState(project_id="project", script=_script(), prompt_overrides={"0": "first", "1": "scene one"})
    api_context._normalize_prompt_overrides(state)
    assert state.prompt_overrides == {"1": "scene one"}


def test_prompt_policy_round_trip_is_project_state_data():
    state = DramaState(project_id="project", prompt_policy=PromptPolicy(style_prefix="style", global_negative_prompt="avoid"))
    restored = api_context._state_from_json(api_context._state_to_json(state))
    assert restored.prompt_policy.style_prefix == "style"
    assert restored.prompt_policy.global_negative_prompt == "avoid"


def test_visual_stage_state_round_trips_with_its_script_signature():
    state = DramaState(
        project_id="project",
        script=_script(),
        visual_stage="generating",
        visual_script_signature="script-v1",
        visual_job_id="visual-job-1",
        visual_performance={"requests": [{"stage": "visualize", "elapsed_ms": 120}]},
    )

    restored = api_context._state_from_json(api_context._state_to_json(state))

    assert restored.visual_stage == "generating"
    assert restored.visual_script_signature == "script-v1"
    assert restored.visual_job_id == "visual-job-1"
    assert restored.visual_performance["requests"][0]["elapsed_ms"] == 120


def test_creative_profile_round_trip_is_project_state_data():
    state = DramaState(
        project_id="project",
        creative_profile=CreativeProfile(genre_tags=["悬疑", "都市"], art_style="赛博朋克", video_ratio="16:9"),
    )
    restored = api_context._state_from_json(api_context._state_to_json(state))

    assert restored.creative_profile.genre_tags == ["悬疑", "都市"]
    assert restored.creative_profile.art_style == "赛博朋克"
    assert restored.creative_profile.video_ratio == "16:9"


def test_legacy_project_creative_profile_is_inferred_from_visual_design():
    legacy = DramaState(
        project_id="legacy",
        script=_script(),
        visual_design=VisualDesign(
            overall_style="国风", color_palette="朱砂", aspect_ratio="16:9", scenes=[],
        ),
    )
    state_json = api_context._state_to_json(legacy)
    data = json.loads(state_json)
    data.pop("creative_profile")
    restored = api_context._state_from_json(json.dumps(data, ensure_ascii=False))

    assert restored.creative_profile.art_style == "国风"
    assert restored.creative_profile.video_ratio == "16:9"


def test_back_full_character_prompt_prioritizes_strict_rear_view_and_excludes_faces():
    prompt = build_character_image_prompt("角色", "黑色短发，深灰色风衣", "back_full")
    negative = build_character_negative_prompt("back_full")

    assert "相机严格位于人物背脊中线正后方" in prompt
    assert "零脸部可见" in prompt
    assert "三分之二背面" in prompt
    assert "正脸" in negative
    assert "三分之二背面" in negative


def test_character_image_prompt_uses_chinese_constraints_without_hardcoded_english():
    from src.services.asset_prompt_composer import _character_project_constraint, _reference_lock

    prompt = build_character_image_prompt(
        "角色", "黑色短发，深灰色风衣", "front_half", art_style="电影级真人写实"
    )
    negative = build_character_negative_prompt("front_half", art_style="电影级真人写实")
    combined = "\n".join((prompt, negative, _character_project_constraint("电影级真人写实", "9:16"), _reference_lock("character", "电影级真人写实")))

    assert "MANDATORY STYLE" not in combined
    assert "OUTPUT FORMAT" not in combined
    assert "FRAMING OVERRIDE" not in combined
    assert "Reference lock" not in combined
    assert "English hard constraint" not in combined
    assert "split panels" not in combined
    assert "必须是真人写实照片" in prompt
    assert "参考图" in combined or "参考" in combined


def test_legacy_english_storyboard_prompts_are_flagged_without_rewriting_them():
    state = DramaState(
        project_id="project",
        generation_prompts=GenerationPrompts(
            api_provider="happyhorse",
            shots=[ShotPrompt(scene_id=1, prompt="A man walks through a dark corridor")],
        ),
    )
    restored = api_context._state_from_json(api_context._state_to_json(state))

    assert restored.generation_prompts.shots[0].prompt == "A man walks through a dark corridor"
    assert restored.generation_prompts.needs_refresh is True
    assert restored.generation_prompts.prompt_language == "mixed"


def test_visual_prompt_system_uses_chinese_examples_for_chinese_storyboards():
    assert "He picks up the cup" not in VISUAL_PROMPT_SYSTEM
    assert "soft warm light" not in VISUAL_PROMPT_SYSTEM
    assert "手指伸向杯沿" in VISUAL_PROMPT_SYSTEM


def test_phone_call_timeline_does_not_become_sms_prompt():
    script = ScriptBreakdown(
        title="test",
        genre="悬疑",
        logline="test",
        duration_sec=10,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=10,
                location="旧钟楼地下室入口",
                characters=["陆临"],
                action_description="陆临独自站在原地拿起手机拨号，电话那头传来苍老声音。",
                camera_direction="中近景/侧面固定",
                emotion_tone="阴冷",
            )
        ],
    )
    shot = ShotPrompt(scene_id=1, prompt="陆临拨通号码，电话那头说她已经找到入口了。", duration_sec=10)

    rendered = build_timed_shot_prompt(shot, script)

    assert "拨号" in rendered or "通话" in rendered
    assert "电话那头只用声音表现" in rendered or "电话内容只用声音表现" in rendered
    assert "短信提示音" not in rendered
    assert "短信内容" not in rendered


def test_surveillance_electric_box_scene_does_not_inherit_lock_sound():
    script = ScriptBreakdown(
        title="test",
        genre="悬疑",
        logline="test",
        duration_sec=9,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=7,
                duration_sec=9,
                location="钟楼侧巷监控死角",
                characters=["苏眠", "顾衡"],
                action_description="苏眠和顾衡贴着墙移动，顾衡看见摄像头红光和墙边电箱，拿出折叠刀切断线路。",
                camera_direction="中近景/手持轻微晃动",
                emotion_tone="紧张潜入",
            )
        ],
    )
    shot = ShotPrompt(scene_id=7, prompt="摄像头红光闪烁，顾衡用折叠刀处理电箱线路。", duration_sec=9)

    rendered = build_timed_shot_prompt(shot, script)

    assert "摄像头" in rendered
    assert "电箱" in rendered
    assert "折叠刀" in rendered
    assert "钥匙入锁" not in rendered
    assert "铁门摩擦声" not in rendered


def test_compact_prompt_uses_canonical_scene_action_for_storage_search_event():
    """Generic compact fields must not hide a concrete scripted event."""
    script = ScriptBreakdown(
        title="test",
        genre="悬疑",
        logline="test",
        duration_sec=10,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=6,
                duration_sec=10,
                location="地下通道",
                characters=["苏眠", "顾衡", "黑衣人"],
                action_description=(
                    "顾衡关掉手电，苏眠被拉到废弃储物柜后蹲下；黑衣人踩过积水搜寻，"
                    "手电光从柜缝扫过苏眠眼睛，顾衡捂住她的嘴，午夜钟声打断搜寻。"
                ),
                camera_direction="中近景/低机位固定",
                emotion_tone="极限紧张",
            )
        ],
    )
    shot = ShotPrompt(
        scene_id=6,
        prompt=(
            "【主体状态】两人保持低姿态不动。\n"
            "【动作时间轴】0-10秒保持安静，等待危险过去。\n"
            "【环境】地下通道。"
        ),
        duration_sec=10,
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "手电光从柜缝扫过人物眼睛" in rendered
    first_timed_line = next(line for line in rendered.splitlines() if re.match(r"^(?:切镜头，)?\d", line))
    assert "当前信息产生克制反应" not in first_timed_line


def test_dark_passage_copper_bell_does_not_get_phone_sms_addendum():
    script = ScriptBreakdown(
        title="test",
        genre="悬疑",
        logline="test",
        duration_sec=12,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=14,
                duration_sec=12,
                location="旧钟楼地下室",
                characters=["苏眠", "顾衡"],
                action_description="灯光突然全部熄灭，沉重齿轮声响起，裂开的铜钟缓缓下沉，露出暗道入口。",
                camera_direction="中远景/固定",
                emotion_tone="惊惧",
            )
        ],
    )
    shot = ShotPrompt(scene_id=14, prompt="铜钟下沉，暗道入口露出。", duration_sec=12)

    rendered = build_timed_shot_prompt(shot, script)

    assert "铜钟" in rendered
    assert "暗道" in rendered
    assert "手机屏幕蓝光" not in rendered
    assert "短信内容" not in rendered
    assert "短信提示音" not in rendered


def test_envelope_water_reveal_is_not_repeated_across_timed_segments():
    script = ScriptBreakdown(
        title="test",
        genre="悬疑",
        logline="test",
        duration_sec=15,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=13,
                duration_sec=15,
                location="旧钟楼地下室",
                characters=["苏眠", "顾衡", "陆临"],
                action_description=(
                    "顾衡和陆临扭打，折叠刀掉落；信封掉进积水，火漆印章被水泡开，露出旧地图，"
                    "苏眠抓起录音机反击。"
                ),
                camera_direction="中景三人/侧面固定",
                emotion_tone="动作冲突",
            )
        ],
    )
    prompt = (
        "【时间轴分镜】\n"
        "核心分镜意图：信封掉进积水，火漆印章被水泡开，露出旧地图。\n"
        "3s，大特写/低机位缓慢推近，牛皮纸信封掉在积水边缘，暗红色火漆印章被水泡开。\n"
        "切镜头，4s，中近景/手持轻微晃动，顾衡和陆临扭打。\n"
        "切镜头，3s，大特写/低机位缓慢推近，牛皮纸信封掉在积水边缘，暗红色火漆印章被水泡开。\n"
        "切镜头，5s，中景三人/侧面固定，苏眠抓起录音机反击。\n"
        "【连续锁定】\n总时长约15s，严格按上述时间轴执行。"
    )
    shot = ShotPrompt(scene_id=13, prompt=prompt, duration_sec=15)

    rendered = build_timed_shot_prompt(shot, script)

    timed_lines = [line for line in rendered.splitlines() if re.match(r"^(?:切镜头，)?\d", line)]
    assert sum("牛皮纸信封掉在积水边缘" in line for line in timed_lines) == 1
    assert sum("火漆印章被水泡开" in line for line in timed_lines) == 1


def test_malformed_timeline_header_is_repaired():
    script = ScriptBreakdown(
        title="test",
        genre="悬疑",
        logline="test",
        duration_sec=6,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=11,
                duration_sec=6,
                location="旧钟楼",
                characters=["苏眠"],
                action_description="苏眠停下脚步看向前方。",
                camera_direction="中近景",
                emotion_tone="紧张",
            )
        ],
    )
    shot = ShotPrompt(
        scene_id=11,
        prompt="时间轴分镜】\n核心分镜意图：苏眠停下脚步。\n6s，中近景固定，苏眠看向前方。",
        duration_sec=6,
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert rendered.startswith("【时间轴分镜】")
    assert "时间轴分镜】" in rendered


def test_gu_heng_entrance_does_not_start_with_sms_when_sms_is_only_dialogue_context():
    script = ScriptBreakdown(
        title="test",
        genre="悬疑",
        logline="test",
        duration_sec=15,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=2,
                duration_sec=15,
                location="钟楼街便利店外部",
                characters=["苏眠", "顾衡"],
                dialogue=[
                    DialogueLine(character="顾衡", line="你不该一个人来。", emotion="低沉"),
                    DialogueLine(character="苏眠", line="短信不是你发的？", emotion="冷笑"),
                    DialogueLine(character="顾衡", line="不是。", emotion="平静"),
                ],
                action_description="顾衡撑黑伞从便利店走出，苏眠将信藏在身后。",
                camera_direction="中近景/侧面固定",
                emotion_tone="雨夜紧张",
            )
        ],
    )
    shot = ShotPrompt(scene_id=2, prompt="顾衡撑黑伞从便利店走出，苏眠将信藏在身后。", duration_sec=15)

    rendered = build_timed_shot_prompt(shot, script)
    first_timed_line = next(line for line in rendered.splitlines() if re.match(r"^\d", line))

    assert "便利店门被推开" in first_timed_line
    assert "短信提示音" not in first_timed_line
    assert "门铃轻响" not in first_timed_line


def test_hide_black_clad_threat_keeps_action_instead_of_generic_fallback():
    script = ScriptBreakdown(
        title="test",
        genre="悬疑",
        logline="test",
        duration_sec=12,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=7,
                duration_sec=12,
                location="废弃地下通道",
                characters=["苏眠", "顾衡"],
                action_description="两人躲藏，黑衣人逼近，钟声响起。",
                camera_direction="中近景/低机位固定",
                emotion_tone="紧张",
            )
        ],
    )
    shot = ShotPrompt(scene_id=7, prompt="两人躲藏，黑衣人逼近，钟声响起。", duration_sec=12)

    rendered = build_timed_shot_prompt(shot, script)

    assert "躲藏" in rendered
    assert "黑衣人" in rendered
    assert "钟声" in rendered
    assert "当前信息产生克制反应" not in next(line for line in rendered.splitlines() if re.match(r"^\d", line))


def test_dark_passage_entrance_keeps_copper_bell_action():
    script = ScriptBreakdown(
        title="test",
        genre="悬疑",
        logline="test",
        duration_sec=12,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=13,
                duration_sec=12,
                location="旧钟楼地下室",
                characters=["苏眠", "顾衡"],
                action_description="灯灭，铜钟下沉，两人牵手进入暗道。",
                camera_direction="中远景/固定",
                emotion_tone="惊惧",
            )
        ],
    )
    shot = ShotPrompt(scene_id=13, prompt="灯灭，铜钟下沉，两人牵手进入暗道。", duration_sec=12)

    rendered = build_timed_shot_prompt(shot, script)

    assert "铜钟缓缓下沉" in rendered
    assert "暗道入口" in rendered
    assert "进入暗道" in rendered
    assert "铜钥匙打开铁盒" not in rendered


def test_lu_lin_phone_call_keeps_call_and_fire_seal_focus_without_visible_new_caller():
    script = ScriptBreakdown(
        title="test",
        genre="悬疑",
        logline="test",
        duration_sec=13,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=14,
                duration_sec=13,
                location="旧钟楼地下室",
                characters=["陆临"],
                dialogue=[
                    DialogueLine(character="陆临", line="她已经找到入口了。", emotion="冷漠汇报"),
                    DialogueLine(character="mystery_caller", line="那就让第七封信，永远留在钟楼下面。", emotion="苍老、冷酷"),
                ],
                action_description="陆临打电话，镜头聚焦水面火漆印章。",
                camera_direction="中近景/侧面固定",
                emotion_tone="阴冷",
            )
        ],
    )
    shot = ShotPrompt(scene_id=14, prompt="陆临打电话，镜头聚焦水面火漆印章。", duration_sec=13)

    rendered = build_timed_shot_prompt(shot, script)

    assert "手机拨号" in rendered
    assert "电话那头只用苍老声音回应" in rendered
    assert "水面上的暗红色火漆印章" in rendered
    assert "mystery_caller" not in rendered
    assert "短信提示音" not in rendered


def test_exit_and_chase_scene_does_not_trigger_entrance_template():
    script = ScriptBreakdown(
        title="test",
        genre="都市",
        logline="test",
        duration_sec=5,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=9,
                duration_sec=5,
                location="老城区巷口咖啡店门口",
                characters=["林默", "阿岚"],
                action_description="林默走出店门，阿岚追出来把一杯热拿铁塞进他手里。",
                camera_direction="中近景/侧面固定",
                emotion_tone="温和",
            )
        ],
    )
    shot = ShotPrompt(
        scene_id=9,
        prompt="林默走出店门，阿岚追出来把一杯热拿铁塞进他手里。",
        duration_sec=5,
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "门被推开" not in rendered
    assert "门铃" not in rendered
    assert "藏到身后" not in rendered


def test_chase_scene_with_real_door_cue_keeps_entrance_template():
    script = ScriptBreakdown(
        title="test",
        genre="都市",
        logline="test",
        duration_sec=5,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=9,
                duration_sec=5,
                location="老城区巷口咖啡店门口",
                characters=["林默", "阿岚"],
                dialogue=[
                    DialogueLine(character="林默", line="你怎么来了？", emotion="意外"),
                    DialogueLine(character="阿岚", line="给你送咖啡。", emotion="温和"),
                ],
                action_description="林默追上阿岚，推门走进咖啡店。",
                camera_direction="中近景/侧面固定",
                emotion_tone="温和",
            )
        ],
    )
    shot = ShotPrompt(
        scene_id=9,
        prompt="林默追上阿岚，推门走进咖啡店。",
        duration_sec=5,
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "门被推开" in rendered
    assert "林默走出" in rendered
    assert "藏到身后" not in rendered
    assert "门铃" not in rendered


def test_plain_exit_scene_does_not_trigger_entrance_template():
    script = ScriptBreakdown(
        title="test",
        genre="都市",
        logline="test",
        duration_sec=5,
        narration_style="none",
        scenes=[
            Scene(
                scene_id=1,
                duration_sec=5,
                location="咖啡店门口",
                characters=["林默"],
                action_description="林默走出店门，在门口雨棚下停住，回头看向店内。",
                camera_direction="中景",
                emotion_tone="克制",
            )
        ],
    )
    shot = ShotPrompt(
        scene_id=1,
        prompt="林默走出店门，在门口雨棚下停住，回头看向店内。",
        duration_sec=5,
    )

    rendered = build_timed_shot_prompt(shot, script)

    assert "门被推开" not in rendered
    assert "门铃" not in rendered
    assert "藏到身后" not in rendered
