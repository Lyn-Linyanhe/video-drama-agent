"""Metrics for the opt-in Kimi storyboard-review benchmark.

The evaluator is deliberately independent from the network runner. It accepts
already materialized :class:`PromptReviewResult` objects, deduplicates issues
that appear both in the report and a shot index, and scores only confirmed
issues. This keeps benchmark numbers stable when the provider emits repeated
or merely suspected candidates.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from src.schemas.prompt_review import PromptReviewIssue, PromptReviewResult
from src.services.prompt_review_facts import snapshot_search_text


# These are the acceptance targets from the review plan. The evaluator also
# accepts a richer {path, operator, value} shape so callers can use custom
# thresholds without coupling themselves to this constant's representation.
ACCEPTANCE_THRESHOLDS: dict[str, float | bool] = {
    "critical_recall": 0.95,
    "critical_precision": 0.90,
    "ordinary_recall": 0.90,
    "ordinary_precision": 0.85,
    "clean_critical_false_positive_rate": 0.05,
    "all_confirmed_issues_addressable": True,
}

_COHORTS = {"clean", "mutation"}
_SEVERITIES = {"critical", "major", "minor"}
_ORDINARY_SEVERITIES = {"major", "minor"}
_THRESHOLD_PATHS = {
    "critical_recall": "critical.recall",
    "critical_precision": "critical.precision",
    "ordinary_recall": "ordinary.recall",
    "ordinary_precision": "ordinary.precision",
    "clean_critical_false_positive_rate": "clean.critical_false_positive_rate",
    "all_confirmed_issues_addressable": "evidence.all_confirmed_issues_addressable",
}


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 6) if denominator else 0.0


def _record_report(record: Mapping[str, Any]) -> PromptReviewResult:
    raw = record.get("report", record.get("result"))
    if raw is None:
        raise ValueError("benchmark record requires report")
    if isinstance(raw, PromptReviewResult):
        return raw
    try:
        return PromptReviewResult.model_validate(raw)
    except Exception as exc:
        raise ValueError(f"benchmark report is invalid: {exc}") from exc


def _validate_record(record: Mapping[str, Any]) -> tuple[str, str, int, str, str, str, PromptReviewResult]:
    if not isinstance(record, Mapping):
        raise ValueError("benchmark record must be an object")
    case_id = str(record.get("case_id") or "").strip()
    if not case_id:
        raise ValueError("benchmark record requires case_id")
    cohort = str(record.get("cohort") or "").strip().lower()
    if cohort not in _COHORTS:
        raise ValueError(f"benchmark record {case_id} has unknown cohort: {cohort}")
    category = str(record.get("expected_category") or "").strip()
    severity = str(record.get("expected_severity") or "").strip().lower()
    code = str(record.get("expected_issue_code") or "").strip()
    try:
        target_scene_id = int(record.get("target_scene_id") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"benchmark record {case_id} has invalid target_scene_id") from exc
    if target_scene_id < 0:
        raise ValueError(f"benchmark record {case_id} has invalid target_scene_id")
    if cohort == "mutation" and (not category or not severity):
        raise ValueError(
            f"benchmark mutation {case_id} requires expected_category and expected_severity"
        )
    if severity and severity not in _SEVERITIES:
        raise ValueError(f"benchmark record {case_id} has unknown expected_severity: {severity}")
    return case_id, cohort, target_scene_id, category, severity, code, _record_report(record)


def _issue_identity(issue: PromptReviewIssue) -> str:
    if issue.candidate_id:
        return f"candidate:{issue.candidate_id}"
    return json.dumps(
        {
            "scene_id": issue.scene_id,
            "related_scene_ids": issue.related_scene_ids,
            "severity": issue.severity,
            "category": issue.category,
            "issue_code": issue.issue_code,
            "description": issue.description,
            "evidence": [item.model_dump(mode="json") for item in issue.evidence_items],
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _report_issues(report: PromptReviewResult) -> list[PromptReviewIssue]:
    """Flatten top-level and per-shot issues without counting an index twice."""

    values: list[Any] = list(report.issues)
    for shot in report.shots:
        if isinstance(shot, Mapping):
            values.extend(shot.get("issues") or [])
        else:
            values.extend(shot.issues)
    for indexed_issues in (report.shots_issue_index or {}).values():
        values.extend(indexed_issues or [])
    unique: list[PromptReviewIssue] = []
    seen: set[str] = set()
    for value in values:
        try:
            issue = value if isinstance(value, PromptReviewIssue) else PromptReviewIssue.model_validate(value)
        except Exception as exc:
            raise ValueError(f"benchmark report contains invalid issue: {exc}") from exc
        identity = _issue_identity(issue)
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(issue)
    return unique


def _confirmed_issues(report: PromptReviewResult) -> list[PromptReviewIssue]:
    return [issue for issue in _report_issues(report) if issue.confidence == "confirmed"]


def _matches_expected(issue: PromptReviewIssue, category: str, severity: str, code: str, target_scene_id: int = 0) -> bool:
    if target_scene_id and issue.scene_id != target_scene_id and target_scene_id not in issue.related_scene_ids:
        return False
    if code and issue.issue_code != code:
        return False
    if category and issue.category != category:
        return False
    if severity and issue.severity != severity:
        return False
    return bool(code or category or severity)


def _issue_evidence_addressable(issue: PromptReviewIssue, report: PromptReviewResult) -> bool:
    """Check evidence presence and, when available, membership in the snapshot."""

    if not issue.evidence_items:
        return False
    if any(not item.excerpt.strip() for item in issue.evidence_items):
        return False
    snapshot = report.audit_snapshot
    if snapshot is None:
        # Unit-level records may intentionally omit a full snapshot. They can
        # still establish the minimum evidence contract.
        return True
    searchable = snapshot_search_text(snapshot)
    if any(item.excerpt not in searchable for item in issue.evidence_items):
        return False
    try:
        # Reuse the production validator for line ranges, fact IDs, scene
        # attribution, and prompt/source membership. Import lazily to avoid a
        # module-import cycle through the reviewer.
        from src.agents.prompt_review_agent import PromptReviewAgent

        target_scene_ids = {issue.scene_id, *issue.related_scene_ids} - {0}
        PromptReviewAgent._validate_issue_evidence(
            issue,
            snapshot,
            issue.scope,
            target_scene_ids or None,
            require_semantic_pair=False,
        )
    except Exception:
        return False
    return True


def _metric(expected: int, detected: int, predicted: int, true_positive: int) -> dict[str, float | int]:
    return {
        "expected": expected,
        "detected": detected,
        "predicted": predicted,
        "true_positive": true_positive,
        "recall": _ratio(detected, expected),
        "precision": _ratio(true_positive, predicted),
    }


def evaluate_benchmark_records(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Return category metrics and evidence coverage for benchmark records.

    A mutation is a positive example only when a confirmed issue matches its
    expected category/severity and, when supplied, its stable issue code.
    Precision counts every confirmed predicted issue after report/shot-index
    deduplication; recall counts mutation cases, avoiding overweighting one
    verbose report.
    """

    normalized = [_validate_record(record) for record in records]
    case_ids = [item[0] for item in normalized]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("benchmark case_id values must be unique")

    critical_expected = critical_detected = critical_predicted = critical_tp = 0
    ordinary_expected = ordinary_detected = ordinary_predicted = ordinary_tp = 0
    clean_count = clean_critical_fp = 0
    confirmed_count = addressable_count = 0
    issue_code_counts: Counter[str] = Counter()
    category_counts: dict[str, dict[str, int]] = {}
    case_summaries: list[dict[str, Any]] = []

    for case_id, cohort, target_scene_id, category, severity, expected_code, report in normalized:
        issues = _confirmed_issues(report)
        critical_issues = [issue for issue in issues if issue.severity == "critical"]
        ordinary_issues = [issue for issue in issues if issue.severity in _ORDINARY_SEVERITIES]
        matching = [issue for issue in issues if _matches_expected(issue, category, severity, expected_code, target_scene_id)]
        if cohort == "mutation":
            if severity == "critical":
                critical_expected += 1
                if matching:
                    critical_detected += 1
            elif severity in _ORDINARY_SEVERITIES:
                ordinary_expected += 1
                if matching:
                    ordinary_detected += 1
        if cohort == "clean":
            clean_count += 1
            if critical_issues:
                clean_critical_fp += 1

        critical_predicted += len(critical_issues)
        ordinary_predicted += len(ordinary_issues)
        if severity == "critical" and cohort == "mutation":
            critical_tp += int(any(
                _matches_expected(issue, category, severity, expected_code, target_scene_id)
                for issue in critical_issues
            ))
        if severity in _ORDINARY_SEVERITIES and cohort == "mutation":
            ordinary_tp += int(any(
                _matches_expected(issue, category, severity, expected_code, target_scene_id)
                for issue in ordinary_issues
            ))

        for issue in issues:
            issue_code_counts[issue.issue_code] += 1
            confirmed_count += 1
            if _issue_evidence_addressable(issue, report):
                addressable_count += 1
        if category:
            bucket = category_counts.setdefault(category, {"expected": 0, "detected": 0})
            bucket["expected"] += 1
            if matching:
                bucket["detected"] += 1
        case_summaries.append({
            "case_id": case_id,
            "cohort": cohort,
            "expected_category": category,
            "expected_severity": severity,
            "confirmed_issue_count": len(issues),
            "issue_codes": sorted({issue.issue_code for issue in issues}),
            "matching_issue": bool(matching),
            "evidence_addressable": all(_issue_evidence_addressable(issue, report) for issue in issues),
        })

    category_metrics = {
        category: {
            **values,
            "recall": _ratio(values["detected"], values["expected"]),
        }
        for category, values in sorted(category_counts.items())
    }
    return {
        "critical": _metric(critical_expected, critical_detected, critical_predicted, critical_tp),
        "ordinary": _metric(ordinary_expected, ordinary_detected, ordinary_predicted, ordinary_tp),
        "clean": {
            "cases": clean_count,
            "critical_false_positives": clean_critical_fp,
            "critical_false_positive_rate": _ratio(clean_critical_fp, clean_count),
        },
        "evidence": {
            "confirmed_issues": confirmed_count,
            "addressable_confirmed_issues": addressable_count,
            "all_confirmed_issues_addressable": addressable_count == confirmed_count,
        },
        "categories": category_metrics,
        "issue_codes": dict(sorted(issue_code_counts.items())),
        "cases": case_summaries,
        "record_count": len(normalized),
    }


