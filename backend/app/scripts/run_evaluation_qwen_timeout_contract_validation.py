from __future__ import annotations

import argparse
import hashlib
import json
import secrets
import subprocess
from pathlib import Path

from app.scripts.run_evaluation_qwen_context_near_miss import _fetch_lm_inventory
from app.scripts.run_evaluation_qwen_multifact_interference_repair import (
    _validate_external_output_path,
    _validate_private_input_path,
    _write_bytes_exclusive,
    _write_model_exclusive,
)
from app.services.evaluation_atomic_claim_contracts import (
    canonical_json_bytes,
    model_bytes_match,
    read_json_object,
)
from app.services.evaluation_qwen_multifact_interference_repair_service import (
    EvaluationQwenMultifactInterferenceRepairError,
    Rag88ExperimentLock,
    _sha256_bytes,
    build_rag88_reference_catalog,
    load_rag87_reference_fixture,
    load_rag88_private_fixture,
)
from app.services.evaluation_qwen_timeout_contract_validation_service import (
    EvaluationQwenTimeoutContractValidationError,
    Rag90HostGate,
    build_rag90_attempt_state,
    build_rag90_host_gate,
    build_rag90_lock,
    build_rag90_manifest,
    build_rag90_private_fixture,
    build_rag90_timeout_evidence,
    load_frozen_rag90_manifest,
    run_rag90_experiment,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_LOCK_PATH = (
    Path(__file__).resolve().parents[1]
    / "evaluation"
    / "fixtures"
    / "rag90_qwen_timeout_contract_validation_lock.json"
)
_RAG88_LOCK_PATH = (
    Path(__file__).resolve().parents[1]
    / "evaluation"
    / "fixtures"
    / "rag88_qwen_multifact_interference_repair_lock.json"
)
_EXPECTED_BRANCH = "feature/qwen-timeout-contract-validation"
_STACKED_BASE = "b063d545f90e28096ddefa5a815a0784f0699d1a"
_ALLOWED_USER_OWNED_DIRTY_PATH = "scripts/test_nvidia_generation.ps1"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare or run the raw-free RAG-90 timeout-only one-shot experiment."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare-private")
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--confirm-repository-external-private-output", action="store_true")

    lock = subparsers.add_parser("build-lock")
    lock.add_argument("--private-input", type=Path, required=True)
    lock.add_argument("--rag87-private-input", type=Path, required=True)
    lock.add_argument("--rag88-private-input", type=Path, required=True)
    lock.add_argument("--rag84-result", type=Path, required=True)
    lock.add_argument("--rag88-result", type=Path, required=True)
    lock.add_argument("--rag88-attempt", type=Path, required=True)
    lock.add_argument("--output", type=Path, required=True)

    host_gate = subparsers.add_parser("build-host-gate")
    host_gate.add_argument("--gpu-utilization-samples", type=int, nargs=3, required=True)
    host_gate.add_argument("--concurrent-evaluation-process-count", type=int, required=True)
    host_gate.add_argument("--concurrent-model-load-observed", action="store_true")
    host_gate.add_argument("--confirm-user-authorized-current-gpu-load", action="store_true")
    host_gate.add_argument("--task-owned-model-load-performed", action="store_true")
    host_gate.add_argument("--output", type=Path, required=True)

    run = subparsers.add_parser("run")
    run.add_argument("--private-input", type=Path, required=True)
    run.add_argument("--host-gate", type=Path, required=True)
    run.add_argument("--prelive-commit", required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--attempt-marker", type=Path, required=True)
    run.add_argument("--confirm-local-only", action="store_true")
    run.add_argument("--confirm-one-shot", action="store_true")
    args = parser.parse_args()

    try:
        if args.command == "prepare-private":
            return _prepare_private(args)
        if args.command == "build-lock":
            return _build_lock(args)
        if args.command == "build-host-gate":
            return _build_host_gate(args)
        return _run(args)
    except (
        EvaluationQwenTimeoutContractValidationError,
        EvaluationQwenMultifactInterferenceRepairError,
        OSError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        reason_code = (
            str(exc)
            if isinstance(
                exc,
                (
                    EvaluationQwenTimeoutContractValidationError,
                    EvaluationQwenMultifactInterferenceRepairError,
                ),
            )
            else "rag90_output_input_or_local_authority_unreadable"
        )
        return _print_blocked(reason_code)


def _prepare_private(args: argparse.Namespace) -> int:
    if not args.confirm_repository_external_private_output:
        return _print_blocked("rag90_private_output_confirmation_required")
    _validate_external_output_path(args.output)
    _write_bytes_exclusive(
        args.output,
        canonical_json_bytes(build_rag90_private_fixture(secrets.token_bytes(32))),
    )
    print(
        json.dumps(
            {
                "status": "private_fixture_prepared",
                "private_input_sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
                "group_count": 12,
                "case_count": 36,
                "raw_content_persisted_in_repository": False,
            },
            sort_keys=True,
        )
    )
    return 0


def _build_lock(args: argparse.Namespace) -> int:
    for path in (
        args.private_input,
        args.rag87_private_input,
        args.rag88_private_input,
        args.rag84_result,
        args.rag88_result,
        args.rag88_attempt,
    ):
        _validate_private_input_path(path)
    _validate_external_output_path(args.output)
    private_bytes, envelope = load_rag88_private_fixture(args.private_input)
    rag87_envelope = load_rag87_reference_fixture(args.rag87_private_input)
    reference_catalog = build_rag88_reference_catalog(rag87_envelope)
    rag88_private_bytes, rag88_envelope = load_rag88_private_fixture(args.rag88_private_input)
    rag88_lock_bytes, rag88_lock_payload = read_json_object(_RAG88_LOCK_PATH)
    rag88_lock = Rag88ExperimentLock.model_validate(rag88_lock_payload)
    if not model_bytes_match(rag88_lock_bytes, rag88_lock):
        raise EvaluationQwenTimeoutContractValidationError("rag90_rag88_lock_model_bytes_drift")
    timeout_evidence = build_rag90_timeout_evidence(
        args.rag84_result.read_bytes(),
        args.rag88_result.read_bytes(),
        args.rag88_attempt.read_bytes(),
    )
    manifest = build_rag90_manifest(
        envelope,
        private_input_sha256=_sha256_bytes(private_bytes),
        reference_catalog=reference_catalog,
        rag88_reference_envelope=rag88_envelope,
        rag88_private_input_sha256=_sha256_bytes(rag88_private_bytes),
        rag88_lock=rag88_lock,
        rag88_lock_sha256=_sha256_bytes(rag88_lock_bytes),
        timeout_evidence=timeout_evidence,
        stacked_base_commit=_STACKED_BASE,
    )
    _write_model_exclusive(args.output, build_rag90_lock(manifest))
    print(
        json.dumps(
            {
                "status": "raw_free_lock_built",
                "selected_timeout_seconds": 360,
                "changed_behavioral_coordinate": "timeout_contract",
                "other_behavioral_coordinate_change_count": 0,
                "rag88_reference_overlap_count": 0,
                "model_call_count": 144,
                "pipeline_failure_count_maximum": 0,
            },
            sort_keys=True,
        )
    )
    return 0


def _build_host_gate(args: argparse.Namespace) -> int:
    _validate_external_output_path(args.output)
    if args.concurrent_evaluation_process_count < 0:
        raise EvaluationQwenTimeoutContractValidationError(
            "rag90_concurrent_evaluation_count_invalid"
        )
    samples = tuple(args.gpu_utilization_samples)
    if len(samples) != 3 or any(value < 0 or value > 100 for value in samples):
        raise EvaluationQwenTimeoutContractValidationError("rag90_gpu_samples_invalid")
    gate = build_rag90_host_gate(
        _fetch_lm_inventory(),
        gpu_utilization_samples_percent=(samples[0], samples[1], samples[2]),
        concurrent_evaluation_process_count=args.concurrent_evaluation_process_count,
        concurrent_model_load_observed=args.concurrent_model_load_observed,
        gpu_load_exception_authorized=args.confirm_user_authorized_current_gpu_load,
        task_owned_model_load_performed=args.task_owned_model_load_performed,
    )
    if not gate.gate_passed:
        reason = gate.reason_codes[0] if gate.reason_codes else "rag90_host_gate_failed"
        return _print_blocked(reason)
    _write_model_exclusive(args.output, gate)
    print(
        json.dumps(
            {
                "status": "host_gate_passed",
                "target_loaded_instance_count": gate.lm_inventory.target_loaded_instance_count,
                "target_context_length": gate.lm_inventory.target_loaded_context_length,
                "gpu_utilization_max_percent": max(gate.gpu_utilization_samples_percent),
                "gpu_high_load_absent": gate.gpu_high_load_absent,
                "gpu_load_exception_authorized": gate.gpu_load_exception_authorized,
                "concurrent_evaluation_process_count": (gate.concurrent_evaluation_process_count),
            },
            sort_keys=True,
        )
    )
    return 0


def _run(args: argparse.Namespace) -> int:
    if not args.confirm_local_only:
        return _print_blocked("rag90_local_confirmation_required")
    if not args.confirm_one_shot:
        return _print_blocked("rag90_one_shot_confirmation_required")
    _validate_private_input_path(args.private_input)
    _validate_private_input_path(args.host_gate)
    _validate_external_output_path(args.output)
    _validate_external_output_path(args.attempt_marker)
    if args.output.resolve(strict=False) == args.attempt_marker.resolve(strict=False):
        raise EvaluationQwenTimeoutContractValidationError("rag90_output_paths_must_differ")
    manifest, envelope = load_frozen_rag90_manifest(_LOCK_PATH, args.private_input)
    host_gate_bytes, host_gate_payload = read_json_object(args.host_gate)
    host_gate = Rag90HostGate.model_validate(host_gate_payload)
    if not model_bytes_match(host_gate_bytes, host_gate):
        raise EvaluationQwenTimeoutContractValidationError("rag90_host_gate_model_bytes_drift")
    current_inventory = _fetch_lm_inventory()
    if (
        current_inventory.target_entry_fingerprint
        != host_gate.lm_inventory.target_entry_fingerprint
        or current_inventory.target_loaded_instance_count != 1
        or current_inventory.target_loaded_context_length != 12312
    ):
        raise EvaluationQwenTimeoutContractValidationError("rag90_host_gate_target_drift")
    _validate_current_commit(args.prelive_commit)
    _validate_current_branch()
    _validate_stacked_base(manifest.stacked_base_commit)
    _validate_worktree_dirty_scope()
    attempt = build_rag90_attempt_state(
        manifest,
        host_gate=host_gate,
        prelive_commit_sha=args.prelive_commit,
    )
    _write_model_exclusive(args.attempt_marker, attempt)
    result = run_rag90_experiment(
        manifest,
        envelope,
        host_gate=host_gate,
        prelive_commit_sha=args.prelive_commit,
        post_lm_inventory_provider=_fetch_lm_inventory,
        progress_callback=_print_progress,
    )
    _write_model_exclusive(args.output, result)
    artifact_sha256 = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "status": "completed",
                "conclusion": result.conclusion,
                "gpu_load_exception_authorized": result.gpu_load_exception_authorized,
                "reason_codes": result.reason_codes,
                "validity_gate_passed": result.validity_gate_passed,
                "baseline_sensitivity_gate_passed": result.baseline_sensitivity_gate_passed,
                "candidate_adoption_gate_passed": result.candidate_adoption_gate_passed,
                "pipeline_failure_count": result.core_result.pipeline_failure_count,
                "eligible_case_count": result.core_result.summary.eligible_case_count,
                "baseline_incomplete_case_count": (
                    result.core_result.summary.baseline_incomplete_case_count
                ),
                "baseline_interference_drop": (
                    result.core_result.summary.baseline_interference_drop
                ),
                "candidate_joint_completeness_delta": (
                    result.core_result.summary.candidate_joint_completeness_delta
                ),
                "candidate_p95_latency_ratio": (
                    result.core_result.summary.candidate_p95_latency_ratio
                ),
                "physical_request_count": (result.phase_telemetry_summary.physical_request_count),
                "physical_request_timeout_count": (
                    result.phase_telemetry_summary.physical_request_timeout_count
                ),
                "artifact_sha256": artifact_sha256,
            },
            sort_keys=True,
        )
    )
    return 0 if result.validity_gate_passed else 2


