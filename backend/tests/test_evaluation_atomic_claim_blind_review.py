from __future__ import annotations

import hashlib
import json
import sys
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.scripts.run_evaluation_atomic_claim_blind_review import main
from app.services.evaluation_atomic_claim_blind_review_service import (
    AtomicClaimBlindCandidateManifest,
    AtomicClaimBlindReferenceDecision,
    AtomicClaimBlindReviewCommitment,
    AtomicClaimBlindReviewManifest,
    AtomicClaimSourceContract,
    EvaluationAtomicClaimCalibrationError,
    build_phase_a_commitment,
    compute_review_scope_fingerprint,
    evaluate_phase_b,
)


def test_phase_a_commits_exact_reference_bytes_before_candidate() -> None:
    reference_bytes, reference = _reference()
    commitment = build_phase_a_commitment(
        reference_bytes,
        reference,
        committed_at_utc=datetime.fromisoformat("2026-08-10T00:01:00+00:00"),
    )

    assert commitment.reference_manifest_sha256 == hashlib.sha256(
        reference_bytes
    ).hexdigest()
    assert commitment.reference_claim_count == 2
    assert commitment.candidate_results_observed is False
    assert commitment.candidate_identifiers_present is False
    assert commitment.raw_content_persisted is False


def test_review_schema_rejects_candidate_contamination_and_raw_fields() -> None:
    payload = _reference_payload()
    payload["candidate_id"] = "contaminating-candidate"

    with pytest.raises(ValidationError):
        AtomicClaimBlindReviewManifest.model_validate(payload)

    payload = _reference_payload()
    payload["decisions"][0]["claim_text"] = "runtime-generated-only"
    with pytest.raises(ValidationError):
        AtomicClaimBlindReviewManifest.model_validate(payload)


def test_review_schema_rejects_duplicate_claim_and_scope_drift() -> None:
    payload = _reference_payload()
    payload["decisions"].append(deepcopy(payload["decisions"][0]))
    with pytest.raises(ValidationError, match="blind_review_claim_identity_duplicate"):
        AtomicClaimBlindReviewManifest.model_validate(payload)

    payload = _reference_payload()
    payload["review_scope_fingerprint"] = "f" * 64
    with pytest.raises(
        ValidationError, match="blind_review_scope_fingerprint_mismatch"
    ):
        AtomicClaimBlindReviewManifest.model_validate(payload)


def test_automatic_or_codex_review_cannot_self_signoff() -> None:
    payload = _reference_payload()
    payload["review_status"] = "human_signed_off"
    payload["requires_human_signoff"] = False

    with pytest.raises(ValidationError, match="blind_review_signoff_evidence_missing"):
        AtomicClaimBlindReviewManifest.model_validate(payload)


def test_phase_b_metrics_remain_unselected_without_human_signoff() -> None:
    reference_bytes, reference = _reference()
    commitment_bytes, commitment = _commitment(reference_bytes, reference)
    candidate_bytes, candidate = _candidate(reference)

    result = evaluate_phase_b(
        reference_manifest_bytes=reference_bytes,
        reference=reference,
        commitment_bytes=commitment_bytes,
        commitment=commitment,
        candidate_manifest_bytes=candidate_bytes,
        candidate=candidate,
    )

    assert result.reference_label_authority == "auxiliary_requires_human_signoff"
    assert result.calibration.calibration_coverage == 1.0
    assert result.calibration.whole_statement_exact_false_negative_count == 1
    assert result.calibration.atomic_equivalence_false_negative_count == 0
    assert result.calibration.atomic_equivalence_false_positive_count == 0
    assert result.candidate_selected is False
    assert result.calibration.candidate_selected is False
    assert result.public_accuracy_claim_allowed is False
    assert result.profile_promotion_allowed is False
    assert "requires_human_signoff" in result.reason_codes


