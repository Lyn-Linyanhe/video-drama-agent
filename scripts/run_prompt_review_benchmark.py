"""Run the opt-in online Kimi storyboard-review benchmark.

This command is intentionally separate from the default test suite. It calls
the configured Kimi endpoint and therefore requires an explicit invocation,
for example::

    python scripts/run_prompt_review_benchmark.py --output benchmark.json \
        --enforce-thresholds
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Iterable

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.agents.prompt_review_agent import PromptReviewAgent
from src.schemas.prompt import PromptPolicy
from src.services.prompt_review_benchmark import (
    ACCEPTANCE_THRESHOLDS,
    _confirmed_issues,
    evaluate_benchmark_records,
    evaluate_thresholds,
)
from tests.fixtures.prompt_review_gold import CONTROLLED_VARIANTS, GOLD_CORPUS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the opt-in online Kimi storyboard-review benchmark")
    parser.add_argument(
        "--source-id",
        action="append",
        dest="source_ids",
        help="Only run this corpus source; repeat the option to select several",
    )
    parser.add_argument(
        "--cohort",
        choices=("all", "clean", "mutation"),
        default="all",
        help="Run clean cases, controlled mutations, or both (default: all)",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=0,
        help="Limit the number of source stories after filtering; zero means all",
    )
    parser.add_argument("--output", type=Path, help="Write the JSON report to this path")
    parser.add_argument(
        "--enforce-thresholds",
        action="store_true",
        help="Exit with status 1 when an acceptance threshold fails",
    )
    return parser


def _selected_source_ids(source_ids: Iterable[str] | None, max_cases: int) -> list[str]:
    available = [case.source_id for case in GOLD_CORPUS]
    requested = {str(item).strip() for item in (source_ids or []) if str(item).strip()}
    selected = [source_id for source_id in available if not requested or source_id in requested]
    if max_cases < 0:
        raise ValueError("--max-cases must be zero or a positive integer")
    return selected[:max_cases] if max_cases else selected


def select_cases(
    source_ids: Iterable[str] | None = None,
    cohort: str = "all",
    max_cases: int = 0,
) -> tuple[list[Any], list[dict[str, Any]]]:
    """Select clean stories and controlled variants without calling Kimi."""

    if cohort not in {"all", "clean", "mutation"}:
        raise ValueError(f"unknown cohort: {cohort}")
    selected_ids = set(_selected_source_ids(source_ids, max_cases))
    cases = [case for case in GOLD_CORPUS if case.source_id in selected_ids and cohort in {"all", "clean"}]
    variants = [
        item for item in CONTROLLED_VARIANTS
        if item["source_id"] in selected_ids and cohort in {"all", "mutation"}
    ]
    return cases, variants


async def _run_selected(cases: list[Any], variants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    reviewer = PromptReviewAgent()
    if not reviewer.enabled():
        raise RuntimeError("Kimi prompt review is disabled; set KIMI_PROMPT_REVIEW_ENABLED=true for this command")
    if not reviewer.is_configured():
        raise RuntimeError(reviewer.configuration_error())
    policy = PromptPolicy()
    records: list[dict[str, Any]] = []
    for case in cases:
        report = await reviewer.run(
            case.script,
            case.prompts,
            None,
            policy,
            source_text=case.source_text,
        )
        records.append({
            "case_id": f"clean:{case.source_id}",
            "cohort": "clean",
            "expected_category": "",
            "expected_severity": "",
            "expected_issue_code": "",
            "report": report,
        })
    for variant in variants:
        report = await reviewer.run(
            variant["script"],
            variant["prompts"],
            None,
            policy,
            source_text=variant["source_text"],
        )
        records.append({
            "case_id": f"mutation:{variant['variant_id']}",
            "cohort": "mutation",
            "expected_category": variant["expected_category"],
            "expected_severity": variant["expected_severity"],
            "expected_issue_code": variant["expected_issue_code"],
            "target_scene_id": variant["target_scene_id"],
            "report": report,
        })
    return records


def _json_report(records: list[dict[str, Any]]) -> dict[str, Any]:
    summary = evaluate_benchmark_records(records)
    thresholds = evaluate_thresholds(summary, ACCEPTANCE_THRESHOLDS)
    return {
        "benchmark_version": 1,
        "provider": "kimi",
        "summary": summary,
        "thresholds": thresholds,
        "records": [
            {
                "case_id": record["case_id"],
                "cohort": record["cohort"],
                "expected_category": record["expected_category"],
                "expected_severity": record["expected_severity"],
                "expected_issue_code": record.get("expected_issue_code", ""),
                "target_scene_id": record.get("target_scene_id", 0),
                "report_version": record["report"].report_version,
                "audit_signature": record["report"].audit_signature,
                "verdict": record["report"].verdict,
                "overall_score": record["report"].overall_score,
                "issues": [
                    issue.model_dump(mode="json")
                    for issue in record["report"].issues
                ],
                "confirmed_issues": [
                    issue.model_dump(mode="json")
                    for issue in _confirmed_issues(record["report"])
                ],
                "stage_records": [
                    item.model_dump(mode="json")
                    for item in record["report"].stage_records
                ],
                "audit_snapshot": (
                    record["report"].audit_snapshot.model_dump(mode="json")
                    if record["report"].audit_snapshot is not None
                    else None
                ),
            }
            for record in records
        ],
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cases, variants = select_cases(args.source_ids, args.cohort, args.max_cases)
        records = asyncio.run(_run_selected(cases, variants))
        payload = _json_report(records)
    except (RuntimeError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2

    rendered = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if args.enforce_thresholds and not payload["thresholds"]["passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
