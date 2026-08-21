"""Offline storyboard-review corpus.

The six cases are deliberately independent short scenes rather than variants
of one plot. Each case supplies ten authored shots, structured facts, and
matching prompts. ``CONTROLLED_VARIANTS`` mutates one contract dimension at a
time for deterministic rule and semantic-review harnesses.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.schemas.prompt import GenerationPrompts, ShotPrompt
from src.schemas.script import DialogueLine, Scene, ScriptBreakdown


@dataclass(frozen=True)
class GoldScriptCase:
    source_id: str
    source_text: str
    script: ScriptBreakdown
    prompts: GenerationPrompts
    story_fingerprint: str


_SOURCES = [
    {
        "source_id": "cafe_rain", "title": "巷口咖啡", "lead": "苏眠", "location": "雨夜咖啡店", "prop": "旧钥匙", "genre": "悬疑", "second": "店员",
        "fingerprint": "discover-key-investigate-handoff-pursuit-revelation",
        "actions": [
            "苏眠推开雨夜咖啡店的门，在地垫旁发现旧钥匙。", "她用纸巾擦去钥匙上的雨水，发现刻着陌生门牌。",
            "苏眠把钥匙递给店员，问他是否见过这个门牌。", "店员看到门牌号后沉默，把收银台下的便签推过去。",
            "苏眠读着便签追问，店员避开她的目光。", "她走到后巷铁门前，把钥匙插进锁孔。",
            "锁芯转动，店员追出来说门后有人在等她。", "苏眠停在门边，确认便签上的时间。",
            "她收好便签推开铁门，回头示意店员别跟来。", "苏眠走入黑巷，钥匙仍攥在掌心。",
        ],
    },
    {
        "source_id": "station_letter", "title": "末班车的信", "lead": "周野", "location": "南站月台", "prop": "蓝色信封", "genre": "悬疑", "second": "广播员",
        "fingerprint": "receive-letter-follow-clue-board-train-choice",
        "actions": [
            "周野在南站月台听见末班车进站，脚边飘来蓝色信封。", "他拆开信封，读到一张没有署名的座位号。",
            "周野向广播员询问座位号，广播员指向即将开门的列车。", "列车门开启，广播员把一张旧车票塞进周野手里。",
            "周野拿着车票跑向车厢，错过了最后一次广播。", "他在指定座位下找到一只录音笔。",
            "录音笔播放母亲的留言，周野扶住座椅才站稳。", "列车启动，窗外的月台灯光向后滑去。",
            "周野拨通留言里的号码，等待对方接听。", "电话接通时，他把蓝色信封放在膝上。",
        ],
    },
    {
        "source_id": "rooftop_seed", "title": "楼顶的种子", "lead": "林夏", "location": "旧居民楼天台", "prop": "铁盒", "genre": "成长", "second": "弟弟",
        "fingerprint": "retrieve-keepsake-reconcile-plant-new-beginning",
        "actions": [
            "林夏爬上旧居民楼天台，在水箱后找到生锈铁盒。", "她打开铁盒，翻出父亲留下的种子包。",
            "弟弟赶到天台，质问林夏为什么瞒着他。", "林夏把种子包递给弟弟，请他一起读背面的字。",
            "两人发现父亲写下的种植日期，沉默地望向空花盆。", "林夏提来雨水桶，把泥土倒进花盆。",
            "弟弟把种子放进掌心，慢慢埋进湿土。", "林夏扶正花盆，风吹动晾衣绳。",
            "姐弟并肩给新土浇水，紧张终于松开。", "天台晨光亮起，铁盒放在花盆旁。",
        ],
    },
    {
        "source_id": "harbor_watch", "title": "港口十二点", "lead": "陈默", "location": "雾港值班室", "prop": "银色怀表", "genre": "犯罪", "second": "船长",
        "fingerprint": "monitor-timepiece-identify-ship-intercept-confession",
        "actions": [
            "陈默在雾港值班室校准时钟，发现桌上的银色怀表停在十二点。", "他打开港口监控，看到怀表主人登上一艘货船。",
            "陈默向船长发出拦截请求，船长要求核对船号。", "他把怀表贴近摄像头，露出背面的编号。",
            "船长认出编号后关闭无线电，陈默立刻冲出值班室。", "陈默沿码头追到货船跳板前，拦住准备登船的人。",
            "那人承认怀表来自失踪的货运员，递出一张清单。", "陈默在雾中展开清单，对照货柜编号。",
            "警笛靠近码头，船长重新接通无线电。", "陈默合上怀表，盯着离港的货船。",
        ],
    },
    {
        "source_id": "clinic_light", "title": "诊室的灯", "lead": "许宁", "location": "社区诊所", "prop": "褪色照片", "genre": "现实", "second": "护士",
        "fingerprint": "find-photo-question-records-visit-reconcile-memory",
        "actions": [
            "许宁在社区诊所等候时，从病历夹里滑出一张褪色照片。", "她认出照片里是年轻的母亲和陌生医生。",
            "许宁请护士查找旧病历，护士带她走向档案柜。", "护士抽出泛黄档案，指着照片背面的日期。",
            "许宁发现日期正是自己出生那天，手指停在纸页上。", "她坐进空诊室，给多年未联系的母亲发消息。",
            "母亲赶到诊所，承认照片记录了一次艰难决定。", "许宁把照片放回桌面，听完母亲的解释。",
            "护士关掉刺眼的顶灯，只留一盏台灯。", "许宁和母亲一起离开诊所，照片夹在病历里。",
        ],
    },
    {
        "source_id": "bicycle_summer", "title": "夏天的自行车", "lead": "顾言", "location": "河堤自行车道", "prop": "红色车铃", "genre": "温情", "second": "邻居",
        "fingerprint": "repair-bicycle-return-memory-ride-forward",
        "actions": [
            "顾言在河堤自行车道捡到一只红色车铃，远处传来链条声。", "他把车铃装到旧自行车上，试着按响第一声。",
            "邻居推着坏车赶来，认出那是童年丢失的车铃。", "顾言帮邻居扶稳车把，检查松动的链条。",
            "两人一起修好后轮，车铃在夏风里再次响起。", "邻居说起搬家前没能告别的那次骑行。",
            "顾言把车铃递回去，邀请邻居沿河堤再骑一次。", "两辆自行车并排穿过树影，速度渐渐一致。",
            "邻居在桥边停下，把旧车铃赠给顾言留念。", "顾言骑向落日，红色车铃挂在车把上。",
        ],
    },
]


def _build_case(spec) -> GoldScriptCase:
    source_id = spec["source_id"]
    title = spec["title"]
    lead = spec["lead"]
    location = spec["location"]
    prop = spec["prop"]
    genre = spec["genre"]
    second_character = spec["second"]
    scenes: list[Scene] = []
    prompts: list[ShotPrompt] = []
    paragraphs: list[str] = []
    actions = spec["actions"]
    for scene_id, action in enumerate(actions, start=1):
        dialogue = []
        if scene_id in {3, 7}:
            line = "你终于来了。" if scene_id == 3 else "我一直在等这一天。"
            dialogue = [DialogueLine(character=second_character, line=line, emotion="克制")]
        scenes.append(Scene(
            scene_id=scene_id, duration_sec=5, location=location,
            characters=[lead, second_character], dialogue=dialogue,
            action_description=action, camera_direction="中景缓慢推进",
            emotion_tone="紧张" if scene_id < 8 else "释然",
            continuity_from_prev=scene_id > 1,
            continuity_reason="同一段动作连续推进" if scene_id > 1 else "",
        ))
        dialogue_text = "；".join(item.line for item in dialogue)
        paragraphs.append(f"第{scene_id}镜，{action}{(' 对白：' + dialogue_text) if dialogue_text else ''}")
        prompt = f"【核心分镜意图】{action}\n【环境】{location}，画面保持统一。"
        if dialogue_text:
            prompt += f"\n【对白】{second_character}说：{dialogue_text}"
        prompt += f"\n【单镜头时间轴】0-2s {lead}观察{prop}；2-5s {lead}完成当前动作。"
        prompts.append(ShotPrompt(scene_id=scene_id, duration_sec=5, prompt=prompt))
    script = ScriptBreakdown(
        title=title, genre=genre, logline=f"{lead}在{location}追查{prop}背后的秘密。",
        duration_sec=50, narration_style="无旁白", scenes=scenes,
    )
    return GoldScriptCase(
        source_id=source_id, source_text="\n\n".join(paragraphs), script=script,
        prompts=GenerationPrompts(api_provider="mock", shots=prompts),
        story_fingerprint=spec["fingerprint"],
    )


GOLD_CORPUS = tuple(_build_case(spec) for spec in _SOURCES)


def controlled_variants() -> tuple[dict, ...]:
    variants = []
    mutation_kinds = (
        ("wrong_attribution", "fidelity", "critical", "prompt.fidelity_wrong_scene"),
        ("omitted_dialogue", "coverage", "major", "prompt.coverage_missing_dialogue"),
        ("action_reversal", "fidelity", "major", "prompt.order_reversed"),
        ("future_leakage", "fidelity", "major", "prompt.future_leakage"),
        ("out_of_script", "fidelity", "critical", "prompt.out_of_script"),
        ("overloaded_shot", "executability", "major", "prompt.timeline_overload"),
    )
    for case in GOLD_CORPUS:
        for kind, expected_category, expected_severity, expected_code in mutation_kinds:
            prompts = case.prompts.model_copy(deep=True)
            target = prompts.shots[1 if kind == "wrong_attribution" else 2]
            if kind == "wrong_attribution":
                target.prompt = prompts.shots[0].prompt
            elif kind == "omitted_dialogue":
                target.prompt = target.prompt.split("\n【对白】", 1)[0]
            elif kind == "action_reversal":
                target.prompt = target.prompt.rsplit("【单镜头时间轴】", 1)[0] + "【单镜头时间轴】0-2s 完成当前动作；2-5s 观察目标。"
            elif kind == "future_leakage":
                target.prompt += "\n未来镜头预先出现：" + case.script.scenes[-1].action_description
            elif kind == "out_of_script":
                target.prompt += "\n角色突然打开一只剧本中没有的保险箱。"
            elif kind == "overloaded_shot":
                target.prompt = "【单镜头时间轴】0-6s 连续追逐；6-12s 完成复杂交接。"
            variants.append({
                "source_id": case.source_id,
                "variant_id": f"{case.source_id}:{kind}",
                "kind": kind,
                "target_scene_id": target.scene_id,
                "expected_category": expected_category,
                "expected_severity": expected_severity,
                "expected_issue_code": expected_code,
                "source_text": case.source_text,
                "script": case.script,
                "prompts": prompts,
            })
    return tuple(variants)


CONTROLLED_VARIANTS = controlled_variants()