def test_phase_b_can_select_screening_only_after_explicit_human_signoff() -> None:
    reference_bytes, reference = _reference(human_signed_off=True)
    commitment_bytes, commitment = _commitment(reference_bytes, reference)
    candidate_bytes, candidate = _candidate(reference)

    result = evaluate_phase_b(
        reference_manifest_bytes=reference_bytes,
        reference=reference,
        commitment_bytes=commitment_bytes,
        commitment=commitment,
        candidate_manifest_bytes=candidate_bytes,
        candidate=candidate,
    )

    assert result.reference_label_authority == "human_calibrated"
    assert result.requires_human_signoff is False
    assert result.candidate_selected is True
    assert result.screening_only is True
    assert result.public_accuracy_claim_allowed is False
    assert result.profile_promotion_allowed is False


def test_phase_b_rejects_reference_mutation_after_commitment() -> None:
    reference_bytes, reference = _reference()
    commitment_bytes, commitment = _commitment(reference_bytes, reference)
    mutated = reference_bytes + b"\n"
    candidate_bytes, candidate = _candidate(reference)

    with pytest.raises(
        EvaluationAtomicClaimCalibrationError,
        match="atomic_claim_phase_a_reference_manifest_hash_mismatch",
    ):
        evaluate_phase_b(
            reference_manifest_bytes=mutated,
            reference=reference,
            commitment_bytes=commitment_bytes,
            commitment=commitment,
            candidate_manifest_bytes=candidate_bytes,
            candidate=candidate,
        )


def test_phase_b_rejects_hash_drift_and_incomplete_coverage() -> None:
    reference_bytes, reference = _reference()
    commitment_bytes, commitment = _commitment(reference_bytes, reference)
    payload = _candidate_payload(reference)
    payload["observations"][0]["question_hash"] = "e" * 64
    candidate = AtomicClaimBlindCandidateManifest.model_validate(payload)
    candidate_bytes = _canonical_bytes(payload)

    with pytest.raises(
        EvaluationAtomicClaimCalibrationError,
        match="atomic_claim_phase_b_hash_binding_drift",
    ):
        evaluate_phase_b(
            reference_manifest_bytes=reference_bytes,
            reference=reference,
            commitment_bytes=commitment_bytes,
            commitment=commitment,
            candidate_manifest_bytes=candidate_bytes,
            candidate=candidate,
        )

    payload = _candidate_payload(reference)
    payload["observations"].pop()
    candidate = AtomicClaimBlindCandidateManifest.model_validate(payload)
    candidate_bytes = _canonical_bytes(payload)
    with pytest.raises(
        EvaluationAtomicClaimCalibrationError,
        match="atomic_claim_phase_b_coverage_incomplete",
    ):
        evaluate_phase_b(
            reference_manifest_bytes=reference_bytes,
            reference=reference,
            commitment_bytes=commitment_bytes,
            commitment=commitment,
            candidate_manifest_bytes=candidate_bytes,
            candidate=candidate,
        )


def test_unanswerable_is_not_applicable_and_cannot_overlap() -> None:
    _, reference = _reference()
    payload = _candidate_payload(reference)
    payload["not_applicable_observations"][0] = {
        "case_id": payload["observations"][0]["case_id"],
        "question_hash": payload["observations"][0]["question_hash"],
        "source_hash": payload["observations"][0]["source_hash"],
        "answer_hash": payload["observations"][0]["answer_hash"],
        "context_hash": payload["observations"][0]["context_hash"],
        "reason": "unanswerable",
    }

    with pytest.raises(ValidationError, match="blind_candidate_not_applicable_overlap"):
        AtomicClaimBlindCandidateManifest.model_validate(payload)


def test_phase_b_output_is_raw_free() -> None:
    reference_bytes, reference = _reference()
    commitment_bytes, commitment = _commitment(reference_bytes, reference)
    candidate_bytes, candidate = _candidate(reference)
    result = evaluate_phase_b(
        reference_manifest_bytes=reference_bytes,
        reference=reference,
        commitment_bytes=commitment_bytes,
        commitment=commitment,
        candidate_manifest_bytes=candidate_bytes,
        candidate=candidate,
    )
    rendered = json.dumps(result.safe_dict(), ensure_ascii=False, sort_keys=True)

    for forbidden_key in (
        '"question"',
        '"answer_text"',
        '"context_text"',
        '"source_text"',
        '"chunk"',
        '"claim_text"',
        '"expected_answer"',
        '"model_output"',
        '"pii"',
        '"secret"',
    ):
        assert forbidden_key not in rendered