def _git_args(*args: str) -> list[str]:
    return ["git", "-c", f"safe.directory={_REPOSITORY_ROOT}", *args]


def _validate_current_commit(expected: str) -> None:
    completed = subprocess.run(
        _git_args("rev-parse", "HEAD"),
        cwd=_REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.stdout.strip() != expected:
        raise EvaluationQwenTimeoutContractValidationError("rag90_prelive_commit_head_mismatch")


def _validate_current_branch() -> None:
    completed = subprocess.run(
        _git_args("branch", "--show-current"),
        cwd=_REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.stdout.strip() != _EXPECTED_BRANCH:
        raise EvaluationQwenTimeoutContractValidationError("rag90_dedicated_branch_mismatch")


def _validate_stacked_base(stacked_base: str) -> None:
    completed = subprocess.run(
        _git_args("merge-base", "--is-ancestor", stacked_base, "HEAD"),
        cwd=_REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise EvaluationQwenTimeoutContractValidationError("rag90_stacked_base_not_ancestor")


def _validate_worktree_dirty_scope() -> None:
    completed = subprocess.run(
        _git_args("status", "--porcelain=v1", "--untracked-files=all"),
        cwd=_REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    dirty_paths = tuple(
        line[3:].strip().replace("\\", "/")
        for line in completed.stdout.splitlines()
        if line.strip()
    )
    if any(path != _ALLOWED_USER_OWNED_DIRTY_PATH for path in dirty_paths):
        raise EvaluationQwenTimeoutContractValidationError(
            "rag90_worktree_experiment_changes_uncommitted"
        )


def _print_progress(payload: dict[str, object]) -> None:
    safe = dict(payload)
    safe["status"] = "rag90_generation_progress"
    print(json.dumps(safe, sort_keys=True))


def _print_blocked(reason_code: str) -> int:
    print(json.dumps({"status": "blocked", "reason_code": reason_code}, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
