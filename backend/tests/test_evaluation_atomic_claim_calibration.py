from __future__ import annotations

import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.scripts.run_evaluation_atomic_claim_calibration import main
from app.services.evaluation_atomic_claim_calibration_service import (
    AtomicClaimCandidateManifest,
    AtomicClaimReviewManifest,
    EvaluationAtomicClaimCalibrationError,
    evaluate_atomic_claim_candidate,
    inspect_reference_payload,
)


def test_legacy_observation_review_fails_closed_without_inferred_claim_labels() -> None:
    candidate = AtomicClaimCandidateManifest.model_validate(_candidate_payload())
    legacy = {
        "schema_version": "phase3.oracle_codex_assisted_review.v1",
        "review_status": "requires_human_signoff",
        "reviewer_type": "codex_assisted_manual_content_review",
        "decisions": [{"manual_context_utilization": 1.0}],
    }

    reference, schema, provenance, requires_signoff = inspect_reference_payload(legacy)
    result = evaluate_atomic_claim_candidate(
        candidate,
        reference=reference,
        reference_schema_version=schema,
        reference_provenance=provenance,
        requires_human_signoff=requires_signoff,
    )

    assert result.calibration_status == "insufficient_hash_bound_claim_labels"
    assert result.calibration_coverage == 0.0
    assert result.reference_claim_count == 0
    assert result.hash_matched_claim_count == 0
    assert result.whole_statement_exact_false_negative_count is None
    assert result.atomic_equivalence_false_positive_count is None
    assert result.candidate_selected is False
    assert result.decision == "not_calibrated"
    assert "legacy_observation_aggregate_not_per_claim" in result.reason_codes
    assert result.requires_human_signoff is True


def test_hash_bound_reference_can_select_equivalence_only_screening_candidate() -> None:
    reference = AtomicClaimReviewManifest.model_validate(_reference_payload())
    candidate = AtomicClaimCandidateManifest.model_validate(_candidate_payload())

    result = evaluate_atomic_claim_candidate(candidate, reference=reference)

    assert result.calibration_status == "complete_hash_bound_claim_labels"
    assert result.calibration_coverage == 1.0
    assert result.not_applicable_observation_count == 1
    assert result.unanswerable_or_abstention_misapplication_count == 0
    assert result.whole_statement_exact_false_negative_count == 1
    assert result.whole_statement_exact_false_positive_count == 0
    assert result.atomic_equivalence_false_negative_count == 0
    assert result.atomic_equivalence_false_positive_count == 0
    assert result.false_negative_reduction_count == 1
    assert result.false_positive_delta_count == 0
    assert result.candidate_selected is True
    assert result.decision == "screening_candidate_selected"
    assert result.screening_only is True
    assert result.primary_metric_status == "calibrated_grounded_answer_pass_rate_unchanged"


def test_candidate_is_rejected_when_false_positive_increases() -> None:
    reference_payload = _reference_payload()
    reference_payload["decisions"][1]["reference_supported"] = False
    candidate_payload = _candidate_payload()
    candidate_payload["observations"][1]["whole_statement_exact_match"] = False
    candidate_payload["observations"][1]["atomic_equivalence_match"] = True
    reference = AtomicClaimReviewManifest.model_validate(reference_payload)
    candidate = AtomicClaimCandidateManifest.model_validate(candidate_payload)

    result = evaluate_atomic_claim_candidate(candidate, reference=reference)

    assert result.atomic_equivalence_false_positive_count == 1
    assert result.false_positive_delta_count == 1
    assert result.candidate_selected is False
    assert "atomic_equivalence_fp_increased" in result.reason_codes


def test_generation_temperature_drift_is_rejected() -> None:
    candidate_payload = _candidate_payload()
    candidate_payload["source"]["generation_temperature"] = 0.1

    with pytest.raises(ValidationError, match="atomic_claim_generation_temperature_drift"):
        AtomicClaimCandidateManifest.model_validate(candidate_payload)


def test_source_fingerprint_drift_is_rejected() -> None:
    reference = AtomicClaimReviewManifest.model_validate(_reference_payload())
    candidate_payload = _candidate_payload()
    candidate_payload["source"]["case_set_fingerprint"] = "9" * 64
    candidate = AtomicClaimCandidateManifest.model_validate(candidate_payload)

    with pytest.raises(
        EvaluationAtomicClaimCalibrationError,
        match="atomic_claim_source_fingerprint_drift",
    ):
        evaluate_atomic_claim_candidate(candidate, reference=reference)


def test_answer_or_context_hash_drift_is_rejected() -> None:
    reference = AtomicClaimReviewManifest.model_validate(_reference_payload())
    candidate_payload = _candidate_payload()
    candidate_payload["observations"][0]["answer_hash"] = "8" * 64
    candidate = AtomicClaimCandidateManifest.model_validate(candidate_payload)

    with pytest.raises(
        EvaluationAtomicClaimCalibrationError,
        match="atomic_claim_answer_context_hash_drift",
    ):
        evaluate_atomic_claim_candidate(candidate, reference=reference)