def test_cli_phase_a_and_phase_b_fail_closed_without_human_signoff(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reference_bytes, reference = _reference()
    reference_path = tmp_path / "reference.json"
    commitment_path = tmp_path / "commitment.json"
    candidate_path = tmp_path / "candidate.json"
    reference_path.write_bytes(reference_bytes)

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "atomic-claim-blind-review",
            "phase-a",
            "--reference-manifest",
            str(reference_path),
            "--output",
            str(commitment_path),
        ],
    )
    assert main() == 0
    phase_a_output = json.loads(capsys.readouterr().out)
    assert phase_a_output["candidate_results_observed"] is False

    candidate_payload = _candidate_payload(reference)
    candidate_payload["phase_a_reference_manifest_sha256"] = phase_a_output[
        "reference_manifest_sha256"
    ]
    candidate_path.write_bytes(_canonical_bytes(candidate_payload))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "atomic-claim-blind-review",
            "phase-b",
            "--reference-manifest",
            str(reference_path),
            "--phase-a-commitment",
            str(commitment_path),
            "--candidate-manifest",
            str(candidate_path),
        ],
    )
    assert main() == 2
    phase_b_output = json.loads(capsys.readouterr().out)
    assert phase_b_output["candidate_selected"] is False
    assert phase_b_output["calibration"]["calibration_coverage"] == 1.0


