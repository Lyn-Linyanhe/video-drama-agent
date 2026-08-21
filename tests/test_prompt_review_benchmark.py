from src.schemas.prompt_review import PromptReviewIssue, PromptReviewResult
from src.services.prompt_review_benchmark import evaluate_benchmark_records


def _report(*issues: PromptReviewIssue) -> PromptReviewResult:
    return PromptReviewResult(
        report_version=2,
        audit_signature="benchmark",
        verdict="needs_review" if issues else "pass",
        overall_score=90,
        passed=not issues,
        summary="benchmark",
        shots=[],
        issues=list(issues),
    )


def _issue(category: str, severity: str, code: str, evidence: bool = True) -> PromptReviewIssue:
    return PromptReviewIssue(
        scene_id=1,
        severity=severity,
        category=category,
        description="测试问题。",
        suggestion="测试修复。",
        issue_code=code,
        confidence="confirmed",
        evidence_items=[{
            "source": "prompt",
            "scene_id": 1,
            "excerpt": "测试片段",
        }] if evidence else [],
    )


def test_benchmark_summary_reports_category_metrics_and_evidence_coverage():
    from src.services.prompt_review_benchmark import evaluate_benchmark_records

    summary = evaluate_benchmark_records([
        {
            "case_id": "clean-1",
            "cohort": "clean",
            "expected_category": "",
            "expected_severity": "",
            "report": _report(),
        },
        {
            "case_id": "critical-hit",
            "cohort": "mutation",
            "expected_category": "fidelity",
            "expected_severity": "critical",
            "report": _report(_issue("fidelity", "critical", "prompt.out_of_script")),
        },
        {
            "case_id": "ordinary-hit",
            "cohort": "mutation",
            "expected_category": "coverage",
            "expected_severity": "major",
            "report": _report(_issue("coverage", "major", "prompt.coverage_missing_dialogue")),
        },
    ])

    assert summary["critical"]["recall"] == 1.0
    assert summary["ordinary"]["precision"] == 1.0
    assert summary["clean"]["critical_false_positive_rate"] == 0.0
    assert summary["evidence"]["all_confirmed_issues_addressable"] is True


def test_benchmark_thresholds_flag_missed_critical_and_unaddressable_evidence():
    from src.services.prompt_review_benchmark import (
        ACCEPTANCE_THRESHOLDS,
        evaluate_benchmark_records,
        evaluate_thresholds,
    )

    summary = evaluate_benchmark_records([
        {
            "case_id": "clean-critical-false-positive",
            "cohort": "clean",
            "expected_category": "",
            "expected_severity": "",
            "report": _report(_issue("fidelity", "critical", "prompt.false_alarm", evidence=False)),
        },
        {
            "case_id": "critical-miss",
            "cohort": "mutation",
            "expected_category": "fidelity",
            "expected_severity": "critical",
            "report": _report(_issue("fidelity", "major", "prompt.out_of_script")),
        },
    ])

    checks = evaluate_thresholds(summary, ACCEPTANCE_THRESHOLDS)

    assert checks["passed"] is False
    assert "critical_recall" in checks["failed"]
    assert "clean_critical_false_positive_rate" in checks["failed"]
    assert "all_confirmed_issues_addressable" in checks["failed"]


def test_benchmark_deduplicates_issue_repeated_in_report_and_shot_index():
    from src.services.prompt_review_benchmark import evaluate_benchmark_records

    issue = _issue("coverage", "major", "prompt.coverage_missing_dialogue")
    report = _report(issue)
    report.shots = [{
        "scene_id": 1,
        "score": 70,
        "passed": False,
        "summary": "",
        "issues": [issue.model_copy(deep=True)],
    }]

    summary = evaluate_benchmark_records([{
        "case_id": "ordinary-duplicate",
        "cohort": "mutation",
        "expected_category": "coverage",
        "expected_severity": "major",
        "expected_issue_code": "prompt.coverage_missing_dialogue",
        "report": report,
    }])

    assert summary["ordinary"]["recall"] == 1.0
    assert summary["ordinary"]["precision"] == 1.0
    assert summary["ordinary"]["predicted"] == 1


