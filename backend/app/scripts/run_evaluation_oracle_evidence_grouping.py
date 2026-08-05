from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.services.evaluation_oracle_evidence_grouping_service import (
    EvaluationOracleEvidenceGroupingError,
    EvaluationOracleEvidenceGroupingService,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Screen one dev-only Oracle evidence-grouping coordinate while keeping "
            "the model, prompt, budgets, questions, source set, and source text fixed."
        )
    )
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--source-screening-artifact", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--confirm-local-only", action="store_true")
    args = parser.parse_args()

    settings = get_settings()
    if settings.app_env == "production" or not args.confirm_local_only:
        raise SystemExit("oracle_grouping_local_confirmation_required")

    try:
        source_bytes = args.source_screening_artifact.read_bytes()
        source = json.loads(source_bytes)
        if not isinstance(source, dict):
            raise ValueError
        source_hash = hashlib.sha256(source_bytes).hexdigest()
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError):
        print(
            json.dumps(
                {"status": "blocked", "reason_code": "oracle_grouping_input_unreadable"},
                sort_keys=True,
            )
        )
        return 2

    try:
        with SessionLocal() as db:
            summary = EvaluationOracleEvidenceGroupingService(settings).run(
                db,
                evaluation_run_id=args.run_id,
                source_screening=source,
                source_screening_artifact_sha256=source_hash,
            )
    except EvaluationOracleEvidenceGroupingError as exc:
        print(json.dumps({"status": "blocked", "reason_code": str(exc)}, sort_keys=True))
        return 2

    rendered = json.dumps(summary.safe_dict(), ensure_ascii=False, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "status": (
                    "selected_for_three_repeat_confirmation"
                    if summary.selected_for_three_repeat_confirmation
                    else "not_selected"
                ),
                "artifact_sha256": hashlib.sha256(rendered.encode()).hexdigest(),
                "auxiliary_pass_delta": summary.auxiliary_pass_delta,
                "required_facts_supported_pass_delta": (
                    summary.required_facts_supported_pass_delta
                ),
                "citation_support_pass_delta": summary.citation_support_pass_delta,
                "mean_context_utilization_delta": (summary.mean_context_utilization_delta),
                "generation_gap_delta": summary.generation_gap_delta,
                "pipeline_failure_count": summary.candidate.pipeline_failure_count,
                "decision_reason_codes": summary.decision_reason_codes,
            },
            sort_keys=True,
        )
    )
    return 0 if summary.selected_for_three_repeat_confirmation else 3


if __name__ == "__main__":
    raise SystemExit(main())