def test_cli_does_not_echo_invalid_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    reference_path = tmp_path / "reference.json"
    output_path = tmp_path / "commitment.json"
    reference_path.write_text(
        json.dumps({"schema_version": "unexpected", "raw_field": "runtime-only"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "atomic-claim-blind-review",
            "phase-a",
            "--reference-manifest",
            str(reference_path),
            "--output",
            str(output_path),
        ],
    )

    assert main() == 2
    assert json.loads(capsys.readouterr().out) == {
        "reason_code": "atomic_claim_blind_input_schema_invalid",
        "status": "blocked",
    }


def _source_payload() -> dict[str, Any]:
    return {
        "source_evaluation_run_id": 112,
        "dataset_name": "local_accuracy_dev_v1",
        "dataset_content_fingerprint": "1" * 64,
        "case_set_fingerprint": "2" * 64,
        "generation_config_fingerprint": "3" * 64,
        "generation_prompt_profile": "baseline",
        "generation_prompt_fingerprint": "4" * 64,
        "generation_budget_fingerprint": "5" * 64,
        "resolved_generation_model": "qwen/qwen3.5-9b",
        "generation_temperature": 0.0,
        "generation_max_context_chars": 6000,
        "generation_max_output_chars": 12000,
        "generation_max_output_tokens": 8192,
    }


def _decisions_payload() -> list[dict[str, Any]]:
    return [
        {
            "case_id": "case-a",
            "question_hash": "6" * 64,
            "source_hash": "7" * 64,
            "answer_hash": "8" * 64,
            "context_hash": "9" * 64,
            "required_fact_id": "fact-a",
            "required_fact_hash": "a" * 64,
            "claim_ordinal": 0,
            "reference_supported": True,
        },
        {
            "case_id": "case-b",
            "question_hash": "b" * 64,
            "source_hash": "c" * 64,
            "answer_hash": "d" * 64,
            "context_hash": "e" * 64,
            "required_fact_id": "fact-b",
            "required_fact_hash": "f" * 64,
            "claim_ordinal": 0,
            "reference_supported": True,
        },
    ]


def _reference_payload(*, human_signed_off: bool = False) -> dict[str, Any]:
    source_payload = _source_payload()
    source = AtomicClaimSourceContract.model_validate(source_payload)
    decisions_payload = _decisions_payload()
    decisions = tuple(
        AtomicClaimBlindReferenceDecision.model_validate(item)
        for item in decisions_payload
    )
    payload: dict[str, Any] = {
        "schema_version": "phase3.oracle_atomic_claim_blind_review.v1",
        "source": source_payload,
        "review_scope_id": "rag79_run112_existing_review_subset",
        "review_scope_fingerprint": compute_review_scope_fingerprint(source, decisions),
        "reviewer_provenance": "codex_assisted_blind_review",
        "reviewer_type": "codex_assisted",
        "review_tool": "local_review_capture",
        "review_tool_version": "v1",
        "reviewed_at_utc": "2026-08-10T00:00:00Z",
        "review_status": "requires_human_signoff",
        "requires_human_signoff": True,
        "human_signoff_provenance": None,
        "human_signoff_at_utc": None,
        "candidate_results_observed": False,
        "candidate_identifiers_present": False,
        "raw_content_persisted": False,
        "decisions": decisions_payload,
    }
    if human_signed_off:
        payload["review_status"] = "human_signed_off"
        payload["requires_human_signoff"] = False
        payload["human_signoff_provenance"] = "human_reviewer:v1"
        payload["human_signoff_at_utc"] = "2026-08-10T00:02:00Z"
    return payload


def _reference(
    *, human_signed_off: bool = False
) -> tuple[bytes, AtomicClaimBlindReviewManifest]:
    payload = _reference_payload(human_signed_off=human_signed_off)
    reference_bytes = _canonical_bytes(payload)
    return reference_bytes, AtomicClaimBlindReviewManifest.model_validate(payload)


def _commitment(
    reference_bytes: bytes,
    reference: AtomicClaimBlindReviewManifest,
) -> tuple[bytes, AtomicClaimBlindReviewCommitment]:
    commitment = build_phase_a_commitment(
        reference_bytes,
        reference,
        committed_at_utc=datetime.fromisoformat("2026-08-10T00:03:00+00:00"),
    )
    commitment_bytes = (commitment.model_dump_json(indent=2) + "\n").encode()
    return commitment_bytes, commitment


def _candidate_payload(reference: AtomicClaimBlindReviewManifest) -> dict[str, Any]:
    observations: list[dict[str, Any]] = []
    for index, decision in enumerate(reference.decisions):
        observations.append(
            {
                **decision.scope_binding(),
                "segmentation_status": "presegmented_reference_claim",
                "whole_statement_exact_match": index == 1,
                "atomic_equivalence_match": True,
            }
        )
    return {
        "schema_version": "phase3.oracle_atomic_claim_blind_candidate.v1",
        "source": reference.source.model_dump(mode="json"),
        "review_scope_id": reference.review_scope_id,
        "review_scope_fingerprint": reference.review_scope_fingerprint,
        "phase_a_reference_manifest_sha256": "0" * 64,
        "candidate_id": "atomic-equivalence-v1",
        "candidate_version": "v1",
        "candidate_dimension": "semantic_equivalence_only",
        "raw_content_persisted": False,
        "pipeline_failure_count": 0,
        "observations": observations,
        "not_applicable_observations": [
            {
                "case_id": "case-u",
                "question_hash": "0" * 64,
                "source_hash": "1" * 64,
                "answer_hash": "2" * 64,
                "context_hash": "3" * 64,
                "reason": "unanswerable",
            }
        ],
    }


def _candidate(
    reference: AtomicClaimBlindReviewManifest,
) -> tuple[bytes, AtomicClaimBlindCandidateManifest]:
    payload = _candidate_payload(reference)
    payload["phase_a_reference_manifest_sha256"] = hashlib.sha256(
        _canonical_bytes(reference.model_dump(mode="json"))
    ).hexdigest()
    candidate_bytes = _canonical_bytes(payload)
    return candidate_bytes, AtomicClaimBlindCandidateManifest.model_validate(payload)


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode()