def test_benchmark_rejects_unknown_cohort_and_missing_mutation_label():
    from src.services.prompt_review_benchmark import evaluate_benchmark_records

    base = {
        "case_id": "invalid",
        "expected_category": "",
        "expected_severity": "",
        "report": _report(),
    }
    try:
        evaluate_benchmark_records([{**base, "cohort": "other"}])
    except ValueError as exc:
        assert "cohort" in str(exc)
    else:
        raise AssertionError("unknown cohort must be rejected")

    try:
        evaluate_benchmark_records([{**base, "cohort": "mutation"}])
    except ValueError as exc:
        assert "expected" in str(exc)
    else:
        raise AssertionError("mutation labels must be required")


def test_online_runner_selection_is_opt_in_and_does_not_call_kimi():
    from scripts.run_prompt_review_benchmark import select_cases

    cases, variants = select_cases(["cafe_rain"], cohort="clean")

    assert [case.source_id for case in cases] == ["cafe_rain"]
    assert variants == []

    cases, variants = select_cases(["cafe_rain"], cohort="mutation", max_cases=1)

    assert cases == []
    assert len(variants) == 6
    assert all(item["source_text"] for item in variants)


def test_benchmark_reads_issue_index_without_double_counting():
    from src.services.prompt_review_benchmark import evaluate_benchmark_records

    issue = _issue("fidelity", "critical", "prompt.out_of_script")
    report = _report()
    report.shots_issue_index = {"1": [issue]}

    summary = evaluate_benchmark_records([{
        "case_id": "indexed-critical",
        "cohort": "mutation",
        "expected_category": "fidelity",
        "expected_severity": "critical",
        "expected_issue_code": "prompt.out_of_script",
        "report": report,
    }])

    assert summary["critical"]["predicted"] == 1
    assert summary["evidence"]["confirmed_issues"] == 1


def test_benchmark_counts_only_one_true_positive_for_duplicate_matches_in_one_mutation():
    first = _issue("coverage", "major", "prompt.duplicate_a")
    second = first.model_copy(update={
        "candidate_id": "candidate-2",
        "description": "同一变异的重复候选。",
    })
    report = _report(first, second)

    summary = evaluate_benchmark_records([{
        "case_id": "duplicate-mutation",
        "cohort": "mutation",
        "expected_category": "coverage",
        "expected_severity": "major",
        "report": report,
    }])

    assert summary["ordinary"]["predicted"] == 2
    assert summary["ordinary"]["true_positive"] == 1
    assert summary["ordinary"]["precision"] == 0.5


def test_benchmark_requires_expected_mutation_scene_when_declared():
    issue = _issue("fidelity", "critical", "prompt.out_of_script")
    issue.scene_id = 1
    report = _report(issue)

    summary = evaluate_benchmark_records([{
        "case_id": "wrong-scene-mutation",
        "cohort": "mutation",
        "target_scene_id": 2,
        "expected_category": "fidelity",
        "expected_severity": "critical",
        "expected_issue_code": "prompt.out_of_script",
        "report": report,
    }])

    assert summary["critical"]["detected"] == 0
    assert summary["critical"]["true_positive"] == 0


def test_online_runner_keeps_confirmed_evidence_for_reproduction():
    from scripts.run_prompt_review_benchmark import _json_report

    issue = _issue("fidelity", "critical", "prompt.out_of_script")
    report = _report(issue)
    payload = _json_report([{
        "case_id": "evidence-case",
        "cohort": "mutation",
        "expected_category": "fidelity",
        "expected_severity": "critical",
        "expected_issue_code": "prompt.out_of_script",
        "report": report,
    }])

    record = payload["records"][0]
    assert record["confirmed_issues"][0]["evidence_items"][0]["excerpt"] == "测试片段"
    assert record["issues"][0]["issue_code"] == "prompt.out_of_script"
    assert record["verdict"] == "needs_review"
    assert record["stage_records"] == []
