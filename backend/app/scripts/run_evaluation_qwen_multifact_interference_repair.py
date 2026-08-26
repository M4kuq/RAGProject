from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
from pathlib import Path

from pydantic import BaseModel

from app.scripts.run_evaluation_qwen_context_near_miss import _fetch_lm_inventory
from app.services.evaluation_atomic_claim_contracts import (
    canonical_json_bytes,
    write_model_json,
)
from app.services.evaluation_atomic_claim_review_workflow_service import (
    _path_has_symlink_component,
    _path_inside_git_checkout,
)
from app.services.evaluation_qwen_multifact_interference_repair_service import (
    EvaluationQwenMultifactInterferenceRepairError,
    _sha256_bytes,
    build_rag88_attempt_state,
    build_rag88_experiment_lock,
    build_rag88_experiment_manifest,
    build_rag88_private_fixture,
    build_rag88_reference_catalog,
    load_frozen_rag88_experiment_manifest,
    load_rag87_reference_fixture,
    load_rag88_private_fixture,
    run_rag88_experiment,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_LOCK_PATH = (
    Path(__file__).resolve().parents[1]
    / "evaluation"
    / "fixtures"
    / "rag88_qwen_multifact_interference_repair_lock.json"
)
_EXPECTED_BRANCH = "feature/qwen-multifact-interference-repair"
_ALLOWED_USER_OWNED_DIRTY_PATH = "scripts/test_nvidia_generation.ps1"


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Prepare or run the frozen raw-free RAG-88 within-case interference and "
            "generic two-pass repair experiment."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare-private")
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--confirm-repository-external-private-output", action="store_true")

    lock = subparsers.add_parser("build-lock")
    lock.add_argument("--private-input", type=Path, required=True)
    lock.add_argument("--rag87-private-input", type=Path, required=True)
    lock.add_argument("--output", type=Path, required=True)

    run = subparsers.add_parser("run")
    run.add_argument("--private-input", type=Path, required=True)
    run.add_argument("--prelive-commit", required=True)
    run.add_argument("--output", type=Path, required=True)
    run.add_argument("--attempt-marker", type=Path, required=True)
    run.add_argument("--confirm-local-only", action="store_true")
    run.add_argument("--confirm-one-shot", action="store_true")
    args = parser.parse_args()

    try:
        if args.command == "prepare-private":
            if not args.confirm_repository_external_private_output:
                return _print_blocked("rag88_private_output_confirmation_required")
            _validate_external_output_path(args.output)
            _write_bytes_exclusive(
                args.output,
                canonical_json_bytes(build_rag88_private_fixture(secrets.token_bytes(32))),
            )
            print(
                json.dumps(
                    {
                        "status": "private_fixture_prepared",
                        "private_input_sha256": hashlib.sha256(
                            args.output.read_bytes()
                        ).hexdigest(),
                        "group_count": 12,
                        "case_count": 36,
                        "raw_content_persisted_in_repository": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "build-lock":
            _validate_private_input_path(args.private_input)
            _validate_private_input_path(args.rag87_private_input)
            _validate_external_output_path(args.output)
            private_bytes, envelope = load_rag88_private_fixture(args.private_input)
            rag87_envelope = load_rag87_reference_fixture(args.rag87_private_input)
            reference_catalog = build_rag88_reference_catalog(rag87_envelope)
            manifest = build_rag88_experiment_manifest(
                envelope,
                private_input_sha256=_sha256_bytes(private_bytes),
                reference_catalog=reference_catalog,
            )
            _write_model_exclusive(args.output, build_rag88_experiment_lock(manifest))
            print(
                json.dumps(
                    {
                        "status": "raw_free_lock_built",
                        "model_call_count": 144,
                        "variant_observation_count": 144,
                        "reference_overlap_count": 0,
                        "minimum_eligible_case_count": 8,
                        "minimum_baseline_incomplete_case_count": 6,
                        "minimum_interference_drop": 0.5,
                        "bootstrap_resamples": 10000,
                        "exact_p_value_maximum": 0.05,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if not args.confirm_local_only:
            return _print_blocked("rag88_local_confirmation_required")
        if not args.confirm_one_shot:
            return _print_blocked("rag88_one_shot_confirmation_required")
        _validate_private_input_path(args.private_input)
        _validate_external_output_path(args.output)
        _validate_external_output_path(args.attempt_marker)
        if args.output.resolve(strict=False) == args.attempt_marker.resolve(strict=False):
            raise EvaluationQwenMultifactInterferenceRepairError("rag88_output_paths_must_differ")
        manifest, envelope = load_frozen_rag88_experiment_manifest(
            _LOCK_PATH,
            args.private_input,
        )
        _validate_current_commit(args.prelive_commit)
        _validate_current_branch()
        _validate_stacked_base(manifest.stacked_base_commit)
        _validate_worktree_dirty_scope()
        pre_inventory = _fetch_lm_inventory()
        attempt = build_rag88_attempt_state(
            manifest,
            prelive_commit_sha=args.prelive_commit,
            pre_lm_inventory=pre_inventory,
        )
        _write_model_exclusive(args.attempt_marker, attempt)
        result = run_rag88_experiment(
            manifest,
            envelope,
            prelive_commit_sha=args.prelive_commit,
            pre_lm_inventory=pre_inventory,
            post_lm_inventory_provider=_fetch_lm_inventory,
            progress_callback=_print_progress,
        )
        _write_model_exclusive(args.output, result)
    except (
        EvaluationQwenMultifactInterferenceRepairError,
        OSError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        reason_code = (
            str(exc)
            if isinstance(exc, EvaluationQwenMultifactInterferenceRepairError)
            else "rag88_output_input_or_local_authority_unreadable"
        )
        return _print_blocked(reason_code)

    artifact_sha256 = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "status": "completed",
                "conclusion": result.conclusion,
                "validity_gate_passed": result.validity_gate_passed,
                "baseline_sensitivity_gate_passed": (result.baseline_sensitivity_gate_passed),
                "candidate_adoption_gate_passed": result.candidate_adoption_gate_passed,
                "candidate_metrics_descriptive_only": (result.candidate_metrics_descriptive_only),
                "model_call_count": result.model_call_count,
                "pipeline_failure_count": result.pipeline_failure_count,
                "binding_drift_count": result.binding_drift_count,
                "case_exclusion_count": result.case_exclusion_count,
                "case_replacement_count": result.case_replacement_count,
                "exact_target_stable": result.exact_target_stable,
                "eligible_case_count": result.summary.eligible_case_count,
                "baseline_incomplete_case_count": (result.summary.baseline_incomplete_case_count),
                "baseline_interference_drop": result.summary.baseline_interference_drop,
                "baseline_interference_bootstrap_ci95": (
                    result.summary.baseline_interference_bootstrap_ci95
                ),
                "baseline_interference_exact_p_value": (
                    result.summary.baseline_interference_exact_p_value
                ),
                "candidate_joint_completeness_delta": (
                    result.summary.candidate_joint_completeness_delta
                ),
                "candidate_p95_latency_ratio": (result.summary.candidate_p95_latency_ratio),
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
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_prelive_commit_head_mismatch")


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
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_dedicated_branch_mismatch")


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
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_stacked_base_not_ancestor")


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
        raise EvaluationQwenMultifactInterferenceRepairError(
            "rag88_worktree_experiment_changes_uncommitted"
        )


def _validate_private_input_path(path: Path) -> None:
    if _path_inside_git_checkout(path):
        raise EvaluationQwenMultifactInterferenceRepairError(
            "rag88_repository_private_input_rejected"
        )
    if not path.is_file() or path.is_symlink() or _path_has_symlink_component(path):
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_private_input_unreadable")


def _validate_external_output_path(path: Path) -> None:
    if _path_inside_git_checkout(path):
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_repository_output_rejected")
    if path.exists() or path.is_symlink():
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_output_already_exists")
    if _path_has_symlink_component(path):
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_output_symlink_rejected")


def _write_bytes_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if _path_has_symlink_component(path):
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_output_symlink_rejected")
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_output_already_exists") from exc
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)


def _write_model_exclusive(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise EvaluationQwenMultifactInterferenceRepairError("rag88_output_already_exists") from exc
    os.close(descriptor)
    write_model_json(path, model)


def _print_progress(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True))


def _print_blocked(reason_code: str) -> int:
    print(json.dumps({"status": "blocked", "reason_code": reason_code}, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
