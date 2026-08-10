from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from app.services.evaluation_atomic_claim_contracts import (
    model_bytes_match,
    read_json_object,
    write_model_json,
)
from app.services.evaluation_atomic_claim_review_workflow_service import (
    _path_has_symlink_component,
    _path_inside_git_checkout,
)
from app.services.evaluation_qwen_multifact_completeness_service import (
    EvaluationQwenMultifactCompletenessError,
    Rag84ExperimentResult,
    load_frozen_rag84_experiment_manifest,
    run_rag84_confirm,
    run_rag84_tune,
)

_LOCK_PATH = (
    Path(__file__).resolve().parents[1]
    / "evaluation"
    / "fixtures"
    / "rag84_qwen_multifact_experiment_lock.json"
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the frozen raw-free RAG-84 Qwen multi-fact experiment."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    tune = subparsers.add_parser("tune")
    _add_common_arguments(tune)
    confirm = subparsers.add_parser("confirm")
    _add_common_arguments(confirm)
    confirm.add_argument("--tune-result", type=Path, required=True)
    confirm.add_argument("--runtime-stable", action="store_true")
    args = parser.parse_args()

    if not args.confirm_local_only:
        return _print_blocked("rag84_local_confirmation_required")
    output: Path = args.output
    try:
        _validate_output_path(output)
        manifest = load_frozen_rag84_experiment_manifest(_LOCK_PATH)
        if args.command == "tune":
            result = run_rag84_tune(
                manifest,
                preconfirm_commit_sha=args.preconfirm_commit,
                progress_callback=_print_progress,
            )
        else:
            tune_result = _load_tune_result(args.tune_result)
            result = run_rag84_confirm(
                manifest,
                preconfirm_commit_sha=args.preconfirm_commit,
                tune_result=tune_result,
                runtime_stable_for_latency=bool(args.runtime_stable),
                progress_callback=_print_progress,
            )
        write_model_json(output, result)
    except (
        EvaluationQwenMultifactCompletenessError,
        OSError,
        UnicodeError,
        ValueError,
    ) as exc:
        reason_code = (
            str(exc)
            if isinstance(exc, EvaluationQwenMultifactCompletenessError)
            else "rag84_output_or_input_unreadable"
        )
        return _print_blocked(reason_code)

    artifact_sha256 = hashlib.sha256(output.read_bytes()).hexdigest()
    baseline, candidate = result.profiles
    print(
        json.dumps(
            {
                "status": "completed",
                "phase": result.phase,
                "gate_passed": result.gate_passed,
                "decision": result.decision,
                "case_observation_count": len(result.observations),
                "baseline_atomic_required_fact_recall": (
                    baseline.atomic_required_fact_recall
                ),
                "candidate_atomic_required_fact_recall": (
                    candidate.atomic_required_fact_recall
                ),
                "baseline_pipeline_failure_count": baseline.pipeline_failure_count,
                "candidate_pipeline_failure_count": candidate.pipeline_failure_count,
                "artifact_sha256": artifact_sha256,
            },
            sort_keys=True,
        )
    )
    return 0 if result.gate_passed else 2


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--preconfirm-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confirm-local-only", action="store_true")


def _validate_output_path(path: Path) -> None:
    if _path_inside_git_checkout(path):
        raise EvaluationQwenMultifactCompletenessError(
            "rag84_repository_output_rejected"
        )
    if path.exists() or path.is_symlink():
        raise EvaluationQwenMultifactCompletenessError("rag84_output_already_exists")
    if _path_has_symlink_component(path):
        raise EvaluationQwenMultifactCompletenessError("rag84_output_symlink_rejected")


def _load_tune_result(path: Path) -> Rag84ExperimentResult:
    payload_bytes, payload = read_json_object(path)
    result = Rag84ExperimentResult.model_validate(payload)
    if not model_bytes_match(payload_bytes, result):
        raise EvaluationQwenMultifactCompletenessError(
            "rag84_tune_result_bytes_model_mismatch"
        )
    return result


def _print_progress(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True))


def _print_blocked(reason_code: str) -> int:
    print(json.dumps({"status": "blocked", "reason_code": reason_code}, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