def test_new_review_schema_forbids_raw_or_unknown_fields() -> None:
    payload = _reference_payload()
    payload["decisions"][0]["claim_text"] = "".join(("synthetic", "-", "value"))

    with pytest.raises(ValidationError):
        AtomicClaimReviewManifest.model_validate(payload)


def test_unanswerable_or_abstention_is_not_applicable_and_cannot_overlap() -> None:
    payload = _candidate_payload()
    payload["not_applicable_observations"][0] = {
        "case_id": payload["observations"][0]["case_id"],
        "answer_hash": payload["observations"][0]["answer_hash"],
        "context_hash": payload["observations"][0]["context_hash"],
        "reason": "unanswerable",
    }

    with pytest.raises(ValidationError, match="atomic_claim_not_applicable_overlap"):
        AtomicClaimCandidateManifest.model_validate(payload)


def test_output_contract_is_raw_free() -> None:
    result = evaluate_atomic_claim_candidate(
        AtomicClaimCandidateManifest.model_validate(_candidate_payload()),
        reference=None,
        reference_schema_version="phase3.oracle_codex_assisted_review.v1",
        reference_provenance="codex_assisted_manual_content_review",
    )
    rendered = json.dumps(result.safe_dict(), ensure_ascii=False, sort_keys=True)

    for forbidden_key in (
        '"question"',
        '"answer_text"',
        '"context_text"',
        '"chunk"',
        '"claim_text"',
        '"expected_answer"',
        '"model_output"',
    ):
        assert forbidden_key not in rendered


def test_cli_classifies_legacy_manifest_without_printing_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate_path = tmp_path / "candidate.json"
    reference_path = tmp_path / "reference.json"
    candidate_path.write_text(json.dumps(_candidate_payload()), encoding="utf-8")
    reference_path.write_text(
        json.dumps(
            {
                "schema_version": "phase3.oracle_codex_assisted_review.v1",
                "review_status": "requires_human_signoff",
                "reviewer_type": "codex_assisted_manual_content_review",
                "decisions": [{"manual_context_utilization": 1.0}],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "atomic-claim-calibration",
            "--candidate-manifest",
            str(candidate_path),
            "--reference-manifest",
            str(reference_path),
        ],
    )

    assert main() == 2
    output = json.loads(capsys.readouterr().out)
    assert output["calibration_status"] == "insufficient_hash_bound_claim_labels"
    assert output["calibration_coverage"] == 0.0
    assert output["whole_statement_exact_false_negative_count"] is None


def test_cli_fails_closed_with_stable_reason_for_malformed_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(json.dumps({"schema_version": "unexpected"}), encoding="utf-8")
    monkeypatch.setattr(
        sys,
        "argv",
        ["atomic-claim-calibration", "--candidate-manifest", str(candidate_path)],
    )

    assert main() == 2
    assert json.loads(capsys.readouterr().out) == {
        "reason_code": "atomic_claim_input_schema_invalid",
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


def _reference_payload() -> dict[str, Any]:
    return {
        "schema_version": "phase3.oracle_atomic_claim_review.v1",
        "source": _source_payload(),
        "reviewer_provenance": "codex_assisted_review",
        "reviewer_version": "v1",
        "review_status": "requires_human_signoff",
        "requires_human_signoff": True,
        "raw_content_persisted": False,
        "decisions": [
            {
                "case_id": "case-a",
                "answer_hash": "a" * 64,
                "context_hash": "b" * 64,
                "required_fact_id": "fact-a",
                "claim_ordinal": 0,
                "reference_supported": True,
            },
            {
                "case_id": "case-b",
                "answer_hash": "c" * 64,
                "context_hash": "d" * 64,
                "required_fact_id": "fact-b",
                "claim_ordinal": 0,
                "reference_supported": True,
            },
        ],
    }


def _candidate_payload() -> dict[str, Any]:
    return {
        "schema_version": "phase3.oracle_atomic_claim_candidate.v1",
        "source": deepcopy(_source_payload()),
        "candidate_id": "atomic-equivalence-v1",
        "candidate_version": "v1",
        "candidate_dimension": "semantic_equivalence_only",
        "raw_content_persisted": False,
        "pipeline_failure_count": 0,
        "observations": [
            {
                "case_id": "case-a",
                "answer_hash": "a" * 64,
                "context_hash": "b" * 64,
                "required_fact_id": "fact-a",
                "claim_ordinal": 0,
                "segmentation_status": "presegmented_reference_claim",
                "whole_statement_exact_match": False,
                "atomic_equivalence_match": True,
            },
            {
                "case_id": "case-b",
                "answer_hash": "c" * 64,
                "context_hash": "d" * 64,
                "required_fact_id": "fact-b",
                "claim_ordinal": 0,
                "segmentation_status": "presegmented_reference_claim",
                "whole_statement_exact_match": True,
                "atomic_equivalence_match": True,
            },
        ],
        "not_applicable_observations": [
            {
                "case_id": "case-u",
                "answer_hash": "e" * 64,
                "context_hash": "f" * 64,
                "reason": "unanswerable",
            }
        ],
    }