def _nested_value(summary: Mapping[str, Any], path: str) -> Any:
    value: Any = summary
    for part in path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return None
        value = value[part]
    return value


def _threshold_definition(name: str, specification: Any) -> tuple[str, str, Any]:
    if isinstance(specification, Mapping):
        path = str(specification.get("path") or _THRESHOLD_PATHS.get(name, name))
        operator = str(specification.get("operator") or specification.get("op") or ">=")
        target = specification.get("value", specification.get("threshold"))
    elif isinstance(specification, (tuple, list)) and len(specification) == 2:
        path, target = _THRESHOLD_PATHS.get(name, name), specification[0]
        operator = str(specification[1])
    else:
        path = _THRESHOLD_PATHS.get(name, name)
        target = specification
        if isinstance(target, bool):
            operator = "=="
        elif "false_positive_rate" in name:
            operator = "<="
        else:
            operator = ">="
    return path, operator, target


def _passes(actual: Any, operator: str, target: Any) -> bool:
    if actual is None:
        return False
    if operator == "==":
        return actual == target
    if operator == ">=":
        return actual >= target
    if operator == "<=":
        return actual <= target
    if operator == ">":
        return actual > target
    if operator == "<":
        return actual < target
    raise ValueError(f"unknown benchmark threshold operator: {operator}")


def evaluate_thresholds(summary: Mapping[str, Any], thresholds: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate thresholds and return named failures for CI or CLI callers."""

    checks: dict[str, dict[str, Any]] = {}
    failed: list[str] = []
    for name, specification in thresholds.items():
        path, operator, target = _threshold_definition(name, specification)
        actual = _nested_value(summary, path)
        passed = _passes(actual, operator, target)
        checks[name] = {
            "path": path,
            "actual": actual,
            "operator": operator,
            "target": target,
            "passed": passed,
        }
        if not passed:
            failed.append(name)
    return {"passed": not failed, "failed": failed, "checks": checks}


__all__ = [
    "ACCEPTANCE_THRESHOLDS",
    "evaluate_benchmark_records",
    "evaluate_thresholds",
]
