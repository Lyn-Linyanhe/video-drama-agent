from tests.fixtures.prompt_review_gold import CONTROLLED_VARIANTS, GOLD_CORPUS


def test_gold_corpus_has_independent_sources_and_sixty_shots():
    assert len(GOLD_CORPUS) == 6
    assert len({case.source_id for case in GOLD_CORPUS}) == 6
    assert sum(len(case.script.scenes) for case in GOLD_CORPUS) == 60
    assert all(len(case.script.scenes) == len(case.prompts.shots) == 10 for case in GOLD_CORPUS)
    # A source id alone is not independence: the six cases must not share one
    # parameterized action template.
    assert len({case.story_fingerprint for case in GOLD_CORPUS}) == 6


def test_controlled_variants_cover_the_required_error_families():
    assert len(CONTROLLED_VARIANTS) == 36
    assert len({item["variant_id"] for item in CONTROLLED_VARIANTS}) == 36
    assert {item["kind"] for item in CONTROLLED_VARIANTS} >= {
        "wrong_attribution", "omitted_dialogue", "action_reversal",
        "future_leakage", "out_of_script", "overloaded_shot",
    }
    assert len({item["source_id"] for item in CONTROLLED_VARIANTS}) == 6
    baseline_by_source = {case.source_id: case for case in GOLD_CORPUS}
    for item in CONTROLLED_VARIANTS:
        baseline = baseline_by_source[item["source_id"]]
        baseline_prompt = next(shot.prompt for shot in baseline.prompts.shots if shot.scene_id == item["target_scene_id"])
        changed_prompt = next(shot.prompt for shot in item["prompts"].shots if shot.scene_id == item["target_scene_id"])
        assert changed_prompt != baseline_prompt
        assert item["expected_category"]
        assert item["expected_severity"] in {"critical", "major", "minor"}
        assert item["expected_issue_code"]


def test_action_reversal_variants_swap_the_event_order_not_only_time_labels():
    baseline_by_source = {case.source_id: case for case in GOLD_CORPUS}
    variants = [item for item in CONTROLLED_VARIANTS if item["kind"] == "action_reversal"]
    assert len(variants) == 6
    for item in variants:
        baseline = baseline_by_source[item["source_id"]]
        base_prompt = next(shot.prompt for shot in baseline.prompts.shots if shot.scene_id == item["target_scene_id"])
        changed_prompt = next(shot.prompt for shot in item["prompts"].shots if shot.scene_id == item["target_scene_id"])
        assert "0-2s" in base_prompt and "2-5s" in base_prompt
        assert changed_prompt.index("完成当前动作") < changed_prompt.index("观察")
