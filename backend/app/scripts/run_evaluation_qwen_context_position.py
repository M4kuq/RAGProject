from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

import httpx
from pydantic import BaseModel

from app.services.evaluation_atomic_claim_contracts import write_model_json
from app.services.evaluation_atomic_claim_review_workflow_service import (
    _path_has_symlink_component,
    _path_inside_git_checkout,
)
from app.services.evaluation_qwen_context_position_service import (
    EvaluationQwenContextPositionError,
    Rag85LMInventorySummary,
    build_rag85_attempt_state,
    load_frozen_rag85_experiment_manifest,
    run_rag85_diagnostic,
)

_MODEL = "qwen/qwen3.5-9b"
_LM_MODELS_URL = "http://127.0.0.1:1234/api/v0/models"
_REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
_LOCK_PATH = (
    Path(__file__).resolve().parents[1]
    / "evaluation"
    / "fixtures"
    / "rag85_qwen_context_position_lock.json"
)
_ALLOWED_USER_OWNED_DIRTY_PATH = "scripts/test_nvidia_generation.ps1"
_EXPECTED_BRANCH = "feature/qwen-context-position-diagnostic"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the frozen one-shot raw-free RAG-85 context-position diagnostic."
    )
    parser.add_argument("--prelive-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--attempt-marker", type=Path, required=True)
    parser.add_argument("--confirm-local-only", action="store_true")
    parser.add_argument("--confirm-one-shot", action="store_true")
    args = parser.parse_args()

    if not args.confirm_local_only:
        return _print_blocked("rag85_local_confirmation_required")
    if not args.confirm_one_shot:
        return _print_blocked("rag85_one_shot_confirmation_required")
    try:
        _validate_output_path(args.output)
        _validate_output_path(args.attempt_marker)
        if args.output.resolve(strict=False) == args.attempt_marker.resolve(strict=False):
            raise EvaluationQwenContextPositionError("rag85_output_paths_must_differ")
        manifest = load_frozen_rag85_experiment_manifest(_LOCK_PATH)
        _validate_current_commit(args.prelive_commit)
        _validate_current_branch()
        _validate_stacked_base(manifest.stacked_base_commit)
        _validate_worktree_dirty_scope()
        pre_inventory = _fetch_lm_inventory()
        attempt = build_rag85_attempt_state(
            manifest,
            prelive_commit_sha=args.prelive_commit,
            pre_lm_inventory=pre_inventory,
        )
        _write_model_exclusive(args.attempt_marker, attempt)
        result = run_rag85_diagnostic(
            manifest,
            prelive_commit_sha=args.prelive_commit,
            pre_lm_inventory=pre_inventory,
            post_lm_inventory_provider=_fetch_lm_inventory,
            progress_callback=_print_progress,
        )
        _write_model_exclusive(args.output, result)
    except (
        EvaluationQwenContextPositionError,
        OSError,
        UnicodeError,
        ValueError,
        subprocess.SubprocessError,
    ) as exc:
        reason_code = (
            str(exc)
            if isinstance(exc, EvaluationQwenContextPositionError)
            else "rag85_output_input_or_local_authority_unreadable"
        )
        return _print_blocked(reason_code)

    artifact_sha256 = hashlib.sha256(args.output.read_bytes()).hexdigest()
    print(
        json.dumps(
            {
                "status": "completed",
                "conclusion": result.conclusion,
                "validity_gate_passed": result.validity_gate_passed,
                "position_dependence_gate_passed": result.position_dependence_gate_passed,
                "generation_count": result.generation_count,
                "pipeline_failure_count": result.pipeline_failure_count,
                "binding_drift_count": result.binding_drift_count,
                "case_exclusion_count": result.case_exclusion_count,
                "lm_inventory_stable": result.lm_inventory_stable,
                "artifact_sha256": artifact_sha256,
            },
            sort_keys=True,
        )
    )
    return 0 if result.validity_gate_passed else 2


def _fetch_lm_inventory() -> Rag85LMInventorySummary:
    try:
        response = httpx.get(_LM_MODELS_URL, timeout=5.0)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return Rag85LMInventorySummary(available=False)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return Rag85LMInventorySummary(available=False)
    entries: list[dict[str, object]] = []
    target_loaded_count: int | None = None
    target_loaded_context_length: int | None = None
    total_loaded = 0
    for item in payload["data"]:
        if not isinstance(item, dict):
            return Rag85LMInventorySummary(available=False)
        model_id = item.get("id")
        state = item.get("state")
        raw_loaded_context_length = item.get("loaded_context_length")
        if (
            not isinstance(model_id, str)
            or not isinstance(state, str)
            or state not in {"loaded", "not-loaded"}
        ):
            return Rag85LMInventorySummary(available=False)
        loaded_count = int(state == "loaded")
        loaded_context_length = (
            raw_loaded_context_length
            if isinstance(raw_loaded_context_length, int) and raw_loaded_context_length > 0
            else None
        )
        if loaded_count and loaded_context_length is None:
            return Rag85LMInventorySummary(available=False)
        total_loaded += loaded_count
        entries.append(
            {
                "model_id": model_id,
                "state": state,
                "loaded_context_length": loaded_context_length,
            }
        )
        if model_id == _MODEL:
            target_loaded_count = loaded_count
            target_loaded_context_length = loaded_context_length
    if target_loaded_count is None:
        return Rag85LMInventorySummary(available=False)
    canonical = json.dumps(
        {"models": sorted(entries, key=lambda item: str(item["model_id"]))},
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return Rag85LMInventorySummary(
        available=True,
        inventory_fingerprint=hashlib.sha256(canonical).hexdigest(),
        model_count=len(entries),
        loaded_instance_count=total_loaded,
        target_loaded_instance_count=target_loaded_count,
        target_loaded_context_length=target_loaded_context_length,
    )


def _validate_current_commit(expected: str) -> None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.stdout.strip() != expected:
        raise EvaluationQwenContextPositionError("rag85_prelive_commit_head_mismatch")


def _validate_worktree_dirty_scope() -> None:
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
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
        raise EvaluationQwenContextPositionError("rag85_worktree_experiment_changes_uncommitted")


def _validate_current_branch() -> None:
    completed = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=_REPOSITORY_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.stdout.strip() != _EXPECTED_BRANCH:
        raise EvaluationQwenContextPositionError("rag85_dedicated_branch_mismatch")


def _validate_stacked_base(stacked_base: str) -> None:
    completed = subprocess.run(
        ["git", "merge-base", "--is-ancestor", stacked_base, "HEAD"],
        cwd=_REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise EvaluationQwenContextPositionError("rag85_stacked_base_not_ancestor")


def _validate_output_path(path: Path) -> None:
    if _path_inside_git_checkout(path):
        raise EvaluationQwenContextPositionError("rag85_repository_output_rejected")
    if path.exists() or path.is_symlink():
        raise EvaluationQwenContextPositionError("rag85_output_already_exists")
    if _path_has_symlink_component(path):
        raise EvaluationQwenContextPositionError("rag85_output_symlink_rejected")


def _write_model_exclusive(path: Path, model: BaseModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if _path_has_symlink_component(path):
        raise EvaluationQwenContextPositionError("rag85_output_symlink_rejected")
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise EvaluationQwenContextPositionError("rag85_output_already_exists") from exc
    os.close(descriptor)
    write_model_json(path, model)


def _print_progress(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True))


def _print_blocked(reason_code: str) -> int:
    print(json.dumps({"status": "blocked", "reason_code": reason_code}, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
