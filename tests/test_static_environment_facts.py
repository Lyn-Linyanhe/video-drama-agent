from src.utils.visual_asset_manifest import extract_static_environment_facts


def test_extract_static_environment_facts_keeps_static_clauses_only():
    source = "破旧公寓起居室；走到窗前；窗台上一只灰猫窜过；红地毯上溅出泪痕；墙面斑驳；窗帘半掩"
    assert extract_static_environment_facts(source) == [
        "破旧公寓起居室", "墙面斑驳", "窗帘半掩"
    ]


def test_extract_static_environment_facts_keeps_continuous_weather():
    assert extract_static_environment_facts("雨棚边缘雨水成线滴落；她望向巷口；铁门生锈") == [
        "雨棚边缘雨水成线滴落", "铁门生锈"
    ]
