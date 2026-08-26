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
from app.services.evaluation_qwen_confirm_fixture_sensitivity_service import (
    EvaluationQwenConfirmFixtureSensitivityError,
    _sha256_bytes,
    build_rag87_attempt_state,
    build_rag87_experiment_lock,
    build_rag87_experiment_manifest,
    build_rag87_private_fixture,
    load_frozen_rag87_experiment_manifest,
    load_rag87_private_fixture,
    run_rag87_diagnostic,
)

_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_LOCK_PATH = (
    Path(__file__).resolve().parents[1]
    / "evaluation"
    / "fixtures"
    / "rag87_qwen_confirm_fixture_sensitivity_lock.json"
)
_EXPECTED_BRANCH = "feature/qwen-confirm-fixture-sensitivity"
_ALLOWED_USER_OWNED_DIRTY_PATH = "scripts/test_nvidia_generation.ps1"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare or run the frozen raw-free RAG-87 fixture-sensitivity diagnostic."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare-private")
    prepare.add_argument("--output", type=Path, required=True)
    prepare.add_argument("--confirm-repository-external-private-output", action="store_true")

    lock = subparsers.add_parser("build-lock")
    lock.add_argument("--private-input", type=Path, required=True)
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
                return _print_blocked("rag87_private_output_confirmation_required")
            _validate_external_output_path(args.output)
            _write_bytes_exclusive(
                args.output,
                canonical_json_bytes(build_rag87_private_fixture(secrets.token_bytes(32))),
            )
            print(
                json.dumps(
                    {
                        "status": "private_fixture_prepared",
                        "private_input_sha256": hashlib.sha256(
                            args.output.read_bytes()
                        ).hexdigest(),
                        "case_count": 12,
                        "raw_content_persisted_in_repository": False,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if args.command == "build-lock":
            _validate_private_input_path(args.private_input)
            _validate_external_output_path(args.output)
            private_bytes, envelope = load_rag87_private_fixture(args.private_input)
            manifest = build_rag87_experiment_manifest(
                envelope,
                private_input_sha256=_sha256_bytes(private_bytes),
            )
            _write_model_exclusive(args.output, build_rag87_experiment_lock(manifest))
            print(
                json.dumps(
                    {
                        "status": "raw_free_lock_built",
                        "generation_count": 36,
                        "structural_features_exact_match": True,
                        "content_overlap_count": 0,
                    },
                    sort_keys=True,
                )
            )
            return 0
        if not args.confirm_local_only:
            return _print_blocked("rag87_local_confirmation_required")
        if not args.confirm_one_shot:
            return _print_blocked("rag87_one_shot_confirmation_required")
        _validate_private_input_path(args.private_input)
        _validate_external_output_path(args.output)
        _validate_external_output_path(args.attempt_marker)
        if args.output.resolve(strict=False) == args.attempt_marker.resolve(strict=False):
            raise EvaluationQwenConfirmFixtureSensitivityError("rag87_output_paths_must_differ")
        manifest, envelope = load_frozen_rag87_experiment_manifest(
            _LOCK_PATH,
            args.private_input,
        )
        _validate_current_commit(args.prelive_commit)
        _validate_current_branch()
        _validate_stacked_base(manifest.stacked_base_commit)
        _validate_worktree_dirty_scope()
        pre_inventory = _fetch_lm_inventory()
        attempt = build_rag87_attempt_state(
            manifest,
            prelive_commit_sha=args.prelive_commit,
            pre_lm_inventory=pre_inventory,
        )
        _write_model_exclusive(args.attempt_marker, attempt)
        result = run_rag87_diagnostic(
            manifest,
            envelope,
            prelive_commit_sha=args.prelive_commit,
            pre_lm_inventory=pre_inventory,
            post_lm_inventory_provider=_fetch_lm_inventory,
            progress_callback=_print_progress,
        )
        _write_model_exclusive(args.output, result)
    except (
        EvaluationQwenConfirmFixtureSensitivityError,
        OSError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        reason_code = (
            str(exc)
            if isinstance(exc, EvaluationQwenConfirmFixtureSensitivityError)
            else "rag87_output_input_or_local_authority_unreadable"
        )
        return _print_blocked(reason_code)

    artifact_sha256 = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "status": "completed",
                "conclusion": result.conclusion,
                "validity_gate_passed": result.validity_gate_passed,
                "sensitivity_gate_passed": result.sensitivity_gate_passed,
                "generation_count": result.generation_count,
                "pipeline_failure_count": result.pipeline_failure_count,
                "binding_drift_count": result.binding_drift_count,
                "case_exclusion_count": result.case_exclusion_count,
                "case_replacement_count": result.case_replacement_count,
                "exact_target_stable": result.exact_target_stable,
                "full_lm_inventory_stable": result.full_lm_inventory_stable,
                "majority_atomic_required_fact_recall": (
                    result.summary.majority_atomic_required_fact_recall
                ),
                "majority_complete_case_count": (result.summary.majority_complete_case_count),
                "first_only_with_majority_insufficiency_case_count": (
                    result.summary.first_only_with_majority_insufficiency_case_count
                ),
                "first_minus_second_majority_recall": (
                    result.summary.first_minus_second_majority_recall
                ),
                "repeat_atomic_required_fact_recall_range": (
                    result.summary.repeat_atomic_required_fact_recall_range
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
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_prelive_commit_head_mismatch")


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
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_dedicated_branch_mismatch")


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
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_stacked_base_not_ancestor")


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
        raise EvaluationQwenConfirmFixtureSensitivityError(
            "rag87_worktree_experiment_changes_uncommitted"
        )


def _validate_private_input_path(path: Path) -> None:
    if _path_inside_git_checkout(path):
        raise EvaluationQwenConfirmFixtureSensitivityError(
            "rag87_repository_private_input_rejected"
        )
    if not path.is_file() or path.is_symlink() or _path_has_symlink_component(path):
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_private_input_unreadable")


def _validate_external_output_path(path: Path) -> None:
    if _path_inside_git_checkout(path):
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_repository_output_rejected")
    if path.exists() or path.is_symlink():
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_output_already_exists")
    if _path_has_symlink_component(path):
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_output_symlink_rejected")


def _write_bytes_exclusive(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if _path_has_symlink_component(path):
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_output_symlink_rejected")
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_output_already_exists") from exc
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(payload)


def _write_model_exclusive(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise EvaluationQwenConfirmFixtureSensitivityError("rag87_output_already_exists") from exc
    os.close(descriptor)
    write_model_json(path, model)


def _print_progress(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True))


def _print_blocked(reason_code: str) -> int:
    print(json.dumps({"status": "blocked", "reason_code": reason_code}, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
